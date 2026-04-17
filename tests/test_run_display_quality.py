from scripts import run_display_quality


def test_run_display_quality_delegates_to_display_quality_main(monkeypatch):
    calls = []
    monkeypatch.setattr(run_display_quality.display_quality, "main", lambda argv: calls.append(argv) or 19)

    code = run_display_quality.main(["display_quality.csv"])

    assert code == 19
    assert calls == [["display_quality.csv"]]
