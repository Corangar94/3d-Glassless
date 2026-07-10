# Native Capture Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the standalone DXGI/D3D11 overlay survive monitor, DPI, desktop-duplication, and device-loss changes without unsafe copy regions, stale resource dereferences, or an in-process fallback.

**Architecture:** Put geometry, rotation, state-transition, and retry decisions in a small platform-neutral C++ module with native unit tests. Keep `overlay.cpp` responsible for Win32/DXGI/D3D binding and resource ownership: it captures a full selected output, normalizes it into an upright logical scene texture, and drives the tested recovery state machine. The launcher reads the overlay's explicit capture state so a deliberate unavailable condition is explained rather than repeatedly restarted.

**Tech Stack:** C++17, Win32, DXGI Output Duplication, D3D11, HLSL, CMake/CTest, pytest, PySide6.

---

## Scope and non-goals

- Change only the standalone desktop-capture overlay.
- Keep online-safe profiles non-injecting. Do not add injection, anti-cheat detection, protection workarounds, Windows Graphics Capture, or a cross-output compositor.
- Treat a target window that spans outputs as `unavailable`; hide the overlay and wait for a display/window configuration change.
- Preserve the last valid image only for `DXGI_ERROR_WAIT_TIMEOUT`. For loss, device recovery, or unavailable capture, hide the overlay instead of placing a stale opaque frame over the desktop.

## Working-tree safety

The workspace already contains unrelated modified and untracked files. Before every commit in this plan, run `git diff -- <named paths>` and stage only the exact paths listed in that task. Do not reset, restore, or stage unrelated work.

## File structure

| File | Responsibility |
| --- | --- |
| `overlay/capture_recovery.h` | Platform-neutral rectangle, rotation, capture-state, and bounded-retry interfaces. |
| `overlay/capture_recovery.cpp` | Deterministic implementations with no Win32, DXGI, or D3D dependencies. |
| `overlay/capture_recovery_tests.cpp` | Console/CTest unit tests for geometry, rotation, transitions, and backoff. |
| `overlay/CMakeLists.txt` | Registers the native unit-test executable with CTest. |
| `overlay/overlay.cpp` | Selects the target output, owns D3D resources, normalizes capture, and applies recovery. |
| `overlay/depth_infer.cpp` | Releases all current/previous depth resources and supports safe reinitialization. |
| `launcher/diagnostics.py` | Parses capture state from the overlay summary and turns reason codes into guidance. |
| `launcher/mainwindow.py` | Shows unavailable/recovering capture state without fighting native recovery. |
| `tests/test_overlay_backend_shader.py` | Updates the previous crop-source contract to the normalized-capture contract. |
| `tests/test_overlay_capture_resilience.py` | Guards required native recovery/source contracts. |
| `tests/test_diagnostics.py` | Verifies capture-state parsing and actionable diagnostics. |
| `tests/test_mainwindow.py` | Verifies unavailable capture does not cause a launcher restart loop. |
| `docs/TROUBLESHOOTING.md` | Documents the capture reason codes and safe next actions. |

## Resource ownership rules

1. `g_dup` owns only the output-duplication session.
2. `g_rawCapTex`/`g_rawSrv` receive a complete raw duplication surface.
3. `g_capTex`/`g_capRtv`/`g_srv` are an upright, target-sized scene texture used by depth and parallax rendering.
4. `g_depth` is destroyed before any capture texture or D3D device it can reference.
5. `DesktopFrameLease` is the sole holder that calls `ReleaseFrame`; a successful `AcquireNextFrame` is paired exactly once.
6. `Unavailable` and `DeviceRecovery` hide the overlay. `Running` shows it only after `g_hasFrame` becomes true.

## State behavior

| Signal | State/result | Visibility | Retry rule |
| --- | --- | --- | --- |
| `WAIT_TIMEOUT` | Remain `Running` | keep last valid image | none |
| `ACCESS_LOST` or duplication `INVALID_CALL` | `Rebinding` | hide | 250 ms, 500 ms, 1 s, then 2 s capped |
| protected, unavailable, unsupported, session-disconnected, or access denied | `Unavailable` | hide | only after a binding-dirty event |
| device removed/reset/hung | `DeviceRecovery` then `Rebinding` | hide | bounded device rebuild then capture rebind |
| target/display/DPI configuration change | `Rebinding` | hide | immediate first bind attempt |

### Task 1: Add a platform-neutral recovery module and native unit-test target

**Files:**
- Create: `overlay/capture_recovery.h`
- Create: `overlay/capture_recovery.cpp`
- Create: `overlay/capture_recovery_tests.cpp`
- Modify: `overlay/CMakeLists.txt:1-11`

- [ ] **Step 1: Write the failing native recovery tests.**

Create `overlay/capture_recovery_tests.cpp` with the complete console test program below. It uses return values instead of `assert`, so Release builds cannot silently remove its checks.

```cpp
#include "capture_recovery.h"

#include <iostream>

using g3d::capture::BuildUprightCaptureRegion;
using g3d::capture::CaptureSignal;
using g3d::capture::CaptureState;
using g3d::capture::Rect;
using g3d::capture::RetrySchedule;
using g3d::capture::Rotation;
using g3d::capture::UprightToRawUv;

#define CHECK(expression)                                                        \
    do {                                                                         \
        if (!(expression)) {                                                     \
            std::cerr << __FILE__ << ':' << __LINE__ << ": " #expression << '\n'; \
            return false;                                                        \
        }                                                                        \
    } while (false)

static bool TestFullOutputRegion() {
    const auto region = BuildUprightCaptureRegion(Rect{100, 50, 1100, 650}, std::nullopt);
    CHECK(region.has_value());
    CHECK(region->left == 0);
    CHECK(region->top == 0);
    CHECK(region->width == 1000);
    CHECK(region->height == 600);
    return true;
}

static bool TestContainedTargetRegion() {
    const auto region = BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{250, 150, 850, 550});
    CHECK(region.has_value());
    CHECK(region->left == 150);
    CHECK(region->top == 100);
    CHECK(region->width == 600);
    CHECK(region->height == 400);
    return true;
}

static bool TestOutOfOutputTargetIsRejected() {
    CHECK(!BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{90, 150, 850, 550}).has_value());
    CHECK(!BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{250, 150, 1150, 550}).has_value());
    CHECK(!BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{250, 150, 250, 550}).has_value());
    return true;
}

static bool TestRotationMapsUprightCornersToRawSurface() {
    const auto identity = UprightToRawUv(Rotation::Identity, {0.25f, 0.75f});
    CHECK(identity.u == 0.25f && identity.v == 0.75f);

    const auto rotate90 = UprightToRawUv(Rotation::Rotate90, {0.0f, 0.0f});
    CHECK(rotate90.u == 0.0f && rotate90.v == 1.0f);

    const auto rotate180 = UprightToRawUv(Rotation::Rotate180, {0.0f, 1.0f});
    CHECK(rotate180.u == 1.0f && rotate180.v == 0.0f);

    const auto rotate270 = UprightToRawUv(Rotation::Rotate270, {1.0f, 0.0f});
    CHECK(rotate270.u == 1.0f && rotate270.v == 1.0f);
    return true;
}

static bool TestTransitionsKeepTimeoutAndHideUnavailable() {
    const auto timeout = g3d::capture::AdvanceCaptureState(
        CaptureState::Running, CaptureSignal::FrameTimeout);
    CHECK(timeout.next_state == CaptureState::Running);
    CHECK(timeout.keep_last_frame);
    CHECK(!timeout.hide_overlay);
    CHECK(!timeout.arm_retry);

    const auto lost = g3d::capture::AdvanceCaptureState(
        CaptureState::Running, CaptureSignal::DuplicationLost);
    CHECK(lost.next_state == CaptureState::Rebinding);
    CHECK(lost.hide_overlay);
    CHECK(lost.arm_retry);

    const auto unavailable = g3d::capture::AdvanceCaptureState(
        CaptureState::Running, CaptureSignal::DuplicationUnavailable);
    CHECK(unavailable.next_state == CaptureState::Unavailable);
    CHECK(unavailable.hide_overlay);
    CHECK(!unavailable.arm_retry);

    const auto dirty = g3d::capture::AdvanceCaptureState(
        CaptureState::Unavailable, CaptureSignal::BindingDirty);
    CHECK(dirty.next_state == CaptureState::Rebinding);
    CHECK(dirty.arm_retry);
    return true;
}

static bool TestRetryBackoffIsBoundedAndDeterministic() {
    RetrySchedule retry;
    retry.Reset(100);
    CHECK(retry.CanAttempt(100));

    retry.RecordFailure(100);
    CHECK(retry.failures() == 1);
    CHECK(!retry.CanAttempt(349));
    CHECK(retry.CanAttempt(350));

    retry.RecordFailure(350);
    CHECK(retry.next_attempt_ms() == 850);
    retry.RecordFailure(850);
    CHECK(retry.next_attempt_ms() == 1850);
    retry.RecordFailure(1850);
    CHECK(retry.next_attempt_ms() == 3850);
    retry.RecordFailure(3850);
    CHECK(retry.next_attempt_ms() == 5850);
    return true;
}

int main() {
    const bool ok = TestFullOutputRegion()
        && TestContainedTargetRegion()
        && TestOutOfOutputTargetIsRejected()
        && TestRotationMapsUprightCornersToRawSurface()
        && TestTransitionsKeepTimeoutAndHideUnavailable()
        && TestRetryBackoffIsBoundedAndDeterministic();
    std::cout << (ok ? "capture recovery tests passed\n" : "capture recovery tests failed\n");
    return ok ? 0 : 1;
}
```

- [ ] **Step 2: Register the missing test target.**

Insert this immediately after `project(Glassless3DOverlay CXX)` in `overlay/CMakeLists.txt`.

```cmake
include(CTest)

if(BUILD_TESTING)
    add_executable(capture_recovery_tests
        capture_recovery.cpp
        capture_recovery_tests.cpp
    )
    target_compile_features(capture_recovery_tests PRIVATE cxx_std_17)
    add_test(NAME capture_recovery_tests COMMAND capture_recovery_tests)
endif()
```

