# Audit 2 remediation - source changes, validation deferred

Base: `a170de9a4fa55dd38512516841091c9b2ba42280` on
`fix/packaged-runtime-self-test`. Remediation branch: `fix/audit-2-remediation`.

The user requested fixes with validation deferred. No pytest, CTest, compiler,
PyInstaller, live camera/display probe, dependency installation, CI dispatch,
merge or release was performed for this change. Regression cases were authored
but not executed. Earlier green checks do not cover this branch.

## Finding-to-change map

| Finding | Implementation |
| --- | --- |
| A2-01: idle content mistaken for failed depth | `SourceIdentity.scene_revision`, all-tile coherent composites, a separate capture-poll clock, and explicit idle-scene telemetry distinguish unchanged pixels from pending or stalled inference. Coherent reuse preserves the real capture timestamp. The native visibility/health envelope and Python GUI/CLI consume the same evidence. |
| A2-02: bad tracking snapshots abandon the frame | Native pose/state read failures invalidate tracking, while capture, visibility, neutral fading, diagnostics and shutdown continue. Legacy coordinates must be finite and producer-timestamp fresh. |
| A2-03: failed backup deletes original | Preparation is isolated from live destinations. Verified immutable original backups and operation-local snapshots are completed before publication. Rollback restores only touched destinations from saved pre-operation bytes; missing required originals abort uninstall before any change. Recovery journals survive failures. |
| A2-04: repair corrupts uninstall ownership | Format-2 ownership preserves the first installation's `backup` decision and original hash across every repair. Repair rollback uses separate pre-repair snapshots. Changed layouts require uninstall first; installer-created proxies remain removable after repair. |
| A2-05: restore paths escape the game | Explicit managed-path allowlist, Windows drive/root/stream/dot-segment rejection, full ancestor reparse checks, resolved containment, complete manifest preflight, and rechecking before each write/delete. No recursive game-directory cleanup. |
| A2-06: frozen utility dispatch | Debug monitor, diagnostics and support collection use explicit `--utility-child` modes. Source and frozen commands share a constructor. The GUI retains child handles, reports exit/output paths, limits noninteractive jobs, and retires owned utilities on close. |
| A2-07: V2 never attaches late | Every frame retries V2 attachment; its payload and sequence mappings attach independently. The legacy filter is reset when the selected pose protocol changes. |
| A2-08: fallback skips V2 validation | Sequenced and double-copy readers acquire a candidate and then call the same production packet validator. Magic, version, finite numeric values, positive distance and confidence range are checked before admission. |
| A2-09: malformed preset strands blocked signals | Full preset and widget-range validation precede changes. `QSignalBlocker` restores previous signal states on every path; failed UI/publication changes restore the prior UI snapshot. |
| A2-10: unsafe configuration writes | Presets, profiles, compact/auto-tune preferences, setup, settings, backend selection, camera/display calibration, tilt and replay settings share a serialized read-modify-write API with unique same-directory temporaries, flush/fsync and atomic replacement. Callers merge only owned fields and surface errors. |
| A2-11: calibration child outlives controller | `QProcess` ownership replaces the detached daemon worker. Close/Escape requests cooperative cancellation, then timed terminate/kill escalation. Controller closure waits for child completion; application shutdown has bounded reaping. Calibration checks cancellation during capture and before configuration publication. Output tails are bounded. |
| A2-12: another tracker satisfies owned-child health | Each `TrackerProcess` uses a random run namespace passed explicitly to its child and native overlay. Pose, validity and backend-status readers/writers use that namespace. A named producer-existence lease prevents overlapping production writers. Failed retirement retains ownership instead of starting a replacement. |

## Compatibility and safety decisions

### Optional ReShade installation

The existing acknowledged-offline policy remains mandatory and is checked before
accessing the target. The default overlay remains non-injecting.

Old format-1 manifests can contain ownership already damaged by the old repair
bug. There is no reliable automatic way to determine whether such a backup is a
true original or a prior injected DLL. Repair/uninstall now refuse those
ambiguous records instead of deleting or restoring guessed data. Preserve the
manifest and `.glassless3d-reshade-backup` directory for manual ownership review.
This is an intentional fail-closed compatibility boundary, not a migration.

New installations use format 2, retaining original and installed hashes.
Uninstall also refuses edited managed files or absent/corrupt original backups.
Immutable originals are retained after successful uninstall as recovery material.
Operation failure retains `.glassless3d-recovery-*` journals and private staging
as applicable. Successful publication removes only known private staging files;
cleanup errors do not justify destructive game-directory traversal.

The path checks protect against malformed manifests and existing symlink/junction
layouts. The sidecar lock serializes cooperating installers. They are not a
security boundary against a same-account attacker actively replacing filesystem
ancestors between system calls; handle-relative hostile-filesystem hardening
would require a separately reviewed Windows implementation.

### Tracking compatibility

Launcher-owned runs no longer read or publish internal pose through shared
`G3D` names. Names follow
`Local\Glassless3D_<32-lowercase-hex-session>_<channel>` and are passed using
`G3D_TRACKING_SESSION`. The native and Python naming schemes agree, including
sequence companions. Invalid supplied session IDs fail closed instead of falling
back to global compatibility names.

A standalone tracker without a session retains the legacy channels and acquires
the legacy production-owner lease. FreeTrack output in launcher-owned sessions
is disabled by default; `tracking.publish_freetrack: true` explicitly enables
that compatibility output. It is not an authenticated private transport.
Tools launched by the GUI receive the owned session; an independently started
legacy debug tool must be given the corresponding session or use standalone
legacy mode. Live settings remain the existing separately coordinated settings
channel, not a newly versioned settings ABI.

Run namespaces isolate ordinary concurrent processes. They do not defend against
malware running as the same user and inspecting process environments/handles.
The session is stable across the owned object's internal restart, whose old
producer must be reaped before replacement. User-created new tracker objects
receive distinct namespaces.

### Unchanged-scene depth

A successful capture API poll has a separate liveness time; it does not replace
the scene's capture time. Age exemptions require all contributing tiles to refer
to the current scene revision and a live unchanged capture binding. New or mixed
scenes retain the ordinary age gate. Once a scene stops changing, incomplete
cached coverage is refreshed from the entire retained scene. An advancing
current-child heartbeat is still mandatory, and outstanding depth work still
consumes the depth-progress budget.

Capture polling provides API/session liveness, not proof that a game application
is internally responsive. An unavailable binding, device error or failed pose
read is not reinterpreted as successful new image acquisition.

## Deferred regression coverage

`tests/test_reshade_install.py` adds backup-failure, repair/uninstall ownership,
missing-backup, rollback, legacy-manifest and path-containment cases.
`tests/test_audit2_remediation.py` adds idle-scene/pending-work health, bad input,
transactional configuration, malformed presets, utility dispatch, cancellation,
run namespace and producer-lease cases. `overlay/audit_policy_tests.cpp` exercises
the actual scene-freshness gate and shared production packet validator.

These cases have not run. Full existing-suite compatibility, both advertised
Python versions, native compilation/DirectML, frozen dispatch and interactive
calibration still require validation before merging or distributing this branch.
No branch protection, required CI check, dependency lock or legal release gate
was disabled to make this source-only pass appear validated.
