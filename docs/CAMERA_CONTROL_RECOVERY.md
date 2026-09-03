# Automatic camera-control recovery

After a stable camera-quality warm-up, Glassless3D locks autofocus and automatic exposure when the webcam backend can complete a reversible transaction. The transactional lock path preserves the current manual value and rolls automatic mode back on if that value cannot be restored.

A later lighting change, moved camera, or focus disturbance can still make a previously safe manual setting unsuitable. The packaged tracker therefore monitors quality after locking and selectively restores only the automatic controller implicated by sustained degradation.

## Default and opt-out

The normal source and frozen tracker paths enable safe control stabilization by default:

```yaml
camera:
  lock_controls_after_warmup: true
```

Existing configuration files that omit the key receive the same packaged default. Set it to `false` to leave autofocus and automatic exposure untouched:

```yaml
camera:
  lock_controls_after_warmup: false
```

An invalid explicit value fails closed: Glassless3D logs the problem and leaves the automatic controls enabled. Direct library callers that do not supply a packaged configuration path retain the constructor behavior they selected explicitly.

## Recovery policy

```yaml
camera:
  control_recovery:
    degradation_hold_ms: 2000
    retry_interval_ms: 5000
    max_attempts_per_episode: 3
```

The default policy waits for two seconds of continuous evidence before changing camera hardware state. A failed recovery attempt waits five seconds before retrying, and each continuous degradation episode is bounded to three attempts. Values are validated atomically; invalid settings fall back to the safe defaults.

The control groups are independent:

- `underexposed`, `overexposed`, or `exposure is hunting` requests automatic exposure;
- `soft or motion-blurred` requests autofocus;
- low camera cadence does not alter exposure or focus because it is not a control-state problem.

A problem that clears resets its episode. Wrap-safe capture timestamps keep sustained-duration and retry timing correct across the Windows uptime rollover. A backwards timestamp restarts the observation window instead of being treated as a very long degradation.

## Hardware boundary

Recovery uses the same latest-frame camera wrapper and therefore shares its bounded, serialized `set()` path.

- Autofocus first tries the previously observed automatic value when it was not a manual-mode value, then the common automatic value `1`.
- Automatic exposure first tries the previously observed automatic value when appropriate, then the common values `0.75` and `1`.
- Missing properties, rejected writes, and driver exceptions are contained and recorded.
- Only a successfully restored group is marked unlocked.

The current lock result is retained by the runtime wrapper. A successful recovery clears that group’s `*_locked` state, restarts camera-quality warm-up, and rearms the existing bounded lock controller. Once the image is stable again, the normal transactional lock path may capture a new manual focus or exposure value.

If autofocus succeeds while automatic exposure fails, only autofocus is cleared; the exposure episode retains its own retry state. Recovery never restarts the camera merely because an optional control property is unsupported.

## Instance-local lock observation

The base tracking loop passes every warm-up lock result to its per-loop `CameraControlLockRetry` object. The recovery runtime wraps that object with a lightweight observer that records the same result after the retry state has accepted it.

This avoids replacing `tracker.main.try_lock_camera_controls` at module scope. Multiple tracking-loop instances, diagnostics, and focused tests can therefore run concurrently without borrowing or restoring one another’s camera-lock handler. The observer forwards retry state, reset calls, attempt counters, timing, and completion exactly to the original controller.

A malformed direct result is normalized to a bounded diagnostic dictionary before it reaches retry accounting. Observation remains tied to the capture owned by that loop instance, and camera replacement clears the associated lock state.

## Runtime layering

The normal source and frozen tracker entrypoints still select `tracker.pose_stability_runtime`. That bootstrap lazily selects `CameraControlRecoveryTrackingLoop`, which subclasses the active pose-stability and latest-frame runtime stack.

The recovery runtime reads the camera configuration once during construction. It enables the lock controller for an absent or true setting, disables it for an explicit false or invalid setting, and leaves direct no-config callers untouched. The lock itself still occurs only after the camera-quality admission window proves the image stable.

Direct callers of `TrackingLoop`, `LatestFrameTrackingLoop`, or `StableLatestFrameTrackingLoop` keep their existing behavior. The lock-policy and recovery modules are listed explicitly in the frozen-package hidden imports.

## Diagnostics

The runtime exposes:

- whether camera-control locking is active;
- the configured recovery policy;
- the most recent transactional camera-lock state;
- the most recent recovery result;
- per-control degradation start and retry timestamps;
- per-control attempt counts; and
- successful autofocus and automatic-exposure recovery counts.