- [ ] **Step 3: Prove the test target fails before the helper exists.**

Run:

```powershell
& 'vendor\_mingw64\mingw64\bin\cmake.exe' -S overlay -B overlay/build_mingw -G 'MinGW Makefiles' -DCMAKE_BUILD_TYPE=Release
& 'vendor\_mingw64\mingw64\bin\cmake.exe' --build overlay/build_mingw --target capture_recovery_tests --config Release
```

Expected: the second command fails because `capture_recovery.cpp` and `capture_recovery.h` do not exist yet.

- [ ] **Step 4: Implement the deterministic helper API.**

Create `overlay/capture_recovery.h`.

```cpp
#pragma once

#include <cstdint>
#include <optional>

namespace g3d::capture {

struct Rect {
    int32_t left;
    int32_t top;
    int32_t right;
    int32_t bottom;
};

struct Region {
    uint32_t left;
    uint32_t top;
    uint32_t width;
    uint32_t height;
};

bool IsValidRect(const Rect& rect);
bool ContainsRect(const Rect& outer, const Rect& inner);
std::optional<Region> BuildUprightCaptureRegion(
    const Rect& output_rect,
    const std::optional<Rect>& target_rect);

enum class Rotation : uint8_t {
    Identity,
    Rotate90,
    Rotate180,
    Rotate270,
};

struct Uv {
    float u;
    float v;
};

Uv UprightToRawUv(Rotation rotation, Uv upright);

enum class CaptureState : uint8_t {
    Running,
    Rebinding,
    DeviceRecovery,
    Unavailable,
};

enum class CaptureSignal : uint8_t {
    FrameReady,
    FrameTimeout,
    DuplicationLost,
    DuplicationUnavailable,
    DeviceLost,
    DeviceRecreated,
    RebindSucceeded,
    RebindRetry,
    BindingDirty,
};

struct RecoveryAction {
    CaptureState next_state;
    bool keep_last_frame;
    bool hide_overlay;
    bool arm_retry;
    bool rebuild_device;
};

RecoveryAction AdvanceCaptureState(CaptureState state, CaptureSignal signal);

class RetrySchedule {
public:
    void Reset(uint64_t now_ms);
    bool CanAttempt(uint64_t now_ms) const;
    void RecordFailure(uint64_t now_ms);
    uint32_t failures() const;
    uint64_t next_attempt_ms() const;

private:
    uint32_t failures_ = 0;
    uint64_t next_attempt_ms_ = 0;
};

}  // namespace g3d::capture
```

Create `overlay/capture_recovery.cpp`.

```cpp
#include "capture_recovery.h"

#include <algorithm>
#include <limits>

namespace g3d::capture {

bool IsValidRect(const Rect& rect) {
    return rect.right > rect.left && rect.bottom > rect.top;
}

bool ContainsRect(const Rect& outer, const Rect& inner) {
    return IsValidRect(outer) && IsValidRect(inner)
        && inner.left >= outer.left
        && inner.top >= outer.top
        && inner.right <= outer.right
        && inner.bottom <= outer.bottom;
}

std::optional<Region> BuildUprightCaptureRegion(
    const Rect& output_rect,
    const std::optional<Rect>& target_rect) {
    if (!IsValidRect(output_rect)) return std::nullopt;

    const Rect selected = target_rect.value_or(output_rect);
    if (!ContainsRect(output_rect, selected)) return std::nullopt;

    const int64_t width = static_cast<int64_t>(selected.right) - selected.left;
    const int64_t height = static_cast<int64_t>(selected.bottom) - selected.top;
    const int64_t left = static_cast<int64_t>(selected.left) - output_rect.left;
    const int64_t top = static_cast<int64_t>(selected.top) - output_rect.top;
    if (left < 0 || top < 0 || width <= 0 || height <= 0
        || width > std::numeric_limits<uint32_t>::max()
        || height > std::numeric_limits<uint32_t>::max()) {
        return std::nullopt;
    }
    return Region{
        static_cast<uint32_t>(left),
        static_cast<uint32_t>(top),
        static_cast<uint32_t>(width),
        static_cast<uint32_t>(height),
    };
}

Uv UprightToRawUv(Rotation rotation, Uv upright) {
    switch (rotation) {
    case Rotation::Identity:  return upright;
    case Rotation::Rotate90:  return Uv{upright.v, 1.0f - upright.u};
    case Rotation::Rotate180: return Uv{1.0f - upright.u, 1.0f - upright.v};
    case Rotation::Rotate270: return Uv{1.0f - upright.v, upright.u};
    }
    return upright;
}

RecoveryAction AdvanceCaptureState(CaptureState state, CaptureSignal signal) {
    switch (signal) {
    case CaptureSignal::FrameReady:
        return {CaptureState::Running, true, false, false, false};
    case CaptureSignal::FrameTimeout:
        return {state, true, state != CaptureState::Running, false, false};
    case CaptureSignal::DuplicationLost:
    case CaptureSignal::RebindRetry:
        return {CaptureState::Rebinding, false, true, true, false};
    case CaptureSignal::DuplicationUnavailable:
        return {CaptureState::Unavailable, false, true, false, false};
    case CaptureSignal::DeviceLost:
        return {CaptureState::DeviceRecovery, false, true, true, true};
    case CaptureSignal::DeviceRecreated:
        return {CaptureState::Rebinding, false, true, true, false};
    case CaptureSignal::RebindSucceeded:
        return {CaptureState::Running, false, false, false, false};
    case CaptureSignal::BindingDirty:
        return {CaptureState::Rebinding, false, true, true, false};
    }
    return {state, state == CaptureState::Running, state != CaptureState::Running, false, false};
}

void RetrySchedule::Reset(uint64_t now_ms) {
    failures_ = 0;
    next_attempt_ms_ = now_ms;
}

bool RetrySchedule::CanAttempt(uint64_t now_ms) const {
    return now_ms >= next_attempt_ms_;
}

void RetrySchedule::RecordFailure(uint64_t now_ms) {
    static constexpr uint32_t kDelaysMs[] = {250, 500, 1000, 2000};
    const uint32_t index = std::min<uint32_t>(failures_, 3);
    ++failures_;
    next_attempt_ms_ = now_ms + kDelaysMs[index];
}

uint32_t RetrySchedule::failures() const {
    return failures_;
}

uint64_t RetrySchedule::next_attempt_ms() const {
    return next_attempt_ms_;
}

}  // namespace g3d::capture
```

- [ ] **Step 5: Run the native helper test and the existing Python crop contract.**

Run:

```powershell
& 'vendor\_mingw64\mingw64\bin\cmake.exe' --build overlay/build_mingw --target capture_recovery_tests --config Release
ctest --test-dir overlay/build_mingw -R '^capture_recovery_tests$' --output-on-failure
pytest tests/test_overlay_backend_shader.py -q
```

Expected: CTest reports `1/1 Test #1: capture_recovery_tests ... Passed`; the existing Python contract remains green because production capture code is unchanged in this task.

- [ ] **Step 6: Commit the tested helper boundary.**

```powershell
git add overlay/CMakeLists.txt overlay/capture_recovery.h overlay/capture_recovery.cpp overlay/capture_recovery_tests.cpp
git commit -m "feat: add capture recovery state helpers"
```

### Task 2: Bind capture to the selected monitor and normalize raw output safely

**Files:**
- Modify: `overlay/overlay.cpp:12-40, 363-396, 615-856, 1100-1274`
- Modify: `tests/test_overlay_backend_shader.py:106-113`
- Create: `tests/test_overlay_capture_resilience.py`

- [ ] **Step 1: Write source-contract tests for monitor selection and normalization.**

Create `tests/test_overlay_capture_resilience.py`.

```python
from pathlib import Path


OVERLAY = Path("overlay/overlay.cpp")


def test_overlay_selects_the_output_matching_the_target_monitor():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "MonitorFromWindow" in source
    assert "desc.Monitor == monitor" in source
    assert "D3D_DRIVER_TYPE_UNKNOWN" in source
    assert "adapter->EnumOutputs(0, &output)" not in source


def test_overlay_normalizes_full_output_before_depth_and_parallax():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "g_rawCapTex" in source
    assert "g_capRtv" in source
    assert "NormalizeCapturedFrame" in source
    assert "CopyResource(g_rawCapTex, source)" in source
    assert "BuildUprightCaptureRegion" in source
    assert "TargetWindowToDuplicationBox" not in source
    assert "CopySubresourceRegion(g_capTex" not in source


def test_overlay_has_explicit_rotation_mapping_for_all_dxgi_rotations():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "DXGI_MODE_ROTATION_ROTATE90" in source
    assert "DXGI_MODE_ROTATION_ROTATE180" in source
    assert "DXGI_MODE_ROTATION_ROTATE270" in source
    assert "NormalizeCB" in source
```

Replace `test_overlay_crops_desktop_duplication_to_target_window_when_available` in `tests/test_overlay_backend_shader.py` with this test.

```python
def test_overlay_normalizes_desktop_duplication_to_the_target_window_when_available():
    source = Path("overlay/overlay.cpp").read_text(encoding="utf-8")

    assert "FindWindowW(nullptr, L\"World of Warcraft\")" in source
    assert "BuildUprightCaptureRegion" in source
    assert "NormalizeCapturedFrame" in source
    assert "CopyResource(g_rawCapTex, source)" in source
    assert "CopySubresourceRegion(g_capTex" not in source
```

- [ ] **Step 2: Run the new contracts to establish the red state.**

Run:

```powershell
pytest tests/test_overlay_capture_resilience.py tests/test_overlay_backend_shader.py -q
```

Expected: the new tests fail because the overlay still selects output zero and uses `TargetWindowToDuplicationBox`.

- [ ] **Step 3: Add explicit binding and target-monitor selection.**

