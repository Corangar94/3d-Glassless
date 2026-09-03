# Orientation-independent camera quality analysis

Glassless3D continuously measures delivered-frame cadence, but brightness, clipping, exposure stability, and sharpness are analyzed at a lower rate because those image operations are comparatively expensive.

## Analysis budget

Every sampled image is now uniformly reduced so its **longest edge is at most 320 pixels** before grayscale conversion, mean/clipping calculations, and Laplacian sharpness analysis.

| Camera frame | Previous width-only result | Longest-edge result |
|---|---:|---:|
| 1280×720 | 320×180 | 320×180 |
| 720×1280 | about 320×569 | 180×320 |
| 1200×1600 | 320×427 | 240×320 |

For a rotated 720×1280 camera, the quality-analysis image falls from about 182,080 pixels to 57,600 pixels—roughly a 68.4% reduction. Normal landscape behavior is unchanged.

Frames whose width and height are already at or below 320 pixels are passed through without allocation or upscaling. Larger frames preserve their aspect ratio and use `cv2.INTER_AREA` for downscaling.

## Processing and cadence

The monitor still records camera cadence from every delivered frame. Image metrics retain the existing wrap-safe `analysis_interval_ms` schedule, which defaults to 80 ms. Between image-analysis samples, the most recent brightness, clipping, and sharpness values are carried through the cadence window exactly as before.

The processing order for an analyzed frame is:

1. compute one uniform longest-edge scale;
2. optionally reduce the BGR image;
3. convert the bounded image to grayscale;
4. measure mean brightness, dark fraction, clipped fraction, and Laplacian variance;
5. append the result to the existing rolling status window.

No quality thresholds, warm-up requirements, exposure-hunting rules, control-lock decisions, or recovery timing values change.

## Orientation behavior

Landscape, portrait, and rotated cameras now have the same maximum pixel budget. A constant image produces the same normalized brightness, clipping fractions, and sharpness result in either orientation. Physical face-pose geometry is unaffected because camera-quality analysis does not alter or replace the original frame passed to the trackers.
