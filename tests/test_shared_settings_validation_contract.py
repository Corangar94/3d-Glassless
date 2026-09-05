from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_shared_settings_abi_is_unchanged():
    source = _source("tracker/shared_settings.py")

    assert 'STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI"' in source
    assert "STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 88" in source
    assert 'SHM_NAME = "G3D_Settings"' in source
    assert "SMOOTHING_ALPHA_OFFSET" in source
    assert "VERSION_OFFSET" in source


def test_pack_settings_routes_every_wire_field_through_strict_validation():
    source = _source("tracker/shared_settings.py")
    pack = source.split("def _pack_settings(", 1)[1].split(
        "\n\n\n@contextmanager",
        1,
    )[0]

    assert pack.count("_finite_float(") == 13
    assert pack.count("_enum_uint32(") == 6
    assert pack.count("_uint32(") == 3
    assert "float(" not in pack
    assert "int(" not in pack


def test_every_abi_enum_has_an_explicit_domain():
    source = _source("tracker/shared_settings.py")
    pack = source.split("def _pack_settings(", 1)[1].split(
        "\n\n\n@contextmanager",
        1,
    )[0]

    assert '_enum_uint32(s.depth_curve, "depth_curve", (0, 1, 2))' in pack
    assert (
        '_enum_uint32(s.display_backend, "display_backend", (0, 1, 2))'
        in pack
    )
    assert (
        '_enum_uint32(s.depth_mode, "depth_mode", (0, 1, 2, 3))'
        in pack
    )
    assert '_enum_uint32(s.stereo_layout, "stereo_layout", (0, 1))' in pack
    assert '_enum_uint32(s.eye_order, "eye_order", (0, 1))' in pack
    assert '_enum_uint32(s.tracking_mode, "tracking_mode", (0, 1))' in pack


def test_complete_snapshot_is_validated_before_mapping_is_marked_odd():
    source = _source("tracker/shared_settings.py")
    publication = source.split("    def _publish_locked(", 1)[1].split(
        "    def _initialize_mapping_locked(",
        1,
    )[0]

    pack = publication.index("data = _pack_settings(settings, committed_version)")
    first_mapping_write = publication.index("ctypes.memmove(")
    version_commit = publication.rindex("ctypes.memmove(")
    local_commit = publication.index("self._version = committed_version")

    assert pack < first_mapping_write < version_commit < local_commit


def test_validation_module_replaces_coercive_local_helpers():
    source = _source("tracker/shared_settings.py")

    assert "from tracker.shared_settings_validation import (" in source
    assert "finite_float as _finite_float" in source
    assert "uint32 as _uint32" in source
    assert "enum_uint32 as _enum_uint32" in source
    assert "def _finite_float(" not in source
    assert "def _uint32(" not in source


def test_multiwriter_coordination_remains_active():
    source = _source("tracker/shared_settings.py")

    assert 'f"{name}_WriteMutex"' in source
    assert "with self._process_write_guard():" in source
    assert "_mapping_version(view)" in source
    assert "self._publish_locked(settings, base_version)" in source


def test_frozen_package_includes_validation_module():
    spec = _source("Glassless3D.spec")

    assert '"tracker.shared_settings_validation"' in spec


def test_documentation_records_strict_types_and_transaction_order():
    docs = _source("docs/SHARED_SETTINGS_VALIDATION.md")

    assert "booleans are never accepted as numbers" in docs
    assert "fractional values are never truncated" in docs
    assert "before the mapping is marked odd" in docs
    assert "88-byte ABI is unchanged" in docs
