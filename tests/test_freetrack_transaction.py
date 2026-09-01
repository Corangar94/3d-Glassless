from __future__ import annotations

import struct
import threading

import pytest

from tracker import freetrack
from tracker.freetrack import (
    FREETRACK_FORMAT,
    FREETRACK_SIZE,
    FreetracWriter,
    _DATA_ID_SIZE,
    _pack_freetrack_packet,
)


def _bare_writer(*, sequence: int = 0) -> FreetracWriter:
    writer = FreetracWriter.__new__(FreetracWriter)
    writer._name = "FT_TEST_TRANSACTION"
    writer._seq = sequence
    writer._handle = None
    writer._view = 4096
    writer._write_lock = threading.RLock()
    return writer


def _recording_memmove(calls, *, fail_on_call: int | None = None):
    def memmove(destination, source, count):
        payload = bytes(source[:count])
        calls.append((int(destination), payload, int(count)))
        if fail_on_call is not None and len(calls) == fail_on_call:
            raise OSError("simulated shared-memory write failure")
        return destination

    return memmove


def _packet_from_calls(calls) -> bytes:
    body_destination, body, body_size = calls[-2]
    id_destination, data_id, id_size = calls[-1]
    assert body_destination == 4096 + _DATA_ID_SIZE
    assert body_size == FREETRACK_SIZE - _DATA_ID_SIZE
    assert id_destination == 4096
    assert id_size == _DATA_ID_SIZE
    return data_id + body


def test_packet_layout_remains_exactly_free_track_compatible():
    packet = _pack_freetrack_packet(7, 1.25, -2.5, 55.0)
    fields = struct.unpack(FREETRACK_FORMAT, packet)

    assert len(packet) == FREETRACK_SIZE == 92
    assert fields[0] == 7
    assert fields[1:6] == (0, 0, 0.0, 0.0, 0.0)
    assert fields[6] == pytest.approx(1.25)
    assert fields[7] == pytest.approx(-2.5)
    assert fields[8] == pytest.approx(55.0)
    assert fields[9:] == (0.0,) * 14


def test_valid_write_installs_body_before_publishing_data_id(monkeypatch):
    writer = _bare_writer(sequence=7)
    calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    writer.write(x=1.25, y=-2.5, z=55.0)

    assert len(calls) == 2
    packet = _packet_from_calls(calls)
    fields = struct.unpack(FREETRACK_FORMAT, packet)
    assert fields[0] == 8
    assert fields[6:9] == pytest.approx((1.25, -2.5, 55.0))
    assert writer._seq == 8


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x", float("nan")),
        ("x", float("inf")),
        ("y", float("-inf")),
        ("z", float("nan")),
    ],
)
def test_nonfinite_pose_is_rejected_before_any_memory_write(
    monkeypatch,
    field,
    value,
):
    writer = _bare_writer(sequence=11)
    calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(calls),
    )
    values = {"x": 1.0, "y": 2.0, "z": 60.0}
    values[field] = value

    with pytest.raises(ValueError, match=f"{field} must be a finite float"):
        writer.write(**values)

    assert calls == []
    assert writer._seq == 11


def test_float32_overflow_is_rejected_before_any_memory_write(monkeypatch):
    writer = _bare_writer(sequence=12)
    calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    with pytest.raises(OverflowError):
        writer.write(x=1e300, y=0.0, z=60.0)

    assert calls == []
    assert writer._seq == 12


def test_body_write_failure_does_not_advance_logical_sequence(monkeypatch):
    writer = _bare_writer(sequence=20)
    calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(calls, fail_on_call=1),
    )

    with pytest.raises(OSError, match="simulated"):
        writer.write(x=1.0, y=2.0, z=60.0)

    assert len(calls) == 1
    assert calls[0][0] == writer._view + _DATA_ID_SIZE
    assert writer._seq == 20


def test_data_id_write_failure_keeps_sequence_retryable(monkeypatch):
    writer = _bare_writer(sequence=20)
    failed_calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(failed_calls, fail_on_call=2),
    )

    with pytest.raises(OSError, match="simulated"):
        writer.write(x=1.0, y=2.0, z=60.0)

    assert len(failed_calls) == 2
    assert writer._seq == 20

    retry_calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(retry_calls),
    )
    writer.write(x=1.5, y=2.5, z=59.0)

    fields = struct.unpack(FREETRACK_FORMAT, _packet_from_calls(retry_calls))
    assert fields[0] == 21
    assert fields[6:9] == pytest.approx((1.5, 2.5, 59.0))
    assert writer._seq == 21


def test_sequence_rollover_publishes_zero_only_after_body(monkeypatch):
    writer = _bare_writer(sequence=0xFFFF_FFFF)
    calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    writer.write(x=0.0, y=0.0, z=60.0)

    fields = struct.unpack(FREETRACK_FORMAT, _packet_from_calls(calls))
    assert fields[0] == 0
    assert writer._seq == 0


def test_write_after_close_does_not_advance_sequence(monkeypatch):
    writer = _bare_writer(sequence=30)
    writer._view = None
    calls = []
    monkeypatch.setattr(
        freetrack.ctypes,
        "memmove",
        _recording_memmove(calls),
    )

    with pytest.raises(RuntimeError, match="after close"):
        writer.write(x=0.0, y=0.0, z=60.0)

    assert calls == []
    assert writer._seq == 30
