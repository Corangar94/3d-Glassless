from __future__ import annotations

import pytest

from tracker.latest_frame_capture import (
    LatestFrameCapturePolicy,
    parse_latest_frame_capture_policy,
)


def test_maximum_supported_timing_values_are_valid():
    policy = LatestFrameCapturePolicy(
        wait_timeout_ms=60_000,
        failure_backoff_ms=10_000,
        shutdown_timeout_ms=60_000,
        max_frame_age_ms=60_000,
        freeze_check_interval_ms=60_000,
        freeze_timeout_ms=60_000,
    )

    assert policy.config_values() == {
        "enabled": True,
        "wait_timeout_ms": 60_000,
        "max_frame_age_ms": 60_000,
        "freeze_check_interval_ms": 60_000,
        "freeze_timeout_ms": 60_000,
        "failure_backoff_ms": 10_000,
        "shutdown_timeout_ms": 60_000,
    }


def test_new_fields_do_not_shift_historical_positional_arguments():
    historical = LatestFrameCapturePolicy(False, 750, 25, 900)
    with_age = LatestFrameCapturePolicy(False, 750, 25, 900, 125)
    with_freeze = LatestFrameCapturePolicy(
        False,
        750,
        25,
        900,
        125,
        50,
        2_000,
    )

    assert historical.enabled is False
    assert historical.wait_timeout_ms == 750
    assert historical.failure_backoff_ms == 25
    assert historical.shutdown_timeout_ms == 900
    assert historical.max_frame_age_ms == 250
    assert historical.freeze_check_interval_ms == 250
    assert historical.freeze_timeout_ms == 3_000

    assert with_age.max_frame_age_ms == 125
    assert with_age.freeze_check_interval_ms == 250
    assert with_age.freeze_timeout_ms == 3_000

    assert with_freeze.max_frame_age_ms == 125
    assert with_freeze.freeze_check_interval_ms == 50
    assert with_freeze.freeze_timeout_ms == 2_000


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wait_timeout_ms": 0},
        {"wait_timeout_ms": 60_001},
        {"max_frame_age_ms": -1},
        {"max_frame_age_ms": 60_001},
        {"freeze_check_interval_ms": -1},
        {"freeze_check_interval_ms": 60_001},
        {"freeze_timeout_ms": -1},
        {"freeze_timeout_ms": 60_001},
        {"failure_backoff_ms": -1},
        {"failure_backoff_ms": 10_001},
        {"shutdown_timeout_ms": -1},
        {"shutdown_timeout_ms": 60_001},
        {"wait_timeout_ms": 1.5},
        {"max_frame_age_ms": True},
        {"freeze_check_interval_ms": 1.5},
        {"freeze_timeout_ms": True},
        {"failure_backoff_ms": True},
        {"shutdown_timeout_ms": "1000"},
    ],
)
def test_direct_policy_rejects_unbounded_or_noninteger_timing(kwargs):
    with pytest.raises(ValueError):
        LatestFrameCapturePolicy(**kwargs)


@pytest.mark.parametrize(
    "latest_frame",
    [
        {"wait_timeout_ms": 10**200},
        {"max_frame_age_ms": 10**200},
        {"freeze_check_interval_ms": 10**200},
        {"freeze_timeout_ms": 10**200},
        {"failure_backoff_ms": 10**200},
        {"shutdown_timeout_ms": 10**200},
    ],
)
def test_parser_falls_back_before_huge_values_reach_thread_timeouts(
    latest_frame,
):
    logs: list[str] = []

    policy = parse_latest_frame_capture_policy(
        {"latest_frame": latest_frame},
        logger=logs.append,
    )

    assert policy == LatestFrameCapturePolicy()
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]


def test_parser_accepts_explicit_freeze_settings():
    policy = parse_latest_frame_capture_policy(
        {
            "latest_frame": {
                "freeze_check_interval_ms": "100",
                "freeze_timeout_ms": "5000",
            }
        }
    )

    assert policy.freeze_check_interval_ms == 100
    assert policy.freeze_timeout_ms == 5_000
