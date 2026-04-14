# tests/test_wizard.py
import os
import winreg
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from launcher.wizard import WelcomePage, GameDirPage, InstallPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_welcome_page_has_title(qapp):
    page = WelcomePage()
    assert "Glassless" in page.title() or "Welcome" in page.title()


def test_welcome_page_is_complete_by_default(qapp):
    page = WelcomePage()
    assert page.isComplete()


def test_game_dir_page_detects_wow_from_registry(qapp, tmp_path):
    game_dir = str(tmp_path / "WoW")
    os.makedirs(game_dir)

    mock_key = MagicMock()
    with (
        patch("launcher.wizard.winreg.OpenKey", return_value=mock_key),
        patch("launcher.wizard.winreg.QueryValueEx", return_value=(game_dir, None)),
    ):
        page = GameDirPage()
        page.initializePage()

    assert page._dir_edit.text() == game_dir


def test_game_dir_page_complete_when_dir_set(qapp, tmp_path):
    page = GameDirPage()
    page._dir_edit.setText(str(tmp_path))
    assert page.isComplete()


def test_game_dir_page_incomplete_when_dir_empty(qapp):
    page = GameDirPage()
    page._dir_edit.setText("")
    assert not page.isComplete()


def test_install_page_calls_install_steps(qapp, tmp_path):
    """InstallPage._run_install uses install_steps generator."""
    game_dir = str(tmp_path)
    steps_yielded = []

    def fake_install_steps(gd, profile_name="wow"):
        steps_yielded.append(gd)
        yield "Copying ReShade"
        yield "Copying shaders"
        yield "Writing ReShade.ini"
        yield "Installing addon"

    with patch("launcher.wizard.install_steps", side_effect=fake_install_steps):
        page = InstallPage()
        page._game_dir = game_dir
        page._run_install()

    assert steps_yielded == [game_dir]
    assert page.isComplete()


def test_install_page_shows_error_on_install_failure(qapp, tmp_path):
    """InstallPage._run_install populates the error label on InstallError."""
    from launcher.reshade_install import InstallError

    def failing_install_steps(gd, profile_name="wow"):
        raise InstallError("Copying ReShade", "file not found")

    with patch("launcher.wizard.install_steps", side_effect=failing_install_steps):
        page = InstallPage()
        page._game_dir = str(tmp_path)
        page._run_install()

    assert not page.isComplete()
    assert "Copying ReShade" in page._error_label.text()


from launcher.wizard import CameraScreenPage, DonePage


def test_camera_screen_page_populates_combo(qapp):
    """CameraScreenPage lists cameras found by probing VideoCapture."""
    mock_cap = MagicMock()
    # index 0 opens; indices 1-4 fail
    mock_cap.isOpened.side_effect = [True, False, False, False, False]

    with patch("launcher.wizard.cv2.VideoCapture", return_value=mock_cap):
        page = CameraScreenPage()
        page.initializePage()

    assert page._camera_combo.count() >= 1


def test_camera_screen_page_fills_screen_from_edid(qapp):
    with patch("launcher.wizard.detect_screen_size_cm", return_value=(59.8, 33.6)):
        page = CameraScreenPage()
        page.initializePage()

    assert page._width_edit.text() == "59.8"
    assert page._height_edit.text() == "33.6"


def test_camera_screen_page_leaves_screen_blank_on_edid_failure(qapp):
    with patch("launcher.wizard.detect_screen_size_cm", return_value=None):
        page = CameraScreenPage()
        page.initializePage()

    assert page._width_edit.text() == ""
    assert page._height_edit.text() == ""


def test_done_page_writes_config(qapp, tmp_path):
    import yaml
    config_path = str(tmp_path / "config.yaml")

    page = DonePage(config_path=config_path)
    page._camera_index = 0
    page._screen_width_cm = 59.8
    page._screen_height_cm = 33.6
    page._write_config()

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    assert cfg["camera"]["index"] == 0
    assert cfg["screen"]["width_cm"] == pytest.approx(59.8)
    assert cfg["tracking"]["ipd_cm"] == 6.3
    assert "gui" in cfg
