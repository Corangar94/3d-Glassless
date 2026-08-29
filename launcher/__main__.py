"""Dispatch the GUI, private tracker, or camera-calibration tools."""
from __future__ import annotations

import sys
from collections.abc import Callable


def _select_main(argv: list[str]) -> Callable[[], None]:
    if "--tracker-child" in argv:
        argv.remove("--tracker-child")
        from tracker.main import main

        return main

    if "--camera-calibration-child" in argv:
        argv.remove("--camera-calibration-child")
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
