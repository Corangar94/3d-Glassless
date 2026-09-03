from __future__ import annotations

from pathlib import Path

import pytest

from tracker.backend_factory import (
    ConfiguredBackendFailoverPolicy,
    parse_backend_failover_policy,
)
from tracker.backend_failover import BackendFailoverPolicy
from tracker.mediapipe_runtime_policy import MediaPipeRuntimePolicy


_FIELDS = (
    "retry_primary_after_ms",
    "max_primary_retries",
    "shadow_probe_interval_ms",
    "shadow_probe_timeout_ms",
    "minimum_healthy_callbacks",
)


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("value", [True, 7.5, "7.0"])
def test_invalid_configured_integer_falls_back_atomically(field, value):
    logs: list[str] = []

    policy = parse_backend_failover_policy(
        {"backend_failover": {field: value}},
        logger=logs.append,
    )

    defaults = BackendFailoverPolicy()
    assert policy.retry_primary_after_ms == defaults.retry_primary_after_ms
    assert policy.max_primary_retries == defaults.max_primary_retries
    assert policy.shadow_probe_interval_ms == defaults.shadow_probe_interval_ms
    assert policy.shadow_probe_timeout_ms == defaults.shadow_probe_timeout_ms
    assert policy.minimum_healthy_callbacks == defaults.minimum_healthy_callbacks
    assert len(logs) == 1
    assert "using safe defaults" in logs[0]


def test_nonmapping_failover_block_falls_back_with_one_diagnostic():
    logs: list[str] = []

    policy = parse_backend_failover_policy(
        {"backend_failover": ["not", "a", "mapping"]},
        logger=logs.append,
    )

    assert policy == ConfiguredBackendFailoverPolicy()
    assert logs == [
        "[G3D] Invalid tracker backend failover settings; using safe defaults"
    ]


def test_integer_strings_remain_supported_for_every_field():
    policy = parse_backend_failover_policy(
        {
            "backend_failover": {
                "retry_primary_after_ms": "12000",
                "max_primary_retries": "2",
                "shadow_probe_interval_ms": "80",
                "shadow_probe_timeout_ms": "4500",
                "minimum_healthy_callbacks": "4",
            }
        }
    )

    assert policy == ConfiguredBackendFailoverPolicy(
        retry_primary_after_ms=12_000,
        max_primary_retries=2,
        shadow_probe_interval_ms=80,
        shadow_probe_timeout_ms=4_500,
        minimum_healthy_callbacks=4,
    )


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("value", [True, 5.0, 5.5, "5"])
def test_direct_configured_policy_requires_real_integral_fields(field, value):
    with pytest.raises(ValueError, match="must be an integer"):
        ConfiguredBackendFailoverPolicy(**{field: value})


def test_direct_configured_policy_normalizes_integral_subclasses():
    class IntegralSubclass(int):
        pass

    policy = ConfiguredBackendFailoverPolicy(
        retry_primary_after_ms=IntegralSubclass(12_000),
        max_primary_retries=IntegralSubclass(2),
        shadow_probe_interval_ms=IntegralSubclass(80),
        shadow_probe_timeout_ms=IntegralSubclass(4_500),
        minimum_healthy_callbacks=IntegralSubclass(4),
    )

    for field in _FIELDS:
        assert type(getattr(policy, field)) is int


def test_zero_opt_outs_remain_valid_only_for_supported_fields():
    policy = ConfiguredBackendFailoverPolicy(
        retry_primary_after_ms=0,
        max_primary_retries=0,
        shadow_probe_interval_ms=0,
    )

    assert policy.retry_primary_after_ms == 0
    assert policy.max_primary_retries == 0
    assert policy.shadow_probe_interval_ms == 0

    for field in ("shadow_probe_timeout_ms", "minimum_healthy_callbacks"):
        with pytest.raises(ValueError):
            ConfiguredBackendFailoverPolicy(**{field: 0})


def test_invalid_failover_does_not_discard_valid_mediapipe_policy():
    logs: list[str] = []

    policy = parse_backend_failover_policy(
        {
            "backend_failover": {
                "retry_primary_after_ms": False,
            },
            "mediapipe_runtime": {
                "stall_timeout_ms": 4_000,
                "max_consecutive_errors": 4,
                "max_backlog_ms": 120,
                "max_result_age_ms": 220,
                "max_consecutive_stale_results": 2,
                "stale_result_window_ms": 900,
                "max_input_width_px": 800,
            },
        },
        logger=logs.append,
    )

    assert policy.retry_primary_after_ms == 30_000
    assert policy.mediapipe_runtime_policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=4_000,
        max_consecutive_errors=4,
        max_backlog_ms=120,
        max_result_age_ms=220,
        max_consecutive_stale_results=2,
        stale_result_window_ms=900,
        max_input_width_px=800,
    )
    assert len(logs) == 1


def test_configured_policy_requires_valid_mediapipe_policy_object():
    with pytest.raises(ValueError, match="mediapipe_runtime_policy"):
        ConfiguredBackendFailoverPolicy(
            mediapipe_runtime_policy=object(),  # type: ignore[arg-type]
        )


def test_parser_uses_strict_helper_for_every_failover_field():
    source = Path("tracker/backend_factory.py").read_text(encoding="utf-8")
    parser = source.split("def parse_backend_failover_policy(", 1)[1].split(
        "\ndef _tracker_class(",
        1,
    )[0]

    assert parser.count("_parse_integer(") == 5
    assert "int(" not in parser
    assert "values = raw if isinstance(raw, dict) else None" in parser
    for field in _FIELDS:
        assert f'"{field}"' in parser
