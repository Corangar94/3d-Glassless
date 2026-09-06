from __future__ import annotations
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import MagicMock
import pytest
from launcher.overlay_health import OverlayProgressMonitor
from launcher.overlay_process import OverlayProcess, OverlayStartError
from launcher.native_provenance import FILES, MANIFEST, verify_native_build
from scripts.verify_ci_evidence import REQUIRED_CHECKS, validate_checks


def summary(**kw):
    data = dict(frame_count=1, depth_total=10, depth_hz=10, depth_age_ms=10,
                depth_published=1, instance_id="child", has_frame=True,
                capture_state="running", capture_reason="bound_target_wgc")
    data.update(kw)
    return SimpleNamespace(**data)


def test_old_child_cannot_supply_new_child_health():
    now=[0.0]; monitor=OverlayProgressMonitor(clock=lambda:now[0])
    assert not monitor.observe(summary(instance_id="old"),"child").healthy
    now[0]=31.0
    assert monitor.observe(summary(instance_id="old"),"child").restart_reason


def test_unchanged_healthy_record_expires():
    now=[0.0]; monitor=OverlayProgressMonitor(clock=lambda:now[0])
    assert monitor.observe(summary(),"child").healthy
    now[0]=1
    assert not monitor.observe(summary(),"child").healthy
    now[0]=6
    assert monitor.observe(summary(),"child").restart_reason == "overlay heartbeat stalled"


def test_inference_completion_without_accepted_depth_is_not_progress():
    now=[0.0]; monitor=OverlayProgressMonitor(clock=lambda:now[0])
    for i in range(7):
        now[0]=float(i)
        result=monitor.observe(summary(frame_count=i+1,depth_total=i*10,depth_published=0),"child")
    assert result.restart_reason == "accepted depth stalled"


def test_capture_pause_does_not_consume_depth_budget_but_stalled_heartbeat_does():
    now=[0.0]; monitor=OverlayProgressMonitor(clock=lambda:now[0])
    for i in range(20):
        now[0]=i
        result=monitor.observe(summary(frame_count=i+1,capture_state="unavailable",capture_reason="target_not_running"),"child")
        assert result.restart_reason is None
    now[0]=25
    assert monitor.observe(summary(frame_count=20),"child").restart_reason


def test_stale_depth_cannot_cancel_recovery():
    monitor=OverlayProgressMonitor()
    assert not monitor.observe(summary(depth_age_ms=751),"child").healthy
    assert monitor.observe(summary(frame_count=2,depth_age_ms=750),"child").healthy


def test_depth_counter_reset_needs_subsequent_progress():
    monitor=OverlayProgressMonitor()
    assert monitor.observe(summary(depth_published=8),"child").healthy
    assert not monitor.observe(summary(frame_count=2,depth_published=1),"child").healthy
    assert monitor.observe(summary(frame_count=3,depth_published=2),"child").healthy


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("exception", [OverlayStartError, OSError, RuntimeError])
def test_new_intent_survives_failed_spawn(monkeypatch, restart, exception):
    overlay=OverlayProcess(); entered=threading.Event(); release=threading.Event(); completed=threading.Event()
    calls=[]; proc=MagicMock();proc.poll.return_value=None
    def spawn(*args):
        calls.append(args)
        if len(calls)==1:
            entered.set()
            assert release.wait(5)
            raise exception("injected spawn failure")
        completed.set()
        return Path("overlay.exe"),proc
    monkeypatch.setattr(overlay,"_spawn",spawn)
    overlay.restart_async("first.exe")
    assert entered.wait(5)
    if restart: overlay.restart_async("second.exe")
    else: overlay.stop_async()
    release.set()
    # Join the actual lifecycle worker, without timing-based success guesses.
    workers=[t for t in threading.enumerate() if t.name=="g3d-overlay-lifecycle"]
    for worker in workers:worker.join(5)
    assert not overlay.is_transitioning()
    assert len(calls)==(2 if restart else 1)
    if restart:
        assert calls[-1][0]=="second.exe"
        assert overlay.is_running()
    overlay.stop()


def test_thread_start_failure_releases_owner(monkeypatch):
    overlay=OverlayProcess()
    def fail(_self):raise RuntimeError("no thread")
    monkeypatch.setattr(threading.Thread,"start",fail)
    with pytest.raises(RuntimeError):overlay.restart_async()
    assert not overlay.is_transitioning()
    assert overlay._worker_owner is None


