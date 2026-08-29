from tracker.pose import FilteredPose, HeadPosition
from tracker.pose_filter import AdaptivePoseFilter
from tracker.pose_shared_memory import (
    POSE_V2_SIZE,
    PoseFlags,
    PoseStateReader,
    PoseStateWriter,
    _prediction_lead_ms,
)


def test_filtered_pose_records_the_exact_prediction_target():
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
        prediction_horizon_ms=10.0,
        max_prediction_ms=80.0,
    )
    filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=1000,
        ),
        publish_timestamp_ms=1000,
    )
    output = filter_.update_pose(
        HeadPosition(
            x_cm=1.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=1033,
        ),
        publish_timestamp_ms=1065,
    )

    assert output.publish_timestamp_ms == 1065
    assert output.prediction_target_timestamp_ms == 1075
    assert output.predicted


def test_prediction_lead_encoding_is_wrap_safe():
    pose = FilteredPose(
        x_cm=0.0,
        y_cm=0.0,
        z_cm=60.0,
        publish_timestamp_ms=0xFFFF_FFFA,
        prediction_target_timestamp_ms=4,
        predicted=True,
    )

    assert _prediction_lead_ms(pose, pose.publish_timestamp_ms) == 10


def test_unreasonable_or_missing_prediction_target_encodes_zero_lead():
    missing = FilteredPose(x_cm=0.0, y_cm=0.0, z_cm=60.0)
    unreasonable = FilteredPose(
        x_cm=0.0,
        y_cm=0.0,
        z_cm=60.0,
        prediction_target_timestamp_ms=5000,
    )

    assert _prediction_lead_ms(missing, 1000) == 0
    assert _prediction_lead_ms(unreasonable, 1000) == 0


def test_pose_v2_transport_preserves_abi_and_prediction_lead():
    name = "G3D_POSE_V2_PREDICTION_TRANSPORT_TEST"
    assert POSE_V2_SIZE == 64
    pose = FilteredPose(
        x_cm=1.0,
        y_cm=-2.0,
        z_cm=61.0,
        vx_cm_s=80.0,
        vy_cm_s=-20.0,
        vz_cm_s=5.0,
        confidence=0.9,
        capture_timestamp_ms=1000,
        publish_timestamp_ms=1040,
        prediction_target_timestamp_ms=1052,
        predicted=True,
    )

    with PoseStateWriter(name=name) as writer:
        reader = PoseStateReader(name=name)
        try:
            writer.write(pose, valid=True)
            packet = reader.read()
        finally:
            reader.close()

    assert packet is not None
    assert packet.prediction_lead_ms == 12
    assert packet.flags & PoseFlags.VALID
    assert packet.flags & PoseFlags.PREDICTED
    assert packet.flags & PoseFlags.PREDICTION_LEAD_VALID


def test_native_overlay_only_extrapolates_when_lead_semantic_is_known():
    source = open("overlay/overlay.cpp", encoding="utf-8").read()

    assert "POSE_V2_PREDICTION_LEAD_VALID" in source
    assert "predictionLeadKnown" in source
    assert "g3d::pose_prediction::Extrapolate(" in source
    assert "predictionLeadKnown ? poseV2.predictionLeadMs : publishAgeMs" in source
    assert "poseFresh && predictionLeadKnown" in source
    assert "native_residual_ms=%u" in source
