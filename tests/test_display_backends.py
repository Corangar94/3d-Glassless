from tracker.display_backends import (
    DisplayBackend,
    DisplayBackendRegistry,
    build_display_layout,
    built_in_backends,
    backend_code,
    backend_id_from_code,
)


def test_builtin_backends_include_primary_overlay_and_future_targets():
    backends = built_in_backends()
    ids = [backend.id for backend in backends]

    assert ids[0] == "desktop_overlay"
    assert "stereo_autostereo" in ids
    assert "lightfield_quilt" in ids
    assert backends[0].status == "primary"


def test_registry_returns_default_primary_backend():
    registry = DisplayBackendRegistry(built_in_backends())

    backend = registry.default()

    assert backend.id == "desktop_overlay"
    assert backend.status == "primary"


def test_registry_lists_experimental_backends():
    registry = DisplayBackendRegistry(built_in_backends())

    experimental = registry.by_status("experimental")

    assert {backend.id for backend in experimental} == {
        "stereo_autostereo",
        "lightfield_quilt",
    }


def test_registry_rejects_duplicate_backend_ids():
    duplicate = DisplayBackend(
        id="desktop_overlay",
        label="Duplicate",
        status="experimental",
        view_count=1,
        description="duplicate",
    )

    try:
        DisplayBackendRegistry([*built_in_backends(), duplicate])
    except ValueError as e:
        assert "duplicate backend id" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_build_display_layout_returns_desktop_single_view_contract():
    layout = build_display_layout("desktop_overlay")

    assert layout.backend_id == "desktop_overlay"
    assert layout.columns == 1
    assert layout.rows == 1
    assert layout.view_count == 1
    assert layout.view_offsets == [0.0]


def test_build_display_layout_returns_stereo_two_view_contract():
    layout = build_display_layout("stereo_autostereo")

    assert layout.columns == 2
    assert layout.rows == 1
    assert layout.view_count == 2
    assert layout.view_offsets == [-0.5, 0.5]


def test_build_display_layout_returns_quilt_grid_contract():
    layout = build_display_layout("lightfield_quilt")

    assert layout.columns == 9
    assert layout.rows == 5
    assert layout.view_count == 45
    assert layout.view_offsets[0] == -1.0
    assert layout.view_offsets[-1] == 1.0


def test_backend_codes_roundtrip_stable_runtime_ids():
    assert backend_code("desktop_overlay") == 0
    assert backend_code("stereo_autostereo") == 1
    assert backend_code("lightfield_quilt") == 2
    assert backend_id_from_code(1) == "stereo_autostereo"
