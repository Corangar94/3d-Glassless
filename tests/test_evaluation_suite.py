import numpy as np
import json

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


def test_run_suite_includes_comfort_result_when_csv_is_provided(tmp_path):
    comfort = tmp_path / "comfort.csv"
    comfort.write_text(
        "eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk\n"
        "1,1,1,1,5,5,1\n",
        encoding="utf-8",
    )

    result = evaluation_suite.run_suite(comfort_csv=comfort)

    assert result.comfort is not None
    assert result.comfort.quality == "GOOD"
    assert result.overall_quality == "GOOD"


def test_run_suite_includes_display_quality_when_csv_is_provided(tmp_path):
    display_quality = tmp_path / "display_quality.csv"
    display_quality.write_text(
        "x_cm,z_cm,crosstalk_percent,view_locked\n"
        "-10,60,8,true\n"
        "10,60,7,true\n",
        encoding="utf-8",
    )

    result = evaluation_suite.run_suite(display_quality_csv=display_quality)

    assert result.display_quality is not None
    assert result.display_quality.quality == "GOOD"
    assert result.overall_quality == "GOOD"


def test_run_suite_includes_latency_when_csv_is_provided(tmp_path):
    latency = tmp_path / "latency.csv"
    latency.write_text(
        "timestamp_ms,tracking_to_display_ms\n"
        "0,10\n"
        "16,12\n",
        encoding="utf-8",
    )

    result = evaluation_suite.run_suite(latency_csv=latency, latency_target_ms=20.0)

    assert result.latency is not None
    assert result.latency.quality == "GOOD"
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


def test_format_suite_json_is_machine_readable(tmp_path):
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    np.save(depth_dir / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(depth_dir / "b.npy", np.full((2, 2), 0.01, dtype=np.float32))

    result = evaluation_suite.run_suite(depth_dir=depth_dir)
    data = json.loads(evaluation_suite.format_suite_json(result))

    assert data["overall_quality"] == "GOOD"
    assert data["depth"]["quality"] == "GOOD"
    assert data["performance"] is None
    assert data["comfort"] is None
    assert data["display_quality"] is None
    assert data["latency"] is None


def test_main_accepts_comfort_csv(tmp_path):
    comfort = tmp_path / "comfort.csv"
    comfort.write_text(
        "eye_strain,headache,nausea,disorientation,depth_realism,ui_readability,crosstalk\n"
        "5,4,4,4,2,2,4\n",
        encoding="utf-8",
    )

    code = evaluation_suite.main(["--comfort-csv", str(comfort)])

    assert code == 1


def test_main_accepts_display_quality_csv(tmp_path):
    display_quality = tmp_path / "display_quality.csv"
    display_quality.write_text(
        "x_cm,z_cm,crosstalk_percent,view_locked\n"
        "0,60,25,false\n",
        encoding="utf-8",
    )

    code = evaluation_suite.main(["--display-quality-csv", str(display_quality)])

    assert code == 1


def test_main_accepts_latency_csv(tmp_path):
    latency = tmp_path / "latency.csv"
    latency.write_text(
        "timestamp_ms,tracking_to_display_ms\n"
        "0,50\n",
        encoding="utf-8",
    )

    code = evaluation_suite.main(["--latency-csv", str(latency), "--latency-target-ms", "20"])

    assert code == 1


def test_main_writes_json_output(tmp_path):
    depth_dir = tmp_path / "depth"
    depth_dir.mkdir()
    np.save(depth_dir / "a.npy", np.zeros((2, 2), dtype=np.float32))
    np.save(depth_dir / "b.npy", np.full((2, 2), 0.01, dtype=np.float32))
    output = tmp_path / "evaluation.json"

    code = evaluation_suite.main([
        "--depth-dir",
        str(depth_dir),
        "--format",
        "json",
        "--output",
        str(output),
    ])

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["overall_quality"] == "GOOD"
