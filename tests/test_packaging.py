from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_defines_runtime_and_dev_dependencies():
    import tomllib

    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert payload["build-system"]["build-backend"] == "hatchling.build"
    assert payload["project"]["requires-python"] == ">=3.11,<3.13"
    assert "dev" in payload["project"]["optional-dependencies"]
    assert any(
        dependency.startswith("pyinstaller")
        for dependency in payload["project"]["optional-dependencies"]["dev"]
    )
    assert payload["project"]["scripts"]["glassless3d"] == "launcher.app:main"


def test_setup_py_is_a_fail_closed_retired_installer_shim():
    source = (ROOT / "setup.py").read_text(encoding="utf-8")

    assert "legacy setup.py game installer is retired" in source
    assert "Online games must use non-injecting capture" in source
    assert "from setuptools" not in source


def test_pyinstaller_does_not_redistribute_reshade_binaries_or_shaders():
    source = (ROOT / "Glassless3D.spec").read_text(encoding="utf-8")

    datas = source.split("datas=[", 1)[1].split("],", 1)[0]
    assert "ReShade32.dll" not in datas
    assert "ReShade64.dll" not in datas
    assert "Glassless3D.addon" not in datas
    assert "shaders/Glassless3D" not in datas
    assert "reshade.me" in datas


def test_pyinstaller_includes_frozen_tracker_child_modules():
    source = (ROOT / "Glassless3D.spec").read_text(encoding="utf-8")

    assert '"tracker.main"' in source
    assert '"tracker.face_tracker"' in source
    assert '"tracker.face_tracker_cv2"' in source


def test_pyinstaller_bundles_standalone_runtime_and_models():
    source = (ROOT / "Glassless3D.spec").read_text(encoding="utf-8")

    assert '("Glassless3DOverlay.exe", ".")' in source
    assert '("onnxruntime.dll", ".")' in source
    assert '("DirectML.dll", ".")' in source
    assert '("models/face_landmarker.task", "models")' in source
    assert (
        '("models/depth_anything_v2_small_fp16.onnx", "models")'
        in source
    )


def test_frozen_entrypoint_dispatches_private_tracker_child():
    source = (ROOT / "launcher" / "__main__.py").read_text(encoding="utf-8")

    assert '"--tracker-child"' in source
    assert "from tracker.main import main" in source


def test_bootstrap_pins_current_official_reshade_release():
    from scripts import bootstrap

    assert bootstrap.RESHADE_VERSION == "6.7.3"
    assert bootstrap.RESHADE_URL == "https://reshade.me/downloads/ReShade_Setup_6.7.3_Addon.exe"
    assert len(bootstrap.RESHADE_INSTALLER_SHA256) == 64
