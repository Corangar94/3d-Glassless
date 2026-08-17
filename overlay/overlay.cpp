// overlay/overlay.cpp
// Glassless3D screen-capture overlay.
// Captures the primary monitor (excluding this window), applies head-tracked
// parallax warp, and presents in a borderless topmost window.
//
// Requirements: game must run in Windowed Fullscreen (not exclusive fullscreen).
// Quit: right-click tray icon, or Ctrl+Shift+G (global hotkey)
//
// Usage: Glassless3DOverlay.exe [screen_width_cm] [screen_height_cm] [strength] [virtual_depth_cm]
//   virtual_depth_cm — how far behind the screen the virtual plane sits.
//                      0 = flat head-tracked pan; 10–20 = noticeable "window" 3D effect.

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <wincodec.h>
#include <d3d11.h>
#include <dxgi1_2.h>
#include <dxgi1_3.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>
#include <inspectable.h>
#include <roapi.h>
#include <winstring.h>
#include <windows.foundation.h>
#include <windows.graphics.directx.h>
#include <windows.graphics.directx.direct3d11.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <chrono>
#include <ctime>
#include <cstdarg>
#include <cwctype>
#include <share.h>
#include <optional>
#include <string>
#include <vector>

#include "capture_recovery.h"
#include "depth_infer.h"

namespace g3d::wgc {

struct SizeInt32 {
    INT32 Width;
    INT32 Height;
};

MIDL_INTERFACE("A9B3D012-3DF2-4EE3-B8D1-8695F457D3C1")
IDirect3DDxgiInterfaceAccess : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE GetInterface(REFIID iid, void** p) = 0;
};

MIDL_INTERFACE("3628E81B-3CAC-4C60-B7F4-23CE0E0C3356")
IGraphicsCaptureItemInterop : public IUnknown {
    virtual HRESULT STDMETHODCALLTYPE CreateForWindow(HWND window, REFIID iid, void** result) = 0;
    virtual HRESULT STDMETHODCALLTYPE CreateForMonitor(HMONITOR monitor, REFIID iid, void** result) = 0;
};

MIDL_INTERFACE("79C3F95B-31F7-4EC2-A464-632EF5D30760")
IGraphicsCaptureItem : public IInspectable {
    virtual HRESULT STDMETHODCALLTYPE get_DisplayName(HSTRING* value) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_Size(SizeInt32* value) = 0;
    virtual HRESULT STDMETHODCALLTYPE add_Closed(IUnknown* handler, EventRegistrationToken* token) = 0;
    virtual HRESULT STDMETHODCALLTYPE remove_Closed(EventRegistrationToken token) = 0;
};

MIDL_INTERFACE("FA50C623-38DA-4B32-ACF3-FA9734AD800E")
IDirect3D11CaptureFrame : public IInspectable {
    virtual HRESULT STDMETHODCALLTYPE get_Surface(
        ABI::Windows::Graphics::DirectX::Direct3D11::IDirect3DSurface** value) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_SystemRelativeTime(ABI::Windows::Foundation::TimeSpan* value) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_ContentSize(SizeInt32* value) = 0;
};

MIDL_INTERFACE("814E42A9-F70F-4AD7-939B-FDDCC6EB880D")
IGraphicsCaptureSession : public IInspectable {
    virtual HRESULT STDMETHODCALLTYPE StartCapture() = 0;
};

MIDL_INTERFACE("24EB6D22-1975-422E-82E7-780DBD8DDF24")
IDirect3D11CaptureFramePool : public IInspectable {
    virtual HRESULT STDMETHODCALLTYPE Recreate(
        ABI::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice* device,
        ABI::Windows::Graphics::DirectX::DirectXPixelFormat pixelFormat,
        INT32 numberOfBuffers,
        SizeInt32 size) = 0;
    virtual HRESULT STDMETHODCALLTYPE TryGetNextFrame(IDirect3D11CaptureFrame** result) = 0;
    virtual HRESULT STDMETHODCALLTYPE add_FrameArrived(IUnknown* handler, EventRegistrationToken* token) = 0;
    virtual HRESULT STDMETHODCALLTYPE remove_FrameArrived(EventRegistrationToken token) = 0;
    virtual HRESULT STDMETHODCALLTYPE CreateCaptureSession(
        IGraphicsCaptureItem* item,
        IGraphicsCaptureSession** result) = 0;
    virtual HRESULT STDMETHODCALLTYPE get_DispatcherQueue(IUnknown** value) = 0;
};

MIDL_INTERFACE("589B103F-6BBC-5DF5-A991-02E28B3B66D5")
IDirect3D11CaptureFramePoolStatics2 : public IInspectable {
    virtual HRESULT STDMETHODCALLTYPE CreateFreeThreaded(
        ABI::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice* device,
        ABI::Windows::Graphics::DirectX::DirectXPixelFormat pixelFormat,
        INT32 numberOfBuffers,
        SizeInt32 size,
        IDirect3D11CaptureFramePool** result) = 0;
};

using CreateDirect3D11DeviceFromDXGIDeviceFn = HRESULT (WINAPI*)(
    IDXGIDevice* dxgiDevice,
    IInspectable** graphicsDevice);

}  // namespace g3d::wgc

__CRT_UUID_DECL(g3d::wgc::IDirect3DDxgiInterfaceAccess,
    0xA9B3D012, 0x3DF2, 0x4EE3, 0xB8, 0xD1, 0x86, 0x95, 0xF4, 0x57, 0xD3, 0xC1)
__CRT_UUID_DECL(g3d::wgc::IGraphicsCaptureItemInterop,
    0x3628E81B, 0x3CAC, 0x4C60, 0xB7, 0xF4, 0x23, 0xCE, 0x0E, 0x0C, 0x33, 0x56)
__CRT_UUID_DECL(g3d::wgc::IGraphicsCaptureItem,
    0x79C3F95B, 0x31F7, 0x4EC2, 0xA4, 0x64, 0x63, 0x2E, 0xF5, 0xD3, 0x07, 0x60)
__CRT_UUID_DECL(g3d::wgc::IDirect3D11CaptureFramePoolStatics2,
    0x589B103F, 0x6BBC, 0x5DF5, 0xA9, 0x91, 0x02, 0xE2, 0x8B, 0x3B, 0x66, 0xD5)

// ── Logging ───────────────────────────────────────────────────────────────
// Writes to overlay.log next to the exe. One FILE* held open for lifetime.
static FILE* g_log = nullptr;

static void LogInit() {
    // Write log next to the exe, regardless of the process CWD.
    // _wfsopen with _SH_DENYNO lets other tools read the log while we're running.
    wchar_t exePath[MAX_PATH]; GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    wchar_t* slash = wcsrchr(exePath, L'\\');
    if (slash) *(slash + 1) = L'\0';
    wchar_t logPath[MAX_PATH];
    swprintf_s(logPath, MAX_PATH, L"%soverlay.log", exePath);
    g_log = _wfsopen(logPath, L"w", _SH_DENYNO);  // shared read/write
    if (g_log) {
        setvbuf(g_log, nullptr, _IONBF, 0);  // unbuffered — survives crashes
    }
}

static void Log(const char* fmt, ...) {
    if (!g_log) return;
    SYSTEMTIME st; GetLocalTime(&st);
    fprintf(g_log, "[%02d:%02d:%02d.%03d] ",
            st.wHour, st.wMinute, st.wSecond, st.wMilliseconds);
    va_list args; va_start(args, fmt);
    vfprintf(g_log, fmt, args);
    va_end(args);
    fputc('\n', g_log);
}

static void LogHR(const char* what, HRESULT hr) {
    Log("%s -> HRESULT=0x%08X %s", what, (unsigned)hr, SUCCEEDED(hr) ? "OK" : "FAIL");
}

static void SafeReleaseQuery(ID3D11Query*& query) {
    if (query) {
        query->Release();
        query = nullptr;
    }
}

template <typename T>
static void SafeRelease(T*& value) {
    if (value) {
        value->Release();
        value = nullptr;
    }
}

static void LogClose() {
    if (g_log) { Log("=== log closed ==="); fclose(g_log); g_log = nullptr; }
}

// WDA_EXCLUDEFROMCAPTURE – hide our window from DXGI / screen recorders
// (Windows 10 2004+). Prevents the black self-capture feedback loop.
#ifndef WDA_EXCLUDEFROMCAPTURE
#define WDA_EXCLUDEFROMCAPTURE 0x00000011
#endif

#ifndef RO_E_CLOSED
#define RO_E_CLOSED _HRESULT_TYPEDEF_(0x80000013)
#endif

static void EnablePerMonitorV2DpiAwareness() {
    using SetProcessDpiAwarenessContextFn = BOOL(WINAPI*)(HANDLE);
    const HMODULE user32 = GetModuleHandleW(L"user32.dll");
    const auto setContext = user32
        ? reinterpret_cast<SetProcessDpiAwarenessContextFn>(
            GetProcAddress(user32, "SetProcessDpiAwarenessContext"))
        : nullptr;
    if (!setContext) {
        Log("SetProcessDpiAwarenessContext unavailable; continuing with current DPI context");
        return;
    }
    const HANDLE perMonitorV2 = reinterpret_cast<HANDLE>(static_cast<intptr_t>(-4));
    const BOOL ok = setContext(perMonitorV2);
    Log("SetProcessDpiAwarenessContext(PMv2): ok=%d GLE=%lu",
        ok ? 1 : 0, ok ? 0 : GetLastError());
}

// ── Shared-memory layout (must match tracker/shared_memory.py) ────────────
static const wchar_t* SHM_NAME      = L"G3D";            // head pose (tracker -> us)
static const wchar_t* SHM_SEQ       = L"G3D_Seq";        // optional pose seqlock companion
static const wchar_t* SHM_STATE     = L"G3D_State";      // face-validity state (tracker -> us)
static const wchar_t* SHM_SETTINGS  = L"G3D_Settings";   // live tuning (settings GUI -> us)
#pragma pack(push, 1)
struct HeadPose { float x, y, z; uint32_t ts; };
struct TrackerState { uint32_t state; uint32_t ts; }; // 0=paused, 1=tracking, 2=hold
// Must match tracker/shared_settings.py STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI" (88 bytes)
struct Settings {
    float     strengthX;
    float     strengthY;
    float     virtualDepthCm;
    float     screenWCm;
    float     screenHCm;
    uint32_t  depthCurve;    // 0=linear, 1=sqrt, 2=gamma
    float     depthGamma;
    float     focusRadius;
    float     headDistCm;
    float     cameraFovDeg;
    float     ipdMm;
    float     smoothingAlpha;
    float     deadzoneM;
    uint32_t  displayBackend; // 0=desktop, 1=stereo, 2=quilt
    uint32_t  depthMode;      // 0=quality, 1=balanced, 2=fast
    uint32_t  version;
    uint32_t  stereoLayout;   // 0=full_sbs, 1=half_sbs
    uint32_t  eyeOrder;       // 0=left_right, 1=right_left
    uint32_t  panelWidthPx;
    uint32_t  panelHeightPx;
    float     focusPlaneCm;
    uint32_t  trackingMode;   // 0=glassless3d_managed, 1=vendor_managed
};  // 88 bytes
static_assert(sizeof(Settings) == 88, "Settings SHM layout must match tracker.shared_settings");
#pragma pack(pop)

template <typename T>
static bool ReadStableSnapshot(const void* view, T* out) {
    if (!view || !out) return false;
    T first = {}, second = {};
    for (int attempt = 0; attempt < 3; ++attempt) {
        memcpy(&first, view, sizeof(T));
        MemoryBarrier();
        memcpy(&second, view, sizeof(T));
        if (memcmp(&first, &second, sizeof(T)) == 0) {
            *out = second;
            return true;
        }
    }
    return false;
}

static bool ReadStableSettings(const void* view, Settings* out) {
    if (!view || !out) return false;
    Settings first = {}, second = {};
    for (int attempt = 0; attempt < 3; ++attempt) {
        memcpy(&first, view, sizeof(first));
        MemoryBarrier();
        memcpy(&second, view, sizeof(second));
        if ((first.version & 1u) == 0 && first.version == second.version
            && memcmp(&first, &second, sizeof(first)) == 0) {
            *out = second;
            return true;
        }
    }
    return false;
}

// ── Shader source ─────────────────────────────────────────────────────────
static const char VS_SRC[] = R"hlsl(
struct VS_OUT { float4 pos : SV_Position; float2 uv : TEXCOORD; };
VS_OUT main(uint id : SV_VertexID) {
    VS_OUT o;
    o.uv  = float2((id & 1) ? 1.0 : 0.0, (id & 2) ? 1.0 : 0.0);
    o.pos = float4(o.uv.x * 2.0 - 1.0, 1.0 - o.uv.y * 2.0, 0.0, 1.0);
    return o;
}
)hlsl";

static const char PS_SRC[] = R"hlsl(
cbuffer CB : register(b0) {
    float headX;           // cm, right = positive
    float headY;           // cm, up    = positive
    float headZ;           // cm, distance from screen (~60 typical)
    float strengthX;       // horizontal parallax amplifier
    float strengthY;       // vertical parallax amplifier
    float screenW;         // cm
    float screenH;         // cm
    float virtualDepth;    // cm — far-plane distance behind the screen
    float debugDepthMode;  // 0=off, 1=depth, 2=confidence, 3=edge/range
    float depthGamma;      // gamma exponent (curve mode 2)
    float focusRadius;     // (reserved — unused in current parallax model)
    float depthCurve;      // 0=linear, 1=sqrt, 2=gamma
    // Depth UV correction: the depth texture covers only the center crop of
    // the captured frame.  Convert screen UV X → depth texture UV X via:
    //   depthUV.x = (screenUV.x - depthCropX0) / depthCropW
    // For 5120×1440: depthCropX0=0.25, depthCropW=0.5 → (x-0.25)*2
    float depthCropX0;     // left edge of center crop in screen UV
    float depthCropW;      // width of center crop in screen UV
    // Render-rate depth interpolation: blend_t advances 0→1 over kBlendFrames
    // render frames after each new inference, hiding the 10 Hz update rate.
    float depthBlend;      // 0=prev inference, 1=latest inference
    float displayBackend;  // 0=desktop_overlay, 1=stereo_autostereo, 2=lightfield_quilt
    float ipdCm;           // inter-pupillary distance in cm for stereo view spread
    float stereoLayout;    // 0=full_sbs, 1=half_sbs
    float eyeOrder;        // 0=left_right, 1=right_left
    float focusPlaneCm;    // convergence plane behind screen where parallax is zero
};
Texture2D    SceneTex     : register(t0);
Texture2D    DepthTex     : register(t1);  // latest inference
Texture2D    DepthPrevTex : register(t2);  // previous inference (for lerp)
SamplerState SceneSmp : register(s0);
SamplerState DepthSmp : register(s1);
struct PS_IN { float4 pos : SV_Position; float2 uv : TEXCOORD; };

float ApplyCurve(float rawD, float curve, float gamma) {
    if (curve < 0.5) return rawD;
    if (curve < 1.5) return sqrt(rawD);
    return pow(max(rawD, 0.0001), gamma);
}

// Sample the (prev⟷current) lerp'd depth at a screen UV, applying the
// center-crop transform on the X axis and the user-selected depth curve
// (linear/sqrt/gamma). Ray-march comparisons and ParallaxShift() use the
// curved depth so tuning via depth_curve / depth_gamma still takes effect.
float SampleDepthScreen(float2 screenUV, float dCropW) {
    float2 duv = float2(saturate((screenUV.x - depthCropX0) / dCropW),
                        saturate(screenUV.y));
    float a = DepthPrevTex.Sample(DepthSmp, duv).r;
    float b = DepthTex.Sample(DepthSmp, duv).r;
    float raw = lerp(a, b, depthBlend);
    return ApplyCurve(raw, depthCurve, depthGamma);
}

static const float kDepthCohesionLow   = 0.05;
static const float kDepthCohesionHigh  = 0.24;
static const float kDepthCohesionBlend = 0.70;
static const float kDepthConfidenceLow  = 0.10;
static const float kDepthConfidenceHigh = 0.30;

struct DepthSample {
    float depth;
    float rawDepth;
    float range;
    float confidence;
};

DepthSample SampleDepthCohesive(float2 screenUV, float dCropW, float2 sceneDdx, float2 sceneDdy) {
    float2 px = max(abs(sceneDdx) + abs(sceneDdy), float2(1.0 / 4096.0, 1.0 / 2160.0));
    float d0 = saturate(SampleDepthScreen(screenUV, dCropW));
    float dl = saturate(SampleDepthScreen(screenUV - float2(px.x, 0.0), dCropW));
    float dr = saturate(SampleDepthScreen(screenUV + float2(px.x, 0.0), dCropW));
    float du = saturate(SampleDepthScreen(screenUV - float2(0.0, px.y), dCropW));
    float dd = saturate(SampleDepthScreen(screenUV + float2(0.0, px.y), dCropW));

    float dMin = min(d0, min(min(dl, dr), min(du, dd)));
    float dMax = max(d0, max(max(dl, dr), max(du, dd)));
    float edge = smoothstep(kDepthCohesionLow, kDepthCohesionHigh, dMax - dMin);
    float localMin = min(d0, min(min(dl, dr), min(du, dd)));
    float localMax = max(d0, max(max(dl, dr), max(du, dd)));
    float trimmedMean = max(0.0f, (d0 + dl + dr + du + dd - localMin - localMax) * 0.25f);
    float localDepth = saturate(trimmedMean);
    DepthSample sample;
    sample.depth = lerp(d0, localDepth, edge * kDepthCohesionBlend);
    sample.rawDepth = d0;
    sample.range = dMax - dMin;
    sample.confidence = 1.0 - smoothstep(kDepthConfidenceLow, kDepthConfidenceHigh, sample.range);
    return sample;
}

// Parallax shift for a given depth d, in screen UV units. `headX/headY` are
// the predicted head displacement from rest (cm).  f = oz/(hz+oz), the
// fraction of head offset that a pixel at virtual depth oz=vd*d sees as UV
// shift on a pinhole-through-window model.
float2 ParallaxShift(float d, float hz, float sw, float sh, float vd, float eyeX, float eyeY) {
    float oz = vd * d;
    float focus = max(focusPlaneCm, 0.0);
    float focusF = focus / (hz + focus);
    float f  = (oz / (hz + oz)) - focusF;
    return float2( (eyeX / sw) * f * strengthX,
                  -(eyeY / sh) * f * strengthY);
}

float2 ApplyConfidenceProtectedParallax(DepthSample d_final, float hz, float sw, float sh, float vd, float eyeX, float eyeY) {
    float confidenceScale = lerp(0.35, 1.0, d_final.confidence);
    return ParallaxShift(d_final.depth, hz, sw, sh, vd, eyeX, eyeY) * confidenceScale;
}

