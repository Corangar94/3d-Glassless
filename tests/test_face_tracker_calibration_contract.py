from pathlib import Path


def _source() -> str:
    return Path("tracker/face_tracker.py").read_text(encoding="utf-8")


def test_pose_conversion_snapshots_before_reading_landmarks():
    method = _source().split("    def _pose_from_result(", 1)[1].split(
        "    def _on_result(",
        1,
    )[0]

    snapshot = method.index("calibration = self._calibration_snapshot()")
    landmarks = method.index('getattr(result, "face_landmarks", None)')
    depth = method.index("estimate_z_cm(")
    xy = method.index("estimate_xy_cm(")

    assert snapshot < landmarks < depth < xy
    assert "calibration.real_ipd_cm" in method
    assert "calibration.camera_fov_deg" in method
    assert "geometry = calibration.camera_geometry" in method


def test_setter_validates_both_values_before_entering_mutation_lock():
    method = _source().split("    def set_calibration(", 1)[1].split(
        "    def reset_session(",
        1,
    )[0]

    ipd_validation = method.index("if not math.isfinite(parsed_ipd_cm):")
    fov_validation = method.index("if not math.isfinite(parsed_fov_deg)")
    lock = method.index('lock = getattr(self, "_lock", None)')
    ipd_assignment = method.rindex("self._real_ipd_cm = next_ipd_cm")
    fov_assignment = method.rindex("self._camera_fov_deg = next_fov_deg")

    assert ipd_validation < lock
    assert fov_validation < lock
    assert lock < ipd_assignment
    assert lock < fov_assignment


def test_documentation_describes_inflight_generation_behavior():
    docs = Path("docs/MEDIAPIPE_CALIBRATION_ATOMICITY.md").read_text(
        encoding="utf-8"
    )

    assert "one locked calibration snapshot" in docs
    assert "finishes using the generation it captured" in docs
    assert "cannot leave a simultaneously supplied IPD partially committed" in docs
