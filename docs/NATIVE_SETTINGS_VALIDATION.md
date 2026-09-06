# Native shared-settings validation

`G3D_Settings` is an optional 88-byte process boundary. Current Python writers validate and coordinate their publications, but a native consumer can remain attached while an older utility, stale binary, or unrelated process owns the mapping. A stable odd/even snapshot therefore still needs semantic validation before it reaches shader constants or runtime mode selection.

The configured native overlay now validates every settings field that it consumes immediately after `ReadStableSettings()` succeeds. The readable monolithic `overlay.cpp` retains its established layout; CMake inserts the dependency-free policy into the single generated production translation unit and fails configuration unless every source anchor is unique.

## Numeric admission

The following values must be finite and inside deliberately broad safety bounds:

| Field | Accepted native range |
|---|---:|
| horizontal/vertical strength | `0.0 .. 16.0` |
| virtual depth | `0.0 .. 1000.0 cm` |
| screen width/height override | `0.0 .. 1000.0 cm` |
| depth gamma | greater than `0.0`, through `16.0` |
| focus radius | `0.0 .. 1.0` |
| IPD | greater than `0.0`, through `200.0 mm` |
| deadzone | `0.0 .. 1000.0 mm` |
| panel width/height | `0 .. 65535 px` |
| focus plane | `0.0 .. 1000.0 cm` |

The limits are wider than the normal live-tuning controls, so valid advanced configurations remain available while infinities, NaNs, negative physical values, and pathological legacy dimensions cannot enter render arithmetic.

A numeric rejection is atomic at the native consumer: the current `ApplySettings()` call returns before updating any global runtime value. The overlay therefore retains the last complete valid settings state rather than mixing defaults, old fields, and a malformed packet. Rejections are logged once per stable settings version with a field bitmask.

## Zero-strength behavior

The live tuning UI includes `0.0×` strength. Native ingestion previously used a strictly-positive condition, so moving the slider to zero silently restored the local `1.0×` default instead of disabling parallax.

Zero is now a valid value for both axes. A horizontal strength of zero disables horizontal parallax; a vertical strength of zero disables vertical parallax. Screen dimensions retain their separate zero sentinel, where zero means to keep the autodetected or command-line value.

## Enum normalization

Unknown legacy enum values do not stall an otherwise numerically valid snapshot. They are normalized to the existing safe behavior and logged once per version:

- depth curve → square-root curve;
- display backend → desktop overlay;
- depth mode → balanced through the existing depth-mode policy;
- stereo layout → full side-by-side;
- eye order → left then right;
- tracking mode → Glassless3D managed.

Supported depth mode `3` remains automatic and continues reaching the adaptive inferencer.

## Compatibility

No shared-memory ABI changes are made:

- mapping name remains `G3D_Settings`;
- structure size remains 88 bytes;
- field order and offsets remain unchanged;
- current lock-free odd/even reads remain unchanged;
- Python writer validation and cross-process writer coordination remain unchanged.

The standalone policy has a native CTest target covering defaults, zero strength, exact boundaries, nonfinite values, combined rejection masks, and safe enum normalization. Python source-contract tests verify the generated-overlay transformations and unchanged ABI.
