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
