# Launcher tracker status transitions

`TrackerProcess` polls the legacy pose shared-memory mapping every 50 ms so launcher position telemetry can update at up to 20 Hz. The tracking state itself changes much less frequently. Publishing the same `tracking` value with every fresh pose caused the UI to repeat status rendering, health bookkeeping, and backend-status reads even though no observable transition occurred.

## Transition-only publication

The subprocess wrapper now sends `status_changed` only when the requested status differs from the last successfully emitted status in the current launcher lifecycle.

A normal sequence therefore looks like:

```text
initializing → tracking → paused → tracking → restarting → initializing → tracking
```

Twenty fresh poses that all report `tracking` produce one status signal and twenty pose signals. Repeated stale polls produce one `paused` signal until either a fresh pose or a restart changes state.

The first status of a user-visible `start()` is never suppressed. A new start resets only the deduplication baseline; lifetime diagnostic counters remain available. Internal stale-process restart does not reset the baseline, so `restarting`, `initializing`, and the recovered state are preserved as real transitions.

## Status-before-pose ordering

For a fresh shared-memory timestamp, the wrapper still resolves and publishes state before emitting either pose signal:

```text
state lookup
→ status transition publication or duplicate suppression
→ commit fresh pose timestamp
→ position_updated
→ position_sampled
```

The fresh timestamp is committed only after the leading status operation succeeds. A transient signal failure can therefore retry that same pose on the next poll instead of silently classifying it as already consumed. When an identical status is suppressed, the previous successful signal already established the state required by the pose consumer.

This preserves the launcher auto-tuning invariant: a pose cannot be treated as active tracking before the corresponding tracking state has been established.

## Transaction and re-entrancy behavior

The status gate installs a provisional baseline before calling the Qt emitter. A direct signal callback that re-enters with the same status is suppressed instead of recursing. A genuinely different nested transition becomes the final baseline.

If emission raises before another transition replaces the provisional value, the prior baseline is restored. The next call can retry the transition. Invalid status objects are emitted fail-open for compatibility but never become a deduplication baseline.

Requests are serialized. Concurrent duplicate requests result in one signal, while transitions remain ordered by entry into the gate.

## Diagnostics

`TrackerProcess.status_emission_snapshot()` reports:

- emitted transitions;
- suppressed consecutive duplicates;
- intentional forced emissions;
- invalid fail-open emissions;
- failed signal emissions;
- lifecycle resets;
- the current status baseline; and
- the most recent decision reason.

The gate is included explicitly in the frozen package. Direct consumers of `status_changed`, `position_updated`, `position_sampled`, `stopped`, and the tracker lifecycle API require no changes.
