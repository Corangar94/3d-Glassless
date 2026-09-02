"""Dispatch the GUI, private tracker, or camera-calibration tools."""
from __future__ import annotations

import os
import sys
from collections.abc import Callable


def _ensure_child_streams() -> None:
    """Give windowed frozen children harmless streams when no console exists."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _select_main(argv: list[str]) -> Callable[[], None]:
    if "--tracker-child" in argv:
        argv.remove("--tracker-child")
        from tracker.latest_frame_runtime import main

        return main

    if "--camera-calibration-child" in argv:
        argv.remove("--camera-calibration-child")
        _ensure_child_streams()
        from scripts.calibrate_camera import main as calibration_main

        def _run_calibration_child() -> None:
            raise SystemExit(calibration_main())

        return _run_calibration_child

    if "--calibrate-camera" in argv:
        argv.remove("--calibrate-camera")
        from launcher.app import _parse_args
        from launcher.camera_calibration_wizard import main as calibration_wizard_main

        def _run_calibration_wizard() -> None:
            args, _unknown = _parse_args(argv[1:])
            calibration_wizard_main(config_path=str(args.config))

        return _run_calibration_wizard

    from launcher.app import main

    return main


def main() -> None:
    _select_main(sys.argv)()


if __name__ == "__main__":
    main()
