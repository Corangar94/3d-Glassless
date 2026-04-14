# Glassless3D GUI Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained PySide6 GUI launcher (`Glassless3D.exe`) with a 5-step first-run wizard, live camera preview, two-mode main window, and PyInstaller packaging.

**Architecture:** Main Qt event loop handles all UI; `TrackerThread` (QThread) runs the camera/tracking loop and emits `position_updated`, `frame_ready`, and `status_changed` signals. `TrackingLoop` is extended with hook methods (`_should_stop`, `_on_frame`, `_on_position`) so the GUI can intercept data without duplicating loop logic.

**Tech Stack:** PySide6 6.6+, pytest-qt, wmi, PyInstaller, existing `tracker.*` package

---

## File Structure

```
launcher/
├── __init__.py              # package marker
├── __main__.py              # python -m launcher entry point
├── app.py                   # QApplication, first-run detection
├── wizard.py                # QWizard — 5 pages
├── mainwindow.py            # QMainWindow — expanded / compact strip modes
├── tracker_thread.py        # QThread wrapping TrackingLoop
├── edid.py                  # WMI screen-size detection
└── reshade_install.py       # copy bundled assets, write ReShade.ini

tests/
├── test_edid.py
├── test_reshade_install.py
├── test_tracker_thread.py
├── test_wizard.py
└── test_mainwindow.py

requirements-gui.txt          # PySide6, wmi
requirements-dev.txt          # add pytest-qt
Glassless3D.spec              # PyInstaller spec
```

**Modified:** `tracker/main.py` (add hook methods to `TrackingLoop`)

---

## Task 1: Scaffold — GUI requirements and package skeleton

**Files:**
- Create: `requirements-gui.txt`
- Modify: `requirements-dev.txt`
- Create: `launcher/__init__.py`
- Create: `launcher/__main__.py`

- [ ] **Step 1: Create `requirements-gui.txt`**

```
PySide6>=6.6
wmi>=1.5
```

- [ ] **Step 2: Update `requirements-dev.txt`**

```
pytest==8.1.1
pytest-qt>=4.4
```

- [ ] **Step 3: Create `launcher/__init__.py`**

Empty file (package marker).

- [ ] **Step 4: Create `launcher/__main__.py`**

```python
from launcher.app import main
main()
```

- [ ] **Step 5: Install GUI dependencies**

```bash
pip install -r requirements-gui.txt -r requirements-dev.txt
```

Expected: packages install without errors.

- [ ] **Step 6: Commit**

```bash
git add requirements-gui.txt requirements-dev.txt launcher/__init__.py launcher/__main__.py
git commit -m "feat: scaffold launcher package and GUI dependencies"
```

---

## Task 2: `launcher/edid.py` — WMI screen-size detection

**Files:**
- Create: `launcher/edid.py`
- Create: `tests/test_edid.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_edid.py
from unittest.mock import MagicMock, patch
from launcher.edid import detect_screen_size_cm


def _make_wmi_monitor(width_mm: int, height_mm: int):
    monitor = MagicMock()
    monitor.ScreenWidth = width_mm
    monitor.ScreenHeight = height_mm
    return monitor


def test_detect_screen_size_cm_returns_dimensions_from_wmi():
    mock_wmi_instance = MagicMock()
    mock_wmi_instance.Win32_DesktopMonitor.return_value = [
        _make_wmi_monitor(597, 336)
    ]
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.return_value = mock_wmi_instance
        result = detect_screen_size_cm()
    assert result == (59.7, 33.6)


def test_detect_screen_size_cm_returns_none_when_no_monitors():
    mock_wmi_instance = MagicMock()
    mock_wmi_instance.Win32_DesktopMonitor.return_value = []
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.return_value = mock_wmi_instance
        result = detect_screen_size_cm()
    assert result is None


def test_detect_screen_size_cm_returns_none_when_dimensions_are_zero():
    mock_wmi_instance = MagicMock()
    mock_wmi_instance.Win32_DesktopMonitor.return_value = [
        _make_wmi_monitor(0, 0)
    ]
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.return_value = mock_wmi_instance
        result = detect_screen_size_cm()
    assert result is None


def test_detect_screen_size_cm_returns_none_on_wmi_exception():
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.side_effect = Exception("WMI unavailable")
        result = detect_screen_size_cm()
    assert result is None
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_edid.py -v
```

Expected: `ImportError: cannot import name 'detect_screen_size_cm'`

- [ ] **Step 3: Implement `launcher/edid.py`**

```python
"""WMI-based screen size detection for Windows."""
from __future__ import annotations


def detect_screen_size_cm() -> tuple[float, float] | None:
    """Return (width_cm, height_cm) from WMI, or None on failure.

    Uses Win32_DesktopMonitor.ScreenWidth / ScreenHeight (in mm).
    Returns None if WMI is unavailable or dimensions are zero.
    """
    try:
        import wmi
        c = wmi.WMI()
        monitors = c.Win32_DesktopMonitor()
        if not monitors:
            return None
        monitor = monitors[0]
        width_mm = getattr(monitor, "ScreenWidth", 0) or 0
        height_mm = getattr(monitor, "ScreenHeight", 0) or 0
        if width_mm == 0 or height_mm == 0:
            return None
        return float(width_mm) / 10.0, float(height_mm) / 10.0
    except Exception:
        return None
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_edid.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/edid.py tests/test_edid.py
git commit -m "feat: add WMI screen-size detection (edid.py)"
```

---

## Task 3: `launcher/reshade_install.py` — Asset installation

