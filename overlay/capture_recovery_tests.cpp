#include "capture_recovery.h"

#include <iostream>

using g3d::capture::BuildUprightCaptureRegion;
using g3d::capture::CaptureSignal;
using g3d::capture::CaptureState;
using g3d::capture::Rect;
using g3d::capture::RetrySchedule;
using g3d::capture::Rotation;
using g3d::capture::UprightToRawUv;

#define CHECK(expression)                                                        \
    do {                                                                         \
        if (!(expression)) {                                                     \
            std::cerr << __FILE__ << ':' << __LINE__ << ": " #expression << '\n'; \
            return false;                                                        \
        }                                                                        \
    } while (false)

static bool TestFullOutputRegion() {
    const auto region = BuildUprightCaptureRegion(Rect{100, 50, 1100, 650}, std::nullopt);
    CHECK(region.has_value());
    CHECK(region->left == 0);
    CHECK(region->top == 0);
    CHECK(region->width == 1000);
    CHECK(region->height == 600);
    return true;
}

static bool TestContainedTargetRegion() {
    const auto region = BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{250, 150, 850, 550});
    CHECK(region.has_value());
    CHECK(region->left == 150);
    CHECK(region->top == 100);
    CHECK(region->width == 600);
    CHECK(region->height == 400);
    return true;
}

static bool TestOutOfOutputTargetIsRejected() {
    CHECK(!BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{90, 150, 850, 550}).has_value());
    CHECK(!BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{250, 150, 1150, 550}).has_value());
    CHECK(!BuildUprightCaptureRegion(
        Rect{100, 50, 1100, 650}, Rect{250, 150, 250, 550}).has_value());
    return true;
}

static bool TestRotationMapsUprightCornersToRawSurface() {
    const auto identity = UprightToRawUv(Rotation::Identity, {0.25f, 0.75f});
    CHECK(identity.u == 0.25f && identity.v == 0.75f);

    const auto rotate90 = UprightToRawUv(Rotation::Rotate90, {0.0f, 0.0f});
    CHECK(rotate90.u == 0.0f && rotate90.v == 1.0f);

    const auto rotate180 = UprightToRawUv(Rotation::Rotate180, {0.0f, 1.0f});
    CHECK(rotate180.u == 1.0f && rotate180.v == 0.0f);

    const auto rotate270 = UprightToRawUv(Rotation::Rotate270, {1.0f, 0.0f});
    CHECK(rotate270.u == 1.0f && rotate270.v == 1.0f);
    return true;
}

static bool TestTransitionsKeepTimeoutAndHideUnavailable() {
    const auto timeout = g3d::capture::AdvanceCaptureState(
        CaptureState::Running, CaptureSignal::FrameTimeout);
    CHECK(timeout.next_state == CaptureState::Running);
    CHECK(timeout.keep_last_frame);
    CHECK(!timeout.hide_overlay);
    CHECK(!timeout.arm_retry);

    const auto lost = g3d::capture::AdvanceCaptureState(
        CaptureState::Running, CaptureSignal::DuplicationLost);
    CHECK(lost.next_state == CaptureState::Rebinding);
    CHECK(lost.hide_overlay);
    CHECK(lost.arm_retry);

    const auto unavailable = g3d::capture::AdvanceCaptureState(
        CaptureState::Running, CaptureSignal::DuplicationUnavailable);
    CHECK(unavailable.next_state == CaptureState::Unavailable);
    CHECK(unavailable.hide_overlay);
    CHECK(!unavailable.arm_retry);

    const auto dirty = g3d::capture::AdvanceCaptureState(
        CaptureState::Unavailable, CaptureSignal::BindingDirty);
    CHECK(dirty.next_state == CaptureState::Rebinding);
    CHECK(dirty.arm_retry);
    return true;
}

static bool TestRetryBackoffIsBoundedAndDeterministic() {
    RetrySchedule retry;
    retry.Reset(100);
    CHECK(retry.CanAttempt(100));

    retry.RecordFailure(100);
    CHECK(retry.failures() == 1);
    CHECK(!retry.CanAttempt(349));
    CHECK(retry.CanAttempt(350));

    retry.RecordFailure(350);
    CHECK(retry.next_attempt_ms() == 850);
    retry.RecordFailure(850);
    CHECK(retry.next_attempt_ms() == 1850);
    retry.RecordFailure(1850);
    CHECK(retry.next_attempt_ms() == 3850);
    retry.RecordFailure(3850);
    CHECK(retry.next_attempt_ms() == 5850);
    return true;
}

int main() {
    const bool ok = TestFullOutputRegion()
        && TestContainedTargetRegion()
        && TestOutOfOutputTargetIsRejected()
        && TestRotationMapsUprightCornersToRawSurface()
        && TestTransitionsKeepTimeoutAndHideUnavailable()
        && TestRetryBackoffIsBoundedAndDeterministic();
    std::cout << (ok ? "capture recovery tests passed\n" : "capture recovery tests failed\n");
    return ok ? 0 : 1;
}
