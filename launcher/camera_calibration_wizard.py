"""Dedicated GUI for full webcam intrinsics and camera-to-screen calibration."""
from __future__ import annotations

from pathlib import Path
import subprocess
import threading
from typing import Any

import yaml
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from launcher.app import CONFIG_PATH
from launcher.camera_calibration_process import (
    CameraCaptureConfig,
    build_board_command,
    build_center_command,
    build_intrinsics_command,
)


class CameraCalibrationDialog(QDialog):
    _job_finished = Signal(object)

    def __init__(self, config_path: str = CONFIG_PATH) -> None:
        super().__init__()
        self._config_path = Path(config_path)
        self._job_running = False
        self._buttons: list[QPushButton] = []
        self.setWindowTitle("Glassless3D Camera Calibration")
        self.setMinimumWidth(560)
        self._build_ui()
        self._job_finished.connect(self._on_job_finished)
        self._refresh_summary()

    def _load_config(self) -> dict[str, Any]:
        if not self._config_path.exists():
            return {}
        loaded = yaml.safe_load(self._config_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def _camera_config(self) -> CameraCaptureConfig:
        config = self._load_config()
        camera = config.get("camera", {})
        if not isinstance(camera, dict):
            camera = {}
        return CameraCaptureConfig(
            index=int(camera.get("index", 0)),
            width=int(camera.get("width", 1280)),
            height=int(camera.get("height", 720)),
            fps=float(camera.get("fps", 30.0)),
        )

    def _current_ipd_cm(self) -> float:
        config = self._load_config()
        tracking = config.get("tracking", {})
        overlay = config.get("overlay", {})
        tracking = tracking if isinstance(tracking, dict) else {}
        overlay = overlay if isinstance(overlay, dict) else {}
        calibration = overlay.get("display_calibration", {})
        calibration = calibration if isinstance(calibration, dict) else {}
        ipd_mm = calibration.get("ipd_mm", overlay.get("ipd_mm"))
        if ipd_mm is not None:
            try:
                value = float(ipd_mm) / 10.0
                if value > 0.0:
                    return value
            except (TypeError, ValueError):
                pass
        try:
            return max(0.1, float(tracking.get("ipd_cm", 6.4)))
        except (TypeError, ValueError):
            return 6.4

    def _current_viewer_distance_cm(self) -> float:
        config = self._load_config()
        overlay = config.get("overlay", {})
        overlay = overlay if isinstance(overlay, dict) else {}
        calibration = overlay.get("display_calibration", {})
        calibration = calibration if isinstance(calibration, dict) else {}
        for raw in (
            calibration.get("viewer_distance_cm"),
            overlay.get("head_dist_cm"),
            60.0,
        ):
            try:
                value = float(raw)
                if value > 0.0:
                    return value
            except (TypeError, ValueError):
                continue
        return 60.0

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Full calibration improves head-position geometry by measuring the webcam lens "
            "and then aligning the camera coordinate system to the screen. Stop tracking "
            "before running either camera step."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._square_mm = QDoubleSpinBox()
        self._square_mm.setRange(5.0, 100.0)
        self._square_mm.setDecimals(1)
        self._square_mm.setValue(25.0)
        self._square_mm.setSuffix(" mm")
        form.addRow("Printed checker square", self._square_mm)

        self._viewer_distance = QDoubleSpinBox()
        self._viewer_distance.setRange(20.0, 200.0)
        self._viewer_distance.setDecimals(1)
        self._viewer_distance.setSuffix(" cm")
        self._viewer_distance.setValue(self._current_viewer_distance_cm())
        form.addRow("Normal viewing distance", self._viewer_distance)

        self._ipd_cm = QDoubleSpinBox()
        self._ipd_cm.setRange(4.0, 8.5)
        self._ipd_cm.setDecimals(2)
        self._ipd_cm.setSuffix(" cm")
        self._ipd_cm.setValue(self._current_ipd_cm())
        form.addRow("IPD", self._ipd_cm)
        layout.addLayout(form)

        row = QHBoxLayout()
        board = QPushButton("1. Save checkerboard PNG")
        intrinsics = QPushButton("2. Calibrate webcam lens")
        center = QPushButton("3. Align camera to screen")
        board.clicked.connect(self._save_board)
        intrinsics.clicked.connect(self._run_intrinsics)
        center.clicked.connect(self._run_center)
        self._buttons.extend((board, intrinsics, center))
        row.addWidget(board)
        row.addWidget(intrinsics)
        row.addWidget(center)
        layout.addLayout(row)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._status = QLabel(
            "Step 1: save and print the checkerboard at 100% scale. "
            "Measure one printed square and enter that size above."
        )
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)

    def _set_busy(self, busy: bool) -> None:
        self._job_running = busy
        for button in self._buttons:
            button.setEnabled(not busy)

    def _run_child(self, command: list[str], label: str) -> None:
        if self._job_running:
            return
        self._set_busy(True)
        self._status.setText(label)

        def worker() -> None:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                payload = {
                    "returncode": int(result.returncode),
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip(),
                }
            except Exception as exc:  # noqa: BLE001
                payload = {"returncode": -1, "stdout": "", "stderr": str(exc)}
            self._job_finished.emit(payload)

        threading.Thread(target=worker, daemon=True).start()

    def _save_board(self) -> None:
        output, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save printable checkerboard",
            str(Path.home() / "Glassless3D_checkerboard_9x6.png"),
            "PNG image (*.png)",
        )
        if not output:
            return
        command = build_board_command(output)
        self._run_child(
            command,
            "Generating checkerboard… Print it at 100% scale after this step completes.",
        )

    def _run_intrinsics(self) -> None:
        command = build_intrinsics_command(
            config_path=self._config_path,
            camera=self._camera_config(),
            square_mm=self._square_mm.value(),
        )
        self._run_child(
            command,
            "Lens calibration is running. Move and tilt the printed board around the frame; "
            "the capture window closes automatically after enough diverse views. Press Esc there to cancel.",
        )

    def _run_center(self) -> None:
        command = build_center_command(
            config_path=self._config_path,
            camera=self._camera_config(),
            viewer_distance_cm=self._viewer_distance.value(),
            ipd_cm=self._ipd_cm.value(),
        )
        self._run_child(
            command,
            "Center alignment is running. Sit at the entered viewing distance, look at screen center, and remain still.",
        )

    def _on_job_finished(self, payload: object) -> None:
        self._set_busy(False)
        if not isinstance(payload, dict):
            self._status.setText("Calibration child returned an invalid result.")
            return
        returncode = int(payload.get("returncode", -1))
        stdout = str(payload.get("stdout", "")).strip()
        stderr = str(payload.get("stderr", "")).strip()
        if returncode == 0:
            self._status.setText(stdout or "Calibration step completed successfully.")
            self._refresh_summary()
        elif returncode == 130:
            self._status.setText("Calibration cancelled.")
        else:
            detail = stderr or stdout or f"exit code {returncode}"
            self._status.setText(f"Calibration failed: {detail}")

    def _refresh_summary(self) -> None:
        config = self._load_config()
        tracking = config.get("tracking", {})
        tracking = tracking if isinstance(tracking, dict) else {}
        calibration = tracking.get("camera_calibration", {})
        calibration = calibration if isinstance(calibration, dict) else {}
        intrinsics = calibration.get("intrinsics")
        quality = calibration.get("quality", {})
        quality = quality if isinstance(quality, dict) else {}
        if not isinstance(intrinsics, dict):
            self._summary.setText("Calibration state: webcam intrinsics have not been measured yet.")
            return
        try:
            fx = float(intrinsics.get("fx", 0.0))
            fy = float(intrinsics.get("fy", 0.0))
            width = int(intrinsics.get("width", 0))
            height = int(intrinsics.get("height", 0))
            error = float(quality.get("mean_reprojection_error_px", intrinsics.get("rms_error_px", 0.0)))
            fov = float(tracking.get("camera_fov_deg", 0.0))
        except (TypeError, ValueError):
            self._summary.setText("Calibration state: saved values are malformed; recalibrate the webcam.")
            return
        self._summary.setText(
            f"Calibration state: {width}×{height}, fx={fx:.1f}, fy={fy:.1f}, "
            f"horizontal FOV={fov:.2f}°, reprojection error={error:.3f}px, "
            f"viewer distance={self._current_viewer_distance_cm():.1f}cm."
        )


def main(config_path: str = CONFIG_PATH) -> None:
    app = QApplication.instance() or QApplication([])
    dialog = CameraCalibrationDialog(config_path=config_path)
    dialog.show()
    raise SystemExit(app.exec())
