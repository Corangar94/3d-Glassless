from launcher import window_discovery
from launcher.window_discovery import RunningGameWindow


def test_running_window_label_identifies_title_binary_and_pid():
    candidate = RunningGameWindow(
        title="Example Game",
        executable_path=r"C:\Games\Example\Example.exe",
        pid=1234,
        hwnd=5678,
    )

    assert candidate.label == "Example Game — Example.exe (PID 1234)"


def test_discovery_is_empty_off_windows(monkeypatch):
    monkeypatch.setattr(window_discovery.sys, "platform", "linux")

    assert window_discovery.discover_running_game_windows() == []
