import pytest

from tracker.camera_reconnect_retry import (
    CameraReconnectBudget,
    CameraReconnectPolicy,
)


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _budget(clock: FakeClock) -> CameraReconnectBudget:
    return CameraReconnectBudget(
        CameraReconnectPolicy(
            immediate_retries=1,
            max_failures=6,
            base_delay_s=0.5,
            max_delay_s=4.0,
            max_outage_s=20.0,
            heartbeat_s=1.0,
        ),
        clock=clock,
    )


def test_first_failure_retries_immediately_then_backoff_doubles():
    clock = FakeClock()
    budget = _budget(clock)

    first = budget.record_failure("camera stalled")
    second = budget.record_failure("all backends unavailable")
    third = budget.record_failure("all backends unavailable")
    fourth = budget.record_failure("all backends unavailable")

    assert first.allowed and first.delay_s == 0.0
    assert second.allowed and second.delay_s == pytest.approx(0.5)
    assert third.allowed and third.delay_s == pytest.approx(1.0)
    assert fourth.allowed and fourth.delay_s == pytest.approx(2.0)


def test_delay_is_capped():
    clock = FakeClock()
    budget = CameraReconnectBudget(
        CameraReconnectPolicy(
            immediate_retries=0,
            max_failures=10,
            base_delay_s=2.0,
            max_delay_s=3.0,
            max_outage_s=60.0,
        ),
        clock=clock,
    )

    assert budget.record_failure("one").delay_s == pytest.approx(2.0)
    assert budget.record_failure("two").delay_s == pytest.approx(3.0)
    assert budget.record_failure("three").delay_s == pytest.approx(3.0)


def test_failure_count_exhausts_local_recovery_budget():
    clock = FakeClock()
    budget = _budget(clock)

    decisions = [budget.record_failure(str(index)) for index in range(6)]

    assert not decisions[-1].allowed
    assert decisions[-1].failure_count == 6
    assert decisions[-1].delay_s == 0.0
    assert budget.failure_count == 6


def test_outage_duration_exhausts_budget_before_failure_count():
    clock = FakeClock(100.0)
    budget = CameraReconnectBudget(
        CameraReconnectPolicy(
            immediate_retries=1,
            max_failures=20,
            base_delay_s=1.0,
            max_delay_s=8.0,
            max_outage_s=5.0,
        ),
        clock=clock,
    )
    assert budget.record_failure("unplugged").allowed
    clock.advance(5.0)

    decision = budget.record_failure("still unplugged")

    assert not decision.allowed
    assert decision.outage_elapsed_s == pytest.approx(5.0)


def test_delay_never_sleeps_beyond_outage_deadline():
    clock = FakeClock(10.0)
    budget = CameraReconnectBudget(
        CameraReconnectPolicy(
            immediate_retries=0,
            max_failures=20,
            base_delay_s=8.0,
            max_delay_s=8.0,
            max_outage_s=10.0,
        ),
        clock=clock,
    )
    first = budget.record_failure("unplugged")
    assert first.delay_s == pytest.approx(8.0)
    clock.advance(8.0)

    second = budget.record_failure("still unplugged")

    assert second.allowed
    assert second.delay_s == pytest.approx(2.0)


def test_snapshot_and_success_reset_start_a_new_outage():
    clock = FakeClock(50.0)
    budget = _budget(clock)
    budget.record_failure("lost")
    clock.advance(3.0)

    snapshot = budget.snapshot()

    assert snapshot.failure_count == 1
    assert snapshot.outage_elapsed_s == pytest.approx(3.0)
    assert snapshot.last_reason == "lost"

    budget.reset()
    reset = budget.snapshot()
    assert reset.failure_count == 0
    assert reset.outage_elapsed_s == 0.0
    assert reset.last_reason == ""
    assert budget.record_failure("new outage").delay_s == 0.0


def test_invalid_policy_values_fail_closed():
    with pytest.raises(ValueError):
        CameraReconnectPolicy(max_failures=0)
    with pytest.raises(ValueError):
        CameraReconnectPolicy(base_delay_s=2.0, max_delay_s=1.0)
    with pytest.raises(ValueError):
        CameraReconnectPolicy(max_outage_s=float("nan"))
    with pytest.raises(ValueError):
        CameraReconnectPolicy(heartbeat_s=0.0)
