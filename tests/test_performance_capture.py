import csv

from tracker.performance_capture import FrameTimingCsvWriter


def test_frame_timing_csv_writer_writes_header_and_rows(tmp_path):
    path = tmp_path / "timings.csv"

    with FrameTimingCsvWriter(path) as writer:
        writer.write(timestamp_ms=0, frame_time_ms=16.5)
        writer.write(timestamp_ms=17, frame_time_ms=17.2)

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows == [
        {"timestamp_ms": "0", "frame_time_ms": "16.5000"},
        {"timestamp_ms": "17", "frame_time_ms": "17.2000"},
    ]


def test_frame_timing_csv_writer_appends_without_rewriting_header(tmp_path):
    path = tmp_path / "timings.csv"

    with FrameTimingCsvWriter(path) as writer:
        writer.write(timestamp_ms=0, frame_time_ms=16.0)
    with FrameTimingCsvWriter(path, append=True) as writer:
        writer.write(timestamp_ms=16, frame_time_ms=17.0)

    lines = path.read_text(encoding="utf-8").splitlines()

    assert lines.count("timestamp_ms,frame_time_ms") == 1
    assert lines[-1] == "16,17.0000"
