from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one match for {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "tracker/face_tracker.py",
    "from tracker.pose import HeadPosition, monotonic_ms\n",
    "from tracker.camera_geometry import CameraGeometry\nfrom tracker.pose import HeadPosition, monotonic_ms\n",
)
replace_once(
    "tracker/face_tracker.py",
    '''        async_mode: bool = True,
        min_tracking_confidence: float = 0.5,
    ) -> None:
''',
    '''        async_mode: bool = True,
        min_tracking_confidence: float = 0.5,
        camera_geometry: CameraGeometry | None = None,
    ) -> None:
''',
)
replace_once(
    "tracker/face_tracker.py",
    '''        self._camera_fov_deg = float(camera_fov_deg)
        self._async_mode = bool(async_mode)
''',
    '''        self._camera_fov_deg = float(camera_fov_deg)
        self._camera_geometry = camera_geometry
        self._async_mode = bool(async_mode)
''',
)
replace_once(
    "tracker/face_tracker.py",
    '''        left_iris = np.array(
            [left.x * image_width, left.y * image_height], dtype=np.float64
        )
        right_iris = np.array(
            [right.x * image_width, right.y * image_height], dtype=np.float64
        )
        ipd_px = float(np.linalg.norm(right_iris - left_iris))
        if not math.isfinite(ipd_px) or ipd_px < 1.0:
            return None

        z_cm = estimate_z_cm(
            ipd_px,
            image_width,
            self._real_ipd_cm,
            self._camera_fov_deg,
            yaw_deg,
        )
        center_x = float((left.x + right.x) * 0.5)
        center_y = float((left.y + right.y) * 0.5)
        x_cm, y_cm = estimate_xy_cm(
            center_x,
            center_y,
            z_cm,
            self._camera_fov_deg,
            image_width,
            image_height,
        )
''',
    '''        left_iris = np.array(
            [left.x * image_width, left.y * image_height], dtype=np.float64
        )
        right_iris = np.array(
            [right.x * image_width, right.y * image_height], dtype=np.float64
        )
        geometry = self._camera_geometry
        if geometry is not None and geometry.intrinsics is not None:
            rectified = geometry.rectified_pixels(
                (left_iris, right_iris),
                image_width=image_width,
                image_height=image_height,
            )
            ipd_px = float(np.linalg.norm(rectified[1] - rectified[0]))
            focal_x, _focal_y = geometry.focal_lengths(image_width, image_height)
            yaw_scale = max(0.45, abs(math.cos(math.radians(yaw_deg))))
            z_camera_cm = (
                focal_x * self._real_ipd_cm * yaw_scale / max(ipd_px, 1.0)
            )
            center_px = (left_iris + right_iris) * 0.5
            x_cm, y_cm, z_cm = geometry.pixel_depth_to_screen(
                float(center_px[0]),
                float(center_px[1]),
                z_camera_cm,
                image_width=image_width,
                image_height=image_height,
            )
            yaw_deg, pitch_deg, roll_deg = geometry.orientation_to_screen(
                yaw_deg, pitch_deg, roll_deg
            )
        else:
            ipd_px = float(np.linalg.norm(right_iris - left_iris))
            if not math.isfinite(ipd_px) or ipd_px < 1.0:
                return None
            z_cm = estimate_z_cm(
                ipd_px,
                image_width,
                self._real_ipd_cm,
                self._camera_fov_deg,
                yaw_deg,
            )
            center_x = float((left.x + right.x) * 0.5)
            center_y = float((left.y + right.y) * 0.5)
            x_cm, y_cm = estimate_xy_cm(
                center_x,
                center_y,
                z_cm,
                self._camera_fov_deg,
                image_width,
                image_height,
            )
''',
)

