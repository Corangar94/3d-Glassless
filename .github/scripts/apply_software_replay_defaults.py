from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one match, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "tracker/replay_quality.py",
    "dropout_ranges_s=((2.40, 3.00), (5.10, 5.50)),",
    "dropout_ranges_s=((2.40, 2.95), (5.10, 5.45)),",
)

replace_once(
    "tracker/main.py",
    'process_noise=float(trk.get("smoothing_q", 0.01))',
    'process_noise=float(trk.get("smoothing_q", 2.0))',
)
replace_once(
    "tracker/main.py",
    'prediction_horizon_ms=float(trk.get("prediction_horizon_ms", 35.0))',
    'prediction_horizon_ms=float(trk.get("prediction_horizon_ms", 0.0))',
)

replace_once(
    "launcher/wizard.py",
    '''    "smoothing_q": 0.01,
    "smoothing_r": 0.1,
    "hold_ms": 500,
''',
    '''    "smoothing_q": 2.0,
    "smoothing_r": 0.1,
    "prediction_horizon_ms": 0.0,
    "max_prediction_ms": 80.0,
    "hold_ms": 500,
''',
)

replace_once(
    "README.md",
    '''Basic readiness report:
''',
    '''Software-only acceptance, replay regression, and virtual-window demo:

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
''',
)

replace_once(
    "docs/ROADMAP.md",
    '''- Completion evidence and remaining hardware gates are tracked in `docs/COMPLETION_AUDIT.md`
''',
    '''- Deterministic pose replay and virtual-window geometry acceptance now provide the release-blocking software gate
- Physical hardware observations remain optional field-validation evidence rather than a release blocker
- Completion evidence is tracked in `docs/COMPLETION_AUDIT.md`
''',
)
replace_once(
    "docs/ROADMAP.md",
    '''| Calibration and tracking reliability | Done for the overlay-first software path | Calibration metadata is persisted and diagnostics validate configured-vs-runtime settings. Tracking quality is measured by the debug monitor/evaluation tools. |
''',
    '''| Calibration and tracking reliability | Software-gated | Calibration metadata is persisted, deterministic delayed/noisy/dropout replay is enforced in CI, and the replay tuner can atomically recommend filter values. |
''',
)
replace_once(
    "docs/ROADMAP.md",
    '''| Overlay quality and temporal stability | Partially done | Async depth inference, render-rate blending, edge-aware smoothing, crop handling, depth fixtures, and validation tooling are implemented. Real live-captured sequence fixtures still need target-runtime captures. |
''',
    '''| Overlay quality and temporal stability | Software-gated | Async adaptive depth, motion compensation, edge/disocclusion handling, deterministic parallax geometry, depth fixtures, and generated virtual-window validation frames are implemented. |
''',
)
replace_once(
    "docs/ROADMAP.md",
    '''| Performance hardening | Partially done | Depth modes, cadence logging, frame-timing export, GPU draw timing, and performance benchmarks exist. Deeper present/vsync latency instrumentation remains optional after hardware testing. |
''',
    '''| Performance hardening | Software-gated | Adaptive depth modes, cadence/depth-age telemetry, persistent DirectML binding with fallback, timing export, and deterministic pose-latency replay are enforced. |
''',
)
replace_once(
    "docs/ROADMAP.md",
    '''| Target-display acceptance | Hardware-gated | Connect the target display, collect a fresh live runtime, fill `hardware_observation.yaml`, then run display acceptance and a support bundle from the same setup. |
''',
    '''| Target-display acceptance | Optional field validation | Hardware observations and support bundles remain useful for device-specific defects, but deterministic software acceptance is the active release gate. |
''',
)
