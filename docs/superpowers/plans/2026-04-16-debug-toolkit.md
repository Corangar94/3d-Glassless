# Debug Toolkit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two debug tools — a live PySide6 monitor that reads G3D shared memory and shows head pose + calculated parallax shift, and an enhanced fake_tracker with static/sweep/interactive modes — so we can diagnose the "watery / tilts strangely" overlay bug without touching C++.

**Architecture:** `SharedMemoryReader` (read-only counterpart to the existing `SharedMemoryWriter`) is added to `tracker/shared_memory.py`. `tracker/debug_monitor.py` is a new file containing `compute_shift_pct` (pure math, testable without Qt) and `MonitorWidget` (PySide6 window polling both SHM segments at 60 Hz). `tests/fake_tracker.py` gains three CLI modes powered by new module-level functions.

**Tech Stack:** Python 3.11, PySide6 6.11 (already installed), `ctypes`/`struct` (already used in shared_memory.py), `msvcrt` (Windows stdlib, single-key input for interactive mode).

**Run tests with:** `"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/ -v`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `tracker/shared_memory.py` | Add `_FILE_MAP_READ` constant + `SharedMemoryReader` class |
| Create | `tracker/debug_monitor.py` | `compute_shift_pct`, `_shift_tag`, `_is_stale`, `MonitorWidget`, `main()` |
| Modify | `tests/fake_tracker.py` | Add `_compute_shift_pct`, `_shift_tag`, `_static_mode`, `_sweep_mode`, `_interactive_mode`, argparse entrypoint |
| Create | `tests/test_debug_monitor.py` | Tests for `SharedMemoryReader`, `compute_shift_pct`, `_is_stale` |
| Create | `tests/test_fake_tracker_modes.py` | Tests for fake_tracker pure functions and static-mode ValueError |

---

## Task 1: SharedMemoryReader

**Files:**
- Modify: `tracker/shared_memory.py`
- Create: `tests/test_debug_monitor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_debug_monitor.py`:

```python
import time
import pytest
from tracker.shared_memory import SharedMemoryWriter, SharedMemoryReader


def test_reader_returns_none_when_absent():
    """Reader returns None if no writer has created the segment."""
    reader = SharedMemoryReader("G3D_TEST_ABSENT_XYZ")
    assert reader.read() is None
    reader.close()


def test_reader_reads_written_values():
    """After writer writes (x, y, z), reader returns the same values."""
    name = "G3D_TEST_RW"
    with SharedMemoryWriter(name) as w:
        w.write(x=12.5, y=-3.0, z=77.2)
        with SharedMemoryReader(name) as r:
            result = r.read()
    assert result is not None
    x, y, z, ts = result
    assert abs(x - 12.5) < 0.001
    assert abs(y - (-3.0)) < 0.001
    assert abs(z - 77.2) < 0.001
    assert ts > 0


def test_reader_context_manager():
    """SharedMemoryReader works as a context manager."""
    name = "G3D_TEST_CTX"
    with SharedMemoryWriter(name):
        with SharedMemoryReader(name) as r:
            assert r.read() is not None
```

