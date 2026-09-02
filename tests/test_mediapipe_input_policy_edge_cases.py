from __future__ import annotations

import pytest

from tracker.mediapipe_runtime_policy import (
    MediaPipeRuntimePolicy,
    parse_mediapipe_runtime_policy,
)


@pytest.mark.parametrize(
    "value",
    [319, 8193, 960.5, True, None, "960.5"],
)
def test_invalid_nested_input_width_falls_back_atomically(value):
    logs: list[str] = []

    policy = parse_mediapipe_runtime_policy(
        {
            "mediapipe_runtime": {
                "stall_timeout_ms": 4_000,
                "max_backlog_ms": 90,
                "max_input_width_px": value,
            }
        },
        logger=logs.append,
    )

    assert policy == MediaPipeRuntimePolicy()
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]


def test_integral_float_and_numeric_string_configuration_are_accepted():
    float_policy = parse_mediapipe_runtime_policy(
        {"mediapipe_runtime": {"max_input_width_px": 960.0}}
    )
    string_policy = parse_mediapipe_runtime_policy(
        {"mediapipe_runtime": {"max_input_width_px": "720"}}
    )

    assert float_policy.max_input_width_px == 960
    assert string_policy.max_input_width_px == 720


def test_zero_cap_is_preserved_by_nested_and_legacy_parsers():
    nested = parse_mediapipe_runtime_policy(
        {"mediapipe_runtime": {"max_input_width_px": 0}}
    )
    legacy = parse_mediapipe_runtime_policy(
        {"async_max_input_width_px": 0}
    )

    assert nested.max_input_width_px == 0
    assert legacy.max_input_width_px == 0
