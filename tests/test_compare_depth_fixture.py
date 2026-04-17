from scripts import compare_depth_fixture


def test_compare_depth_fixture_delegates_to_depth_comparison_main(monkeypatch):
    calls = []
    monkeypatch.setattr(compare_depth_fixture.depth_comparison, "main", lambda argv: calls.append(argv) or 8)

    code = compare_depth_fixture.main(["captured", "baseline"])

    assert code == 8
    assert calls == [["captured", "baseline"]]
