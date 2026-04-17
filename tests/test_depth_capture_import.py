import numpy as np
from PIL import Image

from tracker import depth_capture_import


def test_import_depth_images_writes_sorted_npy_frames(tmp_path):
    source = tmp_path / "screens"
    output = tmp_path / "depth"
    source.mkdir()
    Image.fromarray(np.full((2, 2), 64, dtype=np.uint8)).save(source / "b.png")
    Image.fromarray(np.full((2, 2), 128, dtype=np.uint8)).save(source / "a.png")

    count = depth_capture_import.import_depth_images(source, output)

    assert count == 2
    files = sorted(output.glob("*.npy"))
    assert [file.name for file in files] == ["frame_0000.npy", "frame_0001.npy"]
    assert np.load(files[0])[0, 0] == np.float32(128 / 255)
    assert np.load(files[1])[0, 0] == np.float32(64 / 255)


def test_main_imports_depth_screenshot_directory(tmp_path, capsys):
    source = tmp_path / "screens"
    output = tmp_path / "depth"
    source.mkdir()
    Image.fromarray(np.zeros((1, 1), dtype=np.uint8)).save(source / "depth.png")

    code = depth_capture_import.main([str(source), str(output)])

    assert code == 0
    assert (output / "frame_0000.npy").exists()
    assert "imported 1 depth frames" in capsys.readouterr().out
