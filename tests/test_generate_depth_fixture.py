from scripts import generate_depth_fixture


def test_generate_depth_fixture_delegates_to_depth_synthetic_main(monkeypatch):
    calls = []
    monkeypatch.setattr(generate_depth_fixture.depth_synthetic, "main", lambda argv: calls.append(argv) or 6)

    code = generate_depth_fixture.main(["frames", "--mode", "breathing"])

    assert code == 6
    assert calls == [["frames", "--mode", "breathing"]]
