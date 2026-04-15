# Glassless3D Customization System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an Advanced tab to the launcher with shader tuning, auto-calibration, presets, and tracker calibration — all backed by an expanded v2 shared-memory settings struct (56 bytes).

**Architecture:** Expand G3D_Settings SHM from 20→56 bytes (v2). New parameters flow from the Advanced tab → SHM → overlay (depth curve, per-axis strength, focus radius) and → tracker thread (deadzone, smoothing, fov, ipd). Presets saved to config.yaml under `presets:` key. Screen calibration uses ctypes DPI APIs. Head distance uses MediaPipe on one webcam frame.

**Tech Stack:** Python 3.11, PySide6, ctypes (stdlib), MediaPipe, PyYAML, C++17 HLSL/D3D11, MinGW CMake

---

## File Map

| Action | File |
|---|---|
| Modify | `tracker/shared_settings.py` — expand struct to v2, add `SharedSettingsReader` |
| Modify | `tracker/smoother.py` — add `set_measurement_noise()` |
| Modify | `launcher/tracker_thread.py` — deadzone + live smoothing from SHM |
| Modify | `launcher/settings_gui.py` — migrate to v2 field names |
| Modify | `launcher/mainwindow.py` — add Advanced tab |
| Modify | `overlay/overlay.cpp` — new Settings struct, CBuf, shader |
| Create | `launcher/presets.py` |
| Create | `launcher/calibration.py` |
| Create | `tests/test_shared_settings.py` |
| Create | `tests/test_presets.py` |
| Create | `tests/test_calibration.py` |

---

### Task 1: Expand `tracker/shared_settings.py` to v2

**Files:**
- Modify: `tracker/shared_settings.py`
- Create: `tests/test_shared_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shared_settings.py
import struct
from tracker.shared_settings import STRUCT_FORMAT, STRUCT_SIZE, OverlaySettings

def test_struct_size_is_56():
    assert STRUCT_SIZE == 56

def test_struct_format_roundtrip():
    s = OverlaySettings(
        strength_x=1.5, strength_y=2.0, virtual_depth_cm=30.0,
        screen_w_cm=59.8, screen_h_cm=33.6, depth_curve=1,
        depth_gamma=1.0, focus_radius=0.1, head_dist_cm=60.0,
        camera_fov_deg=90.0, ipd_mm=64.0, smoothing_alpha=0.1,
        deadzone_mm=5.0,
    )
    data = struct.pack(
        STRUCT_FORMAT,
        s.strength_x, s.strength_y, s.virtual_depth_cm, s.screen_w_cm,
        s.screen_h_cm, s.depth_curve, s.depth_gamma, s.focus_radius,
        s.head_dist_cm, s.camera_fov_deg, s.ipd_mm, s.smoothing_alpha,
        s.deadzone_mm, 1,
    )
    assert len(data) == 56
    out = struct.unpack(STRUCT_FORMAT, data)
    assert abs(out[0] - 1.5) < 1e-5
    assert out[5] == 1       # depth_curve uint32
    assert abs(out[11] - 0.1) < 1e-5  # smoothing_alpha
```

- [ ] **Step 2: Run to see it fail**

```bash
cd "E:/Glassless 3d"
python -m pytest tests/test_shared_settings.py -v
```
Expected: `ImportError` — `OverlaySettings` has wrong fields.

- [ ] **Step 3: Replace `tracker/shared_settings.py` entirely**

```python
# tracker/shared_settings.py
"""Shared-memory channel for live overlay tuning (v2, 56 bytes).

Layout (56 bytes, little-endian):
    float   strength_x
    float   strength_y
    float   virtual_depth_cm
    float   screen_w_cm        (0 = overlay autodetect)
    float   screen_h_cm        (0 = overlay autodetect)
    uint32  depth_curve        (0=linear, 1=sqrt, 2=gamma)
    float   depth_gamma
    float   focus_radius       (UV radius for focus ring)
    float   head_dist_cm
    float   camera_fov_deg
    float   ipd_mm
    float   smoothing_alpha    (Kalman measurement noise r)
    float   deadzone_mm
    uint32  version            (monotonic counter)
"""
from __future__ import annotations

import ctypes
import struct
from dataclasses import dataclass

STRUCT_FORMAT = "<fffffIfffffffI"
STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 56
SHM_NAME = "G3D_Settings"

_PAGE_READWRITE   = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_FILE_MAP_READ    = 0x0004
_INVALID_HANDLE   = ctypes.c_void_p(-1)

_k32 = ctypes.windll.kernel32
_k32.CreateFileMappingW.restype = ctypes.c_void_p
_k32.OpenFileMappingW.restype   = ctypes.c_void_p
_k32.MapViewOfFile.restype      = ctypes.c_void_p
_k32.UnmapViewOfFile.argtypes   = [ctypes.c_void_p]
_k32.CloseHandle.argtypes       = [ctypes.c_void_p]


@dataclass(frozen=True)
class OverlaySettings:
    strength_x: float = 1.0
    strength_y: float = 1.0
    virtual_depth_cm: float = 30.0
    screen_w_cm: float = 0.0
    screen_h_cm: float = 0.0
    depth_curve: int = 1          # 0=linear, 1=sqrt, 2=gamma
    depth_gamma: float = 1.0
    focus_radius: float = 0.1
    head_dist_cm: float = 60.0
    camera_fov_deg: float = 90.0
    ipd_mm: float = 64.0
    smoothing_alpha: float = 0.1
    deadzone_mm: float = 5.0


class SharedSettingsWriter:
    """Creates and owns the G3D_Settings shared memory segment."""

    def __init__(self, name: str = SHM_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._version: int = 0

        self._handle = _k32.CreateFileMappingW(
            _INVALID_HANDLE, None, _PAGE_READWRITE, 0, STRUCT_SIZE, name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_ALL_ACCESS, 0, 0, STRUCT_SIZE,
        )
        if not self._view:
            err = ctypes.get_last_error()
            _k32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(err)

        self.write(OverlaySettings())

    def write(self, s: OverlaySettings) -> None:
        view = self._view
        if view is None:
            raise RuntimeError("write() called after close()")
        self._version = (self._version + 1) & 0xFFFF_FFFF
        data = struct.pack(
            STRUCT_FORMAT,
            float(s.strength_x), float(s.strength_y),
            float(s.virtual_depth_cm),
            float(s.screen_w_cm), float(s.screen_h_cm),
            int(s.depth_curve),
            float(s.depth_gamma), float(s.focus_radius),
            float(s.head_dist_cm), float(s.camera_fov_deg),
            float(s.ipd_mm), float(s.smoothing_alpha),
            float(s.deadzone_mm),
            self._version,
        )
        ctypes.memmove(view, data, STRUCT_SIZE)

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SharedSettingsWriter":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class SharedSettingsReader:
    """Opens G3D_Settings read-only. Safe to call even if writer not running."""

    def __init__(self, name: str = SHM_NAME) -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._try_attach()

    def _try_attach(self) -> None:
        if self._view:
            return
        if not self._handle:
            self._handle = _k32.OpenFileMappingW(_FILE_MAP_READ, False, self._name)
        if self._handle and not self._view:
            self._view = _k32.MapViewOfFile(
                self._handle, _FILE_MAP_READ, 0, 0, STRUCT_SIZE,
            )

    def read(self) -> OverlaySettings | None:
        """Return current settings snapshot, or None if writer not running."""
        self._try_attach()
        if not self._view:
            return None
        try:
            raw = (ctypes.c_char * STRUCT_SIZE).from_address(self._view)
            f = struct.unpack(STRUCT_FORMAT, bytes(raw))
            return OverlaySettings(
                strength_x=f[0], strength_y=f[1],
                virtual_depth_cm=f[2],
                screen_w_cm=f[3], screen_h_cm=f[4],
                depth_curve=f[5],
                depth_gamma=f[6], focus_radius=f[7],
                head_dist_cm=f[8], camera_fov_deg=f[9],
                ipd_mm=f[10], smoothing_alpha=f[11],
                deadzone_mm=f[12],
            )
        except Exception:
            return None

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SharedSettingsReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_shared_settings.py -v
```
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tracker/shared_settings.py tests/test_shared_settings.py
git commit -m "feat: expand G3D_Settings SHM to v2 (56 bytes, 13 tunable params)"
```

---

### Task 2: Create `launcher/presets.py`

**Files:**
- Create: `launcher/presets.py`
- Create: `tests/test_presets.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_presets.py
import pytest
from launcher.presets import list_presets, save_preset, load_preset, delete_preset

