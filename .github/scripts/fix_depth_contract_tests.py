from pathlib import Path

path = Path("tests/test_overlay_capture_resilience.py")
text = path.read_text(encoding="utf-8")
replacements = {
    '''    assert "kBlendDurationSec" in depth
''': '''    assert "blend_duration_sec" in depth
    assert "last_depth_arrival" in depth
    assert "interval * 0.90f" in depth
''',
    '''    assert "Apply one contrast transform over the stitched frame" in depth
''': '''    assert "smoothed_global_lo" in depth
    assert "smoothed_global_hi" in depth
    assert "smoothed_contrast_mean" in depth
    assert "smoothed_contrast_gain" in depth
''',
    '''    assert "session->Run(*run_options" in source
''': '''    assert "session->Run(" in source
    assert "*run_options" in source
''',
}
for old, new in replacements.items():
    if new in text:
        continue
    if text.count(old) != 1:
        raise RuntimeError(f"expected one stale contract assertion: {old!r}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8", newline="\n")
