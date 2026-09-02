"""Bound MediaPipe input before RGB conversion and image allocation."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from tracker.mediapipe_runtime_policy import (
    validated_mediapipe_input_width_px,
)


@dataclass(frozen=True)
class PreparedMediaPipeFrame:
    """Prepared BGR input and the exact dimensions used by pose geometry."""

    frame_bgr: np.ndarray
    width: int
    height: int
    resized: bool
    scale: float

    @property
    def pixel_count(self) -> int:
        return self.width * self.height


def prepare_mediapipe_bgr_frame(
    frame_bgr: np.ndarray,
    max_input_width_px: object,
) -> PreparedMediaPipeFrame:
    """Return a no-upscale, aspect-preserving BGR frame for MediaPipe.

    Resizing happens before BGR-to-RGB conversion so both conversion and
    ``mp.Image`` allocation operate on the smaller buffer. A zero width cap keeps
    the direct caller's full-resolution input.
    """
    if not isinstance(frame_bgr, np.ndarray):
        raise TypeError("MediaPipe input must be a numpy array")
    if (
        frame_bgr.ndim != 3
        or frame_bgr.shape[0] <= 0
        or frame_bgr.shape[1] <= 0
        or frame_bgr.shape[2] != 3
    ):
        raise ValueError("MediaPipe input must be a non-empty 3-channel BGR frame")

    height = int(frame_bgr.shape[0])
    width = int(frame_bgr.shape[1])
    maximum_width = validated_mediapipe_input_width_px(max_input_width_px)
    if maximum_width == 0 or width <= maximum_width:
        return PreparedMediaPipeFrame(
            frame_bgr=frame_bgr,
            width=width,
            height=height,
            resized=False,
            scale=1.0,
        )

    scale = maximum_width / float(width)
    target_height = max(1, int(round(height * scale)))
    resized = cv2.resize(
        frame_bgr,
        (maximum_width, target_height),
        interpolation=cv2.INTER_AREA,
    )
    if (
        not isinstance(resized, np.ndarray)
        or resized.ndim != 3
        or resized.shape != (target_height, maximum_width, 3)
    ):
        raise ValueError("MediaPipe input resize returned an invalid frame")
    return PreparedMediaPipeFrame(
        frame_bgr=resized,
        width=maximum_width,
        height=target_height,
        resized=True,
        scale=scale,
    )
