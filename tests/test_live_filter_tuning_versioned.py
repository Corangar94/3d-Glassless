from __future__ import annotations

import math

import pytest

from tracker.live_filter_tuning import (
    LiveFilterTuningController,
    LiveFilterTuningPolicy,
)


class _VersionedReader:
    def __init__(self, samples=()) -> None:
        self.samples = list(samples)
        self.fast_read_count = 0
        self.full_read_count = 0

    def read_smoothing_alpha(self):
        self.fast_read_count += 1
        if not self.samples:
            return None
        sample = self.samples.pop(0)
        if isinstance(sample, BaseException):
            raise sample
        return sample

    def read(self):
        self.full_read_count += 1
        raise AssertionError("full settings read must not run on the fast path")

    def close(self) -> None:
        pass


class _Target:
    def __init__(self) -> None:
        self.values: list[float] = []
        self.error: BaseException | None = None

    def set_measurement_noise(self, value: float) -> None:
        if self.error is not None:
            raise self.error
        self.values.append(value)


def _controller(samples, *, target=None):
    reader = _VersionedReader(samples)
    target = target or _Target()
    controller = LiveFilterTuningController(
        reader,
        target,
        LiveFilterTuningPolicy(poll_interval_s=0.0),
    )
    return reader, target, controller


def test_first_versioned_value_applies_without_full_settings_read():
    reader, target, controller = _controller([(2, 0.28)])

    assert controller.poll(1.0)

    assert target.values == [0.28]
    assert reader.fast_read_count == 1
    assert reader.full_read_count == 0
    snapshot = controller.snapshot()
    assert snapshot.version_fast_path_count == 1
    assert snapshot.last_seen_settings_version == 2


def test_unchanged_version_skips_value_processing_and_target_update():
    _reader, target, controller = _controller(
        [(2, 0.28), (2, 0.99)]
    )

    assert controller.poll(1.0)
    assert not controller.poll(2.0)

    assert target.values == [0.28]
    snapshot = controller.snapshot()
    assert snapshot.unchanged_version_count == 1
    assert snapshot.unchanged_count == 0


def test_new_version_with_same_value_commits_version_without_reapplying():
    _reader, target, controller = _controller(
        [(2, 0.28), (4, 0.28), (4, 0.99)]
    )

    assert controller.poll(1.0)
    assert not controller.poll(2.0)
    assert not controller.poll(3.0)

    assert target.values == [0.28]
    snapshot = controller.snapshot()
    assert snapshot.last_seen_settings_version == 4
    assert snapshot.unchanged_count == 1
    assert snapshot.unchanged_version_count == 1


def test_sub_epsilon_value_commits_version_then_skips_that_version():
    _reader, target, controller = _controller(
        [(2, 0.20), (4, 0.2009), (4, 0.40)]
    )

    assert controller.poll(1.0)
    assert not controller.poll(2.0)
    assert not controller.poll(3.0)

    assert target.values == pytest.approx([0.20])
    assert controller.snapshot().last_seen_settings_version == 4


def test_target_failure_does_not_commit_version_so_snapshot_can_retry():
    target = _Target()
    target.error = RuntimeError("temporary filter failure")
    _reader, target, controller = _controller(
        [(2, 0.20), (2, 0.20)],
        target=target,
    )

    assert not controller.poll(1.0)
    assert controller.snapshot().last_seen_settings_version is None

    target.error = None
    assert controller.poll(2.0)
    assert target.values == [0.20]
    assert controller.snapshot().last_seen_settings_version == 2


@pytest.mark.parametrize(
    "sample",
    [
        object(),
        (),
        (2,),
        (2, 0.2, 3),
        (True, 0.2),
        (-1, 0.2),
        (0x1_0000_0000, 0.2),
        (3, 0.2),
        (2, True),
        (2, "0.2"),
        (2, math.nan),
    ],
)
def test_malformed_or_odd_versioned_sample_fails_closed(sample):
    _reader, target, controller = _controller([sample])

    assert not controller.poll(1.0)

    assert target.values == []
    snapshot = controller.snapshot()
    assert snapshot.invalid_version_sample_count == 1
    assert snapshot.invalid_value_count == 1
    assert snapshot.last_seen_settings_version is None


def test_out_of_range_value_marks_stable_version_seen_once():
    _reader, target, controller = _controller([(2, 1.5), (2, 1.5)])

    assert not controller.poll(1.0)
    assert controller.snapshot().last_seen_settings_version == 2
    assert not controller.poll(2.0)

    snapshot = controller.snapshot()
    assert snapshot.invalid_value_count == 1
    assert snapshot.unchanged_version_count == 1
    assert target.values == []


def test_unavailable_and_read_exception_keep_existing_failure_behavior():
    _reader, target, controller = _controller(
        [None, OSError("mapping unavailable")]
    )

    assert not controller.poll(1.0)
    assert not controller.poll(2.0)

    snapshot = controller.snapshot()
    assert snapshot.unavailable_count == 1
    assert snapshot.read_error_count == 1
    assert target.values == []
