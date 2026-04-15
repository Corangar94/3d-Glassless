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
#include <d3d11.h>
#include <dxgi1_2.h>
#include <d3dcompiler.h>
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cmath>
#include <ctime>
#include <cstdarg>
#include <share.h>
#include <string>

#include "depth_infer.h"

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

static void LogClose() {
    if (g_log) { Log("=== log closed ==="); fclose(g_log); g_log = nullptr; }
}

// WDA_EXCLUDEFROMCAPTURE – hide our window from DXGI / screen recorders
// (Windows 10 2004+). Prevents the black self-capture feedback loop.
#ifndef WDA_EXCLUDEFROMCAPTURE
#define WDA_EXCLUDEFROMCAPTURE 0x00000011
#endif

// ── Shared-memory layout (must match tracker/shared_memory.py) ────────────
static const wchar_t* SHM_NAME      = L"G3D";            // head pose (tracker -> us)
static const wchar_t* SHM_SETTINGS  = L"G3D_Settings";   // live tuning (settings GUI -> us)
#pragma pack(push, 1)
struct HeadPose { float x, y, z; uint32_t ts; };
// Must match tracker/shared_settings.py STRUCT_FORMAT = "<fffffIfffffffI" (56 bytes)
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
    uint32_t  version;
};  // 56 bytes
#pragma pack(pop)

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
    float headX;        // cm, right = positive
    float headY;        // cm, up    = positive
    float headZ;        // cm, distance from screen (~60 typical)
    float strengthX;    // horizontal parallax amplifier
    float strengthY;    // vertical parallax amplifier
    float screenW;      // cm
    float screenH;      // cm
    float virtualDepth; // cm, total depth budget
    float debugDepth;   // >0.5 = greyscale depth map
    float depthGamma;   // gamma exponent (curve mode 2)
    float focusRadius;  // UV radius for focus ring taps
    float depthCurve;   // 0=linear, 1=sqrt, 2=gamma
};
Texture2D    SceneTex : register(t0);
Texture2D    DepthTex : register(t1);
SamplerState SceneSmp : register(s0);
struct PS_IN { float4 pos : SV_Position; float2 uv : TEXCOORD; };

float ApplyCurve(float rawD, float curve, float gamma) {
    if (curve < 0.5) return rawD;
    if (curve < 1.5) return sqrt(rawD);
    return pow(max(rawD, 0.0001), gamma);
}

float4 main(PS_IN i) : SV_Target {
    float rawD = DepthTex.Sample(SceneSmp, i.uv).r;
    float depth = ApplyCurve(rawD, depthCurve, depthGamma);

    if (debugDepth > 0.5) {
        float v = 1.0 - depth;
        return float4(v, v, v, 1.0);
    }

    float hz = max(headZ, 20.0);
    float sw = max(screenW, 1.0);
    float sh = max(screenH, 1.0);
    float vd = max(virtualDepth, 0.0);
    float r  = max(focusRadius, 0.001);

    float2 c = float2(0.5, 0.5);
    float fr =
        ApplyCurve(DepthTex.Sample(SceneSmp, c               ).r, depthCurve, depthGamma) * 0.40 +
        ApplyCurve(DepthTex.Sample(SceneSmp, c + float2(-r, 0)).r, depthCurve, depthGamma) * 0.15 +
        ApplyCurve(DepthTex.Sample(SceneSmp, c + float2( r, 0)).r, depthCurve, depthGamma) * 0.15 +
        ApplyCurve(DepthTex.Sample(SceneSmp, c + float2(0, -r)).r, depthCurve, depthGamma) * 0.15 +
        ApplyCurve(DepthTex.Sample(SceneSmp, c + float2(0,  r)).r, depthCurve, depthGamma) * 0.15;

    float depthDelta = depth - fr;

    float2 sampleUV = float2(
        i.uv.x + (headX / hz) * depthDelta * vd / sw * strengthX,
        i.uv.y - (headY / hz) * depthDelta * vd / sh * strengthY
    );
    return SceneTex.Sample(SceneSmp, saturate(sampleUV));
}
)hlsl";

