"""Display backend descriptors for the overlay-first architecture.

This is intentionally a lightweight contract, not a renderer abstraction. It
keeps product/runtime status explicit while future stereo and quilt backends are
developed behind a stable ID and capability shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

BackendStatus = Literal["primary", "experimental", "deferred"]
_BACKEND_CODES = {
    "desktop_overlay": 0,
    "stereo_autostereo": 1,
    "lightfield_quilt": 2,
}


@dataclass(frozen=True)
class DisplayBackend:
    id: str
    label: str
    status: BackendStatus
    view_count: int
    description: str


@dataclass(frozen=True)
class DisplayLayout:
    backend_id: str
    columns: int
    rows: int
    view_offsets: list[float]

    @property
    def view_count(self) -> int:
        return self.columns * self.rows


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


def build_display_layout(backend_id: str) -> DisplayLayout:
    if backend_id == "desktop_overlay":
        return DisplayLayout(
            backend_id=backend_id,
            columns=1,
            rows=1,
            view_offsets=[0.0],
        )
    if backend_id == "stereo_autostereo":
        return DisplayLayout(
            backend_id=backend_id,
            columns=2,
            rows=1,
            view_offsets=[-0.5, 0.5],
        )
    if backend_id == "lightfield_quilt":
        return DisplayLayout(
            backend_id=backend_id,
            columns=9,
            rows=5,
            view_offsets=_normalized_view_offsets(45),
        )
    raise ValueError(f"unknown display backend: {backend_id}")


def backend_code(backend_id: str) -> int:
    try:
        return _BACKEND_CODES[backend_id]
    except KeyError as e:
        raise ValueError(f"unknown display backend: {backend_id}") from e


def normalize_backend_id(value: object) -> str:
    """Return a stable backend ID from modern IDs or legacy numeric config values."""
    if isinstance(value, str):
        candidate = value.strip()
        if candidate in _BACKEND_CODES:
            return candidate
        try:
            value = int(candidate)
        except ValueError as e:
            raise ValueError(f"unknown display backend: {value}") from e
    try:
        return backend_id_from_code(int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError) as e:
        raise ValueError(f"unknown display backend: {value}") from e


def backend_id_from_code(code: int) -> str:
    for backend_id, backend_code_value in _BACKEND_CODES.items():
        if backend_code_value == code:
            return backend_id
    raise ValueError(f"unknown display backend code: {code}")


def _normalized_view_offsets(view_count: int) -> list[float]:
    if view_count <= 0:
        raise ValueError("view_count must be positive")
    if view_count == 1:
        return [0.0]
    step = 2.0 / (view_count - 1)
    return [round(-1.0 + step * index, 6) for index in range(view_count)]
