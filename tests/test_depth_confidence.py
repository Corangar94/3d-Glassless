import numpy as np
import pytest

from tracker import depth_confidence


def test_compute_spatial_confidence_rejects_invalid_and_out_of_range_pixels():
    depth = np.array([[0.5, np.nan], [-0.1, 1.2]], dtype=np.float32)

    mask = depth_confidence.compute_spatial_confidence(depth)

    assert mask.tolist() == [[True, False], [False, False]]


def test_compute_spatial_confidence_rejects_large_depth_gradient():
    depth = np.array([[0.1, 0.9, 0.1]], dtype=np.float32)

    mask = depth_confidence.compute_spatial_confidence(depth, max_gradient=0.25)

    assert mask.tolist() == [[False, False, False]]


def test_compute_temporal_confidence_rejects_unstable_pixels():
    previous = np.array([[0.2, 0.2]], dtype=np.float32)
    current = np.array([[0.21, 0.5]], dtype=np.float32)

    mask = depth_confidence.compute_temporal_confidence(previous, current, max_delta=0.05)

    assert mask.tolist() == [[True, False]]


def test_combine_confidence_masks_uses_logical_and():
    a = np.array([[True, False], [True, True]])
    b = np.array([[True, True], [False, True]])

    mask = depth_confidence.combine_confidence_masks(a, b)

    assert mask.tolist() == [[True, False], [False, True]]


def test_confidence_helpers_reject_shape_mismatch():
    with pytest.raises(ValueError, match="same shape"):
        depth_confidence.compute_temporal_confidence(np.zeros((1, 2)), np.zeros((2, 1)))


def test_main_writes_confidence_mask(tmp_path):
    depth = tmp_path / "depth.npy"
    output = tmp_path / "confidence.npy"
    np.save(depth, np.array([[0.5, np.nan]], dtype=np.float32))

    code = depth_confidence.main([str(depth), str(output)])

    assert code == 0
    assert np.load(output).tolist() == [[True, False]]
