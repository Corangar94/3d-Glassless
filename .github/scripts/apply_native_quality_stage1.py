from __future__ import annotations

from pathlib import Path
import re


PATH = Path("overlay/overlay.cpp")
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match, found {count}: {old[:120]!r}")
    text = text.replace(old, new, 1)


def regex_once(pattern: str, replacement: str) -> None:
    global text
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"expected one regex match, found {count}: {pattern[:120]!r}")
    text = updated


replace_once(
    '''static const wchar_t* SHM_STATE     = L"G3D_State";      // face-validity state (tracker -> us)
static const wchar_t* SHM_SETTINGS  = L"G3D_Settings";   // live tuning (settings GUI -> us)
#pragma pack(push, 1)
struct HeadPose { float x, y, z; uint32_t ts; };
struct TrackerState { uint32_t state; uint32_t ts; }; // 0=paused, 1=tracking, 2=hold
''',
    '''static const wchar_t* SHM_STATE     = L"G3D_State";      // face-validity state (tracker -> us)
static const wchar_t* SHM_SETTINGS  = L"G3D_Settings";   // live tuning (settings GUI -> us)
static const wchar_t* SHM_POSE_V2   = L"G3D_PoseV2";     // predicted pose + velocity/orientation
static const wchar_t* SHM_POSE_V2_SEQ = L"G3D_PoseV2_Seq";
static constexpr uint32_t POSE_V2_MAGIC = 0x32443347u;
static constexpr uint32_t POSE_V2_VERSION = 2u;
static constexpr uint32_t POSE_V2_VALID = 1u << 0;
static constexpr uint32_t POSE_V2_PREDICTED = 1u << 1;
#pragma pack(push, 1)
struct HeadPose { float x, y, z; uint32_t ts; };
struct PoseV2 {
    uint32_t magic, version;
    float x, y, z;
    float vx, vy, vz;
    float yaw, pitch, roll;
    float confidence;
    uint32_t captureTs, publishTs, flags, reserved;
};
static_assert(sizeof(PoseV2) == 64, "PoseV2 must match tracker.pose_shared_memory");
struct TrackerState { uint32_t state; uint32_t ts; }; // 0=paused, 1=tracking, 2=hold
''',
)

replace_once(
    '''static HANDLE                    g_seqH    = nullptr;
static const void*               g_seqView = nullptr;
static HANDLE                    g_stateH    = nullptr;
''',
    '''static HANDLE                    g_seqH    = nullptr;
static const void*               g_seqView = nullptr;
static HANDLE                    g_poseV2H = nullptr;
static const void*               g_poseV2View = nullptr;
static HANDLE                    g_poseV2SeqH = nullptr;
static const void*               g_poseV2SeqView = nullptr;
static HANDLE                    g_stateH    = nullptr;
''',
)

insert_pose_v2 = r'''
static void TryAttachPoseV2() {
    if (g_poseV2View) return;
    if (!g_poseV2H) {
        g_poseV2H = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_POSE_V2);
        if (g_poseV2H) Log("SHM: OpenFileMapping(G3D_PoseV2) succeeded, handle=%p", g_poseV2H);
    }
    if (g_poseV2H && !g_poseV2View) {
        g_poseV2View = MapViewOfFile(g_poseV2H, FILE_MAP_READ, 0, 0, sizeof(PoseV2));
        if (g_poseV2View) Log("SHM: MapViewOfFile(G3D_PoseV2) succeeded, view=%p", g_poseV2View);
        else Log("SHM: MapViewOfFile(G3D_PoseV2) FAILED, GLE=%lu", GetLastError());
    }
    if (!g_poseV2SeqH) {
        g_poseV2SeqH = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_POSE_V2_SEQ);
        if (g_poseV2SeqH) Log("SHM: OpenFileMapping(G3D_PoseV2_Seq) succeeded, handle=%p", g_poseV2SeqH);
    }
    if (g_poseV2SeqH && !g_poseV2SeqView) {
        g_poseV2SeqView = MapViewOfFile(g_poseV2SeqH, FILE_MAP_READ, 0, 0, sizeof(uint32_t));
        if (g_poseV2SeqView) Log("SHM: MapViewOfFile(G3D_PoseV2_Seq) succeeded, view=%p", g_poseV2SeqView);
    }
}

static bool ReadStablePoseV2(PoseV2* out) {
    if (!g_poseV2View || !out) return false;
    if (!g_poseV2SeqView) return ReadStableSnapshot(g_poseV2View, out);
    for (int attempt = 0; attempt < 8; ++attempt) {
        uint32_t before = 0, after = 0;
        memcpy(&before, g_poseV2SeqView, sizeof(before));
        if (before & 1u) continue;
        MemoryBarrier();
        PoseV2 snapshot = {};
        memcpy(&snapshot, g_poseV2View, sizeof(snapshot));
        MemoryBarrier();
        memcpy(&after, g_poseV2SeqView, sizeof(after));
        if (before == after && (after & 1u) == 0) {
            if (snapshot.magic != POSE_V2_MAGIC || snapshot.version != POSE_V2_VERSION) return false;
            const float values[] = {
                snapshot.x, snapshot.y, snapshot.z,
                snapshot.vx, snapshot.vy, snapshot.vz,
                snapshot.yaw, snapshot.pitch, snapshot.roll,
                snapshot.confidence,
            };
            for (float value : values) if (!std::isfinite(value)) return false;
            *out = snapshot;
            return true;
        }
    }
    return false;
}

'''
replace_once(
    '''// Optional face-validity channel. Older trackers do not publish it, so the
// overlay gracefully falls back to pose timestamp freshness when absent.
static void TryAttachTrackerState() {
''',
    insert_pose_v2 + '''// Optional face-validity channel. Older trackers do not publish it, so the
// overlay gracefully falls back to pose timestamp freshness when absent.
static void TryAttachTrackerState() {
''',
)

