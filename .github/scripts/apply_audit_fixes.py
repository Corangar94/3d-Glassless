from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


def append_once(path: str, marker: str, addition: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text + addition, encoding="utf-8", newline="\n")


# Secure bootstrap downloads/extraction.
replace_once(
    "scripts/bootstrap.py",
    "import subprocess\nimport sys\nimport urllib.request\nimport zipfile\n",
    "import subprocess\nimport sys\nimport tempfile\nimport urllib.parse\nimport urllib.request\nimport zipfile\n",
)

replace_once(
    "scripts/bootstrap.py",
    '''def _download(url: str, dest: str, label: str, *, sha256: str | None = None) -> None:\n    if os.path.exists(dest):\n        if sha256 and _sha256(dest).lower() != sha256.lower():\n            raise RuntimeError(f"SHA-256 mismatch for existing {os.path.relpath(dest, _ROOT)}")\n        print(f"  already present: {os.path.relpath(dest, _ROOT)}")\n        return\n    os.makedirs(os.path.dirname(dest), exist_ok=True)\n    print(f"  downloading {label}...", end="", flush=True)\n    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})\n    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:\n        shutil.copyfileobj(r, f)\n    if sha256 and _sha256(dest).lower() != sha256.lower():\n        os.remove(dest)\n        raise RuntimeError(f"SHA-256 mismatch for downloaded {label}")\n    print(f" {os.path.getsize(dest) // 1024} KB")\n''',
    '''def _download(url: str, dest: str, label: str, *, sha256: str | None = None) -> None:\n    if urllib.parse.urlsplit(url).scheme.lower() != "https":\n        raise RuntimeError(f"refusing non-HTTPS download for {label}")\n    if os.path.exists(dest):\n        if sha256 and _sha256(dest).lower() != sha256.lower():\n            raise RuntimeError(f"SHA-256 mismatch for existing {os.path.relpath(dest, _ROOT)}")\n        print(f"  already present: {os.path.relpath(dest, _ROOT)}")\n        return\n\n    destination_dir = os.path.dirname(dest) or "."\n    os.makedirs(destination_dir, exist_ok=True)\n    print(f"  downloading {label}...", end="", flush=True)\n    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})\n    temp_path: str | None = None\n    try:\n        with tempfile.NamedTemporaryFile(\n            mode="wb", delete=False, dir=destination_dir,\n            prefix=f".{os.path.basename(dest)}.", suffix=".part",\n        ) as temp_file:\n            temp_path = temp_file.name\n            with urllib.request.urlopen(req, timeout=60) as response:\n                shutil.copyfileobj(response, temp_file)\n            temp_file.flush()\n            os.fsync(temp_file.fileno())\n\n        if sha256 and _sha256(temp_path).lower() != sha256.lower():\n            raise RuntimeError(f"SHA-256 mismatch for downloaded {label}")\n        os.replace(temp_path, dest)\n        temp_path = None\n    finally:\n        if temp_path is not None and os.path.exists(temp_path):\n            os.remove(temp_path)\n    print(f" {os.path.getsize(dest) // 1024} KB")\n\n\ndef _safe_archive_destination(root: str, relative: str) -> str:\n    """Return a contained extraction path, rejecting ZIP traversal."""\n    normalized = relative.replace("\\\\", "/")\n    parts = [part for part in normalized.split("/") if part not in ("", ".")]\n    if (\n        not parts\n        or normalized.startswith("/")\n        or ":" in parts[0]\n        or any(part == ".." for part in parts)\n    ):\n        raise RuntimeError(f"unsafe archive member: {relative!r}")\n    root_real = os.path.realpath(root)\n    destination = os.path.realpath(os.path.join(root_real, *parts))\n    try:\n        contained = os.path.commonpath((root_real, destination)) == root_real\n    except ValueError:\n        contained = False\n    if not contained:\n        raise RuntimeError(f"unsafe archive member: {relative!r}")\n    return destination\n''',
)

replace_once(
    "scripts/bootstrap.py",
    '''            dest = os.path.join(SDK_INCLUDE, relative)\n            os.makedirs(os.path.dirname(dest), exist_ok=True)\n            with zf.open(member) as src, open(dest, "wb") as dst:\n                shutil.copyfileobj(src, dst)\n''',
    '''            if member.endswith("/"):\n                continue\n            dest = _safe_archive_destination(SDK_INCLUDE, relative)\n            os.makedirs(os.path.dirname(dest), exist_ok=True)\n            with zf.open(member) as src, open(dest, "wb") as dst:\n                shutil.copyfileobj(src, dst)\n''',
)