**Files:**
- Create: `launcher/reshade_install.py`
- Create: `tests/test_reshade_install.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_reshade_install.py
import os
import json
import pytest
from unittest.mock import patch
from launcher.reshade_install import install_steps, install, InstallError


def _make_bundle(tmp_path):
    """Create a fake bundle directory with all required assets."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "ReShade64.dll").write_bytes(b"fake_dll")
    shaders = bundle / "shaders"
    shaders.mkdir()
    (shaders / "Glassless3D.fx").write_text("fx")
    (shaders / "Glassless3D.fxh").write_text("fxh")
    (bundle / "Glassless3D.addon").write_bytes(b"fake_addon")
    profiles = bundle / "profiles"
    profiles.mkdir()
    profile_data = {
        "name": "wow",
        "reshade": {"RESHADE_DEPTH_INPUT_IS_REVERSED": 1},
        "shader_defaults": {"ConvergenceDist": 60.0},
    }
    (profiles / "wow.json").write_text(json.dumps(profile_data))
    (profiles / "default.json").write_text(json.dumps(profile_data))
    return str(bundle)


def test_install_steps_copies_reshade_dll(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir))
    assert os.path.exists(os.path.join(game_dir, "d3d11.dll"))


def test_install_steps_copies_shaders(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir))
    shader_dir = os.path.join(game_dir, "reshade-shaders", "Shaders")
    assert os.path.exists(os.path.join(shader_dir, "Glassless3D.fx"))
    assert os.path.exists(os.path.join(shader_dir, "Glassless3D.fxh"))


def test_install_steps_writes_reshade_ini(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir))
    ini_path = os.path.join(game_dir, "ReShade.ini")
    content = open(ini_path).read()
    assert "[PREPROCESSOR]" in content
    assert "RESHADE_DEPTH_INPUT_IS_REVERSED" in content


def test_install_steps_copies_addon(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        list(install_steps(game_dir))
    assert os.path.exists(os.path.join(game_dir, "Glassless3D.addon"))


def test_install_steps_yields_step_names_in_order(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        steps = list(install_steps(game_dir))
    assert steps == [
        "Copying ReShade",
        "Copying shaders",
        "Writing ReShade.ini",
        "Installing addon",
    ]


def test_install_steps_raises_install_error_on_missing_dll(tmp_path):
    bundle = _make_bundle(tmp_path)
    os.remove(os.path.join(bundle, "ReShade64.dll"))
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        with pytest.raises(InstallError) as exc_info:
            list(install_steps(game_dir))
    assert exc_info.value.step == "Copying ReShade"


def test_install_convenience_wrapper(tmp_path):
    bundle = _make_bundle(tmp_path)
    game_dir = str(tmp_path / "game")
    os.makedirs(game_dir)
    with patch("launcher.reshade_install._bundle_dir", return_value=bundle):
        install(game_dir)  # must not raise
    assert os.path.exists(os.path.join(game_dir, "d3d11.dll"))
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_reshade_install.py -v
```

Expected: `ImportError: cannot import name 'install_steps'`

- [ ] **Step 3: Implement `launcher/reshade_install.py`**

```python
"""Copy bundled ReShade assets into a game directory."""
from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Generator


class InstallError(Exception):
    """Raised when an installation step fails."""

    def __init__(self, step: str, reason: str) -> None:
        super().__init__(f"{step}: {reason}")
        self.step = step
        self.reason = reason


def _bundle_dir() -> str:
    """Return the directory containing bundled assets.

    In a PyInstaller one-file build, sys._MEIPASS points to the temp
    extraction dir. In development, fall back to the project root.
    """
    if hasattr(sys, "_MEIPASS"):
        return sys._MEIPASS  # type: ignore[attr-defined]
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def install_steps(
    game_dir: str, profile_name: str = "wow"
) -> Generator[str, None, None]:
    """Copy bundled assets into game_dir, yielding each step name on success.

    Raises InstallError if any step fails.
    """
    base = _bundle_dir()

    # Step 1: ReShade DLL
    src_dll = os.path.join(base, "ReShade64.dll")
    dst_dll = os.path.join(game_dir, "d3d11.dll")
    try:
        shutil.copy2(src_dll, dst_dll)
    except OSError as e:
        raise InstallError("Copying ReShade", str(e))
    yield "Copying ReShade"

    # Step 2: Shaders
    shader_dir = os.path.join(game_dir, "reshade-shaders", "Shaders")
    try:
        os.makedirs(shader_dir, exist_ok=True)
        for fname in ("Glassless3D.fx", "Glassless3D.fxh"):
            shutil.copy2(
                os.path.join(base, "shaders", fname),
                os.path.join(shader_dir, fname),
            )
    except OSError as e:
        raise InstallError("Copying shaders", str(e))
    yield "Copying shaders"

    # Step 3: ReShade.ini
    try:
        _write_reshade_ini(game_dir, profile_name, base)
    except OSError as e:
        raise InstallError("Writing ReShade.ini", str(e))
    yield "Writing ReShade.ini"

    # Step 4: Addon
    src_addon = os.path.join(base, "Glassless3D.addon")
    dst_addon = os.path.join(game_dir, "Glassless3D.addon")
    try:
        shutil.copy2(src_addon, dst_addon)
    except OSError as e:
        raise InstallError("Installing addon", str(e))
    yield "Installing addon"


def install(game_dir: str, profile_name: str = "wow") -> None:
    """Copy bundled assets into game_dir. Raises InstallError on failure."""
    for _ in install_steps(game_dir, profile_name):
        pass


def _write_reshade_ini(game_dir: str, profile_name: str, base: str) -> None:
    profile_path = os.path.join(base, "profiles", f"{profile_name}.json")
    if not os.path.exists(profile_path):
        profile_path = os.path.join(base, "profiles", "default.json")
    with open(profile_path) as f:
        profile = json.load(f)

    ini_path = os.path.join(game_dir, "ReShade.ini")
    depth_settings: dict = profile.get("reshade", {})
    shader_defaults: dict = profile.get("shader_defaults", {})
    all_keys = set(depth_settings) | set(shader_defaults)

    lines: list[str] = []
    if os.path.exists(ini_path):
        with open(ini_path) as f:
            lines = f.readlines()

    kept = [
        ln for ln in lines
        if not any(ln.startswith(k) for k in all_keys)
        and ln.strip() not in ("[PREPROCESSOR]", "[Glassless3D.fx]")
    ]
    block: list[str] = []
    if depth_settings:
        block += ["[PREPROCESSOR]\n"] + [f"{k}={v}\n" for k, v in depth_settings.items()]
    if shader_defaults:
        block += ["[Glassless3D.fx]\n"] + [f"{k}={v}\n" for k, v in shader_defaults.items()]

    with open(ini_path, "w") as f:
        f.writelines(kept + block)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_reshade_install.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/reshade_install.py tests/test_reshade_install.py
git commit -m "feat: add reshade_install with step-yielding generator"
```

---

## Task 4: Add hook methods to `TrackingLoop`

