from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from tracker.backend_factory import (
    ConfiguredBackendFailoverPolicy,
    create_face_tracker,
    parse_backend_failover_policy,
)
from tracker.backend_failover import (
    AutoFailoverFaceTracker,
    BackendFailoverPolicy,
)
from tracker.mediapipe_runtime_policy import MediaPipeRuntimePolicy


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


class _Tracker:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)
        self.closed = False

    def process_frame(self, _frame, capture_timestamp_ms=None):
        return None

    def close(self) -> None:
        self.closed = True


def _module_with(tracker_class):
    return SimpleNamespace(FaceTracker=tracker_class)


def _custom_tracking_config() -> dict[str, object]:
    return {
        "backend_failover": {
            "retry_primary_after_ms": 0,
            "max_primary_retries": 1,
            "shadow_probe_interval_ms": 100,
            "shadow_probe_timeout_ms": 4_000,
            "minimum_healthy_callbacks": 4,
        },
        "mediapipe_runtime": {
            "stall_timeout_ms": 4_500,
            "max_consecutive_errors": 5,
            "max_backlog_ms": 90,
            "max_result_age_ms": 180,
            "max_consecutive_stale_results": 4,
            "stale_result_window_ms": 700,
        },
    }


def _production_like_tracker_kwargs() -> dict[str, object]:
    # tracker.main still includes these two legacy-safe defaults in its base
    # constructor mapping. The configured composite policy overrides them for
    # MediaPipe and removes them from OpenCV construction.
    return {
        "real_ipd_cm": 6.3,
        "async_stall_timeout_ms": 5_000,
        "async_max_consecutive_errors": 3,
    }


def _expected_runtime_kwargs() -> dict[str, int]:
    return {
        "async_stall_timeout_ms": 4_500,
        "async_max_consecutive_errors": 5,
        "async_max_backlog_ms": 90,
        "async_max_result_age_ms": 180,
        "async_max_consecutive_stale_results": 4,
        "async_stale_result_window_ms": 700,
    }


def test_parser_remains_a_backend_failover_policy_for_existing_callers():
    policy = parse_backend_failover_policy(_custom_tracking_config())

    assert isinstance(policy, BackendFailoverPolicy)
    assert isinstance(policy, ConfiguredBackendFailoverPolicy)
    assert policy == BackendFailoverPolicy(
        retry_primary_after_ms=0,
        max_primary_retries=1,
        shadow_probe_interval_ms=100,
        shadow_probe_timeout_ms=4_000,
        minimum_healthy_callbacks=4,
    )
    assert BackendFailoverPolicy(
        retry_primary_after_ms=0,
        max_primary_retries=1,
        shadow_probe_interval_ms=100,
        shadow_probe_timeout_ms=4_000,
        minimum_healthy_callbacks=4,
    ) == policy
    assert policy.mediapipe_runtime_policy == MediaPipeRuntimePolicy(
        stall_timeout_ms=4_500,
        max_consecutive_errors=5,
        max_backlog_ms=90,
        max_result_age_ms=180,
        max_consecutive_stale_results=4,
        stale_result_window_ms=700,
    )


def test_policy_kwargs_reach_strict_mediapipe_constructor():
    policy = parse_backend_failover_policy(_custom_tracking_config())

    tracker, selected = create_face_tracker(
        "mediapipe",
        tracker_kwargs=_production_like_tracker_kwargs(),
        failover_policy=policy,
        import_module=lambda _name: _module_with(_Tracker),
    )

    assert selected == "mediapipe"
    assert tracker.kwargs == {
        "real_ipd_cm": 6.3,
        **_expected_runtime_kwargs(),
    }


