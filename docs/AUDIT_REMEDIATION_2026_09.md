# September 2026 audit remediation

Baseline: `5d4bdaeb23c64da1e75c8e16ed5ec4116c8f36e3`.

## Finding-to-fix map

| Finding | Implementation and regression evidence |
|---|---|
| F01 lifecycle wedge | Owner-token cleanup and latest-request-wins reconciliation survive failed spawns, concurrent Stop/Restart, and thread creation failure. |
| F02 stale telemetry | Per-child instance IDs, startup/progress deadlines, monotonic summary admission, and process checks independent of log presence. |
| F03 depth stall | Consecutive worker failures become a renderer failure; native exit code 72 delegates restart to the existing bounded supervisor. Health counts accepted publications, not completed inferences. |
| F04 stale tiles | Each tile retains capture identity; composite age is the oldest contributing source. Startup/mode changes/aging trigger full coverage; expired previous textures cannot reappear through blending. Reused capture pixels are not retimestamped. |
| F05 shutdown ownership | Run-option objects exist before worker creation and remain stable until join; error snapshots have independent synchronization. |
| F06 GPU wait | Polling handles removal, cancellation, deadline and wait errors. Pending transfers cannot fall through to unsafe CPU fallback/free; bounded process isolation is the last-resort teardown boundary. |
| F07 shader arithmetic | Production shared HLSL trims five samples to three and divides by three; actual production SampleDepthCohesive is executed on WARP against six numerical oracles. |
| F08 exact-SHA assurance | Required checks gate release publication for the actual tag commit. Windows checks cover both supported Python versions; native and package workflows have no path-filter blind spots. Server-side branch rules are configured separately, not represented by a source file. |
| F09 build indirection | Canonical overlay.cpp directly calls the mode policy. Removed configure-time source rewriting and global ShowWindow interception. |
| F10 stale deployment | Checked CMake copies, per-file SHA-256 sidecar and embedded commit. Frozen startup and package creation validate the native files; package creation also checks the source commit. Removed tracked executable. |
| F11 dependency reproducibility | Full transitive hashed Windows locks for Python 3.11 and 3.12; no editable build isolation or dependency re-resolution. Use a single OpenCV contrib distribution. Existing release manifest retains pinned native/model/toolchain inventory in addition to Python SBOM. |
| F12 growing-log cost | GUI reads at most 64 KiB; native log rotates at 4 MiB with one retained predecessor. |
| F13 implementation evidence | Portable policies, production HLSL compile/numeric checks, real production DepthInferencer with a checked synthetic ONNX graph, mode changes and lazy-profile shutdown stress. Hardware quality is not inferred from these tests. |

## Additional baseline defects repaired

The first complete Windows baseline contained pre-existing regression failures.
Corrections include strict camera-policy integer parsing, duplicate jump-candidate
rejection, OpenCV identity-specific eye-memory reset, preservation of optical-flow
failure diagnostics, resetting frame-result ordering on camera replacement, and
restoring the missing packaged measurement-admission boundary. Launcher polling
and diagnostics now use the producer's Windows uptime clock rather than guessing
its epoch from Python monotonic time.

Tests that asserted removed source spellings or obsolete mocked lifecycle state
were updated to assert the new contracts. Behavioral failure tests were retained;
no failing suite was disabled to obtain a passing result.

## Validation tiers

Run `python -m scripts.locked_environment`, `python -m pytest -q`,
`python -m scripts.software_acceptance --generate-demo --fail-on-regression`,
`python scripts/bootstrap.py`, and `python -m scripts.run_native_tests`.
The native runner retains JUnit, textual output and source-tagged JSON under
`release/`. CI retains Python JUnit and resolved-environment evidence.

Synthetic DirectML/WARP integration does not certify a particular webcam,
display, driver, real-world latency, or subjective comfort. Follow the hardware
acceptance checklist for that evidence. The Python vulnerability audit is not a
claim of comprehensive native-library vulnerability coverage.

## Distribution remains fail closed

No license was selected as part of maintenance. The existing reviewed LICENSE
and THIRD_PARTY_NOTICES requirements remain in effect. Evaluation packages are
not a substitute for deciding distribution rights.