@pytest.fixture
def tmp_config(tmp_path):
    return str(tmp_path / "config.yaml")

def test_list_empty(tmp_config):
    assert list_presets(tmp_config) == []

def test_save_and_list(tmp_config):
    save_preset(tmp_config, "wow", {"strength_x": 1.5, "depth_curve": 1})
    assert "wow" in list_presets(tmp_config)

def test_load_roundtrip(tmp_config):
    save_preset(tmp_config, "test", {"strength_x": 2.0, "depth_gamma": 1.5})
    loaded = load_preset(tmp_config, "test")
    assert loaded["strength_x"] == 2.0
    assert loaded["depth_gamma"] == 1.5

def test_load_missing_raises(tmp_config):
    with pytest.raises(KeyError):
        load_preset(tmp_config, "nonexistent")

def test_delete(tmp_config):
    save_preset(tmp_config, "to_delete", {"strength_x": 1.0})
    delete_preset(tmp_config, "to_delete")
    assert "to_delete" not in list_presets(tmp_config)

def test_delete_missing_is_noop(tmp_config):
    delete_preset(tmp_config, "nonexistent")  # must not raise
```

- [ ] **Step 2: Run to see it fail**

```bash
python -m pytest tests/test_presets.py -v
```
Expected: `ModuleNotFoundError: No module named 'launcher.presets'`

- [ ] **Step 3: Create `launcher/presets.py`**

```python
# launcher/presets.py
"""Named preset management — stored under `presets:` key in config.yaml."""
from __future__ import annotations

from pathlib import Path
import yaml


def _read(config_path: str) -> dict:
    p = Path(config_path)
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def _write(config_path: str, cfg: dict) -> None:
    Path(config_path).write_text(yaml.safe_dump(cfg, sort_keys=False))


def list_presets(config_path: str) -> list[str]:
    return list((_read(config_path).get("presets") or {}).keys())


def save_preset(config_path: str, name: str, settings: dict) -> None:
    cfg = _read(config_path)
    cfg.setdefault("presets", {})[name] = settings
    _write(config_path, cfg)


def load_preset(config_path: str, name: str) -> dict:
    presets = (_read(config_path).get("presets") or {})
    if name not in presets:
        raise KeyError(f"Preset '{name}' not found in {config_path}")
    return dict(presets[name])


def delete_preset(config_path: str, name: str) -> None:
    cfg = _read(config_path)
    presets = cfg.get("presets") or {}
    presets.pop(name, None)
    cfg["presets"] = presets
    _write(config_path, cfg)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_presets.py -v
```
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/presets.py tests/test_presets.py
git commit -m "feat: add launcher/presets.py for named preset save/load/delete"
```

---

### Task 3: Create `launcher/calibration.py`

**Files:**
- Create: `launcher/calibration.py`
- Create: `tests/test_calibration.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_calibration.py
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
from launcher.calibration import detect_screen_cm, measure_head_distance

def test_detect_screen_cm_returns_floats():
    w, h = detect_screen_cm()
    assert isinstance(w, float) and isinstance(h, float)
    assert w >= 0.0 and h >= 0.0

def test_detect_screen_cm_nonzero_on_real_monitor():
    w, h = detect_screen_cm()
    if w == 0.0 and h == 0.0:
        pytest.skip("No physical monitor detected (headless/CI)")
    assert w > 10.0 and h > 5.0

def test_measure_head_distance_no_camera():
    import cv2
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    with patch.object(cv2, "VideoCapture", return_value=mock_cap):
        assert measure_head_distance(ipd_mm=64.0) == 60.0

def test_measure_head_distance_no_face():
    import cv2
    fake_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, fake_frame)
    with patch.object(cv2, "VideoCapture", return_value=mock_cap):
        with patch("launcher.calibration._detect_face_distance", return_value=None):
            assert measure_head_distance(ipd_mm=64.0) == 60.0
```

