import pytest

from tracker.camera_control_retry import (
    CameraControlLockRetry,
    camera_controls_locked,
)


UINT32_MAX = 0xFFFF_FFFF


def test_no_exposed_controls_is_complete_without_retries():
    assert camera_controls_locked({})


def test_every_exposed_control_must_accept_manual_mode():
    assert camera_controls_locked(
        {"autofocus_locked": True, "auto_exposure_locked": True}
    )
    assert not camera_controls_locked(
        {"autofocus_locked": True, "auto_exposure_locked": False}
    )
    assert not camera_controls_locked({"autofocus_locked": False})


def test_first_stable_attempt_is_immediate():
    retry = CameraControlLockRetry(max_attempts=3, retry_interval_ms=5000)

    assert not retry.should_attempt(1000, stable_for_lock=False)
    assert retry.should_attempt(1000, stable_for_lock=True)


def test_failed_attempt_waits_for_retry_interval():
    retry = CameraControlLockRetry(max_attempts=3, retry_interval_ms=5000)
    assert not retry.record_result(1000, {"autofocus_locked": False})

    assert not retry.should_attempt(5999, stable_for_lock=True)
    assert retry.should_attempt(6000, stable_for_lock=True)
    assert retry.attempts == 1
    assert retry.remaining_attempts == 2


def test_retry_interval_is_safe_across_uint32_rollover():
    retry = CameraControlLockRetry(max_attempts=3, retry_interval_ms=5000)
    assert not retry.record_result(
        UINT32_MAX - 1999,
        {"auto_exposure_locked": False},
    )

    assert not retry.should_attempt(2999, stable_for_lock=True)
    assert retry.should_attempt(3000, stable_for_lock=True)


def test_success_completes_policy_and_stops_future_attempts():
    retry = CameraControlLockRetry(max_attempts=3, retry_interval_ms=100)
    assert retry.record_result(
        1000,
        {"autofocus_locked": True, "auto_exposure_locked": True},
    )

    assert retry.complete
    assert not retry.exhausted
    assert not retry.should_attempt(5000, stable_for_lock=True)
    assert retry.record_result(6000, {"autofocus_locked": False})
    assert retry.attempts == 1


def test_failures_stop_after_bounded_attempt_count():
    retry = CameraControlLockRetry(max_attempts=3, retry_interval_ms=0)
    failure = {"autofocus_locked": False, "auto_exposure_locked": False}

    assert not retry.record_result(1000, failure)
    assert retry.should_attempt(1001, stable_for_lock=True)
    assert not retry.record_result(1001, failure)
    assert retry.should_attempt(1002, stable_for_lock=True)
    assert not retry.record_result(1002, failure)

    assert retry.exhausted
    assert retry.remaining_attempts == 0
    assert not retry.should_attempt(2000, stable_for_lock=True)
    assert not retry.record_result(2000, failure)
    assert retry.attempts == 3


def test_reset_rearms_policy_for_replacement_camera():
    retry = CameraControlLockRetry(max_attempts=1, retry_interval_ms=5000)
    retry.record_result(1000, {"autofocus_locked": False})
    assert retry.exhausted

    retry.reset()

    assert retry.attempts == 0
    assert retry.last_attempt_timestamp_ms is None
    assert not retry.complete
    assert retry.should_attempt(1001, stable_for_lock=True)


def test_invalid_policy_values_fail_closed():
    with pytest.raises(ValueError):
        CameraControlLockRetry(max_attempts=0)
    with pytest.raises(ValueError):
        CameraControlLockRetry(retry_interval_ms=-1)


def test_tracking_loop_uses_and_resets_bounded_retry_controller():
    source = open("tracker/main.py", encoding="utf-8").read()

    assert "CameraControlLockRetry" in source
    assert "self._camera_control_lock_retry.reset()" in source
    assert "retry.should_attempt(" in source
    assert "retry.record_result(" in source
    assert "controls_lock_attempted" not in source