struct CBuf {
    float headX, headY, headZ, strengthX, strengthY;
    float screenW, screenH, virtualDepth, debugDepth;
    float depthGamma, focusRadius, depthCurve;
};

// ── Globals ───────────────────────────────────────────────────────────────
static HWND                      g_hwnd    = nullptr;
static ID3D11Device*             g_dev     = nullptr;
static ID3D11DeviceContext*      g_ctx     = nullptr;
static IDXGISwapChain*           g_swap    = nullptr;
static ID3D11RenderTargetView*   g_rtv     = nullptr;
static ID3D11VertexShader*       g_vs      = nullptr;
static ID3D11PixelShader*        g_ps      = nullptr;
static ID3D11Buffer*             g_cb      = nullptr;
static ID3D11SamplerState*       g_smp     = nullptr;
static IDXGIOutputDuplication*   g_dup     = nullptr;
static ID3D11Texture2D*          g_capTex  = nullptr;
static ID3D11ShaderResourceView* g_srv     = nullptr;
static HANDLE                    g_shmH    = nullptr;
static const void*               g_shmView = nullptr;
// Monocular depth inferencer (Depth Anything V2 Small via ONNX Runtime + DirectML).
// When null / failed to init, the overlay falls back to a uniform 0.5 depth texture
// so the parallax math still runs (effectively the old flat-plane behavior).
static DepthInferencer*          g_depth       = nullptr;
static ID3D11Texture2D*          g_fallbackTex = nullptr;  // 1x1 R16F=0.5 used when g_depth is null
static ID3D11ShaderResourceView* g_fallbackSrv = nullptr;
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
static bool  g_debugDepth   = false;  // Ctrl+D: show depth map as greyscale
// CLI-provided overrides (0 = not provided → use autodetect / settings GUI)
static float g_cliScreenW   = 0.0f;
static float g_cliScreenH   = 0.0f;
static float g_cliStrength  = 0.0f;
static float g_cliDepth     = -1.0f;  // -1 = not provided
static bool  g_running        = true;
static bool  g_hasFrame       = false;  // true once we have at least one captured frame
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
    if (msg == WM_DESTROY) { Log("WndProc: WM_DESTROY"); g_running = false; PostQuitMessage(0); }
    if (msg == WM_HOTKEY && wp == HOTKEY_QUIT)  { Log("WndProc: WM_HOTKEY quit"); g_running = false; PostQuitMessage(0); }
    if (msg == WM_HOTKEY && wp == HOTKEY_DEBUG)      { g_debugDepth = !g_debugDepth; Log("WndProc: debug depth %s", g_debugDepth ? "ON" : "OFF"); }
    if (msg == WM_HOTKEY && wp == HOTKEY_SCREENSHOT) { g_wantScreenshot = true; Log("WndProc: screenshot queued"); }
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

