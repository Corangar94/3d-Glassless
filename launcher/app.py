"""QApplication entry point for Glassless3D."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

import yaml

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
from PySide6.QtWidgets import QApplication, QWizard
from PySide6.QtCore import QTimer

CONFIG_PATH = os.path.join(
    os.environ.get("APPDATA", "."), "Glassless3D", "config.yaml"
)


def _is_first_run(config_path: str = CONFIG_PATH) -> bool:
    return not os.path.exists(config_path)


def _load_config(config_path: str = CONFIG_PATH) -> dict[str, object]:
    with open(config_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Config at {config_path!r} is not a YAML mapping")
    return data


def _parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Launch the Glassless3D overlay-first runtime controller."
    )
    parser.add_argument(
        "--config",
        default=CONFIG_PATH,
        help="Path to the launcher config.yaml. Defaults to %%APPDATA%%/Glassless3D/config.yaml.",
    )
    return parser.parse_known_args(argv)


def _request_app_shutdown(app: QApplication) -> None:
    """Close windows first so MainWindow.closeEvent can stop child processes."""
    app.closeAllWindows()
    app.quit()


def _make_sigint_handler(app: QApplication):
    def _handler(_signum: object, _frame: object) -> None:
        _request_app_shutdown(app)

    return _handler


def _install_console_interrupt_handler(app: QApplication) -> QTimer:
    signal.signal(signal.SIGINT, _make_sigint_handler(app))
    # Periodic no-op lets Python process Ctrl+C while Qt owns the event loop.
    timer = QTimer(app)
    timer.setInterval(250)
    timer.timeout.connect(lambda: None)
    timer.start()
    return timer


def main(argv: list[str] | None = None) -> None:
    raw_args = sys.argv[1:] if argv is None else argv
    args, qt_args = _parse_args(raw_args)

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Glassless3D")

    config_path = str(args.config)
    if _is_first_run(config_path):
        from launcher.wizard import SetupWizard
        wizard = SetupWizard(config_path=config_path)
        if wizard.exec() != QWizard.DialogCode.Accepted:
            sys.exit(0)

    config = _load_config(config_path)
    from launcher.mainwindow import MainWindow
    window = MainWindow(config=config, config_path=config_path)
    window.show()
    interrupt_timer = _install_console_interrupt_handler(app)
    try:
        code = app.exec()
    except KeyboardInterrupt:
        _request_app_shutdown(app)
        code = 130
    finally:
        interrupt_timer.stop()
    sys.exit(code)
