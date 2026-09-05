#pragma once

#include <cstdint>

namespace g3d::depth_mode {

inline constexpr uint32_t kQuality = 0;
inline constexpr uint32_t kBalanced = 1;
inline constexpr uint32_t kFast = 2;
inline constexpr uint32_t kAuto = 3;

inline constexpr uint32_t DefaultRequestedMode() {
    return kAuto;
}

inline constexpr bool IsSupported(uint32_t mode) {
    return mode <= kAuto;
}

inline constexpr uint32_t NormalizeRequestedMode(uint32_t mode) {
    return IsSupported(mode) ? mode : kBalanced;
}

}  // namespace g3d::depth_mode