float4 main(PS_IN i) : SV_Target {
    float dCropW = max(depthCropW, 0.01);
    float2 localUV = i.uv;
    float viewOffset = 0.0;

    // Experimental real-time display backend layouts. These mirror
    // tracker.display_backends:
    //   stereo_autostereo: 2x1 side-by-side views, offsets -0.5/+0.5
    //   lightfield_quilt: 9x5 quilt, 45 views, normalized offsets -1..+1
    if (displayBackend > 0.5 && displayBackend < 1.5) {
        float rightSlot = step(0.5, i.uv.x);
        float rightEye = (eyeOrder < 0.5) ? rightSlot : (1.0 - rightSlot);
        localUV = float2(frac(i.uv.x * 2.0), i.uv.y);
        if (stereoLayout < 0.5) {
            // Full-SBS and half-SBS both occupy two side-by-side slots in the
            // live desktop. The output resolution is owned by Windows/display
            // mode, so the shader maps each slot back to full source UVs.
            localUV = float2(frac(i.uv.x * 2.0), i.uv.y);
        }
        viewOffset = lerp(-0.5, 0.5, rightEye);
    } else if (displayBackend > 1.5 && displayBackend < 2.5) {
        float2 quiltGrid = float2(9.0, 5.0);
        float2 cell = floor(i.uv * quiltGrid);
        localUV = frac(i.uv * quiltGrid);
        float viewIndex = cell.y * 9.0 + cell.x;
        viewOffset = -1.0 + (viewIndex / 44.0) * 2.0;
    }
    float eyeX = headX + viewOffset * ipdCm;
    float eyeY = headY;

    // Capture SceneTex mip derivatives from the UNSHIFTED UV *before* the
    // ray-march does any dependent sampling.  HLSL's hardware derivative
    // (ddx/ddy) is only valid outside dynamic control flow; using SampleGrad
    // with these ddx/ddy on the final shifted UV prevents mip popping — on a
    // 5120-wide output the shifted UV's local derivative would otherwise skip
    // mip levels as the head moves, which reads as edge shimmer ≈ swim.
    //   — Ben Golus, "Distinctive Derivative Differences"
    //     https://bgolus.medium.com/distinctive-derivative-differences-cce38d36797b
    float2 sceneDdx = ddx(localUV);
    float2 sceneDdy = ddy(localUV);

    float hz = max(headZ, 20.0);
    float sw = max(screenW, 1.0);
    float sh = max(screenH, 1.0);
    float vd = max(virtualDepth, 0.0);

    // Screen-edge fade: the shift is tapered to 0 over the outer 8% so
    // shifted UVs never leave [0,1] except from round-off.  Also used to
    // scale down the ray-march range near edges (cheap occlusion-free zone).
    const float kFade = 0.08;
    float fadeX = saturate(min(localUV.x, 1.0 - localUV.x) / kFade);
    float fadeY = saturate(min(localUV.y, 1.0 - localUV.y) / kFade);
    float fade  = min(fadeX, fadeY);

    // ── Classical inverse-warp parallax ─────────────────────────────────
    // Sample depth at the output pixel, shift by the f(d) factor for that
    // depth, read the shifted source pixel. Each depth layer therefore maps
    // to its own proportional shift, which is what gives the scene visible
    // layered parallax.
    //
    // A previous revision used steep / POM ray-march to nudge silhouette
    // shifts to follow the true source geometry. In practice, on open-world
    // scenes with mostly-far depth maps (sky + distant terrain), the POM
    // ray rarely intersects the height field before t→1, so every mid/far
    // pixel ended up with the *same* maximum shift — the scene translated
    // as one rigid sheet and the user perceived "very little depth". The
    // inverse-warp formulation below preserves per-pixel shift variation,
    // which is the essential 3D cue here. The residual swim at sharp depth
    // discontinuities is handled by the temporal EMA + wide smoothstep
    // blend in the depth pipeline, not by ray-marching in the shader.
    DepthSample d_final = SampleDepthCohesive(localUV, dCropW, sceneDdx, sceneDdy);
    if (debugDepthMode > 0.5 && debugDepthMode < 1.5) {
        float v = 1.0 - d_final.depth; // near=bright, far=dark
        return float4(v, v, v, 1.0);
    }
    if (debugDepthMode >= 1.5 && debugDepthMode < 2.5) {
        // debug confidence: green is stable depth, red is edge-uncertain.
        return float4(1.0 - d_final.confidence, d_final.confidence, 0.0, 1.0);
    }
    if (debugDepthMode >= 2.5) {
        // debug edge: bright pixels have large local depth disagreement.
        float edge = saturate((d_final.range - kDepthConfidenceLow) / max(0.001, kDepthConfidenceHigh - kDepthConfidenceLow));
        return float4(edge, edge, edge, 1.0);
    }

    float2 uv_final = localUV + ApplyConfidenceProtectedParallax(d_final, hz, sw, sh, vd, eyeX, eyeY) * fade;

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
}
)hlsl";

struct NormalizeCB {
    float cropX;
    float cropY;
    float cropW;
    float cropH;
    float rotation;
    float hdrInput;
    float padding[2];
};
static_assert(sizeof(NormalizeCB) % 16 == 0, "normalize buffer must be 16-byte aligned");

static const char NORMALIZE_PS_SRC[] = R"hlsl(
Texture2D RawFrame : register(t0);
SamplerState RawSampler : register(s0);
cbuffer NormalizeCB : register(b0) {
    float cropX;
    float cropY;
    float cropW;
    float cropH;
    float rotation;
    float hdrInput;
    float2 padding;
};
struct VS_OUT { float4 pos : SV_Position; float2 uv : TEXCOORD; };
float2 UprightToRaw(float2 uv) {
    if (rotation < 1.5) return uv;
    if (rotation < 2.5) return float2(uv.y, 1.0 - uv.x);
    if (rotation < 3.5) return float2(1.0 - uv.x, 1.0 - uv.y);
    return float2(1.0 - uv.y, uv.x);
}
float4 main(VS_OUT input) : SV_Target {
    float2 upright = float2(cropX, cropY) + input.uv * float2(cropW, cropH);
    float4 color = RawFrame.SampleLevel(RawSampler, UprightToRaw(upright), 0.0);
    if (hdrInput > 1.5) {
        const float m1=0.1593017578, m2=78.84375;
        const float c1=0.8359375, c2=18.8515625, c3=18.6875;
        float3 p=pow(saturate(color.rgb),1.0/m2);
        color.rgb=pow(max(p-c1,0.0)/max(c2-c3*p,1e-5),1.0/m1)*49.26;
        color.rgb=color.rgb/(1.0+color.rgb);
        color.rgb=pow(color.rgb,1.0/2.2);
    } else if (hdrInput > 0.5) {
        // Desktop Duplication exposes HDR desktops as linear scRGB. Apply a
        // conservative Reinhard mapping before the SDR overlay back buffer so
        // highlights do not simply clamp to white.
        color.rgb = max(color.rgb, 0.0);
        color.rgb = color.rgb / (1.0 + color.rgb);
        color.rgb = pow(color.rgb, 1.0 / 2.2);
    }
    return color;
}
)hlsl";

struct CBuf {
    float headX, headY, headZ, strengthX, strengthY;
    float screenW, screenH, virtualDepth, debugDepthMode;
    float depthGamma, focusRadius, depthCurve;
    float depthCropX0, depthCropW, depthBlend, displayBackend;
    float ipdCm, stereoLayout, eyeOrder;
    float focusPlaneCm;
};
static_assert(sizeof(CBuf) % 16 == 0, "D3D constant buffers must be 16-byte aligned");

struct CaptureBinding {
    LUID adapterLuid = {};
    HMONITOR monitor = nullptr;
    wchar_t deviceName[32] = {};
    RECT desktopRect = {};
    DXGI_MODE_ROTATION rotation = DXGI_MODE_ROTATION_IDENTITY;
    DXGI_COLOR_SPACE_TYPE colorSpace = DXGI_COLOR_SPACE_RGB_FULL_G22_NONE_P709;
    UINT bitsPerColor = 8;
    float minLuminance = 0.0f;
    float maxLuminance = 0.0f;
    g3d::capture::Region region = {};
};

enum class BindingStatus {
    Ready,
    NoOutput,
    TargetSpansOutput,
    TargetUnavailable,
};

// ── Globals ───────────────────────────────────────────────────────────────
static HWND                      g_hwnd    = nullptr;
static ID3D11Device*             g_dev     = nullptr;
static ID3D11DeviceContext*      g_ctx     = nullptr;
static IDXGISwapChain*           g_swap    = nullptr;
static HANDLE                    g_frameLatencyWaitable = nullptr; // owned by swap chain
static bool                      g_flipModelSwap = false;
static ID3D11RenderTargetView*   g_rtv     = nullptr;
static ID3D11VertexShader*       g_vs      = nullptr;
static ID3D11PixelShader*        g_ps      = nullptr;
static ID3D11PixelShader*        g_normalizePs = nullptr;
static ID3D11Buffer*             g_cb      = nullptr;
static ID3D11Buffer*             g_normalizeCb = nullptr;
static ID3D11SamplerState*       g_sceneSmp = nullptr;
static ID3D11SamplerState*       g_depthSmp = nullptr;
static IDXGIOutputDuplication*   g_dup     = nullptr;
static ID3D11Texture2D*          g_rawCapTex = nullptr;
static ID3D11ShaderResourceView* g_rawSrv = nullptr;
static ID3D11Texture2D*          g_capTex  = nullptr;
static ID3D11Texture2D*          g_blackProbeTex = nullptr;
static ID3D11RenderTargetView*   g_capRtv = nullptr;
static ID3D11ShaderResourceView* g_srv     = nullptr;
static HANDLE                    g_shmH    = nullptr;
static const void*               g_shmView = nullptr;
static HANDLE                    g_seqH    = nullptr;
static const void*               g_seqView = nullptr;
static HANDLE                    g_stateH    = nullptr;
static const void*               g_stateView = nullptr;
// Monocular depth inferencer (Depth Anything V2 Small via ONNX Runtime + DirectML).
// When null / failed to init, the overlay falls back to a uniform 0.5 depth texture
// so the parallax math still runs (effectively the old flat-plane behavior).
static DepthInferencer*          g_depth       = nullptr;
static ID3D11Texture2D*          g_fallbackTex = nullptr;  // 1x1 R16F=0.5 used when g_depth is null
static ID3D11ShaderResourceView* g_fallbackSrv = nullptr;
static ID3D11Query*              g_gpuDisjoint = nullptr;
static ID3D11Query*              g_gpuStart    = nullptr;
static ID3D11Query*              g_gpuEnd      = nullptr;
static bool                      g_gpuTimingPending = false;
static double                    g_lastGpuMs = -1.0;
static double                    g_lastCaptureCpuMs = 0.0;
static double                    g_lastPresentCpuMs = 0.0;
static double                    g_lastFrameCpuMs = 0.0;
static int                       g_gpuTimingSamples = 0;

class ScopedCpuTimer {
public:
    explicit ScopedCpuTimer(double* destination)
        : destination_(destination), started_(std::chrono::steady_clock::now()) {}
    ~ScopedCpuTimer() {
        if (destination_) {
            *destination_ = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - started_).count();
        }
    }
private:
    double* destination_;
    std::chrono::steady_clock::time_point started_;
};
static HWND                      g_targetWindow = nullptr;
static std::wstring              g_targetExePath;
static DWORD                     g_targetPid = 0;
static RECT                      g_targetRect = {};
static bool                      g_useTargetWindow = false;
static CaptureBinding            g_binding = {};
static bool                      g_bindingDirty = true;
static UINT                      g_captureW = 0;
static UINT                      g_captureH = 0;
static UINT                      g_renderWidth = 0;
static UINT                      g_renderHeight = 0;
static bool                      g_swapResizePending = false;
static UINT                      g_pendingSwapWidth = 0;
static UINT                      g_pendingSwapHeight = 0;
static uint64_t                  g_nextTargetPollMs = 0;
static LUID                      g_deviceAdapterLuid = {};
static bool                      g_hasDeviceAdapterLuid = false;
using g3d::capture::CaptureSignal;
using g3d::capture::CaptureState;
using g3d::capture::RetrySchedule;
static CaptureState              g_captureState = CaptureState::Rebinding;
static RetrySchedule g_rebindRetry;
static const char*               g_captureReason = "startup";
static int                       g_acquireOk = 0;
static int                       g_acquireTimeout = 0;
static int                       g_acquireLost = 0;
static int                       g_acquireOther = 0;
static DWORD                     g_nextBlackProbeMs = 0;
static int                       g_blackProbeStreak = 0;

enum class CaptureBackend {
    DesktopDuplication,
    WindowsGraphicsCapture,
};

static CaptureBackend            g_captureBackend = CaptureBackend::DesktopDuplication;
static IInspectable*             g_wgcInspectableDevice = nullptr;
static ABI::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice* g_wgcDevice = nullptr;
static g3d::wgc::IGraphicsCaptureItem* g_wgcItem = nullptr;
static g3d::wgc::IDirect3D11CaptureFramePool* g_wgcFramePool = nullptr;
static g3d::wgc::IGraphicsCaptureSession* g_wgcSession = nullptr;
static HWND                      g_wgcWindow = nullptr;
static UINT                      g_wgcWidth = 0;
static UINT                      g_wgcHeight = 0;
static uint64_t                  g_captureFrameSerial = 0;
static uint64_t                  g_lastCaptureFrameMs = 0;
static bool                      g_presentRetryPending = false;

static void CloseWinRtObject(IUnknown* object) {
    if (!object) return;
    ABI::Windows::Foundation::IClosable* closable = nullptr;
    if (SUCCEEDED(object->QueryInterface(__uuidof(ABI::Windows::Foundation::IClosable),
                                         reinterpret_cast<void**>(&closable)))) {
        closable->Close();
        closable->Release();
    }
}

static void ReleaseWgcResources() {
    CloseWinRtObject(g_wgcSession);
    CloseWinRtObject(g_wgcFramePool);
    SafeRelease(g_wgcSession);
    SafeRelease(g_wgcFramePool);
    SafeRelease(g_wgcItem);
    SafeRelease(g_wgcDevice);
    SafeRelease(g_wgcInspectableDevice);
    g_wgcWindow = nullptr;
    g_wgcWidth = 0;
    g_wgcHeight = 0;
}

static void ReleaseCaptureTextures() {
    SafeRelease(g_blackProbeTex);
    SafeRelease(g_capRtv);
    SafeRelease(g_srv);
    SafeRelease(g_capTex);
    SafeRelease(g_rawSrv);
    SafeRelease(g_rawCapTex);
    g_captureW = 0;
    g_captureH = 0;
    g_blackProbeStreak = 0;
}
// Settings channel (G3D_Settings) — optional; owned by the settings GUI.
static HANDLE                    g_setH    = nullptr;
static const void*               g_setView = nullptr;
// Autodetected values (cached so settings GUI can override back to "auto" without losing them).
static float g_autoScreenW  = 0.0f;
static float g_autoScreenH  = 0.0f;
// Live values, rebuilt each frame from (autodetect ∪ CLI ∪ settings-shm).
// Sentinel 0 for screen dims = "auto-detect in Init()".
static float g_screenW      = 0.0f;
static float g_screenH      = 0.0f;
static float g_strength     = 1.0f;   // physical 1:1 by default
static float g_virtualDepth = 30.0f;  // cm — total depth budget around screen plane (±Dmax/2)
static float    g_strengthX   = 1.0f;
static float    g_strengthY   = 1.0f;
static uint32_t g_depthCurve  = 1;    // sqrt default
static float    g_depthGamma  = 1.0f;
static float    g_focusRadius = 0.1f;
static float    g_deadzoneCm  = 0.5f; // soft deadzone on dx/dy (default 5 mm)
static uint32_t g_displayBackend = 0;  // 0=desktop, 1=stereo, 2=quilt
static uint32_t g_depthMode = 1;       // 0=quality, 1=balanced, 2=fast
static float g_ipdCm = 6.4f;
static uint32_t g_stereoLayout = 0;    // 0=full_sbs, 1=half_sbs
static uint32_t g_eyeOrder = 0;        // 0=left_right, 1=right_left
static uint32_t g_panelWidthPx = 0;
static uint32_t g_panelHeightPx = 0;
static float g_focusPlaneCm = 0.0f;
static uint32_t g_trackingMode = 0;    // 0=glassless3d_managed, 1=vendor_managed

static const char* DepthModeName(uint32_t mode) {
    switch (mode) {
        case 0: return "quality";
        case 2: return "fast";
        default: return "balanced";
    }
}

static bool InitGpuTiming() {
    D3D11_QUERY_DESC q = {};
    q.Query = D3D11_QUERY_TIMESTAMP_DISJOINT;
    HRESULT hr = g_dev->CreateQuery(&q, &g_gpuDisjoint);
    if (FAILED(hr)) {
        LogHR("CreateQuery(TIMESTAMP_DISJOINT)", hr);
        return false;
    }
    q.Query = D3D11_QUERY_TIMESTAMP;
    hr = g_dev->CreateQuery(&q, &g_gpuStart);
    if (FAILED(hr)) {
        LogHR("CreateQuery(TIMESTAMP start)", hr);
        SafeReleaseQuery(g_gpuDisjoint);
        return false;
    }
    hr = g_dev->CreateQuery(&q, &g_gpuEnd);
    if (FAILED(hr)) {
        LogHR("CreateQuery(TIMESTAMP end)", hr);
        SafeReleaseQuery(g_gpuStart);
        SafeReleaseQuery(g_gpuDisjoint);
        return false;
    }
    Log("GPU timing queries initialized");
    return true;
}

static void ResolveGpuTiming() {
    if (!g_gpuTimingPending || !g_gpuDisjoint || !g_gpuStart || !g_gpuEnd) return;

    D3D11_QUERY_DATA_TIMESTAMP_DISJOINT disjoint = {};
    HRESULT hr = g_ctx->GetData(g_gpuDisjoint, &disjoint, sizeof(disjoint), 0);
    if (hr != S_OK) return;

    UINT64 start = 0, end = 0;
    if (g_ctx->GetData(g_gpuStart, &start, sizeof(start), 0) == S_OK &&
        g_ctx->GetData(g_gpuEnd, &end, sizeof(end), 0) == S_OK) {
        if (!disjoint.Disjoint && disjoint.Frequency > 0 && end >= start) {
            g_lastGpuMs = (double)(end - start) * 1000.0 / (double)disjoint.Frequency;
            g_gpuTimingSamples++;
        }
        g_gpuTimingPending = false;
    }
}

static void BeginGpuTiming() {
    if (g_gpuTimingPending || !g_gpuDisjoint || !g_gpuStart || !g_gpuEnd) return;
    g_ctx->Begin(g_gpuDisjoint);
    g_ctx->End(g_gpuStart);
}

static void EndGpuTiming() {
    if (g_gpuTimingPending || !g_gpuDisjoint || !g_gpuStart || !g_gpuEnd) return;
    g_ctx->End(g_gpuEnd);
    g_ctx->End(g_gpuDisjoint);
    g_gpuTimingPending = true;
}

// ── Head-pose smoothing (One-Euro filter) ────────────────────────────────
// Raw head pose from the webcam tracker has sub-centimeter jitter even
// after its Kalman stage. The parallax formula multiplies head displacement
// by depth-dependent shift magnitude, so a ~1 mm jitter on a near-field
// pixel can translate to multi-pixel shift noise → visible "watery" motion.
//
// One-Euro (Casiez/Roussel/Vogel 2012, https://gery.casiez.net/1euro/) is
// the canonical choice here: two chained EMAs where the signal cutoff
// adapts with estimated velocity. Sub-millimeter still-held jitter is
// filtered hard (low cutoff), but fast head motion sees a high cutoff so
// there is no added latency. Three independent instances — one per axis.
//
// Defaults retuned for the MediaPipe tracker that feeds G3D SHM at ~10 Hz
// (was 1.0/0.007/1.0 — too conservative for a 10 Hz source, whose 5 Hz
// Nyquist means `min_cutoff=1 Hz` already attenuates legitimate head motion).
//   min_cutoff = 0.4 Hz   — floor cutoff when the head is still (kills mm jitter)
//   beta       = 0.02     — opens cutoff aggressively on motion (no added lag)
//   d_cutoff   = 1.0 Hz   — low-pass for the derivative estimator itself
// Casiez tuning procedure: hold still → drop min_cutoff until jitter is gone;
// then move fast → raise β until lag disappears. https://gery.casiez.net/1euro/
struct OneEuro {
    float min_cutoff = 0.4f;
    float beta       = 0.02f;
    float d_cutoff   = 1.0f;
    float x_prev     = 0.0f;
    float dx_prev    = 0.0f;
    double t_prev    = 0.0;
    bool   initialized = false;
};

