from __future__ import annotations

from contextlib import contextmanager
import ctypes
import struct
import threading

import pytest

from tracker import shared_settings
from tracker.shared_settings import (
    OverlaySettings,
    SharedSettingsReader,
    SharedSettingsWriter,
    STRUCT_FORMAT,
    STRUCT_SIZE,
    VERSION_INDEX,
    VERSION_OFFSET,
    _WAIT_ABANDONED,
    _WAIT_OBJECT_0,
    _WAIT_TIMEOUT,
    _acquired_write_mutex,
    _mapping_version,
    _normalized_committed_version,
    _pack_settings,
)


def _buffer(payload: bytes | None = None):
    memory = ctypes.create_string_buffer(STRUCT_SIZE)
    if payload is not None:
        ctypes.memmove(ctypes.addressof(memory), payload, STRUCT_SIZE)
    return memory, ctypes.addressof(memory)


def _coordinated_writer(view: int, *, local_version: int = 0):
    writer = SharedSettingsWriter.__new__(SharedSettingsWriter)
    writer._name = "G3D_SETTINGS_COORDINATION_TEST"
    writer._handle = None
    writer._view = view
    writer._mutex_handle = 1234
    writer._version = local_version
    writer._write_lock = threading.Lock()
    return writer


def _fields(view: int):
    raw = bytes((ctypes.c_char * STRUCT_SIZE).from_address(view))
    return struct.unpack(STRUCT_FORMAT, raw)


def _install_python_mutex(monkeypatch):
    mutex = threading.Lock()

    @contextmanager
    def acquired(_handle, _name):
        with mutex:
            yield

    monkeypatch.setattr(
        shared_settings,
        "_acquired_write_mutex",
        acquired,
    )
    return mutex


def test_normalized_version_advances_past_abandoned_odd_marker():
    assert _normalized_committed_version(0) == 0
    assert _normalized_committed_version(6) == 6
    assert _normalized_committed_version(7) == 8
    assert _normalized_committed_version(0xFFFF_FFFF) == 0


def test_new_zero_filled_mapping_receives_one_complete_default_snapshot():
    memory, view = _buffer()
    writer = _coordinated_writer(view)

    writer._initialize_mapping_locked(view)

    fields = _fields(view)
    assert fields[VERSION_INDEX] == 2
    assert fields[0] == pytest.approx(OverlaySettings().strength_x)
    assert fields[2] == pytest.approx(OverlaySettings().virtual_depth_cm)
    assert fields[14] == OverlaySettings().depth_mode
    assert writer._version == 2
    assert memory.raw  # keep the backing buffer live through assertions


def test_attaching_writer_preserves_existing_committed_snapshot():
    original = OverlaySettings(strength_x=2.25, smoothing_alpha=0.31)
    payload = _pack_settings(original, 42)
    memory, view = _buffer(payload)
    before = bytes(memory)
    writer = _coordinated_writer(view)

    writer._initialize_mapping_locked(view)

    assert bytes(memory) == before
    assert writer._version == 42
    fields = _fields(view)
    assert fields[0] == pytest.approx(2.25)
    assert fields[11] == pytest.approx(0.31)


def test_version_zero_with_nonzero_payload_is_preserved_as_rollover_commit():
    payload = _pack_settings(
        OverlaySettings(strength_x=1.75, smoothing_alpha=0.22),
        0,
    )
    memory, view = _buffer(payload)
    before = bytes(memory)
    writer = _coordinated_writer(view, local_version=99)

    writer._initialize_mapping_locked(view)

    assert bytes(memory) == before
    assert writer._version == 0
    assert _mapping_version(view) == 0


def test_abandoned_odd_mapping_is_replaced_by_complete_default_commit():
    payload = bytearray(
        _pack_settings(OverlaySettings(strength_x=9.0), 6)
    )
    struct.pack_into("<I", payload, VERSION_OFFSET, 7)
    memory, view = _buffer(bytes(payload))
    writer = _coordinated_writer(view)

    writer._initialize_mapping_locked(view)

    fields = _fields(view)
    assert fields[VERSION_INDEX] == 10
    assert fields[0] == pytest.approx(OverlaySettings().strength_x)
    assert writer._version == 10


