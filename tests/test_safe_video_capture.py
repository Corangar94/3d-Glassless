from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import cv2
import pytest

import tracker.main as tracker_main
from tracker.main import TrackingLoop, _open_camera
from tracker.safe_video_capture import (
    SafeVideoCapture,
    install_safe_video_capture,
)


class _ExplodingCapture:
    def __init__(self) -> None:
        self.release_calls = 0

    def isOpened(self):
        raise RuntimeError("driver state query failed")

    def release(self):
        self.release_calls += 1
        raise OSError("driver cleanup failed")

    def read(self):
        raise RuntimeError("device vanished")

    def set(self, _property_id, _value):
        raise ValueError("unsupported property")

    def get(self, _property_id):
        raise TypeError("unsupported property")

    def grab(self):
        raise RuntimeError("grab failed")

    def retrieve(self, *_args, **_kwargs):
        raise RuntimeError("retrieve failed")

    def open(self, *_args, **_kwargs):
        raise RuntimeError("open failed")

    def getBackendName(self):
        raise RuntimeError("backend name unavailable")


class _OpenedCapture:
    def __init__(self) -> None:
        self.release_calls = 0

    def isOpened(self):
        return True

    def release(self):
        self.release_calls += 1

    def read(self):
        return True, object()

    def set(self, _property_id, _value):
        return True

    def get(self, _property_id):
        return 30.0

    def getBackendName(self):
        return "TEST"


class _SettingsReader:
    def read(self):
        return None

    def close(self) -> None:
        pass


def _safe(factory, *args):
    return SafeVideoCapture(*args, _factory=factory)


def test_constructor_exception_becomes_closed_capture():
    def construct(*_args, **_kwargs):
        raise RuntimeError("camera constructor failed")

    cap = _safe(construct, 0, cv2.CAP_DSHOW)

    assert not cap.isOpened()
    assert cap.read() == (False, None)
    assert cap.set(cv2.CAP_PROP_FPS, 30.0) is False
    assert cap.get(cv2.CAP_PROP_FPS) == 0.0
    cap.release()
    assert cap.failure_summary == "construct:RuntimeError"


def test_every_driver_operation_is_converted_to_failure_values():
    native = _ExplodingCapture()
    cap = _safe(lambda *_args, **_kwargs: native, 0)

    assert not cap.isOpened()
    assert cap.read() == (False, None)
    assert not cap.grab()
    assert cap.retrieve() == (False, None)
    assert not cap.set(1, 2.0)
    assert cap.get(1) == 0.0
    assert not cap.open(0)
    assert cap.getBackendName() == ""
    cap.release()
    cap.release()

    assert native.release_calls == 1
    assert {failure.stage for failure in cap.failures} == {
        "isOpened",
        "read",
        "grab",
        "retrieve",
        "set",
        "get",
        "open",
        "getBackendName",
        "release",
    }


def test_malformed_read_and_retrieve_results_fail_closed():
    native = MagicMock()
    native.read.return_value = True
    native.retrieve.return_value = (True, object(), "unexpected")
    cap = _safe(lambda: native)

    assert cap.read() == (False, None)
    assert cap.retrieve() == (False, None)
    assert [failure.stage for failure in cap.failures] == ["read", "retrieve"]


def test_normal_capture_behavior_is_preserved():
    native = _OpenedCapture()
    cap = _safe(lambda *_args: native, 3, cv2.CAP_MSMF)

    assert cap.isOpened()
    ok, frame = cap.read()
    assert ok and frame is not None
    assert cap.set(cv2.CAP_PROP_FPS, 30.0)
    assert cap.get(cv2.CAP_PROP_FPS) == 30.0
    assert cap.getBackendName() == "TEST"
    cap.release()

    assert native.release_calls == 1
    assert cap.failures == ()


def test_install_is_idempotent_and_wraps_the_current_factory(monkeypatch):
    native = _OpenedCapture()
    factory = MagicMock(return_value=native)
    monkeypatch.setattr(cv2, "VideoCapture", factory)

    installed = install_safe_video_capture()
    installed_again = install_safe_video_capture()
    cap = cv2.VideoCapture(7, cv2.CAP_DSHOW)

    assert installed_again is installed
    assert getattr(installed, "__g3d_safe_video_capture__", False)
    factory.assert_called_once_with(7, cv2.CAP_DSHOW)
    assert cap.isOpened()


def test_open_camera_falls_through_constructor_and_state_query_exceptions():
    exploding = _ExplodingCapture()
    opened = _OpenedCapture()
    constructor_failure = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("constructor failed")
    )
    candidates = [
        _safe(constructor_failure, 0, cv2.CAP_DSHOW),
        _safe(lambda *_args: exploding, 0, cv2.CAP_MSMF),
        _safe(lambda *_args: opened, 0),
    ]
    metadata: dict[str, object] = {}

    with patch("tracker.main.cv2.VideoCapture", side_effect=candidates) as factory:
        selected = _open_camera(0, metadata=metadata)

    assert selected is candidates[-1]
    assert factory.call_args_list == [
        call(0, tracker_main.cv2.CAP_DSHOW),
        call(0, tracker_main.cv2.CAP_MSMF),
        call(0),
    ]
    assert exploding.release_calls == 1
    assert metadata["backend_name"] == "default backend"


def test_configuration_and_mode_query_exceptions_do_not_reject_open_handle():
    native = _OpenedCapture()
    native.set = MagicMock(side_effect=RuntimeError("set failed"))
    native.get = MagicMock(side_effect=RuntimeError("get failed"))
    cap = _safe(lambda *_args: native, 0)
    metadata: dict[str, object] = {}

    with patch("tracker.main.cv2.VideoCapture", return_value=cap):
        selected = _open_camera(0, width=1280, height=720, fps=30, metadata=metadata)

    assert selected is cap
    assert cap.isOpened()
    assert metadata["backend_index"] == 0
    assert {failure.stage for failure in cap.failures} == {"set", "get"}


def test_tracking_loop_recovers_when_native_read_and_release_throw():
    broken_native = _ExplodingCapture()
    recovered_native = _OpenedCapture()
    broken = _safe(lambda *_args: broken_native, 0)
    # The active session must initially look open, then fail at read time.
    broken_native.isOpened = MagicMock(return_value=True)
    recovered = _safe(lambda *_args: recovered_native, 0)
    tracker = MagicMock()
    tracker.process_frame.return_value = None
    writer = MagicMock()
    smoother = MagicMock()
    loop = TrackingLoop(
        tracker=tracker,
        writer=writer,
        smoother=smoother,
        hold_ms=0,
    )

    with (
        patch("tracker.main._open_camera", side_effect=[broken, recovered]),
        patch("tracker.main.SharedSettingsReader", return_value=_SettingsReader()),
        patch("tracker.main.time.sleep"),
    ):
        loop.run(camera_index=0, max_frames=1)

    assert broken_native.release_calls == 1
    assert tracker.reset_session.call_count == 1
    assert smoother.reset.call_count == 1
    assert tracker.process_frame.call_count == 1


def test_reconnect_module_installs_boundary_before_tracker_camera_use():
    assert getattr(tracker_main.cv2.VideoCapture, "__g3d_safe_video_capture__", False)
