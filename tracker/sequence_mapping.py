"""Bounded attachment helper for optional shared-memory sequence mappings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


DEFAULT_SEQUENCE_ATTACH_ATTEMPTS = 8


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
