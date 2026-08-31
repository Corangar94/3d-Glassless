"""Construct strict tracker backends or the automatic runtime controller."""
from __future__ import annotations

import importlib
from typing import Callable, Mapping

from tracker.backend_failover import (
    AutoFailoverFaceTracker,
    BackendFailoverPolicy,
)


ImportModule = Callable[[str], object]
LogFunction = Callable[[str], None]


def parse_backend_failover_policy(
    tracking_config: object,
    *,
    logger: LogFunction = print,
) -> BackendFailoverPolicy:
    """Read bounded auto-backend recovery settings with safe defaults."""
    tracking = tracking_config if isinstance(tracking_config, dict) else {}
    raw = tracking.get("backend_failover", {})
    values = raw if isinstance(raw, dict) else {}
    try:
        return BackendFailoverPolicy(
            retry_primary_after_ms=int(
                values.get("retry_primary_after_ms", 30_000)
            ),
            max_primary_retries=int(
                values.get("max_primary_retries", 1)
            ),
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid tracker backend failover settings; "
            "using safe defaults"
        )
        return BackendFailoverPolicy()


def _tracker_class(module: object, module_name: str):
    tracker_class = getattr(module, "FaceTracker", None)
    if not callable(tracker_class):
        raise ImportError(f"{module_name}.FaceTracker is unavailable")
    return tracker_class


def create_face_tracker(
    backend: object,
    *,
    tracker_kwargs: Mapping[str, object],
    failover_policy: BackendFailoverPolicy | None = None,
    import_module: ImportModule = importlib.import_module,
    logger: LogFunction = print,
) -> tuple[object, str]:
    """Create one tracker while preserving explicit backend semantics.

    ``mediapipe`` and ``cv2`` are strict selections. ``auto`` owns both lazy
    factories: it starts with MediaPipe when possible, switches locally only on
    its explicit async-health failure, and uses the bounded shadow recovery
    policy before considering promotion back from OpenCV.
    """
    backend_id = str(backend or "auto").strip().lower()
    if backend_id not in {"auto", "mediapipe", "cv2"}:
        raise ValueError(
            "tracking backend must be one of: auto, mediapipe, cv2"
        )
    kwargs = dict(tracker_kwargs)

    def make_mediapipe() -> object:
        module_name = "tracker.face_tracker"
        module = import_module(module_name)
        return _tracker_class(module, module_name)(**kwargs)

    def make_cv2() -> object:
        module_name = "tracker.face_tracker_cv2"
        module = import_module(module_name)
        return _tracker_class(module, module_name)(**kwargs)

    if backend_id == "mediapipe":
        return make_mediapipe(), "mediapipe"
    if backend_id == "cv2":
        return make_cv2(), "cv2"

    tracker = AutoFailoverFaceTracker(
        primary_factory=make_mediapipe,
        fallback_factory=make_cv2,
        policy=failover_policy or BackendFailoverPolicy(),
        logger=logger,
    )
    return tracker, tracker.active_backend
