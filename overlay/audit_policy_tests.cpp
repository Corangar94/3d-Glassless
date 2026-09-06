#include "composite_freshness.h"
#include "gpu_wait_policy.h"
#include <array>
#include <iostream>
#include <limits>

int main() {
    using namespace g3d::depth;
    using namespace g3d::gpu;
    int failures = 0;
    const auto check = [&](bool ok, const char* name) {
        if (!ok) { std::cerr << name << '\n'; ++failures; }
    };
    std::array<SourceIdentity, 4> tiles{{{1,100}, {2,700}, {3,900}, {4,900}}};
    auto composite = OldestCompositeSource(tiles.data(), tiles.size(), 4);
    check(composite.captured_ms == 100, "oldest tile retains capture time");
    ResultFreshnessGate gate;
    check(gate.consider(composite, 1000) == PublishDecision::StaleSource,
          "fresh center cannot hide 900ms peripheral tile");
    check(CompositeNeedsRefresh(tiles.data(), tiles.size(), 1000), "expired coverage refresh");
    tiles.fill({5, 1000});
    composite = OldestCompositeSource(tiles.data(), tiles.size(), 5);
    check(gate.consider(composite, 1750) == PublishDecision::Accept, "750ms boundary");
    check(gate.consider({6,1000}, 1751) == PublishDecision::StaleSource, "751ms boundary");
    tiles[1] = {};
    check(OldestCompositeSource(tiles.data(), 4, 6).generation == 0, "uninitialized coverage");
    check(CompositeNeedsRefresh(tiles.data(), 4, 1000), "startup refresh");
    check(OldestCompositeSource(nullptr, 0, 1).generation == 0, "empty composite");
    check(FenceDecision(2,3,false,false,10,2000) == WaitDecision::Pending, "pending fence");
    check(FenceDecision(3,3,false,false,10,2000) == WaitDecision::Complete, "complete fence");
    check(FenceDecision(UINT64_MAX,3,false,false,10,2000) == WaitDecision::Removed, "removal sentinel");
    check(FenceDecision(3,3,true,false,10,2000) == WaitDecision::Removed, "removal beats completion");
    check(FenceDecision(2,3,false,true,10,2000) == WaitDecision::Cancelled, "cancel pending fence");
    check(FenceDecision(2,3,false,false,2000,2000) == WaitDecision::Timeout, "bounded deadline");
    check(FenceDecision(3,3,false,true,2000,2000) == WaitDecision::Complete, "completed resource safe on cancel");
    return failures ? 1 : 0;
}