- [ ] **Step 2: Run tests — expect FAIL (ImportError: cannot import name 'SharedMemoryReader')**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/test_debug_monitor.py -v
```

Expected: 3 errors — `ImportError: cannot import name 'SharedMemoryReader' from 'tracker.shared_memory'`

- [ ] **Step 3: Add `_FILE_MAP_READ` and `OpenFileMappingW` setup to `tracker/shared_memory.py`**

After line 11 (`_INVALID_HANDLE = ...`), add:

```python
_FILE_MAP_READ = 0x0004
```

After line 17 (`_k32.CreateFileMappingW.restype = ctypes.c_void_p`), add:

```python
_k32.OpenFileMappingW.restype = ctypes.c_void_p
```

- [ ] **Step 4: Add `SharedMemoryReader` class to `tracker/shared_memory.py`** — append after `SharedMemoryWriter`:

```python
class SharedMemoryReader:
    """Read-only view of a Windows Named Shared Memory segment.

    Returns None from read() if the segment does not exist yet.
    Retries attachment automatically on each read() call.
    """

    def __init__(self, name: str = "G3D") -> None:
        self._name = name
        self._handle: int | None = None
        self._view: int | None = None
        self._try_attach()

    def _try_attach(self) -> None:
        if self._view is not None:
            return
        if self._handle is None:
            self._handle = _k32.OpenFileMappingW(_FILE_MAP_READ, False, self._name)
            if self._handle is None:
                return  # writer not running yet
        self._view = _k32.MapViewOfFile(
            self._handle, _FILE_MAP_READ, 0, 0, STRUCT_SIZE,
        )
        if self._view is None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def read(self) -> tuple[float, float, float, int] | None:
        """Return (x_cm, y_cm, z_cm, timestamp_ms) or None if segment absent."""
        self._try_attach()
        if self._view is None:
            return None
        try:
            raw = (ctypes.c_char * STRUCT_SIZE).from_address(self._view)
            x, y, z, ts = struct.unpack(STRUCT_FORMAT, bytes(raw))
        except OSError:
            self._view = None  # stale; force re-attach next call
            return None
        return x, y, z, ts

    def close(self) -> None:
        if self._view is not None:
            _k32.UnmapViewOfFile(self._view)
            self._view = None
        if self._handle is not None:
            _k32.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> "SharedMemoryReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
```

- [ ] **Step 5: Run tests — expect all 3 PASS**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/test_debug_monitor.py -v
```

Expected:
```
PASSED tests/test_debug_monitor.py::test_reader_returns_none_when_absent
PASSED tests/test_debug_monitor.py::test_reader_reads_written_values
PASSED tests/test_debug_monitor.py::test_reader_context_manager
```

- [ ] **Step 6: Commit**

```bash
git add tracker/shared_memory.py tests/test_debug_monitor.py
git commit -m "feat: add SharedMemoryReader to tracker/shared_memory"
```

---

## Task 2: compute_shift_pct and helpers

**Files:**
- Create: `tracker/debug_monitor.py` (pure functions only, no Qt yet)
- Modify: `tests/test_debug_monitor.py`

- [ ] **Step 1: Add failing tests to `tests/test_debug_monitor.py`**

Append to the existing file:

```python
from tracker.debug_monitor import compute_shift_pct, _shift_tag, _is_stale


def test_compute_shift_pct_zero_head():
    """At (0, 0, 60) head position the shift is exactly 0%."""
    sx, sy = compute_shift_pct(0.0, 0.0, 60.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    assert sx == 0.0
    assert sy == 0.0


def test_compute_shift_pct_known_values():
    """Known inputs: headX=10, headZ=25, vd=30, sw=119.3, str=1.0 → ~4.86%."""
    # f = 30 / (25 + 30) = 0.5455
    # sx = abs(10 / 119.3) * 0.5455 * 1.0 * 100 ≈ 4.575%
    sx, sy = compute_shift_pct(10.0, 0.0, 25.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    assert abs(sx - 4.575) < 0.01
    assert sy == 0.0


def test_compute_shift_pct_uses_abs():
    """Negative headX gives same shift as positive headX (abs used)."""
    sx_pos, _ = compute_shift_pct(10.0, 0.0, 60.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    sx_neg, _ = compute_shift_pct(-10.0, 0.0, 60.0, 1.0, 1.0, 30.0, 119.3, 33.6)
    assert abs(sx_pos - sx_neg) < 0.001


def test_shift_tag_good():
    assert _shift_tag(1.5, 1.0) == "GOOD"


def test_shift_tag_high():
    assert _shift_tag(3.0, 1.0) == "HIGH"


def test_shift_tag_danger():
    assert _shift_tag(5.0, 1.0) == "DANGER"


def test_is_stale_true():
    """Timestamp 600 ms old is stale (threshold 500 ms)."""
    now = 10_000
    ts = now - 600
    assert _is_stale(ts, now) is True


def test_is_stale_false():
    """Timestamp 100 ms old is not stale."""
    now = 10_000
    ts = now - 100
    assert _is_stale(ts, now) is False


def test_is_stale_wraps():
    """Timestamp wraps correctly around 32-bit boundary."""
    now = 100
    ts = (0xFFFF_FFFF - 200)  # 200 ms before overflow, so age = 301 ms
    assert _is_stale(ts, now, threshold_ms=250) is True
```

