import pytest

from tracker.async_inference_watchdog import (
    AsyncInferenceFailure,
    AsyncInferenceWatchdog,
)


def test_callback_stall_is_detected_after_timeout():
    watchdog = AsyncInferenceWatchdog(stall_timeout_ms=5000)
    watchdog.record_submission(1000)
    watchdog.record_submission(5999)
    watchdog.raise_if_unhealthy()

    watchdog.record_submission(6000)

    with pytest.raises(AsyncInferenceFailure, match="callbacks stalled"):
        watchdog.raise_if_unhealthy()


def test_successful_callback_moves_stall_anchor_forward():
    watchdog = AsyncInferenceWatchdog(stall_timeout_ms=5000)
    watchdog.record_submission(1000)
    watchdog.record_submission(4000)
    watchdog.record_callback(3900)
    watchdog.record_submission(8000)

    snapshot = watchdog.snapshot()
    assert snapshot.callback_lag_ms == 4100
    watchdog.raise_if_unhealthy()


def test_three_consecutive_submission_errors_escalate():
    watchdog = AsyncInferenceWatchdog(max_consecutive_errors=3)
    for _ in range(3):
        watchdog.record_submission_error(RuntimeError("submit failed"))

    with pytest.raises(
        AsyncInferenceFailure,
        match="submission failed 3 consecutive times",
    ):
        watchdog.raise_if_unhealthy()


def test_successful_submission_resets_submission_error_episode():
    watchdog = AsyncInferenceWatchdog(max_consecutive_errors=3)
    watchdog.record_submission_error(RuntimeError("first"))
    watchdog.record_submission_error(RuntimeError("second"))
    watchdog.record_submission(1000)
    watchdog.record_submission_error(RuntimeError("new episode"))

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_submission_errors == 1
    watchdog.raise_if_unhealthy()


def test_three_consecutive_callback_processing_errors_escalate():
    watchdog = AsyncInferenceWatchdog(max_consecutive_errors=3)
    watchdog.record_submission(1000)
    for timestamp in (1000, 1033, 1066):
        watchdog.record_callback(
            timestamp,
            error=IndexError("malformed landmarks"),
        )

    with pytest.raises(
        AsyncInferenceFailure,
        match="callback processing failed 3 consecutive times",
    ):
        watchdog.raise_if_unhealthy()


def test_successful_callback_resets_callback_error_episode():
    watchdog = AsyncInferenceWatchdog(max_consecutive_errors=3)
    watchdog.record_callback(1000, error=ValueError("bad"))
    watchdog.record_callback(1033, error=ValueError("bad"))
    watchdog.record_callback(1066)
    watchdog.record_callback(1099, error=ValueError("new episode"))

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_callback_errors == 1
    assert snapshot.last_callback_ms == 1099
    watchdog.raise_if_unhealthy()


def test_callback_error_still_proves_inference_progress():
    watchdog = AsyncInferenceWatchdog(
        max_consecutive_errors=4,
        stall_timeout_ms=5000,
    )
    watchdog.record_submission(1000)
    watchdog.record_submission(5000)
    watchdog.record_callback(4900, error=ValueError("bad result"))
    watchdog.record_submission(9000)

    assert watchdog.snapshot().callback_lag_ms == 4100
    watchdog.raise_if_unhealthy()


def test_session_reset_clears_health_episode():
    watchdog = AsyncInferenceWatchdog(max_consecutive_errors=2)
    watchdog.record_submission(1000)
    watchdog.record_submission_error(RuntimeError("failed"))
    watchdog.record_callback(1000, error=ValueError("bad"))

    watchdog.reset_session()

    snapshot = watchdog.snapshot()
    assert snapshot.first_submission_ms is None
    assert snapshot.last_submission_ms is None
    assert snapshot.last_callback_ms is None
    assert snapshot.consecutive_submission_errors == 0
    assert snapshot.consecutive_callback_errors == 0
    assert snapshot.callback_lag_ms == 0
    assert snapshot.last_error == ""


def test_invalid_watchdog_configuration_fails_closed():
    with pytest.raises(ValueError):
        AsyncInferenceWatchdog(max_consecutive_errors=0)
    with pytest.raises(ValueError):
        AsyncInferenceWatchdog(stall_timeout_ms=0)
