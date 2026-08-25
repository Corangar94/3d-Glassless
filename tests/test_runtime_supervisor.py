import pytest

from launcher.runtime_supervisor import RecoveryPolicy, RuntimeRecoveryController


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _controller(clock: FakeClock) -> RuntimeRecoveryController:
    return RuntimeRecoveryController(
        RecoveryPolicy(
            immediate_retries=1,
            base_delay_s=1.0,
            max_delay_s=8.0,
            max_failures=5,
            failure_window_s=30.0,
            cooldown_s=20.0,
            stable_reset_s=10.0,
        ),
        clock=clock,
    )


def test_first_failure_is_immediate_then_backoff_doubles():
    clock = FakeClock()
    controller = _controller(clock)

    first = controller.record_failure("overlay", "process exited")
    second = controller.record_failure("overlay", "process exited")
    third = controller.record_failure("overlay", "capture lost")
    fourth = controller.record_failure("overlay", "capture lost")

    assert first.allowed and first.delay_s == 0.0
    assert second.allowed and second.delay_s == pytest.approx(1.0)
    assert third.allowed and third.delay_s == pytest.approx(2.0)
    assert fourth.allowed and fourth.delay_s == pytest.approx(4.0)


def test_repeated_failures_open_circuit_and_report_retry_time():
    clock = FakeClock(100.0)
    controller = _controller(clock)

    decisions = [
        controller.record_failure("tracker", f"failure {index}")
        for index in range(5)
    ]

    blocked = decisions[-1]
    assert not blocked.allowed
    assert blocked.circuit_open
    assert blocked.failure_count == 5
    assert blocked.delay_s == pytest.approx(20.0)
    snapshot = controller.snapshot("tracker")
    assert snapshot.circuit_open
    assert snapshot.retry_after_s == pytest.approx(20.0)


def test_open_circuit_rejects_extra_failures_without_growing_history():
    clock = FakeClock()
    controller = _controller(clock)
    for _ in range(5):
        controller.record_failure("overlay", "crash")

    extra = controller.record_failure("overlay", "another crash")

    assert not extra.allowed
    assert extra.failure_count == 5
    assert controller.snapshot("overlay").failure_count == 5


def test_cooldown_expiry_allows_clean_immediate_retry():
    clock = FakeClock()
    controller = _controller(clock)
    for _ in range(5):
        controller.record_failure("tracker", "stale")
    clock.advance(20.1)

    decision = controller.record_failure("tracker", "retry after cooldown")

    assert decision.allowed
    assert decision.delay_s == 0.0
    assert decision.failure_count == 1


def test_failure_after_rolling_window_starts_new_immediate_episode():
    clock = FakeClock()
    controller = _controller(clock)
    controller.record_failure("overlay", "first")
    controller.record_failure("overlay", "second")
    clock.advance(30.1)

    decision = controller.record_failure("overlay", "new sparse failure")

    assert decision.allowed
    assert decision.delay_s == 0.0
    assert decision.failure_count == 1
    assert controller.snapshot("overlay").consecutive_failures == 1


def test_stable_health_resets_backoff_only_after_required_interval():
    clock = FakeClock()
    controller = _controller(clock)
    controller.record_failure("overlay", "lost")
    controller.record_failure("overlay", "lost")

    assert not controller.mark_healthy("overlay")
    clock.advance(9.9)
    assert not controller.mark_healthy("overlay")
    assert controller.snapshot("overlay").consecutive_failures == 2
    clock.advance(0.2)
    assert controller.mark_healthy("overlay")

    decision = controller.record_failure("overlay", "new episode")
    assert decision.allowed
    assert decision.delay_s == 0.0
    assert decision.failure_count == 1


def test_components_have_independent_failure_budgets():
    clock = FakeClock()
    controller = _controller(clock)
    for _ in range(4):
        controller.record_failure("overlay", "capture")

    tracker = controller.record_failure("tracker", "camera")

    assert tracker.allowed
    assert tracker.delay_s == 0.0
    assert controller.snapshot("overlay").failure_count == 4
    assert controller.snapshot("tracker").failure_count == 1


def test_manual_reset_clears_one_or_all_circuits():
    clock = FakeClock()
    controller = _controller(clock)
    for component in ("tracker", "overlay"):
        for _ in range(5):
            controller.record_failure(component, "failed")

    controller.reset("overlay")
    assert not controller.snapshot("overlay").circuit_open
    assert controller.snapshot("tracker").circuit_open

    controller.reset()
    assert not controller.snapshot("tracker").circuit_open


def test_invalid_policy_values_fail_closed():
    with pytest.raises(ValueError):
        RecoveryPolicy(max_failures=0)
    with pytest.raises(ValueError):
        RecoveryPolicy(base_delay_s=5.0, max_delay_s=1.0)
    with pytest.raises(ValueError):
        RecoveryPolicy(cooldown_s=float("nan"))
