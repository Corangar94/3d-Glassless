#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>

namespace g3d::depth {

constexpr uint64_t kColdOutstandingWorkDeadlineMs = 15'000;
constexpr uint64_t kMinimumOutstandingWorkDeadlineMs = 5'000;
constexpr uint64_t kMaximumOutstandingWorkDeadlineMs = 15'000;

inline uint64_t OutstandingWorkDeadlineMs(float last_inference_ms) {
    if (!std::isfinite(last_inference_ms) || last_inference_ms <= 0.0f) {
        return kColdOutstandingWorkDeadlineMs;
    }
    const double scaled = std::ceil(static_cast<double>(last_inference_ms) * 8.0);
    return std::max<uint64_t>(
        kMinimumOutstandingWorkDeadlineMs,
        std::min<uint64_t>(kMaximumOutstandingWorkDeadlineMs,
                           static_cast<uint64_t>(scaled)));
}

inline bool OutstandingWorkTimedOut(
    uint64_t started_ms,
    uint64_t now_ms,
    uint64_t deadline_ms) {
    return started_ms != 0 && now_ms >= started_ms
        && now_ms - started_ms > deadline_ms;
}

}  // namespace g3d::depth
