# tests/test_app.py
import os
import pytest
from PySide6.QtWidgets import QApplication

from launcher.app import (
    CONFIG_PATH,
    _is_first_run,
    _load_config,
    _make_sigint_handler,
    _parse_args,
    _request_app_shutdown,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_is_first_run_true_when_config_absent(tmp_path):
    path = str(tmp_path / "nonexistent.yaml")
    assert _is_first_run(path) is True


def test_is_first_run_false_when_config_exists(tmp_path):
    path = str(tmp_path / "config.yaml")
    with open(path, "w"):
        pass
    assert _is_first_run(path) is False


def test_load_config_returns_dict(tmp_path):
    import yaml
    cfg = {"camera": {"index": 0}, "screen": {"width_cm": 60.0, "height_cm": 34.0},
           "tracking": {"ipd_cm": 6.3, "smoothing_q": 0.01, "smoothing_r": 0.1, "hold_ms": 500},
           "gui": {"compact_mode": False}}
    path = str(tmp_path / "config.yaml")
    with open(path, "w") as f:
        yaml.dump(cfg, f)
    loaded = _load_config(path)
    assert loaded["camera"]["index"] == 0


def test_config_path_uses_appdata():
    appdata = os.environ.get("APPDATA", ".")
    assert CONFIG_PATH.startswith(appdata)
    assert CONFIG_PATH.endswith("config.yaml")


def test_parse_args_accepts_explicit_config_path():
    args, qt_args = _parse_args(["--config", "config.yaml", "-platform", "offscreen"])

    assert args.config == "config.yaml"
    assert qt_args == ["-platform", "offscreen"]


def test_parse_args_defaults_to_appdata_config():
    args, _ = _parse_args([])

    assert args.config == CONFIG_PATH


def test_request_app_shutdown_closes_windows_and_quits():
    calls = []

    class FakeApp:
        def closeAllWindows(self):
            calls.append("close")

        def quit(self):
            calls.append("quit")

    _request_app_shutdown(FakeApp())

    assert calls == ["close", "quit"]


def test_sigint_handler_requests_app_shutdown():
    calls = []

    class FakeApp:
        def closeAllWindows(self):
            calls.append("close")

        def quit(self):
            calls.append("quit")

    handler = _make_sigint_handler(FakeApp())
    handler(None, None)

    assert calls == ["close", "quit"]
