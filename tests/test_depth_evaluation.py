import numpy as np
import pytest

from tracker.depth_evaluation import (
    DepthStabilityMetrics,
    classify_depth_stability,
    compute_depth_stability,
)


def test_compute_depth_stability_zero_for_identical_frames():
    frames = [
        np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32),
        np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32),
    ]

    metrics = compute_depth_stability(frames)

    assert metrics.frame_count == 2
    assert metrics.mean_abs_delta == 0.0
    assert metrics.p95_abs_delta == 0.0
    assert metrics.max_abs_delta == 0.0


def test_compute_depth_stability_reports_temporal_delta_distribution():
    frames = [
        np.array([[0.0, 0.0], [0.0, 0.0]], dtype=np.float32),
        np.array([[0.0, 0.1], [0.2, 0.4]], dtype=np.float32),
    ]

    metrics = compute_depth_stability(frames)

    assert metrics.mean_abs_delta == pytest.approx(0.175)
    assert metrics.max_abs_delta == pytest.approx(0.4)
    assert metrics.p95_abs_delta == pytest.approx(0.37)


def test_compute_depth_stability_uses_mask():
    frames = [
        np.array([[0.0, 0.0], [0.0, 0.0]], dtype=np.float32),
        np.array([[1.0, 0.0], [0.0, 0.0]], dtype=np.float32),
    ]
    mask = np.array([[False, True], [True, True]])

    metrics = compute_depth_stability(frames, mask=mask)

    assert metrics.mean_abs_delta == 0.0
    assert metrics.max_abs_delta == 0.0


def test_compute_depth_stability_rejects_shape_mismatch():
    frames = [
        np.zeros((2, 2), dtype=np.float32),
        np.zeros((3, 2), dtype=np.float32),
    ]

    with pytest.raises(ValueError, match="same shape"):
        compute_depth_stability(frames)


def test_classify_depth_stability_thresholds():
    assert classify_depth_stability(
        DepthStabilityMetrics(2, 0.005, 0.02, 0.03)
    ) == "GOOD"
    assert classify_depth_stability(
        DepthStabilityMetrics(2, 0.02, 0.08, 0.10)
    ) == "WARN"
    assert classify_depth_stability(
        DepthStabilityMetrics(2, 0.08, 0.20, 0.40)
    ) == "DANGER"