replace_once(
    "scripts/bootstrap.py",
    '''            out = os.path.join(dest_dir, rel)\n            os.makedirs(os.path.dirname(out), exist_ok=True)\n            with zf.open(name) as src, open(out, "wb") as dst:\n                shutil.copyfileobj(src, dst)\n''',
    '''            out = _safe_archive_destination(dest_dir, rel)\n            os.makedirs(os.path.dirname(out), exist_ok=True)\n            with zf.open(name) as src, open(out, "wb") as dst:\n                shutil.copyfileobj(src, dst)\n''',
)

# Harden tracker calibration and malformed measurements.
replace_once(
    "tracker/main.py",
    "from tracker.shared_settings import SharedSettingsReader\n",
    "from tracker.shared_settings import OverlaySettings, SharedSettingsReader\n",
)

replace_once(
    "tracker/main.py",
    "_CAMERA_READ_FAILURES_BEFORE_REOPEN = 3\n_CAMERA_MAX_REOPEN_ATTEMPTS = 3\n",
    "_CAMERA_READ_FAILURES_BEFORE_REOPEN = 3\n_CAMERA_MAX_REOPEN_ATTEMPTS = 3\n_DEFAULT_CAMERA_FOV_DEG = 90.0\n_DEFAULT_SMOOTHING_R = 0.1\n",
)

replace_once(
    "tracker/main.py",
    '''def _resolve_ipd_cm(cfg: dict[str, Any]) -> float:\n    """Resolve the single runtime IPD source, preferring display calibration."""\n    tracking_raw = cfg.get("tracking", {})\n    overlay_raw = cfg.get("overlay", {})\n    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}\n    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}\n    calibration_raw = overlay.get("display_calibration", {})\n    calibration = calibration_raw if isinstance(calibration_raw, dict) else {}\n    candidates_mm = (\n        calibration.get("ipd_mm"),\n        overlay.get("ipd_mm"),\n    )\n    for value in candidates_mm:\n        if not isinstance(value, (int, float, str)):\n            continue\n        try:\n            parsed = float(value)\n        except ValueError:\n            continue\n        if parsed > 0.0:\n            return parsed / 10.0\n    tracking_ipd = tracking.get("ipd_cm", 6.4)\n    if not isinstance(tracking_ipd, (int, float, str)):\n        return 6.4\n    try:\n        return max(0.1, float(tracking_ipd))\n    except ValueError:\n        return 6.4\n''',
    '''def _finite_float(value: object) -> float | None:\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError, OverflowError):\n        return None\n    return parsed if math.isfinite(parsed) else None\n\n\ndef _resolve_ipd_cm(cfg: dict[str, Any]) -> float:\n    """Resolve the single runtime IPD source, preferring display calibration."""\n    tracking_raw = cfg.get("tracking", {})\n    overlay_raw = cfg.get("overlay", {})\n    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}\n    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}\n    calibration_raw = overlay.get("display_calibration", {})\n    calibration = calibration_raw if isinstance(calibration_raw, dict) else {}\n    for value in (calibration.get("ipd_mm"), overlay.get("ipd_mm")):\n        parsed = _finite_float(value)\n        if parsed is not None and parsed > 0.0:\n            return parsed / 10.0\n    parsed = _finite_float(tracking.get("ipd_cm", 6.4))\n    if parsed is None:\n        return 6.4\n    return max(0.1, parsed)\n\n\ndef _resolve_camera_fov_deg(cfg: dict[str, Any]) -> float:\n    tracking_raw = cfg.get("tracking", {})\n    overlay_raw = cfg.get("overlay", {})\n    tracking = tracking_raw if isinstance(tracking_raw, dict) else {}\n    overlay = overlay_raw if isinstance(overlay_raw, dict) else {}\n    for value in (tracking.get("camera_fov_deg"), overlay.get("camera_fov_deg")):\n        parsed = _finite_float(value)\n        if parsed is not None and 0.0 < parsed < 180.0:\n            return parsed\n    return _DEFAULT_CAMERA_FOV_DEG\n''',
)