- [ ] **Step 2: Run to see it fail**

```bash
python -m pytest tests/test_calibration.py -v
```
Expected: `ModuleNotFoundError: No module named 'launcher.calibration'`

- [ ] **Step 3: Create `launcher/calibration.py`**

```python
# launcher/calibration.py
"""One-shot hardware calibration helpers."""
from __future__ import annotations

import ctypes
import math

import cv2
import numpy as np

_HORZSIZE   = 4
_VERTSIZE   = 6
_HORZRES    = 8
_VERTRES    = 10
_LOGPIXELSX = 88
_LOGPIXELSY = 90

_LEFT_IRIS  = 468
_RIGHT_IRIS = 473


def detect_screen_cm() -> tuple[float, float]:
    """Return (width_cm, height_cm) of the primary monitor via EDID/DPI.
    Returns (0.0, 0.0) on failure.
    """
    try:
        gdi32 = ctypes.windll.gdi32
        user32 = ctypes.windll.user32
        hdc = user32.GetDC(None)
        if not hdc:
            return 0.0, 0.0
        wmm   = gdi32.GetDeviceCaps(hdc, _HORZSIZE)
        hmm   = gdi32.GetDeviceCaps(hdc, _VERTSIZE)
        px_w  = gdi32.GetDeviceCaps(hdc, _HORZRES)
        px_h  = gdi32.GetDeviceCaps(hdc, _VERTRES)
        dpi_x = gdi32.GetDeviceCaps(hdc, _LOGPIXELSX)
        dpi_y = gdi32.GetDeviceCaps(hdc, _LOGPIXELSY)
        user32.ReleaseDC(None, hdc)
        edid_ok = wmm > 150 and hmm > 100 and not (wmm == 320 and hmm == 240)
        if edid_ok:
            return wmm / 10.0, hmm / 10.0
        if dpi_x > 0 and dpi_y > 0 and px_w > 0 and px_h > 0:
            return px_w / dpi_x * 2.54, px_h / dpi_y * 2.54
        return 0.0, 0.0
    except Exception:  # noqa: BLE001
        return 0.0, 0.0


def _detect_face_distance(frame_bgr: np.ndarray, ipd_mm: float) -> float | None:
    """Run MediaPipe on one BGR frame, return head distance in cm or None."""
    import mediapipe as mp
    from mediapipe import tasks
    import pathlib

    model_path = str(
        pathlib.Path(__file__).resolve().parent.parent
        / "models" / "face_landmarker.task"
    )
    options = tasks.vision.FaceLandmarkerOptions(
        base_options=tasks.BaseOptions(model_asset_path=model_path),
        running_mode=tasks.vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
    )
    with tasks.vision.FaceLandmarker.create_from_options(options) as lmk:
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = lmk.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))

    if not result.face_landmarks:
        return None

    lm = result.face_landmarks[0]
    left  = np.array([lm[_LEFT_IRIS].x * w,  lm[_LEFT_IRIS].y  * h])
    right = np.array([lm[_RIGHT_IRIS].x * w, lm[_RIGHT_IRIS].y * h])
    ipd_px = float(np.linalg.norm(right - left))
    if ipd_px < 1.0:
        return None

    focal_px = w / (2.0 * math.tan(math.radians(45.0)))  # assume 90° FOV
    return (focal_px * (ipd_mm / 10.0)) / ipd_px


def measure_head_distance(ipd_mm: float = 64.0) -> float:
    """Return estimated head distance in cm (60.0 fallback on any failure)."""
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return 60.0
    try:
        ok, frame = cap.read()
        if not ok:
            return 60.0
        result = _detect_face_distance(frame, ipd_mm)
        return result if result is not None else 60.0
    except Exception:  # noqa: BLE001
        return 60.0
    finally:
        cap.release()
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_calibration.py -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/calibration.py tests/test_calibration.py
git commit -m "feat: add launcher/calibration.py for screen size and head distance detection"
```

---

### Task 4: Add `set_measurement_noise` to `tracker/smoother.py`

**Files:**
- Modify: `tracker/smoother.py`
- Modify: `tests/test_smoother.py`

- [ ] **Step 1: Write the failing test** — add at end of `tests/test_smoother.py`:

```python
def test_set_measurement_noise_updates_responsiveness():
    from tracker.smoother import HeadSmoother
    s = HeadSmoother(process_noise=0.01, measurement_noise=0.1)
    s.set_measurement_noise(0.001)  # very responsive
    for _ in range(15):
        s.update(10.0, 0.0, 60.0)
    x, _, _ = s.update(10.0, 0.0, 60.0)
    assert x > 9.5  # after 16 frames with very low r, should be close to 10
```

- [ ] **Step 2: Run to see it fail**

```bash
python -m pytest tests/test_smoother.py::test_set_measurement_noise_updates_responsiveness -v
```
Expected: `AttributeError: 'HeadSmoother' object has no attribute 'set_measurement_noise'`

- [ ] **Step 3: Add method to `KalmanFilter1D` in `tracker/smoother.py`** — insert after the `reset` method (after line 33):

```python
    def set_measurement_noise(self, r: float) -> None:
        """Update measurement noise covariance in-place."""
        if r <= 0:
            raise ValueError(f"measurement_noise must be positive, got {r}")
        self._r = r
```

Add to `HeadSmoother` after its `reset` method:

```python
    def set_measurement_noise(self, r: float) -> None:
        """Update measurement noise on all three Kalman axes (higher r = more smoothing)."""
        self._kf_x.set_measurement_noise(r)
        self._kf_y.set_measurement_noise(r)
        self._kf_z.set_measurement_noise(r)
```

- [ ] **Step 4: Run all smoother tests**

```bash
python -m pytest tests/test_smoother.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tracker/smoother.py tests/test_smoother.py
git commit -m "feat: add HeadSmoother.set_measurement_noise for live smoothing control"
```

---

### Task 5: Update `launcher/tracker_thread.py` — deadzone and live smoothing

**Files:**
- Modify: `launcher/tracker_thread.py`
- Modify: `tests/test_tracker_thread.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_tracker_thread.py`:

