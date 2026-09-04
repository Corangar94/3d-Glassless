from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tracker import live_filter_tuning_runtime, main as tracker_main
from tracker.camera_control_recovery_runtime import (
    CameraControlRecoveryTrackingLoop,
)
from tracker.live_filter_tuning import LiveFilterTuningPolicy
from tracker.live_filter_tuning_runtime import (
    LiveFilterTuningTrackingLoop,
)


class _Reader:
    def __init__(self, values=()) -> None:
        self.values = list(values)
        self.read_count = 0
        self.close_count = 0

    def read(self):
        self.read_count += 1
        if not self.values:
            return None
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self) -> None:
        self.close_count += 1


class _Smoother:
    def __init__(self) -> None:
        self.measurement_noise_values: list[float] = []

    def set_measurement_noise(self, value: float) -> None:
        self.measurement_noise_values.append(value)


class _BrokenController:
    def close(self) -> None:
        raise OSError("close failed")

    def snapshot(self):
        raise RuntimeError("snapshot failed")


def _construct(
    monkeypatch,
    *,
    smoother: object | None = None,
    reader: object | None = None,
    config_path: object | None = None,
    policy: LiveFilterTuningPolicy | None = None,
) -> LiveFilterTuningTrackingLoop:
    target = smoother if smoother is not None else _Smoother()

    def fake_parent_init(self, *args, **kwargs):
        self._smoother = target

    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "__init__",
        fake_parent_init,
    )
    kwargs: dict[str, object] = {}
    if reader is not None:
        kwargs["live_filter_settings_reader"] = reader
    if config_path is not None:
        kwargs["config_path"] = config_path
    if policy is not None:
        kwargs["live_filter_tuning_policy"] = policy
    return LiveFilterTuningTrackingLoop(**kwargs)


def test_injected_reader_creates_controller_after_smoother(monkeypatch):
    reader = _Reader([SimpleNamespace(smoothing_alpha=0.2)])
    smoother = _Smoother()
    policy = LiveFilterTuningPolicy(poll_interval_s=0.0)

    loop = _construct(
        monkeypatch,
        smoother=smoother,
        reader=reader,
        policy=policy,
    )

    assert loop.live_filter_tuning_policy == policy
    assert loop.live_filter_tuning_snapshot() is not None
    assert loop._live_filter_settings_reader is reader


def test_direct_no_config_loop_keeps_static_smoothing(monkeypatch):
    loop = _construct(monkeypatch)

    assert loop.live_filter_tuning_policy is None
    assert loop.live_filter_tuning_snapshot() is None


def test_configured_runtime_lazily_opens_default_reader(monkeypatch):
    reader = _Reader()
    opened: list[bool] = []
    monkeypatch.setattr(
        live_filter_tuning_runtime,
        "_open_default_settings_reader",
        lambda: opened.append(True) or reader,
    )

    loop = _construct(
        monkeypatch,
        config_path="config.yaml",
    )

    assert opened == [True]
    assert loop._live_filter_settings_reader is reader
    assert loop.live_filter_tuning_snapshot() is not None


def test_missing_default_mapping_preserves_static_smoothing(monkeypatch):
    monkeypatch.setattr(
        live_filter_tuning_runtime,
        "_open_default_settings_reader",
        lambda: None,
    )

    loop = _construct(monkeypatch, config_path="config.yaml")

    assert loop.live_filter_tuning_snapshot() is None


def test_explicit_reader_is_closed_when_smoother_cannot_be_tuned(monkeypatch):
    reader = _Reader()

    loop = _construct(
        monkeypatch,
        smoother=object(),
        reader=reader,
    )

    assert reader.close_count == 1
    assert loop.live_filter_tuning_snapshot() is None


def test_explicit_reader_is_closed_when_parent_construction_fails(monkeypatch):
    reader = _Reader()
    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "__init__",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("parent construction failed")
        ),
    )

    with pytest.raises(RuntimeError, match="parent construction failed"):
        LiveFilterTuningTrackingLoop(
            live_filter_settings_reader=reader,
        )

    assert reader.close_count == 1


def test_controller_initialization_failure_closes_reader_and_retains_policy(
    monkeypatch,
):
    reader = _Reader()
    policy = LiveFilterTuningPolicy(poll_interval_s=0.25)
    logs: list[str] = []
    monkeypatch.setattr(
        live_filter_tuning_runtime,
        "LiveFilterTuningController",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("controller construction failed")
        ),
    )
    monkeypatch.setattr(
        live_filter_tuning_runtime,
        "print",
        logs.append,
        raising=False,
    )

    loop = _construct(
        monkeypatch,
        reader=reader,
        policy=policy,
    )

    assert reader.close_count == 1
    assert loop.live_filter_tuning_policy == policy
    assert loop.live_filter_tuning_snapshot() is None
    assert any("RuntimeError" in line for line in logs)


