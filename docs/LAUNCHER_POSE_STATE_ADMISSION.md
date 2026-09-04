# Launcher pose/state admission

The launcher reads head position from the legacy `G3D` shared-memory mapping and face-presence state from the companion `G3D_State` mapping. Both timestamps use the same wrap-safe uint32 system-uptime clock.

Named mappings can outlive the tracker child that created them while another process still holds a handle. A newly launched child also creates the pose mapping before the state mapping and initially writes a neutral pose. Reading either value without session and timestamp admission can therefore expose an old pose as current, attach an old state label to a new pose, or report tracking during startup.

## Child-session boundary

`TrackerProcess` records launcher monotonic time immediately **before** `Popen`. A pose is admitted only when its shared timestamp:

- is a real uint32 integer;
- is strictly later than that launch boundary on the wrap-safe timeline;
- is no more than 800 ms old when polled; and
- is no more than 25 ms ahead of launcher time.

Strictly later is intentional. A retained pre-launch mapping can share the same millisecond as process launch; the current child will produce another frame, while accepting equality could expose the prior session.

A rejected pre-session mapping is treated like an absent mapping. Until a current-session pose has both usable state and permission to reach the launcher signals, the child receives the full 45-second model/camera initialization budget. Retained data and the child writer's unresolved initial neutral pose therefore cannot cause the shorter 2.5-second live-stream restart loop.

All comparisons use unsigned forward deltas and remain correct across the approximately 49.7-day uint32 millisecond rollover.

## Pose/state correlation

The tracker writes state immediately before pose for every publication. The launcher accepts the state for a pose only when the state timestamp:

- is strictly later than the current child launch boundary;
- is not ahead of the pose timestamp; and
- trails the pose by no more than 100 ms.

A state already written for the next frame is never attached early to the previous pose. A retained state from an earlier child is never attached to the new child's first pose.

When the mappings are coherent, the launcher order is:

```text
pose session/freshness admission
→ state timestamp correlation
→ status transition publication
→ pose timestamp commit
→ mark current session usable
→ position_updated
→ position_sampled
```

Status therefore remains established before a pose can feed live auto-tuning.

## Startup and compatibility behavior

Before any tracking/hold/paused status has been established, a current-session pose with missing or incoherent state is consumed but not emitted. This prevents the child writer's initial neutral pose from being retried and later relabeled as tracking. Consuming that timestamp does not end initialization; only a pose that is actually permitted to reach the launcher signals activates the shorter live-stream timeout.

Once a live status is established, a transient state/pose race preserves that prior status and allows the fresh pose through. This handles the short interval where state for frame N+1 can be visible while pose still represents frame N.

For an older tracker child that never publishes `G3D_State`, the launcher waits 500 ms after process launch and then permits a fresh pose with a `tracking` compatibility fallback. Current children normally correlate state immediately, so this grace path is not used.

## Timeout behavior

The launcher distinguishes startup from a stalled active stream:

- before the first usable pose is exposed: 45-second initialization timeout;
- after a usable pose is exposed: paused after 800 ms without a new pose;
- active stream restart after the configured 2.5-second stale interval.

Missing mappings, retained mappings, rejected timestamps, unresolved startup poses, and duplicate timestamps all pass through the same timeout boundary selected for the current lifecycle phase.

## Diagnostics

`TrackerProcess.poll_admission_snapshot()` exposes lifetime counts for accepted/rejected poses, malformed/pre-session/stale/future pose rejections, correlated and rejected state snapshots, missing/malformed/pre-session/stale/future state reasons, established-state preservation, legacy fallback, startup waiting, session resets, and the latest decision reasons.

The admission controller is platform-independent and explicitly included in the frozen package. The existing shared-memory names and binary layouts are unchanged.
