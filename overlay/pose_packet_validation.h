#pragma once
#include <cmath>
#include <cstdint>

namespace g3d::pose {
template <typename Packet>
inline bool ValidatePacket(const Packet& packet, uint32_t magic, uint32_t version) {
    if (packet.magic != magic || packet.version != version) return false;
    const float values[] = {packet.x, packet.y, packet.z, packet.vx, packet.vy, packet.vz,
        packet.yaw, packet.pitch, packet.roll, packet.confidence};
    for (float value : values) if (!std::isfinite(value)) return false;
    return packet.z > 0.0f && packet.confidence >= 0.0f && packet.confidence <= 1.0f;
}
} // namespace g3d::pose
