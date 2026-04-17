from scripts import run_friendly_depth_experiment


def test_run_friendly_depth_experiment_delegates_to_module_main(monkeypatch):
    calls = []
    monkeypatch.setattr(
        run_friendly_depth_experiment.friendly_depth_experiment,
        "main",
        lambda argv: calls.append(argv) or 11,
    )

    code = run_friendly_depth_experiment.main(["--title", "Demo"])

    assert code == 11
    assert calls == [["--title", "Demo"]]
