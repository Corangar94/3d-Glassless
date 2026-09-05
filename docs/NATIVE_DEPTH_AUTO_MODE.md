# Native adaptive depth mode

The shared settings ABI and `DepthInferencer` use four requested performance modes:

| Code | Requested mode |
|---:|---|
| `0` | quality |
| `1` | balanced |
| `2` | fast |
| `3` | auto |

Mode `3` delegates the actual profile choice to the native inferencer. It may move among quality, balanced, and fast according to measured inference time, render CPU/GPU cost, and ultrawide tile count. The existing hysteresis requires repeated evidence before moving toward a more expensive profile and reacts faster when load requires a cheaper profile.

## Correct settings boundary

The overlay now preserves all four supported ABI values. An absent settings channel also requests auto rather than silently resetting the inferencer to balanced on every frame. Unknown values from a legacy or malformed writer fail to balanced mode.

This matters because the overlay reapplies settings every render iteration. The previous local default was balanced and the previous shared-memory guard accepted only values through `2`. Even though the global default, launcher, strict writer, diagnostics, and inferencer all understood mode `3`, either path replaced it with mode `1` before `DepthInferencer::set_performance_mode()`.

## Build integration

`depth_mode_policy.h` is the single source of truth for supported mode values, the default request, and invalid-value normalization. The monolithic Windows overlay is configured into the build directory with exact required substitutions that include and call this policy. Configuration fails if any source anchor changes, preventing a future source edit from silently dropping the adaptive path.

The generated translation unit otherwise remains byte-for-byte equivalent to `overlay.cpp`, and CMake tracks the source as a configure dependency. The source directory is added to the include path so the generated file resolves the existing native headers.

## Diagnostics

The existing periodic overlay log already reports both:

- `mode=` — the requested mode; and
- `active=` — the concrete quality, balanced, or fast profile selected by the inferencer.

With auto requested, `mode=auto` remains stable while `active=` reflects adaptive decisions. Explicit quality, balanced, and fast requests continue to bypass automatic profile selection.

## Compatibility

No shared-memory name, field offset, enum code, native class signature, or model resource changes. Existing settings using codes `0`, `1`, or `2` retain their behavior. The strict settings writer already accepts `depth_mode=3`, and the repository default remains auto.
