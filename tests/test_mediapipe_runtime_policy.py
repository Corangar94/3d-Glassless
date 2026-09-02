from __future__ import annotations

import pytest

from tracker.mediapipe_runtime_policy import (
    DEFAULT_MEDIAPIPE_INPUT_WIDTH_PX,
    MAX_MEDIAPIPE_INPUT_WIDTH_PX,
    MIN_MEDIAPIPE_INPUT_WIDTH_PX,
    MediaPipeRuntimePolicy,
    parse_mediapipe_runtime_policy,
    validated_mediapipe_input_width_px,
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
        max_input_width_px=960,
    )
    assert policy.tracker_kwargs() == {
        "async_stall_timeout_ms": 5_000,
        "async_max_consecutive_errors": 3,
        "async_max_backlog_ms": 150,
        "async_max_result_age_ms": 250,
        "async_max_consecutive_stale_results": 3,
        "async_stale_result_window_ms": 1_000,
        "max_input_width_px": 960,
    }
    assert policy.config_values() == {
        "stall_timeout_ms": 5_000,
        "max_consecutive_errors": 3,
        "max_backlog_ms": 150,
        "max_result_age_ms": 250,
        "max_consecutive_stale_results": 3,
        "stale_result_window_ms": 1_000,
        "max_input_width_px": 960,
    }
    assert DEFAULT_MEDIAPIPE_INPUT_WIDTH_PX == 960


def test_nested_policy_is_parsed_atomically():
    policy = parse_mediapipe_runtime_policy(
        {
            "mediapipe_runtime": {
                "stall_timeout_ms": 4_000,
                "max_consecutive_errors": 5,
                "max_backlog_ms": 90,
                "max_result_age_ms": 180,
                "max_consecutive_stale_results": 4,
                "stale_result_window_ms": 700,
                "max_input_width_px": 720,
            }
        }
    )

    assert policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=4_000,
        max_consecutive_errors=5,
        max_backlog_ms=90,
        max_result_age_ms=180,
        max_consecutive_stale_results=4,
        stale_result_window_ms=700,
        max_input_width_px=720,
    )


def test_numeric_strings_are_accepted_from_yaml_edits():
    policy = parse_mediapipe_runtime_policy(
        {
            "mediapipe_runtime": {
                "stall_timeout_ms": "4500",
                "max_consecutive_errors": "4",
                "max_backlog_ms": "120",
                "max_result_age_ms": "220",
                "max_consecutive_stale_results": "2",
                "stale_result_window_ms": "900",
                "max_input_width_px": "800",
            }
        }
    )

    assert policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=4_500,
        max_consecutive_errors=4,
        max_backlog_ms=120,
        max_result_age_ms=220,
        max_consecutive_stale_results=2,
        stale_result_window_ms=900,
        max_input_width_px=800,
    )


def test_valid_legacy_top_level_async_keys_remain_supported():
    policy = parse_mediapipe_runtime_policy(
        {
            "async_stall_timeout_ms": 4_200,
            "async_max_consecutive_errors": 4,
            "async_max_backlog_ms": 110,
            "async_max_result_age_ms": 210,
            "async_max_consecutive_stale_results": 2,
            "async_stale_result_window_ms": 850,
            "async_max_input_width_px": 640,
        }
    )

    assert policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=4_200,
        max_consecutive_errors=4,
        max_backlog_ms=110,
        max_result_age_ms=210,
        max_consecutive_stale_results=2,
        stale_result_window_ms=850,
        max_input_width_px=640,
    )


def test_nested_policy_wins_over_legacy_top_level_values():
    policy = parse_mediapipe_runtime_policy(
        {
            "async_stall_timeout_ms": 9_999,
            "async_max_backlog_ms": 999,
            "async_max_input_width_px": 640,
            "mediapipe_runtime": {
                "stall_timeout_ms": 4_000,
                "max_backlog_ms": 80,
                "max_input_width_px": 720,
            },
        }
    )

    assert policy.stall_timeout_ms == 4_000
    assert policy.max_backlog_ms == 80
    assert policy.max_consecutive_errors == 3
    assert policy.max_input_width_px == 720


def test_one_invalid_nested_setting_falls_back_atomically():
    logs: list[str] = []
    policy = parse_mediapipe_runtime_policy(
        {
            "mediapipe_runtime": {
                "stall_timeout_ms": 4_000,
                "max_consecutive_errors": 5,
                "max_backlog_ms": 90,
                "max_result_age_ms": 180,
                "max_consecutive_stale_results": 4,
                "stale_result_window_ms": 700,
                "max_input_width_px": 319,
            }
        },
        logger=logs.append,
    )

    assert policy == MediaPipeRuntimePolicy()
    assert any("using safe defaults" in message for message in logs)


def test_non_mapping_nested_policy_falls_back_atomically():
    logs: list[str] = []

    policy = parse_mediapipe_runtime_policy(
        {"mediapipe_runtime": ["not", "a", "mapping"]},
        logger=logs.append,
    )

    assert policy == MediaPipeRuntimePolicy()
    assert len(logs) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"stall_timeout_ms": 0},
        {"max_consecutive_errors": 0},
        {"max_backlog_ms": -1},
        {"max_result_age_ms": -1},
        {"max_consecutive_stale_results": -1},
        {"stale_result_window_ms": 0},
        {"max_input_width_px": -1},
        {"max_input_width_px": MIN_MEDIAPIPE_INPUT_WIDTH_PX - 1},
        {"max_input_width_px": MAX_MEDIAPIPE_INPUT_WIDTH_PX + 1},
        {"max_input_width_px": 960.5},
        {"max_input_width_px": True},
    ],
)
def test_invalid_policy_values_fail_closed(kwargs):
    with pytest.raises(ValueError):
        MediaPipeRuntimePolicy(**kwargs)


def test_zero_backlog_stale_and_input_width_remain_supported_opt_outs():
    policy = MediaPipeRuntimePolicy(
        max_backlog_ms=0,
        max_result_age_ms=0,
        max_consecutive_stale_results=0,
        max_input_width_px=0,
    )

    assert policy.max_backlog_ms == 0
    assert policy.max_result_age_ms == 0
    assert policy.max_consecutive_stale_results == 0
    assert policy.max_input_width_px == 0


def test_integral_direct_values_are_normalized_to_int():
    assert validated_mediapipe_input_width_px("960") == 960
    assert validated_mediapipe_input_width_px(960.0) == 960
    assert MediaPipeRuntimePolicy(max_input_width_px=960.0).max_input_width_px == 960
