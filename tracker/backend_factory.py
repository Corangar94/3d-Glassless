"""Construct strict tracker backends or the automatic runtime controller."""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib
from typing import Callable, Mapping

from tracker.backend_failover import (
    AutoFailoverFaceTracker,
    BackendFailoverPolicy,
)
from tracker.mediapipe_runtime_policy import (
    MediaPipeRuntimePolicy,
    parse_mediapipe_runtime_policy,
)


ImportModule = Callable[[str], object]
LogFunction = Callable[[str], None]


@dataclass(frozen=True, eq=False)
class ConfiguredBackendFailoverPolicy(BackendFailoverPolicy):
    """Backward-compatible failover policy carrying MediaPipe runtime limits.

    ``tracker.main`` already parses the tracking mapping once through
    ``parse_backend_failover_policy`` for every configured backend. Attaching the
    MediaPipe policy here lets strict MediaPipe, automatic primary, and shadow
    recovery construction share that one validated configuration boundary
    without changing direct callers that pass a plain ``BackendFailoverPolicy``.
    """

    mediapipe_runtime_policy: MediaPipeRuntimePolicy = field(
        default_factory=MediaPipeRuntimePolicy,
        repr=False,
    )

    def _failover_values(self) -> tuple[int, int, int, int, int]:
        return (
            self.retry_primary_after_ms,
            self.max_primary_retries,
            self.shadow_probe_interval_ms,
            self.shadow_probe_timeout_ms,
            self.minimum_healthy_callbacks,
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ConfiguredBackendFailoverPolicy):
            return (
                self._failover_values() == other._failover_values()
                and self.mediapipe_runtime_policy
                == other.mediapipe_runtime_policy
            )
        if isinstance(other, BackendFailoverPolicy):
            return self._failover_values() == (
                other.retry_primary_after_ms,
                other.max_primary_retries,
                other.shadow_probe_interval_ms,
                other.shadow_probe_timeout_ms,
                other.minimum_healthy_callbacks,
            )
        return NotImplemented

    def __hash__(self) -> int:
        # Equality with the historical base policy intentionally ignores the
        # attached MediaPipe settings, so the hash must use the same base fields.
        return hash(self._failover_values())


def parse_backend_failover_policy(
    tracking_config: object,
    *,
    logger: LogFunction = print,
) -> ConfiguredBackendFailoverPolicy:
    """Read bounded backend and MediaPipe runtime policies from one mapping."""
    tracking = tracking_config if isinstance(tracking_config, dict) else {}
    raw = tracking.get("backend_failover", {})
    values = raw if isinstance(raw, dict) else {}
    try:
        failover = BackendFailoverPolicy(
            retry_primary_after_ms=int(
                values.get("retry_primary_after_ms", 30_000)
            ),
            max_primary_retries=int(
                values.get("max_primary_retries", 1)
            ),
            shadow_probe_interval_ms=int(
                values.get("shadow_probe_interval_ms", 100)
            ),
            shadow_probe_timeout_ms=int(
                values.get("shadow_probe_timeout_ms", 5_000)
            ),
            minimum_healthy_callbacks=int(
                values.get("minimum_healthy_callbacks", 3)
            ),
        )
    except (TypeError, ValueError, OverflowError):
        logger(
            "[G3D] Invalid tracker backend failover settings; "
            "using safe defaults"
        )
        failover = BackendFailoverPolicy()

    mediapipe = parse_mediapipe_runtime_policy(
        tracking,
        logger=logger,
    )
    return ConfiguredBackendFailoverPolicy(
        retry_primary_after_ms=failover.retry_primary_after_ms,
        max_primary_retries=failover.max_primary_retries,
        shadow_probe_interval_ms=failover.shadow_probe_interval_ms,
        shadow_probe_timeout_ms=failover.shadow_probe_timeout_ms,
        minimum_healthy_callbacks=failover.minimum_healthy_callbacks,
        mediapipe_runtime_policy=mediapipe,
    )


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

    A configured policy returned by ``parse_backend_failover_policy`` also
    carries the validated MediaPipe latency limits. Plain policies supplied by
    direct callers retain the historical behavior and do not rewrite their
    explicit tracker keyword mapping.
    """
    backend_id = str(backend or "auto").strip().lower()
    if backend_id not in {"auto", "mediapipe", "cv2"}:
        raise ValueError(
            "tracking backend must be one of: auto, mediapipe, cv2"
        )
    base_kwargs = dict(tracker_kwargs)
    resolved_policy = failover_policy or BackendFailoverPolicy()
    runtime_policy = getattr(
        resolved_policy,
        "mediapipe_runtime_policy",
        None,
    )
    mediapipe_kwargs = dict(base_kwargs)
    if isinstance(runtime_policy, MediaPipeRuntimePolicy):
        mediapipe_kwargs.update(runtime_policy.tracker_kwargs())

    def make_mediapipe() -> object:
        module_name = "tracker.face_tracker"
        module = import_module(module_name)
        return _tracker_class(module, module_name)(**mediapipe_kwargs)

    def make_cv2() -> object:
        module_name = "tracker.face_tracker_cv2"
        module = import_module(module_name)
        return _tracker_class(module, module_name)(**base_kwargs)

    if backend_id == "mediapipe":
        return make_mediapipe(), "mediapipe"
    if backend_id == "cv2":
        return make_cv2(), "cv2"

    tracker = AutoFailoverFaceTracker(
        primary_factory=make_mediapipe,
        fallback_factory=make_cv2,
        policy=resolved_policy,
        logger=logger,
    )
    return tracker, tracker.active_backend
