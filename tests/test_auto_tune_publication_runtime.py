from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from launcher import runtime_mainwindow
from launcher.auto_tune_publication import AutoTunePublicationWriter
from launcher.auto_tune_timeline import AutoTuneSampleTimeline
from launcher.runtime_mainwindow import MainWindow, _TimestampedAutoTuner


@dataclass(frozen=True)
class Settings:
    head_dist_cm: float = 60.0
    smoothing_alpha: float = 0.28
    deadzone_mm: float = 3.0


class Clock:
    def __init__(self, value: float = 1.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class Writer:
    def __init__(self) -> None:
        self.writes: list[object] = []

    def write(self, settings: object) -> None:
        self.writes.append(settings)


def _window(
    *,
    status: str = "tracking",
    auto_tune: bool = True,
) -> MainWindow:
    window = MainWindow.__new__(MainWindow)
    window._tracking_status = status
    window._auto_tune_enabled = auto_tune
    window._auto_tuner = MagicMock()
    window._auto_tune_sample_timeline = AutoTuneSampleTimeline()
    window._last_auto_tune_write_s = 123.0
    window._auto_tune_status = MagicMock()
    window._auto_tune_publication_writer = None
    return window


def _install_proxy(window: MainWindow, *, clock: Clock):
    delegate = Writer()
    proxy = AutoTunePublicationWriter(delegate, clock=clock)
    window._settings_writer = proxy
    window._auto_tune_publication_writer = proxy
    return delegate, proxy


def test_install_wraps_existing_writer_and_seeds_current_settings():
    window = _window()
    delegate = Writer()
    current = Settings()
    window._settings_writer = delegate
    window._settings = current

    assert window._install_auto_tune_publication_writer()

    proxy = window._settings_writer
    assert isinstance(proxy, AutoTunePublicationWriter)
    assert proxy.delegate is delegate
    assert window._auto_tune_publication_writer is proxy
    snapshot = proxy.publication_snapshot()
    assert snapshot.external_seed_count == 1
    assert snapshot.last_values is not None
    assert snapshot.last_values.head_dist_cm == pytest.approx(60.0)


def test_install_is_idempotent_and_does_not_nest_writer():
    window = _window()
    delegate = Writer()
    proxy = AutoTunePublicationWriter(delegate)
    window._settings_writer = proxy

    assert window._install_auto_tune_publication_writer()
    assert window._settings_writer is proxy
    assert window._auto_tune_publication_writer is proxy
    assert proxy.delegate is delegate


def test_install_fails_safely_without_compatible_writer():
    window = _window()
    window._settings_writer = object()

    assert not window._install_auto_tune_publication_writer()
    assert window._auto_tune_publication_writer is None


def test_legacy_position_path_gates_only_auto_tune_write(monkeypatch):
    window = _window(status="tracking", auto_tune=True)
    clock = Clock(1.0)
    delegate, proxy = _install_proxy(window, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, head_dist_cm=60.1)

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        lambda owner, *_args: owner._settings_writer.write(candidate),
    )

    clock.value = 1.25
    window._on_position(0.0, 0.0, 60.0)

    assert delegate.writes == [baseline]
    assert proxy.publication_snapshot().suppressed_count == 1


def test_disabled_auto_tune_position_write_is_manual_pass_through(monkeypatch):
    window = _window(status="tracking", auto_tune=False)
    clock = Clock(1.0)
    delegate, proxy = _install_proxy(window, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, head_dist_cm=60.1)

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        lambda owner, *_args: owner._settings_writer.write(candidate),
    )

    clock.value = 1.25
    window._on_position(0.0, 0.0, 60.0)

    assert delegate.writes == [baseline, candidate]
    assert proxy.publication_snapshot().external_seed_count == 2


def test_nontracking_position_write_is_not_marked_auto_tune(monkeypatch):
    window = _window(status="hold", auto_tune=True)
    clock = Clock(1.0)
    delegate, proxy = _install_proxy(window, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, head_dist_cm=60.1)

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        lambda owner, *_args: owner._settings_writer.write(candidate),
    )

    window._on_position(0.0, 0.0, 60.0)

    assert delegate.writes == [baseline, candidate]