static inline float OneEuroAlpha(float cutoff_hz, float te_s) {
    const float kTwoPi = 6.28318530717958647692f;
    float r = kTwoPi * cutoff_hz * te_s;
    return r / (r + 1.0f);
}

static float OneEuroFilter(OneEuro& f, float x, double t_seconds) {
    if (!f.initialized) {
        f.x_prev = x; f.dx_prev = 0.0f;
        f.t_prev = t_seconds; f.initialized = true;
        return x;
    }
    float te = (float)(t_seconds - f.t_prev);
    if (te <= 0.0f) te = 1e-3f;            // guard: duplicate/clock-skew stamps
    float dx     = (x - f.x_prev) / te;
    float ad     = OneEuroAlpha(f.d_cutoff, te);
    float dx_hat = ad * dx + (1.0f - ad) * f.dx_prev;
    float mag    = dx_hat < 0.0f ? -dx_hat : dx_hat;
    float fc     = f.min_cutoff + f.beta * mag;
    float a      = OneEuroAlpha(fc, te);
    float x_hat  = a * x + (1.0f - a) * f.x_prev;
    f.x_prev = x_hat; f.dx_prev = dx_hat; f.t_prev = t_seconds;
    return x_hat;
}

// ── Soft hysteretic deadzone ─────────────────────────────────────────────
// A hard deadzone (`return 0 if |x|<dz else x`) produces a visible snap
// when crossing the threshold — the parallax would suddenly jump by `dz`
// worth of shift. A soft deadzone applies a smoothstep weight between dz
// and 2·dz so the output gradually re-engages. At |x|≤dz output is 0;
// at |x|≥2·dz output is x; between, output is x·smoothstep(dz, 2dz, |x|).
static float SoftDeadzone(float x, float dz) {
    if (dz <= 0.0f) return x;
    float a = x < 0.0f ? -x : x;
    if (a <= dz) return 0.0f;
    float dz_hi = 2.0f * dz;
    if (a >= dz_hi) return x;
    float t = (a - dz) / dz;          // 0 at dz, 1 at 2·dz
    float s = t * t * (3.0f - 2.0f * t);
    return x * s;
}

// Three independent One-Euro instances, one per axis (cm).
static OneEuro g_oeX, g_oeY, g_oeZ;

// ── Head-pose forward prediction ─────────────────────────────────────────
// Motion-to-photon latency in this pipeline is the sum of webcam exposure
// (~16 ms at 60 Hz), MediaPipe inference + Kalman (~10-15 ms), SHM write,
// our overlay frame that reads it (0-16 ms until next render), One-Euro
// smoothing (~few ms at min_cutoff=1 Hz), render, Present+VSync (0-16 ms).
// Total roughly 35-50 ms. Extrapolating the filtered pose forward by that
// horizon puts the parallax shift where the eye actually is when the
// pixel lights up — without it the scene lazily "sloshes" after the head,
// which reads as motion wateriness.  Capped to prevent over-extrapolation
// during rapid motion direction changes.
static constexpr float kPredictHorizonSec = 0.035f;      // 35 ms default
static constexpr float kPredictMaxDeltaCm = 2.0f;        // clamp per-axis extrapolation
static constexpr DWORD kPoseStaleMs = 500;

static inline float OneEuroPredictedVelocity(const OneEuro& f) {
    // dx_prev is the filtered derivative estimate after the last update.
    // Units: cm/s. Valid after at least two samples.
    return f.initialized ? f.dx_prev : 0.0f;
}

static inline float ClampAbs(float v, float max_abs) {
    if (v >  max_abs) return  max_abs;
    if (v < -max_abs) return -max_abs;
    return v;
}

static void ClampVectorLength(float& x, float& y, float max_len) {
    if (max_len <= 0.0f) return;
    float len = sqrtf(x * x + y * y);
    if (len <= max_len) return;
    float scale = max_len / len;
    x *= scale;
    y *= scale;
}

static constexpr float kMaxParallaxRelCm = 6.0f;
static std::wstring NormalizeExePath(std::wstring path) {
    for (wchar_t& ch : path) {
        if (ch == L'/') ch = L'\\';
        ch = static_cast<wchar_t>(towlower(ch));
    }
    return path;
}

struct TargetWindowSearch {
    std::wstring normalizedPath;
    DWORD targetPid = 0;
    HWND best = nullptr;
    uint64_t bestScore = 0;
};

static BOOL CALLBACK FindTargetWindowProc(HWND hwnd, LPARAM lparam) {
    auto* search = reinterpret_cast<TargetWindowSearch*>(lparam);
    if (!IsWindowVisible(hwnd) || IsIconic(hwnd)) return TRUE;
    const LONG_PTR exStyle = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
    if ((exStyle & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)) != 0) return TRUE;

    DWORD pid = 0;
    GetWindowThreadProcessId(hwnd, &pid);
    if (search->targetPid != 0 && pid != search->targetPid) return TRUE;
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return TRUE;
    wchar_t imagePath[32768] = {};
    DWORD imagePathLen = static_cast<DWORD>(_countof(imagePath));
    const BOOL queried = QueryFullProcessImageNameW(process, 0, imagePath, &imagePathLen);
    CloseHandle(process);
    if (!queried || NormalizeExePath(std::wstring(imagePath, imagePathLen)) != search->normalizedPath) {
        return TRUE;
    }

    RECT client = {};
    if (!GetClientRect(hwnd, &client)) return TRUE;
    const LONG width = client.right - client.left;
    const LONG height = client.bottom - client.top;
    if (width < 320 || height < 240) return TRUE;
    uint64_t score = static_cast<uint64_t>(width) * static_cast<uint64_t>(height);
    if (GetAncestor(hwnd, GA_ROOTOWNER) == hwnd) score += (1ull << 61);
    if (GetForegroundWindow() == hwnd) score += (1ull << 62);
    if (!search->best || score > search->bestScore) {
        search->best = hwnd;
        search->bestScore = score;
    }
    return TRUE;
}

static HWND FindTargetWindow() {
    if (g_targetExePath.empty()) return nullptr;
    TargetWindowSearch search = {};
    search.normalizedPath = NormalizeExePath(g_targetExePath);
    search.targetPid = g_targetPid;
    EnumWindows(FindTargetWindowProc, reinterpret_cast<LPARAM>(&search));
    return search.best;
}

static bool DetectTargetWindowRect() {
    HWND target = FindTargetWindow();
    RECT nextRect = {};
    RECT client = {};
    bool valid = target && IsWindowVisible(target) && GetClientRect(target, &client);
    if (valid) {
        POINT topLeft = { client.left, client.top };
        POINT bottomRight = { client.right, client.bottom };
        valid = ClientToScreen(target, &topLeft) && ClientToScreen(target, &bottomRight);
        if (valid) {
            nextRect = { topLeft.x, topLeft.y, bottomRight.x, bottomRight.y };
            valid = nextRect.right - nextRect.left >= 320
                && nextRect.bottom - nextRect.top >= 240;
        }
    }
    if (!valid) {
        const bool changed = g_useTargetWindow || g_targetWindow != nullptr;
        g_targetWindow = nullptr;
        g_targetRect = {};
        g_useTargetWindow = false;
        if (changed) g_bindingDirty = true;
        return false;
    }

    const bool changed = target != g_targetWindow || !EqualRect(&nextRect, &g_targetRect);
    g_targetWindow = target;
    g_targetRect = nextRect;
    g_useTargetWindow = true;
    if (changed) g_bindingDirty = true;
    return true;
}

static g3d::capture::Rect ToCaptureRect(const RECT& rect) {
    return { rect.left, rect.top, rect.right, rect.bottom };
}

static bool SameLuid(const LUID& left, const LUID& right) {
    return left.HighPart == right.HighPart && left.LowPart == right.LowPart;
}

static bool AutodetectScreenSizeCm(float* outW, float* outH, const wchar_t* deviceName = nullptr);

static HMONITOR DesiredCaptureMonitor() {
    if (g_useTargetWindow && g_targetWindow) {
        return MonitorFromWindow(g_targetWindow, MONITOR_DEFAULTTONULL);
    }
    return MonitorFromWindow(g_hwnd, MONITOR_DEFAULTTOPRIMARY);
}

static BindingStatus FindCaptureBinding(HMONITOR monitor, CaptureBinding* binding) {
    if (!monitor || !binding) return BindingStatus::NoOutput;

    IDXGIFactory1* factory = nullptr;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory));
    if (FAILED(hr)) {
        LogHR("CreateDXGIFactory1", hr);
        return BindingStatus::NoOutput;
    }

    for (UINT adapterIndex = 0;; ++adapterIndex) {
        IDXGIAdapter1* adapter = nullptr;
        const HRESULT adapterHr = factory->EnumAdapters1(adapterIndex, &adapter);
        if (adapterHr == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(adapterHr) || !adapter) continue;

        DXGI_ADAPTER_DESC1 adapterDesc = {};
        const HRESULT descHr = adapter->GetDesc1(&adapterDesc);
        if (SUCCEEDED(descHr) && !(adapterDesc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) {
            for (UINT outputIndex = 0;; ++outputIndex) {
                IDXGIOutput* output = nullptr;
                const HRESULT outputHr = adapter->EnumOutputs(outputIndex, &output);
                if (outputHr == DXGI_ERROR_NOT_FOUND) break;
                if (FAILED(outputHr) || !output) continue;

                DXGI_OUTPUT_DESC desc = {};
                const HRESULT outputDescHr = output->GetDesc(&desc);
                if (SUCCEEDED(outputDescHr) && desc.AttachedToDesktop && desc.Monitor == monitor) {
                    binding->adapterLuid = adapterDesc.AdapterLuid;
                    binding->monitor = desc.Monitor;
                    wcsncpy_s(binding->deviceName, _countof(binding->deviceName),
                        desc.DeviceName, _TRUNCATE);
                    binding->desktopRect = desc.DesktopCoordinates;
                    binding->rotation = desc.Rotation;
                    IDXGIOutput6* output6 = nullptr;
                    if (SUCCEEDED(output->QueryInterface(__uuidof(IDXGIOutput6),
                            reinterpret_cast<void**>(&output6)))) {
                        DXGI_OUTPUT_DESC1 desc1 = {};
                        if (SUCCEEDED(output6->GetDesc1(&desc1))) {
                            binding->colorSpace = desc1.ColorSpace;
                            binding->bitsPerColor = desc1.BitsPerColor;
                            binding->minLuminance = desc1.MinLuminance;
                            binding->maxLuminance = desc1.MaxLuminance;
                        }
                        output6->Release();
                    }
                    output->Release();
                    adapter->Release();
                    factory->Release();
                    return BindingStatus::Ready;
                }
                output->Release();
            }
        }
        adapter->Release();
    }
    factory->Release();
    return BindingStatus::NoOutput;
}

static BindingStatus RefreshCaptureBinding() {
    CaptureBinding next = {};
    const BindingStatus status = FindCaptureBinding(DesiredCaptureMonitor(), &next);
    if (status != BindingStatus::Ready) return status;

    const std::optional<g3d::capture::Rect> target = g_useTargetWindow
        ? std::optional<g3d::capture::Rect>(ToCaptureRect(g_targetRect))
        : std::nullopt;
    const auto region = g3d::capture::BuildUprightCaptureRegion(
        ToCaptureRect(next.desktopRect), target);
    if (!region) {
        g_binding = next;
        g_bindingDirty = false;
        return BindingStatus::TargetSpansOutput;
    }

    next.region = *region;
    g_binding = next;
    g_bindingDirty = false;
    float monitorW = 0.0f, monitorH = 0.0f;
    if (AutodetectScreenSizeCm(&monitorW, &monitorH, g_binding.deviceName)) {
        g_autoScreenW = monitorW;
        g_autoScreenH = monitorH;
    }
    Log("Capture binding: output=%ls rect=(%ld,%ld)-(%ld,%ld) region=%u,%u %ux%u rotation=%u color_space=%u bpc=%u luminance=%.3f..%.1f",
        g_binding.deviceName,
        g_binding.desktopRect.left, g_binding.desktopRect.top,
        g_binding.desktopRect.right, g_binding.desktopRect.bottom,
        g_binding.region.left, g_binding.region.top,
        g_binding.region.width, g_binding.region.height,
        static_cast<unsigned>(g_binding.rotation), static_cast<unsigned>(g_binding.colorSpace),
        g_binding.bitsPerColor, g_binding.minLuminance, g_binding.maxLuminance);
    if (!g_targetExePath.empty() && (!g_useTargetWindow || !g_targetWindow)) {
        return BindingStatus::TargetUnavailable;
    }
    return BindingStatus::Ready;
}

static IDXGIAdapter1* OpenAdapterForLuid(const LUID& luid) {
    IDXGIFactory1* factory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory)))) {
        return nullptr;
    }
    for (UINT index = 0;; ++index) {
        IDXGIAdapter1* adapter = nullptr;
        const HRESULT hr = factory->EnumAdapters1(index, &adapter);
        if (hr == DXGI_ERROR_NOT_FOUND) break;
        if (FAILED(hr) || !adapter) continue;
        DXGI_ADAPTER_DESC1 desc = {};
        if (SUCCEEDED(adapter->GetDesc1(&desc)) && SameLuid(desc.AdapterLuid, luid)) {
            factory->Release();
            return adapter;
        }
        adapter->Release();
    }
    factory->Release();
    return nullptr;
}

static void SyncOverlayWindowToBinding() {
    if (!g_hwnd) return;
    const RECT rect = g_useTargetWindow ? g_targetRect : g_binding.desktopRect;
    const LONG width = rect.right - rect.left;
    const LONG height = rect.bottom - rect.top;
    if (width <= 0 || height <= 0) return;
    SetWindowPos(g_hwnd, HWND_TOPMOST, rect.left, rect.top, width, height,
        SWP_NOACTIVATE | SWP_NOOWNERZORDER);
}

static void PollTargetWindow() {
    const uint64_t nowMs = GetTickCount64();
    if (nowMs < g_nextTargetPollMs) return;
    g_nextTargetPollMs = nowMs + 250;

    if (g_captureBackend == CaptureBackend::WindowsGraphicsCapture && IsWindow(g_wgcWindow)) {
        RECT client = {};
        POINT topLeft = {};
        POINT bottomRight = {};
        if (GetClientRect(g_wgcWindow, &client)) {
            topLeft = { client.left, client.top };
            bottomRight = { client.right, client.bottom };
            if (ClientToScreen(g_wgcWindow, &topLeft) && ClientToScreen(g_wgcWindow, &bottomRight)) {
                RECT nextRect = { topLeft.x, topLeft.y, bottomRight.x, bottomRight.y };
                const LONG oldWidth = g_targetRect.right - g_targetRect.left;
                const LONG oldHeight = g_targetRect.bottom - g_targetRect.top;
                const LONG newWidth = nextRect.right - nextRect.left;
                const LONG newHeight = nextRect.bottom - nextRect.top;
                const bool sizeChanged = oldWidth != newWidth || oldHeight != newHeight;
                const bool monitorChanged = MonitorFromWindow(g_wgcWindow, MONITOR_DEFAULTTONULL)
                    != g_binding.monitor;
                const bool positionChanged = nextRect.left != g_targetRect.left
                    || nextRect.top != g_targetRect.top;
                g_targetWindow = g_wgcWindow;
                g_targetRect = nextRect;
                g_useTargetWindow = true;
                if (sizeChanged || monitorChanged) {
                    g_bindingDirty = true;
                } else if (positionChanged) {
                    // WGC captures the HWND independently of desktop position;
                    // moving it only requires repositioning our presentation.
                    SyncOverlayWindowToBinding();
                }
                return;
            }
        }
        g_bindingDirty = true;
        return;
    }

    const HWND oldWindow = g_targetWindow;
    const RECT oldRect = g_targetRect;
    const bool oldEnabled = g_useTargetWindow;
    DetectTargetWindowRect();
    if (oldWindow != g_targetWindow || oldEnabled != g_useTargetWindow
        || !EqualRect(&oldRect, &g_targetRect)) {
        g_bindingDirty = true;
    }
}

static int g_debugDepthMode = 0;  // Ctrl+D: off/depth/confidence/edge
static constexpr int kDebugDepthModeCount = 4;
// CLI-provided overrides (0 = not provided → use autodetect / settings GUI)
static float g_cliScreenW   = 0.0f;
static float g_cliScreenH   = 0.0f;
static float g_cliStrength  = 0.0f;
static float g_cliDepth     = -1.0f;  // -1 = not provided
static bool  g_running        = true;
static bool  g_hasFrame       = false;  // true once we have at least one captured frame
static bool  g_overlayRevealed = false; // alpha stays zero until first successful present
static bool  g_overlayVisible = false;
static bool  g_wantScreenshot = false;  // Ctrl+Shift+S: save next rendered frame

static const int  HOTKEY_QUIT       = 1;
static const int  HOTKEY_DEBUG      = 2;
static const int  HOTKEY_SCREENSHOT = 3;
static const UINT WM_TRAYICON   = WM_USER + 1;
static const UINT TRAY_MENU_QUIT = 1001;
static NOTIFYICONDATAW g_nid     = {};

// ── Error helper ──────────────────────────────────────────────────────────
static void FatalError(const wchar_t* msg, HRESULT hr = S_OK) {
    wchar_t buf[512];
    if (FAILED(hr))
        _snwprintf_s(buf, _countof(buf), _TRUNCATE, L"%s\n\nHRESULT: 0x%08X", msg, (unsigned)hr);
    else
        wcsncpy_s(buf, msg, _TRUNCATE);
    MessageBoxW(nullptr, buf, L"Glassless3D Overlay Error", MB_OK | MB_ICONERROR);
}

