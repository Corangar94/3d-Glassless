from __future__ import annotations

import yaml
from PySide6.QtWidgets import QApplication

from launcher.wizard import DonePage
from tracker.mediapipe_runtime_policy import MediaPipeRuntimePolicy


def test_setup_writes_automatic_backend_and_mediapipe_defaults(tmp_path):
    _app = QApplication.instance() or QApplication([])
    config_path = tmp_path / "config.yaml"
    page = DonePage(config_path=str(config_path))
    page._write_config()

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
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
    assert "async_stall_timeout_ms" not in tracking
    assert "async_max_consecutive_errors" not in tracking
