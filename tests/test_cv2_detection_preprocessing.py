from unittest.mock import MagicMock, patch

import numpy as np

from tracker.cv2_temporal_tracker import FaceBox, FaceObservation
from tracker.face_tracker_cv2 import FaceTracker


class Detector:
    def __init__(self, result):
        self.result = result
        self.images = []

    def detect(self, gray, *, prior=None, allow_full_scan=True):
        self.images.append(gray)
        return self.result


class Motion:
    def __init__(self, result):
        self.result = result
        self.current_box = result.box if result is not None else None
        self.initializations = 0

    def track(self, _gray):
        return self.result

    def initialize(self, _gray, box, eyes=None):
        self.current_box = box
        self.initializations += 1
        return True

    def reset(self):
        self.current_box = None


def _tracker(detector, motion, *, frame_index):
    tracker = FaceTracker(
        real_ipd_cm=6.3,
        screen_width_cm=60.0,
        screen_height_cm=34.0,
        camera_fov_deg=90.0,
        detector=detector,
        motion_tracker=motion,
        detection_interval_frames=5,
    )
    tracker._frame_index = frame_index
    return tracker


def test_high_quality_flow_frame_skips_histogram_equalization_and_cascade():
    flow = FaceObservation(FaceBox(20.0, 20.0, 50.0, 50.0), None, "flow", 0.9)
    detector = Detector(None)
    tracker = _tracker(detector, Motion(flow), frame_index=1)
    gray = np.arange(120 * 160, dtype=np.uint8).reshape(120, 160)

    with patch("tracker.face_tracker_cv2.cv2.equalizeHist") as equalize:
        result = tracker._observe(gray)

    assert result is flow
    equalize.assert_not_called()
    assert detector.images == []


def test_detector_frame_equalizes_copy_but_reseeds_flow_from_raw_gray():
    predicted = FaceObservation(
        FaceBox(20.0, 20.0, 50.0, 50.0),
        None,
        "flow",
        0.9,
    )
    detected = FaceObservation(
        FaceBox(21.0, 20.0, 50.0, 50.0),
        None,
        "cascade",
        1.0,
    )
    detector = Detector(detected)
    motion = Motion(predicted)
    tracker = _tracker(detector, motion, frame_index=5)
    raw_gray = np.zeros((120, 160), dtype=np.uint8)
    equalized = np.full((120, 160), 77, dtype=np.uint8)

    with patch(
        "tracker.face_tracker_cv2.cv2.equalizeHist",
        return_value=equalized,
    ) as equalize:
        result = tracker._observe(raw_gray)

    assert result is not None
    equalize.assert_called_once_with(raw_gray)
    assert detector.images == [equalized]
    assert motion.initializations == 1