- [ ] **Step 2: Run — expect FAIL (ModuleNotFoundError: No module named 'tracker.debug_monitor')**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/test_debug_monitor.py -v -k "shift or stale"
```

Expected: errors importing `tracker.debug_monitor`

- [ ] **Step 3: Create `tracker/debug_monitor.py` with pure functions only**

```python
# tracker/debug_monitor.py
"""Live read-only debug monitor for Glassless3D parallax state.

Pure functions (compute_shift_pct, _shift_tag, _is_stale) are importable
without triggering PySide6; the Qt window is created only when main() runs.
"""
from __future__ import annotations


def compute_shift_pct(
    head_x: float,
    head_y: float,
    head_z: float,
    strength_x: float,
    strength_y: float,
    virtual_depth_cm: float,
    screen_w_cm: float,
    screen_h_cm: float,
) -> tuple[float, float]:
    """Return (shift_x_pct, shift_y_pct) matching the overlay HLSL parallax formula.

    Formula: f = oz / (hz + oz);  shift_pct = abs(head / screen) * f * strength * 100
    """
    denom = head_z + virtual_depth_cm
    f = virtual_depth_cm / max(denom, 0.001)
    shift_x = abs(head_x / max(screen_w_cm, 0.001)) * f * strength_x * 100.0
    shift_y = abs(head_y / max(screen_h_cm, 0.001)) * f * strength_y * 100.0
    return shift_x, shift_y


def _shift_tag(shift_x_pct: float, shift_y_pct: float) -> str:
    """Return 'GOOD' / 'HIGH' / 'DANGER' based on worst-axis shift."""
    worst = max(shift_x_pct, shift_y_pct)
    if worst < 2.0:
        return "GOOD"
    if worst < 4.0:
        return "HIGH"
    return "DANGER"


def _is_stale(timestamp_ms: int, now_ms: int, threshold_ms: int = 500) -> bool:
    """Return True if timestamp_ms is older than threshold_ms. Handles 32-bit wrap."""
    age = (now_ms - timestamp_ms) & 0xFFFF_FFFF
    return age > threshold_ms
```

- [ ] **Step 4: Run — expect all new tests PASS**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/test_debug_monitor.py -v
```

Expected: all tests pass (the 3 reader tests from Task 1 + 9 new tests).

- [ ] **Step 5: Commit**

```bash
git add tracker/debug_monitor.py tests/test_debug_monitor.py
git commit -m "feat: add compute_shift_pct, _shift_tag, _is_stale to debug_monitor"
```

---

## Task 3: MonitorWidget (PySide6 window)

**Files:**
- Modify: `tracker/debug_monitor.py` — append `MonitorWidget` class and `main()`

No new automated tests (Qt event loop requires manual smoke test). The pure functions are already covered.

- [ ] **Step 1: Append Qt imports and constants to `tracker/debug_monitor.py`**

Append after `_is_stale`:

```python
import time

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from tracker.shared_memory import SharedMemoryReader
from tracker.shared_settings import OverlaySettings, SharedSettingsReader

_DEFAULT_SETTINGS = OverlaySettings(
    strength_x=1.0,
    strength_y=1.0,
    virtual_depth_cm=30.0,
    screen_w_cm=119.3,
    screen_h_cm=33.6,
)

_DEPTH_CURVE_NAMES: dict[int, str] = {0: "linear", 1: "sqrt", 2: "gamma"}
```

- [ ] **Step 2: Append `MonitorWidget` class to `tracker/debug_monitor.py`**

