#pragma once

#include <algorithm>
#include <cstdint>
#include <limits>

namespace g3d::depth {

struct SourceIdentity {
    uint64_t generation = 0;
    uint64_t captured_ms = 0;
};

enum class PublishDecision : uint8_t {
    Accept,
    InvalidGeneration,
    NonmonotonicGeneration,
    StaleSource,
};

struct FreshnessPolicy {
    // Matches the parallax-health zero-strength depth-age boundary. A result at
    // the exact boundary remains valid; an older result cannot restart blending.
    uint64_t max_source_age_ms = 750;
};

struct FreshnessSnapshot {
    uint64_t last_published_generation = 0;
    uint64_t last_published_source_ms = 0;
    uint64_t accepted_count = 0;
    uint64_t stale_drop_count = 0;
    uint64_t nonmonotonic_drop_count = 0;
    uint64_t invalid_drop_count = 0;
    uint64_t last_rejected_generation = 0;
    uint64_t last_rejected_age_ms = 0;
};

inline uint64_t SourceAgeMs(uint64_t now_ms, uint64_t source_ms) {
    // steady_clock should not move backwards. Treat an unexpected future source
    // as age zero rather than underflowing into an enormous stale duration.
    return now_ms >= source_ms ? now_ms - source_ms : 0;
}

inline uint32_t SaturatingAgeU32(uint64_t now_ms, uint64_t source_ms) {
    return static_cast<uint32_t>(std::min<uint64_t>(
        std::numeric_limits<uint32_t>::max(),
        SourceAgeMs(now_ms, source_ms)));
}

class ResultFreshnessGate {
public:
    explicit ResultFreshnessGate(FreshnessPolicy policy = {})
        : policy_(policy) {}

    PublishDecision consider(SourceIdentity source, uint64_t now_ms) {
        if (source.generation == 0) {
            ++invalid_drop_count_;
            remember_rejection(source.generation, 0);
            return PublishDecision::InvalidGeneration;
        }
        if (source.generation <= last_published_generation_) {
            ++nonmonotonic_drop_count_;
            remember_rejection(
                source.generation,
                SourceAgeMs(now_ms, source.captured_ms));
            return PublishDecision::NonmonotonicGeneration;
        }

        const uint64_t age_ms = SourceAgeMs(now_ms, source.captured_ms);
        if (policy_.max_source_age_ms > 0
            && age_ms > policy_.max_source_age_ms) {
            ++stale_drop_count_;
            remember_rejection(source.generation, age_ms);
            return PublishDecision::StaleSource;
        }

        last_published_generation_ = source.generation;
        last_published_source_ms_ = source.captured_ms;
        ++accepted_count_;
        return PublishDecision::Accept;
    }

    void reset() {
        last_published_generation_ = 0;
        last_published_source_ms_ = 0;
        accepted_count_ = 0;
        stale_drop_count_ = 0;
        nonmonotonic_drop_count_ = 0;
        invalid_drop_count_ = 0;
        last_rejected_generation_ = 0;
        last_rejected_age_ms_ = 0;
    }

    FreshnessSnapshot snapshot() const {
        return {
            last_published_generation_,
            last_published_source_ms_,
            accepted_count_,
            stale_drop_count_,
            nonmonotonic_drop_count_,
            invalid_drop_count_,
            last_rejected_generation_,
            last_rejected_age_ms_,
        };
    }

private:
    void remember_rejection(uint64_t generation, uint64_t age_ms) {
        last_rejected_generation_ = generation;
        last_rejected_age_ms_ = age_ms;
    }

    FreshnessPolicy policy_;
    uint64_t last_published_generation_ = 0;
    uint64_t last_published_source_ms_ = 0;
    uint64_t accepted_count_ = 0;
    uint64_t stale_drop_count_ = 0;
    uint64_t nonmonotonic_drop_count_ = 0;
    uint64_t invalid_drop_count_ = 0;
    uint64_t last_rejected_generation_ = 0;
    uint64_t last_rejected_age_ms_ = 0;
};

}  // namespace g3d::depth
