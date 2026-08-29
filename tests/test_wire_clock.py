import ctypes
import os
from pathlib import Path

import pytest

from tracker import pose
from tracker import shared_memory
from tracker.pose import HeadPosition
from tracker.pose_filter import AdaptivePoseFilter
from tracker.shared_memory import (
    SharedMemoryReader,
    SharedMemoryWriter,
    TrackingStateReader,
    TrackingStateWriter,
)


UINT32_MASK = 0xFFFF_FFFF


def _uint32_elapsed(newer: int, older: int) -> int:
    return (int(newer) - int(older)) & UINT32_MASK


@pytest.mark.skipif(os.name != "nt", reason="Windows wire clock contract")
def test_wire_clock_matches_native_gettickcount_epoch():
    kernel32 = ctypes.windll.kernel32
    kernel32.GetTickCount.restype = ctypes.c_ulong

    before = int(kernel32.GetTickCount()) & UINT32_MASK
    published = pose.monotonic_ms()
    after = int(kernel32.GetTickCount()) & UINT32_MASK

    bracket = _uint32_elapsed(after, before)
    offset = _uint32_elapsed(published, before)
    # Timestamp zero is reserved. At the exact rollover instant Python publishes
    # UINT32_MAX, which is one millisecond old relative to native zero.
    if before == 0 or after == 0:
        assert _uint32_elapsed(after, published) <= 1
    else:
        assert bracket < 10_000
        assert offset <= bracket


def test_wire_clock_wraps_to_uint32(monkeypatch):
    monkeypatch.setattr(pose, "_wire_uptime_ms64", lambda: 0x1_0000_0017)

    assert pose.monotonic_ms() == 0x17


def test_wire_clock_reserves_zero_missing_sentinel(monkeypatch):
    monkeypatch.setattr(pose, "_wire_uptime_ms64", lambda: 0x1_0000_0000)

    assert pose.monotonic_ms() == UINT32_MASK
    assert pose.normalize_wire_timestamp(0) == UINT32_MASK
    assert pose.elapsed_u32_ms(0, UINT32_MASK) == 1


def test_elapsed_wire_clock_is_wrap_safe():
    assert pose.elapsed_u32_ms(5, 0xFFFF_FFFB) == 10
    assert pose.elapsed_u32_ms(20, 10) == 10


def test_missing_head_timestamp_uses_nonzero_normalized_fallback():
    unstamped = HeadPosition(x_cm=0.0, y_cm=0.0, z_cm=60.0)

    stamped = unstamped.with_timestamp_if_missing(timestamp_ms=0)

    assert stamped.capture_timestamp_ms == UINT32_MASK


def test_pose_filter_never_publishes_zero_timestamp(monkeypatch):
    monkeypatch.setattr("tracker.pose_filter.monotonic_ms", lambda: UINT32_MASK)
    filter_ = AdaptivePoseFilter(prediction_horizon_ms=1.0)

    output = filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=0,
        ),
        publish_timestamp_ms=0,
    )

    assert output.capture_timestamp_ms == UINT32_MASK
    assert output.publish_timestamp_ms == UINT32_MASK
    assert output.prediction_target_timestamp_ms == UINT32_MASK


def test_prediction_target_wraps_without_emitting_zero():
    filter_ = AdaptivePoseFilter(
        prediction_horizon_ms=10.0,
        max_prediction_ms=20.0,
    )
    filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=0xFFFF_FFF8,
        ),
        publish_timestamp_ms=0xFFFF_FFF8,
    )

    output = filter_.predict(publish_timestamp_ms=2)

    assert output.capture_timestamp_ms == 0xFFFF_FFF8
    assert output.publish_timestamp_ms == 2
    assert output.prediction_target_timestamp_ms == 12


def test_tracker_backends_normalize_explicit_zero_capture_time():
    mediapipe_source = Path("tracker/face_tracker.py").read_text(encoding="utf-8")
    cv2_source = Path("tracker/face_tracker_cv2.py").read_text(encoding="utf-8")

    assert "else normalize_wire_timestamp(capture_timestamp_ms)" in mediapipe_source
    assert "else normalize_wire_timestamp(capture_timestamp_ms)" in cv2_source
    assert "_last_delivered_timestamp_ms: int | None = None" in mediapipe_source


def test_legacy_pose_writer_uses_shared_wire_clock(monkeypatch):
    timestamp = 0xF1234567
    monkeypatch.setattr(shared_memory, "monotonic_ms", lambda: timestamp)

    with SharedMemoryWriter(name="G3D_WIRE_CLOCK_TEST") as writer:
        reader = SharedMemoryReader(name="G3D_WIRE_CLOCK_TEST")
        try:
            writer.write(x=1.0, y=2.0, z=63.0)
            snapshot = reader.read()
        finally:
            reader.close()

    assert snapshot is not None
    assert snapshot[3] == timestamp


def test_tracking_state_writer_uses_shared_wire_clock(monkeypatch):
    timestamp = 0xE7654321
    monkeypatch.setattr(shared_memory, "monotonic_ms", lambda: timestamp)

    with TrackingStateWriter(name="G3D_STATE_WIRE_CLOCK_TEST") as writer:
        reader = TrackingStateReader(name="G3D_STATE_WIRE_CLOCK_TEST")
        try:
            writer.write("tracking")
            snapshot = reader.read()
        finally:
            reader.close()

    assert snapshot == ("tracking", timestamp)


def test_legacy_writers_do_not_create_a_second_clock_domain():
    source = open("tracker/shared_memory.py", encoding="utf-8").read()

    assert "from tracker.pose import monotonic_ms" in source
    assert "time.monotonic_ns" not in source
    assert source.count("ts = monotonic_ms()") == 2


def test_debug_monitor_uses_shared_wire_clock_for_freshness():
    source = open("tracker/debug_monitor.py", encoding="utf-8").read()

    assert "from tracker.pose import monotonic_ms" in source
    assert "now_ms = monotonic_ms()" in source
    assert "time.monotonic() * 1000" not in source


def test_calibration_bench_uses_shared_wire_clock_for_freshness():
    source = open("tracker/calibration_bench.py", encoding="utf-8").read()

    assert "from tracker.pose import monotonic_ms as shared_uptime_ms" in source
    assert "now_ms_fn = monotonic_ms or shared_uptime_ms" in source
    assert "lambda: int(time.monotonic() * 1000)" not in source


def test_native_overlay_computes_v2_and_state_age_from_gettickcount():
    source = open("overlay/overlay.cpp", encoding="utf-8").read()

    assert "DWORD nowMs = GetTickCount();" in source
    assert "nowMs - poseV2.publishTs" in source
    assert "nowMs - trackerStateTs" in source
