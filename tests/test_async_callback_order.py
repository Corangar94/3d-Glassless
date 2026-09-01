from __future__ import annotations

import pytest

from tracker.async_callback_order import LatestCallbackPublicationGate


def test_first_and_strictly_newer_callbacks_publish():
    gate = LatestCallbackPublicationGate()

    assert gate.begin_processing(1000)
    assert gate.accept_publication(1000)
    assert gate.begin_processing(1033)
    assert gate.accept_publication(1033)

    snapshot = gate.snapshot()
    assert snapshot.latest_published_timestamp_ms == 1033
    assert snapshot.accepted_publication_count == 2
    assert snapshot.total_drop_count == 0


def test_already_obsolete_callback_is_rejected_before_conversion():
    gate = LatestCallbackPublicationGate()
    gate.accept_publication(1033)

    assert not gate.begin_processing(1000)

    snapshot = gate.snapshot()
    assert snapshot.out_of_order_drop_count == 1
    assert snapshot.pre_conversion_drop_count == 1
    assert snapshot.post_conversion_drop_count == 0
    assert snapshot.last_dropped_timestamp_ms == 1000


def test_callback_that_loses_conversion_race_is_rejected_after_conversion():
    gate = LatestCallbackPublicationGate()

    assert gate.begin_processing(1000)
    assert gate.begin_processing(1033)
    assert gate.accept_publication(1033)
    assert not gate.accept_publication(1000)

    snapshot = gate.snapshot()
    assert snapshot.latest_published_timestamp_ms == 1033
    assert snapshot.out_of_order_drop_count == 1
    assert snapshot.pre_conversion_drop_count == 0
    assert snapshot.post_conversion_drop_count == 1


def test_duplicate_callback_is_counted_separately():
    gate = LatestCallbackPublicationGate()
    gate.accept_publication(1000)

    assert not gate.begin_processing(1000)

    snapshot = gate.snapshot()
    assert snapshot.duplicate_drop_count == 1
    assert snapshot.out_of_order_drop_count == 0
    assert snapshot.total_drop_count == 1


def test_failed_conversion_does_not_claim_publication_timestamp():
    gate = LatestCallbackPublicationGate()

    assert gate.begin_processing(1000)
    # The owner abandons the callback because conversion failed.
    assert gate.begin_processing(1000)
    assert gate.accept_publication(1000)

    snapshot = gate.snapshot()
    assert snapshot.latest_published_timestamp_ms == 1000
    assert snapshot.accepted_publication_count == 1
    assert snapshot.total_drop_count == 0


def test_negative_callback_timestamp_fails_closed():
    gate = LatestCallbackPublicationGate()

    with pytest.raises(ValueError):
        gate.begin_processing(-1)
    with pytest.raises(ValueError):
        gate.accept_publication(-1)


def test_recording_a_drop_for_newer_data_is_forbidden():
    gate = LatestCallbackPublicationGate()

    with pytest.raises(ValueError):
        gate._record_drop(1000, after_conversion=False)