**Files:**
- Modify: `tracker/main.py`
- Modify: `tests/test_main.py` (add one test)

The goal is to add `_should_stop()`, `_on_frame()`, and `_on_position()` hooks to `TrackingLoop.run()` so the GUI thread can intercept data via subclass overrides. All three default to no-ops so existing tests are unaffected.

- [ ] **Step 1: Write the new failing test**

Add this test to `tests/test_main.py`:

```python
def test_tracking_loop_calls_on_position_hook():
    """_on_position is called once per frame with correct status strings."""
    positions = []

    class RecordingLoop(TrackingLoop):
        def _on_position(self, x, y, z, status):
            positions.append(status)

    mock_tracker = MagicMock()
    mock_tracker.process_frame.side_effect = [
        HeadPosition(x_cm=1.0, y_cm=0.0, z_cm=60.0),
        None,
    ]
    mock_writer = MagicMock()
    mock_smoother = MagicMock()
    mock_smoother.update.return_value = (1.0, 0.0, 60.0)

    loop = RecordingLoop(
        tracker=mock_tracker,
        writer=mock_writer,
        smoother=mock_smoother,
        hold_ms=500,
    )
    mock_cap = _make_mock_cap()
    with patch("tracker.main.cv2.VideoCapture", return_value=mock_cap):
        loop.run(camera_index=0, max_frames=2)

    assert positions[0] == "tracking"
    assert positions[1] in ("hold", "paused")
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_main.py::test_tracking_loop_calls_on_position_hook -v
```

Expected: FAIL — `_on_position` hook doesn't exist yet.

- [ ] **Step 3: Modify `tracker/main.py`**

Replace the `TrackingLoop` class body with the version below. Changes:
1. `while True` → `while not self._should_stop()`
2. Add `self._on_frame(frame)` call after successful read
3. Add `status` variable and `self._on_position(x, y, z, status)` call after write
4. Add three hook methods at the bottom of the class

```python
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
        self._last_smoothed: tuple[float, float, float] = (0.0, 0.0, 60.0)

    def run(self, camera_index: int = 0, max_frames: Optional[int] = None) -> None:
        """Run the tracking loop. Blocks until max_frames reached or Ctrl+C."""
        cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera {camera_index}")
        frame_count = 0
        try:
            while not self._should_stop():
                ok, frame = cap.read()
                if not ok:
                    break

                self._on_frame(frame)

                pos: Optional[HeadPosition] = self._tracker.process_frame(frame)

                if pos is not None:
                    self._last_face_ms = time.monotonic() * 1000.0
                    smoothed = self._smoother.update(pos.x_cm, pos.y_cm, pos.z_cm)
                    self._last_smoothed = smoothed
                    x, y, z = smoothed
                    status = "tracking"
                else:
                    now_ms = time.monotonic() * 1000.0
                    hold_expired = (
                        self._last_face_ms is None
                        or now_ms - self._last_face_ms > self._hold_ms
                    )
                    if hold_expired:
                        x, y, z = 0.0, 0.0, 60.0
                        status = "paused"
                    else:
                        x, y, z = self._last_smoothed
                        status = "hold"

                self._writer.write(x=x, y=y, z=z)
                self._on_position(x, y, z, status)
                frame_count += 1
                if max_frames is not None and frame_count >= max_frames:
                    break
        finally:
            cap.release()

    # --- Hook methods: override in subclasses ---

    def _should_stop(self) -> bool:
        """Return True to exit the loop. Base class never stops early."""
        return False

    def _on_frame(self, frame: object) -> None:  # noqa: ARG002
        """Called with each captured frame before face detection."""

    def _on_position(self, x: float, y: float, z: float, status: str) -> None:  # noqa: ARG002
        """Called after each position is computed and written."""
```

- [ ] **Step 4: Run all main tests — expect pass**

```bash
pytest tests/test_main.py -v
```

Expected: 6 tests PASS (5 original + 1 new).

- [ ] **Step 5: Commit**

```bash
git add tracker/main.py tests/test_main.py
git commit -m "feat: add _should_stop/_on_frame/_on_position hooks to TrackingLoop"
```

---

## Task 5: `launcher/tracker_thread.py` — QThread tracker

**Files:**
- Create: `launcher/tracker_thread.py`
- Create: `tests/test_tracker_thread.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tracker_thread.py
import threading
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy

from launcher.tracker_thread import TrackerThread

CONFIG = {
    "camera": {"index": 0},
    "screen": {"width_cm": 59.8, "height_cm": 33.6},
    "tracking": {
        "ipd_cm": 6.3,
        "smoothing_q": 0.01,
        "smoothing_r": 0.1,
        "hold_ms": 500,
    },
}


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    return app


def _make_mock_cap(frames=3):
    cap = MagicMock()
    cap.isOpened.return_value = True
    frame = MagicMock()
    reads = [(True, frame)] * frames + [(False, None)]
    cap.read.side_effect = reads
    return cap


def test_tracker_thread_emits_position_updated(qapp):
    mock_cap = _make_mock_cap(frames=2)
    mock_face_pos = MagicMock()
    mock_face_pos.x_cm = 1.0
    mock_face_pos.y_cm = 0.0
    mock_face_pos.z_cm = 60.0

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=mock_cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
    ):
        ft_instance = MockFT.return_value.__enter__.return_value
        ft_instance.process_frame.return_value = mock_face_pos
        MockFW.return_value.__enter__.return_value = MagicMock()
        hs_instance = MockHS.return_value
        hs_instance.update.return_value = (1.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        spy = QSignalSpy(thread.position_updated)
        thread.start()
        thread.wait(2000)

    assert len(spy) >= 1
    first = spy[0]
    assert first[0] == pytest.approx(1.0)


def test_tracker_thread_emits_status_changed_tracking(qapp):
    mock_cap = _make_mock_cap(frames=1)
    mock_face_pos = MagicMock()
    mock_face_pos.x_cm = 0.0
    mock_face_pos.y_cm = 0.0
    mock_face_pos.z_cm = 60.0

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=mock_cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
    ):
        MockFT.return_value.__enter__.return_value.process_frame.return_value = mock_face_pos
        MockFW.return_value.__enter__.return_value = MagicMock()
        MockHS.return_value.update.return_value = (0.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        spy = QSignalSpy(thread.status_changed)
        thread.start()
        thread.wait(2000)

    statuses = [s[0] for s in spy]
    assert "tracking" in statuses


def test_tracker_thread_stop_terminates_thread(qapp):
    # Cap that reads forever until stop event
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.read.return_value = (True, MagicMock())

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother") as MockHS,
    ):
        MockFT.return_value.__enter__.return_value.process_frame.return_value = None
        MockFW.return_value.__enter__.return_value = MagicMock()
        MockHS.return_value.update.return_value = (0.0, 0.0, 60.0)

        thread = TrackerThread(camera_index=0, config=CONFIG)
        thread.start()
        assert thread.isRunning()
        thread.stop()
        assert not thread.isRunning()


def test_tracker_thread_emits_error_status_on_camera_failure(qapp):
    cap = MagicMock()
    cap.isOpened.return_value = False

    with (
        patch("launcher.tracker_thread.cv2.VideoCapture", return_value=cap),
        patch("launcher.tracker_thread.FaceTracker") as MockFT,
        patch("launcher.tracker_thread.FreetracWriter") as MockFW,
        patch("launcher.tracker_thread.HeadSmoother"),
    ):
        MockFT.return_value.__enter__.return_value = MagicMock()
        MockFW.return_value.__enter__.return_value = MagicMock()

        thread = TrackerThread(camera_index=0, config=CONFIG)
        spy = QSignalSpy(thread.status_changed)
        thread.start()
        thread.wait(2000)

    statuses = [s[0] for s in spy]
    assert "error" in statuses
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_tracker_thread.py -v
```

