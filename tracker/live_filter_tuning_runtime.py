"""Packaged tracker runtime that consumes live Kalman smoothing settings."""
from __future__ import annotations

from typing import Any

from tracker import main as tracker_main
from tracker.camera_control_recovery_runtime import (
    CameraControlRecoveryTrackingLoop,
)
from tracker.live_filter_tuning import (
    LiveFilterTuningController,
    LiveFilterTuningPolicy,
    LiveFilterTuningSnapshot,
)


_DEFAULT_SETTINGS_READER = object()


def _open_default_settings_reader() -> object | None:
    """Open the Windows settings mapping without making this module platform-bound."""
    try:
        # tracker.shared_settings binds Win32 functions at import time. Keeping
        # this import lazy lets the pure controller and non-Windows source tests
        # remain usable while the packaged Windows child gets the real mapping.
        from tracker.shared_settings import SharedSettingsReader

        return SharedSettingsReader()
    except Exception as error:
        print(
            "[G3D] Live filter tuning unavailable; using configured "
            f"smoothing ({type(error).__name__})"
        )
        return None


def _close_reader(reader: object | None) -> None:
    close = getattr(reader, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        pass


class LiveFilterTuningTrackingLoop(CameraControlRecoveryTrackingLoop):
    """Apply ``G3D_Settings.smoothing_alpha`` before each filter update."""

    def __init__(
        self,
        *args: object,
        live_filter_settings_reader: object = _DEFAULT_SETTINGS_READER,
        live_filter_tuning_policy: LiveFilterTuningPolicy | None = None,
        **kwargs: object,
    ) -> None:
        config_path = kwargs.get("config_path")
        self._live_filter_tuning: LiveFilterTuningController | None = None
        self._live_filter_settings_reader: object | None = None
        self._live_filter_tuning_last_snapshot: (
            LiveFilterTuningSnapshot | None
        ) = None
        self._live_filter_tuning_last_policy: (
            LiveFilterTuningPolicy | None
        ) = None
        super().__init__(*args, **kwargs)

        target = getattr(self, "_smoother", None)
        if not callable(getattr(target, "set_measurement_noise", None)):
            if live_filter_settings_reader is not _DEFAULT_SETTINGS_READER:
                _close_reader(live_filter_settings_reader)
            return

        reader = live_filter_settings_reader
        if reader is _DEFAULT_SETTINGS_READER:
            # The normal source/frozen child always has a config path. Direct
            # library loops without one retain their static constructor setting
            # and avoid importing the Windows shared-memory module.
            reader = (
                _open_default_settings_reader()
                if config_path
                else None
            )
        if reader is None:
            return

        policy = live_filter_tuning_policy or LiveFilterTuningPolicy()
        self._live_filter_settings_reader = reader
        try:
            self._live_filter_tuning = LiveFilterTuningController(
                reader,
                target,
                policy,
            )
            self._live_filter_tuning_last_policy = policy
        except Exception as error:
            _close_reader(reader)
            self._live_filter_settings_reader = None
            print(
                "[G3D] Live filter tuning initialization failed; using "
                f"configured smoothing ({type(error).__name__})"
            )

    @property
    def live_filter_tuning_policy(self) -> LiveFilterTuningPolicy | None:
        controller = self._live_filter_tuning
        return (
            controller.policy
            if controller is not None
            else self._live_filter_tuning_last_policy
        )

    def live_filter_tuning_snapshot(
        self,
    ) -> LiveFilterTuningSnapshot | None:
        controller = self._live_filter_tuning
        return (
            controller.snapshot()
            if controller is not None
            else self._live_filter_tuning_last_snapshot
        )

    def _poll_live_filter_tuning(self) -> bool:
        controller = self._live_filter_tuning
        if controller is None:
            return False
        try:
            return controller.poll()
        except Exception:
            # The controller already contains its known process-boundary errors;
            # this final guard ensures optional live tuning can never stop pose
            # publication if a dynamically patched reader violates the contract.
            return False

    def _update_filter(self, pose: object) -> Any:
        self._poll_live_filter_tuning()
        return super()._update_filter(pose)

    def _close_live_filter_tuning(self) -> None:
        controller = self._live_filter_tuning
        self._live_filter_tuning = None
        reader = self._live_filter_settings_reader
        self._live_filter_settings_reader = None
        if controller is not None:
            controller.close()
            self._live_filter_tuning_last_snapshot = controller.snapshot()
        elif reader is not None:
            _close_reader(reader)

    def run(self, *args: object, **kwargs: object) -> Any:
        try:
            return super().run(*args, **kwargs)
        finally:
            self._close_live_filter_tuning()


def main() -> None:
    """Run the tracker bootstrap with every packaged protection active."""
    original_loop = tracker_main.TrackingLoop
    tracker_main.TrackingLoop = LiveFilterTuningTrackingLoop
    try:
        tracker_main.main()
    finally:
        tracker_main.TrackingLoop = original_loop
