from __future__ import annotations

import math

import pytest

from tracker.pose import HeadPosition
from tracker.pose_step_limiter import (
    PoseStepLimiter,
    PoseStepLimiterPolicy,
    limit_pose_step,
)


def test_pure_step_helper_preserves_existing_radial_and_z_clamp():
    output = limit_pose_step(
        (30.0, 40.0, 160.0),
        (0.0, 0.0, 60.0),
        maximum_xy_step_cm=10.0,
        maximum_z_step_cm=12.0,
    )

    assert output == pytest.approx((6.0, 8.0, 72.0))


def test_first_pose_starts_episode_without_limiting():
    limiter = PoseStepLimiter()

    output = limiter.limit((20.0, -5.0, 80.0), 1000)

    assert output == (20.0, -5.0, 80.0)
    snapshot = limiter.snapshot()
    assert snapshot.last_timestamp_ms == 1000
    assert snapshot.limited_sample_count == 0


def test_default_limits_are_equivalent_physical_speeds_at_30_and_60_fps():
    thirty = PoseStepLimiter()
    sixty = PoseStepLimiter()
    thirty.limit((0.0, 0.0, 60.0), 1000)
    sixty.limit((0.0, 0.0, 60.0), 1000)

    thirty_output = thirty.limit((30.0, 40.0, 100.0), 1033)
    sixty_output = sixty.limit((30.0, 40.0, 100.0), 1016)

    assert math.hypot(thirty_output[0], thirty_output[1]) == pytest.approx(9.9)
    assert thirty_output[2] == pytest.approx(71.88)
    assert math.hypot(sixty_output[0], sixty_output[1]) == pytest.approx(4.8)
    assert sixty_output[2] == pytest.approx(65.76)
    assert math.hypot(thirty_output[0], thirty_output[1]) / 0.033 == pytest.approx(300.0)
    assert math.hypot(sixty_output[0], sixty_output[1]) / 0.016 == pytest.approx(300.0)


def test_low_fps_frame_gets_more_legitimate_travel_than_old_fixed_cap():
    limiter = PoseStepLimiter()
    limiter.limit((0.0, 0.0, 60.0), 1000)

    output = limiter.limit((30.0, 40.0, 120.0), 1100)

    assert math.hypot(output[0], output[1]) == pytest.approx(30.0)
    assert output[2] == pytest.approx(96.0)


def test_duplicate_timestamp_cannot_claim_new_travel_time():
    limiter = PoseStepLimiter()
    first = limiter.limit((1.0, 2.0, 60.0), 1000)

    output = limiter.limit((100.0, 100.0, 160.0), 1000)

    assert output == first
    snapshot = limiter.snapshot()
    assert snapshot.last_timestamp_ms == 1000
    assert snapshot.duplicate_or_out_of_order_count == 1
    assert snapshot.limited_sample_count == 1


def test_out_of_order_timestamp_is_ignored_without_moving_anchor():
    limiter = PoseStepLimiter()
    limiter.limit((0.0, 0.0, 60.0), 1000)
    limiter.limit((3.0, 0.0, 60.0), 1010)

    ignored = limiter.limit((100.0, 0.0, 100.0), 1005)
    recovered = limiter.limit((6.0, 0.0, 60.0), 1020)

    assert ignored == pytest.approx((3.0, 0.0, 60.0))
    assert recovered == pytest.approx((6.0, 0.0, 60.0))
    assert limiter.snapshot().duplicate_or_out_of_order_count == 1


def test_uint32_timestamp_rollover_uses_actual_elapsed_time():
    limiter = PoseStepLimiter()
    limiter.limit((0.0, 0.0, 60.0), 0xFFFF_FFF0)

    output = limiter.limit((30.0, 40.0, 100.0), 0x20)

    # 48 ms across rollover => 14.4 cm XY and 17.28 cm Z.
    assert math.hypot(output[0], output[1]) == pytest.approx(14.4)
    assert output[2] == pytest.approx(77.28)
    assert limiter.snapshot().last_interval_ms == 48


