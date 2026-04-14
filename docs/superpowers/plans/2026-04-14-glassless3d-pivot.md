# Glassless3D Pivot Plan — OpenTrack + Custom Shader

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the glassless 3D system by adding an OpenTrack-compatible FreeTrack writer, a head-tracked parallax HLSL shader, and a C++ ReShade addon that bridges them — so the user can play WoW (and any DirectX game) in glasses-free 3D using their webcam.

**Architecture:** Our Python tracker (already built) writes head X/Y/Z to the industry-standard FreeTrack shared memory segment (`FT_SharedMem`). A C++ ReShade addon reads that segment once per frame and injects the values as uniforms into our HLSL shader. The shader uses the game's depth buffer to compute a per-pixel parallax offset: objects nearer than the convergence plane shift toward the viewer; objects beyond it recede. The user can also substitute **OpenTrack** (external app) as a drop-in tracker replacement — both write the same `FT_SharedMem` format.

**Tech Stack:** Python 3.11, MediaPipe, OpenCV, ctypes (FreeTrack shm), ReShade 5.9 HLSL, C++17/MSVC 2022, CMake 3.20+, pytest

---

## Already Completed

| Task | Status | What was built |
|------|--------|----------------|
| Task 1 | ✅ done | Scaffold, config.yaml, .gitignore, requirements |
| Task 2 | ✅ done | `tracker/smoother.py` — Kalman filter (1D + HeadSmoother) |
| Task 3 | ✅ done | `tracker/face_tracker.py` — MediaPipe head pose → cm |
| Task 4 | ✅ done | `tracker/shared_memory.py` — Windows Named Shared Memory writer (custom G3D format, kept for tests) |

---

## File Map (remaining work)

```
Glassless 3d/
├── tracker/
│   ├── freetrack.py         # NEW: writes FT_SharedMem in FreeTrack format (OpenTrack-compatible)
│   └── main.py              # NEW: entry point — webcam loop → FaceTracker → HeadSmoother → FreetracWriter
├── tests/
│   ├── test_freetrack.py    # NEW: FreeTrack writer tests
│   └── test_main.py         # NEW: TrackingLoop unit tests (mocked deps)
├── shaders/
│   ├── Glassless3D.fxh      # NEW: depth helpers + G3D_ParallaxOffset()
│   └── Glassless3D.fx       # NEW: full parallax effect shader
├── addon/
│   ├── Glassless3D.cpp      # NEW: ReShade addon — reads FT_SharedMem → sets uniforms
│   ├── CMakeLists.txt       # NEW
│   └── build.bat            # NEW
├── profiles/
│   ├── default.json         # NEW: generic depth settings
│   └── wow.json             # NEW: WoW reversed-depth settings
└── setup.py                 # NEW: copies addon + shader into game dir
```

---

## Task 5: FreeTrack Writer

**Why:** The FreeTrack shared memory protocol (`FT_SharedMem`) is the standard that both OpenTrack and FreeTrack-aware games/apps understand. Writing to it means the user can substitute OpenTrack at any time with no addon changes.

**Files:**
- Create: `tracker/freetrack.py`
- Create: `tests/test_freetrack.py`

