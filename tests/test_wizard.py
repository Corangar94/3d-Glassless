# tests/test_wizard.py
from unittest.mock import MagicMock, call, patch

import cv2
import pytest
from PySide6.QtWidgets import QApplication

from launcher.wizard import (
    CameraScreenPage,
    DonePage,
    OverlayReadyPage,
    SetupWizard,
    WelcomePage,
)


def _mock_camera_probe(
    *opened: tuple[int, int | None],
    constructor_errors: set[tuple[int, int | None]] | None = None,
    opened_errors: set[tuple[int, int | None]] | None = None,
    release_errors: set[tuple[int, int | None]] | None = None,
):
    constructor_errors = constructor_errors or set()
    opened_errors = opened_errors or set()
    release_errors = release_errors or set()
    opened_set = set(opened)
    captures: dict[tuple[int, int | None], list[MagicMock]] = {}

    def make_capture(*args):
        camera_index = int(args[0])
        backend_id = int(args[1]) if len(args) > 1 else None
        key = (camera_index, backend_id)
        if key in constructor_errors:
            raise RuntimeError("backend constructor failed")
        cap = MagicMock()
        if key in opened_errors:
            cap.isOpened.side_effect = RuntimeError("isOpened failed")
        else:
            cap.isOpened.return_value = key in opened_set
        if key in release_errors:
            cap.release.side_effect = RuntimeError("release failed")
        captures.setdefault(key, []).append(cap)
        return cap

    return patch("launcher.wizard.cv2.VideoCapture", side_effect=make_capture), captures


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


def test_camera_screen_page_populates_combo_from_directshow(qapp):
    probe, _captures = _mock_camera_probe((0, cv2.CAP_DSHOW))
    with probe:
        page = CameraScreenPage()
        page.initializePage()

    assert page._camera_combo.count() == 1
    assert page.selected_camera_index() == 0
    assert "DirectShow" in page._camera_combo.currentText()


def test_camera_screen_page_falls_through_to_media_foundation(qapp):
    probe, captures = _mock_camera_probe((2, cv2.CAP_MSMF))
    with probe:
        page = CameraScreenPage()
        page.initializePage()

    assert page._camera_combo.count() == 1
    assert page.selected_camera_index() == 2
    assert "Media Foundation" in page._camera_combo.currentText()
    captures[(2, cv2.CAP_DSHOW)][0].release.assert_called_once()
    captures[(2, cv2.CAP_MSMF)][0].release.assert_called_once()


def test_camera_screen_page_uses_default_backend_as_final_fallback(qapp):
    probe, _captures = _mock_camera_probe((1, None))
    with probe as video_capture:
        page = CameraScreenPage()
        page.initializePage()

    assert page.selected_camera_index() == 1
    assert "default backend" in page._camera_combo.currentText()
    assert call(1, cv2.CAP_DSHOW) in video_capture.call_args_list
    assert call(1, cv2.CAP_MSMF) in video_capture.call_args_list
    assert call(1) in video_capture.call_args_list


def test_camera_probe_survives_constructor_exception(qapp):
    probe, _captures = _mock_camera_probe(
        (0, cv2.CAP_MSMF),
        constructor_errors={(0, cv2.CAP_DSHOW)},
    )
    with probe:
        page = CameraScreenPage()
        page.initializePage()

    assert page.selected_camera_index() == 0
    assert "Media Foundation" in page._camera_combo.currentText()


def test_camera_probe_survives_isopened_and_release_exceptions(qapp):
    probe, captures = _mock_camera_probe(
        (0, None),
        opened_errors={(0, cv2.CAP_DSHOW)},
        release_errors={(0, cv2.CAP_DSHOW), (0, None)},
    )
    with probe:
        page = CameraScreenPage()
        page.initializePage()

    assert page.selected_camera_index() == 0
    assert "default backend" in page._camera_combo.currentText()
    assert captures[(0, cv2.CAP_DSHOW)][0].release.called
    assert captures[(0, None)][0].release.called


def test_camera_screen_page_selected_camera_uses_item_data(qapp):
    probe, _captures = _mock_camera_probe((2, cv2.CAP_DSHOW))
    with probe:
        page = CameraScreenPage()
        page.initializePage()

    assert page.selected_camera_index() == 2


def test_camera_screen_page_selected_camera_falls_back_when_none_found(qapp):
    probe, _captures = _mock_camera_probe()
    with probe:
        page = CameraScreenPage()
        page.initializePage()

    assert page._camera_combo.currentIndex() == -1
    assert page.selected_camera_index() == 0


def test_camera_screen_page_fills_screen_from_edid(qapp):
    probe, _captures = _mock_camera_probe()
    with (
        probe,
        patch("launcher.wizard.detect_screen_size_cm", return_value=(59.8, 33.6)),
    ):
        page = CameraScreenPage()
        page.initializePage()

    assert page._width_edit.text() == "59.8"
    assert page._height_edit.text() == "33.6"


def test_camera_screen_page_leaves_screen_blank_on_edid_failure(qapp):
    probe, _captures = _mock_camera_probe()
    with (
        probe,
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
    assert cfg["camera"]["reconnect"] == {
        "immediate_retries": 1,
        "max_failures": 8,
        "base_delay_s": pytest.approx(0.5),
        "max_delay_s": pytest.approx(8.0),
        "max_outage_s": pytest.approx(45.0),
        "heartbeat_s": pytest.approx(1.0),
    }
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
