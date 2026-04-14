# Glassless3D GUI Launcher Design Spec

**Date:** 2026-04-14  
**Status:** Approved

---

## Goal

Replace the command-line tracker entry point with a self-contained Windows GUI launcher (`Glassless3D.exe`) that a non-technical user can double-click, run through a 5-step wizard, and be tracking within 60 seconds — no Python, no command line, no manual file copying.

---

## Architecture

### Threads

| Thread | Responsibility |
|--------|---------------|
| Main (Qt event loop) | All UI — wizard, main window, signals/slots |
| `TrackerThread` (QThread) | OpenCV capture → FaceTracker → HeadSmoother → FreetracWriter; emits `position_updated(x, y, z)` signal |

The `FreetracWriter.write()` call happens inside `TrackerThread` — it is a shared-memory write (microseconds), so no additional thread is needed.

### Module layout

```
launcher/
├── __init__.py
├── app.py              # QApplication entry point; detects first-run, shows wizard or main window
├── wizard.py           # QWizard — 5 pages
├── mainwindow.py       # QMainWindow — two-mode tracker window
├── tracker_thread.py   # QThread wrapping TrackingLoop; emits position_updated signal
├── edid.py             # WMI screen-size detection with manual fallback
└── reshade_install.py  # Copies bundled assets, writes ReShade.ini
```

**First-run detection:** absence of `config.yaml` in the app data directory (`%APPDATA%\Glassless3D\config.yaml`). After wizard completes it writes this file, so subsequent launches go straight to the main window.

---

## First-Run Setup Wizard (`wizard.py`)

A `QWizard` with 5 pages shown once on first launch.

### Page 1 — Welcome
- App logo, tagline, "Setup takes about 60 seconds"
- Single "Get Started →" button

### Page 2 — Game Directory
- Auto-detect WoW via registry key `HKLM\SOFTWARE\Blizzard Entertainment\World of Warcraft\InstallPath`
- Show detected path in a green "Found automatically" badge with the path
- "Browse for another game" opens a `QFileDialog` folder picker
- "Use This Folder →" advances; writes `game_dir` to wizard state

### Page 3 — Auto-Install (no user input)
- Progress checklist shown while install runs in a `QThread`:
  1. Copy `ReShade64.dll` → `{game_dir}\d3d11.dll`
  2. Copy `Glassless3D.fx` + `Glassless3D.fxh` → `{game_dir}\reshade-shaders\Shaders\`
  3. Write `ReShade.ini` depth settings (reverse depth, FAR_PLANE)
  4. Copy `Glassless3D.addon` → `{game_dir}\`
- Progress bar fills as each step completes
- Label: "No internet needed"
- On completion, auto-advances after 0.5 s

### Page 4 — Camera & Screen
- **Webcam:** `QComboBox` populated from `cv2.VideoCapture` probing indices 0–4; shows device names where available
- **Monitor size:** auto-filled from `edid.py` (WMI `Win32_DesktopMonitor.ScreenWidth` / `ScreenHeight`); displayed as two read-only fields in cm. A small "● auto-detected" badge; clicking either field makes it editable (fallback for EDID failure)
- Writes `camera.index`, `screen.width_cm`, `screen.height_cm` to wizard state
- "Looks Good →" advances

### Page 5 — Done
- Green checkmark, "Ready to go!"
- Instructions: "Launch WoW, press Home → enable Glassless3D"
- "▶ Start Tracking" button — writes `config.yaml` then opens main window and starts tracker
- Footnote: "Tracker starts automatically on next launch"

---

## Main Window (`mainwindow.py`)

Always-on-top (`Qt.WindowStaysOnTopHint`), frameless drag via `mousePressEvent`/`mouseMoveEvent`.

### Two modes, toggled by a chevron button in the title bar

**Expanded mode** (~270×310 px):
- Title bar: logo dot + "GLASSLESS 3D" + "▲ compact" toggle + close button
- Camera preview: 240×150 `QLabel` updated from `TrackerThread` via signal (JPEG-compressed frame, decoded in UI thread); face detection bounding box drawn with `QPainter`
- Status badge overlay: "● TRACKING" (green) / "● HOLD" (amber) / "● PAUSED" (grey) / "● STOPPED" (grey)
- XYZ readout: three `QLabel` tiles, monospace font, teal colour
- Start/Stop button

**Compact strip mode** (~400×100 px):
- Title bar: logo dot + "GLASSLESS 3D" + "▼ expand"
- Thumbnail camera preview (100×72)
- XYZ tiles
- Stop button

Mode is persisted to `config.yaml` under `gui.compact_mode`.

### Status states

| State | Camera border | Badge | Button |
|-------|--------------|-------|--------|
| Tracking | teal corners | ● TRACKING (green) | ■ Stop |
| Face lost, hold active | amber border | ● HOLD (amber) | ■ Stop |
| Face lost, hold expired | dim border | ● PAUSED (grey) | ■ Stop |
| Stopped | no border | ● STOPPED (grey) | ▶ Start |
| Camera error | red border | ✕ NO CAMERA (red) | ▶ Start |

### Camera preview

`TrackerThread` emits `frame_ready(bytes)` with a JPEG-encoded frame (quality 60) at camera FPS. The main window slot decodes and sets it on the `QLabel`. This keeps OpenCV work off the UI thread while keeping preview smooth.

---

## `edid.py` — Screen Size Detection

```python
def detect_screen_size_cm() -> tuple[float, float] | None:
    """Return (width_cm, height_cm) from WMI, or None on failure."""
