from __future__ import annotations

import threading

import pytest

from launcher.status_emission import (
    StatusEmissionDecision,
    StatusEmissionGate,
)


def test_first_status_emits_and_exact_consecutive_duplicate_is_suppressed():
    gate = StatusEmissionGate()
    emitted: list[str] = []

    first = gate.emit("tracking", emitted.append)
    duplicate = gate.emit("tracking", emitted.append)

    assert first == StatusEmissionDecision(
        emitted=True,
        status="tracking",
        reason="status transition emitted",
    )
    assert duplicate == StatusEmissionDecision(
        emitted=False,
        status="tracking",
        reason="consecutive duplicate status suppressed",
    )
    assert emitted == ["tracking"]
    snapshot = gate.snapshot()
    assert snapshot.emitted_count == 1
    assert snapshot.suppressed_count == 1
    assert snapshot.last_status == "tracking"


def test_real_transitions_emit_immediately_and_repeated_paused_is_suppressed():
    gate = StatusEmissionGate()
    emitted: list[str] = []

    for status in (
        "initializing",
        "tracking",
        "tracking",
        "paused",
        "paused",
        "tracking",
        "restarting",
        "initializing",
        "tracking",
    ):
        gate.emit(status, emitted.append)

    assert emitted == [
        "initializing",
        "tracking",
        "paused",
        "tracking",
        "restarting",
        "initializing",
        "tracking",
    ]
    snapshot = gate.snapshot()
    assert snapshot.emitted_count == 7
    assert snapshot.suppressed_count == 2
    assert snapshot.last_status == "tracking"


def test_force_emits_an_intentional_duplicate():
    gate = StatusEmissionGate()
    emitted: list[str] = []
    gate.emit("tracking", emitted.append)

    decision = gate.emit("tracking", emitted.append, force=True)

    assert decision.emitted
    assert decision.forced
    assert emitted == ["tracking", "tracking"]
    snapshot = gate.snapshot()
    assert snapshot.forced_count == 1
    assert snapshot.emitted_count == 2


def test_reset_starts_a_new_lifecycle_without_erasing_lifetime_counts():
    gate = StatusEmissionGate()
    emitted: list[str] = []
    gate.emit("initializing", emitted.append)
    gate.emit("initializing", emitted.append)

    gate.reset()
    gate.emit("initializing", emitted.append)

    assert emitted == ["initializing", "initializing"]
    snapshot = gate.snapshot()
    assert snapshot.reset_count == 1
    assert snapshot.emitted_count == 2
    assert snapshot.suppressed_count == 1
    assert snapshot.last_status == "initializing"


def test_emitter_failure_rolls_back_baseline_and_next_call_retries():
    gate = StatusEmissionGate()
    successful: list[str] = []

    def failing(_status: str) -> None:
        raise RuntimeError("signal failed")

    with pytest.raises(RuntimeError, match="signal failed"):
        gate.emit("tracking", failing)

    failed = gate.snapshot()
    assert failed.failed_emission_count == 1
    assert failed.emitted_count == 0
    assert failed.last_status is None

    retry = gate.emit("tracking", successful.append)

    assert retry.emitted
    assert successful == ["tracking"]
    assert gate.snapshot().last_status == "tracking"


def test_invalid_status_emits_fail_open_without_becoming_baseline():
    gate = StatusEmissionGate()
    emitted: list[str] = []

    decision = gate.emit(None, emitted.append)

    assert decision.emitted
    assert decision.fail_open
    assert emitted == ["None"]
    snapshot = gate.snapshot()
    assert snapshot.fail_open_count == 1
    assert snapshot.last_status is None

    # A valid first status still publishes immediately.
    gate.emit("tracking", emitted.append)
    assert emitted == ["None", "tracking"]


def test_unprintable_invalid_status_uses_bounded_fallback_text():
    class Unprintable:
        def __str__(self) -> str:
            raise RuntimeError("cannot stringify")

    gate = StatusEmissionGate()
    emitted: list[str] = []

    decision = gate.emit(Unprintable(), emitted.append)

    assert decision.fail_open
    assert emitted == ["<invalid tracker status>"]
    assert gate.snapshot().last_status is None


def test_invalid_status_emitter_failure_is_counted_without_baseline():
    gate = StatusEmissionGate()

    with pytest.raises(RuntimeError):
        gate.emit(None, lambda _status: (_ for _ in ()).throw(RuntimeError()))

    snapshot = gate.snapshot()
    assert snapshot.failed_emission_count == 1
    assert snapshot.fail_open_count == 0
    assert snapshot.last_status is None


def test_same_status_reentrant_signal_is_suppressed():
    gate = StatusEmissionGate()
    emitted: list[str] = []

    def emitter(status: str) -> None:
        emitted.append(status)
        gate.emit(status, emitter)

    gate.emit("tracking", emitter)

    assert emitted == ["tracking"]
    snapshot = gate.snapshot()
    assert snapshot.emitted_count == 1
    assert snapshot.suppressed_count == 1
    assert snapshot.last_status == "tracking"


def test_reentrant_real_transition_becomes_the_final_baseline():
    gate = StatusEmissionGate()
    emitted: list[str] = []

    def emitter(status: str) -> None:
        emitted.append(status)
        if status == "initializing":
            gate.emit("tracking", emitter)

    gate.emit("initializing", emitter)

    assert emitted == ["initializing", "tracking"]
    snapshot = gate.snapshot()
    assert snapshot.emitted_count == 2
    assert snapshot.last_status == "tracking"


def test_concurrent_duplicate_requests_emit_only_once():
    gate = StatusEmissionGate()
    emitted: list[str] = []
    emitted_lock = threading.Lock()
    start = threading.Barrier(12)

    def emitter(status: str) -> None:
        with emitted_lock:
            emitted.append(status)

    def worker() -> None:
        start.wait(timeout=2.0)
        gate.emit("tracking", emitter)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3.0)

    assert not any(thread.is_alive() for thread in threads)
    assert emitted == ["tracking"]
    snapshot = gate.snapshot()
    assert snapshot.emitted_count == 1
    assert snapshot.suppressed_count == 11


def test_invalid_emitter_and_force_types_fail_before_state_change():
    gate = StatusEmissionGate()

    with pytest.raises(TypeError, match="emitter"):
        gate.emit("tracking", object())
    with pytest.raises(TypeError, match="force"):
        gate.emit("tracking", lambda _status: None, force=1)

    snapshot = gate.snapshot()
    assert snapshot.emitted_count == 0
    assert snapshot.suppressed_count == 0
    assert snapshot.last_status is None
