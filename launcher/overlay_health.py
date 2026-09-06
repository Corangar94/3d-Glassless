"""Clock-injected, per-child progress admission for the native overlay.

A process handle, a recently touched file, and a completed inference are each
insufficient evidence of usable output. Only the expected child's advancing
summary and accepted depth publication can establish health.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable, Protocol


class Summary(Protocol):
    frame_count: int
    depth_total: int
    depth_hz: int
    depth_age_ms: int | None
    depth_published: int | None
    instance_id: str | None
    has_frame: bool
    capture_state: str | None
    capture_reason: str | None


def coherent_idle_scene(summary: Summary) -> bool:
    revision = getattr(summary, "capture_revision", None)
    poll_age = getattr(summary, "capture_poll_age_ms", None)
    return (
        summary.capture_state == "running" and summary.has_frame
        and isinstance(revision, int) and revision > 0
        and getattr(summary, "depth_revision", None) == revision
        and isinstance(poll_age, int) and 0 <= poll_age <= 1000
        and getattr(summary, "depth_pending", None) is False
        and (getattr(summary, "depth_failures", None) or 0) == 0
        and (summary.depth_published or 0) > 0
    )


@dataclass(frozen=True)
class HealthDecision:
    current: bool = False
    healthy: bool = False
    restart_reason: str | None = None


class OverlayProgressMonitor:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        startup_timeout_s: float = 30.0,
        heartbeat_timeout_s: float = 5.0,
        depth_timeout_s: float = 5.0,
    ) -> None:
        self._clock = clock
        self.startup_timeout_s = startup_timeout_s
        self.heartbeat_timeout_s = heartbeat_timeout_s
        self.depth_timeout_s = depth_timeout_s
        self.reset()

    def reset(self, instance_id: str | None = None) -> None:
        now = self._clock()
        self._instance = instance_id
        self._started = now
        self._heartbeat = now
        self._depth_progress = now
        self._frame: int | None = None
        self._depth: int | None = None

    def observe(self, summary: Summary | None, instance_id: str | None) -> HealthDecision:
        now = self._clock()
        if instance_id != self._instance:
            self.reset(instance_id)
        matching = summary is not None and (
            instance_id is None or summary.instance_id == instance_id
        )
        if not matching:
            deadline = self.startup_timeout_s if self._frame is None else self.heartbeat_timeout_s
            reference = self._started if self._frame is None else self._heartbeat
            reason = "startup telemetry missing" if self._frame is None else "telemetry lost"
            return HealthDecision(restart_reason=reason if now - reference >= deadline else None)

        assert summary is not None
        advanced = self._frame is None or summary.frame_count > self._frame
        if advanced:
            self._frame = summary.frame_count
            self._heartbeat = now
        if now - self._heartbeat >= self.heartbeat_timeout_s:
            return HealthDecision(restart_reason="overlay heartbeat stalled")
        if not advanced:
            # Duplicate/regressing summaries cannot cancel pending recovery or
            # reset a stable-run episode, even during the heartbeat grace period.
            return HealthDecision()

        if summary.capture_state in {"unavailable", "rebinding", "device_recovery"}:
            # Intentional capture pauses do not consume a depth outage budget.
            # A stalled native recovery still trips the independent heartbeat.
            if not str(summary.capture_reason or "").startswith("depth_"):
                self._depth_progress = now
            elif now - self._depth_progress >= self.depth_timeout_s:
                return HealthDecision(True, False, "depth recovery stalled")
            return HealthDecision(current=True)

        if coherent_idle_scene(summary):
            # Only an advancing current-child heartbeat plus a successful capture
            # poll and exact ALL-tile scene identity can suspend depth deadlines.
            self._depth = summary.depth_published
            self._depth_progress = now
            return HealthDecision(current=True, healthy=True)

        published = summary.depth_published
        if published is None and instance_id is None:
            # Legacy diagnostic fixtures/readers may not have the new fields.
            # Launcher-owned new binaries must provide the explicit counter.
            published = summary.depth_total
        usable = (
            summary.has_frame
            and published is not None
            and published > 0
            and (summary.depth_age_ms is None and instance_id is None
                 or summary.depth_age_ms is not None and 0 <= summary.depth_age_ms <= 750)
        )
        if published is not None and self._depth is not None and published < self._depth:
            # Native capture recovery can recreate the inferencer without
            # replacing the child process. A counter reset is not progress;
            # require a subsequent advancing usable publication to recover.
            self._depth = published
            return HealthDecision(current=True)
        depth_advanced = published is not None and (
            self._depth is None or published > self._depth
        )
        if usable and depth_advanced:
            self._depth = published
            self._depth_progress = now
            return HealthDecision(current=True, healthy=True)
        if now - self._depth_progress >= self.depth_timeout_s:
            return HealthDecision(True, False, "accepted depth stalled")
        return HealthDecision(current=True)
