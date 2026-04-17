"""Synthetic depth sequences for stability benchmark fixtures."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np


def make_static_depth_sequence(
    frame_count: int,
    width: int,
    height: int,
) -> list[np.ndarray]:
    """Return repeated horizontal gradient depth frames in `[0, 1]`."""
    _validate_shape(width, height)
    if frame_count < 0:
        raise ValueError("frame_count must be non-negative")
    frame = np.tile(np.linspace(0.0, 1.0, width, dtype=np.float32), (height, 1))
    return [frame.copy() for _ in range(frame_count)]


def make_breathing_depth_sequence(
    frame_count: int,
    width: int,
    height: int,
    amplitude: float = 0.05,
) -> list[np.ndarray]:
    """Return a sequence with sinusoidal temporal depth drift."""
    frames = make_static_depth_sequence(frame_count, width, height)
    if frame_count == 0:
        return frames
    for i, frame in enumerate(frames):
        phase = i / frame_count * 2.0 * np.pi
        frame += np.float32(np.sin(phase) * amplitude)
        np.clip(frame, 0.0, 1.0, out=frame)
    return frames


def write_depth_sequence(output_dir: str | Path, frames: Sequence[np.ndarray]) -> None:
    """Write frames as `frame_0000.npy`, `frame_0001.npy`, ..."""
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames):
        np.save(path / f"frame_{i:04d}.npy", np.asarray(frame, dtype=np.float32))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic depth benchmark frames")
    parser.add_argument("output_dir")
    parser.add_argument("--mode", choices=["static", "breathing"], default="static")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--width", type=int, default=64)
    parser.add_argument("--height", type=int, default=64)
    parser.add_argument("--amplitude", type=float, default=0.05)
    args = parser.parse_args(argv)

    if args.mode == "static":
        frames = make_static_depth_sequence(args.frames, args.width, args.height)
    else:
        frames = make_breathing_depth_sequence(
            args.frames,
            args.width,
            args.height,
            amplitude=args.amplitude,
        )
    write_depth_sequence(args.output_dir, frames)
    print(f"wrote {len(frames)} depth frames to {args.output_dir}")
    return 0


def _validate_shape(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")


if __name__ == "__main__":
    raise SystemExit(main())
