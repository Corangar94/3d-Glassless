import numpy as np

from tracker import depth_benchmark


def test_load_depth_frames_reads_npy_files_in_sorted_order(tmp_path):
    np.save(tmp_path / "frame_002.npy", np.full((2, 2), 2.0, dtype=np.float32))
    np.save(tmp_path / "frame_001.npy", np.full((2, 2), 1.0, dtype=np.float32))

    frames = depth_benchmark.load_depth_frames(tmp_path)

    assert len(frames) == 2
    assert frames[0][0, 0] == 1.0
    assert frames[1][0, 0] == 2.0


def test_run_benchmark_reports_classification_and_metrics(tmp_path):
    np.save(tmp_path / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(tmp_path / "b.npy", np.full((2, 2), 0.01, dtype=np.float32))

    result = depth_benchmark.run_benchmark(tmp_path)

    assert result.quality == "GOOD"
    assert result.metrics.frame_count == 2
    assert "quality=GOOD" in depth_benchmark.format_benchmark_result(result)
    assert "mean_abs_delta=0.0100" in depth_benchmark.format_benchmark_result(result)


def test_run_benchmark_raises_when_directory_has_no_frames(tmp_path):
    try:
        depth_benchmark.run_benchmark(tmp_path)
    except ValueError as e:
        assert "no .npy depth frames" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_main_returns_nonzero_for_danger_quality(tmp_path, capsys):
    np.save(tmp_path / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(tmp_path / "b.npy", np.full((2, 2), 0.4, dtype=np.float32))

    code = depth_benchmark.main([str(tmp_path)])

    assert code == 1
    assert "quality=DANGER" in capsys.readouterr().out


def test_main_returns_zero_for_good_quality(tmp_path, capsys):
    np.save(tmp_path / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(tmp_path / "b.npy", np.full((2, 2), 0.01, dtype=np.float32))

    code = depth_benchmark.main([str(tmp_path)])

    assert code == 0
    assert "quality=GOOD" in capsys.readouterr().out
