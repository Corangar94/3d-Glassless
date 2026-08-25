# tests/test_wizard.py
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from launcher.wizard import (
    CameraScreenPage,
    DonePage,
    OverlayReadyPage,
    SetupWizard,
    WelcomePage,
)


def _mock_camera_probe(*opened_values: bool):
    def make_cap(opened: bool) -> MagicMock:
        cap = MagicMock()
        cap.isOpened.return_value = opened
        return cap

    values = list(opened_values)
    values.extend([False] * (5 - len(values)))
    return patch(
        "launcher.wizard.cv2.VideoCapture",
        side_effect=[make_cap(opened) for opened in values[:5]],
    )


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


def test_camera_screen_page_populates_combo(qapp):
    with _mock_camera_probe(True):
        page = CameraScreenPage()
        page.initializePage()

    assert page._camera_combo.count() >= 1


def test_camera_screen_page_selected_camera_uses_item_data(qapp):
    with _mock_camera_probe(False, False, True):
        page = CameraScreenPage()
        page.initializePage()

    assert page.selected_camera_index() == 2


def test_camera_screen_page_selected_camera_falls_back_when_none_found(qapp):
    with _mock_camera_probe(False, False, False, False, False):
        page = CameraScreenPage()
        page.initializePage()

    assert page._camera_combo.currentIndex() == -1
    assert page.selected_camera_index() == 0


def test_camera_screen_page_fills_screen_from_edid(qapp):
    with (
        _mock_camera_probe(False, False, False, False, False),
        patch("launcher.wizard.detect_screen_size_cm", return_value=(59.8, 33.6)),
    ):
        page = CameraScreenPage()
        page.initializePage()

    assert page._width_edit.text() == "59.8"
    assert page._height_edit.text() == "33.6"


def test_camera_screen_page_leaves_screen_blank_on_edid_failure(qapp):
    with (
        _mock_camera_probe(False, False, False, False, False),
        patch("launcher.wizard.detect_screen_size_cm", return_value=None),
    ):
        page = CameraScreenPage()
        page.initializePage()

    assert page._width_edit.text() == ""
    assert page._height_edit.text() == ""


def test_overlay_ready_page_reports_missing_overlay_requirements(qapp, monkeypatch):
    monkeypatch.setattr("launcher.wizard.find_overlay_exe", lambda: None)
    monkeypatch.setattr("launcher.wizard.find_depth_model", lambda: None)

    page = OverlayReadyPage()
    page.initializePage()

    status = page._status_label.text().lower()
    assert "overlay not fully ready" in status
    assert "overlay executable missing" in status
    assert "depth model missing" in status


def test_overlay_ready_page_reports_success_when_requirements_found(qapp, monkeypatch):
    monkeypatch.setattr("launcher.wizard.find_overlay_exe", lambda: "overlay.exe")
    monkeypatch.setattr("launcher.wizard.find_depth_model", lambda: "depth.onnx")

    page = OverlayReadyPage()
    page.initializePage()

    assert "overlay executable and depth model were found" in page._status_label.text().lower()


def test_done_page_mentions_overlay_workflow(qapp, tmp_path):
    page = DonePage(config_path=str(tmp_path / "config.yaml"))
    subtitle = page.subTitle().lower()
    assert "overlay" in subtitle
    assert "reshade" not in subtitle
    assert "home" not in subtitle


def test_done_page_writes_overlay_config_defaults(qapp, tmp_path):
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
    assert cfg["overlay"] == {
        "display_backend": "desktop_overlay",
        "depth_performance_mode": "auto",
        "camera_fov_deg": pytest.approx(90.0),
        "strength_x": pytest.approx(1.0),
        "strength_y": pytest.approx(1.0),
        "virtual_depth_cm": pytest.approx(30.0),
        "depth_curve": 2,
        "depth_gamma": pytest.approx(2.0),
        "screen_w_cm": pytest.approx(59.8),
        "screen_h_cm": pytest.approx(33.6),
    }
    assert cfg["gui"] == {"compact_mode": False}


def test_done_page_writes_default_non_injecting_game_profile(qapp, tmp_path):
    import yaml

    config_path = str(tmp_path / "config.yaml")
    page = DonePage(config_path=config_path)
    page._write_config()

    with open(config_path, encoding="utf-8") as config_file:
        cfg = yaml.safe_load(config_file)

    assert cfg["active_game_profile"] == "default"
    assert cfg["game_profiles"]["default"]["play_context"] == "online_multiplayer"
    assert cfg["game_profiles"]["default"]["requested_mode"] == "non_injecting_desktop"
    assert cfg["game_profiles"]["default"]["advanced_acknowledged"] is False


def test_setup_wizard_has_four_pages(qapp, tmp_path):
    wizard = SetupWizard(config_path=str(tmp_path / "config.yaml"))
    assert len(wizard.pageIds()) == 4
