from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_async_backlog_admission_precedes_input_preparation():
    source = _source("tracker/face_tracker.py")
    process = source.split("    def process_frame(", 1)[1].split(
        "    def close(",
        1,
    )[0]

    admission = process.index("watchdog.should_submit(")
    preparation = process.index("self._prepared_mediapipe_image(frame_bgr)")
    submission = process.index("self._landmarker.detect_async(")

    assert admission < preparation < submission


def test_bgr_resize_precedes_rgb_conversion_and_image_allocation():
    helper = _source("tracker/mediapipe_input.py")
    tracker = _source("tracker/face_tracker.py")
    preparation = tracker.split(
        "    def _prepared_mediapipe_image(",
        1,
    )[1].split("    def process_frame(", 1)[0]
    conversion = tracker.split("    def _mediapipe_image(", 1)[1].split(
        "    def _prepared_mediapipe_image(",
        1,
    )[0]

    assert "cv2.resize(" in helper
    assert "cv2.cvtColor(" not in helper
    assert "prepare_mediapipe_bgr_frame(" in preparation
    assert "self._mediapipe_image(prepared.frame_bgr)" in preparation
    assert "cv2.cvtColor(" in conversion
    assert "mp.Image(" in conversion


def test_synchronous_pose_conversion_uses_prepared_dimensions():
    source = _source("tracker/face_tracker.py")
    process = source.split("    def process_frame(", 1)[1].split(
        "    def close(",
        1,
    )[0]

    assert "image, prepared_width, prepared_height" in process
    assert "prepared_width," in process
    assert "prepared_height," in process
    assert "h, w = frame_bgr.shape[:2]" not in process


def test_original_camera_frame_is_not_reassigned_by_preparation():
    source = _source("tracker/face_tracker.py")
    process = source.split("    def process_frame(", 1)[1].split(
        "    def close(",
        1,
    )[0]

    assert "frame_bgr =" not in process
    assert process.count("self._prepared_mediapipe_image(frame_bgr)") == 2


def test_docs_record_default_pixel_reduction_and_opt_out():
    docs = _source("docs/MEDIAPIPE_INPUT_RESOLUTION.md")

    assert "43.75%" in docs
    assert "Set the cap to `0`" in docs
    assert "before BGR-to-RGB conversion" in docs
