import math

import pytest

from launcher.auto_tune import TrackingAutoTuner


def test_auto_tuner_is_stable_while_head_is_still():
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    result = tuner.update(0.0, 0.0, 60.0, 0.1)

    assert result.smoothing_alpha == 0.28
    assert result.deadzone_mm == 3.0
    assert result.head_dist_cm == 60.0


def test_auto_tuner_becomes_responsive_during_deliberate_motion():
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    result = None
    for index in range(1, 8):
        result = tuner.update(float(index) * 4.0, 0.0, 60.0, index * 0.1)

    assert result is not None
    assert result.smoothing_alpha < 0.1
    assert result.deadzone_mm < 1.0


def test_auto_tuner_filters_and_clamps_viewing_distance():
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    result = tuner.update(0.0, 0.0, 500.0, 0.1)

    assert 60.0 < result.head_dist_cm < 200.0


def _run_constant_motion(rate_hz: int):
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    result = None
    for index in range(1, rate_hz + 1):
        timestamp = index / rate_hz
        result = tuner.update(10.0 * timestamp, 0.0, 60.0, timestamp)
    assert result is not None
    return result


def test_elapsed_time_ema_is_consistent_across_callback_rates():
    slow = _run_constant_motion(15)
    nominal = _run_constant_motion(30)
    fast = _run_constant_motion(60)

    assert slow.speed_cm_s == pytest.approx(nominal.speed_cm_s, abs=1e-9)
    assert fast.speed_cm_s == pytest.approx(nominal.speed_cm_s, abs=1e-9)
    assert slow.smoothing_alpha == pytest.approx(
        fast.smoothing_alpha,
        abs=1e-9,
    )
    assert slow.deadzone_mm == pytest.approx(fast.deadzone_mm, abs=1e-9)


def test_long_gap_starts_fresh_episode_without_motion_spike():
    tuner = TrackingAutoTuner(reset_after_s=0.5)
    tuner.update(0.0, 0.0, 60.0, 0.0)
    moving = tuner.update(20.0, 0.0, 60.0, 0.1)
    assert moving.speed_cm_s > 20.0

    reacquired = tuner.update(-40.0, 10.0, 100.0, 0.6)

    assert reacquired.speed_cm_s == 0.0
    assert reacquired.smoothing_alpha == 0.28
    assert reacquired.head_dist_cm == 100.0
    assert tuner.episode_reset_count == 1


def test_duplicate_or_backward_time_is_ignored_without_mutating_history():
    tuner = TrackingAutoTuner()
    first = tuner.update(0.0, 0.0, 60.0, 1.0)

    duplicate = tuner.update(100.0, 0.0, 200.0, 1.0)
    backward = tuner.update(100.0, 0.0, 200.0, 0.9)

    assert duplicate == first
    assert backward == first
    assert tuner.rejected_sample_count == 2

    resumed = tuner.update(1.0, 0.0, 60.0, 1.1)
    assert 0.0 < resumed.speed_cm_s < 20.0


@pytest.mark.parametrize(
    "sample",
    [
        (float("nan"), 0.0, 60.0, 1.0),
        (0.0, float("inf"), 60.0, 1.0),
        (0.0, 0.0, float("-inf"), 1.0),
        (0.0, 0.0, 60.0, float("nan")),
        (True, 0.0, 60.0, 1.0),
        (0.0, 0.0, 60.0, -1.0),
    ],
)
def test_invalid_samples_are_rejected_without_poisoning_tuning(sample):
    tuner = TrackingAutoTuner()

    rejected = tuner.update(*sample)

    assert rejected.head_dist_cm == 60.0
    assert rejected.speed_cm_s == 0.0
    assert rejected.smoothing_alpha == 0.28
    assert rejected.deadzone_mm == 3.0
    assert tuner.rejected_sample_count == 1

    valid = tuner.update(0.0, 0.0, 70.0, 1.0)
    assert valid.head_dist_cm == 70.0
    assert all(
        math.isfinite(value)
        for value in (
            valid.head_dist_cm,
            valid.smoothing_alpha,
            valid.deadzone_mm,
            valid.speed_cm_s,
        )
    )


def test_instantaneous_motion_spike_is_bounded_before_ema():
    tuner = TrackingAutoTuner(maximum_speed_cm_s=300.0)
    tuner.update(0.0, 0.0, 60.0, 0.0)

    result = tuner.update(1e300, -1e300, 60.0, 1.0 / 30.0)

    # At the nominal rate, alpha is 0.25, so a capped 300 cm/s sample can
    # contribute at most 75 cm/s to the long-lived speed state.
    assert result.speed_cm_s == pytest.approx(75.0)
    assert math.isfinite(result.speed_cm_s)


def test_explicit_reset_clears_motion_and_distance_history():
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    tuner.update(20.0, 0.0, 120.0, 0.1)

    tuner.reset()
    result = tuner.update(0.0, 0.0, 80.0, 10.0)

    assert result.speed_cm_s == 0.0
    assert result.head_dist_cm == 80.0
    assert result.smoothing_alpha == 0.28


@pytest.mark.parametrize(
    "kwargs",
    [
        {"reset_after_s": 0.0},
        {"reset_after_s": -1.0},
        {"reset_after_s": float("nan")},
        {"reset_after_s": True},
        {"maximum_speed_cm_s": 0.0},
        {"maximum_speed_cm_s": float("inf")},
        {"maximum_speed_cm_s": False},
    ],
)
def test_invalid_tuner_limits_fail_closed(kwargs):
    with pytest.raises(ValueError):
        TrackingAutoTuner(**kwargs)