def test_two_writer_instances_derive_versions_from_shared_mapping(
    monkeypatch,
):
    _install_python_mutex(monkeypatch)
    memory, view = _buffer(
        _pack_settings(OverlaySettings(strength_x=1.0), 4)
    )
    first = _coordinated_writer(view, local_version=100)
    second = _coordinated_writer(view, local_version=0)

    first.write(OverlaySettings(strength_x=2.0, smoothing_alpha=0.20))
    assert _mapping_version(view) == 6
    second.write(OverlaySettings(strength_x=3.0, smoothing_alpha=0.30))

    fields = _fields(view)
    assert fields[VERSION_INDEX] == 8
    assert fields[0] == pytest.approx(3.0)
    assert fields[11] == pytest.approx(0.30)
    assert first._version == 6
    assert second._version == 8
    assert memory.raw


def test_concurrent_writers_publish_unique_monotonic_even_versions(
    monkeypatch,
):
    _install_python_mutex(monkeypatch)
    memory, view = _buffer(_pack_settings(OverlaySettings(), 2))
    first = _coordinated_writer(view)
    second = _coordinated_writer(view)
    committed: list[int] = []
    committed_lock = threading.Lock()

    def write_many(writer, base):
        for offset in range(20):
            writer.write(
                OverlaySettings(
                    strength_x=base + offset / 100.0,
                    smoothing_alpha=0.10 + offset / 1000.0,
                )
            )
            with committed_lock:
                committed.append(_mapping_version(view))

    threads = [
        threading.Thread(target=write_many, args=(first, 1.0)),
        threading.Thread(target=write_many, args=(second, 2.0)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3.0)

    assert all(not thread.is_alive() for thread in threads)
    assert _mapping_version(view) == 82
    assert all(version % 2 == 0 for version in committed)
    assert len(set(committed)) == 40
    assert memory.raw


def test_actual_write_recovers_from_abandoned_odd_marker(monkeypatch):
    _install_python_mutex(monkeypatch)
    payload = bytearray(_pack_settings(OverlaySettings(), 6))
    struct.pack_into("<I", payload, VERSION_OFFSET, 7)
    memory, view = _buffer(bytes(payload))
    writer = _coordinated_writer(view, local_version=6)

    writer.write(OverlaySettings(strength_x=4.0))

    fields = _fields(view)
    assert fields[VERSION_INDEX] == 10
    assert fields[0] == pytest.approx(4.0)
    assert writer._version == 10


class _KernelMutex:
    def __init__(self, wait_result):
        self.wait_result = wait_result
        self.wait_calls = []
        self.release_calls = []

    def WaitForSingleObject(self, handle, timeout):
        self.wait_calls.append((handle, timeout))
        return self.wait_result

    def ReleaseMutex(self, handle):
        self.release_calls.append(handle)
        return 1


def test_mutex_accepts_abandoned_ownership_and_releases(monkeypatch):
    kernel = _KernelMutex(_WAIT_ABANDONED)
    monkeypatch.setattr(shared_settings, "_k32", kernel)

    with _acquired_write_mutex(12, "TEST"):
        pass

    assert kernel.wait_calls == [(12, shared_settings._WRITE_MUTEX_TIMEOUT_MS)]
    assert kernel.release_calls == [12]


def test_mutex_timeout_fails_before_publication(monkeypatch):
    kernel = _KernelMutex(_WAIT_TIMEOUT)
    monkeypatch.setattr(shared_settings, "_k32", kernel)

    with pytest.raises(TimeoutError, match="coordinating"):
        with _acquired_write_mutex(12, "TEST"):
            raise AssertionError("body must not run")

    assert kernel.release_calls == []


def test_mutex_releases_when_publication_body_raises(monkeypatch):
    kernel = _KernelMutex(_WAIT_OBJECT_0)
    monkeypatch.setattr(shared_settings, "_k32", kernel)

    with pytest.raises(RuntimeError, match="publication failed"):
        with _acquired_write_mutex(12, "TEST"):
            raise RuntimeError("publication failed")

    assert kernel.release_calls == [12]


class _DetachKernel:
    def __init__(self):
        self.unmapped = []
        self.closed = []

    def UnmapViewOfFile(self, view):
        self.unmapped.append(view)
        return 1

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def test_reader_detach_unmaps_stale_view_and_closes_handle(monkeypatch):
    kernel = _DetachKernel()
    monkeypatch.setattr(shared_settings, "_k32", kernel)
    reader = SharedSettingsReader.__new__(SharedSettingsReader)
    reader._name = "TEST"
    reader._view = 100
    reader._handle = 200

    reader._detach()
    reader._detach()

    assert kernel.unmapped == [100]
    assert kernel.closed == [200]
    assert reader._view is None
    assert reader._handle is None
