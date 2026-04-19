import json

import numpy as np

from tracker import depth_fixtures


def _write_fixture(root):
    fixture_dir = root / "stable"
    fixture_dir.mkdir(parents=True)
    np.save(fixture_dir / "frame_0000.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(fixture_dir / "frame_0001.npy", np.full((2, 2), 0.01, dtype=np.float32))
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "fixtures": [
                    {
                        "name": "stable",
                        "directory": "stable",
                        "kind": "captured",
                        "source": "overlay depth debug",
                        "description": "stable live capture",
                        "frame_count": 2,
                        "width": 2,
                        "height": 2,
                        "expected_quality": "GOOD",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return fixture_dir


def test_load_fixture_manifest_resolves_fixture_directories(tmp_path):
    fixture_dir = _write_fixture(tmp_path)

    fixtures = depth_fixtures.load_fixture_manifest(tmp_path)

    assert len(fixtures) == 1
    assert fixtures[0].name == "stable"
    assert fixtures[0].path == fixture_dir
    assert fixtures[0].expected_quality == "GOOD"


def test_benchmark_fixture_runs_depth_stability_for_named_fixture(tmp_path):
    _write_fixture(tmp_path)

    result = depth_fixtures.benchmark_fixture("stable", tmp_path)

    assert result.fixture.name == "stable"
    assert result.result.quality == "GOOD"
    assert result.result.metrics.frame_count == 2


def test_main_lists_fixtures(tmp_path, capsys):
    _write_fixture(tmp_path)

    code = depth_fixtures.main(["--root", str(tmp_path), "--list"])

    assert code == 0
    assert "stable" in capsys.readouterr().out


def test_main_benchmarks_all_fixtures(tmp_path, capsys):
    _write_fixture(tmp_path)

    code = depth_fixtures.main(["--root", str(tmp_path), "--benchmark-all"])

    assert code == 0
    output = capsys.readouterr().out
    assert "stable" in output
    assert "quality=GOOD" in output


def test_default_fixture_manifest_includes_smoke_sequence():
    fixtures = depth_fixtures.load_fixture_manifest()

    assert any(fixture.name == "synthetic_static_smoke" for fixture in fixtures)

    result = depth_fixtures.benchmark_fixture("synthetic_static_smoke")

    assert result.result.quality == "GOOD"
