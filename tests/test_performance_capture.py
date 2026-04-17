import csv

from tracker.performance_capture import (
    FrameTimingCsvWriter,
    extract_overlay_frame_timings,
    main,
)


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


def test_extract_overlay_frame_timings_converts_summary_deltas(tmp_path):
    log = tmp_path / "overlay.log"
    log.write_text(
        "[15:26:00.000] Frame#100 acq[ok=100 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=100 changes=10 (10/s) ts=1] depth[total=8 8Hz] "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n"
        "[15:26:01.000] Frame#160 acq[ok=160 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=160 changes=10 (10/s) ts=2] depth[total=16 8Hz] "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )

    samples = extract_overlay_frame_timings(log)

    assert len(samples) == 1
    assert samples[0].timestamp_ms == 1000
    assert round(samples[0].frame_time_ms, 4) == 16.6667


def test_main_exports_overlay_timings_csv(tmp_path, capsys):
    log = tmp_path / "overlay.log"
    output = tmp_path / "timings.csv"
    log.write_text(
        "[15:26:00.000] Frame#10 acq[ok=10 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=10 changes=1 (1/s) ts=1] depth[total=1 1Hz] "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n"
        "[15:26:01.000] Frame#70 acq[ok=70 timeout=0 lost=0 other=0] "
        "shm[LIVE reads=70 changes=1 (1/s) ts=2] depth[total=2 1Hz] "
        "head=(0.00,0.00,60.00) rest=(0.00,0.00) rel=(0.00,0.00) "
        "wobble=0.00 strength=1.00 depth=30.00 hasFrame=1\n",
        encoding="utf-8",
    )

    code = main([str(log), str(output)])

    assert code == 0
    assert output.read_text(encoding="utf-8").splitlines() == [
        "timestamp_ms,frame_time_ms",
        "1000,16.6667",
    ]
    assert "wrote 1 frame timing samples" in capsys.readouterr().out
