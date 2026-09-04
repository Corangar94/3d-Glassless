from __future__ import annotations

from types import SimpleNamespace

import pytest

from tracker.live_filter_tuning import (
    LiveFilterTuningController,
    LiveFilterTuningPolicy,
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


class _Target:
    def __init__(self) -> None:
        self.values: list[float] = []
        self.error: BaseException | None = None

    def set_measurement_noise(self, value: float) -> None:
        if self.error is not None:
            raise self.error
        self.values.append(value)


def _settings(value):
    return SimpleNamespace(smoothing_alpha=value)


def test_first_valid_value_is_applied_immediately():
    reader = _Reader([_settings(0.28)])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert controller.poll(10.0)

    assert target.values == [0.28]
    snapshot = controller.snapshot()
    assert snapshot.poll_count == 1
    assert snapshot.applied_count == 1
    assert snapshot.last_applied_measurement_noise == pytest.approx(0.28)


def test_mapping_settings_are_supported():
    reader = _Reader([{"smoothing_alpha": 0.06}])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert controller.poll(0.0)
    assert target.values == [0.06]


def test_poll_interval_prevents_camera_rate_reader_hammering():
    reader = _Reader([_settings(0.1), _settings(0.2)])
    target = _Target()
    controller = LiveFilterTuningController(
        reader,
        target,
        LiveFilterTuningPolicy(poll_interval_s=0.1),
    )

    assert controller.poll(1.0)
    assert not controller.poll(1.099)
    assert controller.poll(1.1)

    assert reader.read_count == 2
    assert target.values == [0.1, 0.2]
    assert controller.snapshot().skipped_poll_count == 1


def test_unchanged_and_sub_epsilon_values_do_not_reapply():
    reader = _Reader(
        [_settings(0.1), _settings(0.1), _settings(0.1009), _settings(0.1011)]
    )
    target = _Target()
    controller = LiveFilterTuningController(
        reader,
        target,
        LiveFilterTuningPolicy(poll_interval_s=0.0, change_epsilon=0.001),
    )

    assert controller.poll(1.0)
    assert not controller.poll(2.0)
    assert not controller.poll(3.0)
    assert controller.poll(4.0)

    assert target.values == pytest.approx([0.1, 0.1011])
    assert controller.snapshot().unchanged_count == 2


def test_small_changes_accumulate_relative_to_last_applied_value():
    reader = _Reader(
        [_settings(0.1), _settings(0.1006), _settings(0.1012)]
    )
    target = _Target()
    controller = LiveFilterTuningController(
        reader,
        target,
        LiveFilterTuningPolicy(poll_interval_s=0.0, change_epsilon=0.001),
    )

    assert controller.poll(1.0)
    assert not controller.poll(2.0)
    assert controller.poll(3.0)

    assert target.values == pytest.approx([0.1, 0.1012])


@pytest.mark.parametrize("value", [0.01, 1.0])
def test_exact_measurement_noise_bounds_are_valid(value):
    reader = _Reader([_settings(value)])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert controller.poll(1.0)
    assert target.values == [value]


@pytest.mark.parametrize(
    "settings",
    [
        _settings(0.009),
        _settings(1.001),
        _settings(True),
        _settings(False),
        _settings(float("nan")),
        _settings(float("inf")),
        _settings(float("-inf")),
        _settings("0.1"),
        object(),
        {},
    ],
)
def test_invalid_values_leave_filter_unchanged(settings):
    reader = _Reader([settings])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert not controller.poll(1.0)
    assert target.values == []
    assert controller.snapshot().invalid_value_count == 1


def test_unavailable_mapping_keeps_configured_filter_value():
    reader = _Reader([None])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert not controller.poll(1.0)
    assert target.values == []
    snapshot = controller.snapshot()
    assert snapshot.unavailable_count == 1
    assert snapshot.last_error == ""


def test_reader_exception_is_contained_and_interval_is_consumed():
    reader = _Reader([OSError("mapping unavailable"), _settings(0.2)])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert not controller.poll(1.0)
    assert not controller.poll(1.05)
    assert controller.poll(1.1)

    assert target.values == [0.2]
    snapshot = controller.snapshot()
    assert snapshot.read_error_count == 1
    assert snapshot.skipped_poll_count == 1


def test_target_exception_is_contained_and_value_can_retry():
    reader = _Reader([_settings(0.2), _settings(0.2)])
    target = _Target()
    target.error = RuntimeError("filter rejected value")
    controller = LiveFilterTuningController(
        reader,
        target,
        LiveFilterTuningPolicy(poll_interval_s=0.0),
    )

    assert not controller.poll(1.0)
    target.error = None
    assert controller.poll(2.0)

    assert target.values == [0.2]
    snapshot = controller.snapshot()
    assert snapshot.apply_error_count == 1
    assert snapshot.applied_count == 1


def test_backward_clock_starts_a_new_poll_window():
    reader = _Reader([_settings(0.1), _settings(0.2)])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert controller.poll(10.0)
    assert controller.poll(5.0)

    assert target.values == [0.1, 0.2]
    assert controller.snapshot().clock_reset_count == 1


@pytest.mark.parametrize(
    "now_s",
    [True, False, -1.0, float("nan"), float("inf"), "1.0", object()],
)
def test_invalid_explicit_clock_values_fail_closed(now_s):
    reader = _Reader([_settings(0.1)])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert not controller.poll(now_s)
    assert reader.read_count == 0
    assert target.values == []
    assert controller.snapshot().clock_error_count == 1


def test_clock_exception_is_contained():
    reader = _Reader([_settings(0.1)])
    target = _Target()

    def broken_clock():
        raise RuntimeError("clock failed")

    controller = LiveFilterTuningController(
        reader,
        target,
        clock=broken_clock,
    )

    assert not controller.poll()
    assert reader.read_count == 0
    snapshot = controller.snapshot()
    assert snapshot.clock_error_count == 1
    assert "RuntimeError" in snapshot.last_error


def test_close_is_idempotent_and_closed_controller_does_not_read():
    reader = _Reader([_settings(0.1)])
    target = _Target()
    controller = LiveFilterTuningController(reader, target)

    assert controller.close()
    assert controller.close()
    assert not controller.poll(1.0)

    assert reader.close_count == 1
    assert reader.read_count == 0
    snapshot = controller.snapshot()
    assert snapshot.closed
    assert snapshot.skipped_poll_count == 1


def test_close_exception_is_contained_once():
    class BrokenReader(_Reader):
        def close(self):
            self.close_count += 1
            raise OSError("close failed")

    reader = BrokenReader()
    controller = LiveFilterTuningController(reader, _Target())

    assert not controller.close()
    assert controller.close()

    assert reader.close_count == 1
    assert controller.snapshot().close_error_count == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"poll_interval_s": -0.1},
        {"poll_interval_s": True},
        {"minimum_measurement_noise": 0.0},
        {"minimum_measurement_noise": False},
        {
            "minimum_measurement_noise": 0.5,
            "maximum_measurement_noise": 0.4,
        },
        {"maximum_measurement_noise": float("inf")},
        {"change_epsilon": -0.1},
        {"change_epsilon": "0.1"},
    ],
)
def test_invalid_policy_values_fail_closed(kwargs):
    with pytest.raises(ValueError):
        LiveFilterTuningPolicy(**kwargs)


def test_constructor_requires_reader_target_and_clock_contracts():
    with pytest.raises(TypeError, match="read"):
        LiveFilterTuningController(object(), _Target())
    with pytest.raises(TypeError, match="set_measurement_noise"):
        LiveFilterTuningController(_Reader(), object())
    with pytest.raises(TypeError, match="clock"):
        LiveFilterTuningController(_Reader(), _Target(), clock=object())
