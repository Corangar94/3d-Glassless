#pragma once

#include "depth_result_freshness.h"
#include <algorithm>
#include <cstddef>
#include <vector>

namespace g3d::depth {
struct TileCoverageSnapshot {
    bool all_valid = false;
    bool complete_frame = false;
    uint64_t oldest_source_ms = 0;
};

// Worker-owned provenance for actual inference, not synthetic cache filling or
// scheduler recency counters. Publication copies a snapshot under the worker lock.
class TileCoverage {
public:
    void reset(std::size_t count) { sources_.assign(count, {}); }
    void clear() { std::fill(sources_.begin(), sources_.end(), SourceIdentity{}); }
    void record(const std::vector<int>& tiles, SourceIdentity source) {
        for (int tile : tiles) {
            if (tile >= 0 && static_cast<std::size_t>(tile) < sources_.size())
                sources_[static_cast<std::size_t>(tile)] = source;
        }
    }

    TileCoverageSnapshot snapshot(SourceIdentity batch) const {
        TileCoverageSnapshot result;
        if (sources_.empty()) return result;
        result.all_valid = true;
        result.complete_frame = batch.generation != 0;
        result.oldest_source_ms = UINT64_MAX;
        for (const auto source : sources_) {
            result.all_valid = result.all_valid && source.generation != 0;
            result.complete_frame = result.complete_frame && SameSource(source, batch);
            result.oldest_source_ms = std::min(result.oldest_source_ms, source.captured_ms);
        }
        if (!result.all_valid) result.oldest_source_ms = 0;
        return result;
    }

private:
    std::vector<SourceIdentity> sources_;
};
}  // namespace g3d::depth
