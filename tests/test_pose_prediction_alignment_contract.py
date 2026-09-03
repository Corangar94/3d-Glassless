from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_overlay_passes_publish_age_and_producer_lead_to_native_prediction():
    overlay = _source("overlay/overlay.cpp")
    call = overlay.split(
        "const auto nativePrediction = g3d::pose_prediction::Extrapolate(",
        1,
    )[1].split(");", 1)[0]

    assert "publishAgeMs" in call
    assert "poseV2.predictionLeadMs" in call
    assert "poseFresh && predictionLeadKnown" in call


def test_extrapolate_uses_signed_residual_and_confidence_gain():
    header = _source("overlay/pose_prediction.h")
    extrapolate = header.split("inline Result Extrapolate(", 1)[1].split(
        "}  // namespace g3d::pose_prediction",
        1,
    )[0]

    confidence = extrapolate.index("PredictionConfidenceScale(confidence)")
    correction = extrapolate.index("ResidualCorrectionMs(")
    signed_dt = extrapolate.index(
        "static_cast<float>(result.signed_residual_ms) / 1000.0f"
    )
    position = extrapolate.index("result.x += result.delta_x_cm")

    assert confidence < correction < signed_dt < position
    assert "result.rewound = result.signed_residual_ms < 0" in extrapolate


def test_historical_forward_only_helper_remains_available():
    header = _source("overlay/pose_prediction.h")

    assert "inline uint32_t ResidualDelayMs(" in header
    assert "inline int32_t ResidualCorrectionMs(" in header
    assert header.index("ResidualDelayMs(") < header.index("ResidualCorrectionMs(")


def test_shared_memory_abi_does_not_change():
    pose_memory = _source("tracker/pose_shared_memory.py")
    overlay = _source("overlay/overlay.cpp")

    assert 'POSE_V2_FORMAT = "<IIffffffffffIIII"' in pose_memory
    assert "static_assert(sizeof(PoseV2) == 64" in overlay
    assert "predictionLeadMs" in overlay


def test_native_test_target_remains_registered():
    cmake = _source("overlay/CMakeLists.txt")

    assert "pose_prediction_tests.cpp" in cmake
    assert "NAME pose_prediction_tests" in cmake


def test_documentation_records_signed_alignment_and_confidence_behavior():
    docs = _source("docs/POSE_PREDICTION_ALIGNMENT.md")

    assert "pose must be rewound" in docs
    assert "forward correction is limited to 20 ms" in docs
    assert "rewind correction is limited to 80 ms" in docs
    assert "confidence 0.45 applies half" in docs
