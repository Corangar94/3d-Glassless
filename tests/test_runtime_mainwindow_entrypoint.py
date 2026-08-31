from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_gui_entrypoint_uses_runtime_aware_mainwindow():
    source = _source("launcher/app.py")

    assert "from launcher.runtime_mainwindow import MainWindow" in source
    assert "from launcher.mainwindow import MainWindow" not in source


def test_runtime_window_refreshes_backend_and_overlay_health():
    source = _source("launcher/runtime_mainwindow.py")

    assert "def _refresh_tracker_backend_health(self) -> None:" in source
    assert "read_tracker_backend_status()" in source
    assert "tracker_backend_tile_text(" in source
    assert "def _refresh_runtime_health(self) -> None:" in source
    assert "super()._refresh_runtime_health()" in source
    assert "def _on_status(self, status: str) -> None:" in source


def test_base_mainwindow_is_not_rewritten_for_the_operator_layer():
    source = _source("launcher/runtime_mainwindow.py")

    assert "class MainWindow(_BaseMainWindow):" in source
    assert "class MainWindow(QMainWindow):" not in source


def test_frozen_package_explicitly_includes_runtime_window():
    spec = _source("Glassless3D.spec")

    assert '"launcher.runtime_mainwindow"' in spec
    assert '"launcher.tracker_backend_diagnostics"' in spec
    assert '"tracker.backend_status_shared_memory"' in spec
