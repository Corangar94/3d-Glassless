# Cadence-independent camera quality statistics

Glassless3D records camera cadence on every delivered frame, but brightness, clipping, and Laplacian sharpness are intentionally analyzed at a lower rate. The default image-analysis interval is 80 ms.

Between analysis events, the latest image metrics remain attached to per-frame samples so diagnostics always have a current value. Those carried values are not new observations and must not receive additional statistical weight.

## Previous weighting

The quality status previously calculated image medians and brightness jitter from every per-frame sample. At 30 fps, one analyzed brightness value could appear two or three times before the next analysis. At a temporarily lower cadence it might appear once, while a burst of faster frames could repeat it many more times.

As a result, otherwise identical image-analysis results could produce different:

- median brightness;
- dark and clipped fractions;
- median sharpness;
- exposure-hunting jitter; and
- camera-control lock admission.

The difference depended on frame cadence and on where analysis timestamps happened to fall within the frame sequence, rather than only on the observed images.

## Current behavior

Image statistics now use only samples whose `analyzed` flag is true:

- brightness is the median of actual analysis events;
- brightness jitter is the population deviation of those events;
- dark and clipped fractions use the same distinct observations;
- sharpness uses the median of actual analyses.

Delivered-frame intervals continue to use every valid frame, so FPS measurement remains unchanged. The rolling frame count, minimum number of image analyses, exposure and sharpness thresholds, quality labels, warm-up size, control-lock transaction, and recovery timing are also unchanged.

The first frame of every camera session is always analyzed. A defensive fallback retains the newest carried sample only for manually constructed or legacy sample deques containing no analyzed entry.

## Effect

A sequence of image analyses now produces the same quality status whether the camera delivered many or few intermediate frames. This makes exposure-hunting detection and automatic-control stabilization depend on image evidence rather than delivery cadence.
