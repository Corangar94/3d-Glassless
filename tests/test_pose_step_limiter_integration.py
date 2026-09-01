from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from tracker.backend_transition_state import (
    mark_backend_transition,
    reset_backend_transition_generation,
)
from tracker.main import (
    TrackingLoop,
    _limit_pose_step,
    _pose_step_limiter_policy,
)
from tracker.pose import FilteredPose, HeadPosition
from tracker.pose_step_limiter import PoseStepLimiter, PoseStepLimiterPolicy


class Tracker:
    def __init__(self, pose: HeadPosition | None = None) -> None:
        self.pose = pose
        self.reset_count = 0

    def process_frame(self, _frame, capture_timestamp_ms=None):
        return self.pose

    def reset_session(self) -> None:
        self.reset_count += 1


class Filter:
    def __init__(self) -> None:
        self.inputs: list[HeadPosition] = []
        self.reset_count = 0

    def update_pose(self, pose: HeadPosition) -> FilteredPose:
        self.inputs.append(pose)
        return FilteredPose(
            x_cm=pose.x_cm,
            y_cm=pose.y_cm,
            z_cm=pose.z_cm,
            confidence=pose.confidence,
            capture_timestamp_ms=pose.capture_timestamp_ms,
        )

    def predict(self) -> FilteredPose:
        return FilteredPose(x_cm=0.0, y_cm=0.0, z_cm=60.0)

    def set_measurement_noise(self, _value: float) -> None:
        pass

    def reset(self) -> None:
        self.reset_count += 1


class Capture:
    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.release_count = 0

    def read(self):
        return True, self.frame

    def release(self) -> None:
        self.release_count += 1


class SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _clean_transition_state():
    reset_backend_transition_generation()
    yield
    reset_backend_transition_generation()


def test_valid_pose_step_policy_is_parsed():
    policy = _pose_step_limiter_policy(
        {
            "pose_step_limit": {
                "max_xy_speed_cm_s": 250.0,
                "max_z_speed_cm_s": 310.0,
                "reset_after_ms": 650,
            }
        }
    )

    assert policy == PoseStepLimiterPolicy(
        max_xy_speed_cm_s=250.0,
        max_z_speed_cm_s=310.0,
        reset_after_ms=650,
    )


def test_invalid_pose_step_policy_falls_back_atomically(capsys):
    policy = _pose_step_limiter_policy(
        {
            "pose_step_limit": {
                "max_xy_speed_cm_s": -1.0,
                "max_z_speed_cm_s": "bad",
                "reset_after_ms": 0,
            }
        }
    )

    assert policy == PoseStepLimiterPolicy()
    assert "using safe defaults" in capsys.readouterr().out


def test_historical_fixed_step_helper_remains_compatible():
    assert _limit_pose_step(
        (30.0, 40.0, 160.0),
        (0.0, 0.0, 60.0),
    ) == pytest.approx((6.0, 8.0, 72.0))


def test_tracking_loop_applies_limiter_before_pose_filter(monkeypatch):
    raw = HeadPosition(
        x_cm=100.0,
        y_cm=0.0,
        z_cm=100.0,
        confidence=0.8,
        capture_timestamp_ms=1033,
    )
    limited = HeadPosition(
        x_cm=9.9,
        y_cm=0.0,
        z_cm=71.88,
        confidence=0.8,
        capture_timestamp_ms=1033,
    )
    tracker = Tracker(raw)
    filter_ = Filter()
    limiter = MagicMock(spec=PoseStepLimiter)
    limiter.limit_head_position.return_value = limited
    capture = Capture(np.zeros((4, 6, 3), dtype=np.uint8))
    writer = MagicMock()
    loop = TrackingLoop(
        tracker=tracker,
        writer=writer,
        smoother=filter_,
        hold_ms=0,
        pose_step_limiter=limiter,
    )
    monkeypatch.setattr(
        loop,
        "_open_camera_with_recovery",
        lambda *_args, **_kwargs: (capture, 0),
    )
    monkeypatch.setattr(
        "tracker.main.SharedSettingsReader",
        SettingsReader,
    )

    loop.run(max_frames=1)

    limiter.reset.assert_called_once_with()
    limiter.limit_head_position.assert_called_once_with(raw)
    assert filter_.inputs == [limited]
    assert loop._last_raw_pos == limited.xyz
    assert capture.release_count == 1


def test_backend_transition_resets_capture_time_limiter():
    limiter = MagicMock(spec=PoseStepLimiter)
    loop = TrackingLoop(
        tracker=Tracker(),
        writer=MagicMock(),
        smoother=Filter(),
        pose_step_limiter=limiter,
    )

    mark_backend_transition(preserve_position=True)
    loop._synchronize_tracker_backend_transition()

    limiter.reset.assert_called_once_with()
    assert loop._last_raw_pos is None


def test_camera_session_reset_resets_capture_time_limiter():
    tracker = Tracker()
    filter_ = Filter()
    limiter = MagicMock(spec=PoseStepLimiter)
    loop = TrackingLoop(
        tracker=tracker,
        writer=MagicMock(),
        smoother=filter_,
        pose_step_limiter=limiter,
    )

    loop._reset_capture_session()

    limiter.reset.assert_called_once_with()
    assert tracker.reset_count == 1
    assert filter_.reset_count == 1
    assert loop._last_raw_pos is None


def test_repository_and_setup_defaults_match():
    config = open("config.yaml", encoding="utf-8").read()
    wizard = open("launcher/wizard.py", encoding="utf-8").read()

    for source in (config, wizard):
        assert "pose_step_limit" in source
        assert "max_xy_speed_cm_s" in source
        assert "300.0" in source
        assert "max_z_speed_cm_s" in source
        assert "360.0" in source
        assert "reset_after_ms" in source
        assert "500" in source


def test_frozen_package_includes_pose_step_limiter():
    spec = open("Glassless3D.spec", encoding="utf-8").read()

    assert '"tracker.pose_step_limiter"' in spec


def test_tracking_loop_source_uses_stateful_limiter_not_fixed_frame_cap():
    source = open("tracker/main.py", encoding="utf-8").read()
    measured_block = source.split(
        "if measured is not None:",
        1,
    )[1].split("else:", 1)[0]

    assert "self._pose_step_limiter.limit_head_position(" in measured_block
    assert "_limit_pose_step(" not in measured_block
    assert "limit_pose_step," in source
