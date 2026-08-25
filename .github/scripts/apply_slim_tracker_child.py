from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if new and new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "tracker/main.py",
    '''def _make_tray_image():
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (64, 64), (30, 30, 30, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse([8, 8, 56, 56], fill=(60, 200, 60, 255))
    draw.ellipse([26, 26, 38, 38], fill=(255, 255, 255, 255))
    return image


''',
    "",
    "Pillow tray-image helper",
)
replace_once(
    "tracker/main.py",
    '''    stop_event = threading.Event()
    tray_icon = None
    try:
        import pystray
        def _on_quit(icon, _item):
            stop_event.set(); icon.stop()
        tray_icon = pystray.Icon("G3D Tracker", _make_tray_image(), "Glassless3D Tracker", menu=pystray.Menu(pystray.MenuItem("Quit Tracker", _on_quit)))
        threading.Thread(target=tray_icon.run, daemon=True).start()
        print("[G3D] Tray icon active — right-click it to quit.")
    except Exception:
        print("[G3D] Tray icon unavailable — press Ctrl+C to stop.")

''',
    '''    stop_event = threading.Event()
    print(
        "[G3D] Tracker child is supervised by the launcher; "
        "press Ctrl+C when running it directly."
    )

''',
    "child pystray startup",
)
replace_once(
    "tracker/main.py",
    '''    if tray_icon is not None:
        tray_icon.stop()
''',
    "",
    "child pystray shutdown",
)
replace_once(
    "tests/test_release_packaging.py",
    '''    assert 'collect_all(\\n    "mediapipe"\\n)' in source
''',
    '''    assert "prepare_slim_mediapipe_runtime" in source
    assert "collect_all" not in source
    assert '"PIL"' in source
    assert '"matplotlib"' in source
    assert '"sounddevice"' in source
''',
    "standalone spec MediaPipe contract",
)
