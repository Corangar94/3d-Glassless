"""Offline production initialization inside a source or frozen launcher.

Only generated pixels, temporary config, and uniquely named Local mappings are
used. No webcam, desktop capture, download, or native overlay process is started.
Run this in a dedicated process: the external smoke runner supplies the hard
wall-clock bound even if a third-party library or Qt callback hangs.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import uuid
from typing import Callable

SCHEMA_VERSION = 1
REQUIRED_CHECKS = (
    "runtime_assets", "production_imports", "shared_memory",
    "mediapipe_inference", "opencv_inference", "qt_main_window",
)
SCOPE = "offline_packaged_runtime"


def write_report(path: Path, payload: dict) -> None:
    """Readers see either the previous complete report or this complete report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_assets(root: Path, expected_commit: str | None) -> dict:
    from launcher.native_provenance import verify_native_build
    from scripts.bootstrap import FACE_MODEL_SHA256, DEPTH_MODEL_SHA256

    native = verify_native_build(root, expected_commit=expected_commit)
    hashes = {}
    for name, expected in (
        ("face_landmarker.task", FACE_MODEL_SHA256),
        ("depth_anything_v2_small_fp16.onnx", DEPTH_MODEL_SHA256),
    ):
        actual = _sha256(root / "models" / name)
        if actual != expected:
            raise ValueError("model integrity check failed: " + name)
        hashes[name] = actual
    return {"source_commit": native["source_commit"], "model_sha256": hashes}


def _production_imports() -> dict:
    modules = (
        "tracker.pose_stability_runtime", "tracker.camera_control_recovery_runtime",
        "tracker.live_filter_tuning_runtime", "tracker.face_tracker",
        "tracker.face_tracker_cv2", "launcher.runtime_mainwindow",
        "launcher.camera_calibration_wizard",
    )
    for name in modules:
        importlib.import_module(name)
    return {"modules": list(modules)}


def _shared_memory(nonce: str) -> dict:
    from tracker.pose import FilteredPose, monotonic_ms
    from tracker.pose_shared_memory import PoseStateReader, PoseStateWriter
    from tracker.shared_memory import (
        SharedMemoryReader, SharedMemoryWriter, TrackingStateReader, TrackingStateWriter,
    )
    from tracker.shared_settings import OverlaySettings, SharedSettingsReader, SharedSettingsWriter

    prefix = "Local\\Glassless3D_SelfTest_" + nonce
    with ExitStack() as stack:
        writer = stack.enter_context(SharedMemoryWriter(prefix + "_Legacy"))
        reader = stack.enter_context(SharedMemoryReader(prefix + "_Legacy"))
        writer.write(x=1.25, y=-2.5, z=65.0)
        sample = reader.read()
        if sample is None or sample[:3] != (1.25, -2.5, 65.0) or sample[3] == 0:
            raise RuntimeError("legacy pose round trip failed")
        state_writer = stack.enter_context(TrackingStateWriter(prefix + "_State"))
        state_reader = stack.enter_context(TrackingStateReader(prefix + "_State"))
        state_writer.write("hold")
        state = state_reader.read()
        if state is None or state[0] != "hold" or state[1] == 0:
            raise RuntimeError("tracking state round trip failed")
        pose_writer = stack.enter_context(PoseStateWriter(prefix + "_Pose"))
        pose_reader = stack.enter_context(PoseStateReader(prefix + "_Pose"))
        timestamp = monotonic_ms()
        pose_writer.write(FilteredPose(1.25, -2.5, 65.0, confidence=1.0,
            capture_timestamp_ms=timestamp, publish_timestamp_ms=timestamp))
        pose = pose_reader.read()
        if pose is None or (pose.x_cm, pose.y_cm, pose.z_cm) != (1.25, -2.5, 65.0):
            raise RuntimeError("versioned pose round trip failed")
        if pose.capture_timestamp_ms != timestamp or pose.publish_timestamp_ms != timestamp:
            raise RuntimeError("versioned pose timestamps changed")
        settings_writer = stack.enter_context(SharedSettingsWriter(prefix + "_Settings"))
        settings_reader = stack.enter_context(SharedSettingsReader(prefix + "_Settings"))
        settings_writer.write(OverlaySettings(depth_mode=3, head_dist_cm=65.0))
        settings = settings_reader.read()
        if settings is None or settings.depth_mode != 3 or settings.head_dist_cm != 65.0:
            raise RuntimeError("settings round trip failed")
    return {"channels": ["legacy_pose", "tracking_state", "pose_v2", "settings"],
            "isolated": True}


