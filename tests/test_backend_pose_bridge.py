from __future__ import annotations

import math

import pytest

from tracker.backend_pose_bridge import (
    BackendPoseContinuityBridge,
    PoseContinuityPolicy,
    _clamp_xy_magnitude,
)
from tracker.pose import HeadPosition


def _pose(
    x: float,
    timestamp: int,
    *,
    y: float = 0.0,
    z: float = 60.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
) -> HeadPosition:
    return HeadPosition(
        x_cm=x,
        y_cm=y,
        z_cm=z,
        yaw_deg=yaw,
        pitch_deg=pitch,
        roll_deg=roll,
        confidence=0.7,
        capture_timestamp_ms=timestamp,
    )


def test_first_fresh_backend_pose_is_aligned_to_recent_source():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(blend_ms=400)
    )
    source = _pose(10.0, 1000, y=3.0, z=65.0, yaw=12.0)
    assert bridge.apply(source, 1000) is source

    bridge.begin_transition(1033)
    adjusted = bridge.apply(_pose(0.0, 1033, z=60.0), 1033)

    assert adjusted is not None
    assert adjusted.x_cm == pytest.approx(10.0)
    assert adjusted.y_cm == pytest.approx(3.0)
    assert adjusted.z_cm == pytest.approx(65.0)
    assert adjusted.yaw_deg == pytest.approx(12.0)
    assert adjusted.confidence == pytest.approx(0.7)
    assert adjusted.capture_timestamp_ms == 1033


def test_offset_decays_linearly_to_new_backend_coordinates():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(blend_ms=400)
    )
    bridge.apply(_pose(8.0, 1000), 1000)
    bridge.begin_transition(1000)

    start = bridge.apply(_pose(0.0, 1000), 1000)
    halfway = bridge.apply(_pose(0.0, 1200), 1200)
    finished = bridge.apply(_pose(0.0, 1400), 1400)

    assert start is not None and start.x_cm == pytest.approx(8.0)
    assert halfway is not None and halfway.x_cm == pytest.approx(4.0)
    assert finished is not None and finished.x_cm == pytest.approx(0.0)
    assert not bridge.transition_active


def test_stale_source_pose_is_not_carried_into_new_backend():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(max_source_age_ms=500)
    )
    bridge.apply(_pose(15.0, 1000), 1000)
    bridge.begin_transition(1600)

    result = bridge.apply(_pose(1.0, 1600), 1600)

    assert result is not None
    assert result.x_cm == pytest.approx(1.0)
    assert not bridge.transition_active


def test_alignment_offsets_are_bounded_by_total_xy_magnitude():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(
            max_xy_offset_cm=20.0,
            max_z_offset_cm=10.0,
            max_angle_offset_deg=15.0,
        )
    )
    bridge.apply(
        _pose(100.0, 1000, y=-100.0, z=100.0, yaw=90.0),
        1000,
    )
    bridge.begin_transition(1000)

    result = bridge.apply(
        _pose(0.0, 1000, y=0.0, z=60.0, yaw=0.0),
        1000,
    )

    assert result is not None
    expected_axis = 20.0 / math.sqrt(2.0)
    assert result.x_cm == pytest.approx(expected_axis)
    assert result.y_cm == pytest.approx(-expected_axis)
    assert math.hypot(result.x_cm, result.y_cm) == pytest.approx(20.0)
    assert result.z_cm == pytest.approx(70.0)
    assert result.yaw_deg == pytest.approx(15.0)


def test_axis_only_alignment_limit_is_unchanged():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(max_xy_offset_cm=20.0)
    )
    bridge.apply(_pose(100.0, 1000), 1000)
    bridge.begin_transition(1000)

    result = bridge.apply(_pose(0.0, 1000), 1000)

    assert result is not None
    assert result.x_cm == pytest.approx(20.0)
    assert result.y_cm == pytest.approx(0.0)


