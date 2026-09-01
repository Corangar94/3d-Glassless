from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_callback_order_is_checked_before_and_after_pose_conversion():
    source = _source("tracker/face_tracker.py")
    callback = source.split("    def _on_result(", 1)[1].split(
        "    def _poll_latest(",
        1,
    )[0]

    precheck = callback.index("callback_order.begin_processing(timestamp)")
    conversion = callback.index("self._pose_from_result(")
    final_claim = callback.index(
        "callback_order.accept_publication(timestamp)"
    )
    publication = callback.index("self._latest_pose = pose")
    health = callback.rindex(
        "self._async_watchdog.record_callback(timestamp)"
    )

    assert precheck < conversion < final_claim < publication < health


def test_final_claim_and_publication_share_the_same_tracker_lock():
    source = _source("tracker/face_tracker.py")
    callback = source.split("    def _on_result(", 1)[1].split(
        "    def _poll_latest(",
        1,
    )[0]
    post_conversion = callback.split(
        'freshness = getattr(self, "_async_result_freshness", None)',
        1,
    )[1]

    locked = post_conversion.split("with self._lock:", 1)[1]
    assert "callback_order.accept_publication(timestamp)" in locked
    assert "self._latest_pose = pose" in locked
    assert "self._async_watchdog.record_callback(timestamp)" in locked


def test_obsolete_conversion_errors_are_filtered_before_watchdog_recording():
    source = _source("tracker/face_tracker.py")
    callback = source.split("    def _on_result(", 1)[1].split(
        "    def _poll_latest(",
        1,
    )[0]
    error_path = callback.split("except Exception as error:", 1)[1].split(
        "freshness =",
        1,
    )[0]

    relevance = error_path.index(
        "self._callback_order_gate_locked().is_newer("
    )
    health = error_path.index("self._async_watchdog.record_callback(")
    assert relevance < health
    assert "if current and self._async_watchdog is not None:" in error_path


def test_session_reset_preserves_callback_order_timeline():
    source = _source("tracker/face_tracker.py")
    reset = source.split("    def reset_session(self) -> None:", 1)[1].split(
        "    def async_health_snapshot",
        1,
    )[0]

    assert "callback publication ordering are deliberately not reset" in reset
    assert "_async_callback_order =" not in reset


def test_frozen_package_includes_callback_order_gate():
    spec = _source("Glassless3D.spec")

    assert '"tracker.async_callback_order"' in spec


def test_documentation_distinguishes_callback_and_delivery_gates():
    docs = _source("docs/MEDIAPIPE_CALLBACK_PUBLICATION.md")

    assert "checks callback order twice" in docs
    assert "successfully processed no-face callback also claims" in docs
    assert "The generic `PoseResultTimelineGate` remains" in docs
