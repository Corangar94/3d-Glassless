from pathlib import Path

path = Path("tracker/main.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"expected one tracker/main.py match for {old[:100]!r}")
    text = text.replace(old, new, 1)


replace_once(
    "from tracker.camera_geometry import CameraGeometry\n",
    "from tracker.camera_geometry import CameraGeometry\nfrom tracker.camera_quality import CameraQualityMonitor, try_lock_camera_controls\n",
)
replace_once(
    '''class TrackingLoop:
    def __init__(self, tracker: FaceTrackerLike, writer: PoseWriterLike, smoother: HeadSmoother | AdaptivePoseFilter, hold_ms: int = 500, stop_event: Optional[threading.Event] = None, camera_tilt_deg: float = 0.0, config_path: Optional[str] = None) -> None:
''',
    '''class TrackingLoop:
    def __init__(self, tracker: FaceTrackerLike, writer: PoseWriterLike, smoother: HeadSmoother | AdaptivePoseFilter, hold_ms: int = 500, stop_event: Optional[threading.Event] = None, camera_tilt_deg: float = 0.0, config_path: Optional[str] = None, camera_quality_monitor: CameraQualityMonitor | None = None, lock_camera_controls: bool = False) -> None:
''',
)
replace_once(
    '''        self._stop_event, self._camera_tilt_deg, self._config_path = stop_event, camera_tilt_deg, config_path
''',
    '''        self._stop_event, self._camera_tilt_deg, self._config_path = stop_event, camera_tilt_deg, config_path
        self._camera_quality_monitor = camera_quality_monitor
        self._lock_camera_controls = bool(lock_camera_controls)
''',
)
replace_once(
    '''        settings_reader = SharedSettingsReader()
        applied_calibration: tuple[float | None, float | None] | None = None
        try:
''',
    '''        settings_reader = SharedSettingsReader()
        applied_calibration: tuple[float | None, float | None] | None = None
        last_quality_log_ms = 0
        controls_lock_attempted = False
        try:
''',
)
replace_once(
    '''                self._on_frame(frame)
                settings = settings_reader.read()
''',
    '''                self._on_frame(frame)
                if self._camera_quality_monitor is not None:
                    camera_quality = self._camera_quality_monitor.update(
                        frame, capture_timestamp_ms
                    )
                    if capture_timestamp_ms - last_quality_log_ms >= 2000:
                        problems = ", ".join(camera_quality.problems) or "none"
                        fps_text = (
                            f"{camera_quality.fps:.1f}"
                            if camera_quality.fps is not None
                            else "unknown"
                        )
                        print(
                            "[G3D] Camera quality "
                            f"{camera_quality.quality}: brightness={camera_quality.brightness:.2f} "
                            f"jitter={camera_quality.brightness_jitter:.3f} "
                            f"sharpness={camera_quality.sharpness:.1f} "
                            f"fps={fps_text} problems={problems}"
                        )
                        last_quality_log_ms = capture_timestamp_ms
                    if (
                        self._lock_camera_controls
                        and not controls_lock_attempted
                        and camera_quality.stable_for_lock
                    ):
                        controls_lock_attempted = True
                        result = try_lock_camera_controls(cap)
                        print(f"[G3D] Camera control lock result: {result}")
                settings = settings_reader.read()
''',
)
replace_once(
    '''    camera_geometry = CameraGeometry.from_config(
        cfg,
        fallback_width=int(cam.get("width", 0)),
        fallback_height=int(cam.get("height", 0)),
    )
    smoother = AdaptivePoseFilter''',
    '''    camera_geometry = CameraGeometry.from_config(
        cfg,
        fallback_width=int(cam.get("width", 0)),
        fallback_height=int(cam.get("height", 0)),
    )
    camera_quality_monitor = CameraQualityMonitor(
        window_size=int(cam.get("quality_window_frames", 45)),
        minimum_sharpness=float(cam.get("minimum_sharpness", 35.0)),
        minimum_fps=float(cam.get("minimum_fps", 20.0)),
    )
    smoother = AdaptivePoseFilter''',
)
replace_once(
    '''        loop = TrackingLoop(tracker=tracker, writer=_MultiWriter(), smoother=smoother, hold_ms=int(trk["hold_ms"]), stop_event=stop_event, camera_tilt_deg=float(trk.get("camera_tilt_deg", 0.0)), config_path=args.config)
''',
    '''        loop = TrackingLoop(tracker=tracker, writer=_MultiWriter(), smoother=smoother, hold_ms=int(trk["hold_ms"]), stop_event=stop_event, camera_tilt_deg=float(trk.get("camera_tilt_deg", 0.0)), config_path=args.config, camera_quality_monitor=camera_quality_monitor, lock_camera_controls=bool(cam.get("lock_controls_after_warmup", False)))
''',
)

path.write_text(text, encoding="utf-8", newline="\n")
