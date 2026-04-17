import numpy as np

from tracker import depth_comparison


def test_compare_depth_stability_reports_regression_ratio(tmp_path):
    baseline = tmp_path / "baseline"
    captured = tmp_path / "captured"
    baseline.mkdir()
    captured.mkdir()
    np.save(baseline / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(baseline / "b.npy", np.full((2, 2), 0.004, dtype=np.float32))
    np.save(captured / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(captured / "b.npy", np.full((2, 2), 0.012, dtype=np.float32))

    result = depth_comparison.compare_depth_stability(captured, baseline)

    assert result.captured.quality == "GOOD"
    assert result.baseline.quality == "GOOD"
    assert result.mean_delta_ratio == 3.0
    assert result.regressed is True


def test_format_comparison_result_includes_ratio(tmp_path):
    baseline = tmp_path / "baseline"
    captured = tmp_path / "captured"
    baseline.mkdir()
    captured.mkdir()
    np.save(baseline / "a.npy", np.zeros((1, 1), dtype=np.float32))
    np.save(baseline / "b.npy", np.full((1, 1), 0.01, dtype=np.float32))
    np.save(captured / "a.npy", np.zeros((1, 1), dtype=np.float32))
    np.save(captured / "b.npy", np.full((1, 1), 0.01, dtype=np.float32))

    result = depth_comparison.compare_depth_stability(captured, baseline)
    text = depth_comparison.format_comparison_result(result)

    assert "mean_delta_ratio=1.00" in text
    assert "regressed=False" in text


def test_main_returns_nonzero_for_regression(tmp_path, capsys):
    baseline = tmp_path / "baseline"
    captured = tmp_path / "captured"
    baseline.mkdir()
    captured.mkdir()
    np.save(baseline / "a.npy", np.zeros((1, 1), dtype=np.float32))
    np.save(baseline / "b.npy", np.full((1, 1), 0.01, dtype=np.float32))
    np.save(captured / "a.npy", np.zeros((1, 1), dtype=np.float32))
    np.save(captured / "b.npy", np.full((1, 1), 0.05, dtype=np.float32))

    code = depth_comparison.main([str(captured), str(baseline), "--max-ratio", "2.0"])

    assert code == 1
    assert "regressed=True" in capsys.readouterr().out
