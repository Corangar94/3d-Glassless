import json

from tracker import comfort_evaluation


def test_compute_comfort_metrics_summarizes_subjective_scores():
    samples = [
        comfort_evaluation.ComfortSurveySample(
            eye_strain=1,
            headache=1,
            nausea=1,
            disorientation=1,
            depth_realism=5,
            ui_readability=5,
            crosstalk=1,
        ),
        comfort_evaluation.ComfortSurveySample(
            eye_strain=2,
            headache=1,
            nausea=1,
            disorientation=2,
            depth_realism=4,
            ui_readability=4,
            crosstalk=2,
        ),
    ]

    metrics = comfort_evaluation.compute_comfort_metrics(samples)

    assert metrics.sample_count == 2
    assert metrics.avg_discomfort == 1.25
    assert metrics.max_discomfort == 2
    assert metrics.avg_depth_realism == 4.5
    assert metrics.avg_ui_readability == 4.5
    assert metrics.avg_crosstalk == 1.5


def test_classify_comfort_quality_flags_high_discomfort():
    metrics = comfort_evaluation.ComfortMetrics(
        sample_count=2,
        avg_discomfort=4.0,
        max_discomfort=5,
        avg_depth_realism=3.0,
        avg_ui_readability=4.0,
        avg_crosstalk=2.0,
    )

    assert comfort_evaluation.classify_comfort_quality(metrics) == "DANGER"


def test_load_comfort_csv_reads_repeatable_operator_capture(tmp_path):
    path = tmp_path / "comfort.csv"
    path.write_text(
        "eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk\n"
        "1,1,1,1,5,5,1\n"
        "3,2,2,2,4,4,3\n",
        encoding="utf-8",
    )

    samples = comfort_evaluation.load_comfort_csv(path)

    assert len(samples) == 2
    assert samples[1].eye_strain == 3
    assert samples[1].crosstalk == 3


def test_run_comfort_benchmark_returns_quality_and_source_path(tmp_path):
    path = tmp_path / "comfort.csv"
    path.write_text(
        "eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk\n"
        "1,1,1,1,5,5,1\n",
        encoding="utf-8",
    )

    result = comfort_evaluation.run_benchmark(path)

    assert result.source_path == path
    assert result.quality == "GOOD"
    assert result.metrics.sample_count == 1


def test_format_comfort_json_is_machine_readable(tmp_path):
    path = tmp_path / "comfort.csv"
    path.write_text(
        "eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk\n"
        "1,1,1,1,5,5,1\n",
        encoding="utf-8",
    )

    data = json.loads(comfort_evaluation.format_benchmark_json(comfort_evaluation.run_benchmark(path)))

    assert data["quality"] == "GOOD"
    assert data["metrics"]["avg_ui_readability"] == 5.0


def test_main_returns_nonzero_for_danger(tmp_path, capsys):
    path = tmp_path / "comfort.csv"
    path.write_text(
        "eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk\n"
        "5,4,4,4,2,2,4\n",
        encoding="utf-8",
    )

    code = comfort_evaluation.main([str(path)])

    assert code == 1
    assert "quality=DANGER" in capsys.readouterr().out
