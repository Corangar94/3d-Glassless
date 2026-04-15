# Glassless3D Debug Toolkit — Design Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Two focused debug tools — a live read-only SHM monitor and an enhanced fake_tracker — that together let us diagnose and reproduce the "watery / tilts strangely" overlay bug without touching C++.

**Architecture:** Both tools are pure Python. `debug_monitor.py` reads G3D + G3D_Settings shared memory and surfaces the values + derived parallax math in a PySide6 window. `fake_tracker.py` gains three CLI modes (static, sweep, interactive) that write controlled values to G3D exactly as the real tracker does, and print computed UV shift to the terminal.

**Tech Stack:** Python 3.11, PySide6 (already in project), `ctypes`/`struct` (already used in `tracker/shared_memory.py`), `msvcrt` (Windows single-key input for interactive mode).

---

## Diagnostic Workflow (why these tools)

1. Run `python tests/fake_tracker.py --static x=0 y=0 z=60`
2. If the overlay looks correct → the real tracker is producing bad values (likely headZ ~25 cm instead of ~60 cm). Fix: camera FOV calibration / IPD tuning.
3. If the overlay is still watery/tilted at static (0,0,60) → the bug is in the depth model or the HLSL shader, not the tracker.
4. `debug_monitor.py` tells us which case we're in while the real tracker is running, without needing to read `overlay.log`.

---

## File Map

| Action | Path |
|--------|------|
| Create | `tracker/debug_monitor.py` |
| Modify | `tests/fake_tracker.py` |
| Create | `tests/test_debug_monitor.py` |
| Create | `tests/test_fake_tracker_modes.py` |

---

## Tool 1 — `tracker/debug_monitor.py`

### Behaviour

- Opens a `SharedMemoryReader("G3D")` and `SharedSettingsReader("G3D_Settings")` on startup. Both may be absent; the monitor retries silently.
- Polls both segments at 60 Hz via a `QTimer`.
- Displays the values described below; updates in-place (no flicker).
- Never writes to any shared memory segment.
- Exits cleanly when the window is closed.

### SharedMemoryReader (new class in `tracker/shared_memory.py`)

```python
class SharedMemoryReader:
    """Read-only view of a Windows Named Shared Memory segment."""
    def __init__(self, name: str = "G3D") -> None: ...
    def read(self) -> tuple[float, float, float, int] | None:
        """Return (x, y, z, timestamp_ms) or None if segment absent."""
    def close(self) -> None: ...
    def __enter__(self) -> "SharedMemoryReader": ...
    def __exit__(self, *_: object) -> None: ...
```

Uses `OpenFileMappingW` (not `CreateFileMappingW`) so it never creates the segment.

### UI Layout (single `QWidget`, no menus)

**Status row**
- Label: `● TRACKING` (green) / `● STALE` (orange, timestamp not updated in >500 ms) / `● NO TRACKER` (red, segment absent). Shows SHM age in ms next to it.

**Raw Head Pose panel** (read from G3D)
- Three large numeric labels: `headX`, `headY`, `headZ` in cm, one decimal place.
- `headZ` label turns red and shows `⚠ expected 50–80 cm` when z < 40.0.
- `headX` and `headY` turn orange when |value| > 15 cm (large offset at rest = EMA not yet calibrated).

**Calculated Parallax panel** (derived from G3D + G3D_Settings)

Values computed:
```
f          = virtual_depth_cm / (headZ + virtual_depth_cm)   # depth factor
shift_x_pct = abs(headX / screen_w_cm) * f * strength_x * 100
shift_y_pct = abs(headY / screen_h_cm) * f * strength_y * 100
```

Displayed: `virtualDepth`, `f`, `shift_x_pct`, `shift_y_pct`.

A horizontal gauge bar (0–10%) colour-coded:
- Green: < 2% — subtle, correct range
- Yellow: 2–4% — noticeable but acceptable
- Red: > 4% — too large, will look like sliding screen

If G3D_Settings is absent, substitute defaults: `virtual_depth_cm=30`, `strength_x=1.0`, `strength_y=1.0`, `screen_w_cm=119.3`, `screen_h_cm=33.6`.

