from __future__ import annotations

from unittest.mock import MagicMock

import cv2
import pytest

from tracker.camera_control_recovery import (
    CameraControlRecovery,
    CameraControlRecoveryPolicy,
    CameraControlRecoveryRequest,
    apply_camera_control_recovery,
    parse_camera_control_recovery_policy,
    try_restore_automatic_camera_controls,
)


def _policy(**overrides) -> CameraControlRecoveryPolicy:
    values = {
        "degradation_hold_ms": 2_000,
        "retry_interval_ms": 5_000,
        "max_attempts_per_episode": 3,
    }
    values.update(overrides)
    return CameraControlRecoveryPolicy(**values)


def test_exposure_problem_requires_exact_sustained_hold_boundary():
    recovery = CameraControlRecovery(_policy())
    state = {"auto_exposure_locked": True}

    assert not recovery.observe(1000, ("underexposed",), state).requested
    assert not recovery.observe(2999, ("underexposed",), state).requested
    request = recovery.observe(3000, ("underexposed",), state)

    assert request.auto_exposure
    assert not request.autofocus
    assert request.reasons == ("underexposed",)


def test_focus_and_exposure_are_requested_independently():
    recovery = CameraControlRecovery(_policy(degradation_hold_ms=0))
    state = {
        "autofocus_locked": True,
        "auto_exposure_locked": True,
    }

    focus = recovery.observe(1000, ("soft or motion-blurred",), state)
    assert focus.autofocus
    assert not focus.auto_exposure

    exposure = recovery.observe(1033, ("overexposed",), state)
    assert not exposure.autofocus
    assert exposure.auto_exposure

    both = recovery.observe(
        1066,
        ("soft or motion-blurred", "exposure is hunting"),
        state,
    )
    assert both.autofocus
    assert both.auto_exposure


def test_cadence_only_warning_never_changes_camera_controls():
    recovery = CameraControlRecovery(_policy(degradation_hold_ms=0))
    state = {
        "autofocus_locked": True,
        "auto_exposure_locked": True,
    }

    request = recovery.observe(
        1000,
        ("camera cadence low (12.0 fps)",),
        state,
    )

    assert not request.requested


def test_unlocked_groups_are_never_requested():
    recovery = CameraControlRecovery(_policy(degradation_hold_ms=0))

    request = recovery.observe(
        1000,
        ("underexposed", "soft or motion-blurred"),
        {
            "autofocus_locked": False,
            "auto_exposure_locked": False,
        },
    )

    assert not request.requested


def test_problem_clearing_starts_a_new_hold_episode():
    recovery = CameraControlRecovery(_policy())
    state = {"auto_exposure_locked": True}
    recovery.observe(1000, ("underexposed",), state)
    recovery.observe(2500, (), state)

    assert not recovery.observe(3000, ("underexposed",), state).requested
    assert not recovery.observe(4999, ("underexposed",), state).requested
    assert recovery.observe(5000, ("underexposed",), state).auto_exposure


def test_failed_attempt_obeys_retry_delay_and_episode_limit():
    recovery = CameraControlRecovery(
        _policy(
            degradation_hold_ms=0,
            retry_interval_ms=5000,
            max_attempts_per_episode=2,
        )
    )
    state = {"auto_exposure_locked": True}
    request = recovery.observe(1000, ("underexposed",), state)
    recovery.record_result(1000, request, {"auto_exposure_reenabled": False})

    assert not recovery.observe(5999, ("underexposed",), state).requested
    second = recovery.observe(6000, ("underexposed",), state)
    assert second.auto_exposure
    recovery.record_result(6000, second, {"auto_exposure_reenabled": False})

    assert not recovery.observe(20_000, ("underexposed",), state).requested
    snapshot = recovery.snapshot()
    assert snapshot.exposure_attempts == 2

    # A healthy frame resets the bounded episode and permits future recovery.
    recovery.observe(20_001, (), state)
    assert recovery.observe(20_002, ("underexposed",), state).auto_exposure


def test_success_clears_only_the_recovered_group():
    recovery = CameraControlRecovery(_policy(degradation_hold_ms=0))
    state = {
        "autofocus_locked": True,
        "auto_exposure_locked": True,
    }
    request = recovery.observe(
        1000,
        ("soft or motion-blurred", "underexposed"),
        state,
    )

    recovered = recovery.record_result(
        1000,
        request,
        {
            "autofocus_reenabled": True,
            "auto_exposure_reenabled": False,
        },
    )

    assert recovered == ("autofocus",)
    snapshot = recovery.snapshot()
    assert snapshot.focus_attempts == 0
    assert snapshot.exposure_attempts == 1
    assert snapshot.autofocus_recovery_count == 1
    assert snapshot.auto_exposure_recovery_count == 0


def test_timing_is_wrap_safe():
    recovery = CameraControlRecovery(_policy(degradation_hold_ms=500))
    state = {"autofocus_locked": True}
    recovery.observe(0xFFFF_FF00, ("soft or motion-blurred",), state)

    request = recovery.observe(0x0000_00F4, ("soft or motion-blurred",), state)

    assert request.autofocus


