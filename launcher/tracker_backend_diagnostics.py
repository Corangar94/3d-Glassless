"""Read and format tracker backend recovery state for launcher diagnostics."""
from __future__ import annotations

from tracker.backend_status_shared_memory import (
    TrackerBackendStatus,
    TrackerBackendStatusReader,
)


_BACKEND_STATUS_FRESH_MS = 2_000


def configured_tracker_backend(config: object) -> str:
    root = config if isinstance(config, dict) else {}
    tracking = root.get("tracking", {})
    values = tracking if isinstance(tracking, dict) else {}
    backend = str(
        values.get("tracker_backend", "auto") or "auto"
    ).strip().lower()
    return backend if backend in {"auto", "mediapipe", "cv2"} else "unknown"


def read_tracker_backend_status(
    *,
    max_age_ms: int = _BACKEND_STATUS_FRESH_MS,
) -> tuple[TrackerBackendStatus | None, bool]:
    try:
        with TrackerBackendStatusReader() as reader:
            status = reader.read()
    except Exception:
        return None, False
    if status is None:
        return None, False
    try:
        return status, status.is_fresh(max_age_ms)
    except Exception:
        return status, False


def evaluate_tracker_backend_status(
    configured_mode: str,
    status: TrackerBackendStatus | None,
    fresh: bool,
) -> tuple[list[str], list[str]]:
    """Return (problems, warnings) without making fallback itself fatal."""
    problems: list[str] = []
    warnings: list[str] = []
    configured = str(configured_mode or "unknown").strip().lower()
    if status is None:
        warnings.append("tracker backend runtime status is unavailable")
        return problems, warnings
    if not fresh:
        warnings.append(
            f"tracker backend runtime status is stale ({status.age_ms()}ms old)"
        )
        return problems, warnings

    if (
        configured != "unknown"
        and status.configured_mode != "unknown"
        and status.configured_mode != configured
    ):
        problems.append(
            "running tracker mode "
            f"{status.configured_mode} does not match configured mode "
            f"{configured}; restart tracking to apply the configuration"
        )

    if (
        configured in {"mediapipe", "cv2"}
        and status.active_backend != configured
    ):
        problems.append(
            "active tracker backend "
            f"{status.active_backend} does not match strict configured backend "
            f"{configured}"
        )

    auto_runtime = (
        configured == "auto" and status.configured_mode == "auto"
    )
    if auto_runtime and status.active_backend == "cv2":
        reason = f": {status.last_failure}" if status.last_failure else ""
        warnings.append(f"tracker is using the OpenCV fallback{reason}")

    if auto_runtime and status.candidate_active:
        age = (
            f"{status.candidate_age_ms}ms"
            if status.candidate_age_ms is not None
            else "unknown age"
        )
        warnings.append(
            "MediaPipe shadow recovery is active "
            f"({age}, probes={status.candidate_probe_count}, "
            f"healthy_callbacks={status.candidate_healthy_callbacks})"
        )
    elif (
        auto_runtime
        and status.active_backend == "cv2"
        and status.retry_in_ms is not None
    ):
        warnings.append(
            "MediaPipe shadow recovery retry is due in "
            f"{status.retry_in_ms}ms"
        )
    return problems, warnings


def tracker_backend_status_to_dict(
    status: TrackerBackendStatus | None,
    *,
    fresh: bool,
) -> dict[str, object] | None:
    if status is None:
        return None
    return {
        "configured_mode": status.configured_mode,
        "active_backend": status.active_backend,
        "fresh": bool(fresh),
        "age_ms": status.age_ms(),
        "failover_count": status.failover_count,
        "primary_retry_attempts": status.primary_retry_attempts,
        "retry_in_ms": status.retry_in_ms,
        "candidate_active": status.candidate_active,
        "candidate_age_ms": status.candidate_age_ms,
        "candidate_probe_count": status.candidate_probe_count,
        "candidate_healthy_callbacks": status.candidate_healthy_callbacks,
        "backend_transition_id": status.backend_transition_id,
        "pose_transition_active": status.pose_transition_active,
        "pose_transition_preserves_position": (
            status.pose_transition_preserves_position
        ),
        "last_failure": status.last_failure or None,
        "timestamp_ms": status.timestamp_ms,
    }


def format_tracker_backend_status(
    status: TrackerBackendStatus | None,
    *,
    fresh: bool,
) -> list[str]:
    if status is None:
        return ["Tracker backend: unavailable"]
    freshness = "fresh" if fresh else f"stale, {status.age_ms()}ms old"
    lines = [
        (
            "Tracker backend: "
            f"configured={status.configured_mode} "
            f"active={status.active_backend} ({freshness})"
        ),
        (
            "Tracker backend recovery: "
            f"failovers={status.failover_count} "
            f"primary_retries={status.primary_retry_attempts} "
            f"retry_in_ms="
            f"{status.retry_in_ms if status.retry_in_ms is not None else 'none'}"
        ),
    ]
    if status.candidate_active:
        age = (
            status.candidate_age_ms
            if status.candidate_age_ms is not None
            else "unknown"
        )
        lines.append(
            "Tracker shadow candidate: "
            f"age_ms={age} "
            f"probes={status.candidate_probe_count} "
            f"healthy_callbacks={status.candidate_healthy_callbacks}"
        )
    else:
        lines.append("Tracker shadow candidate: inactive")
    lines.append(
        "Tracker pose transition: "
        f"id={status.backend_transition_id} "
        f"active={status.pose_transition_active} "
        f"preserves_position={status.pose_transition_preserves_position}"
    )
    lines.append(
        f"Tracker backend last failure: {status.last_failure or 'none'}"
    )
    return lines


def tracker_backend_tile_text(
    status: TrackerBackendStatus | None,
    *,
    fresh: bool,
) -> tuple[str, str]:
    """Return a compact launcher label and tooltip."""
    if status is None:
        return "Unavailable", "Tracker backend status mapping is unavailable"
    if not fresh:
        return "Stale", f"Backend status is {status.age_ms()} ms old"
    if status.candidate_active:
        label = f"OpenCV + probe {status.candidate_healthy_callbacks}"
    elif status.active_backend == "cv2":
        label = "OpenCV fallback"
    elif status.active_backend == "mediapipe":
        label = "MediaPipe"
    else:
        label = "Unknown"
    tooltip_parts = [
        f"Configured: {status.configured_mode}",
        f"Active: {status.active_backend}",
        f"Failovers: {status.failover_count}",
    ]
    if status.retry_in_ms is not None:
        tooltip_parts.append(f"Retry in: {status.retry_in_ms} ms")
    if status.candidate_active:
        candidate_age = (
            f"{status.candidate_age_ms} ms"
            if status.candidate_age_ms is not None
            else "unknown"
        )
        tooltip_parts.extend(
            [
                f"Candidate age: {candidate_age}",
                f"Candidate probes: {status.candidate_probe_count}",
                (
                    "Healthy callbacks: "
                    f"{status.candidate_healthy_callbacks}"
                ),
            ]
        )
    if status.last_failure:
        tooltip_parts.append(f"Last failure: {status.last_failure}")
    return label, "\n".join(tooltip_parts)
