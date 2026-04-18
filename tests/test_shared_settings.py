import struct
from tracker.shared_settings import (
    STRUCT_FORMAT, STRUCT_SIZE, OverlaySettings,
    SharedSettingsWriter, SharedSettingsReader,
)

def test_struct_size_is_64():
    assert STRUCT_SIZE == 64

def test_struct_format_roundtrip():
    s = OverlaySettings(
        strength_x=1.5, strength_y=2.0, virtual_depth_cm=30.0,
        screen_w_cm=59.8, screen_h_cm=33.6, depth_curve=1,
        depth_gamma=1.0, focus_radius=0.1, head_dist_cm=60.0,
        camera_fov_deg=90.0, ipd_mm=64.0, smoothing_alpha=0.1,
        deadzone_mm=5.0, display_backend=1, depth_mode=2,
    )
    data = struct.pack(
        STRUCT_FORMAT,
        s.strength_x, s.strength_y, s.virtual_depth_cm, s.screen_w_cm,
        s.screen_h_cm, s.depth_curve, s.depth_gamma, s.focus_radius,
        s.head_dist_cm, s.camera_fov_deg, s.ipd_mm, s.smoothing_alpha,
        s.deadzone_mm, s.display_backend, s.depth_mode, 1,
    )
    assert len(data) == 64
    out = struct.unpack(STRUCT_FORMAT, data)
    assert abs(out[0] - 1.5) < 1e-5
    assert out[5] == 1       # depth_curve uint32
    assert abs(out[11] - 0.1) < 1e-5  # smoothing_alpha
    assert out[13] == 1       # display_backend uint32
    assert out[14] == 2       # depth_mode uint32


def test_reader_returns_none_when_no_writer():
    """Reader returns None gracefully when no writer is running."""
    reader = SharedSettingsReader(name="G3D_SETTINGS_NOWRITER_TEST")
    try:
        assert reader.read() is None
    finally:
        reader.close()


def test_writer_reader_roundtrip():
    """Write settings via Writer, read them back via Reader."""
    settings = OverlaySettings(
        strength_x=2.0, strength_y=1.5, virtual_depth_cm=40.0,
        screen_w_cm=59.8, screen_h_cm=33.6, depth_curve=2,
        depth_gamma=0.8, focus_radius=0.15, head_dist_cm=55.0,
        camera_fov_deg=75.0, ipd_mm=63.0, smoothing_alpha=0.05,
        deadzone_mm=3.0, display_backend=2, depth_mode=2,
    )
    with SharedSettingsWriter(name="G3D_SETTINGS_ROUNDTRIP_TEST") as writer:
        writer.write(settings)
        reader = SharedSettingsReader(name="G3D_SETTINGS_ROUNDTRIP_TEST")
        try:
            result = reader.read()
            assert result is not None
            assert abs(result.strength_x - 2.0) < 1e-5
            assert abs(result.strength_y - 1.5) < 1e-5
            assert result.depth_curve == 2
            assert abs(result.depth_gamma - 0.8) < 1e-5
            assert abs(result.deadzone_mm - 3.0) < 1e-5
            assert result.display_backend == 2
            assert result.depth_mode == 2
        finally:
            reader.close()


def test_writer_context_manager():
    """SharedSettingsWriter works as a context manager and close() is idempotent."""
    with SharedSettingsWriter(name="G3D_SETTINGS_CTX_TEST") as writer:
        writer.write(OverlaySettings())
    # No assertion needed — no exception means success
