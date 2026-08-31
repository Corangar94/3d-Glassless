from __future__ import annotations

from collections import deque

import pytest

from tracker.async_inference_watchdog import AsyncInferenceFailure
from tracker.backend_failover import (
    AutoFailoverFaceTracker,
    BackendFailoverPolicy,
)


class FakeTracker:
    def __init__(
        self,
        name: str,
        *results: object,
        ready_after: int | None = None,
    ) -> None:
        self.name = name
        self.results = deque(results)
        self.ready_after = ready_after
        self.calls: list[tuple[object, int | None]] = []
        self.calibrations: list[dict[str, float]] = []
        self.reset_count = 0
        self.close_count = 0

    def process_frame(self, frame, capture_timestamp_ms=None):
        self.calls.append((frame, capture_timestamp_ms))
        result = self.results.popleft() if self.results else None
        if isinstance(result, BaseException):
            raise result
        return result

    def ready_for_promotion(self) -> bool:
        return (
            self.ready_after is not None
            and len(self.calls) >= self.ready_after
        )

    def set_calibration(self, **values: float) -> None:
        self.calibrations.append(dict(values))

    def reset_session(self) -> None:
        self.reset_count += 1

    def close(self) -> None:
        self.close_count += 1


def test_healthy_primary_is_used_without_constructing_fallback():
    primary = FakeTracker("mediapipe", "primary pose")
    fallback_calls = 0

    def fallback_factory():
        nonlocal fallback_calls
        fallback_calls += 1
        return FakeTracker("cv2")

    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=fallback_factory,
        logger=lambda _message: None,
    )
    frame = object()

    assert tracker.process_frame(frame, 1000) == "primary pose"
    assert tracker.active_backend == "mediapipe"
    assert primary.calls == [(frame, 1000)]
    assert fallback_calls == 0


def test_watchdog_failure_switches_to_fallback_on_the_same_frame():
    primary = FakeTracker(
        "mediapipe",
        AsyncInferenceFailure("callback stalled"),
    )
    fallback = FakeTracker("cv2", "fallback pose")
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=lambda: fallback,
        logger=lambda _message: None,
    )
    frame = object()

    assert tracker.process_frame(frame, 1234) == "fallback pose"
    snapshot = tracker.snapshot(1234)
    assert snapshot.active_backend == "cv2"
    assert snapshot.failover_count == 1
    assert "callback stalled" in snapshot.last_failure
    assert primary.close_count == 1
    assert fallback.calls == [(frame, 1234)]


def test_non_health_primary_exception_propagates_without_fallback():
    primary = FakeTracker("mediapipe", TypeError("tracker bug"))
    fallback_calls = 0

    def fallback_factory():
        nonlocal fallback_calls
        fallback_calls += 1
        return FakeTracker("cv2")

    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: primary,
        fallback_factory=fallback_factory,
        logger=lambda _message: None,
    )

    with pytest.raises(TypeError, match="tracker bug"):
        tracker.process_frame(object(), 1000)

    assert tracker.active_backend == "mediapipe"
    assert fallback_calls == 0
    assert primary.close_count == 0


def test_primary_startup_failure_uses_fallback_and_delays_retry_from_first_frame():
    fallback = FakeTracker("cv2", "fallback")
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: (_ for _ in ()).throw(
            ImportError("mediapipe unavailable")
        ),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=30_000,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    assert tracker.active_backend == "cv2"
    assert tracker.process_frame(object(), 5000) == "fallback"
    snapshot = tracker.snapshot(5000)
    assert snapshot.failover_count == 1
    assert snapshot.primary_retry_attempts == 0
    assert snapshot.retry_in_ms == 30_000


