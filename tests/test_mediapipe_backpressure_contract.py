from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_async_admission_precedes_mediapipe_image_conversion():
    source = _source("tracker/face_tracker.py")
    process = source.split("    def process_frame(", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    health = process.index("watchdog.raise_if_unhealthy(media_timestamp_ms)")
    admission = process.index("watchdog.should_submit(")
    conversion = process.index("image = self._mediapipe_image(frame_bgr)")
    submission = process.index("self._landmarker.detect_async(")

    assert health < admission < conversion < submission
    assert "watchdog.record_throttled_submission()" in process
    assert "return self._poll_latest()" in process[admission:conversion]


def test_submission_timeline_is_committed_only_after_detect_async_success():
    source = _source("tracker/face_tracker.py")
    process = source.split("    def process_frame(", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    call = process.index("self._landmarker.detect_async(")
    wire_commit = process.index(
        "self._last_submitted_wire_timestamp_ms = wire_timestamp_ms"
    )
    media_commit = process.index(
        "self._last_submitted_media_timestamp_ms = media_timestamp_ms"
    )

    assert call < wire_commit
    assert call < media_commit
    assert "except (RuntimeError, ValueError) as error:" in process


def test_sync_mode_still_converts_and_detects_every_input():
    source = _source("tracker/face_tracker.py")
    process = source.split("    def process_frame(", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    sync_tail = process.rsplit(
        "image = self._mediapipe_image(frame_bgr)",
        1,
    )[1]
    assert "result = self._landmarker.detect(image)" in sync_tail


def test_backpressure_default_and_disable_contract_are_documented():
    source = _source("tracker/face_tracker.py")
    docs = _source("docs/MEDIAPIPE_ASYNC_BACKPRESSURE.md")

    assert "async_max_backlog_ms: int = 150" in source
    assert "async_max_backlog_ms cannot be negative" in source
    assert "150 ms" in docs
    assert "async_max_backlog_ms=0" in docs
