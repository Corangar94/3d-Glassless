# Glassless3D.spec
# Build: pyinstaller Glassless3D.spec
# Output: dist/Glassless3D.exe

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

block_cipher = None

# Collect mediapipe model data
mediapipe_data = collect_data_files("mediapipe")
mediapipe_libs = collect_dynamic_libs("mediapipe")

a = Analysis(
    ["launcher/__main__.py"],
    pathex=["."],
    datas=[
        # Standalone non-injecting runtime and its required models.
        ("Glassless3DOverlay.exe", "."),
        ("models/face_landmarker.task", "models"),
        ("models/depth_anything_v2_small_fp16.onnx", "models"),
        # ReShade explicitly requests that distributors link users to
        # reshade.me instead of repackaging its binaries or shader files.
        # The optional offline-only integration is prepared separately with
        # `python scripts/bootstrap.py --with-reshade`.
        ("profiles/wow.json",       "profiles"),
        ("profiles/default.json",   "profiles"),
        # MediaPipe models
        *mediapipe_data,
    ],
    # Native inference dependencies must be beside the extracted overlay.
    # PyInstaller places root-target binaries in its one-file extraction root.
    binaries=[
        *mediapipe_libs,
        ("onnxruntime.dll", "."),
        ("DirectML.dll", "."),
    ],
    hiddenimports=[
        # The frozen executable dispatches this private child mode at runtime.
        "tracker.main",
        "tracker.face_tracker",
        "tracker.face_tracker_cv2",
        "wmi",
        "win32com",
        "win32com.client",
        "pythoncom",
        "pywintypes",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Glassless3D",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=False,
    uac_uiaccess=False,
    icon=None,
)
