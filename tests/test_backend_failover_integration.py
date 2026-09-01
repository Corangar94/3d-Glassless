from pathlib import Path

import yaml

from tracker.mediapipe_runtime_policy import MediaPipeRuntimePolicy


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_tracker_main_uses_runtime_factory_for_auto_mode():
    source = _source("tracker/main.py")

    assert "from tracker.backend_factory import (" in source
    assert "tracker, selected_backend = create_face_tracker(" in source
    assert "failover_policy=parse_backend_failover_policy(trk)" in source
    assert '"(active={selected_backend}, runtime failover enabled)"' in source
    assert "with (\n        tracker," in source
    assert "face_tracker_cls(" not in source


def test_explicit_class_loader_remains_available_for_direct_callers():
    source = _source("tracker/main.py")

    assert "def _load_face_tracker_class(backend: str):" in source
    assert "Retained strict class loader" in source


def test_frozen_package_explicitly_includes_failover_modules():
    source = _source("Glassless3D.spec")

    assert '"tracker.backend_factory"' in source
    assert '"tracker.backend_failover"' in source
    assert '"tracker.mediapipe_runtime_policy"' in source
    assert '"tracker.async_inference_watchdog"' in source
    assert '"tracker.async_callback_order"' in source
    assert '"tracker.async_result_freshness"' in source
    assert '"tracker.pose_result_timeline"' in source
    assert '"tracker.face_tracker"' in source
    assert '"tracker.face_tracker_cv2"' in source


def test_repository_config_enables_bounded_auto_failover():
    config = yaml.safe_load(_source("config.yaml"))
    tracking = config["tracking"]

    assert tracking["tracker_backend"] == "auto"
    assert tracking["backend_failover"] == {
        "retry_primary_after_ms": 30_000,
        "max_primary_retries": 1,
        "shadow_probe_interval_ms": 100,
        "shadow_probe_timeout_ms": 5_000,
        "minimum_healthy_callbacks": 3,
    }
    assert tracking["mediapipe_runtime"] == (
        MediaPipeRuntimePolicy().config_values()
    )


def test_mediapipe_exposes_no_face_callback_readiness():
    source = _source("tracker/face_tracker.py")

    assert "def async_health_snapshot(self):" in source
    assert "def ready_for_promotion(self) -> bool:" in source
    assert "snapshot.last_callback_ms is not None" in source
    assert "snapshot.consecutive_submission_errors == 0" in source
    assert "snapshot.consecutive_callback_errors == 0" in source
