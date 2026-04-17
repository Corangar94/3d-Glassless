import json

from tracker import display_quality


def test_compute_display_quality_metrics_reports_usable_width_and_crosstalk():
    samples = [
        display_quality.DisplayMeasurementSample(x_cm=-10.0, z_cm=60.0, crosstalk_percent=8.0, view_locked=True),
        display_quality.DisplayMeasurementSample(x_cm=0.0, z_cm=60.0, crosstalk_percent=6.0, view_locked=True),
        display_quality.DisplayMeasurementSample(x_cm=12.0, z_cm=60.0, crosstalk_percent=18.0, view_locked=False),
    ]

    metrics = display_quality.compute_display_quality_metrics(samples)

    assert metrics.sample_count == 3
    assert metrics.usable_sample_count == 2
    assert metrics.usable_width_cm == 10.0
    assert metrics.avg_crosstalk_percent == 7.0
    assert metrics.max_crosstalk_percent == 18.0


def test_classify_display_quality_flags_missing_usable_viewing_zone():
    metrics = display_quality.DisplayQualityMetrics(
        sample_count=2,
        usable_sample_count=0,
        usable_width_cm=0.0,
        avg_crosstalk_percent=8.0,
        max_crosstalk_percent=12.0,
    )

    assert display_quality.classify_display_quality(metrics) == "DANGER"


def test_load_display_quality_csv_reads_measurement_grid(tmp_path):
    path = tmp_path / "display_quality.csv"
    path.write_text(
        "x_cm,z_cm,crosstalk_percent,view_locked\n"
        "-10,60,8,true\n"
        "0,60,6,true\n",
        encoding="utf-8",
    )

    samples = display_quality.load_display_quality_csv(path)

    assert len(samples) == 2
    assert samples[0].x_cm == -10.0
    assert samples[0].view_locked is True


def test_run_display_quality_benchmark_returns_quality_and_json(tmp_path):
    path = tmp_path / "display_quality.csv"
    path.write_text(
        "x_cm,z_cm,crosstalk_percent,view_locked\n"
        "-10,60,8,true\n"
        "10,60,7,true\n",
        encoding="utf-8",
    )

    result = display_quality.run_benchmark(path)
    data = json.loads(display_quality.format_benchmark_json(result))

    assert result.quality == "GOOD"
    assert data["metrics"]["usable_width_cm"] == 20.0


def test_main_returns_nonzero_for_danger(tmp_path, capsys):
    path = tmp_path / "display_quality.csv"
    path.write_text(
        "x_cm,z_cm,crosstalk_percent,view_locked\n"
        "0,60,25,false\n",
        encoding="utf-8",
    )

    code = display_quality.main([str(path)])

    assert code == 1
    assert "quality=DANGER" in capsys.readouterr().out