replace_once(
    "tracker/face_tracker_cv2.py",
    "from tracker.pose import HeadPosition, monotonic_ms\n",
    "from tracker.camera_geometry import CameraGeometry\nfrom tracker.pose import HeadPosition, monotonic_ms\n",
)
replace_once(
    "tracker/face_tracker_cv2.py",
    '''        model_path: str = "",
        **_options: object,
''',
    '''        model_path: str = "",
        camera_geometry: CameraGeometry | None = None,
        **_options: object,
''',
)
replace_once(
    "tracker/face_tracker_cv2.py",
    '''        self._real_ipd_cm = float(real_ipd_cm)
        self._camera_fov_deg = float(camera_fov_deg)
''',
    '''        self._real_ipd_cm = float(real_ipd_cm)
        self._camera_fov_deg = float(camera_fov_deg)
        self._camera_geometry = camera_geometry
''',
)
replace_once(
    "tracker/face_tracker_cv2.py",
    '''        focal_px = w / (2.0 * math.tan(math.radians(self._camera_fov_deg / 2.0)))
''',
    '''        geometry = self._camera_geometry
        focal_px = (
            geometry.focal_lengths(w, h)[0]
            if geometry is not None and geometry.intrinsics is not None
            else w / (2.0 * math.tan(math.radians(self._camera_fov_deg / 2.0)))
        )
''',
)
replace_once(
    "tracker/face_tracker_cv2.py",
    '''        aspect = w / max(h, 1)
        phys_half_w = z_cm * math.tan(math.radians(self._camera_fov_deg / 2.0))
        phys_half_h = phys_half_w / aspect
        return HeadPosition(
            x_cm=-((cx_norm - 0.5) * 2.0 * phys_half_w),
            y_cm=-((cy_norm - 0.5) * 2.0 * phys_half_h),
            z_cm=z_cm,
''',
    '''        if geometry is not None and geometry.intrinsics is not None:
            x_cm, y_cm, screen_z_cm = geometry.pixel_depth_to_screen(
                cx_norm * w,
                cy_norm * h,
                z_cm,
                image_width=w,
                image_height=h,
            )
        else:
            aspect = w / max(h, 1)
            phys_half_w = z_cm * math.tan(math.radians(self._camera_fov_deg / 2.0))
            phys_half_h = phys_half_w / aspect
            x_cm = -((cx_norm - 0.5) * 2.0 * phys_half_w)
            y_cm = -((cy_norm - 0.5) * 2.0 * phys_half_h)
            screen_z_cm = z_cm
        return HeadPosition(
            x_cm=x_cm,
            y_cm=y_cm,
            z_cm=screen_z_cm,
''',
)

replace_once(
    "tracker/main.py",
    "from tracker.face_tracker_cv2 import HeadPosition\n",
    "from tracker.camera_geometry import CameraGeometry\nfrom tracker.face_tracker_cv2 import HeadPosition\n",
)
replace_once(
    "tracker/main.py",
    '''    face_tracker_cls, selected_backend = _load_face_tracker_class(tracker_backend)
    smoother = AdaptivePoseFilter''',
    '''    face_tracker_cls, selected_backend = _load_face_tracker_class(tracker_backend)
    camera_geometry = CameraGeometry.from_config(
        cfg,
        fallback_width=int(cam.get("width", 0)),
        fallback_height=int(cam.get("height", 0)),
    )
    smoother = AdaptivePoseFilter''',
)
replace_once(
    "tracker/main.py",
    '''face_tracker_cls(real_ipd_cm=_resolve_ipd_cm(cfg), screen_width_cm=scr["width_cm"], screen_height_cm=scr["height_cm"], camera_fov_deg=_resolve_camera_fov_deg(cfg), async_mode=bool(trk.get("live_stream", True)), min_tracking_confidence=float(trk.get("min_tracking_confidence", 0.5))) as tracker,''',
    '''face_tracker_cls(real_ipd_cm=_resolve_ipd_cm(cfg), screen_width_cm=scr["width_cm"], screen_height_cm=scr["height_cm"], camera_fov_deg=_resolve_camera_fov_deg(cfg), async_mode=bool(trk.get("live_stream", True)), min_tracking_confidence=float(trk.get("min_tracking_confidence", 0.5)), camera_geometry=camera_geometry) as tracker,''',
)
