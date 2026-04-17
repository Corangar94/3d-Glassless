import json

from tracker import latency_evaluation


def test_compute_latency_metrics_reports_percentiles_and_budget_rate():
    samples = [
        latency_evaluation.LatencySample(timestamp_ms=0, tracking_to_display_ms=10.0),
        latency_evaluation.LatencySample(timestamp_ms=16, tracking_to_display_ms=20.0),
        latency_evaluation.LatencySample(timestamp_ms=32, tracking_to_display_ms=30.0),
    ]

    metrics = latency_evaluation.compute_latency_metrics(samples, target_ms=20.0)

    assert metrics.sample_count == 3
    assert metrics.avg_latency_ms == 20.0
    assert metrics.p95_latency_ms == 30.0
    assert metrics.max_latency_ms == 30.0
    assert metrics.over_target_rate == 1 / 3


def test_classify_latency_flags_high_p95_latency():
    metrics = latency_evaluation.LatencyMetrics(
        sample_count=4,
        target_ms=20.0,
        avg_latency_ms=18.0,
        p95_latency_ms=45.0,
        max_latency_ms=45.0,
        over_target_rate=0.25,
    )

    assert latency_evaluation.classify_latency(metrics) == "DANGER"


def test_load_latency_csv_reads_captured_samples(tmp_path):
    path = tmp_path / "latency.csv"
    path.write_text(
        "timestamp_ms,tracking_to_display_ms\n"
        "0,10\n"
        "16,15\n",
        encoding="utf-8",
    )

    samples = latency_evaluation.load_latency_csv(path)

    assert len(samples) == 2
    assert samples[1].timestamp_ms == 16
    assert samples[1].tracking_to_display_ms == 15.0


def test_run_latency_benchmark_returns_quality_and_json(tmp_path):
    path = tmp_path / "latency.csv"
    path.write_text(
        "timestamp_ms,tracking_to_display_ms\n"
        "0,10\n"
        "16,12\n",
        encoding="utf-8",
    )

    result = latency_evaluation.run_benchmark(path, target_ms=20.0)
    data = json.loads(latency_evaluation.format_benchmark_json(result))

    assert result.quality == "GOOD"
    assert data["metrics"]["p95_latency_ms"] == 12.0


def test_main_returns_nonzero_for_danger(tmp_path, capsys):
    path = tmp_path / "latency.csv"
    path.write_text(
        "timestamp_ms,tracking_to_display_ms\n"
        "0,50\n",
        encoding="utf-8",
    )

    code = latency_evaluation.main([str(path), "--target-ms", "20"])

    assert code == 1
    assert "quality=DANGER" in capsys.readouterr().out
