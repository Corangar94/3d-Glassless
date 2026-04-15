import struct
from tracker.shared_settings import STRUCT_FORMAT, STRUCT_SIZE, OverlaySettings

def test_struct_size_is_56():
    assert STRUCT_SIZE == 56

def test_struct_format_roundtrip():
    s = OverlaySettings(
        strength_x=1.5, strength_y=2.0, virtual_depth_cm=30.0,
        screen_w_cm=59.8, screen_h_cm=33.6, depth_curve=1,
        depth_gamma=1.0, focus_radius=0.1, head_dist_cm=60.0,
        camera_fov_deg=90.0, ipd_mm=64.0, smoothing_alpha=0.1,
        deadzone_mm=5.0,
    )
    data = struct.pack(
        STRUCT_FORMAT,
        s.strength_x, s.strength_y, s.virtual_depth_cm, s.screen_w_cm,
        s.screen_h_cm, s.depth_curve, s.depth_gamma, s.focus_radius,
        s.head_dist_cm, s.camera_fov_deg, s.ipd_mm, s.smoothing_alpha,
        s.deadzone_mm, 1,
    )
    assert len(data) == 56
    out = struct.unpack(STRUCT_FORMAT, data)
    assert abs(out[0] - 1.5) < 1e-5
    assert out[5] == 1       # depth_curve uint32
    assert abs(out[11] - 0.1) < 1e-5  # smoothing_alpha
