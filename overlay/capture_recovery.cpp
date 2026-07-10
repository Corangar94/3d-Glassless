#include "capture_recovery.h"

#include <algorithm>
#include <limits>

namespace g3d::capture {

bool IsValidRect(const Rect& rect) {
    return rect.right > rect.left && rect.bottom > rect.top;
}

bool ContainsRect(const Rect& outer, const Rect& inner) {
    return IsValidRect(outer) && IsValidRect(inner)
        && inner.left >= outer.left
        && inner.top >= outer.top
        && inner.right <= outer.right
        && inner.bottom <= outer.bottom;
}

std::optional<Region> BuildUprightCaptureRegion(
    const Rect& output_rect,
    const std::optional<Rect>& target_rect) {
    if (!IsValidRect(output_rect)) return std::nullopt;

    const Rect selected = target_rect.value_or(output_rect);
    if (!ContainsRect(output_rect, selected)) return std::nullopt;

    const int64_t width = static_cast<int64_t>(selected.right) - selected.left;
    const int64_t height = static_cast<int64_t>(selected.bottom) - selected.top;
    const int64_t left = static_cast<int64_t>(selected.left) - output_rect.left;
    const int64_t top = static_cast<int64_t>(selected.top) - output_rect.top;
    if (left < 0 || top < 0 || width <= 0 || height <= 0
        || width > std::numeric_limits<uint32_t>::max()
        || height > std::numeric_limits<uint32_t>::max()) {
        return std::nullopt;
    }
    return Region{
        static_cast<uint32_t>(left),
        static_cast<uint32_t>(top),
        static_cast<uint32_t>(width),
        static_cast<uint32_t>(height),
    };
}

Uv UprightToRawUv(Rotation rotation, Uv upright) {
    switch (rotation) {
    case Rotation::Identity:  return upright;
    case Rotation::Rotate90:  return Uv{upright.v, 1.0f - upright.u};
    case Rotation::Rotate180: return Uv{1.0f - upright.u, 1.0f - upright.v};
    case Rotation::Rotate270: return Uv{1.0f - upright.v, upright.u};
    }
    return upright;
}

RecoveryAction AdvanceCaptureState(CaptureState state, CaptureSignal signal) {
    switch (signal) {
    case CaptureSignal::FrameReady:
        return {CaptureState::Running, true, false, false, false};
    case CaptureSignal::FrameTimeout:
        return {state, true, state != CaptureState::Running, false, false};
    case CaptureSignal::DuplicationLost:
    case CaptureSignal::RebindRetry:
        return {CaptureState::Rebinding, false, true, true, false};
    case CaptureSignal::DuplicationUnavailable:
        return {CaptureState::Unavailable, false, true, false, false};
    case CaptureSignal::DeviceLost:
        return {CaptureState::DeviceRecovery, false, true, true, true};
    case CaptureSignal::DeviceRecreated:
        return {CaptureState::Rebinding, false, true, true, false};
    case CaptureSignal::RebindSucceeded:
        return {CaptureState::Running, false, false, false, false};
    case CaptureSignal::BindingDirty:
        return {CaptureState::Rebinding, false, true, true, false};
    }
    return {state, state == CaptureState::Running, state != CaptureState::Running, false, false};
}

void RetrySchedule::Reset(uint64_t now_ms) {
    failures_ = 0;
    next_attempt_ms_ = now_ms;
}

bool RetrySchedule::CanAttempt(uint64_t now_ms) const {
    return now_ms >= next_attempt_ms_;
}

void RetrySchedule::RecordFailure(uint64_t now_ms) {
    static constexpr uint32_t kDelaysMs[] = {250, 500, 1000, 2000};
    const uint32_t index = std::min<uint32_t>(failures_, 3);
    ++failures_;
    next_attempt_ms_ = now_ms + kDelaysMs[index];
}

uint32_t RetrySchedule::failures() const {
    return failures_;
}

uint64_t RetrySchedule::next_attempt_ms() const {
    return next_attempt_ms_;
}

}  // namespace g3d::capture