```python
class MonitorWidget(QWidget):
    """Read-only live display of G3D head pose + calculated parallax shift."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Glassless3D Debug Monitor")
        self.setMinimumWidth(500)

        self._pose_reader = SharedMemoryReader("G3D")
        self._settings_reader = SharedSettingsReader("G3D_Settings")

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(16)  # ~60 Hz

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Status row
        row = QHBoxLayout()
        self._status_lbl = QLabel("● NO TRACKER")
        self._status_lbl.setStyleSheet("color:#e74c3c; font-weight:bold;")
        self._age_lbl = QLabel("")
        self._age_lbl.setStyleSheet("color:#888;")
        row.addWidget(self._status_lbl)
        row.addWidget(self._age_lbl)
        row.addStretch()
        root.addLayout(row)

        # Head Pose panel
        pose_box = QGroupBox("Raw Head Pose (from G3D shared memory)")
        grid = QGridLayout(pose_box)
        mono_large = QFont("Courier New", 22)
        for col, name in enumerate(["headX", "headY", "headZ"]):
            lbl = QLabel(name)
            lbl.setStyleSheet("color:#888; font-size:11px;")
            grid.addWidget(lbl, 0, col)
        self._hx = QLabel("—")
        self._hy = QLabel("—")
        self._hz = QLabel("—")
        for col, lbl in enumerate([self._hx, self._hy, self._hz]):
            lbl.setFont(mono_large)
            grid.addWidget(lbl, 1, col)
        self._hz_warn = QLabel("")
        self._hz_warn.setStyleSheet("color:#e74c3c; font-size:10px;")
        grid.addWidget(self._hz_warn, 2, 2)
        root.addWidget(pose_box)

        # Parallax panel
        par_box = QGroupBox("Calculated Parallax (what the shader sees)")
        pgrid = QGridLayout(par_box)
        mono_med = QFont("Courier New", 14)
        for col, name in enumerate(["virtualDepth", "depth f", "shiftX %", "shiftY %"]):
            lbl = QLabel(name)
            lbl.setStyleSheet("color:#888; font-size:11px;")
            pgrid.addWidget(lbl, 0, col)
        self._vd = QLabel("—")
        self._f = QLabel("—")
        self._sx = QLabel("—")
        self._sy = QLabel("—")
        for col, lbl in enumerate([self._vd, self._f, self._sx, self._sy]):
            lbl.setFont(mono_med)
            pgrid.addWidget(lbl, 1, col)
        root.addWidget(par_box)

        # Settings panel
        set_box = QGroupBox("Settings (from G3D_Settings — or defaults if absent)")
        sgrid = QGridLayout(set_box)
        sgrid.setVerticalSpacing(2)
        self._set_vals: dict[str, QLabel] = {}
        fields = ["strengthX", "strengthY", "screenW cm", "screenH cm",
                  "cameraFOV°", "ipd mm", "depthCurve"]
        for i, name in enumerate(fields):
            r, c = divmod(i, 4)
            k = QLabel(f"{name}:")
            k.setStyleSheet("color:#888; font-size:11px;")
            v = QLabel("—")
            v.setStyleSheet("font-size:11px;")
            sgrid.addWidget(k, r * 2, c)
            sgrid.addWidget(v, r * 2 + 1, c)
            self._set_vals[name] = v
        root.addWidget(set_box)

    # --------------------------------------------------------------- Poll --

    def _poll(self) -> None:
        now_ms = int(time.monotonic() * 1000)
        pose = self._pose_reader.read()
        settings = self._settings_reader.read() or _DEFAULT_SETTINGS

        if pose is None:
            self._status_lbl.setText("● NO TRACKER")
            self._status_lbl.setStyleSheet("color:#e74c3c; font-weight:bold;")
            self._age_lbl.setText("")
        else:
            x, y, z, ts = pose
            if _is_stale(ts, now_ms):
                self._status_lbl.setText("● STALE")
                self._status_lbl.setStyleSheet("color:#f39c12; font-weight:bold;")
            else:
                self._status_lbl.setText("● TRACKING")
                self._status_lbl.setStyleSheet("color:#2ecc71; font-weight:bold;")
            age = (now_ms - ts) & 0xFFFF_FFFF
            self._age_lbl.setText(f"{age} ms")

            # Head pose labels
            self._hx.setText(f"{x:+.1f} cm")
            self._hy.setText(f"{y:+.1f} cm")
            self._hz.setText(f"{z:.1f} cm")
            self._hx.setStyleSheet("color:#e67e22;" if abs(x) > 15 else "color:#ecf0f1;")
            self._hy.setStyleSheet("color:#e67e22;" if abs(y) > 15 else "color:#ecf0f1;")
            if z < 40.0:
                self._hz.setStyleSheet("color:#e74c3c;")
                self._hz_warn.setText("⚠ expected 50–80 cm")
            else:
                self._hz.setStyleSheet("color:#ecf0f1;")
                self._hz_warn.setText("")

            # Parallax
            sw = settings.screen_w_cm if settings.screen_w_cm > 0 else 119.3
            sh = settings.screen_h_cm if settings.screen_h_cm > 0 else 33.6
            sx_pct, sy_pct = compute_shift_pct(
                x, y, z,
                settings.strength_x, settings.strength_y,
                settings.virtual_depth_cm, sw, sh,
            )
            f_val = settings.virtual_depth_cm / max(z + settings.virtual_depth_cm, 0.001)
            tag = _shift_tag(sx_pct, sy_pct)
            color = {"GOOD": "#2ecc71", "HIGH": "#f39c12", "DANGER": "#e74c3c"}[tag]
            self._vd.setText(f"{settings.virtual_depth_cm:.1f} cm")
            self._f.setText(f"{f_val:.3f}")
            self._sx.setText(f"{sx_pct:.2f}%")
            self._sy.setText(f"{sy_pct:.2f}%")
            self._sx.setStyleSheet(f"color:{color};")
            self._sy.setStyleSheet(f"color:{color};")

        # Settings panel
        sw = settings.screen_w_cm if settings.screen_w_cm > 0 else 119.3
        sh = settings.screen_h_cm if settings.screen_h_cm > 0 else 33.6
        self._set_vals["strengthX"].setText(f"{settings.strength_x:.2f}")
        self._set_vals["strengthY"].setText(f"{settings.strength_y:.2f}")
        self._set_vals["screenW cm"].setText(f"{sw:.1f}")
        self._set_vals["screenH cm"].setText(f"{sh:.1f}")
        self._set_vals["cameraFOV°"].setText(f"{settings.camera_fov_deg:.1f}")
        self._set_vals["ipd mm"].setText(f"{settings.ipd_mm:.1f}")
        self._set_vals["depthCurve"].setText(
            _DEPTH_CURVE_NAMES.get(settings.depth_curve, str(settings.depth_curve))
        )

    def closeEvent(self, event: object) -> None:
        self._timer.stop()
        self._pose_reader.close()
        self._settings_reader.close()
        super().closeEvent(event)  # type: ignore[arg-type]
```

