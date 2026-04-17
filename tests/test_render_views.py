from scripts import render_views


def test_render_views_delegates_to_view_renderer_main(monkeypatch):
    calls = []
    monkeypatch.setattr(render_views.view_renderer, "main", lambda argv: calls.append(argv) or 9)

    code = render_views.main(["image.png", "depth.npy", "out.png"])

    assert code == 9
    assert calls == [["image.png", "depth.npy", "out.png"]]
