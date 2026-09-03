from __future__ import annotations

import numpy as np

from tracker.frame_freeze_detector import (
    FrameFreezeDetector,
    _SAMPLE_COLUMNS,
    _SAMPLE_ROWS,
    _sample_indices,
    _sampled_frame_fingerprint,
)


def _large_frame(value: int = 0) -> np.ndarray:
    return np.full((720, 1280, 3), value, dtype=np.uint8)


def _unsampled_coordinate() -> tuple[int, int]:
    rows = set(map(int, _sample_indices(720, _SAMPLE_ROWS)))
    columns = set(map(int, _sample_indices(1280, _SAMPLE_COLUMNS)))
    row = next(index for index in range(720) if index not in rows)
    column = next(index for index in range(1280) if index not in columns)
    return row, column


def test_large_camera_frame_uses_bounded_spatial_grid():
    fingerprint = _sampled_frame_fingerprint(_large_frame())

    assert fingerprint is not None
    assert not fingerprint.exact
    assert fingerprint.signature[-5] == "spatial-grid"
    assert fingerprint.signature[-4] == _SAMPLE_ROWS
    assert fingerprint.signature[-3] == _SAMPLE_COLUMNS
    assert fingerprint.signature[-2] == _SAMPLE_ROWS * _SAMPLE_COLUMNS * 3


def test_changing_large_frames_do_not_pay_full_buffer_hash_cost():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )

    detector.observe(_large_frame(0), 1.0)
    detector.observe(_large_frame(1), 1.25)

    snapshot = detector.snapshot()
    assert snapshot.fingerprint_count == 2
    assert snapshot.full_fingerprint_count == 0
    assert not snapshot.frozen


def test_periodic_large_duplicate_is_exactly_confirmed_at_timeout():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )
    frame = _large_frame(7)
    detector.observe(frame, 10.0)
    baseline = detector.observe(frame.copy(), 10.25)

    observation = detector.observe(frame.copy(), 13.0)

    assert not baseline.frozen
    assert observation.frozen
    assert observation.frozen_age_ms == 3_000
    assert observation.episode_started
    assert detector.snapshot().full_fingerprint_count == 2


def test_sparse_large_caller_requires_two_exact_frames_before_freeze():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )
    frame = _large_frame(11)
    detector.observe(frame, 20.0)

    first_exact = detector.observe(frame.copy(), 23.0)
    confirmed = detector.observe(frame.copy(), 23.25)

    assert not first_exact.frozen
    assert confirmed.frozen
    assert confirmed.frozen_age_ms == 3_250
    assert detector.snapshot().full_fingerprint_count == 2


def test_change_outside_grid_cannot_cause_false_freeze():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )
    frame = _large_frame()
    detector.observe(frame, 30.0)
    detector.observe(frame.copy(), 30.25)

    changed = frame.copy()
    row, column = _unsampled_coordinate()
    changed[row, column, 0] = 255
    observation = detector.observe(changed, 33.0)

    assert not observation.frozen
    assert detector.snapshot().full_fingerprint_count == 2

    # The changed frame itself can become frozen only after a new full timeout.
    confirmed = detector.observe(changed.copy(), 36.0)
    assert confirmed.frozen
    assert confirmed.frozen_age_ms == 3_000


def test_change_outside_grid_clears_an_established_freeze():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )
    frame = _large_frame()
    detector.observe(frame, 40.0)
    detector.observe(frame.copy(), 40.25)
    assert detector.observe(frame.copy(), 43.0).frozen

    changed = frame.copy()
    row, column = _unsampled_coordinate()
    changed[row, column, 1] = 255
    observation = detector.observe(changed, 43.25)

    assert observation.checked
    assert not observation.frozen
    assert not detector.snapshot().frozen


def test_small_frame_full_hash_is_not_duplicated_at_confirmation():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=100,
    )
    frame = np.zeros((4, 6, 3), dtype=np.uint8)
    detector.observe(frame, 1.0)

    observation = detector.observe(frame.copy(), 1.101)

    assert observation.frozen
    snapshot = detector.snapshot()
    assert snapshot.fingerprint_count == 2
    assert snapshot.full_fingerprint_count == 2