def test_timestamped_position_uses_producer_time_and_gates_publication(monkeypatch):
    window = _window(status="tracking", auto_tune=True)
    delegate_tuner = MagicMock()
    window._auto_tuner = _TimestampedAutoTuner(delegate_tuner)
    clock = Clock(1.0)
    delegate, proxy = _install_proxy(window, clock=clock)
    baseline = Settings()
    proxy.write(baseline)
    candidate = replace(baseline, head_dist_cm=60.1)

    def base_position(owner, x, y, z):
        owner._auto_tuner.update(x, y, z, 999.0)
        owner._settings_writer.write(candidate)

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        base_position,
    )

    clock.value = 1.25
    window._on_timestamped_position(1.0, 2.0, 60.0, 1000)

    delegate_tuner.update.assert_called_once_with(1.0, 2.0, 60.0, 1.0)
    assert delegate.writes == [baseline]
    assert proxy.publication_snapshot().suppressed_count == 1


def test_timestamped_position_disarms_both_contexts_after_base_failure(monkeypatch):
    window = _window(status="tracking", auto_tune=True)
    tuner_delegate = MagicMock()
    tuner = _TimestampedAutoTuner(tuner_delegate)
    window._auto_tuner = tuner
    clock = Clock(1.0)
    delegate, proxy = _install_proxy(window, clock=clock)

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_position",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("ui failed")),
    )

    with pytest.raises(RuntimeError, match="ui failed"):
        window._on_timestamped_position(1.0, 2.0, 60.0, 1000)

    # Both adapters must have returned to their normal fallback/manual state.
    tuner.update(1.0, 2.0, 60.0, 22.0)
    tuner_delegate.update.assert_called_once_with(1.0, 2.0, 60.0, 22.0)
    manual = Settings()
    proxy.write(manual)
    assert delegate.writes == [manual]


def test_tracking_boundary_resets_publication_episode():
    window = _window(status="initializing")
    publication = MagicMock()
    window._auto_tune_publication_writer = publication

    assert window._reset_auto_tuner_on_tracking_boundary(
        "initializing",
        "tracking",
    )

    publication.reset_publication.assert_called_once_with()
    window._auto_tuner.reset.assert_called_once_with()
    assert window._last_auto_tune_write_s == 0.0


def test_boundary_resets_publication_even_with_legacy_unresettable_tuner():
    window = _window(status="initializing")
    window._auto_tuner = object()
    publication = MagicMock()
    window._auto_tune_publication_writer = publication

    assert not window._reset_auto_tuner_on_tracking_boundary(
        "initializing",
        "tracking",
    )

    publication.reset_publication.assert_called_once_with()
    assert window._last_auto_tune_write_s == 123.0


def test_toggle_reinstalls_and_resets_publication_writer(monkeypatch):
    window = _window(status="stopped")
    publication = MagicMock()
    window._auto_tune_publication_writer = publication
    window._settings_writer = publication

    monkeypatch.setattr(
        runtime_mainwindow._BaseMainWindow,
        "_on_auto_tune_toggle",
        lambda owner, checked: setattr(owner, "_auto_tune_enabled", checked),
    )
    window._install_timestamped_auto_tuner = MagicMock(return_value=True)
    window._install_auto_tune_publication_writer = MagicMock(return_value=True)

    window._on_auto_tune_toggle(True)

    window._install_timestamped_auto_tuner.assert_called_once_with()
    window._install_auto_tune_publication_writer.assert_called_once_with()
    publication.reset_publication.assert_called_once_with()
    assert window._auto_tune_sample_timeline.snapshot().reset_count == 1


def test_runtime_exposes_publication_snapshot_or_none():
    window = _window()
    assert window.auto_tune_publication_snapshot() is None

    publication = MagicMock()
    expected = object()
    publication.publication_snapshot.return_value = expected
    window._auto_tune_publication_writer = publication

    assert window.auto_tune_publication_snapshot() is expected


def test_runtime_source_keeps_base_250ms_throttle_and_gates_only_writer():
    base = Path("launcher/mainwindow.py").read_text(encoding="utf-8")
    runtime = Path("launcher/runtime_mainwindow.py").read_text(
        encoding="utf-8"
    )

    base_position = base.split("    def _on_position(", 1)[1].split(
        "    def _on_frame(",
        1,
    )[0]
    dispatch = runtime.split(
        "    def _dispatch_position_with_publication_gate(",
        1,
    )[1].split("    def _on_position(", 1)[0]

    assert "now_s - self._last_auto_tune_write_s < 0.25" in base_position
    assert "self._settings_writer.write(self._settings)" in base_position
    assert "with self._auto_tune_publication_context():" in dispatch
    assert "super()._on_position" in dispatch


def test_frozen_package_includes_publication_module():
    spec = Path("Glassless3D.spec").read_text(encoding="utf-8")

    assert '"launcher.auto_tune_publication"' in spec
