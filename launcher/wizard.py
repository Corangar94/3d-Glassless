"""4-page first-run setup wizard focused on overlay onboarding."""
from __future__ import annotations

import os
from typing import Optional

import cv2
import yaml
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from launcher.edid import detect_screen_size_cm
from launcher.overlay_process import find_depth_model, find_overlay_exe


# ── Page 1: Welcome ────────────────────────────────────────────────────────────

class WelcomePage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Welcome to Glassless3D")
        self.setSubTitle(
            "Turn your flat monitor into glassless 3D. "
            "Setup takes about 60 seconds."
        )
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Click Next to begin."))


# ── Page 2: Camera & Screen ────────────────────────────────────────────────────

class CameraScreenPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Camera & Screen")
        self.setSubTitle(
            "Select your webcam and confirm your monitor size."
        )

        self._camera_combo = QComboBox()
        self._width_edit = QLineEdit()
        self._width_edit.setPlaceholderText("Width (cm)")
        self._height_edit = QLineEdit()
        self._height_edit.setPlaceholderText("Height (cm)")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Webcam:"))
        layout.addWidget(self._camera_combo)
        layout.addWidget(QLabel("Monitor width (cm):"))
        layout.addWidget(self._width_edit)
        layout.addWidget(QLabel("Monitor height (cm):"))
        layout.addWidget(self._height_edit)

        self.registerField(
            "camera_index",
            self._camera_combo,
            "currentIndex",
            self._camera_combo.currentIndexChanged,
        )
        self.registerField("screen_width_cm*", self._width_edit)
        self.registerField("screen_height_cm*", self._height_edit)

    def initializePage(self) -> None:
        self._probe_cameras()
        dims = detect_screen_size_cm()
        if dims is not None:
            self._width_edit.setText(f"{dims[0]:.1f}")
            self._height_edit.setText(f"{dims[1]:.1f}")

    def _probe_cameras(self) -> None:
        self._camera_combo.clear()
        for idx in range(5):
            cap = cv2.VideoCapture(idx)
            opened = cap.isOpened()
            cap.release()
            if opened:
                self._camera_combo.addItem(f"Camera {idx}", idx)

    def selected_camera_index(self) -> int:
        camera_index = self._camera_combo.currentData()
        if isinstance(camera_index, int) and camera_index >= 0:
            return camera_index
        return 0


# ── Page 3: Overlay Ready ──────────────────────────────────────────────────────

class OverlayReadyPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Overlay readiness")
        self.setSubTitle(
            "Glassless3D uses the standalone desktop overlay as the primary runtime."
        )
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self._status_label)

    def initializePage(self) -> None:
        problems: list[str] = []
        if find_overlay_exe() is None:
            problems.append("Overlay executable missing")
        if find_depth_model() is None:
            problems.append("Depth model missing")

        if problems:
            self._status_label.setText(
                "Overlay not fully ready:\n- " + "\n- ".join(problems)
            )
            return

        self._status_label.setText("Overlay executable and depth model were found.")


# ── Page 4: Done ───────────────────────────────────────────────────────────────

_DEFAULT_TRACKING = {
    "ipd_cm": 6.3,
    "smoothing_q": 0.01,
    "smoothing_r": 0.1,
    "hold_ms": 500,
}

_DEFAULT_OVERLAY = {
    "strength_x": 1.0,
    "strength_y": 1.0,
    "virtual_depth_cm": 30.0,
}


class DonePage(QWizardPage):
    def __init__(
        self,
        config_path: str,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Ready to go!")
        self.setSubTitle(
            "Finish setup, then launch the Glassless3D overlay workflow from the app."
        )
        self._config_path = config_path
        self._camera_index: int = 0
        self._screen_width_cm: float = 59.8
        self._screen_height_cm: float = 33.6

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("Click Finish to save your settings for the overlay workflow.")
        )

    def initializePage(self) -> None:
        self._camera_index = self._read_camera_index()
        try:
            self._screen_width_cm = float(self.field("screen_width_cm"))
            self._screen_height_cm = float(self.field("screen_height_cm"))
        except (ValueError, TypeError):
            self._screen_width_cm = 59.8
            self._screen_height_cm = 33.6

    def _read_camera_index(self) -> int:
        wizard = self.wizard()
        if wizard is None:
            return 0
        for page_id in wizard.pageIds():
            page = wizard.page(page_id)
            if isinstance(page, CameraScreenPage):
                return page.selected_camera_index()
        return 0

    def validatePage(self) -> bool:
        self._write_config()
        return True

    def _write_config(self) -> None:
        config = {
            "camera": {"index": self._camera_index},
            "screen": {
                "width_cm": self._screen_width_cm,
                "height_cm": self._screen_height_cm,
            },
            "tracking": _DEFAULT_TRACKING,
            "overlay": {
                **_DEFAULT_OVERLAY,
                "screen_w_cm": self._screen_width_cm,
                "screen_h_cm": self._screen_height_cm,
            },
            "gui": {"compact_mode": False},
        }
        dirname = os.path.dirname(self._config_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        with open(self._config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)


# ── SetupWizard ────────────────────────────────────────────────────────────────

class SetupWizard(QWizard):
    def __init__(
        self,
        config_path: str,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setWindowTitle("Glassless3D Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.addPage(WelcomePage())
        self.addPage(CameraScreenPage())
        self.addPage(OverlayReadyPage())
        self.addPage(DonePage(config_path=config_path))