// ── Window procedure ──────────────────────────────────────────────────────
static LRESULT CALLBACK WndProc(HWND hw, UINT msg, WPARAM wp, LPARAM lp) {
    if (msg == WM_MOUSEACTIVATE) return MA_NOACTIVATE;
    if (msg == WM_DESTROY) { Log("WndProc: WM_DESTROY"); g_running = false; PostQuitMessage(0); }
    if (msg == WM_HOTKEY && wp == HOTKEY_QUIT)  { Log("WndProc: WM_HOTKEY quit"); g_running = false; PostQuitMessage(0); }
    if (msg == WM_HOTKEY && wp == HOTKEY_DEBUG)      { g_debugDepthMode = (g_debugDepthMode + 1) % kDebugDepthModeCount; Log("WndProc: debug depth mode %d", g_debugDepthMode); }
    if (msg == WM_HOTKEY && wp == HOTKEY_SCREENSHOT) { g_wantScreenshot = true; Log("WndProc: screenshot queued"); }
    if (msg == WM_DPICHANGED) {
        const RECT* suggested = reinterpret_cast<const RECT*>(lp);
        if (suggested && !g_useTargetWindow) {
            SetWindowPos(hw, nullptr, suggested->left, suggested->top,
                suggested->right - suggested->left, suggested->bottom - suggested->top,
                SWP_NOACTIVATE | SWP_NOZORDER);
        }
        g_bindingDirty = true;
        return 0;
    }
    if (msg == WM_DISPLAYCHANGE) {
        g_bindingDirty = true;
        return 0;
    }
    if (msg == WM_SIZE) {
        if (wp != SIZE_MINIMIZED) {
            g_pendingSwapWidth = LOWORD(lp);
            g_pendingSwapHeight = HIWORD(lp);
            g_swapResizePending = g_pendingSwapWidth > 0 && g_pendingSwapHeight > 0;
        }
        return 0;
    }
    if (msg == WM_TRAYICON) {
        if (lp == WM_RBUTTONUP || lp == WM_LBUTTONUP) {
            POINT pt; GetCursorPos(&pt);
            HMENU menu = CreatePopupMenu();
            AppendMenuW(menu, MF_STRING, TRAY_MENU_QUIT, L"Quit Overlay");
            SetForegroundWindow(hw);
            TrackPopupMenu(menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, hw, nullptr);
            DestroyMenu(menu);
        }
    }
    if (msg == WM_COMMAND && LOWORD(wp) == TRAY_MENU_QUIT) {
        Log("WndProc: tray Quit");
        g_running = false; PostQuitMessage(0);
    }
    return DefWindowProcW(hw, msg, wp, lp);
}

static ID3DBlob* CompileShader(const char* src, const char* entry, const char* profile) {
    ID3DBlob* code = nullptr, *err = nullptr;
    HRESULT hr = D3DCompile(src, strlen(src), nullptr, nullptr, nullptr,
                            entry, profile, 0, 0, &code, &err);
    if (FAILED(hr)) {
        if (err) {
            // Convert error to wide string for MessageBox
            const char* msg = (const char*)err->GetBufferPointer();
            int len = MultiByteToWideChar(CP_ACP, 0, msg, -1, nullptr, 0);
            std::wstring wmsg(len, L'\0');
            MultiByteToWideChar(CP_ACP, 0, msg, -1, wmsg.data(), len);
            FatalError(wmsg.c_str());
            err->Release();
        }
        return nullptr;
    }
    if (err) err->Release();
    return code;
}

static void SetRenderViewport() {
    if (!g_ctx || g_renderWidth == 0 || g_renderHeight == 0) return;
    const D3D11_VIEWPORT viewport = {
        0.0f, 0.0f,
        static_cast<float>(g_renderWidth), static_cast<float>(g_renderHeight),
        0.0f, 1.0f,
    };
    g_ctx->RSSetViewports(1, &viewport);
}

static float NormalizeRotationValue(DXGI_MODE_ROTATION rotation) {
    switch (rotation) {
    case DXGI_MODE_ROTATION_ROTATE90: return 2.0f;
    case DXGI_MODE_ROTATION_ROTATE180: return 3.0f;
    case DXGI_MODE_ROTATION_ROTATE270: return 4.0f;
    case DXGI_MODE_ROTATION_UNSPECIFIED: return 0.0f;
    case DXGI_MODE_ROTATION_IDENTITY: return 1.0f;
    }
    return 1.0f;
}

static bool CreateCaptureTextures(const DXGI_OUTDUPL_DESC& duplicationDesc) {
    ReleaseCaptureTextures();
    if (g_binding.region.width == 0 || g_binding.region.height == 0
        || duplicationDesc.ModeDesc.Width == 0 || duplicationDesc.ModeDesc.Height == 0) {
        Log("CreateCaptureTextures: empty capture dimensions");
        return false;
    }

    D3D11_TEXTURE2D_DESC raw = {};
    raw.Width = duplicationDesc.ModeDesc.Width;
    raw.Height = duplicationDesc.ModeDesc.Height;
    raw.MipLevels = 1;
    raw.ArraySize = 1;
    raw.Format = duplicationDesc.ModeDesc.Format;
    raw.SampleDesc.Count = 1;
    raw.Usage = D3D11_USAGE_DEFAULT;
    raw.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    HRESULT hr = g_dev->CreateTexture2D(&raw, nullptr, &g_rawCapTex);
    if (FAILED(hr)) { LogHR("CreateTexture2D(raw capture)", hr); return false; }
    hr = g_dev->CreateShaderResourceView(g_rawCapTex, nullptr, &g_rawSrv);
    if (FAILED(hr)) {
        LogHR("CreateShaderResourceView(raw capture)", hr);
        ReleaseCaptureTextures();
        return false;
    }

    D3D11_TEXTURE2D_DESC logical = raw;
    logical.Width = g_binding.region.width;
    logical.Height = g_binding.region.height;
    logical.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    logical.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
    hr = g_dev->CreateTexture2D(&logical, nullptr, &g_capTex);
    if (FAILED(hr)) {
        LogHR("CreateTexture2D(logical capture)", hr);
        ReleaseCaptureTextures();
        return false;
    }
    hr = g_dev->CreateShaderResourceView(g_capTex, nullptr, &g_srv);
    if (FAILED(hr)) {
        LogHR("CreateShaderResourceView(logical capture)", hr);
        ReleaseCaptureTextures();
        return false;
    }
    hr = g_dev->CreateRenderTargetView(g_capTex, nullptr, &g_capRtv);
    if (FAILED(hr)) {
        LogHR("CreateRenderTargetView(logical capture)", hr);
        ReleaseCaptureTextures();
        return false;
    }

    g_captureW = logical.Width;
    g_captureH = logical.Height;
    return true;
}

static bool CreateWgcCaptureTextures(UINT width, UINT height) {
    ReleaseCaptureTextures();
    if (width == 0 || height == 0) {
        Log("CreateWgcCaptureTextures: empty capture dimensions");
        return false;
    }

    D3D11_TEXTURE2D_DESC desc = {};
    desc.Width = width;
    desc.Height = height;
    desc.MipLevels = 1;
    desc.ArraySize = 1;
    desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    desc.SampleDesc.Count = 1;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_SHADER_RESOURCE | D3D11_BIND_RENDER_TARGET;
    HRESULT hr = g_dev->CreateTexture2D(&desc, nullptr, &g_capTex);
    if (FAILED(hr)) {
        LogHR("CreateTexture2D(WGC capture)", hr);
        ReleaseCaptureTextures();
        return false;
    }
    hr = g_dev->CreateShaderResourceView(g_capTex, nullptr, &g_srv);
    if (FAILED(hr)) {
        LogHR("CreateShaderResourceView(WGC capture)", hr);
        ReleaseCaptureTextures();
        return false;
    }
    hr = g_dev->CreateRenderTargetView(g_capTex, nullptr, &g_capRtv);
    if (FAILED(hr)) {
        LogHR("CreateRenderTargetView(WGC capture)", hr);
        ReleaseCaptureTextures();
        return false;
    }

    g_captureW = width;
    g_captureH = height;
    return true;
}

static bool NormalizeCapturedFrame(ID3D11Texture2D* source) {
    if (!source || !g_rawCapTex || !g_rawSrv || !g_capRtv || !g_normalizePs || !g_normalizeCb) {
        return false;
    }
    const LONG outputWidth = g_binding.desktopRect.right - g_binding.desktopRect.left;
    const LONG outputHeight = g_binding.desktopRect.bottom - g_binding.desktopRect.top;
    if (outputWidth <= 0 || outputHeight <= 0) return false;

    g_ctx->CopyResource(g_rawCapTex, source);
    D3D11_TEXTURE2D_DESC sourceDesc = {};
    source->GetDesc(&sourceDesc);
    const bool pqOutput = g_binding.colorSpace == DXGI_COLOR_SPACE_RGB_FULL_G2084_NONE_P2020
        || g_binding.colorSpace == DXGI_COLOR_SPACE_RGB_STUDIO_G2084_NONE_P2020;
    const bool linearHdr = sourceDesc.Format == DXGI_FORMAT_R16G16B16A16_FLOAT;
    const bool packedHdr = sourceDesc.Format == DXGI_FORMAT_R10G10B10A2_UNORM && pqOutput;
    const float hdrMode = packedHdr ? 2.0f : (linearHdr ? 1.0f : 0.0f);
    const NormalizeCB constants = {
        static_cast<float>(g_binding.region.left) / outputWidth,
        static_cast<float>(g_binding.region.top) / outputHeight,
        static_cast<float>(g_binding.region.width) / outputWidth,
        static_cast<float>(g_binding.region.height) / outputHeight,
        NormalizeRotationValue(g_binding.rotation),
        hdrMode,
        {0.0f, 0.0f},
    };
    g_ctx->UpdateSubresource(g_normalizeCb, 0, nullptr, &constants, 0, 0);
    const D3D11_VIEWPORT captureViewport = {
        0.0f, 0.0f,
        static_cast<float>(g_captureW), static_cast<float>(g_captureH),
        0.0f, 1.0f,
    };
    g_ctx->RSSetViewports(1, &captureViewport);
    g_ctx->OMSetRenderTargets(1, &g_capRtv, nullptr);
    g_ctx->VSSetShader(g_vs, nullptr, 0);
    g_ctx->PSSetShader(g_normalizePs, nullptr, 0);
    g_ctx->PSSetConstantBuffers(0, 1, &g_normalizeCb);
    g_ctx->PSSetShaderResources(0, 1, &g_rawSrv);
    g_ctx->PSSetSamplers(0, 1, &g_sceneSmp);
    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    g_ctx->Draw(4, 0);
    ID3D11ShaderResourceView* nullSrv = nullptr;
    g_ctx->PSSetShaderResources(0, 1, &nullSrv);
    g_ctx->OMSetRenderTargets(0, nullptr, nullptr);
    SetRenderViewport();
    return true;
}

static HRESULT InitDuplication() {
    Log("InitDuplication: begin");
    if (!g_dev || !g_binding.monitor) return E_POINTER;

    IDXGIDevice* dxgiDevice = nullptr;
    IDXGIAdapter* adapter = nullptr;
    IDXGIOutput* selectedOutput = nullptr;
    IDXGIOutput1* output1 = nullptr;
    HRESULT hr = S_OK;
    do {
        hr = g_dev->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void**>(&dxgiDevice));
        if (FAILED(hr)) break;
        hr = dxgiDevice->GetParent(__uuidof(IDXGIAdapter), reinterpret_cast<void**>(&adapter));
        if (FAILED(hr)) break;

        for (UINT index = 0;; ++index) {
            IDXGIOutput* candidate = nullptr;
            const HRESULT enumHr = adapter->EnumOutputs(index, &candidate);
            if (enumHr == DXGI_ERROR_NOT_FOUND) break;
            if (FAILED(enumHr) || !candidate) { hr = enumHr; break; }
            DXGI_OUTPUT_DESC desc = {};
            const HRESULT descHr = candidate->GetDesc(&desc);
            if (SUCCEEDED(descHr) && desc.AttachedToDesktop && desc.Monitor == g_binding.monitor) {
                selectedOutput = candidate;
                break;
            }
            candidate->Release();
        }
        if (!selectedOutput) {
            if (SUCCEEDED(hr)) hr = DXGI_ERROR_NOT_FOUND;
            break;
        }

        hr = selectedOutput->QueryInterface(__uuidof(IDXGIOutput1), reinterpret_cast<void**>(&output1));
        if (FAILED(hr)) break;
        hr = output1->DuplicateOutput(g_dev, &g_dup);
        if (FAILED(hr)) break;

        DXGI_OUTDUPL_DESC duplicationDesc = {};
        g_dup->GetDesc(&duplicationDesc);
        g_binding.rotation = duplicationDesc.Rotation;
        Log("Duplication: mode=%ux%u format=%u rotation=%u desktopImageInSystemMemory=%d",
            duplicationDesc.ModeDesc.Width, duplicationDesc.ModeDesc.Height,
            static_cast<unsigned>(duplicationDesc.ModeDesc.Format),
            static_cast<unsigned>(duplicationDesc.Rotation),
            duplicationDesc.DesktopImageInSystemMemory ? 1 : 0);
        if (!CreateCaptureTextures(duplicationDesc)) {
            hr = E_FAIL;
            break;
        }
    } while (false);

    if (FAILED(hr)) {
        LogHR("InitDuplication", hr);
        SafeRelease(g_dup);
        ReleaseCaptureTextures();
    }
    SafeRelease(output1);
    SafeRelease(selectedOutput);
    SafeRelease(adapter);
    SafeRelease(dxgiDevice);
    if (SUCCEEDED(hr)) Log("InitDuplication: complete");
    return hr;
}

static HRESULT CreateWgcItemForWindow(HWND target, g3d::wgc::IGraphicsCaptureItem** item) {
    if (!target || !item) return E_POINTER;
    *item = nullptr;

    HSTRING className = nullptr;
    HRESULT hr = WindowsCreateString(
        L"Windows.Graphics.Capture.GraphicsCaptureItem",
        static_cast<UINT32>(wcslen(L"Windows.Graphics.Capture.GraphicsCaptureItem")),
        &className);
    if (FAILED(hr)) return hr;

    g3d::wgc::IGraphicsCaptureItemInterop* interop = nullptr;
    hr = RoGetActivationFactory(className, __uuidof(g3d::wgc::IGraphicsCaptureItemInterop),
        reinterpret_cast<void**>(&interop));
    WindowsDeleteString(className);
    if (FAILED(hr)) return hr;

    hr = interop->CreateForWindow(
        target,
        __uuidof(g3d::wgc::IGraphicsCaptureItem),
        reinterpret_cast<void**>(item));
    interop->Release();
    return hr;
}

static HRESULT CreateWgcFramePoolFactory(
    g3d::wgc::IDirect3D11CaptureFramePoolStatics2** factory) {
    if (!factory) return E_POINTER;
    *factory = nullptr;

    HSTRING className = nullptr;
    HRESULT hr = WindowsCreateString(
        L"Windows.Graphics.Capture.Direct3D11CaptureFramePool",
        static_cast<UINT32>(wcslen(L"Windows.Graphics.Capture.Direct3D11CaptureFramePool")),
        &className);
    if (FAILED(hr)) return hr;
    hr = RoGetActivationFactory(
        className,
        __uuidof(g3d::wgc::IDirect3D11CaptureFramePoolStatics2),
        reinterpret_cast<void**>(factory));
    WindowsDeleteString(className);
    return hr;
}

static HRESULT InitWgcCapture() {
    Log("InitWgcCapture: begin");
    if (!g_dev || !g_useTargetWindow || !g_targetWindow) return E_POINTER;

    ReleaseWgcResources();
    SafeRelease(g_dup);
    g_captureBackend = CaptureBackend::WindowsGraphicsCapture;

    const HRESULT roHr = RoInitialize(RO_INIT_MULTITHREADED);
    if (FAILED(roHr) && roHr != RPC_E_CHANGED_MODE) {
        LogHR("RoInitialize", roHr);
        return roHr;
    }

    IDXGIDevice* dxgiDevice = nullptr;
    HRESULT hr = g_dev->QueryInterface(__uuidof(IDXGIDevice), reinterpret_cast<void**>(&dxgiDevice));
    if (FAILED(hr)) return hr;

    HMODULE d3d11 = GetModuleHandleW(L"d3d11.dll");
    if (!d3d11) d3d11 = LoadLibraryW(L"d3d11.dll");
    auto createDevice = d3d11
        ? reinterpret_cast<g3d::wgc::CreateDirect3D11DeviceFromDXGIDeviceFn>(
            GetProcAddress(d3d11, "CreateDirect3D11DeviceFromDXGIDevice"))
        : nullptr;
    if (!createDevice) {
        dxgiDevice->Release();
        Log("InitWgcCapture: CreateDirect3D11DeviceFromDXGIDevice unavailable");
        return HRESULT_FROM_WIN32(ERROR_PROC_NOT_FOUND);
    }

    hr = createDevice(dxgiDevice, &g_wgcInspectableDevice);
    dxgiDevice->Release();
    if (FAILED(hr)) {
        LogHR("CreateDirect3D11DeviceFromDXGIDevice", hr);
        return hr;
    }
    hr = g_wgcInspectableDevice->QueryInterface(
        __uuidof(ABI::Windows::Graphics::DirectX::Direct3D11::IDirect3DDevice),
        reinterpret_cast<void**>(&g_wgcDevice));
    if (FAILED(hr)) {
        LogHR("QI IDirect3DDevice", hr);
        return hr;
    }

    hr = CreateWgcItemForWindow(g_targetWindow, &g_wgcItem);
    if (FAILED(hr)) {
        LogHR("CreateForWindow(WGC item)", hr);
        return hr;
    }

    g3d::wgc::SizeInt32 itemSize = {};
    hr = g_wgcItem->get_Size(&itemSize);
    if (FAILED(hr) || itemSize.Width <= 0 || itemSize.Height <= 0) {
        const UINT fallbackW = static_cast<UINT>(g_targetRect.right - g_targetRect.left);
        const UINT fallbackH = static_cast<UINT>(g_targetRect.bottom - g_targetRect.top);
        itemSize.Width = static_cast<INT32>(fallbackW);
        itemSize.Height = static_cast<INT32>(fallbackH);
        Log("InitWgcCapture: item size unavailable, using target rect %ux%u", fallbackW, fallbackH);
    }
    if (itemSize.Width <= 0 || itemSize.Height <= 0) return E_INVALIDARG;

    g3d::wgc::IDirect3D11CaptureFramePoolStatics2* poolFactory = nullptr;
    hr = CreateWgcFramePoolFactory(&poolFactory);
    if (FAILED(hr)) {
        LogHR("RoGetActivationFactory(Direct3D11CaptureFramePool)", hr);
        return hr;
    }

    hr = poolFactory->CreateFreeThreaded(
        g_wgcDevice,
        ABI::Windows::Graphics::DirectX::DirectXPixelFormat_B8G8R8A8UIntNormalized,
        2,
        itemSize,
        &g_wgcFramePool);
    poolFactory->Release();
    if (FAILED(hr)) {
        LogHR("Direct3D11CaptureFramePool::CreateFreeThreaded", hr);
        return hr;
    }

    hr = g_wgcFramePool->CreateCaptureSession(g_wgcItem, &g_wgcSession);
    if (FAILED(hr)) {
        LogHR("CreateCaptureSession", hr);
        return hr;
    }

    if (!CreateWgcCaptureTextures(static_cast<UINT>(itemSize.Width), static_cast<UINT>(itemSize.Height))) {
        return E_FAIL;
    }
    hr = g_wgcSession->StartCapture();
    if (FAILED(hr)) {
        LogHR("GraphicsCaptureSession::StartCapture", hr);
        return hr;
    }

    g_wgcWindow = g_targetWindow;
    g_wgcWidth = static_cast<UINT>(itemSize.Width);
    g_wgcHeight = static_cast<UINT>(itemSize.Height);
    Log("InitWgcCapture: complete hwnd=%p size=%ux%u", g_wgcWindow, g_wgcWidth, g_wgcHeight);
    return S_OK;
}

// Try to (re)open the tracker shared memory. Safe to call repeatedly.
static void TryAttachShm() {
    if (g_shmView) return;
    bool newHandle = false, newView = false;
    if (!g_shmH) {
        g_shmH = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_NAME);
        if (g_shmH) { newHandle = true; Log("SHM: OpenFileMapping(G3D) succeeded, handle=%p", g_shmH); }
    }
    if (g_shmH && !g_shmView) {
        g_shmView = MapViewOfFile(g_shmH, FILE_MAP_READ, 0, 0, sizeof(HeadPose));
        if (g_shmView) { newView = true; Log("SHM: MapViewOfFile succeeded, view=%p (size=%zu)", g_shmView, sizeof(HeadPose)); }
        else Log("SHM: MapViewOfFile FAILED, GLE=%lu", GetLastError());
    }
    (void)newHandle; (void)newView;
}