def _mediapipe_inference(root: Path) -> dict:
    import numpy as np
    from tracker.face_tracker import FaceTracker
    from tracker.pose import monotonic_ms

    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    options = dict(real_ipd_cm=6.4, screen_width_cm=60.0, screen_height_cm=34.0,
                   model_path=str(root / "models" / "face_landmarker.task"))
    with FaceTracker(**options, async_mode=False) as tracker:
        if tracker.process_frame(frame, capture_timestamp_ms=monotonic_ms()) is not None:
            raise RuntimeError("blank fixture unexpectedly produced a face")

    callback_received = threading.Event()
    callback_errors: list[str] = []

    class ObservedTracker(FaceTracker):
        def _on_result(self, *args):
            try:
                super()._on_result(*args)
            except Exception as error:
                callback_errors.append(str(error))
            finally:
                callback_received.set()

    with ObservedTracker(**options, async_mode=True) as tracker:
        tracker.process_frame(frame, capture_timestamp_ms=monotonic_ms())
        if not callback_received.wait(10.0):
            raise RuntimeError("MediaPipe callback did not complete")
        if callback_errors:
            raise RuntimeError("MediaPipe callback failed: " + callback_errors[0])
    return {"input": "generated_blank_320x240", "modes": ["image", "live_stream"],
            "callback_received": True, "webcam_opened": False}


def _opencv_inference() -> dict:
    import cv2
    import numpy as np
    from tracker.face_tracker_cv2 import FaceTracker

    for name in ("haarcascade_frontalface_default.xml", "haarcascade_eye.xml"):
        classifier = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / name))
        if classifier.empty():
            raise RuntimeError("OpenCV cascade missing: " + name)
    with FaceTracker(real_ipd_cm=6.4, screen_width_cm=60.0, screen_height_cm=34.0) as tracker:
        if tracker.process_frame(np.zeros((240, 320, 3), dtype=np.uint8)) is not None:
            raise RuntimeError("blank OpenCV fixture unexpectedly produced a face")
    return {"input": "generated_blank_320x240", "cascades_loaded": True}


