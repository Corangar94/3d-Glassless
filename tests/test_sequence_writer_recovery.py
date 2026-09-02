from __future__ import annotations

import ctypes
import struct
import threading

import pytest

from tracker import (
    backend_status_shared_memory,
    pose_shared_memory,
    shared_memory,
)
from tracker.backend_status_shared_memory import (
    STATUS_SIZE,
    TrackerBackendStatus,
    TrackerBackendStatusWriter,
    decode_tracker_backend_status,
)
from tracker.pose import FilteredPose
from tracker.pose_shared_memory import (
    POSE_V2_FORMAT,
    POSE_V2_SIZE,
    PoseStateWriter,
)
from tracker.shared_memory import (
    STRUCT_FORMAT,
    STRUCT_SIZE,
    SharedMemoryWriter,
)


def _install_first_copy_failure(monkeypatch):
    real_memmove = ctypes.memmove
    calls = 0

    def fail_first(destination, source, count):
        nonlocal calls
        calls += 1
        if calls == 1:
            # Model a genuinely torn body rather than a failure before the API
            # touches memory. The odd marker must keep readers from accepting it.
            partial = max(1, int(count) // 2)
            real_memmove(destination, source, partial)
            raise OSError("simulated payload copy failure")
        return real_memmove(destination, source, count)

    monkeypatch.setattr(ctypes, "memmove", fail_first)
    return real_memmove


def _legacy_writer_buffers():
    payload = ctypes.create_string_buffer(STRUCT_SIZE)
    sequence = ctypes.c_uint32(0)
    writer = SharedMemoryWriter.__new__(SharedMemoryWriter)
    writer._name = "G3D_TEST"
    writer._handle = None
    writer._view = ctypes.addressof(payload)
    writer._seq_handle = None
    writer._seq_view = ctypes.addressof(sequence)
    writer._committed_sequence = 0
    writer._write_lock = threading.RLock()
    return writer, payload, sequence


def _pose_writer_buffers():
    payload = ctypes.create_string_buffer(POSE_V2_SIZE)
    sequence = ctypes.c_uint32(0)
    writer = PoseStateWriter.__new__(PoseStateWriter)
    writer._name = "G3D_POSE_TEST"
    writer._handle = None
    writer._view = ctypes.addressof(payload)
    writer._seq_handle = None
    writer._seq_view = ctypes.addressof(sequence)
    writer._committed_sequence = 0
    writer._write_lock = threading.RLock()
    return writer, payload, sequence


def _backend_writer_buffers():
    payload = ctypes.create_string_buffer(STATUS_SIZE)
    sequence = ctypes.c_uint32(0)
    writer = TrackerBackendStatusWriter.__new__(
        TrackerBackendStatusWriter
    )
    writer._name = "G3D_BACKEND_TEST"
    writer._k32 = None
    writer._handle = None
    writer._view = ctypes.addressof(payload)
    writer._seq_handle = None
    writer._seq_view = ctypes.addressof(sequence)
    writer._committed_sequence = 0
    writer._write_lock = threading.RLock()
    return writer, payload, sequence


def test_legacy_pose_writer_recovers_even_sequence_after_copy_failure(
    monkeypatch,
):
    writer, payload, sequence = _legacy_writer_buffers()
    monkeypatch.setattr(shared_memory, "monotonic_ms", lambda: 1234)
    real_memmove = _install_first_copy_failure(monkeypatch)

    with pytest.raises(OSError, match="payload copy failure"):
        writer.write(1.0, 2.0, 60.0)

    assert sequence.value == 1
    assert writer._committed_sequence == 0

    monkeypatch.setattr(ctypes, "memmove", real_memmove)
    writer.write(4.0, 5.0, 70.0)

    assert sequence.value == 2
    assert writer._committed_sequence == 2
    assert struct.unpack(STRUCT_FORMAT, payload.raw) == pytest.approx(
        (4.0, 5.0, 70.0, 1234)
    )

    writer.write(6.0, 7.0, 80.0)
    assert sequence.value == 4
    assert writer._committed_sequence == 4


def test_pose_v2_writer_recovers_even_sequence_after_copy_failure(
    monkeypatch,
):
    writer, payload, sequence = _pose_writer_buffers()
    real_memmove = _install_first_copy_failure(monkeypatch)
    failed_pose = FilteredPose(
        x_cm=1.0,
        y_cm=2.0,
        z_cm=60.0,
        confidence=0.8,
        capture_timestamp_ms=1000,
        publish_timestamp_ms=1010,
    )

    with pytest.raises(OSError, match="payload copy failure"):
        writer.write(failed_pose)

    assert sequence.value == 1
    assert writer._committed_sequence == 0

    monkeypatch.setattr(ctypes, "memmove", real_memmove)
    recovered_pose = FilteredPose(
        x_cm=4.0,
        y_cm=5.0,
        z_cm=70.0,
        vx_cm_s=1.0,
        confidence=0.9,
        capture_timestamp_ms=1100,
        publish_timestamp_ms=1110,
    )
    writer.write(recovered_pose)

    fields = struct.unpack(POSE_V2_FORMAT, payload.raw)
    assert sequence.value == 2
    assert writer._committed_sequence == 2
    assert fields[2:5] == pytest.approx((4.0, 5.0, 70.0))
    assert fields[5] == pytest.approx(1.0)
    assert fields[12] == 1100
    assert fields[13] == 1110

    writer.write(recovered_pose)
    assert sequence.value == 4
    assert writer._committed_sequence == 4


def test_backend_status_writer_recovers_even_sequence_after_copy_failure(
    monkeypatch,
):
    writer, payload, sequence = _backend_writer_buffers()
    real_memmove = _install_first_copy_failure(monkeypatch)
    failed_status = TrackerBackendStatus(
        configured_mode="auto",
        active_backend="cv2",
        failover_count=1,
        timestamp_ms=1000,
    )

    with pytest.raises(OSError, match="payload copy failure"):
        writer.write(failed_status)

    assert sequence.value == 1
    assert writer._committed_sequence == 0

    monkeypatch.setattr(ctypes, "memmove", real_memmove)
    recovered_status = TrackerBackendStatus(
        configured_mode="auto",
        active_backend="mediapipe",
        failover_count=2,
        candidate_probe_count=7,
        timestamp_ms=1100,
    )
    writer.write(recovered_status)

    decoded = decode_tracker_backend_status(payload.raw)
    assert sequence.value == 2
    assert writer._committed_sequence == 2
    assert decoded is not None
    assert decoded.active_backend == "mediapipe"
    assert decoded.failover_count == 2
    assert decoded.candidate_probe_count == 7
    assert decoded.timestamp_ms == 1100

    writer.write(recovered_status)
    assert sequence.value == 4
    assert writer._committed_sequence == 4


def test_no_sequence_mapping_keeps_legacy_single_copy_path(monkeypatch):
    writer, payload, _sequence = _legacy_writer_buffers()
    writer._seq_view = None
    monkeypatch.setattr(shared_memory, "monotonic_ms", lambda: 2222)

    writer.write(8.0, 9.0, 90.0)

    assert writer._committed_sequence == 0
    assert struct.unpack(STRUCT_FORMAT, payload.raw) == pytest.approx(
        (8.0, 9.0, 90.0, 2222)
    )


def test_invalid_pose_v2_payload_fails_before_odd_marker(monkeypatch):
    writer, _payload, sequence = _pose_writer_buffers()

    def unexpected_memmove(*_args):
        raise AssertionError("invalid payload reached shared memory")

    monkeypatch.setattr(pose_shared_memory.ctypes, "memmove", unexpected_memmove)

    with pytest.raises(ValueError, match="non-finite"):
        writer.write(
            FilteredPose(
                x_cm=float("nan"),
                y_cm=0.0,
                z_cm=60.0,
            )
        )

    assert sequence.value == 0
    assert writer._committed_sequence == 0


def test_invalid_backend_status_is_encoded_before_odd_marker(monkeypatch):
    writer, _payload, sequence = _backend_writer_buffers()
    original_encoder = backend_status_shared_memory.encode_tracker_backend_status

    def fail_encoding(_status):
        raise ValueError("bad backend status")

    monkeypatch.setattr(
        backend_status_shared_memory,
        "encode_tracker_backend_status",
        fail_encoding,
    )

    with pytest.raises(ValueError, match="bad backend status"):
        writer.write(
            TrackerBackendStatus(
                configured_mode="auto",
                active_backend="mediapipe",
            )
        )

    assert sequence.value == 0
    assert writer._committed_sequence == 0
    monkeypatch.setattr(
        backend_status_shared_memory,
        "encode_tracker_backend_status",
        original_encoder,
    )