static void TryAttachPoseSequence() {
    if (g_seqView) return;
    if (!g_seqH) {
        g_seqH = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_SEQ);
        if (g_seqH) Log("SHM: OpenFileMapping(G3D_Seq) succeeded, handle=%p", g_seqH);
    }
    if (g_seqH && !g_seqView) {
        g_seqView = MapViewOfFile(g_seqH, FILE_MAP_READ, 0, 0, sizeof(uint32_t));
        if (g_seqView) Log("SHM: MapViewOfFile(G3D_Seq) succeeded, view=%p", g_seqView);
        else Log("SHM: MapViewOfFile(G3D_Seq) FAILED, GLE=%lu", GetLastError());
    }
}

static bool ReadStablePose(HeadPose* out) {
    if (!g_shmView || !out) return false;
    if (!g_seqView) return ReadStableSnapshot(g_shmView, out);
    for (int attempt = 0; attempt < 4; ++attempt) {
        uint32_t before = 0, after = 0;
        memcpy(&before, g_seqView, sizeof(before));
        if (before & 1u) continue;
        MemoryBarrier();
        HeadPose snapshot = {};
        memcpy(&snapshot, g_shmView, sizeof(snapshot));
        MemoryBarrier();
        memcpy(&after, g_seqView, sizeof(after));
        if (before == after && (after & 1u) == 0) {
            *out = snapshot;
            return true;
        }
    }
    return false;
}

// Optional face-validity channel. Older trackers do not publish it, so the
// overlay gracefully falls back to pose timestamp freshness when absent.
static void TryAttachTrackerState() {
    if (g_stateView) return;
    if (!g_stateH) {
        g_stateH = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_STATE);
        if (g_stateH) Log("SHM: OpenFileMapping(G3D_State) succeeded, handle=%p", g_stateH);
    }
    if (g_stateH && !g_stateView) {
        g_stateView = MapViewOfFile(g_stateH, FILE_MAP_READ, 0, 0, sizeof(TrackerState));
        if (g_stateView) Log("SHM: MapViewOfFile(G3D_State) succeeded, view=%p", g_stateView);
        else Log("SHM: MapViewOfFile(G3D_State) FAILED, GLE=%lu", GetLastError());
    }
}

// Try to (re)open the settings shared memory (G3D_Settings). Optional:
// if the GUI isn't running, we just keep the CLI/autodetect defaults.
static void TryAttachSettings() {
    if (g_setView) return;
    if (!g_setH) {
        g_setH = OpenFileMappingW(FILE_MAP_READ, FALSE, SHM_SETTINGS);
        if (g_setH) Log("SHM: OpenFileMapping(G3D_Settings) succeeded, handle=%p", g_setH);
    }
    if (g_setH && !g_setView) {
        g_setView = MapViewOfFile(g_setH, FILE_MAP_READ, 0, 0, sizeof(Settings));
        if (g_setView) Log("SHM: MapViewOfFile(G3D_Settings) succeeded, view=%p", g_setView);
        else Log("SHM: MapViewOfFile(G3D_Settings) FAILED, GLE=%lu", GetLastError());
    }
}

// Resolve effective runtime parameters each frame.
// Priority: CLI override > settings GUI > autodetect/default.
static void ApplySettings() {
    float sw = g_autoScreenW, sh = g_autoScreenH;
    float sx = 1.0f, sy = 1.0f, dp = 30.0f;
    uint32_t dc = 1;
    uint32_t db = 0;
    uint32_t dm = 1;
    uint32_t stereoLayout = 0;
    uint32_t eyeOrder = 0;
    uint32_t panelWidthPx = 0;
    uint32_t panelHeightPx = 0;
    uint32_t trackingMode = 0;
    float ipdCm = 6.4f;
    float dg = 1.0f, fr = 0.1f;
    float focusPlaneCm = 0.0f;
    float dz_cm = 0.5f;   // default 5 mm

    if (g_setView) {
        Settings s = {};
        if (!ReadStableSettings(g_setView, &s)) return;
        if (s.strengthX    > 0.0f)    sx = s.strengthX;
        if (s.strengthY    > 0.0f)    sy = s.strengthY;
        if (s.virtualDepthCm >= 0.0f) dp = s.virtualDepthCm;
        if (s.screenWCm    > 0.0f)    sw = s.screenWCm;
        if (s.screenHCm    > 0.0f)    sh = s.screenHCm;
        dc = s.depthCurve;
        if (s.depthGamma  > 0.0f)    dg = s.depthGamma;
        if (s.focusRadius >= 0.0f)    fr = s.focusRadius;
        if (s.ipdMm       > 0.0f)     ipdCm = s.ipdMm * 0.1f;
        if (s.deadzoneM   >= 0.0f)    dz_cm = s.deadzoneM * 0.1f;  // mm → cm
        db = s.displayBackend;
        dm = s.depthMode <= 2 ? s.depthMode : 1;
        stereoLayout = s.stereoLayout <= 1 ? s.stereoLayout : 0;
        eyeOrder = s.eyeOrder <= 1 ? s.eyeOrder : 0;
        panelWidthPx = s.panelWidthPx;
        panelHeightPx = s.panelHeightPx;
        if (s.focusPlaneCm >= 0.0f) focusPlaneCm = s.focusPlaneCm;
        trackingMode = s.trackingMode <= 1 ? s.trackingMode : 0;
    }

    if (g_cliScreenW  > 0.0f) sw = g_cliScreenW;
    if (g_cliScreenH  > 0.0f) sh = g_cliScreenH;
    if (g_cliStrength > 0.0f) { sx = g_cliStrength; sy = g_cliStrength; }
    if (g_cliDepth   >= 0.0f) dp = g_cliDepth;

    g_screenW      = sw > 0.0f ? sw : 59.8f;
    g_screenH      = sh > 0.0f ? sh : 33.6f;
    g_strength     = sx;
    g_strengthX    = sx;
    g_strengthY    = sy;
    g_virtualDepth = dp;
    g_depthCurve   = dc;
    g_depthGamma   = dg;
    g_focusRadius  = fr;
    g_deadzoneCm   = dz_cm;
    g_displayBackend = db;
    g_depthMode = dm;
    g_ipdCm = ipdCm;
    g_stereoLayout = stereoLayout;
    g_eyeOrder = eyeOrder;
    g_panelWidthPx = panelWidthPx;
    g_panelHeightPx = panelHeightPx;
    g_focusPlaneCm = focusPlaneCm;
    g_trackingMode = trackingMode;
    if (g_depth) g_depth->set_performance_mode(g_depthMode);
}

// Query the primary monitor's physical size via EDID.
// Writes cm into *outW/*outH. Returns true on success (both > 0).
// Two sources, in order of preference:
//   1. GetDeviceCaps(HORZSIZE / VERTSIZE) — mm from the display driver.
//      This reads EDID on modern Windows and works for the vast majority of
//      monitors; a small number report 0 or a generic 320x240mm fallback.
//   2. Fallback: DPI-based estimate — resolution / effective DPI * 2.54.
//      Less accurate but always returns something plausible.
static bool AutodetectScreenSizeCm(float* outW, float* outH, const wchar_t* deviceName) {
    HDC hdc = deviceName && *deviceName
        ? CreateDCW(L"DISPLAY", deviceName, nullptr, nullptr)
        : GetDC(nullptr);
    if (!hdc) return false;

    int wmm = GetDeviceCaps(hdc, HORZSIZE);
    int hmm = GetDeviceCaps(hdc, VERTSIZE);
    int pxW = GetDeviceCaps(hdc, HORZRES);
    int pxH = GetDeviceCaps(hdc, VERTRES);
    int dpiX = GetDeviceCaps(hdc, LOGPIXELSX);
    int dpiY = GetDeviceCaps(hdc, LOGPIXELSY);
    if (deviceName && *deviceName) DeleteDC(hdc);
    else ReleaseDC(nullptr, hdc);

    Log("Autodetect: HORZSIZE=%dmm VERTSIZE=%dmm HORZRES=%d VERTRES=%d DPI=%dx%d",
        wmm, hmm, pxW, pxH, dpiX, dpiY);

    // Sanity filter: reject obviously-bogus values. 320x240mm is the classic
    // "driver didn't report anything" default; anything < 15cm wide is a phone.
    bool edidOk = (wmm > 150 && hmm > 100) &&
                  !(wmm == 320 && hmm == 240);
    if (edidOk) {
        *outW = wmm / 10.0f;
        *outH = hmm / 10.0f;
        Log("Autodetect: using EDID -> %.2fx%.2f cm", *outW, *outH);
        return true;
    }

    // DPI-based fallback (less accurate, but always plausible).
    if (dpiX > 0 && dpiY > 0 && pxW > 0 && pxH > 0) {
        *outW = (float)pxW / (float)dpiX * 2.54f;
        *outH = (float)pxH / (float)dpiY * 2.54f;
        Log("Autodetect: EDID unreliable, using DPI -> %.2fx%.2f cm", *outW, *outH);
        return true;
    }

    Log("Autodetect: FAILED (no usable source)");
    return false;
}

// Locate the depth ONNX model. Search order:
//   1. VDA-Small (preferred — temporally consistent):
//      <exe_dir>/models/video_depth_anything_vits_518.onnx
//      <exe_dir>/../models/video_depth_anything_vits_518.onnx  (dev layout)
//   2. Depth Anything V2 Small (fallback):
//      <exe_dir>/models/depth_anything_v2_small_fp16.onnx
//      <exe_dir>/../models/depth_anything_v2_small_fp16.onnx
//
// Both models share the same I/O contract:
//   input:  float32 [1, 3, 518, 518]   (ImageNet-normalized NCHW)
//   output: float32 [1, 518, 518] or [1, 1, 518, 518]
//
// To use VDA: python scripts/export_vda_onnx.py --install --replace
// Returns empty string if no model found.
static std::wstring FindDepthModel() {
    wchar_t exePath[MAX_PATH]; GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    wchar_t* slash = wcsrchr(exePath, L'\\');
    if (slash) *(slash + 1) = L'\0';

    // Prefer VDA-Small (better temporal consistency); fall back to DAv2-Small.
    const wchar_t* candidates[] = {
        L"models\\video_depth_anything_vits_518.onnx",      // VDA near exe
        L"..\\models\\video_depth_anything_vits_518.onnx",  // VDA dev layout
        L"models\\depth_anything_v2_small_fp16.onnx",       // DAv2 near exe
        L"..\\models\\depth_anything_v2_small_fp16.onnx",   // DAv2 dev layout
    };
    for (const wchar_t* rel : candidates) {
        wchar_t full[MAX_PATH];
        swprintf_s(full, MAX_PATH, L"%s%s", exePath, rel);
        DWORD attrs = GetFileAttributesW(full);
        if (attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY)) {
            Log("FindDepthModel: found at %ls", full);
            return std::wstring(full);
        }
    }
    Log("FindDepthModel: NOT found (checked %zu candidates next to exe)",
        sizeof(candidates)/sizeof(candidates[0]));
    return std::wstring();
}

// Create a 1x1 R16_FLOAT=0.0 texture + SRV used when depth inference is unavailable.
// depth=0.0 means "farthest" in our convention, so the scene sits at the virtual
// far plane and behaves exactly like the pre-depth flat-plane overlay.
static bool CreateFallbackDepthSrv() {
    // IEEE 754 half 0.0 is all zero bits.
    uint16_t zero = 0;
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = 1; td.Height = 1; td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R16_FLOAT;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_IMMUTABLE;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;

    D3D11_SUBRESOURCE_DATA sr = {};
    sr.pSysMem = &zero;
    sr.SysMemPitch = sizeof(uint16_t);

    HRESULT hr = g_dev->CreateTexture2D(&td, &sr, &g_fallbackTex);
    if (FAILED(hr)) { LogHR("CreateTexture2D(fallback depth)", hr); return false; }

    D3D11_SHADER_RESOURCE_VIEW_DESC sd = {};
    sd.Format = td.Format;
    sd.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
    sd.Texture2D.MipLevels = 1;
    hr = g_dev->CreateShaderResourceView(g_fallbackTex, &sd, &g_fallbackSrv);
    if (FAILED(hr)) { LogHR("CreateShaderResourceView(fallback depth)", hr); return false; }
    Log("Fallback depth SRV created (1x1 R16F=0.0)");
    return true;
}

// Attempt to bring up monocular depth inference. Non-fatal: on any failure we
// fall back to the 1x1 zero-depth SRV so the overlay still runs.
static void InitDepth() {
    std::wstring model = FindDepthModel();
    if (model.empty()) {
        Log("InitDepth: model file not found — running without per-pixel depth. "
            "Run scripts/bootstrap.py to download it.");
        return;
    }
    if (!g_capTex) {
        Log("InitDepth: capture texture not ready, skipping depth init");
        return;
    }
    D3D11_TEXTURE2D_DESC cd = {};
    g_capTex->GetDesc(&cd);

    auto* d = new DepthInferencer();
    if (!d->init(g_dev, g_ctx, model, (int)cd.Width, (int)cd.Height)) {
        Log("InitDepth: DepthInferencer::init failed: %s", d->last_error());
        delete d;
        return;
    }
    g_depth = d;
    g_depth->set_performance_mode(g_depthMode);
    Log("InitDepth: depth inference online (capture %ux%u, model 518x518)",
        cd.Width, cd.Height);
}

static const char* CaptureStateName(CaptureState state) {
    switch (state) {
    case CaptureState::Running: return "running";
    case CaptureState::Rebinding: return "rebinding";
    case CaptureState::DeviceRecovery: return "device_recovery";
    case CaptureState::Unavailable: return "unavailable";
    }
    return "unavailable";
}

static void UpdateOverlayVisibility() {
    if (!g_hwnd) return;
    bool targetForeground = true;
    if (!g_targetExePath.empty()) {
        targetForeground = false;
        const HWND foreground = GetForegroundWindow();
        DWORD foregroundPid = 0;
        DWORD selectedPid = g_targetPid;
        if (foreground) GetWindowThreadProcessId(foreground, &foregroundPid);
        if (selectedPid == 0 && g_targetWindow) {
            GetWindowThreadProcessId(g_targetWindow, &selectedPid);
        }
        targetForeground = selectedPid != 0 && foregroundPid == selectedPid;
    }

    const uint64_t nowMs = GetTickCount64();
    static constexpr uint64_t kCaptureFrameStaleMs = 1000;
    const bool captureFresh = g_lastCaptureFrameMs != 0
        && nowMs - g_lastCaptureFrameMs <= kCaptureFrameStaleMs;
    const bool visible = g_captureState == CaptureState::Running
        && g_hasFrame
        && (g_targetExePath.empty() || (targetForeground && captureFresh));
    if (visible == g_overlayVisible) return;
    ShowWindow(g_hwnd, visible ? SW_SHOWNOACTIVATE : SW_HIDE);
    g_overlayVisible = visible;
    Log("Overlay visibility: %s target_foreground=%d capture_fresh=%d",
        visible ? "shown" : "hidden", targetForeground ? 1 : 0, captureFresh ? 1 : 0);
}

static void SetCaptureState(CaptureState state, const char* reason) {
    g_captureState = state;
    g_captureReason = reason;
    Log("CaptureStatus: state=%s reason=%s", CaptureStateName(state), reason);
    UpdateOverlayVisibility();
}

static void DestroyCaptureResources() {
    g_hasFrame = false;
    g_lastCaptureFrameMs = 0;
    g_presentRetryPending = false;
    if (g_depth) { delete g_depth; g_depth = nullptr; }
    ReleaseWgcResources();
    ReleaseCaptureTextures();
    SafeRelease(g_dup);
    g_captureBackend = CaptureBackend::DesktopDuplication;
}

static void QueueCaptureSignal(CaptureSignal signal, const char* reason) {
    const auto action = g3d::capture::AdvanceCaptureState(g_captureState, signal);
    if (signal == CaptureSignal::BindingDirty) {
        DestroyCaptureResources();
        g_rebindRetry.Reset(GetTickCount64());
    } else if (signal == CaptureSignal::DuplicationLost || signal == CaptureSignal::RebindRetry) {
        DestroyCaptureResources();
        g_rebindRetry.RecordFailure(GetTickCount64());
    }
    SetCaptureState(action.next_state, reason);
}

static bool IsUnavailableDuplicationFailure(HRESULT hr) {
    return hr == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE
        || hr == DXGI_ERROR_UNSUPPORTED
        || hr == DXGI_ERROR_SESSION_DISCONNECTED
        || hr == E_ACCESSDENIED;
}

static bool IsDeviceLoss(HRESULT hr) {
    return hr == DXGI_ERROR_DEVICE_REMOVED
        || hr == DXGI_ERROR_DEVICE_RESET
        || hr == DXGI_ERROR_DEVICE_HUNG;
}

static HRESULT CreateDeviceAndRenderer();
static void EnterDeviceRecovery(const char* operation, HRESULT hr, const char* reasonCode);

class DesktopFrameLease {
public:
    explicit DesktopFrameLease(IDXGIOutputDuplication* duplication) : duplication_(duplication) {
        if (duplication_) duplication_->AddRef();
    }

    ~DesktopFrameLease() {
        if (acquired_ && duplication_) {
            const HRESULT hr = duplication_->ReleaseFrame();
            if (FAILED(hr)) LogHR("ReleaseFrame", hr);
        }
        SafeRelease(texture_);
        SafeRelease(resource_);
        SafeRelease(duplication_);
    }

    HRESULT Acquire(UINT timeoutMs, DXGI_OUTDUPL_FRAME_INFO* frameInfo) {
        if (!duplication_ || !frameInfo) return E_POINTER;
        const HRESULT hr = duplication_->AcquireNextFrame(timeoutMs, frameInfo, &resource_);
        if (FAILED(hr)) return hr;
        acquired_ = true;
        if (!resource_) return E_FAIL;
        return resource_->QueryInterface(
            __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&texture_));
    }

    ID3D11Texture2D* texture() const { return texture_; }

private:
    IDXGIOutputDuplication* duplication_ = nullptr;
    IDXGIResource* resource_ = nullptr;
    ID3D11Texture2D* texture_ = nullptr;
    bool acquired_ = false;
};

