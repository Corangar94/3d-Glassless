from __future__ import annotations

from types import SimpleNamespace

import pytest

from tracker.backend_factory import (
    create_face_tracker,
    parse_backend_failover_policy,
)
from tracker.backend_failover import AutoFailoverFaceTracker


class FakeTracker:
    def __init__(self, backend: str, **kwargs: object) -> None:
        self.backend = backend
        self.kwargs = dict(kwargs)
        self.close_count = 0

    def process_frame(self, _frame, capture_timestamp_ms=None):
        return self.backend, capture_timestamp_ms

    def close(self) -> None:
        self.close_count += 1

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _modules(*, mediapipe_error: Exception | None = None):
    calls: list[str] = []

    class MediaPipeTracker(FakeTracker):
        def __init__(self, **kwargs: object) -> None:
            if mediapipe_error is not None:
                raise mediapipe_error
            super().__init__("mediapipe", **kwargs)

    class Cv2Tracker(FakeTracker):
        def __init__(self, **kwargs: object) -> None:
            super().__init__("cv2", **kwargs)

    modules = {
        "tracker.face_tracker": SimpleNamespace(FaceTracker=MediaPipeTracker),
        "tracker.face_tracker_cv2": SimpleNamespace(FaceTracker=Cv2Tracker),
    }

    def import_module(name: str):
        calls.append(name)
        return modules[name]

    return import_module, calls


def test_explicit_mediapipe_is_strict_and_does_not_import_fallback():
    importer, calls = _modules()

    tracker, selected = create_face_tracker(
        "mediapipe",
        tracker_kwargs={"camera_fov_deg": 90.0},
        import_module=importer,
    )

    assert isinstance(tracker, FakeTracker)
    assert tracker.backend == "mediapipe"
    assert selected == "mediapipe"
    assert calls == ["tracker.face_tracker"]


def test_explicit_mediapipe_constructor_failure_propagates():
    importer, calls = _modules(
        mediapipe_error=RuntimeError("model initialization failed")
    )

    with pytest.raises(RuntimeError, match="model initialization failed"):
        create_face_tracker(
            "mediapipe",
            tracker_kwargs={},
            import_module=importer,
        )

    assert calls == ["tracker.face_tracker"]


def test_explicit_cv2_never_imports_mediapipe():
    importer, calls = _modules()

    tracker, selected = create_face_tracker(
        "cv2",
        tracker_kwargs={"real_ipd_cm": 6.3},
        import_module=importer,
    )

    assert tracker.backend == "cv2"
    assert selected == "cv2"
    assert calls == ["tracker.face_tracker_cv2"]


def test_auto_starts_mediapipe_and_keeps_fallback_lazy():
    importer, calls = _modules()

    tracker, selected = create_face_tracker(
        "auto",
        tracker_kwargs={"camera_fov_deg": 80.0},
        import_module=importer,
        logger=lambda _message: None,
    )

    assert isinstance(tracker, AutoFailoverFaceTracker)
    assert selected == "mediapipe"
    assert tracker.active_backend == "mediapipe"
    assert calls == ["tracker.face_tracker"]


def test_auto_constructor_failure_starts_cv2_without_process_exit():
    importer, calls = _modules(
        mediapipe_error=RuntimeError("task creation failed")
    )

    tracker, selected = create_face_tracker(
        "auto",
        tracker_kwargs={"screen_width_cm": 60.0},
        import_module=importer,
        logger=lambda _message: None,
    )

    assert isinstance(tracker, AutoFailoverFaceTracker)
    assert selected == "cv2"
    assert tracker.active_backend == "cv2"
    assert calls == [
        "tracker.face_tracker",
        "tracker.face_tracker_cv2",
    ]


def test_auto_import_failure_starts_cv2():
    calls: list[str] = []

    class Cv2Tracker(FakeTracker):
        def __init__(self, **kwargs: object) -> None:
            super().__init__("cv2", **kwargs)

    def importer(name: str):
        calls.append(name)
        if name == "tracker.face_tracker":
            raise ImportError("mediapipe unavailable")
        return SimpleNamespace(FaceTracker=Cv2Tracker)

    tracker, selected = create_face_tracker(
        "auto",
        tracker_kwargs={},
        import_module=importer,
        logger=lambda _message: None,
    )

    assert selected == "cv2"
    assert tracker.active_backend == "cv2"
    assert calls == [
        "tracker.face_tracker",
        "tracker.face_tracker_cv2",
    ]


def test_constructor_kwargs_reach_each_backend():
    importer, _calls = _modules(
        mediapipe_error=RuntimeError("primary unavailable")
    )
    kwargs = {
        "real_ipd_cm": 6.4,
        "screen_width_cm": 60.0,
        "async_mode": True,
    }

    tracker, _selected = create_face_tracker(
        "auto",
        tracker_kwargs=kwargs,
        import_module=importer,
        logger=lambda _message: None,
    )

    active = tracker._active
    assert active.kwargs == kwargs


def test_invalid_backend_fails_before_importing_modules():
    importer, calls = _modules()

    with pytest.raises(ValueError, match="auto, mediapipe, cv2"):
        create_face_tracker(
            "unknown",
            tracker_kwargs={},
            import_module=importer,
        )

    assert calls == []


def test_valid_failover_policy_is_parsed():
    policy = parse_backend_failover_policy(
        {
            "backend_failover": {
                "retry_primary_after_ms": 12_000,
                "max_primary_retries": 2,
            }
        }
    )

    assert policy.retry_primary_after_ms == 12_000
    assert policy.max_primary_retries == 2


def test_invalid_failover_policy_uses_safe_defaults():
    logs: list[str] = []

    policy = parse_backend_failover_policy(
        {
            "backend_failover": {
                "retry_primary_after_ms": -1,
                "max_primary_retries": "bad",
            }
        },
        logger=logs.append,
    )

    assert policy.retry_primary_after_ms == 30_000
    assert policy.max_primary_retries == 1
    assert any("using safe defaults" in line for line in logs)
