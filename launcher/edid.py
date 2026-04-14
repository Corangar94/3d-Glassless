"""WMI-based screen size detection for Windows."""
from __future__ import annotations


def detect_screen_size_cm() -> tuple[float, float] | None:
    """Return (width_cm, height_cm) from WMI, or None on failure.

    Uses Win32_DesktopMonitor.ScreenWidth / ScreenHeight (in mm).
    Returns None if WMI is unavailable or dimensions are zero.
    """
    try:
        import wmi

        c = wmi.WMI()
        monitors = c.Win32_DesktopMonitor()
        if not monitors:
            return None
        monitor = monitors[0]
        width_mm = getattr(monitor, "ScreenWidth", 0) or 0
        height_mm = getattr(monitor, "ScreenHeight", 0) or 0
        if width_mm == 0 or height_mm == 0:
            return None
        return float(width_mm) / 10.0, float(height_mm) / 10.0
    except Exception:
        return None