replace_once(
    "tracker/main.py",
    '''    if width > 0:\n        cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(width))\n    if height > 0:\n        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(height))\n    if fps > 0:\n        cap.set(cv2.CAP_PROP_FPS, float(fps))\n''',
    '''    requested = (\n        (cv2.CAP_PROP_FRAME_WIDTH, width),\n        (cv2.CAP_PROP_FRAME_HEIGHT, height),\n        (cv2.CAP_PROP_FPS, fps),\n    )\n    for property_id, value in requested:\n        parsed = _finite_float(value)\n        if parsed is None or parsed <= 0.0:\n            continue\n        try:\n            cap.set(property_id, parsed)\n        except (cv2.error, TypeError, ValueError):\n            continue\n''',
)

replace_once(
    "tracker/main.py",
    '''    module = importlib.import_module("tracker.face_tracker_cv2")\n    return module.FaceTracker, "cv2"\n\n\ndef _limit_pose_step(\n''',
    '''    module = importlib.import_module("tracker.face_tracker_cv2")\n    return module.FaceTracker, "cv2"\n\n\ndef _validated_live_calibration(\n    settings: OverlaySettings | None,\n) -> tuple[float | None, float | None]:\n    if settings is None:\n        return None, None\n    ipd_mm = _finite_float(settings.ipd_mm)\n    ipd_cm = ipd_mm / 10.0 if ipd_mm is not None and ipd_mm > 0.0 else None\n    fov = _finite_float(settings.camera_fov_deg)\n    if fov is None or not (0.0 < fov < 180.0):\n        fov = None\n    return ipd_cm, fov\n\n\ndef _measurement_noise(settings: OverlaySettings | None) -> float:\n    if settings is None:\n        return _DEFAULT_SMOOTHING_R\n    parsed = _finite_float(settings.smoothing_alpha)\n    if parsed is None or parsed <= 0.0:\n        return _DEFAULT_SMOOTHING_R\n    return max(parsed, 1e-6)\n\n\ndef _apply_live_calibration(\n    tracker: FaceTrackerLike,\n    settings: OverlaySettings | None,\n    previous: tuple[float | None, float | None] | None,\n) -> tuple[float | None, float | None] | None:\n    calibration = _validated_live_calibration(settings)\n    if calibration == previous:\n        return previous\n    real_ipd_cm, camera_fov_deg = calibration\n    values: dict[str, float] = {}\n    if real_ipd_cm is not None:\n        values["real_ipd_cm"] = real_ipd_cm\n    if camera_fov_deg is not None:\n        values["camera_fov_deg"] = camera_fov_deg\n    if not values:\n        return previous\n    set_calibration = getattr(tracker, "set_calibration", None)\n    if not callable(set_calibration):\n        return previous\n    set_calibration(**values)\n    return calibration\n\n\ndef _validated_pose(position: HeadPosition | None) -> tuple[float, float, float] | None:\n    if position is None:\n        return None\n    x = _finite_float(position.x_cm)\n    y = _finite_float(position.y_cm)\n    z = _finite_float(position.z_cm)\n    if x is None or y is None or z is None or z <= 0.0:\n        return None\n    return x, y, z\n\n\ndef _limit_pose_step(\n''',
)

replace_once(
    "tracker/main.py",
    '''        settings_reader = SharedSettingsReader()\n        try:\n''',
    '''        settings_reader = SharedSettingsReader()\n        applied_calibration: tuple[float | None, float | None] | None = None\n        try:\n''',
)

