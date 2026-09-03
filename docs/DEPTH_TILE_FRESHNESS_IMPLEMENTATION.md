Implementation invariants for the native ultrawide compositor:

1. Every non-neutral cached tile carries the source-capture timestamp that produced it.
2. Tile freshness is evaluated after selected tiles are updated and before global contrast sampling and R16F packing.
3. Aging attenuation is applied to the packed value, not destructively to the cached inference.
4. An expired or metadata-inconsistent tile is neutralized, removed from the active generation set, and detached from prior temporal smoothing.
5. Generation zero returns the tile to the established oldest-tile scheduler.
6. Whole-result source freshness remains a separate publication boundary.