**FreeTrack struct reference** (from opentrack's `fttypes.h`):
```
offset  type      field
0       uint32    DataID        — sequence number, incremented each write
4       int32     CamWidth      — 0
8       int32     CamHeight     — 0
12      float     Yaw           — 0.0 (we only use translation)
16      float     Pitch         — 0.0
20      float     Roll          — 0.0
24      float     X             — head X in cm (right = positive)
28      float     Y             — head Y in cm (up = positive)
32      float     Z             — head Z in cm (distance from screen)
36      float     RawYaw        — 0.0
40      float     RawPitch      — 0.0
44      float     RawRoll       — 0.0
48      float     RawX          — 0.0
52      float     RawY          — 0.0
56      float     RawZ          — 0.0
60–91   8×float   tracking pts  — all 0.0
Total: 92 bytes
```

Shared memory name: `"FT_SharedMem"`. Mutex name: `"FT_Mutext"` (typo in original — keep it).

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_freetrack.py
import ctypes
import struct

from tracker.freetrack import FreetracWriter, FREETRACK_FORMAT, FREETRACK_SIZE


def test_struct_size():
    """FreeTrack struct must be exactly 92 bytes."""
    assert FREETRACK_SIZE == 92


def test_writer_default_on_init():
    """Writer should initialise X=0, Y=0, Z=60 and DataID=0 on construction."""
    with FreetracWriter(name="FT_TEST_DEFAULT") as writer:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "FT_TEST_DEFAULT")
        assert h, "Could not open shared memory"
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, FREETRACK_SIZE)
        assert view, "Could not map view"
        raw = (ctypes.c_char * FREETRACK_SIZE).from_address(view)
        fields = struct.unpack(FREETRACK_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        # fields: DataID, CamW, CamH, Yaw, Pitch, Roll, X, Y, Z, RawYaw…RawZ, 8 pts
        data_id, _, _, yaw, pitch, roll, x, y, z = fields[:9]
        assert data_id == 0
        assert x == 0.0 and y == 0.0 and z == 60.0
        assert yaw == 0.0 and pitch == 0.0 and roll == 0.0


def test_writer_write_advances_data_id():
    """DataID must increment with each write so readers can detect new data."""
    with FreetracWriter(name="FT_TEST_DATAID") as writer:
        writer.write(x=1.0, y=2.0, z=50.0)
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "FT_TEST_DATAID")
        assert h
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, FREETRACK_SIZE)
        assert view
        raw = (ctypes.c_char * FREETRACK_SIZE).from_address(view)
        fields = struct.unpack(FREETRACK_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        data_id = fields[0]
        assert data_id == 1  # first write after init increments to 1


def test_writer_write_and_read_back():
    """Written X/Y/Z values must be readable via a second mapping."""
    with FreetracWriter(name="FT_TEST_RW") as writer:
        writer.write(x=3.5, y=-1.2, z=58.0)
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenFileMappingW.restype = ctypes.c_void_p
        kernel32.MapViewOfFile.restype = ctypes.c_void_p
        h = kernel32.OpenFileMappingW(0x0004, False, "FT_TEST_RW")
        assert h
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, FREETRACK_SIZE)
        assert view
        raw = (ctypes.c_char * FREETRACK_SIZE).from_address(view)
        fields = struct.unpack(FREETRACK_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        _, _, _, _, _, _, x, y, z = fields[:9]
        assert abs(x - 3.5) < 1e-5
        assert abs(y - (-1.2)) < 1e-5
        assert abs(z - 58.0) < 1e-4
```

- [ ] **Step 5.2: Run test to verify it fails**

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python -m pytest tests/test_freetrack.py -v
```

Expected: `ImportError: cannot import name 'FreetracWriter'`

- [ ] **Step 5.3: Implement `tracker/freetrack.py`**

```python
# tracker/freetrack.py
"""
Writes head pose to the FreeTrack shared memory segment (FT_SharedMem).

Layout matches opentrack's fttypes.h FTData struct:
  uint32 DataID | int32 CamW | int32 CamH |
  float Yaw Pitch Roll X Y Z |
  float RawYaw RawPitch RawRoll RawX RawY RawZ |
  float X1 Y1 X2 Y2 X3 Y3 X4 Y4
Total: 92 bytes

Both this module and OpenTrack write to "FT_SharedMem".
The ReShade addon reads DataID + X/Y/Z from offset 0/24/28/32.
"""
import ctypes
import struct

# <Iii  = uint32 DataID, int32 CamW, int32 CamH
# 6f    = Yaw Pitch Roll X Y Z
# 6f    = RawYaw RawPitch RawRoll RawX RawY RawZ
# 8f    = 8 tracking point floats (X1,Y1 ... X4,Y4)
FREETRACK_FORMAT = "<Iii6f6f8f"
FREETRACK_SIZE = struct.calcsize(FREETRACK_FORMAT)  # 92 bytes

_SHM_NAME = "FT_SharedMem"

_PAGE_READWRITE = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_INVALID_HANDLE = ctypes.c_void_p(-1)

_k32 = ctypes.windll.kernel32

# NOTE: These mutate the process-global windll.kernel32 object.
# All callers in this process inherit these restype/argtypes settings.
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.argtypes = [ctypes.c_void_p]


class FreetracWriter:
    """
    Writes head pose to Windows Named Shared Memory in FreeTrack format.

    Default name is "FT_SharedMem" (standard FreeTrack/OpenTrack protocol).
    Pass a different name only for testing.
    """

    def __init__(self, name: str = _SHM_NAME) -> None:
        self._name = name
        self._seq: int = 0
        self._handle: int | None = None
        self._view: int | None = None

        self._handle = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, FREETRACK_SIZE, name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_ALL_ACCESS, 0, 0, FREETRACK_SIZE,
        )
        if not self._view:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(err)

        # Initialise: head centred, 60 cm away, DataID = 0
        self._write_raw(seq=0, x=0.0, y=0.0, z=60.0)

    def write(self, x: float, y: float, z: float) -> None:
        """Write head position. Increments DataID so readers detect new data."""
        self._seq = (self._seq + 1) & 0xFFFF_FFFF
        self._write_raw(seq=self._seq, x=x, y=y, z=z)

    def _write_raw(self, seq: int, x: float, y: float, z: float) -> None:
        view = self._view
        if view is None:
            raise RuntimeError("write() called after close()")
        data = struct.pack(
            FREETRACK_FORMAT,
            seq,   # DataID
            0, 0,  # CamWidth, CamHeight
            0.0, 0.0, 0.0,  # Yaw, Pitch, Roll
            x, y, z,        # X, Y, Z (cm)
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # Raw pose (unused)
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,  # Tracking pts (unused)
        )
        ctypes.memmove(view, data, FREETRACK_SIZE)

    def close(self) -> None:
        """Release shared memory mapping and handle."""
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "FreetracWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
```

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_freetrack.py -v
```

Expected: 4 tests PASS.

Also run the full suite to check for regressions:

```bash
.venv/Scripts/python -m pytest tests/ -v
```

Expected: All 19 + 4 = 23 tests PASS.

- [ ] **Step 5.5: Commit**

```bash
git add tracker/freetrack.py tests/test_freetrack.py
git commit -m "feat: add FreeTrack shared memory writer (OpenTrack-compatible FT_SharedMem)"
```

---

## Task 6: Main Tracking Loop

**Why:** Ties together FaceTracker, HeadSmoother, and FreetracWriter into a runnable script. Handles face-lost hold (keeps last known position for `hold_ms` before resetting to centre).

**Files:**
- Create: `tracker/main.py`
- Create: `tests/test_main.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_main.py
import threading
from unittest.mock import MagicMock

from tracker.main import TrackingLoop
from tracker.face_tracker import HeadPosition


def test_tracking_loop_terminates_after_max_frames():
    """Loop must stop after exactly max_frames frames and not hang."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None  # no face

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (0.0, 0.0, 60.0)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )
    loop.run(camera_index=0, max_frames=5)
    assert mock_tracker.process_frame.call_count == 5


def test_tracking_loop_writes_default_when_no_face_and_hold_expired():
    """When face is lost and hold_ms=0, loop writes (0, 0, 60) immediately."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (0.0, 0.0, 60.0)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=0,
    )
    loop.run(camera_index=0, max_frames=3)

    for call in mock_writer.write.call_args_list:
        assert call.kwargs["z"] == 60.0


