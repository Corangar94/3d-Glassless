"""No-camera standalone runtime self-test.

This exercises the pieces most likely to be broken by bundle slimming: Qt,
OpenCV, the native overlay layout, DirectML/ONNX Runtime DLLs, the MediaPipe C
library, the face model, and one blank Face Landmarker inference. It does not
open a webcam or launch the overlay.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback
from typing import Callable


def _record(
    report: dict[str, object],
    name: str,
    operation: Callable[[], object],
) -> None:
    try:
        detail = operation()
    except Exception as error:  # noqa: BLE001 - diagnostic boundary
        report[name] = {
            "passed": False,
            "error": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        }
    else:
        report[name] = {"passed": True, "detail": detail}


def run_self_test() -> dict[str, object]:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    report: dict[str, object] = {
        "schema_version": 1,
        "frozen": bool(getattr(sys, "frozen", False)),
        "executable": sys.executable,
    }

    def verify_python_runtime() -> dict[str, object]:
        import cv2
        import numpy as np
        import yaml

        frame = np.zeros((32, 32, 3), dtype=np.uint8)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        parsed = yaml.safe_load("ok: true\n")
        if gray.shape != (32, 32) or parsed != {"ok": True}:
            raise RuntimeError("OpenCV/NumPy/YAML smoke operation failed")
        return {
            "opencv": cv2.__version__,
            "numpy": np.__version__,
        }

    def verify_qt() -> str:
        from PySide6.QtWidgets import QApplication, QLabel

        app = QApplication.instance() or QApplication(
            ["Glassless3D-self-test", "-platform", "offscreen"]
        )
        label = QLabel("Glassless3D")
        label.ensurePolished()
        app.processEvents()
        if label.text() != "Glassless3D":
            raise RuntimeError("Qt widget smoke operation failed")
        label.deleteLater()
        return "Qt Widgets initialized offscreen"

    def verify_native_layout() -> dict[str, object]:
        from launcher.overlay_process import (
            _project_root,
            find_depth_model,
            find_overlay_exe,
            missing_overlay_runtime_assets,
        )

        root = _project_root()
        overlay = find_overlay_exe()
        depth_model = find_depth_model()
        if overlay is None:
            raise FileNotFoundError("Glassless3DOverlay.exe was not found")
        if depth_model is None:
            raise FileNotFoundError("supported depth model was not found")
        missing = missing_overlay_runtime_assets(overlay)
        if missing:
            raise FileNotFoundError(
                "native runtime layout is incomplete: " + ", ".join(missing)
            )
        face_model = root / "models" / "face_landmarker.task"
        if not face_model.is_file():
            raise FileNotFoundError(f"face model was not found: {face_model}")
        return {
            "runtime_root": str(root),
            "overlay": str(overlay),
            "depth_model": str(depth_model),
            "face_model": str(face_model),
        }

    def verify_face_landmarker() -> dict[str, object]:
        import numpy as np
        from mediapipe.tasks.python.core.base_options import BaseOptions
        from mediapipe.tasks.python.vision.core.image import Image, ImageFormat
        from mediapipe.tasks.python.vision.core.vision_task_running_mode import (
            VisionTaskRunningMode,
        )
        from mediapipe.tasks.python.vision.face_landmarker import (
            FaceLandmarker,
            FaceLandmarkerOptions,
        )
        from launcher.overlay_process import _project_root

        model = _project_root() / "models" / "face_landmarker.task"
        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=VisionTaskRunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
        )
        landmarker = FaceLandmarker.create_from_options(options)
        try:
            pixels = np.zeros((128, 128, 3), dtype=np.uint8)
            image = Image(image_format=ImageFormat.SRGB, data=pixels)
            result = landmarker.detect(image)
        finally:
            landmarker.close()
        return {
            "blank_faces": len(result.face_landmarks),
            "model_bytes": model.stat().st_size,
        }

    _record(report, "python_runtime", verify_python_runtime)
    _record(report, "qt", verify_qt)
    _record(report, "native_layout", verify_native_layout)
    _record(report, "face_landmarker", verify_face_landmarker)
    checks = [value for key, value in report.items() if key not in {"schema_version", "frozen", "executable"}]
    report["passed"] = all(
        isinstance(value, dict) and value.get("passed") is True
        for value in checks
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify the Glassless3D standalone runtime without hardware"
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    report = run_self_test()
    encoded = json.dumps(report, indent=2, sort_keys=True)
    print(encoded)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8", newline="\n")
    raise SystemExit(0 if report.get("passed") is True else 1)


if __name__ == "__main__":
    main()
