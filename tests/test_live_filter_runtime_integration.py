"""Full run-loop regressions: fake I/O, real constructors, adapter and filter."""
from __future__ import annotations

from unittest.mock import patch
import numpy as np
import pytest

from tracker import main as base
from tracker.latest_frame_capture import LatestFrameCapturePolicy
from tracker.live_filter_tuning import LiveFilterTuningPolicy
from tracker.live_filter_tuning_runtime import LiveFilterTuningTrackingLoop
from tracker.pose import HeadPosition
from tracker.pose_filter import AdaptivePoseFilter
from tracker.shared_settings import OverlaySettings


class Reader:
    def __init__(self, values):
        self.values = iter(values)

    def read(self):
        value = next(self.values, None)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        pass

class VersionReader(Reader):
    def read_smoothing_alpha(self):
        return self.read()


class Capture:
    def isOpened(self):
        return True

    def read(self):
        return True, np.zeros((16, 16, 3), dtype=np.uint8)

    def release(self):
        pass


class Tracker:
    def __init__(self):
        self.index = 0

    def process_frame(self, frame, capture_timestamp_ms=None):
        self.index += 1
        return HeadPosition(x_cm=0, y_cm=0, z_cm=60, confidence=0.9,
                            capture_timestamp_ms=1000 + 33 * self.index)

    def reset_session(self):
        pass

class Writer:
    def write_state(self, status):
        pass

    def write_pose(self, pose, valid=True):
        pass

    def write(self, **kwargs):
        pass


class ObservedFilter(AdaptivePoseFilter):
    def __init__(self):
        super().__init__(measurement_noise=0.4)
        self.current = 0.4
        self.used = []
        self.writes = []

    def set_measurement_noise(self, value):
        self.current = value
        self.writes.append(value)
        return super().set_measurement_noise(value)

    def update_pose(self, pose, **kwargs):
        self.used.append(self.current)
        return super().update_pose(pose, **kwargs)

def exercise(base_values, reader, *, direct=False, policy=None, clock=None):
    target = ObservedFilter()
    kwargs = dict(tracker=Tracker(), writer=Writer(), smoother=target)
    if direct:
        loop = base.TrackingLoop(**kwargs)
    else:
        loop = LiveFilterTuningTrackingLoop(
            **kwargs, live_filter_settings_reader=reader,
            latest_frame_capture_policy=LatestFrameCapturePolicy(enabled=False),
            live_filter_tuning_policy=policy or LiveFilterTuningPolicy(poll_interval_s=0),
        )
        if clock is not None:
            loop._live_filter_tuning._clock = clock
    with patch.object(base, "_open_camera", return_value=Capture()), patch.object(
        base, "SharedSettingsReader", return_value=Reader(base_values)
    ):
        loop.run(max_frames=len(base_values))
    snapshot = None if direct else loop.live_filter_tuning_snapshot()
    return target, snapshot


def settings(value):
    return OverlaySettings(smoothing_alpha=value)


@pytest.mark.parametrize("direct", [False, True])
def test_no_settings_preserves_configured_filter_value(direct):
    target, _ = exercise([None] * 3, None, direct=direct)
    assert target.used == [0.4] * 3 and target.writes == []

@pytest.mark.parametrize("direct", [False, True])
def test_unavailable_read_preserves_last_live_value(direct):
    values = [settings(0.3), None, None]
    target, snapshot = exercise(values, Reader(values), direct=direct)
    assert target.used == [0.3] * 3 and target.writes == [0.3]
    if snapshot is not None:
        assert snapshot.last_applied_measurement_noise == 0.3
        assert snapshot.unavailable_count == 2


@pytest.mark.parametrize("invalid", [0.005, 1.01, True, False, "0.5", float("nan"), float("inf"), 10**1000])
@pytest.mark.parametrize("direct", [False, True])
def test_invalid_live_values_never_enter_filter_through_base_loop(invalid, direct):
    values = [settings(invalid)] * 3
    target, snapshot = exercise(values, Reader(values), direct=direct)
    assert target.used == [0.4] * 3 and target.writes == []
    if snapshot is not None:
        assert snapshot.invalid_value_count == 3


def test_controller_read_failure_and_recovery_keep_actual_value_and_telemetry_aligned():
    reader = Reader([settings(0.3), OSError("unavailable"), settings(0.6)])
    target, snapshot = exercise([settings(0.1)] * 3, reader)
    assert target.used == [0.3, 0.3, 0.6]
    assert target.writes == [0.3, 0.6]
    assert snapshot.read_error_count == 1
    assert snapshot.last_applied_measurement_noise == 0.6

def test_throttled_controller_is_not_bypassed_by_per_frame_settings():
    times = iter([1.0, 1.01, 1.02])
    target, snapshot = exercise(
        [settings(0.1)] * 3, Reader([settings(0.3)] * 3),
        policy=LiveFilterTuningPolicy(poll_interval_s=0.1), clock=lambda: next(times),
    )
    assert target.used == [0.3] * 3 and target.writes == [0.3]
    assert snapshot.skipped_poll_count == 2


def test_unchanged_version_is_not_undone_by_full_settings_reader():
    reader = VersionReader([(2, 0.3)] * 3)
    target, snapshot = exercise([settings(0.1)] * 3, reader)
    assert target.used == [0.3] * 3 and target.writes == [0.3]
    assert snapshot.unchanged_version_count == 2
    assert snapshot.last_applied_measurement_noise == 0.3


def test_custom_controller_bounds_own_admission_without_base_fallback():
    target, snapshot = exercise(
        [settings(0.8)] * 3, Reader([settings(0.8)] * 3),
        policy=LiveFilterTuningPolicy(poll_interval_s=0, maximum_measurement_noise=0.5),
    )
    assert target.used == [0.4] * 3 and target.writes == []
    assert snapshot.invalid_value_count == 3