```python
def test_apply_deadzone_first_call_accepted():
    from launcher.tracker_thread import _apply_deadzone
    out, prev = _apply_deadzone((1.0, 0.0, 60.0), None, deadzone_cm=0.5)
    assert out == (1.0, 0.0, 60.0)
    assert prev == (1.0, 0.0, 60.0)

def test_apply_deadzone_suppresses_small_move():
    from launcher.tracker_thread import _apply_deadzone
    _, prev = _apply_deadzone((1.0, 0.0, 60.0), None, deadzone_cm=0.5)
    out, prev2 = _apply_deadzone((1.3, 0.0, 60.0), prev, deadzone_cm=0.5)
    assert out == (1.0, 0.0, 60.0)  # clamped to previous

def test_apply_deadzone_passes_large_move():
    from launcher.tracker_thread import _apply_deadzone
    _, prev = _apply_deadzone((1.0, 0.0, 60.0), None, deadzone_cm=0.5)
    out, _ = _apply_deadzone((2.0, 0.0, 60.0), prev, deadzone_cm=0.5)
    assert out == (2.0, 0.0, 60.0)
```

- [ ] **Step 2: Run to see it fail**

```bash
python -m pytest tests/test_tracker_thread.py::test_apply_deadzone_first_call_accepted -v
```
Expected: `ImportError` — `_apply_deadzone` not defined.

- [ ] **Step 3: Add imports at top of `launcher/tracker_thread.py`**

After the existing `import threading` line, add:
```python
import math
```

After the existing `from tracker.shared_memory import SharedMemoryWriter` line, add:
```python
from tracker.shared_settings import SharedSettingsReader
```

- [ ] **Step 4: Add `_apply_deadzone` function before `_SignallingLoop` class**

```python
def _apply_deadzone(
    raw: tuple[float, float, float],
    prev: tuple[float, float, float] | None,
    deadzone_cm: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return (effective_pos, new_prev).

    Suppresses XY movements smaller than deadzone_cm. Z (distance) is
    always passed through.
    """
    if prev is None:
        return raw, raw
    if math.hypot(raw[0] - prev[0], raw[1] - prev[1]) < deadzone_cm:
        return prev, prev
    return raw, raw
```

- [ ] **Step 5: Update `_SignallingLoop.__init__`** — add after `self._hold_ms = hold_ms`:

```python
self._settings_reader = SharedSettingsReader()
self._last_raw_pos: tuple[float, float, float] | None = None
```

- [ ] **Step 6: Update `_SignallingLoop.run()`** — replace the `if pos is not None:` block:

Current:
```python
if pos is not None:
    self._last_face_ms = time.monotonic() * 1000.0
    smoothed = self._smoother.update(pos.x_cm, pos.y_cm, pos.z_cm)
    self._last_smoothed = smoothed
    x, y, z = smoothed
    status = "tracking"
```

Replace with:
```python
if pos is not None:
    self._last_face_ms = time.monotonic() * 1000.0
    settings = self._settings_reader.read()
    deadzone_cm = (settings.deadzone_mm / 10.0) if settings else 0.5
    smoothing_r = settings.smoothing_alpha if settings else 0.1
    self._smoother.set_measurement_noise(max(smoothing_r, 1e-6))
    raw = (pos.x_cm, pos.y_cm, pos.z_cm)
    effective, self._last_raw_pos = _apply_deadzone(
        raw, self._last_raw_pos, deadzone_cm
    )
    smoothed = self._smoother.update(effective[0], effective[1], effective[2])
    self._last_smoothed = smoothed
    x, y, z = smoothed
    status = "tracking"
```

- [ ] **Step 7: Close settings reader in `_SignallingLoop.run()` `finally` block** — add after `cap.release()`:

```python
self._settings_reader.close()
```

- [ ] **Step 8: Update `TrackerThread.run()` to read fov/ipd from SHM at startup**

In `TrackerThread.run()`, before the `with (FaceTracker(...` block, add:

```python
_r = SharedSettingsReader()
_startup = _r.read()
_r.close()
_ipd_cm = (
    (_startup.ipd_mm / 10.0)
    if _startup and _startup.ipd_mm > 0
    else trk["ipd_cm"]
)
_fov_deg = (
    _startup.camera_fov_deg
    if _startup and _startup.camera_fov_deg > 0
    else 60.0
)
```

Then update the `FaceTracker(...)` call:
```python
FaceTracker(
    real_ipd_cm=_ipd_cm,
    screen_width_cm=scr["width_cm"],
    screen_height_cm=scr["height_cm"],
    camera_fov_deg=_fov_deg,
) as tracker,
```

- [ ] **Step 9: Run tests**

```bash
python -m pytest tests/test_tracker_thread.py -v
```
Expected: all tests PASS.

- [ ] **Step 10: Commit**

```bash
git add launcher/tracker_thread.py tests/test_tracker_thread.py
git commit -m "feat: tracker reads live deadzone/smoothing from SHM; uses fov/ipd at startup"
```

---

### Task 6: Update `overlay/overlay.cpp` — new Settings, CBuf, shader

**Files:**
- Modify: `overlay/overlay.cpp`

- [ ] **Step 1: Replace the Settings struct** (currently line 79)

Find:
```cpp
// Must match tracker/shared_settings.py STRUCT_FORMAT = "<ffffI"
struct Settings { float strength, virtualDepthCm, screenWCm, screenHCm; uint32_t version; };
```

Replace with:
```cpp
// Must match tracker/shared_settings.py STRUCT_FORMAT = "<fffffIfffffffI" (56 bytes)
#pragma pack(push, 1)
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
    float     deadzoneM;     // mm
    uint32_t  version;
};  // 56 bytes
#pragma pack(pop)
```

- [ ] **Step 2: Add new globals** — after `static float g_virtualDepth = 30.0f;` add:

```cpp
static float    g_strengthX   = 1.0f;
static float    g_strengthY   = 1.0f;
static uint32_t g_depthCurve  = 1;    // sqrt default
static float    g_depthGamma  = 1.0f;
static float    g_focusRadius = 0.1f;
```

- [ ] **Step 3: Replace the CBuf struct** (line 183)

Replace:
```cpp
struct CBuf { float headX, headY, headZ, strength, screenW, screenH, virtualDepth, debugDepth; };
```

