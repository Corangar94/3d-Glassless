from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tracker import camera_control_recovery_runtime
from tracker.camera_control_lock_policy import (
    DEFAULT_LOCK_CONTROLS_AFTER_WARMUP,
    parse_camera_control_lock_enabled,
)
from tracker.camera_control_recovery import CameraControlRecoveryPolicy
from tracker.camera_control_recovery_runtime import (
    CameraControlRecoveryTrackingLoop,
)
from tracker.pose_stability_runtime import StableLatestFrameTrackingLoop


def test_missing_lock_setting_uses_safe_packaged_default():
    assert DEFAULT_LOCK_CONTROLS_AFTER_WARMUP is True
    assert parse_camera_control_lock_enabled({}) is True
    assert parse_camera_control_lock_enabled(None) is True


@pytest.mark.parametrize(
    "value, expected",
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("YES", True),
        (" on ", True),
        ("1", True),
        ("false", False),
        ("NO", False),
        (" off ", False),
        ("0", False),
    ],
)
def test_explicit_boolean_forms_are_supported(value, expected):
    assert parse_camera_control_lock_enabled(
        {"lock_controls_after_warmup": value}
    ) is expected


@pytest.mark.parametrize(
    "value",
    [None, 2, -1, 1.0, [], {}, "maybe", ""],
)
def test_invalid_explicit_value_fails_closed(value):
    logs: list[str] = []

    enabled = parse_camera_control_lock_enabled(
        {"lock_controls_after_warmup": value},
        logger=logs.append,
    )

    assert enabled is False
    assert len(logs) == 1
    assert "leaving automatic controls enabled" in logs[0]


def _write_config(tmp_path, camera: object) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"camera": camera}), encoding="utf-8")
    return path


def _construct_runtime(
    monkeypatch,
    *,
    config_path: object | None,
    lock_camera_controls: object = None,
):
    observed: dict[str, object] = {}

    def fake_super_init(self, *args, **kwargs):
        observed.update(kwargs)
        self._camera_control_lock_retry = None
        self._camera_quality_monitor = None

    monkeypatch.setattr(
        StableLatestFrameTrackingLoop,
        "__init__",
        fake_super_init,
    )
    kwargs: dict[str, object] = {
        "camera_control_recovery_policy": CameraControlRecoveryPolicy(),
    }
    if config_path is not None:
        kwargs["config_path"] = config_path
    if lock_camera_controls is not None:
        kwargs["lock_camera_controls"] = lock_camera_controls
    loop = CameraControlRecoveryTrackingLoop(**kwargs)
    return loop, observed


def test_valid_config_with_missing_key_enables_lock_controller(
    tmp_path,
    monkeypatch,
):
    config_path = _write_config(tmp_path, {})

    _loop, observed = _construct_runtime(
        monkeypatch,
        config_path=config_path,
        lock_camera_controls=False,
    )

    assert observed["lock_camera_controls"] is True


def test_explicit_false_in_packaged_config_disables_lock_controller(
    tmp_path,
    monkeypatch,
):
    config_path = _write_config(
        tmp_path,
        {"lock_controls_after_warmup": False},
    )

    _loop, observed = _construct_runtime(
        monkeypatch,
        config_path=config_path,
        lock_camera_controls=True,
    )

    assert observed["lock_camera_controls"] is False


def test_boolean_string_in_packaged_config_is_honored(tmp_path, monkeypatch):
    config_path = _write_config(
        tmp_path,
        {"lock_controls_after_warmup": "off"},
    )

    _loop, observed = _construct_runtime(
        monkeypatch,
        config_path=config_path,
        lock_camera_controls=True,
    )

    assert observed["lock_camera_controls"] is False


def test_invalid_packaged_value_disables_hardware_changes(
    tmp_path,
    monkeypatch,
):
    config_path = _write_config(
        tmp_path,
        {"lock_controls_after_warmup": "invalid"},
    )
    logs: list[str] = []
    monkeypatch.setattr(
        camera_control_recovery_runtime,
        "print",
        logs.append,
        raising=False,
    )

    _loop, observed = _construct_runtime(
        monkeypatch,
        config_path=config_path,
        lock_camera_controls=True,
    )

    assert observed["lock_camera_controls"] is False
    assert any("leaving automatic controls enabled" in line for line in logs)


def test_direct_no_config_caller_keeps_explicit_constructor_choice(monkeypatch):
    _loop, observed = _construct_runtime(
        monkeypatch,
        config_path=None,
        lock_camera_controls=False,
    )

    assert observed["lock_camera_controls"] is False


def test_unreadable_config_preserves_caller_default(tmp_path, monkeypatch):
    logs: list[str] = []
    monkeypatch.setattr(
        camera_control_recovery_runtime,
        "print",
        logs.append,
        raising=False,
    )

    _loop, observed = _construct_runtime(
        monkeypatch,
        config_path=tmp_path / "missing.yaml",
        lock_camera_controls=False,
    )

    assert observed["lock_camera_controls"] is False
    assert any("using caller defaults" in line for line in logs)


def test_repository_setup_frozen_and_docs_use_same_default():
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    wizard = Path("launcher/wizard.py").read_text(encoding="utf-8")
    spec = Path("Glassless3D.spec").read_text(encoding="utf-8")
    locking_docs = Path("docs/CAMERA_CONTROL_LOCKING.md").read_text(
        encoding="utf-8"
    )
    recovery_docs = Path("docs/CAMERA_CONTROL_RECOVERY.md").read_text(
        encoding="utf-8"
    )

    assert config["camera"]["lock_controls_after_warmup"] is True
    assert "DEFAULT_LOCK_CONTROLS_AFTER_WARMUP" in wizard
    assert '"lock_controls_after_warmup": (' in wizard
    assert '"tracker.camera_control_lock_policy"' in spec
    assert "enable the stabilized-camera policy by default" in locking_docs
    assert "enable safe control stabilization by default" in recovery_docs
    assert "lock_controls_after_warmup: false" in recovery_docs
