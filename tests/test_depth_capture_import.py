import json

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


def test_main_registers_imported_capture_in_fixture_manifest(tmp_path):
    source = tmp_path / "screens"
    fixture_root = tmp_path / "fixtures"
    output = fixture_root / "live_overlay_smoke"
    source.mkdir()
    Image.fromarray(np.zeros((3, 4), dtype=np.uint8)).save(source / "depth.png")

    code = depth_capture_import.main(
        [
            str(source),
            str(output),
            "--fixture-root",
            str(fixture_root),
            "--fixture-name",
            "live_overlay_smoke",
            "--description",
            "Live overlay depth-debug capture smoke fixture.",
            "--expected-quality",
            "WARN",
        ]
    )

    assert code == 0
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["fixtures"] == [
        {
            "name": "live_overlay_smoke",
            "directory": "live_overlay_smoke",
            "kind": "captured",
            "source": "overlay depth-debug screenshots",
            "description": "Live overlay depth-debug capture smoke fixture.",
            "frame_count": 1,
            "width": 4,
            "height": 3,
            "expected_quality": "WARN",
        }
    ]


def test_main_replaces_existing_fixture_manifest_entry(tmp_path):
    source = tmp_path / "screens"
    fixture_root = tmp_path / "fixtures"
    output = fixture_root / "live_overlay_smoke"
    source.mkdir()
    fixture_root.mkdir()
    Image.fromarray(np.zeros((2, 2), dtype=np.uint8)).save(source / "depth.png")
    (fixture_root / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "fixtures": [
                    {
                        "name": "live_overlay_smoke",
                        "directory": "old_dir",
                        "kind": "captured",
                        "source": "old",
                        "description": "old",
                        "frame_count": 99,
                        "width": 99,
                        "height": 99,
                    },
                    {
                        "name": "synthetic_static_smoke",
                        "directory": "synthetic_static_smoke",
                        "kind": "synthetic",
                        "source": "synthetic",
                        "description": "synthetic",
                        "frame_count": 4,
                        "width": 8,
                        "height": 4,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    code = depth_capture_import.main(
        [
            str(source),
            str(output),
            "--fixture-root",
            str(fixture_root),
            "--fixture-name",
            "live_overlay_smoke",
        ]
    )

    assert code == 0
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in manifest["fixtures"]] == [
        "synthetic_static_smoke",
        "live_overlay_smoke",
    ]
    assert manifest["fixtures"][1]["directory"] == "live_overlay_smoke"
    assert manifest["fixtures"][1]["frame_count"] == 1
