import numpy as np
from PIL import Image

from tracker import view_renderer


def test_compose_view_grid_packs_views_by_rows():
    views = [
        np.full((1, 2, 3), index, dtype=np.uint8)
        for index in range(4)
    ]

    grid = view_renderer.compose_view_grid(views, columns=2, rows=2)

    assert grid.shape == (2, 4, 3)
    assert np.all(grid[0, 0:2] == 0)
    assert np.all(grid[0, 2:4] == 1)
    assert np.all(grid[1, 0:2] == 2)
    assert np.all(grid[1, 2:4] == 3)


def test_render_backend_views_uses_display_layout_count():
    image = np.zeros((2, 2, 3), dtype=np.uint8)
    depth = np.full((2, 2), 0.5, dtype=np.float32)

    views = view_renderer.render_backend_views(image, depth, "stereo_autostereo")

    assert len(views) == 2


def test_render_backend_views_passes_confidence_mask_to_reprojection():
    image = np.full((1, 2, 3), 9, dtype=np.uint8)
    depth = np.full((1, 2), 0.5, dtype=np.float32)
    confidence = np.array([[True, False]])

    view = view_renderer.render_backend_views(
        image,
        depth,
        "desktop_overlay",
        confidence_mask=confidence,
        fill_value=0,
    )[0]

    assert np.array_equal(view[0, 0], np.full(3, 9))
    assert np.array_equal(view[0, 1], np.zeros(3))


def test_main_writes_quilt_png(tmp_path):
    image_path = tmp_path / "image.png"
    depth_path = tmp_path / "depth.npy"
    output_path = tmp_path / "quilt.png"
    Image.fromarray(np.zeros((2, 2, 3), dtype=np.uint8)).save(image_path)
    np.save(depth_path, np.full((2, 2), 0.5, dtype=np.float32))

    code = view_renderer.main([
        str(image_path),
        str(depth_path),
        str(output_path),
        "--backend",
        "stereo_autostereo",
    ])

    assert code == 0
    assert Image.open(output_path).size == (4, 2)


def test_main_accepts_confidence_mask(tmp_path):
    image_path = tmp_path / "image.png"
    depth_path = tmp_path / "depth.npy"
    confidence_path = tmp_path / "confidence.npy"
    output_path = tmp_path / "masked.png"
    Image.fromarray(np.full((1, 2, 3), 255, dtype=np.uint8)).save(image_path)
    np.save(depth_path, np.full((1, 2), 0.5, dtype=np.float32))
    np.save(confidence_path, np.array([[True, False]]))

    code = view_renderer.main([
        str(image_path),
        str(depth_path),
        str(output_path),
        "--backend",
        "desktop_overlay",
        "--confidence-mask",
        str(confidence_path),
        "--fill-value",
        "0",
    ])

    assert code == 0
    assert np.array(Image.open(output_path))[0, 1, 0] == 0
