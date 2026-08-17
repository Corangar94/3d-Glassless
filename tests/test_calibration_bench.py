import json
from unittest.mock import MagicMock, patch

from tracker import calibration_bench


def test_capture_tracking_samples_records_valid_and_stale_poses():
    reader = MagicMock()
    reader.read.side_effect = [
        (1.0, 2.0, 60.0, 1000),
        (1.2, 2.0, 60.0, 1000),
        None,
    ]
    times = iter([1000, 1801, 1900])

    samples = calibration_bench.capture_tracking_samples(
        duration_s=0.3,
        interval_s=0.1,
        reader=reader,
        monotonic_ms=lambda: next(times),
        sleep=lambda _seconds: None,
    )

    assert len(samples) == 3
    assert samples[0].valid is True
    assert samples[1].valid is False
    assert samples[2].valid is False


def test_format_benchmark_json_is_machine_readable():
    samples = [
        calibration_bench.PoseSample(timestamp_ms=0, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
        calibration_bench.PoseSample(timestamp_ms=16, x_cm=0.1, y_cm=0.0, z_cm=60.0, valid=True),
    ]

    data = json.loads(calibration_bench.format_benchmark_json(samples))

    assert data["quality"] == "GOOD"
    assert data["metrics"]["sample_count"] == 2
    assert data["samples"][0]["z_cm"] == 60.0


def test_main_writes_tracking_benchmark_json(tmp_path, capsys):
    output = tmp_path / "tracking_bench.json"
    samples = [
        calibration_bench.PoseSample(timestamp_ms=0, x_cm=0.0, y_cm=0.0, z_cm=60.0, valid=True),
    ]

    with patch.object(calibration_bench, "capture_tracking_samples", return_value=samples):
        code = calibration_bench.main(["--duration", "0.1", "--output", str(output)])

    assert code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["quality"] == "GOOD"
    assert "wrote tracking calibration bench" in capsys.readouterr().out