Add `#include <optional>` and `#include "capture_recovery.h"` after the existing standard-library includes in `overlay/overlay.cpp`. Replace the capture globals with this extension; retain existing globals not shown here.

```cpp
struct CaptureBinding {
    LUID adapter_luid = {};
    HMONITOR monitor = nullptr;
    wchar_t device_name[32] = {};
    RECT desktop_rect = {};
    DXGI_MODE_ROTATION rotation = DXGI_MODE_ROTATION_IDENTITY;
    g3d::capture::Region region = {};
};

enum class BindingStatus {
    Ready,
    NoOutput,
    TargetSpansOutput,
};

static HWND                      g_targetWindow = nullptr;
static CaptureBinding            g_binding = {};
static bool                      g_bindingDirty = true;
static ID3D11Texture2D*          g_rawCapTex = nullptr;
static ID3D11ShaderResourceView* g_rawSrv = nullptr;
static ID3D11RenderTargetView*   g_capRtv = nullptr;
static ID3D11PixelShader*        g_normalizePs = nullptr;
static ID3D11Buffer*             g_normalizeCb = nullptr;

template <typename T>
static void SafeRelease(T*& value) {
    if (value) {
        value->Release();
        value = nullptr;
    }
}

static void ReleaseCaptureTextures() {
    SafeRelease(g_capRtv);
    SafeRelease(g_srv);
    SafeRelease(g_capTex);
    SafeRelease(g_rawSrv);
    SafeRelease(g_rawCapTex);
    g_captureW = 0;
    g_captureH = 0;
}
```

Use these helpers directly below `DetectTargetWindowRect`. They make target coordinates explicit, enumerate only attached outputs, and preserve the adapter LUID for device creation.

```cpp
static g3d::capture::Rect ToCaptureRect(const RECT& rect) {
    return {rect.left, rect.top, rect.right, rect.bottom};
}

static bool SameLuid(const LUID& left, const LUID& right) {
    return left.HighPart == right.HighPart && left.LowPart == right.LowPart;
}

static HMONITOR DesiredCaptureMonitor() {
    if (g_useTargetWindow && g_targetWindow) {
        return MonitorFromWindow(g_targetWindow, MONITOR_DEFAULTTONULL);
    }
    return MonitorFromWindow(g_hwnd, MONITOR_DEFAULTTOPRIMARY);
}

static bool DetectTargetWindowRect() {
    HWND target = FindWowWindow();
    RECT next_rect = {};
    bool valid = target && IsWindowVisible(target);
    if (valid) {
        RECT client = {};
        POINT top_left = {};
        POINT bottom_right = {};
        valid = GetClientRect(target, &client);
        if (valid) {
            top_left.x = client.left;
            top_left.y = client.top;
            bottom_right.x = client.right;
            bottom_right.y = client.bottom;
            valid = ClientToScreen(target, &top_left)
                && ClientToScreen(target, &bottom_right);
        }
        if (valid) {
            next_rect = {top_left.x, top_left.y, bottom_right.x, bottom_right.y};
            valid = next_rect.right - next_rect.left >= 320
                && next_rect.bottom - next_rect.top >= 240;
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

    const bool changed = target != g_targetWindow || !EqualRect(&next_rect, &g_targetRect);
    g_targetWindow = target;
    g_targetRect = next_rect;
    g_useTargetWindow = true;
    if (changed) g_bindingDirty = true;
    return true;
}

static BindingStatus FindCaptureBinding(HMONITOR monitor, CaptureBinding* binding) {
    if (!monitor || !binding) return BindingStatus::NoOutput;

    IDXGIFactory1* factory = nullptr;
    HRESULT hr = CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory));
    if (FAILED(hr)) {
        LogHR("CreateDXGIFactory1", hr);
        return BindingStatus::NoOutput;
    }

    for (UINT adapter_index = 0;; ++adapter_index) {
        IDXGIAdapter1* adapter = nullptr;
        if (factory->EnumAdapters1(adapter_index, &adapter) == DXGI_ERROR_NOT_FOUND) break;
        if (!adapter) continue;
        DXGI_ADAPTER_DESC1 adapter_desc = {};
        const HRESULT adapter_hr = adapter->GetDesc1(&adapter_desc);
        if (SUCCEEDED(adapter_hr) && !(adapter_desc.Flags & DXGI_ADAPTER_FLAG_SOFTWARE)) {
            for (UINT output_index = 0;; ++output_index) {
                IDXGIOutput* output = nullptr;
                if (adapter->EnumOutputs(output_index, &output) == DXGI_ERROR_NOT_FOUND) break;
                if (!output) continue;
                DXGI_OUTPUT_DESC desc = {};
                const HRESULT desc_hr = output->GetDesc(&desc);
                output->Release();
                if (SUCCEEDED(desc_hr) && desc.AttachedToDesktop && desc.Monitor == monitor) {
                    binding->adapter_luid = adapter_desc.AdapterLuid;
                    binding->monitor = desc.Monitor;
                    wcsncpy_s(binding->device_name, _countof(binding->device_name),
                        desc.DeviceName, _TRUNCATE);
                    binding->desktop_rect = desc.DesktopCoordinates;
                    binding->rotation = desc.Rotation;
                    adapter->Release();
                    factory->Release();
                    return BindingStatus::Ready;
                }
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
        ToCaptureRect(next.desktop_rect), target);
    if (!region) return BindingStatus::TargetSpansOutput;

    next.region = *region;
    g_binding = next;
    g_bindingDirty = false;
    Log("Capture binding: output=%ls rect=(%ld,%ld)-(%ld,%ld) region=%u,%u %ux%u rotation=%u",
        g_binding.device_name,
        g_binding.desktop_rect.left, g_binding.desktop_rect.top,
        g_binding.desktop_rect.right, g_binding.desktop_rect.bottom,
        g_binding.region.left, g_binding.region.top,
        g_binding.region.width, g_binding.region.height,
        static_cast<unsigned>(g_binding.rotation));
    return BindingStatus::Ready;
}

static void SyncOverlayWindowToBinding() {
    if (!g_hwnd) return;
    const RECT rect = g_useTargetWindow ? g_targetRect : g_binding.desktop_rect;
    const LONG width = rect.right - rect.left;
    const LONG height = rect.bottom - rect.top;
    if (width <= 0 || height <= 0) return;
    SetWindowPos(g_hwnd, HWND_TOPMOST, rect.left, rect.top, width, height,
        SWP_NOACTIVATE | SWP_NOOWNERZORDER);
}

static IDXGIAdapter1* OpenAdapterForLuid(const LUID& luid) {
    IDXGIFactory1* factory = nullptr;
    if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1), reinterpret_cast<void**>(&factory)))) {
        return nullptr;
    }
    for (UINT index = 0;; ++index) {
        IDXGIAdapter1* adapter = nullptr;
        if (factory->EnumAdapters1(index, &adapter) == DXGI_ERROR_NOT_FOUND) break;
        if (!adapter) continue;
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
```

Update `DetectTargetWindowRect` so it sets `g_targetWindow` and marks `g_bindingDirty` whenever the target HWND or rectangle changes. On every failed target lookup, clear `g_targetWindow`, set `g_useTargetWindow = false`, and mark the binding dirty if a target had previously been active.

After `CreateWindowExW` succeeds and before the existing D3D creation block in `Init`, replace the default-adapter creation with this selected-adapter code. Task 3 changes the `return false` branches into a nonfatal state transition; this task first establishes correct same-adapter capture.

```cpp
const BindingStatus initial_binding = RefreshCaptureBinding();
if (initial_binding != BindingStatus::Ready) {
    Log("Initial capture binding unavailable: %d", static_cast<int>(initial_binding));
    return false;
}
IDXGIAdapter1* selected_adapter = OpenAdapterForLuid(g_binding.adapter_luid);
if (!selected_adapter) {
    Log("OpenAdapterForLuid failed for selected capture output");
    return false;
}

D3D_FEATURE_LEVEL feature_level = {};
const HRESULT device_hr = D3D11CreateDeviceAndSwapChain(
    selected_adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0,
    nullptr, 0, D3D11_SDK_VERSION, &scd,
    &g_swap, &g_dev, &feature_level, &g_ctx);
selected_adapter->Release();
LogHR("D3D11CreateDeviceAndSwapChain(selected adapter)", device_hr);
if (FAILED(device_hr)) return false;
```

- [ ] **Step 4: Replace raw crop copies with an upright normalization pass.**

Add this constant-buffer type and HLSL source next to the existing fullscreen shader source. `rotation` is the numeric `DXGI_MODE_ROTATION` value; identity and unspecified values take the identity branch.

```cpp
struct NormalizeCB {
    float crop_x;
    float crop_y;
    float crop_w;
    float crop_h;
    float rotation;
    float padding[3];
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
    float3 padding;
};
struct VS_OUT { float4 pos : SV_Position; float2 uv : TEXCOORD; };
float2 UprightToRaw(float2 uv) {
    if (rotation < 1.5) return uv;
    if (rotation < 2.5) return float2(uv.y, 1.0 - uv.x);
    if (rotation < 3.5) return float2(1.0 - uv.x, 1.0 - uv.y);
    return float2(1.0 - uv.y, uv.x);
}
float4 main(VS_OUT input) : SV_Target {
    const float2 upright = float2(cropX, cropY) + input.uv * float2(cropW, cropH);
    return RawFrame.SampleLevel(RawSampler, UprightToRaw(upright), 0.0);
}
)hlsl";
```

Replace `TargetWindowToDuplicationBox` and the old `CopySubresourceRegion` path with these two functions. `CreateCaptureTextures` creates one complete raw-output texture and one upright logical scene texture. It performs no raw-surface crop math.