static bool InitDuplication() {
    Log("InitDuplication: begin");
    IDXGIDevice*  dxgiDev = nullptr;
    IDXGIAdapter* adapter = nullptr;
    IDXGIOutput*  output  = nullptr;
    IDXGIOutput1* out1    = nullptr;

    HRESULT hr;
    hr = g_dev->QueryInterface(__uuidof(IDXGIDevice),  (void**)&dxgiDev);
    LogHR("QueryInterface(IDXGIDevice)", hr);
    hr = dxgiDev->GetParent   (__uuidof(IDXGIAdapter), (void**)&adapter);
    LogHR("GetParent(IDXGIAdapter)", hr);

    // Log adapter description
    DXGI_ADAPTER_DESC adesc = {};
    if (adapter && SUCCEEDED(adapter->GetDesc(&adesc))) {
        char name[128] = {};
        WideCharToMultiByte(CP_UTF8, 0, adesc.Description, -1, name, sizeof(name), nullptr, nullptr);
        Log("Adapter: %s VRAM=%lluMB", name, (unsigned long long)adesc.DedicatedVideoMemory / (1024 * 1024));
    }

    // Enumerate all outputs and log them
    for (UINT i = 0; ; i++) {
        IDXGIOutput* o = nullptr;
        if (FAILED(adapter->EnumOutputs(i, &o))) break;
        DXGI_OUTPUT_DESC od = {};
        o->GetDesc(&od);
        char dn[64] = {};
        WideCharToMultiByte(CP_UTF8, 0, od.DeviceName, -1, dn, sizeof(dn), nullptr, nullptr);
        Log("Output[%u]: %s rect=(%ld,%ld)-(%ld,%ld) attached=%d rotation=%d",
            i, dn, od.DesktopCoordinates.left, od.DesktopCoordinates.top,
            od.DesktopCoordinates.right, od.DesktopCoordinates.bottom,
            od.AttachedToDesktop ? 1 : 0, (int)od.Rotation);
        o->Release();
    }

    hr = adapter->EnumOutputs(0, &output);
    LogHR("EnumOutputs(0)", hr);
    hr = output->QueryInterface(__uuidof(IDXGIOutput1), (void**)&out1);
    LogHR("QueryInterface(IDXGIOutput1)", hr);

    hr = out1->DuplicateOutput(g_dev, &g_dup);
    LogHR("DuplicateOutput", hr);
    out1->Release(); output->Release(); adapter->Release(); dxgiDev->Release();

    if (FAILED(hr)) {
        FatalError(
            L"DuplicateOutput failed.\n\n"
            L"Make sure:\n"
            L"  - The game is in Windowed Fullscreen mode (not exclusive fullscreen)\n"
            L"  - You are NOT running over Remote Desktop\n"
            L"  - Only one instance of this overlay is running", hr);
        return false;
    }

    DXGI_OUTDUPL_DESC dd = {};
    g_dup->GetDesc(&dd);
    Log("Duplication: mode=%ux%u format=%u rotation=%u desktopImageInSystemMemory=%d",
        dd.ModeDesc.Width, dd.ModeDesc.Height, (unsigned)dd.ModeDesc.Format,
        (unsigned)dd.Rotation, dd.DesktopImageInSystemMemory ? 1 : 0);

    D3D11_TEXTURE2D_DESC td = {};
    td.Width = dd.ModeDesc.Width; td.Height = dd.ModeDesc.Height;
    td.MipLevels = 1; td.ArraySize = 1;
    td.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_DEFAULT;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    hr = g_dev->CreateTexture2D(&td, nullptr, &g_capTex);
    if (FAILED(hr)) { FatalError(L"CreateTexture2D (capture) failed", hr); return false; }

    D3D11_SHADER_RESOURCE_VIEW_DESC sd = {};
    sd.Format = td.Format;
    sd.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
    sd.Texture2D.MipLevels = 1;
    hr = g_dev->CreateShaderResourceView(g_capTex, &sd, &g_srv);
    LogHR("CreateShaderResourceView", hr);
    if (FAILED(hr)) { FatalError(L"CreateShaderResourceView failed", hr); return false; }

    Log("InitDuplication: complete");
    return true;
}