**Settings panel** (read from G3D_Settings, or defaults if absent)
- Small read-only grid: `strengthX`, `strengthY`, `screenW cm`, `screenH cm`, `cameraFOV°`, `ipd mm`, `depthCurve`.

### Entry point

```python
# tracker/debug_monitor.py
if __name__ == "__main__":
    ...

# Also runnable as:
# python -m tracker.debug_monitor
```

---

## Tool 2 — enhanced `tests/fake_tracker.py`

### New CLI interface

```
python tests/fake_tracker.py                              # existing: sine 10 s
python tests/fake_tracker.py --static x=0 y=0 z=60       # hold values forever (Ctrl+C to stop)
python tests/fake_tracker.py --sweep amp=10 period=4 z=60 # X sine ±amp cm, constant Z
python tests/fake_tracker.py --interactive                 # keyboard control
```

All modes share a common write loop at 120 Hz.

### Shift % printout (all modes)

Every 0.5 s, each mode prints one line to stdout:
```
fake_tracker: x=+0.00 y=+0.00 z=60.00 → shiftX=0.00% shiftY=0.00% [GOOD]
```
Reads G3D_Settings to compute shift using the same formula as the monitor. If G3D_Settings is absent, uses the same defaults. Appends `[GOOD]` / `[HIGH]` / `[DANGER]` based on the same thresholds (<2%, 2–4%, >4%).

### `--static x=F y=F z=F`

- Parses `x=<float>`, `y=<float>`, `z=<float>` from the remaining args.
- Writes those fixed values at 120 Hz until `Ctrl+C`.
- `z` must be > 0; raises `ValueError` otherwise.

### `--sweep amp=F period=F z=F`

- `amp`: half-amplitude in cm for X axis (default 10.0).
- `period`: full cycle duration in seconds (default 4.0).
- `z`: constant Z distance in cm (default 60.0).
- Y is held at 0.
- Formula: `x = amp * sin(2π * t / period)`, `y = 0`, `z = z`.
- Runs until `Ctrl+C`.

### `--interactive`

- Uses `msvcrt.kbhit()` + `msvcrt.getwch()` (Windows) for non-blocking single-key input: each 120 Hz loop tick calls `kbhit()` first; only calls `getwch()` if a key is waiting, so the write loop never blocks.
- Initial state: `x=0, y=0, z=60`.
- Key bindings:
  - `←` / `→` arrow: x ± 1 cm
  - `↑` / `↓` arrow: y ± 1 cm
  - `+` / `-`: z ± 5 cm (clamped to 5–300 cm)
  - `r`: reset to `x=0, y=0, z=60`
  - `q` or `Ctrl+C`: quit
- Prints current state + shift % after every key press.
- Runs at 120 Hz write rate regardless of key press rate.

---

## Tests

### `tests/test_fake_tracker_modes.py`

```python
def test_static_writes_given_values():
    """--static should write exactly the given x/y/z for at least 5 frames."""

def test_sweep_oscillates_x():
    """--sweep x should cross zero and reach near amp within one period."""

def test_static_rejects_zero_z():
    """z=0 in --static mode must raise ValueError."""

def test_shift_pct_computation():
    """shift_pct computed from known values matches expected formula output."""
```

### `tests/test_debug_monitor.py`

```python
def test_shared_memory_reader_returns_none_when_absent():
    """Reader returns None if the segment has not been created."""

def test_shared_memory_reader_reads_written_values():
    """After SharedMemoryWriter writes (x,y,z), reader returns same values."""

def test_shift_pct_formula():
    """shift_x_pct = abs(headX / sw) * f * sx * 100 with known inputs."""

def test_status_stale_when_timestamp_old():
    """Status is STALE when last timestamp is more than 500 ms ago."""
```

---

## Out of Scope

- Writing to SHM from `debug_monitor.py` (pure read only)
- Plotting time-series history graphs (read `overlay.log` for history)
- C++ debug HUD in the overlay window
- macOS / Linux support (`msvcrt` is Windows-only; interactive mode documents this)