Expected: `ImportError: cannot import name 'TrackerThread'`

- [ ] **Step 3: Implement `launcher/tracker_thread.py`**

```python
"""QThread that runs the head-tracking loop and emits Qt signals."""
from __future__ import annotations

import threading
from typing import Callable

import cv2
from PySide6.QtCore import QThread, Signal

from tracker.face_tracker import FaceTracker
from tracker.freetrack import FreetracWriter
from tracker.main import TrackingLoop
from tracker.smoother import HeadSmoother


class _SignallingLoop(TrackingLoop):
    """TrackingLoop subclass that checks a stop event and calls signal callbacks."""

    def __init__(
        self,
        stop_event: threading.Event,
        on_frame: Callable[[bytes], None],
        on_position: Callable[[float, float, float], None],
        on_status: Callable[[str], None],
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._stop_event = stop_event
        self._on_frame_cb = on_frame
        self._on_position_cb = on_position
        self._on_status_cb = on_status

    def _should_stop(self) -> bool:
        return self._stop_event.is_set()

    def _on_frame(self, frame: object) -> None:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        if ok:
            self._on_frame_cb(bytes(buf))

    def _on_position(self, x: float, y: float, z: float, status: str) -> None:
        self._on_position_cb(x, y, z)
        self._on_status_cb(status)


class TrackerThread(QThread):
    """Runs the tracking loop in a background thread, emitting Qt signals."""

    position_updated = Signal(float, float, float)  # x, y, z in cm
    frame_ready = Signal(bytes)                      # JPEG-encoded camera frame
    status_changed = Signal(str)                     # "tracking"|"hold"|"paused"|"error"

    def __init__(self, camera_index: int, config: dict, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._camera_index = camera_index
        self._config = config
        self._stop_event = threading.Event()

    def run(self) -> None:
        trk = self._config["tracking"]
        scr = self._config["screen"]
        smoother = HeadSmoother(
            process_noise=trk["smoothing_q"],
            measurement_noise=trk["smoothing_r"],
        )
        try:
            with (
                FaceTracker(
                    real_ipd_cm=trk["ipd_cm"],
                    screen_width_cm=scr["width_cm"],
                    screen_height_cm=scr["height_cm"],
                ) as tracker,
                FreetracWriter() as writer,
            ):
                loop = _SignallingLoop(
                    stop_event=self._stop_event,
                    on_frame=self.frame_ready.emit,
                    on_position=self.position_updated.emit,
                    on_status=self.status_changed.emit,
                    tracker=tracker,
                    writer=writer,
                    smoother=smoother,
                    hold_ms=trk["hold_ms"],
                )
                loop.run(camera_index=self._camera_index)
        except RuntimeError:
            self.status_changed.emit("error")

    def stop(self) -> None:
        """Signal the loop to stop and block until the thread exits."""
        self._stop_event.set()
        self.wait()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_tracker_thread.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/tracker_thread.py tests/test_tracker_thread.py
git commit -m "feat: add TrackerThread QThread with stop event and Qt signals"
```

---

## Task 6: `launcher/wizard.py` — Pages 1–3 (Welcome, Game Dir, Install)

**Files:**
- Create: `launcher/wizard.py` (partial — pages 1-3 only; pages 4-5 added in Task 7)
- Create: `tests/test_wizard.py` (partial)

- [ ] **Step 1: Write failing tests for pages 1-3**

```python
# tests/test_wizard.py
import os
import winreg
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from launcher.wizard import WelcomePage, GameDirPage, InstallPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_welcome_page_has_title(qapp):
    page = WelcomePage()
    assert "Glassless" in page.title() or "Welcome" in page.title()


def test_welcome_page_is_complete_by_default(qapp):
    page = WelcomePage()
    assert page.isComplete()


def test_game_dir_page_detects_wow_from_registry(qapp, tmp_path):
    game_dir = str(tmp_path / "WoW")
    os.makedirs(game_dir)

    mock_key = MagicMock()
    with (
        patch("launcher.wizard.winreg.OpenKey", return_value=mock_key),
        patch("launcher.wizard.winreg.QueryValueEx", return_value=(game_dir, None)),
    ):
        page = GameDirPage()
        page.initializePage()

    assert page._dir_edit.text() == game_dir


def test_game_dir_page_complete_when_dir_set(qapp, tmp_path):
    page = GameDirPage()
    page._dir_edit.setText(str(tmp_path))
    assert page.isComplete()


def test_game_dir_page_incomplete_when_dir_empty(qapp):
    page = GameDirPage()
    page._dir_edit.setText("")
    assert not page.isComplete()


def test_install_page_calls_install_steps(qapp, tmp_path):
    """InstallPage._run_install uses install_steps generator."""
    game_dir = str(tmp_path)
    steps_yielded = []

    def fake_install_steps(gd, profile_name="wow"):
        steps_yielded.append(gd)
        yield "Copying ReShade"
        yield "Copying shaders"
        yield "Writing ReShade.ini"
        yield "Installing addon"

    with patch("launcher.wizard.install_steps", side_effect=fake_install_steps):
        page = InstallPage()
        page._game_dir = game_dir
        page._run_install()

    assert steps_yielded == [game_dir]
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_wizard.py -v
```

