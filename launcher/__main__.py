"""Dispatch the GUI, private tracker, or camera-calibration child."""
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

        def _run_calibration() -> None:
            raise SystemExit(calibration_main())

        return _run_calibration

    from launcher.app import main

    return main


def main() -> None:
    _select_main(sys.argv)()


if __name__ == "__main__":
    main()
