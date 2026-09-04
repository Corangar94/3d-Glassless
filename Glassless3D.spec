# Glassless3D.spec
# Build: pyinstaller --clean --noconfirm Glassless3D.spec
# Output: dist/Glassless3D/Glassless3D.exe
#
# A one-folder bundle is intentional. The native overlay, DirectML runtime,
# MediaPipe runtime, and depth models are large; extracting a one-file build on
# every launch increases startup latency and antivirus surface area. PyInstaller
# places runtime content under dist/Glassless3D/_internal/ while keeping the
# user-facing launcher at dist/Glassless3D/Glassless3D.exe.

from PyInstaller.utils.hooks import collect_all, collect_data_files

mediapipe_data, mediapipe_libs, mediapipe_hiddenimports = collect_all(
    "mediapipe"
)
opencv_data = collect_data_files("cv2", includes=["data/*.xml"])

runtime_datas = [
    # Standalone non-injecting native runtime and required models.
    ("Glassless3DOverlay.exe", "."),
    ("models/face_landmarker.task", "models"),
    ("models/depth_anything_v2_small_fp16.onnx", "models"),
    # ReShade explicitly requests that distributors link users to reshade.me
    # instead of repackaging its binaries or shader files. The optional,
    # offline-only integration is prepared separately by the user.
    ("profiles/wow.json", "profiles"),
    ("profiles/default.json", "profiles"),
    *mediapipe_data,
    *opencv_data,
]

runtime_binaries = [
    *mediapipe_libs,
    ("onnxruntime.dll", "."),
    ("DirectML.dll", "."),
]

hidden_imports = [
    *mediapipe_hiddenimports,
    # The frozen executable dispatches these private/optional modes at runtime.
    "tracker.main",
    "tracker.latest_frame_capture",
    "tracker.frame_freeze_detector",
    "tracker.latest_frame_runtime",
    "tracker.pose_jump_confirmation",
    "tracker.pose_stability_runtime",
    "tracker.camera_control_lock_policy",
    "tracker.camera_control_recovery",
    "tracker.camera_control_recovery_runtime",
    "tracker.live_filter_tuning",
    "tracker.live_filter_tuning_runtime",
    "tracker.backend_factory",
    "tracker.backend_failover",
    "tracker.backend_pose_bridge",
    "tracker.backend_transition_state",
    "tracker.backend_status_shared_memory",
    "tracker.sequence_mapping",
    "tracker.async_callback_order",
    "tracker.async_inference_watchdog",
    "tracker.async_result_freshness",
    "tracker.mediapipe_runtime_policy",
    "tracker.mediapipe_input",
    "tracker.face_tracker",
    "tracker.face_tracker_cv2",
    "tracker.cv2_temporal_tracker",
    "tracker.scheduled_cascade_detector",
    "tracker.camera_geometry",
    "tracker.camera_calibration",
    "tracker.calibration_runtime_sync",
    "tracker.camera_quality",
    "tracker.pose",
    "tracker.pose_filter",
    "tracker.pose_result_timeline",
    "tracker.pose_shared_memory",
    "tracker.prediction_lead",
    "tracker.pose_step_limiter",
    "tracker.shared_settings",
    "launcher.auto_tune_timeline",
    "launcher.runtime_mainwindow",
    "launcher.tracker_backend_diagnostics",
    "launcher.camera_calibration_process",
    "launcher.camera_calibration_wizard",
    "scripts.calibrate_camera",
    "wmi",
    "win32com",
    "win32com.client",
    "pythoncom",
    "pywintypes",
]

a = Analysis(
    ["launcher/__main__.py"],
    pathex=["."],
    datas=runtime_datas,
    binaries=runtime_binaries,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Glassless3D",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    uac_uiaccess=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Glassless3D",
)
