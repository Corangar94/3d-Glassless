from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_frozen_package_includes_transition_modules():
    spec = _source("Glassless3D.spec")

    assert '"tracker.backend_pose_bridge"' in spec
    assert '"tracker.backend_transition_state"' in spec


def test_failover_marks_transition_before_new_backend_output():
    source = _source("tracker/backend_failover.py")

    assert "def _begin_backend_transition(" in source
    assert "preserve_position = self._pose_bridge.begin_transition(" in source
    assert "mark_backend_transition(" in source
    assert source.index("self._begin_backend_transition(capture_timestamp_ms)") < source.index(
        "fallback_result = self._process("
    )


def test_pose_filter_distinguishes_recent_and_stale_transitions():
    source = _source("tracker/pose_filter.py")

    assert "current_backend_transition_state" in source
    assert "if transition.preserve_position:" in source
    assert "self._reset_dynamics()" in source
    assert "self._reset_state()" in source
    assert "def reset_dynamics(self) -> None:" in source


def test_readme_documents_transition_blend_and_stale_reset():
    readme = _source("README.md")

    assert "offset decays over 450 ms" in readme
    assert "source older than 750 ms is not carried forward" in readme
    assert "velocity and covariance are cleared" in readme
