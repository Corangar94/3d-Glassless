import subprocess
import sys

import yaml

from scripts import calibrate_display_backend


def test_calibrate_display_backend_delegates_to_display_calibration_main(monkeypatch):
    calls = []
    monkeypatch.setattr(calibrate_display_backend.display_calibration, "main", lambda argv: calls.append(argv) or 13)

    code = calibrate_display_backend.main(["stereo_autostereo"])

    assert code == 13
    assert calls == [["stereo_autostereo"]]


def test_calibrate_display_backend_script_runs_from_repo_root(tmp_path):
    config = tmp_path / "config.yaml"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/calibrate_display_backend.py",
            "stereo_autostereo",
            "--config",
            str(config),
            "--viewer-distance-cm",
            "65",
            "--view-cone-deg",
            "35",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert data["overlay"]["display_backend"] == "stereo_autostereo"
    assert data["overlay"]["display_calibration"]["viewer_distance_cm"] == 65.0