static void ResetDuplication() {
    g_hasFrame = false;
    if (g_srv)   { g_srv->Release();   g_srv   = nullptr; }
    if (g_capTex){ g_capTex->Release();g_capTex= nullptr; }
    if (g_dup)   { g_dup->Release();   g_dup   = nullptr; }
    Sleep(300);
    InitDuplication();
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
    float dg = 1.0f, fr = 0.1f;

    if (g_setView) {
        Settings s; memcpy(&s, g_setView, sizeof(s));
        if (s.strengthX    > 0.0f)    sx = s.strengthX;
        if (s.strengthY    > 0.0f)    sy = s.strengthY;
        if (s.virtualDepthCm >= 0.0f) dp = s.virtualDepthCm;
        if (s.screenWCm    > 0.0f)    sw = s.screenWCm;
        if (s.screenHCm    > 0.0f)    sh = s.screenHCm;
        dc = s.depthCurve;
        if (s.depthGamma  > 0.0f)    dg = s.depthGamma;
        if (s.focusRadius >= 0.0f)    fr = s.focusRadius;
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
}

// Query the primary monitor's physical size via EDID.
// Writes cm into *outW/*outH. Returns true on success (both > 0).
// Two sources, in order of preference:
//   1. GetDeviceCaps(HORZSIZE / VERTSIZE) — mm from the display driver.
//      This reads EDID on modern Windows and works for the vast majority of
//      monitors; a small number report 0 or a generic 320x240mm fallback.
//   2. Fallback: DPI-based estimate — resolution / effective DPI * 2.54.
//      Less accurate but always returns something plausible.
static bool AutodetectScreenSizeCm(float* outW, float* outH) {
    HDC hdc = GetDC(nullptr);
    if (!hdc) return false;

    int wmm = GetDeviceCaps(hdc, HORZSIZE);
    int hmm = GetDeviceCaps(hdc, VERTSIZE);
    int pxW = GetDeviceCaps(hdc, HORZRES);
    int pxH = GetDeviceCaps(hdc, VERTRES);
    int dpiX = GetDeviceCaps(hdc, LOGPIXELSX);
    int dpiY = GetDeviceCaps(hdc, LOGPIXELSY);
    ReleaseDC(nullptr, hdc);

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

// Locate depth_anything_v2_small_fp16.onnx. Searches (in order):
//   1. <exe_dir>/models/depth_anything_v2_small_fp16.onnx
//   2. <exe_dir>/../models/depth_anything_v2_small_fp16.onnx   (dev layout)
// Returns empty string if not found.
static std::wstring FindDepthModel() {
    wchar_t exePath[MAX_PATH]; GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    wchar_t* slash = wcsrchr(exePath, L'\\');
    if (slash) *(slash + 1) = L'\0';
    const wchar_t* candidates[] = {
        L"models\\depth_anything_v2_small_fp16.onnx",
        L"..\\models\\depth_anything_v2_small_fp16.onnx",
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
    Log("InitDepth: depth inference online (capture %ux%u, model 518x518)",
        cd.Width, cd.Height);
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
    TryAttachSettings();
    Log("SHM initial attach: pose=%s settings=%s",
        g_shmView ? "ATTACHED" : "(tracker not running?)",
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

    // WS_EX_LAYERED + WS_EX_TRANSPARENT is the ONLY way to make a window
    // click-through across processes. HTTRANSPARENT only works same-thread.
    // Set styles AFTER CreateWindowExW too — more reliable across Windows versions.
    g_hwnd = CreateWindowExW(
        WS_EX_TOPMOST | WS_EX_NOACTIVATE,
        L"G3DOverlay", L"Glassless3D Overlay",
        WS_POPUP, 0, 0, sw, sh, nullptr, nullptr, hInst, nullptr);

    if (!g_hwnd) { FatalError(L"CreateWindowEx failed", (HRESULT)GetLastError()); return false; }
    Log("CreateWindowEx: hwnd=%p size=%dx%d", g_hwnd, sw, sh);

    // Cross-process click-through requires WS_EX_LAYERED + WS_EX_TRANSPARENT together.
    // WS_EX_TRANSPARENT alone is *same-thread only* — other apps' clicks still get
    // captured. This is the formula used by Discord/OBS/MSI Afterburner overlays.
    LONG exStyle = GetWindowLongW(g_hwnd, GWL_EXSTYLE);
    Log("Pre-style: exStyle=0x%08lX", exStyle);
    SetWindowLongW(g_hwnd, GWL_EXSTYLE, exStyle | WS_EX_LAYERED | WS_EX_TRANSPARENT);
    Log("Post-style: exStyle=0x%08lX (LAYERED|TRANSPARENT applied)",
        GetWindowLongW(g_hwnd, GWL_EXSTYLE));

    // Fully opaque — D3D swap chain content still renders through the redirection surface.
    BOOL lwa = SetLayeredWindowAttributes(g_hwnd, 0, 255, LWA_ALPHA);
    Log("SetLayeredWindowAttributes(alpha=255): ok=%d GLE=%lu", lwa ? 1 : 0, lwa ? 0 : GetLastError());

    // CRITICAL: hide our own window from DXGI capture so we don't capture ourselves
    BOOL wda = SetWindowDisplayAffinity(g_hwnd, WDA_EXCLUDEFROMCAPTURE);
    Log("SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE): ok=%d GLE=%lu", wda ? 1 : 0, wda ? 0 : GetLastError());

    ShowWindow(g_hwnd, SW_SHOWNOACTIVATE);
    Log("ShowWindow called");

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

    // D3D11 + swap chain
    // Use DXGI_SWAP_EFFECT_DISCARD (BitBlt model) — compatible with all window styles.
    // FLIP_DISCARD fails with certain window flag combinations on some drivers.
    DXGI_SWAP_CHAIN_DESC scd = {};
    scd.BufferCount = 1;
    scd.BufferDesc.Width  = (UINT)sw;
    scd.BufferDesc.Height = (UINT)sh;
    scd.BufferDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    scd.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    scd.OutputWindow = g_hwnd;
    scd.SampleDesc.Count = 1;
    scd.Windowed  = TRUE;
    scd.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    D3D_FEATURE_LEVEL fl;
    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr, 0,
        nullptr, 0, D3D11_SDK_VERSION, &scd, &g_swap, &g_dev, &fl, &g_ctx);
    LogHR("D3D11CreateDeviceAndSwapChain", hr);
    Log("D3D feature level: 0x%04X (11_0=0xb000 11_1=0xb100)", (unsigned)fl);
    if (FAILED(hr)) {
        FatalError(L"D3D11CreateDeviceAndSwapChain failed.\n\nEnsure your GPU drivers are up to date.", hr);
        return false;
    }

    ID3D11Texture2D* bb = nullptr;
    hr = g_swap->GetBuffer(0, __uuidof(ID3D11Texture2D), (void**)&bb);
    LogHR("SwapChain GetBuffer", hr);
    if (FAILED(hr)) { FatalError(L"GetBuffer failed", hr); return false; }
    hr = g_dev->CreateRenderTargetView(bb, nullptr, &g_rtv);
    LogHR("CreateRenderTargetView", hr);
    bb->Release();
    if (FAILED(hr)) { FatalError(L"CreateRenderTargetView failed", hr); return false; }

    // Shaders
    ID3DBlob* vsB = CompileShader(VS_SRC, "main", "vs_5_0");
    ID3DBlob* psB = CompileShader(PS_SRC, "main", "ps_5_0");
    if (!vsB || !psB) return false;
    g_dev->CreateVertexShader(vsB->GetBufferPointer(), vsB->GetBufferSize(), nullptr, &g_vs);
    g_dev->CreatePixelShader (psB->GetBufferPointer(), psB->GetBufferSize(), nullptr, &g_ps);
    vsB->Release(); psB->Release();

    // Constant buffer
    D3D11_BUFFER_DESC cbd = {};
    cbd.ByteWidth = sizeof(CBuf); cbd.Usage = D3D11_USAGE_DYNAMIC;
    cbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER; cbd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    g_dev->CreateBuffer(&cbd, nullptr, &g_cb);

    // Sampler
    D3D11_SAMPLER_DESC sd2 = {};
    sd2.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd2.AddressU = sd2.AddressV = sd2.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    g_dev->CreateSamplerState(&sd2, &g_smp);

    // Viewport
    D3D11_VIEWPORT vp = { 0, 0, (float)sw, (float)sh, 0, 1 };
    g_ctx->RSSetViewports(1, &vp);

    if (!InitDuplication()) return false;

    // Bring up depth first as a 1x1 fallback so the pixel shader always has
    // a valid t1 SRV, then try to upgrade to the real DepthInferencer.
    if (!CreateFallbackDepthSrv()) {
        Log("Warning: fallback depth SRV creation failed — depth slot will be null");
    }
    InitDepth();   // best-effort; g_depth stays null on failure

    return true;
}

static void Frame() {
    static int frameCount = 0;
    static int acquireOk = 0, acquireTimeout = 0, acquireLost = 0, acquireOther = 0;
    static uint32_t lastShmTs = 0;
    static int shmReads = 0, shmChanges = 0;
    frameCount++;

    // Lazy (re)connect to both shared-memory channels — either side may start late
    TryAttachShm();
    TryAttachSettings();
    ApplySettings();   // CLI > GUI > autodetect → publishes to g_screenW, g_strength, etc.

    // Read head position from shared memory
    float hx = 0.f, hy = 0.f, hz = 60.f;
    uint32_t ts = 0;
    if (g_shmView) {
        HeadPose p; memcpy(&p, g_shmView, sizeof(p));
        hx = p.x; hy = p.y; hz = p.z; ts = p.ts;
        shmReads++;
        if (ts != lastShmTs) { shmChanges++; lastShmTs = ts; }
    }

    // Test-wobble disabled — rendering pipeline confirmed working.
    // Re-enable by setting TEST_WOBBLE=true to diagnose without tracker.
    const bool TEST_WOBBLE = false;
    float wobble = 0.f;
    if (TEST_WOBBLE) {
        double t = (double)GetTickCount() / 1000.0;
        wobble = 4.0f * (float)sin(t * 2.0);
        hx += wobble;
    }

    // Acquire new desktop frame (16 ms timeout)
    DXGI_OUTDUPL_FRAME_INFO fi = {};
    IDXGIResource* res = nullptr;
    HRESULT hr = g_dup->AcquireNextFrame(16, &fi, &res);

    if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_INVALID_CALL) {
        acquireLost++;
        Log("AcquireNextFrame: ACCESS_LOST/INVALID_CALL (0x%08X), resetting", (unsigned)hr);
        ResetDuplication(); return;
    }
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        acquireTimeout++;
    } else if (SUCCEEDED(hr) && res) {
        acquireOk++;
        ID3D11Texture2D* src = nullptr;
        if (SUCCEEDED(res->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&src))) {
            g_ctx->CopyResource(g_capTex, src);
            src->Release();
            g_hasFrame = true;

            // Phase 1: synchronous per-frame depth inference. Non-fatal on
            // failure — previous depth texture remains bound, so the overlay
            // keeps rendering (possibly with a stale depth map).
            if (g_depth) {
                if (!g_depth->run(g_capTex)) {
                    static int depthFails = 0;
                    if (++depthFails < 5 || depthFails % 120 == 0)
                        Log("DepthInferencer::run failed (#%d): %s",
                            depthFails, g_depth->last_error());
                }
            }
        }
        res->Release();
        g_dup->ReleaseFrame();
    } else {
        acquireOther++;
        if (acquireOther < 5 || acquireOther % 60 == 0)
            Log("AcquireNextFrame: unexpected HRESULT=0x%08X (count=%d)", (unsigned)hr, acquireOther);
    }

    // Periodic summary — once per second at ~60fps
    static int lastChanges = 0;
    static uint64_t lastInferences = 0;
    if (frameCount % 60 == 0) {
        int changesThisSec = shmChanges - lastChanges;
        lastChanges = shmChanges;
        uint64_t infNow = g_depth ? g_depth->inferences_completed() : 0;
        int depthHz = (int)(infNow - lastInferences);
        lastInferences = infNow;
        const char* shmStatus;
        if (!g_shmView)                 shmStatus = "NO_SHM (tracker not running?)";
        else if (changesThisSec == 0)   shmStatus = "STALE (tracker running but not writing?)";
        else                            shmStatus = "LIVE";
        Log("Frame#%d acq[ok=%d timeout=%d lost=%d other=%d] shm[%s reads=%d changes=%d (%d/s) ts=%u] "
            "depth[total=%llu %dHz] head=(%.2f,%.2f,%.2f) wobble=%.2f strength=%.2f depth=%.2f hasFrame=%d",
            frameCount, acquireOk, acquireTimeout, acquireLost, acquireOther,
            shmStatus, shmReads, shmChanges, changesThisSec, ts,
            (unsigned long long)infNow, depthHz,
            hx - wobble, hy, hz, wobble, g_strength, g_virtualDepth, g_hasFrame ? 1 : 0);
    }

    // Don't render until we have at least one real frame
    if (!g_hasFrame) {
        if (frameCount == 1 || frameCount % 120 == 0)
            Log("Frame#%d: no captured frame yet, skipping render", frameCount);
        return;
    }

    // Update constant buffer
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    g_ctx->Map(g_cb, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped);
    CBuf cb = {
        hx, hy, hz,
        g_strengthX, g_strengthY, g_screenW, g_screenH, g_virtualDepth,
        g_debugDepth ? 1.0f : 0.0f,
        g_depthGamma, g_focusRadius, (float)g_depthCurve,
    };
    memcpy(mapped.pData, &cb, sizeof(cb));
    g_ctx->Unmap(g_cb, 0);

    // Draw fullscreen quad
    g_ctx->OMSetRenderTargets(1, &g_rtv, nullptr);
    g_ctx->VSSetShader(g_vs, nullptr, 0);
    g_ctx->PSSetShader(g_ps, nullptr, 0);
    g_ctx->PSSetConstantBuffers(0, 1, &g_cb);
    // Bind scene (t0) + depth (t1). Depth comes from the real inferencer when
    // it's online, else the 1x1 zero-depth fallback so the shader sampling at
    // t1 is always safe.
    ID3D11ShaderResourceView* depthSrv =
        (g_depth ? g_depth->depth_srv() : g_fallbackSrv);
    ID3D11ShaderResourceView* srvs[2] = { g_srv, depthSrv };
    g_ctx->PSSetShaderResources(0, 2, srvs);
    g_ctx->PSSetSamplers(0, 1, &g_smp);
    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    g_ctx->Draw(4, 0);

    // Screenshot: capture back buffer to BMP before Present() so the texture
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
                        L"%sscreenshot_%04d%02d%02d_%02d%02d%02d%s.bmp",
                        exeDir,
                        t.wYear, t.wMonth, t.wDay,
                        t.wHour, t.wMinute, t.wSecond,
                        g_debugDepth ? L"_depth" : L"");
                    char path[MAX_PATH];
                    WideCharToMultiByte(CP_UTF8, 0, wpath, -1, path, MAX_PATH, nullptr, nullptr);

                    UINT W = desc.Width, H = desc.Height;
                    UINT rowBytes  = W * 4;          // BGRA, 4 bytes/px
                    UINT imgBytes  = rowBytes * H;

                    BITMAPFILEHEADER fh = {};
                    fh.bfType    = 0x4D42;           // 'BM'
                    fh.bfOffBits = sizeof(BITMAPFILEHEADER) + sizeof(BITMAPINFOHEADER);
                    fh.bfSize    = fh.bfOffBits + imgBytes;

                    BITMAPINFOHEADER ih = {};
                    ih.biSize        = sizeof(BITMAPINFOHEADER);
                    ih.biWidth       = (LONG)W;
                    ih.biHeight      = -(LONG)H;     // negative → top-down rows
                    ih.biPlanes      = 1;
                    ih.biBitCount    = 32;
                    ih.biCompression = BI_RGB;
                    ih.biSizeImage   = imgBytes;

                    FILE* f = fopen(path, "wb");
                    if (f) {
                        fwrite(&fh, sizeof(fh), 1, f);
                        fwrite(&ih, sizeof(ih), 1, f);
                        const uint8_t* row = (const uint8_t*)sm.pData;
                        for (UINT r = 0; r < H; ++r, row += sm.RowPitch)
                            fwrite(row, rowBytes, 1, f);
                        fclose(f);
                        Log("Screenshot saved: %s (%ux%u)", path, W, H);
                    } else {
                        Log("Screenshot: fopen failed: %s", path);
                    }

                    g_ctx->Unmap(stg, 0);
                }
                stg->Release();
            }
            bb->Release();
        }
    }

    g_swap->Present(0, 0);
}