With:
```cpp
struct CBuf {
    float headX, headY, headZ, strengthX, strengthY;
    float screenW, screenH, virtualDepth, debugDepth;
    float depthGamma, focusRadius, depthCurve;
};
```

- [ ] **Step 4: Replace the entire PS_SRC shader string**

Replace the block `static const char PS_SRC[] = R"hlsl(...)hlsl";` with:

```cpp
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
```

- [ ] **Step 5: Replace `ApplySettings()` body**

Replace the entire function body of `ApplySettings()`:

```cpp
static void ApplySettings() {
    float sw = g_autoScreenW, sh = g_autoScreenH;
    float sx = 1.0f, sy = 1.0f, dp = 30.0f;
    uint32_t dc = 1;
    float dg = 1.0f, fr = 0.1f;

    if (g_setView) {
        Settings s; memcpy(&s, g_setView, sizeof(s));
        if (s.strengthX    > 0.0f)   sx = s.strengthX;
        if (s.strengthY    > 0.0f)   sy = s.strengthY;
        if (s.virtualDepthCm >= 0.0f) dp = s.virtualDepthCm;
        if (s.screenWCm    > 0.0f)   sw = s.screenWCm;
        if (s.screenHCm    > 0.0f)   sh = s.screenHCm;
        dc = s.depthCurve;
        if (s.depthGamma  > 0.0f)   dg = s.depthGamma;
        if (s.focusRadius >= 0.0f)   fr = s.focusRadius;
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
```

- [ ] **Step 6: Update CBuf fill in `Frame()`** (currently line 830)

Replace:
```cpp
CBuf cb = { hx, hy, hz, g_strength, g_screenW, g_screenH, g_virtualDepth, g_debugDepth ? 1.0f : 0.0f };
```

With:
```cpp
CBuf cb = {
    hx, hy, hz,
    g_strengthX, g_strengthY, g_screenW, g_screenH, g_virtualDepth,
    g_debugDepth ? 1.0f : 0.0f,
    g_depthGamma, g_focusRadius, (float)g_depthCurve,
};
```

- [ ] **Step 7: Commit the source change**

```bash
git add overlay/overlay.cpp
git commit -m "feat: overlay v2 — per-axis strength, selectable depth curve, configurable focus radius"
```

---

### Task 7: Rebuild overlay binary

- [ ] **Step 1: Build**

```bash
cd "E:/Glassless 3d/overlay/build_mingw"
cmake --build . --config Release 2>&1 | tail -30
```
Expected: `[100%] Built target Glassless3DOverlay` with no errors.

- [ ] **Step 2: Copy to project root** (POST_BUILD can silently fail)

```bash
cp "E:/Glassless 3d/overlay/build_mingw/Glassless3DOverlay.exe" "E:/Glassless 3d/Glassless3DOverlay.exe"
ls -la "E:/Glassless 3d/Glassless3DOverlay.exe"
```

- [ ] **Step 3: Commit the binary**

```bash
cd "E:/Glassless 3d"
git add Glassless3DOverlay.exe
git commit -m "build: rebuild overlay with v2 CBuf (per-axis strength, depth curve, focus radius)"
```

---

### Task 8: Migrate `launcher/settings_gui.py` to v2 field names

**Files:**
- Modify: `launcher/settings_gui.py`

- [ ] **Step 1: Update `_save_overlay_settings`**

Replace:
```python
cfg["overlay"].update(
    strength=float(s.strength),
    virtual_depth_cm=float(s.virtual_depth_cm),
    screen_w_cm=float(s.screen_w_cm),
    screen_h_cm=float(s.screen_h_cm),
)
```
With:
```python
cfg["overlay"].update(
    strength_x=float(s.strength_x),
    strength_y=float(s.strength_y),
    virtual_depth_cm=float(s.virtual_depth_cm),
    screen_w_cm=float(s.screen_w_cm),
    screen_h_cm=float(s.screen_h_cm),
    depth_curve=int(s.depth_curve),
    depth_gamma=float(s.depth_gamma),
    focus_radius=float(s.focus_radius),
)
```

- [ ] **Step 2: Update `SettingsWindow.__init__` initial load**

Replace:
```python
initial = OverlaySettings(
    strength=float(cfg.get("strength", 1.0)),
    virtual_depth_cm=float(cfg.get("virtual_depth_cm", 10.0)),
    screen_w_cm=float(cfg.get("screen_w_cm", 0.0)),
    screen_h_cm=float(cfg.get("screen_h_cm", 0.0)),
)
```
With:
```python
initial = OverlaySettings(
    strength_x=float(cfg.get("strength_x", 1.0)),
    strength_y=float(cfg.get("strength_y", 1.0)),
    virtual_depth_cm=float(cfg.get("virtual_depth_cm", 30.0)),
    screen_w_cm=float(cfg.get("screen_w_cm", 0.0)),
    screen_h_cm=float(cfg.get("screen_h_cm", 0.0)),
    depth_curve=int(cfg.get("depth_curve", 1)),
    depth_gamma=float(cfg.get("depth_gamma", 1.0)),
    focus_radius=float(cfg.get("focus_radius", 0.1)),
)
```

- [ ] **Step 3: Update `_snapshot`**

Replace:
```python
def _snapshot(self) -> OverlaySettings:
    return OverlaySettings(
        strength=self._strength.value(),
        virtual_depth_cm=self._depth.value(),
        screen_w_cm=float(self._sw.value()),
        screen_h_cm=float(self._sh.value()),
    )
```
With:
```python
def _snapshot(self) -> OverlaySettings:
    return OverlaySettings(
        strength_x=self._strength.value(),
        strength_y=self._strength.value(),
        virtual_depth_cm=self._depth.value(),
        screen_w_cm=float(self._sw.value()),
        screen_h_cm=float(self._sh.value()),
    )
```

- [ ] **Step 4: Update `_on_reset`**

Replace `defaults.strength` with `defaults.strength_x`.

- [ ] **Step 5: Verify it launches without error**

```bash
python -m launcher.settings_gui
```
Expected: window opens cleanly. Close it.

- [ ] **Step 6: Commit**

```bash
git add launcher/settings_gui.py
git commit -m "fix: migrate settings_gui.py to v2 OverlaySettings field names"
```

---

### Task 9: Add Advanced tab to `launcher/mainwindow.py`

