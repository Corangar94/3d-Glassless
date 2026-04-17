import numpy as np
import pytest

from tracker.display_backends import build_display_layout
from tracker.depth_reprojection import synthesize_views


def test_synthesize_views_returns_original_for_center_view():
    image = np.arange(12, dtype=np.float32).reshape(2, 2, 3)
    depth = np.full((2, 2), 0.5, dtype=np.float32)

    views = synthesize_views(image, depth, view_offsets=[0.0], max_parallax_px=4)

    assert len(views) == 1
    assert np.array_equal(views[0], image)


def test_synthesize_views_warps_left_and_right_differently():
    image = np.arange(15, dtype=np.float32).reshape(1, 5, 3)
    depth = np.ones((1, 5), dtype=np.float32)

    left, right = synthesize_views(image, depth, view_offsets=[-1.0, 1.0], max_parallax_px=1)

    assert np.array_equal(left[0, 0], image[0, 1])
    assert np.array_equal(right[0, 1], image[0, 0])
    assert not np.array_equal(left, right)


def test_synthesize_views_rejects_depth_shape_mismatch():
    image = np.zeros((2, 2, 3), dtype=np.float32)
    depth = np.zeros((1, 2), dtype=np.float32)

    with pytest.raises(ValueError, match="depth shape"):
        synthesize_views(image, depth, view_offsets=[0.0])


def test_synthesize_views_fills_low_confidence_samples():
    image = np.arange(12, dtype=np.float32).reshape(2, 2, 3)
    depth = np.full((2, 2), 0.5, dtype=np.float32)
    confidence = np.array([[True, False], [True, True]])

    view = synthesize_views(
        image,
        depth,
        view_offsets=[0.0],
        confidence_mask=confidence,
        fill_value=-1.0,
    )[0]

    assert np.array_equal(view[0, 0], image[0, 0])
    assert np.array_equal(view[0, 1], np.full(3, -1.0))


def test_synthesize_views_rejects_confidence_shape_mismatch():
    image = np.zeros((2, 2, 3), dtype=np.float32)
    depth = np.zeros((2, 2), dtype=np.float32)
    confidence = np.ones((1, 2), dtype=bool)

    with pytest.raises(ValueError, match="confidence"):
        synthesize_views(image, depth, view_offsets=[0.0], confidence_mask=confidence)


def test_synthesize_views_matches_quilt_layout_view_count():
    image = np.zeros((2, 2, 3), dtype=np.float32)
    depth = np.full((2, 2), 0.5, dtype=np.float32)
    layout = build_display_layout("lightfield_quilt")

    views = synthesize_views(image, depth, layout.view_offsets)

    assert len(views) == layout.view_count
