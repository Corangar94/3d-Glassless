import pytest

from tracker.performance_evaluation import (
    FrameTimingSample,
    classify_frame_pacing,
    compute_frame_timing_metrics,
)


def test_compute_frame_timing_metrics_reports_percentiles_and_fps():
    samples = [
        FrameTimingSample(timestamp_ms=0, frame_time_ms=16.0),
        FrameTimingSample(timestamp_ms=16, frame_time_ms=17.0),
        FrameTimingSample(timestamp_ms=33, frame_time_ms=20.0),
        FrameTimingSample(timestamp_ms=53, frame_time_ms=33.0),
    ]

    metrics = compute_frame_timing_metrics(samples, target_fps=60.0)

    assert metrics.sample_count == 4
    assert metrics.avg_frame_time_ms == pytest.approx(21.5)
    assert metrics.p95_frame_time_ms == pytest.approx(31.05)
    assert metrics.max_frame_time_ms == pytest.approx(33.0)
    assert metrics.avg_fps == pytest.approx(46.51, rel=0.01)


def test_compute_frame_timing_metrics_counts_frames_over_budget():
    samples = [
        FrameTimingSample(timestamp_ms=0, frame_time_ms=16.0),
        FrameTimingSample(timestamp_ms=16, frame_time_ms=17.0),
        FrameTimingSample(timestamp_ms=33, frame_time_ms=18.0),
    ]

    metrics = compute_frame_timing_metrics(samples, target_fps=60.0)

    assert metrics.frame_budget_ms == pytest.approx(16.6667, rel=0.001)
    assert metrics.over_budget_count == 2
    assert metrics.over_budget_rate == pytest.approx(2 / 3)


def test_compute_frame_timing_metrics_handles_empty_input():
    metrics = compute_frame_timing_metrics([], target_fps=60.0)

    assert metrics.sample_count == 0
    assert metrics.avg_fps == 0.0
    assert metrics.over_budget_rate == 1.0


def test_classify_frame_pacing_thresholds():
    good = compute_frame_timing_metrics([
        FrameTimingSample(timestamp_ms=0, frame_time_ms=15.0),
        FrameTimingSample(timestamp_ms=16, frame_time_ms=16.0),
    ])
    assert classify_frame_pacing(good) == "GOOD"

    warn = compute_frame_timing_metrics([
        FrameTimingSample(timestamp_ms=0, frame_time_ms=15.0),
        FrameTimingSample(timestamp_ms=16, frame_time_ms=18.0),
        FrameTimingSample(timestamp_ms=34, frame_time_ms=18.0),
        FrameTimingSample(timestamp_ms=52, frame_time_ms=18.0),
    ])
    assert classify_frame_pacing(warn) == "WARN"

    danger = compute_frame_timing_metrics([
        FrameTimingSample(timestamp_ms=0, frame_time_ms=35.0),
        FrameTimingSample(timestamp_ms=35, frame_time_ms=40.0),
    ])
    assert classify_frame_pacing(danger) == "DANGER"
