"""Live-tuning GUI for the Glassless3D overlay.

Writes to the G3D_Settings shared-memory segment; the overlay re-reads that
segment each frame, so slider changes take effect instantly.

Run:   python -m launcher.settings_gui
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from tracker.shared_settings import OverlaySettings, SharedSettingsWriter

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return yaml.safe_load(CONFIG_PATH.read_text()) or {}
    except Exception:  # noqa: BLE001  (user-editable file; degrade to empty)
        return {}


def _save_overlay_settings(s: OverlaySettings) -> None:
    cfg = _load_config()
    cfg.setdefault("overlay", {})
    cfg["overlay"].update(
        strength_x=float(s.strength_x),
        strength_y=float(s.strength_y),
        virtual_depth_cm=float(s.virtual_depth_cm),
        screen_w_cm=float(s.screen_w_cm),
        screen_h_cm=float(s.screen_h_cm),
        depth_curve=int(s.depth_curve),
        depth_gamma=float(s.depth_gamma),
        focus_radius=float(s.focus_radius),
    )
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))


class _LabeledSlider(QWidget):
    """Slider + live value label, backed by a float range [lo, hi]."""

    def __init__(
        self,
        lo: float,
        hi: float,
        value: float,
        step: float,
        fmt: str = "{:.2f}",
    ) -> None:
        super().__init__()
        self._lo = lo
        self._hi = hi
        self._step = step
        self._fmt = fmt

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(int(round((hi - lo) / step)))
        self._slider.setValue(self._to_ticks(value))
        self._slider.setTickInterval(max(1, self._slider.maximum() // 10))
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)

        self._label = QLabel(fmt.format(value))
        self._label.setMinimumWidth(60)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._slider)
        lay.addWidget(self._label)

        self._slider.valueChanged.connect(self._on_changed)
        self._listener = None

    def _to_ticks(self, v: float) -> int:
        return int(round((v - self._lo) / self._step))

    def _from_ticks(self, t: int) -> float:
        return self._lo + t * self._step

    def value(self) -> float:
        return self._from_ticks(self._slider.value())

    def on_change(self, cb) -> None:
        self._listener = cb

    def _on_changed(self, _ticks: int) -> None:
        v = self.value()
        self._label.setText(self._fmt.format(v))
        if self._listener is not None:
            self._listener(v)


class SettingsWindow(QWidget):
    """Live overlay tuning window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Glassless3D — Live Tuning")
        self.setMinimumWidth(420)

        cfg = _load_config().get("overlay", {})
        initial = OverlaySettings(
            strength_x=float(cfg.get("strength_x", 1.0)),
            strength_y=float(cfg.get("strength_y", 1.0)),
            virtual_depth_cm=float(cfg.get("virtual_depth_cm", 30.0)),
            screen_w_cm=float(cfg.get("screen_w_cm", 0.0)),
            screen_h_cm=float(cfg.get("screen_h_cm", 0.0)),
            depth_curve=int(cfg.get("depth_curve", 1)),
            depth_gamma=float(cfg.get("depth_gamma", 1.0)),
            focus_radius=float(cfg.get("focus_radius", 0.1)),
        )

        self._writer = SharedSettingsWriter()
        self._current = initial

        # --- UI -----------------------------------------------------------
        root = QVBoxLayout(self)

        hint = QLabel(
            "Changes apply instantly to the running overlay.\n"
            "Strength amplifies parallax. Virtual depth controls 3D 'room' feel.\n"
            "Screen size 0 = let the overlay auto-detect."
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(hint)

        # Group: parallax tuning
        par_grp = QGroupBox("Parallax")
        par_form = QFormLayout(par_grp)

        self._strength = _LabeledSlider(
            lo=0.0, hi=5.0, value=initial.strength_x, step=0.05, fmt="{:.2f}×",
        )
        self._strength.on_change(self._on_any_change)
        par_form.addRow("Strength", self._strength)

        self._depth = _LabeledSlider(
            lo=0.0, hi=200.0, value=initial.virtual_depth_cm, step=1.0, fmt="{:.0f} cm",
        )
        self._depth.on_change(self._on_any_change)
        par_form.addRow("Virtual depth", self._depth)
        root.addWidget(par_grp)

        # Group: screen override
        sz_grp = QGroupBox("Screen size override (0 = autodetect)")
        sz_form = QFormLayout(sz_grp)

        self._sw = QDoubleSpinBox()
        self._sw.setRange(0.0, 500.0)
        self._sw.setDecimals(1)
        self._sw.setSuffix(" cm")
        self._sw.setValue(initial.screen_w_cm)
        self._sw.valueChanged.connect(self._on_any_change)
        sz_form.addRow("Width", self._sw)

        self._sh = QDoubleSpinBox()
        self._sh.setRange(0.0, 500.0)
        self._sh.setDecimals(1)
        self._sh.setSuffix(" cm")
        self._sh.setValue(initial.screen_h_cm)
        self._sh.valueChanged.connect(self._on_any_change)
        sz_form.addRow("Height", self._sh)
        root.addWidget(sz_grp)

        # Preset / utility row
        self._status = QLabel("")
        self._status.setStyleSheet("color: #4a4;")
        root.addWidget(self._status)

        btns = QVBoxLayout()
        save_btn = QPushButton("Save to config.yaml")
        save_btn.clicked.connect(self._on_save)
        btns.addWidget(save_btn)

        reset_btn = QPushButton("Reset to defaults")
        reset_btn.clicked.connect(self._on_reset)
        btns.addWidget(reset_btn)
        root.addLayout(btns)

        # Publish initial snapshot so the overlay sees our values immediately.
        self._publish()

    def _snapshot(self) -> OverlaySettings:
        return OverlaySettings(
            strength_x=self._strength.value(),
            strength_y=self._strength.value(),
            virtual_depth_cm=self._depth.value(),
            screen_w_cm=float(self._sw.value()),
            screen_h_cm=float(self._sh.value()),
        )

    def _publish(self) -> None:
        self._current = self._snapshot()
        self._writer.write(self._current)

    def _on_any_change(self, *_: object) -> None:
        self._publish()
        self._status.setText("live")

    def _on_save(self) -> None:
        _save_overlay_settings(self._snapshot())
        self._status.setText(f"saved to {CONFIG_PATH.name}")

    def _on_reset(self) -> None:
        defaults = OverlaySettings()
        self._strength._slider.setValue(self._strength._to_ticks(defaults.strength_x))
        self._depth._slider.setValue(self._depth._to_ticks(defaults.virtual_depth_cm))
        self._sw.setValue(defaults.screen_w_cm)
        self._sh.setValue(defaults.screen_h_cm)
        # _on_any_change already fired from the slider/spinbox signals.

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._writer.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    win = SettingsWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
