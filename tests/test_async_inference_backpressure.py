from __future__ import annotations

import pytest

from tracker.async_inference_watchdog import (
    AsyncInferenceFailure,
    AsyncInferenceWatchdog,
)


def test_first_submission_is_always_admitted():
    watchdog = AsyncInferenceWatchdog()

    assert watchdog.should_submit(1000, max_backlog_ms=150)


def test_outstanding_backlog_is_bounded_against_current_frame_time():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_submission(1000)
    watchdog.record_submission(1033)
    watchdog.record_submission(1066)
    watchdog.record_submission(1099)
    watchdog.record_submission(1132)

    assert watchdog.should_submit(1149, max_backlog_ms=150)
    assert not watchdog.should_submit(1150, max_backlog_ms=150)


def test_callback_progress_reopens_submission_gate():
    watchdog = AsyncInferenceWatchdog()
    for timestamp in (1000, 1033, 1066, 1099, 1132):
        watchdog.record_submission(timestamp)
    assert not watchdog.should_submit(1165, max_backlog_ms=150)

    watchdog.record_callback(1099)

    assert watchdog.should_submit(1165, max_backlog_ms=150)


def test_fully_caught_up_task_is_admitted_after_long_camera_pause():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_submission(1000)
    watchdog.record_callback(1000)

    assert watchdog.should_submit(30_000, max_backlog_ms=150)


def test_zero_backlog_limit_disables_throttling():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_submission(1000)

    assert watchdog.should_submit(100_000, max_backlog_ms=0)


def test_negative_backlog_limit_fails_closed():
    watchdog = AsyncInferenceWatchdog()

    with pytest.raises(ValueError):
        watchdog.should_submit(1000, max_backlog_ms=-1)


def test_throttled_inputs_are_observable_but_not_errors():
    watchdog = AsyncInferenceWatchdog(max_consecutive_errors=1)
    watchdog.record_submission(1000)

    watchdog.record_throttled_submission()
    watchdog.record_throttled_submission()

    snapshot = watchdog.snapshot(1200)
    assert snapshot.throttled_submission_count == 2
    assert snapshot.consecutive_submission_errors == 0
    watchdog.raise_if_unhealthy(1200)


def test_current_callback_age_keeps_growing_while_submissions_are_throttled():
    watchdog = AsyncInferenceWatchdog(stall_timeout_ms=5000)
    watchdog.record_submission(1000)
    watchdog.record_submission(1132)
    assert not watchdog.should_submit(1200, max_backlog_ms=150)
    watchdog.record_throttled_submission()

    before = watchdog.snapshot(5999)
    assert before.callback_lag_ms == 132
    assert before.callback_age_ms == 4999
    watchdog.raise_if_unhealthy(5999)

    with pytest.raises(AsyncInferenceFailure, match="callbacks stalled"):
        watchdog.raise_if_unhealthy(6000)


def test_no_argument_snapshot_retains_historical_lag_semantics():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_submission(1000)
    watchdog.record_submission(1300)

    snapshot = watchdog.snapshot()

    assert snapshot.callback_lag_ms == 300
    assert snapshot.callback_age_ms == 300


def test_callback_error_resets_healthy_streak_and_progresses_stall_anchor():
    watchdog = AsyncInferenceWatchdog(
        max_consecutive_errors=3,
        stall_timeout_ms=5000,
    )
    watchdog.record_submission(1000)
    watchdog.record_callback(1000)
    watchdog.record_callback(1033)
    watchdog.record_callback(1066, error=ValueError("bad result"))
    watchdog.record_submission(5000)

    snapshot = watchdog.snapshot(5000)
    assert snapshot.consecutive_successful_callbacks == 0
    assert snapshot.callback_age_ms == 3934
    watchdog.raise_if_unhealthy(5000)


def test_session_reset_clears_backpressure_episode():
    watchdog = AsyncInferenceWatchdog()
    watchdog.record_submission(1000)
    watchdog.record_throttled_submission()

    watchdog.reset_session()

    snapshot = watchdog.snapshot(2000)
    assert snapshot.first_submission_ms is None
    assert snapshot.last_submission_ms is None
    assert snapshot.callback_age_ms == 0
    assert snapshot.throttled_submission_count == 0
    assert watchdog.should_submit(2000, max_backlog_ms=150)
