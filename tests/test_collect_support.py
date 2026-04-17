from scripts import collect_support


def test_collect_support_delegates_to_support_bundle_main(monkeypatch):
    calls = []
    monkeypatch.setattr(collect_support.support_bundle, "main", lambda argv: calls.append(argv) or 7)

    code = collect_support.main(["--output-dir", "bundle"])

    assert code == 7
    assert calls == [["--output-dir", "bundle"]]
