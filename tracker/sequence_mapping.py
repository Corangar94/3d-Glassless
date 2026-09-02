"""Shared helpers for optional sequence-guarded memory mappings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


DEFAULT_SEQUENCE_ATTACH_ATTEMPTS = 8
_UINT32_MASK = 0xFFFF_FFFF


class Kernel32SequenceApi(Protocol):
    def OpenFileMappingW(
        self,
        desired_access: int,
        inherit_handle: bool,
        name: str,
    ) -> int | None:
        ...

    def MapViewOfFile(
        self,
        handle: int,
        desired_access: int,
        offset_high: int,
        offset_low: int,
        number_of_bytes: int,
    ) -> int | None:
        ...

    def CloseHandle(self, handle: int) -> object:
        ...


@dataclass(frozen=True)
class SequenceMappingAttachment:
    handle: int | None
    view: int | None
    attempts_remaining: int


@dataclass(frozen=True)
class SequenceWriteMarkers:
    writing: int
    committed: int


def next_sequence_write_markers(
    last_committed_sequence: int,
) -> SequenceWriteMarkers:
    """Return the next odd writing marker and even committed marker.

    Writers keep the last *successfully* committed even value locally. If a
    payload copy fails after the odd marker is visible, the local value remains
    unchanged and the same marker pair is reused on the next attempt. This lets
    a complete retry restore the mapping to a readable even state.
    """
    committed = int(last_committed_sequence)
    if not 0 <= committed <= _UINT32_MASK or committed & 1:
        raise ValueError(
            "last committed sequence must be an even unsigned 32-bit integer"
        )
    writing = (committed + 1) & _UINT32_MASK
    return SequenceWriteMarkers(
        writing=writing,
        committed=(writing + 1) & _UINT32_MASK,
    )


def try_attach_sequence_mapping(
    kernel32: Kernel32SequenceApi,
    mapping_name: str,
    *,
    handle: int | None,
    view: int | None,
    attempts_remaining: int,
    file_map_read: int,
    size: int = 4,
) -> SequenceMappingAttachment:
    """Attempt one optional sequence-map attachment.

    The primary data mapping may become visible just before its companion
    ``_Seq`` mapping is created. Readers therefore retry a small bounded number
    of times. The budget prevents a legacy writer with no sequence mapping from
    causing one failed Windows API call on every read forever.
    """
    remaining = max(0, int(attempts_remaining))
    if view is not None or remaining <= 0:
        return SequenceMappingAttachment(handle, view, remaining)

    remaining -= 1
    current_handle = handle
    if not current_handle:
        current_handle = kernel32.OpenFileMappingW(
            file_map_read,
            False,
            mapping_name,
        )
        if not current_handle:
            return SequenceMappingAttachment(None, None, remaining)

    mapped_view = kernel32.MapViewOfFile(
        current_handle,
        file_map_read,
        0,
        0,
        size,
    )
    if not mapped_view:
        kernel32.CloseHandle(current_handle)
        return SequenceMappingAttachment(None, None, remaining)

    return SequenceMappingAttachment(current_handle, mapped_view, 0)
