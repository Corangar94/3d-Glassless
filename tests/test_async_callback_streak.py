from tracker.async_inference_watchdog import AsyncInferenceWatchdog


def test_successful_progressing_callbacks_build_a_streak():
    watchdog = AsyncInferenceWatchdog()

    watchdog.record_callback(1000)
    watchdog.record_callback(1033)
    watchdog.record_callback(1066)

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_successful_callbacks == 3
    assert snapshot.last_callback_ms == 1066


def test_duplicate_or_backward_callback_does_not_inflate_streak():
    watchdog = AsyncInferenceWatchdog()

    watchdog.record_callback(1000)
    watchdog.record_callback(1000)
    watchdog.record_callback(999)

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_successful_callbacks == 1
    assert snapshot.last_callback_ms == 1000


def test_callback_error_breaks_healthy_streak():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_callback(1000)
    watchdog.record_callback(1033)

    watchdog.record_callback(1066, error=ValueError("bad landmarks"))

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_successful_callbacks == 0
    assert snapshot.consecutive_callback_errors == 1
    assert snapshot.last_callback_ms == 1066


def test_success_after_callback_error_starts_a_new_streak():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_callback(1000, error=ValueError("bad"))

    watchdog.record_callback(1033)

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_callback_errors == 0
    assert snapshot.consecutive_successful_callbacks == 1


def test_submission_error_breaks_callback_health_streak():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_callback(1000)
    watchdog.record_callback(1033)

    watchdog.record_submission_error(RuntimeError("queue rejected"))

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_successful_callbacks == 0
    assert snapshot.consecutive_submission_errors == 1


def test_session_reset_clears_callback_streak():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_callback(1000)
    watchdog.record_callback(1033)

    watchdog.reset_session()

    snapshot = watchdog.snapshot()
    assert snapshot.consecutive_successful_callbacks == 0
    assert snapshot.last_callback_ms is None
