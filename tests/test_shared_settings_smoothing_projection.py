from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_settings_layout_and_field_indices_are_unchanged():
    source = _source("tracker/shared_settings.py")

    assert 'STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI"' in source
    assert "STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 88" in source
    assert "SMOOTHING_ALPHA_INDEX = 11" in source
    assert "VERSION_INDEX = 15" in source
    assert 'SMOOTHING_ALPHA_OFFSET = struct.calcsize("<fffffIfffff")' in source
    assert 'VERSION_OFFSET = struct.calcsize("<fffffIfffffffII")' in source


def test_projection_reads_version_before_and_after_only_one_float():
    source = _source("tracker/shared_settings.py")
    projection = source.split(
        "    def read_smoothing_alpha(self)",
        1,
    )[1].split("    def read(self)", 1)[0]

    first = projection.index("first_version = ctypes.c_uint32.from_address(")
    odd = projection.index("if first_version & 1:")
    smoothing = projection.index("smoothing_alpha = ctypes.c_float.from_address(")
    second = projection.index("second_version = ctypes.c_uint32.from_address(")
    compare = projection.index("first_version == second_version")
    result = projection.index("return second_version, float(smoothing_alpha)")

    assert first < odd < smoothing < second < compare < result
    assert "bytes(" not in projection
    assert "struct.unpack(" not in projection
    assert "OverlaySettings(" not in projection


def test_projection_and_full_reader_share_same_version_marker():
    source = _source("tracker/shared_settings.py")
    projection = source.split(
        "    def read_smoothing_alpha(self)",
        1,
    )[1].split("    def read(self)", 1)[0]
    full_read = source.split("    def read(self)", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    assert projection.count("VERSION_OFFSET") == 2
    assert "struct.unpack_from(" in full_read
    assert "VERSION_OFFSET" in full_read
    assert "f[11]" in full_read


def test_live_controller_prefers_projection_and_preserves_full_reader_fallback():
    source = _source("tracker/live_filter_tuning.py")
    reader = source.split(
        "    def _read_measurement_noise(",
        1,
    )[1].split("    def poll(", 1)[0]

    projection = reader.index('getattr(self._reader, "read_smoothing_alpha", None)')
    callable_gate = reader.index("if callable(fast_read):")
    full_read = reader.index("settings = self._reader.read()")

    assert projection < callable_gate < full_read
    assert "parsed.version == self._last_seen_settings_version" in reader
    assert "parsed.value == self._last_seen_settings_value" in reader
    assert "self._unchanged_version_count += 1" in reader
    assert "self._version_value_collision_count += 1" in reader


def test_apply_failure_does_not_commit_version_value_pair_before_retry():
    source = _source("tracker/live_filter_tuning.py")
    poll = source.split("    def poll(", 1)[1].split("    def close(", 1)[0]

    setter = poll.index("self._target.set_measurement_noise(measurement_noise)")
    failure = poll.index("except Exception as error:", setter)
    success_commit = poll.index(
        "self._commit_version_sample(version, source)",
        failure,
    )

    assert setter < failure < success_commit
    failure_block = poll[failure:success_commit]
    assert "Do not commit the version/value pair" in failure_block
    assert "self._commit_version_sample" not in failure_block


def test_writer_coordination_reads_mapping_version_under_named_mutex():
    source = _source("tracker/shared_settings.py")
    write = source.split("    def write(self, settings:", 1)[1].split(
        "    def close(self)",
        1,
    )[0]

    process_guard = write.index("with self._process_write_guard():")
    mapping_version = write.index("_mapping_version(view)")
    publish = write.index("self._publish_locked(settings, base_version)")

    assert process_guard < mapping_version < publish
    assert 'f"{name}_WriteMutex"' in source


def test_documentation_records_projection_legacy_fallback_and_writer_coordination():
    docs = _source("docs/LIVE_FILTER_TUNING.md")

    assert "version-aware scalar projection" in docs
    assert "two full 88-byte snapshots" in docs
    assert "Readers without the projection method" in docs
    assert "named Windows mutex" in docs
    assert "preserves the existing committed snapshot" in docs