- [ ] **Step 3: Append `main()` to `tracker/debug_monitor.py`**

```python
def main() -> None:
    app = QApplication.instance() or QApplication([])
    w = MonitorWidget()
    w.show()
    app.exec()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Verify existing tests still pass**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/test_debug_monitor.py -v
```

Expected: all 12 tests pass (the Qt imports are at module level but PySide6 is in the venv).

- [ ] **Step 5: Manual smoke test**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m tracker.debug_monitor
```

Expected: window opens showing "● NO TRACKER" in red. Close it.

- [ ] **Step 6: Commit**

```bash
git add tracker/debug_monitor.py
git commit -m "feat: add MonitorWidget PySide6 live debug window"
```

---

## Task 4: Enhanced fake_tracker — static + sweep modes

**Files:**
- Modify: `tests/fake_tracker.py`
- Create: `tests/test_fake_tracker_modes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_fake_tracker_modes.py`:

```python
import math
import sys
from pathlib import Path

# fake_tracker.py lives in tests/ alongside this file
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from fake_tracker import _compute_shift_pct, _shift_tag, _parse_kvs


def test_compute_shift_pct_zero_position():
    """(0, 0, 60) gives zero shift."""
    from tracker.shared_settings import OverlaySettings
    s = OverlaySettings(strength_x=1.0, strength_y=1.0,
                        virtual_depth_cm=30.0, screen_w_cm=119.3, screen_h_cm=33.6)
    sx, sy = _compute_shift_pct(0.0, 0.0, 60.0, s)
    assert sx == 0.0
    assert sy == 0.0


def test_compute_shift_pct_known_values():
    """headX=10, headZ=25, vd=30, sw=119.3, str=1.0 → ~4.575%."""
    from tracker.shared_settings import OverlaySettings
    s = OverlaySettings(strength_x=1.0, strength_y=1.0,
                        virtual_depth_cm=30.0, screen_w_cm=119.3, screen_h_cm=33.6)
    sx, _ = _compute_shift_pct(10.0, 0.0, 25.0, s)
    assert abs(sx - 4.575) < 0.01


