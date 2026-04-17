import subprocess
import sys

from scripts import collect_support


def test_collect_support_delegates_to_support_bundle_main(monkeypatch):
    calls = []
    monkeypatch.setattr(collect_support.support_bundle, "main", lambda argv: calls.append(argv) or 7)

    code = collect_support.main(["--output-dir", "bundle"])

    assert code == 7
    assert calls == [["--output-dir", "bundle"]]


def test_collect_support_script_is_runnable_from_outside_repo(tmp_path):
    result = subprocess.run(
        [sys.executable, str(collect_support.__file__), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Create a Glassless3D support bundle" in result.stdout
