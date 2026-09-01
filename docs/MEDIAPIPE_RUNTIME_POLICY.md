# MediaPipe runtime policy

Glassless3D groups the asynchronous MediaPipe latency and health limits under one validated configuration block:

```yaml
tracking:
  mediapipe_runtime:
    stall_timeout_ms: 5000
    max_consecutive_errors: 3
    max_backlog_ms: 150
    max_result_age_ms: 250
    max_consecutive_stale_results: 3
    stale_result_window_ms: 1000
```

The entire block is parsed before a MediaPipe tracker is constructed. If any value is invalid, the complete MediaPipe block falls back to the documented defaults instead of combining partially accepted limits.

## Settings

`stall_timeout_ms` is the maximum time without asynchronous callback progress before the MediaPipe tracker is declared unhealthy and automatic mode changes to the OpenCV fallback.

`max_consecutive_errors` bounds repeated `detect_async` submission failures and callback-processing failures.

`max_backlog_ms` stops preparing and submitting new MediaPipe images when accepted work is too far ahead of callback progress. A value of `0` disables backlog throttling.

`max_result_age_ms` rejects completed poses that are too old for responsive head-coupled rendering. A value of `0` disables the completed-result age gate.

`max_consecutive_stale_results` escalates a sustained burst of obsolete completed poses to automatic backend failover. A value of `0` keeps stale-pose dropping but disables burst escalation.

`stale_result_window_ms` defines the observation window for that consecutive stale-result burst.

## Backend behavior

The validated values are applied to:

- explicitly selected `mediapipe` tracking;
- the preferred MediaPipe backend in `auto` mode; and
- every shadow MediaPipe recovery candidate created while OpenCV remains active.

They are not passed to an explicitly selected OpenCV tracker or the OpenCV fallback constructor.

Direct library callers that pass a plain `BackendFailoverPolicy` retain their existing tracker keyword behavior. The launcher and tracker entry point use the configured policy returned by `parse_backend_failover_policy`, which carries both backend-recovery and MediaPipe-runtime limits through the existing single configuration boundary.

## Legacy configuration

Valid legacy top-level keys such as `async_max_backlog_ms` and `async_max_result_age_ms` are still read when `tracking.mediapipe_runtime` is absent. When both forms exist, the nested block wins. New repository and first-run configurations write only the nested form.
