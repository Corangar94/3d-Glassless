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


# Secure bootstrap downloads and archive extraction.
replace_once(
    "scripts/bootstrap.py",
    "import subprocess\nimport sys\nimport urllib.request\nimport zipfile\n",
    "import subprocess\nimport sys\nimport tempfile\nimport urllib.parse\nimport urllib.request\nimport zipfile\n",
)

replace_once(
    "scripts/bootstrap.py",
    '''def _download(url: str, dest: str, label: str, *, sha256: str | None = None) -> None:\n    if os.path.exists(dest):\n        if sha256 and _sha256(dest).lower() != sha256.lower():\n            raise RuntimeError(f"SHA-256 mismatch for existing {os.path.relpath(dest, _ROOT)}")\n        print(f"  already present: {os.path.relpath(dest, _ROOT)}")\n        return\n    os.makedirs(os.path.dirname(dest), exist_ok=True)\n    print(f"  downloading {label}...", end="", flush=True)\n    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})\n    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:\n        shutil.copyfileobj(r, f)\n    if sha256 and _sha256(dest).lower() != sha256.lower():\n        os.remove(dest)\n        raise RuntimeError(f"SHA-256 mismatch for downloaded {label}")\n    print(f" {os.path.getsize(dest) // 1024} KB")\n''',
    '''def _download(url: str, dest: str, label: str, *, sha256: str | None = None) -> None:\n    if urllib.parse.urlsplit(url).scheme.lower() != "https":\n        raise RuntimeError(f"refusing non-HTTPS download for {label}")\n    if os.path.exists(dest):\n        if sha256 and _sha256(dest).lower() != sha256.lower():\n            raise RuntimeError(f"SHA-256 mismatch for existing {os.path.relpath(dest, _ROOT)}")\n        print(f"  already present: {os.path.relpath(dest, _ROOT)}")\n        return\n\n    destination_dir = os.path.dirname(dest) or "."\n    os.makedirs(destination_dir, exist_ok=True)\n    print(f"  downloading {label}...", end="", flush=True)\n    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})\n    temp_path: str | None = None\n    try:\n        with tempfile.NamedTemporaryFile(\n            mode="wb",\n            delete=False,\n            dir=destination_dir,\n            prefix=f".{os.path.basename(dest)}.",\n            suffix=".part",\n        ) as temp_file:\n            temp_path = temp_file.name\n            with urllib.request.urlopen(req, timeout=60) as response:\n                shutil.copyfileobj(response, temp_file)\n            temp_file.flush()\n            os.fsync(temp_file.fileno())\n\n        if sha256 and _sha256(temp_path).lower() != sha256.lower():\n            raise RuntimeError(f"SHA-256 mismatch for downloaded {label}")\n        os.replace(temp_path, dest)\n        temp_path = None\n    finally:\n        if temp_path is not None and os.path.exists(temp_path):\n            os.remove(temp_path)\n    print(f" {os.path.getsize(dest) // 1024} KB")\n\n\ndef _safe_archive_destination(root: str, relative: str) -> str:\n    """Return a contained extraction path, rejecting ZIP traversal."""\n    normalized = relative.replace("\\\\", "/")\n    parts = [part for part in normalized.split("/") if part not in ("", ".")]\n    if (\n        not parts\n        or normalized.startswith("/")\n        or ":" in parts[0]\n        or any(part == ".." for part in parts)\n    ):\n        raise RuntimeError(f"unsafe archive member: {relative!r}")\n    root_real = os.path.realpath(root)\n    destination = os.path.realpath(os.path.join(root_real, *parts))\n    try:\n        contained = os.path.commonpath((root_real, destination)) == root_real\n    except ValueError:\n        contained = False\n    if not contained:\n        raise RuntimeError(f"unsafe archive member: {relative!r}")\n    return destination\n''',
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

# Keep the native overlay hidden until a real depth inference has been uploaded.
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

# Regression coverage for the audited paths.
replace_once(
    "tests/test_bootstrap.py",
    "from scripts import bootstrap\n",
    "import hashlib\nimport io\nimport zipfile\n\nimport pytest\n\nfrom scripts import bootstrap\n",
)

append_once(
    "tests/test_bootstrap.py",
    "def test_download_is_atomic_and_verifies_before_replace",
    '''\n\nclass _Response(io.BytesIO):\n    def __enter__(self):\n        return self\n\n    def __exit__(self, *_args):\n        self.close()\n\n\ndef test_download_is_atomic_and_verifies_before_replace(tmp_path, monkeypatch):\n    payload = b"verified payload"\n    expected = hashlib.sha256(payload).hexdigest()\n    destination = tmp_path / "asset.bin"\n    monkeypatch.setattr(\n        bootstrap.urllib.request,\n        "urlopen",\n        lambda _request, timeout: _Response(payload),\n    )\n\n    bootstrap._download(\n        "https://example.test/asset.bin",\n        str(destination),\n        "asset",\n        sha256=expected,\n    )\n\n    assert destination.read_bytes() == payload\n    assert not list(tmp_path.glob(".*.part"))\n\n\ndef test_download_rejects_non_https_url(tmp_path):\n    with pytest.raises(RuntimeError, match="non-HTTPS"):\n        bootstrap._download(\n            "http://example.test/asset.bin",\n            str(tmp_path / "asset.bin"),\n            "asset",\n        )\n\n\ndef test_nupkg_extraction_rejects_path_traversal(tmp_path):\n    package = tmp_path / "unsafe.nupkg"\n    destination = tmp_path / "extract"\n    with zipfile.ZipFile(package, "w") as archive:\n        archive.writestr("payload/../../escape.txt", "owned")\n\n    with pytest.raises(RuntimeError, match="unsafe archive member"):\n        bootstrap._extract_from_nupkg(\n            str(package),\n            "payload/",\n            str(destination),\n        )\n\n    assert not (tmp_path / "escape.txt").exists()\n\n\ndef test_nupkg_extraction_rejects_windows_style_traversal(tmp_path):\n    package = tmp_path / "unsafe-windows.nupkg"\n    destination = tmp_path / "extract"\n    with zipfile.ZipFile(package, "w") as archive:\n        archive.writestr(r"payload/..\\escape.txt", "owned")\n\n    with pytest.raises(RuntimeError, match="unsafe archive member"):\n        bootstrap._extract_from_nupkg(\n            str(package),\n            "payload/",\n            str(destination),\n        )\n\n    assert not (tmp_path / "escape.txt").exists()\n\n\ndef test_nupkg_extraction_writes_contained_nested_member(tmp_path):\n    package = tmp_path / "safe.nupkg"\n    destination = tmp_path / "extract"\n    with zipfile.ZipFile(package, "w") as archive:\n        archive.writestr("payload/nested/asset.dll", b"dll")\n\n    assert bootstrap._extract_from_nupkg(\n        str(package),\n        "payload/",\n        str(destination),\n    ) == 1\n    assert (destination / "nested" / "asset.dll").read_bytes() == b"dll"\n''',
)

append_once(
    "tests/test_main.py",
    "def test_live_calibration_is_applied_before_tracking",
    '''\n\ndef test_resolvers_reject_non_finite_calibration_values():\n    cfg = {\n        "tracking": {"ipd_cm": "inf", "camera_fov_deg": "nan"},\n        "overlay": {"camera_fov_deg": "inf"},\n    }\n    assert tracker_main._resolve_ipd_cm(cfg) == 6.4\n    assert tracker_main._resolve_camera_fov_deg(cfg) == 90.0\n\n\ndef test_live_calibration_is_applied_before_tracking():\n    events = []\n\n    class Tracker:\n        def set_calibration(self, **values):\n            events.append(("calibration", values))\n\n        def process_frame(self, _frame):\n            events.append(("process", None))\n            return HeadPosition(x_cm=1.0, y_cm=2.0, z_cm=60.0)\n\n    class FakeSettingsReader:\n        def read(self):\n            return OverlaySettings(ipd_mm=65.0, camera_fov_deg=88.0)\n\n        def close(self):\n            pass\n\n    smoother = MagicMock()\n    smoother.update.return_value = (1.0, 2.0, 60.0)\n    loop = TrackingLoop(Tracker(), MagicMock(), smoother)\n    with (\n        patch("tracker.main.cv2.VideoCapture", return_value=_make_mock_cap()),\n        patch("tracker.main.SharedSettingsReader", return_value=FakeSettingsReader()),\n    ):\n        loop.run(max_frames=1)\n\n    assert events[0] == (\n        "calibration",\n        {"real_ipd_cm": 6.5, "camera_fov_deg": 88.0},\n    )\n    assert events[1][0] == "process"\n\n\ndef test_non_finite_tracker_pose_is_treated_as_missing_face():\n    tracker = MagicMock()\n    tracker.process_frame.return_value = HeadPosition(\n        x_cm=float("nan"), y_cm=0.0, z_cm=60.0\n    )\n    writer = MagicMock()\n    smoother = MagicMock()\n    loop = TrackingLoop(tracker, writer, smoother, hold_ms=0)\n    with patch("tracker.main.cv2.VideoCapture", return_value=_make_mock_cap()):\n        loop.run(max_frames=1)\n\n    smoother.update.assert_not_called()\n    writer.write.assert_called_once_with(x=0.0, y=0.0, z=60.0)\n''',
)

append_once(
    "tests/test_phase0_head_coupled.py",
    "def test_overlay_remains_hidden_until_first_real_depth_upload",
    '''\n\ndef test_overlay_remains_hidden_until_first_real_depth_upload():\n    overlay = source("overlay/overlay.cpp")\n    header = source("overlay/depth_infer.h")\n    implementation = source("overlay/depth_infer.cpp")\n    assert "g_depth->has_valid_depth()" in overlay\n    assert "bool has_valid_depth() const;" in header\n    assert "bool DepthInferencer::has_valid_depth() const" in implementation\n    assert "has_valid_depth = false;" in implementation\n''',
)
