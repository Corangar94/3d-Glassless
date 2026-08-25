"""Software reference model and deterministic virtual-window validation scene."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np


@dataclass(frozen=True)
class ParallaxSettings:
    screen_width_cm: float = 60.0
    screen_height_cm: float = 34.0
    head_distance_cm: float = 60.0
    virtual_depth_cm: float = 30.0
    focus_plane_cm: float = 10.0
    strength_x: float = 1.0
    strength_y: float = 0.4

    def __post_init__(self) -> None:
        values = (
            self.screen_width_cm,
            self.screen_height_cm,
            self.head_distance_cm,
            self.virtual_depth_cm,
            self.focus_plane_cm,
            self.strength_x,
            self.strength_y,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("parallax settings must be finite")
        if self.screen_width_cm <= 0.0 or self.screen_height_cm <= 0.0:
            raise ValueError("screen dimensions must be positive")
        if self.head_distance_cm <= 0.0:
            raise ValueError("head distance must be positive")
        if self.virtual_depth_cm < 0.0 or self.focus_plane_cm < 0.0:
            raise ValueError("virtual depth and focus plane cannot be negative")


@dataclass(frozen=True)
class ParallaxGateResult:
    passed: bool
    focus_depth: float
    shallow_shift_x: float
    focus_shift_x: float
    deep_shift_x: float
    maximum_shift_uv: float
    failures: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    def write_json(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_mapping(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def depth_curve(depth: float, mode: int = 2, gamma: float = 2.0) -> float:
    value = min(1.0, max(0.0, float(depth)))
    if mode == 1:
        return math.sqrt(value)
    if mode == 2:
        return value ** max(0.01, float(gamma))
    return value


def parallax_shift_uv(
    depth: float,
    *,
    head_x_cm: float,
    head_y_cm: float,
    settings: ParallaxSettings = ParallaxSettings(),
    depth_curve_mode: int = 0,
    depth_gamma: float = 2.0,
) -> tuple[float, float]:
    """Mirror the native shader's pinhole-through-window shift equation."""
    shaped_depth = depth_curve(depth, depth_curve_mode, depth_gamma)
    object_depth = settings.virtual_depth_cm * shaped_depth
    focus_fraction = settings.focus_plane_cm / (
        settings.head_distance_cm + settings.focus_plane_cm
    )
    object_fraction = object_depth / (
        settings.head_distance_cm + object_depth
    ) if object_depth > 0.0 else 0.0
    factor = object_fraction - focus_fraction
    return (
        (float(head_x_cm) / settings.screen_width_cm)
        * factor
        * settings.strength_x,
        (float(head_y_cm) / settings.screen_height_cm)
        * factor
        * settings.strength_y,
    )


def inverse_lookup_uv(
    base_uv: Sequence[float],
    depth: float,
    *,
    head_x_cm: float,
    head_y_cm: float,
    settings: ParallaxSettings = ParallaxSettings(),
) -> tuple[float, float]:
    shift = parallax_shift_uv(
        depth,
        head_x_cm=head_x_cm,
        head_y_cm=head_y_cm,
        settings=settings,
    )
    return float(base_uv[0]) - shift[0], float(base_uv[1]) - shift[1]


def focus_depth(settings: ParallaxSettings) -> float:
    if settings.virtual_depth_cm <= 0.0:
        return 0.0
    return min(1.0, max(0.0, settings.focus_plane_cm / settings.virtual_depth_cm))


def evaluate_parallax_gate(
    settings: ParallaxSettings = ParallaxSettings(),
) -> ParallaxGateResult:
    focus = focus_depth(settings)
    shallow = max(0.0, focus * 0.25)
    deep = min(1.0, focus + (1.0 - focus) * 0.75)
    shallow_shift = parallax_shift_uv(
        shallow,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )[0]
    focus_shift = parallax_shift_uv(
        focus,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )[0]
    deep_shift = parallax_shift_uv(
        deep,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )[0]
    sampled = [
        parallax_shift_uv(
            index / 100.0,
            head_x_cm=12.0,
            head_y_cm=8.0,
            settings=settings,
        )
        for index in range(101)
    ]
    maximum_shift = max(math.hypot(x, y) for x, y in sampled)
    failures: list[str] = []
    if settings.focus_plane_cm > 0.0:
        if shallow_shift >= 0.0:
            failures.append("shallower-than-focus content does not shift oppositely")
        if abs(focus_shift) > 1e-7:
            failures.append(
                f"focus plane is not neutral ({focus_shift:.9f} UV)"
            )
    if deep_shift <= focus_shift:
        failures.append("deeper content does not increase window parallax")
    previous = -float("inf")
    for index in range(101):
        shift = parallax_shift_uv(
            index / 100.0,
            head_x_cm=8.0,
            head_y_cm=0.0,
            settings=settings,
        )[0]
        if shift + 1e-9 < previous:
            failures.append("parallax is not monotonic with virtual depth")
            break
        previous = shift
    if maximum_shift > 0.30:
        failures.append(
            f"reference motion exceeds safe software bound ({maximum_shift:.3f} UV)"
        )
    return ParallaxGateResult(
        passed=not failures,
        focus_depth=focus,
        shallow_shift_x=shallow_shift,
        focus_shift_x=focus_shift,
        deep_shift_x=deep_shift,
        maximum_shift_uv=maximum_shift,
        failures=tuple(failures),
    )


