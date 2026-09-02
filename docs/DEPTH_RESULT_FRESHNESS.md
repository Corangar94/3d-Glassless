# Source-aware native depth freshness

The DirectML depth worker is sequential, but upload time is not the same as source-frame time. A result can complete long after the captured desktop frame that produced it. Treating the upload as a fresh depth update can restart texture blending and parallax over a screen image that has already moved on.

## Source identity

Every desktop frame staged for depth inference now receives:

- a nonzero, monotonically increasing source generation; and
- the steady-clock timestamp recorded when the captured texture enters the depth staging ring.

That identity travels with the frame through all five asynchronous states:

```text
staging ring → pending tensor → running inference → ready upload → published depth
```

The pixel/tensor buffers may continue to move or swap independently, but their source identity is transferred in the same synchronization boundary.

## Publication boundary

A completed result may update the current depth texture only when:

1. its source generation is nonzero;
2. its generation is newer than the last published generation; and
3. its source frame is no more than 750 ms old at the upload boundary.

The exact 750 ms boundary is accepted. A result at 751 ms is discarded without touching the current/previous depth textures, blend start time, or last valid source timestamp.

The 750 ms budget matches the native parallax-health point where depth contribution already reaches zero. A result too old to contribute therefore cannot masquerade as new merely because its GPU upload happens now.

## Temporal-history isolation

Depth postprocessing maintains CPU-side history for motion-aligned EMA smoothing, tile reuse, global percentile range, and contrast stabilization. An inference has already touched those caches before the main thread makes the final source-age decision.

When a completion is rejected, the runtime clears that CPU-side temporal history before staging the next inference. The existing valid GPU depth textures and their current blend remain untouched. The next accepted inference therefore starts a fresh postprocessing episode rather than inheriting tile or normalization state from a map that was never publishable.

This reset is safe at the handoff boundary: `output_ready` is set only after the worker marks itself idle, and the main thread cannot queue the next tensor until after it drains and classifies the completion.

## Depth age semantics

`DepthInferencer::depth_age_ms()` now reports the age of the captured desktop frame that produced the currently published depth. It returns `UINT32_MAX` until a source-aware result has actually been published.

`depth_upload_age_ms()` retains the former diagnostic: elapsed time since the accepted depth texture upload. Keeping both values distinguishes an old source uploaded recently from a genuinely current depth map.

## Failure and visibility behavior

A stale or nonmonotonic completion is a controlled drop, not a device failure. The previous valid depth remains available while the next capture can be staged normally.

The first-window visibility guard now requires at least one accepted depth publication rather than merely one completed inference. A stale first inference cannot reveal the initial flat 0.5 depth texture.

## Diagnostics

The inferencer reports:

- accepted depth publications;
- stale-source drops;
- duplicate or older-generation drops;
- invalid zero-generation drops; and
- the most recently published source generation.

The standalone freshness controller is header-only and has a native CTest target covering exact age boundaries, generation ordering, reset behavior, future-clock safety, disabled age rejection, and uint32 age saturation.
