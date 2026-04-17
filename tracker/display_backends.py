"""Display backend descriptors for the overlay-first architecture.

This is intentionally a lightweight contract, not a renderer abstraction. It
keeps product/runtime status explicit while future stereo and quilt backends are
developed behind a stable ID and capability shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

BackendStatus = Literal["primary", "experimental", "deferred"]


@dataclass(frozen=True)
class DisplayBackend:
    id: str
    label: str
    status: BackendStatus
    view_count: int
    description: str


class DisplayBackendRegistry:
    def __init__(self, backends: Sequence[DisplayBackend]) -> None:
        self._backends = list(backends)
        seen: set[str] = set()
        for backend in self._backends:
            if backend.id in seen:
                raise ValueError(f"duplicate backend id: {backend.id}")
            seen.add(backend.id)

    def all(self) -> list[DisplayBackend]:
        return list(self._backends)

    def default(self) -> DisplayBackend:
        for backend in self._backends:
            if backend.status == "primary":
                return backend
        raise ValueError("no primary display backend registered")

    def by_status(self, status: BackendStatus) -> list[DisplayBackend]:
        return [backend for backend in self._backends if backend.status == status]


def built_in_backends() -> list[DisplayBackend]:
    return [
        DisplayBackend(
            id="desktop_overlay",
            label="Desktop Overlay",
            status="primary",
            view_count=1,
            description="Standalone Windows desktop overlay with head-tracked depth parallax.",
        ),
        DisplayBackend(
            id="stereo_autostereo",
            label="Stereo Autostereo",
            status="experimental",
            view_count=2,
            description="Future two-view output path for tracked lenticular displays.",
        ),
        DisplayBackend(
            id="lightfield_quilt",
            label="Light-field Quilt",
            status="experimental",
            view_count=45,
            description="Future multiview/quilt output path for light-field displays.",
        ),
    ]