replace_once(
    "tracker/main.py",
    '''                self._on_frame(frame)\n\n                pos: Optional[HeadPosition] = self._tracker.process_frame(frame)\n\n                if pos is not None:\n                    measurement_s = time.monotonic()\n                    self._last_face_ms = measurement_s * 1000.0\n                    settings = settings_reader.read()\n                    if settings is not None:\n                        set_calibration = getattr(self._tracker, "set_calibration", None)\n                        if callable(set_calibration):\n                            set_calibration(\n                                real_ipd_cm=max(settings.ipd_mm / 10.0, 0.1),\n                                camera_fov_deg=settings.camera_fov_deg,\n                            )\n                    smoothing_r = settings.smoothing_alpha if settings else 0.1\n                    self._smoother.set_measurement_noise(max(smoothing_r, 1e-6))\n                    raw = _limit_pose_step(\n                        (pos.x_cm, pos.y_cm, pos.z_cm),\n                        self._last_raw_pos,\n                    )\n''',
    '''                self._on_frame(frame)\n\n                settings = settings_reader.read()\n                applied_calibration = _apply_live_calibration(\n                    self._tracker, settings, applied_calibration\n                )\n                raw_position = _validated_pose(self._tracker.process_frame(frame))\n\n                if raw_position is not None:\n                    measurement_s = time.monotonic()\n                    self._last_face_ms = measurement_s * 1000.0\n                    self._smoother.set_measurement_noise(_measurement_noise(settings))\n                    raw = _limit_pose_step(\n                        raw_position,\n                        self._last_raw_pos,\n                    )\n''',
)

replace_once(
    "tracker/main.py",
    '''        camera_fov_deg=float(trk.get("camera_fov_deg", cfg.get("overlay", {}).get("camera_fov_deg", 90.0))),\n''',
    '''        camera_fov_deg=_resolve_camera_fov_deg(cfg),\n''',
)

# Do not display the overlay until at least one real depth result was uploaded.
replace_once(
    "overlay/depth_infer.h",
    '''    uint64_t inferences_completed() const;\n\n    // UV transform to convert screen UV X → depth texture UV X.\n''',
    '''    uint64_t inferences_completed() const;\n\n    // True after the first completed inference has reached the GPU texture.\n    bool has_valid_depth() const;\n\n    // UV transform to convert screen UV X → depth texture UV X.\n''',
)

replace_once(
    "overlay/depth_infer.cpp",
    '''        output_ready = false;\n        pending_input_f32.clear();\n''',
    '''        output_ready = false;\n        has_valid_depth = false;\n        pending_input_f32.clear();\n''',
)

replace_once(
    "overlay/depth_infer.cpp",
    '''uint64_t DepthInferencer::inferences_completed() const {\n    return impl_ ? impl_->inferences.load(std::memory_order_relaxed) : 0;\n}\n\nfloat DepthInferencer::depth_crop_x0_uv() const {\n''',
    '''uint64_t DepthInferencer::inferences_completed() const {\n    return impl_ ? impl_->inferences.load(std::memory_order_relaxed) : 0;\n}\n\nbool DepthInferencer::has_valid_depth() const {\n    return impl_ && impl_->has_valid_depth;\n}\n\nfloat DepthInferencer::depth_crop_x0_uv() const {\n''',
)

replace_once(
    "overlay/overlay.cpp",
    '''    const bool visible = g_captureState == CaptureState::Running\n        && g_hasFrame\n        && g_depth != nullptr\n        && (g_targetExePath.empty() || (targetForeground && captureFresh));\n''',
    '''    const bool visible = g_captureState == CaptureState::Running\n        && g_hasFrame\n        && g_depth != nullptr\n        && g_depth->has_valid_depth()\n        && (g_targetExePath.empty() || (targetForeground && captureFresh));\n''',
)

# Regression coverage.
replace_once(
    "tests/test_bootstrap.py",
    "from scripts import bootstrap\n",
    "import hashlib\nimport io\nimport zipfile\n\nimport pytest\n\nfrom scripts import bootstrap\n",
)

