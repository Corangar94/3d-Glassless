#include "depth_cohesion_shader.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <iostream>
#include <string>

namespace {

bool Near(float actual, float expected, float tolerance = 1e-6f) {
    return std::fabs(actual - expected) <= tolerance;
}

bool Check(bool condition, const char* message) {
    if (condition) return true;
    std::cerr << "depth_cohesion_tests: " << message << '\n';
    return false;
}

}  // namespace

int main() {
    using g3d::depth_cohesion::TrimmedMean5;
    int failures = 0;
    const auto require = [&failures](bool condition, const char* message) {
        if (!Check(condition, message)) ++failures;
    };

    require(Near(TrimmedMean5(.5f, .5f, .5f, .5f, .5f), .5f),
        "uniform depth must remain unchanged");
    require(Near(TrimmedMean5(.5f, .1f, .5f, .5f, .9f), .5f),
        "one low and one high outlier must be removed");
    require(Near(TrimmedMean5(.1f, .2f, .4f, .6f, .8f), .4f),
        "ordered ramp trimmed mean mismatch");
    require(Near(TrimmedMean5(0, 0, 0, 0, 0), 0),
        "zero boundary mismatch");
    require(Near(TrimmedMean5(1, 1, 1, 1, 1), 1),
        "one boundary mismatch");

    std::array<float, 5> values = {.1f, .2f, .4f, .6f, .8f};
    do {
        require(Near(
            TrimmedMean5(values[0], values[1], values[2], values[3], values[4]),
            .4f), "trimmed mean must be permutation invariant");
    } while (std::next_permutation(values.begin(), values.end()));

    const std::string hlsl = G3D_DEPTH_COHESION_HLSL;
    require(hlsl.find("/ 3.0") != std::string::npos,
        "production HLSL must divide the three retained taps by three");
    require(hlsl.find("* 0.25") == std::string::npos,
        "production HLSL still contains the former quarter-weight bias");
    return failures == 0 ? 0 : 1;
}
