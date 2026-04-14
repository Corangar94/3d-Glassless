"""5-page first-run setup wizard."""
from __future__ import annotations

import os
import winreg
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from launcher.reshade_install import InstallError, install_steps

import cv2
import yaml
from launcher.edid import detect_screen_size_cm


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


# ── Page 2: Game Directory ─────────────────────────────────────────────────────

_WOW_REGISTRY_KEY = r"SOFTWARE\Blizzard Entertainment\World of Warcraft"
_WOW_REGISTRY_VALUE = "InstallPath"


class GameDirPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Select your game folder")
        self.setSubTitle(
            "Glassless3D will install into this directory."
        )

        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("Game directory…")
        self._dir_edit.textChanged.connect(self.completeChanged)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)

        layout = QVBoxLayout(self)
        layout.addWidget(self._dir_edit)
        layout.addWidget(browse_btn)

        self.registerField("game_dir*", self._dir_edit)

    def initializePage(self) -> None:
        detected = self._detect_wow()
        if detected:
            self._dir_edit.setText(detected)

    def isComplete(self) -> bool:
        return bool(self._dir_edit.text().strip())

    def _detect_wow(self) -> Optional[str]:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, _WOW_REGISTRY_KEY
            ) as key:
                value, _ = winreg.QueryValueEx(key, _WOW_REGISTRY_VALUE)
                if os.path.isdir(value):
                    return value
        except (FileNotFoundError, OSError):
            pass
        return None

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "Select game folder")
        if path:
            self._dir_edit.setText(path)


# ── Page 3: Auto-Install ───────────────────────────────────────────────────────

class _InstallWorker(QThread):
    step_done = Signal(str)
    all_done = Signal()
    failed = Signal(str, str)

    def __init__(self, game_dir: str, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._game_dir = game_dir

    def run(self) -> None:
        try:
            for step_name in install_steps(self._game_dir):
                self.step_done.emit(step_name)
            self.all_done.emit()
        except InstallError as e:
            self.failed.emit(e.step, e.reason)


class InstallPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Installing…")
        self.setSubTitle("No internet needed. This takes a few seconds.")

        self._status_label = QLabel("Preparing…")
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setValue(0)
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: red;")

        layout = QVBoxLayout(self)
        layout.addWidget(self._status_label)
        layout.addWidget(self._progress)
        layout.addWidget(self._error_label)

        self._complete = False
        self._worker: Optional[_InstallWorker] = None
        self._game_dir: str = ""

    def initializePage(self) -> None:
        if self._worker and self._worker.isRunning():
            return  # already running, do not restart
        self._game_dir = self.field("game_dir")
        self._complete = False
        self._error_label.setText("")
        self._progress.setValue(0)
        self._worker = _InstallWorker(self._game_dir)
        self._worker.step_done.connect(self._on_step)
        self._worker.all_done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _run_install(self) -> None:
        """Synchronous install used in tests (bypasses QThread)."""
        try:
            for step_name in install_steps(self._game_dir):
                self._on_step(step_name)
            self._on_done()
        except InstallError as e:
            self._on_failed(e.step, e.reason)

    def _on_step(self, name: str) -> None:
        self._status_label.setText(name)
        self._progress.setValue(self._progress.value() + 1)

    def _on_done(self) -> None:
        self._complete = True
        self.completeChanged.emit()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(
            500,
            lambda: self.wizard().next()
            if self.wizard() and self.wizard().currentPage() is self
            else None,
        )

    def _on_failed(self, step: str, reason: str) -> None:
        self._error_label.setText(f"Failed at '{step}': {reason}")

    def isComplete(self) -> bool:
        return self._complete


# ── Page 4: Camera & Screen ────────────────────────────────────────────────────

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

        self.registerField("camera_index", self._camera_combo, "currentIndex",
                           self._camera_combo.currentIndexChanged)
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
            if cap.isOpened():
                self._camera_combo.addItem(f"Camera {idx}", idx)
                cap.release()
            else:
                cap.release()
                break


# ── Page 5: Done ───────────────────────────────────────────────────────────────

_DEFAULT_TRACKING = {
    "ipd_cm": 6.3,
    "smoothing_q": 0.01,
    "smoothing_r": 0.1,
    "hold_ms": 500,
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
            "Launch your game, press Home to open ReShade, then enable Glassless3D."
        )
        self._config_path = config_path
        self._camera_index: int = 0
        self._screen_width_cm: float = 59.8
        self._screen_height_cm: float = 33.6

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Click Finish to start tracking."))

    def initializePage(self) -> None:
        self._camera_index = self.field("camera_index")
        try:
            self._screen_width_cm = float(self.field("screen_width_cm"))
            self._screen_height_cm = float(self.field("screen_height_cm"))
        except (ValueError, TypeError):
            self._screen_width_cm = 59.8
            self._screen_height_cm = 33.6

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
        self.addPage(GameDirPage())
        self.addPage(InstallPage())
        self.addPage(CameraScreenPage())
        self.addPage(DonePage(config_path=config_path))