append_once(
    "tests/test_bootstrap.py",
    "def test_download_is_atomic_and_verifies_before_replace",
    '''\n\nclass _Response(io.BytesIO):\n    def __enter__(self):\n        return self\n\n    def __exit__(self, *_args):\n        self.close()\n\n\ndef test_download_is_atomic_and_verifies_before_replace(tmp_path, monkeypatch):\n    payload = b"verified payload"\n    expected = hashlib.sha256(payload).hexdigest()\n    destination = tmp_path / "asset.bin"\n    monkeypatch.setattr(\n        bootstrap.urllib.request, "urlopen",\n        lambda _request, timeout: _Response(payload),\n    )\n    bootstrap._download(\n        "https://example.test/asset.bin", str(destination), "asset", sha256=expected\n    )\n    assert destination.read_bytes() == payload\n    assert not list(tmp_path.glob(".*.part"))\n\n\ndef test_download_rejects_non_https_url(tmp_path):\n    with pytest.raises(RuntimeError, match="non-HTTPS"):\n        bootstrap._download(\n            "http://example.test/asset.bin", str(tmp_path / "asset.bin"), "asset"\n        )\n\n\ndef test_nupkg_extraction_rejects_path_traversal(tmp_path):\n    package = tmp_path / "unsafe.nupkg"\n    destination = tmp_path / "extract"\n    with zipfile.ZipFile(package, "w") as archive:\n        archive.writestr("payload/../../escape.txt", "owned")\n    with pytest.raises(RuntimeError, match="unsafe archive member"):\n        bootstrap._extract_from_nupkg(str(package), "payload/", str(destination))\n    assert not (tmp_path / "escape.txt").exists()\n\n\ndef test_nupkg_extraction_writes_contained_nested_member(tmp_path):\n    package = tmp_path / "safe.nupkg"\n    destination = tmp_path / "extract"\n    with zipfile.ZipFile(package, "w") as archive:\n        archive.writestr("payload/nested/asset.dll", b"dll")\n    assert bootstrap._extract_from_nupkg(\n        str(package), "payload/", str(destination)\n    ) == 1\n    assert (destination / "nested" / "asset.dll").read_bytes() == b"dll"\n''',
)

append_once(
    "tests/test_main.py",
    "def test_live_calibration_is_applied_before_tracking",
    '''\n\ndef test_resolvers_reject_non_finite_calibration_values():\n    cfg = {\n        "tracking": {"ipd_cm": "inf", "camera_fov_deg": "nan"},\n        "overlay": {"camera_fov_deg": "inf"},\n    }\n    assert tracker_main._resolve_ipd_cm(cfg) == 6.4\n    assert tracker_main._resolve_camera_fov_deg(cfg) == 90.0\n\n\ndef test_live_calibration_is_applied_before_tracking():\n    events = []\n\n    class Tracker:\n        def set_calibration(self, **values):\n            events.append(("calibration", values))\n\n        def process_frame(self, _frame):\n            events.append(("process", None))\n            return HeadPosition(x_cm=1.0, y_cm=2.0, z_cm=60.0)\n\n    class FakeSettingsReader:\n        def read(self):\n            return OverlaySettings(ipd_mm=65.0, camera_fov_deg=88.0)\n\n        def close(self):\n            pass\n\n    smoother = MagicMock()\n    smoother.update.return_value = (1.0, 2.0, 60.0)\n    loop = TrackingLoop(Tracker(), MagicMock(), smoother)\n    with (\n        patch("tracker.main.cv2.VideoCapture", return_value=_make_mock_cap()),\n        patch("tracker.main.SharedSettingsReader", return_value=FakeSettingsReader()),\n    ):\n        loop.run(max_frames=1)\n\n    assert events[0] == (\n        "calibration", {"real_ipd_cm": 6.5, "camera_fov_deg": 88.0}\n    )\n    assert events[1][0] == "process"\n\n\ndef test_non_finite_tracker_pose_is_treated_as_missing_face():\n    tracker = MagicMock()\n    tracker.process_frame.return_value = HeadPosition(\n        x_cm=float("nan"), y_cm=0.0, z_cm=60.0\n    )\n    writer = MagicMock()\n    smoother = MagicMock()\n    loop = TrackingLoop(tracker, writer, smoother, hold_ms=0)\n    with patch("tracker.main.cv2.VideoCapture", return_value=_make_mock_cap()):\n        loop.run(max_frames=1)\n    smoother.update.assert_not_called()\n    writer.write.assert_called_once_with(x=0.0, y=0.0, z=60.0)\n''',
)

append_once(
    "tests/test_phase0_head_coupled.py",
    "def test_overlay_remains_hidden_until_first_real_depth_upload",
    '''\n\ndef test_overlay_remains_hidden_until_first_real_depth_upload():\n    overlay = source("overlay/overlay.cpp")\n    header = source("overlay/depth_infer.h")\n    implementation = source("overlay/depth_infer.cpp")\n    assert "g_depth->has_valid_depth()" in overlay\n    assert "bool has_valid_depth() const;" in header\n    assert "bool DepthInferencer::has_valid_depth() const" in implementation\n    assert "has_valid_depth = false;" in implementation\n''',
)
