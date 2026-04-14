# tests/test_shared_memory.py
import ctypes
import struct

from tracker.shared_memory import SharedMemoryWriter, STRUCT_FORMAT, STRUCT_SIZE


def test_struct_size():
    """Packed struct must be exactly 16 bytes (3 floats + 1 uint32)."""
    assert STRUCT_SIZE == 16


def test_struct_format_roundtrip():
    """Pack/unpack should preserve values with float precision."""
    data = struct.pack(STRUCT_FORMAT, 5.0, -3.5, 62.1, 12345)
    x, y, z, ts = struct.unpack(STRUCT_FORMAT, data)
    assert abs(x - 5.0) < 1e-5
    assert abs(y - (-3.5)) < 1e-5
    assert abs(z - 62.1) < 1e-4
    assert ts == 12345


def test_writer_write_and_read_back():
    """Write head data and read it back from the same shared memory."""
    writer = SharedMemoryWriter(name="G3D_TEST")
    try:
        writer.write(x=1.5, y=-2.0, z=55.0)
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "G3D_TEST")
        assert h, "Could not open shared memory for reading"
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, STRUCT_SIZE)
        assert view, "Could not map view of shared memory"
        raw = (ctypes.c_char * STRUCT_SIZE).from_address(view)
        x, y, z, _ = struct.unpack(STRUCT_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        assert abs(x - 1.5) < 1e-5
        assert abs(y - (-2.0)) < 1e-5
        assert abs(z - 55.0) < 1e-4
    finally:
        writer.close()


def test_writer_default_on_init():
    """Writer should initialise memory to safe default (0, 0, 60)."""
    writer = SharedMemoryWriter(name="G3D_DEFAULT_TEST")
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "G3D_DEFAULT_TEST")
        assert h, "Could not open shared memory for reading"
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, STRUCT_SIZE)
        assert view, "Could not map view of shared memory"
        raw = (ctypes.c_char * STRUCT_SIZE).from_address(view)
        x, y, z, _ = struct.unpack(STRUCT_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        assert x == 0.0 and y == 0.0 and z == 60.0
    finally:
        writer.close()


def test_writer_context_manager():
    """SharedMemoryWriter works as a context manager."""
    with SharedMemoryWriter(name="G3D_CTX_TEST") as writer:
        writer.write(x=3.0, y=1.0, z=70.0)
    # After __exit__, close() has been called — no assertions needed beyond no exception
