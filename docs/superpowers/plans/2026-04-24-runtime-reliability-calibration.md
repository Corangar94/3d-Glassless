# Runtime Reliability And Calibration Bench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Glassless3D fail loudly or recover when tracking freezes, and add a repeatable calibration bench for measuring tracking quality before judging WoW screenshots.

**Architecture:** Extend the existing subprocess tracker, diagnostics parser, and pure tracking metrics instead of adding parallel infrastructure. The tracker process owns restart-on-stale behavior, diagnostics marks stale shared memory as not-ready, and `tracker.calibration_bench` samples `G3D` shared memory into reusable `PoseSample` metrics.

**Tech Stack:** Python, PySide6 `QTimer`, existing `tracker.shared_memory`, existing `tracker.evaluation`, pytest.

---

### Task 1: Tracker Watchdog

**Files:**
- Modify: `launcher/tracker_process.py`
- Test: `tests/test_tracker_process.py`

- [ ] Add focused tests that call `_poll()` with a live subprocess and stale shared-memory timestamp.
- [ ] Implement restart-on-stale after a longer timeout than the UI paused threshold.
- [ ] Emit `restarting` then `initializing` around the restart attempt.
- [ ] Keep restart attempts capped so a dead camera does not loop forever.

### Task 2: Diagnostics Stale Tracking Gate

**Files:**
- Modify: `launcher/diagnostics.py`
- Test: `tests/test_diagnostics.py`

- [ ] Add a test where the latest overlay log reports `STALE`.
- [ ] Mark stale shared memory as a problem, not only a warning.
- [ ] Keep live shared memory as ready when other assets are present.

### Task 3: Calibration Bench Command

**Files:**
- Create: `tracker/calibration_bench.py`
- Test: `tests/test_calibration_bench.py`

- [ ] Add tests for sampling valid, stale, and missing shared-memory poses.
- [ ] Implement `capture_tracking_samples()` using `SharedMemoryReader`.
- [ ] Implement JSON and text formatters using `compute_tracking_metrics()`.
- [ ] Add `python -m tracker.calibration_bench --duration 10 --output tracking_bench.json`.

### Task 4: Launcher Surfacing

**Files:**
- Modify: `launcher/mainwindow.py`
- Test: `tests/test_mainwindow.py`

- [ ] Show a clear tracker tile/status during `restarting`.
- [ ] Leave overlay stopped if tracker escalates to `error`.
- [ ] Keep the window restore behavior from the previous runtime fix.

### Task 5: Verification

**Commands:**
- `pytest tests/test_tracker_process.py tests/test_diagnostics.py tests/test_calibration_bench.py tests/test_mainwindow.py -q`
- `pytest tests/ -q`

**Manual smoke after tests:**
- Start with `python -m launcher`.
- Confirm diagnostics is not `READY` if the overlay log reports `SHM STALE`.
- Run `python -m tracker.calibration_bench --duration 10 --output tracking_bench.json` while tracker is live.
