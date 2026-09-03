# Instance-scoped camera lock observation

The base tracking loop currently calls `tracker.main.try_lock_camera_controls` directly after a stable camera-quality warm-up. The recovery runtime needs the returned focus/exposure state so it can later restore only a control that was actually locked.

A recovery loop previously replaced that module-global function with an instance closure for the full duration of `run()`. That was correct for the normal single-loop executable, but it was not re-entrant: a second embedded loop could capture the first loop's wrapper as its “original” helper, and calls from another thread could be recorded by the wrong instance.

## Transparent observer broker

The recovery runtime now installs one reference-counted dispatcher only while at least one recovery-enabled loop is active.

For each lock request, the dispatcher:

1. snapshots the original helper and active observers under a short lock;
2. calls the original hardware helper exactly once outside the broker lock;
3. returns the original object unchanged;
4. notifies each loop observer after a mapping result is available.

Each loop records a result only when the capture object is the camera it currently owns. Overlapping loops can therefore share the dispatcher without sharing lock state. A lock request from an unrelated embedded caller still executes the original helper exactly once and is not adopted by another loop.

Observer failures are contained because recovery telemetry must not convert a successful hardware lock into a tracking failure. Exceptions from the original hardware helper remain unchanged.

## Lifecycle and external ownership

Registration is a context manager around one loop's `run()` call. Nested and concurrent registrations share the same original helper. The final registration restores the original global function in `finally`, including when tracking raises.

The broker restores the slot only when its dispatcher still owns it. If an embedding application deliberately replaces the helper while recovery is active, cleanup does not overwrite that third-party mutation.

This is a compatibility bridge until the base `TrackingLoop` exposes a native instance method for camera-control locking. It removes instance cross-talk without changing direct callers or duplicating the main camera loop.
