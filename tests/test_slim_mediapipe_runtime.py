from __future__ import annotations

import json
from pathlib import Path

from scripts.prepare_mediapipe_runtime import (
    forbidden_runtime_prefixes,
    prepare_slim_mediapipe_runtime,
)


def _write(path: Path, content: bytes = b"# generated test fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _fake_mediapipe(root: Path) -> Path:
    package = root / "mediapipe"
    _write(package / "__init__.py")
    _write(package / "tasks" / "__init__.py")
    _write(package / "tasks" / "c" / "__init__.py")
    _write(package / "tasks" / "c" / "libmediapipe.dll", b"MZfixture")
    for relative in (
        "tasks/python/core/__init__.py",
        "tasks/python/core/base_options.py",
        "tasks/python/core/base_options_c.py",
        "tasks/python/core/mediapipe_c_bindings.py",
        "tasks/python/core/mediapipe_c_utils.py",
        "tasks/python/components/__init__.py",
        "tasks/python/components/containers/category.py",
        "tasks/python/components/containers/category_c.py",
        "tasks/python/components/containers/landmark.py",
        "tasks/python/components/containers/landmark_c.py",
        "tasks/python/components/containers/matrix_c.py",
        "tasks/python/vision/core/__init__.py",
        "tasks/python/vision/core/image.py",
        "tasks/python/vision/core/vision_task_running_mode.py",
        "tasks/python/vision/face_landmarker.py",
    ):
        _write(package / relative)
    # Unrelated upstream tasks must never enter the generated runtime.
    _write(package / "tasks" / "python" / "audio" / "audio_classifier.py")
    _write(package / "tasks" / "python" / "genai" / "llm_inference.py")
    _write(package / "tasks" / "python" / "vision" / "pose_landmarker.py")
    return package


def test_prepare_slim_runtime_copies_only_face_landmarker_surface(tmp_path):
    source = _fake_mediapipe(tmp_path / "source")
    output = tmp_path / "output"

    result = prepare_slim_mediapipe_runtime(output, source_root=source)

    assert result.library_path.is_file()
    assert (
        result.package_root
        / "tasks"
        / "python"
        / "vision"
        / "face_landmarker.py"
    ).is_file()
    assert not (
        result.package_root
        / "tasks"
        / "python"
        / "audio"
        / "audio_classifier.py"
    ).exists()
    assert not (
        result.package_root
        / "tasks"
        / "python"
        / "genai"
        / "llm_inference.py"
    ).exists()
    assert not (
        result.package_root
        / "tasks"
        / "python"
        / "vision"
        / "pose_landmarker.py"
    ).exists()
    initializer = (
        result.package_root / "tasks" / "python" / "vision" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "Slim MediaPipe vision runtime" in initializer
    manifest = json.loads(
        (output / "slim-mediapipe-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["purpose"] == "MediaPipe Face Landmarker runtime only"
    assert "mediapipe/tasks/c/libmediapipe.dll" in manifest["files"]


def test_prepare_slim_runtime_replaces_stale_output(tmp_path):
    source = _fake_mediapipe(tmp_path / "source")
    output = tmp_path / "output"
    stale = output / "mediapipe" / "tasks" / "python" / "audio" / "stale.py"
    _write(stale)

    prepare_slim_mediapipe_runtime(output, source_root=source)

    assert not stale.exists()


def test_spec_uses_slim_runtime_instead_of_collect_all():
    source = Path("Glassless3D.spec").read_text(encoding="utf-8")

    assert "prepare_slim_mediapipe_runtime" in source
    assert "collect_all" not in source
    assert "pathex=[str(slim_mediapipe.root), \".\"]" in source
    assert '"matplotlib"' in source
    assert '"PIL"' in source
    assert '"sounddevice"' in source
    assert "str(slim_mediapipe.library_path)" in source


def test_tracker_and_calibration_avoid_umbrella_mediapipe_imports():
    tracker = Path("tracker/face_tracker.py").read_text(encoding="utf-8")
    calibration = Path("launcher/calibration.py").read_text(encoding="utf-8")

    assert "import mediapipe as" not in tracker
    assert "from mediapipe import tasks" not in tracker
    assert "import mediapipe as" not in calibration
    assert "from mediapipe import tasks" not in calibration
    assert "mediapipe.tasks.python.vision.face_landmarker" in tracker
    assert "mediapipe.tasks.python.vision.face_landmarker" in calibration


def test_forbidden_prefixes_cover_unrelated_tasks():
    prefixes = forbidden_runtime_prefixes()

    assert "mediapipe.tasks.python.audio" in prefixes
    assert "mediapipe.tasks.python.genai" in prefixes
    assert "mediapipe.tasks.python.text" in prefixes
    assert "mediapipe.tasks.python.vision.pose_landmarker" in prefixes
