from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_reverse_flow_runs_only_after_forward_admission():
    source = _source("tracker/cv2_temporal_tracker.py")
    track = source.split("    def track(self, gray:", 1)[1].split(
        "\n\nclass CascadeFaceDetector:",
        1,
    )[0]

    forward = track.index('direction="forward"')
    forward_count = track.index("forward_count = int(np.count_nonzero(valid))")
    minimum_gate = track.index("if forward_count < self._minimum_points:")
    backward = track.index('direction="backward"')
    round_trip = track.index("round_trip_error = np.linalg.norm(")
    robust_inliers = track.index("inliers = self._robust_inliers(")

    assert forward < forward_count < minimum_gate < backward
    assert backward < round_trip < robust_inliers


def test_round_trip_gate_precedes_motion_and_state_publication():
    source = _source("tracker/cv2_temporal_tracker.py")
    track = source.split("    def track(self, gray:", 1)[1].split(
        "\n\nclass CascadeFaceDetector:",
        1,
    )[0]

    consistency = track.index(
        "round_trip_error <= self._maximum_forward_backward_error"
    )
    minimum_gate = track.index("if consistent_count < self._minimum_points:")
    motion = track.index("if math.hypot(dx, dy) > maximum_motion:")
    publish_box = track.index("self._box = new_box")

    assert consistency < minimum_gate < motion < publish_box


def test_flow_quality_includes_forward_and_round_trip_evidence():
    source = _source("tracker/cv2_temporal_tracker.py")
    track = source.split("    def track(self, gray:", 1)[1].split(
        "\n\nclass CascadeFaceDetector:",
        1,
    )[0]

    assert "forward_quality = math.exp(" in track
    assert "consistency_quality = math.exp(" in track
    assert (
        "valid_fraction * forward_quality * consistency_quality"
        in track
    )


def test_face_tracker_falls_back_to_detection_when_flow_returns_none():
    source = _source("tracker/face_tracker_cv2.py")
    observe = source.split("    def _observe(self, gray:", 1)[1].split(
        "    def _clear_eye_memory",
        1,
    )[0]

    predicted = observe.index("predicted = self._motion.track(gray)")
    detect_due = observe.index("predicted is None")
    detect = observe.index("detected = self._detector.detect(")

    assert predicted < detect_due < detect
    assert "allow_full_scan=True" in observe


def test_documentation_records_threshold_cost_and_fallback():
    docs = _source("docs/OPENCV_FLOW_CONSISTENCY.md")

    assert "at most 1.5 pixels" in docs
    assert "at most 40 points" in docs
    assert "cascade pass on that same frame" in docs
    assert "Malformed point/status/error arrays fail closed" in docs
