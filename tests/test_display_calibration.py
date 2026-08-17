import yaml

from tracker import display_calibration


def test_default_calibration_matches_backend_layout():
    calibration = display_calibration.default_calibration("lightfield_quilt")

    assert calibration.backend_id == "lightfield_quilt"
    assert calibration.columns == 9
    assert calibration.rows == 5
    assert calibration.view_count == 45


def test_default_stereo_calibration_includes_sbs_hardware_fields():
    calibration = display_calibration.default_calibration("stereo_autostereo")

    assert calibration.stereo_layout == "full_sbs"
    assert calibration.eye_order == "left_right"
    assert calibration.ipd_mm == 64.0
    assert calibration.focus_plane_cm == 0.0
    assert calibration.panel_width_px == 0
    assert calibration.tracking_mode == "glassless3d_managed"


def test_save_calibration_writes_overlay_display_calibration(tmp_path):
    path = tmp_path / "config.yaml"

    calibration = display_calibration.save_calibration(
        path,
        backend_id="stereo_autostereo",
        viewer_distance_cm=65.0,
        view_cone_deg=35.0,
    )

    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert calibration.viewer_distance_cm == 65.0
    assert cfg["overlay"]["display_calibration"]["backend_id"] == "stereo_autostereo"
    assert cfg["overlay"]["display_calibration"]["view_cone_deg"] == 35.0


def test_save_calibration_writes_stereo_layout_and_panel_metadata(tmp_path):
    path = tmp_path / "config.yaml"

    display_calibration.save_calibration(
        path,
        backend_id="stereo_autostereo",
        panel_width_px=3840,
        panel_height_px=1080,
        panel_width_cm=34.4,
        panel_height_cm=19.3,
        ipd_mm=63.5,
        stereo_layout="half_sbs",
        eye_order="right_left",
        focus_plane_cm=12.0,
        tracking_mode="vendor_managed",
    )

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))["overlay"]["display_calibration"]
    assert saved["panel_width_px"] == 3840
    assert saved["panel_height_px"] == 1080
    assert saved["panel_width_cm"] == 34.4
    assert saved["panel_height_cm"] == 19.3
    assert saved["ipd_mm"] == 63.5
    assert saved["stereo_layout"] == "half_sbs"
    assert saved["eye_order"] == "right_left"
    assert saved["focus_plane_cm"] == 12.0
    assert saved["tracking_mode"] == "vendor_managed"


def test_save_calibration_rejects_invalid_stereo_layout(tmp_path):
    try:
        display_calibration.save_calibration(
            tmp_path / "config.yaml",
            backend_id="stereo_autostereo",
            stereo_layout="checkerboard",
        )
    except ValueError as e:
        assert "stereo_layout" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_save_calibration_rejects_invalid_distance(tmp_path):
    try:
        display_calibration.save_calibration(
            tmp_path / "config.yaml",
            backend_id="stereo_autostereo",
            viewer_distance_cm=0.0,
        )
    except ValueError as e:
        assert "viewer_distance_cm" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_main_writes_calibration(tmp_path, capsys):
    path = tmp_path / "config.yaml"

    code = display_calibration.main([
        "--config",
        str(path),
        "--viewer-distance-cm",
        "70",
        "--panel-resolution",
        "3840x1080",
        "--stereo-layout",
        "half_sbs",
        "--eye-order",
        "right_left",
        "stereo_autostereo",
    ])

    assert code == 0
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))["overlay"]["display_calibration"]
    assert saved["viewer_distance_cm"] == 70.0
    assert saved["panel_width_px"] == 3840
    assert saved["panel_height_px"] == 1080
    assert saved["stereo_layout"] == "half_sbs"
    assert saved["eye_order"] == "right_left"
    assert "wrote stereo_autostereo calibration" in capsys.readouterr().out
