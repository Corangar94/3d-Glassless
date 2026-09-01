# Glassless3D

[![Audit and dependency gates](https://github.com/Corangar94/3d-Glassless/actions/workflows/audit-regressions.yml/badge.svg?branch=master)](https://github.com/Corangar94/3d-Glassless/actions/workflows/audit-regressions.yml)
[![Native overlay build](https://github.com/Corangar94/3d-Glassless/actions/workflows/native-overlay-build.yml/badge.svg?branch=master)](https://github.com/Corangar94/3d-Glassless/actions/workflows/native-overlay-build.yml)
[![Standalone Windows package](https://github.com/Corangar94/3d-Glassless/actions/workflows/windows-package.yml/badge.svg?branch=master)](https://github.com/Corangar94/3d-Glassless/actions/workflows/windows-package.yml)

Glassless3D creates a **single-view, webcam-tracked virtual-window effect** on an ordinary Windows monitor. As the viewer moves, scene layers reproject using head position and monocular depth, producing motion parallax.

An ordinary monitor cannot deliver separate left/right images to the eyes, so this is not binocular stereoscopic 3D. The current target is convincing head-coupled 2.5D for one tracked viewer.

> **Project status:** the complete Windows suite, dependency audit, hash-pinned bootstrap, native overlay build, deterministic delayed/noisy/dropout pose replay, synthetic virtual-window geometry gate, and standalone Windows packaging run in CI. Hardware observations are optional field-validation evidence rather than a release blocker.

## How it works

The default, non-injecting desktop backend:

1. captures a selected application window or display;
2. estimates scene depth with Depth Anything V2 through DirectML;
3. tracks the viewer with a webcam;
4. renders a depth-dependent inverse warp through D3D11; and
5. hides or rebinds safely when tracking, capture, depth, or the selected target becomes unavailable.

The overlay is not shown until the first real depth result has reached the GPU.

## Standalone Windows package

CI builds a one-folder Windows x64 package containing the launcher, tracker, native overlay, DirectML/ONNX Runtime libraries, and verified models. The standalone package does **not** require a separately installed Python interpreter.

Every candidate includes:

- a deterministic ZIP;
- per-file and archive SHA-256 values;
- an exact release manifest;
- a CycloneDX Python-environment SBOM;
- software-acceptance JSON/Markdown; and
- generated virtual-window validation frames.

The project has not selected a software license yet, so current CI packages are evaluation artifacts and contain `UNLICENSED_PREVIEW.txt`. Publication is blocked until reviewed `LICENSE` and `THIRD_PARTY_NOTICES.md` files are committed. See [Releasing Glassless3D](docs/RELEASING.md).

## Source-install requirements

- 64-bit Windows with D3D11 and DirectML support
- Python 3.11 or 3.12
- a webcam
- 7-Zip installed in its normal Windows location or available as `7z` on `PATH`
- enough free disk space for the models, native dependencies, and pinned build toolchain

## Fresh source install

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

## Automatic runtime recovery

The launcher supervises both the tracker and native overlay instead of restarting them in an unlimited tight loop:

- the first transient tracker or overlay failure retries immediately;
- repeated failures use exponential backoff, capped at 20 seconds by default;
- five failures inside the default 90-second rolling window open a 60-second cooldown circuit;
- the full runtime resumes automatically when the cooldown expires;
- **Recover runtime** or the primary **Retry runtime** action clears the circuit immediately;
- an explicit Stop cancels every queued restart by generation token;
- missing executables, incomplete runtime assets, invalid policy, and other nonrecoverable startup failures stop immediately rather than consuming the crash-loop budget;
- a stable 30-second run resets the failure episode.

The launcher-level policy is configurable in `config.yaml`:

```yaml
recovery:
  immediate_retries: 1
  base_delay_s: 1.0
  max_delay_s: 20.0
  max_failures: 5
  failure_window_s: 90.0
  cooldown_s: 60.0
  stable_reset_s: 30.0
```

### Tracker backend failover

The default `auto` tracker mode recovers inside the tracker process before the launcher needs to restart it:

- MediaPipe remains the preferred high-quality backend;
- an explicit MediaPipe async-health failure switches the current frame to the OpenCV fallback;
- ordinary implementation errors still surface instead of being hidden as failover events;
- after 30 seconds on OpenCV, one MediaPipe instance is sampled in shadow mode at 10 Hz;
- promotion requires three advancing, error-free callbacks and a usable current pose;
- a shadow candidate that cannot prove health within five seconds is closed without interrupting OpenCV output;
- after the bounded probe is exhausted, OpenCV remains active until the tracker process is intentionally restarted;
- explicit `mediapipe` and `cv2` selections remain strict and never switch automatically.

Backend transitions are also stabilized perceptually:

- a recent source pose is aligned to the replacement backend and the bounded offset decays over 450 ms;
- a source older than 750 ms is not carried forward, because the view was already stale or paused;
- the current filtered position is preserved while backend-specific velocity and covariance are cleared;
- the same transition treatment applies when OpenCV is promoted back to a healthy MediaPipe instance.

The backend-recovery and MediaPipe latency policies are configurable independently:

```yaml
tracking:
  tracker_backend: auto
  backend_failover:
    retry_primary_after_ms: 30000
    max_primary_retries: 1
    shadow_probe_interval_ms: 100
    shadow_probe_timeout_ms: 5000
    minimum_healthy_callbacks: 3
  mediapipe_runtime:
    stall_timeout_ms: 5000
    max_consecutive_errors: 3
    max_backlog_ms: 150
    max_result_age_ms: 250
    max_consecutive_stale_results: 3
    stale_result_window_ms: 1000
```

The MediaPipe block is validated atomically and is applied to strict MediaPipe, the automatic primary, and shadow recovery candidates. See [MediaPipe runtime policy](docs/MEDIAPIPE_RUNTIME_POLICY.md), [live-stream backpressure](docs/MEDIAPIPE_ASYNC_BACKPRESSURE.md), and [sampled recovery](docs/SHADOW_MEDIAPIPE_RECOVERY.md).

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

Add `--write-config` to atomically apply the recommended filter values. These software gates are reproducible and run in CI; they do not require a webcam, monitor, or GPU.

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

See [Troubleshooting](docs/TROUBLESHOOTING.md) for active runtime guidance. The [hardware acceptance checklist](docs/HARDWARE_ACCEPTANCE_CHECKLIST.md) remains available for optional device-specific field validation.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m compileall -q launcher tracker scripts tests
python -m pytest -q
python -m PyInstaller --clean --noconfirm Glassless3D.spec
```

Permanent CI gates run the complete Windows test suite, `pip check`, `pip-audit`, deterministic software acceptance with generated validation frames, the hash-pinned bootstrap, a clean native overlay build, frozen-entrypoint smoke tests, and deterministic standalone packaging.

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

A project license has not yet been selected, and default-branch protection plus the first prerelease are tracked in [issue #3](https://github.com/Corangar94/3d-Glassless/issues/3). The release workflow fails closed until the legal files are reviewed and committed. Do not assume redistribution terms from the public repository or CI artifacts.
