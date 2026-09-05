from tracker.live_filter_tuning import LiveFilterTuningSnapshot


def test_original_positional_snapshot_constructor_remains_compatible():
    snapshot = LiveFilterTuningSnapshot(
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        0.25,
        12.0,
        False,
        "legacy",
    )

    assert snapshot.poll_count == 1
    assert snapshot.close_error_count == 11
    assert snapshot.last_applied_measurement_noise == 0.25
    assert snapshot.last_poll_s == 12.0
    assert snapshot.closed is False
    assert snapshot.last_error == "legacy"
    assert snapshot.version_fast_path_count == 0
    assert snapshot.unchanged_version_count == 0
    assert snapshot.invalid_version_sample_count == 0
    assert snapshot.last_seen_settings_version is None
    assert snapshot.version_value_collision_count == 0
    assert snapshot.last_seen_settings_value is None
