#include "depth_result_freshness.h"

#include <cassert>
#include <cstdint>
#include <limits>

using g3d::depth::FreshnessPolicy;
using g3d::depth::PublishDecision;
using g3d::depth::ResultFreshnessGate;
using g3d::depth::SaturatingAgeU32;
using g3d::depth::SourceAgeMs;
using g3d::depth::SourceIdentity;

static void test_first_and_newer_sources_publish() {
    ResultFreshnessGate gate;

    assert(gate.consider({1, 1000}, 1100) == PublishDecision::Accept);
    assert(gate.consider({2, 1200}, 1300) == PublishDecision::Accept);

    const auto snapshot = gate.snapshot();
    assert(snapshot.last_published_generation == 2);
    assert(snapshot.last_published_source_ms == 1200);
    assert(snapshot.accepted_count == 2);
    assert(snapshot.stale_drop_count == 0);
    assert(snapshot.nonmonotonic_drop_count == 0);
}

static void test_exact_age_boundary_is_accepted() {
    ResultFreshnessGate gate(FreshnessPolicy{750});

    assert(gate.consider({1, 1000}, 1750) == PublishDecision::Accept);
    assert(gate.snapshot().accepted_count == 1);
}

static void test_source_beyond_age_boundary_is_dropped() {
    ResultFreshnessGate gate(FreshnessPolicy{750});

    assert(gate.consider({1, 1000}, 1751) == PublishDecision::StaleSource);

    const auto snapshot = gate.snapshot();
    assert(snapshot.accepted_count == 0);
    assert(snapshot.stale_drop_count == 1);
    assert(snapshot.last_rejected_generation == 1);
    assert(snapshot.last_rejected_age_ms == 751);
}

static void test_stale_drop_does_not_advance_generation_anchor() {
    ResultFreshnessGate gate(FreshnessPolicy{100});

    assert(gate.consider({1, 0}, 101) == PublishDecision::StaleSource);
    assert(gate.consider({1, 200}, 250) == PublishDecision::Accept);
    assert(gate.snapshot().last_published_generation == 1);
}

static void test_duplicate_and_older_generations_cannot_replace_depth() {
    ResultFreshnessGate gate;

    assert(gate.consider({5, 1000}, 1050) == PublishDecision::Accept);
    assert(gate.consider({5, 1100}, 1120)
        == PublishDecision::NonmonotonicGeneration);
    assert(gate.consider({4, 1110}, 1130)
        == PublishDecision::NonmonotonicGeneration);
    assert(gate.consider({6, 1120}, 1140) == PublishDecision::Accept);

    const auto snapshot = gate.snapshot();
    assert(snapshot.last_published_generation == 6);
    assert(snapshot.nonmonotonic_drop_count == 2);
    assert(snapshot.accepted_count == 2);
}

static void test_generation_zero_is_never_publishable() {
    ResultFreshnessGate gate;

    assert(gate.consider({0, 1000}, 1000)
        == PublishDecision::InvalidGeneration);
    assert(gate.snapshot().invalid_drop_count == 1);
}

static void test_future_source_time_fails_safe_without_underflow() {
    ResultFreshnessGate gate(FreshnessPolicy{1});

    assert(SourceAgeMs(1000, 1100) == 0);
    assert(gate.consider({1, 1100}, 1000) == PublishDecision::Accept);
}

static void test_zero_age_limit_disables_stale_rejection_only() {
    ResultFreshnessGate gate(FreshnessPolicy{0});

    assert(gate.consider({1, 1}, 10'000'000) == PublishDecision::Accept);
    assert(gate.consider({1, 2}, 10'000'001)
        == PublishDecision::NonmonotonicGeneration);
}

static void test_age_conversion_saturates_to_uint32() {
    const uint64_t huge = static_cast<uint64_t>(
        std::numeric_limits<uint32_t>::max()) + 99;
    assert(SaturatingAgeU32(huge, 0)
        == std::numeric_limits<uint32_t>::max());
}

static void test_reset_starts_a_new_depth_session() {
    ResultFreshnessGate gate;
    assert(gate.consider({10, 1000}, 1010) == PublishDecision::Accept);

    gate.reset();

    assert(gate.consider({1, 2000}, 2010) == PublishDecision::Accept);
    const auto snapshot = gate.snapshot();
    assert(snapshot.last_published_generation == 1);
    assert(snapshot.accepted_count == 1);
    assert(snapshot.stale_drop_count == 0);
}

int main() {
    test_first_and_newer_sources_publish();
    test_exact_age_boundary_is_accepted();
    test_source_beyond_age_boundary_is_dropped();
    test_stale_drop_does_not_advance_generation_anchor();
    test_duplicate_and_older_generations_cannot_replace_depth();
    test_generation_zero_is_never_publishable();
    test_future_source_time_fails_safe_without_underflow();
    test_zero_age_limit_disables_stale_rejection_only();
    test_age_conversion_saturates_to_uint32();
    test_reset_starts_a_new_depth_session();
    return 0;
}
