import subprocess
import sys


SCRIPT_WRAPPERS = [
    "scripts/calibrate_display_backend.py",
    "scripts/audit_completion.py",
    "scripts/collect_support.py",
    "scripts/compare_depth_fixture.py",
    "scripts/export_overlay_timings.py",
    "scripts/generate_depth_confidence.py",
    "scripts/generate_depth_fixture.py",
    "scripts/generate_stereo_validation.py",
    "scripts/import_depth_capture.py",
    "scripts/render_views.py",
    "scripts/run_comfort_evaluation.py",
    "scripts/run_display_acceptance.py",
    "scripts/run_display_quality.py",
    "scripts/run_evaluation.py",
    "scripts/run_friendly_depth_experiment.py",
    "scripts/run_latency_evaluation.py",
    "scripts/run_live_runtime_check.py",
    "scripts/run_settings_writer.py",
    "scripts/set_display_backend.py",
]


def test_script_wrappers_show_help_when_run_directly_from_repo_root():
    for script in SCRIPT_WRAPPERS:
        result = subprocess.run(
            [sys.executable, script, "--help"],
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{script} failed:\n{result.stderr}"
        assert "usage:" in result.stdout.lower()