replace_once(
    '''    TryAttachShm();
    TryAttachPoseSequence();
    TryAttachTrackerState();
    TryAttachSettings();
    Log("SHM initial attach: pose=%s pose_seq=%s state=%s settings=%s",
        g_shmView ? "ATTACHED" : "(tracker not running?)",
        g_seqView ? "ATTACHED" : "(legacy snapshot fallback)",
        g_stateView ? "ATTACHED" : "(legacy tracker fallback)",
        g_setView ? "ATTACHED" : "(settings GUI not running — OK)");
''',
    '''    TryAttachShm();
    TryAttachPoseSequence();
    TryAttachPoseV2();
    TryAttachTrackerState();
    TryAttachSettings();
    Log("SHM initial attach: pose=%s pose_seq=%s pose_v2=%s state=%s settings=%s",
        g_shmView ? "ATTACHED" : "(tracker not running?)",
        g_seqView ? "ATTACHED" : "(legacy snapshot fallback)",
        g_poseV2View ? "ATTACHED" : "(legacy pose pipeline)",
        g_stateView ? "ATTACHED" : "(legacy tracker fallback)",
        g_setView ? "ATTACHED" : "(settings GUI not running — OK)");
''',
)

replace_once(
    '''    TryAttachShm();
    TryAttachPoseSequence();
    TryAttachTrackerState();
    TryAttachSettings();
''',
    '''    TryAttachShm();
    TryAttachPoseSequence();
    TryAttachPoseV2();
    TryAttachTrackerState();
    TryAttachSettings();
''',
)

