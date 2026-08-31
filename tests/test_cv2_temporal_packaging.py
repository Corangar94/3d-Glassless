from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_frozen_package_includes_temporal_tracker_and_cascade_xml():
    spec = _source("Glassless3D.spec")

    assert 'collect_data_files("cv2", includes=["data/*.xml"])' in spec
    assert "*opencv_data," in spec
    assert '"tracker.cv2_temporal_tracker"' in spec


def test_fallback_uses_periodic_detection_and_sparse_flow():
    source = _source("tracker/face_tracker_cv2.py")

    assert "detection_interval_frames: int = 5" in source
    assert "full_scan_interval_frames: int = 30" in source
    assert "maximum_cascade_misses: int = 2" in source
    assert "SparseFaceMotionTracker" in source
    assert "predicted.quality < self._minimum_flow_quality" in source
    assert "self._cascade_misses <= self._maximum_cascade_misses" in source


def test_eye_geometry_is_propagated_and_expires():
    source = _source("tracker/face_tracker_cv2.py")

    assert 'source = "cascade_flow_eyes"' in source
    assert "def _synthetic_eyes(" in source
    assert "self._eye_center_ratio" in source
    assert "self._eye_age_frames > self._eye_track_hold_frames" in source


def test_documentation_records_authoritative_cascade_and_cadence():
    documentation = _source("docs/OPENCV_FALLBACK_TRACKING.md")

    assert "Cascades remain authoritative" in documentation
    assert "every five frames" in documentation
    assert "Every thirty frames" in documentation
    assert "the third miss retires the track" in documentation
    assert "eighteen frames" in documentation