def test_shift_tag_classification():
    assert _shift_tag(1.0, 1.0) == "GOOD"
    assert _shift_tag(3.0, 1.0) == "HIGH"
    assert _shift_tag(5.0, 5.0) == "DANGER"


def test_parse_kvs_defaults():
    result = _parse_kvs([], {"x": 0.0, "y": 0.0, "z": 60.0})
    assert result == {"x": 0.0, "y": 0.0, "z": 60.0}


def test_parse_kvs_override():
    result = _parse_kvs(["x=5.5", "z=80.0"], {"x": 0.0, "y": 0.0, "z": 60.0})
    assert result["x"] == 5.5
    assert result["z"] == 80.0
    assert result["y"] == 0.0


def test_static_rejects_zero_z():
    """z=0 must raise ValueError — zero depth is meaningless."""
    from fake_tracker import _static_mode
    with pytest.raises(ValueError, match="z must be > 0"):
        _static_mode(0.0, 0.0, 0.0)


def test_sweep_formula_at_quarter_period():
    """At t = period/4, x should equal amp (sin(π/2) = 1)."""
    amp, period = 10.0, 4.0
    t = period / 4  # quarter period
    x = amp * math.sin(2 * math.pi * t / period)
    assert abs(x - amp) < 0.001


def test_sweep_formula_at_zero():
    """At t = 0, x = 0 (sin(0) = 0)."""
    amp, period = 10.0, 4.0
    x = amp * math.sin(2 * math.pi * 0.0 / period)
    assert x == 0.0
```

- [ ] **Step 2: Run — expect FAIL (ImportError from fake_tracker)**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/test_fake_tracker_modes.py -v
```

Expected: `ImportError: cannot import name '_compute_shift_pct' from 'fake_tracker'`

- [ ] **Step 3: Rewrite `tests/fake_tracker.py` with all new modes**

Replace the entire file content:

