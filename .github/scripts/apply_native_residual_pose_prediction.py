from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path} {label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "tracker/pose_filter.py",
    '''            return FilteredPose(
                x_cm=0.0,
                y_cm=0.0,
                z_cm=60.0,
                publish_timestamp_ms=publish_ms & _UINT32,
            )
''',
    '''            return FilteredPose(
                x_cm=0.0,
                y_cm=0.0,
                z_cm=60.0,
                publish_timestamp_ms=publish_ms & _UINT32,
                prediction_target_timestamp_ms=publish_ms & _UINT32,
            )
''',
    "uninitialized target timestamp",
)
replace_once(
    "tracker/pose_filter.py",
    '''            capture_timestamp_ms=self._last_capture_timestamp_ms,
            publish_timestamp_ms=publish_ms & _UINT32,
            predicted=target_ms != self._last_capture_timestamp_ms,
''',
    '''            capture_timestamp_ms=self._last_capture_timestamp_ms,
            publish_timestamp_ms=publish_ms & _UINT32,
            prediction_target_timestamp_ms=target_ms,
            predicted=target_ms != self._last_capture_timestamp_ms,
''',
    "filtered pose target timestamp",
)

replace_once(
    "tracker/main.py",
    '''        capture_timestamp_ms=pose.capture_timestamp_ms,
        publish_timestamp_ms=pose.publish_timestamp_ms,
        predicted=pose.predicted,
''',
    '''        capture_timestamp_ms=pose.capture_timestamp_ms,
        publish_timestamp_ms=pose.publish_timestamp_ms,
        prediction_target_timestamp_ms=pose.prediction_target_timestamp_ms,
        predicted=pose.predicted,
''',
    "tilt pose target preservation",
)

replace_once(
    "tracker/pose_shared_memory.py",
    '''class PoseFlags(IntFlag):
    VALID = 1 << 0
    PREDICTED = 1 << 1
    ORIENTATION_VALID = 1 << 2
''',
    '''class PoseFlags(IntFlag):
    VALID = 1 << 0
    PREDICTED = 1 << 1
    ORIENTATION_VALID = 1 << 2
    PREDICTION_LEAD_VALID = 1 << 3
''',
    "lead-valid pose flag",
)
replace_once(
    "tracker/pose_shared_memory.py",
    '''        flags = PoseFlags.VALID if valid else PoseFlags(0)
''',
    '''        # The old reserved uint32 now carries producer prediction lead.
        # Mark that semantic explicitly so a newer overlay can remain safe when
        # paired with an older V2 writer that still leaves the word at zero.
        flags = (
            (PoseFlags.VALID if valid else PoseFlags(0))
            | PoseFlags.PREDICTION_LEAD_VALID
        )
''',
    "lead-valid writer flag",
)

replace_once(
    "overlay/overlay.cpp",
    '''#include "depth_infer.h"
#include "parallax_health.h"
''',
    '''#include "depth_infer.h"
#include "parallax_health.h"
#include "pose_prediction.h"
''',
    "pose prediction include",
)
replace_once(
    "overlay/overlay.cpp",
    '''static constexpr uint32_t POSE_V2_VALID = 1u << 0;
static constexpr uint32_t POSE_V2_PREDICTED = 1u << 1;
''',
    '''static constexpr uint32_t POSE_V2_VALID = 1u << 0;
static constexpr uint32_t POSE_V2_PREDICTED = 1u << 1;
static constexpr uint32_t POSE_V2_PREDICTION_LEAD_VALID = 1u << 3;
''',
    "lead-valid native flag",
)
replace_once(
    "overlay/overlay.cpp",
    '''    uint32_t captureTs, publishTs, flags, reserved;
''',
    '''    uint32_t captureTs, publishTs, flags, predictionLeadMs;
''',
    "PoseV2 lead field",
)
replace_once(
    "overlay/overlay.cpp",
    '''    uint32_t poseAgeMs = kPoseStaleMs + 1;
    uint32_t ts = 0;
''',
    '''    uint32_t poseAgeMs = kPoseStaleMs + 1;
    uint32_t nativeResidualPredictionMs = 0;
    float nativePredictDx = 0.0f, nativePredictDy = 0.0f, nativePredictDz = 0.0f;
    uint32_t ts = 0;
''',
    "native prediction telemetry state",
)
replace_once(
    "overlay/overlay.cpp",
    '''        poseFresh = (poseV2.flags & POSE_V2_VALID) != 0
            && hz > 0.0f
            && poseConfidence >= 0.05f
            && publishAgeMs <= kPoseStaleMs;
''',
    '''        poseFresh = (poseV2.flags & POSE_V2_VALID) != 0
            && hz > 0.0f
            && poseConfidence >= 0.05f
            && publishAgeMs <= kPoseStaleMs;

        const bool predictionLeadKnown =
            (poseV2.flags & POSE_V2_PREDICTION_LEAD_VALID) != 0;
        const auto nativePrediction = g3d::pose_prediction::Extrapolate(
            hx, hy, hz,
            poseVelocityX, poseVelocityY, poseVelocityZ,
            publishAgeMs,
            predictionLeadKnown ? poseV2.predictionLeadMs : publishAgeMs,
            poseConfidence,
            poseFresh && predictionLeadKnown);
        hx = nativePrediction.x;
        hy = nativePrediction.y;
        hz = nativePrediction.z;
        nativeResidualPredictionMs = nativePrediction.residual_ms;
        nativePredictDx = nativePrediction.delta_x_cm;
        nativePredictDy = nativePrediction.delta_y_cm;
        nativePredictDz = nativePrediction.delta_z_cm;
''',
    "bounded residual V2 extrapolation",
)
replace_once(
    "overlay/overlay.cpp",
    '''            Log("PoseV2 source=predicted confidence=%.3f velocity=(%.2f,%.2f,%.2f) orientation=(%.1f,%.1f,%.1f) capture_ts=%u publish_ts=%u flags=0x%X",
                poseConfidence, poseVelocityX, poseVelocityY, poseVelocityZ,
                poseYaw, posePitch, poseRoll, poseV2.captureTs, poseV2.publishTs,
                poseV2.flags);
''',
    '''            Log("PoseV2 source=predicted confidence=%.3f velocity=(%.2f,%.2f,%.2f) orientation=(%.1f,%.1f,%.1f) capture_ts=%u publish_ts=%u producer_lead_ms=%u native_residual_ms=%u native_delta=(%.3f,%.3f,%.3f) flags=0x%X",
                poseConfidence, poseVelocityX, poseVelocityY, poseVelocityZ,
                poseYaw, posePitch, poseRoll, poseV2.captureTs, poseV2.publishTs,
                poseV2.predictionLeadMs, nativeResidualPredictionMs,
                nativePredictDx, nativePredictDy, nativePredictDz,
                poseV2.flags);
''',
    "native residual telemetry",
)