```cpp
static bool CreateCaptureTextures(const DXGI_OUTDUPL_DESC& duplication_desc) {
    if (g_binding.region.width == 0 || g_binding.region.height == 0
        || duplication_desc.ModeDesc.Width == 0 || duplication_desc.ModeDesc.Height == 0) {
        Log("CreateCaptureTextures: empty capture dimensions");
        return false;
    }

    D3D11_TEXTURE2D_DESC raw = {};
    raw.Width = duplication_desc.ModeDesc.Width;
    raw.Height = duplication_desc.ModeDesc.Height;
    raw.MipLevels = 1;
    raw.ArraySize = 1;
    raw.Format = duplication_desc.ModeDesc.Format;
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

static void SetRenderViewport();

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

static bool NormalizeCapturedFrame(ID3D11Texture2D* source) {
    if (!source || !g_rawCapTex || !g_rawSrv || !g_capRtv || !g_normalizePs || !g_normalizeCb) {
        return false;
    }
    const LONG output_width = g_binding.desktop_rect.right - g_binding.desktop_rect.left;
    const LONG output_height = g_binding.desktop_rect.bottom - g_binding.desktop_rect.top;
    if (output_width <= 0 || output_height <= 0) return false;

    g_ctx->CopyResource(g_rawCapTex, source);
    const NormalizeCB constants = {
        static_cast<float>(g_binding.region.left) / output_width,
        static_cast<float>(g_binding.region.top) / output_height,
        static_cast<float>(g_binding.region.width) / output_width,
        static_cast<float>(g_binding.region.height) / output_height,
        NormalizeRotationValue(g_binding.rotation),
        {0.0f, 0.0f, 0.0f},
    };
    g_ctx->UpdateSubresource(g_normalizeCb, 0, nullptr, &constants, 0, 0);
    D3D11_VIEWPORT viewport = {0.0f, 0.0f, static_cast<float>(g_captureW), static_cast<float>(g_captureH), 0.0f, 1.0f};
    g_ctx->RSSetViewports(1, &viewport);
    g_ctx->OMSetRenderTargets(1, &g_capRtv, nullptr);
    g_ctx->VSSetShader(g_vs, nullptr, 0);
    g_ctx->PSSetShader(g_normalizePs, nullptr, 0);
    g_ctx->PSSetConstantBuffers(0, 1, &g_normalizeCb);
    g_ctx->PSSetShaderResources(0, 1, &g_rawSrv);
    g_ctx->PSSetSamplers(0, 1, &g_sceneSmp);
    g_ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
    g_ctx->Draw(4, 0);
    ID3D11ShaderResourceView* null_srv = nullptr;
    g_ctx->PSSetShaderResources(0, 1, &null_srv);
    SetRenderViewport();
    return true;
}
```

Add `static UINT g_renderWidth = 0;`, `static UINT g_renderHeight = 0;`, and this helper with the renderer globals. `CreateRenderTargetAndViewport` in Task 4 assigns both dimensions before calling it, so the normalization pass cannot leave the final parallax pass with a capture-sized viewport.

```cpp
static void SetRenderViewport() {
    if (!g_ctx || g_renderWidth == 0 || g_renderHeight == 0) return;
    const D3D11_VIEWPORT viewport = {
        0.0f, 0.0f,
        static_cast<float>(g_renderWidth), static_cast<float>(g_renderHeight),
        0.0f, 1.0f,
    };
    g_ctx->RSSetViewports(1, &viewport);
}
```

Replace the existing one-off viewport initialization in `Init` with this assignment so Task 2 is independently correct before the resize refactor.

```cpp
g_renderWidth = static_cast<UINT>(overlayW);
g_renderHeight = static_cast<UINT>(overlayH);
SetRenderViewport();
```

In the existing renderer-initialization block, create the normalization shader and buffer with this code immediately after the current scene shader and `g_cb` creation. On either failure, release the newly created normalization resource and return `false` from `Init`.

```cpp
ID3DBlob* normalize_blob = CompileShader(NORMALIZE_PS_SRC, "main", "ps_5_0");
if (!normalize_blob) return false;
hr = g_dev->CreatePixelShader(
    normalize_blob->GetBufferPointer(), normalize_blob->GetBufferSize(), nullptr, &g_normalizePs);
normalize_blob->Release();
if (FAILED(hr)) {
    LogHR("CreatePixelShader(normalize)", hr);
    return false;
}

D3D11_BUFFER_DESC normalize_desc = {};
normalize_desc.ByteWidth = sizeof(NormalizeCB);
normalize_desc.Usage = D3D11_USAGE_DYNAMIC;
normalize_desc.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
normalize_desc.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
hr = g_dev->CreateBuffer(&normalize_desc, nullptr, &g_normalizeCb);
if (FAILED(hr)) {
    LogHR("CreateBuffer(NormalizeCB)", hr);
    SafeRelease(g_normalizePs);
    return false;
}
```

Release `g_normalizeCb` and `g_normalizePs` in the current cleanup function alongside `g_cb` and `g_ps`. Replace `InitDuplication` with the full HRESULT-returning version below. It finds only the selected output, creates no global capture texture until `DuplicateOutput` succeeds, and never uses `EnumOutputs(0)`.

```cpp
static HRESULT InitDuplication() {
    if (!g_dev || !g_binding.monitor) return E_POINTER;

    IDXGIDevice* dxgi_device = nullptr;
    IDXGIAdapter* adapter = nullptr;
    IDXGIOutput* selected_output = nullptr;
    IDXGIOutput1* output1 = nullptr;
    HRESULT hr = S_OK;
    do {
        hr = g_dev->QueryInterface(__uuidof(IDXGIDevice),
            reinterpret_cast<void**>(&dxgi_device));
        if (FAILED(hr)) break;
        hr = dxgi_device->GetParent(__uuidof(IDXGIAdapter), reinterpret_cast<void**>(&adapter));
        if (FAILED(hr)) break;

        for (UINT index = 0;; ++index) {
            IDXGIOutput* candidate = nullptr;
            const HRESULT enum_hr = adapter->EnumOutputs(index, &candidate);
            if (enum_hr == DXGI_ERROR_NOT_FOUND) break;
            if (FAILED(enum_hr) || !candidate) { hr = enum_hr; break; }
            DXGI_OUTPUT_DESC desc = {};
            const HRESULT desc_hr = candidate->GetDesc(&desc);
            if (SUCCEEDED(desc_hr) && desc.AttachedToDesktop && desc.Monitor == g_binding.monitor) {
                selected_output = candidate;
                break;
            }
            candidate->Release();
        }
        if (!selected_output) {
            if (SUCCEEDED(hr)) hr = DXGI_ERROR_NOT_FOUND;
            break;
        }

        hr = selected_output->QueryInterface(__uuidof(IDXGIOutput1),
            reinterpret_cast<void**>(&output1));
        if (FAILED(hr)) break;
        hr = output1->DuplicateOutput(g_dev, &g_dup);
        if (FAILED(hr)) break;

        DXGI_OUTDUPL_DESC duplication_desc = {};
        g_dup->GetDesc(&duplication_desc);
        g_binding.rotation = duplication_desc.Rotation;
        if (!CreateCaptureTextures(duplication_desc)) {
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
    SafeRelease(selected_output);
    SafeRelease(adapter);
    SafeRelease(dxgi_device);
    return hr;
}
```

Replace the existing boolean initialization check with this HRESULT check until Task 3 changes startup to the state-driven loop.

```cpp
const HRESULT duplication_hr = InitDuplication();
if (FAILED(duplication_hr)) return false;
```

- [ ] **Step 5: Run focused tests and build the native target.**

Run:

```powershell
pytest tests/test_overlay_capture_resilience.py tests/test_overlay_backend_shader.py -q
& 'vendor\_mingw64\mingw64\bin\cmake.exe' --build overlay/build_mingw --target Glassless3DOverlay capture_recovery_tests --config Release
ctest --test-dir overlay/build_mingw -R '^capture_recovery_tests$' --output-on-failure
```

Expected: all focused pytest tests pass, the overlay compiles, and the CTest helper remains green.

- [ ] **Step 6: Commit target-bound normalized capture.**

```powershell
git add overlay/overlay.cpp tests/test_overlay_backend_shader.py tests/test_overlay_capture_resilience.py
git commit -m "feat: bind native capture to target output"
```

### Task 3: Make duplication acquisition fail-safe and rebind with bounded backoff

**Files:**
- Modify: `overlay/overlay.cpp:363-396, 749-856, 1282-1438, 1618-1650`
- Modify: `tests/test_overlay_capture_resilience.py`

- [ ] **Step 1: Extend the source contracts for frame ownership and rebind state.**

Append these tests to `tests/test_overlay_capture_resilience.py`.

```python
def test_overlay_uses_a_scoped_lease_for_every_acquired_desktop_frame():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "class DesktopFrameLease" in source
    assert "~DesktopFrameLease" in source
    assert "duplication_->ReleaseFrame()" in source
    assert "g_dup->ReleaseFrame();" not in source


def test_overlay_has_explicit_rebind_and_unavailable_paths():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "CaptureState::Rebinding" in source
    assert "CaptureState::Unavailable" in source
    assert "DXGI_ERROR_ACCESS_LOST" in source
    assert "DXGI_ERROR_INVALID_CALL" in source
    assert "DXGI_ERROR_NOT_CURRENTLY_AVAILABLE" in source
    assert "DXGI_ERROR_SESSION_DISCONNECTED" in source
    assert "target_spans_output" in source
    assert "CaptureStatus: state=%s reason=%s" in source


def test_overlay_does_not_sleep_inside_duplication_reset():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "static void ResetDuplication()" not in source
    assert "Sleep(300)" not in source
    assert "RetrySchedule g_rebindRetry" in source
```

- [ ] **Step 2: Run the contracts before adding the recovery path.**

Run:

```powershell
pytest tests/test_overlay_capture_resilience.py -q
```

Expected: the three new tests fail because `Frame` manually releases `g_dup` and `ResetDuplication` sleeps.

- [ ] **Step 3: Add the RAII desktop-frame lease.**

Place this class above `Frame` in `overlay/overlay.cpp` and use it for every call to `AcquireNextFrame`.