```python
# tests/fake_tracker.py
"""Fake head tracker for testing the Glassless3D overlay without a webcam.

Modes
-----
(default)    Sine oscillation for N seconds (original behaviour).
--static     Hold fixed x/y/z forever.
--sweep      Sine sweep on X with constant Z.
--interactive  Keyboard control (Windows only).
"""
import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tracker.shared_memory import SharedMemoryWriter  # noqa: E402
from tracker.shared_settings import OverlaySettings, SharedSettingsReader  # noqa: E402

_DEFAULT_SETTINGS = OverlaySettings(
    strength_x=1.0,
    strength_y=1.0,
    virtual_depth_cm=30.0,
    screen_w_cm=119.3,
    screen_h_cm=33.6,
)


# ----------------------------------------------------------------- helpers --

def _compute_shift_pct(
    x: float, y: float, z: float, s: OverlaySettings,
) -> tuple[float, float]:
    """Return (shift_x_pct, shift_y_pct) using same formula as the HLSL shader."""
    denom = z + s.virtual_depth_cm
    f = s.virtual_depth_cm / max(denom, 0.001)
    sx = abs(x / max(s.screen_w_cm, 0.001)) * f * s.strength_x * 100.0
    sy = abs(y / max(s.screen_h_cm, 0.001)) * f * s.strength_y * 100.0
    return sx, sy


def _shift_tag(sx: float, sy: float) -> str:
    worst = max(sx, sy)
    if worst < 2.0:
        return "GOOD"
    if worst < 4.0:
        return "HIGH"
    return "DANGER"


def _read_settings() -> OverlaySettings:
    reader = SharedSettingsReader()
    s = reader.read()
    reader.close()
    return s or _DEFAULT_SETTINGS


def _parse_kvs(kvs: list[str], defaults: dict) -> dict:
    """Parse ['x=1.0', 'z=80'] into a dict, applying defaults for missing keys."""
    d = dict(defaults)
    for kv in kvs:
        k, v = kv.split("=", 1)
        d[k.strip()] = float(v)
    return d


def _print_status(x: float, y: float, z: float, settings: OverlaySettings) -> None:
    sx, sy = _compute_shift_pct(x, y, z, settings)
    tag = _shift_tag(sx, sy)
    print(
        f"fake_tracker: x={x:+.2f} y={y:+.2f} z={z:.2f}"
        f" → shiftX={sx:.2f}% shiftY={sy:.2f}% [{tag}]",
        flush=True,
    )


# ------------------------------------------------------------------- modes --

def _write_loop(gen: object) -> None:
    """Run the write loop. gen() returns (x, y, z) each tick; None to stop."""
    with SharedMemoryWriter() as w:
        frame = 0
        last_print = 0.0
        settings = _read_settings()
        try:
            while True:
                result = gen()
                if result is None:
                    break
                x, y, z = result
                w.write(x=x, y=y, z=z)
                now = time.monotonic()
                if now - last_print >= 0.5:
                    if frame % 60 == 0:
                        settings = _read_settings()
                    _print_status(x, y, z, settings)
                    last_print = now
                frame += 1
                time.sleep(1 / 120)
        except KeyboardInterrupt:
            pass


def _static_mode(x: float, y: float, z: float) -> None:
    if z <= 0:
        raise ValueError(f"z must be > 0, got {z}")
    print(f"fake_tracker [static]: x={x} y={y} z={z} — Ctrl+C to stop", flush=True)

    def gen() -> tuple[float, float, float]:
        return (x, y, z)

    _write_loop(gen)


def _sweep_mode(amp: float, period: float, z: float) -> None:
    print(
        f"fake_tracker [sweep]: amp={amp} period={period}s z={z} — Ctrl+C to stop",
        flush=True,
    )
    t0 = time.monotonic()

    def gen() -> tuple[float, float, float]:
        t = time.monotonic() - t0
        return (amp * math.sin(2 * math.pi * t / period), 0.0, z)

    _write_loop(gen)


def _interactive_mode() -> None:
    """Keyboard-driven mode (Windows only — uses msvcrt)."""
    import msvcrt

    x, y, z = 0.0, 0.0, 60.0
    print(
        "fake_tracker [interactive]: ←→=x  ↑↓=y  +/-=z  r=reset  q=quit",
        flush=True,
    )
    settings = _read_settings()

    with SharedMemoryWriter() as w:
        frame = 0
        try:
            while True:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):   # extended key prefix
                        ch2 = msvcrt.getwch()
                        if ch2 == "K":   x -= 1.0           # left arrow
                        elif ch2 == "M": x += 1.0           # right arrow
                        elif ch2 == "H": y += 1.0           # up arrow
                        elif ch2 == "P": y -= 1.0           # down arrow
                    elif ch in ("+", "="):
                        z = min(z + 5.0, 300.0)
                    elif ch == "-":
                        z = max(z - 5.0, 5.0)
                    elif ch == "r":
                        x, y, z = 0.0, 0.0, 60.0
                    elif ch in ("q", "Q", "\x03"):
                        break
                    sx, sy = _compute_shift_pct(x, y, z, settings)
                    tag = _shift_tag(sx, sy)
                    print(
                        f"  x={x:+.1f} y={y:+.1f} z={z:.1f}"
                        f" → {sx:.2f}% {sy:.2f}% [{tag}]",
                        flush=True,
                    )
                w.write(x=x, y=y, z=z)
                if frame % 60 == 0:
                    settings = _read_settings()
                frame += 1
                time.sleep(1 / 120)
        except KeyboardInterrupt:
            pass


# -------------------------------------------------- original sine oscillation --

def main(duration_sec: float = 10.0) -> None:
    """Original mode: sine oscillation for duration_sec seconds."""
    t0 = time.monotonic()
    with SharedMemoryWriter() as w:
        print(f"fake_tracker: writing to G3D for {duration_sec}s", flush=True)
        while (t := time.monotonic() - t0) < duration_sec:
            x = 5.0 * math.sin(t * 2.0)
            y = 2.0 * math.cos(t * 2.0)
            z = 60.0
            w.write(x=x, y=y, z=z)
            time.sleep(1 / 120)
        print("fake_tracker: done", flush=True)


# ---------------------------------------------------------------- entrypoint --

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Glassless3D fake head tracker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--static", nargs="+", metavar="KEY=VAL",
        help="Hold fixed values forever: x=0 y=0 z=60",
    )
    group.add_argument(
        "--sweep", nargs="+", metavar="KEY=VAL",
        help="Sine sweep on X: amp=10 period=4 z=60",
    )
    group.add_argument(
        "--interactive", action="store_true",
        help="Arrow keys control x/y, +/- control z, r=reset, q=quit",
    )
    parser.add_argument(
        "duration", nargs="?", type=float, default=10.0,
        help="Duration in seconds for default sine mode (default: 10)",
    )
    args = parser.parse_args()

    if args.static is not None:
        kv = _parse_kvs(args.static, {"x": 0.0, "y": 0.0, "z": 60.0})
        _static_mode(kv["x"], kv["y"], kv["z"])
    elif args.sweep is not None:
        kv = _parse_kvs(args.sweep, {"amp": 10.0, "period": 4.0, "z": 60.0})
        _sweep_mode(kv["amp"], kv["period"], kv["z"])
    elif args.interactive:
        _interactive_mode()
    else:
        main(args.duration)
```

