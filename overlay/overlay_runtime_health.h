#pragma once

#include <cstdint>

namespace g3d::runtime_health {

// Visibility is a pure decision. It must neither submit inference nor retire
// the captured image while the worker is still producing its first result.
inline bool OverlayVisible(bool running, bool has_frame, bool depth_ready,
                           bool target_foreground) {
    return running && has_frame && depth_ready && target_foreground;
}

class DepthRecoveryEpisode {
public:
    void MarkFailure() {
        active_ = true;
        awaiting_session_ = true;
        has_healthy_start_ = false;
    }

    // Session creation is not recovery evidence. It only permits observations
    // from this new session to start a fresh sustained-health window.
    void SessionStarted() {
        awaiting_session_ = false;
        has_healthy_start_ = false;
        last_publications_ = 0;
    }

    bool Observe(uint64_t now_ms, uint64_t publications,
                 bool healthy, bool complete_held_frame) {
        if (!active_ || awaiting_session_) return false;
        if (!healthy || publications == 0 || publications < last_publications_) {
            has_healthy_start_ = false;
            last_publications_ = publications;
            return false;
        }
        last_publications_ = publications;
        if (!has_healthy_start_ || now_ms < healthy_since_ms_) {
            has_healthy_start_ = true;
            healthy_since_ms_ = now_ms;
            return false;
        }
        // A changing scene needs repeated publication. A fully covered static
        // image can instead prove sustained health without inventing new frames.
        if (now_ms - healthy_since_ms_ < 2000
            || (publications < 3 && !complete_held_frame)) return false;
        active_ = false;
        has_healthy_start_ = false;
        return true;
    }

    bool active() const { return active_; }

private:
    bool active_ = false, awaiting_session_ = false, has_healthy_start_ = false;
    uint64_t healthy_since_ms_ = 0, last_publications_ = 0;
};
}  // namespace g3d::runtime_health