pose_block = r'''    // Prefer the versioned producer-filtered pose. It is already filtered and
    // predicted from camera time to display time, so applying One-Euro and a
    // second fixed prediction here would add lag and overshoot. Legacy G3D
    // producers continue through the old filter/prediction fallback.
    float hx = 0.f, hy = 0.f, hz = 60.f;
    float poseVelocityX = 0.f, poseVelocityY = 0.f, poseVelocityZ = 0.f;
    float poseConfidence = 0.f;
    float poseYaw = 0.f, posePitch = 0.f, poseRoll = 0.f;
    uint32_t ts = 0;
    DWORD nowMs = GetTickCount();
    bool poseFresh = false;
    bool newPoseSample = false;
    bool usingPoseV2 = false;
    PoseV2 poseV2 = {};
    if (g_poseV2View && ReadStablePoseV2(&poseV2)) {
        const DWORD publishAgeMs = nowMs - poseV2.publishTs;
        usingPoseV2 = true;
        hx = poseV2.x; hy = poseV2.y; hz = poseV2.z;
        poseVelocityX = poseV2.vx;
        poseVelocityY = poseV2.vy;
        poseVelocityZ = poseV2.vz;
        poseYaw = poseV2.yaw;
        posePitch = poseV2.pitch;
        poseRoll = poseV2.roll;
        poseConfidence = std::clamp(poseV2.confidence, 0.0f, 1.0f);
        ts = poseV2.captureTs;
        shmReads++;
        if (!seenPose || ts != lastShmTs) {
            newPoseSample = true;
            shmChanges++;
            lastShmTs = ts;
            lastPoseChangeMs = nowMs;
            seenPose = true;
        }
        poseFresh = (poseV2.flags & POSE_V2_VALID) != 0
            && hz > 0.0f
            && poseConfidence >= 0.05f
            && publishAgeMs <= kPoseStaleMs;
    } else if (g_shmView) {
        HeadPose p;
        if (!ReadStablePose(&p)) return;
        hx = p.x; hy = p.y; hz = p.z; ts = p.ts;
        shmReads++;
        if (!seenPose || ts != lastShmTs) {
            newPoseSample = true;
            shmChanges++;
            lastShmTs = ts;
            lastPoseChangeMs = nowMs;
            seenPose = true;
        }
        poseFresh = seenPose && (nowMs - lastPoseChangeMs <= kPoseStaleMs);
    }

    uint32_t trackerStateTs = 0;
    uint32_t trackerState = 1;
    bool trackerStateFresh = true;
    if (g_stateView) {
        TrackerState stateSnapshot;
        if (!ReadStableSnapshot(g_stateView, &stateSnapshot)) return;
        trackerState = stateSnapshot.state;
        trackerStateTs = stateSnapshot.ts;
        trackerStateFresh = (nowMs - trackerStateTs) <= kPoseStaleMs;
        poseFresh = poseFresh && trackerStateFresh && trackerState == 1;
    }

    if (!usingPoseV2) {
        if (poseFresh && newPoseSample) {
            double t_sec = (double)ts / 1000.0;
            filteredHx = OneEuroFilter(g_oeX, hx, t_sec);
            filteredHy = OneEuroFilter(g_oeY, hy, t_sec);
            filteredHz = OneEuroFilter(g_oeZ, hz, t_sec);
            lastFilteredWallMs = nowMs;
        }
        if (poseFresh) {
            hx = filteredHx;
            hy = filteredHy;
            hz = filteredHz;
            const float sampleAgeSec = std::min(0.050f, (nowMs - lastFilteredWallMs) / 1000.0f);
            const float predictSec = kPredictHorizonSec + sampleAgeSec;
            poseVelocityX = OneEuroPredictedVelocity(g_oeX);
            poseVelocityY = OneEuroPredictedVelocity(g_oeY);
            poseVelocityZ = OneEuroPredictedVelocity(g_oeZ);
            hx += ClampAbs(poseVelocityX * predictSec, kPredictMaxDeltaCm);
            hy += ClampAbs(poseVelocityY * predictSec, kPredictMaxDeltaCm);
            hz += ClampAbs(poseVelocityZ * predictSec, kPredictMaxDeltaCm);
        }
    }

'''
regex_once(
    r'''    // Read head position from shared memory\n.*?\n    // Explicit rest calibration\. A moving rest EMA absorbs slow deliberate''',
    pose_block + '''    // Explicit rest calibration. A moving rest EMA absorbs slow deliberate''',
)

replace_once(
    '''    const float vx = OneEuroPredictedVelocity(g_oeX);
    const float vy = OneEuroPredictedVelocity(g_oeY);
    const bool motionActive = poseFresh && (std::fabs(vx) + std::fabs(vy) > 0.05f);
''',
    '''    const bool motionActive = poseFresh
        && (std::fabs(poseVelocityX) + std::fabs(poseVelocityY) > 0.05f);
''',
)

replace_once(
    '''            g_hasFrame ? 1 : 0, CaptureStateName(g_captureState), g_captureReason);
    }
''',
    '''            g_hasFrame ? 1 : 0, CaptureStateName(g_captureState), g_captureReason);
        if (usingPoseV2) {
            Log("PoseV2 source=predicted confidence=%.3f velocity=(%.2f,%.2f,%.2f) orientation=(%.1f,%.1f,%.1f) capture_ts=%u publish_ts=%u flags=0x%X",
                poseConfidence, poseVelocityX, poseVelocityY, poseVelocityZ,
                poseYaw, posePitch, poseRoll, poseV2.captureTs, poseV2.publishTs,
                poseV2.flags);
        }
    }
''',
)

replace_once(
    '''    D3D11_SAMPLER_DESC sceneSd = {};
    sceneSd.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
''',
    '''    D3D11_SAMPLER_DESC sceneSd = {};
    // Linear LOD0 sampling avoids sub-pixel stair-stepping as predicted head
    // motion moves the source UV by fractions of a pixel. The shader restores
    // restrained local contrast only on high-confidence, non-edge regions.
    sceneSd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
''',
)

