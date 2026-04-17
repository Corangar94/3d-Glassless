"""Import overlay depth-debug screenshots as benchmark depth frames."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

from tracker.depth_synthetic import write_depth_sequence

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp"}


def import_depth_images(source_dir: str | Path, output_dir: str | Path) -> int:
    source = Path(source_dir)
    frames = []
    for image_path in sorted(source.iterdir()):
        if image_path.suffix.lower() not in _IMAGE_EXTENSIONS:
            continue
        image = Image.open(image_path).convert("L")
        frames.append(np.asarray(image, dtype=np.float32) / 255.0)
    write_depth_sequence(output_dir, frames)
    return len(frames)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import depth-debug screenshots into .npy frames")
    parser.add_argument("source_dir", help="Directory containing depth screenshots")
    parser.add_argument("output_dir", help="Output directory for frame_XXXX.npy files")
    args = parser.parse_args(argv)

    count = import_depth_images(args.source_dir, args.output_dir)
    print(f"imported {count} depth frames to {args.output_dir}")
    return 0 if count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
