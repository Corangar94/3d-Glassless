"""Qt-native system tray controller for the launcher process.

The tracker child is intentionally headless and supervised by the launcher.
Using Qt for the user-facing tray avoids the former optional pystray/Pillow
runtime and ensures that Quit follows the normal MainWindow shutdown path.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QSystemTrayIcon


def make_tray_icon(size: int = 64) -> QIcon:
    """Create a small in-memory Glassless3D tray icon."""
    pixels = max(16, int(size))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(30, 30, 30))
    painter.drawEllipse(2, 2, pixels - 4, pixels - 4)
    painter.setBrush(QColor(60, 200, 90))
    margin = max(5, pixels // 7)
    painter.drawEllipse(margin, margin, pixels - 2 * margin, pixels - 2 * margin)
    painter.setBrush(QColor(255, 255, 255))
    pupil = max(5, pixels // 5)
    painter.drawEllipse(
        (pixels - pupil) // 2,
        (pixels - pupil) // 2,
        pupil,
        pupil,
    )
    painter.end()
    return QIcon(pixmap)


class SystemTrayController(QObject):
    """Own the launcher tray icon and its show/quit actions."""

    def __init__(
        self,
        app: QApplication,
        window: QMainWindow,
        *,
        quit_callback: Callable[[], None] | None = None,
        available: bool | None = None,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._window = window
        self._quit_callback = quit_callback or self._default_quit
        supported = (
            QSystemTrayIcon.isSystemTrayAvailable()
            if available is None
            else bool(available)
        )
        self._tray: QSystemTrayIcon | None = None
        self._menu: QMenu | None = None
        if not supported:
            return

        tray = QSystemTrayIcon(make_tray_icon(), self)
        tray.setToolTip("Glassless3D")
        menu = QMenu(window)
        show_action = menu.addAction("Show Glassless3D")
        quit_action = menu.addAction("Quit Glassless3D")
        show_action.triggered.connect(self.show_window)
        quit_action.triggered.connect(self._quit_callback)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self._tray = tray
        self._menu = menu

    @property
    def active(self) -> bool:
        return self._tray is not None and self._tray.isVisible()

    def show_window(self) -> None:
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def _default_quit(self) -> None:
        self._app.closeAllWindows()
        self._app.quit()

    def close(self) -> None:
        if self._tray is not None:
            self._tray.hide()
            self._tray.deleteLater()
            self._tray = None
        self._menu = None
