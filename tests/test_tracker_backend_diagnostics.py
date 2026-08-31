from __future__ import annotations

import json
from pathlib import Path

from launcher.diagnostics import (
    DiagnosticsReport,
    format_diagnostics_json,
    format_diagnostics_report,
)
from launcher.tracker_backend_diagnostics import (
    configured_tracker_backend,
    evaluate_tracker_backend_status,
    format_tracker_backend_status,
    read_tracker_backend_status,
    tracker_backend_status_to_dict,
    tracker_backend_tile_text,
)
from tracker.backend_status_shared_memory import TrackerBackendStatus


def _status(**overrides) -> TrackerBackendStatus:
    values = {
        "configured_mode": "auto",
        "active_backend": "cv2",
        "failover_count": 2,
        "primary_retry_attempts": 1,
        "retry_in_ms": 5000,
        "candidate_active": False,
        "candidate_age_ms": None,
        "candidate_probe_count": 0,
        "candidate_healthy_callbacks": 0,
        "backend_transition_id": 3,
        "pose_transition_active": False,
        "pose_transition_preserves_position": True,
        "last_failure": "AsyncInferenceFailure: callback stalled",
        "timestamp_ms": 1000,
    }
    values.update(overrides)
    return TrackerBackendStatus(**values)


def _report(
    status: TrackerBackendStatus | None,
    *,
    fresh: bool,
) -> DiagnosticsReport:
    return DiagnosticsReport(
        project_root=Path("C:/Glassless3D"),
        python_executable=Path("python.exe"),
        overlay_exe=Path("Glassless3DOverlay.exe"),
        depth_model=Path("depth.onnx"),
        config_path=Path("config.yaml"),
        config_loaded=True,
        ready=True,
        problems=[],
        configured_tracker_backend="auto",
        tracker_backend_status=status,
        tracker_backend_status_fresh=fresh,
    )


def test_configured_tracker_backend_is_validated():
    assert configured_tracker_backend(None) == "auto"
    assert configured_tracker_backend(
        {"tracking": {"tracker_backend": "CV2"}}
    ) == "cv2"
    assert configured_tracker_backend(
        {"tracking": {"tracker_backend": "bad"}}
    ) == "unknown"


def test_auto_fallback_is_warning_not_readiness_problem():
    problems, warnings = evaluate_tracker_backend_status(
        "auto",
        _status(),
        True,
    )

    assert problems == []
    assert any("OpenCV fallback" in warning for warning in warnings)
    assert any("5000ms" in warning for warning in warnings)


def test_strict_backend_mismatch_is_a_problem():
    problems, warnings = evaluate_tracker_backend_status(
        "mediapipe",
        _status(configured_mode="mediapipe"),
        True,
    )

    assert any(
        "does not match strict configured backend" in item
        for item in problems
    )
    assert warnings == []


def test_running_tracker_mode_must_match_current_configuration():
    problems, _warnings = evaluate_tracker_backend_status(
        "auto",
        _status(configured_mode="mediapipe", active_backend="mediapipe"),
        True,
    )

    assert any(
        "running tracker mode mediapipe does not match configured mode auto"
        in item
        for item in problems
    )
    assert any("restart tracking" in item for item in problems)


def test_active_shadow_candidate_reports_age_probes_and_callbacks():
    status = _status(
        candidate_active=True,
        candidate_age_ms=750,
        candidate_probe_count=8,
        candidate_healthy_callbacks=2,
        retry_in_ms=None,
    )

    problems, warnings = evaluate_tracker_backend_status("auto", status, True)
    lines = format_tracker_backend_status(status, fresh=True)
    label, tooltip = tracker_backend_tile_text(status, fresh=True)

    assert problems == []
    assert any("750ms" in warning for warning in warnings)
    assert any("probes=8" in warning for warning in warnings)
    assert any("healthy_callbacks=2" in warning for warning in warnings)
    assert any("age_ms=750" in line for line in lines)
    assert label == "OpenCV + probe 2"
    assert "Candidate probes: 8" in tooltip


def test_stale_status_does_not_trigger_backend_mismatch():
    problems, warnings = evaluate_tracker_backend_status(
        "mediapipe",
        _status(configured_mode="mediapipe"),
        False,
    )

    assert problems == []
    assert any("stale" in warning for warning in warnings)


def test_text_report_contains_structured_backend_recovery_details():
    text = format_diagnostics_report(_report(_status(), fresh=True))

    assert "Configured tracker backend: auto" in text
    assert "Tracker backend: configured=auto active=cv2 (fresh)" in text
    assert "failovers=2" in text
    assert "retry_in_ms=5000" in text
    assert "AsyncInferenceFailure: callback stalled" in text


def test_json_report_contains_structured_backend_status():
    data = json.loads(format_diagnostics_json(_report(_status(), fresh=True)))

    assert data["configured_tracker_backend"] == "auto"
    assert data["tracker_backend_status"]["active_backend"] == "cv2"
    assert data["tracker_backend_status"]["fresh"] is True
    assert data["tracker_backend_status"]["retry_in_ms"] == 5000
    assert data["tracker_backend_status"]["last_failure"].startswith(
        "AsyncInferenceFailure"
    )


def test_status_dictionary_reports_age_and_candidate_telemetry():
    status = _status(
        candidate_active=True,
        candidate_age_ms=300,
        candidate_probe_count=4,
        candidate_healthy_callbacks=2,
    )

    data = tracker_backend_status_to_dict(status, fresh=True)

    assert data is not None
    assert data["candidate_active"] is True
    assert data["candidate_age_ms"] == 300
    assert data["candidate_probe_count"] == 4
    assert data["candidate_healthy_callbacks"] == 2


def test_backend_status_reader_freshness_is_evaluated(monkeypatch):
    status = _status(timestamp_ms=1000)

    class Reader:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self):
            return status

    monkeypatch.setattr(
        "launcher.tracker_backend_diagnostics.TrackerBackendStatusReader",
        Reader,
    )
    monkeypatch.setattr(
        "tracker.backend_status_shared_memory.monotonic_ms",
        lambda: 1200,
    )

    read, fresh = read_tracker_backend_status(max_age_ms=250)

    assert read is status
    assert fresh
