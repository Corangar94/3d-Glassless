from __future__ import annotations

from dataclasses import dataclass, replace
import math
import threading

import pytest

from launcher.auto_tune_publication import (
    AutoTunePublicationDecision,
    AutoTunePublicationGate,
    AutoTunePublicationPolicy,
    AutoTunePublicationValues,
    AutoTunePublicationWriter,
)


@dataclass(frozen=True)
class Settings:
    head_dist_cm: float = 60.0
    smoothing_alpha: float = 0.28
    deadzone_mm: float = 3.0
    unrelated: float = 1.0


class Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class Writer:
    def __init__(self) -> None:
        self.writes: list[object] = []
        self.closed = False

    def write(self, settings: object) -> str:
        self.writes.append(settings)
        return "written"

    def close(self) -> None:
        self.closed = True


class FailingWriter(Writer):
    def write(self, settings: object) -> str:
        raise RuntimeError("write failed")


def _auto_write(proxy: AutoTunePublicationWriter, settings: Settings):
    with proxy.auto_tune_write():
        return proxy.write(settings)


def test_default_policy_values_are_small_but_nonzero():
    policy = AutoTunePublicationPolicy()

    assert policy.minimum_head_distance_change_cm == pytest.approx(0.25)
    assert policy.minimum_smoothing_change == pytest.approx(0.005)
    assert policy.minimum_deadzone_change_mm == pytest.approx(0.10)
    assert policy.maximum_silence_s == pytest.approx(2.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"minimum_head_distance_change_cm": True},
        {"minimum_smoothing_change": -0.01},
        {"minimum_deadzone_change_mm": math.inf},
        {"maximum_silence_s": math.nan},
        {"maximum_silence_s": 60.1},
        {"minimum_smoothing_change": 1.1},
    ],
)
def test_invalid_policy_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        AutoTunePublicationPolicy(**kwargs)


def test_value_extraction_rejects_missing_boolean_or_nonfinite_fields():
    assert AutoTunePublicationValues.from_settings(object()) is None
    assert AutoTunePublicationValues.from_settings(
        Settings(head_dist_cm=True)
    ) is None
    assert AutoTunePublicationValues.from_settings(
        Settings(smoothing_alpha=math.nan)
    ) is None


def test_first_armed_write_after_reset_is_published():
    delegate = Writer()
    clock = Clock(10.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    settings = Settings()

    assert _auto_write(proxy, settings) == "written"

    assert delegate.writes == [settings]
    snapshot = proxy.publication_snapshot()
    assert snapshot.published_count == 1
    assert snapshot.last_values == AutoTunePublicationValues(60.0, 0.28, 3.0)
    assert snapshot.last_published_at_s == pytest.approx(10.0)


def test_manual_write_always_passes_through_and_seeds_baseline():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    manual = Settings(head_dist_cm=70.0)

    assert proxy.write(manual) == "written"
    clock.value = 1.25
    assert _auto_write(proxy, replace(manual, head_dist_cm=70.1)) is None

    assert delegate.writes == [manual]
    snapshot = proxy.publication_snapshot()
    assert snapshot.external_seed_count == 1
    assert snapshot.suppressed_count == 1
    assert snapshot.last_values == AutoTunePublicationValues(70.0, 0.28, 3.0)


def test_unrelated_settings_change_is_suppressed_in_auto_context():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)

    clock.value = 1.5
    changed = replace(baseline, unrelated=2.0)
    assert _auto_write(proxy, changed) is None
    assert delegate.writes == [baseline]


def test_exactly_unchanged_values_do_not_force_periodic_version_churn():
    delegate = Writer()
    clock = Clock(0.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    settings = Settings()
    proxy.write(settings)

    clock.value = 100.0
    assert _auto_write(proxy, settings) is None

    snapshot = proxy.publication_snapshot()
    assert snapshot.suppressed_count == 1
    assert snapshot.forced_count == 0
    assert snapshot.last_decision_reason == "tuned values unchanged"
    assert len(delegate.writes) == 1


@pytest.mark.parametrize(
    "field, delta",
    [
        ("head_dist_cm", 0.25),
        ("smoothing_alpha", -0.005),
        ("deadzone_mm", 0.10),
    ],
)
def test_exact_publication_threshold_is_immediate(field, delta):
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, **{field: getattr(baseline, field) + delta})

    clock.value = 1.25
    assert _auto_write(proxy, candidate) == "written"

    assert delegate.writes == [baseline, candidate]
    assert proxy.publication_snapshot().published_count == 1


def test_subthreshold_drift_accumulates_against_last_published_baseline():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)

    clock.value = 1.25
    assert _auto_write(proxy, replace(baseline, head_dist_cm=60.10)) is None
    clock.value = 1.50
    assert _auto_write(proxy, replace(baseline, head_dist_cm=60.20)) is None
    clock.value = 1.75
    crossed = replace(baseline, head_dist_cm=60.25)
    assert _auto_write(proxy, crossed) == "written"

    assert delegate.writes == [baseline, crossed]
    assert proxy.publication_snapshot().suppressed_count == 2


