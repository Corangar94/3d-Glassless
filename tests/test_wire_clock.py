import ctypes
import os

import pytest

from tracker import pose
from tracker import shared_memory
from tracker.shared_memory import (
    SharedMemoryReader,
    SharedMemoryWriter,
    TrackingStateReader,
    TrackingStateWriter,
)


UINT32_MASK = 0xFFFF_FFFF


def _uint32_distance(a: int, b: int) -> int:
    forward = (int(a) - int(b)) & UINT32_MASK
    backward = (int(b) - int(a)) & UINT32_MASK
    return min(forward, backward)


@pytest.mark.skipif(os.name != "nt", reason="Windows wire clock contract")
def test_wire_clock_matches_native_gettickcount_epoch():
    kernel32 = ctypes.windll.kernel32
    kernel32.GetTickCount.restype = ctypes.c_ulong

    before = int(kernel32.GetTickCount()) & UINT32_MASK
    published = pose.monotonic_ms()
    after = int(kernel32.GetTickCount()) & UINT32_MASK

    assert min(
        _uint32_distance(published, before),
        _uint32_distance(published, after),
    ) <= 10


def test_wire_clock_wraps_to_uint32(monkeypatch):
    monkeypatch.setattr(pose, "_wire_uptime_ms64", lambda: 0x1_0000_0017)

    assert pose.monotonic_ms() == 0x17


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


def test_native_overlay_computes_v2_and_state_age_from_gettickcount():
    source = open("overlay/overlay.cpp", encoding="utf-8").read()

    assert "DWORD nowMs = GetTickCount();" in source
    assert "nowMs - poseV2.publishTs" in source
    assert "nowMs - trackerStateTs" in source