def test_long_measurement_gap_starts_fresh_episode():
    limiter = PoseStepLimiter(
        PoseStepLimiterPolicy(reset_after_ms=500)
    )
    limiter.limit((0.0, 0.0, 60.0), 1000)

    output = limiter.limit((100.0, -100.0, 150.0), 1500)

    assert output == (100.0, -100.0, 150.0)
    snapshot = limiter.snapshot()
    assert snapshot.episode_reset_count == 1
    assert snapshot.last_interval_ms is None


def test_gap_just_before_reset_still_uses_speed_bound():
    limiter = PoseStepLimiter(
        PoseStepLimiterPolicy(
            max_xy_speed_cm_s=100.0,
            max_z_speed_cm_s=100.0,
            reset_after_ms=500,
        )
    )
    limiter.limit((0.0, 0.0, 60.0), 1000)

    output = limiter.limit((100.0, 0.0, 160.0), 1499)

    assert output == pytest.approx((49.9, 0.0, 109.9))
    assert limiter.snapshot().episode_reset_count == 0


def test_zero_speed_disables_corresponding_axis_limits():
    limiter = PoseStepLimiter(
        PoseStepLimiterPolicy(
            max_xy_speed_cm_s=0.0,
            max_z_speed_cm_s=0.0,
        )
    )
    limiter.limit((0.0, 0.0, 60.0), 1000)

    output = limiter.limit((100.0, -100.0, 160.0), 1033)

    assert output == (100.0, -100.0, 160.0)


def test_head_position_metadata_survives_translation_limit():
    limiter = PoseStepLimiter(
        PoseStepLimiterPolicy(
            max_xy_speed_cm_s=100.0,
            max_z_speed_cm_s=100.0,
        )
    )
    limiter.limit_head_position(
        HeadPosition(0.0, 0.0, 60.0, capture_timestamp_ms=1000)
    )
    pose = HeadPosition(
        x_cm=20.0,
        y_cm=0.0,
        z_cm=80.0,
        yaw_deg=12.0,
        pitch_deg=-3.0,
        roll_deg=4.0,
        confidence=0.6,
        capture_timestamp_ms=1100,
    )

    output = limiter.limit_head_position(pose)

    assert output.xyz == pytest.approx((10.0, 0.0, 70.0))
    assert output.yaw_deg == 12.0
    assert output.pitch_deg == -3.0
    assert output.roll_deg == 4.0
    assert output.confidence == 0.6
    assert output.capture_timestamp_ms == 1100


def test_reset_forgets_anchor_but_preserves_lifetime_counters():
    limiter = PoseStepLimiter()
    limiter.limit((0.0, 0.0, 60.0), 1000)
    limiter.limit((100.0, 0.0, 160.0), 1033)
    assert limiter.snapshot().limited_sample_count == 1

    limiter.reset()
    output = limiter.limit((100.0, 0.0, 160.0), 2000)

    assert output == (100.0, 0.0, 160.0)
    assert limiter.snapshot().limited_sample_count == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_xy_speed_cm_s": -1.0},
        {"max_z_speed_cm_s": -1.0},
        {"max_xy_speed_cm_s": float("nan")},
        {"max_z_speed_cm_s": float("inf")},
        {"reset_after_ms": 0},
    ],
)
def test_invalid_policy_fails_closed(kwargs):
    with pytest.raises(ValueError):
        PoseStepLimiterPolicy(**kwargs)


@pytest.mark.parametrize(
    "position",
    [
        (float("nan"), 0.0, 60.0),
        (0.0, float("inf"), 60.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, -1.0),
    ],
)
def test_invalid_raw_position_fails_closed(position):
    with pytest.raises(ValueError):
        PoseStepLimiter().limit(position, 1000)
