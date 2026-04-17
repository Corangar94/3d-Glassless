from scripts import run_evaluation


def test_run_evaluation_delegates_to_evaluation_suite_main(monkeypatch):
    calls = []
    monkeypatch.setattr(run_evaluation.evaluation_suite, "main", lambda argv: calls.append(argv) or 4)

    code = run_evaluation.main(["--depth-dir", "frames"])

    assert code == 4
    assert calls == [["--depth-dir", "frames"]]
