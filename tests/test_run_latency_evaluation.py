from scripts import run_latency_evaluation


def test_run_latency_evaluation_delegates_to_latency_main(monkeypatch):
    calls = []
    monkeypatch.setattr(run_latency_evaluation.latency_evaluation, "main", lambda argv: calls.append(argv) or 23)

    code = run_latency_evaluation.main(["latency.csv"])

    assert code == 23
    assert calls == [["latency.csv"]]
