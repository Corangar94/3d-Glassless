#pragma once
#include "depth_result_freshness.h"
#include <cstddef>

namespace g3d::depth {
// A composite's generation describes the new assembly, while its timestamp
// describes its OLDEST visible input. Restitching never refreshes old pixels.
inline SourceIdentity OldestCompositeSource(
    const SourceIdentity* tiles, std::size_t count, uint64_t generation) {
    if (!tiles || count == 0 || generation == 0) return {};
    uint64_t oldest = std::numeric_limits<uint64_t>::max();
    for (std::size_t i = 0; i < count; ++i) {
        if (tiles[i].generation == 0) return {};
        oldest = std::min(oldest, tiles[i].captured_ms);
    }
    return {generation, oldest};
}

inline bool CompositeNeedsRefresh(
    const SourceIdentity* tiles, std::size_t count, uint64_t now_ms,
    uint64_t refresh_age_ms = 375) {
    const auto oldest = OldestCompositeSource(tiles, count, 1);
    return oldest.generation == 0
        || SourceAgeMs(now_ms, oldest.captured_ms) >= refresh_age_ms;
}
}  // namespace g3d::depth