def test_changed_subthreshold_values_force_convergence_at_exact_silence_limit():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, head_dist_cm=60.10)

    clock.value = 2.999
    assert _auto_write(proxy, candidate) is None
    clock.value = 3.0
    assert _auto_write(proxy, candidate) == "written"

    snapshot = proxy.publication_snapshot()
    assert snapshot.forced_count == 1
    assert snapshot.published_count == 1
    assert snapshot.last_decision_reason == (
        "bounded silence expired; force convergence"
    )


def test_backward_clock_publishes_fail_open_and_reanchors():
    delegate = Writer()
    clock = Clock(10.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, head_dist_cm=60.1)

    clock.value = 9.0
    assert _auto_write(proxy, candidate) == "written"

    snapshot = proxy.publication_snapshot()
    assert snapshot.fail_open_count == 1
    assert snapshot.last_published_at_s == pytest.approx(9.0)


def test_invalid_candidate_publishes_fail_open_but_does_not_poison_baseline():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    invalid = Settings(head_dist_cm=math.nan)

    assert _auto_write(proxy, invalid) == "written"
    snapshot = proxy.publication_snapshot()
    assert snapshot.fail_open_count == 1
    assert snapshot.last_values is None
    assert snapshot.last_published_at_s is None

    clock.value = 1.25
    valid = Settings()
    assert _auto_write(proxy, valid) == "written"
    assert delegate.writes == [invalid, valid]


def test_clock_failure_publishes_fail_open_without_establishing_baseline():
    delegate = Writer()

    def failing_clock():
        raise RuntimeError("clock failed")

    proxy = AutoTunePublicationWriter(delegate, clock=failing_clock)
    settings = Settings()

    assert _auto_write(proxy, settings) == "written"
    snapshot = proxy.publication_snapshot()
    assert snapshot.fail_open_count == 1
    assert snapshot.last_values is None


def test_delegate_failure_does_not_commit_auto_publication_state():
    proxy = AutoTunePublicationWriter(FailingWriter(), clock=Clock(1.0))

    with pytest.raises(RuntimeError, match="write failed"):
        _auto_write(proxy, Settings())

    snapshot = proxy.publication_snapshot()
    assert snapshot.published_count == 0
    assert snapshot.last_values is None


def test_delegate_failure_does_not_seed_manual_baseline():
    proxy = AutoTunePublicationWriter(FailingWriter(), clock=Clock(1.0))

    with pytest.raises(RuntimeError, match="write failed"):
        proxy.write(Settings())

    snapshot = proxy.publication_snapshot()
    assert snapshot.external_seed_count == 0
    assert snapshot.last_values is None


def test_reset_retains_lifetime_counters_and_makes_next_write_immediate():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    clock.value = 1.25
    assert _auto_write(proxy, replace(baseline, head_dist_cm=60.1)) is None

    proxy.reset_publication()
    clock.value = 1.5
    candidate = replace(baseline, head_dist_cm=60.1)
    assert _auto_write(proxy, candidate) == "written"

    snapshot = proxy.publication_snapshot()
    assert snapshot.reset_count == 1
    assert snapshot.suppressed_count == 1
    assert snapshot.published_count == 1


def test_nested_auto_tune_context_stays_armed_until_outer_exit():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, head_dist_cm=60.1)

    with proxy.auto_tune_write():
        with proxy.auto_tune_write():
            assert proxy.write(candidate) is None
        assert proxy.write(candidate) is None

    # Outside the context the same candidate is a manual write and must publish.
    assert proxy.write(candidate) == "written"
    assert delegate.writes == [baseline, candidate]


def test_auto_tune_arming_is_thread_local_so_manual_thread_is_never_suppressed():
    delegate = Writer()
    clock = Clock(1.0)
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    manual = replace(baseline, head_dist_cm=60.1)
    result: list[object] = []

    with proxy.auto_tune_write():
        thread = threading.Thread(target=lambda: result.append(proxy.write(manual)))
        thread.start()
        thread.join(2.0)
        assert not thread.is_alive()

    assert result == ["written"]
    assert delegate.writes == [baseline, manual]


def test_close_and_attribute_forwarding_preserve_delegate_contract():
    delegate = Writer()
    delegate.extra = "forwarded"
    proxy = AutoTunePublicationWriter(delegate)

    assert proxy.extra == "forwarded"
    proxy.close()
    assert delegate.closed


def test_gate_can_be_exercised_directly_for_diagnostics():
    gate = AutoTunePublicationGate()
    settings = Settings()
    decision = gate.decide(settings, 1.0)
    assert decision == AutoTunePublicationDecision(
        publish=True,
        reason="first value after publication reset",
    )
    gate.record_published(settings, 1.0, decision)
    suppressed = gate.decide(settings, 1.25)
    assert not suppressed.publish
    gate.record_suppressed(suppressed)

    snapshot = gate.snapshot()
    assert snapshot.published_count == 1
    assert snapshot.suppressed_count == 1
