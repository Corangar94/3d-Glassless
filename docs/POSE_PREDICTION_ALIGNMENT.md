# Render-time pose prediction alignment

The Python pose filter publishes a position that may already be projected beyond its publish timestamp. `prediction_lead_ms` records how far the producer target is ahead of publication. The native overlay then compares that target with the age of the packet when it renders.

## Signed residual correction

The required temporal correction is:

```text
render delay since publication - producer prediction lead
```

A positive result means the packet has aged beyond the producer target and needs a small forward extrapolation. A negative result means the producer target is still ahead of the current render time and the pose must be rewound along its published velocity.

Previously, negative residuals were clamped to zero. A configured producer prediction horizon could therefore leave the displayed viewpoint ahead of the viewer until wall-clock delay caught up, producing lead and overshoot during reversals.

The native correction supports both directions:

- forward correction is limited to 20 ms;
- rewind correction is limited to 80 ms, matching the maximum producer prediction window;
- combined X/Y correction is capped at 2 cm;
- Z correction is capped at 2.5 cm;
- the existing unsigned `native_residual_ms` diagnostic remains the correction magnitude, while the result also records the signed residual and whether rewind was applied.

The position clamps are symmetric, so forward and rewind cannot create a large spatial correction.

## Physical velocity boundary

A displacement cap alone does not keep very short residual corrections proportional to time. For example, a bad 5,000 cm/s velocity multiplied by only 1 ms produces 5 cm and immediately saturates the old 2 cm displacement cap. The resulting visible correction is then as large as a much older packet even though render time is almost aligned with the producer target.

Before residual time or confidence is applied, the native helper now bounds:

- combined X/Y speed to 300 cm/s while preserving direction; and
- absolute Z speed to 360 cm/s.

These limits match the tracker’s established raw-pose speed policy. At a 1 ms residual, the largest default correction is therefore 0.30 cm in combined X/Y and 0.36 cm in Z. At longer residuals, the existing 2 cm and 2.5 cm displacement limits remain the final independent boundary.

The result records input and bounded speed magnitudes plus whether velocity limiting occurred. Invalid or non-finite speed limits fail closed to zero correction rather than behaving as unlimited motion. Optional custom speed limits are appended to the `Extrapolate()` signature, preserving existing call sites.

## Confidence weighting

A marginally valid pose should not receive the same velocity extrapolation as a high-confidence pose. Native correction is multiplied by a smooth confidence gain after the physical velocity boundary:

- confidence at or below 0.15: no native correction;
- confidence at or above 0.75: full correction;
- values between those points: smoothstep interpolation.

For example, confidence 0.45 applies half of the already-bounded velocity correction. This avoids an abrupt on/off velocity step at the confidence threshold and reduces the tendency for uncertain measurements to overshoot.

## Compatibility

`ResidualDelayMs()` remains the historical forward-only helper for direct callers. The overlay path uses `Extrapolate()`, which applies signed correction, physical speed limits, confidence scaling, and final displacement limits in that order.

Existing calls and argument order remain source compatible: the rewind and speed limits are optional trailing parameters. The producer prediction lead remains encoded in `G3D_PoseV2`, so no shared-memory ABI change is required. The additional speed diagnostics exist only in the native helper result.
