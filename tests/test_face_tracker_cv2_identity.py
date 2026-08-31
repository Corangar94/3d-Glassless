from tracker.cv2_temporal_tracker import EyePair, FaceBox, FaceObservation
from tracker.face_tracker_cv2 import FaceTracker


class FakeDetector:
    def detect(self, *_args, **_kwargs):
        return None


class FakeMotion:
    current_box = None

    def track(self, _gray):
        return None

    def initialize(self, _gray, box, eyes=None):
        self.current_box = box
        return True

    def reset(self):
        self.current_box = None


def _tracker() -> FaceTracker:
    return FaceTracker(
        real_ipd_cm=6.3,
        screen_width_cm=60.0,
        screen_height_cm=34.0,
        camera_fov_deg=90.0,
        detector=FakeDetector(),
        motion_tracker=FakeMotion(),
    )


def test_incompatible_detection_clears_previous_subject_eye_memory():
    tracker = _tracker()
    first_box = FaceBox(10.0, 10.0, 50.0, 60.0)
    first_eyes = EyePair((23.0, 30.0), (47.0, 31.0))
    tracker._usable_eyes(
        FaceObservation(first_box, first_eyes, "cascade", 1.0)
    )
    assert tracker._eye_ratio is not None

    predicted = FaceObservation(first_box, first_eyes, "flow", 0.9)
    different_face = FaceObservation(
        FaceBox(120.0, 70.0, 55.0, 65.0),
        None,
        "cascade",
        1.0,
    )
    corrected = tracker._corrected_detection(
        predicted,
        different_face,
        __import__("numpy").zeros((180, 240), dtype="uint8"),
    )

    assert corrected.box == different_face.box
    assert corrected.eyes is None
    assert tracker._eye_ratio is None
    assert tracker._eye_center_ratio is None
    assert tracker._usable_eyes(corrected) is None
