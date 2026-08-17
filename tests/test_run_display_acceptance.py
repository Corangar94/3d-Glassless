from scripts import run_display_acceptance


def test_run_display_acceptance_delegates_to_tracker_main(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_display_acceptance.display_acceptance,
        "main",
        lambda argv: calls.append(argv) or 17,
    )

    code = run_display_acceptance.main(["out", "--config", "config.yaml"])

    assert code == 17
    assert calls == [["out", "--config", "config.yaml"]]
