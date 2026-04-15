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
