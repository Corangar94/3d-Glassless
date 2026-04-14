# Glassless3D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a glasses-free 3D gaming overlay that uses webcam head tracking + game depth buffers to create a parallax 3D effect on any regular monitor.

**Architecture:** Python (MediaPipe) tracks head position → writes to Windows Named Shared Memory → C++ ReShade addon reads it per frame → sets HLSL shader uniforms → shader warps each game frame using the depth buffer.

**Tech Stack:** Python 3.11+, MediaPipe 0.10+, OpenCV, ctypes (Windows mmap), ReShade 5.9 (HLSL), C++17/MSVC 2022, CMake 3.20+, pytest

---

## File Map

```
Glassless 3d/
├── tracker/
│   ├── main.py              # entry point + tracking loop
│   ├── face_tracker.py      # MediaPipe head pose → X/Y/Z in cm
│   ├── shared_memory.py     # Windows Named Shared Memory writer
│   ├── smoother.py          # Kalman filter (one per axis)
│   ├── calibration.py       # interactive CLI: screen dims + IPD
│   └── requirements.txt
├── tests/
│   ├── test_smoother.py
│   ├── test_face_tracker.py
│   ├── test_shared_memory.py
│   └── test_calibration.py
├── addon/
│   ├── Glassless3D.cpp      # ReShade addon (~150 lines)
│   ├── CMakeLists.txt
│   └── build.bat
├── shaders/
│   ├── Glassless3D.fxh      # depth helpers + ComputeParallaxOffset
│   └── Glassless3D.fx       # full effect shader
├── profiles/
│   ├── default.json
│   └── wow.json
├── vendor/
│   └── reshade/             # ReShade SDK headers (downloaded by script)
├── scripts/
│   └── download_reshade_sdk.py
├── setup.py                 # installs ReShade + addon + shader into game dir
├── config.yaml
└── .gitignore
```

---

## Task 1: Scaffold — config, gitignore, requirements

**Files:**
- Create: `.gitignore`
- Create: `config.yaml`
- Create: `tracker/requirements.txt`
- Create: `tests/__init__.py`

- [ ] **Step 1.1: Create `.gitignore`**

```
__pycache__/
*.pyc
*.pyo
.pytest_cache/
*.egg-info/
dist/
build/
addon/build/
vendor/
*.addon
*.dll
*.pdb
.venv/
```

- [ ] **Step 1.2: Create `config.yaml`**

```yaml
camera:
  index: 0          # webcam device index (0 = default camera)
  width: 1280
  height: 720

screen:
  width_cm: 59.8    # physical screen width in centimetres (27" = 59.8cm)
  height_cm: 33.6   # physical screen height in centimetres

tracking:
  ipd_cm: 6.3       # inter-pupillary distance in cm (average adult = 6.3)
  smoothing_q: 0.01 # Kalman process noise (lower = smoother, more lag)
  smoothing_r: 0.1  # Kalman measurement noise (lower = trusts camera more)
  hold_ms: 500      # ms to hold last known position when face lost

shared_memory:
  name: "G3D"       # Windows Named Shared Memory key
```

- [ ] **Step 1.3: Create `tracker/requirements.txt`**

```
mediapipe==0.10.14
opencv-python==4.9.0.80
numpy==1.26.4
pyyaml==6.0.1
pytest==8.1.1
```

- [ ] **Step 1.4: Create `tests/__init__.py`**

Empty file — just touch it:
```python
```

- [ ] **Step 1.5: Install dependencies**

```bash
cd "E:/Glassless 3d"
python -m venv .venv
.venv/Scripts/activate
pip install -r tracker/requirements.txt
```

Expected: All packages install without error.

- [ ] **Step 1.6: Commit**

```bash
git init
git add .gitignore config.yaml tracker/requirements.txt tests/__init__.py
git commit -m "chore: scaffold project structure and config"
```

---

## Task 2: Kalman Smoother

**Files:**
- Create: `tracker/smoother.py`
- Create: `tests/test_smoother.py`

- [ ] **Step 2.1: Write the failing test**

```python
# tests/test_smoother.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tracker'))
from smoother import KalmanFilter1D, HeadSmoother

def test_kalman_converges_to_constant():
    """Repeated measurement of the same value should converge to it."""
    kf = KalmanFilter1D(process_noise=0.01, measurement_noise=0.1)
    for _ in range(50):
        result = kf.update(10.0)
    assert abs(result - 10.0) < 0.01

def test_kalman_smooths_step_change():
    """A sudden jump should not immediately appear in output."""
    kf = KalmanFilter1D(process_noise=0.01, measurement_noise=0.1)
    for _ in range(20):
        kf.update(0.0)
    after_jump = kf.update(100.0)
    assert after_jump < 50.0  # filter dampens it

def test_head_smoother_three_axes():
    """HeadSmoother wraps three independent Kalman filters."""
    smoother = HeadSmoother(process_noise=0.01, measurement_noise=0.1)
    x, y, z = smoother.update(5.0, -3.0, 60.0)
    assert isinstance(x, float)
    assert isinstance(y, float)
    assert isinstance(z, float)

def test_head_smoother_axes_independent():
    """Updating one axis should not affect others."""
    smoother = HeadSmoother(process_noise=0.01, measurement_noise=0.1)
    for _ in range(30):
        x, y, z = smoother.update(10.0, 0.0, 60.0)
    assert abs(x - 10.0) < 0.1
    assert abs(y - 0.0) < 0.1
```

- [ ] **Step 2.2: Run test to verify it fails**

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python -m pytest tests/test_smoother.py -v
```

Expected: `ImportError: No module named 'smoother'`

- [ ] **Step 2.3: Implement `tracker/smoother.py`**

```python
# tracker/smoother.py


class KalmanFilter1D:
    """Single-axis Kalman filter for smoothing noisy measurements."""

    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1):
        self._q = process_noise      # process noise covariance
        self._r = measurement_noise  # measurement noise covariance
        self._x = 0.0                # state estimate
        self._p = 1.0                # error covariance

    def update(self, measurement: float) -> float:
        # Prediction step
        self._p += self._q
        # Update step
        k = self._p / (self._p + self._r)   # Kalman gain
        self._x += k * (measurement - self._x)
        self._p *= (1.0 - k)
        return self._x

    def reset(self, value: float = 0.0) -> None:
        self._x = value
        self._p = 1.0


