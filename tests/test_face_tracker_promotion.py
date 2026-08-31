from __future__ import annotations

from tracker.async_inference_watchdog import AsyncInferenceWatchdog
from tracker.face_tracker import FaceTracker


def _tracker(*, async_mode: bool) -> FaceTracker:
    tracker = FaceTracker.__new__(FaceTracker)
    tracker._async_mode = async_mode
    tracker._async_watchdog = (
        AsyncInferenceWatchdog(
            max_consecutive_errors=3,
            stall_timeout_ms=5000,
        )
        if async_mode
        else None
    )
    return tracker


def test_async_tracker_is_not_ready_before_any_callback():
    tracker = _tracker(async_mode=True)
    tracker._async_watchdog.record_submission(1000)

    assert not tracker.ready_for_promotion()
    snapshot = tracker.async_health_snapshot()
    assert snapshot is not None
    assert snapshot.last_callback_ms is None


def test_healthy_no_face_callback_proves_promotion_readiness():
    tracker = _tracker(async_mode=True)
    tracker._async_watchdog.record_submission(1000)
    tracker._async_watchdog.record_callback(1000)

    assert tracker.ready_for_promotion()


def test_submission_or_callback_error_blocks_promotion():
    submission_error = _tracker(async_mode=True)
    submission_error._async_watchdog.record_submission(1000)
    submission_error._async_watchdog.record_callback(1000)
    submission_error._async_watchdog.record_submission_error(
        RuntimeError("submit failed")
    )

    callback_error = _tracker(async_mode=True)
    callback_error._async_watchdog.record_submission(1000)
    callback_error._async_watchdog.record_callback(
        1000,
        error=ValueError("bad callback"),
    )

    assert not submission_error.ready_for_promotion()
    assert not callback_error.ready_for_promotion()


def test_later_success_restores_promotion_readiness():
    tracker = _tracker(async_mode=True)
    tracker._async_watchdog.record_submission_error(RuntimeError("failed"))
    tracker._async_watchdog.record_callback(
        1000,
        error=ValueError("bad callback"),
    )
    tracker._async_watchdog.record_submission(1033)
    tracker._async_watchdog.record_callback(1033)

    assert tracker.ready_for_promotion()


def test_synchronous_mediapipe_is_ready_after_a_successful_call_contract():
    tracker = _tracker(async_mode=False)

    assert tracker.async_health_snapshot() is None
    assert tracker.ready_for_promotion()