static void TickCaptureRebind() {
    if (g_captureState == CaptureState::DeviceRecovery) {
        if (!g_rebindRetry.CanAttempt(GetTickCount64())) return;
        const BindingStatus recoveryBinding = RefreshCaptureBinding();
        if (recoveryBinding == BindingStatus::TargetSpansOutput) {
            g_bindingDirty = false;
            g_rebindRetry.RecordFailure(GetTickCount64());
            SetCaptureState(CaptureState::Unavailable, "target_spans_output");
            return;
        }
        if (recoveryBinding != BindingStatus::Ready
            && recoveryBinding != BindingStatus::TargetUnavailable) {
            g_bindingDirty = false;
            g_rebindRetry.RecordFailure(GetTickCount64());
            SetCaptureState(CaptureState::Unavailable, "no_matching_output");
            return;
        }
        SyncOverlayWindowToBinding();
        const HRESULT createHr = CreateDeviceAndRenderer();
        if (FAILED(createHr)) {
            LogHR("CreateDeviceAndRenderer", createHr);
            g_rebindRetry.RecordFailure(GetTickCount64());
            return;
        }
        g_rebindRetry.Reset(GetTickCount64());
        if (recoveryBinding == BindingStatus::TargetUnavailable) {
            g_bindingDirty = false;
            g_rebindRetry.RecordFailure(GetTickCount64());
            SetCaptureState(CaptureState::Unavailable, "target_not_running");
            return;
        }
        SetCaptureState(CaptureState::Rebinding, "device_recreated");
        return;
    }
    if (g_captureState == CaptureState::Unavailable) {
        // Unavailable is a degraded state, not a terminal state. Protected
        // content, fullscreen transitions, and temporary duplication limits
        // routinely clear without a display/binding notification.
        if (g_bindingDirty) {
            QueueCaptureSignal(CaptureSignal::BindingDirty, "binding_changed");
        } else if (g_rebindRetry.CanAttempt(GetTickCount64())) {
            SetCaptureState(CaptureState::Rebinding, "retry_scheduled");
        } else {
            return;
        }
    }
    if (g_captureState != CaptureState::Rebinding
        || !g_rebindRetry.CanAttempt(GetTickCount64())) {
        return;
    }

    const BindingStatus bindingStatus = RefreshCaptureBinding();
    if (bindingStatus == BindingStatus::TargetSpansOutput) {
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "target_spans_output");
        return;
    }
    if (bindingStatus == BindingStatus::TargetUnavailable) {
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "target_not_running");
        return;
    }
    if (bindingStatus != BindingStatus::Ready) {
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "no_matching_output");
        return;
    }

    SyncOverlayWindowToBinding();
    if (!g_dev || !g_ctx || !g_swap) {
        g_rebindRetry.Reset(GetTickCount64());
        SetCaptureState(CaptureState::DeviceRecovery, "renderer_missing");
        return;
    }
    if (g_hasDeviceAdapterLuid && !SameLuid(g_deviceAdapterLuid, g_binding.adapterLuid)) {
        EnterDeviceRecovery("capture adapter changed", DXGI_ERROR_DEVICE_RESET, "adapter_changed");
        return;
    }
    HRESULT hr = E_FAIL;
    bool usingWgc = false;
    if (g_useTargetWindow && g_targetWindow) {
        hr = InitWgcCapture();
        if (SUCCEEDED(hr)) {
            usingWgc = true;
        } else {
            LogHR("InitWgcCapture failed; target capture will retry", hr);
            if (IsDeviceLoss(hr)
                || (g_dev && IsDeviceLoss(g_dev->GetDeviceRemovedReason()))) {
                EnterDeviceRecovery("InitWgcCapture", hr, "device_lost");
                return;
            }
            DestroyCaptureResources();
            g_bindingDirty = false;
            g_rebindRetry.RecordFailure(GetTickCount64());
            SetCaptureState(CaptureState::Unavailable, "target_capture_unavailable");
            return;
        }
    }
    if (!usingWgc) {
        g_captureBackend = CaptureBackend::DesktopDuplication;
        hr = InitDuplication();
    }
    if (SUCCEEDED(hr)) {
        InitDepth();
        g_rebindRetry.Reset(GetTickCount64());
        const char* boundReason = usingWgc ? "bound_target_wgc" : "bound_desktop";
        SetCaptureState(CaptureState::Running, boundReason);
    } else if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_INVALID_CALL) {
        QueueCaptureSignal(CaptureSignal::RebindRetry, "duplicate_retry");
    } else if (IsUnavailableDuplicationFailure(hr)) {
        DestroyCaptureResources();
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "duplicate_unavailable");
    } else if (IsDeviceLoss(hr)
        || (g_dev && IsDeviceLoss(g_dev->GetDeviceRemovedReason()))) {
        EnterDeviceRecovery("InitDuplication", hr, "device_lost");
    } else {
        QueueCaptureSignal(CaptureSignal::RebindRetry, "duplicate_failed");
    }
}

static bool CaptureIsUniformBlack() {
    if (!g_dev || !g_ctx || !g_capTex || g_captureW < 4 || g_captureH < 4) return false;
    const DWORD nowMs = GetTickCount();
    if (nowMs < g_nextBlackProbeMs) return false;
    g_nextBlackProbeMs = nowMs + 250;

    if (!g_blackProbeTex) {
        D3D11_TEXTURE2D_DESC desc = {};
        desc.Width = 3;
        desc.Height = 3;
        desc.MipLevels = 1;
        desc.ArraySize = 1;
        desc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        desc.SampleDesc.Count = 1;
        desc.Usage = D3D11_USAGE_STAGING;
        desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        const HRESULT createHr = g_dev->CreateTexture2D(&desc, nullptr, &g_blackProbeTex);
        if (FAILED(createHr)) {
            LogHR("CreateTexture2D(black probe)", createHr);
            return false;
        }
    }

    const UINT sampleX[3] = { g_captureW / 4, g_captureW / 2, (g_captureW * 3) / 4 };
    const UINT sampleY[3] = { g_captureH / 4, g_captureH / 2, (g_captureH * 3) / 4 };
    for (UINT y = 0; y < 3; ++y) {
        for (UINT x = 0; x < 3; ++x) {
            const D3D11_BOX box = {
                sampleX[x], sampleY[y], 0,
                sampleX[x] + 1, sampleY[y] + 1, 1,
            };
            g_ctx->CopySubresourceRegion(g_blackProbeTex, 0, x, y, 0, g_capTex, 0, &box);
        }
    }

    D3D11_MAPPED_SUBRESOURCE mapped = {};
    const HRESULT mapHr = g_ctx->Map(g_blackProbeTex, 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(mapHr)) return false;
    bool black = true;
    for (UINT y = 0; y < 3 && black; ++y) {
        const auto* row = static_cast<const uint8_t*>(mapped.pData) + y * mapped.RowPitch;
        for (UINT x = 0; x < 3; ++x) {
            const uint8_t* pixel = row + x * 4;
            if (pixel[0] > 4 || pixel[1] > 4 || pixel[2] > 4) {
                black = false;
                break;
            }
        }
    }
    g_ctx->Unmap(g_blackProbeTex, 0);
    return black;
}

static bool RejectUniformBlackTargetFrame() {
    if (!g_useTargetWindow || !g_targetWindow) return false;

    DWORD foregroundPid = 0;
    DWORD targetPid = 0;
    const HWND foreground = GetForegroundWindow();
    if (foreground) GetWindowThreadProcessId(foreground, &foregroundPid);
    GetWindowThreadProcessId(g_targetWindow, &targetPid);
    if (foregroundPid == 0 || foregroundPid != targetPid) return false;
    if (!CaptureIsUniformBlack()) {
        g_blackProbeStreak = 0;
        return false;
    }

    ++g_blackProbeStreak;
    g_hasFrame = false;
    SetLayeredWindowAttributes(g_hwnd, 0, 0, LWA_ALPHA);
    g_overlayRevealed = false;
    UpdateOverlayVisibility();
    if (g_blackProbeStreak >= 3) {
        Log("Capture safety: repeated uniform-black game frames; scheduling retry");
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "black_capture");
    }
    return true;
}

static bool RecreateWgcFramePool(UINT width, UINT height) {
    if (!g_wgcFramePool || !g_wgcDevice || width == 0 || height == 0) return false;
    g3d::wgc::SizeInt32 size = {
        static_cast<INT32>(width), static_cast<INT32>(height)
    };
    const HRESULT hr = g_wgcFramePool->Recreate(
        g_wgcDevice,
        ABI::Windows::Graphics::DirectX::DirectXPixelFormat_B8G8R8A8UIntNormalized,
        2, size);
    if (FAILED(hr)) {
        LogHR("Direct3D11CaptureFramePool::Recreate", hr);
        return false;
    }
    g_hasFrame = false;
    g_lastCaptureFrameMs = 0;
    if (g_depth) { delete g_depth; g_depth = nullptr; }
    if (!CreateWgcCaptureTextures(width, height)) return false;
    g_wgcWidth = width;
    g_wgcHeight = height;
    InitDepth();
    Log("WGC frame pool recreated in-place: %ux%u", width, height);
    UpdateOverlayVisibility();
    return true;
}

static void UpdateWgcCapture() {
    if (g_captureState != CaptureState::Running || !g_wgcFramePool) return;

    g3d::wgc::IDirect3D11CaptureFrame* latestFrame = nullptr;
    for (;;) {
        g3d::wgc::IDirect3D11CaptureFrame* nextFrame = nullptr;
        const HRESULT nextHr = g_wgcFramePool->TryGetNextFrame(&nextFrame);
        if (FAILED(nextHr)) {
            if (nextHr == RO_E_CLOSED) {
                QueueCaptureSignal(CaptureSignal::RebindRetry, "wgc_closed");
            } else if (IsDeviceLoss(nextHr)
                || (g_dev && IsDeviceLoss(g_dev->GetDeviceRemovedReason()))) {
                EnterDeviceRecovery("WGC TryGetNextFrame", nextHr, "device_lost");
            } else {
                QueueCaptureSignal(CaptureSignal::RebindRetry, "wgc_frame_failed");
            }
            SafeRelease(latestFrame);
            return;
        }
        if (!nextFrame) break;
        CloseWinRtObject(latestFrame);
        SafeRelease(latestFrame);
        latestFrame = nextFrame;
    }
    if (!latestFrame) {
        ++g_acquireTimeout;
        return;
    }

    g3d::wgc::SizeInt32 contentSize = {};
    if (SUCCEEDED(latestFrame->get_ContentSize(&contentSize))
        && contentSize.Width > 0 && contentSize.Height > 0
        && (static_cast<UINT>(contentSize.Width) != g_wgcWidth
            || static_cast<UINT>(contentSize.Height) != g_wgcHeight)) {
        Log("WGC frame resized: %dx%d -> recreating frame pool", contentSize.Width, contentSize.Height);
        CloseWinRtObject(latestFrame);
        SafeRelease(latestFrame);
        if (!RecreateWgcFramePool(static_cast<UINT>(contentSize.Width),
                                  static_cast<UINT>(contentSize.Height))) {
            QueueCaptureSignal(CaptureSignal::RebindRetry, "wgc_resize_failed");
        }
        return;
    }

    ABI::Windows::Graphics::DirectX::Direct3D11::IDirect3DSurface* surface = nullptr;
    HRESULT hr = latestFrame->get_Surface(&surface);
    if (FAILED(hr) || !surface) {
        LogHR("WGC frame get_Surface", hr);
        CloseWinRtObject(latestFrame);
        SafeRelease(latestFrame);
        QueueCaptureSignal(CaptureSignal::RebindRetry, "wgc_surface_failed");
        return;
    }

    g3d::wgc::IDirect3DDxgiInterfaceAccess* access = nullptr;
    hr = surface->QueryInterface(__uuidof(g3d::wgc::IDirect3DDxgiInterfaceAccess),
        reinterpret_cast<void**>(&access));
    if (SUCCEEDED(hr) && access) {
        ID3D11Texture2D* frameTex = nullptr;
        hr = access->GetInterface(__uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&frameTex));
        if (SUCCEEDED(hr) && frameTex && g_capTex) {
            g_ctx->CopyResource(g_capTex, frameTex);
            ++g_acquireOk;
            if (!RejectUniformBlackTargetFrame()) {
                g_hasFrame = true;
                g_lastCaptureFrameMs = GetTickCount64();
                ++g_captureFrameSerial;
                UpdateOverlayVisibility();
                if (g_depth && !g_depth->run(g_capTex)) {
                    static int depthFails = 0;
                    if (++depthFails < 5 || depthFails % 120 == 0) {
                        Log("DepthInferencer::run failed (#%d): %s", depthFails, g_depth->last_error());
                    }
                }
            }
        } else {
            LogHR("WGC GetInterface(ID3D11Texture2D)", hr);
            QueueCaptureSignal(CaptureSignal::RebindRetry, "wgc_texture_failed");
        }
        SafeRelease(frameTex);
        access->Release();
    } else {
        LogHR("QI IDirect3DDxgiInterfaceAccess", hr);
        QueueCaptureSignal(CaptureSignal::RebindRetry, "wgc_access_failed");
    }

    surface->Release();
    CloseWinRtObject(latestFrame);
    latestFrame->Release();
}

static void UpdateCapture() {
    if (g_captureBackend == CaptureBackend::WindowsGraphicsCapture) {
        UpdateWgcCapture();
        return;
    }
    if (g_captureState != CaptureState::Running || !g_dup) return;

    DXGI_OUTDUPL_FRAME_INFO info = {};
    DesktopFrameLease frame(g_dup);
    const HRESULT hr = frame.Acquire(16, &info);
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        ++g_acquireTimeout;
        return;
    }
    if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_INVALID_CALL) {
        ++g_acquireLost;
        QueueCaptureSignal(CaptureSignal::DuplicationLost,
            hr == DXGI_ERROR_ACCESS_LOST ? "access_lost" : "invalid_call");
        return;
    }
    if (IsDeviceLoss(hr)
        || (g_dev && IsDeviceLoss(g_dev->GetDeviceRemovedReason()))) {
        ++g_acquireOther;
        EnterDeviceRecovery("AcquireNextFrame", hr, "device_lost");
        return;
    }
    if (IsUnavailableDuplicationFailure(hr)) {
        ++g_acquireOther;
        DestroyCaptureResources();
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "duplicate_unavailable");
        return;
    }
    if (FAILED(hr) || !frame.texture()) {
        ++g_acquireOther;
        QueueCaptureSignal(CaptureSignal::RebindRetry, "acquire_failed");
        return;
    }

    if (info.ProtectedContentMaskedOut) {
        // This means *some pixels* were replaced, not that the duplication
        // object or the whole frame is unusable. WDA_EXCLUDEFROMCAPTURE can
        // itself trigger the flag on otherwise valid desktops. Keep the frame;
        // the uniform-black probe below remains the whole-frame safety gate.
        static uint64_t protectedFrames = 0;
        if (++protectedFrames <= 3 || protectedFrames % 600 == 0) {
            Log("Capture notice: ProtectedContentMaskedOut=TRUE; accepting usable frame");
        }
    }

    ++g_acquireOk;
    if (!NormalizeCapturedFrame(frame.texture())) {
        QueueCaptureSignal(CaptureSignal::RebindRetry, "normalize_failed");
        return;
    }

    if (RejectUniformBlackTargetFrame()) return;

    g_hasFrame = true;
    g_lastCaptureFrameMs = GetTickCount64();
    ++g_captureFrameSerial;
    UpdateOverlayVisibility();
    if (g_depth && !g_depth->run(g_capTex)) {
        static int depthFails = 0;
        if (++depthFails < 5 || depthFails % 120 == 0) {
            Log("DepthInferencer::run failed (#%d): %s", depthFails, g_depth->last_error());
        }
    }
}

static DWORD CaptureIdleWaitMs() {
    if (g_captureState == CaptureState::Running) {
        // WGC TryGetNextFrame is non-blocking. A short message-aware wait avoids
        // spinning at hundreds of iterations/s when neither capture nor tracker
        // produced a sample. Desktop Duplication already blocks up to 16 ms.
        return 4;
    }

    const uint64_t nowMs = GetTickCount64();
    uint64_t deadlineMs = g_nextTargetPollMs;
    if (g_captureState == CaptureState::Rebinding
        || g_captureState == CaptureState::DeviceRecovery
        || g_captureState == CaptureState::Unavailable) {
        const uint64_t retryMs = g_rebindRetry.next_attempt_ms();
        if (deadlineMs == 0 || retryMs < deadlineMs) deadlineMs = retryMs;
    }

    if (deadlineMs <= nowMs) return 1;
    const uint64_t remainingMs = deadlineMs - nowMs;
    return static_cast<DWORD>(remainingMs < 50 ? remainingMs : 50);
}

static void DestroyRendererResources() {
    SafeRelease(g_normalizeCb);
    SafeRelease(g_normalizePs);
    SafeRelease(g_fallbackSrv);
    SafeRelease(g_fallbackTex);
    SafeRelease(g_gpuEnd);
    SafeRelease(g_gpuStart);
    SafeRelease(g_gpuDisjoint);
    SafeRelease(g_sceneSmp);
    SafeRelease(g_depthSmp);
    SafeRelease(g_cb);
    SafeRelease(g_ps);
    SafeRelease(g_vs);
    SafeRelease(g_rtv);
    g_gpuTimingPending = false;
    g_lastGpuMs = -1.0;
    g_gpuTimingSamples = 0;
    g_renderWidth = 0;
    g_renderHeight = 0;
}

static void DestroyDeviceResources() {
    DestroyCaptureResources();
    if (g_ctx) {
        g_ctx->ClearState();
        g_ctx->Flush();
    }
    DestroyRendererResources();
    g_frameLatencyWaitable = nullptr;
    g_flipModelSwap = false;
    SafeRelease(g_swap);
    SafeRelease(g_ctx);
    SafeRelease(g_dev);
    g_deviceAdapterLuid = {};
    g_hasDeviceAdapterLuid = false;
}

static HRESULT CreateRenderTargetAndViewport(UINT width, UINT height) {
    if (!g_swap || !g_dev || !g_ctx || width == 0 || height == 0) return E_INVALIDARG;
    SafeRelease(g_rtv);
    ID3D11Texture2D* backBuffer = nullptr;
    HRESULT hr = g_swap->GetBuffer(
        0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&backBuffer));
    if (FAILED(hr)) return hr;
    hr = g_dev->CreateRenderTargetView(backBuffer, nullptr, &g_rtv);
    backBuffer->Release();
    if (FAILED(hr)) return hr;
    g_renderWidth = width;
    g_renderHeight = height;
    SetRenderViewport();
    return S_OK;
}

static HRESULT CreateRendererResources() {
    ID3DBlob* vsB = CompileShader(VS_SRC, "main", "vs_5_0");
    ID3DBlob* psB = CompileShader(PS_SRC, "main", "ps_5_0");
    ID3DBlob* normalizeB = CompileShader(NORMALIZE_PS_SRC, "main", "ps_5_0");
    if (!vsB || !psB || !normalizeB) {
        SafeRelease(vsB);
        SafeRelease(psB);
        SafeRelease(normalizeB);
        return E_FAIL;
    }
    HRESULT hr = g_dev->CreateVertexShader(
        vsB->GetBufferPointer(), vsB->GetBufferSize(), nullptr, &g_vs);
    if (SUCCEEDED(hr)) {
        hr = g_dev->CreatePixelShader(
            psB->GetBufferPointer(), psB->GetBufferSize(), nullptr, &g_ps);
    }
    if (SUCCEEDED(hr)) {
        hr = g_dev->CreatePixelShader(
            normalizeB->GetBufferPointer(), normalizeB->GetBufferSize(), nullptr, &g_normalizePs);
    }
    SafeRelease(vsB);
    SafeRelease(psB);
    SafeRelease(normalizeB);
    if (FAILED(hr)) return hr;

    D3D11_BUFFER_DESC cbd = {};
    cbd.ByteWidth = sizeof(CBuf);
    cbd.Usage = D3D11_USAGE_DYNAMIC;
    cbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    cbd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    hr = g_dev->CreateBuffer(&cbd, nullptr, &g_cb);
    if (FAILED(hr)) return hr;

    D3D11_BUFFER_DESC normalizeCbd = {};
    normalizeCbd.ByteWidth = sizeof(NormalizeCB);
    normalizeCbd.Usage = D3D11_USAGE_DEFAULT;
    normalizeCbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    hr = g_dev->CreateBuffer(&normalizeCbd, nullptr, &g_normalizeCb);
    if (FAILED(hr)) return hr;

    D3D11_SAMPLER_DESC sceneSd = {};
    sceneSd.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sceneSd.AddressU = sceneSd.AddressV = sceneSd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    hr = g_dev->CreateSamplerState(&sceneSd, &g_sceneSmp);
    if (FAILED(hr)) return hr;

    D3D11_SAMPLER_DESC depthSd = sceneSd;
    depthSd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    hr = g_dev->CreateSamplerState(&depthSd, &g_depthSmp);
    if (FAILED(hr)) return hr;

    if (!CreateFallbackDepthSrv()) return E_FAIL;
    InitGpuTiming();
    return S_OK;
}