```cpp
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
        if (texture_) texture_->Release();
        if (resource_) resource_->Release();
        if (duplication_) duplication_->Release();
    }

    HRESULT Acquire(UINT timeout_ms, DXGI_OUTDUPL_FRAME_INFO* frame_info) {
        if (!duplication_ || !frame_info) return E_POINTER;
        const HRESULT hr = duplication_->AcquireNextFrame(timeout_ms, frame_info, &resource_);
        if (FAILED(hr)) return hr;
        acquired_ = true;
        if (!resource_) return E_FAIL;
        return resource_->QueryInterface(__uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&texture_));
    }

    ID3D11Texture2D* texture() const { return texture_; }

private:
    IDXGIOutputDuplication* duplication_ = nullptr;
    IDXGIResource* resource_ = nullptr;
    ID3D11Texture2D* texture_ = nullptr;
    bool acquired_ = false;
};
```

- [ ] **Step 4: Replace `ResetDuplication` with state-driven capture resource lifecycle.**

Use these globals and helpers. `DestroyCaptureResources` destroys `g_depth` first, because its worker owns staging resources tied to `g_capTex` dimensions.

```cpp
using g3d::capture::CaptureSignal;
using g3d::capture::CaptureState;
using g3d::capture::RetrySchedule;

static CaptureState g_captureState = CaptureState::Rebinding;
static RetrySchedule g_rebindRetry;
static const char* g_captureReason = "startup";

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
    const bool visible = g_captureState == CaptureState::Running && g_hasFrame;
    ShowWindow(g_hwnd, visible ? SW_SHOWNOACTIVATE : SW_HIDE);
}

static void SetCaptureState(CaptureState state, const char* reason) {
    g_captureState = state;
    g_captureReason = reason;
    Log("CaptureStatus: state=%s reason=%s", CaptureStateName(state), reason);
    UpdateOverlayVisibility();
}

static void DestroyCaptureResources() {
    g_hasFrame = false;
    if (g_depth) { delete g_depth; g_depth = nullptr; }
    ReleaseCaptureTextures();
    SafeRelease(g_dup);
}

static void QueueCaptureSignal(CaptureSignal signal, const char* reason) {
    const auto action = g3d::capture::AdvanceCaptureState(g_captureState, signal);
    if (action.rebuild_device) return;
    if (signal == CaptureSignal::BindingDirty) {
        DestroyCaptureResources();
        g_rebindRetry.Reset(GetTickCount64());
    }
    if (signal == CaptureSignal::DuplicationLost || signal == CaptureSignal::RebindRetry) {
        DestroyCaptureResources();
        g_rebindRetry.RecordFailure(GetTickCount64());
    }
    SetCaptureState(action.next_state, reason);
}
```

Add a binding result classifier and rebind tick. `DuplicateOutput` failures that cannot be safely retried on the current display remain dormant in `Unavailable`; no branch calls `Sleep` or retries each frame.

```cpp
static bool IsUnavailableDuplicationFailure(HRESULT hr) {
    return hr == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE
        || hr == DXGI_ERROR_UNSUPPORTED
        || hr == DXGI_ERROR_SESSION_DISCONNECTED
        || hr == E_ACCESSDENIED;
}

static void TickCaptureRebind() {
    if (g_captureState == CaptureState::Unavailable) {
        if (!g_bindingDirty) return;
        QueueCaptureSignal(CaptureSignal::BindingDirty, "binding_changed");
    }
    if (g_captureState != CaptureState::Rebinding || !g_rebindRetry.CanAttempt(GetTickCount64())) return;

    const BindingStatus binding_status = RefreshCaptureBinding();
    if (binding_status == BindingStatus::TargetSpansOutput) {
        SetCaptureState(CaptureState::Unavailable, "target_spans_output");
        return;
    }
    if (binding_status != BindingStatus::Ready) {
        SetCaptureState(CaptureState::Unavailable, "no_matching_output");
        return;
    }

    SyncOverlayWindowToBinding();
    const HRESULT hr = InitDuplication();
    if (SUCCEEDED(hr)) {
        InitDepth();
        g_rebindRetry.Reset(GetTickCount64());
        SetCaptureState(CaptureState::Running, "bound");
    } else if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_INVALID_CALL) {
        QueueCaptureSignal(CaptureSignal::RebindRetry, "duplicate_retry");
    } else if (IsUnavailableDuplicationFailure(hr)) {
        DestroyCaptureResources();
        SetCaptureState(CaptureState::Unavailable, "duplicate_unavailable");
    } else {
        QueueCaptureSignal(CaptureSignal::RebindRetry, "duplicate_failed");
    }
}
```

Change `InitDuplication` to return its final `HRESULT` rather than `bool`. On every early failure, release its local DXGI interfaces and return the failure. It must leave every global capture pointer null until `CreateCaptureTextures` succeeds.

At the end of `Init`, replace the direct `InitDuplication()` / `InitDepth()` startup calls with this nonfatal initialization. The first `Frame` call performs the bind through `TickCaptureRebind`.

```cpp
g_rebindRetry.Reset(GetTickCount64());
SetCaptureState(CaptureState::Rebinding, "startup");
return true;
```

- [ ] **Step 5: Refactor acquisition into `UpdateCapture` and preserve only timeout frames.**

Replace the acquisition block at the start of `Frame` with this function, then call the following gate after settings are applied. It prevents null scene/depth bindings while the overlay is intentionally hidden.

```cpp
TickCaptureRebind();
UpdateCapture();
if (g_captureState != CaptureState::Running || !g_hasFrame) return;
```

```cpp
static void UpdateCapture() {
    if (g_captureState != CaptureState::Running || !g_dup) return;

    DXGI_OUTDUPL_FRAME_INFO info = {};
    DesktopFrameLease frame(g_dup);
    const HRESULT hr = frame.Acquire(16, &info);
    if (hr == DXGI_ERROR_WAIT_TIMEOUT) {
        return;
    }
    if (hr == DXGI_ERROR_ACCESS_LOST || hr == DXGI_ERROR_INVALID_CALL) {
        QueueCaptureSignal(CaptureSignal::DuplicationLost,
            hr == DXGI_ERROR_ACCESS_LOST ? "access_lost" : "invalid_call");
        return;
    }
    if (IsUnavailableDuplicationFailure(hr)) {
        DestroyCaptureResources();
        SetCaptureState(CaptureState::Unavailable, "duplicate_unavailable");
        return;
    }
    if (FAILED(hr) || !frame.texture()) {
        QueueCaptureSignal(CaptureSignal::RebindRetry, "acquire_failed");
        return;
    }
    if (!NormalizeCapturedFrame(frame.texture())) {
        QueueCaptureSignal(CaptureSignal::RebindRetry, "normalize_failed");
        return;
    }

    g_hasFrame = true;
    UpdateOverlayVisibility();
    if (g_depth && !g_depth->run(g_capTex)) {
        Log("DepthInferencer::run failed: %s", g_depth->last_error());
    }
}
```

Leave the `DesktopFrameLease` in scope until `NormalizeCapturedFrame` and depth submission return; its destructor makes every successful acquire release exactly once, including failures after `QueryInterface` succeeds.
Its constructor takes an explicit COM reference, so a recovery branch may clear the global `g_dup` without leaving the lease with a dangling pointer before its destructor calls `ReleaseFrame`.

- [ ] **Step 6: Pass recovery contracts and commit.**

Run:

```powershell
pytest tests/test_overlay_capture_resilience.py tests/test_overlay_backend_shader.py -q
& 'vendor\_mingw64\mingw64\bin\cmake.exe' --build overlay/build_mingw --target Glassless3DOverlay capture_recovery_tests --config Release
ctest --test-dir overlay/build_mingw -R '^capture_recovery_tests$' --output-on-failure
git add overlay/overlay.cpp tests/test_overlay_capture_resilience.py
git commit -m "feat: recover desktop duplication safely"
```

Expected: source contracts pass, native compilation succeeds, and the only `ReleaseFrame` call is owned by `DesktopFrameLease`.

### Task 4: Recover D3D resources, DPI changes, and size-dependent depth state

**Files:**
- Modify: `overlay/overlay.cpp:20-40, 695-747, 1100-1280, 1470-1650`
- Modify: `overlay/depth_infer.cpp:820-885`
- Modify: `tests/test_overlay_capture_resilience.py`

- [ ] **Step 1: Write contracts for DPI, device-loss handling, `Present`, and depth cleanup.**

Append these tests to `tests/test_overlay_capture_resilience.py`.

```python
def test_overlay_marks_bindings_dirty_for_dpi_and_display_changes():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "SetProcessDpiAwarenessContext" in source
    assert "WM_DPICHANGED" in source
    assert "WM_DISPLAYCHANGE" in source
    assert "g_bindingDirty = true" in source


def test_overlay_checks_present_and_enters_device_recovery():
    source = OVERLAY.read_text(encoding="utf-8")

    assert "const HRESULT present_hr = g_swap->Present(0, 0);" in source
    assert "GetDeviceRemovedReason" in source
    assert "DXGI_ERROR_DEVICE_REMOVED" in source
    assert "DXGI_ERROR_DEVICE_RESET" in source
    assert "DXGI_ERROR_DEVICE_HUNG" in source
    assert "CaptureState::DeviceRecovery" in source


def test_depth_cleanup_releases_both_current_and_previous_depth_resources():
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "if (depth_prev_srv) { depth_prev_srv->Release(); depth_prev_srv = nullptr; }" in source
    assert "if (depth_prev_tex) { depth_prev_tex->Release(); depth_prev_tex = nullptr; }" in source
    assert "if (depth_srv) { depth_srv->Release(); depth_srv = nullptr; }" in source
    assert "if (depth_tex) { depth_tex->Release(); depth_tex = nullptr; }" in source
```

- [ ] **Step 2: Run the new contracts before device recovery is added.**

Run:

```powershell
pytest tests/test_overlay_capture_resilience.py -q
```

Expected: the new tests fail because the existing window procedure ignores display/DPI changes, `Present` ignores its result, and cleanup omits previous depth resources.

