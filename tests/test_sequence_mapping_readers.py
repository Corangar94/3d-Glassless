from __future__ import annotations

from collections import deque

from tracker import backend_status_shared_memory, pose_shared_memory, shared_memory
from tracker.backend_status_shared_memory import TrackerBackendStatusReader
from tracker.pose_shared_memory import PoseStateReader
from tracker.shared_memory import SharedMemoryReader


class FakeKernel32:
    def __init__(self, *, opens=(), maps=()) -> None:
        self.opens = deque(opens)
        self.maps = deque(maps)
        self.open_names: list[str] = []
        self.closed: list[int] = []

    def OpenFileMappingW(self, _access, _inherit, name):
        self.open_names.append(name)
        return self.opens.popleft() if self.opens else 0

    def MapViewOfFile(self, _handle, _access, _high, _low, _size):
        return self.maps.popleft() if self.maps else 0

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


def _prepare_reader(reader, name: str) -> None:
    reader._name = name
    reader._handle = 10
    reader._view = 20
    reader._seq_handle = None
    reader._seq_view = None
    reader._seq_attach_attempts_remaining = 3


def test_legacy_pose_reader_retries_after_main_mapping_is_already_attached(
    monkeypatch,
):
    reader = SharedMemoryReader.__new__(SharedMemoryReader)
    _prepare_reader(reader, "G3D_TEST")
    kernel32 = FakeKernel32(opens=(0, 30), maps=(40,))
    monkeypatch.setattr(shared_memory, "_k32", kernel32)

    reader._try_attach()
    assert reader._view == 20
    assert reader._seq_view is None
    assert reader._seq_attach_attempts_remaining == 2

    reader._try_attach()
    assert reader._seq_handle == 30
    assert reader._seq_view == 40
    assert reader._seq_attach_attempts_remaining == 0
    assert kernel32.open_names == ["G3D_TEST_Seq", "G3D_TEST_Seq"]


def test_pose_v2_reader_retries_after_main_mapping_is_already_attached(
    monkeypatch,
):
    reader = PoseStateReader.__new__(PoseStateReader)
    _prepare_reader(reader, "G3D_POSE_TEST")
    kernel32 = FakeKernel32(opens=(0, 31), maps=(41,))
    monkeypatch.setattr(pose_shared_memory, "_k32", kernel32)

    reader._try_attach()
    reader._try_attach()

    assert reader._seq_handle == 31
    assert reader._seq_view == 41
    assert reader._seq_attach_attempts_remaining == 0
    assert kernel32.open_names == [
        "G3D_POSE_TEST_Seq",
        "G3D_POSE_TEST_Seq",
    ]


def test_backend_status_reader_retries_after_main_mapping_is_attached():
    reader = TrackerBackendStatusReader.__new__(TrackerBackendStatusReader)
    _prepare_reader(reader, "G3D_BACKEND_TEST")
    kernel32 = FakeKernel32(opens=(0, 32), maps=(42,))
    reader._k32 = kernel32

    reader._try_attach()
    reader._try_attach()

    assert reader._seq_handle == 32
    assert reader._seq_view == 42
    assert reader._seq_attach_attempts_remaining == 0
    assert kernel32.open_names == [
        "G3D_BACKEND_TEST_Seq",
        "G3D_BACKEND_TEST_Seq",
    ]


def test_reader_stops_retrying_after_budget_is_exhausted(monkeypatch):
    reader = SharedMemoryReader.__new__(SharedMemoryReader)
    _prepare_reader(reader, "G3D_LEGACY_ONLY")
    reader._seq_attach_attempts_remaining = 1
    kernel32 = FakeKernel32(opens=(0, 99), maps=(100,))
    monkeypatch.setattr(shared_memory, "_k32", kernel32)

    reader._try_attach()
    reader._try_attach()
    reader._try_attach()

    assert reader._seq_view is None
    assert reader._seq_attach_attempts_remaining == 0
    assert kernel32.open_names == ["G3D_LEGACY_ONLY_Seq"]


def test_main_mapping_attachment_is_not_repeated_during_sequence_retry(
    monkeypatch,
):
    reader = SharedMemoryReader.__new__(SharedMemoryReader)
    _prepare_reader(reader, "G3D_STABLE_MAIN")
    kernel32 = FakeKernel32(opens=(0, 0, 0))
    monkeypatch.setattr(shared_memory, "_k32", kernel32)

    reader._try_attach()
    reader._try_attach()
    reader._try_attach()

    assert reader._handle == 10
    assert reader._view == 20
    assert kernel32.open_names == [
        "G3D_STABLE_MAIN_Seq",
        "G3D_STABLE_MAIN_Seq",
        "G3D_STABLE_MAIN_Seq",
    ]
