# Sampled MediaPipe recovery

When `tracking.tracker_backend` is `auto`, Glassless3D normally uses MediaPipe and switches in-process to the temporal OpenCV tracker if MediaPipe becomes unhealthy.

After the configured retry delay, MediaPipe is started again as a **shadow candidate**. OpenCV remains the sole source of visible pose output until the candidate proves that it is healthy.

## Default packaged policy

```yaml
tracking:
  backend_failover:
    retry_primary_after_ms: 30000
    max_primary_retries: 1
    shadow_probe_interval_ms: 100
    shadow_probe_timeout_ms: 5000
    minimum_healthy_callbacks: 3
```

The default behavior is:

- keep OpenCV running at the camera cadence;
- submit at most one shadow MediaPipe frame every 100 ms (10 Hz);
- require three consecutive advancing callbacks with no submission/callback errors;
- require a usable MediaPipe pose before promotion;
- discard and close the candidate if it cannot satisfy those conditions within five seconds;
- preserve the existing pose-continuity blend when promotion succeeds.

A callback that contains no face still proves that the asynchronous task is alive, but it does **not** trigger promotion without a pose. This prevents a healthy-but-empty candidate from replacing an OpenCV tracker that still sees the viewer.

The probe interval uses the existing wrap-safe uint32 camera clock. Third-party controller candidates that do not expose asynchronous health telemetry retain historical every-frame probing for compatibility.

## Runtime diagnostics

The tracker publishes backend recovery state through the separate versioned `G3D_TrackerBackendV1` named shared-memory block. Existing pose (`G3D_PoseV2`) and face-state (`G3D_State`) layouts are unchanged.

Run the launcher’s **Run diagnostics** action, or use either command directly:

```powershell
python -m launcher.diagnostics
python -m launcher.diagnostics --format json
```

The report includes:

- configured and currently active tracker backends;
- whether the status sample is fresh;
- failover count and last MediaPipe failure;
- retry countdown and primary retry count;
- shadow candidate age, sampled-frame count, and healthy callback streak;
- backend transition generation and pose-continuity state.

Using OpenCV in `auto` mode is reported as a warning rather than a readiness failure because it is an intentional working fallback. A mismatch in an explicit strict `mediapipe` or `cv2` configuration is reported as a problem. Support bundles inherit the same structured JSON diagnostics automatically.