- [ ] **Step 4: Run tests — expect all PASS**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/test_fake_tracker_modes.py -v
```

Expected:
```
PASSED tests/test_fake_tracker_modes.py::test_compute_shift_pct_zero_position
PASSED tests/test_fake_tracker_modes.py::test_compute_shift_pct_known_values
PASSED tests/test_fake_tracker_modes.py::test_shift_tag_classification
PASSED tests/test_fake_tracker_modes.py::test_parse_kvs_defaults
PASSED tests/test_fake_tracker_modes.py::test_parse_kvs_override
PASSED tests/test_fake_tracker_modes.py::test_static_rejects_zero_z
PASSED tests/test_fake_tracker_modes.py::test_sweep_formula_at_quarter_period
PASSED tests/test_fake_tracker_modes.py::test_sweep_formula_at_zero
```

- [ ] **Step 5: Run the full test suite to check for regressions**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/ -v -k "not test_face_tracker_init"
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Manual smoke test — static mode**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" tests/fake_tracker.py --static x=0 y=0 z=60
```

Expected output (repeating every 0.5 s):
```
fake_tracker [static]: x=0.0 y=0.0 z=60 — Ctrl+C to stop
fake_tracker: x=+0.00 y=+0.00 z=60.00 → shiftX=0.00% shiftY=0.00% [GOOD]
```

Press Ctrl+C to stop.

- [ ] **Step 7: Commit**

```bash
git add tests/fake_tracker.py tests/test_fake_tracker_modes.py
git commit -m "feat: add --static, --sweep, --interactive modes to fake_tracker"
```

---

## Task 5: Full integration smoke test

**Files:** none changed — this is a validation step.

- [ ] **Step 1: Run complete test suite**

```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m pytest tests/ -v -k "not test_face_tracker_init"
```

Expected: all tests pass.

- [ ] **Step 2: Run the two tools side-by-side (two terminals)**

Terminal 1 — static fake tracker:
```
"E:/Glassless 3d/.venv/Scripts/python.exe" tests/fake_tracker.py --static x=10 y=0 z=25
```

Terminal 2 — monitor:
```
"E:/Glassless 3d/.venv/Scripts/python.exe" -m tracker.debug_monitor
```

Expected in monitor: status turns `● TRACKING` (green), headZ shows `25.0 cm` in red with `⚠ expected 50–80 cm`, shiftX shows ~4.57% in red `[DANGER]`.

- [ ] **Step 3: Change z to 60 — verify monitor goes green**

Stop terminal 1 (Ctrl+C), restart with:
```
"E:/Glassless 3d/.venv/Scripts/python.exe" tests/fake_tracker.py --static x=0 y=0 z=60
```

Expected in monitor: headZ turns white (`60.0 cm`), shiftX = `0.00%` in green `[GOOD]`.

- [ ] **Step 4: Commit if any last-minute fixes were made**

```bash
git add -p  # stage only intentional changes
git commit -m "chore: debug toolkit integration verified"
```
