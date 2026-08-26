from pathlib import Path

path = Path("overlay/overlay.cpp")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''#include "capture_recovery.h"
#include "depth_infer.h"
''',
    '''#include "capture_recovery.h"
#include "depth_infer.h"
#include "parallax_health.h"
''',
    "parallax health include",
)

replace_once(
    '''static double                    g_lastFrameCpuMs = 0.0;
static int                       g_gpuTimingSamples = 0;
''',
    '''static double                    g_lastFrameCpuMs = 0.0;
static int                       g_gpuTimingSamples = 0;
static float                     g_parallaxHealthScale = 0.0f;
static float                     g_parallaxHealthTarget = 0.0f;
static uint64_t                  g_parallaxHealthLastMs = 0;
''',
    "parallax health globals",
)

replace_once(
    '''    float poseConfidence = 0.f;
    float poseYaw = 0.f, posePitch = 0.f, poseRoll = 0.f;
    uint32_t ts = 0;
''',
    '''    float poseConfidence = 0.f;
    float poseYaw = 0.f, posePitch = 0.f, poseRoll = 0.f;
    uint32_t poseAgeMs = kPoseStaleMs + 1;
    uint32_t ts = 0;
''',
    "pose age state",
)

replace_once(
    '''    if (g_poseV2View && ReadStablePoseV2(&poseV2)) {
        const DWORD publishAgeMs = nowMs - poseV2.publishTs;
        usingPoseV2 = true;
''',
    '''    if (g_poseV2View && ReadStablePoseV2(&poseV2)) {
        const DWORD publishAgeMs = nowMs - poseV2.publishTs;
        poseAgeMs = publishAgeMs;
        usingPoseV2 = true;
''',
    "v2 pose age",
)

replace_once(
    '''        poseFresh = seenPose && (nowMs - lastPoseChangeMs <= kPoseStaleMs);
    }

    uint32_t trackerStateTs = 0;
''',
    '''        poseAgeMs = seenPose ? (nowMs - lastPoseChangeMs) : kPoseStaleMs + 1;
        poseFresh = seenPose && (poseAgeMs <= kPoseStaleMs);
    }

    uint32_t trackerStateTs = 0;
''',
    "legacy pose age",
)

replace_once(
    '''    UpdateOverlayVisibility();

    // Periodic summary based on wall time. Capture recovery can intentionally
''',
    '''    UpdateOverlayVisibility();

    // Convert upstream tracking/depth health into a continuous comfort
    // envelope. Pose confidence/age degrades parallax before the hard stale
    // cutoff, while recovery ramps back more slowly to avoid a visible pop.
    const uint32_t depthAgeMs = g_depth ? g_depth->depth_age_ms() : 0u;
    const bool depthReady = g_depth && g_depth->inferences_completed() > 0;
    const g3d::parallax::HealthInputs healthInputs = {
        poseFresh,
        depthReady,
        usingPoseV2,
        usingPoseV2 ? poseConfidence : 1.0f,
        poseAgeMs,
        depthAgeMs,
    };
    g_parallaxHealthTarget = g3d::parallax::TargetScale(healthInputs);
    const uint64_t healthNowMs = GetTickCount64();
    const float healthDt = g_parallaxHealthLastMs == 0
        ? 1.0f / 120.0f
        : static_cast<float>(std::min<uint64_t>(250, healthNowMs - g_parallaxHealthLastMs)) / 1000.0f;
    g_parallaxHealthLastMs = healthNowMs;
    g_parallaxHealthScale = g3d::parallax::SlewScale(
        g_parallaxHealthScale,
        g_parallaxHealthTarget,
        healthDt);
    const bool healthAnimating = std::fabs(
        g_parallaxHealthScale - g_parallaxHealthTarget) > 0.0025f;

    // Periodic summary based on wall time. Capture recovery can intentionally
''',
    "continuous parallax health envelope",
)

replace_once(
    '''        if (g_depth) {
            Log("DepthIO path=%s fallbacks=%llu",
''',
    '''        Log("ParallaxHealth scale=%.3f target=%.3f pose_fresh=%d pose_source=%s pose_confidence=%.3f pose_age_ms=%u depth_ready=%d depth_age_ms=%u",
            g_parallaxHealthScale,
            g_parallaxHealthTarget,
            poseFresh ? 1 : 0,
            usingPoseV2 ? "v2" : "legacy",
            usingPoseV2 ? poseConfidence : 1.0f,
            poseAgeMs,
            depthReady ? 1 : 0,
            depthAgeMs);
        if (g_depth) {
            Log("DepthIO path=%s fallbacks=%llu",
''',
    "health summary log",
)

replace_once(
    '''    const bool animationDue = (motionActive || blendActive) && (nowMs - lastRenderMs >= 8);
''',
    '''    const bool animationDue = (motionActive || blendActive || healthAnimating)
        && (nowMs - lastRenderMs >= 8);
''',
    "health envelope rendering cadence",
)

replace_once(
    '''    CBuf cb = {
        dx, dy, hz,
        g_strengthX, g_strengthY, g_screenW, g_screenH, g_virtualDepth,
''',
    '''    CBuf cb = {
        dx, dy, hz,
        g_strengthX * g_parallaxHealthScale,
        g_strengthY * g_parallaxHealthScale,
        g_screenW, g_screenH, g_virtualDepth,
''',
    "health-scaled shader strength",
)

path.write_text(text, encoding="utf-8", newline="\n")
