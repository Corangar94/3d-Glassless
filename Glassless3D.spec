# Glassless3D.spec
# Build: pyinstaller --clean --noconfirm Glassless3D.spec
# Output: dist/Glassless3D/Glassless3D.exe
#
# A one-folder bundle is intentional. The native overlay, DirectML runtime,
# MediaPipe runtime, and depth models are large; extracting a one-file build on
# every launch increases startup latency and antivirus surface area. PyInstaller
# places runtime content under dist/Glassless3D/_internal/ while keeping the
# user-facing launcher at dist/Glassless3D/Glassless3D.exe.

from scripts.prepare_mediapipe_runtime import (
    forbidden_runtime_prefixes,
    prepare_slim_mediapipe_runtime,
)

slim_mediapipe = prepare_slim_mediapipe_runtime("build/mediapipe_slim")

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
]

runtime_binaries = [
    # Face Landmarker uses importlib.resources to locate this exact path.
    (str(slim_mediapipe.library_path), "mediapipe/tasks/c"),
    ("onnxruntime.dll", "."),
    ("DirectML.dll", "."),
]

hidden_imports = [
    # The frozen executable dispatches these private modes dynamically.
    "launcher.frozen_self_test",
    "launcher.system_tray",
    "tracker.main",
    "tracker.face_tracker",
    "tracker.face_tracker_cv2",
    "tracker.camera_geometry",
    "tracker.camera_quality",
    "tracker.pose",
    "tracker.pose_filter",
    "tracker.pose_shared_memory",
    # WMI diagnostics resolve these dynamically.
    "wmi",
    "win32com",
    "win32com.client",
    "pythoncom",
    "pywintypes",
]

excluded_modules = [
    # The tracker no longer uses the optional child-process tray stack.
    "PIL",
    "pystray",
    # MediaPipe declares these for unrelated tasks; the slim package excludes
    # them and their heavy transitive UI/audio dependencies.
    "matplotlib",
    "sounddevice",
    "tkinter",
    *forbidden_runtime_prefixes(),
]

a = Analysis(
    ["launcher/__main__.py"],
    # Put the generated package before site-packages so analysis sees the
    # narrow package initializers instead of MediaPipe's umbrella imports.
    pathex=[str(slim_mediapipe.root), "."],
    datas=runtime_datas,
    binaries=runtime_binaries,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
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
