import pytest

from tracker.evaluation import (
    PoseSample,
    classify_tracking_quality,
    compute_tracking_metrics,
)


def test_compute_tracking_metrics_reports_zero_jitter_for_static_valid_samples():
    samples = [
        PoseSample(timestamp_ms=0, x_cm=1.0, y_cm=2.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=16, x_cm=1.0, y_cm=2.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=32, x_cm=1.0, y_cm=2.0, z_cm=60.0, valid=True),
    ]

    metrics = compute_tracking_metrics(samples)

    assert metrics.sample_count == 3
    assert metrics.valid_count == 3
    assert metrics.loss_rate == 0.0
    assert metrics.jitter_cm == 0.0
    assert metrics.max_valid_gap_ms == 16


def test_compute_tracking_metrics_reports_rms_frame_to_frame_jitter():
    samples = [
        PoseSample(timestamp_ms=0, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=16, x_cm=3.0, y_cm=4.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=32, x_cm=3.0, y_cm=4.0, z_cm=72.0, valid=True),
    ]

    metrics = compute_tracking_metrics(samples)

    assert metrics.jitter_cm == pytest.approx((5.0**2 + 12.0**2) ** 0.5 / 2**0.5)


def test_compute_tracking_metrics_tracks_loss_and_reacquisition_time():
    samples = [
        PoseSample(timestamp_ms=0, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=16, valid=False),
        PoseSample(timestamp_ms=32, valid=False),
        PoseSample(timestamp_ms=48, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=64, valid=False),
        PoseSample(timestamp_ms=96, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
    ]

    metrics = compute_tracking_metrics(samples)

    assert metrics.valid_count == 3
    assert metrics.loss_rate == pytest.approx(0.5)
    assert metrics.reacquisition_count == 2
    assert metrics.avg_reacquisition_ms == pytest.approx(32.0)
    assert metrics.max_reacquisition_ms == 32


def test_compute_tracking_metrics_handles_empty_input():
    metrics = compute_tracking_metrics([])

    assert metrics.sample_count == 0
    assert metrics.valid_count == 0
    assert metrics.loss_rate == 1.0
    assert metrics.jitter_cm == 0.0


def test_classify_tracking_quality_good_warn_danger():
    good = compute_tracking_metrics([
        PoseSample(timestamp_ms=0, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=16, x_cm=0.1, y_cm=0.0, z_cm=60.0, valid=True),
    ])
    assert classify_tracking_quality(good) == "GOOD"

    warn = compute_tracking_metrics([
        PoseSample(timestamp_ms=0, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
        PoseSample(timestamp_ms=16, x_cm=0.8, y_cm=0.0, z_cm=60.0, valid=True),
    ])
    assert classify_tracking_quality(warn) == "WARN"

    danger = compute_tracking_metrics([
        PoseSample(timestamp_ms=0, valid=False),
        PoseSample(timestamp_ms=16, valid=False),
        PoseSample(timestamp_ms=32, valid=False),
        PoseSample(timestamp_ms=48, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
    ])
    assert classify_tracking_quality(danger) == "DANGER"