def test_backwards_timestamp_restarts_sustained_window():
    recovery = CameraControlRecovery(_policy(degradation_hold_ms=1000))
    state = {"autofocus_locked": True}
    recovery.observe(5000, ("soft or motion-blurred",), state)

    assert not recovery.observe(1000, ("soft or motion-blurred",), state).requested
    assert not recovery.observe(1999, ("soft or motion-blurred",), state).requested
    assert recovery.observe(2000, ("soft or motion-blurred",), state).autofocus


def test_autofocus_recovery_uses_previous_auto_value_then_common_fallback(
    monkeypatch,
):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    cap = MagicMock()

    def set_control(property_id, value):
        return property_id == 1001 and value == 1.0

    cap.set.side_effect = set_control
    result = try_restore_automatic_camera_controls(
        cap,
        CameraControlRecoveryRequest(autofocus=True),
        {"autofocus_locked": True, "autofocus_value": 2.0},
    )

    assert result["autofocus_reenabled"] is True
    assert result["autofocus_automatic_value"] == 1.0
    assert [tuple(call.args) for call in cap.set.call_args_list] == [
        (1001, 2.0),
        (1001, 1.0),
    ]


def test_manual_autofocus_value_is_not_mistaken_for_automatic(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", 1001, raising=False)
    cap = MagicMock()
    cap.set.return_value = True

    result = try_restore_automatic_camera_controls(
        cap,
        CameraControlRecoveryRequest(autofocus=True),
        {"autofocus_locked": True, "autofocus_value": 0.0},
    )

    assert result["autofocus_automatic_value"] == 1.0
    cap.set.assert_called_once_with(1001, 1.0)


def test_auto_exposure_recovery_uses_backend_conventions(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTO_EXPOSURE", 1002, raising=False)
    cap = MagicMock()
    cap.set.side_effect = lambda property_id, value: (
        property_id == 1002 and value == 0.75
    )

    result = try_restore_automatic_camera_controls(
        cap,
        CameraControlRecoveryRequest(auto_exposure=True),
        {"auto_exposure_locked": True, "auto_exposure_value": 0.25},
    )

    assert result["auto_exposure_reenabled"] is True
    assert result["auto_exposure_automatic_value"] == 0.75
    cap.set.assert_called_once_with(1002, 0.75)


def test_driver_exceptions_and_missing_properties_are_contained(monkeypatch):
    monkeypatch.setattr(cv2, "CAP_PROP_AUTOFOCUS", None, raising=False)
    monkeypatch.setattr(cv2, "CAP_PROP_AUTO_EXPOSURE", 1002, raising=False)
    cap = MagicMock()
    cap.set.side_effect = RuntimeError("driver rejected control")

    result = try_restore_automatic_camera_controls(
        cap,
        CameraControlRecoveryRequest(
            autofocus=True,
            auto_exposure=True,
        ),
        {
            "autofocus_locked": True,
            "auto_exposure_locked": True,
        },
    )

    assert result["autofocus_reenabled"] is False
    assert result["auto_exposure_reenabled"] is False
    assert any("unavailable" in error for error in result["errors"])
    assert any("RuntimeError" in error for error in result["errors"])


def test_successful_recovery_updates_only_corresponding_lock_state():
    state = {
        "autofocus_locked": True,
        "focus_preserved": True,
        "auto_exposure_locked": True,
        "exposure_preserved": True,
    }

    updated = apply_camera_control_recovery(
        state,
        {
            "autofocus_reenabled": True,
            "autofocus_automatic_value": 1.0,
            "auto_exposure_reenabled": False,
        },
    )

    assert updated["autofocus_locked"] is False
    assert updated["focus_preserved"] is False
    assert updated["autofocus_recovery_value"] == 1.0
    assert updated["auto_exposure_locked"] is True
    assert updated["exposure_preserved"] is True


def test_policy_parser_accepts_strings_and_falls_back_atomically():
    policy = parse_camera_control_recovery_policy(
        {
            "control_recovery": {
                "degradation_hold_ms": "1500",
                "retry_interval_ms": "4000",
                "max_attempts_per_episode": "2",
            }
        }
    )
    assert policy == CameraControlRecoveryPolicy(1500, 4000, 2)

    logs: list[str] = []
    invalid = parse_camera_control_recovery_policy(
        {
            "control_recovery": {
                "degradation_hold_ms": 1500,
                "retry_interval_ms": -1,
                "max_attempts_per_episode": 2,
            }
        },
        logger=logs.append,
    )
    assert invalid == CameraControlRecoveryPolicy()
    assert len(logs) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"degradation_hold_ms": -1},
        {"degradation_hold_ms": 60_001},
        {"retry_interval_ms": -1},
        {"retry_interval_ms": 60_001},
        {"max_attempts_per_episode": 0},
        {"max_attempts_per_episode": 21},
        {"degradation_hold_ms": 1.5},
        {"retry_interval_ms": True},
    ],
)
def test_invalid_policy_values_fail_closed(kwargs):
    with pytest.raises(ValueError):
        CameraControlRecoveryPolicy(**kwargs)
