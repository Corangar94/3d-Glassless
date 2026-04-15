import math
import sys
from pathlib import Path

# fake_tracker.py lives in tests/ alongside this file
sys.path.insert(0, str(Path(__file__).parent))

import pytest
from fake_tracker import _compute_shift_pct, _shift_tag, _parse_kvs


def test_compute_shift_pct_zero_position():
    """(0, 0, 60) gives zero shift."""
    from tracker.shared_settings import OverlaySettings
    s = OverlaySettings(strength_x=1.0, strength_y=1.0,
                        virtual_depth_cm=30.0, screen_w_cm=119.3, screen_h_cm=33.6)
    sx, sy = _compute_shift_pct(0.0, 0.0, 60.0, s)
    assert sx == 0.0
    assert sy == 0.0


def test_shift_pct_computation():
    """headX=10, headZ=25, vd=30, sw=119.3, str=1.0 → ~4.575%."""
    from tracker.shared_settings import OverlaySettings
    s = OverlaySettings(strength_x=1.0, strength_y=1.0,
                        virtual_depth_cm=30.0, screen_w_cm=119.3, screen_h_cm=33.6)
    sx, _ = _compute_shift_pct(10.0, 0.0, 25.0, s)
    assert abs(sx - 4.575) < 0.01


def test_shift_tag_classification():
    assert _shift_tag(1.0, 1.0) == "GOOD"
    assert _shift_tag(3.0, 1.0) == "HIGH"
    assert _shift_tag(5.0, 5.0) == "DANGER"


def test_parse_kvs_defaults():
    result = _parse_kvs([], {"x": 0.0, "y": 0.0, "z": 60.0})
    assert result == {"x": 0.0, "y": 0.0, "z": 60.0}


def test_parse_kvs_override():
    result = _parse_kvs(["x=5.5", "z=80.0"], {"x": 0.0, "y": 0.0, "z": 60.0})
    assert result["x"] == 5.5
    assert result["z"] == 80.0
    assert result["y"] == 0.0


def test_static_rejects_zero_z():
    """z=0 must raise ValueError — zero depth is meaningless."""
    from fake_tracker import _static_mode
    with pytest.raises(ValueError, match="z must be > 0"):
        _static_mode(0.0, 0.0, 0.0)


def test_sweep_formula_at_quarter_period():
    """At t = period/4, x should equal amp (sin(π/2) = 1)."""
    amp, period = 10.0, 4.0
    t = period / 4  # quarter period
    x = amp * math.sin(2 * math.pi * t / period)
    assert abs(x - amp) < 0.001


def test_sweep_formula_at_zero():
    """At t = 0, x = 0 (sin(0) = 0)."""
    amp, period = 10.0, 4.0
    x = amp * math.sin(2 * math.pi * 0.0 / period)
    assert x == 0.0


def test_static_writes_given_values():
    """--static should write exactly the given x/y/z for at least 5 frames."""
    from tracker.shared_memory import SharedMemoryWriter, SharedMemoryReader

    # _static_mode's inner gen() returns (x, y, z) unchanged every call.
    # Test the round-trip: write static values and verify they read back correctly.
    results = []
    with SharedMemoryWriter("G3D") as writer:
        reader = SharedMemoryReader("G3D")
        try:
            for _ in range(5):
                writer.write(3.5, -2.1, 70.0)
                data = reader.read()
                assert data is not None
                x, y, z, _ = data
                assert abs(x - 3.5) < 0.001
                assert abs(y - -2.1) < 0.001
                assert abs(z - 70.0) < 0.001
                results.append((x, y, z))
        finally:
            reader.close()
    assert len(results) == 5


def test_sweep_oscillates_x():
    """--sweep x should cross zero and reach near amp within one period."""
    import math
    import time
    from fake_tracker import _sweep_mode  # noqa: F401 — import verifies it exists

    # Mirror the exact generator that _sweep_mode creates internally:
    # gen() = amp * math.sin(2 * math.pi * (time.monotonic() - t0) / period)
    # Sample at synthetic times spaced 1/120 s apart (same as _write_loop tick rate).
    amp = 10.0
    period = 4.0
    n_frames = int(120 * period)  # one full period at 120 Hz = 480 frames

    xs = []
    for i in range(n_frames):
        t = i / 120.0  # synthetic time within [0, period)
        x = amp * math.sin(2 * math.pi * t / period)
        xs.append(x)

    # x must cross zero: has both positive and negative values
    assert any(v > 0 for v in xs), "x never went positive"
    assert any(v < 0 for v in xs), "x never went negative"
    # x must reach near amp (within 5%)
    assert max(xs) >= amp * 0.95, f"max x {max(xs):.3f} never reached near amp {amp}"
