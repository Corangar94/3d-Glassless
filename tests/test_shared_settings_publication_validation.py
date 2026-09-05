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
    VERSION_INDEX,
    VERSION_OFFSET,
    _VERSION_END,
    _VERSION_SIZE,
)


def _bare_writer(*, version: int = 8) -> SharedSettingsWriter:
    writer = SharedSettingsWriter.__new__(SharedSettingsWriter)
    writer._name = "STRICT_SETTINGS_PUBLICATION_TEST"
    writer._handle = None
    writer._view = 4096
    writer._mutex_handle = None
    writer._version = version
    writer._write_lock = threading.Lock()
    return writer


def _recording_memmove(calls):
    def memmove(destination, source, count):
        calls.append((int(destination), bytes(source[:count]), int(count)))
        return destination

    return memmove


@pytest.mark.parametrize(
    "changes, field_name",
    [
        ({"strength_x": True}, "strength_x"),
        ({"strength_x": "1.0"}, "strength_x"),
        ({"depth_curve": 1.0}, "depth_curve"),
        ({"depth_curve": 3}, "depth_curve"),
        ({"display_backend": 3}, "display_backend"),
        ({"depth_mode": 4}, "depth_mode"),
        ({"stereo_layout": 2}, "stereo_layout"),
        ({"eye_order": -1}, "eye_order"),
        ({"panel_width_px": 1920.0}, "panel_width_px"),
        ({"tracking_mode": 2}, "tracking_mode"),
    ],
)
def test_invalid_wire_value_is_rejected_before_mapping_becomes_odd(
    monkeypatch,
    changes,
    field_name,
):
    writer = _bare_writer(version=8)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    with pytest.raises(ValueError, match=field_name):
        writer.write(replace(OverlaySettings(), **changes))

    assert calls == []
    assert writer._version == 8


def test_valid_auto_depth_mode_remains_part_of_the_wire_domain(monkeypatch):
    writer = _bare_writer(version=8)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    writer.write(OverlaySettings(depth_mode=3))

    assert len(calls) == 4
    prefix = calls[-3][1]
    suffix = calls[-2][1]
    committed_marker = calls[-1][1]
    payload = prefix + committed_marker + suffix
    fields = struct.unpack(STRUCT_FORMAT, payload)
    assert calls[0][0] == writer._view + VERSION_OFFSET
    assert calls[0][2] == _VERSION_SIZE
    assert calls[-2][0] == writer._view + _VERSION_END
    assert fields[14] == 3
    assert fields[VERSION_INDEX] == 10
    assert writer._version == 10


def test_rejected_write_cannot_replace_a_prior_committed_snapshot(monkeypatch):
    writer = _bare_writer(version=20)
    calls = []
    monkeypatch.setattr(
        shared_settings.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    writer.write(OverlaySettings(strength_x=1.75, tracking_mode=1))
    committed_calls = list(calls)
    assert writer._version == 22

    with pytest.raises(ValueError, match="tracking_mode"):
        writer.write(OverlaySettings(tracking_mode=True))

    assert calls == committed_calls
    assert writer._version == 22
