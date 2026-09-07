# Source-aware native depth freshness

The DirectML depth worker is sequential, but upload time is not the same as source-frame time. A result can complete long after the captured desktop frame that produced it. Treating the upload as a fresh depth update can restart texture blending and parallax over a screen image that has already moved on.

## Source identity

Every desktop frame submitted to depth inference receives:

- a nonzero, monotonically increasing source generation; and
- the steady-clock timestamp recorded when the immutable compact capture is retained, before any busy/rate-limit wait.

That identity travels with the frame through all five asynchronous states:

```text
staging ring → pending tensor → running inference → ready upload → published depth
```

The pixel/tensor buffers may continue to move or swap independently, but their source identity is transferred in the same synchronization boundary.

## Publication boundary

A completed result may update the current depth texture only when:

1. its source generation is nonzero;
2. its generation is newer, or it is the single permitted partial-to-complete upgrade of the same generation and original timestamp; and
3. its source is no more than 750 ms old, or every tile describes the exact capture still held on screen.

For changing scenes and partial atlases, the exact 750 ms boundary is accepted; an obsolete result at 751 ms is discarded without touching the accepted GPU textures or blend. A complete result for an unchanged held image is accepted even when late. This exemption requires matching capture generation **and timestamp**, plus actual per-tile coverage. It does not restamp pixels or exempt obsolete results.

Fast-mode batches retain per-tile source identities. A batch generation is not proof of full-image coverage. During continuous capture, fast mode selects the least-recently completed tile each pass so every region receives a bounded share of available inference capacity. After 200 ms without a new capture, polling schedules one all-tile completion pass using the retained compact pixels and original source metadata. Once accepted, that pass is not repeated. This also closes the dead end after a late partial result is rejected.

## Temporal-history isolation

Depth postprocessing maintains CPU-side history for motion-aligned EMA smoothing, tile reuse, global percentile range, and contrast stabilization. An inference has already touched those caches before the main thread makes the final source-age decision.

When a completion is rejected, the runtime clears that CPU-side temporal history before staging the next inference. The existing valid GPU depth textures and their current blend remain untouched. The next accepted inference therefore starts a fresh postprocessing episode rather than inheriting tile or normalization state from a map that was never publishable.

This reset is safe at the handoff boundary: `output_ready` is set only after the worker marks itself idle, and the main thread cannot queue the next tensor until after it drains and classifies the completion.

## Depth age semantics

`DepthInferencer::depth_age_ms()` reports the oldest inferred tile's source age. It returns `UINT32_MAX` until every tile has a real inferred source. `latest_depth_generation()` describes the newest batch; `complete_depth_generation()` is nonzero only when the entire atlas belongs to one capture. Only complete coverage matching the held capture enables the static-scene health exemption.

`depth_upload_age_ms()` retains the former diagnostic: elapsed time since the accepted depth texture upload. Keeping both values distinguishes an old source uploaded recently from a genuinely current depth map.

## Failure and visibility behavior

A stale or nonmonotonic completion is a controlled drop, not a device failure. The previous valid depth remains available while the next capture can be staged normally.

Visibility is now an explicit, pure predicate requiring a running capture session, a valid captured image, an accepted depth publication, and target foreground eligibility. It neither clears capture validity nor calls `run()`. The old `ShowWindow` macro interceptor is removed. Completing the first inference during an idle poll can reveal the overlay without another capture.

A pending worker failure is handled before any recovery success check. Retry backoff survives initialization and old publications from the failed session. Only a new session can prove recovery: two seconds of healthy depth plus repeated publication, or a fully covered held image. No-frame intervals alone are not failures.

Outstanding inference work is separately monitored for progress. The deadline is adaptive and bounded between 5 and 15 seconds; it is not armed when the pipeline is idle. If pending/running work exceeds that deadline, the inferencer reports failure, publishes the distinct `depth_timeout` capture reason, and requests ONNX Runtime termination so the existing capture/rebind recovery path can retire the session. DirectML fence waits are finite as well, preventing the worker from intentionally waiting forever at the copy boundary.

## Diagnostics

The inferencer reports:

- accepted depth publications;
- stale-source drops;
- duplicate or older-generation drops;
- invalid zero-generation drops; and
- the most recently published source generation.

The standalone freshness controller is header-only and has a native CTest target covering exact age boundaries, generation ordering, reset behavior, future-clock safety, disabled age rejection, and uint32 age saturation.
