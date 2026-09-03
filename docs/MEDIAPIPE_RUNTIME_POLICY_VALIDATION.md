# Strict MediaPipe runtime-policy validation

The MediaPipe runtime policy controls asynchronous health, error escalation, backlog admission, result freshness, stale-result failover, and the observation window used for those decisions.

These values are integer counts or millisecond durations. General `int(...)` conversion is unsafe at this boundary because Python silently transforms some invalid configuration:

- `stall_timeout_ms: true` becomes `1`, causing failover after roughly one millisecond without callback progress;
- `max_backlog_ms: false` becomes `0`, disabling backlog throttling;
- `max_result_age_ms: 0.9` becomes `0`, disabling stale-result rejection;
- decimal values are truncated rather than rejected.

## Strict fields

The following nested settings and their legacy `async_*` equivalents now accept only a real integral value, excluding booleans, or a base-10 integer string:

- `stall_timeout_ms`;
- `max_consecutive_errors`;
- `max_backlog_ms`;
- `max_result_age_ms`;
- `max_consecutive_stale_results`;
- `stale_result_window_ms`.

Examples such as `5000`, `"5000"`, and NumPy/integral subclasses are valid. Values such as `true`, `5000.0`, `5000.5`, `"5000.0"`, and empty strings are invalid. Direct construction of `MediaPipeRuntimePolicy` requires actual integral objects; integer strings are a configuration-file compatibility feature handled by the parser.

One invalid field causes the complete optional policy block to fall back atomically to the known-safe defaults and produces one diagnostic message. Nested configuration remains authoritative when both nested and legacy keys are present.

## Existing zero semantics

Zero remains valid only where it is already a documented opt-out:

- `max_backlog_ms: 0` disables backlog throttling;
- `max_result_age_ms: 0` disables completed-result age rejection;
- `max_consecutive_stale_results: 0` keeps stale-result dropping but disables burst escalation.

`stall_timeout_ms`, `max_consecutive_errors`, and `stale_result_window_ms` must remain at least one.

## Input-size compatibility

`max_input_width_px` keeps its established separate compatibility contract. It accepts zero to preserve full-resolution input, or a value from 320 through 8192. Existing whole numeric values such as `960.0` and integer strings remain accepted; fractional and boolean values remain invalid.

The field now bounds the longest submitted image edge, as documented in [Bounded MediaPipe input resolution](MEDIAPIPE_INPUT_RESOLUTION.md).

## Nested and legacy examples

Preferred form:

```yaml
tracking:
  mediapipe_runtime:
    stall_timeout_ms: 5000
    max_consecutive_errors: 3
    max_backlog_ms: 150
    max_result_age_ms: 250
    max_consecutive_stale_results: 3
    stale_result_window_ms: 1000
    max_input_width_px: 960
```

Legacy top-level keys remain supported when the nested mapping is absent:

```yaml
tracking:
  async_stall_timeout_ms: "5000"
  async_max_consecutive_errors: "3"
  async_max_backlog_ms: "150"
  async_max_result_age_ms: "250"
  async_max_consecutive_stale_results: "3"
  async_stale_result_window_ms: "1000"
```
