# tests/test_mainwindow.py
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from launcher.mainwindow import MainWindow

CONFIG = {
    "camera": {"index": 0},
    "screen": {"width_cm": 59.8, "height_cm": 33.6},
    "tracking": {
        "ipd_cm": 6.3,
        "smoothing_q": 0.01,
        "smoothing_r": 0.1,
        "hold_ms": 500,
    },
    "gui": {"compact_mode": False},
}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def window(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerThread"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
    return win


def test_mainwindow_starts_in_expanded_mode(window):
    assert not window._compact
    # Expanded width ~270, compact ~400 — expanded must be narrower
    assert window.width() < 350


def test_mainwindow_toggle_switches_to_compact(window):
    window._toggle_mode()
    assert window._compact
    assert window.width() >= 350


def test_mainwindow_toggle_back_to_expanded(window):
    window._compact = True
    window._apply_mode()
    window._toggle_mode()
    assert not window._compact
    assert window.width() < 350


def test_mainwindow_xyz_labels_update_on_signal(window):
    window._on_position(2.5, -1.0, 57.3)
    assert "2.5" in window._label_x.text()
    assert "-1.0" in window._label_y.text()
    assert "57.3" in window._label_z.text()


def test_mainwindow_status_badge_tracking(window):
    window._on_status("tracking")
    assert "TRACKING" in window._status_label.text().upper()


def test_mainwindow_status_badge_error(window):
    window._on_status("error")
    assert "CAMERA" in window._status_label.text().upper() or \
           "ERROR" in window._status_label.text().upper()


def test_mainwindow_is_always_on_top(window):
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
