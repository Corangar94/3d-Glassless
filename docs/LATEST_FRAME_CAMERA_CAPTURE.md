# Latest-only camera acquisition

OpenCV `VideoCapture.read()` combines grabbing and retrieving the next frame. Glassless3D still requests `CAP_PROP_BUFFERSIZE=1`, but a successful OpenCV property call does not guarantee that the backend or device accepted the requested value.

If face tracking or another camera-loop task takes longer than one camera interval, a synchronous `read()` loop can therefore consume queued older frames. The tracker may remain smooth while head-coupled parallax visibly lags behind the viewer.

## Runtime design

The normal source and frozen tracker entrypoints use `LatestFrameCapture`:

- one daemon worker exclusively calls the underlying camera `read()` method;
- every completed read receives a wrap-safe monotonic acquisition timestamp immediately;
- only the newest completed event is retained;
- a slow processing loop skips superseded frames instead of performing tracking work on them;
- a successful frame that has already waited too long is retired before camera-quality analysis or tracker inference;
- the consumer continues waiting for a newer generation within its original read deadline;
- failure events remain deliverable so camera reopen and reconnect accounting is unchanged;
- an unexpectedly terminated worker wakes consumers and causes immediate failed reads instead of repeated full-timeout waits;
- the single consumer never receives the same generation twice;
- tracker backends and camera-quality analysis receive the selected frame's acquisition timestamp rather than the later processing-loop timestamp;
- camera property reads and writes are serialized with frame reads and receive priority between frames;
- a control call blocked behind a stuck native read returns a safe default after the configured wait timeout;
- repeated failures and read timeouts still flow into the existing three-read camera-reopen and bounded reconnect policy.

The adapter retains the frame object returned by the capture backend without adding another full-frame copy. When a newer event replaces it, only the worker's reference to the superseded object is discarded; a frame already returned to the consumer remains independently referenced there.

## Configuration

```yaml
camera:
  latest_frame:
    enabled: true
    wait_timeout_ms: 1000
    max_frame_age_ms: 250
    failure_backoff_ms: 20
    shutdown_timeout_ms: 1000
```

`enabled` controls the packaged/source tracker entrypoint. Direct `tracker.main.TrackingLoop` callers retain the historical synchronous capture behavior unless they explicitly use `LatestFrameTrackingLoop` or `LatestFrameCapture`.

`wait_timeout_ms` bounds both consumer waits for a newer generation and camera-control waits behind an in-progress native read. A frame timeout is returned as a failed camera read and participates in existing reconnect handling. Valid values are 1–60,000 ms.

`max_frame_age_ms` limits time spent in the latest-frame slot after the native read completed. A successful event at the exact limit remains eligible; an older event is retired once and the consumer keeps waiting. Set it to `0` to disable this early gate. Valid values are 0–60,000 ms.

This age is measured with a process-local steady clock, independently of the wrapping wire timestamp sent to tracker backends. A backwards, non-finite, or unavailable steady-clock observation is treated as unknown rather than falsely stale. The gate does not estimate sensor exposure time; it bounds delay after OpenCV returns the completed frame.

`failure_backoff_ms` prevents a disconnected or failing backend from spinning at full CPU while it reports failed reads. Valid values are 0–10,000 ms.

`shutdown_timeout_ms` bounds worker shutdown. Release first asks the worker to stop, then releases the native device to unblock a backend read that did not return during the short grace interval. Valid values are 0–60,000 ms.

Invalid values cause the entire latest-frame policy to fall back to safe defaults. If worker creation itself fails, tracking logs the failure and continues with the existing synchronous camera path.

## Worker failure boundary

Ordinary camera-driver exceptions remain per-read failure events and keep the worker alive. A worker-level termination outside that normal boundary—for example a fatal exception in the worker infrastructure—is recorded once, wakes every waiting consumer, and makes subsequent reads fail immediately. This allows the existing three-read reopen policy to replace the camera without adding up to three configured read timeouts first.

A frame already published before the fatal termination remains eligible and is delivered before the worker-failure signal. Normal stop/release sets the stop event first and is never recorded as an unexpected worker failure.

## Recovery and observability

Each camera opened by initial startup or backend rotation is wrapped independently. Releasing the wrapper releases the native capture once, and the next recovered camera receives a new worker and generation timeline. A final snapshot from the retired wrapper remains available for diagnostics.

`LatestFrameCapture.snapshot()` reports:

- captured and delivered frame counts;
- frames superseded before delivery;
- successful frames retired by the age gate and the most recent stale age;
- failed native reads and consumer timeouts;
- latest and delivered generation numbers;
- latest and last-delivered acquisition timestamps;
- worker liveness, unexpected-failure state, and failure episode count;
- wrapper/release state; and
- the last contained error.

The existing safe `VideoCapture` boundary remains underneath this adapter, so constructor, state-query, property, read, and release exceptions continue to become bounded recovery signals rather than terminating the tracker process.