def test_native_manifest_rejects_changed_binary_and_wrong_commit(tmp_path):
    files={}
    for name in FILES:
        data=name.encode();(tmp_path/name).write_bytes(data)
        files[name]=hashlib.sha256(data).hexdigest()
    (tmp_path/MANIFEST).write_text(json.dumps({"files":files,"source_commit":"abc"}))
    verify_native_build(tmp_path,"abc")
    with pytest.raises(ValueError):verify_native_build(tmp_path,"other")
    (tmp_path/FILES[0]).write_bytes(b"stale")
    with pytest.raises(ValueError):verify_native_build(tmp_path)


def test_release_rejects_missing_wrong_sha_untrusted_and_superseded_checks():
    checks=[dict(name=name,id=i,head_sha="sha",status="completed",conclusion="success",app={"slug":"github-actions"}) for i,name in enumerate(sorted(REQUIRED_CHECKS))]
    assert validate_checks(checks,"sha")==[]
    assert validate_checks(checks,"other")
    assert validate_checks([],"sha")
    bad=dict(checks[0],id=100,status="queued",conclusion=None)
    assert checks[0]["name"] in validate_checks(checks+[bad],"sha")
    assert validate_checks([dict(c,app={"slug":"other"}) for c in checks],"sha")


def test_log_tail_is_bounded_and_ignores_partial_first_record(tmp_path):
    from launcher.diagnostics import _latest_overlay_summary
    path=tmp_path/"overlay.log"
    with path.open("wb") as stream:
        stream.seek(16*1024*1024)
        stream.write(b"partial record\nnot a summary\n")
    assert _latest_overlay_summary(path) is None


def test_packaged_tracker_constructs_real_admission_and_rejects_expired_pose(monkeypatch):
    from tracker import main as tracker_main
    from tracker.latest_frame_capture import LatestFrameCapturePolicy
    from tracker.pose_stability_runtime import StableLatestFrameTrackingLoop
    from tracker.pose import HeadPosition
    tracker = SimpleNamespace(process_frame=lambda frame: None)
    loop = StableLatestFrameTrackingLoop(
        tracker=tracker, writer=MagicMock(), smoother=MagicMock(),
        latest_frame_capture_policy=LatestFrameCapturePolicy(enabled=False))
    monkeypatch.setattr(tracker_main, "monotonic_ms", lambda: 1000)
    def pose(ts, confidence=0.9):
        return HeadPosition(1.0, 2.0, 60.0, confidence=confidence, capture_timestamp_ms=ts)
    assert loop._measurement_admission.accept(pose(1000)) is not None
    assert loop._measurement_admission.accept(pose(200)) is None
    assert loop._measurement_admission.accept(pose(1000, 0.01)) is None
    loop._measurement_admission.reset()
    assert loop.pose_jump_confirmation_snapshot().anchor_timestamp_ms is None


def test_launcher_session_clock_is_independent_of_python_monotonic_epoch(monkeypatch, qapp):
    import launcher.tracker_process as module
    now = [5000]
    monkeypatch.setattr(module, "wire_now_ms", lambda: now[0])
    monkeypatch.setattr(module.time, "monotonic", lambda: 1_000_000.0)
    proc = MagicMock(); proc.poll.return_value = None
    reader = MagicMock(); reader.read.return_value = (1.0, 2.0, 60.0, 5001)
    state = MagicMock(); state.read.return_value = ("tracking", 5001)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(module, "SharedMemoryReader", lambda *a: reader)
    monkeypatch.setattr(module, "TrackingStateReader", lambda *a: state)
    tracker = module.TrackerProcess("test.yaml")
    assert tracker._launch_process()
    assert tracker.poll_admission_snapshot().session_start_timestamp_ms == 5000
    events = []
    tracker.position_sampled.connect(lambda *args: events.append(args))
    now[0] = 5002
    tracker._poll()
    assert events == [(1.0, 2.0, 60.0, 5001)]
    tracker._close_readers()
    tracker._proc = None


def test_locked_environment_rejects_unlocked_runner_packages(tmp_path):
    from scripts.locked_environment import validate_locked_versions
    lock = tmp_path / "environment.lock"
    lock.write_text("Some_Package==1.2 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    validate_locked_versions(lock, {"some-package": "1.2", "glassless3d": "0.1.0"})
    for installed in ({"some-package": "1.2", "extra": "1"}, {"some-package": "1.3"}, {}):
        with pytest.raises(RuntimeError):
            validate_locked_versions(lock, installed)


def test_package_provenance_prefers_actual_checkout_over_event_merge_sha(monkeypatch):
    from scripts import package_windows_release as package
    monkeypatch.setenv("GITHUB_SHA", "synthetic-merge")
    monkeypatch.setattr(package, "_git_output", lambda *args: "actual-checkout")
    assert package._source_commit(None) == "actual-checkout"
    assert package._source_commit("explicit") == "explicit"
    monkeypatch.setattr(package, "_git_output", lambda *args: None)
    assert package._source_commit(None) == "synthetic-merge"
