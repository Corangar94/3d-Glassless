import pytest
from tracker.shared_memory import SharedMemoryWriter, SharedMemoryReader


def test_reader_returns_none_when_absent():
    """Reader returns None if no writer has created the segment."""
    reader = SharedMemoryReader("G3D_TEST_ABSENT_XYZ")
    assert reader.read() is None
    reader.close()


def test_reader_reads_written_values():
    """After writer writes (x, y, z), reader returns the same values."""
    name = "G3D_TEST_RW"
    with SharedMemoryWriter(name) as w:
        w.write(x=12.5, y=-3.0, z=77.2)
        with SharedMemoryReader(name) as r:
            result = r.read()
    assert result is not None
    x, y, z, ts = result
    assert abs(x - 12.5) < 0.001
    assert abs(y - (-3.0)) < 0.001
    assert abs(z - 77.2) < 0.001
    assert ts > 0


def test_reader_context_manager():
    """SharedMemoryReader works as a context manager."""
    name = "G3D_TEST_CTX"
    with SharedMemoryWriter(name):
        with SharedMemoryReader(name) as r:
            assert r.read() is not None


def test_reader_reattaches_after_writer_starts():
    """Reader constructed before writer should attach on next read() after writer starts."""
    name = "G3D_TEST_REATTACH"
    reader = SharedMemoryReader(name)
    assert reader.read() is None          # writer not yet running
    with SharedMemoryWriter(name) as w:
        w.write(x=1.0, y=2.0, z=3.0)
        result = reader.read()            # should now attach and return data
    assert result is not None
    x, y, z, _ = result
    assert abs(x - 1.0) < 0.001
    assert abs(y - 2.0) < 0.001
    assert abs(z - 3.0) < 0.001
    reader.close()


from tracker.debug_monitor import compute_shift_pct, _shift_tag, _is_stale


def test_compute_shift_pct_zero_head():
    """At (0, 0, 60) head position the shift is exactly 0%."""
    sx, sy = compute_shift_pct(0.0, 0.0, 60.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    assert sx == 0.0
    assert sy == 0.0


def test_compute_shift_pct_known_values():
    """Known inputs: headX=10, headZ=25, vd=30, sw=119.3, str=1.0 → ~4.575%."""
    # f = 30 / (25 + 30) = 0.5455
    # sx = abs(10 / 119.3) * 0.5455 * 1.0 * 100 ≈ 4.575%
    sx, sy = compute_shift_pct(10.0, 0.0, 25.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    assert abs(sx - 4.575) < 0.01
    assert sy == 0.0


def test_compute_shift_pct_uses_abs():
    """Negative headX gives same shift as positive headX (abs used)."""
    sx_pos, _ = compute_shift_pct(10.0, 0.0, 60.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    sx_neg, _ = compute_shift_pct(-10.0, 0.0, 60.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    assert abs(sx_pos - sx_neg) < 0.001


def test_shift_tag_good():
    assert _shift_tag(1.5, 1.0) == "GOOD"


def test_shift_tag_high():
    assert _shift_tag(3.0, 1.0) == "HIGH"


def test_shift_tag_danger():
    assert _shift_tag(5.0, 1.0) == "DANGER"


def test_is_stale_true():
    """Timestamp 600 ms old is stale (threshold 500 ms)."""
    now = 10_000
    ts = now - 600
    assert _is_stale(ts, now) is True


def test_is_stale_false():
    """Timestamp 100 ms old is not stale."""
    now = 10_000
    ts = now - 100
    assert _is_stale(ts, now) is False


def test_is_stale_wraps():
    """Timestamp wraps correctly around 32-bit boundary."""
    now = 100
    ts = (0xFFFF_FFFF - 200)  # 200 ms before overflow, so age = 301 ms
    assert _is_stale(ts, now, threshold_ms=250) is True
