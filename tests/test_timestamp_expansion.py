from pathlib import Path

from tracker.timestamp_expansion import expand_u32_timestamp


def test_normal_forward_timestamps_preserve_delta():
    previous = 1000

    assert expand_u32_timestamp(1033, previous) == 1033


def test_uint32_wrap_expands_forward_without_going_backwards():
    previous = 0xFFFF_FFF0

    expanded = expand_u32_timestamp(0x0000_0010, previous)

    assert expanded == previous + 32
    assert expanded > 0xFFFF_FFFF
    assert expanded & 0xFFFF_FFFF == 0x10


def test_duplicate_wire_timestamp_becomes_strictly_increasing_for_mediapipe():
    previous = 5000

    assert expand_u32_timestamp(5000, previous) == 5001


def test_small_out_of_order_wire_sample_does_not_fake_a_49_day_jump():
    previous = 10_000

    expanded = expand_u32_timestamp(9_999, previous)

    assert expanded == previous + 1


def test_large_forward_delta_below_half_range_is_preserved():
    previous = 100
    wire = 0x1000_0000

    expanded = expand_u32_timestamp(wire, previous)

    assert expanded - previous == (wire - 100) & 0xFFFF_FFFF


def test_face_tracker_uses_extended_timeline_only_for_async_submission():
    source = Path("tracker/face_tracker.py").read_text(encoding="utf-8")

    assert "from tracker.timestamp_expansion import expand_u32_timestamp" in source
    assert "media_timestamp_ms = expand_u32_timestamp(" in source
    assert "self._landmarker.detect_async(image, media_timestamp_ms)" in source
    assert "return self._pose_from_result(result, w, h, wire_timestamp_ms)" in source
    assert "capture_timestamp_ms=int(timestamp_ms) & 0xFFFF_FFFF" in source
