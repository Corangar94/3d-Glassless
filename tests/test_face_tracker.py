from tracker.face_tracker import estimate_z_cm, estimate_xy_cm, FaceTracker


def test_estimate_z_closer_when_eyes_further_apart():
    """Wider pixel eye distance → head is closer to camera."""
    z_close = estimate_z_cm(
        ipd_px=120.0, image_width=1280,
        real_ipd_cm=6.3, camera_fov_deg=60.0
    )
    z_far = estimate_z_cm(
        ipd_px=60.0, image_width=1280,
        real_ipd_cm=6.3, camera_fov_deg=60.0
    )
    assert z_close < z_far


def test_estimate_z_positive():
    """Z should always be a positive distance."""
    z = estimate_z_cm(ipd_px=80.0, image_width=1280,
                      real_ipd_cm=6.3, camera_fov_deg=60.0)
    assert z > 0.0


def test_estimate_xy_centred_gives_zero():
    """Nose at image centre → X and Y offset should be near zero."""
    x, y = estimate_xy_cm(
        nose_x_norm=0.5, nose_y_norm=0.5,
        screen_width_cm=59.8, screen_height_cm=33.6
    )
    assert abs(x) < 0.01
    assert abs(y) < 0.01


def test_estimate_xy_right_of_centre():
    """Nose right of centre → positive X offset."""
    x, _ = estimate_xy_cm(
        nose_x_norm=0.7, nose_y_norm=0.5,
        screen_width_cm=59.8, screen_height_cm=33.6
    )
    assert x > 0.0


def test_face_tracker_init():
    """FaceTracker constructs without error."""
    tracker = FaceTracker(
        real_ipd_cm=6.3,
        screen_width_cm=59.8,
        screen_height_cm=33.6,
        camera_fov_deg=60.0,
    )
    assert tracker is not None
    tracker.close()