def test_shadow_retry_delay_is_wrap_safe():
    failed = FakeTracker(
        "mediapipe",
        AsyncInferenceFailure("stalled"),
    )
    recovered = FakeTracker("mediapipe-retry", None, ready_after=2)
    primary_factory_calls = 0

    def primary_factory():
        nonlocal primary_factory_calls
        primary_factory_calls += 1
        return failed if primary_factory_calls == 1 else recovered

    fallback = FakeTracker("cv2", "f1", "f2", "f3", "f4")
    tracker = AutoFailoverFaceTracker(
        primary_factory=primary_factory,
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=100,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    assert tracker.process_frame("first", 0xFFFF_FFF0) == "f1"
    assert tracker.process_frame("before", 0x20) == "f2"
    assert primary_factory_calls == 1

    # 0xFFFFFFF0 -> 0x54 is exactly 100 ms across uint32 rollover.
    assert tracker.process_frame("probe-1", 0x54) == "f3"
    assert primary_factory_calls == 2
    assert tracker.active_backend == "cv2"
    assert tracker.snapshot(0x54).primary_candidate_active

    assert tracker.process_frame("probe-2", 0x55) == "f4"
    assert tracker.active_backend == "mediapipe"
    assert fallback.close_count == 1
    assert recovered.calls == [("probe-1", 0x54), ("probe-2", 0x55)]


def test_ready_candidate_pose_is_returned_on_promotion_frame():
    failed = FakeTracker("mediapipe", AsyncInferenceFailure("failed"))
    candidate = FakeTracker("candidate", "better pose", ready_after=1)
    factories = iter((failed, candidate))
    fallback = FakeTracker("cv2", "fallback-1", "fallback-2")
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: next(factories),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    # The first call degrades and starts the retry delay from the failure frame.
    assert tracker.process_frame("failure", 1000) == "fallback-1"
    # The next fallback frame is also the shadow probe; its primary pose wins.
    assert tracker.process_frame("promotion", 1001) == "better pose"
    assert tracker.active_backend == "mediapipe"


def test_no_face_callback_can_promote_without_a_pose_result():
    failed = FakeTracker("mediapipe", AsyncInferenceFailure("failed"))
    candidate = FakeTracker("candidate", None, ready_after=1)
    factories = iter((failed, candidate))
    fallback = FakeTracker("cv2", "fallback-1", "fallback-2")
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: next(factories),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    assert tracker.process_frame("failure", 1000) == "fallback-1"
    assert tracker.process_frame("promotion", 1001) == "fallback-2"
    assert tracker.active_backend == "mediapipe"


def test_failed_shadow_candidate_is_bounded_and_never_breaks_fallback():
    failed = FakeTracker("mediapipe", AsyncInferenceFailure("failed"))
    candidate = FakeTracker(
        "candidate",
        RuntimeError("candidate bug"),
    )
    factories = iter((failed, candidate))
    fallback = FakeTracker("cv2", "f1", "f2", "f3")
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: next(factories),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    assert tracker.process_frame("failure", 1000) == "f1"
    assert tracker.process_frame("probe", 1001) == "f2"
    assert tracker.process_frame("sticky", 2000) == "f3"
    snapshot = tracker.snapshot(2000)
    assert snapshot.active_backend == "cv2"
    assert snapshot.primary_retry_attempts == 1
    assert not snapshot.primary_candidate_active
    assert snapshot.retry_in_ms is None
    assert candidate.close_count == 1


def test_live_calibration_reaches_fallback_and_future_candidate():
    failed = FakeTracker("mediapipe", AsyncInferenceFailure("failed"))
    candidate = FakeTracker("candidate", None, ready_after=10)
    factories = iter((failed, candidate))
    fallback = FakeTracker("cv2", None, None)
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: next(factories),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    tracker.process_frame(object(), 1000)
    tracker.set_calibration(real_ipd_cm=6.4, camera_fov_deg=78.0)
    tracker.process_frame(object(), 1001)

    expected = {"real_ipd_cm": 6.4, "camera_fov_deg": 78.0}
    assert fallback.calibrations[-1] == expected
    assert candidate.calibrations[-1] == expected


def test_reset_and_close_cover_active_and_shadow_trackers_once():
    failed = FakeTracker("mediapipe", AsyncInferenceFailure("failed"))
    candidate = FakeTracker("candidate", None, ready_after=10)
    factories = iter((failed, candidate))
    fallback = FakeTracker("cv2", None, None)
    tracker = AutoFailoverFaceTracker(
        primary_factory=lambda: next(factories),
        fallback_factory=lambda: fallback,
        policy=BackendFailoverPolicy(
            retry_primary_after_ms=0,
            max_primary_retries=1,
        ),
        logger=lambda _message: None,
    )

    tracker.process_frame(object(), 1000)
    tracker.process_frame(object(), 1001)
    tracker.reset_session()
    tracker.close()
    tracker.close()

    assert fallback.reset_count == 1
    assert candidate.reset_count == 1
    assert fallback.close_count == 1
    assert candidate.close_count == 1
    with pytest.raises(RuntimeError, match="controller is closed"):
        tracker.process_frame(object(), 1002)


def test_invalid_policy_and_failure_configuration_fail_closed():
    with pytest.raises(ValueError):
        BackendFailoverPolicy(retry_primary_after_ms=-1)
    with pytest.raises(ValueError):
        BackendFailoverPolicy(max_primary_retries=-1)
    with pytest.raises(ValueError):
        AutoFailoverFaceTracker(
            primary_factory=lambda: FakeTracker("primary"),
            fallback_factory=lambda: FakeTracker("fallback"),
            primary_failure_types=(),
        )