**Files:**
- Modify: `launcher/mainwindow.py`

- [ ] **Step 1: Replace the import block**

Replace the existing `from PySide6.QtWidgets import (...)` block with:

```python
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from tracker.shared_settings import OverlaySettings, SharedSettingsWriter
from launcher.presets import list_presets, save_preset, load_preset, delete_preset
from launcher.calibration import detect_screen_cm, measure_head_distance
```

- [ ] **Step 2: Update window dimensions**

Replace:
```python
_EXPANDED_W, _EXPANDED_H = 270, 310
_COMPACT_W, _COMPACT_H = 400, 100
```
With:
```python
_EXPANDED_W, _EXPANDED_H = 430, 440
_COMPACT_W,  _COMPACT_H  = 430, 100
```

- [ ] **Step 3: Add writer + settings state in `__init__`** — after `self._drag_pos: Optional[QPoint] = None` add:

```python
self._settings_writer = SharedSettingsWriter()
ov = config.get("overlay", {})
self._settings = OverlaySettings(
    strength_x=float(ov.get("strength_x", 1.0)),
    strength_y=float(ov.get("strength_y", 1.0)),
    virtual_depth_cm=float(ov.get("virtual_depth_cm", 30.0)),
    screen_w_cm=float(ov.get("screen_w_cm", 0.0)),
    screen_h_cm=float(ov.get("screen_h_cm", 0.0)),
    depth_curve=int(ov.get("depth_curve", 1)),
    depth_gamma=float(ov.get("depth_gamma", 1.0)),
    focus_radius=float(ov.get("focus_radius", 0.1)),
    head_dist_cm=float(ov.get("head_dist_cm", 60.0)),
    camera_fov_deg=float(ov.get("camera_fov_deg", 90.0)),
    ipd_mm=float(ov.get("ipd_mm", 64.0)),
    smoothing_alpha=float(ov.get("smoothing_alpha", 0.1)),
    deadzone_mm=float(ov.get("deadzone_mm", 5.0)),
)
self._settings_writer.write(self._settings)
```

- [ ] **Step 4: Replace `_build_ui` to use QTabWidget**

```python
def _build_ui(self) -> None:
    root = QWidget()
    root.setStyleSheet(f"background:{_DARK_BG};border-radius:8px;")
    self.setCentralWidget(root)
    self._root_layout = QVBoxLayout(root)
    self._root_layout.setContentsMargins(0, 0, 0, 0)
    self._root_layout.setSpacing(0)

    self._root_layout.addWidget(self._make_titlebar())

    self._tabs = QTabWidget()
    self._tabs.setStyleSheet(
        "QTabWidget::pane{border:none;background:#0d0d22;}"
        "QTabBar::tab{background:#1a1a2e;color:#888;padding:5px 14px;}"
        "QTabBar::tab:selected{background:#0d0d22;color:#c8c8e8;}"
    )

    tracker_tab = QWidget()
    tl = QVBoxLayout(tracker_tab)
    tl.setContentsMargins(0, 0, 0, 0)
    tl.setSpacing(0)
    self._camera_label = QLabel()
    self._camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    self._camera_label.setStyleSheet("background:#0a0a0a;")
    tl.addWidget(self._camera_label)
    tl.addLayout(self._make_xyz_row())
    tl.addWidget(self._make_action_button())
    self._tabs.addTab(tracker_tab, "Tracker")
    self._tabs.addTab(self._make_advanced_tab(), "Advanced")
    self._root_layout.addWidget(self._tabs)
```

- [ ] **Step 5: Add helper methods** — add to `MainWindow` class:

