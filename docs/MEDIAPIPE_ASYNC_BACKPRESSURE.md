# MediaPipe live-stream backpressure

Glassless3D uses MediaPipe Face Landmarker in `LIVE_STREAM` mode. The task can become temporarily slower than the webcam, especially during CPU contention, model warm-up, display changes, or a shadow recovery probe.

Submitting every camera frame in that state does not improve visible pose freshness. It can instead spend camera-thread time on BGR-to-RGB conversion and `mp.Image` allocation for inputs that the live-stream task is too far behind to use.

## Runtime behavior

The MediaPipe tracker now maintains a bounded submission backlog:

- the first input is always admitted;
- normal callback progress keeps admitting new inputs;
- once unacknowledged inference work is 150 ms behind, new camera frames are skipped before color conversion or image allocation;
- the latest completed pose can still be delivered on a skipped frame;
- callback progress immediately reopens the gate;
- caught-up inference is admitted after a long camera pause;
- `async_max_backlog_ms=0` disables the gate for direct API users.

The default 150 ms budget permits a short burst so normal asynchronous scheduling is unaffected, while preventing a slow task from accumulating conversion/allocation work at full webcam cadence.

## Stall safety

Throttling is not considered an inference error. However, it must not hide a genuinely dead MediaPipe task.

The watchdog therefore distinguishes:

- **callback lag** — accepted submissions ahead of callback progress;
- **callback age** — current camera time since the last callback progress.

Callback age continues increasing while inputs are throttled. The existing five-second stall threshold therefore still raises `AsyncInferenceFailure`, allowing automatic in-process fallback to OpenCV.

## Timestamp ownership

A throttled or rejected frame does not advance MediaPipe's private submitted timestamp timeline. Only a successful `detect_async` call commits the wire and expanded media timestamps. Camera-session reset continues to preserve that monotonic timeline while invalidating callbacks from the retired session.
