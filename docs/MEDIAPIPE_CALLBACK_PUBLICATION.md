# Latest-only MediaPipe callback publication

MediaPipe `LIVE_STREAM` work is asynchronous. Glassless3D therefore treats callback completion order as an untrusted implementation detail and publishes results strictly by MediaPipe's expanded monotonic timestamp.

## The race

A callback can begin landmark and geometry conversion, release the tracker lock, and finish after a newer callback. Without a final ordering check, the older callback can overwrite a newer undelivered pose—or resurrect an older face pose after a newer no-face result.

The downstream monotonic pose-result gate cannot recover the newer pose once it has already been overwritten. It can only reject the older value, causing an avoidable hold or dropout.

## Publication boundary

`FaceTracker._on_result` now checks callback order twice under the same lock used for `_latest_pose`:

1. Before pose conversion, a callback that is already duplicate or older is discarded without landmark/geometry work.
2. After conversion, the timestamp is checked again and atomically claimed with publication. This closes the race where a newer callback finishes while the older callback is converting.

A successfully processed no-face callback also claims its timestamp. An older pose therefore cannot restore tracking after the newer callback has established that no face was present.

## Error and lifecycle behavior

- An obsolete conversion error that finishes after a newer successful callback is ignored for async-health accounting; it cannot reset healthy progress or trigger fallback.
- A conversion error for the newest callback still reaches the existing watchdog.
- Session reset keeps callback publication ordering because the MediaPipe task and its expanded timestamp timeline live for the full tracker lifetime. The existing submitted-timestamp floor separately rejects callbacks from the retired camera session.
- Closing the tracker rejects every later callback before conversion.
- Duplicate and out-of-order callback drops are counted separately, including whether they were rejected before or after conversion.

## Relationship to other gates

The callback publication gate protects MediaPipe's internal latest-result slot. The generic `PoseResultTimelineGate` remains the next boundary and rejects duplicate, malformed, or backward results from any tracker backend before the camera loop can refresh face presence, filtering, or publication. The stale-result freshness gate separately rejects timestamp-advancing poses that are too old for display.
