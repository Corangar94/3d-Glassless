# Latest-only camera acquisition

OpenCV `VideoCapture.read()` grabs, decodes, and returns the next frame. Glassless3D still requests `CAP_PROP_BUFFERSIZE=1`, but capture backends and drivers do not guarantee that every requested property is accepted by the device.

If face tracking or another camera-loop task takes longer than one camera interval, a synchronous `read()` loop can therefore consume queued older frames. The tracker may remain smooth while head-coupled parallax visibly lags behind the viewer.

## Runtime design

The normal source and frozen tracker entrypoints use `LatestFrameCapture`:

- one daemon worker exclusively calls the underlying camera `read()` method;
- every completed read receives a wrap-safe monotonic acquisition timestamp immediately;
- only the newest completed event is retained;
- a slow processing loop skips superseded frames instead of performing tracking work on them;
- the single consumer never receives the same generation twice;
- tracker backends and camera-quality cadence receive the worker acquisition timestamp rather than the later processing-loop timestamp;
- camera property reads and writes are serialized with frame reads and receive priority between frames;
- repeated failures and read timeouts still flow into the existing three-read camera-reopen and bounded reconnect policy.

The frame object is not copied by the adapter. OpenCV's Python camera read returns a new frame object for the completed read; retaining only the newest reference avoids another full image allocation and copy in the latency path.

## Configuration

```yaml
camera:
  latest_frame:
    enabled: true
    wait_timeout_ms: 1000
    failure_backoff_ms: 20
    shutdown_timeout_ms: 1000
```

`enabled` controls the packaged/source tracker entrypoint. Direct `tracker.main.TrackingLoop` callers retain the historical synchronous capture behavior unless they explicitly use `LatestFrameTrackingLoop` or `LatestFrameCapture`.

`wait_timeout_ms` bounds how long the processing loop waits for a generation newer than the one it last consumed. A timeout is returned as a failed camera read and participates in existing reconnect handling.

`failure_backoff_ms` prevents a disconnected or failing backend from spinning at full CPU while it reports failed reads.

`shutdown_timeout_ms` bounds worker shutdown. Release first asks the worker to stop, then releases the native device to unblock a backend read that did not return during the short grace interval.

Invalid values cause the entire latest-frame policy to fall back to safe defaults.

## Recovery and observability

Each camera opened by initial startup or backend rotation is wrapped independently. Releasing the wrapper releases the native capture once, and the next recovered camera receives a new worker and generation timeline.

`LatestFrameCapture.snapshot()` reports:

- captured and delivered frame counts;
- frames superseded before delivery;
- failed native reads and consumer timeouts;
- latest and delivered generation numbers;
- latest acquisition timestamps;
- worker/release state; and
- the last contained error.

The existing safe `VideoCapture` boundary remains underneath this adapter, so constructor, state-query, property, read, and release exceptions continue to become bounded recovery signals rather than terminating the tracker process.
