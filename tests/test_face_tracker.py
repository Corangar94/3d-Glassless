import pytest
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
        z_cm=60.0, camera_fov_deg=60.0, image_width=1280, image_height=720,
    )
    assert abs(x) < 0.01
    assert abs(y) < 0.01


def test_estimate_xy_right_of_centre():
    """Nose right of centre → positive X offset (camera x is mirrored → right user = left image)."""
    x, _ = estimate_xy_cm(
        nose_x_norm=0.3, nose_y_norm=0.5,  # left of image = user's right (mirrored)
        z_cm=60.0, camera_fov_deg=60.0, image_width=1280, image_height=720,
    )
    assert x > 0.0


def test_face_tracker_init():
    """FaceTracker constructs without error and is a context manager."""
    import os
    model_path = os.path.join(os.path.dirname(__file__), '..', 'models', 'face_landmarker.task')
    with FaceTracker(
        real_ipd_cm=6.3,
        screen_width_cm=59.8,
        screen_height_cm=33.6,
        camera_fov_deg=60.0,
        model_path=model_path,
    ) as tracker:
        assert tracker._landmarker is not None


def test_estimate_xy_above_centre_gives_positive_y():
    """Nose above centre (y_norm < 0.5) → positive Y offset."""
    _, y = estimate_xy_cm(
        nose_x_norm=0.5, nose_y_norm=0.3,
        z_cm=60.0, camera_fov_deg=60.0, image_width=1280, image_height=720,
    )
    assert y > 0.0


def test_estimate_xy_below_centre_gives_negative_y():
    """Nose below centre (y_norm > 0.5) → negative Y offset."""
    _, y = estimate_xy_cm(
        nose_x_norm=0.5, nose_y_norm=0.7,
        z_cm=60.0, camera_fov_deg=60.0, image_width=1280, image_height=720,
    )
    assert y < 0.0


def test_estimate_xy_left_of_image_gives_positive_x():
    """Nose left of image centre (x_norm < 0.5) → positive X (user's right, mirrored camera)."""
    x, _ = estimate_xy_cm(
        nose_x_norm=0.3, nose_y_norm=0.5,
        z_cm=60.0, camera_fov_deg=60.0, image_width=1280, image_height=720,
    )
    assert x > 0.0


def test_face_tracker_rejects_zero_fov():
    """Zero camera FOV must raise ValueError."""
    with pytest.raises(ValueError):
        FaceTracker(
            real_ipd_cm=6.3,
            screen_width_cm=59.8,
            screen_height_cm=33.6,
            camera_fov_deg=0.0,
        )
