#!/usr/bin/env python3
"""Start a controlled Glassless3D runtime and require fresh diagnostics."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from launcher import diagnostics
from launcher.overlay_process import OverlayProcess, OverlayStartError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--tracker-mode", choices=["fake-static"], default="fake-static")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path

    children: list[subprocess.Popen[bytes]] = []
    overlay = OverlayProcess()
    try:
        _clear_overlay_log()
        children.append(_start_settings_writer(config_path))
        children.append(_start_fake_tracker(args.tracker_mode))
        overlay.start()
        deadline = time.monotonic() + max(0.1, args.timeout)
        last_report: diagnostics.DiagnosticsReport | None = None
        while time.monotonic() < deadline:
            exit_code = overlay.poll_exit_code()
            if exit_code is not None:
                print(
                    f"overlay exited before runtime became ready (exit code {exit_code})",
                    file=sys.stderr,
                )
                return 1
            last_report = diagnostics.collect_diagnostics(
                config_path,
                require_live_runtime=True,
            )
            if last_report.ready:
                print(diagnostics.format_diagnostics_report(last_report))
                return 0
            time.sleep(max(0.05, args.poll_interval))
        if last_report is not None:
            print(diagnostics.format_diagnostics_report(last_report))
        else:
            print("Glassless3D live runtime check timed out before diagnostics ran")
        return 1
    except OverlayStartError as e:
        print(f"overlay start failed: {e}", file=sys.stderr)
        return 1
    finally:
        overlay.stop()
        for child in children:
            _stop_child(child)


def _start_settings_writer(config_path: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [sys.executable, "scripts/run_settings_writer.py", "--config", str(config_path)],
        cwd=str(Path(__file__).resolve().parent.parent),
    )


def _overlay_log_path() -> Path:
    return Path(__file__).resolve().parent.parent / "overlay.log"


def _clear_overlay_log() -> None:
    try:
        _overlay_log_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f"warning: could not clear previous overlay log: {e}", file=sys.stderr)


def _start_fake_tracker(mode: str) -> subprocess.Popen[bytes]:
    if mode != "fake-static":
        raise ValueError(f"unsupported tracker mode: {mode}")
    return subprocess.Popen(
        [sys.executable, "tests/fake_tracker.py", "--static", "x=0", "y=0", "z=60"],
        cwd=str(Path(__file__).resolve().parent.parent),
    )


def _stop_child(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    try:
        child.terminate()
        child.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        child.kill()
        try:
            child.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
