from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow

from launcher.system_tray import SystemTrayController, make_tray_icon


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_generated_tray_icon_is_not_null(qapp):
    icon = make_tray_icon()

    assert isinstance(icon, QIcon)
    assert not icon.isNull()


def test_unavailable_system_tray_is_a_safe_noop(qapp):
    window = QMainWindow()
    controller = SystemTrayController(qapp, window, available=False)

    assert controller.active is False
    controller.close()


def test_show_window_restores_and_activates_launcher(qapp):
    window = MagicMock(spec=QMainWindow)
    controller = SystemTrayController(qapp, window, available=False)

    controller.show_window()

    window.showNormal.assert_called_once()
    window.raise_.assert_called_once()
    window.activateWindow.assert_called_once()


def test_tracker_child_has_no_optional_pillow_or_pystray_runtime():
    source = Path("tracker/main.py").read_text(encoding="utf-8")

    assert "from PIL" not in source
    assert "import pystray" not in source
    assert "_make_tray_image" not in source
    assert "supervised by the launcher" in source


def test_launcher_owns_the_qt_tray_lifetime():
    source = Path("launcher/app.py").read_text(encoding="utf-8")

    assert "SystemTrayController" in source
    assert "app.setWindowIcon(make_tray_icon())" in source
    assert "tray.close()" in source
