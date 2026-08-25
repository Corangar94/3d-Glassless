# Glassless3D

[![Audit and dependency gates](https://github.com/Corangar94/3d-Glassless/actions/workflows/audit-regressions.yml/badge.svg?branch=master)](https://github.com/Corangar94/3d-Glassless/actions/workflows/audit-regressions.yml)
[![Native overlay build](https://github.com/Corangar94/3d-Glassless/actions/workflows/native-overlay-build.yml/badge.svg?branch=master)](https://github.com/Corangar94/3d-Glassless/actions/workflows/native-overlay-build.yml)

Glassless3D creates a **single-view, webcam-tracked virtual-window effect** on an ordinary Windows monitor. As the viewer moves, scene layers reproject using head position and monocular depth, producing motion parallax.

An ordinary monitor cannot deliver separate left/right images to the eyes, so this is not binocular stereoscopic 3D. The current target is convincing head-coupled 2.5D for one tracked viewer.

> **Project status:** the automated Windows tests, dependency audit, hash-pinned bootstrap, and native overlay build are green. Physical webcam/monitor acceptance is still tracked in [issue #2](https://github.com/Corangar94/3d-Glassless/issues/2).

## How it works

The default, non-injecting desktop backend:

1. captures a selected application window or display;
2. estimates scene depth with Depth Anything V2 through DirectML;
3. tracks the viewer with a webcam;
4. renders a depth-dependent inverse warp through D3D11; and
5. hides or rebinds safely when tracking, capture, depth, or the selected target becomes unavailable.

The overlay is not shown until the first real depth result has reached the GPU.

## Requirements

- 64-bit Windows with D3D11 and DirectML support
- Python 3.11 or 3.12
- a webcam
- 7-Zip installed in its normal Windows location or available as `7z` on `PATH`
- enough free disk space for the models, native dependencies, and pinned build toolchain

## Fresh install

Run the following in PowerShell:

```powershell
git clone https://github.com/Corangar94/3d-Glassless.git
cd 3d-Glassless

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .

python scripts/bootstrap.py
python -m launcher
```

The bootstrap command:

- downloads primary assets only over HTTPS;
- verifies repository-maintained SHA-256 values;
- safely extracts and tree-verifies ONNX Runtime, DirectML, and the native toolchain;
- builds `Glassless3DOverlay.exe`; and
- verifies the executable, runtime DLLs, face model, and depth model before reporting success.

A modified or incomplete cached dependency tree is discarded and rebuilt from its pinned input.

## First run

1. Select the intended webcam and confirm the screen dimensions.
2. Select a running target process, or use the desktop path.
3. Start tracking.
4. Move naturally and press `Ctrl+R` once the normal viewing position is comfortable.
5. Use the launcher’s runtime tiles or diagnostics command to confirm live tracking, capture, and depth flow.

## Controls

- `Ctrl+R`: recenter at the current viewing position
- `Ctrl+D`: cycle depth/confidence debug views
- `Ctrl+Shift+S`: save an overlay screenshot
- `Ctrl+Shift+G`: quit the native overlay

## Diagnostics and support

Software-only acceptance, replay regression, and virtual-window demo:

```powershell
python -m scripts.software_acceptance `
  --output-dir software_acceptance `
  --generate-demo `
  --fail-on-regression
```

Filter tuning against deterministic delayed/noisy/dropout traces:

```powershell
python -m scripts.replay_quality `
  --config config.yaml `
  --tune `
  --output-json replay_report.json `
  --output-markdown replay_report.md
```

Add `--write-config` to atomically apply the recommended filter values. These
software gates are reproducible and run in CI; they do not require a webcam,
monitor, or GPU.

Basic readiness report:

```powershell
python -m launcher.diagnostics --config config.yaml
```

Require a healthy live overlay and fresh face tracking:

```powershell
python -m launcher.diagnostics `
  --config config.yaml `
  --require-live-runtime `
  --require-face-tracking
```

Live tracking monitor and calibration benchmark:

```powershell
python -m tracker.debug_monitor
python -m tracker.calibration_bench --duration 10 --output tracking_bench.json
```

Collect a support bundle after reproducing a runtime problem:

```powershell
python -m scripts.collect_support `
  --config config.yaml `
  --output-dir support_bundle `
  --require-live-runtime
```

See [Troubleshooting](docs/TROUBLESHOOTING.md) and the [hardware acceptance checklist](docs/HARDWARE_ACCEPTANCE_CHECKLIST.md) for deeper guidance.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m compileall -q launcher tracker scripts tests
python -m pytest -q
```

Permanent CI gates run the complete Windows test suite, `pip check`, `pip-audit`, the hash-pinned bootstrap, and a clean native overlay build.

## Experimental ReShade path

The ReShade/addon path is not the default and must be prepared explicitly:

```powershell
python scripts/bootstrap.py --with-reshade
```

Treat injected modes as advanced/offline functionality. Online compatibility and anti-cheat policy are title-specific; use only where the game publisher permits it.

## Direction

1. Stabilize the head-coupled projection, calibration, diagnostics, and packaging.
2. Build a dedicated image/native-scene viewer using off-axis projection and precomputed depth.
3. Use real game depth through a supported ReShade path where appropriate.
4. Keep arbitrary desktop AI-depth conversion as an experimental fallback.

See [Head-coupled 3D direction](docs/HEAD_COUPLED_3D_DIRECTION.md), [architecture](docs/ARCHITECTURE.md), and the [roadmap](docs/ROADMAP.md).

## Release governance

A project license has not yet been selected, and `master` protection plus the first prerelease are tracked in [issue #3](https://github.com/Corangar94/3d-Glassless/issues/3). Do not assume redistribution terms until a license is committed.
