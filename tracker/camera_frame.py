"""Normalize OpenCV webcam output to contiguous uint8 BGR frames."""
from __future__ import annotations

import cv2
import numpy as np


class CameraFrameFormatError(ValueError):
    """A successful capture result cannot be consumed by the tracker pipeline."""


def _as_uint8(frame: np.ndarray) -> np.ndarray:
    if frame.dtype == np.uint8:
        return frame
    if frame.dtype == np.bool_:
        return frame.astype(np.uint8, copy=False) * np.uint8(255)
    if frame.dtype == np.uint16:
        # Use a deterministic full-range conversion rather than per-frame
        # normalization, which would introduce brightness pumping into camera
        # quality metrics and face tracking.
        return cv2.convertScaleAbs(frame, alpha=255.0 / 65535.0)
    raise CameraFrameFormatError(
        f"unsupported camera frame dtype {frame.dtype}"
    )


def normalize_camera_frame(frame: object) -> object:
    """Return a downstream-safe frame while preserving test/opaque adapters.

    Native OpenCV ``VideoCapture`` normally returns a NumPy array. Some camera
    backends instead produce grayscale, one-channel, BGRA, uint16, non-contiguous
    arrays, or ``cv2.UMat``. The quality monitor and both face trackers expect a
    contiguous uint8 BGR image, so normalize those known OpenCV image types at a
    single boundary.

    Opaque non-OpenCV objects are returned unchanged for compatibility with
    injected test doubles and third-party adapters. A real OpenCV backend still
    has to provide either a NumPy array or UMat.
    """
    if isinstance(frame, cv2.UMat):
        try:
            frame = frame.get()
        except Exception as error:
            raise CameraFrameFormatError(
                "camera UMat could not be transferred to host memory"
            ) from error

    if not isinstance(frame, np.ndarray):
        return frame
    if frame.size <= 0:
        raise CameraFrameFormatError("camera frame is empty")

    array = _as_uint8(frame)
    try:
        if array.ndim == 2:
            bgr = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
        elif array.ndim == 3:
            channels = int(array.shape[2])
            if channels == 1:
                bgr = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
            elif channels == 3:
                bgr = array
            elif channels == 4:
                bgr = cv2.cvtColor(array, cv2.COLOR_BGRA2BGR)
            else:
                raise CameraFrameFormatError(
                    f"unsupported camera channel count {channels}"
                )
        else:
            raise CameraFrameFormatError(
                f"unsupported camera frame rank {array.ndim}"
            )
    except CameraFrameFormatError:
        raise
    except Exception as error:
        raise CameraFrameFormatError(
            "camera frame color conversion failed"
        ) from error

    if bgr.ndim != 3 or bgr.shape[2] != 3 or bgr.size <= 0:
        raise CameraFrameFormatError(
            "camera frame normalization did not produce BGR output"
        )
    return np.ascontiguousarray(bgr)
