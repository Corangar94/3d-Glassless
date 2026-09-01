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
    _VERSION_END,
    _VERSION_SIZE,
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


def _recording_memmove(calls, *, fail_on_call: int | None = None):
    def memmove(destination, source, count):
        payload = bytes(source[:count])
        calls.append((int(destination), payload, int(count)))
        if fail_on_call is not None and len(calls) == fail_on_call:
            raise OSError("simulated shared-memory write failure")
        return destination

    return memmove


def _snapshot_from_commit_calls(calls) -> bytes:
    odd_destination, odd_marker, odd_size = calls[-4]
    prefix_destination, prefix, prefix_size = calls[-3]
    suffix_destination, suffix, suffix_size = calls[-2]
    even_destination, even_marker, even_size = calls[-1]

    assert odd_destination == 4096 + VERSION_OFFSET
    assert odd_size == _VERSION_SIZE
    assert struct.unpack("<I", odd_marker)[0] & 1
    assert prefix_destination == 4096
    assert prefix_size == VERSION_OFFSET
    assert suffix_destination == 4096 + _VERSION_END
    assert suffix_size == STRUCT_SIZE - _VERSION_END
    assert even_destination == 4096 + VERSION_OFFSET
    assert even_size == _VERSION_SIZE
    assert not (struct.unpack("<I", even_marker)[0] & 1)
    return prefix + even_marker + suffix


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


def test_valid_write_keeps_version_odd_until_both_body_slices_are_installed(
    monkeypatch,
):
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
        stereo_layout=1,
        panel_width_px=3840,
    )

    writer.write(settings)

    assert len(calls) == 4
    snapshot = _snapshot_from_commit_calls(calls)
    fields = struct.unpack(STRUCT_FORMAT, snapshot)
    assert fields[0] == pytest.approx(1.5)
    assert fields[5] == 2
    assert fields[13] == 1
    assert fields[VERSION_INDEX] == 6
    assert fields[16] == 1
    assert fields[18] == 3840
    assert writer._version == 6


def test_failed_validation_preserves_previous_committed_version(monkeypatch):
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
    committed = struct.unpack(
        STRUCT_FORMAT,
        _snapshot_from_commit_calls(successful_calls),
    )
    assert committed[VERSION_INDEX] == 8
    assert committed[0] == pytest.approx(2.0)


@pytest.mark.parametrize("fail_on_call", [2, 3, 4])
def test_partial_copy_or_commit_failure_never_advances_writer_version(
    monkeypatch,
    fail_on_call,
):
    writer = _bare_writer(version=6)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls, fail_on_call=fail_on_call),
    )

    with pytest.raises(OSError, match="simulated"):
        writer.write(OverlaySettings(strength_x=2.5))

    assert len(calls) == fail_on_call
    assert struct.unpack("<I", calls[0][1])[0] == 7
    assert writer._version == 6
    if fail_on_call < 4:
        assert all(
            not (
                destination == writer._view + VERSION_OFFSET
                and not (struct.unpack("<I", payload)[0] & 1)
            )
            for destination, payload, size in calls
            if size == _VERSION_SIZE
        )


def test_failed_copy_retries_the_same_odd_and_even_versions(monkeypatch):
    writer = _bare_writer(version=10)
    failed_calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(failed_calls, fail_on_call=3),
    )
    with pytest.raises(OSError):
        writer.write(OverlaySettings(strength_x=2.0))
    assert writer._version == 10

    retry_calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(retry_calls),
    )
    writer.write(OverlaySettings(strength_x=3.0))

    assert struct.unpack("<I", retry_calls[0][1])[0] == 11
    snapshot = _snapshot_from_commit_calls(retry_calls)
    fields = struct.unpack(STRUCT_FORMAT, snapshot)
    assert fields[VERSION_INDEX] == 12
    assert fields[0] == pytest.approx(3.0)
    assert writer._version == 12


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
    fields = struct.unpack(
        STRUCT_FORMAT,
        _snapshot_from_commit_calls(calls),
    )
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
