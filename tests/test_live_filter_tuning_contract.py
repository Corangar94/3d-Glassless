from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_launcher_writes_tuned_smoothing_to_versioned_settings():
    source = _source("launcher/mainwindow.py")
    position = source.split(
        "    def _on_position(self, x: float, y: float, z: float) -> None:",
        1,
    )[1].split("    def _on_frame(", 1)[0]

    replace = position.index("self._settings = dataclasses.replace(")
    smoothing = position.index("smoothing_alpha=tuned.smoothing_alpha")
    write = position.index("self._settings_writer.write(self._settings)")

    assert replace < smoothing < write


def test_shared_settings_field_remains_the_kalman_measurement_noise():
    source = _source("tracker/shared_settings.py")

    assert "smoothing_alpha: float = 0.1" in source
    assert "# Kalman measurement noise r" in source
    assert 'STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI"' in source


def test_live_runtime_polls_before_the_producer_filter_update():
    source = _source("tracker/live_filter_tuning_runtime.py")
    update = source.split("    def _update_filter(", 1)[1].split(
        "    def _close_live_filter_tuning(",
        1,
    )[0]

    poll = update.index("self._poll_live_filter_tuning()")
    parent = update.index("return super()._update_filter(pose)")
    assert poll < parent


def test_controller_applies_only_to_measurement_noise_setter():
    controller = _source("tracker/live_filter_tuning.py")
    pose_filter = _source("tracker/pose_filter.py")

    assert "self._target.set_measurement_noise(measurement_noise)" in controller
    assert "def set_measurement_noise(self, value: float) -> None:" in pose_filter
    assert "self._x.set_measurement_noise(value)" in pose_filter
    assert "self._y.set_measurement_noise(value)" in pose_filter
    assert "self._z.set_measurement_noise(value * 1.5)" in pose_filter


def test_native_overlay_does_not_add_a_second_smoothing_pass():
    overlay = _source("overlay/overlay.cpp")
    settings = overlay.split("struct Settings {", 1)[1].split("};", 1)[0]
    apply_settings = overlay.split("static void ApplySettings()", 1)[1].split(
        "static bool AutodetectScreenSizeCm",
        1,
    )[0]
    frame = overlay.split("static void Frame()", 1)[1]

    assert "smoothingAlpha" in settings
    assert "smoothingAlpha" not in apply_settings
    assert "smoothingAlpha" not in frame


def test_active_bootstrap_selects_live_tuning_stack_lazily():
    source = _source("tracker/pose_stability_runtime.py")
    main = source.split("def main() -> None:", 1)[1]

    assert "from tracker.live_filter_tuning_runtime import" in main
    assert "LiveFilterTuningTrackingLoop" in main
    assert "tracker_main.TrackingLoop = LiveFilterTuningTrackingLoop" in main


def test_frozen_package_contains_live_tuning_and_settings_reader():
    spec = _source("Glassless3D.spec")

    assert '"tracker.live_filter_tuning"' in spec
    assert '"tracker.live_filter_tuning_runtime"' in spec
    assert '"tracker.shared_settings"' in spec


def test_documentation_records_single_filter_and_poll_bounds():
    docs = _source("docs/LIVE_FILTER_TUNING.md")
    auto_tune_docs = _source("docs/TIME_AWARE_AUTO_TUNING.md")

    assert "polls the shared settings mapping at most every 100 ms" in docs
    assert "between `0.01` and `1.0`" in docs
    assert "more than `0.001`" in docs
    assert "does not add a second smoothing pass" in auto_tune_docs
    assert "AdaptivePoseFilter.set_measurement_noise()" in auto_tune_docs
