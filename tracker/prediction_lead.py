"""Encode producer prediction lead without confusing unknown with zero."""
from __future__ import annotations

from dataclasses import dataclass
import numbers


UINT32_MASK = 0xFFFF_FFFF
UINT32_HALF_RANGE = 0x8000_0000
MAX_ENCODED_PREDICTION_LEAD_MS = 1_000


@dataclass(frozen=True)
class PredictionLeadEncoding:
    """One transport value plus whether its zero/nonzero value is meaningful."""

    value_ms: int = 0
    valid: bool = False


def _wire_integer(value: object) -> int | None:
    """Return one explicit uint32 integer without silently wrapping bad input."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        return None
    parsed = int(value)
    return parsed if 0 <= parsed <= UINT32_MASK else None


def _maximum_is_valid(value: object) -> bool:
    return bool(
        not isinstance(value, bool)
        and isinstance(value, numbers.Integral)
        and 0 <= int(value) < UINT32_HALF_RANGE
    )


def encode_prediction_lead(
    prediction_target_timestamp_ms: object,
    publish_timestamp_ms: object,
    *,
    maximum_lead_ms: int = MAX_ENCODED_PREDICTION_LEAD_MS,
) -> PredictionLeadEncoding:
    """Return a bounded forward uint32 lead and an explicit validity bit.

    A value of zero can mean either "the producer target is exactly publication
    time" or "the target is unknown". The caller must transport ``valid`` with
    the value so a renderer never interprets an invalid zero as a known target.
    """
    target = _wire_integer(prediction_target_timestamp_ms)
    publish = _wire_integer(publish_timestamp_ms)
    if target in (None, 0) or publish in (None, 0):
        return PredictionLeadEncoding()
    if not _maximum_is_valid(maximum_lead_ms):
        return PredictionLeadEncoding()

    lead = (target - publish) & UINT32_MASK
    if lead >= UINT32_HALF_RANGE or lead > int(maximum_lead_ms):
        return PredictionLeadEncoding()
    return PredictionLeadEncoding(value_ms=lead, valid=True)


def sanitize_prediction_lead(
    value_ms: object,
    declared_valid: bool,
    *,
    maximum_lead_ms: int = MAX_ENCODED_PREDICTION_LEAD_MS,
) -> PredictionLeadEncoding:
    """Fail closed when a received value conflicts with its validity flag."""
    if declared_valid is not True:
        return PredictionLeadEncoding()
    value = _wire_integer(value_ms)
    if (
        value is None
        or not _maximum_is_valid(maximum_lead_ms)
        or value > int(maximum_lead_ms)
    ):
        return PredictionLeadEncoding()
    return PredictionLeadEncoding(value_ms=value, valid=True)
