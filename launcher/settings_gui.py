"""Live-tuning GUI for the Glassless3D overlay.

Writes to the G3D_Settings shared-memory segment; the overlay re-reads that
segment each frame, so slider changes take effect instantly.

Run:   python -m launcher.settings_gui
"""
from __future__ import annotations

import sys
import os
import tempfile
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
from tracker.display_backends import backend_code, normalize_backend_id

CONFIG_PATH = Path(os.environ.get("APPDATA", ".")) / "Glassless3D" / "config.yaml"
_STEREO_LAYOUTS = {"full_sbs": 0, "half_sbs": 1}
_EYE_ORDERS = {"left_right": 0, "right_left": 1}
_TRACKING_MODES = {"glassless3d_managed": 0, "vendor_managed": 1}


def _load_config() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        loaded = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  (user-editable file; degrade to empty)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _ensure_mapping_child(data: dict[str, object], key: str) -> dict[str, object]:
    child = data.get(key)
    if isinstance(child, dict):
        return child
    child = {}
    data[key] = child
    return child


def _save_overlay_settings(s: OverlaySettings) -> None:
    cfg = _load_config()
    overlay = _ensure_mapping_child(cfg, "overlay")
    overlay.update(
        strength_x=float(s.strength_x),
        strength_y=float(s.strength_y),
        virtual_depth_cm=float(s.virtual_depth_cm),
        screen_w_cm=float(s.screen_w_cm),
        screen_h_cm=float(s.screen_h_cm),
        depth_curve=int(s.depth_curve),
        depth_gamma=float(s.depth_gamma),
        focus_radius=float(s.focus_radius),
    )
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=CONFIG_PATH.parent,
        ) as handle:
            yaml.safe_dump(cfg, handle, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, CONFIG_PATH)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def _float_value(data: dict[str, object], key: str, default: float) -> float:
    raw = data.get(key, default)
    if not isinstance(raw, (int, float, str)):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _calibration_float(
    overlay: dict[str, object],
    calibration: dict[str, object],
    calibration_key: str,
    overlay_key: str,
    default: float,
) -> float:
    value = _float_value(calibration, calibration_key, 0.0)
    if value > 0.0:
        return value
    return _float_value(overlay, overlay_key, default)


def _enum_value(data: dict[str, object], key: str, choices: dict[str, int], default: int) -> int:
    value = data.get(key)
    if isinstance(value, str):
        return choices.get(value.strip().lower(), default)
    try:
        code = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return code if code in choices.values() else default


def _positive_int(data: dict[str, object], key: str) -> int:
    raw = data.get(key, 0)
    if not isinstance(raw, (int, float, str)):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _overlay_settings_from_config(cfg: dict[str, object]) -> OverlaySettings:
    calibration = cfg.get("display_calibration", {})
    if not isinstance(calibration, dict):
        calibration = {}
    try:
        display_backend = backend_code(normalize_backend_id(cfg.get("display_backend", "desktop_overlay")))
    except ValueError:
        display_backend = 0
    return OverlaySettings(
        strength_x=_float_value(cfg, "strength_x", 1.0),
        strength_y=_float_value(cfg, "strength_y", 1.0),
        virtual_depth_cm=_float_value(cfg, "virtual_depth_cm", 30.0),
        screen_w_cm=_calibration_float(cfg, calibration, "panel_width_cm", "screen_w_cm", 0.0),
        screen_h_cm=_calibration_float(cfg, calibration, "panel_height_cm", "screen_h_cm", 0.0),
        depth_curve=int(_float_value(cfg, "depth_curve", 1.0)),
        depth_gamma=_float_value(cfg, "depth_gamma", 1.0),
        focus_radius=_float_value(cfg, "focus_radius", 0.1),
        head_dist_cm=_calibration_float(cfg, calibration, "viewer_distance_cm", "head_dist_cm", 60.0),
        camera_fov_deg=_float_value(cfg, "camera_fov_deg", 90.0),
        ipd_mm=_calibration_float(cfg, calibration, "ipd_mm", "ipd_mm", 64.0),
        smoothing_alpha=_float_value(cfg, "smoothing_alpha", 0.1),
        deadzone_mm=_float_value(cfg, "deadzone_mm", 5.0),
        display_backend=display_backend,
        stereo_layout=_enum_value(calibration, "stereo_layout", _STEREO_LAYOUTS, 0),
        eye_order=_enum_value(calibration, "eye_order", _EYE_ORDERS, 0),
        panel_width_px=_positive_int(calibration, "panel_width_px"),
        panel_height_px=_positive_int(calibration, "panel_height_px"),
        focus_plane_cm=_float_value(calibration, "focus_plane_cm", 0.0),
        tracking_mode=_enum_value(calibration, "tracking_mode", _TRACKING_MODES, 0),
    )


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
        if not isinstance(cfg, dict):
            cfg = {}
        initial = _overlay_settings_from_config(cfg)

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
            depth_curve=self._current.depth_curve,
            depth_gamma=self._current.depth_gamma,
            focus_radius=self._current.focus_radius,
            head_dist_cm=self._current.head_dist_cm,
            camera_fov_deg=self._current.camera_fov_deg,
            ipd_mm=self._current.ipd_mm,
            smoothing_alpha=self._current.smoothing_alpha,
            deadzone_mm=self._current.deadzone_mm,
            display_backend=self._current.display_backend,
            depth_mode=self._current.depth_mode,
            stereo_layout=self._current.stereo_layout,
            eye_order=self._current.eye_order,
            panel_width_px=self._current.panel_width_px,
            panel_height_px=self._current.panel_height_px,
            focus_plane_cm=self._current.focus_plane_cm,
            tracking_mode=self._current.tracking_mode,
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
    # The main launcher is the sole production G3D_Settings writer. Keep this
    # historical entry point as an alias so the old shortcut cannot create a
    # competing writer or edit a different root config.yaml.
    from launcher.app import main as launcher_main

    launcher_main(sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
