from __future__ import annotations

import math

import numpy as np

from tracker.frame_freeze_detector import FrameFreezeDetector


def _frame(value: int = 0) -> np.ndarray:
    return np.full((4, 6, 3), value, dtype=np.uint8)


def test_first_frame_establishes_identity_without_freezing():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )

    observation = detector.observe(_frame(), 10.0)

    assert observation.checked
    assert observation.supported
    assert not observation.frozen
    snapshot = detector.snapshot()
    assert snapshot.fingerprint_count == 1
    assert snapshot.freeze_episode_count == 0


def test_byte_identical_frame_freezes_at_exact_timeout():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )
    frame = _frame(7)
    detector.observe(frame, 10.0)

    observation = detector.observe(frame.copy(), 13.0)

    assert observation.checked
    assert observation.frozen
    assert observation.frozen_age_ms == 3_000
    assert observation.episode_started
    snapshot = detector.snapshot()
    assert snapshot.freeze_episode_count == 1
    assert snapshot.last_frozen_age_ms == 3_000


def test_fingerprint_is_sampled_not_computed_for_every_frame():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=3_000,
    )
    frame = _frame()

    first = detector.observe(frame, 1.0)
    skipped_a = detector.observe(frame, 1.050)
    skipped_b = detector.observe(frame, 1.249)
    second = detector.observe(frame, 1.250)

    assert first.checked
    assert not skipped_a.checked
    assert not skipped_b.checked
    assert second.checked
    assert detector.snapshot().fingerprint_count == 2


def test_changed_frame_clears_a_frozen_episode():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=100,
    )
    frozen_frame = _frame(1)
    detector.observe(frozen_frame, 1.0)
    assert detector.observe(frozen_frame.copy(), 1.1).frozen

    changed = detector.observe(_frame(2), 1.101)

    assert changed.checked
    assert not changed.frozen
    snapshot = detector.snapshot()
    assert snapshot.freeze_episode_count == 1
    assert not snapshot.frozen
    assert snapshot.last_frozen_age_ms is None


def test_frozen_state_persists_between_checks_until_change_is_sampled():
    detector = FrameFreezeDetector(
        check_interval_ms=250,
        freeze_timeout_ms=500,
    )
    frame = _frame(3)
    detector.observe(frame, 1.0)
    assert detector.observe(frame.copy(), 1.5).frozen

    between_checks = detector.observe(_frame(4), 1.6)
    sampled_change = detector.observe(_frame(4), 1.75)

    assert not between_checks.checked
    assert between_checks.frozen
    assert between_checks.frozen_age_ms == 600
    assert sampled_change.checked
    assert not sampled_change.frozen


def test_failure_reset_starts_a_new_identity_episode():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=100,
    )
    frame = _frame(5)
    detector.observe(frame, 1.0)
    assert detector.observe(frame.copy(), 1.1).frozen

    detector.reset()
    restarted = detector.observe(frame.copy(), 5.0)

    assert restarted.checked
    assert not restarted.frozen
    assert detector.snapshot().freeze_episode_count == 1


def test_zero_timeout_disables_detector_and_hashing():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=0,
    )

    for timestamp in (1.0, 2.0, 10.0):
        observation = detector.observe(_frame(), timestamp)
        assert not observation.checked
        assert not observation.frozen

    assert detector.snapshot().fingerprint_count == 0


def test_noncontiguous_array_is_ignored_without_false_freeze():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=1,
    )
    noncontiguous = _frame()[:, ::2, :]
    assert not noncontiguous.flags.c_contiguous

    first = detector.observe(noncontiguous, 1.0)
    second = detector.observe(noncontiguous, 2.0)

    assert first.checked and not first.supported
    assert second.checked and not second.supported
    assert not second.frozen
    assert detector.snapshot().fingerprint_count == 0


def test_opaque_frame_is_ignored_without_false_freeze():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=1,
    )

    first = detector.observe(object(), 1.0)
    second = detector.observe(object(), 2.0)

    assert not first.supported
    assert not second.supported
    assert not detector.snapshot().frozen


def test_shape_and_dtype_are_part_of_frame_identity():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=1,
    )
    first = np.zeros((2, 6), dtype=np.uint8)
    reshaped = np.zeros((3, 4), dtype=np.uint8)
    wider_dtype = np.zeros((2, 3), dtype=np.uint16)

    detector.observe(first, 1.0)
    assert not detector.observe(reshaped, 2.0).frozen
    assert not detector.observe(wider_dtype, 3.0).frozen


def test_backward_or_nonfinite_clock_resets_timing_without_false_freeze():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=10,
    )
    frame = _frame()
    detector.observe(frame, 10.0)

    backward = detector.observe(frame, 9.0)
    nonfinite = detector.observe(frame, math.nan)
    restarted = detector.observe(frame, 20.0)

    assert not backward.supported
    assert not backward.frozen
    assert not nonfinite.supported
    assert not nonfinite.frozen
    assert restarted.checked
    assert not restarted.frozen


def test_episode_count_increments_only_on_new_freeze_transitions():
    detector = FrameFreezeDetector(
        check_interval_ms=0,
        freeze_timeout_ms=100,
    )
    first = _frame(8)
    second = _frame(9)

    detector.observe(first, 1.0)
    assert detector.observe(first.copy(), 1.1).episode_started
    assert not detector.observe(first.copy(), 1.2).episode_started
    detector.observe(second, 1.3)
    assert detector.observe(second.copy(), 1.4).episode_started

    assert detector.snapshot().freeze_episode_count == 2
