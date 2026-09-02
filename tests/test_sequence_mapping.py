from __future__ import annotations

from collections import deque

import pytest

from tracker.sequence_mapping import (
    DEFAULT_SEQUENCE_ATTACH_ATTEMPTS,
    next_sequence_write_markers,
    try_attach_sequence_mapping,
)


class FakeKernel32:
    def __init__(self, *, opens=(), maps=()) -> None:
        self.opens = deque(opens)
        self.maps = deque(maps)
        self.open_calls: list[tuple[int, bool, str]] = []
        self.map_calls: list[tuple[int, int, int, int, int]] = []
        self.closed: list[int] = []

    def OpenFileMappingW(self, access, inherit, name):
        self.open_calls.append((access, inherit, name))
        return self.opens.popleft() if self.opens else 0

    def MapViewOfFile(self, handle, access, high, low, size):
        self.map_calls.append((handle, access, high, low, size))
        return self.maps.popleft() if self.maps else 0

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def _attempt(kernel32, *, handle=None, view=None, remaining=8):
    return try_attach_sequence_mapping(
        kernel32,
        "G3D_Test_Seq",
        handle=handle,
        view=view,
        attempts_remaining=remaining,
        file_map_read=4,
        size=4,
    )


def test_default_retry_budget_is_small_and_bounded():
    assert DEFAULT_SEQUENCE_ATTACH_ATTEMPTS == 8


def test_existing_view_needs_no_api_calls():
    kernel32 = FakeKernel32()

    result = _attempt(kernel32, handle=10, view=20, remaining=7)

    assert result.handle == 10
    assert result.view == 20
    assert result.attempts_remaining == 7
    assert kernel32.open_calls == []
    assert kernel32.map_calls == []


def test_missing_sequence_mapping_consumes_one_attempt():
    kernel32 = FakeKernel32(opens=(0,))

    result = _attempt(kernel32, remaining=3)

    assert result.handle is None
    assert result.view is None
    assert result.attempts_remaining == 2
    assert kernel32.open_calls == [(4, False, "G3D_Test_Seq")]
    assert kernel32.map_calls == []


def test_sequence_mapping_can_appear_on_a_later_attempt():
    kernel32 = FakeKernel32(opens=(0, 30), maps=(40,))

    first = _attempt(kernel32, remaining=3)
    second = _attempt(
        kernel32,
        handle=first.handle,
        view=first.view,
        remaining=first.attempts_remaining,
    )

    assert first.attempts_remaining == 2
    assert second.handle == 30
    assert second.view == 40
    assert second.attempts_remaining == 0
    assert len(kernel32.open_calls) == 2
    assert kernel32.map_calls == [(30, 4, 0, 0, 4)]


def test_failed_view_mapping_closes_handle_before_retry():
    kernel32 = FakeKernel32(opens=(30, 31), maps=(0, 41))

    first = _attempt(kernel32, remaining=3)
    second = _attempt(
        kernel32,
        handle=first.handle,
        view=first.view,
        remaining=first.attempts_remaining,
    )

    assert first.handle is None
    assert first.view is None
    assert kernel32.closed == [30]
    assert second.handle == 31
    assert second.view == 41
    assert second.attempts_remaining == 0


def test_exhausted_budget_stops_future_windows_api_calls():
    kernel32 = FakeKernel32(opens=(0,))

    result = _attempt(kernel32, remaining=1)
    again = _attempt(
        kernel32,
        handle=result.handle,
        view=result.view,
        remaining=result.attempts_remaining,
    )

    assert result.attempts_remaining == 0
    assert again == result
    assert len(kernel32.open_calls) == 1


def test_negative_retry_budget_is_treated_as_exhausted():
    kernel32 = FakeKernel32(opens=(99,), maps=(100,))

    result = _attempt(kernel32, remaining=-5)

    assert result.attempts_remaining == 0
    assert result.handle is None
    assert result.view is None
    assert kernel32.open_calls == []


@pytest.mark.parametrize(
    ("committed", "writing", "next_committed"),
    [
        (0, 1, 2),
        (2, 3, 4),
        (100, 101, 102),
        (0xFFFF_FFFE, 0xFFFF_FFFF, 0),
    ],
)
def test_sequence_write_markers_are_odd_then_even(
    committed,
    writing,
    next_committed,
):
    markers = next_sequence_write_markers(committed)

    assert markers.writing == writing
    assert markers.committed == next_committed
    assert markers.writing & 1
    assert not (markers.committed & 1)


@pytest.mark.parametrize("committed", [-1, 1, 3, 0x1_0000_0000])
def test_sequence_write_markers_reject_invalid_committed_state(committed):
    with pytest.raises(ValueError):
        next_sequence_write_markers(committed)
