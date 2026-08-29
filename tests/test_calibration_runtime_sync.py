import math

import pytest
import yaml

from tracker.calibration_runtime_sync import (
    horizontal_fov_deg,
    synchronize_runtime_projection,
)
from tracker.camera_geometry import CameraGeometry, CameraIntrinsics


def _geometry() -> CameraGeometry:
    return CameraGeometry(
        intrinsics=CameraIntrinsics(
            width=1280,
            height=720,
            fx=640.0,
            fy=650.0,
            cx=640.0,
            cy=360.0,
        )
    )


def test_horizontal_fov_comes_from_calibrated_fx():
    fov = horizontal_fov_deg(_geometry())

    assert fov == pytest.approx(90.0, abs=1e-6)


def test_sync_updates_tracker_and_overlay_fov_atomically(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "tracking": {"camera_fov_deg": 60.0, "ipd_cm": 6.4},
                "overlay": {"camera_fov_deg": 60.0},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    synchronize_runtime_projection(path, _geometry())

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["tracking"]["camera_fov_deg"] == pytest.approx(90.0)
    assert saved["overlay"]["camera_fov_deg"] == pytest.approx(90.0)
    assert saved["tracking"]["ipd_cm"] == 6.4
    assert not (tmp_path / "config.yaml.tmp").exists()


def test_center_sync_updates_current_and_legacy_viewer_distance(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("overlay: {}\n", encoding="utf-8")

    synchronize_runtime_projection(
        path,
        _geometry(),
        viewer_distance_cm=72.5,
    )

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    overlay = saved["overlay"]
    assert overlay["head_dist_cm"] == 72.5
    assert overlay["display_calibration"]["viewer_distance_cm"] == 72.5


def test_invalid_viewer_distance_is_rejected_without_replacing_config(tmp_path):
    path = tmp_path / "config.yaml"
    original = "tracking:\n  camera_fov_deg: 80\n"
    path.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        synchronize_runtime_projection(
            path,
            _geometry(),
            viewer_distance_cm=float("nan"),
        )

    assert path.read_text(encoding="utf-8") == original


def test_fov_none_without_intrinsics():
    assert horizontal_fov_deg(CameraGeometry()) is None
