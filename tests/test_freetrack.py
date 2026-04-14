# tests/test_freetrack.py
import ctypes
import struct

from tracker.freetrack import FreetracWriter, FREETRACK_FORMAT, FREETRACK_SIZE


def test_struct_size():
    """FreeTrack struct must be exactly 92 bytes."""
    assert FREETRACK_SIZE == 92


def test_writer_default_on_init():
    """Writer should initialise X=0, Y=0, Z=60 and DataID=0 on construction."""
    with FreetracWriter(name="FT_TEST_DEFAULT") as writer:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "FT_TEST_DEFAULT")
        assert h, "Could not open shared memory"
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, FREETRACK_SIZE)
        assert view, "Could not map view"
        raw = (ctypes.c_char * FREETRACK_SIZE).from_address(view)
        fields = struct.unpack(FREETRACK_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        # fields: DataID, CamW, CamH, Yaw, Pitch, Roll, X, Y, Z, RawYaw…RawZ, 8 pts
        data_id, _, _, yaw, pitch, roll, x, y, z = fields[:9]
        assert data_id == 0
        assert x == 0.0 and y == 0.0 and z == 60.0
        assert yaw == 0.0 and pitch == 0.0 and roll == 0.0


def test_writer_write_advances_data_id():
    """DataID must increment with each write so readers can detect new data."""
    with FreetracWriter(name="FT_TEST_DATAID") as writer:
        writer.write(x=1.0, y=2.0, z=50.0)
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "FT_TEST_DATAID")
        assert h
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, FREETRACK_SIZE)
        assert view
        raw = (ctypes.c_char * FREETRACK_SIZE).from_address(view)
        fields = struct.unpack(FREETRACK_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        data_id = fields[0]
        assert data_id == 1  # first write after init increments to 1


def test_writer_write_and_read_back():
    """Written X/Y/Z values must be readable via a second mapping."""
    with FreetracWriter(name="FT_TEST_RW") as writer:
        writer.write(x=3.5, y=-1.2, z=58.0)
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "FT_TEST_RW")
        assert h
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, FREETRACK_SIZE)
        assert view
        raw = (ctypes.c_char * FREETRACK_SIZE).from_address(view)
        fields = struct.unpack(FREETRACK_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        _, _, _, _, _, _, x, y, z = fields[:9]
        assert abs(x - 3.5) < 1e-5
        assert abs(y - (-1.2)) < 1e-5
        assert abs(z - 58.0) < 1e-4
