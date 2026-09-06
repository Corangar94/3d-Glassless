# Correct depth cohesion at silhouettes

The parallax shader samples a five-tap cross around each screen pixel. To avoid
letting one foreground or background outlier dominate an edge, it removes the
lowest and highest depth tap and blends the center toward the mean of the
remaining samples.

## Audit finding

The previous expression removed two values from five but divided those three
samples by four:

```text
(d0 + left + right + up + down - minimum - maximum) × 0.25
```

Even a perfectly uniform local depth of `0.5` therefore produced a trimmed value
of `0.375`. Because the project convention is `0 = near, 1 = far`, the cohesion
result was biased toward zero. The bias became strongest where the edge detector
deliberately increased the cohesion blend, reducing far-plane parallax and making
silhouettes appear to compress or swim as the viewer moved.

## Correction

The retained population contains exactly three taps, so the production shader
now divides by three:

```text
(a + b + c + d + e - minimum - maximum) / 3
```

Uniform depth remains unchanged, one low and one high outlier are discarded, and
the result is independent of sample order. Edge thresholds, the 70% maximum
cohesion blend, depth confidence, depth curve, and disocclusion behavior are
otherwise unchanged.

## Validation

A portable C++ test covers uniform depth, zero and one boundaries, outlier
removal, a depth ramp, all 120 permutations of five distinct samples, and the
exact production HLSL normalization literal.

On Windows, a WARP test compiles the shipped vertex and pixel shaders, executes
the real `SampleDepthCohesive` function against six depth textures, and compares
GPU output with an independent sorted-middle-three oracle.