- [ ] **Step 3: Make DPI awareness and window events declarative, never resource-mutating.**

Use this MinGW-compatible dynamic call before `Init(hInst)` in `WinMain`, after `LogInit()`.

```cpp
static void EnablePerMonitorV2DpiAwareness() {
    using SetProcessDpiAwarenessContextFn = BOOL(WINAPI*)(HANDLE);
    const HMODULE user32 = GetModuleHandleW(L"user32.dll");
    const auto set_context = user32
        ? reinterpret_cast<SetProcessDpiAwarenessContextFn>(
            GetProcAddress(user32, "SetProcessDpiAwarenessContext"))
        : nullptr;
    if (!set_context) {
        Log("SetProcessDpiAwarenessContext unavailable; continuing with current DPI context");
        return;
    }
    const HANDLE per_monitor_v2 = reinterpret_cast<HANDLE>(static_cast<intptr_t>(-4));
    const BOOL ok = set_context(per_monitor_v2);
    Log("SetProcessDpiAwarenessContext(PMv2): ok=%d GLE=%lu", ok ? 1 : 0, ok ? 0 : GetLastError());
}
```

Add these globals and replace the corresponding branch logic in `WndProc`. The window procedure only records work; `Frame` performs D3D calls after queued messages have been dispatched.

```cpp
static bool g_swapResizePending = false;
static UINT g_pendingSwapWidth = 0;
static UINT g_pendingSwapHeight = 0;

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
```

Add this bounded target poll near the capture globals, then call `PollTargetWindow()` at the start of `Frame` after queued messages are dispatched. The actual window movement is applied by `SyncOverlayWindowToBinding` only after the new binding has been validated.

```cpp
static uint64_t g_nextTargetPollMs = 0;

static void PollTargetWindow() {
    const uint64_t now_ms = GetTickCount64();
    if (now_ms < g_nextTargetPollMs) return;
    g_nextTargetPollMs = now_ms + 250;

    const HWND old_window = g_targetWindow;
    const RECT old_rect = g_targetRect;
    const bool old_enabled = g_useTargetWindow;
    DetectTargetWindowRect();
    if (old_window != g_targetWindow || old_enabled != g_useTargetWindow
        || !EqualRect(&old_rect, &g_targetRect)) {
        g_bindingDirty = true;
    }
}
```

Immediately after `PollTargetWindow()` in `Frame`, queue a rebind whenever a currently running capture observes a dirty binding.

```cpp
if (g_captureState == CaptureState::Running && g_bindingDirty) {
    QueueCaptureSignal(CaptureSignal::BindingDirty, "binding_changed");
}
```

This is the only automatic exit from `Unavailable`: `TickCaptureRebind` first sees `g_bindingDirty`, validates the new output/target region, then binds it.

- [ ] **Step 4: Split device-owned resource teardown from capture teardown.**

Use these helpers. They ensure the depth worker and capture objects are gone before their device/context is released.

```cpp
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
}

static void DestroyDeviceResources() {
    DestroyCaptureResources();
    if (g_ctx) {
        g_ctx->ClearState();
        g_ctx->Flush();
    }
    DestroyRendererResources();
    SafeRelease(g_swap);
    SafeRelease(g_ctx);
    SafeRelease(g_dev);
}

static bool IsDeviceLoss(HRESULT hr) {
    return hr == DXGI_ERROR_DEVICE_REMOVED
        || hr == DXGI_ERROR_DEVICE_RESET
        || hr == DXGI_ERROR_DEVICE_HUNG;
}

static void EnterDeviceRecovery(const char* operation, HRESULT hr, const char* reason_code) {
    const HRESULT reason = g_dev ? g_dev->GetDeviceRemovedReason() : hr;
    Log("Device recovery: operation=%s hr=0x%08X reason=0x%08X",
        operation, static_cast<unsigned>(hr), static_cast<unsigned>(reason));
    DestroyDeviceResources();
    g_rebindRetry.Reset(GetTickCount64());
    SetCaptureState(CaptureState::DeviceRecovery, reason_code);
}

static bool HandleDeviceResult(const char* operation, HRESULT hr) {
    if (SUCCEEDED(hr)) return true;
    if (IsDeviceLoss(hr) || (g_dev && IsDeviceLoss(g_dev->GetDeviceRemovedReason()))) {
        EnterDeviceRecovery(operation, hr, "device_lost");
        return false;
    }
    LogHR(operation, hr);
    EnterDeviceRecovery(operation, hr, "renderer_failed");
    return false;
}
```

Replace the monolithic D3D initialization with the concrete helpers below. They leave capture creation to `TickCaptureRebind`, so no duplication object survives a device rebuild.

```cpp
static HRESULT CreateRenderTargetAndViewport(UINT width, UINT height) {
    if (!g_swap || !g_dev || !g_ctx || width == 0 || height == 0) return E_INVALIDARG;
    SafeRelease(g_rtv);
    ID3D11Texture2D* back_buffer = nullptr;
    HRESULT hr = g_swap->GetBuffer(0, __uuidof(ID3D11Texture2D),
        reinterpret_cast<void**>(&back_buffer));
    if (FAILED(hr)) return hr;
    hr = g_dev->CreateRenderTargetView(back_buffer, nullptr, &g_rtv);
    back_buffer->Release();
    if (FAILED(hr)) return hr;
    g_renderWidth = width;
    g_renderHeight = height;
    SetRenderViewport();
    return S_OK;
}

static HRESULT CreateRendererShadersAndState() {
    ID3DBlob* vs_blob = CompileShader(VS_SRC, "main", "vs_5_0");
    ID3DBlob* ps_blob = CompileShader(PS_SRC, "main", "ps_5_0");
    ID3DBlob* normalize_blob = CompileShader(NORMALIZE_PS_SRC, "main", "ps_5_0");
    if (!vs_blob || !ps_blob || !normalize_blob) {
        SafeRelease(vs_blob);
        SafeRelease(ps_blob);
        SafeRelease(normalize_blob);
        return E_FAIL;
    }
    HRESULT hr = g_dev->CreateVertexShader(vs_blob->GetBufferPointer(), vs_blob->GetBufferSize(), nullptr, &g_vs);
    if (SUCCEEDED(hr)) hr = g_dev->CreatePixelShader(ps_blob->GetBufferPointer(), ps_blob->GetBufferSize(), nullptr, &g_ps);
    if (SUCCEEDED(hr)) hr = g_dev->CreatePixelShader(normalize_blob->GetBufferPointer(), normalize_blob->GetBufferSize(), nullptr, &g_normalizePs);
    SafeRelease(vs_blob);
    SafeRelease(ps_blob);
    SafeRelease(normalize_blob);
    if (FAILED(hr)) return hr;

    D3D11_BUFFER_DESC dynamic_buffer = {};
    dynamic_buffer.Usage = D3D11_USAGE_DYNAMIC;
    dynamic_buffer.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    dynamic_buffer.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    dynamic_buffer.ByteWidth = sizeof(CBuf);
    hr = g_dev->CreateBuffer(&dynamic_buffer, nullptr, &g_cb);
    if (SUCCEEDED(hr)) {
        dynamic_buffer.ByteWidth = sizeof(NormalizeCB);
        hr = g_dev->CreateBuffer(&dynamic_buffer, nullptr, &g_normalizeCb);
    }
    if (FAILED(hr)) return hr;

    D3D11_SAMPLER_DESC scene_sampler = {};
    scene_sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    scene_sampler.AddressU = scene_sampler.AddressV = scene_sampler.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    hr = g_dev->CreateSamplerState(&scene_sampler, &g_sceneSmp);
    if (SUCCEEDED(hr)) {
        D3D11_SAMPLER_DESC depth_sampler = scene_sampler;
        depth_sampler.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        hr = g_dev->CreateSamplerState(&depth_sampler, &g_depthSmp);
    }
    if (FAILED(hr)) return hr;
    if (!CreateFallbackDepthSrv()) return E_FAIL;
    InitGpuTiming();
    return S_OK;
}

static HRESULT CreateDeviceAndRenderer() {
    IDXGIAdapter1* adapter = OpenAdapterForLuid(g_binding.adapter_luid);
    if (!adapter) return DXGI_ERROR_NOT_FOUND;

    RECT client_rect = {};
    if (!GetClientRect(g_hwnd, &client_rect)) {
        adapter->Release();
        return HRESULT_FROM_WIN32(GetLastError());
    }
    const UINT client_width = static_cast<UINT>(client_rect.right - client_rect.left);
    const UINT client_height = static_cast<UINT>(client_rect.bottom - client_rect.top);
    if (client_width == 0 || client_height == 0) {
        adapter->Release();
        return E_INVALIDARG;
    }

    DXGI_SWAP_CHAIN_DESC swap_desc = {};
    swap_desc.BufferCount = 1;
    swap_desc.BufferDesc.Width = client_width;
    swap_desc.BufferDesc.Height = client_height;
    swap_desc.BufferDesc.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
    swap_desc.BufferUsage = DXGI_USAGE_RENDER_TARGET_OUTPUT;
    swap_desc.OutputWindow = g_hwnd;
    swap_desc.SampleDesc.Count = 1;
    swap_desc.Windowed = TRUE;
    swap_desc.SwapEffect = DXGI_SWAP_EFFECT_DISCARD;

    D3D_FEATURE_LEVEL feature_level = {};
    HRESULT hr = D3D11CreateDeviceAndSwapChain(
        adapter, D3D_DRIVER_TYPE_UNKNOWN, nullptr, 0, nullptr, 0,
        D3D11_SDK_VERSION, &swap_desc, &g_swap, &g_dev, &feature_level, &g_ctx);
    adapter->Release();
    if (FAILED(hr)) return hr;

    hr = CreateRenderTargetAndViewport(swap_desc.BufferDesc.Width, swap_desc.BufferDesc.Height);
    if (SUCCEEDED(hr)) hr = CreateRendererShadersAndState();
    if (FAILED(hr)) DestroyDeviceResources();
    return hr;
}

static HRESULT ResizeSwapChain(UINT width, UINT height) {
    if (!g_swap || !g_ctx || width == 0 || height == 0) return E_INVALIDARG;
    g_ctx->OMSetRenderTargets(0, nullptr, nullptr);
    g_ctx->ClearState();
    g_ctx->Flush();
    SafeRelease(g_rtv);
    HRESULT hr = g_swap->ResizeBuffers(0, width, height, DXGI_FORMAT_UNKNOWN, 0);
    if (FAILED(hr)) return hr;
    return CreateRenderTargetAndViewport(width, height);
}
```