def _checkerboard(width: int, height: int, tile: int = 48) -> np.ndarray:
    yy, xx = np.indices((height, width))
    cells = ((xx // tile + yy // tile) % 2).astype(np.uint8)
    dark = np.array((36, 42, 52), dtype=np.uint8)
    light = np.array((86, 104, 128), dtype=np.uint8)
    return np.where(cells[..., None] == 0, dark, light)


def _shift_image(image: np.ndarray, shift_x: float, shift_y: float) -> np.ndarray:
    matrix = np.array(((1.0, 0.0, shift_x), (0.0, 1.0, shift_y)), dtype=np.float32)
    return cv2.warpAffine(
        image,
        matrix,
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    )


def render_validation_frame(
    *,
    width: int = 1280,
    height: int = 720,
    head_x_cm: float = 0.0,
    head_y_cm: float = 0.0,
    settings: ParallaxSettings = ParallaxSettings(),
) -> np.ndarray:
    """Render deterministic near/focus/far layers using the reference math."""
    if width < 320 or height < 180:
        raise ValueError("validation frame must be at least 320x180")
    focus = focus_depth(settings)
    layers = (
        (0.92, _checkerboard(width, height)),
        (focus, np.zeros((height, width, 3), dtype=np.uint8)),
        (max(0.02, focus * 0.22), np.zeros((height, width, 3), dtype=np.uint8)),
    )
    cv2.rectangle(
        layers[1][1],
        (width // 4, height // 4),
        (3 * width // 4, 3 * height // 4),
        (64, 170, 230),
        -1,
    )
    cv2.putText(
        layers[1][1],
        "FOCUS PLANE",
        (width // 3, height // 2),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.7, width / 1500.0),
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    cv2.circle(
        layers[2][1],
        (width // 2, height // 2),
        min(width, height) // 7,
        (70, 80, 245),
        -1,
        cv2.LINE_AA,
    )
    cv2.putText(
        layers[2][1],
        "NEAR",
        (width // 2 - 58, height // 2 + 12),
        cv2.FONT_HERSHEY_SIMPLEX,
        max(0.7, width / 1500.0),
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    output = np.zeros((height, width, 3), dtype=np.uint8)
    for depth, layer in layers:
        shift_uv = parallax_shift_uv(
            depth,
            head_x_cm=head_x_cm,
            head_y_cm=head_y_cm,
            settings=settings,
        )
        # The displayed layer moves with the projected virtual point; the
        # native inverse lookup samples the source in the opposite direction.
        shifted = _shift_image(
            layer,
            shift_uv[0] * width,
            shift_uv[1] * height,
        )
        mask = np.any(shifted != 0, axis=2)
        output[mask] = shifted[mask]
    cv2.putText(
        output,
        f"head=({head_x_cm:+.1f},{head_y_cm:+.1f}) cm",
        (24, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return output


def write_validation_sequence(
    directory: str | Path,
    *,
    head_positions_cm: Iterable[tuple[float, float]] = (
        (-10.0, 0.0),
        (-5.0, 0.0),
        (0.0, 0.0),
        (5.0, 0.0),
        (10.0, 0.0),
    ),
    settings: ParallaxSettings = ParallaxSettings(),
    width: int = 1280,
    height: int = 720,
) -> tuple[Path, ...]:
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for index, (head_x, head_y) in enumerate(head_positions_cm):
        frame = render_validation_frame(
            width=width,
            height=height,
            head_x_cm=head_x,
            head_y_cm=head_y,
            settings=settings,
        )
        path = destination / f"virtual_window_{index:02d}.png"
        if not cv2.imwrite(str(path), frame):
            raise RuntimeError(f"could not write {path}")
        outputs.append(path)
    return tuple(outputs)
