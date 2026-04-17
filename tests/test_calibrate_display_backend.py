from scripts import calibrate_display_backend


def test_calibrate_display_backend_delegates_to_display_calibration_main(monkeypatch):
    calls = []
    monkeypatch.setattr(calibrate_display_backend.display_calibration, "main", lambda argv: calls.append(argv) or 13)

    code = calibrate_display_backend.main(["stereo_autostereo"])

    assert code == 13
    assert calls == [["stereo_autostereo"]]
