from unittest.mock import MagicMock, patch
from launcher.edid import detect_screen_size_cm


def _make_wmi_monitor(width_mm: int, height_mm: int):
    monitor = MagicMock()
    monitor.ScreenWidth = width_mm
    monitor.ScreenHeight = height_mm
    return monitor


def test_detect_screen_size_cm_returns_dimensions_from_wmi():
    mock_wmi_instance = MagicMock()
    mock_wmi_instance.Win32_DesktopMonitor.return_value = [
        _make_wmi_monitor(597, 336)
    ]
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.return_value = mock_wmi_instance
        result = detect_screen_size_cm()
    assert result == (59.7, 33.6)


def test_detect_screen_size_cm_returns_none_when_no_monitors():
    mock_wmi_instance = MagicMock()
    mock_wmi_instance.Win32_DesktopMonitor.return_value = []
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.return_value = mock_wmi_instance
        result = detect_screen_size_cm()
    assert result is None


def test_detect_screen_size_cm_returns_none_when_dimensions_are_zero():
    mock_wmi_instance = MagicMock()
    mock_wmi_instance.Win32_DesktopMonitor.return_value = [
        _make_wmi_monitor(0, 0)
    ]
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.return_value = mock_wmi_instance
        result = detect_screen_size_cm()
    assert result is None


def test_detect_screen_size_cm_returns_none_on_wmi_exception():
    with patch("launcher.edid.wmi") as mock_wmi_module:
        mock_wmi_module.WMI.side_effect = Exception("WMI unavailable")
        result = detect_screen_size_cm()
    assert result is None