def _qt_main_window(workspace: Path, nonce: str) -> dict:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from launcher.runtime_mainwindow import MainWindow
    from tracker.shared_settings import SharedSettingsReader, SharedSettingsWriter

    if QApplication.instance() is not None:
        raise RuntimeError("self-test requires a fresh Qt process")
    app = QApplication(["Glassless3D-self-test", "-platform", "offscreen"])
    app.setApplicationName("Glassless3D offline self-test")
    config = {"gui": {"compact_mode": False}, "tracking": {"auto_tune": False},
              "overlay": {"screen_w_cm": 60.0, "screen_h_cm": 34.0}}
    config_path = workspace / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    name = "Local\\Glassless3D_SelfTest_" + nonce + "_GUI"
    failures: list[str] = []
    result: dict = {}
    old_hook = sys.excepthook

    def exception_hook(kind, value, traceback):
        failures.append(kind.__name__ + ": " + str(value))
        app.exit(1)

    sys.excepthook = exception_hook
    window = None
    try:
        with SharedSettingsWriter(name) as writer, SharedSettingsReader(name) as reader:
            window = MainWindow(config=config, config_path=str(config_path), settings_writer=writer)
            window.show()  # offscreen platform; never maps onto the user's desktop

            def exercise():
                try:
                    if window._thread is not None or window._overlay.is_running():
                        raise RuntimeError("offline GUI unexpectedly started a runtime child")
                    if window._tabs.count() < 2:
                        raise RuntimeError("runtime/advanced tabs were not constructed")
                    window._tabs.setCurrentIndex(1)
                    image = window.grab()  # own QWidget only, retained in memory
                    if image.isNull() or image.width() <= 0 or image.height() <= 0:
                        raise RuntimeError("Qt did not render the main window")
                    settings = reader.read()
                    if settings is None or settings.screen_w_cm != 60.0:
                        raise RuntimeError("main window did not use its isolated settings channel")
                    result.update({"tabs": window._tabs.count(), "platform": app.platformName(),
                                   "rendered": True, "runtime_children_started": False})
                except Exception as error:
                    failures.append(type(error).__name__ + ": " + str(error))
                finally:
                    window.close()
                    app.exit(1 if failures else 0)

            QTimer.singleShot(100, exercise)
            exit_code = app.exec()
            if exit_code != 0 or failures or not result:
                raise RuntimeError("Qt startup/render/close failed: " + "; ".join(failures))
    finally:
        if window is not None:
            window.close()
            window.deleteLater()
        app.processEvents()
        sys.excepthook = old_hook
    return result


def run_checks(root: Path, workspace: Path, output: Path, request_id: str,
               expected_commit: str | None = None) -> dict:
    checks: tuple[tuple[str, Callable[[], dict]], ...] = (
        ("runtime_assets", lambda: _runtime_assets(root, expected_commit)),
        ("production_imports", _production_imports),
        ("shared_memory", lambda: _shared_memory(request_id)),
        ("mediapipe_inference", lambda: _mediapipe_inference(root)),
        ("opencv_inference", _opencv_inference),
        ("qt_main_window", lambda: _qt_main_window(workspace, request_id)),
    )
    report = {"schema_version": SCHEMA_VERSION, "scope": SCOPE, "request_id": request_id,
              "frozen": getattr(sys, "frozen", False) is True,
              "python_version": sys.version.split()[0], "status": "running", "passed": False,
              "checks": [{"name": name, "status": "not_run"} for name, _ in checks]}
    write_report(output, report)
    for record, (_, check) in zip(report["checks"], checks):
        record["status"] = "running"
        write_report(output, report)
        started = time.monotonic()
        try:
            record["details"] = check()
            record["status"] = "passed"
        except Exception as error:
            record["status"] = "failed"
            record["error"] = type(error).__name__ + ": " + str(error)[:2000]
        record["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        write_report(output, report)
        if record["status"] != "passed":
            break
    report["passed"] = all(item["status"] == "passed" for item in report["checks"])
    report["status"] = "passed" if report["passed"] else "failed"
    write_report(output, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline startup/model/IPC/Qt self-test; no capture")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--expected-commit", default=None)
    args = parser.parse_args(argv)
    nonce = args.request_id or uuid.uuid4().hex
    if re.fullmatch(r"[a-f0-9]{32}", nonce) is None:
        parser.error("request-id must be 32 lowercase hexadecimal characters")
    if args.expected_commit is not None and re.fullmatch(r"[a-f0-9]{40}", args.expected_commit) is None:
        parser.error("expected-commit must be a full lowercase Git SHA")
    # Set before importing Qt, MediaPipe, or any production GUI module. Config
    # and cache writes are restricted to this disposable self-test episode.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["PYTHONUTF8"] = "1"
    with tempfile.TemporaryDirectory(prefix="glassless-self-test-") as directory:
        workspace = Path(directory)
        os.environ["APPDATA"] = str(workspace)
        os.environ["LOCALAPPDATA"] = str(workspace)
        os.environ["MPLCONFIGDIR"] = str(workspace / "matplotlib")
        report = run_checks(Path(__file__).resolve().parent.parent, workspace,
                            args.output.resolve(), nonce, args.expected_commit)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
