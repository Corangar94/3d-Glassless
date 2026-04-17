# tests/test_overlay_process.py
"""Tests for launcher.overlay_process.

The overlay is a real Windows binary with GPU/D3D dependencies, so these
tests mock subprocess.Popen and filesystem lookups. We verify:
  * find_overlay_exe walks the candidate list correctly
  * OverlayProcess.start raises cleanly when the exe is missing
  * missing depth model is non-fatal (warns but continues)
  * stop() gracefully handles already-stopped, terminates, then kills on timeout
  * is_running / poll_exit_code reflect the underlying Popen state
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from launcher import overlay_process
from launcher.overlay_process import (
    OverlayProcess,
    OverlayStartError,
    find_depth_model,
    find_overlay_exe,
)


# ── find_overlay_exe ────────────────────────────────────────────────────────

def test_find_overlay_exe_prefers_project_root(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    root_exe = tmp_path / "Glassless3DOverlay.exe"
    root_exe.write_bytes(b"")
    (tmp_path / "overlay" / "build_mingw").mkdir(parents=True)
    (tmp_path / "overlay" / "build_mingw" / "Glassless3DOverlay.exe").write_bytes(b"")
    assert find_overlay_exe() == root_exe


def test_find_overlay_exe_falls_back_to_build_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    build_dir = tmp_path / "overlay" / "build_mingw"
    build_dir.mkdir(parents=True)
    build_exe = build_dir / "Glassless3DOverlay.exe"
    build_exe.write_bytes(b"")
    assert find_overlay_exe() == build_exe


def test_find_overlay_exe_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    assert find_overlay_exe() is None


def test_find_depth_model_present(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    (tmp_path / "models").mkdir()
    model = tmp_path / "models" / "depth_anything_v2_small_fp16.onnx"
    model.write_bytes(b"")
    assert find_depth_model() == model


def test_find_depth_model_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    assert find_depth_model() is None


# ── OverlayProcess.start ────────────────────────────────────────────────────

def test_start_raises_actionable_message_when_overlay_missing(monkeypatch):
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: None)
    p = OverlayProcess()
    with pytest.raises(OverlayStartError) as exc_info:
        p.start()
    message = str(exc_info.value)
    assert "overlay" in message.lower()
    assert "primary runtime" in message.lower()
    assert "bootstrap.py" in message
    assert not p.is_running()


def test_start_spawns_subprocess_and_records_path(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    model = tmp_path / "models" / "depth_anything_v2_small_fp16.onnx"
    model.parent.mkdir()
    model.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: model)

    fake_popen = MagicMock()
    fake_popen.return_value.poll.return_value = None
    with patch.object(overlay_process.subprocess, "Popen", fake_popen):
        p = OverlayProcess()
        returned = p.start()

    assert returned == exe
    assert p.is_running()
    args, kwargs = fake_popen.call_args
    assert args[0] == [str(exe)]
    assert kwargs["cwd"] == str(tmp_path)


def test_start_warns_but_succeeds_without_model(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: None)

    fake_popen = MagicMock()
    fake_popen.return_value.poll.return_value = None
    with patch.object(overlay_process.subprocess, "Popen", fake_popen):
        OverlayProcess().start()

    err = capsys.readouterr().err
    assert "depth model not found" in err.lower()
    assert "flat fallback depth" in err.lower()


def test_start_wraps_subprocess_launch_failure(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: None)

    with patch.object(
        overlay_process.subprocess,
        "Popen",
        side_effect=OSError("blocked by antivirus"),
    ):
        with pytest.raises(OverlayStartError) as exc_info:
            OverlayProcess().start()

    message = str(exc_info.value)
    assert "desktop overlay" in message.lower()
    assert "bootstrap.py" in message
    assert "blocked by antivirus" in message


def test_start_is_idempotent_when_running(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: None)
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)

    fake_popen = MagicMock()
    fake_popen.return_value.poll.return_value = None
    with patch.object(overlay_process.subprocess, "Popen", fake_popen):
        p = OverlayProcess()
        p.start()
        p.start()   # second call must not spawn again
    assert fake_popen.call_count == 1


# ── OverlayProcess.stop ─────────────────────────────────────────────────────

def test_stop_noop_when_never_started():
    p = OverlayProcess()
    p.stop()   # must not raise
    assert not p.is_running()


def test_stop_terminates_running_process(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: None)
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)

    proc = MagicMock()
    proc.poll.return_value = None    # still running until terminate
    fake_popen = MagicMock(return_value=proc)
    with patch.object(overlay_process.subprocess, "Popen", fake_popen):
        p = OverlayProcess()
        p.start()
        p.stop()
    proc.terminate.assert_called_once()
    proc.wait.assert_called_once()
    assert not p.is_running()


def test_stop_kills_on_timeout(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: None)
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)

    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [subprocess.TimeoutExpired("overlay", 3.0), None]
    fake_popen = MagicMock(return_value=proc)
    with patch.object(overlay_process.subprocess, "Popen", fake_popen):
        p = OverlayProcess()
        p.start()
        p.stop()
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


# ── status accessors ────────────────────────────────────────────────────────

def test_is_running_false_after_exit(tmp_path, monkeypatch):
    exe = tmp_path / "Glassless3DOverlay.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(overlay_process, "find_overlay_exe", lambda: exe)
    monkeypatch.setattr(overlay_process, "find_depth_model", lambda: None)
    monkeypatch.setattr(overlay_process, "_project_root", lambda: tmp_path)

    proc = MagicMock()
    proc.poll.return_value = 0
    with patch.object(overlay_process.subprocess, "Popen", return_value=proc):
        p = OverlayProcess()
        p.start()
    assert not p.is_running()
    assert p.poll_exit_code() == 0
