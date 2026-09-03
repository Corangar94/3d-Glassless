# Per-tile freshness for ultrawide depth composition

The native depth worker does not infer every ultrawide tile on every cycle. Balanced and fast modes refresh the center more frequently and reuse cached side tiles so inference cost remains bounded.

A composed depth texture therefore contains geometry from more than one desktop capture. Treating the complete texture as if every tile came from the newest center inference can keep old peripheral geometry at full strength after the corresponding screen content has moved.

## Source ownership

Each cached tile now retains the steady-clock capture timestamp of the frame that produced it. The timestamp is updated in the same worker handoff that stores the tile's normalized depth and completion generation.

At composition time, every contributing tile is evaluated against the same age policy used by native parallax health:

- through 140 ms: full tile contribution;
- from 140 through 750 ms: linear attenuation toward neutral depth;
- at exactly 750 ms: zero geometric contribution, but still within the inclusive freshness boundary;
- beyond 750 ms: the tile is expired, reset to neutral depth, removed from the active generation set, and its temporal smoothing history is cleared.

Attenuation is applied only while packing the composed R16F upload. The cached normalized tile is not repeatedly faded in place, so a later refresh starts from its real inference result rather than an already attenuated copy.

## Scheduler behavior

An expired tile receives generation zero. The established oldest-tile scheduler therefore prioritizes it on a subsequent balanced or fast cycle. Until refreshed, the tile contributes neutral depth instead of stale parallax.

The center and any other recently refreshed tiles continue contributing normally. Unlike applying the oldest tile's age to the entire texture, per-tile attenuation does not unnecessarily fade a fresh center because one peripheral region is older.

## Whole-result freshness

The existing result-publication gate remains authoritative for the inference that just completed. A whole result whose source capture exceeds 750 ms is still rejected before texture upload or blend restart.

Per-tile freshness addresses a different boundary: cached geometry from earlier accepted results that is reused when only a subset of tiles is inferred. The two checks are complementary.

## Diagnostics

The inferencer reports the cumulative number of cached tiles expired and neutralized during the current depth session. Missing/uninitialized tiles remain neutral and are not counted as stale expirations.
