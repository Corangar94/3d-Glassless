from __future__ import annotations

from dataclasses import replace
import struct
import threading

import pytest

from tracker import shared_settings
from tracker.shared_settings import (
    OverlaySettings,
    SharedSettingsWriter,
    STRUCT_FORMAT,
    STRUCT_SIZE,
    VERSION_INDEX,
    VERSION_OFFSET,
    _pack_settings,
    _settings_versions,
)


def _bare_writer(*, version: int = 0) -> SharedSettingsWriter:
    writer = SharedSettingsWriter.__new__(SharedSettingsWriter)
    writer._name = "TEST"
    writer._handle = None
    writer._view = 4096
    writer._version = version
    writer._write_lock = threading.Lock()
    return writer


def _recording_memmove(calls):
    def memmove(destination, source, count):
        payload = bytes(source[:count])
        calls.append((int(destination), payload, int(count)))
        return destination

    return memmove


def test_invalid_float_is_rejected_before_mapping_becomes_odd(monkeypatch):
    writer = _bare_writer(version=8)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    with pytest.raises(ValueError, match="strength_x must be a finite float"):
        writer.write(
            replace(
                OverlaySettings(strength_x=2.0),
                strength_x=float("nan"),
            )
        )

    assert calls == []
    assert writer._version == 8


def test_invalid_uint32_is_rejected_before_mapping_becomes_odd(monkeypatch):
    writer = _bare_writer(version=10)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    with pytest.raises(ValueError, match="depth_curve"):
        writer.write(OverlaySettings(depth_curve=-1))

    assert calls == []
    assert writer._version == 10


def test_float32_overflow_is_rejected_before_publication(monkeypatch):
    writer = _bare_writer(version=12)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    with pytest.raises(OverflowError):
        writer.write(OverlaySettings(strength_x=1e300))

    assert calls == []
    assert writer._version == 12


def test_valid_write_marks_odd_then_installs_complete_even_snapshot(monkeypatch):
    writer = _bare_writer(version=4)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )
    settings = OverlaySettings(
        strength_x=1.5,
        depth_curve=2,
        display_backend=1,
    )

    writer.write(settings)

    assert len(calls) == 2
    marker_destination, marker_payload, marker_size = calls[0]
    assert marker_destination == writer._view + VERSION_OFFSET
    assert marker_size == 4
    assert struct.unpack("<I", marker_payload)[0] == 5

    snapshot_destination, snapshot, snapshot_size = calls[1]
    assert snapshot_destination == writer._view
    assert snapshot_size == STRUCT_SIZE
    fields = struct.unpack(STRUCT_FORMAT, snapshot)
    assert fields[0] == pytest.approx(1.5)
    assert fields[5] == 2
    assert fields[13] == 1
    assert fields[VERSION_INDEX] == 6
    assert writer._version == 6


def test_failed_update_preserves_previous_committed_version(monkeypatch):
    writer = _bare_writer(version=6)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    writer.write(OverlaySettings(strength_x=2.0))
    assert writer._version == 8
    successful_calls = list(calls)

    with pytest.raises(ValueError):
        writer.write(OverlaySettings(ipd_mm=float("inf")))

    assert calls == successful_calls
    assert writer._version == 8
    committed = struct.unpack(STRUCT_FORMAT, calls[-1][1])
    assert committed[VERSION_INDEX] == 8
    assert committed[0] == pytest.approx(2.0)


def test_version_rollover_publishes_maximum_odd_then_zero_even(monkeypatch):
    writer = _bare_writer(version=0xFFFF_FFFE)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    writer.write(OverlaySettings())

    assert struct.unpack("<I", calls[0][1])[0] == 0xFFFF_FFFF
    fields = struct.unpack(STRUCT_FORMAT, calls[1][1])
    assert fields[VERSION_INDEX] == 0
    assert writer._version == 0


def test_version_helper_always_returns_odd_then_even():
    for current in (0, 2, 100, 0xFFFF_FFFE):
        writing, committed = _settings_versions(current)
        assert writing & 1
        assert not (committed & 1)
        assert committed == (writing + 1) & 0xFFFF_FFFE


def test_pack_settings_is_complete_and_side_effect_free():
    settings = OverlaySettings(
        strength_x=1.25,
        panel_width_px=3840,
        panel_height_px=1080,
    )

    payload = _pack_settings(settings, 42)
    fields = struct.unpack(STRUCT_FORMAT, payload)

    assert len(payload) == STRUCT_SIZE
    assert fields[0] == pytest.approx(1.25)
    assert fields[18] == 3840
    assert fields[19] == 1080
    assert fields[VERSION_INDEX] == 42
