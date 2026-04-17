"""QApplication entry point for Glassless3D."""
from __future__ import annotations

import argparse
import logging
import os
import sys

import yaml

logging.basicConfig(
    level=logging.WARNING,
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
    sys.exit(app.exec())
