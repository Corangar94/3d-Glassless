from tracker.display_backends import (
    DisplayBackend,
    DisplayBackendRegistry,
    built_in_backends,
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
