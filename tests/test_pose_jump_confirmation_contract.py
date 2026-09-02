from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_tracking_loop_uses_the_wrapped_admission_accept_method():
    source = _source("tracker/main.py")

    assert "self._measurement_admission.accept(" in source
    assert "self._measurement_admission.reset()" in source


def test_packaged_entrypoints_select_stability_runtime():
    source_entry = _source("tracker/__main__.py")
    frozen_entry = _source("launcher/__main__.py")

    assert "from tracker.pose_stability_runtime import main" in source_entry
    assert "from tracker.pose_stability_runtime import main" in frozen_entry


def test_jump_confirmation_is_after_existing_measurement_admission():
    source = _source("tracker/pose_stability_runtime.py")

    admission_call = source.index("accepted = method(*args, **kwargs)")
    confirmation_call = source.index(
        "return self._confirmation.filter(accepted)"
    )
    assert admission_call < confirmation_call


def test_confirmation_runtime_preserves_latest_frame_inheritance():
    source = _source("tracker/pose_stability_runtime.py")

    assert (
        "class StableLatestFrameTrackingLoop(LatestFrameTrackingLoop):"
        in source
    )


def test_documentation_states_one_measurement_confirmation_delay():
    docs = _source("docs/POSE_JUMP_CONFIRMATION.md")

    assert "adds only one accepted-measurement interval" in docs
    assert "after** the existing freshness and confidence" in docs
    assert "before** raw translation/orientation limiting" in docs
