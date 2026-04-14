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
    binaries=mediapipe_libs,
    datas=[
        # Bundled ReShade assets
        ("ReShade64.dll",           "."),
        ("shaders/Glassless3D.fx",  "shaders"),
        ("shaders/Glassless3D.fxh", "shaders"),
        ("Glassless3D.addon",       "."),
        ("profiles/wow.json",       "profiles"),
        ("profiles/default.json",   "profiles"),
        # MediaPipe models
        *mediapipe_data,
    ],
    hiddenimports=[
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
