"""Cooperative cancellation for interactive calibration subprocesses."""
from __future__ import annotations
import os
from pathlib import Path

CANCEL_ENV = "G3D_CALIBRATION_CANCEL_FILE"


class CalibrationCancelled(InterruptedError):
    pass


def check_cancelled() -> None:
    path = os.environ.get(CANCEL_ENV)
    if path and Path(path).exists():
        raise CalibrationCancelled("Calibration was cancelled by its controller")
