from scripts import import_depth_capture


def test_import_depth_capture_delegates_to_depth_capture_import_main(monkeypatch):
    calls = []
    monkeypatch.setattr(import_depth_capture.depth_capture_import, "main", lambda argv: calls.append(argv) or 10)

    code = import_depth_capture.main(["screens", "frames"])

    assert code == 10
    assert calls == [["screens", "frames"]]
