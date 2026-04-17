from tracker import performance_benchmark


def test_load_frame_timings_reads_csv_rows_in_order(tmp_path):
    csv_path = tmp_path / "timings.csv"
    csv_path.write_text(
        "timestamp_ms,frame_time_ms\n"
        "16,17.5\n"
        "0,16.0\n",
        encoding="utf-8",
    )

    samples = performance_benchmark.load_frame_timings(csv_path)

    assert [s.timestamp_ms for s in samples] == [0, 16]
    assert [s.frame_time_ms for s in samples] == [16.0, 17.5]


def test_run_benchmark_reports_quality_and_metrics(tmp_path):
    csv_path = tmp_path / "timings.csv"
    csv_path.write_text(
        "timestamp_ms,frame_time_ms\n"
        "0,15.0\n"
        "16,16.0\n",
        encoding="utf-8",
    )

    result = performance_benchmark.run_benchmark(csv_path, target_fps=60.0)

    assert result.quality == "GOOD"
    assert result.metrics.sample_count == 2
    text = performance_benchmark.format_benchmark_result(result)
    assert "quality=GOOD" in text
    assert "p95_frame_time_ms=" in text


def test_run_benchmark_raises_when_csv_has_no_samples(tmp_path):
    csv_path = tmp_path / "empty.csv"
    csv_path.write_text("timestamp_ms,frame_time_ms\n", encoding="utf-8")

    try:
        performance_benchmark.run_benchmark(csv_path)
    except ValueError as e:
        assert "no frame timing samples" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_main_returns_nonzero_for_danger_quality(tmp_path, capsys):
    csv_path = tmp_path / "timings.csv"
    csv_path.write_text(
        "timestamp_ms,frame_time_ms\n"
        "0,35.0\n"
        "35,40.0\n",
        encoding="utf-8",
    )

    code = performance_benchmark.main([str(csv_path)])

    assert code == 1
    assert "quality=DANGER" in capsys.readouterr().out


def test_main_returns_zero_for_good_quality(tmp_path, capsys):
    csv_path = tmp_path / "timings.csv"
    csv_path.write_text(
        "timestamp_ms,frame_time_ms\n"
        "0,15.0\n"
        "16,16.0\n",
        encoding="utf-8",
    )

    code = performance_benchmark.main([str(csv_path)])

    assert code == 0
    assert "quality=GOOD" in capsys.readouterr().out