static void Cleanup() {
    Shell_NotifyIconW(NIM_DELETE, &g_nid);
    UnregisterHotKey(g_hwnd, HOTKEY_QUIT);
    UnregisterHotKey(g_hwnd, HOTKEY_DEBUG);
    UnregisterHotKey(g_hwnd, HOTKEY_SCREENSHOT);
    if (g_shmView) UnmapViewOfFile((void*)g_shmView);
    if (g_shmH)    CloseHandle(g_shmH);
    if (g_setView) UnmapViewOfFile((void*)g_setView);
    if (g_setH)    CloseHandle(g_setH);
    // Depth resources: destroy inferencer BEFORE the D3D device that owns its
    // staging/depth textures and ORT session.
    if (g_depth)       { delete g_depth; g_depth = nullptr; }
    if (g_fallbackSrv) { g_fallbackSrv->Release(); g_fallbackSrv = nullptr; }
    if (g_fallbackTex) { g_fallbackTex->Release(); g_fallbackTex = nullptr; }
    if (g_srv)     g_srv->Release();
    if (g_capTex)  g_capTex->Release();
    if (g_dup)     g_dup->Release();
    if (g_smp)     g_smp->Release();
    if (g_cb)      g_cb->Release();
    if (g_ps)      g_ps->Release();
    if (g_vs)      g_vs->Release();
    if (g_rtv)     g_rtv->Release();
    if (g_swap)    g_swap->Release();
    if (g_ctx)     g_ctx->Release();
    if (g_dev)     g_dev->Release();
}

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE, LPSTR cmd, int) {
    LogInit();
    Log("=== Glassless3D Overlay starting ===");
    Log("cmdline: '%s'", cmd ? cmd : "");

    // Single-instance guard — DuplicateOutput allows only one holder per output
    HANDLE mutex = CreateMutexW(nullptr, TRUE, L"Global\\Glassless3DOverlay");
    DWORD gle = GetLastError();
    Log("CreateMutex: handle=%p GLE=%lu (%s)", mutex, gle,
        gle == ERROR_ALREADY_EXISTS ? "ALREADY_EXISTS" : "new owner");
    if (gle == ERROR_ALREADY_EXISTS) {
        Log("Exiting: another overlay instance is already running");
        MessageBoxW(nullptr,
            L"Glassless3D Overlay is already running.\n\nCheck the system tray.",
            L"Already Running", MB_OK | MB_ICONINFORMATION);
        if (mutex) CloseHandle(mutex);
        LogClose();
        return 0;
    }

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
        Cleanup(); if (mutex) CloseHandle(mutex); LogClose(); return 1;
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
        Frame();
        loopIters++;
    }
    Log("Exiting main loop after %d iterations", loopIters);
    Cleanup();
    Log("Cleanup done");
    if (mutex) CloseHandle(mutex);
    LogClose();
    return 0;
}