```python
# ── Slider helper ──────────────────────────────────────────────────────────

def _make_slider(self, lo: float, hi: float, value: float, step: float) -> QSlider:
    s = QSlider(Qt.Orientation.Horizontal)
    s.setMinimum(0)
    s.setMaximum(int(round((hi - lo) / step)))
    s.setValue(int(round((value - lo) / step)))
    s.setProperty("_lo", lo)
    s.setProperty("_step", step)
    return s

def _slider_value(self, s: QSlider) -> float:
    return s.property("_lo") + s.value() * s.property("_step")

# ── Advanced tab ───────────────────────────────────────────────────────────

def _make_advanced_tab(self) -> QWidget:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea{border:none;background:#0d0d22;}")
    inner = QWidget()
    inner.setStyleSheet("background:#0d0d22;color:#c8c8e8;")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(8, 8, 8, 8)
    lay.setSpacing(8)

    # Presets
    pg = QGroupBox("Presets")
    pg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
    pl = QHBoxLayout(pg)
    self._preset_combo = QComboBox()
    self._preset_combo.setEditable(True)
    self._refresh_presets()
    for label, slot in [("Save", self._on_preset_save),
                         ("Load", self._on_preset_load),
                         ("Delete", self._on_preset_delete)]:
        btn = QPushButton(label)
        btn.clicked.connect(slot)
        pl.addWidget(btn)
    pl.insertWidget(0, self._preset_combo)
    lay.addWidget(pg)

    # Shader
    sg = QGroupBox("Shader Tuning")
    sg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
    sf = QFormLayout(sg)
    self._depth_curve_combo = QComboBox()
    self._depth_curve_combo.addItems(["Linear", "√ sqrt", "Gamma γ"])
    self._depth_curve_combo.setCurrentIndex(int(self._settings.depth_curve))
    self._depth_curve_combo.currentIndexChanged.connect(self._on_settings_change)
    sf.addRow("Depth curve", self._depth_curve_combo)
    self._depth_gamma_spin = QDoubleSpinBox()
    self._depth_gamma_spin.setRange(0.3, 3.0)
    self._depth_gamma_spin.setSingleStep(0.1)
    self._depth_gamma_spin.setValue(self._settings.depth_gamma)
    self._depth_gamma_spin.valueChanged.connect(self._on_settings_change)
    sf.addRow("Gamma γ", self._depth_gamma_spin)
    self._strength_x_slider = self._make_slider(0.0, 5.0, self._settings.strength_x, 0.05)
    self._strength_x_slider.valueChanged.connect(self._on_settings_change)
    sf.addRow("Strength X", self._strength_x_slider)
    self._strength_y_slider = self._make_slider(0.0, 5.0, self._settings.strength_y, 0.05)
    self._strength_y_slider.valueChanged.connect(self._on_settings_change)
    sf.addRow("Strength Y", self._strength_y_slider)
    self._focus_radius_slider = self._make_slider(0.0, 0.5, self._settings.focus_radius, 0.01)
    self._focus_radius_slider.valueChanged.connect(self._on_settings_change)
    sf.addRow("Focus radius", self._focus_radius_slider)
    self._virtual_depth_slider = self._make_slider(0.0, 200.0, self._settings.virtual_depth_cm, 1.0)
    self._virtual_depth_slider.valueChanged.connect(self._on_settings_change)
    sf.addRow("Virtual depth cm", self._virtual_depth_slider)
    lay.addWidget(sg)

    # Calibration
    cg = QGroupBox("Auto-Calibration")
    cg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
    cf = QFormLayout(cg)
    self._screen_w_spin = QDoubleSpinBox()
    self._screen_w_spin.setRange(0.0, 500.0)
    self._screen_w_spin.setDecimals(1)
    self._screen_w_spin.setSuffix(" cm")
    self._screen_w_spin.setValue(self._settings.screen_w_cm)
    self._screen_w_spin.valueChanged.connect(self._on_settings_change)
    self._screen_h_spin = QDoubleSpinBox()
    self._screen_h_spin.setRange(0.0, 500.0)
    self._screen_h_spin.setDecimals(1)
    self._screen_h_spin.setSuffix(" cm")
    self._screen_h_spin.setValue(self._settings.screen_h_cm)
    self._screen_h_spin.valueChanged.connect(self._on_settings_change)
    detect_btn = QPushButton("Auto-detect screen size")
    detect_btn.clicked.connect(self._on_detect_screen)
    self._head_dist_spin = QDoubleSpinBox()
    self._head_dist_spin.setRange(20.0, 200.0)
    self._head_dist_spin.setDecimals(1)
    self._head_dist_spin.setSuffix(" cm")
    self._head_dist_spin.setValue(self._settings.head_dist_cm)
    self._head_dist_spin.valueChanged.connect(self._on_settings_change)
    measure_btn = QPushButton("Measure head distance from camera")
    measure_btn.clicked.connect(self._on_measure_head)
    self._calib_status = QLabel("")
    self._calib_status.setStyleSheet("color:#4a4;font-size:10px;")
    cf.addRow("Screen W", self._screen_w_spin)
    cf.addRow("Screen H", self._screen_h_spin)
    cf.addRow("", detect_btn)
    cf.addRow("Head dist", self._head_dist_spin)
    cf.addRow("", measure_btn)
    cf.addRow("", self._calib_status)
    lay.addWidget(cg)

    # Tracker
    tg = QGroupBox("Tracker Calibration")
    tg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
    tf = QFormLayout(tg)
    self._fov_combo = QComboBox()
    self._fov_combo.setEditable(True)
    for fov in ["60", "70", "78", "90", "100", "110", "120"]:
        self._fov_combo.addItem(f"{fov}°", float(fov))
    idx = self._fov_combo.findText(f"{int(self._settings.camera_fov_deg)}°")
    if idx >= 0:
        self._fov_combo.setCurrentIndex(idx)
    self._fov_combo.currentIndexChanged.connect(self._on_settings_change)
    tf.addRow("Camera FOV", self._fov_combo)
    self._ipd_spin = QDoubleSpinBox()
    self._ipd_spin.setRange(50.0, 80.0)
    self._ipd_spin.setDecimals(1)
    self._ipd_spin.setSuffix(" mm")
    self._ipd_spin.setValue(self._settings.ipd_mm)
    self._ipd_spin.valueChanged.connect(self._on_settings_change)
    tf.addRow("IPD", self._ipd_spin)
    self._smoothing_slider = self._make_slider(0.01, 1.0, self._settings.smoothing_alpha, 0.01)
    self._smoothing_slider.valueChanged.connect(self._on_settings_change)
    tf.addRow("Smoothing α", self._smoothing_slider)
    self._deadzone_slider = self._make_slider(0.0, 30.0, self._settings.deadzone_mm, 0.5)
    self._deadzone_slider.valueChanged.connect(self._on_settings_change)
    tf.addRow("Deadzone mm", self._deadzone_slider)
    lay.addWidget(tg)
    lay.addStretch()

    save_cfg_btn = QPushButton("Save to config.yaml")
    save_cfg_btn.clicked.connect(self._on_save_config)
    lay.addWidget(save_cfg_btn)

    scroll.setWidget(inner)
    return scroll

# ── Settings slots ─────────────────────────────────────────────────────────

def _snapshot_settings(self) -> OverlaySettings:
    fov_text = self._fov_combo.currentText().replace("°", "").strip()
    try:
        fov = float(fov_text)
    except ValueError:
        fov = 90.0
    return OverlaySettings(
        strength_x=self._slider_value(self._strength_x_slider),
        strength_y=self._slider_value(self._strength_y_slider),
        virtual_depth_cm=self._slider_value(self._virtual_depth_slider),
        screen_w_cm=float(self._screen_w_spin.value()),
        screen_h_cm=float(self._screen_h_spin.value()),
        depth_curve=self._depth_curve_combo.currentIndex(),
        depth_gamma=float(self._depth_gamma_spin.value()),
        focus_radius=self._slider_value(self._focus_radius_slider),
        head_dist_cm=float(self._head_dist_spin.value()),
        camera_fov_deg=fov,
        ipd_mm=float(self._ipd_spin.value()),
        smoothing_alpha=self._slider_value(self._smoothing_slider),
        deadzone_mm=self._slider_value(self._deadzone_slider),
    )

def _on_settings_change(self, *_: object) -> None:
    self._settings = self._snapshot_settings()
    self._settings_writer.write(self._settings)

def _on_detect_screen(self) -> None:
    self._calib_status.setText("Detecting…")
    w, h = detect_screen_cm()
    if w > 0 and h > 0:
        self._screen_w_spin.setValue(w)
        self._screen_h_spin.setValue(h)
        self._calib_status.setText(f"Detected: {w:.1f} × {h:.1f} cm")
    else:
        self._calib_status.setText("Detection failed — enter manually")

def _on_measure_head(self) -> None:
    self._calib_status.setText("Measuring (hold still 3 s)…")
    dist = measure_head_distance(ipd_mm=self._ipd_spin.value())
    self._head_dist_spin.setValue(dist)
    self._calib_status.setText(f"Measured: {dist:.1f} cm")

def _refresh_presets(self) -> None:
    self._preset_combo.clear()
    for name in list_presets(self._config_path):
        self._preset_combo.addItem(name)

def _on_preset_save(self) -> None:
    name = self._preset_combo.currentText().strip()
    if not name:
        return
    s = self._snapshot_settings()
    save_preset(self._config_path, name, {
        "strength_x": s.strength_x, "strength_y": s.strength_y,
        "virtual_depth_cm": s.virtual_depth_cm,
        "screen_w_cm": s.screen_w_cm, "screen_h_cm": s.screen_h_cm,
        "depth_curve": s.depth_curve, "depth_gamma": s.depth_gamma,
        "focus_radius": s.focus_radius, "head_dist_cm": s.head_dist_cm,
        "camera_fov_deg": s.camera_fov_deg, "ipd_mm": s.ipd_mm,
        "smoothing_alpha": s.smoothing_alpha, "deadzone_mm": s.deadzone_mm,
    })
    self._refresh_presets()

def _on_preset_load(self) -> None:
    name = self._preset_combo.currentText().strip()
    try:
        data = load_preset(self._config_path, name)
    except KeyError:
        return
    widgets = [
        self._strength_x_slider, self._strength_y_slider,
        self._virtual_depth_slider, self._focus_radius_slider,
        self._smoothing_slider, self._deadzone_slider,
        self._depth_gamma_spin, self._ipd_spin,
        self._screen_w_spin, self._screen_h_spin,
        self._head_dist_spin, self._depth_curve_combo, self._fov_combo,
    ]
    for w in widgets:
        w.blockSignals(True)
    def _set_slider(sl: QSlider, v: float) -> None:
        sl.setValue(int(round((v - sl.property("_lo")) / sl.property("_step"))))
    _set_slider(self._strength_x_slider,    data.get("strength_x",      1.0))
    _set_slider(self._strength_y_slider,    data.get("strength_y",      1.0))
    _set_slider(self._virtual_depth_slider, data.get("virtual_depth_cm",30.0))
    _set_slider(self._focus_radius_slider,  data.get("focus_radius",    0.1))
    _set_slider(self._smoothing_slider,     data.get("smoothing_alpha", 0.1))
    _set_slider(self._deadzone_slider,      data.get("deadzone_mm",     5.0))
    self._depth_gamma_spin.setValue(data.get("depth_gamma", 1.0))
    self._ipd_spin.setValue(data.get("ipd_mm", 64.0))
    self._screen_w_spin.setValue(data.get("screen_w_cm", 0.0))
    self._screen_h_spin.setValue(data.get("screen_h_cm", 0.0))
    self._head_dist_spin.setValue(data.get("head_dist_cm", 60.0))
    self._depth_curve_combo.setCurrentIndex(int(data.get("depth_curve", 1)))
    idx = self._fov_combo.findText(f"{int(data.get('camera_fov_deg', 90))}°")
    if idx >= 0:
        self._fov_combo.setCurrentIndex(idx)
    for w in widgets:
        w.blockSignals(False)
    self._on_settings_change()

def _on_preset_delete(self) -> None:
    name = self._preset_combo.currentText().strip()
    delete_preset(self._config_path, name)
    self._refresh_presets()

def _on_save_config(self) -> None:
    s = self._snapshot_settings()
    try:
        try:
            with open(self._config_path) as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            cfg = {}
        cfg.setdefault("overlay", {}).update(
            strength_x=s.strength_x, strength_y=s.strength_y,
            virtual_depth_cm=s.virtual_depth_cm,
            screen_w_cm=s.screen_w_cm, screen_h_cm=s.screen_h_cm,
            depth_curve=s.depth_curve, depth_gamma=s.depth_gamma,
            focus_radius=s.focus_radius, head_dist_cm=s.head_dist_cm,
            camera_fov_deg=s.camera_fov_deg, ipd_mm=s.ipd_mm,
            smoothing_alpha=s.smoothing_alpha, deadzone_mm=s.deadzone_mm,
        )
        with open(self._config_path, "w") as f:
            yaml.dump(cfg, f, default_flow_style=False)
    except OSError:
        pass
```

