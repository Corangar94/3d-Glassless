"""Scheduled ROI/full-frame cascade calls for the OpenCV fallback tracker."""
from __future__ import annotations

import inspect
from typing import Callable

import numpy as np

from tracker.cv2_temporal_tracker import (
    CascadeFaceDetector,
    FaceBox,
    FaceObservation,
    select_face_candidate,
)


class ScheduledCascadeFaceDetector(CascadeFaceDetector):
    """Add an explicit periodic full-frame scan to the ROI-first detector.

    ``CascadeFaceDetector`` already falls back to a full scan when an ROI scan
    misses and ``allow_full_scan`` is true. This subclass additionally supports
    a scheduled full scan even when the ROI would succeed, so a drifting track
    cannot indefinitely hide a better full-frame detection.
    """

    def detect(
        self,
        gray: np.ndarray,
        *,
        prior: FaceBox | None = None,
        allow_full_scan: bool = True,
        force_full_scan: bool = False,
    ) -> FaceObservation | None:
        candidates = []
        if prior is not None and not force_full_scan:
            candidates = self._roi_faces(gray, prior)
        if force_full_scan or (not candidates and allow_full_scan):
            candidates = self._full_faces(gray)

        selected = select_face_candidate(candidates, prior)
        if selected is None:
            return None
        clipped = selected.clipped(gray.shape[1], gray.shape[0])
        if clipped is None:
            return None
        return FaceObservation(
            clipped,
            self._eyes(gray, clipped),
            "cascade",
            1.0,
        )


class CascadeDetectorCallAdapter:
    """Resolve optional ``force_full_scan`` support once, before any frames.

    Project detectors use the new explicit keyword. Legacy injected detectors
    are kept compatible without an exception-driven retry: a forced scan is
    requested by omitting the prior, which preserves their historical API while
    ensuring they cannot remain trapped in an obsolete ROI.
    """

    def __init__(self, detector: object) -> None:
        detect = getattr(detector, "detect", None)
        if not callable(detect):
            raise TypeError("fallback detector.detect must be callable")
        self._detect: Callable[..., FaceObservation | None] = detect
        self._supports_force_full_scan = self._detect_supports_force_full_scan(
            detect
        )

    @staticmethod
    def _detect_supports_force_full_scan(detect: Callable[..., object]) -> bool:
        try:
            parameters = inspect.signature(detect).parameters
        except (TypeError, ValueError):
            return False
        parameter = parameters.get("force_full_scan")
        if parameter is not None and parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
        return any(
            item.kind is inspect.Parameter.VAR_KEYWORD
            for item in parameters.values()
        )

    @property
    def supports_force_full_scan(self) -> bool:
        return self._supports_force_full_scan

    def detect(
        self,
        gray: np.ndarray,
        *,
        prior: FaceBox | None,
        allow_full_scan: bool,
        force_full_scan: bool,
    ) -> FaceObservation | None:
        if self._supports_force_full_scan:
            return self._detect(
                gray,
                prior=prior,
                allow_full_scan=allow_full_scan,
                force_full_scan=force_full_scan,
            )
        return self._detect(
            gray,
            prior=None if force_full_scan else prior,
            allow_full_scan=True if force_full_scan else allow_full_scan,
        )
