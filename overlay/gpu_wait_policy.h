#pragma once
#include <cstdint>
#include <limits>

namespace g3d::gpu {
enum class WaitDecision { Pending, Complete, Removed, Cancelled, Timeout };
inline WaitDecision FenceDecision(uint64_t completed, uint64_t target,
                                  bool removed, bool cancelled,
                                  uint64_t elapsed_ms, uint64_t timeout_ms) {
    if (removed || completed == std::numeric_limits<uint64_t>::max())
        return WaitDecision::Removed;
    if (completed >= target) return WaitDecision::Complete;
    if (cancelled) return WaitDecision::Cancelled;
    if (elapsed_ms >= timeout_ms) return WaitDecision::Timeout;
    return WaitDecision::Pending;
}
}  // namespace g3d::gpu
