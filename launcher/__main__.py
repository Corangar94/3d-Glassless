"""Dispatch the GUI or the private tracker child in frozen builds."""
from __future__ import annotations

import sys
from collections.abc import Callable


def _select_main(argv: list[str]) -> Callable[[], None]:
    if "--tracker-child" in argv:
        argv.remove("--tracker-child")
        from tracker.main import main

        return main

    from launcher.app import main

    return main


def main() -> None:
    _select_main(sys.argv)()


if __name__ == "__main__":
    main()
