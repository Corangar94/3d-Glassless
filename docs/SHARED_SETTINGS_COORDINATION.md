# Shared settings writer coordination

`G3D_Settings` is an 88-byte shared-memory channel used by the launcher, native overlay, packaged tracker, and optional writer utility. Its ABI places a uint32 version marker in the middle of the structure. Odd markers mean a publication is in progress; matching even markers mean a reader may accept the snapshot.

That seqlock protects readers from one writer, but it does not by itself serialize multiple independent writer processes. Two writers that each maintain a private counter can reuse versions or interleave body copies. A newly attached writer can also overwrite a surviving snapshot if its constructor unconditionally publishes defaults.

## Writer ownership

Every updated `SharedSettingsWriter` opens a named Windows mutex:

```text
<shared-memory-name>_WriteMutex
```

The mutex covers initialization, version selection, and the complete odd/body/even transaction. The in-process lock remains in place as well, so threads sharing one writer instance and processes using different writer instances are both serialized.

Writers do not trust their private counter as the next global version. While holding the mutex, each writer reads the current mapping marker, normalizes an abandoned odd marker to the following even base, and derives the next transaction from that shared value.

## Attach behavior

A writer joining an existing mapping follows these rules:

- an even nonzero version preserves the current snapshot;
- version zero with any nonzero payload is preserved as a legitimate uint32 rollover commit;
- a completely zero-filled mapping receives one complete default snapshot;
- an odd marker is treated as an abandoned transaction and replaced by one complete default commit.

Attaching a launcher or utility writer therefore does not create a temporary settings reset before its first intentional write.

## Publication transaction

A settings object is validated and packed before the mapping is touched. Publication then performs:

1. aligned four-byte store of the odd writing marker;
2. copy of the structure prefix before the version word;
3. copy of the structure suffix after the version word;
4. aligned four-byte store of the even committed marker.

The version word remains odd while both body slices are installed. The committed marker is the final store. Readers continue using the unchanged double-version check and do not need to open the writer mutex.

A writer waits at most one second for the process mutex. Timeout fails only that update, preventing a wedged or suspended writer from blocking the launcher indefinitely. Windows abandoned-mutex ownership is accepted so a surviving writer can repair a transaction after another process terminates.

## Rollover and crash recovery

The counter advances by two and wraps from `0xFFFFFFFE` through odd `0xFFFFFFFF` to committed version zero. Because zero is valid after rollover, initialization distinguishes it from a new mapping by inspecting the complete payload.

If a process terminates after publishing an odd marker, the next coordinated writer advances past that marker and writes a complete default snapshot. It never marks partially inherited data even.

## Compatibility with older writers

The mapping name, size, offsets, native ABI, and odd/even reader protocol are unchanged. An older writer can still publish to the channel, although it cannot participate in the new mutex. The live-filter consumer therefore also checks the scalar together with the version: if an older restarted writer reuses an even version with a changed `smoothing_alpha`, the changed value is admitted and counted rather than ignored.

## Reader recovery

When a Python settings reader encounters an invalid mapping access, it now unmaps the view and closes the associated handle before retrying later. This avoids retaining stale views and leaking handles through repeated writer restarts.

## Tested invariants

Focused tests cover:

- first-writer initialization;
- second-writer attach without reset;
- version-zero rollover preservation;
- abandoned odd-marker repair;
- two-writer shared version selection;
- concurrent unique even commits;
- mutex timeout, abandonment, and exception release;
- reader detach idempotence; and
- live tuning recovery when a legacy writer reuses a version with a changed scalar.
