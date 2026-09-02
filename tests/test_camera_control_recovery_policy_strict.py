from __future__ import annotations

import pytest

from tracker.camera_control_recovery import (
    CameraControlRecoveryPolicy,
    parse_camera_control_recovery_policy,
)


@pytest.mark.parametrize(
    "control_recovery",
    [
        {"degradation_hold_ms": 1500.5},
        {"degradation_hold_ms": True},
        {"retry_interval_ms": 4000.5},
        {"retry_interval_ms": False},
        {"max_attempts_per_episode": 2.5},
        {"max_attempts_per_episode": True},
        {"max_attempts_per_episode": "3.0"},
    ],
)
def test_fractional_or_boolean_integer_settings_fall_back_atomically(
    control_recovery,
):
    logs: list[str] = []

    policy = parse_camera_control_recovery_policy(
        {"control_recovery": control_recovery},
        logger=logs.append,
    )

    assert policy == CameraControlRecoveryPolicy()
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]


def test_integer_strings_remain_supported():
    policy = parse_camera_control_recovery_policy(
        {
            "control_recovery": {
                "degradation_hold_ms": "1500",
                "retry_interval_ms": "4000",
                "max_attempts_per_episode": "2",
            }
        }
    )

    assert policy == CameraControlRecoveryPolicy(
        degradation_hold_ms=1500,
        retry_interval_ms=4000,
        max_attempts_per_episode=2,
    )