During initial `Init`, create the window/tray shell first, call `RefreshCaptureBinding`, and either call `CreateDeviceAndRenderer()` followed by `SetCaptureState(CaptureState::Rebinding, "startup")`, or call `SetCaptureState(CaptureState::Unavailable, "no_matching_output")`. Do not show a fatal modal merely because capture cannot be bound yet.

Add this device-recovery branch at the start of `TickCaptureRebind`.

```cpp
if (g_captureState == CaptureState::DeviceRecovery) {
    if (!g_rebindRetry.CanAttempt(GetTickCount64())) return;
    const BindingStatus binding_status = RefreshCaptureBinding();
    if (binding_status == BindingStatus::TargetSpansOutput) {
        SetCaptureState(CaptureState::Unavailable, "target_spans_output");
        return;
    }
    if (binding_status != BindingStatus::Ready) {
        SetCaptureState(CaptureState::Unavailable, "no_matching_output");
        return;
    }
    SyncOverlayWindowToBinding();
    const HRESULT create_hr = CreateDeviceAndRenderer();
    if (FAILED(create_hr)) {
        LogHR("CreateDeviceAndRenderer", create_hr);
        g_rebindRetry.RecordFailure(GetTickCount64());
        return;
    }
    g_rebindRetry.Reset(GetTickCount64());
    SetCaptureState(CaptureState::Rebinding, "device_recreated");
}
```

At the top of `Frame`, after queued window messages and before `TickCaptureRebind`, consume `g_swapResizePending` with this exact check. If the result begins device recovery, return immediately and do not touch old views.

```cpp
if (g_swapResizePending) {
    const UINT width = g_pendingSwapWidth;
    const UINT height = g_pendingSwapHeight;
    g_swapResizePending = false;
    const HRESULT resize_hr = ResizeSwapChain(width, height);
    if (!HandleDeviceResult("ResizeBuffers", resize_hr)) return;
}
```

- [ ] **Step 5: Check every visible device-loss point and repair depth teardown.**

Replace the final render call with the checked form below; return from `Frame` after a failed `Present`.

```cpp
const HRESULT present_hr = g_swap->Present(0, 0);
if (!HandleDeviceResult("Present", present_hr)) return;
```

Use `HandleDeviceResult` for `ResizeBuffers`, `GetBuffer`, `CreateRenderTargetView`, `Map`, and resource creation failures that return an `HRESULT`. Do not call D3D methods after it enters device recovery.

In `DepthInferImpl::cleanup` in `overlay/depth_infer.cpp`, replace the D3D-resource releases with this exact order.

```cpp
if (depth_prev_srv) { depth_prev_srv->Release(); depth_prev_srv = nullptr; }
if (depth_srv) { depth_srv->Release(); depth_srv = nullptr; }
if (depth_prev_tex) { depth_prev_tex->Release(); depth_prev_tex = nullptr; }
if (depth_tex) { depth_tex->Release(); depth_tex = nullptr; }
if (stage_bgra) { stage_bgra->Release(); stage_bgra = nullptr; }
```

At the beginning of `DepthInferencer::init`, call `impl_->cleanup()`, reset `stop` to `false`, reset `input_pending` and `output_ready` under `impl_->m`, then create fresh D3D resources. If `create_d3d_resources` or `create_ort_session` fails, call `impl_->cleanup()` before returning `false`.

Replace `DepthInferencer::init` with this complete implementation.

```cpp
bool DepthInferencer::init(ID3D11Device* dev, ID3D11DeviceContext* ctx,
                           const std::wstring& model_path,
                           int capture_w, int capture_h) {
    if (!dev || !ctx || capture_w <= 0 || capture_h <= 0) return false;
    impl_->cleanup();
    {
        std::lock_guard<std::mutex> lock(impl_->m);
        impl_->input_pending = false;
        impl_->output_ready = false;
        impl_->pending_input_f32.clear();
        impl_->running_input_f32.clear();
        impl_->ready_upload_fp16.clear();
        impl_->last_err.clear();
    }
    impl_->stop.store(false, std::memory_order_relaxed);
    impl_->inferences.store(0, std::memory_order_relaxed);
    impl_->dev = dev;
    impl_->ctx = ctx;
    impl_->cap_w = capture_w;
    impl_->cap_h = capture_h;
    if (!impl_->create_d3d_resources()) {
        impl_->cleanup();
        return false;
    }
    if (!impl_->create_ort_session(model_path)) {
        impl_->cleanup();
        return false;
    }
    impl_->worker = std::thread([impl = impl_.get()] { impl->worker_loop(); });
    return true;
}
```

- [ ] **Step 6: Run tests and commit device/DPI recovery.**

Run:

```powershell
pytest tests/test_overlay_capture_resilience.py tests/test_overlay_backend_shader.py -q
& 'vendor\_mingw64\mingw64\bin\cmake.exe' --build overlay/build_mingw --target Glassless3DOverlay capture_recovery_tests --config Release
ctest --test-dir overlay/build_mingw -R '^capture_recovery_tests$' --output-on-failure
git add overlay/overlay.cpp overlay/depth_infer.cpp tests/test_overlay_capture_resilience.py
git commit -m "fix: recover native overlay device resources"
```

Expected: the helper test, source contracts, and native build all pass; `depth_prev_srv` and `depth_prev_tex` are released before any device replacement.

### Task 5: Surface capture availability without a launcher restart loop

**Files:**
- Modify: `overlay/overlay.cpp:1440-1470`
- Modify: `launcher/diagnostics.py:33-123, 164-225, 254-340, 766-835`
- Modify: `launcher/mainwindow.py:978-1026`
- Modify: `tests/test_diagnostics.py`
- Modify: `tests/test_mainwindow.py`
- Modify: `docs/TROUBLESHOOTING.md`

- [ ] **Step 1: Write diagnostics and launcher tests.**

Append this test to `tests/test_diagnostics.py`.

```python
def test_diagnostics_explains_unavailable_capture_from_overlay_summary(tmp_path, monkeypatch):
    log_path = tmp_path / "overlay.log"
    log_path.write_text(
        "[15:26:00.371] Frame#300 acq[ok=300 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=300 changes=200 (34/s) ts=123] "
        "depth[total=17 5Hz mode=balanced] head=(0.00,0.00,60.00) "
        "rest=(0.00,0.00) rel=(0.00,0.00) wobble=0.00 strength=1.00 "
        "depth=30.00 hasFrame=0 capture=unavailable capture_reason=target_spans_output\n",
        encoding="utf-8",
    )
    cfg = tmp_path / "config.yaml"
    cfg.write_text("camera:\n  index: 0\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "find_overlay_exe", lambda: tmp_path / "overlay.exe")
    monkeypatch.setattr(diagnostics, "find_depth_model", lambda: tmp_path / "model.onnx")
    monkeypatch.setattr(diagnostics, "_find_overlay_log", lambda _exe: log_path)
    monkeypatch.setattr(
        diagnostics,
        "_probe_camera",
        lambda index: diagnostics.CameraProbe(index=index, opened=True, frame_ok=True),
    )

    report = diagnostics.collect_diagnostics(config_path=cfg)

    assert report.ready is False
    assert report.overlay_summary is not None
    assert report.overlay_summary.capture_state == "unavailable"
    assert report.overlay_summary.capture_reason == "target_spans_output"
    assert "move it fully onto one display" in diagnostics.format_diagnostics_report(report)
    assert '"capture_reason": "target_spans_output"' in diagnostics.format_diagnostics_json(report)
```

Append this test to `tests/test_mainwindow.py`.

```python
def test_runtime_health_does_not_restart_an_intentionally_unavailable_capture(window):
    summary = diagnostics.OverlayRuntimeSummary(
        frame_count=120,
        acq_ok=118,
        acq_timeout=2,
        acq_lost=3,
        acq_other=0,
        shm_status="LIVE",
        shm_changes_per_sec=7,
        depth_total=28,
        depth_hz=8,
        head_z_cm=58.5,
        has_frame=False,
        capture_state="unavailable",
        capture_reason="target_spans_output",
    )
    fake_thread = MagicMock()
    fake_thread.isRunning.return_value = True
    window._thread = fake_thread
    window._overlay_started = True
    window._overlay = MagicMock()
    window._overlay.is_running.return_value = True

    window._apply_runtime_health(summary)
    window._apply_runtime_health(summary)
    window._apply_runtime_health(summary)

    window._overlay.stop.assert_not_called()
    window._overlay.start.assert_not_called()
    assert "Unavailable" in window._capture_tile.text()
```

- [ ] **Step 2: Run these tests to establish the red state.**

Run:

```powershell
pytest tests/test_diagnostics.py tests/test_mainwindow.py -q
```

Expected: the new diagnostics test fails because the current summary parser does not retain capture fields; the main-window test fails because the state fields do not exist.

- [ ] **Step 3: Add capture state to the periodic overlay summary and diagnostics model.**

Append the following fields at the end of `OverlayRuntimeSummary` in `launcher/diagnostics.py`, preserving defaults so existing constructors remain valid.

```python
    capture_state: str | None = None
    capture_reason: str | None = None
```

Extend the end of `_SUMMARY_RE` exactly as follows.

```python
    r"hasFrame=(?P<has_frame>[01])"
    r"(?:\s+capture=(?P<capture_state>[a-z_]+)\s+capture_reason=(?P<capture_reason>[a-z0-9_]+))?"
)
```

