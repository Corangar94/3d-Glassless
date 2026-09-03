from __future__ import annotations

from pathlib import Path

import pytest

from tracker.latest_frame_capture import (
    LatestFrameCapturePolicy,
    parse_latest_frame_capture_policy,
)


@pytest.mark.parametrize(
    "field,value",
    [
        ("wait_timeout_ms", True),
        ("wait_timeout_ms", 750.5),
        ("wait_timeout_ms", "750.0"),
        ("max_frame_age_ms", False),
        ("max_frame_age_ms", 250.5),
        ("max_frame_age_ms", "250.0"),
        ("freeze_check_interval_ms", True),
        ("freeze_check_interval_ms", 250.5),
        ("freeze_check_interval_ms", "250.0"),
        ("freeze_timeout_ms", False),
        ("freeze_timeout_ms", 3000.5),
        ("freeze_timeout_ms", "3000.0"),
        ("failure_backoff_ms", True),
        ("failure_backoff_ms", 20.5),
        ("failure_backoff_ms", "20.0"),
        ("shutdown_timeout_ms", False),
        ("shutdown_timeout_ms", 1000.5),
        ("shutdown_timeout_ms", "1000.0"),
    ],
)
def test_fractional_boolean_or_decimal_string_timing_falls_back_atomically(
    field,
    value,
):
    logs: list[str] = []

    policy = parse_latest_frame_capture_policy(
        {"latest_frame": {field: value}},
        logger=logs.append,
    )

    assert policy == LatestFrameCapturePolicy()
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]


def test_integer_strings_for_every_timing_field_remain_supported():
    policy = parse_latest_frame_capture_policy(
        {
            "latest_frame": {
                "enabled": "false",
                "wait_timeout_ms": "750",
                "max_frame_age_ms": "225",
                "freeze_check_interval_ms": "200",
                "freeze_timeout_ms": "2750",
                "failure_backoff_ms": "25",
                "shutdown_timeout_ms": "900",
            }
        }
    )

    assert policy == LatestFrameCapturePolicy(
        enabled=False,
        wait_timeout_ms=750,
        max_frame_age_ms=225,
        freeze_check_interval_ms=200,
        freeze_timeout_ms=2750,
        failure_backoff_ms=25,
        shutdown_timeout_ms=900,
    )


def test_all_minimum_timing_boundaries_are_accepted():
    policy = parse_latest_frame_capture_policy(
        {
            "latest_frame": {
                "wait_timeout_ms": 1,
                "max_frame_age_ms": 0,
                "freeze_check_interval_ms": 0,
                "freeze_timeout_ms": 0,
                "failure_backoff_ms": 0,
                "shutdown_timeout_ms": 0,
            }
        }
    )

    assert policy.wait_timeout_ms == 1
    assert policy.max_frame_age_ms == 0
    assert policy.freeze_check_interval_ms == 0
    assert policy.freeze_timeout_ms == 0
    assert policy.failure_backoff_ms == 0
    assert policy.shutdown_timeout_ms == 0


def test_all_maximum_timing_boundaries_are_accepted():
    policy = parse_latest_frame_capture_policy(
        {
            "latest_frame": {
                "wait_timeout_ms": 60_000,
                "max_frame_age_ms": 60_000,
                "freeze_check_interval_ms": 60_000,
                "freeze_timeout_ms": 60_000,
                "failure_backoff_ms": 10_000,
                "shutdown_timeout_ms": 60_000,
            }
        }
    )

    assert policy == LatestFrameCapturePolicy(
        wait_timeout_ms=60_000,
        max_frame_age_ms=60_000,
        freeze_check_interval_ms=60_000,
        freeze_timeout_ms=60_000,
        failure_backoff_ms=10_000,
        shutdown_timeout_ms=60_000,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("wait_timeout_ms", True),
        ("wait_timeout_ms", 1.5),
        ("max_frame_age_ms", False),
        ("max_frame_age_ms", 0.5),
        ("freeze_check_interval_ms", True),
        ("freeze_timeout_ms", 1.5),
        ("failure_backoff_ms", False),
        ("shutdown_timeout_ms", 1.5),
    ],
)
def test_direct_policy_rejects_non_integer_timing_values(field, value):
    with pytest.raises(ValueError, match="must be an integer"):
        LatestFrameCapturePolicy(**{field: value})


def test_parser_uses_strict_helper_for_every_timing_field():
    source = Path("tracker/latest_frame_capture.py").read_text(
        encoding="utf-8"
    )
    parser = source.split(
        "def parse_latest_frame_capture_policy(",
        1,
    )[1].split("\ndef _normalized_read_result(", 1)[0]

    for field in (
        "wait_timeout_ms",
        "max_frame_age_ms",
        "freeze_check_interval_ms",
        "freeze_timeout_ms",
        "failure_backoff_ms",
        "shutdown_timeout_ms",
    ):
        assert f'"{field}",' in parser
    assert parser.count("_parse_integer(") == 6
    assert "int(values.get(" not in parser
