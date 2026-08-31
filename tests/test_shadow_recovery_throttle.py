from __future__ import annotations

from tracker.async_inference_watchdog import (
    AsyncInferenceFailure,
    AsyncInferenceWatchdog,
)
from tracker.backend_failover import (
    AutoFailoverFaceTracker,
    BackendFailoverPolicy,
)
from tracker.pose import HeadPosition


class _FailingPrimary:
    def __init__(self) -> None:
        self.closed = False

    def process_frame(self, _frame, capture_timestamp_ms=None):
        raise AsyncInferenceFailure("primary unhealthy")

    def close(self) -> None:
        self.closed = True


class _Fallback:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.closed = False

    def process_frame(self, _frame, capture_timestamp_ms=None):
        timestamp = int(capture_timestamp_ms or 1)
        self.calls.append(timestamp)
        return HeadPosition(
            x_cm=10.0,
            y_cm=0.0,
            z_cm=60.0,
            confidence=0.7,
            capture_timestamp_ms=timestamp,
        )

    def close(self) -> None:
        self.closed = True


class _AsyncCandidate:
    def __init__(self, *, pose_after_calls: int | None = 3) -> None:
        self.calls: list[int] = []
        self.closed = False
        self.pose_after_calls = pose_after_calls
        self._async_watchdog = AsyncInferenceWatchdog(
            max_consecutive_errors=3,
            stall_timeout_ms=5000,
        )

    def process_frame(self, _frame, capture_timestamp_ms=None):
        timestamp = int(capture_timestamp_ms or 1)
        self.calls.append(timestamp)
        self._async_watchdog.record_submission(timestamp)
        self._async_watchdog.record_callback(timestamp)
        if (
            self.pose_after_calls is None
            or len(self.calls) < self.pose_after_calls
        ):
            return None
        return HeadPosition(
            x_cm=1.0,
            y_cm=2.0,
            z_cm=61.0,
            confidence=0.95,
            capture_timestamp_ms=timestamp,
        )

    def close(self) -> None:
        self.closed = True


class _LegacyCandidate:
    def __init__(self) -> None:
        self.calls: list[int] = []
        self.closed = False

    def process_frame(self, _frame, capture_timestamp_ms=None):
        timestamp = int(capture_timestamp_ms or 1)
        self.calls.append(timestamp)
        if len(self.calls) < 2:
            return None
        return HeadPosition(
            x_cm=2.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=timestamp,
        )

    def close(self) -> None:
        self.closed = True


def _controller(candidate, policy):
    failed = _FailingPrimary()
    fallback = _Fallback()
    primary_instances = iter((failed, candidate))
    controller = AutoFailoverFaceTracker(
        primary_factory=lambda: next(primary_instances),
        fallback_factory=lambda: fallback,
        policy=policy,
        logger=lambda _message: None,
    )
    return controller, failed, fallback


def test_packaged_probe_cadence_keeps_fallback_continuous():
    candidate = _AsyncCandidate(pose_after_calls=3)
    controller, failed, fallback = _controller(
        candidate,
        BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
            shadow_probe_interval_ms=100,
            shadow_probe_timeout_ms=5000,
            minimum_healthy_callbacks=3,
        ),
    )

    controller.process_frame(object(), capture_timestamp_ms=1000)
    controller.process_frame(object(), capture_timestamp_ms=1001)
    controller.process_frame(object(), capture_timestamp_ms=1050)
    controller.process_frame(object(), capture_timestamp_ms=1101)
    controller.process_frame(object(), capture_timestamp_ms=1150)

    pending = controller.snapshot(1150)
    assert pending.active_backend == "cv2"
    assert pending.primary_candidate_active
    assert pending.primary_candidate_probe_count == 2
    assert pending.primary_candidate_healthy_callbacks == 2

    controller.process_frame(object(), capture_timestamp_ms=1201)

    assert controller.active_backend == "mediapipe"
    assert candidate.calls == [1001, 1101, 1201]
    assert fallback.calls == [1000, 1001, 1050, 1101, 1150, 1201]
    assert failed.closed
    assert fallback.closed


def test_three_healthy_callbacks_without_a_pose_do_not_promote():
    candidate = _AsyncCandidate(pose_after_calls=4)
    controller, _failed, fallback = _controller(
        candidate,
        BackendFailoverPolicy(
            retry_primary_after_ms=0,
            shadow_probe_interval_ms=100,
            shadow_probe_timeout_ms=5000,
            minimum_healthy_callbacks=3,
        ),
    )

    for timestamp in (1000, 1001, 1101, 1201):
        controller.process_frame(object(), capture_timestamp_ms=timestamp)

    snapshot = controller.snapshot(1201)
    assert snapshot.active_backend == "cv2"
    assert snapshot.primary_candidate_healthy_callbacks == 3
    assert not fallback.closed

    controller.process_frame(object(), capture_timestamp_ms=1301)

    assert controller.active_backend == "mediapipe"
    assert fallback.closed


def test_nonprogressing_candidate_is_discarded_at_hard_timeout():
    candidate = _AsyncCandidate(pose_after_calls=None)
    controller, _failed, fallback = _controller(
        candidate,
        BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
            shadow_probe_interval_ms=100,
            shadow_probe_timeout_ms=500,
            minimum_healthy_callbacks=3,
        ),
    )

    for timestamp in (1000, 1001, 1101, 1201, 1501):
        controller.process_frame(object(), capture_timestamp_ms=timestamp)

    snapshot = controller.snapshot(1501)
    assert snapshot.active_backend == "cv2"
    assert not snapshot.primary_candidate_active
    assert "TimeoutError" in snapshot.last_failure
    assert candidate.closed
    assert not fallback.closed
    assert candidate.calls == [1001, 1101, 1201]


def test_probe_cadence_is_wrap_safe():
    candidate = _AsyncCandidate(pose_after_calls=None)
    controller, _failed, _fallback = _controller(
        candidate,
        BackendFailoverPolicy(
            retry_primary_after_ms=0,
            shadow_probe_interval_ms=100,
            shadow_probe_timeout_ms=1000,
            minimum_healthy_callbacks=3,
        ),
    )

    controller.process_frame(object(), capture_timestamp_ms=0xFFFF_FFF0)
    controller.process_frame(object(), capture_timestamp_ms=0xFFFF_FFF1)
    controller.process_frame(object(), capture_timestamp_ms=0x0000_0020)
    controller.process_frame(object(), capture_timestamp_ms=0x0000_0060)

    assert candidate.calls == [0xFFFF_FFF1, 0x0000_0060]


def test_direct_controller_default_preserves_every_frame_legacy_probing():
    candidate = _LegacyCandidate()
    controller, _failed, fallback = _controller(
        candidate,
        BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
        ),
    )

    controller.process_frame(object(), capture_timestamp_ms=1000)
    controller.process_frame(object(), capture_timestamp_ms=1001)
    controller.process_frame(object(), capture_timestamp_ms=1002)

    assert BackendFailoverPolicy().shadow_probe_interval_ms == 0
    assert candidate.calls == [1001, 1002]
    assert controller.active_backend == "mediapipe"
    assert fallback.closed