shader_helpers = r'''
float4 SampleSceneQuality(float2 uv, float2 sceneDdx, float2 sceneDdy,
                          float confidence, float depthRange) {
    float4 center = SceneTex.SampleGrad(SceneSmp, uv, sceneDdx, sceneDdy);
    float4 left   = SceneTex.SampleGrad(SceneSmp, uv - sceneDdx, sceneDdx, sceneDdy);
    float4 right  = SceneTex.SampleGrad(SceneSmp, uv + sceneDdx, sceneDdx, sceneDdy);
    float4 up     = SceneTex.SampleGrad(SceneSmp, uv - sceneDdy, sceneDdx, sceneDdy);
    float4 down   = SceneTex.SampleGrad(SceneSmp, uv + sceneDdy, sceneDdx, sceneDdy);
    float stable = confidence * (1.0 - smoothstep(0.04, 0.20, depthRange));
    float amount = 0.10 * stable;
    float3 laplacian = center.rgb * 4.0 - left.rgb - right.rgb - up.rgb - down.rgb;
    return float4(saturate(center.rgb + laplacian * amount), center.a);
}

float2 ResolveDepthDisocclusion(float2 localUV, float2 requestedUV,
                                float targetDepth, float dCropW,
                                float2 sceneDdx, float2 sceneDdy,
                                out float consistency) {
    float2 halfUV = lerp(localUV, requestedUV, 0.50);
    float2 quarterUV = lerp(localUV, requestedUV, 0.25);
    DepthSample full = SampleDepthCohesive(requestedUV, dCropW, sceneDdx, sceneDdy);
    DepthSample half = SampleDepthCohesive(halfUV, dCropW, sceneDdx, sceneDdy);
    DepthSample quarter = SampleDepthCohesive(quarterUV, dCropW, sceneDdx, sceneDdy);
    float fullError = abs(full.depth - targetDepth) + (1.0 - full.confidence) * 0.12;
    float halfError = abs(half.depth - targetDepth) + (1.0 - half.confidence) * 0.12;
    float quarterError = abs(quarter.depth - targetDepth) + (1.0 - quarter.confidence) * 0.12;
    float2 selected = requestedUV;
    float selectedError = fullError;
    if (halfError < selectedError) { selected = halfUV; selectedError = halfError; }
    if (quarterError < selectedError) { selected = quarterUV; selectedError = quarterError; }
    consistency = 1.0 - smoothstep(0.04, 0.18, selectedError);
    return lerp(localUV, selected, lerp(0.30, 1.0, consistency));
}

'''
replace_once(
    '''float4 main(PS_IN i) : SV_Target {
''',
    shader_helpers + '''float4 main(PS_IN i) : SV_Target {
''',
)

replace_once(
    '''    // Inverse texture lookup: subtract the projected screen displacement so
    // a virtual point behind the panel follows the viewer like a real window.
    float2 uv_final = localUV - ApplyConfidenceProtectedParallax(d_final, hz, sw, sh, vd, eyeX, eyeY) * fade;

    // OOB safety (fade should have already prevented it — this is belt &
    // suspenders). saturate() would stretch the edge pixel and cause image
    // doubling, so fall back to the unshifted pixel instead.
    if (uv_final.x < 0.0 || uv_final.x > 1.0 ||
        uv_final.y < 0.0 || uv_final.y > 1.0) {
        uv_final = localUV;
    }
    // SampleGrad with the pre-branch base-UV derivatives: proper mip
    // selection on the shifted UV, matching the on-screen pixel rate.
    return SceneTex.SampleGrad(SceneSmp, uv_final, sceneDdx, sceneDdy);
''',
    '''    // Inverse texture lookup: subtract the projected screen displacement so
    // a virtual point behind the panel follows the viewer like a real window.
    float2 requestedUV = localUV
        - ApplyConfidenceProtectedParallax(d_final, hz, sw, sh, vd, eyeX, eyeY) * fade;
    if (requestedUV.x < 0.0 || requestedUV.x > 1.0 ||
        requestedUV.y < 0.0 || requestedUV.y > 1.0) {
        requestedUV = localUV;
    }

    // At a moving silhouette, the requested source may belong to a different
    // depth layer. Search progressively back toward the unshifted pixel and
    // choose the depth-consistent sample. This prevents foreground colors from
    // being stretched across a newly exposed background without a costly full
    // multilayer renderer.
    float sourceConsistency = 1.0;
    float2 uv_final = ResolveDepthDisocclusion(
        localUV, requestedUV, d_final.depth, dCropW,
        sceneDdx, sceneDdy, sourceConsistency);
    return SampleSceneQuality(
        uv_final, sceneDdx, sceneDdy,
        d_final.confidence * sourceConsistency, d_final.range);
''',
)

replace_once(
    '''    if (g_shmView) UnmapViewOfFile((void*)g_shmView);
    if (g_shmH)    CloseHandle(g_shmH);
    if (g_seqView) UnmapViewOfFile((void*)g_seqView);
    if (g_seqH)    CloseHandle(g_seqH);
''',
    '''    if (g_shmView) UnmapViewOfFile((void*)g_shmView);
    if (g_shmH)    CloseHandle(g_shmH);
    if (g_seqView) UnmapViewOfFile((void*)g_seqView);
    if (g_seqH)    CloseHandle(g_seqH);
    if (g_poseV2View) UnmapViewOfFile((void*)g_poseV2View);
    if (g_poseV2H) CloseHandle(g_poseV2H);
    if (g_poseV2SeqView) UnmapViewOfFile((void*)g_poseV2SeqView);
    if (g_poseV2SeqH) CloseHandle(g_poseV2SeqH);
''',
)

PATH.write_text(text, encoding="utf-8", newline="\n")