static HRESULT CreateDeviceAndRenderer() {
    IDXGIAdapter1* selectedAdapter = OpenAdapterForLuid(g_binding.adapterLuid);
    if (!selectedAdapter) return DXGI_ERROR_NOT_FOUND;

    RECT clientRect = {};
    if (!g_hwnd || !GetClientRect(g_hwnd, &clientRect)) {
        selectedAdapter->Release();
        return HRESULT_FROM_WIN32(GetLastError());
    }
    const UINT clientWidth = static_cast<UINT>(clientRect.right - clientRect.left);
    const UINT clientHeight = static_cast<UINT>(clientRect.bottom - clientRect.top);
    if (clientWidth == 0 || clientHeight == 0) {
        selectedAdapter->Release();
        return E_INVALIDARG;
    }

    DXGI_SWAP_CHAIN_DESC scd = {};
    scd.BufferCount = 2;
    scd.BufferDesc.Width = clientWidth;
    scd.BufferDesc.Height = clientHeight;
    scd.BufferDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    scd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    scd.OutputWindow = g_hwnd;
    scd.SampleDesc.Count = 1;
    scd.Windowed = TRUE;
    scd.SwapEffect = DXGI_SWAP_EFFECT_FLIP_DISCARD;
    scd.Flags = DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT;

    D3D_FEATURE_LEVEL featureLevel = {};
    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        selectedAdapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
        &scd, &g_swap, &g_dev, &featureLevel, &g_ctx);
    LogHR("D3D11CreateDeviceAndSwapChain(flip waitable)", hr);
    g_flipModelSwap = SUCCEEDED(hr);
    if (FAILED(hr)) {
        SafeRelease(g_swap); SafeRelease(g_ctx); SafeRelease(g_dev);
        scd.BufferCount = 1;
        scd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;
        scd.Flags = 0;
        hr = D3D11CreateDeviceAndSwapChain(
            selectedAdapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr,
            D3D11_CREATE_DEVICE_BGRA_SUPPORT, nullptr, 0, D3D11_SDK_VERSION,
            &scd, &g_swap, &g_dev, &featureLevel, &g_ctx);
        LogHR("D3D11CreateDeviceAndSwapChain(legacy fallback)", hr);
        g_flipModelSwap = false;
    }
    selectedAdapter->Release();
    if (FAILED(hr)) return hr;

    IDXGISwapChain2* swap2 = nullptr;
    if (SUCCEEDED(g_swap->QueryInterface(__uuidof(IDXGISwapChain2),
                                         reinterpret_cast<void**>(&swap2)))) {
        if (SUCCEEDED(swap2->SetMaximumFrameLatency(1))) {
            g_frameLatencyWaitable = swap2->GetFrameLatencyWaitableObject();
        }
        swap2->Release();
    }
    Log("Swap pacing: flip=%d waitable=%p", g_flipModelSwap ? 1 : 0, g_frameLatencyWaitable);

    // Keep the transparent desktop overlay from queuing multiple full-resolution
    // frames ahead of the game.  The default DXGI latency is three frames, which
    // can monopolize the GPU on high-refresh ultrawide displays.
    IDXGIDevice1* dxgiDevice = nullptr;
    if (SUCCEEDED(g_dev->QueryInterface(__uuidof(IDXGIDevice1),
                                        reinterpret_cast<void**>(&dxgiDevice)))) {
        const HRESULT latencyHr = dxgiDevice->SetMaximumFrameLatency(1);
        LogHR("IDXGIDevice1::SetMaximumFrameLatency(1)", latencyHr);
        dxgiDevice->Release();
    }

    g_deviceAdapterLuid = g_binding.adapterLuid;
    g_hasDeviceAdapterLuid = true;
    hr = CreateRenderTargetAndViewport(clientWidth, clientHeight);
    if (SUCCEEDED(hr)) hr = CreateRendererResources();
    if (FAILED(hr)) DestroyDeviceResources();
    g_swapResizePending = false;
    return hr;
}

static HRESULT ResizeSwapChain(UINT width, UINT height) {
    if (!g_swap || !g_ctx || width == 0 || height == 0) return E_INVALIDARG;
    g_ctx->OMSetRenderTargets(0, nullptr, nullptr);
    g_ctx->ClearState();
    g_ctx->Flush();
    SafeRelease(g_rtv);
    const UINT flags = g_flipModelSwap ? DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT : 0;
    HRESULT hr = g_swap->ResizeBuffers(0, width, height, DXGI_FORMAT_UNKNOWN, flags);
    if (FAILED(hr)) return hr;
    return CreateRenderTargetAndViewport(width, height);
}

static void EnterDeviceRecovery(const char* operation, HRESULT hr, const char* reasonCode) {
    const HRESULT removedReason = g_dev ? g_dev->GetDeviceRemovedReason() : hr;
    Log("Device recovery: operation=%s hr=0x%08X reason=0x%08X",
        operation, static_cast<unsigned>(hr), static_cast<unsigned>(removedReason));
    DestroyDeviceResources();
    g_rebindRetry.Reset(GetTickCount64());
    SetCaptureState(CaptureState::DeviceRecovery, reasonCode);
}

static bool HandleDeviceResult(const char* operation, HRESULT hr) {
    if (SUCCEEDED(hr)) return true;
    const HRESULT removedReason = g_dev ? g_dev->GetDeviceRemovedReason() : hr;
    if (IsDeviceLoss(hr) || IsDeviceLoss(removedReason)) {
        EnterDeviceRecovery(operation, hr, "device_lost");
        return false;
    }
    LogHR(operation, hr);
    EnterDeviceRecovery(operation, hr, "renderer_failed");
    return false;
}

static bool Init(HINSTANCE hInst) {
    // Auto-detect physical screen size once; stored in g_auto* for reuse.
    float aw = 0, ah = 0;
    if (AutodetectScreenSizeCm(&aw, &ah)) {
        g_autoScreenW = aw;
        g_autoScreenH = ah;
    } else {
        g_autoScreenW = 59.8f;
        g_autoScreenH = 33.6f;
        Log("Autodetect: using hardcoded fallback %.2fx%.2f cm",
            g_autoScreenW, g_autoScreenH);
    }

    // Resolve effective values (CLI > GUI > autodetect) for the initial frame.
    ApplySettings();

    Log("Init: begin. screenW=%.2fcm screenH=%.2fcm strength=%.3f depth=%.2fcm",
        g_screenW, g_screenH, g_strength, g_virtualDepth);

    // Shared memory: try once now, retry each frame if tracker / GUI isn't up yet.
    TryAttachShm();
    TryAttachPoseSequence();
    TryAttachTrackerState();
    TryAttachSettings();
    Log("SHM initial attach: pose=%s pose_seq=%s state=%s settings=%s",
        g_shmView ? "ATTACHED" : "(tracker not running?)",
        g_seqView ? "ATTACHED" : "(legacy snapshot fallback)",
        g_stateView ? "ATTACHED" : "(legacy tracker fallback)",
        g_setView ? "ATTACHED" : "(settings GUI not running — OK)");

    WNDCLASSEXW wc = { sizeof(wc) };
    wc.lpfnWndProc = WndProc; wc.hInstance = hInst; wc.lpszClassName = L"G3DOverlay";
    ATOM cls = RegisterClassExW(&wc);
    Log("RegisterClassEx: atom=%u GLE=%lu", cls, cls ? 0 : GetLastError());

    int sw = GetSystemMetrics(SM_CXSCREEN);
    int sh = GetSystemMetrics(SM_CYSCREEN);
    int vw = GetSystemMetrics(SM_CXVIRTUALSCREEN);
    int vh = GetSystemMetrics(SM_CYVIRTUALSCREEN);
    int monitors = GetSystemMetrics(SM_CMONITORS);
    Log("Metrics: primary=%dx%d virtual=%dx%d monitors=%d", sw, sh, vw, vh, monitors);
    DetectTargetWindowRect();
    int overlayX = 0;
    int overlayY = 0;
    int overlayW = sw;
    int overlayH = sh;
    if (g_useTargetWindow) {
        overlayX = (int)g_targetRect.left;
        overlayY = (int)g_targetRect.top;
        overlayW = (int)(g_targetRect.right - g_targetRect.left);
        overlayH = (int)(g_targetRect.bottom - g_targetRect.top);
        Log("Target window active: configured game overlay=(%d,%d) %dx%d",
            overlayX, overlayY, overlayW, overlayH);
    } else if (!g_targetExePath.empty()) {
        Log("Target window not available yet: renderer will remain hidden and wait");
    } else {
        Log("Target window inactive: using full primary desktop capture");
    }

    // WS_EX_LAYERED + WS_EX_TRANSPARENT is the ONLY way to make a window
    // click-through across processes. HTTRANSPARENT only works same-thread.
    // Set styles AFTER CreateWindowExW too — more reliable across Windows versions.
    g_hwnd = CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        L"G3DOverlay", L"Glassless3D Overlay",
        WS_POPUP, overlayX, overlayY, overlayW, overlayH, nullptr, nullptr, hInst, nullptr);

    if (!g_hwnd) { FatalError(L"CreateWindowEx failed", (HRESULT)GetLastError()); return false; }
    MoveWindow(g_hwnd, overlayX, overlayY, overlayW, overlayH, FALSE);
    Log("CreateWindowEx: hwnd=%p pos=(%d,%d) size=%dx%d", g_hwnd, overlayX, overlayY, overlayW, overlayH);

    // Cross-process click-through requires WS_EX_LAYERED + WS_EX_TRANSPARENT together.
    // WS_EX_TRANSPARENT alone is *same-thread only* — other apps' clicks still get
    // captured. This is the formula used by Discord/OBS/MSI Afterburner overlays.
    LONG exStyle = GetWindowLongW(g_hwnd, GWL_EXSTYLE);
    Log("Pre-style: exStyle=0x%08lX", exStyle);
    SetWindowLongW(g_hwnd, GWL_EXSTYLE,
        exStyle | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW);
    Log("Post-style: exStyle=0x%08lX (LAYERED|TRANSPARENT|NOACTIVATE|TOOLWINDOW applied)",
        GetWindowLongW(g_hwnd, GWL_EXSTYLE));

    // Start fully transparent. The window is revealed only after the first
    // captured frame has been presented, preventing a full-screen black flash.
    BOOL lwa = SetLayeredWindowAttributes(g_hwnd, 0, 0, LWA_ALPHA);
    Log("SetLayeredWindowAttributes(alpha=0): ok=%d GLE=%lu", lwa ? 1 : 0, lwa ? 0 : GetLastError());

    // CRITICAL: hide our own window from DXGI capture so we don't capture ourselves
    BOOL wda = SetWindowDisplayAffinity(g_hwnd, WDA_EXCLUDEFROMCAPTURE);
    Log("SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE): ok=%d GLE=%lu", wda ? 1 : 0, wda ? 0 : GetLastError());

    ShowWindow(g_hwnd, SW_HIDE);
    g_overlayVisible = false;
    Log("Overlay window created hidden until a fresh target frame is ready");

    // Global hotkey: Ctrl+Shift+G to quit (works even when game has focus)
    BOOL hk = RegisterHotKey(g_hwnd, HOTKEY_QUIT, MOD_CONTROL | MOD_SHIFT, 'G');
    Log("RegisterHotKey(Ctrl+Shift+G): ok=%d GLE=%lu", hk ? 1 : 0, hk ? 0 : GetLastError());
    BOOL hkd = RegisterHotKey(g_hwnd, HOTKEY_DEBUG, MOD_CONTROL, 'D');
    Log("RegisterHotKey(Ctrl+D depth debug): ok=%d GLE=%lu", hkd ? 1 : 0, hkd ? 0 : GetLastError());
    BOOL hks = RegisterHotKey(g_hwnd, HOTKEY_SCREENSHOT, MOD_CONTROL | MOD_SHIFT, 'S');
    Log("RegisterHotKey(Ctrl+Shift+S screenshot): ok=%d GLE=%lu", hks ? 1 : 0, hks ? 0 : GetLastError());

    // System tray icon
    g_nid.cbSize           = sizeof(g_nid);
    g_nid.hWnd             = g_hwnd;
    g_nid.uID              = 1;
    g_nid.uFlags           = NIF_ICON | NIF_TIP | NIF_MESSAGE;
    g_nid.uCallbackMessage = WM_TRAYICON;
    g_nid.hIcon            = LoadIconW(nullptr, IDI_APPLICATION);
    wcscpy_s(g_nid.szTip, L"Glassless3D Overlay — right-click to quit");
    Shell_NotifyIconW(NIM_ADD, &g_nid);

    const BindingStatus initialBinding = RefreshCaptureBinding();
    if (initialBinding == BindingStatus::NoOutput) {
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "no_matching_output");
        return true;
    }
    SyncOverlayWindowToBinding();
    const HRESULT createHr = CreateDeviceAndRenderer();
    if (FAILED(createHr)) {
        LogHR("Initial CreateDeviceAndRenderer", createHr);
        g_rebindRetry.Reset(GetTickCount64());
        SetCaptureState(CaptureState::DeviceRecovery, "renderer_create_failed");
        return true;
    }
    g_rebindRetry.Reset(GetTickCount64());
    if (initialBinding == BindingStatus::TargetSpansOutput) {
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "target_spans_output");
    } else if (initialBinding == BindingStatus::TargetUnavailable) {
        g_bindingDirty = false;
        g_rebindRetry.RecordFailure(GetTickCount64());
        SetCaptureState(CaptureState::Unavailable, "target_not_running");
    } else {
        SetCaptureState(CaptureState::Rebinding, "startup");
    }
    return true;
}

