#include "depth_tile_freshness.h"

#include <cassert>
#include <cmath>
#include <cstdint>
#include <limits>

using g3d::depth::EvaluateTileFreshness;
using g3d::depth::SaturatingTileAgeU32;
using g3d::depth::TileAgeMs;
using g3d::depth::TileContributionScale;
using g3d::depth::TileFreshnessPolicy;

static bool near(float first, float second, float tolerance = 1e-5f) {
    return std::fabs(first - second) <= tolerance;
}

static void test_missing_source_is_neutral_without_stale_count() {
    const auto decision = EvaluateTileFreshness(0, 1000);
    assert(!decision.usable);
    assert(!decision.stale);
    assert(decision.contribution_scale == 0.0f);
}

static void test_future_source_fails_safe_as_fresh() {
    const auto decision = EvaluateTileFreshness(1100, 1000);
    assert(decision.usable);
    assert(!decision.stale);
    assert(decision.age_ms == 0);
    assert(decision.contribution_scale == 1.0f);
}

static void test_full_strength_boundary_is_inclusive() {
    const auto before = EvaluateTileFreshness(1000, 1139);
    const auto exact = EvaluateTileFreshness(1000, 1140);
    assert(before.usable && exact.usable);
    assert(before.contribution_scale == 1.0f);
    assert(exact.contribution_scale == 1.0f);
}

static void test_midpoint_age_has_half_contribution() {
    // Midpoint of 140..750 is 445 ms.
    const auto decision = EvaluateTileFreshness(1000, 1445);
    assert(decision.usable);
    assert(!decision.stale);
    assert(near(decision.contribution_scale, 0.5f));
}

static void test_zero_strength_boundary_is_inclusive_but_neutral() {
    const auto decision = EvaluateTileFreshness(1000, 1750);
    assert(decision.usable);
    assert(!decision.stale);
    assert(decision.age_ms == 750);
    assert(decision.contribution_scale == 0.0f);
}

static void test_beyond_zero_strength_boundary_is_stale() {
    const auto decision = EvaluateTileFreshness(1000, 1751);
    assert(!decision.usable);
    assert(decision.stale);
    assert(decision.age_ms == 751);
    assert(decision.contribution_scale == 0.0f);
}

static void test_disabled_age_policy_keeps_known_tile_at_full_strength() {
    const auto decision = EvaluateTileFreshness(
        1,
        10'000'000,
        TileFreshnessPolicy{140, 0});
    assert(decision.usable);
    assert(!decision.stale);
    assert(decision.contribution_scale == 1.0f);
}

static void test_invalid_window_fails_closed_to_binary_freshness() {
    const auto fresh = EvaluateTileFreshness(
        1000,
        1100,
        TileFreshnessPolicy{750, 750});
    const auto expired = EvaluateTileFreshness(
        1000,
        1751,
        TileFreshnessPolicy{750, 750});
    assert(fresh.usable);
    assert(fresh.contribution_scale == 1.0f);
    assert(expired.stale);
    assert(expired.contribution_scale == 0.0f);
}

static void test_scale_helper_clamps_age() {
    assert(TileContributionScale(0, TileFreshnessPolicy{}) == 1.0f);
    assert(TileContributionScale(140, TileFreshnessPolicy{}) == 1.0f);
    assert(TileContributionScale(750, TileFreshnessPolicy{}) == 0.0f);
    assert(TileContributionScale(9999, TileFreshnessPolicy{}) == 0.0f);
}

static void test_age_conversion_saturates() {
    const uint64_t huge = static_cast<uint64_t>(
        std::numeric_limits<uint32_t>::max()) + 99;
    assert(SaturatingTileAgeU32(huge, 0)
        == std::numeric_limits<uint32_t>::max());
    assert(TileAgeMs(1000, 1100) == 0);
}

int main() {
    test_missing_source_is_neutral_without_stale_count();
    test_future_source_fails_safe_as_fresh();
    test_full_strength_boundary_is_inclusive();
    test_midpoint_age_has_half_contribution();
    test_zero_strength_boundary_is_inclusive_but_neutral();
    test_beyond_zero_strength_boundary_is_stale();
    test_disabled_age_policy_keeps_known_tile_at_full_strength();
    test_invalid_window_fails_closed_to_binary_freshness();
    test_scale_helper_clamps_age();
    test_age_conversion_saturates();
    return 0;
}
