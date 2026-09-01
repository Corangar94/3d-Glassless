from __future__ import annotations

from pathlib import Path

import yaml

from tracker.backend_factory import create_face_tracker
from tracker.mediapipe_runtime_policy import (
    MediaPipeRuntimePolicy,
    parse_mediapipe_runtime_policy,
)


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


class _Tracker:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)


def test_policy_kwargs_reach_strict_mediapipe_constructor():
    policy = MediaPipeRuntimePolicy(
        stall_timeout_ms=4_000,
        max_consecutive_errors=5,
        max_backlog_ms=90,
        max_result_age_ms=180,
        max_consecutive_stale_results=4,
        stale_result_window_ms=700,
    )

    tracker, selected = create_face_tracker(
        "mediapipe",
        tracker_kwargs={
            "real_ipd_cm": 6.3,
            **policy.tracker_kwargs(),
        },
        import_module=lambda _name: type(
            "Module",
            (),
            {"FaceTracker": _Tracker},
        )(),
    )

    assert selected == "mediapipe"
    assert tracker.kwargs["real_ipd_cm"] == 6.3
    assert tracker.kwargs["async_stall_timeout_ms"] == 4_000
    assert tracker.kwargs["async_max_consecutive_errors"] == 5
    assert tracker.kwargs["async_max_backlog_ms"] == 90
    assert tracker.kwargs["async_max_result_age_ms"] == 180
    assert tracker.kwargs["async_max_consecutive_stale_results"] == 4
    assert tracker.kwargs["async_stale_result_window_ms"] == 700


def test_tracker_main_parses_policy_once_and_expands_all_kwargs():
    source = _source("tracker/main.py")

    assert (
        "from tracker.mediapipe_runtime_policy import "
        "parse_mediapipe_runtime_policy"
    ) in source
    assert source.count("parse_mediapipe_runtime_policy(trk)") == 1
    assert "**mediapipe_runtime_policy.tracker_kwargs()," in source

    tracker_kwargs_block = source.split(
        "tracker_kwargs = {",
        1,
    )[1].split("}\n    tracker, selected_backend", 1)[0]
    assert 'trk.get("async_stall_timeout_ms"' not in tracker_kwargs_block
    assert 'trk.get("async_max_consecutive_errors"' not in tracker_kwargs_block


def test_repository_config_contains_complete_default_policy():
    config = yaml.safe_load(_source("config.yaml"))
    tracking = config["tracking"]
    policy = parse_mediapipe_runtime_policy(tracking)

    assert policy == MediaPipeRuntimePolicy()
    assert tracking["async_stall_timeout_ms"] == 5_000
    assert tracking["async_max_consecutive_errors"] == 3
    assert tracking["async_max_backlog_ms"] == 150
    assert tracking["async_max_result_age_ms"] == 250
    assert tracking["async_max_consecutive_stale_results"] == 3
    assert tracking["async_stale_result_window_ms"] == 1_000


def test_first_run_defaults_match_repository_policy():
    wizard = _source("launcher/wizard.py")

    for fragment in (
        '"async_stall_timeout_ms": 5_000',
        '"async_max_consecutive_errors": 3',
        '"async_max_backlog_ms": 150',
        '"async_max_result_age_ms": 250',
        '"async_max_consecutive_stale_results": 3',
        '"async_stale_result_window_ms": 1_000',
    ):
        assert fragment in wizard


def test_frozen_package_includes_runtime_policy_and_result_timeline():
    spec = _source("Glassless3D.spec")

    assert '"tracker.mediapipe_runtime_policy"' in spec
    assert '"tracker.pose_result_timeline"' in spec
