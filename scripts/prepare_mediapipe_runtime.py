"""Prepare the minimal MediaPipe Face Landmarker package used by PyInstaller.

MediaPipe's public ``tasks.python`` and ``vision`` package initializers import
all audio, text, GenAI, metadata, drawing, and vision tasks. Freezing through
those umbrella imports pulls unrelated packages such as matplotlib and
sounddevice into the Windows bundle. This module copies the exact Face
Landmarker dependency surface into a generated build tree with narrow package
initializers. Source installs continue to use the unmodified upstream wheel.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import shutil
from typing import Iterable


@dataclass(frozen=True)
class SlimMediaPipeRuntime:
    root: Path
    package_root: Path
    library_path: Path
    copied_files: tuple[str, ...]


def _installed_mediapipe_root() -> Path:
    spec = importlib.util.find_spec("mediapipe")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("mediapipe is not installed in the packaging environment")
    root = Path(next(iter(spec.submodule_search_locations))).resolve()
    if not root.is_dir():
        raise RuntimeError(f"mediapipe package directory is missing: {root}")
    return root


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"required MediaPipe runtime file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_python_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"required MediaPipe package directory is missing: {source}")
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix not in {".py", ".pyi"}:
            continue
        relative = path.relative_to(source)
        _copy_file(path, destination / relative)


def _write_initializer(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _inventory(root: Path) -> tuple[str, ...]:
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def prepare_slim_mediapipe_runtime(
    destination_root: str | Path,
    *,
    source_root: str | Path | None = None,
) -> SlimMediaPipeRuntime:
    """Create and validate a narrow Face Landmarker-only package tree."""
    source = (
        Path(source_root).resolve()
        if source_root is not None
        else _installed_mediapipe_root()
    )
    destination = Path(destination_root).resolve()
    if destination.exists():
        shutil.rmtree(destination)
    package = destination / "mediapipe"

    _copy_file(source / "__init__.py", package / "__init__.py")
    _copy_file(source / "tasks" / "__init__.py", package / "tasks" / "__init__.py")
    _copy_file(
        source / "tasks" / "c" / "__init__.py",
        package / "tasks" / "c" / "__init__.py",
    )
    library_name = "libmediapipe.dll"
    _copy_file(
        source / "tasks" / "c" / library_name,
        package / "tasks" / "c" / library_name,
    )

    _copy_python_tree(
        source / "tasks" / "python" / "core",
        package / "tasks" / "python" / "core",
    )
    _copy_python_tree(
        source / "tasks" / "python" / "components" / "containers",
        package / "tasks" / "python" / "components" / "containers",
    )
    _copy_file(
        source / "tasks" / "python" / "components" / "__init__.py",
        package / "tasks" / "python" / "components" / "__init__.py",
    )
    _copy_python_tree(
        source / "tasks" / "python" / "vision" / "core",
        package / "tasks" / "python" / "vision" / "core",
    )
    _copy_file(
        source / "tasks" / "python" / "vision" / "face_landmarker.py",
        package / "tasks" / "python" / "vision" / "face_landmarker.py",
    )

    # Avoid upstream umbrella initializers that import every MediaPipe task.
    _write_initializer(
        package / "tasks" / "python" / "__init__.py",
        '"""Slim Face Landmarker runtime generated for Glassless3D."""\n',
    )
    _write_initializer(
        package / "tasks" / "python" / "vision" / "__init__.py",
        '"""Slim MediaPipe vision runtime generated for Glassless3D."""\n',
    )

    library = package / "tasks" / "c" / library_name
    required_modules = (
        package / "tasks" / "python" / "core" / "base_options.py",
        package / "tasks" / "python" / "core" / "mediapipe_c_bindings.py",
        package / "tasks" / "python" / "vision" / "core" / "image.py",
        package / "tasks" / "python" / "vision" / "face_landmarker.py",
    )
    missing = [str(path) for path in (*required_modules, library) if not path.is_file()]
    if missing:
        raise RuntimeError("slim MediaPipe runtime is incomplete: " + ", ".join(missing))

    copied = _inventory(destination)
    manifest = {
        "schema_version": 1,
        "source_package": str(source),
        "purpose": "MediaPipe Face Landmarker runtime only",
        "files": list(copied),
    }
    (destination / "slim-mediapipe-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return SlimMediaPipeRuntime(
        root=destination,
        package_root=package,
        library_path=library,
        copied_files=copied,
    )


def forbidden_runtime_prefixes() -> tuple[str, ...]:
    """Modules intentionally excluded from the frozen face-tracking runtime."""
    return (
        "mediapipe.tasks.python.audio",
        "mediapipe.tasks.python.genai",
        "mediapipe.tasks.python.metadata",
        "mediapipe.tasks.python.text",
        "mediapipe.tasks.python.test",
        "mediapipe.tasks.python.vision.drawing_styles",
        "mediapipe.tasks.python.vision.drawing_utils",
        "mediapipe.tasks.python.vision.face_detector",
        "mediapipe.tasks.python.vision.gesture_recognizer",
        "mediapipe.tasks.python.vision.hand_landmarker",
        "mediapipe.tasks.python.vision.holistic_landmarker",
        "mediapipe.tasks.python.vision.image_classifier",
        "mediapipe.tasks.python.vision.image_embedder",
        "mediapipe.tasks.python.vision.image_segmenter",
        "mediapipe.tasks.python.vision.interactive_segmenter",
        "mediapipe.tasks.python.vision.object_detector",
        "mediapipe.tasks.python.vision.pose_landmarker",
    )
