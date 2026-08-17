import numpy as np
import yaml
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


def test_render_backend_grid_can_swap_stereo_eye_order():
    left = np.full((1, 2, 3), 10, dtype=np.uint8)
    right = np.full((1, 2, 3), 20, dtype=np.uint8)

    grid = view_renderer.compose_stereo_grid(
        [left, right],
        eye_order="right_left",
        stereo_layout="full_sbs",
    )

    assert np.all(grid[:, 0:2] == 20)
    assert np.all(grid[:, 2:4] == 10)


def test_render_backend_grid_half_sbs_preserves_requested_output_width():
    image = np.zeros((2, 4, 3), dtype=np.uint8)
    depth = np.full((2, 4), 0.5, dtype=np.float32)

    grid = view_renderer.render_backend_grid(
        image,
        depth,
        "stereo_autostereo",
        stereo_layout="half_sbs",
    )

    assert grid.shape == image.shape


def test_compose_stereo_grid_top_bottom_stacks_ordered_views():
    left = np.full((1, 2, 3), 10, dtype=np.uint8)
    right = np.full((1, 2, 3), 20, dtype=np.uint8)

    grid = view_renderer.compose_stereo_grid(
        [left, right],
        eye_order="right_left",
        stereo_layout="top_bottom",
    )

    assert grid.shape == (2, 2, 3)
    assert np.all(grid[0:1, :] == 20)
    assert np.all(grid[1:2, :] == 10)


def test_compose_stereo_grid_half_top_bottom_preserves_requested_output_height():
    left = np.full((4, 2, 3), 10, dtype=np.uint8)
    right = np.full((4, 2, 3), 20, dtype=np.uint8)

    grid = view_renderer.compose_stereo_grid(
        [left, right],
        stereo_layout="half_top_bottom",
    )

    assert grid.shape == (4, 2, 3)
    assert np.all(grid[0:2, :] == 10)
    assert np.all(grid[2:4, :] == 20)


def test_compose_stereo_grid_anaglyph_combines_left_red_with_right_cyan():
    left = np.array([[[10, 11, 12], [20, 21, 22]]], dtype=np.uint8)
    right = np.array([[[100, 101, 102], [200, 201, 202]]], dtype=np.uint8)

    grid = view_renderer.compose_stereo_grid(
        [left, right],
        stereo_layout="anaglyph",
    )

    assert grid.shape == left.shape
    assert np.array_equal(
        grid,
        np.array([[[10, 101, 102], [20, 201, 202]]], dtype=np.uint8),
    )


def test_compose_stereo_grid_crossview_places_right_then_left():
    left = np.full((1, 2, 3), 10, dtype=np.uint8)
    right = np.full((1, 2, 3), 20, dtype=np.uint8)

    grid = view_renderer.compose_stereo_grid(
        [left, right],
        stereo_layout="crossview",
    )

    assert np.all(grid[:, 0:2] == 20)
    assert np.all(grid[:, 2:4] == 10)


def test_compose_stereo_grid_parallelview_places_left_then_right():
    left = np.full((1, 2, 3), 10, dtype=np.uint8)
    right = np.full((1, 2, 3), 20, dtype=np.uint8)

    grid = view_renderer.compose_stereo_grid(
        [left, right],
        stereo_layout="parallelview",
    )

    assert np.all(grid[:, 0:2] == 10)
    assert np.all(grid[:, 2:4] == 20)


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
        "--stereo-layout",
        "half_sbs",
        "--eye-order",
        "right_left",
    ])

    assert code == 0
    assert Image.open(output_path).size == (2, 2)


def test_main_uses_stereo_layout_and_eye_order_from_config(tmp_path):
    image_path = tmp_path / "image.png"
    depth_path = tmp_path / "depth.npy"
    output_path = tmp_path / "configured.png"
    config_path = tmp_path / "config.yaml"
    Image.fromarray(np.zeros((2, 4, 3), dtype=np.uint8)).save(image_path)
    np.save(depth_path, np.full((2, 4), 0.5, dtype=np.float32))
    config_path.write_text(
        yaml.safe_dump(
            {
                "overlay": {
                    "display_backend": "stereo_autostereo",
                    "display_calibration": {
                        "stereo_layout": "half_sbs",
                        "eye_order": "right_left",
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    code = view_renderer.main([
        str(image_path),
        str(depth_path),
        str(output_path),
        "--config",
        str(config_path),
    ])

    assert code == 0
    assert Image.open(output_path).size == (4, 2)


def test_stereo_options_from_non_mapping_config_returns_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")

    assert view_renderer.stereo_options_from_config(config_path) == {}


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
