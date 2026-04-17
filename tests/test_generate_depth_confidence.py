from scripts import generate_depth_confidence


def test_generate_depth_confidence_delegates_to_depth_confidence_main(monkeypatch):
    calls = []
    monkeypatch.setattr(generate_depth_confidence.depth_confidence, "main", lambda argv: calls.append(argv) or 29)

    code = generate_depth_confidence.main(["depth.npy", "confidence.npy"])

    assert code == 29
    assert calls == [["depth.npy", "confidence.npy"]]
