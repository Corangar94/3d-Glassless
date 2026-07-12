from launcher.auto_tune import TrackingAutoTuner


def test_auto_tuner_is_stable_while_head_is_still():
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    result = tuner.update(0.0, 0.0, 60.0, 0.1)

    assert result.smoothing_alpha == 0.28
    assert result.deadzone_mm == 3.0
    assert result.head_dist_cm == 60.0


def test_auto_tuner_becomes_responsive_during_deliberate_motion():
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    result = None
    for index in range(1, 8):
        result = tuner.update(float(index) * 4.0, 0.0, 60.0, index * 0.1)

    assert result is not None
    assert result.smoothing_alpha < 0.1
    assert result.deadzone_mm < 1.0


def test_auto_tuner_filters_and_clamps_viewing_distance():
    tuner = TrackingAutoTuner()
    tuner.update(0.0, 0.0, 60.0, 0.0)
    result = tuner.update(0.0, 0.0, 500.0, 0.1)

    assert 60.0 < result.head_dist_cm < 200.0
