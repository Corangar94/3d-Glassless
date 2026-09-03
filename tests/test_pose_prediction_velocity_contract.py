from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_velocity_is_bounded_before_residual_displacement():
    header = _source("overlay/pose_prediction.h")
    extrapolate = header.split("inline Result Extrapolate(", 1)[1].split(
        "}  // namespace g3d::pose_prediction",
        1,
    )[0]

    velocity = extrapolate.index(
        "const Vector2 bounded_xy_velocity = ClampMagnitude("
    )
    confidence = extrapolate.index(
        "result.confidence_scale = PredictionConfidenceScale(confidence)"
    )
    residual = extrapolate.index(
        "result.signed_residual_ms = ResidualCorrectionMs("
    )
    delta = extrapolate.index(
        "bounded_xy_velocity.x * confidence_dt"
    )
    position = extrapolate.index("result.x += result.delta_x_cm")

    assert velocity < confidence < residual < delta < position
    assert "bounded_z_velocity * confidence_dt" in extrapolate


def test_default_speed_limits_match_tracker_physical_limits():
    header = _source("overlay/pose_prediction.h")
    tracker_config = _source("config.yaml")

    assert "float maximum_xy_speed_cm_s = 300.0f" in header
    assert "float maximum_z_speed_cm_s = 360.0f" in header
    assert "max_xy_speed_cm_s: 300.0" in tracker_config
    assert "max_z_speed_cm_s: 360.0" in tracker_config


def test_velocity_diagnostics_are_native_only_and_abi_stays_unchanged():
    header = _source("overlay/pose_prediction.h")
    pose_memory = _source("tracker/pose_shared_memory.py")
    overlay = _source("overlay/overlay.cpp")

    for field in (
        "input_xy_speed_cm_s",
        "input_z_speed_cm_s",
        "bounded_xy_speed_cm_s",
        "bounded_z_speed_cm_s",
        "velocity_limited",
    ):
        assert field in header
        assert field not in pose_memory

    assert 'POSE_V2_FORMAT = "<IIffffffffffIIII"' in pose_memory
    assert "static_assert(sizeof(PoseV2) == 64" in overlay


def test_native_prediction_test_target_remains_registered():
    cmake = _source("overlay/CMakeLists.txt")

    assert "pose_prediction_tests.cpp" in cmake
    assert "NAME pose_prediction_tests" in cmake


def test_documentation_explains_tiny_interval_spike_protection():
    docs = _source("docs/POSE_PREDICTION_ALIGNMENT.md")

    assert "Physical velocity boundary" in docs
    assert "5,000 cm/s velocity" in docs
    assert "0.30 cm in combined X/Y" in docs
    assert "Invalid or non-finite speed limits fail closed" in docs