def test_xy_alignment_clamp_preserves_direction():
    x_cm, y_cm = _clamp_xy_magnitude(30.0, 40.0, 10.0)

    assert x_cm == pytest.approx(6.0)
    assert y_cm == pytest.approx(8.0)
    assert math.hypot(x_cm, y_cm) == pytest.approx(10.0)


def test_zero_xy_limit_disables_translation_alignment_only():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(
            max_xy_offset_cm=0.0,
            max_z_offset_cm=10.0,
            max_angle_offset_deg=15.0,
        )
    )
    bridge.apply(
        _pose(10.0, 1000, y=5.0, z=70.0, yaw=10.0),
        1000,
    )
    bridge.begin_transition(1000)

    result = bridge.apply(
        _pose(0.0, 1000, y=0.0, z=60.0, yaw=0.0),
        1000,
    )

    assert result is not None
    assert result.x_cm == pytest.approx(0.0)
    assert result.y_cm == pytest.approx(0.0)
    assert result.z_cm == pytest.approx(70.0)
    assert result.yaw_deg == pytest.approx(10.0)


def test_angle_alignment_uses_shortest_wrap_direction():
    bridge = BackendPoseContinuityBridge()
    bridge.apply(_pose(0.0, 1000, yaw=179.0), 1000)
    bridge.begin_transition(1000)

    result = bridge.apply(_pose(0.0, 1000, yaw=-179.0), 1000)

    assert result is not None
    assert result.yaw_deg == pytest.approx(179.0)


def test_blend_elapsed_time_is_wrap_safe():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(blend_ms=400)
    )
    start_timestamp = 0xFFFF_FFF0
    bridge.apply(_pose(8.0, start_timestamp), start_timestamp)
    bridge.begin_transition(start_timestamp)
    bridge.apply(_pose(0.0, start_timestamp), start_timestamp)

    # 0xFFFFFFF0 -> 0xB8 is 200 ms across uint32 rollover.
    halfway = bridge.apply(_pose(0.0, 0xB8), 0xB8)

    assert halfway is not None
    assert halfway.x_cm == pytest.approx(4.0)


def test_transition_waits_for_first_real_pose():
    bridge = BackendPoseContinuityBridge()
    bridge.apply(_pose(4.0, 1000), 1000)
    bridge.begin_transition(1033)

    assert bridge.apply(None, 1033) is None
    assert bridge.transition_active
    result = bridge.apply(_pose(0.0, 1066), 1066)

    assert result is not None
    assert 0.0 < result.x_cm < 4.0


def test_reset_drops_old_source_and_pending_transition():
    bridge = BackendPoseContinuityBridge()
    bridge.apply(_pose(9.0, 1000), 1000)
    bridge.begin_transition(1033)

    bridge.reset()
    result = bridge.apply(_pose(1.0, 1033), 1033)

    assert result is not None and result.x_cm == pytest.approx(1.0)
    assert bridge.last_output is result
    assert not bridge.transition_active


def test_zero_blend_disables_alignment():
    bridge = BackendPoseContinuityBridge(
        PoseContinuityPolicy(blend_ms=0)
    )
    bridge.apply(_pose(9.0, 1000), 1000)
    bridge.begin_transition(1000)

    result = bridge.apply(_pose(1.0, 1000), 1000)

    assert result is not None and result.x_cm == pytest.approx(1.0)
    assert not bridge.transition_active


def test_invalid_policy_values_fail_closed():
    with pytest.raises(ValueError):
        PoseContinuityPolicy(blend_ms=-1)
    with pytest.raises(ValueError):
        PoseContinuityPolicy(max_source_age_ms=-1)
    with pytest.raises(ValueError):
        PoseContinuityPolicy(max_xy_offset_cm=float("nan"))
    with pytest.raises(ValueError):
        PoseContinuityPolicy(max_z_offset_cm=-1.0)
    with pytest.raises(ValueError):
        PoseContinuityPolicy(max_angle_offset_deg=-1.0)
