from scripts import generate_stereo_validation


def test_generate_stereo_validation_delegates_to_tracker_main(monkeypatch):
    calls = []
    monkeypatch.setattr(
        generate_stereo_validation.stereo_validation,
        "main",
        lambda argv: calls.append(argv) or 7,
    )

    code = generate_stereo_validation.main(["out"])

    assert code == 7
    assert calls == [["out"]]