Expected: `ImportError: cannot import name 'WelcomePage'`

- [ ] **Step 3: Implement `launcher/wizard.py` pages 1-3**

```python
"""5-page first-run setup wizard."""
from __future__ import annotations

import os
import winreg
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from launcher.reshade_install import InstallError, install_steps


# ── Page 1: Welcome ────────────────────────────────────────────────────────────

class WelcomePage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Welcome to Glassless3D")
        self.setSubTitle(
            "Turn your flat monitor into glassless 3D. "
            "Setup takes about 60 seconds."
        )
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Click Next to begin."))


# ── Page 2: Game Directory ─────────────────────────────────────────────────────

_WOW_REGISTRY_KEY = r"SOFTWARE\Blizzard Entertainment\World of Warcraft"
_WOW_REGISTRY_VALUE = "InstallPath"


class GameDirPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Select your game folder")
        self.setSubTitle(
            "Glassless3D will install into this directory."
        )

        self._dir_edit = QLineEdit()
        self._dir_edit.setPlaceholderText("Game directory…")
        self._dir_edit.textChanged.connect(self.completeChanged)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)

        layout = QVBoxLayout(self)
        layout.addWidget(self._dir_edit)
        layout.addWidget(browse_btn)

        self.registerField("game_dir*", self._dir_edit)

    def initializePage(self) -> None:
        detected = self._detect_wow()
        if detected:
            self._dir_edit.setText(detected)

    def isComplete(self) -> bool:
        return bool(self._dir_edit.text().strip())

    def _detect_wow(self) -> Optional[str]:
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, _WOW_REGISTRY_KEY
            ) as key:
                value, _ = winreg.QueryValueEx(key, _WOW_REGISTRY_VALUE)
                if os.path.isdir(value):
                    return value
        except (FileNotFoundError, OSError):
            pass
        return None

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, "Select game folder")
        if path:
            self._dir_edit.setText(path)


# ── Page 3: Auto-Install ───────────────────────────────────────────────────────

class _InstallWorker(QThread):
    step_done = Signal(str)
    all_done = Signal()
    failed = Signal(str, str)

    def __init__(self, game_dir: str, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._game_dir = game_dir

    def run(self) -> None:
        try:
            for step_name in install_steps(self._game_dir):
                self.step_done.emit(step_name)
            self.all_done.emit()
        except InstallError as e:
            self.failed.emit(e.step, e.reason)


class InstallPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Installing…")
        self.setSubTitle("No internet needed. This takes a few seconds.")

        self._status_label = QLabel("Preparing…")
        self._progress = QProgressBar()
        self._progress.setRange(0, 4)
        self._progress.setValue(0)
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: red;")

        layout = QVBoxLayout(self)
        layout.addWidget(self._status_label)
        layout.addWidget(self._progress)
        layout.addWidget(self._error_label)

        self._complete = False
        self._worker: Optional[_InstallWorker] = None
        self._game_dir: str = ""

    def initializePage(self) -> None:
        self._game_dir = self.field("game_dir")
        self._complete = False
        self._error_label.setText("")
        self._progress.setValue(0)
        self._worker = _InstallWorker(self._game_dir)
        self._worker.step_done.connect(self._on_step)
        self._worker.all_done.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _run_install(self) -> None:
        """Synchronous install used in tests (bypasses QThread)."""
        for step_name in install_steps(self._game_dir):
            self._on_step(step_name)
        self._on_done()

    def _on_step(self, name: str) -> None:
        self._status_label.setText(name)
        self._progress.setValue(self._progress.value() + 1)

    def _on_done(self) -> None:
        self._complete = True
        self.completeChanged.emit()
        # Auto-advance after brief pause
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: self.wizard().next() if self.wizard() else None)

    def _on_failed(self, step: str, reason: str) -> None:
        self._error_label.setText(f"Failed at '{step}': {reason}")

    def isComplete(self) -> bool:
        return self._complete
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_wizard.py -v
```

Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/wizard.py tests/test_wizard.py
git commit -m "feat: add wizard pages 1-3 (welcome, game dir, auto-install)"
```

---

## Task 7: `launcher/wizard.py` — Pages 4–5 (Camera & Screen, Done)

**Files:**
- Modify: `launcher/wizard.py` (append pages 4-5 and `SetupWizard` class)
- Modify: `tests/test_wizard.py` (add tests for pages 4-5)

- [ ] **Step 1: Write failing tests for pages 4-5**

Add to `tests/test_wizard.py`:

```python
from launcher.wizard import CameraScreenPage, DonePage


def test_camera_screen_page_populates_combo(qapp):
    """CameraScreenPage lists cameras found by probing VideoCapture."""
    mock_cap = MagicMock()
    # index 0 opens; index 1+ fail
    mock_cap.isOpened.side_effect = [True, False, False, False, False]

    with patch("launcher.wizard.cv2.VideoCapture", return_value=mock_cap):
        page = CameraScreenPage()
        page.initializePage()

    assert page._camera_combo.count() >= 1


def test_camera_screen_page_fills_screen_from_edid(qapp):
    with patch("launcher.wizard.detect_screen_size_cm", return_value=(59.8, 33.6)):
        page = CameraScreenPage()
        page.initializePage()

    assert page._width_edit.text() == "59.8"
    assert page._height_edit.text() == "33.6"


def test_camera_screen_page_leaves_screen_blank_on_edid_failure(qapp):
    with patch("launcher.wizard.detect_screen_size_cm", return_value=None):
        page = CameraScreenPage()
        page.initializePage()

    assert page._width_edit.text() == ""
    assert page._height_edit.text() == ""


