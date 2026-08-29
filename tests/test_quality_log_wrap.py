from pathlib import Path

from tracker.pose import elapsed_u32_ms


def test_camera_quality_log_elapsed_time_survives_uint32_rollover():
    last_log_ms = 0xFFFF_FC18  # 1000 ms before rollover
    current_ms = 0x0000_03E8   # 1000 ms after rollover

    assert elapsed_u32_ms(current_ms, last_log_ms) == 2000


def test_tracking_loop_uses_shared_wrap_safe_elapsed_helper():
    source = Path("tracker/main.py").read_text(encoding="utf-8")

    assert "from tracker.pose import FilteredPose, elapsed_u32_ms, monotonic_ms" in source
    assert (
        "if elapsed_u32_ms(capture_timestamp_ms, last_quality_log_ms) >= 2000:"
        in source
    )
    assert "capture_timestamp_ms - last_quality_log_ms" not in source