def test_tracking_loop_smooths_face_position():
    """When a face is detected, smoother.update() is called with the detected position."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = HeadPosition(
        x_cm=5.0, y_cm=-2.0, z_cm=55.0
    )

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (4.9, -1.9, 55.1)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )
    loop.run(camera_index=0, max_frames=1)

    mock_smoother.update.assert_called_once_with(5.0, -2.0, 55.0)
    mock_writer.write.assert_called_once_with(x=4.9, y=-1.9, z=55.1)
```

- [ ] **Step 6.2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_main.py -v
```

Expected: `ImportError: cannot import name 'TrackingLoop'`

- [ ] **Step 6.3: Implement `tracker/main.py`**

```python
# tracker/main.py
import argparse
import time
from typing import Optional

import cv2
import yaml

from tracker.face_tracker import FaceTracker, HeadPosition
from tracker.freetrack import FreetracWriter
from tracker.smoother import HeadSmoother


class TrackingLoop:
    """Reads webcam frames, tracks head pose, smooths, and writes to FT_SharedMem."""

    def __init__(
        self,
        tracker: FaceTracker,
        writer: FreetracWriter,
        smoother: HeadSmoother,
        hold_ms: int = 500,
    ) -> None:
        self._tracker = tracker
        self._writer = writer
        self._smoother = smoother
        self._hold_ms = hold_ms
        self._last_face_ms: Optional[float] = None

    def run(self, camera_index: int = 0, max_frames: Optional[int] = None) -> None:
        """Run the tracking loop. Blocks until max_frames reached or Ctrl+C."""
        cap = cv2.VideoCapture(camera_index)
        frame_count = 0
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    break

                pos: Optional[HeadPosition] = self._tracker.process_frame(frame)

                if pos is not None:
                    self._last_face_ms = time.monotonic() * 1000.0
                    x, y, z = self._smoother.update(pos.x_cm, pos.y_cm, pos.z_cm)
                else:
                    now_ms = time.monotonic() * 1000.0
                    hold_expired = (
                        self._last_face_ms is None
                        or now_ms - self._last_face_ms > self._hold_ms
                    )
                    if hold_expired:
                        x, y, z = 0.0, 0.0, 60.0
                    else:
                        x, y, z = self._smoother.update(0.0, 0.0, 60.0)

                self._writer.write(x=x, y=y, z=z)
                frame_count += 1
                if max_frames is not None and frame_count >= max_frames:
                    break
        finally:
            cap.release()


def _load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="Glassless3D head tracker")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    cam = cfg["camera"]
    scr = cfg["screen"]
    trk = cfg["tracking"]

    tracker = FaceTracker(
        real_ipd_cm=trk["ipd_cm"],
        screen_width_cm=scr["width_cm"],
        screen_height_cm=scr["height_cm"],
    )
    smoother = HeadSmoother(
        process_noise=trk["smoothing_q"],
        measurement_noise=trk["smoothing_r"],
    )

    print(f"[G3D] Starting tracker — camera {cam['index']}")
    print("[G3D] Writing to FT_SharedMem (FreeTrack protocol)")
    print("[G3D] Press Ctrl+C to stop.")

    with FreetracWriter() as writer:
        loop = TrackingLoop(
            tracker=tracker,
            writer=writer,
            smoother=smoother,
            hold_ms=trk["hold_ms"],
        )
        try:
            loop.run(camera_index=cam["index"])
        except KeyboardInterrupt:
            print("\n[G3D] Stopped.")
        finally:
            tracker.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_main.py -v
```

Expected: 3 tests PASS.

Run full suite:

```bash
.venv/Scripts/python -m pytest tests/ -v
```

Expected: All 26 tests PASS.

- [ ] **Step 6.5: Commit**

```bash
git add tracker/main.py tests/test_main.py
git commit -m "feat: add main tracking loop with face-lost hold and FreeTrack output"
```

---

## Task 7: HLSL Shader

> **Note:** HLSL shaders cannot be pytest-tested. Visual testing instructions are at the end of this task.

**Files:**
- Create: `shaders/Glassless3D.fxh`
- Create: `shaders/Glassless3D.fx`

**How the effect works:**
- The depth buffer gives a [0,1] value per pixel: 0 = near, 1 = far
- Head position in UV space: `head_uv = (head_x / screen_w_cm, -head_y / screen_h_cm)`
- Parallax offset per pixel: `offset = head_uv * (1 − depth / convergence) * strength`
  - depth < convergence → factor > 0 → shifts same direction as head (pops out)
  - depth > convergence → factor < 0 → shifts opposite direction (recedes)
  - depth == convergence → no shift (sits on screen)
- Sample the back buffer at `uv + offset` for the final colour

- [ ] **Step 7.1: Create `shaders/Glassless3D.fxh`**

```hlsl
// shaders/Glassless3D.fxh
// Shared math for Glassless3D.fx

// Compute the UV-space parallax offset for one pixel.
//   head_uv    : head X/Y normalised to UV fraction
//                 = float2(head_x_cm / screen_w_cm, -head_y_cm / screen_h_cm)
//   depth      : linearised depth [0,1] (0=near, 1=far)
//   convergence: depth plane that appears "on" the screen [0,1]
//   strength   : overall scale factor (0=off, 1=full)
float2 G3D_ParallaxOffset(
    float2 head_uv,
    float  depth,
    float  convergence,
    float  strength)
{
    // Objects at convergence → zero offset (appear on-screen).
    // Objects closer          → positive offset (pop toward viewer).
    // Objects further         → negative offset (recede).
    float factor = (1.0 - depth / max(convergence, 0.001)) * strength;
    return head_uv * factor;
}
```

- [ ] **Step 7.2: Create `shaders/Glassless3D.fx`**

```hlsl
// shaders/Glassless3D.fx
// Glassless3D — head-tracked depth parallax for ReShade 5.9+
// Requires Glassless3D.addon (reads FT_SharedMem) running alongside.

#include "ReShade.fxh"
#include "Glassless3D.fxh"

// ── Uniforms set by Glassless3D.addon each frame ───────────────────────────
// Defaults produce a no-op effect when the addon / tracker is not running.
uniform float g3d_HeadX < source = "g3d_HeadX"; > = 0.0;
uniform float g3d_HeadY < source = "g3d_HeadY"; > = 0.0;
uniform float g3d_HeadZ < source = "g3d_HeadZ"; > = 60.0;

// ── User-tunable parameters (ReShade UI overlay) ──────────────────────────
uniform float ScreenWidthCM <
    ui_category = "Screen Setup";
    ui_type     = "slider";
    ui_label    = "Screen Width (cm)";
    ui_tooltip  = "Measure your monitor with a ruler.";
    ui_min = 20.0; ui_max = 120.0; ui_step = 0.5;
> = 59.8;

uniform float ScreenHeightCM <
    ui_category = "Screen Setup";
    ui_type     = "slider";
    ui_label    = "Screen Height (cm)";
    ui_min = 10.0; ui_max = 70.0; ui_step = 0.5;
> = 33.6;

uniform float ConvergenceDist <
    ui_category = "3D Effect";
    ui_type     = "slider";
    ui_label    = "Convergence Distance (0-1)";
    ui_tooltip  = "Depth plane that sits on the screen. "
                  "0=near, 1=far. Start at 0.5, adjust until "
                  "mid-scene objects feel flat.";
    ui_min = 0.01; ui_max = 0.99; ui_step = 0.01;
> = 0.50;

uniform float EffectStrength <
    ui_category = "3D Effect";
    ui_type     = "slider";
    ui_label    = "Effect Strength";
    ui_tooltip  = "Increase for more 3D. Reduce if ghosting appears.";
    ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
> = 0.30;

uniform bool ShowDepthBuffer <
    ui_category = "Debug";
    ui_label    = "Show Depth Buffer";
    ui_tooltip  = "Greyscale depth view. Dark=near, white=far. "
                  "If all one colour, depth isn't accessible.";
> = false;

// ── Pixel shader ─────────────────────────────────────────────────────────
float4 PS_Glassless3D(float4 pos : SV_Position, float2 uv : TEXCOORD) : SV_Target
{
    float depth = ReShade::GetLinearizedDepth(uv);

    if (ShowDepthBuffer)
        return float4(depth, depth, depth, 1.0);

    // Convert head cm → UV-space fraction; flip Y (image Y grows down, world Y up)
    float2 head_uv = float2(
         g3d_HeadX / max(ScreenWidthCM,  1.0),
        -g3d_HeadY / max(ScreenHeightCM, 1.0)
    );

    float2 offset    = G3D_ParallaxOffset(head_uv, depth, ConvergenceDist, EffectStrength);
    float2 sampleUV  = saturate(uv + offset);

    return tex2D(ReShade::BackBuffer, sampleUV);
}

// ── Technique ─────────────────────────────────────────────────────────────
technique Glassless3D <
    ui_label   = "Glassless3D";
    ui_tooltip = "Head-tracked parallax 3D effect. "
                 "Requires Glassless3D.addon + tracker (or OpenTrack) running.";
>
{
    pass
    {
        VertexShader = PostProcessVS;
        PixelShader  = PS_Glassless3D;
    }
}
```

- [ ] **Step 7.3: Visual test in ReShade** (after Task 9 build is done)

1. Launch WoW. Open ReShade overlay (Home key).
2. Enable the `Glassless3D` technique.
3. Enable **Show Depth Buffer** — you should see a greyscale image (dark=near, light=far). If all one colour, the depth buffer isn't accessible; check Task 9 game profile settings.
4. Disable **Show Depth Buffer**.
5. Set **Effect Strength** = 0.8 (exaggerated for testing).
6. Move your head left/right — the scene should shift with you.
7. Adjust **Convergence Distance** until mid-range objects feel stationary.
8. Reduce **Effect Strength** to 0.3 for comfortable play.

- [ ] **Step 7.4: Commit**

```bash
git add shaders/
git commit -m "feat: add HLSL parallax shader with depth-based 3D warp and ReShade UI controls"
```

---

## Task 8: C++ ReShade Addon

**What it does:** On every frame, reads head X/Y/Z from `FT_SharedMem` and calls `set_uniform_value_float` for each of the three uniforms in `Glassless3D.fx`. Falls back to safe defaults (0,0,60) when the tracker is not running.

**Prerequisites before building:**
- Visual Studio 2022 with "Desktop development with C++" workload
- CMake 3.20+ on PATH
- ReShade SDK headers in `vendor/reshade/include/`

**Get ReShade SDK headers** (run once before building):
```bash
cd "E:/Glassless 3d"
.venv/Scripts/python -c "
import os, urllib.request, zipfile, shutil
VER = '5.9.2'
url = f'https://github.com/crosire/reshade/archive/refs/tags/v{VER}.zip'
os.makedirs('vendor', exist_ok=True)
print('Downloading...')
urllib.request.urlretrieve(url, 'vendor/_src.zip')
with zipfile.ZipFile('vendor/_src.zip') as z:
    prefix = f'reshade-{VER}/include/'
    for m in z.namelist():
        if m.startswith(prefix) and m != prefix:
            rel = m[len(prefix):]
            dst = os.path.join('vendor/reshade/include', rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with z.open(m) as src, open(dst, 'wb') as out:
                shutil.copyfileobj(src, out)
os.remove('vendor/_src.zip')
print('Done: vendor/reshade/include/')
"
```

Expected output: `Done: vendor/reshade/include/`

Verify: `ls vendor/reshade/include/` shows `reshade.hpp` and other `.hpp` files.

**Files:**
- Create: `addon/Glassless3D.cpp`
- Create: `addon/CMakeLists.txt`
- Create: `addon/build.bat`

- [ ] **Step 8.1: Create `addon/Glassless3D.cpp`**

```cpp
// addon/Glassless3D.cpp
// Glassless3D ReShade Addon
//
// Reads head pose from FT_SharedMem (FreeTrack / OpenTrack protocol)
// and injects it into Glassless3D.fx uniforms before each frame's effects.
//
// The tracker (tracker/main.py or OpenTrack) must be running to provide data.
// When it is not, uniforms default to (0, 0, 60) — a neutral no-op.

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
#include <cstring>
#include <cstdint>

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
    if (TryOpenSharedMemory() && s_pView)
        std::memcpy(&d, s_pView, sizeof(FTData));
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
BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID)
{
    switch (reason)
    {
    case DLL_PROCESS_ATTACH:
        reshade::register_event<reshade::addon_event::begin_effects>(&on_begin_effects);
        break;
    case DLL_PROCESS_DETACH:
        reshade::unregister_event<reshade::addon_event::begin_effects>(&on_begin_effects);
        CloseSharedMemory();
        break;
    }
    return TRUE;
}
```

- [ ] **Step 8.2: Create `addon/CMakeLists.txt`**

```cmake
cmake_minimum_required(VERSION 3.20)
project(Glassless3D LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(RESHADE_INCLUDE "${CMAKE_SOURCE_DIR}/../vendor/reshade/include"
    CACHE PATH "Path to ReShade SDK include directory")

add_library(Glassless3D SHARED Glassless3D.cpp)

target_include_directories(Glassless3D PRIVATE ${RESHADE_INCLUDE})

target_compile_definitions(Glassless3D PRIVATE
    WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)

set_target_properties(Glassless3D PROPERTIES
    SUFFIX ".addon"
    PREFIX "")

target_compile_options(Glassless3D PRIVATE $<$<CONFIG:Release>:/O2 /GL>)
target_link_options(Glassless3D   PRIVATE $<$<CONFIG:Release>:/LTCG>)
```

- [ ] **Step 8.3: Create `addon/build.bat`**

```bat
@echo off
setlocal

echo === Glassless3D Addon Build ===

if not exist "..\vendor\reshade\include\reshade.hpp" (
    echo ERROR: ReShade SDK headers not found.
    echo Run the Python one-liner in Task 8 of the plan first.
    exit /b 1
)

if not exist build mkdir build
cd build

cmake .. -G "Visual Studio 17 2022" -A x64
if errorlevel 1 ( echo ERROR: CMake configure failed. & exit /b 1 )

cmake --build . --config Release
if errorlevel 1 ( echo ERROR: Build failed. & exit /b 1 )

copy /Y Release\Glassless3D.addon ..\..\
echo.
echo === Build complete: Glassless3D.addon ===
```

- [ ] **Step 8.4: Build the addon**

```bat
cd "E:\Glassless 3d\addon"
build.bat
```

Expected output ends with:
```
=== Build complete: Glassless3D.addon ===
```

Verify `E:\Glassless 3d\Glassless3D.addon` exists.

- [ ] **Step 8.5: Commit**

```bash
git add addon/ vendor/reshade/include/
git commit -m "feat: add C++ ReShade addon that reads FreeTrack shm and sets parallax uniforms"
```

---

## Task 9: Game Profiles + Setup Script

**Files:**
- Create: `profiles/default.json`
- Create: `profiles/wow.json`
- Create: `setup.py`

- [ ] **Step 9.1: Create `profiles/default.json`**

```json
{
  "name": "Default",
  "notes": "Generic fallback for any DirectX game. Tune ConvergenceDist in the ReShade UI.",
  "reshade": {
    "RESHADE_DEPTH_INPUT_IS_REVERSED": 0,
    "RESHADE_DEPTH_INPUT_IS_LOGARITHMIC": 0,
    "RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN": 0,
    "RESHADE_DEPTH_LINEARIZATION_FAR_PLANE": 1000.0
  },
  "shader_defaults": {
    "ConvergenceDist": 0.50,
    "EffectStrength": 0.30
  }
}
```

- [ ] **Step 9.2: Create `profiles/wow.json`**

```json
{
  "name": "World of Warcraft",
  "notes": "WoW (Dragonflight / TWW) on DirectX 11. Reversed depth buffer. Verified on patch 11.x.",
  "reshade": {
    "RESHADE_DEPTH_INPUT_IS_REVERSED": 1,
    "RESHADE_DEPTH_INPUT_IS_LOGARITHMIC": 0,
    "RESHADE_DEPTH_INPUT_IS_UPSIDE_DOWN": 0,
    "RESHADE_DEPTH_LINEARIZATION_FAR_PLANE": 2000.0
  },
  "shader_defaults": {
    "ConvergenceDist": 0.45,
    "EffectStrength": 0.28
  },
  "setup": {
    "executable": "Wow.exe",
    "registry_key": "HKLM\\SOFTWARE\\WOW6432Node\\Blizzard Entertainment\\World of Warcraft",
    "registry_value": "InstallPath",
    "common_paths": [
      "C:\\Program Files (x86)\\World of Warcraft\\_retail_",
      "D:\\World of Warcraft\\_retail_",
      "E:\\World of Warcraft\\_retail_"
    ]
  }
}
```

- [ ] **Step 9.3: Create `setup.py`**

```python
#!/usr/bin/env python3
# setup.py
# Copies Glassless3D.addon + shaders into a game directory,
# and updates ReShade.ini with the correct depth buffer settings.
#
# Usage:
#   python setup.py --game wow
#   python setup.py --game-dir "C:\Games\MyGame" --profile default
#   python setup.py --game wow --dry-run

import argparse
import json
import os
import shutil
import sys
import winreg

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
SHADERS_DIR  = os.path.join(BASE_DIR, "shaders")
ADDON_PATH   = os.path.join(BASE_DIR, "Glassless3D.addon")

RESHADE_URL = "https://reshade.me/downloads/ReShade_Setup_5.9.2.exe"


def load_profile(name: str) -> dict:
    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if not os.path.exists(path):
        sys.exit(f"ERROR: Profile '{name}' not found at {path}")
    with open(path) as f:
        return json.load(f)


def find_game_dir(profile: dict) -> str:
    setup = profile.get("setup", {})

    reg_key = setup.get("registry_key", "")
    reg_val = setup.get("registry_value", "")
    if reg_key and reg_val:
        try:
            root_str, subkey = reg_key.split("\\", 1)
            root = {"HKLM": winreg.HKEY_LOCAL_MACHINE,
                    "HKCU": winreg.HKEY_CURRENT_USER}[root_str]
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, reg_val)
                if os.path.isdir(value):
                    return value
        except (FileNotFoundError, OSError, KeyError):
            pass

    for path in setup.get("common_paths", []):
        if os.path.isdir(path):
            return path

    sys.exit(
        "ERROR: Could not find game directory automatically.\n"
        "Use --game-dir to specify it manually."
    )


def apply_depth_settings(game_dir: str, profile: dict, dry_run: bool) -> None:
    ini_path = os.path.join(game_dir, "ReShade.ini")
    settings = profile.get("reshade", {})

    lines: list[str] = []
    if os.path.exists(ini_path):
        with open(ini_path) as f:
            lines = f.readlines()

    # Strip any existing PREPROCESSOR keys, then append fresh block
    kept = [l for l in lines
            if not any(l.startswith(k) for k in settings)
            and not l.strip() == "[PREPROCESSOR]"]
    block = ["[PREPROCESSOR]\n"] + [f"{k}={v}\n" for k, v in settings.items()]

    if dry_run:
        print(f"  [dry-run] Would write to {ini_path}:")
        for line in block:
            print(f"    {line}", end="")
    else:
        with open(ini_path, "w") as f:
            f.writelines(kept + block)
        print(f"  ✓ Updated {ini_path}")


def install(game_dir: str, profile: dict, dry_run: bool) -> None:
    print(f"\nInstalling to: {game_dir}")
    print(f"Profile:       {profile['name']}")
    if dry_run:
        print("(DRY RUN — no files written)\n")

    # Addon
    dst_addon = os.path.join(game_dir, "Glassless3D.addon")
    if dry_run:
        print(f"  [dry-run] Would copy {ADDON_PATH} → {dst_addon}")
    else:
        shutil.copy2(ADDON_PATH, dst_addon)
        print(f"  ✓ Copied addon → {dst_addon}")

    # Shaders
    shader_dst = os.path.join(game_dir, "reshade-shaders", "Shaders")
    if not dry_run:
        os.makedirs(shader_dst, exist_ok=True)
    for fname in ["Glassless3D.fx", "Glassless3D.fxh"]:
        src = os.path.join(SHADERS_DIR, fname)
        dst = os.path.join(shader_dst, fname)
        if dry_run:
            print(f"  [dry-run] Would copy {src} → {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"  ✓ Copied {fname}")

    apply_depth_settings(game_dir, profile, dry_run)

    print("\n── Next steps ────────────────────────────────────────────────")
    print(f"  1. Install ReShade into {game_dir} if not already done:")
    print(f"     Download: {RESHADE_URL}")
    print("     Run installer → select Wow.exe → choose DirectX 11")
    print("  2. Start the tracker:   python tracker/main.py")
    print("     (or use OpenTrack with NeuralNet tracker + FreeTrack output)")
    print("  3. Launch the game.")
    print("  4. Press Home → ReShade overlay → enable 'Glassless3D'.")
    print("  5. Enjoy!\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Glassless3D installer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--game",     help="Profile name (wow, default)")
    group.add_argument("--game-dir", help="Path to game directory")
    parser.add_argument("--profile", default="default",
                        help="Profile to use with --game-dir")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not os.path.exists(ADDON_PATH):
        sys.exit("ERROR: Glassless3D.addon not found.\nBuild it: cd addon && build.bat")

    if args.game:
        profile  = load_profile(args.game)
        game_dir = find_game_dir(profile)
    else:
        profile  = load_profile(args.profile)
        game_dir = os.path.abspath(args.game_dir)
        if not os.path.isdir(game_dir):
            sys.exit(f"ERROR: Directory not found: {game_dir}")

    install(game_dir, profile, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 9.4: Test dry-run (no files written)**

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python setup.py --game-dir "C:/tmp/test" --profile default --dry-run
```

Expected: Prints what would happen with no errors and no files written.

- [ ] **Step 9.5: Commit**

```bash
git add profiles/ setup.py
git commit -m "feat: add WoW + default game profiles and setup installer script"
```

---

## Task 10: Full Integration Run

Wire everything together for the first live end-to-end test.

- [ ] **Step 10.1: Install ReShade into WoW**

Download ReShade 5.9.2 from `https://reshade.me` and run the installer:
- Select `Wow.exe` from your `_retail_` directory
- Choose **DirectX 10/11/12**
- Uncheck all built-in effect packages

- [ ] **Step 10.2: Run the setup script**

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python setup.py --game wow
```

Expected: Copies addon and shaders, updates ReShade.ini with WoW depth settings.

- [ ] **Step 10.3: Start the Python tracker**

Open a terminal and leave it running:

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python tracker/main.py
```

Expected: `[G3D] Starting tracker — camera 0`

Alternatively, run **OpenTrack** instead:
- Input: NeuralNet Tracker (webcam)
- Output: FreeTrack 2.0 (writes `FT_SharedMem`)
- Enable filter: "Accela" or "Kalman" for smoothing
- Start tracking

- [ ] **Step 10.4: Launch WoW and enable the effect**

1. Log in and enter any outdoor zone.
2. Press **Home** to open the ReShade overlay.
3. Enable **Glassless3D** in the technique list.
4. Enable **Show Depth Buffer** — confirm you see a greyscale depth image. Disable after confirming.

- [ ] **Step 10.5: Calibrate**

In the ReShade UI:
1. Set **Screen Width** and **Screen Height** to your physical monitor measurements
2. Move your head left/right — the scene should shift
3. Adjust **Convergence Distance** until ground/mid-range objects feel stationary
4. Set **Effect Strength** to 0.25–0.35 for comfortable long-session play

- [ ] **Step 10.6: Final commit**

```bash
git add .
git commit -m "feat: complete Glassless3D v1 — FreeTrack-compatible tracker, HLSL shader, ReShade addon"
```

---

## Self-Review

**Spec coverage:**
- ✅ Head tracking with face-lost hold → Tasks 5–6
- ✅ FreeTrack/OpenTrack compatibility → Task 5 (`FT_SharedMem`)
- ✅ HLSL parallax shader with depth buffer → Task 7
- ✅ C++ ReShade addon bridges shm → uniforms → Task 8
- ✅ WoW profile with reversed depth → Task 9
- ✅ Generic game support via profiles → Task 9
- ✅ Setup installer → Task 9
- ✅ OpenTrack as drop-in alternative → documented in Tasks 6 + 10

**Type consistency:**
- `FreetracWriter.write(x, y, z)` → `TrackingLoop` calls `writer.write(x=x, y=y, z=z)` ✅
- `HeadPosition.x_cm/y_cm/z_cm` → `TrackingLoop` unpacks as `pos.x_cm, pos.y_cm, pos.z_cm` ✅
- Shader uniform names `g3d_HeadX/Y/Z` → C++ addon `set("g3d_HeadX", d.X)` etc. ✅
- FreeTrack X/Y/Z offsets in struct match Python `FREETRACK_FORMAT` layout ✅

**Placeholder scan:** None found.