def test_done_page_writes_config(qapp, tmp_path):
    import yaml
    config_path = str(tmp_path / "config.yaml")

    page = DonePage(config_path=config_path)
    # Simulate field values by subclassing field() lookup
    page._camera_index = 0
    page._screen_width_cm = 59.8
    page._screen_height_cm = 33.6
    page._write_config()

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    assert cfg["camera"]["index"] == 0
    assert cfg["screen"]["width_cm"] == pytest.approx(59.8)
    assert cfg["tracking"]["ipd_cm"] == 6.3
    assert "gui" in cfg
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_wizard.py -k "camera or done" -v
```

Expected: `ImportError: cannot import name 'CameraScreenPage'`

- [ ] **Step 3: Append pages 4-5 and `SetupWizard` to `launcher/wizard.py`**

First, update the import block at the **top** of `launcher/wizard.py` — add these lines after the existing imports:

```python
import cv2
import yaml
from PySide6.QtWidgets import QComboBox  # add to existing PySide6 import line
from launcher.edid import detect_screen_size_cm
```

Then append the following to the **end** of `launcher/wizard.py`:

```python
import cv2
import yaml
from launcher.edid import detect_screen_size_cm


# ── Page 4: Camera & Screen ────────────────────────────────────────────────────

class CameraScreenPage(QWizardPage):
    def __init__(self, parent: Optional[object] = None) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Camera & Screen")
        self.setSubTitle(
            "Select your webcam and confirm your monitor size."
        )

        self._camera_combo = QComboBox()
        self._width_edit = QLineEdit()
        self._width_edit.setPlaceholderText("Width (cm)")
        self._height_edit = QLineEdit()
        self._height_edit.setPlaceholderText("Height (cm)")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Webcam:"))
        layout.addWidget(self._camera_combo)
        layout.addWidget(QLabel("Monitor width (cm):"))
        layout.addWidget(self._width_edit)
        layout.addWidget(QLabel("Monitor height (cm):"))
        layout.addWidget(self._height_edit)

        self.registerField("camera_index", self._camera_combo, "currentIndex",
                           self._camera_combo.currentIndexChanged)
        self.registerField("screen_width_cm*", self._width_edit)
        self.registerField("screen_height_cm*", self._height_edit)

    def initializePage(self) -> None:
        self._probe_cameras()
        dims = detect_screen_size_cm()
        if dims is not None:
            self._width_edit.setText(f"{dims[0]:.1f}")
            self._height_edit.setText(f"{dims[1]:.1f}")

    def _probe_cameras(self) -> None:
        self._camera_combo.clear()
        for idx in range(5):
            cap = cv2.VideoCapture(idx)
            if cap.isOpened():
                self._camera_combo.addItem(f"Camera {idx}", idx)
                cap.release()
            else:
                cap.release()
                break


# ── Page 5: Done ───────────────────────────────────────────────────────────────

_DEFAULT_TRACKING = {
    "ipd_cm": 6.3,
    "smoothing_q": 0.01,
    "smoothing_r": 0.1,
    "hold_ms": 500,
}


class DonePage(QWizardPage):
    def __init__(
        self,
        config_path: str,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setTitle("Ready to go!")
        self.setSubTitle(
            "Launch your game, press Home to open ReShade, then enable Glassless3D."
        )
        self._config_path = config_path
        # Cached field values (set in initializePage)
        self._camera_index: int = 0
        self._screen_width_cm: float = 59.8
        self._screen_height_cm: float = 33.6

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Click Finish to start tracking."))

    def initializePage(self) -> None:
        self._camera_index = self.field("camera_index")
        try:
            self._screen_width_cm = float(self.field("screen_width_cm"))
            self._screen_height_cm = float(self.field("screen_height_cm"))
        except (ValueError, TypeError):
            self._screen_width_cm = 59.8
            self._screen_height_cm = 33.6

    def validatePage(self) -> bool:
        self._write_config()
        return True

    def _write_config(self) -> None:
        config = {
            "camera": {"index": self._camera_index},
            "screen": {
                "width_cm": self._screen_width_cm,
                "height_cm": self._screen_height_cm,
            },
            "tracking": _DEFAULT_TRACKING,
            "gui": {"compact_mode": False},
        }
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)


# ── SetupWizard ────────────────────────────────────────────────────────────────

