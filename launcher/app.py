"""QApplication entry point for Glassless3D."""
from __future__ import annotations

import logging
import os
import sys

import yaml

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
from PySide6.QtWidgets import QApplication, QWizard

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


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Glassless3D")

    if _is_first_run():
        from launcher.wizard import SetupWizard
        wizard = SetupWizard(config_path=CONFIG_PATH)
        if wizard.exec() != QWizard.DialogCode.Accepted:
            sys.exit(0)

    config = _load_config()
    from launcher.mainwindow import MainWindow
    window = MainWindow(config=config, config_path=CONFIG_PATH)
    window.show()
    sys.exit(app.exec())
