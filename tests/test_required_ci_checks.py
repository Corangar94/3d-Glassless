from pathlib import Path


def _workflow(name: str) -> str:
    return (Path(".github/workflows") / name).read_text(encoding="utf-8")


def test_required_windows_matrix_and_portable_policy_job_exist():
    audit = _workflow("audit-regressions.yml")
    assert "python-version: ['3.11', '3.12']" in audit
    assert "python-version: ${{ matrix.python-version }}" in audit
    assert "portable-native-policies:" in audit
    assert "overlay/parallax_health_tests.cpp" in audit
    assert "overlay/depth_mode_policy_tests.cpp" in audit


def test_required_native_and_package_workflows_are_never_path_skipped():
    for name in ("native-overlay-build.yml", "windows-package.yml"):
        trigger = _workflow(name).split("permissions:", 1)[0]
        assert "pull_request:" in trigger
        assert "push:" in trigger
        assert "paths:" not in trigger
