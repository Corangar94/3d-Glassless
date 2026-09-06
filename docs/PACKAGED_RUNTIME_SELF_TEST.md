# Offline packaged-runtime validation

This follow-up to PR #103 adds production startup probes beyond the existing
`--help` checks. It does not merge or publish a release, decide a license, or
certify webcam/display hardware, tracking accuracy, depth quality or comfort.

## Newly reproduced fallback defect

The previously locked Windows x64 wheel
`opencv_contrib_python-5.0.0.93-cp37-abi3-win_amd64.whl` has SHA-256
`461622db95c964652d4d8fda171034961c3de270f78a6095aaad31050771774a`.
Inspection of the downloaded archive and installed RECORD found zero XML files.
The production fallback needs the frontal-face and eye Haar cascades, so a
successful `import cv2`, mock-based tracker tests, and frozen `--help` did not
establish that fallback tracking could initialize.

The verified replacement is
`opencv_contrib_python-4.14.0.94-cp37-abi3-win_amd64.whl`, SHA-256
`626617c1e66b2537d075e00adba62bd7566df37f7878d185e047bfd49a6d50c7`.
It includes 17 cascade XML files. Both Windows locks now select that exact
wheel. The project and tracker requirement ranges constrain OpenCV below 5
pending explicit compatibility/asset revalidation. No second OpenCV
distribution is installed beside contrib.

`Glassless3D.spec` refuses to build without the required cascade assets. An
unmocked fallback initialization/inference regression now runs in the Python
suite as well, so the missing assets cannot hide behind a successful import.

Primary package records:
- https://pypi.org/project/opencv-contrib-python/5.0.0.93/
- https://pypi.org/project/opencv-contrib-python/4.14.0.94/

## Probe coverage

The launcher has a strict `--self-test` dispatch path selected before interactive
or tracker-child modes. Conflicting modes are rejected, not silently ignored.
All six stages must pass, in order:

1. Verify native file hashes/source identity and both pinned model hashes.
2. Import the production tracker, GUI, and calibration entry modules.
3. Round-trip legacy pose, validity, versioned pose, and settings through real,
   uniquely named `Local` Windows mappings, never the live G3D channels.
4. Initialize the actual MediaPipe face model and process generated blank input
   in image and live-stream modes, including callback completion and closure.
5. Initialize actual OpenCV classifiers and process the same generated input.
6. Construct the actual runtime MainWindow with a temporary configuration and
   injected isolated settings transport, execute the Qt event loop, render its
   own widget offscreen, and close it without starting tracker/overlay children.

The MainWindow still creates/owns its normal settings writer by default. The
optional injected writer follows the same ownership/close contract. No live
settings namespace is changed just to run the test.

These probes do not open a webcam, capture the desktop, start the native overlay,
download missing files, or save screenshots. The widget render remains in
memory. Depth-model bytes are verified here; the separate native integration
suite exercises the production inferencer with its existing synthetic graph.

## Evidence and supervision

`launcher.self_test` writes a JSON report before starting and atomically updates
it as stages complete. A failed stage stops later probes. The report includes
scope, schema, request ID, frozen/source status and per-stage outcomes. It never
converts a missing asset into a skipped success.

`scripts.run_frozen_smoke` invokes the actual bundled executable from an unrelated
working directory, removes Python source-path assistance, supplies a fresh
request nonce, and enforces a process deadline (default 120 seconds). It rejects
nonzero exits, timeouts, missing/oversized/malformed reports, stale nonces,
source-only execution, missing/duplicated/reordered stages, unsuccessful stages,
and a mismatched native source commit. Failure log tails are bounded to 64 KiB.
Configuration and cache locations belong to a temporary self-test episode.

The runner is implemented and its success/failure contracts are tested. Fresh
frozen-build execution was blocked in the implementation session, so this
follow-up is NOT a validated Windows package and does not replace PR #103's
previously built artifacts. The new runner is not yet a required CI step; that
integration must follow permitted frozen-build validation, not bypass it.

## Observed validation on Windows, Python 3.12

- Complete Python suite: 2,319 passed; zero failures, errors, or skips.
- Targeted startup/window tests: 106 passed.
- Source-mode offline self-test: all six stages passed with the actual installed
  models, OpenCV cascades, Windows shared memory and offscreen runtime window.
- `pip check` passed, and the installed dependency closure exactly matches the
  updated Windows Python 3.12 hash lock.
- The updated environment's Python dependency audit reported no known
  vulnerabilities. This is not a native-library vulnerability certification.

Python 3.11 was not rerun for this follow-up. The original PR #103 CI results
belong to its earlier commit, not these changes. Fresh PyInstaller execution
was blocked by the tool safety layer; no alternate build service was used.
No release or updated frozen binary is claimed from this follow-up.
