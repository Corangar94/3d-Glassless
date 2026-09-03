# Strict tracker backend-failover validation

Automatic tracking starts with MediaPipe when available, degrades to OpenCV after an explicit asynchronous-health failure, and may probe a bounded MediaPipe recovery candidate while OpenCV remains the visible pose source.

The failover policy contains integer durations and counts. General `int(...)` conversion is unsafe because it silently changes invalid YAML values:

- `retry_primary_after_ms: false` becomes `0`, making the primary retry immediately due;
- `shadow_probe_interval_ms: false` becomes `0`, changing bounded probe cadence to every fallback frame;
- `minimum_healthy_callbacks: true` becomes `1`, weakening recovery admission;
- fractional values are truncated rather than rejected.

## Strict fields

Every value under `tracking.backend_failover` now accepts only a real integral object, excluding booleans, or a base-10 integer string:

- `retry_primary_after_ms`;
- `max_primary_retries`;
- `shadow_probe_interval_ms`;
- `shadow_probe_timeout_ms`;
- `minimum_healthy_callbacks`.

Configuration values such as `30000` and `"30000"` are valid. Values such as `true`, `30000.0`, `30000.5`, `"30000.0"`, empty strings, and collections are invalid. Direct construction of `ConfiguredBackendFailoverPolicy` requires actual integral objects; integer strings are accepted only by the configuration parser.

An explicit non-mapping `backend_failover` value is also invalid. Any invalid failover field replaces that complete group atomically with the safe defaults and produces one diagnostic message.

The MediaPipe runtime block is validated independently. An invalid failover group therefore does not discard a separately valid `tracking.mediapipe_runtime` policy, and an invalid MediaPipe group does not alter valid failover timings.

## Existing zero semantics

Zero remains valid where it already has a defined meaning:

- `retry_primary_after_ms: 0` makes the bounded retry immediately eligible after failover;
- `max_primary_retries: 0` disables primary recovery attempts;
- `shadow_probe_interval_ms: 0` probes a candidate on each fallback frame.

`shadow_probe_timeout_ms` and `minimum_healthy_callbacks` must remain at least one.

## Example

```yaml
tracking:
  tracker_backend: auto
  backend_failover:
    retry_primary_after_ms: 30000
    max_primary_retries: 1
    shadow_probe_interval_ms: 100
    shadow_probe_timeout_ms: 5000
    minimum_healthy_callbacks: 3
```

These strict configuration guarantees apply to `ConfiguredBackendFailoverPolicy`, the policy created by the normal launcher/tracker path. A plain `BackendFailoverPolicy` supplied directly by an embedding application retains its existing library-level constructor behavior.
