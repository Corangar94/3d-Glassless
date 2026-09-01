from __future__ import annotations

import pytest

from tracker.mediapipe_runtime_policy import (
    MediaPipeRuntimePolicy,
    parse_mediapipe_runtime_policy,
)


def test_default_policy_matches_runtime_contract():
    policy = parse_mediapipe_runtime_policy({})

    assert policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=5_000,
        max_consecutive_errors=3,
        max_backlog_ms=150,
        max_result_age_ms=250,
        max_consecutive_stale_results=3,
        stale_result_window_ms=1_000,
    )
    assert policy.tracker_kwargs() == {
        "async_stall_timeout_ms": 5_000,
        "async_max_consecutive_errors": 3,
        "async_max_backlog_ms": 150,
        "async_max_result_age_ms": 250,
        "async_max_consecutive_stale_results": 3,
        "async_stale_result_window_ms": 1_000,
    }


def test_all_explicit_async_limits_are_parsed():
    policy = parse_mediapipe_runtime_policy(
        {
            "async_stall_timeout_ms": 4_000,
            "async_max_consecutive_errors": 5,
            "async_max_backlog_ms": 90,
            "async_max_result_age_ms": 180,
            "async_max_consecutive_stale_results": 4,
            "async_stale_result_window_ms": 700,
        }
    )

    assert policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=4_000,
        max_consecutive_errors=5,
        max_backlog_ms=90,
        max_result_age_ms=180,
        max_consecutive_stale_results=4,
        stale_result_window_ms=700,
    )


def test_numeric_strings_are_accepted_from_yaml_or_environment_edits():
    policy = parse_mediapipe_runtime_policy(
        {
            "async_stall_timeout_ms": "4500",
            "async_max_consecutive_errors": "4",
            "async_max_backlog_ms": "120",
            "async_max_result_age_ms": "220",
            "async_max_consecutive_stale_results": "2",
            "async_stale_result_window_ms": "900",
        }
    )

    assert policy.stall_timeout_ms == 4500
    assert policy.max_consecutive_errors == 4
    assert policy.max_backlog_ms == 120
    assert policy.max_result_age_ms == 220
    assert policy.max_consecutive_stale_results == 2
    assert policy.stale_result_window_ms == 900


def test_one_invalid_setting_falls_back_atomically():
    logs: list[str] = []
    policy = parse_mediapipe_runtime_policy(
        {
            "async_stall_timeout_ms": 4_000,
            "async_max_consecutive_errors": 5,
            "async_max_backlog_ms": -1,
            "async_max_result_age_ms": 180,
            "async_max_consecutive_stale_results": 4,
            "async_stale_result_window_ms": 700,
        },
        logger=logs.append,
    )

    assert policy == MediaPipeRuntimePolicy()
    assert any("using safe defaults" in message for message in logs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stall_timeout_ms": 0},
        {"max_consecutive_errors": 0},
        {"max_backlog_ms": -1},
        {"max_result_age_ms": -1},
        {"max_consecutive_stale_results": -1},
        {"stale_result_window_ms": 0},
    ],
)
def test_invalid_policy_values_fail_closed(kwargs):
    with pytest.raises(ValueError):
        MediaPipeRuntimePolicy(**kwargs)


def test_zero_backlog_and_stale_threshold_remain_supported_opt_outs():
    policy = MediaPipeRuntimePolicy(
        max_backlog_ms=0,
        max_result_age_ms=0,
        max_consecutive_stale_results=0,
    )

    assert policy.max_backlog_ms == 0
    assert policy.max_result_age_ms == 0
    assert policy.max_consecutive_stale_results == 0