static void Frame() {
    ScopedCpuTimer frameCpuTimer(&g_lastFrameCpuMs);
    static int frameCount = 0;
    static uint32_t lastShmTs = 0;
    static bool seenPose = false;
    static DWORD lastPoseChangeMs = 0;
    static int shmReads = 0, shmChanges = 0;
    static float filteredHx = 0.0f, filteredHy = 0.0f, filteredHz = 60.0f;
    static DWORD lastFilteredWallMs = 0;
    frameCount++;
    ResolveGpuTiming();

    // Lazy (re)connect to both shared-memory channels — either side may start late
    TryAttachShm();
    TryAttachPoseSequence();
    TryAttachTrackerState();
    TryAttachSettings();
    ApplySettings();   // CLI > GUI > autodetect → publishes to g_screenW, g_strength, etc.

    PollTargetWindow();
    if (g_captureState == CaptureState::Running && g_bindingDirty) {
        QueueCaptureSignal(CaptureSignal::BindingDirty, "binding_changed");
    }
    if (g_swapResizePending && g_swap) {
        const UINT width = g_pendingSwapWidth;
        const UINT height = g_pendingSwapHeight;
        g_swapResizePending = false;
        const HRESULT resizeHr = ResizeSwapChain(width, height);
        if (!HandleDeviceResult("ResizeBuffers", resizeHr)) return;
    }

    // Read head position from shared memory
    float hx = 0.f, hy = 0.f, hz = 60.f;
    uint32_t ts = 0;
    DWORD nowMs = GetTickCount();
    bool poseFresh = false;
    bool newPoseSample = false;
    if (g_shmView) {
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
    uint32_t trackerState = 1; // legacy tracker fallback: pose freshness is validity
    bool trackerStateFresh = true;
    if (g_stateView) {
        TrackerState stateSnapshot;
        if (!ReadStableSnapshot(g_stateView, &stateSnapshot)) return;
        trackerState = stateSnapshot.state;
        trackerStateTs = stateSnapshot.ts;
        trackerStateFresh = (nowMs - trackerStateTs) <= kPoseStaleMs;
        poseFresh = poseFresh && trackerStateFresh && trackerState == 1;
    }

    // One-Euro pre-filter on raw head pose (cm). The tracker already runs a
    // Kalman stage, but residual sub-cm jitter is multiplied by the parallax
    // shift magnitude and appears as visible "watery" ripple during motion.
    // One-Euro lets us kill that still-held jitter hard without adding lag
    // when the user actually moves (cutoff adapts with estimated velocity).
    if (poseFresh && newPoseSample) {
        // Use the producer timestamp and update each filter exactly once per
        // camera sample. Re-filtering a 10-30 Hz sample at 240 Hz makes the
        // response refresh-rate dependent and collapses motion toward rest.
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

        // Forward-prediction by the measured motion-to-photon latency. The
        // filter gives us a low-noise velocity estimate (`dx_prev`) — use it
        // to extrapolate where the head will be when the current frame lights
        // up on the panel.  Clamped so that rapid direction reversals cannot
        // over-shoot and create a counter-phase wobble.
        const float sampleAgeSec = std::min(0.050f, (nowMs - lastFilteredWallMs) / 1000.0f);
        const float predictSec = kPredictHorizonSec + sampleAgeSec;
        hx += ClampAbs(OneEuroPredictedVelocity(g_oeX) * predictSec, kPredictMaxDeltaCm);
        hy += ClampAbs(OneEuroPredictedVelocity(g_oeY) * predictSec, kPredictMaxDeltaCm);
        hz += ClampAbs(OneEuroPredictedVelocity(g_oeZ) * predictSec, kPredictMaxDeltaCm);
    }

    // Drift correction: the camera is rarely perfectly centered on the user,
    // so raw headX/headY can have a large static offset even at rest (10-20 cm
    // is common).  Feeding that raw offset to the shader permanently shifts the
    // parallax sample, making the overlay look like a "floating second screen."
    //
    // Fix: maintain an EMA of the user's "rest" position.  Parallax is applied
    // relative to that baseline, so the overlay is transparent when the user is
    // stationary and depth appears only when they move their head.
    //
    // Two-speed alpha:
    //   • First 300 frames (~5 s at 60 fps): alpha=0.05 → fast convergence, EMA
    //     reaches >99 % of rest position within ~2 seconds.
    //   • After 300 frames: alpha=0.001 → very slow long-term drift so the
    //     baseline adapts if the user repositions without ever restarting.
    static float g_emaX = 0.0f, g_emaY = 0.0f;
    static int   g_emaFrames = 0;
    static bool  g_emaInit = false;

    if (poseFresh && newPoseSample) {
        if (!g_emaInit) {
            g_emaX = hx; g_emaY = hy;
            g_emaInit = true;
        }
        const float alpha = (g_emaFrames < 300) ? 0.05f : 0.001f;
        g_emaX = alpha * hx + (1.0f - alpha) * g_emaX;
        g_emaY = alpha * hy + (1.0f - alpha) * g_emaY;
        g_emaFrames++;

        if (g_emaFrames == 300)
            Log("DriftEMA calibrated: restX=%.2f restY=%.2f (will now use relative head position)", g_emaX, g_emaY);
    } else if (g_stateView && (!trackerStateFresh || trackerState != 1)) {
        // Face loss is an explicit neutral/recenter boundary. Do not retain a
        // stale rest position and create a jump when the viewer returns.
        g_emaInit = false;
        g_emaFrames = 0;
    }

    // Relative displacement from rest — what the shader actually uses.
    float dx = g_emaInit ? (hx - g_emaX) : 0.0f;
    float dy = g_emaInit ? (hy - g_emaY) : 0.0f;

    // Soft hysteretic deadzone: below g_deadzoneCm output is 0, above 2·dz
    // output is the raw displacement, and between a smoothstep re-engages
    // parallax gradually. A hard "if < dz then 0" would snap by dz·parallax
    // at the threshold — visible as a pop when the user shifts slightly.
    dx = SoftDeadzone(dx, g_deadzoneCm);
    dy = SoftDeadzone(dy, g_deadzoneCm);
    ClampVectorLength(dx, dy, kMaxParallaxRelCm);
    if (!poseFresh) { dx = 0.0f; dy = 0.0f; }

    // Test-wobble disabled — rendering pipeline confirmed working.
    // Re-enable by setting TEST_WOBBLE=true to diagnose without tracker.
    const bool TEST_WOBBLE = false;
    float wobble = 0.f;
    if (TEST_WOBBLE) {
        double t = (double)GetTickCount() / 1000.0;
        wobble = 4.0f * (float)sin(t * 2.0);
        hx += wobble;
    }

    {
        ScopedCpuTimer captureCpuTimer(&g_lastCaptureCpuMs);
        TickCaptureRebind();
        UpdateCapture();
    }
    UpdateOverlayVisibility();

    // Periodic summary based on wall time. Capture recovery can intentionally
    // throttle the loop, so frame counts are not a reliable one-second clock.
    static int lastChanges = 0;
    static uint64_t lastInferences = 0;
    static uint64_t lastSummaryMs = GetTickCount64();
    const uint64_t summaryNowMs = GetTickCount64();
    const uint64_t summaryElapsedMs = summaryNowMs - lastSummaryMs;
    if (summaryElapsedMs >= 1000) {
        const int changesSinceSummary = shmChanges - lastChanges;
        const int changesThisSec = static_cast<int>(
            (static_cast<uint64_t>(changesSinceSummary) * 1000u) / summaryElapsedMs);
        lastChanges = shmChanges;
        uint64_t infNow = g_depth ? g_depth->inferences_completed() : 0;
        const uint64_t inferenceDelta = infNow >= lastInferences
            ? infNow - lastInferences
            : infNow;  // DepthInferencer was recreated during recovery.
        const int depthHz = static_cast<int>(
            (inferenceDelta * 1000u) / summaryElapsedMs);
        lastInferences = infNow;
        lastSummaryMs = summaryNowMs;
        const char* shmStatus;
        if (!g_shmView)                 shmStatus = "NO_SHM (tracker not running?)";
        else if (changesThisSec == 0)   shmStatus = "STALE (tracker running but not writing?)";
        else                            shmStatus = "LIVE";
        Log("Frame#%d acq[ok=%d timeout=%d lost=%d other=%d] shm[%s reads=%d changes=%d (%d/s) ts=%u] "
            "depth[total=%llu %dHz mode=%s] timing[capture_cpu=%.3f draw_gpu=%.3f present_cpu=%.3f frame_cpu=%.3f] backend=%u layout=%u eye_order=%u ipd=%.2f focus=%.2f panel=%ux%u tracking=%u "
            "head=(%.2f,%.2f,%.2f) rest=(%.2f,%.2f) rel=(%.2f,%.2f) wobble=%.2f strength=%.2f depth=%.2f "
            "hasFrame=%d capture=%s capture_reason=%s",
            frameCount, g_acquireOk, g_acquireTimeout, g_acquireLost, g_acquireOther,
            shmStatus, shmReads, shmChanges, changesThisSec, ts,
            (unsigned long long)infNow, depthHz, DepthModeName(g_depthMode),
            g_lastCaptureCpuMs, g_lastGpuMs, g_lastPresentCpuMs, g_lastFrameCpuMs, g_displayBackend,
            g_stereoLayout, g_eyeOrder, g_ipdCm, g_focusPlaneCm, g_panelWidthPx, g_panelHeightPx, g_trackingMode,
            hx - wobble, hy, hz, g_emaX, g_emaY, dx - wobble, dy, wobble, g_strength, g_virtualDepth,
            g_hasFrame ? 1 : 0, CaptureStateName(g_captureState), g_captureReason);
    }

    // Don't render until we have at least one real frame
    if (g_captureState != CaptureState::Running || !g_hasFrame) {
        if (frameCount == 1 || frameCount % 120 == 0)
            Log("Frame#%d: no captured frame yet, skipping render", frameCount);
        return;
    }
    if (!g_overlayVisible) return;

    // Event-driven rendering for WGC: draw immediately for new game/tracker
    // samples, and at up to 120 Hz while prediction or a depth crossfade is
    // visibly changing. A stationary unchanged frame is not redrawn at the
    // monitor's full refresh rate.
    static uint64_t lastRenderedCaptureSerial = 0;
    static uint32_t lastRenderedPoseTs = 0;
    static uint32_t lastRenderedStateTs = 0;
    static bool lastRenderedPoseFresh = false;
    static DWORD lastRenderMs = 0;
    const float vx = OneEuroPredictedVelocity(g_oeX);
    const float vy = OneEuroPredictedVelocity(g_oeY);
    const bool motionActive = poseFresh && (std::fabs(vx) + std::fabs(vy) > 0.05f);
    const bool blendActive = g_depth && g_depth->depth_blend() < 0.999f;
    const bool newActivity = g_captureFrameSerial != lastRenderedCaptureSerial
        || ts != lastRenderedPoseTs
        || trackerStateTs != lastRenderedStateTs
        || poseFresh != lastRenderedPoseFresh
        || g_presentRetryPending
        || g_wantScreenshot;
    const bool animationDue = (motionActive || blendActive) && (nowMs - lastRenderMs >= 8);
    if (g_captureBackend == CaptureBackend::WindowsGraphicsCapture
        && !newActivity && !animationDue) {
        return;
    }
    lastRenderedCaptureSerial = g_captureFrameSerial;
    lastRenderedPoseTs = ts;
    lastRenderedStateTs = trackerStateTs;
    lastRenderedPoseFresh = poseFresh;
    lastRenderMs = nowMs;

    // Update constant buffer — use relative (dx, dy) so the parallax is zero
    // at the user's rest position and only responds to head movement.
    // hz stays absolute: the physics formula needs the real eye-to-screen distance.
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    HRESULT hr = g_ctx->Map(g_cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped);
    if (FAILED(hr)) {
        HandleDeviceResult("Map(CBuf)", hr);
        return;
    }
    float cropX0    = g_depth ? g_depth->depth_crop_x0_uv() : 0.0f;
    float cropW     = g_depth ? g_depth->depth_crop_w_uv()  : 1.0f;
    float depthBlend = g_depth ? g_depth->depth_blend() : 1.0f;
    CBuf cb = {
        dx, dy, hz,
        g_strengthX, g_strengthY, g_screenW, g_screenH, g_virtualDepth,
        (float)g_debugDepthMode,
        g_depthGamma, g_focusRadius, (float)g_depthCurve,
        cropX0, cropW, depthBlend, (float)g_displayBackend,
        g_ipdCm, (float)g_stereoLayout, (float)g_eyeOrder,
        g_focusPlaneCm,
    };
    memcpy(mapped.pData, &cb, sizeof(cb));
    g_ctx->Unmap(g_cb, 0);

    // Draw fullscreen quad
    g_ctx->OMSetRenderTargets(1, &g_rtv, nullptr);
    g_ctx->VSSetShader(g_vs, nullptr, 0);
    g_ctx->PSSetShader(g_ps, nullptr, 0);
    g_ctx->PSSetConstantBuffers(0, 1, &g_cb);
    // Bind scene (t0) + depth latest (t1) + depth previous (t2).
    // The shader lerps t1/t2 using depthBlend to hide the 10 Hz inference
    // rate. Fallback SRV is used at both depth slots when depth is offline.
    ID3D11ShaderResourceView* depthSrv     = g_depth ? g_depth->depth_srv()      : g_fallbackSrv;
    ID3D11ShaderResourceView* depthPrevSrv = g_depth ? g_depth->depth_prev_srv() : g_fallbackSrv;
    ID3D11ShaderResourceView* srvs[3] = { g_srv, depthSrv, depthPrevSrv };
    g_ctx->PSSetShaderResources(0, 3, srvs);
    g_ctx->PSSetSamplers(0, 1, &g_sceneSmp);
    g_ctx->PSSetSamplers(1, 1, &g_depthSmp);
    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    BeginGpuTiming();
    g_ctx->Draw(4, 0);
    EndGpuTiming();

    // Screenshot: capture back buffer to PNG before Present() so the texture
    // is still valid.  Flag is set by Ctrl+Shift+S hotkey.
    if (g_wantScreenshot) {
        g_wantScreenshot = false;

        ID3D11Texture2D* bb = nullptr;
        if (SUCCEEDED(g_swap->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&bb))) {
            D3D11_TEXTURE2D_DESC desc = {};
            bb->GetDesc(&desc);
            desc.Usage          = D3D11_USAGE_STAGING;
            desc.BindFlags      = 0;
            desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
            desc.MiscFlags      = 0;

            ID3D11Texture2D* stg = nullptr;
            if (SUCCEEDED(g_dev->CreateTexture2D(&desc, nullptr, &stg))) {
                g_ctx->CopyResource(stg, bb);

                D3D11_MAPPED_SUBRESOURCE sm = {};
                if (SUCCEEDED(g_ctx->Map(stg, 0, D3D11_MAP_READ, 0, &sm))) {
                    // Build timestamped path next to the exe.
                    SYSTEMTIME t = {};
                    GetLocalTime(&t);
                    wchar_t exeDir[MAX_PATH]; GetModuleFileNameW(nullptr, exeDir, MAX_PATH);
                    wchar_t* sl = wcsrchr(exeDir, L'\\');
                    if (sl) *(sl + 1) = L'\0';
                    wchar_t wpath[MAX_PATH];
                    swprintf_s(wpath, MAX_PATH,
                        L"%sscreenshot_%04d%02d%02d_%02d%02d%02d%s.png",
                        exeDir,
                        t.wYear, t.wMonth, t.wDay,
                        t.wHour, t.wMinute, t.wSecond,
                        g_debugDepthMode > 0 ? L"_depth" : L"");
                    char path[MAX_PATH];
                    WideCharToMultiByte(CP_UTF8, 0, wpath, -1, path, MAX_PATH, nullptr, nullptr);

                    UINT W = desc.Width, H = desc.Height;

                    // Save PNG via WIC (Windows Imaging Component — no extra DLLs needed).
                    // The back buffer is BGRA which matches GUID_WICPixelFormat32bppBGRA.
                    bool saved = false;
                    IWICImagingFactory* wic = nullptr;
                    if (SUCCEEDED(CoCreateInstance(CLSID_WICImagingFactory, nullptr,
                                                   CLSCTX_INPROC_SERVER,
                                                   IID_IWICImagingFactory, (void**)&wic))) {
                        IWICStream* wicStream = nullptr;
                        if (SUCCEEDED(wic->CreateStream(&wicStream)) &&
                            SUCCEEDED(wicStream->InitializeFromFilename(wpath, GENERIC_WRITE))) {
                            IWICBitmapEncoder* enc = nullptr;
                            if (SUCCEEDED(wic->CreateEncoder(GUID_ContainerFormatPng, nullptr, &enc)) &&
                                SUCCEEDED(enc->Initialize(wicStream, WICBitmapEncoderNoCache))) {
                                IWICBitmapFrameEncode* frame = nullptr;
                                IPropertyBag2* props = nullptr;
                                if (SUCCEEDED(enc->CreateNewFrame(&frame, &props))) {
                                    frame->Initialize(props);
                                    frame->SetSize(W, H);
                                    WICPixelFormatGUID fmt = GUID_WICPixelFormat32bppBGRA;
                                    frame->SetPixelFormat(&fmt);
                                    // WritePixels requires contiguous rows; copy if stride != W*4.
                                    UINT rowBytes = W * 4;
                                    if (sm.RowPitch == rowBytes) {
                                        frame->WritePixels(H, rowBytes,
                                                           rowBytes * H,
                                                           (BYTE*)sm.pData);
                                    } else {
                                        std::vector<uint8_t> packed(rowBytes * H);
                                        const uint8_t* src = (const uint8_t*)sm.pData;
                                        for (UINT r = 0; r < H; ++r)
                                            memcpy(packed.data() + r * rowBytes,
                                                   src + r * sm.RowPitch, rowBytes);
                                        frame->WritePixels(H, rowBytes,
                                                           rowBytes * H,
                                                           packed.data());
                                    }
                                    if (SUCCEEDED(frame->Commit()) &&
                                        SUCCEEDED(enc->Commit())) {
                                        saved = true;
                                    }
                                    if (props) props->Release();
                                    frame->Release();
                                }
                                enc->Release();
                            }
                            wicStream->Release();
                        }
                        wic->Release();
                    }
                    if (saved)
                        Log("Screenshot saved: %s (%ux%u)", path, W, H);
                    else
                        Log("Screenshot: WIC PNG save failed: %s", path);

                    g_ctx->Unmap(stg, 0);
                }
                stg->Release();
            }
            bb->Release();
        }
    }

    // The frame-latency waitable object paces the flip-model path. Never let
    // Present block the capture/render thread: when DWM is not ready, retain a
    // retry request and drop this presentation attempt instead of making the
    // game appear frozen behind a stale full-screen overlay.
    const auto presentStarted = std::chrono::steady_clock::now();
    const UINT syncInterval = g_flipModelSwap ? 0u : 1u;
    const UINT presentFlags = g_flipModelSwap ? DXGI_PRESENT_DO_NOT_WAIT : 0u;
    const HRESULT present_hr = g_swap->Present(syncInterval, presentFlags);
    g_lastPresentCpuMs = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - presentStarted).count();
    if (present_hr == DXGI_ERROR_WAS_STILL_DRAWING) {
        static uint64_t droppedPresents = 0;
        ++droppedPresents;
        g_presentRetryPending = true;
        if (droppedPresents <= 3 || droppedPresents % 120 == 0) {
            Log("Present skipped without blocking (queue busy), total=%llu",
                static_cast<unsigned long long>(droppedPresents));
        }
        return;
    }
    if (!HandleDeviceResult("Present", present_hr)) return;
    g_presentRetryPending = false;
    if (!g_overlayRevealed && g_hasFrame) {
        const BOOL revealed = SetLayeredWindowAttributes(g_hwnd, 0, 255, LWA_ALPHA);
        Log("First frame presented; reveal overlay: ok=%d GLE=%lu",
            revealed ? 1 : 0, revealed ? 0 : GetLastError());
        g_overlayRevealed = revealed != FALSE;
    }
}

static void Cleanup() {
    Shell_NotifyIconW(NIM_DELETE, &g_nid);
    UnregisterHotKey(g_hwnd, HOTKEY_QUIT);
    UnregisterHotKey(g_hwnd, HOTKEY_DEBUG);
    UnregisterHotKey(g_hwnd, HOTKEY_SCREENSHOT);
    if (g_shmView) UnmapViewOfFile((void*)g_shmView);
    if (g_shmH)    CloseHandle(g_shmH);
    if (g_seqView) UnmapViewOfFile((void*)g_seqView);
    if (g_seqH)    CloseHandle(g_seqH);
    if (g_stateView) UnmapViewOfFile((void*)g_stateView);
    if (g_stateH)    CloseHandle(g_stateH);
    if (g_setView) UnmapViewOfFile((void*)g_setView);
    if (g_setH)    CloseHandle(g_setH);
    DestroyDeviceResources();
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE, LPSTR cmd, int) {
    // Acquire singleton ownership before opening/truncating overlay.log. A stale
    // instance must not let a rejected replacement corrupt the active log or
    // display a focus-stealing modal over the game.
    HANDLE mutex = CreateMutexW(nullptr, TRUE, L"Global\\Glassless3DOverlay");
    const DWORD mutexError = GetLastError();
    if (mutex && mutexError == ERROR_ALREADY_EXISTS) {
        CloseHandle(mutex);
        return 2;
    }

    LogInit();
    Log("=== Glassless3D Overlay starting ===");
    Log("cmdline: '%s'", cmd ? cmd : "");
    Log("CreateMutex: handle=%p GLE=%lu", mutex, mutexError);
    EnablePerMonitorV2DpiAwareness();

    int wideArgc = 0;
    LPWSTR* wideArgv = CommandLineToArgvW(GetCommandLineW(), &wideArgc);
    if (wideArgv) {
        for (int i = 1; i + 1 < wideArgc; ++i) {
            if (wcscmp(wideArgv[i], L"--target-exe") == 0) {
                g_targetExePath = wideArgv[++i];
            } else if (wcscmp(wideArgv[i], L"--target-pid") == 0) {
                wchar_t* end = nullptr;
                const unsigned long parsed = wcstoul(wideArgv[++i], &end, 10);
                if (end && *end == L'\0' && parsed > 0) {
                    g_targetPid = static_cast<DWORD>(parsed);
                }
            }
        }
        LocalFree(wideArgv);
    }
    if (!g_targetExePath.empty()) {
        Log("Configured target executable: %ls pid=%lu",
            g_targetExePath.c_str(), static_cast<unsigned long>(g_targetPid));
    }

    // WIC (used for Ctrl+Shift+S PNG screenshots) requires COM on this thread.
    HRESULT hrCo = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    Log("CoInitializeEx -> HRESULT=0x%08X", (unsigned)hrCo);

    // Args: width_cm  height_cm  strength  virtual_depth_cm
    // 0 or negative = leave default / trigger autodetect / allow GUI override.
    float w = 0, h = 0, s = 0, d = -1;
    int n = sscanf(cmd, "%f %f %f %f", &w, &h, &s, &d);
    if (n >= 1 && w > 0)  g_cliScreenW  = w;
    if (n >= 2 && h > 0)  g_cliScreenH  = h;
    if (n >= 3 && s > 0)  g_cliStrength = s;
    if (n >= 4 && d >= 0) g_cliDepth    = d;
    Log("Args parsed (n=%d): cliW=%.2f cliH=%.2f cliStrength=%.3f cliDepth=%.2f (0/-1 = defer to GUI/autodetect)",
        n, g_cliScreenW, g_cliScreenH, g_cliStrength, g_cliDepth);

    if (!Init(hInst)) {
        Log("Init FAILED, cleaning up and exiting");
        Cleanup();
        if (mutex) CloseHandle(mutex);
        if (SUCCEEDED(hrCo)) CoUninitialize();
        LogClose();
        return 1;
    }
    Log("Init complete, entering message loop");

    MSG msg = {};
    int loopIters = 0;
    while (g_running) {
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
            if (msg.message == WM_QUIT) { Log("Main loop: WM_QUIT"); g_running = false; }
        }
        if (g_frameLatencyWaitable && g_captureState == CaptureState::Running) {
            MsgWaitForMultipleObjectsEx(1, &g_frameLatencyWaitable, 16,
                QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        }
        Frame();
        const DWORD idleWaitMs = CaptureIdleWaitMs();
        if (idleWaitMs > 0) {
            MsgWaitForMultipleObjectsEx(
                0, nullptr, idleWaitMs, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        }
        loopIters++;
    }
    Log("Exiting main loop after %d iterations", loopIters);
    Cleanup();
    Log("Cleanup done");
    if (mutex) CloseHandle(mutex);
    if (SUCCEEDED(hrCo)) CoUninitialize();
    LogClose();
    return 0;
}
