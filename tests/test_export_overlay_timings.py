from scripts import export_overlay_timings


def test_export_overlay_timings_delegates_to_performance_capture_main(monkeypatch):
    calls = []
    monkeypatch.setattr(export_overlay_timings.performance_capture, "main", lambda argv: calls.append(argv) or 5)

    code = export_overlay_timings.main(["overlay.log", "timings.csv"])

    assert code == 5
    assert calls == [["overlay.log", "timings.csv"]]