def test_policy_kwargs_reach_auto_primary_and_shadow_constructors():
    media_kwargs: list[dict[str, object]] = []

    class _MediaPipeTracker(_Tracker):
        def __init__(self, **kwargs: object) -> None:
            media_kwargs.append(dict(kwargs))
            if len(media_kwargs) == 1:
                raise RuntimeError("initial MediaPipe construction failed")
            super().__init__(**kwargs)

    class _Cv2Tracker(_Tracker):
        pass

    def importer(name: str):
        return (
            _module_with(_MediaPipeTracker)
            if name == "tracker.face_tracker"
            else _module_with(_Cv2Tracker)
        )

    policy = parse_backend_failover_policy(_custom_tracking_config())
    tracker, selected = create_face_tracker(
        "auto",
        tracker_kwargs=_production_like_tracker_kwargs(),
        failover_policy=policy,
        import_module=importer,
        logger=lambda _message: None,
    )

    assert isinstance(tracker, AutoFailoverFaceTracker)
    assert selected == "cv2"
    assert tracker._active.kwargs == {"real_ipd_cm": 6.3}

    tracker.process_frame(object(), capture_timestamp_ms=1000)

    assert len(media_kwargs) == 2
    assert media_kwargs[0] == media_kwargs[1]
    assert media_kwargs[1] == {
        "real_ipd_cm": 6.3,
        **_expected_runtime_kwargs(),
    }
    assert tracker._primary_candidate is not None


def test_strict_cv2_strips_configured_mediapipe_only_limits():
    policy = parse_backend_failover_policy(_custom_tracking_config())

    tracker, selected = create_face_tracker(
        "cv2",
        tracker_kwargs=_production_like_tracker_kwargs(),
        failover_policy=policy,
        import_module=lambda _name: _module_with(_Tracker),
    )

    assert selected == "cv2"
    assert tracker.kwargs == {"real_ipd_cm": 6.3}


def test_plain_failover_policy_preserves_direct_caller_kwargs():
    tracker, _selected = create_face_tracker(
        "mediapipe",
        tracker_kwargs={
            "real_ipd_cm": 6.3,
            "async_max_backlog_ms": 77,
        },
        failover_policy=BackendFailoverPolicy(),
        import_module=lambda _name: _module_with(_Tracker),
    )

    assert tracker.kwargs == {
        "real_ipd_cm": 6.3,
        "async_max_backlog_ms": 77,
    }


def test_invalid_failover_settings_do_not_discard_valid_media_policy():
    logs: list[str] = []
    config = _custom_tracking_config()
    config["backend_failover"] = {"max_primary_retries": -1}

    policy = parse_backend_failover_policy(config, logger=logs.append)

    assert policy == BackendFailoverPolicy()
    assert policy.mediapipe_runtime_policy.max_backlog_ms == 90
    assert any("backend failover" in message for message in logs)


def test_invalid_media_policy_does_not_discard_valid_failover_settings():
    logs: list[str] = []
    config = _custom_tracking_config()
    config["mediapipe_runtime"] = {"max_backlog_ms": -1}

    policy = parse_backend_failover_policy(config, logger=logs.append)

    assert policy.retry_primary_after_ms == 0
    assert policy.shadow_probe_timeout_ms == 4_000
    assert policy.mediapipe_runtime_policy == MediaPipeRuntimePolicy()
    assert any("MediaPipe async runtime" in message for message in logs)


def test_repository_config_contains_complete_nested_policy():
    config = yaml.safe_load(_source("config.yaml"))
    tracking = config["tracking"]
    policy = parse_backend_failover_policy(tracking)

    assert policy.mediapipe_runtime_policy == MediaPipeRuntimePolicy()
    assert tracking["mediapipe_runtime"] == (
        MediaPipeRuntimePolicy().config_values()
    )
    assert "async_stall_timeout_ms" not in tracking
    assert "async_max_result_age_ms" not in tracking


def test_first_run_defaults_use_the_same_policy_object():
    wizard = _source("launcher/wizard.py")

    assert (
        "from tracker.mediapipe_runtime_policy import "
        "MediaPipeRuntimePolicy"
    ) in wizard
    assert (
        '"mediapipe_runtime": MediaPipeRuntimePolicy().config_values()'
        in wizard
    )


def test_main_still_has_one_existing_configuration_boundary():
    source = _source("tracker/main.py")

    assert source.count("parse_backend_failover_policy(trk)") == 1
    assert "failover_policy=parse_backend_failover_policy(trk)" in source


def test_frozen_package_preserves_all_runtime_gates_and_policy():
    spec = _source("Glassless3D.spec")

    for module in (
        "tracker.mediapipe_runtime_policy",
        "tracker.async_callback_order",
        "tracker.async_result_freshness",
        "tracker.pose_result_timeline",
    ):
        assert f'"{module}"' in spec
