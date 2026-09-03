#pragma once

#include <algorithm>
#include <cstdint>
#include <limits>

namespace g3d::depth {

struct TileFreshnessPolicy {
    // Keep this aligned with the whole-result publication and parallax-health
    // zero-strength boundary. The exact boundary remains usable.
    uint64_t max_tile_age_ms = 750;
};

struct TileFreshnessDecision {
    bool usable = false;
    bool stale = false;
    uint64_t age_ms = 0;
};

inline uint64_t TileAgeMs(uint64_t reference_ms, uint64_t source_ms) {
    if (source_ms == 0) return std::numeric_limits<uint64_t>::max();
    // steady_clock should not move backwards. Treat a future source as age zero
    // rather than underflowing and spuriously neutralizing fresh geometry.
    return reference_ms >= source_ms ? reference_ms - source_ms : 0;
}

inline TileFreshnessDecision EvaluateTileFreshness(
    uint64_t source_ms,
    uint64_t reference_ms,
    TileFreshnessPolicy policy = {}) {
    if (source_ms == 0) return {};
    const uint64_t age_ms = TileAgeMs(reference_ms, source_ms);
    if (policy.max_tile_age_ms > 0 && age_ms > policy.max_tile_age_ms) {
        return {false, true, age_ms};
    }
    return {true, false, age_ms};
}

inline uint32_t SaturatingTileAgeU32(
    uint64_t reference_ms,
    uint64_t source_ms) {
    return static_cast<uint32_t>(std::min<uint64_t>(
        std::numeric_limits<uint32_t>::max(),
        TileAgeMs(reference_ms, source_ms)));
}

}  // namespace g3d::depth
