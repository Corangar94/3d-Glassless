from __future__ import annotations

from enum import IntEnum
from fractions import Fraction
import math

import numpy as np
import pytest

from tracker.shared_settings_validation import (
    UINT32_MAX,
    enum_uint32,
    finite_float,
    uint32,
)


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 0.0),
        (-3, -3.0),
        (1.25, 1.25),
        (Fraction(1, 4), 0.25),
        (np.float32(0.5), 0.5),
        (np.int64(7), 7.0),
    ],
)
def test_finite_float_accepts_real_numeric_values(value, expected):
    assert finite_float(value, "value") == pytest.approx(expected)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1.0",
        b"1.0",
        None,
        complex(1.0, 0.0),
        math.nan,
        math.inf,
        -math.inf,
        np.float64(math.nan),
    ],
)
def test_finite_float_rejects_coercive_or_nonfinite_values(value):
    with pytest.raises(ValueError, match="strength_x"):
        finite_float(value, "strength_x")


class _Mode(IntEnum):
    ZERO = 0
    THREE = 3


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 0),
        (UINT32_MAX, UINT32_MAX),
        (_Mode.ZERO, 0),
        (_Mode.THREE, 3),
        (np.int32(2), 2),
        (np.uint64(UINT32_MAX), UINT32_MAX),
    ],
)
def test_uint32_accepts_true_integral_values(value, expected):
    assert uint32(value, "mode") == expected


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        1.0,
        1.5,
        np.float32(2.0),
        "1",
        "1.0",
        b"1",
        None,
        -1,
        UINT32_MAX + 1,
    ],
)
def test_uint32_rejects_lossy_or_out_of_range_values(value):
    with pytest.raises(ValueError, match="depth_mode"):
        uint32(value, "depth_mode")


@pytest.mark.parametrize("value", [0, 1, 2, 3, _Mode.THREE, np.int32(2)])
def test_enum_uint32_accepts_explicit_domain_values(value):
    assert enum_uint32(value, "depth_mode", (0, 1, 2, 3)) == int(value)


@pytest.mark.parametrize("value", [4, UINT32_MAX, 1.0, True, "3"])
def test_enum_uint32_rejects_out_of_domain_or_coercive_values(value):
    with pytest.raises(ValueError, match="depth_mode"):
        enum_uint32(value, "depth_mode", (0, 1, 2, 3))


def test_enum_error_lists_allowed_values():
    with pytest.raises(ValueError, match="0, 1, 2"):
        enum_uint32(3, "depth_curve", (0, 1, 2))


def test_error_messages_identify_the_rejected_field():
    with pytest.raises(ValueError, match="camera_fov_deg"):
        finite_float(True, "camera_fov_deg")
    with pytest.raises(ValueError, match="panel_width_px"):
        uint32(1920.5, "panel_width_px")
    with pytest.raises(ValueError, match="tracking_mode"):
        enum_uint32(2, "tracking_mode", (0, 1))
