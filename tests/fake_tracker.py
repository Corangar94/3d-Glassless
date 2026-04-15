# tests/fake_tracker.py
"""Fake head tracker for testing the Glassless3D overlay without a webcam.

Modes
-----
(default)    Sine oscillation for N seconds (original behaviour).
--static     Hold fixed x/y/z forever.
--sweep      Sine sweep on X with constant Z.
--interactive  Keyboard control (Windows only).
"""
import argparse
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tracker.shared_memory import SharedMemoryWriter  # noqa: E402
from tracker.shared_settings import OverlaySettings, SharedSettingsReader  # noqa: E402

_DEFAULT_SETTINGS = OverlaySettings(
    strength_x=1.0,
    strength_y=1.0,
    virtual_depth_cm=30.0,
    screen_w_cm=119.3,
    screen_h_cm=33.6,
)

_WRITE_HZ: int = 120
_STATUS_INTERVAL_S: float = 0.5
_DEFAULT_Z_CM: float = 60.0
_SHIFT_GOOD: float = 2.0
_SHIFT_HIGH: float = 4.0
_INTERACTIVE_XY_STEP: float = 1.0
_INTERACTIVE_Z_STEP: float = 5.0
_INTERACTIVE_Z_MAX: float = 300.0
_INTERACTIVE_Z_MIN: float = 5.0


# ----------------------------------------------------------------- helpers --

def _compute_shift_pct(
    x: float, y: float, z: float, s: OverlaySettings,
) -> tuple[float, float]:
    """Return (shift_x_pct, shift_y_pct) using same formula as the HLSL shader."""
    denom = z + s.virtual_depth_cm
    f = s.virtual_depth_cm / max(denom, 0.001)
    sx = abs(x / max(s.screen_w_cm, 0.001)) * f * s.strength_x * 100.0
    sy = abs(y / max(s.screen_h_cm, 0.001)) * f * s.strength_y * 100.0
    return sx, sy


def _shift_tag(sx: float, sy: float) -> str:
    worst = max(sx, sy)
    if worst < _SHIFT_GOOD:
        return "GOOD"
    if worst < _SHIFT_HIGH:
        return "HIGH"
    return "DANGER"


def _read_settings() -> OverlaySettings:
    reader = SharedSettingsReader()
    s = reader.read()
    reader.close()
    return s or _DEFAULT_SETTINGS


def _parse_kvs(kvs: list[str], defaults: dict) -> dict:
    """Parse ['x=1.0', 'z=80'] into a dict, applying defaults for missing keys."""
    d = dict(defaults)
    for kv in kvs:
        k, v = kv.split("=", 1)
        d[k.strip()] = float(v)
    for k in d:
        if k not in defaults:
            raise ValueError(f"Unknown key '{k}'. Valid keys: {sorted(defaults)}")
    return d


def _print_status(x: float, y: float, z: float, settings: OverlaySettings) -> None:
    sx, sy = _compute_shift_pct(x, y, z, settings)
    tag = _shift_tag(sx, sy)
    print(
        f"fake_tracker: x={x:+.2f} y={y:+.2f} z={z:.2f}"
        f" → shiftX={sx:.2f}% shiftY={sy:.2f}% [{tag}]",
        flush=True,
    )


# ------------------------------------------------------------------- modes --

def _write_loop(gen: Callable[[], tuple[float, float, float]]) -> None:
    """Run the write loop. gen is called every tick and must return (x, y, z). Loop runs until KeyboardInterrupt."""
    with SharedMemoryWriter() as w:
        last_print = 0.0
        last_settings_read = time.monotonic() - 2.0  # force immediate first read
        settings = _read_settings()
        try:
            while True:
                result = gen()
                x, y, z = result
                w.write(x=x, y=y, z=z)
                now = time.monotonic()
                if now - last_settings_read >= 1.0:
                    settings = _read_settings()
                    last_settings_read = now
                if now - last_print >= _STATUS_INTERVAL_S:
                    _print_status(x, y, z, settings)
                    last_print = now
                time.sleep(1 / _WRITE_HZ)
        except KeyboardInterrupt:
            pass


def _static_mode(x: float, y: float, z: float) -> None:
    if z <= 0:
        raise ValueError(f"z must be > 0, got {z}")
    print(f"fake_tracker [static]: x={x} y={y} z={z} — Ctrl+C to stop", flush=True)

    def gen() -> tuple[float, float, float]:
        return (x, y, z)

    _write_loop(gen)


