from __future__ import annotations

from dataclasses import dataclass
import struct

from tracker.backend_status_shared_memory import (
    STATUS_MAGIC,
    STATUS_SIZE,
    TrackerBackendStatus,
    decode_tracker_backend_status,
    encode_tracker_backend_status,
    infer_configured_tracker_mode,
    status_from_tracker,
)


def test_backend_status_round_trip_preserves_recovery_telemetry():
    status = TrackerBackendStatus(
        configured_mode="auto",
        active_backend="cv2",
        failover_count=2,
        primary_retry_attempts=1,
        retry_in_ms=12_345,
        candidate_active=True,
        candidate_age_ms=789,
        candidate_probe_count=6,
        candidate_healthy_callbacks=2,
        backend_transition_id=4,
        pose_transition_active=True,
        pose_transition_preserves_position=True,
        last_failure="AsyncInferenceFailure: callback stalled",
        timestamp_ms=0xFFFF_FFF0,
    )

    decoded = decode_tracker_backend_status(
        encode_tracker_backend_status(status)
    )

    assert decoded == status
    assert len(encode_tracker_backend_status(status)) == STATUS_SIZE


def test_optional_values_survive_as_none():
    decoded = decode_tracker_backend_status(
        encode_tracker_backend_status(
            TrackerBackendStatus(
                configured_mode="mediapipe",
                active_backend="mediapipe",
                retry_in_ms=None,
                candidate_age_ms=None,
                timestamp_ms=123,
            )
        )
    )

    assert decoded is not None
    assert decoded.retry_in_ms is None
    assert decoded.candidate_age_ms is None


def test_invalid_magic_or_size_fails_closed():
    payload = bytearray(
        encode_tracker_backend_status(
            TrackerBackendStatus(
                configured_mode="auto",
                active_backend="mediapipe",
            )
        )
    )
    struct.pack_into("<I", payload, 0, STATUS_MAGIC ^ 1)

    assert decode_tracker_backend_status(bytes(payload)) is None
    assert decode_tracker_backend_status(bytes(payload[:-1])) is None


def test_failure_text_is_single_line_and_bounded():
    status = TrackerBackendStatus(
        configured_mode="auto",
        active_backend="cv2",
        last_failure="line one\n" + "é" * 300,
    )

    decoded = decode_tracker_backend_status(
        encode_tracker_backend_status(status)
    )

    assert decoded is not None
    assert "\n" not in decoded.last_failure
    assert len(decoded.last_failure.encode("utf-8")) <= 191


def test_status_freshness_is_wrap_safe():
    status = TrackerBackendStatus(
        configured_mode="auto",
        active_backend="cv2",
        timestamp_ms=0xFFFF_FFF0,
    )

    assert status.age_ms(0x20) == 48
    assert status.is_fresh(48, 0x20)
    assert not status.is_fresh(47, 0x20)


@dataclass
class _Snapshot:
    active_backend: str = "cv2"
    failover_count: int = 3
    primary_retry_attempts: int = 1
    retry_in_ms: int | None = 5000
    primary_candidate_active: bool = True
    primary_candidate_age_ms: int | None = 800
    primary_candidate_probe_count: int = 8
    primary_candidate_healthy_callbacks: int = 2
    backend_transition_id: int = 7
    pose_transition_active: bool = True
    pose_transition_preserves_position: bool = False
    last_failure: str = "callback stalled"


class _AutoTracker:
    active_backend = "cv2"

    def process_frame(self, frame, capture_timestamp_ms=None):
        return None

    def snapshot(self, timestamp_ms=None):
        assert timestamp_ms == 4242
        return _Snapshot()


def test_status_is_derived_from_auto_failover_snapshot():
    tracker = _AutoTracker()

    status = status_from_tracker(tracker, timestamp_ms=4242)

    assert status.configured_mode == "auto"
    assert status.active_backend == "cv2"
    assert status.failover_count == 3
    assert status.retry_in_ms == 5000
    assert status.candidate_active
    assert status.candidate_age_ms == 800
    assert status.candidate_probe_count == 8
    assert status.candidate_healthy_callbacks == 2
    assert status.last_failure == "callback stalled"
    assert status.timestamp_ms == 4242


def test_strict_backends_are_inferred_from_tracker_module():
    MediaPipeTracker = type(
        "FaceTracker",
        (),
        {
            "__module__": "tracker.face_tracker",
            "process_frame": lambda self, frame, capture_timestamp_ms=None: None,
        },
    )
    Cv2Tracker = type(
        "FaceTracker",
        (),
        {
            "__module__": "tracker.face_tracker_cv2",
            "process_frame": lambda self, frame, capture_timestamp_ms=None: None,
        },
    )

    assert infer_configured_tracker_mode(MediaPipeTracker()) == "mediapipe"
    assert infer_configured_tracker_mode(Cv2Tracker()) == "cv2"
    assert status_from_tracker(MediaPipeTracker()).active_backend == "mediapipe"
    assert status_from_tracker(Cv2Tracker()).active_backend == "cv2"
