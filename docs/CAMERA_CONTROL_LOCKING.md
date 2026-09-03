# Transactional webcam control locking

After a stable camera-quality warm-up, Glassless3D disables autofocus and automatic exposure when the backend can complete a reversible transaction. This reduces focus pumping and brightness hunting without forcing unsupported cameras into manual mode.

Camera backends disagree on property support, accepted values, and whether failures return `False`, invalid numbers, or exceptions, so this optimization must fail without degrading the image.

## Default and opt-out

The packaged source and frozen tracker paths enable the stabilized-camera policy by default:

```yaml
camera:
  lock_controls_after_warmup: true
```

Existing configuration files that do not contain the key also receive the safe default. Set the value to `false` to keep autofocus and automatic exposure untouched.

Boolean YAML values, `0`/`1`, and common boolean strings are accepted. An invalid explicit value fails closed: automatic controls remain enabled and the tracker logs the configuration problem. A malformed configuration root or camera block preserves the caller’s existing setting instead of enabling hardware changes.

Direct library callers that instantiate `TrackingLoop`, `LatestFrameTrackingLoop`, or `StableLatestFrameTrackingLoop` without a packaged configuration path retain their existing constructor default.

## Admission before hardware changes

The tracker does not lock controls immediately after opening a camera. The camera-quality monitor first requires a stable evidence window with:

- enough delivered frames and image-analysis samples;
- no underexposure, overexposure, softness, cadence, or exposure-hunting problem;
- low brightness jitter; and
- sharpness above the normal minimum.

Only then does the bounded retry controller attempt the hardware transaction. Unsupported or incomplete control groups do not prevent face tracking.

## Safe transaction

Each automatic control is handled independently:

1. Read the current manual value first (`FOCUS` or `EXPOSURE`).
2. Read the current automatic-mode value when available.
3. If the manual value is unavailable or non-finite, leave automatic mode unchanged.
4. Disable the automatic mode using the backend-specific conventions.
5. Restore the exact manual value captured before the mode change.
6. If restoration fails, immediately try to re-enable the automatic mode.

Autofocus manual mode uses `0`. Automatic exposure tries `0.25` first for DirectShow-style backends and then `0` for backends using a boolean convention. Exposure rollback tries the previously reported automatic value followed by the common `0.75` and `1.0` automatic-mode values.

A control group is reported complete only when both the automatic-mode lock and manual-value preservation succeed. Older direct callers that provide only the historical `*_locked` result keys retain their previous retry semantics.

## Release restoration

When locking is enabled, each opened capture is wrapped by an idempotent release boundary. The wrapper retains that capture’s last successful lock result independently of the active camera-quality session.

Before a normal tracker shutdown or camera reconnect releases the driver, the wrapper selectively restores every automatic controller still marked locked. Restoration therefore still occurs when the base tracking loop has already cleared its quality and retry state in preparation for a reconnect.

- A focus controller restored earlier by quality recovery is not written again at release.
- A retired wrapper uses only its own lock snapshot and cannot overwrite a replacement camera’s state.
- Restoration runs before the underlying `release()` call and uses the latest-frame wrapper’s serialized, bounded control path.
- A rejected write or driver exception is logged but cannot prevent the underlying camera from being released.
- Multiple release calls restore and release at most once.
- The explicit `lock_controls_after_warmup: false` opt-out does not add the release wrapper.

This keeps the stabilization policy local to the tracker session instead of relying on every webcam driver to reset manual mode when its handle closes.

## Failure behavior

- Missing manual property IDs never cause the corresponding automatic mode to be disabled.
- Read failures and non-finite values perform no hardware writes for that group.
- Rejected or throwing property writes are contained and reported in the result dictionary.
- A failed restoration triggers a best-effort rollback and remains an incomplete lock attempt even when rollback succeeds.
- The existing bounded retry controller may try again after the configured interval, then continues tracking if the hardware cannot be locked safely.
- If a later lighting or focus change makes the stored manual value unsuitable, the sustained quality-recovery path selectively restores the affected automatic controller and starts a new warm-up.

This policy favors a functioning automatic camera over an uncertain manual state. Camera-control locking never determines whether face tracking itself can run.
