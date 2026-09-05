# Strict shared-settings publication

`G3D_Settings` is a binary process boundary, not a configuration parser. Values entering its 88-byte ABI must already have the exact scalar type and enum meaning expected by the native overlay and packaged tracker.

The writer now validates the complete `OverlaySettings` object before it marks the mapping with an odd in-progress version. A rejected value therefore cannot hide, partially replace, or invalidate the last committed settings snapshot.

## Scalar rules

Every floating-point field must be a finite real numeric value.

- booleans are never accepted as numbers;
- strings and byte strings are not parsed at the ABI boundary;
- complex values are rejected;
- NaN and positive or negative infinity are rejected;
- normal Python and NumPy real/integer scalars remain supported.

Every uint32 field must be a true integral value in the inclusive range `0..0xFFFFFFFF`.

- fractional values are never truncated;
- integral-looking floats such as `1.0` are rejected;
- booleans are rejected even though Python subclasses `bool` from `int`;
- strings are not converted;
- negative and oversized integers are rejected.

Human-readable parsing remains the responsibility of the launcher, wizard, profile loader, or other producer before it constructs `OverlaySettings`. The shared-memory writer is the final fail-closed boundary.

## Explicit enum domains

The wire enums accept only their documented values:

| Field | Accepted values |
|---|---|
| `depth_curve` | `0`, `1`, `2` |
| `display_backend` | `0`, `1`, `2` |
| `depth_mode` | `0`, `1`, `2`, `3` |
| `stereo_layout` | `0`, `1` |
| `eye_order` | `0`, `1` |
| `tracking_mode` | `0`, `1` |

Panel dimensions and the internally generated publication version remain general uint32 values.

`depth_mode=3` remains a valid ABI value for automatic performance selection. Rejecting unknown enum codes at publication prevents an old or malformed producer from silently selecting an unintended native fallback.

## Atomic failure behavior

Publication order remains:

1. validate every scalar and enum;
2. pack the complete even-version snapshot in private memory;
3. acquire the coordinated writer transaction and mark the mapping odd;
4. copy both body slices around the mid-structure version word;
5. publish the even committed version as the final store.

Because validation and packing occur before the mapping is marked odd, a bad field produces no shared-memory write and does not advance the writer version. Readers continue seeing the previous complete even snapshot.

The cross-process named mutex, abandoned-writer recovery, uint32 version rollover, lock-free reader protocol, mapping name, field offsets, and 88-byte ABI are unchanged.

## Diagnostics

Validation errors identify the rejected field. For example, a fractional `panel_width_px`, boolean `tracking_mode`, or text `strength_x` fails at the writer with a field-specific `ValueError`, allowing the calling UI or utility to report the real configuration problem instead of publishing a coercion.
