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
        self._progress.setRange(0, 4)
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
        for step_name in install_steps(self._game_dir):
            self._on_step(step_name)
        self._on_done()

    def _on_step(self, name: str) -> None:
        self._status_label.setText(name)
        self._progress.setValue(self._progress.value() + 1)

    def _on_done(self) -> None:
        self._complete = True
        self.completeChanged.emit()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: self.wizard().next() if self.wizard() else None)

    def _on_failed(self, step: str, reason: str) -> None:
        self._error_label.setText(f"Failed at '{step}': {reason}")

    def isComplete(self) -> bool:
        return self._complete
