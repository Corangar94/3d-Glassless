import json

import pytest
import yaml

from scripts import replay_quality as replay_cli
from tracker.pose import HeadPosition
from tracker.pose_filter import AdaptivePoseFilter, ConstantVelocityFilter1D
from tracker.replay_quality import FilterSettings, benchmark, tune


def test_display_projection_does_not_advance_measurement_state():
    axis = ConstantVelocityFilter1D(
        process_noise=4800.0,
        measurement_noise=0.1,
    )
    axis.update(0.0, 1000)
    axis.update(1.0, 1033)
    measurement_timestamp = axis.state_timestamp_ms

    projected, velocity = axis.project(1100)

    assert projected > 1.0
    assert velocity > 0.0
    assert axis.state_timestamp_ms == measurement_timestamp


def test_adaptive_filter_accepts_next_camera_sample_after_display_prediction():
    filter_ = AdaptivePoseFilter(
        process_noise=2.0,
        measurement_noise=0.1,
        prediction_horizon_ms=0.0,
    )
    filter_.update_pose(
        HeadPosition(
            x_cm=0.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=1000,
        ),
        publish_timestamp_ms=1032,
    )
    filter_.predict(publish_timestamp_ms=1080)
    output = filter_.update_pose(
        HeadPosition(
            x_cm=1.0,
            y_cm=0.0,
            z_cm=60.0,
            capture_timestamp_ms=1033,
        ),
        publish_timestamp_ms=1065,
    )

    assert output.capture_timestamp_ms == 1033
    assert 0.0 < output.x_cm < 3.0
    assert output.vx_cm_s > 0.0


def test_default_replay_settings_pass_the_software_gate():
    report = benchmark()

    assert report.passed, report.failures
    assert report.weighted_score < 0.80
    assert all(result.improvement_ratio < 1.0 for result in report.scenarios)
    assert {result.name for result in report.scenarios} == {
        "smooth_lateral",
        "direction_reversal",
        "dropout_recovery",
        "stationary_jitter",
    }


def test_replay_gate_catches_the_previous_overpredicting_defaults():
    report = benchmark(
        FilterSettings(
            process_noise=0.01,
            measurement_noise=0.1,
            prediction_horizon_ms=35.0,
            max_prediction_ms=80.0,
        )
    )

    assert not report.passed
    assert any("worse than raw hold" in failure for failure in report.failures)


def test_bounded_tuner_matches_or_improves_default_score():
    default = benchmark()
    recommended = tune(
        process_noise_values=(1.0, 2.0, 3.0),
        measurement_noise_values=(0.05, 0.1),
        prediction_horizon_values_ms=(0.0, 5.0),
    )

    assert recommended.weighted_score <= default.weighted_score + 1e-9
    assert recommended.passed


def test_report_outputs_machine_and_human_readable_artifacts(tmp_path):
    report = benchmark()
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"

    report.write_json(json_path)
    report.write_markdown(markdown_path)

    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["passed"] is True
    assert parsed["settings"]["prediction_horizon_ms"] == 0.0
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Glassless3D software replay report" in markdown
    assert "stationary_jitter" in markdown


def test_cli_can_write_recommended_settings_atomically(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "tracking": {
                    "smoothing_q": 0.01,
                    "smoothing_r": 0.1,
                    "prediction_horizon_ms": 35.0,
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    report_json = tmp_path / "report.json"

    exit_code = replay_cli.main(
        [
            "--config",
            str(config),
            "--tune",
            "--write-config",
            "--output-json",
            str(report_json),
            "--fail-on-regression",
        ]
    )

    assert exit_code == 0
    saved = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert saved["tracking"]["smoothing_q"] >= 0.5
    assert saved["tracking"]["prediction_horizon_ms"] <= 15.0
    assert report_json.exists()
    assert not (tmp_path / "config.yaml.tmp").exists()