class HeadSmoother:
    """Three independent Kalman filters for X, Y, Z head position axes."""

    def __init__(self, process_noise: float = 0.01, measurement_noise: float = 0.1):
        self._kf_x = KalmanFilter1D(process_noise, measurement_noise)
        self._kf_y = KalmanFilter1D(process_noise, measurement_noise)
        self._kf_z = KalmanFilter1D(process_noise, measurement_noise)

    def update(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        return (
            self._kf_x.update(x),
            self._kf_y.update(y),
            self._kf_z.update(z),
        )

    def reset(self) -> None:
        self._kf_x.reset()
        self._kf_y.reset()
        self._kf_z.reset(60.0)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_smoother.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 2.5: Commit**

```bash
git add tracker/smoother.py tests/test_smoother.py
git commit -m "feat: add Kalman filter smoother for head position axes"
```

---

## Task 3: Face Tracker

**Files:**
- Create: `tracker/face_tracker.py`
- Create: `tests/test_face_tracker.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/test_face_tracker.py
import sys, os, math
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tracker'))
from face_tracker import estimate_z_cm, estimate_xy_cm, FaceTracker

def test_estimate_z_closer_when_eyes_further_apart():
    """Wider pixel eye distance → head is closer to camera."""
    z_close = estimate_z_cm(
        ipd_px=120.0, image_width=1280,
        real_ipd_cm=6.3, camera_fov_deg=60.0
    )
    z_far = estimate_z_cm(
        ipd_px=60.0, image_width=1280,
        real_ipd_cm=6.3, camera_fov_deg=60.0
    )
    assert z_close < z_far

def test_estimate_z_positive():
    """Z should always be a positive distance."""
    z = estimate_z_cm(ipd_px=80.0, image_width=1280,
                      real_ipd_cm=6.3, camera_fov_deg=60.0)
    assert z > 0.0

def test_estimate_xy_centred_gives_zero():
    """Nose at image centre → X and Y offset should be near zero."""
    x, y = estimate_xy_cm(
        nose_x_norm=0.5, nose_y_norm=0.5,
        screen_width_cm=59.8, screen_height_cm=33.6
    )
    assert abs(x) < 0.01
    assert abs(y) < 0.01

def test_estimate_xy_right_of_centre():
    """Nose right of centre → positive X offset."""
    x, _ = estimate_xy_cm(
        nose_x_norm=0.7, nose_y_norm=0.5,
        screen_width_cm=59.8, screen_height_cm=33.6
    )
    assert x > 0.0

def test_face_tracker_init():
    """FaceTracker constructs without error."""
    tracker = FaceTracker(
        real_ipd_cm=6.3,
        screen_width_cm=59.8,
        screen_height_cm=33.6,
        camera_fov_deg=60.0,
    )
    assert tracker is not None
```

- [ ] **Step 3.2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_face_tracker.py -v
```

Expected: `ImportError: No module named 'face_tracker'`

- [ ] **Step 3.3: Implement `tracker/face_tracker.py`**

```python
# tracker/face_tracker.py
import math
from dataclasses import dataclass
from typing import Optional

import cv2
import mediapipe as mp
import numpy as np

# MediaPipe landmark indices (with refine_landmarks=True, 478 total)
_NOSE_TIP = 1
_LEFT_IRIS_CENTER = 468   # available only with refine_landmarks=True
_RIGHT_IRIS_CENTER = 473  # available only with refine_landmarks=True


@dataclass
class HeadPosition:
    x_cm: float   # right = positive, left = negative
    y_cm: float   # up = positive, down = negative (flipped from image coords)
    z_cm: float   # distance from screen (always positive)


def estimate_z_cm(
    ipd_px: float,
    image_width: int,
    real_ipd_cm: float,
    camera_fov_deg: float,
) -> float:
    """Estimate head Z distance using inter-iris pixel distance."""
    focal_px = image_width / (2.0 * math.tan(math.radians(camera_fov_deg / 2.0)))
    return (focal_px * real_ipd_cm) / max(ipd_px, 1.0)


def estimate_xy_cm(
    nose_x_norm: float,
    nose_y_norm: float,
    screen_width_cm: float,
    screen_height_cm: float,
) -> tuple[float, float]:
    """Convert normalised nose position to cm offset from screen centre."""
    x_cm = (nose_x_norm - 0.5) * screen_width_cm
    y_cm = -((nose_y_norm - 0.5) * screen_height_cm)  # flip Y: up is positive
    return x_cm, y_cm


class FaceTracker:
    """Wraps MediaPipe FaceMesh and converts landmarks to head pose in cm."""

    def __init__(
        self,
        real_ipd_cm: float,
        screen_width_cm: float,
        screen_height_cm: float,
        camera_fov_deg: float = 60.0,
    ):
        self._real_ipd_cm = real_ipd_cm
        self._screen_width_cm = screen_width_cm
        self._screen_height_cm = screen_height_cm
        self._camera_fov_deg = camera_fov_deg
        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,   # enables iris detection (landmarks 468-477)
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def process_frame(self, frame_bgr: np.ndarray) -> Optional[HeadPosition]:
        """Process one BGR camera frame. Returns None if no face detected."""
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        result = self._face_mesh.process(rgb)

        if not result.multi_face_landmarks:
            return None

        lm = result.multi_face_landmarks[0].landmark

        # Iris centres in pixel space
        left_iris = np.array([lm[_LEFT_IRIS_CENTER].x * w,
                               lm[_LEFT_IRIS_CENTER].y * h])
        right_iris = np.array([lm[_RIGHT_IRIS_CENTER].x * w,
                                lm[_RIGHT_IRIS_CENTER].y * h])
        ipd_px = float(np.linalg.norm(right_iris - left_iris))

        z_cm = estimate_z_cm(ipd_px, w, self._real_ipd_cm, self._camera_fov_deg)
        x_cm, y_cm = estimate_xy_cm(
            lm[_NOSE_TIP].x, lm[_NOSE_TIP].y,
            self._screen_width_cm, self._screen_height_cm,
        )
        return HeadPosition(x_cm=x_cm, y_cm=y_cm, z_cm=z_cm)

    def close(self) -> None:
        self._face_mesh.close()
```

- [ ] **Step 3.4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_face_tracker.py -v
```

Expected: 5 tests PASS. (Note: `FaceTracker.process_frame` is not unit-tested here — it requires a live camera. It will be exercised by running `main.py` manually.)

- [ ] **Step 3.5: Commit**

```bash
git add tracker/face_tracker.py tests/test_face_tracker.py
git commit -m "feat: add MediaPipe face tracker with head pose estimation"
```

---

## Task 4: Shared Memory Writer

**Files:**
- Create: `tracker/shared_memory.py`
- Create: `tests/test_shared_memory.py`

- [ ] **Step 4.1: Write the failing test**

```python
# tests/test_shared_memory.py
import sys, os, struct, ctypes, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tracker'))
from shared_memory import SharedMemoryWriter, STRUCT_FORMAT, STRUCT_SIZE

def test_struct_size():
    """Packed struct must be exactly 16 bytes (3 floats + 1 uint32)."""
    assert STRUCT_SIZE == 16

def test_struct_format_roundtrip():
    """Pack/unpack should preserve values with float precision."""
    data = struct.pack(STRUCT_FORMAT, 5.0, -3.5, 62.1, 12345)
    x, y, z, ts = struct.unpack(STRUCT_FORMAT, data)
    assert abs(x - 5.0) < 1e-5
    assert abs(y - (-3.5)) < 1e-5
    assert abs(z - 62.1) < 1e-4
    assert ts == 12345

def test_writer_write_and_read_back():
    """Write head data and read it back from the same shared memory."""
    writer = SharedMemoryWriter(name="G3D_TEST")
    try:
        writer.write(x=1.5, y=-2.0, z=55.0)
        # Open a second view over the same mapping and read raw bytes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenFileMappingW(0x0004, False, "G3D_TEST")  # FILE_MAP_READ
        assert h, "Could not open shared memory for reading"
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, STRUCT_SIZE)
        assert view
        raw = (ctypes.c_char * STRUCT_SIZE).from_address(view)
        x, y, z, _ = struct.unpack(STRUCT_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        assert abs(x - 1.5) < 1e-5
        assert abs(y - (-2.0)) < 1e-5
        assert abs(z - 55.0) < 1e-4
    finally:
        writer.close()

def test_writer_default_on_init():
    """Writer should initialise memory to safe default (0, 0, 60)."""
    writer = SharedMemoryWriter(name="G3D_DEFAULT_TEST")
    try:
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenFileMappingW(0x0004, False, "G3D_DEFAULT_TEST")
        view = kernel32.MapViewOfFile(h, 0x0004, 0, 0, STRUCT_SIZE)
        raw = (ctypes.c_char * STRUCT_SIZE).from_address(view)
        x, y, z, _ = struct.unpack(STRUCT_FORMAT, bytes(raw))
        kernel32.UnmapViewOfFile(view)
        kernel32.CloseHandle(h)
        assert x == 0.0 and y == 0.0 and z == 60.0
    finally:
        writer.close()
```

- [ ] **Step 4.2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_shared_memory.py -v
```

Expected: `ImportError: No module named 'shared_memory'`

- [ ] **Step 4.3: Implement `tracker/shared_memory.py`**

```python
# tracker/shared_memory.py
import ctypes
import ctypes.wintypes
import struct
import time

STRUCT_FORMAT = "<fffI"   # little-endian: float x, float y, float z, uint32 timestamp
STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 16

_PAGE_READWRITE = 0x04
_FILE_MAP_ALL_ACCESS = 0xF001F
_INVALID_HANDLE = ctypes.wintypes.HANDLE(-1).value

_k32 = ctypes.windll.kernel32


class SharedMemoryWriter:
    """Writes HeadData {x, y, z, timestamp} to a Windows Named Shared Memory segment."""

    def __init__(self, name: str = "G3D"):
        self._name = name
        self._handle = _k32.CreateFileMappingW(
            _INVALID_HANDLE,
            None,
            _PAGE_READWRITE,
            0,
            STRUCT_SIZE,
            name,
        )
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

        self._view = _k32.MapViewOfFile(
            self._handle,
            _FILE_MAP_ALL_ACCESS,
            0, 0,
            STRUCT_SIZE,
        )
        if not self._view:
            raise ctypes.WinError(ctypes.get_last_error())

        # Initialise to safe defaults: head centred, 60 cm away
        self.write(x=0.0, y=0.0, z=60.0)

    def write(self, x: float, y: float, z: float) -> None:
        ts = int(time.monotonic_ns() // 1_000_000) & 0xFFFF_FFFF
        data = struct.pack(STRUCT_FORMAT, x, y, z, ts)
        ctypes.memmove(self._view, data, STRUCT_SIZE)

    def close(self) -> None:
        if self._view:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
```

- [ ] **Step 4.4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_shared_memory.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 4.5: Commit**

```bash
git add tracker/shared_memory.py tests/test_shared_memory.py
git commit -m "feat: add Windows Named Shared Memory writer for head data"
```

---

## Task 5: Main Tracking Loop

**Files:**
- Create: `tracker/main.py`
- Create: `tests/test_main.py`

- [ ] **Step 5.1: Write the failing test**

```python
# tests/test_main.py
import sys, os, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tracker'))
from unittest.mock import MagicMock, patch
from main import TrackingLoop

def test_tracking_loop_starts_and_stops():
    """Loop must start, run for a tick, and stop cleanly on request."""
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

    t = threading.Thread(target=loop.run, kwargs={"max_frames": 5})
    t.start()
    t.join(timeout=5.0)
    assert not t.is_alive(), "Loop did not terminate"

def test_tracking_loop_writes_default_when_no_face():
    """When face is lost, loop should write (0, 0, 60) after hold_ms."""
    mock_tracker = MagicMock()
    mock_tracker.process_frame.return_value = None

    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (0.0, 0.0, 60.0)

    loop = TrackingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=0,   # expire immediately
    )
    loop.run(max_frames=3)
    mock_writer.write.assert_called()
    # All writes should be the default safe position
    for call in mock_writer.write.call_args_list:
        assert call.kwargs.get("z", call.args[2] if len(call.args) > 2 else 60.0) == 60.0
```

- [ ] **Step 5.2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_main.py -v
```

Expected: `ImportError: No module named 'main'`

- [ ] **Step 5.3: Implement `tracker/main.py`**

```python
# tracker/main.py
import argparse
import time
from typing import Optional

import cv2
import yaml

from face_tracker import FaceTracker, HeadPosition
from shared_memory import SharedMemoryWriter
from smoother import HeadSmoother


class TrackingLoop:
    """Captures webcam frames, tracks head, smooths, and writes to shared memory."""

    def __init__(
        self,
        tracker,
        writer,
        smoother,
        hold_ms: int = 500,
    ):
        self._tracker = tracker
        self._writer = writer
        self._smoother = smoother
        self._hold_ms = hold_ms
        self._last_face_ms: Optional[float] = None

    def run(self, camera_index: int = 0, max_frames: Optional[int] = None) -> None:
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
                    if (self._last_face_ms is None or
                            now_ms - self._last_face_ms > self._hold_ms):
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
    shm = cfg["shared_memory"]

    tracker = FaceTracker(
        real_ipd_cm=trk["ipd_cm"],
        screen_width_cm=scr["width_cm"],
        screen_height_cm=scr["height_cm"],
    )
    smoother = HeadSmoother(
        process_noise=trk["smoothing_q"],
        measurement_noise=trk["smoothing_r"],
    )

    print(f"[G3D] Starting tracker — camera {cam['index']}, "
          f"shared memory key '{shm['name']}'")
    print("[G3D] Press Ctrl+C to stop.")

    with SharedMemoryWriter(name=shm["name"]) as writer:
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

- [ ] **Step 5.4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_main.py -v
```

Expected: 2 tests PASS.

- [ ] **Step 5.5: Smoke-test with real webcam**

```bash
cd "E:/Glassless 3d/tracker"
../.venv/Scripts/python main.py
```

Expected: Console prints `[G3D] Starting tracker`. Move your face — no errors. Ctrl+C stops it cleanly.

- [ ] **Step 5.6: Commit**

```bash
git add tracker/main.py tests/test_main.py
git commit -m "feat: add main tracking loop with face-lost hold and graceful shutdown"
```

---

## Task 6: Calibration CLI

**Files:**
- Create: `tracker/calibration.py`
- Create: `tests/test_calibration.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/test_calibration.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tracker'))
from calibration import validate_screen_size, validate_ipd, cm_from_inches

def test_validate_screen_size_valid():
    assert validate_screen_size(59.8, 33.6) is True

def test_validate_screen_size_zero_rejected():
    assert validate_screen_size(0.0, 33.6) is False

def test_validate_screen_size_negative_rejected():
    assert validate_screen_size(59.8, -1.0) is False

def test_validate_ipd_valid():
    assert validate_ipd(6.3) is True

def test_validate_ipd_out_of_range():
    assert validate_ipd(2.0) is False   # too small
    assert validate_ipd(10.0) is False  # too large

def test_cm_from_inches_27_inch():
    """27" diagonal, 16:9 → width ~59.8cm, height ~33.6cm."""
    w, h = cm_from_inches(27.0, aspect=(16, 9))
    assert abs(w - 59.77) < 0.1
    assert abs(h - 33.62) < 0.1
```

- [ ] **Step 6.2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_calibration.py -v
```

Expected: `ImportError: No module named 'calibration'`

- [ ] **Step 6.3: Implement `tracker/calibration.py`**

```python
# tracker/calibration.py
import math
import yaml


def validate_screen_size(width_cm: float, height_cm: float) -> bool:
    return width_cm > 0.0 and height_cm > 0.0


def validate_ipd(ipd_cm: float) -> bool:
    return 4.0 <= ipd_cm <= 8.0   # normal human range


def cm_from_inches(diagonal_inches: float, aspect: tuple[int, int]) -> tuple[float, float]:
    """Convert diagonal screen size in inches to (width_cm, height_cm)."""
    ar_w, ar_h = aspect
    diagonal_cm = diagonal_inches * 2.54
    scale = diagonal_cm / math.sqrt(ar_w**2 + ar_h**2)
    return scale * ar_w, scale * ar_h


def run_calibration(config_path: str = "config.yaml") -> None:
    """Interactive CLI calibration wizard. Updates config.yaml in place."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    print("\n=== Glassless3D Calibration ===\n")

    # Screen size
    print("Enter your monitor's diagonal size in inches (e.g. 27):")
    while True:
        try:
            diag = float(input("  Diagonal (inches): "))
            w, h = cm_from_inches(diag, (16, 9))
            print(f"  → Calculated: {w:.1f} cm wide × {h:.1f} cm tall")
            print("  Press Enter to accept, or type custom width in cm:")
            custom = input("  Width cm (Enter to accept): ").strip()
            if custom:
                w = float(custom)
                h = float(input("  Height cm: "))
            if validate_screen_size(w, h):
                cfg["screen"]["width_cm"] = round(w, 1)
                cfg["screen"]["height_cm"] = round(h, 1)
                break
            print("  Invalid values. Try again.")
        except ValueError:
            print("  Please enter a number.")

    # IPD
    print("\nEnter your inter-pupillary distance in cm (average = 6.3):")
    while True:
        try:
            ipd = float(input("  IPD (cm): ") or "6.3")
            if validate_ipd(ipd):
                cfg["tracking"]["ipd_cm"] = ipd
                break
            print("  IPD must be between 4.0 and 8.0 cm.")
        except ValueError:
            print("  Please enter a number.")

    with open(config_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)

    print(f"\n✓ Saved to {config_path}")
    print(f"  Screen: {cfg['screen']['width_cm']} × {cfg['screen']['height_cm']} cm")
    print(f"  IPD: {cfg['tracking']['ipd_cm']} cm\n")


if __name__ == "__main__":
    run_calibration()
```

- [ ] **Step 6.4: Run tests to verify they pass**

```bash
.venv/Scripts/python -m pytest tests/test_calibration.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 6.5: Commit**

```bash
git add tracker/calibration.py tests/test_calibration.py
git commit -m "feat: add interactive calibration CLI for screen dims and IPD"
```

---

## Task 7: HLSL Shader

**Files:**
- Create: `shaders/Glassless3D.fxh`
- Create: `shaders/Glassless3D.fx`

> **Note:** HLSL shaders cannot be unit-tested with pytest. Testing is visual — done live in the ReShade UI. Manual test instructions are at the end of this task.

- [ ] **Step 7.1: Create `shaders/Glassless3D.fxh`**

```hlsl
// shaders/Glassless3D.fxh
// Helper functions shared by Glassless3D.fx

// Linearise a raw depth value to [0, 1] range (0 = near, 1 = far).
// Handles both normal and reversed depth buffers via ReShade's GetLinearizedDepth.
// Call ReShade::GetLinearizedDepth(uv) from the effect directly — this header
// provides the parallax math only.

// Compute the UV-space parallax offset for a pixel given:
//   head_uv     : head X/Y normalised to screen UV (head_x / screen_w, head_y / screen_h)
//   depth       : linearised depth [0, 1] for this pixel
//   convergence : depth value [0, 1] where the convergence plane sits (0 = near, 1 = far)
//   strength    : overall effect multiplier
float2 G3D_ParallaxOffset(float2 head_uv, float depth, float convergence, float strength)
{
    // Objects at convergence → zero offset (they appear "on" the screen).
    // Objects closer than convergence → positive offset (pop out).
    // Objects beyond convergence → negative offset (recede).
    float factor = (1.0 - depth / max(convergence, 0.001)) * strength;
    return head_uv * factor;
}

// Clamp UV to [0, 1] and replicate border pixels (avoids black edges).
float2 G3D_ClampUV(float2 uv)
{
    return saturate(uv);
}
```

- [ ] **Step 7.2: Create `shaders/Glassless3D.fx`**

```hlsl
// shaders/Glassless3D.fx
// Glassless3D — depth-based head-tracked parallax effect for ReShade 5.9+

#include "ReShade.fxh"
#include "Glassless3D.fxh"

// ──────────────────────────────────────────────────────────────────────────────
// Uniforms set by the C++ addon (Glassless3D.addon) each frame.
// Default to safe neutral values so the effect is invisible when tracker is off.
// ──────────────────────────────────────────────────────────────────────────────
uniform float g3d_HeadX = 0.0;   // head X position in cm, right = positive
uniform float g3d_HeadY = 0.0;   // head Y position in cm, up = positive
uniform float g3d_HeadZ = 60.0;  // head Z distance in cm from screen

// ──────────────────────────────────────────────────────────────────────────────
// User-tunable parameters (visible in the ReShade UI overlay)
// ──────────────────────────────────────────────────────────────────────────────
uniform float ScreenWidthCM <
    ui_category = "Screen Setup";
    ui_type = "slider";
    ui_label = "Screen Width (cm)";
    ui_tooltip = "Physical width of your monitor. Measure it with a ruler.";
    ui_min = 20.0; ui_max = 120.0; ui_step = 0.5;
> = 59.8;

uniform float ScreenHeightCM <
    ui_category = "Screen Setup";
    ui_type = "slider";
    ui_label = "Screen Height (cm)";
    ui_min = 10.0; ui_max = 70.0; ui_step = 0.5;
> = 33.6;

uniform float ConvergenceDist <
    ui_category = "3D Effect";
    ui_type = "slider";
    ui_label = "Convergence Distance (0-1)";
    ui_tooltip = "Depth plane that appears 'on' the screen. 0=near, 1=far. "
                 "Start at 0.5 and adjust until mid-scene objects feel flat.";
    ui_min = 0.01; ui_max = 0.99; ui_step = 0.01;
> = 0.50;

uniform float EffectStrength <
    ui_category = "3D Effect";
    ui_type = "slider";
    ui_label = "Effect Strength";
    ui_tooltip = "Increase for more pronounced 3D. Reduce if you see ghosting.";
    ui_min = 0.0; ui_max = 1.0; ui_step = 0.01;
> = 0.30;

uniform bool ShowDebugDepth <
    ui_category = "Debug";
    ui_label = "Show Depth Buffer";
    ui_tooltip = "Visualise the raw depth buffer. Use this to verify depth access.";
> = false;

// ──────────────────────────────────────────────────────────────────────────────
// Pixel shader
// ──────────────────────────────────────────────────────────────────────────────
float4 PS_Glassless3D(float4 pos : SV_Position, float2 uv : TEXCOORD) : SV_Target
{
    float depth = ReShade::GetLinearizedDepth(uv);

    if (ShowDebugDepth)
        return float4(depth, depth, depth, 1.0);

    // Normalise head X/Y from cm to UV-space fraction
    float2 head_uv = float2(
         g3d_HeadX / max(ScreenWidthCM,  1.0),
        -g3d_HeadY / max(ScreenHeightCM, 1.0)   // negate: image Y is flipped
    );

    float2 offset   = G3D_ParallaxOffset(head_uv, depth, ConvergenceDist, EffectStrength);
    float2 sampleUV = G3D_ClampUV(uv + offset);

    return tex2D(ReShade::BackBuffer, sampleUV);
}

// ──────────────────────────────────────────────────────────────────────────────
// Technique
// ──────────────────────────────────────────────────────────────────────────────
technique Glassless3D <
    ui_label = "Glassless3D";
    ui_tooltip = "Head-tracked parallax 3D. Requires Glassless3D.addon + tracker running.";
>
{
    pass
    {
        VertexShader = PostProcessVS;
        PixelShader  = PS_Glassless3D;
    }
}
```

- [ ] **Step 7.3: Manual visual test in ReShade**

Once ReShade is installed in a game (Task 11), do this to verify the shader:

1. Launch the game. Open the ReShade overlay (default: `Home` key).
2. Enable the `Glassless3D` technique in the list.
3. Set **Show Depth Buffer** = ON. You should see a greyscale depth image — dark near, white far. If all white/black, the depth buffer isn't accessible (check game profile settings).
4. Set **Show Depth Buffer** = OFF.
5. Set **Effect Strength** to 0.8 (exaggerated for testing).
6. Move your head left and right while the tracker is running — the scene should shift with you.
7. Adjust **Convergence Distance** until mid-distance objects feel stationary and close objects pop toward you.
8. Reduce **Effect Strength** to 0.3 for comfortable play.

- [ ] **Step 7.4: Commit**

```bash
git add shaders/
git commit -m "feat: add HLSL parallax shader with depth-based 3D warp and ReShade UI controls"
```

---

## Task 8: Vendor — Download ReShade SDK Headers

**Files:**
- Create: `scripts/download_reshade_sdk.py`

The C++ addon needs ReShade's `include/` headers. This task downloads exactly what's needed.

- [ ] **Step 8.1: Create `scripts/download_reshade_sdk.py`**

```python
#!/usr/bin/env python3
# scripts/download_reshade_sdk.py
# Downloads the ReShade SDK headers required to compile the addon.
# Run once before building the addon.

import os
import sys
import urllib.request
import zipfile
import shutil

RESHADE_VERSION = "5.9.2"
ZIP_URL = (
    f"https://github.com/crosire/reshade/archive/refs/tags/v{RESHADE_VERSION}.zip"
)
DEST_DIR = os.path.join(os.path.dirname(__file__), "..", "vendor", "reshade")
TMP_ZIP = os.path.join(os.path.dirname(__file__), "..", "vendor", "_reshade_src.zip")


def main() -> None:
    os.makedirs(os.path.dirname(TMP_ZIP), exist_ok=True)

    print(f"Downloading ReShade {RESHADE_VERSION} source...")
    urllib.request.urlretrieve(ZIP_URL, TMP_ZIP, _progress)
    print()

    print("Extracting include headers...")
    with zipfile.ZipFile(TMP_ZIP) as zf:
        prefix = f"reshade-{RESHADE_VERSION}/include/"
        members = [m for m in zf.namelist() if m.startswith(prefix)]
        if not members:
            sys.exit("ERROR: Could not find include/ in archive.")
        for member in members:
            relative = member[len(prefix):]
            if not relative:
                continue
            dest = os.path.join(DEST_DIR, "include", relative)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(member) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)

    os.remove(TMP_ZIP)
    print(f"✓ ReShade SDK headers saved to vendor/reshade/include/")


def _progress(block_num, block_size, total_size):
    downloaded = block_num * block_size
    pct = min(downloaded / total_size * 100, 100) if total_size > 0 else 0
    print(f"\r  {pct:.0f}% ({downloaded // 1024} KB)", end="", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 8.2: Run the download script**

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python scripts/download_reshade_sdk.py
```

Expected: `✓ ReShade SDK headers saved to vendor/reshade/include/`

Verify:
```bash
ls vendor/reshade/include/
```

Expected: `reshade.hpp`, `reshade_api.hpp`, and several other `.hpp` files.

- [ ] **Step 8.3: Commit**

```bash
git add scripts/download_reshade_sdk.py
git commit -m "feat: add script to download ReShade SDK headers for addon build"
```

---

## Task 9: C++ ReShade Addon

**Files:**
- Create: `addon/Glassless3D.cpp`
- Create: `addon/CMakeLists.txt`
- Create: `addon/build.bat`

- [ ] **Step 9.1: Create `addon/Glassless3D.cpp`**

```cpp
// addon/Glassless3D.cpp
// Glassless3D ReShade Addon
// Reads head position from Windows Named Shared Memory and feeds it
// to the Glassless3D.fx shader as uniform values each frame.

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
#include <cstring>
#include <cstdint>

#include <reshade.hpp>

// ──────────────────────────────────────────────────────────────────────────────
// Shared memory layout must match tracker/shared_memory.py STRUCT_FORMAT "<fffI"
// ──────────────────────────────────────────────────────────────────────────────
#pragma pack(push, 1)
struct HeadData {
    float    x;          // cm, right = positive
    float    y;          // cm, up = positive
    float    z;          // cm, distance from screen
    uint32_t timestamp;  // ms, for staleness detection
};
#pragma pack(pop)
static_assert(sizeof(HeadData) == 16, "HeadData must be 16 bytes");

static constexpr const wchar_t* kMapName = L"G3D";
static constexpr float          kDefaultZ = 60.0f;

static HANDLE s_hMap  = NULL;
static LPVOID s_pView = NULL;

// ──────────────────────────────────────────────────────────────────────────────
// Try to open the shared memory mapping created by the Python tracker.
// Returns true on success. Called lazily so we don't block DllMain.
// ──────────────────────────────────────────────────────────────────────────────
static bool TryOpenSharedMemory()
{
    if (s_pView) return true;

    s_hMap = OpenFileMappingW(FILE_MAP_READ, FALSE, kMapName);
    if (!s_hMap) return false;

    s_pView = MapViewOfFile(s_hMap, FILE_MAP_READ, 0, 0, sizeof(HeadData));
    if (!s_pView) {
        CloseHandle(s_hMap);
        s_hMap = NULL;
        return false;
    }
    return true;
}

static void CloseSharedMemory()
{
    if (s_pView) { UnmapViewOfFile(s_pView); s_pView = NULL; }
    if (s_hMap)  { CloseHandle(s_hMap);       s_hMap  = NULL; }
}

static HeadData ReadHeadData()
{
    HeadData data = { 0.0f, 0.0f, kDefaultZ, 0 };
    if (TryOpenSharedMemory() && s_pView)
        std::memcpy(&data, s_pView, sizeof(HeadData));
    return data;
}

// ──────────────────────────────────────────────────────────────────────────────
// ReShade event: fires just before effects are applied each frame.
// We read head data here and push it into the shader as uniforms.
// ──────────────────────────────────────────────────────────────────────────────
static void on_begin_effects(
    reshade::api::effect_runtime* runtime,
    reshade::api::command_list*,
    reshade::api::resource_view,
    reshade::api::resource_view)
{
    const HeadData d = ReadHeadData();

    auto set = [&](const char* name, float value) {
        const auto var = runtime->find_uniform_variable("Glassless3D.fx", name);
        if (var != reshade::api::effect_uniform_variable{ 0 })
            runtime->set_uniform_value_float(var, &value, 1);
    };

    set("g3d_HeadX", d.x);
    set("g3d_HeadY", d.y);
    set("g3d_HeadZ", d.z);
}

// ──────────────────────────────────────────────────────────────────────────────
// DLL entry point
// ──────────────────────────────────────────────────────────────────────────────
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

- [ ] **Step 9.2: Create `addon/CMakeLists.txt`**

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
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    UNICODE
    _UNICODE
)

# Output directly as .addon (ReShade addon extension)
set_target_properties(Glassless3D PROPERTIES
    SUFFIX ".addon"
    PREFIX ""
)

# Strip debug info from release build
target_compile_options(Glassless3D PRIVATE $<$<CONFIG:Release>:/O2 /GL>)
target_link_options(Glassless3D PRIVATE $<$<CONFIG:Release>:/LTCG>)
```

- [ ] **Step 9.3: Create `addon/build.bat`**

```bat
@echo off
setlocal

echo === Glassless3D Addon Build ===

:: Verify ReShade SDK headers exist
if not exist "..\vendor\reshade\include\reshade.hpp" (
    echo ERROR: ReShade SDK headers not found.
    echo Run:  python scripts/download_reshade_sdk.py
    exit /b 1
)

:: Create build dir
if not exist build mkdir build
cd build

:: Configure
cmake .. -G "Visual Studio 17 2022" -A x64
if errorlevel 1 (
    echo ERROR: CMake configure failed.
    exit /b 1
)

:: Build Release
cmake --build . --config Release
if errorlevel 1 (
    echo ERROR: Build failed.
    exit /b 1
)

:: Copy output
copy /Y Release\Glassless3D.addon ..\..\
echo.
echo === Build complete ===
echo Output: Glassless3D.addon
echo Copy it to your game directory alongside ReShade.
```

- [ ] **Step 9.4: Build the addon**

Prerequisites: Visual Studio 2022 with C++ workload installed, ReShade SDK headers present (Task 8 done).

```bat
cd "E:\Glassless 3d\addon"
build.bat
```

Expected output:
```
=== Glassless3D Addon Build ===
...
=== Build complete ===
Output: Glassless3D.addon
```

Verify `Glassless3D.addon` exists in `E:\Glassless 3d\`.

- [ ] **Step 9.5: Verify the addon exports no C symbols (sanity check)**

```bash
dumpbin /EXPORTS "E:/Glassless 3d/Glassless3D.addon"
```

Expected: No named exports (the addon registers via DllMain, not exported functions). Output should show 0 exports.

- [ ] **Step 9.6: Commit**

```bash
git add addon/
git commit -m "feat: add C++ ReShade addon that feeds head position to shader uniforms"
```

---

## Task 10: Game Profiles

**Files:**
- Create: `profiles/default.json`
- Create: `profiles/wow.json`

- [ ] **Step 10.1: Create `profiles/default.json`**

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

- [ ] **Step 10.2: Create `profiles/wow.json`**

```json
{
  "name": "World of Warcraft",
  "notes": "WoW (Dragonflight/TWW) on DirectX 11. Uses reversed depth buffer. Verified on patch 11.x.",
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

- [ ] **Step 10.3: Commit**

```bash
git add profiles/
git commit -m "feat: add game profiles for default and WoW with depth buffer settings"
```

---

## Task 11: Setup Script

**Files:**
- Create: `setup.py`

- [ ] **Step 11.1: Create `setup.py`**

```python
#!/usr/bin/env python3
# setup.py
# Installs ReShade + Glassless3D addon + shader into a game directory.
# Usage:
#   python setup.py --game wow
#   python setup.py --game-dir "C:\Games\MyGame" --profile default
#   python setup.py --game wow --dry-run

import argparse
import json
import os
import shutil
import sys
import urllib.request
import winreg

RESHADE_VERSION = "5.9.2"
RESHADE_DL_URL = (
    f"https://github.com/crosire/reshade/releases/download/v{RESHADE_VERSION}/"
    f"ReShade_Setup_{RESHADE_VERSION}.exe"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
SHADERS_DIR = os.path.join(BASE_DIR, "shaders")
ADDON_PATH = os.path.join(BASE_DIR, "Glassless3D.addon")


def load_profile(name: str) -> dict:
    path = os.path.join(PROFILES_DIR, f"{name}.json")
    if not os.path.exists(path):
        sys.exit(f"ERROR: Profile '{name}' not found at {path}")
    with open(path) as f:
        return json.load(f)


def find_game_dir(profile: dict) -> str:
    """Locate the game install directory via registry or common paths."""
    setup = profile.get("setup", {})

    # Try registry
    reg_key = setup.get("registry_key", "")
    reg_val = setup.get("registry_value", "")
    if reg_key and reg_val:
        try:
            root, subkey = reg_key.split("\\", 1)
            root_map = {
                "HKLM": winreg.HKEY_LOCAL_MACHINE,
                "HKCU": winreg.HKEY_CURRENT_USER,
            }
            with winreg.OpenKey(root_map[root], subkey) as key:
                value, _ = winreg.QueryValueEx(key, reg_val)
                if os.path.isdir(value):
                    return value
        except (FileNotFoundError, OSError):
            pass

    # Try common paths
    for path in setup.get("common_paths", []):
        if os.path.isdir(path):
            return path

    sys.exit(
        "ERROR: Could not find game directory automatically.\n"
        "Use --game-dir to specify it manually."
    )


def check_addon_built() -> None:
    if not os.path.exists(ADDON_PATH):
        sys.exit(
            "ERROR: Glassless3D.addon not found.\n"
            "Build it first:  cd addon && build.bat"
        )


def apply_profile_to_reshade_ini(game_dir: str, profile: dict, dry_run: bool) -> None:
    """Write depth buffer settings to ReShade.ini preprocessor section."""
    ini_path = os.path.join(game_dir, "ReShade.ini")
    settings = profile.get("reshade", {})
    defaults = profile.get("shader_defaults", {})

    lines = []
    if os.path.exists(ini_path):
        with open(ini_path) as f:
            lines = f.readlines()

    # Inject or update [PREPROCESSOR] section
    preprocessor_block = ["[PREPROCESSOR]\n"]
    for k, v in settings.items():
        preprocessor_block.append(f"{k}={v}\n")

    # Remove existing [PREPROCESSOR] block and re-insert
    new_lines = [l for l in lines if not l.startswith("[PREPROCESSOR]") and
                 not any(l.startswith(k) for k in settings)]
    new_lines.extend(preprocessor_block)

    if dry_run:
        print(f"  [dry-run] Would write ReShade.ini at {ini_path}")
        for l in preprocessor_block:
            print(f"    {l}", end="")
    else:
        with open(ini_path, "w") as f:
            f.writelines(new_lines)
        print(f"  ✓ Updated {ini_path}")


def install(game_dir: str, profile: dict, dry_run: bool) -> None:
    print(f"\nInstalling to: {game_dir}")
    print(f"Profile: {profile['name']}")
    if dry_run:
        print("(DRY RUN — no files will be written)\n")

    # Copy addon
    dst_addon = os.path.join(game_dir, "Glassless3D.addon")
    if dry_run:
        print(f"  [dry-run] Would copy {ADDON_PATH} → {dst_addon}")
    else:
        shutil.copy2(ADDON_PATH, dst_addon)
        print(f"  ✓ Copied addon → {dst_addon}")

    # Copy shaders
    reshade_shaders = os.path.join(game_dir, "reshade-shaders", "Shaders")
    os.makedirs(reshade_shaders, exist_ok=True) if not dry_run else None
    for fname in ["Glassless3D.fx", "Glassless3D.fxh"]:
        src = os.path.join(SHADERS_DIR, fname)
        dst = os.path.join(reshade_shaders, fname)
        if dry_run:
            print(f"  [dry-run] Would copy {src} → {dst}")
        else:
            shutil.copy2(src, dst)
            print(f"  ✓ Copied {fname} → {dst}")

    apply_profile_to_reshade_ini(game_dir, profile, dry_run)

    print("\nNext steps:")
    print("  1. Install ReShade into the game if not already done:")
    print(f"     Download: {RESHADE_DL_URL}")
    print("     Run installer, select game .exe, choose DirectX 11.")
    print("  2. Start the Python tracker:  python tracker/main.py")
    print("  3. Launch the game.")
    print("  4. Press Home to open ReShade overlay, enable 'Glassless3D'.")
    print("  5. Enjoy glasses-free 3D!\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Glassless3D game installer")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--game", help="Game profile name (e.g. wow, default)")
    group.add_argument("--game-dir", help="Path to game directory")
    parser.add_argument("--profile", default="default",
                        help="Profile to use when --game-dir is specified")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without writing files")
    args = parser.parse_args()

    check_addon_built()

    if args.game:
        profile = load_profile(args.game)
        game_dir = find_game_dir(profile)
    else:
        profile = load_profile(args.profile)
        game_dir = os.path.abspath(args.game_dir)
        if not os.path.isdir(game_dir):
            sys.exit(f"ERROR: Game directory not found: {game_dir}")

    install(game_dir, profile, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 11.2: Test dry-run mode**

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python setup.py --game-dir "C:/tmp/test_game" --profile default --dry-run
```

Expected: Prints what it would do without writing any files. No errors.

- [ ] **Step 11.3: Commit**

```bash
git add setup.py
git commit -m "feat: add setup script to install ReShade addon and shader into any game"
```

---

## Task 12: Full Integration Run

This task wires everything together for the first real end-to-end test.

- [ ] **Step 12.1: Install ReShade into WoW**

Download ReShade 5.9.2 from https://reshade.me and run the installer:
- Select `Wow.exe` from your WoW `_retail_` directory
- Select **DirectX 10/11/12**
- Uncheck all built-in effect packages (we only need our shader)

- [ ] **Step 12.2: Run the setup script for WoW**

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python setup.py --game wow
```

Expected: Copies addon + shaders, updates ReShade.ini with WoW depth settings.

- [ ] **Step 12.3: Start the Python tracker**

Open a terminal and keep it running:

```bash
cd "E:/Glassless 3d"
.venv/Scripts/python tracker/main.py
```

Expected: `[G3D] Starting tracker — camera 0, shared memory key 'G3D'`

- [ ] **Step 12.4: Launch WoW**

- Log in and enter a zone.
- Press **Home** to open the ReShade overlay.
- Find `Glassless3D` in the technique list and check the box to enable it.
- Enable **Show Depth Buffer** — verify you see a greyscale depth image. Disable it after confirming.

- [ ] **Step 12.5: Calibrate the effect**

In the ReShade UI:
1. Set **Screen Width / Height** to match your physical monitor measurements
2. Move your head left/right — scene should shift
3. Adjust **Convergence Distance** until ground/mid-distance objects are stable
4. Set **Effect Strength** to taste (0.25–0.35 is comfortable for long sessions)

- [ ] **Step 12.6: Commit final state**

```bash
git add .
git commit -m "feat: complete Glassless3D v1 — tracker, addon, shader, profiles, setup"
```

---

## Self-Review Notes

**Spec coverage check:**
- ✅ Python head tracker (Tasks 2–6)
- ✅ Shared memory bridge (Task 4)
- ✅ C++ ReShade addon (Tasks 8–9)
- ✅ HLSL shader (Task 7)
- ✅ Game profiles (Task 10)
- ✅ Setup script (Task 11)
- ✅ WoW as first target with generic support (profiles + Task 12)
- ✅ Calibration tool (Task 6)

**Type consistency:** `HeadData` struct layout matches in both Python (`STRUCT_FORMAT = "<fffI"`) and C++ (`#pragma pack(push,1) struct HeadData`). `HeadPosition` returned from `face_tracker.py` uses field names `x_cm/y_cm/z_cm`; `TrackingLoop` accesses them as `pos.x_cm` — consistent. `SharedMemoryWriter.write()` uses keyword args `x=, y=, z=` — consistent with all call sites.
