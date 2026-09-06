from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_measurement_gap_reset_precedes_every_axis_update():
    source = _source("tracker/pose_filter.py")
    update = source.split("    def update_pose(", 1)[1].split(
        "    def predict(",
        1,
    )[0]

    transition = update.index("self._synchronize_backend_transition()")
    gap_reset = update.index("self._reset_for_measurement_gap(capture_ms)")
    x_update = update.index("self._x.update(")
    orientation_update = update.index("self._update_orientation_axis(")

    assert transition < gap_reset < x_update < orientation_update


def test_prediction_does_not_apply_measurement_gap_reset():
    import ast
    source = _source("tracker/pose_filter.py")
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "AdaptivePoseFilter")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "predict")
    predict = ast.get_source_segment(source, method)
    assert "_reset_for_measurement_gap" not in predict
    assert "self._synchronize_backend_transition()" in predict


def test_timestamp_half_range_is_not_a_forward_gap():
    source = _source("tracker/pose_filter.py")

    assert "return None if delta >= _UINT32_HALF_RANGE else delta" in source


def test_documentation_separates_hold_policy_from_reacquisition_reset():
    docs = _source("docs/POSE_FILTER_REACQUISITION.md")

    assert "Calling `predict()` during the existing hold period does not reset" in docs
    assert "`TrackingLoop` retains ownership" in docs
    assert "at least **500 ms**" in docs
