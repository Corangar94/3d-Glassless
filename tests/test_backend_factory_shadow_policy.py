from tracker.backend_factory import parse_backend_failover_policy
from tracker.backend_failover import BackendFailoverPolicy


def test_packaged_defaults_sample_shadow_mediapipe_at_ten_hz():
    policy = parse_backend_failover_policy({})

    assert policy.retry_primary_after_ms == 30_000
    assert policy.max_primary_retries == 1
    assert policy.shadow_probe_interval_ms == 100
    assert policy.shadow_probe_timeout_ms == 5_000
    assert policy.minimum_healthy_callbacks == 3


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

    assert policy == BackendFailoverPolicy(
        retry_primary_after_ms=12_000,
        max_primary_retries=2,
        shadow_probe_interval_ms=250,
        shadow_probe_timeout_ms=4_000,
        minimum_healthy_callbacks=5,
    )


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

    assert policy == BackendFailoverPolicy()
    assert "using safe defaults" in capsys.readouterr().out


def test_repository_config_exposes_shadow_recovery_controls():
    source = open("config.yaml", encoding="utf-8").read()

    assert "shadow_probe_interval_ms: 100" in source
    assert "shadow_probe_timeout_ms: 5000" in source
    assert "minimum_healthy_callbacks: 3" in source
