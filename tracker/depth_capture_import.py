"""Import overlay depth-debug screenshots as benchmark depth frames."""
from __future__ import annotations

import argparse
import json
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


def register_depth_fixture(
    fixture_root: str | Path,
    fixture_name: str,
    output_dir: str | Path,
    frame_count: int,
    width: int,
    height: int,
    description: str = "Live overlay depth-debug capture imported from screenshots.",
    expected_quality: str | None = None,
) -> None:
    root = Path(fixture_root)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"version": 1, "fixtures": []}

    output = Path(output_dir)
    try:
        directory = output.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("output_dir must be inside fixture_root when registering a fixture") from exc

    fixture = {
        "name": fixture_name,
        "directory": directory,
        "kind": "captured",
        "source": "overlay depth-debug screenshots",
        "description": description,
        "frame_count": frame_count,
        "width": width,
        "height": height,
    }
    if expected_quality:
        fixture["expected_quality"] = expected_quality

    fixtures = [item for item in manifest.get("fixtures", []) if item.get("name") != fixture_name]
    fixtures.append(fixture)
    manifest["version"] = int(manifest.get("version", 1))
    manifest["fixtures"] = fixtures
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _first_frame_shape(output_dir: str | Path) -> tuple[int, int]:
    first = next(iter(sorted(Path(output_dir).glob("*.npy"))), None)
    if first is None:
        return (0, 0)
    frame = np.load(first)
    return int(frame.shape[1]), int(frame.shape[0])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import depth-debug screenshots into .npy frames")
    parser.add_argument("source_dir", help="Directory containing depth screenshots")
    parser.add_argument("output_dir", help="Output directory for frame_XXXX.npy files")
    parser.add_argument("--fixture-root", help="Depth fixture root containing manifest.json")
    parser.add_argument("--fixture-name", help="Register imported frames in the fixture manifest with this name")
    parser.add_argument(
        "--description",
        default="Live overlay depth-debug capture imported from screenshots.",
        help="Fixture description used with --fixture-name",
    )
    parser.add_argument("--expected-quality", choices=["GOOD", "WARN", "DANGER"])
    args = parser.parse_args(argv)

    count = import_depth_images(args.source_dir, args.output_dir)
    if args.fixture_name:
        fixture_root = args.fixture_root or Path(args.output_dir).parent
        width, height = _first_frame_shape(args.output_dir)
        register_depth_fixture(
            fixture_root,
            args.fixture_name,
            args.output_dir,
            frame_count=count,
            width=width,
            height=height,
            description=args.description,
            expected_quality=args.expected_quality,
        )
    print(f"imported {count} depth frames to {args.output_dir}")
    return 0 if count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
