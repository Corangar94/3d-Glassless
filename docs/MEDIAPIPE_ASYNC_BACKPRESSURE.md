# MediaPipe live-stream backpressure

Glassless3D uses MediaPipe Face Landmarker in `LIVE_STREAM` mode. The task can become temporarily slower than the webcam, especially during CPU contention, model warm-up, display changes, or a shadow recovery probe.

Submitting every camera frame in that state does not improve visible pose freshness. It can instead spend camera-thread time on BGR-to-RGB conversion and `mp.Image` allocation for inputs that the live-stream task is too far behind to use.

## Runtime behavior

The MediaPipe tracker maintains a bounded submission backlog:

- the first input is always admitted;
- normal callback progress keeps admitting new inputs;
- once unacknowledged inference work is 150 ms behind, new camera frames are skipped before color conversion or image allocation;
- the latest completed pose can still be delivered on a skipped frame;
- callback progress immediately reopens the gate;
- caught-up inference is admitted after a long camera pause;
- `async_max_backlog_ms=0` disables the gate for direct API users.

The default 150 ms budget permits a short burst so normal asynchronous scheduling is unaffected, while preventing a slow task from accumulating conversion/allocation work at full webcam cadence.

## Stale result rejection

A callback can prove that MediaPipe is alive yet still finish too late to be useful for head-coupled rendering. Returning that old pose as a new measurement would refresh the tracker's visible `tracking` state and can create delayed parallax.

The asynchronous delivery boundary therefore rejects completed poses older than 250 ms relative to the current camera frame:

- age uses the same wrap-safe uint32 Windows uptime clock as capture and rendering;
- a result exactly at the limit remains eligible;
- an older result is retired once rather than reconsidered on later frames;
- a timestamp that is slightly ahead of the caller is not misclassified as billions of milliseconds old;
- missing legacy timestamps remain deliverable;
- `async_max_result_age_ms=0` disables the gate for direct API users;
- synchronous `IMAGE` mode is unchanged.

One isolated late pose is dropped without declaring the task unhealthy. A stream that remains alive but unusably delayed is handled separately:

- three stale pose results observed within 1000 ms raise `AsyncInferenceFailure`;
- automatic mode switches to the OpenCV fallback on the frame that reaches the threshold;
- a fresh pose ends the stale burst immediately;
- a healthy callback with no face also ends the burst, so the absence of a viewer is not mistaken for persistent pipeline latency;
- stale results separated by more than the window begin a new episode;
- `async_max_consecutive_stale_results=0` keeps dropping stale poses without escalating.

A shadow MediaPipe recovery candidate is subject to the same rule. Persistent late candidate poses discard the candidate while the working OpenCV fallback remains visible.

MediaPipe callback timestamps are normalized to the project's nonzero wire-time contract, including the exact 49.7-day rollover instant.

## Stall safety

Throttling or dropping an isolated stale pose is not considered an inference error. However, neither behavior may hide a genuinely dead or permanently delayed MediaPipe task.

The callback watchdog distinguishes:

- **callback lag** — accepted submissions ahead of callback progress;
- **callback age** — current camera time since the last callback progress.

Callback age continues increasing while inputs are throttled. The existing five-second stall threshold therefore still raises `AsyncInferenceFailure`, allowing automatic in-process fallback to OpenCV. The stale-result burst gate covers the complementary case where callbacks continue advancing but their completed poses remain too old to display.

## Timestamp ownership

A throttled or rejected frame does not advance MediaPipe's private submitted timestamp timeline. Only a successful `detect_async` call commits the wire and expanded media timestamps. Camera-session reset continues to preserve that monotonic timeline while invalidating callbacks from the retired session.