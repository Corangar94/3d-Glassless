"""Deterministic stereo/quilt validation-card generation."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

from tracker.view_renderer import EYE_ORDER_CHOICES, STEREO_LAYOUT_CHOICES, render_backend_grid, stereo_options_from_config


@dataclass(frozen=True)
class ValidationAssets:
    image_path: Path
    depth_path: Path
    output_path: Path


def generate_validation_card(width: int = 640, height: int = 360) -> tuple[np.ndarray, np.ndarray]:
    """Return an RGB test card and matching normalized depth map.

    The card intentionally combines a horizontal depth ramp, screen-plane UI
    bars, and a near foreground occluder so stereo output reveals eye order,
    convergence, UI stability, and edge tearing in one static image.
    """
    if width < 32 or height < 24:
        raise ValueError("validation card must be at least 32x24")

    x = np.linspace(0.25, 0.95, width, dtype=np.float32)
    depth = np.repeat(x[None, :], height, axis=0)
    image = np.zeros((height, width, 3), dtype=np.uint8)

    image[..., 0] = np.linspace(40, 190, width, dtype=np.uint8)[None, :]
    image[..., 1] = 70
    image[..., 2] = np.linspace(190, 40, width, dtype=np.uint8)[None, :]

    # Screen-plane UI bands should remain stable with little/no parallax.
    top_h = max(2, height // 12)
    bottom_y = height - max(3, height // 8)
    image[:top_h, :, :] = (230, 230, 230)
    image[bottom_y:, :, :] = (25, 25, 25)
    depth[:top_h, :] = 0.0
    depth[bottom_y:, :] = 0.0

    # Foreground occluder with high-contrast vertical edges for disocclusion checks.
    occ_w = max(6, width // 5)
    occ_h = max(6, height // 3)
    x0 = (width - occ_w) // 2
    y0 = (height - occ_h) // 2
    image[y0:y0 + occ_h, x0:x0 + occ_w, :] = (245, 210, 35)
    depth[y0:y0 + occ_h, x0:x0 + occ_w] = 0.08

    # Left/right color rails make swapped eyes obvious in SBS viewers.
    rail_w = max(2, width // 32)
    image[:, :rail_w, :] = (255, 40, 40)
    image[:, -rail_w:, :] = (40, 110, 255)

    return image, depth.astype(np.float32, copy=False)


def write_validation_assets(
    output_dir: str | Path,
    backend_id: str = "stereo_autostereo",
    width: int = 640,
    height: int = 360,
    max_parallax_px: float = 8.0,
    stereo_layout: str | None = None,
    eye_order: str | None = None,
    config_path: str | Path | None = None,
) -> ValidationAssets:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    config_options = stereo_options_from_config(Path(config_path) if config_path else None)
    backend_id = config_options.get("backend_id", backend_id)
    stereo_layout = stereo_layout or config_options.get("stereo_layout", "full_sbs")
    eye_order = eye_order or config_options.get("eye_order", "left_right")
    image, depth = generate_validation_card(width=width, height=height)
    grid = render_backend_grid(
        image,
        depth,
        backend_id,
        max_parallax_px=max_parallax_px,
        stereo_layout=stereo_layout,
        eye_order=eye_order,
    )

    image_path = output / "validation_source.png"
    depth_path = output / "validation_depth.npy"
    rendered_path = output / f"{backend_id}_validation.png"
    Image.fromarray(image).save(image_path)
    np.save(depth_path, depth)
    Image.fromarray(np.asarray(grid, dtype=np.uint8)).save(rendered_path)
    return ValidationAssets(
        image_path=image_path,
        depth_path=depth_path,
        output_path=rendered_path,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate stereo/quilt validation-card assets")
    parser.add_argument("output_dir")
    parser.add_argument("--backend", default="stereo_autostereo")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=360)
    parser.add_argument("--max-parallax-px", type=float, default=8.0)
    parser.add_argument("--config", type=Path, help="config.yaml with overlay display calibration")
    parser.add_argument("--stereo-layout", choices=STEREO_LAYOUT_CHOICES)
    parser.add_argument("--eye-order", choices=EYE_ORDER_CHOICES)
    args = parser.parse_args(argv)

    result = write_validation_assets(
        args.output_dir,
        backend_id=args.backend,
        width=args.width,
        height=args.height,
        max_parallax_px=args.max_parallax_px,
        stereo_layout=args.stereo_layout,
        eye_order=args.eye_order,
        config_path=args.config,
    )
    print(f"wrote source card to {result.image_path}")
    print(f"wrote depth map to {result.depth_path}")
    print(f"wrote {args.backend} validation grid to {result.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
