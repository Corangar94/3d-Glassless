#include "depth_mode_policy.h"

#include <cstdint>
#include <iostream>
#include <limits>

namespace {

bool Check(bool condition, const char* message) {
    if (condition) return true;
    std::cerr << "depth_mode_policy_tests: " << message << '\n';
    return false;
}

}  // namespace

int main() {
    using namespace g3d::depth_mode;

    int failures = 0;
    const auto require = [&failures](bool condition, const char* message) {
        if (!Check(condition, message)) ++failures;
    };

    static_assert(kQuality == 0);
    static_assert(kBalanced == 1);
    static_assert(kFast == 2);
    static_assert(kAuto == 3);
    static_assert(DefaultRequestedMode() == kAuto);

    require(DefaultRequestedMode() == 3,
        "the native overlay default must request adaptive depth");
    require(NormalizeRequestedMode(kQuality) == kQuality,
        "quality mode changed during normalization");
    require(NormalizeRequestedMode(kBalanced) == kBalanced,
        "balanced mode changed during normalization");
    require(NormalizeRequestedMode(kFast) == kFast,
        "fast mode changed during normalization");
    require(NormalizeRequestedMode(kAuto) == kAuto,
        "auto mode must reach the adaptive inferencer");
    require(NormalizeRequestedMode(4) == kBalanced,
        "first unknown mode must fail to balanced");
    require(
        NormalizeRequestedMode(std::numeric_limits<uint32_t>::max())
            == kBalanced,
        "large unknown mode must fail to balanced");

    return failures == 0 ? 0 : 1;
}