def _sweep_mode(amp: float, period: float, z: float) -> None:
    if z <= 0:
        raise ValueError(f"z must be > 0, got {z}")
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period}")
    print(
        f"fake_tracker [sweep]: amp={amp} period={period}s z={z} — Ctrl+C to stop",
        flush=True,
    )
    t0 = time.monotonic()

    def gen() -> tuple[float, float, float]:
        t = time.monotonic() - t0
        return (amp * math.sin(2 * math.pi * t / period), 0.0, z)

    _write_loop(gen)


def _interactive_mode() -> None:
    """Keyboard-driven mode (Windows only — uses msvcrt)."""
    import msvcrt

    x, y, z = 0.0, 0.0, _DEFAULT_Z_CM
    print(
        "fake_tracker [interactive]: ←→=x  ↑↓=y  +/-=z  r=reset  q=quit",
        flush=True,
    )
    settings = _read_settings()

    with SharedMemoryWriter() as w:
        last_print = time.monotonic() - 1.0  # force immediate first print
        last_settings_read = time.monotonic() - 2.0  # force immediate first read
        try:
            while True:
                now = time.monotonic()
                if now - last_settings_read >= 1.0:
                    settings = _read_settings()
                    last_settings_read = now
                if now - last_print >= _STATUS_INTERVAL_S:
                    _print_status(x, y, z, settings)
                    last_print = now
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):   # extended key prefix
                        ch2 = msvcrt.getwch()
                        if ch2 == "K":   x -= _INTERACTIVE_XY_STEP   # left arrow
                        elif ch2 == "M": x += _INTERACTIVE_XY_STEP   # right arrow
                        elif ch2 == "H": y += _INTERACTIVE_XY_STEP   # up arrow
                        elif ch2 == "P": y -= _INTERACTIVE_XY_STEP   # down arrow
                    elif ch in ("+", "="):
                        z = min(z + _INTERACTIVE_Z_STEP, _INTERACTIVE_Z_MAX)
                    elif ch == "-":
                        z = max(z - _INTERACTIVE_Z_STEP, _INTERACTIVE_Z_MIN)
                    elif ch == "r":
                        x, y, z = 0.0, 0.0, _DEFAULT_Z_CM
                    elif ch in ("q", "Q", "\x03"):
                        break
                    sx, sy = _compute_shift_pct(x, y, z, settings)
                    tag = _shift_tag(sx, sy)
                    print(
                        f"  x={x:+.1f} y={y:+.1f} z={z:.1f}"
                        f" → {sx:.2f}% {sy:.2f}% [{tag}]",
                        flush=True,
                    )
                w.write(x=x, y=y, z=z)
                time.sleep(1 / _WRITE_HZ)
        except KeyboardInterrupt:
            pass


# -------------------------------------------------- original sine oscillation --

def main(duration_sec: float = 10.0) -> None:
    """Original mode: sine oscillation for duration_sec seconds."""
    t0 = time.monotonic()
    with SharedMemoryWriter() as w:
        print(f"fake_tracker: writing to G3D for {duration_sec}s", flush=True)
        while (t := time.monotonic() - t0) < duration_sec:
            x = 5.0 * math.sin(t * 2.0)
            y = 2.0 * math.cos(t * 2.0)
            z = _DEFAULT_Z_CM
            w.write(x=x, y=y, z=z)
            time.sleep(1 / _WRITE_HZ)
        print("fake_tracker: done", flush=True)


# ---------------------------------------------------------------- entrypoint --

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Glassless3D fake head tracker")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--static", nargs="+", metavar="KEY=VAL",
        help="Hold fixed values forever: x=0 y=0 z=60",
    )
    group.add_argument(
        "--sweep", nargs="+", metavar="KEY=VAL",
        help="Sine sweep on X: amp=10 period=4 z=60",
    )
    group.add_argument(
        "--interactive", action="store_true",
        help="Arrow keys control x/y, +/- control z, r=reset, q=quit",
    )
    parser.add_argument(
        "duration", nargs="?", type=float, default=10.0,
        help="Duration in seconds for default sine mode (default: 10)",
    )
    args = parser.parse_args()

    if args.static is not None:
        kv = _parse_kvs(args.static, {"x": 0.0, "y": 0.0, "z": _DEFAULT_Z_CM})
        _static_mode(kv["x"], kv["y"], kv["z"])
    elif args.sweep is not None:
        kv = _parse_kvs(args.sweep, {"amp": 10.0, "period": 4.0, "z": _DEFAULT_Z_CM})
        _sweep_mode(kv["amp"], kv["period"], kv["z"])
    elif args.interactive:
        _interactive_mode()
    else:
        main(args.duration)