```

Uses `wmi` package (`Win32_DesktopMonitor`). `ScreenWidth` / `ScreenHeight` are in mm (divide by 10). Returns `None` if WMI fails or returns 0 (common for some display drivers) — wizard then shows empty editable fields.

---

## `reshade_install.py` — Installation Logic

```python
def install(game_dir: str, profile_name: str = "wow") -> None:
    """Copy bundled assets into game_dir. Raises InstallError on failure."""
```

Bundled assets are located via `sys._MEIPASS` (PyInstaller) or `BASE_DIR` (dev). Wraps the existing `setup.py` logic; `install()` raises `InstallError(step, reason)` on failure so the wizard can show a clear error message per step.

---

## `tracker_thread.py`

```python
class TrackerThread(QThread):
    position_updated = Signal(float, float, float)  # x, y, z in cm
    frame_ready = Signal(bytes)                      # JPEG bytes
    status_changed = Signal(str)                     # "tracking"|"hold"|"paused"|"error"
```

Wraps `TrackingLoop.run()`. Overrides `run()` to call the loop. Stop is coordinated via a threading `Event` passed to a custom `TrackingLoop` subclass that checks it after each frame.

---

## Packaging (`Glassless3D.spec`)

PyInstaller one-file build. Bundled data:
- `ReShade64.dll` (MIT licence) — installed as `d3d11.dll` by wizard
- `shaders/Glassless3D.fx`, `shaders/Glassless3D.fxh`
- `Glassless3D.addon`
- MediaPipe model files (auto-collected by `mediapipe` hook)
- `profiles/wow.json`, `profiles/default.json`

Output: `dist/Glassless3D.exe`, ~90 MB. No Python runtime required on the user machine.

Build command:
```bash
pyinstaller Glassless3D.spec
```

---

## Dependencies Added

`requirements-gui.txt`:
```
PySide6>=6.6
wmi>=1.5
```

`requirements.txt` unchanged (tracker library remains standalone).

---

## Testing

| Module | Approach |
|--------|----------|
| `wizard.py` | `QTest.mouseClick` through each page with mocked `reshade_install.install` and `edid.detect_screen_size_cm` |
| `edid.py` | Mock `wmi.WMI()` return; test zero-value fallback returns `None` |
| `reshade_install.py` | `tmp_path` fixture; assert files copied and `ReShade.ini` written correctly |
| `tracker_thread.py` | `QSignalSpy` on `position_updated`; feed mock frames via patched `cv2.VideoCapture` |
| Main window modes | `QTest` toggle between compact/expanded; assert geometry changes |

Minimum coverage target: 80%.

---

## Out of Scope

- macOS / Linux support (Windows-only for now; WMI, named shared memory, ReShade)
- In-app effect strength slider (use ReShade overlay in-game)
- Auto-update mechanism
- Multi-monitor support (uses primary monitor EDID)
