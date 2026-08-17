import numpy as np
import yaml

from tracker import stereo_validation


def test_generate_validation_card_contains_depth_ramp_and_foreground_occluder():
    image, depth = stereo_validation.generate_validation_card(width=64, height=32)

    assert image.shape == (32, 64, 3)
    assert image.dtype == np.uint8
    assert depth.shape == (32, 64)
    assert depth.dtype == np.float32
    assert depth[16, 4] < depth[16, 56]
    assert depth[16, 32] < depth[16, 4]


def test_write_validation_assets_creates_source_depth_and_backend_grid(tmp_path):
    result = stereo_validation.write_validation_assets(
        tmp_path,
        backend_id="stereo_autostereo",
        width=64,
        height=32,
        max_parallax_px=4.0,
    )

    assert result.image_path.is_file()
    assert result.depth_path.is_file()
    assert result.output_path.is_file()
    saved_depth = np.load(result.depth_path)
    assert saved_depth.shape == (32, 64)


def test_validation_assets_honor_half_sbs_layout(tmp_path):
    result = stereo_validation.write_validation_assets(
        tmp_path,
        backend_id="stereo_autostereo",
        width=64,
        height=32,
        stereo_layout="half_sbs",
    )

    from PIL import Image

    assert Image.open(result.output_path).size == (64, 32)


def test_main_accepts_top_bottom_validation_layout(tmp_path):
    code = stereo_validation.main([
        str(tmp_path),
        "--backend",
        "stereo_autostereo",
        "--width",
        "64",
        "--height",
        "32",
        "--stereo-layout",
        "top_bottom",
    ])

    from PIL import Image

    assert code == 0
    assert Image.open(tmp_path / "stereo_autostereo_validation.png").size == (64, 64)


def test_validation_assets_use_stereo_calibration_from_config(tmp_path):
    config_path = tmp_path / "config.yaml"
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

    result = stereo_validation.write_validation_assets(
        tmp_path / "assets",
        width=64,
        height=32,
        config_path=config_path,
    )

    from PIL import Image

    assert result.output_path.name == "stereo_autostereo_validation.png"
    assert Image.open(result.output_path).size == (64, 32)
