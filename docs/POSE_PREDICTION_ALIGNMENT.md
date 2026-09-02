# Render-time pose prediction alignment

The Python pose filter publishes a position that may already be projected beyond its publish timestamp. `prediction_lead_ms` records how far the producer target is ahead of publication. The native overlay then compares that target with the age of the packet when it renders.

## Signed residual correction

The required temporal correction is:

```text
render delay since publication - producer prediction lead
```

A positive result means the packet has aged beyond the producer target and needs a small forward extrapolation. A negative result means the producer target is still ahead of the current render time and the pose must be rewound along its published velocity.

Previously, negative residuals were clamped to zero. A configured producer prediction horizon could therefore leave the displayed viewpoint ahead of the viewer until wall-clock delay caught up, producing lead and overshoot during reversals.

The native correction now supports both directions:

- forward correction is limited to 20 ms;
- rewind correction is limited to 80 ms, matching the maximum producer prediction window;
- combined X/Y correction remains capped at 2 cm;
- Z correction remains capped at 2.5 cm;
- the existing unsigned `native_residual_ms` diagnostic remains the correction magnitude, while the result also records the signed residual and whether rewind was applied.

The position clamps remain symmetric, so an unexpected velocity cannot create a large correction in either direction.

## Confidence weighting

A marginally valid pose should not receive the same velocity extrapolation as a high-confidence pose. Native correction is therefore multiplied by a smooth confidence gain:

- confidence at or below 0.15: no native correction;
- confidence at or above 0.75: full correction;
- values between those points: smoothstep interpolation.

For example, confidence 0.45 applies half of the velocity correction. This avoids an abrupt on/off velocity step at the confidence threshold and reduces the tendency for uncertain measurements to overshoot.

## Compatibility

`ResidualDelayMs()` remains the historical forward-only helper for direct callers. The overlay path uses `Extrapolate()`, which now applies signed correction. Existing calls and argument order remain source compatible; the optional rewind limit is appended to the function signature.

The producer prediction lead remains encoded in `G3D_PoseV2`, so no shared-memory ABI change is required.
