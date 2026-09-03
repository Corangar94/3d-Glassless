# Explicit PoseV2 prediction-lead validity

`G3D_PoseV2` uses its final uint32 word for the producer prediction lead and bit 3 of `flags` to say whether that word is meaningful.

A numeric value of zero has two different meanings:

- the filtered pose explicitly represents the publish timestamp, so the known lead is zero; or
- the producer target is missing, behind publication, malformed, or outside the bounded transport window, so lead is unknown.

The writer must not collapse those cases into the same valid-zero packet. With signed render-time correction, an invalid zero would tell the native overlay that the producer applied no lead, allowing it to add forward extrapolation to a pose whose temporal target is actually unknown.

## Encoding

The producer now emits `PREDICTION_LEAD_VALID` only when:

- both target and publish timestamps are explicit nonzero integers;
- the target is forward from publication on the wrap-safe uint32 timeline; and
- the forward lead is no more than 1,000 ms.

An exact target equal to publication is valid and encodes `(value=0, valid=true)`. A missing target encodes `(value=0, valid=false)`. Forward targets across the uint32 uptime rollover remain valid.

The existing value-only `_prediction_lead_ms()` helper remains available for focused direct callers, but packet construction uses the full value-plus-validity result.

## Reading

The Python reader also treats the validity bit and numeric value as one contract. It clears the validity bit and exposes a zero lead when:

- the bit was not declared by the writer;
- the word is malformed; or
- the word exceeds the 1,000 ms transport bound.

This keeps diagnostics and downstream Python consumers from reporting an invalid word as a known zero. The native overlay already gates residual correction on the same flag.

## Compatibility

The shared-memory mapping name, version, 64-byte struct format, field order, and flag bit are unchanged. Older writers that leave the reserved word at zero without setting bit 3 continue to mean “lead unknown.” New writers can distinguish that from an intentional zero lead without an ABI revision.
