import numpy as np

from tracker import evaluation_suite


def test_run_suite_combines_depth_and_performance_results(tmp_path):
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    np.save(depth_dir / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(depth_dir / "b.npy", np.full((2, 2), 0.01, dtype=np.float32))
    timings = tmp_path / "timings.csv"
    timings.write_text(
        "timestamp_ms,frame_time_ms\n"
        "0,15.0\n"
        "16,16.0\n",
        encoding="utf-8",
    )

    result = evaluation_suite.run_suite(depth_dir=depth_dir, timing_csv=timings)

    assert result.depth is not None
    assert result.performance is not None
    assert result.overall_quality == "GOOD"


def test_run_suite_overall_quality_uses_worst_result(tmp_path):
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    np.save(depth_dir / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(depth_dir / "b.npy", np.full((2, 2), 0.4, dtype=np.float32))
    timings = tmp_path / "timings.csv"
    timings.write_text(
        "timestamp_ms,frame_time_ms\n"
        "0,15.0\n"
        "16,16.0\n",
        encoding="utf-8",
    )

    result = evaluation_suite.run_suite(depth_dir=depth_dir, timing_csv=timings)

    assert result.depth is not None
    assert result.depth.quality == "DANGER"
    assert result.overall_quality == "DANGER"


def test_main_returns_nonzero_when_any_benchmark_is_danger(tmp_path, capsys):
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    np.save(depth_dir / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(depth_dir / "b.npy", np.full((2, 2), 0.4, dtype=np.float32))

    code = evaluation_suite.main(["--depth-dir", str(depth_dir)])

    assert code == 1
    assert "overall_quality=DANGER" in capsys.readouterr().out
