from pathlib import Path

from launcher.__main__ import _select_main


def test_entrypoint_dispatches_private_self_test(monkeypatch):
    argv = ["Glassless3D.exe", "--self-test", "--output", "report.json"]

    selected = _select_main(argv)

    assert selected.__module__ == "launcher.frozen_self_test"
    assert selected.__name__ == "main"
    assert "--self-test" not in argv
    assert argv[-2:] == ["--output", "report.json"]


def test_self_test_covers_native_layout_qt_opencv_and_face_landmarker():
    source = Path("launcher/frozen_self_test.py").read_text(encoding="utf-8")

    assert "verify_python_runtime" in source
    assert "verify_qt" in source
    assert "verify_native_layout" in source
    assert "verify_face_landmarker" in source
    assert "find_overlay_exe" in source
    assert "missing_overlay_runtime_assets" in source
    assert "FaceLandmarker.create_from_options" in source
    assert "landmarker.detect(image)" in source
    assert "VideoCapture" not in source


def test_spec_includes_self_test_and_qt_tray_modules():
    source = Path("Glassless3D.spec").read_text(encoding="utf-8")

    assert '"launcher.frozen_self_test"' in source
    assert '"launcher.system_tray"' in source
