#pragma once

#include <cstdint>
#include <optional>

namespace g3d::capture {

struct Rect {
    int32_t left;
    int32_t top;
    int32_t right;
    int32_t bottom;
};

struct Region {
    uint32_t left;
    uint32_t top;
    uint32_t width;
    uint32_t height;
};

bool IsValidRect(const Rect& rect);
bool ContainsRect(const Rect& outer, const Rect& inner);
std::optional<Region> BuildUprightCaptureRegion(
    const Rect& output_rect,
    const std::optional<Rect>& target_rect);

enum class Rotation : uint8_t {
    Identity,
    Rotate90,
    Rotate180,
    Rotate270,
};

struct Uv {
    float u;
    float v;
};

Uv UprightToRawUv(Rotation rotation, Uv upright);

enum class CaptureState : uint8_t {
    Running,
    Rebinding,
    DeviceRecovery,
    Unavailable,
};

enum class CaptureSignal : uint8_t {
    FrameReady,
    FrameTimeout,
    DuplicationLost,
    DuplicationUnavailable,
    DeviceLost,
    DeviceRecreated,
    RebindSucceeded,
    RebindRetry,
    BindingDirty,
};

struct RecoveryAction {
    CaptureState next_state;
    bool keep_last_frame;
    bool hide_overlay;
    bool arm_retry;
    bool rebuild_device;
};

RecoveryAction AdvanceCaptureState(CaptureState state, CaptureSignal signal);

class RetrySchedule {
public:
    void Reset(uint64_t now_ms);
    bool CanAttempt(uint64_t now_ms) const;
    void RecordFailure(uint64_t now_ms);
    uint32_t failures() const;
    uint64_t next_attempt_ms() const;

private:
    uint32_t failures_ = 0;
    uint64_t next_attempt_ms_ = 0;
};

}  // namespace g3d::capture
