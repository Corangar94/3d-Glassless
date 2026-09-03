# Forward-backward validation for OpenCV face flow

The OpenCV fallback uses Shi-Tomasi features and pyramidal Lucas-Kanade optical flow to carry a cascade-detected face box between scheduled detector corrections. The cascade remains authoritative; flow is only a short-lived bridge.

A low forward Lucas-Kanade error is not sufficient evidence that a feature still represents the same image point. A point on an occlusion boundary, repeated texture, or background region can receive a plausible forward match while drifting away from the face. If enough such points move together, their median displacement can pull the tracked face box before the next cascade correction.

## Consistency gate

Each flow update now follows this order:

1. Track all seeded points from the previous grayscale frame to the current frame.
2. Apply the existing forward status, finite-value, and Lucas-Kanade error checks.
3. Track only those forward-valid points back from the current frame to the previous frame.
4. Require a valid reverse status, finite reverse point and error, and reverse Lucas-Kanade error within the existing limit.
5. Measure Euclidean distance between each returned point and its original location.
6. Keep the point only when that round-trip error is at most 1.5 pixels.
7. Apply the existing robust displacement inlier test, face-motion limit, scale-step limit, and image-bound clipping.

Malformed point/status/error arrays fail closed. A reverse backend exception is reported distinctly from a forward exception so the fallback diagnostics identify which stage failed.

## Quality and recovery

Accepted flow quality combines:

- the fraction of original points that survive every gate;
- the median forward Lucas-Kanade error; and
- the median accepted round-trip error.

A sub-threshold but imperfect reverse match therefore lowers quality before it becomes bad enough for outright rejection. The tracker exposes the latest accepted median round-trip error and a cumulative count of points rejected by forward-backward validation.

If fewer than the required points survive, flow returns no observation. The existing `FaceTracker` path then performs a cascade pass on that same frame; repeated detector misses retain their existing bounded retirement behavior. No stale flow update is published merely to avoid detection.

## Cost bound

The reverse pass runs only after the forward filters and only on their surviving points. The fallback seeds at most 40 points, and `FaceTracker` performs flow on its longest-edge-bounded tracking image. The extra work is therefore a small sparse pyramidal operation rather than another cascade or full-frame dense flow.

## Compatibility

`SparseFaceMotionTracker` keeps its existing constructor and injected-flow mechanism. The optional `maximum_forward_backward_error` parameter defaults to 1.5 pixels and must be finite and positive. Injected flow functions used for tests or integrations are called once in each direction for a successful candidate update and must return the normal OpenCV three-item `(points, status, error)` result in both directions.
