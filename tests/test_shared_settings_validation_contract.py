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


def test_pack_settings_routes_every_scalar_through_strict_validation():
    source = _source("tracker/shared_settings.py")
    pack = source.split("def _pack_settings(", 1)[1].split(
        "\n\n\nclass SharedSettingsWriter",
        1,
    )[0]

    assert pack.count("_finite_float(") == 13
    assert pack.count("_uint32(") == 9
    assert "float(" not in pack
    assert "int(" not in pack


def test_complete_snapshot_is_validated_before_mapping_is_marked_odd():
    source = _source("tracker/shared_settings.py")
    write = source.split("    def write(self, s:", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    pack = write.index("data = _pack_settings(s, committed_version)")
    first_mapping_write = write.index("ctypes.memmove(")
    version_commit = write.rindex("ctypes.memmove(")
    local_commit = write.index("self._version = committed_version")

    assert pack < first_mapping_write < version_commit < local_commit


def test_validation_module_replaces_coercive_local_helpers():
    source = _source("tracker/shared_settings.py")

    assert "from tracker.shared_settings_validation import (" in source
    assert "finite_float as _finite_float" in source
    assert "uint32 as _uint32" in source
    assert "def _finite_float(" not in source
    assert "def _uint32(" not in source


def test_frozen_package_includes_validation_module():
    spec = _source("Glassless3D.spec")

    assert '"tracker.shared_settings_validation"' in spec


def test_documentation_records_strict_types_and_transaction_order():
    docs = _source("docs/SHARED_SETTINGS_VALIDATION.md")

    assert "booleans are never accepted as numbers" in docs
    assert "fractional values are never truncated" in docs
    assert "before the mapping is marked odd" in docs
    assert "88-byte ABI is unchanged" in docs
