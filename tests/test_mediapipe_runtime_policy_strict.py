from __future__ import annotations

from pathlib import Path

import pytest

from tracker.mediapipe_runtime_policy import (
    MediaPipeRuntimePolicy,
    parse_mediapipe_runtime_policy,
)


_NESTED_FIELDS = (
    "stall_timeout_ms",
    "max_consecutive_errors",
    "max_backlog_ms",
    "max_result_age_ms",
    "max_consecutive_stale_results",
    "stale_result_window_ms",
)
_LEGACY_FIELDS = tuple(f"async_{field}" for field in _NESTED_FIELDS)


@pytest.mark.parametrize("field", _NESTED_FIELDS)
@pytest.mark.parametrize("value", [True, 7.5, "7.0"])
def test_invalid_nested_integer_falls_back_atomically(field, value):
    logs: list[str] = []

    policy = parse_mediapipe_runtime_policy(
        {"mediapipe_runtime": {field: value}},
        logger=logs.append,
    )

    assert policy == MediaPipeRuntimePolicy()
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]


@pytest.mark.parametrize("field", _LEGACY_FIELDS)
@pytest.mark.parametrize("value", [False, 12.5, "12.0"])
def test_invalid_legacy_integer_falls_back_atomically(field, value):
    logs: list[str] = []

    policy = parse_mediapipe_runtime_policy(
        {field: value},
        logger=logs.append,
    )

    assert policy == MediaPipeRuntimePolicy()
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]


def test_nested_integer_strings_remain_supported_for_every_field():
    policy = parse_mediapipe_runtime_policy(
        {
            "mediapipe_runtime": {
                "stall_timeout_ms": "4500",
                "max_consecutive_errors": "4",
                "max_backlog_ms": "120",
                "max_result_age_ms": "220",
                "max_consecutive_stale_results": "2",
                "stale_result_window_ms": "900",
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
    )


def test_legacy_integer_strings_remain_supported_for_every_field():
    policy = parse_mediapipe_runtime_policy(
        {
            "async_stall_timeout_ms": "4200",
            "async_max_consecutive_errors": "5",
            "async_max_backlog_ms": "110",
            "async_max_result_age_ms": "210",
            "async_max_consecutive_stale_results": "4",
            "async_stale_result_window_ms": "850",
        }
    )

    assert policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=4_200,
        max_consecutive_errors=5,
        max_backlog_ms=110,
        max_result_age_ms=210,
        max_consecutive_stale_results=4,
        stale_result_window_ms=850,
    )


@pytest.mark.parametrize("field", _NESTED_FIELDS)
@pytest.mark.parametrize("value", [True, 5.0, 5.5, "5"])
def test_direct_policy_requires_real_integral_runtime_fields(field, value):
    with pytest.raises(ValueError, match="must be an integer"):
        MediaPipeRuntimePolicy(**{field: value})


def test_direct_policy_normalizes_integral_subclasses_to_builtin_int():
    class IntegralSubclass(int):
        pass

    policy = MediaPipeRuntimePolicy(
        stall_timeout_ms=IntegralSubclass(4_500),
        max_consecutive_errors=IntegralSubclass(4),
        max_backlog_ms=IntegralSubclass(120),
        max_result_age_ms=IntegralSubclass(220),
        max_consecutive_stale_results=IntegralSubclass(2),
        stale_result_window_ms=IntegralSubclass(900),
    )

    for field in _NESTED_FIELDS:
        assert type(getattr(policy, field)) is int


def test_zero_opt_outs_remain_valid_only_for_supported_fields():
    policy = MediaPipeRuntimePolicy(
        max_backlog_ms=0,
        max_result_age_ms=0,
        max_consecutive_stale_results=0,
    )

    assert policy.max_backlog_ms == 0
    assert policy.max_result_age_ms == 0
    assert policy.max_consecutive_stale_results == 0

    for field in (
        "stall_timeout_ms",
        "max_consecutive_errors",
        "stale_result_window_ms",
    ):
        with pytest.raises(ValueError):
            MediaPipeRuntimePolicy(**{field: 0})


def test_parser_routes_nested_and_legacy_fields_through_strict_helper():
    source = Path("tracker/mediapipe_runtime_policy.py").read_text(
        encoding="utf-8"
    )
    builder = source.split("def _policy_from_values(", 1)[1].split(
        "\ndef parse_mediapipe_runtime_policy(",
        1,
    )[0]
    parser = source.split("def parse_mediapipe_runtime_policy(", 1)[1]

    assert builder.count("_parse_integer(") == 6
    assert 'prefix="async_"' in parser
    assert "int(" not in builder
    for field in _NESTED_FIELDS:
        assert f'"{field}"' in builder
