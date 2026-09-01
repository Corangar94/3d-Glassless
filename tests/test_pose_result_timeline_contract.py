from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_frame_adapter_filters_after_backend_call_and_before_return():
    source = _source("tracker/frame_processor.py")
    call = source.index("result = self.process_frame(")
    filtering = source.index("return self.result_timeline.filter(result)")
    publishing = source.index("publisher.publish(capture_timestamp_ms)")

    assert call < filtering < publishing


def test_tracking_loop_receives_adapter_output_before_validation():
    source = _source("tracker/main.py")
    measured = source.index("measured = _validated_pose(")
    process = source.index(
        "self._process_frame(frame, capture_timestamp_ms)",
        measured,
    )
    face_refresh = source.index(
        "self._last_face_ms = time.monotonic() * 1000.0",
        process,
    )
    limiter = source.index(
        "self._pose_step_limiter.limit_head_position(",
        process,
    )

    assert measured < process < face_refresh < limiter


def test_frozen_package_includes_result_timeline_gate():
    spec = _source("Glassless3D.spec")

    assert '"tracker.pose_result_timeline"' in spec


def test_documentation_states_rejected_results_use_hold_path():
    docs = _source("docs/POSE_RESULT_TIMELINE.md")

    assert "before `TrackingLoop` performs validation" in docs
    assert "enters `hold` prediction" in docs
    assert "cannot refresh face presence" in docs
