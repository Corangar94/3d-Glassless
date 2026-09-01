from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np

from tracker.pose_result_timeline import PoseResultTimelineGate


def test_dynamic_magic_mock_attribute_does_not_create_a_fake_timestamp():
    result = MagicMock()
    gate = PoseResultTimelineGate()

    assert gate.filter(result) is result

    snapshot = gate.snapshot()
    assert snapshot.accepted_untimestamped_count == 1
    assert snapshot.last_timestamp_ms is None


def test_broad_getattr_wrapper_remains_an_opaque_legacy_result():
    class DynamicResult:
        def __getattr__(self, _name):
            return 1000

    result = DynamicResult()
    gate = PoseResultTimelineGate()

    assert gate.filter(result) is result
    assert gate.snapshot().accepted_untimestamped_count == 1


def test_declared_slot_timestamp_is_ordered_normally():
    class SlottedResult:
        __slots__ = ("capture_timestamp_ms",)

        def __init__(self, timestamp_ms):
            self.capture_timestamp_ms = timestamp_ms

    gate = PoseResultTimelineGate()
    first = SlottedResult(1000)
    duplicate = SlottedResult(1000)

    assert gate.filter(first) is first
    assert gate.filter(duplicate) is None
    assert gate.snapshot().duplicate_count == 1


def test_nonintegral_numpy_float_timestamp_is_malformed():
    class Result:
        capture_timestamp_ms = np.float32(1000.5)

    gate = PoseResultTimelineGate()

    assert gate.filter(Result()) is None
    assert gate.snapshot().malformed_timestamp_count == 1


def test_integral_numpy_timestamp_is_accepted():
    class Result:
        capture_timestamp_ms = np.uint32(1000)

    result = Result()
    gate = PoseResultTimelineGate()

    assert gate.filter(result) is result
    assert gate.snapshot().last_timestamp_ms == 1000
