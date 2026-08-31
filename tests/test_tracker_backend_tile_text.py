from launcher.tracker_backend_diagnostics import tracker_backend_tile_text
from tracker.backend_status_shared_memory import TrackerBackendStatus


def _status(configured_mode: str, active_backend: str, **values):
    return TrackerBackendStatus(
        configured_mode=configured_mode,
        active_backend=active_backend,
        **values,
    )


def test_strict_cv2_is_not_mislabelled_as_fallback():
    label, tooltip = tracker_backend_tile_text(
        _status("cv2", "cv2"),
        fresh=True,
    )

    assert label == "OpenCV"
    assert "Configured: cv2" in tooltip
    assert "Active: cv2" in tooltip


def test_auto_cv2_is_labelled_as_fallback():
    label, _tooltip = tracker_backend_tile_text(
        _status("auto", "cv2"),
        fresh=True,
    )

    assert label == "OpenCV fallback"


def test_candidate_progress_label_is_only_used_for_auto_mode():
    auto_label, _ = tracker_backend_tile_text(
        _status(
            "auto",
            "cv2",
            candidate_active=True,
            candidate_healthy_callbacks=2,
        ),
        fresh=True,
    )
    strict_label, _ = tracker_backend_tile_text(
        _status(
            "cv2",
            "cv2",
            candidate_active=True,
            candidate_healthy_callbacks=2,
        ),
        fresh=True,
    )

    assert auto_label == "OpenCV + probe 2"
    assert strict_label == "OpenCV"
