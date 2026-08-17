// addon/Glassless3D.cpp
// Glassless3D ReShade Addon
//
// Reads head pose from FT_SharedMem (FreeTrack / OpenTrack protocol)
// and injects it into Glassless3D.fx uniforms before each frame's effects.
//
// The tracker (tracker/main.py or OpenTrack) must be running to provide data.
// When it is not, uniforms default to (0, 0, 60) — a neutral no-op.

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <Windows.h>
#include <cstring>
#include <cstdint>
#include <cmath>

#include <reshade.hpp>

// ── FreeTrack / OpenTrack shared memory layout ────────────────────────────
// Matches opentrack fttypes.h FTData (first 36 bytes are all we need).
#pragma pack(push, 1)
struct FTData {
    uint32_t DataID;     // Sequence number; changes on each tracker write
    int32_t  CamWidth;
    int32_t  CamHeight;
    float    Yaw, Pitch, Roll;
    float    X;          // cm, right = positive
    float    Y;          // cm, up = positive
    float    Z;          // cm, distance from screen
};
#pragma pack(pop)

static constexpr const wchar_t* kMapName  = L"FT_SharedMem";
static constexpr float           kDefaultZ = 60.0f;

static HANDLE s_hMap  = NULL;
static LPVOID s_pView = NULL;
static uint32_t s_lastDataId = 0;
static DWORD s_lastChangeMs = 0;
static bool s_registered = false;

// Open the shared memory mapping lazily (tracker may start after the game).
static bool TryOpenSharedMemory()
{
    if (s_pView) return true;
    s_hMap = OpenFileMappingW(FILE_MAP_READ, FALSE, kMapName);
    if (!s_hMap) return false;
    s_pView = MapViewOfFile(s_hMap, FILE_MAP_READ, 0, 0, sizeof(FTData));
    if (!s_pView) { CloseHandle(s_hMap); s_hMap = NULL; return false; }
    return true;
}

static void CloseSharedMemory()
{
    if (s_pView) { UnmapViewOfFile(s_pView); s_pView = NULL; }
    if (s_hMap)  { CloseHandle(s_hMap);       s_hMap  = NULL; }
}

static FTData ReadHeadData()
{
    FTData d = {0, 0, 0, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, kDefaultZ};
    if (!TryOpenSharedMemory() || !s_pView)
        return d;

    FTData first{}, second{};
    std::memcpy(&first, s_pView, sizeof(FTData));
    std::memcpy(&second, s_pView, sizeof(FTData));
    if (first.DataID != second.DataID)
        return d;

    const DWORD now = GetTickCount();
    if (second.DataID != s_lastDataId)
    {
        s_lastDataId = second.DataID;
        s_lastChangeMs = now;
    }
    if (second.DataID == 0 || s_lastChangeMs == 0 || now - s_lastChangeMs > 500 ||
        !std::isfinite(second.X) || !std::isfinite(second.Y) || !std::isfinite(second.Z) ||
        second.Z < 10.0f || second.Z > 300.0f)
        return d;
    d = second;
    return d;
}

// ── ReShade event: fires just before effects each frame ───────────────────
static void on_begin_effects(
    reshade::api::effect_runtime* runtime,
    reshade::api::command_list*,
    reshade::api::resource_view,
    reshade::api::resource_view)
{
    const FTData d = ReadHeadData();

    auto set = [&](const char* name, float value) {
        const auto var = runtime->find_uniform_variable("Glassless3D.fx", name);
        if (var != reshade::api::effect_uniform_variable{0})
            runtime->set_uniform_value_float(var, &value, 1);
    };

    set("g3d_HeadX", d.X);
    set("g3d_HeadY", d.Y);
    set("g3d_HeadZ", d.Z);
}

// ── DLL entry point ───────────────────────────────────────────────────────
BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID)
{
    switch (reason)
    {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(module);
        if (!reshade::register_addon(module))
            return FALSE;
        s_registered = true;
        reshade::register_event<reshade::addon_event::reshade_begin_effects>(&on_begin_effects);
        break;
    case DLL_PROCESS_DETACH:
        if (s_registered)
        {
            reshade::unregister_event<reshade::addon_event::reshade_begin_effects>(&on_begin_effects);
            reshade::unregister_addon(module);
            s_registered = false;
        }
        CloseSharedMemory();
        break;
    }
    return TRUE;
}
