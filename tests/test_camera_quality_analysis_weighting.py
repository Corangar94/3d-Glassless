from __future__ import annotations

from collections import deque
import statistics

import numpy as np
import pytest

from tracker.camera_quality import CameraQualityMonitor


def _frame() -> np.ndarray:
    return np.zeros((4, 6, 3), dtype=np.uint8)


def _install_metrics(monkeypatch, values) -> None:
    pending = deque(values)
    monkeypatch.setattr(
        CameraQualityMonitor,
        "_analyze_frame",
        staticmethod(lambda _frame: pending.popleft()),
    )


def test_carried_frames_do_not_weight_image_metric_medians(monkeypatch):
    _install_metrics(
        monkeypatch,
        [
            (0.2, 0.4, 0.1, 20.0),
            (0.8, 0.0, 0.3, 80.0),
        ],
    )
    monitor = CameraQualityMonitor(
        window_size=16,
        minimum_sharpness=0.0,
        analysis_interval_ms=80,
    )
    frame = _frame()

    for timestamp in range(1000, 1080, 10):
        monitor.update(frame, timestamp)
    status = monitor.update(frame, 1080)

    assert monitor.image_analysis_count == 2
    assert status.brightness == pytest.approx(0.5)
    assert status.brightness_jitter == pytest.approx(0.3)
    assert status.sharpness == pytest.approx(50.0)
    assert status.dark_fraction == pytest.approx(0.2)
    assert status.clipped_fraction == pytest.approx(0.2)


def test_fps_still_uses_every_delivered_frame_interval(monkeypatch):
    _install_metrics(
        monkeypatch,
        [
            (0.5, 0.0, 0.0, 80.0),
            (0.6, 0.0, 0.0, 80.0),
        ],
    )
    monitor = CameraQualityMonitor(
        window_size=16,
        minimum_sharpness=0.0,
        analysis_interval_ms=80,
    )
    frame = _frame()

    monitor.update(frame, 1000)
    monitor.update(frame, 1033)
    monitor.update(frame, 1066)
    status = monitor.update(frame, 1080)

    assert monitor.image_analysis_count == 2
    assert status.fps == pytest.approx(1000.0 / 33.0)


def test_analysis_phase_does_not_change_image_statistics(monkeypatch):
    metrics = [
        (0.30, 0.05, 0.00, 40.0),
        (0.55, 0.00, 0.05, 70.0),
        (0.75, 0.00, 0.10, 100.0),
    ]

    def run(timestamps):
        _install_metrics(monkeypatch, list(metrics))
        monitor = CameraQualityMonitor(
            window_size=32,
            minimum_sharpness=0.0,
            analysis_interval_ms=80,
        )
        frame = _frame()
        status = None
        for timestamp in timestamps:
            status = monitor.update(frame, timestamp)
        assert status is not None
        return status

    dense = run(
        [
            1000,
            1010,
            1020,
            1030,
            1040,
            1050,
            1060,
            1070,
            1080,
            1090,
            1100,
            1110,
            1120,
            1130,
            1140,
            1150,
            1160,
        ]
    )
    sparse = run([1000, 1080, 1160])

    assert dense.brightness == pytest.approx(sparse.brightness)
    assert dense.brightness_jitter == pytest.approx(
        sparse.brightness_jitter
    )
    assert dense.sharpness == pytest.approx(sparse.sharpness)
    assert dense.dark_fraction == pytest.approx(sparse.dark_fraction)
    assert dense.clipped_fraction == pytest.approx(sparse.clipped_fraction)


def test_exposure_hunting_uses_distinct_analysis_values(monkeypatch):
    values = [0.50, 0.60, 0.50]
    _install_metrics(
        monkeypatch,
        [(value, 0.0, 0.0, 80.0) for value in values],
    )
    monitor = CameraQualityMonitor(
        window_size=32,
        minimum_sharpness=0.0,
        analysis_interval_ms=80,
    )
    frame = _frame()

    status = None
    for timestamp in range(1000, 1161, 10):
        status = monitor.update(frame, timestamp)

    assert status is not None
    assert status.brightness_jitter == pytest.approx(
        statistics.pstdev(values)
    )
    assert "exposure is hunting" in status.problems
