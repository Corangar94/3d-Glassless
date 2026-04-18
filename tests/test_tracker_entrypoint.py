import runpy

import tracker.main


def test_tracker_module_entrypoint_delegates_to_main(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(tracker.main, "main", lambda: calls.append("main"))

    runpy.run_module("tracker", run_name="__main__")

    assert calls == ["main"]
