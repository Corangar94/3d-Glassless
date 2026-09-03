from __future__ import annotations

import pytest

from tracker.prediction_lead import (
    MAX_ENCODED_PREDICTION_LEAD_MS,
    PredictionLeadEncoding,
    encode_prediction_lead,
    sanitize_prediction_lead,
)


def test_equal_target_and_publish_is_known_zero_lead():
    assert encode_prediction_lead(1000, 1000) == PredictionLeadEncoding(
        value_ms=0,
        valid=True,
    )


def test_missing_target_is_invalid_zero_not_known_zero():
    assert encode_prediction_lead(0, 1000) == PredictionLeadEncoding()
    assert encode_prediction_lead(None, 1000) == PredictionLeadEncoding()


def test_forward_lead_and_exact_maximum_are_valid():
    assert encode_prediction_lead(1080, 1000) == PredictionLeadEncoding(
        value_ms=80,
        valid=True,
    )
    assert encode_prediction_lead(
        1000 + MAX_ENCODED_PREDICTION_LEAD_MS,
        1000,
    ) == PredictionLeadEncoding(
        value_ms=MAX_ENCODED_PREDICTION_LEAD_MS,
        valid=True,
    )


def test_uint32_wrap_forward_lead_is_valid():
    assert encode_prediction_lead(0x20, 0xFFFF_FFF0) == (
        PredictionLeadEncoding(value_ms=48, valid=True)
    )


def test_backward_or_overlong_target_is_invalid():
    assert encode_prediction_lead(999, 1000) == PredictionLeadEncoding()
    assert encode_prediction_lead(
        1001 + MAX_ENCODED_PREDICTION_LEAD_MS,
        1000,
    ) == PredictionLeadEncoding()


@pytest.mark.parametrize(
    "target,publish,maximum",
    [
        (True, 1000, 1000),
        (1000, False, 1000),
        (1000.0, 1000, 1000),
        (1000, 1000.0, 1000),
        (1000, 1000, True),
        (1000, 1000, -1),
        (1000, 1000, 0x8000_0000),
    ],
)
def test_invalid_encoding_inputs_fail_closed(target, publish, maximum):
    assert encode_prediction_lead(
        target,
        publish,
        maximum_lead_ms=maximum,
    ) == PredictionLeadEncoding()


def test_sanitizer_requires_declared_validity_even_for_zero():
    assert sanitize_prediction_lead(0, False) == PredictionLeadEncoding()
    assert sanitize_prediction_lead(0, True) == PredictionLeadEncoding(
        value_ms=0,
        valid=True,
    )


def test_sanitizer_clears_out_of_range_or_malformed_values():
    assert sanitize_prediction_lead(
        MAX_ENCODED_PREDICTION_LEAD_MS + 1,
        True,
    ) == PredictionLeadEncoding()
    assert sanitize_prediction_lead(True, True) == PredictionLeadEncoding()
    assert sanitize_prediction_lead(80.0, True) == PredictionLeadEncoding()


def test_sanitizer_accepts_valid_forward_lead():
    assert sanitize_prediction_lead(80, True) == PredictionLeadEncoding(
        value_ms=80,
        valid=True,
    )
