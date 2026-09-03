# Strict latest-frame camera policy validation

The latest-only camera worker uses several millisecond settings to bound consumer waits, stale-frame delivery, frozen-frame detection, failure backoff, control-property access, and shutdown.

Those fields are configuration integers rather than arbitrary numbers. Converting them with Python's general `int(...)` constructor is unsafe because it silently changes some invalid values:

- `true` becomes `1`;
- `false` becomes `0`;
- `250.9` becomes `250`;
- `0.5` becomes `0`.

For this policy, those conversions can have functional consequences. A boolean wait value can create a 1 ms timeout, while a fractional value below one can become zero and disable stale-frame or freeze protection.

## Accepted forms

Every timing field now accepts only:

- a real integral value, excluding booleans; or
- a base-10 integer string such as `"250"`.

Decimal strings such as `"250.0"`, floating-point values, booleans, empty strings, collections, and other objects are rejected. An invalid field causes the complete optional `camera.latest_frame` block to fall back atomically to the safe default policy and produces one diagnostic message.

The `enabled` field keeps its established boolean parser and accepts YAML booleans, `0`/`1`, and common boolean strings.

## Bounds

| Field | Minimum | Maximum | Zero meaning |
|---|---:|---:|---|
| `wait_timeout_ms` | 1 | 60,000 | not allowed |
| `max_frame_age_ms` | 0 | 60,000 | stale-frame age gate disabled |
| `freeze_check_interval_ms` | 0 | 60,000 | freeze sampling disabled |
| `freeze_timeout_ms` | 0 | 60,000 | frozen-frame failure disabled |
| `failure_backoff_ms` | 0 | 10,000 | no failure backoff |
| `shutdown_timeout_ms` | 0 | 60,000 | immediate bounded teardown path |

Direct construction of `LatestFrameCapturePolicy` enforces the same real-integer and range contract. Valid repository and first-run defaults are unchanged.

## Examples

Valid:

```yaml
camera:
  latest_frame:
    enabled: true
    wait_timeout_ms: 1000
    max_frame_age_ms: 250
    freeze_check_interval_ms: 250
    freeze_timeout_ms: 3000
    failure_backoff_ms: 20
    shutdown_timeout_ms: 1000
```

Also valid for compatibility:

```yaml
camera:
  latest_frame:
    wait_timeout_ms: "1000"
```

Invalid and therefore replaced atomically by safe defaults:

```yaml
camera:
  latest_frame:
    max_frame_age_ms: 0.5
    freeze_timeout_ms: false
```
