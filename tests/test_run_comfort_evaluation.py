from scripts import run_comfort_evaluation


def test_run_comfort_evaluation_delegates_to_comfort_evaluation_main(monkeypatch):
    calls = []
    monkeypatch.setattr(run_comfort_evaluation.comfort_evaluation, "main", lambda argv: calls.append(argv) or 17)

    code = run_comfort_evaluation.main(["comfort.csv"])

    assert code == 17
    assert calls == [["comfort.csv"]]