Set the two fields in `parse_overlay_summary_line`, and include them in `_summary_to_dict`.

```python
        capture_state=match.group("capture_state"),
        capture_reason=match.group("capture_reason"),
```

```python
        "capture_state": summary.capture_state,
        "capture_reason": summary.capture_reason,
```

Append capture state to the existing periodic summary log call in `overlay/overlay.cpp` by changing its terminal format and argument list to this exact suffix.

```cpp
"hasFrame=%d capture=%s capture_reason=%s",
g_hasFrame ? 1 : 0,
CaptureStateName(g_captureState),
g_captureReason,
```

Add this guidance mapping next to `_DEPTH_HZ_READY_MIN`.

```python
_CAPTURE_REASON_GUIDANCE = {
    "target_spans_output": "target window spans multiple displays; move it fully onto one display",
    "no_matching_output": "no attached display output matches the target; reconnect or enable the display",
    "duplicate_unavailable": "desktop capture is unavailable or protected for this output; use a normal local desktop session",
    "device_lost": "the graphics device was reset; wait for the overlay to rebind after the display stabilizes",
}
```

After existing `has_frame` checks in `collect_diagnostics`, add this logic.

```python
        if overlay_summary.capture_state == "unavailable":
            guidance = _CAPTURE_REASON_GUIDANCE.get(
                overlay_summary.capture_reason or "",
                "desktop capture is unavailable; check the overlay log for its capture reason",
            )
            problems.append(f"overlay capture unavailable: {guidance}")
        elif overlay_summary.capture_state in {"rebinding", "device_recovery"}:
            warnings.append(
                f"overlay capture is {overlay_summary.capture_state}; waiting for native recovery"
            )
```

Print `- capture: <state> (<reason>)` in `format_diagnostics_report` under `Latest overlay summary`, using `unavailable` for missing fields.

```python
                (
                    f"- capture: {s.capture_state or 'unavailable'} "
                    f"({s.capture_reason or 'not reported'})"
                ),
```

- [ ] **Step 4: Make launcher health display native state and defer to its recovery loop.**

Add this branch at the beginning of `_maybe_recover_overlay` after the process-running check.

```python
        if summary.capture_state in {"unavailable", "rebinding", "device_recovery"}:
            self._capture_loss_count = 0
            return
```

Replace the capture-tile assignment in `_apply_runtime_health` with the following exact branch.

```python
        if summary.capture_state == "unavailable":
            self._capture_tile.setText("Capture\nUnavailable")
            self._capture_tile.setToolTip(summary.capture_reason or "desktop capture unavailable")
        elif summary.capture_state in {"rebinding", "device_recovery"}:
            self._capture_tile.setText("Capture\nRecovering")
            self._capture_tile.setToolTip(summary.capture_reason or "native recovery in progress")
        else:
            self._capture_tile.setText(
                "Capture\nFrame OK" if summary.has_frame else "Capture\nNo frame"
            )
            self._capture_tile.setToolTip("")
```

- [ ] **Step 5: Add a user-facing troubleshooting section.**

Append this exact section to `docs/TROUBLESHOOTING.md` under the overlay troubleshooting material.

```markdown
### Capture unavailable or recovering

The standalone overlay uses desktop capture only. It never changes into an injected game backend when capture cannot be used.

- `target_spans_output`: Move the game's client area fully onto one display. Cross-display capture is intentionally disabled.
- `no_matching_output`: Enable or reconnect the display that contains the game window, then wait for the overlay to rebind.
- `duplicate_unavailable`: Use a normal local desktop session and check whether protected or display-only content is active.
- `device_lost`: Wait for the graphics driver/display to settle. The overlay rebuilds its renderer and capture binding with bounded retries.
```

- [ ] **Step 6: Run launcher-focused tests and commit the diagnostic surface.**

Run:

```powershell
pytest tests/test_diagnostics.py tests/test_mainwindow.py tests/test_overlay_capture_resilience.py -q
git add overlay/overlay.cpp launcher/diagnostics.py launcher/mainwindow.py docs/TROUBLESHOOTING.md tests/test_diagnostics.py tests/test_mainwindow.py
git commit -m "feat: report native capture availability"
```

Expected: unavailable capture produces a clear operator message, JSON contains both reason fields, and the launcher leaves native recovery in control.

### Task 6: Verify the integrated native overlay and capture the staged executable evidence

**Files:**
- Modify only if a test exposes a concrete defect: files named by the failing test.

- [ ] **Step 1: Run the native unit test and focused Python regression suite.**

Run:

```powershell
& 'vendor\_mingw64\mingw64\bin\cmake.exe' -S overlay -B overlay/build_mingw -G 'MinGW Makefiles' -DCMAKE_BUILD_TYPE=Release
& 'vendor\_mingw64\mingw64\bin\cmake.exe' --build overlay/build_mingw --target capture_recovery_tests Glassless3DOverlay --config Release
ctest --test-dir overlay/build_mingw --output-on-failure
pytest tests/test_overlay_capture_resilience.py tests/test_overlay_backend_shader.py tests/test_diagnostics.py tests/test_mainwindow.py -q
```

Expected: CMake configuration succeeds, CTest reports the capture helper as passed, and every focused pytest test passes.

- [ ] **Step 2: Run the full regression suite and static typing check.**

Run:

```powershell
pytest tests/ -q
pyright launcher setup.py
```

Expected: pytest passes and pyright reports zero errors for the checked paths.

- [ ] **Step 3: Force-copy and compare the root executable to the actual build output.**

The CMake post-build copy intentionally suppresses errors, so verify content rather than trusting its exit code.

```powershell
$built = Get-Item -LiteralPath 'overlay\build_mingw\Glassless3DOverlay.exe' -ErrorAction Stop
Copy-Item -LiteralPath $built.FullName -Destination 'Glassless3DOverlay.exe' -Force
$rootHash = (Get-FileHash -Algorithm SHA256 -LiteralPath 'Glassless3DOverlay.exe').Hash
$buildHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $built.FullName).Hash
if ($rootHash -ne $buildHash) { throw 'Root Glassless3DOverlay.exe does not match build output.' }
Get-Item -LiteralPath 'Glassless3DOverlay.exe' | Select-Object FullName, LastWriteTimeUtc, Length
Get-FileHash -Algorithm SHA256 -LiteralPath 'Glassless3DOverlay.exe'
```

Expected: both SHA-256 hashes match and the root executable timestamp is current.

- [ ] **Step 4: Perform the manual display matrix and retain `overlay.log` evidence.**

1. Run the overlay with the target fully on the primary monitor. Confirm `CaptureStatus: state=running reason=bound` and a periodic summary with `capture=running`.
2. Move the target fully to a second attached monitor. Confirm the log's selected `output=` is that monitor, the scene remains upright, and it returns to `running`.
3. Drag the target across the monitor boundary. Confirm `CaptureStatus: state=unavailable reason=target_spans_output`, the overlay hides, diagnostics say to move it fully onto one display, and the launcher does not restart it.
4. Move the target fully back onto either monitor. Confirm a binding-dirty event causes `rebinding` then `running` without relaunching the process.
5. Change display scale, resolution, or orientation. Confirm `WM_DPICHANGED` or `WM_DISPLAYCHANGE` causes a rebind, depth resumes after its capture-size reinitialization, and no invalid crop/copy diagnostic appears.
6. Force a desktop-duplication interruption available in the test environment, such as changing monitor topology. Confirm `access_lost` enters bounded `rebinding`; `WAIT_TIMEOUT` alone leaves `running` and continues displaying the last valid frame.
7. Run `python -m launcher.diagnostics --config config.yaml` after each unavailable/recovery case and retain the relevant summary/log lines with the test notes.

- [ ] **Step 5: Inspect the final diff, then commit any evidence-backed corrective change.**

Run:

```powershell
git diff --check
git status --short
git diff -- overlay/overlay.cpp overlay/depth_infer.cpp overlay/capture_recovery.h overlay/capture_recovery.cpp overlay/CMakeLists.txt launcher/diagnostics.py launcher/mainwindow.py tests/test_overlay_capture_resilience.py tests/test_overlay_backend_shader.py tests/test_diagnostics.py tests/test_mainwindow.py docs/TROUBLESHOOTING.md
```

Expected: `git diff --check` prints no whitespace errors. If the manual matrix exposed a reproducible defect, add its regression test first, make the smallest fix, rerun Steps 1-4, and commit that scoped change with a message that names the defect.

## Plan self-review

### Spec coverage

- Output/adapter binding, target-bound monitor selection, and target-spanning rejection: Tasks 1 and 2.
- Full-output capture, upright rotation normalization, no unsafe crop box, and capture-size resources: Task 2.
- Scoped `AcquireNextFrame`/`ReleaseFrame`, timeout preservation, loss recovery, bounded retry, and unavailable behavior: Task 3.
- Device removed/reset/hung recovery, checked `Present`, D3D resource destruction, DPI/display messages, and depth cleanup: Task 4.
- User-facing reason codes, diagnostics, launcher behavior, and troubleshooting instructions: Task 5.
- Native build, staged executable hash, focused/full tests, and physical display verification: Task 6.

### Placeholder scan

The plan contains concrete paths, test bodies, state names, reason codes, code snippets, commands, expected outcomes, and commit messages. It deliberately contains no deferred implementation markers.

### Type consistency

- The pure module uses `g3d::capture::Rect`, `Region`, `Rotation`, `CaptureState`, `CaptureSignal`, `RetrySchedule`, and `AdvanceCaptureState` consistently across Tasks 1-4.
- The D3D layer uses `CaptureBinding`, `BindingStatus`, `DesktopFrameLease`, `DestroyCaptureResources`, `TickCaptureRebind`, `UpdateCapture`, and `EnterDeviceRecovery` consistently across Tasks 2-4.
- Diagnostics uses optional `OverlayRuntimeSummary.capture_state` and `.capture_reason` so existing test constructors remain source-compatible.
