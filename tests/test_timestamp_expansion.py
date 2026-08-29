from pathlib import Path

from tracker.timestamp_expansion import expand_u32_timestamp, forward_u32_delta


def test_normal_forward_timestamps_preserve_delta():
    previous_wire = 1000
    previous_extended = 1000

    assert expand_u32_timestamp(1033, previous_wire, previous_extended) == 1033


def test_uint32_wrap_expands_forward_without_going_backwards():
    previous_wire = 0xFFFF_FFF0
    previous_extended = 0xFFFF_FFF0

    expanded = expand_u32_timestamp(0x0000_0010, previous_wire, previous_extended)

    assert expanded == previous_extended + 32
    assert expanded > 0xFFFF_FFFF
    assert expanded & 0xFFFF_FFFF == 0x10


def test_duplicate_wire_timestamp_is_dropped_instead_of_retimestamped():
    assert forward_u32_delta(5000, 5000) is None
    assert expand_u32_timestamp(5000, 5000, 5000) is None


def test_small_out_of_order_wire_sample_is_dropped():
    assert forward_u32_delta(9_999, 10_000) is None
    assert expand_u32_timestamp(9_999, 10_000, 10_000) is None


def test_large_forward_delta_below_half_range_is_preserved():
    previous_wire = 100
    previous_extended = 100
    wire = 0x1000_0000

    expanded = expand_u32_timestamp(wire, previous_wire, previous_extended)

    assert expanded is not None
    assert expanded - previous_extended == (wire - previous_wire) & 0xFFFF_FFFF
    assert expanded & 0xFFFF_FFFF == wire


def test_half_range_or_larger_is_rejected_as_ambiguous_backward_time():
    assert forward_u32_delta(0x8000_0000, 0) is None
    assert forward_u32_delta(0, 1) is None


def test_first_submission_keeps_the_wire_value_exactly():
    assert expand_u32_timestamp(0xFEDC_BA98, None, None) == 0xFEDC_BA98


def test_face_tracker_uses_extended_timeline_only_for_async_submission():
    source = Path("tracker/face_tracker.py").read_text(encoding="utf-8")

    assert "from tracker.timestamp_expansion import expand_u32_timestamp" in source
    assert "self._last_submitted_wire_timestamp_ms" in source
    assert "media_timestamp_ms = expand_u32_timestamp(" in source
    assert "if media_timestamp_ms is None:" in source
    assert "self._landmarker.detect_async(image, media_timestamp_ms)" in source
    assert "return self._pose_from_result(result, w, h, wire_timestamp_ms)" in source
    assert "capture_timestamp_ms=int(timestamp_ms) & 0xFFFF_FFFF" in source
