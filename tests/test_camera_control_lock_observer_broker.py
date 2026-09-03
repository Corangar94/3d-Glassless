from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from tracker import camera_control_recovery_runtime, main as tracker_main
from tracker.camera_control_recovery_runtime import (
    _CAMERA_CONTROL_LOCK_OBSERVERS,
)


@pytest.fixture(autouse=True)
def _broker_is_idle():
    assert _CAMERA_CONTROL_LOCK_OBSERVERS.active_observer_count == 0
    yield
    assert _CAMERA_CONTROL_LOCK_OBSERVERS.active_observer_count == 0


def test_nested_observers_share_one_original_hardware_call(monkeypatch):
    capture = object()
    original = MagicMock(return_value={"autofocus_locked": True})
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)
    first: list[tuple[object, dict[str, object]]] = []
    second: list[tuple[object, dict[str, object]]] = []

    with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(
        lambda cap, result: first.append((cap, dict(result)))
    ):
        with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(
            lambda cap, result: second.append((cap, dict(result)))
        ):
            result = tracker_main.try_lock_camera_controls(capture)

    assert result == {"autofocus_locked": True}
    original.assert_called_once_with(capture)
    assert first == [(capture, result)]
    assert second == [(capture, result)]
    assert tracker_main.try_lock_camera_controls is original


def test_overlapping_loop_observers_do_not_cross_record_captures(monkeypatch):
    first_capture = object()
    second_capture = object()
    original_calls: list[object] = []
    original_lock = threading.Lock()

    def original(capture):
        with original_lock:
            original_calls.append(capture)
        return {"capture": capture}

    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)
    entered = threading.Barrier(2)
    finished_call = threading.Barrier(2)
    first_records: list[object] = []
    second_records: list[object] = []

    def run(owner, records):
        def observer(capture, _result):
            if capture is owner:
                records.append(capture)

        with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(observer):
            entered.wait(timeout=2.0)
            tracker_main.try_lock_camera_controls(owner)
            finished_call.wait(timeout=2.0)

    first = threading.Thread(
        target=run,
        args=(first_capture, first_records),
    )
    second = threading.Thread(
        target=run,
        args=(second_capture, second_records),
    )
    first.start()
    second.start()
    first.join(3.0)
    second.join(3.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(map(id, original_calls)) == sorted(
        (id(first_capture), id(second_capture))
    )
    assert first_records == [first_capture]
    assert second_records == [second_capture]
    assert tracker_main.try_lock_camera_controls is original


def test_unrelated_call_delegates_to_original_without_result_changes(monkeypatch):
    capture = object()
    original_result = {"auto_exposure_locked": True}
    original = MagicMock(return_value=original_result)
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)
    observed: list[object] = []

    with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(
        lambda owned_capture, _result: observed.append(owned_capture)
        if False
        else None
    ):
        returned = tracker_main.try_lock_camera_controls(capture)

    assert returned is original_result
    original.assert_called_once_with(capture)
    assert observed == []


def test_observer_failure_cannot_turn_successful_lock_into_failure(monkeypatch):
    capture = object()
    expected = {"autofocus_locked": True}
    original = MagicMock(return_value=expected)
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)

    def fail_observer(_capture, _result):
        raise RuntimeError("observer failed")

    with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(fail_observer):
        returned = tracker_main.try_lock_camera_controls(capture)

    assert returned is expected
    original.assert_called_once_with(capture)
    assert tracker_main.try_lock_camera_controls is original


def test_non_mapping_result_is_returned_without_observer_coercion(monkeypatch):
    capture = object()
    original = MagicMock(return_value="legacy-result")
    observer = MagicMock()
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)

    with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(observer):
        returned = tracker_main.try_lock_camera_controls(capture)

    assert returned == "legacy-result"
    observer.assert_not_called()
    assert tracker_main.try_lock_camera_controls is original


def test_original_exception_propagates_and_context_restores_global(monkeypatch):
    original = MagicMock(side_effect=RuntimeError("driver failure"))
    observer = MagicMock()
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)

    with pytest.raises(RuntimeError, match="driver failure"):
        with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(observer):
            tracker_main.try_lock_camera_controls(object())

    observer.assert_not_called()
    assert tracker_main.try_lock_camera_controls is original


def test_exit_does_not_overwrite_third_party_global_replacement(monkeypatch):
    original = MagicMock(return_value={})
    replacement = MagicMock(return_value={})
    monkeypatch.setattr(tracker_main, "try_lock_camera_controls", original)

    with _CAMERA_CONTROL_LOCK_OBSERVERS.observe(
        lambda _capture, _result: None
    ):
        assert (
            tracker_main.try_lock_camera_controls
            is camera_control_recovery_runtime._dispatch_camera_control_lock
        )
        tracker_main.try_lock_camera_controls = replacement

    assert tracker_main.try_lock_camera_controls is replacement
