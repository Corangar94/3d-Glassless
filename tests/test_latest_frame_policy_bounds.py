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
    )

    assert policy.config_values() == {
        "enabled": True,
        "wait_timeout_ms": 60_000,
        "failure_backoff_ms": 10_000,
        "shutdown_timeout_ms": 60_000,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"wait_timeout_ms": 0},
        {"wait_timeout_ms": 60_001},
        {"failure_backoff_ms": -1},
        {"failure_backoff_ms": 10_001},
        {"shutdown_timeout_ms": -1},
        {"shutdown_timeout_ms": 60_001},
        {"wait_timeout_ms": 1.5},
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