- [ ] **Step 6: Close writer in `closeEvent`** — add before `event.accept()`:

```python
self._settings_writer.close()
```

- [ ] **Step 7: Launch and verify**

```bash
cd "E:/Glassless 3d"
python -m launcher
```
Expected: launcher opens with "Tracker" and "Advanced" tabs. Sliders in Advanced tab live-update the running overlay (check overlay.log for updated strength/depth values).

- [ ] **Step 8: Commit**

```bash
git add launcher/mainwindow.py
git commit -m "feat: add Advanced tab with shader tuning, presets, calibration, tracker controls"
```

---

## Self-Review

**Spec coverage:**
- ✅ Depth curve dropdown (linear/sqrt/gamma) + gamma slider — Task 6, Task 9
- ✅ Per-axis Strength X / Y — Task 6, Task 9
- ✅ Focus zone radius — Task 6, Task 9
- ✅ Virtual depth slider — Task 9
- ✅ Screen auto-detect button — Task 3, Task 9
- ✅ Head distance measure button — Task 3, Task 9
- ✅ Presets save/load/delete dropdown — Task 2, Task 9
- ✅ Camera FOV, IPD — Task 5, Task 9
- ✅ Smoothing α (live, per-frame from SHM) — Task 4, Task 5, Task 9
- ✅ Deadzone mm (live, per-frame from SHM) — Task 5, Task 9
- ✅ SHM v2 56-byte struct — Task 1
- ✅ Error handling: detect_screen_cm → (0,0); measure_head_distance → 60.0 fallback — Task 3

**No placeholders.**

**Type consistency:** `OverlaySettings` fields used identically across all tasks. `_slider_value`/`_make_slider` defined in Task 9 Step 5 and used in the same step. `_apply_deadzone` defined and tested in Task 5. `set_measurement_noise` defined in Task 4, called in Task 5.
