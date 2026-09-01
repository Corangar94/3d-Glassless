from tracker.backend_factory import (
    ConfiguredBackendFailoverPolicy,
    parse_backend_failover_policy,
)
from tracker.mediapipe_runtime_policy import MediaPipeRuntimePolicy


def _failover_values(policy):
    return (
        policy.retry_primary_after_ms,
        policy.max_primary_retries,
        policy.shadow_probe_interval_ms,
        policy.shadow_probe_timeout_ms,
        policy.minimum_healthy_callbacks,
    )


def test_packaged_defaults_sample_shadow_mediapipe_at_ten_hz():
    policy = parse_backend_failover_policy({})

    assert isinstance(policy, ConfiguredBackendFailoverPolicy)
    assert _failover_values(policy) == (30_000, 1, 100, 5_000, 3)
    assert policy.mediapipe_runtime_policy == MediaPipeRuntimePolicy()


def test_explicit_shadow_recovery_policy_is_parsed():
    policy = parse_backend_failover_policy(
        {
            "backend_failover": {
                "retry_primary_after_ms": 12_000,
                "max_primary_retries": 2,
                "shadow_probe_interval_ms": 250,
                "shadow_probe_timeout_ms": 4_000,
                "minimum_healthy_callbacks": 5,
            }
        }
    )

    assert _failover_values(policy) == (12_000, 2, 250, 4_000, 5)


def test_invalid_shadow_recovery_policy_falls_back_atomically(capsys):
    policy = parse_backend_failover_policy(
        {
            "backend_failover": {
                "retry_primary_after_ms": 1,
                "shadow_probe_interval_ms": -1,
                "shadow_probe_timeout_ms": 0,
                "minimum_healthy_callbacks": 0,
            }
        }
    )

    assert _failover_values(policy) == (30_000, 1, 100, 5_000, 3)
    assert policy.mediapipe_runtime_policy == MediaPipeRuntimePolicy()
    assert "using safe defaults" in capsys.readouterr().out


def test_repository_config_exposes_shadow_recovery_controls():
    source = open("config.yaml", encoding="utf-8").read()

    assert "shadow_probe_interval_ms: 100" in source
    assert "shadow_probe_timeout_ms: 5000" in source
    assert "minimum_healthy_callbacks: 3" in source