def test_update_filter_polls_before_parent_filter_update(monkeypatch):
    reader = _Reader([SimpleNamespace(smoothing_alpha=0.28)])
    smoother = _Smoother()
    loop = _construct(
        monkeypatch,
        smoother=smoother,
        reader=reader,
        policy=LiveFilterTuningPolicy(poll_interval_s=0.0),
    )
    observed: list[tuple[object, tuple[float, ...]]] = []

    def parent_update(self, pose):
        observed.append((pose, tuple(smoother.measurement_noise_values)))
        return "filtered"

    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "_update_filter",
        parent_update,
    )
    pose = object()

    result = loop._update_filter(pose)

    assert result == "filtered"
    assert observed == [(pose, (0.28,))]


def test_unavailable_settings_do_not_block_parent_filter(monkeypatch):
    reader = _Reader([None])
    smoother = _Smoother()
    loop = _construct(monkeypatch, smoother=smoother, reader=reader)
    parent = MagicMock(return_value="filtered")
    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "_update_filter",
        parent,
    )
    pose = object()

    assert loop._update_filter(pose) == "filtered"
    parent.assert_called_once_with(loop, pose)
    assert smoother.measurement_noise_values == []


def test_unexpected_controller_exception_cannot_stop_pose_filter(monkeypatch):
    loop = _construct(monkeypatch, reader=_Reader())
    loop._live_filter_tuning = MagicMock()
    loop._live_filter_tuning.poll.side_effect = RuntimeError("patched failure")
    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "_update_filter",
        lambda _self, _pose: "filtered",
    )

    assert loop._update_filter(object()) == "filtered"


def test_run_closes_reader_after_normal_return_and_retains_snapshot(monkeypatch):
    reader = _Reader()
    loop = _construct(monkeypatch, reader=reader)
    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "run",
        lambda *_args, **_kwargs: "done",
    )

    assert loop.run() == "done"
    assert reader.close_count == 1
    snapshot = loop.live_filter_tuning_snapshot()
    assert snapshot is not None
    assert snapshot.closed
    assert loop.live_filter_tuning_policy == LiveFilterTuningPolicy()


def test_run_closes_reader_when_tracking_raises(monkeypatch):
    reader = _Reader()
    loop = _construct(monkeypatch, reader=reader)
    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("tracking failed")
        ),
    )

    with pytest.raises(RuntimeError, match="tracking failed"):
        loop.run()

    assert reader.close_count == 1
    assert loop._live_filter_settings_reader is None
    snapshot = loop.live_filter_tuning_snapshot()
    assert snapshot is not None and snapshot.closed


def test_broken_optional_cleanup_does_not_mask_normal_tracker_return(
    monkeypatch,
):
    loop = LiveFilterTuningTrackingLoop.__new__(
        LiveFilterTuningTrackingLoop
    )
    loop._live_filter_tuning = _BrokenController()
    loop._live_filter_settings_reader = None
    loop._live_filter_tuning_last_snapshot = None
    loop._live_filter_tuning_last_policy = None
    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "run",
        lambda *_args, **_kwargs: "done",
    )

    assert loop.run() == "done"
    assert loop._live_filter_tuning is None


def test_broken_optional_cleanup_does_not_mask_tracking_exception(
    monkeypatch,
):
    loop = LiveFilterTuningTrackingLoop.__new__(
        LiveFilterTuningTrackingLoop
    )
    loop._live_filter_tuning = _BrokenController()
    loop._live_filter_settings_reader = None
    loop._live_filter_tuning_last_snapshot = None
    loop._live_filter_tuning_last_policy = None
    monkeypatch.setattr(
        CameraControlRecoveryTrackingLoop,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("tracking failed")
        ),
    )

    with pytest.raises(RuntimeError, match="tracking failed"):
        loop.run()

    assert loop._live_filter_tuning is None


def test_runtime_inherits_camera_recovery_and_full_stability_stack():
    assert issubclass(
        LiveFilterTuningTrackingLoop,
        CameraControlRecoveryTrackingLoop,
    )


def test_runtime_main_selects_live_tuning_loop_and_restores_base(monkeypatch):
    original = tracker_main.TrackingLoop
    observed: list[object] = []
    monkeypatch.setattr(
        tracker_main,
        "main",
        lambda: observed.append(tracker_main.TrackingLoop),
    )

    live_filter_tuning_runtime.main()

    assert observed == [LiveFilterTuningTrackingLoop]
    assert tracker_main.TrackingLoop is original
