# Continuity-safe scheduled cascade scans

The OpenCV fallback uses sparse optical flow between periodic Haar-cascade corrections. Every 30 frames it forces a full-frame face scan so an inaccurate ROI cannot hide a better global detection indefinitely.

A forced global scan can occasionally miss the established viewer while a small search around the tracked ROI would still succeed. It can also detect only another distant face. Previously, either case discarded the ROI path for that frame: a miss incremented the fallback track’s cascade-miss count, while a distant detection could pull the correction toward another viewer.

## Selective continuity fallback

A scheduled scan now follows this sequence:

1. run the required full-frame cascade;
2. select the best global candidate relative to the previous tracked box;
3. accept that result immediately when it overlaps the prior by at least 0.08 IoU or its center remains within 0.75 prior-box widths/heights;
4. otherwise run one small prior-ROI cascade;
5. combine the global and ROI candidates and apply the existing temporal candidate scoring.

The normal successful scheduled scan still performs one face-cascade call. The additional ROI call occurs only when the global result is absent, clipped outside the image, or implausibly far from the tracked viewer.

## Reacquisition behavior

Global candidates are never discarded. If both the global and ROI scans find faces, the existing continuity score normally prefers the ROI-compatible viewer. If the ROI misses, the distant global candidate remains available for genuine reacquisition. With no prior box, a forced scan remains a single full-frame call.

This change therefore improves viewer continuity without weakening periodic global discovery or adding recurring work to the common path.

## Compatibility

The `force_full_scan` detector API and legacy adapter behavior are unchanged. Ordinary ROI-first correction frames still fall back to a full-frame scan on an ROI miss when allowed. Eye detection, box clipping, detector cadence, optical-flow thresholds, and physical pose reconstruction are unchanged.