class SetupWizard(QWizard):
    def __init__(
        self,
        config_path: str,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self.setWindowTitle("Glassless3D Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.addPage(WelcomePage())
        self.addPage(GameDirPage())
        self.addPage(InstallPage())
        self.addPage(CameraScreenPage())
        self.addPage(DonePage(config_path=config_path))
```

- [ ] **Step 4: Run all wizard tests — expect pass**

```bash
pytest tests/test_wizard.py -v
```

Expected: 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/wizard.py tests/test_wizard.py
git commit -m "feat: complete wizard with camera/screen page and config write"
```

---

## Task 8: `launcher/mainwindow.py` — Two-mode tracker window

**Files:**
- Create: `launcher/mainwindow.py`
- Create: `tests/test_mainwindow.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mainwindow.py
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from launcher.mainwindow import MainWindow

CONFIG = {
    "camera": {"index": 0},
    "screen": {"width_cm": 59.8, "height_cm": 33.6},
    "tracking": {
        "ipd_cm": 6.3,
        "smoothing_q": 0.01,
        "smoothing_r": 0.1,
        "hold_ms": 500,
    },
    "gui": {"compact_mode": False},
}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


@pytest.fixture
def window(qapp, tmp_path):
    cfg_path = str(tmp_path / "config.yaml")
    with patch("launcher.mainwindow.TrackerThread"):
        win = MainWindow(config=CONFIG, config_path=cfg_path)
    return win


def test_mainwindow_starts_in_expanded_mode(window):
    assert not window._compact
    # Expanded width ~270, compact ~400 — expanded must be narrower
    assert window.width() < 350


def test_mainwindow_toggle_switches_to_compact(window):
    window._toggle_mode()
    assert window._compact
    assert window.width() >= 350


def test_mainwindow_toggle_back_to_expanded(window):
    window._compact = True
    window._apply_mode()
    window._toggle_mode()
    assert not window._compact
    assert window.width() < 350


def test_mainwindow_xyz_labels_update_on_signal(window):
    window._on_position(2.5, -1.0, 57.3)
    assert "2.5" in window._label_x.text()
    assert "-1.0" in window._label_y.text()
    assert "57.3" in window._label_z.text()


def test_mainwindow_status_badge_tracking(window):
    window._on_status("tracking")
    assert "TRACKING" in window._status_label.text().upper()


def test_mainwindow_status_badge_error(window):
    window._on_status("error")
    assert "CAMERA" in window._status_label.text().upper() or \
           "ERROR" in window._status_label.text().upper()


def test_mainwindow_is_always_on_top(window):
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_mainwindow.py -v
```

Expected: `ImportError: cannot import name 'MainWindow'`

- [ ] **Step 3: Implement `launcher/mainwindow.py`**

```python
"""Always-on-top two-mode tracker window."""
from __future__ import annotations

from typing import Optional

import yaml
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher.tracker_thread import TrackerThread

# Window dimensions
_EXPANDED_W, _EXPANDED_H = 270, 310
_COMPACT_W, _COMPACT_H = 400, 100

_STATUS_TEXT = {
    "tracking": "● TRACKING",
    "hold":     "● HOLD",
    "paused":   "● PAUSED",
    "stopped":  "● STOPPED",
    "error":    "✕ NO CAMERA",
}
_STATUS_COLOR = {
    "tracking": "#28c840",
    "hold":     "#febc2e",
    "paused":   "#888888",
    "stopped":  "#888888",
    "error":    "#e84040",
}
_DARK_BG = "#0d0d22"
_TITLE_BG = "#1a1a2e"


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: dict,
        config_path: str,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._config = config
        self._config_path = config_path
        self._compact: bool = config.get("gui", {}).get("compact_mode", False)
        self._thread: Optional[TrackerThread] = None
        self._drag_pos: Optional[QPoint] = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._build_ui()
        self._apply_mode()
        self._on_status("stopped")

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet(f"background:{_DARK_BG};border-radius:8px;")
        self.setCentralWidget(root)
        self._root_layout = QVBoxLayout(root)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._root_layout.addWidget(self._make_titlebar())
        self._camera_label = QLabel()
        self._camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_label.setStyleSheet("background:#0a0a0a;")
        self._root_layout.addWidget(self._camera_label)
        self._root_layout.addLayout(self._make_xyz_row())
        self._root_layout.addWidget(self._make_action_button())

    def _make_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(f"background:{_TITLE_BG};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)

        logo = QLabel("● GLASSLESS 3D")
        logo.setStyleSheet("color:#c8c8e8;font-size:11px;font-weight:bold;")
        self._status_label = QLabel("● STOPPED")
        self._status_label.setStyleSheet("color:#888;font-size:10px;")
        self._toggle_btn = QPushButton("▲")
        self._toggle_btn.setFixedSize(24, 18)
        self._toggle_btn.setStyleSheet(
            "background:transparent;color:#555;font-size:10px;border:none;"
        )
        self._toggle_btn.clicked.connect(self._toggle_mode)

        layout.addWidget(logo)
        layout.addStretch()
        layout.addWidget(self._status_label)
        layout.addWidget(self._toggle_btn)
        return bar

    def _make_xyz_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        self._label_x = self._xyz_tile("X")
        self._label_y = self._xyz_tile("Y")
        self._label_z = self._xyz_tile("Z")
        for lbl in (self._label_x, self._label_y, self._label_z):
            row.addWidget(lbl)
        return row

    def _xyz_tile(self, axis: str) -> QLabel:
        tile = QLabel(f"{axis}\n0.0")
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            "background:#111128;color:#3ecfcf;font-family:monospace;"
            "font-size:13px;font-weight:bold;border-radius:3px;padding:4px;"
        )
        return tile

    def _make_action_button(self) -> QPushButton:
        self._action_btn = QPushButton("▶ START TRACKING")
        self._action_btn.setStyleSheet(
            "background:#28c840;color:#111;font-weight:bold;"
            "font-size:11px;padding:8px;border:none;"
        )
        self._action_btn.clicked.connect(self._toggle_tracking)
        return self._action_btn

    # ── Mode switching ─────────────────────────────────────────────────────────

    def _toggle_mode(self) -> None:
        self._compact = not self._compact
        self._apply_mode()
        self._save_compact_pref()

    def _apply_mode(self) -> None:
        if self._compact:
            self.setFixedSize(_COMPACT_W, _COMPACT_H)
            self._camera_label.setFixedHeight(72)
            self._toggle_btn.setText("▼")
        else:
            self.setFixedSize(_EXPANDED_W, _EXPANDED_H)
            self._camera_label.setFixedHeight(150)
            self._toggle_btn.setText("▲")

    def _save_compact_pref(self) -> None:
        try:
            import os
            with open(self._config_path) as f:
                cfg = yaml.safe_load(f)
            cfg.setdefault("gui", {})["compact_mode"] = self._compact
            with open(self._config_path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
        except OSError:
            pass

    # ── Tracking control ───────────────────────────────────────────────────────

    def _toggle_tracking(self) -> None:
        if self._thread and self._thread.isRunning():
            self._stop_tracking()
        else:
            self._start_tracking()

    def _start_tracking(self) -> None:
        cam_idx = self._config["camera"]["index"]
        self._thread = TrackerThread(camera_index=cam_idx, config=self._config)
        self._thread.position_updated.connect(self._on_position)
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.status_changed.connect(self._on_status)
        self._thread.start()
        self._action_btn.setText("■ STOP TRACKING")
        self._action_btn.setStyleSheet(
            "background:#e84040;color:#fff;font-weight:bold;"
            "font-size:11px;padding:8px;border:none;"
        )

    def _stop_tracking(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None
        self._on_status("stopped")
        self._action_btn.setText("▶ START TRACKING")
        self._action_btn.setStyleSheet(
            "background:#28c840;color:#111;font-weight:bold;"
            "font-size:11px;padding:8px;border:none;"
        )

    # ── Signal slots ───────────────────────────────────────────────────────────

    def _on_position(self, x: float, y: float, z: float) -> None:
        self._label_x.setText(f"X\n{x:+.1f}")
        self._label_y.setText(f"Y\n{y:+.1f}")
        self._label_z.setText(f"Z\n{z:.1f}")

    def _on_frame(self, jpeg: bytes) -> None:
        pix = QPixmap()
        pix.loadFromData(jpeg, "JPEG")
        self._camera_label.setPixmap(
            pix.scaled(
                self._camera_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_status(self, status: str) -> None:
        text = _STATUS_TEXT.get(status, f"● {status.upper()}")
        color = _STATUS_COLOR.get(status, "#888")
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            f"color:{color};font-size:10px;font-weight:bold;"
        )

    # ── Drag to move ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event: object) -> None:
        if event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
            self._drag_pos = (
                event.globalPosition().toPoint()  # type: ignore[attr-defined]
                - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event: object) -> None:
        if (
            self._drag_pos is not None
            and event.buttons() == Qt.MouseButton.LeftButton  # type: ignore[attr-defined]
        ):
            self.move(
                event.globalPosition().toPoint()  # type: ignore[attr-defined]
                - self._drag_pos
            )

    def closeEvent(self, event: object) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        event.accept()  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_mainwindow.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add launcher/mainwindow.py tests/test_mainwindow.py
git commit -m "feat: add two-mode always-on-top MainWindow"
```

---

## Task 9: `launcher/app.py` — Entry point

**Files:**
- Create: `launcher/app.py`
- Create: `tests/test_app.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_app.py
import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

from launcher.app import CONFIG_PATH, _is_first_run, _load_config


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_is_first_run_true_when_config_absent(tmp_path):
    path = str(tmp_path / "nonexistent.yaml")
    assert _is_first_run(path) is True


def test_is_first_run_false_when_config_exists(tmp_path):
    path = str(tmp_path / "config.yaml")
    open(path, "w").close()
    assert _is_first_run(path) is False


def test_load_config_returns_dict(tmp_path):
    import yaml
    cfg = {"camera": {"index": 0}, "screen": {"width_cm": 60.0, "height_cm": 34.0},
           "tracking": {"ipd_cm": 6.3, "smoothing_q": 0.01, "smoothing_r": 0.1, "hold_ms": 500},
           "gui": {"compact_mode": False}}
    path = str(tmp_path / "config.yaml")
    with open(path, "w") as f:
        yaml.dump(cfg, f)
    loaded = _load_config(path)
    assert loaded["camera"]["index"] == 0


def test_config_path_uses_appdata():
    appdata = os.environ.get("APPDATA", ".")
    assert CONFIG_PATH.startswith(appdata)
    assert CONFIG_PATH.endswith("config.yaml")
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_app.py -v
```

Expected: `ImportError: cannot import name 'CONFIG_PATH'`

- [ ] **Step 3: Implement `launcher/app.py`**

```python
"""QApplication entry point for Glassless3D."""
from __future__ import annotations

import os
import sys

import yaml
from PySide6.QtWidgets import QApplication, QWizard

CONFIG_PATH = os.path.join(
    os.environ.get("APPDATA", "."), "Glassless3D", "config.yaml"
)


def _is_first_run(config_path: str = CONFIG_PATH) -> bool:
    return not os.path.exists(config_path)


def _load_config(config_path: str = CONFIG_PATH) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Glassless3D")

    if _is_first_run():
        from launcher.wizard import SetupWizard
        wizard = SetupWizard(config_path=CONFIG_PATH)
        if wizard.exec() != QWizard.DialogCode.Accepted:
            sys.exit(0)

    config = _load_config()
    from launcher.mainwindow import MainWindow
    window = MainWindow(config=config, config_path=CONFIG_PATH)
    window.show()
    sys.exit(app.exec())
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_app.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest --tb=short -q
```

Expected: all tests PASS, ≥80% coverage.

- [ ] **Step 6: Commit**

```bash
git add launcher/app.py tests/test_app.py
git commit -m "feat: add app.py entry point with first-run detection"
```

---

## Task 10: PyInstaller spec (`Glassless3D.spec`)

**Files:**
- Create: `Glassless3D.spec`

No automated tests — verify manually by running the build and launching the exe.

- [ ] **Step 1: Create `Glassless3D.spec`**

```python
# Glassless3D.spec
# Build: pyinstaller Glassless3D.spec
# Output: dist/Glassless3D.exe (~90 MB)

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Collect mediapipe model data
mediapipe_data = collect_data_files("mediapipe")
mediapipe_libs = collect_dynamic_libs("mediapipe")

a = Analysis(
    ["launcher/__main__.py"],
    pathex=["."],
    binaries=mediapipe_libs,
    datas=[
        # Bundled ReShade assets
        ("ReShade64.dll",          "."),
        ("shaders/Glassless3D.fx", "shaders"),
        ("shaders/Glassless3D.fxh","shaders"),
        ("Glassless3D.addon",      "."),
        ("profiles/wow.json",      "profiles"),
        ("profiles/default.json",  "profiles"),
        # MediaPipe models
        *mediapipe_data,
    ],
    hiddenimports=[
        "wmi",
        "win32com",
        "win32com.client",
        "pythoncom",
        "pywintypes",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Glassless3D",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no console window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # add icon= "icon.ico" when available
    onefile=True,
)
```

- [ ] **Step 2: Verify build prerequisites exist**

```bash
ls ReShade64.dll shaders/Glassless3D.fx Glassless3D.addon profiles/wow.json
```

Expected: all files present. If `ReShade64.dll` is missing, download from reshade.me (MIT) and rename from the installer.

- [ ] **Step 3: Build**

```bash
pip install pyinstaller
pyinstaller Glassless3D.spec
```

Expected: `dist/Glassless3D.exe` created, no errors.

- [ ] **Step 4: Smoke-test the exe**

Run `dist/Glassless3D.exe` on a Windows machine with a webcam.
- First launch: wizard appears
- Complete wizard: config written to `%APPDATA%\Glassless3D\config.yaml`
- Main window appears: always-on-top, compact toggle works
- Start Tracking: camera preview shows, XYZ updates
- Stop Tracking: status badge shows STOPPED

- [ ] **Step 5: Commit**

```bash
git add Glassless3D.spec
git commit -m "feat: add PyInstaller spec for single-exe distribution"
```

---

## Completion Checklist

- [ ] `pytest --tb=short -q` — all tests pass
- [ ] `pytest --cov=launcher --cov-report=term-missing` — ≥80% coverage on `launcher/`
- [ ] `dist/Glassless3D.exe` builds without errors
- [ ] First-run wizard completes and writes `config.yaml`
- [ ] Main window always-on-top, both modes work, tracking starts/stops cleanly
