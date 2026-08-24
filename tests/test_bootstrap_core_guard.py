import subprocess
import sys


def test_internal_bootstrap_core_refuses_direct_execution():
    result = subprocess.run(
        [sys.executable, "scripts/_bootstrap_core.py"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "scripts/bootstrap.py" in output
    assert "intentionally cannot run standalone" in output
