# tracker/debug_monitor.py
"""Live read-only debug monitor for Glassless3D parallax state.

Pure functions (compute_shift_pct, _shift_tag, _is_stale) are importable
without triggering PySide6; the Qt window is created only when main() runs.
"""
from __future__ import annotations


def compute_shift_pct(
    head_x: float,
    head_y: float,
    head_z: float,
    strength_x: float,
    strength_y: float,
    virtual_depth_cm: float,
    screen_w_cm: float,
    screen_h_cm: float,
) -> tuple[float, float]:
    """Return (shift_x_pct, shift_y_pct) matching the overlay HLSL parallax formula.

    Formula: f = oz / (hz + oz);  shift_pct = abs(head / screen) * f * strength * 100
    """
    denom = head_z + virtual_depth_cm
    f = virtual_depth_cm / max(denom, 0.001)
    shift_x = abs(head_x / max(screen_w_cm, 0.001)) * f * strength_x * 100.0
    shift_y = abs(head_y / max(screen_h_cm, 0.001)) * f * strength_y * 100.0
    return shift_x, shift_y


def _shift_tag(shift_x_pct: float, shift_y_pct: float) -> str:
    """Return 'GOOD' / 'HIGH' / 'DANGER' based on worst-axis shift."""
    worst = max(shift_x_pct, shift_y_pct)
    if worst < 2.0:
        return "GOOD"
    if worst < 4.0:
        return "HIGH"
    return "DANGER"


def _is_stale(timestamp_ms: int, now_ms: int, threshold_ms: int = 500) -> bool:
    """Return True if timestamp_ms is older than threshold_ms. Handles 32-bit wrap."""
    age = (now_ms - timestamp_ms) & 0xFFFF_FFFF
    return age > threshold_ms


from collections import deque

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from tracker.pose import monotonic_ms
from tracker.shared_memory import SharedMemoryReader
from tracker.shared_settings import OverlaySettings, SharedSettingsReader
from tracker.evaluation import PoseSample, classify_tracking_quality, compute_tracking_metrics

_DEFAULT_SETTINGS = OverlaySettings(
    strength_x=1.0,
    strength_y=1.0,
    virtual_depth_cm=30.0,
    screen_w_cm=119.3,
    screen_h_cm=33.6,
)

_DEPTH_CURVE_NAMES: dict[int, str] = {0: "linear", 1: "sqrt", 2: "gamma"}


class MonitorWidget(QWidget):
    """Read-only live display of G3D head pose + calculated parallax shift."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Glassless3D Debug Monitor")
        self.setMinimumWidth(500)

        self._pose_reader = SharedMemoryReader("G3D")
        self._settings_reader = SharedSettingsReader("G3D_Settings")
        self._quality_samples: deque[PoseSample] = deque(maxlen=300)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(16)  # ~60 Hz

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # Status row
        row = QHBoxLayout()
        self._status_lbl = QLabel("● NO TRACKER")
        self._status_lbl.setStyleSheet("color:#e74c3c; font-weight:bold;")
        self._age_lbl = QLabel("")
        self._age_lbl.setStyleSheet("color:#888;")
        row.addWidget(self._status_lbl)
        row.addWidget(self._age_lbl)
        row.addStretch()
        root.addLayout(row)

        # Head Pose panel
        pose_box = QGroupBox("Raw Head Pose (from G3D shared memory)")
        grid = QGridLayout(pose_box)
        mono_large = QFont("Courier New", 22)
        for col, name in enumerate(["headX", "headY", "headZ"]):
            lbl = QLabel(name)
            lbl.setStyleSheet("color:#888; font-size:11px;")
            grid.addWidget(lbl, 0, col)
        self._hx = QLabel("—")
        self._hy = QLabel("—")
        self._hz = QLabel("—")
        for col, lbl in enumerate([self._hx, self._hy, self._hz]):
            lbl.setFont(mono_large)
            grid.addWidget(lbl, 1, col)
        self._hz_warn = QLabel("")
        self._hz_warn.setStyleSheet("color:#e74c3c; font-size:10px;")
        grid.addWidget(self._hz_warn, 2, 2)
        root.addWidget(pose_box)

        # Parallax panel
        par_box = QGroupBox("Calculated Parallax (what the shader sees)")
        pgrid = QGridLayout(par_box)
        mono_med = QFont("Courier New", 14)
        for col, name in enumerate(["virtualDepth", "depth f", "shiftX %", "shiftY %"]):
            lbl = QLabel(name)
            lbl.setStyleSheet("color:#888; font-size:11px;")
            pgrid.addWidget(lbl, 0, col)
        self._vd = QLabel("—")
        self._f = QLabel("—")
        self._sx = QLabel("—")
        self._sy = QLabel("—")
        for col, lbl in enumerate([self._vd, self._f, self._sx, self._sy]):
            lbl.setFont(mono_med)
            pgrid.addWidget(lbl, 1, col)
        root.addWidget(par_box)

        quality_box = QGroupBox("Tracking Quality (rolling window)")
        qgrid = QGridLayout(quality_box)
        self._quality_vals: dict[str, QLabel] = {}
        for col, name in enumerate(["quality", "loss", "jitter", "reacq"]):
            key = QLabel(f"{name}:")
            key.setStyleSheet("color:#888; font-size:11px;")
            val = QLabel("—")
            val.setStyleSheet("font-size:11px;")
            qgrid.addWidget(key, 0, col)
            qgrid.addWidget(val, 1, col)
            self._quality_vals[name] = val
        root.addWidget(quality_box)

        # Settings panel
        set_box = QGroupBox("Settings (from G3D_Settings — or defaults if absent)")
        sgrid = QGridLayout(set_box)
        sgrid.setVerticalSpacing(2)
        self._set_vals: dict[str, QLabel] = {}
        fields = ["strengthX", "strengthY", "screenW cm", "screenH cm",
                  "cameraFOV°", "ipd mm", "depthCurve"]
        for i, name in enumerate(fields):
            r, c = divmod(i, 4)
            k = QLabel(f"{name}:")
            k.setStyleSheet("color:#888; font-size:11px;")
            v = QLabel("—")
            v.setStyleSheet("font-size:11px;")
            sgrid.addWidget(k, r * 2, c)
            sgrid.addWidget(v, r * 2 + 1, c)
            self._set_vals[name] = v
        root.addWidget(set_box)

    def _poll(self) -> None:
        # Shared-memory freshness must use the same Windows uptime epoch as the
        # tracker/native overlay, not a generic process-local monotonic clock.
        now_ms = monotonic_ms()
        pose = self._pose_reader.read()
        settings = self._settings_reader.read() or _DEFAULT_SETTINGS

        if pose is None:
            self._quality_samples.append(PoseSample(timestamp_ms=now_ms, valid=False))
            self._status_lbl.setText("● NO TRACKER")
            self._status_lbl.setStyleSheet("color:#e74c3c; font-weight:bold;")
            self._age_lbl.setText("")
        else:
            x, y, z, ts = pose
            stale = _is_stale(ts, now_ms)
            self._quality_samples.append(
                PoseSample(timestamp_ms=now_ms, x_cm=x, y_cm=y, z_cm=z, valid=not stale)
            )
            if stale:
                self._status_lbl.setText("● STALE")
                self._status_lbl.setStyleSheet("color:#f39c12; font-weight:bold;")
            else:
                self._status_lbl.setText("● TRACKING")
                self._status_lbl.setStyleSheet("color:#2ecc71; font-weight:bold;")
            age = (now_ms - ts) & 0xFFFF_FFFF
            self._age_lbl.setText(f"{age} ms")

            self._hx.setText(f"{x:+.1f} cm")
            self._hy.setText(f"{y:+.1f} cm")
            self._hz.setText(f"{z:.1f} cm")
            self._hx.setStyleSheet("color:#e67e22;" if abs(x) > 15 else "color:#ecf0f1;")
            self._hy.setStyleSheet("color:#e67e22;" if abs(y) > 15 else "color:#ecf0f1;")
            if z < 40.0:
                self._hz.setStyleSheet("color:#e74c3c;")
                self._hz_warn.setText("⚠ expected 50–80 cm")
            else:
                self._hz.setStyleSheet("color:#ecf0f1;")
                self._hz_warn.setText("")

            sw = settings.screen_w_cm if settings.screen_w_cm > 0 else 119.3
            sh = settings.screen_h_cm if settings.screen_h_cm > 0 else 33.6
            sx_pct, sy_pct = compute_shift_pct(
                x, y, z,
                settings.strength_x, settings.strength_y,
                settings.virtual_depth_cm, sw, sh,
            )
            f_val = settings.virtual_depth_cm / max(z + settings.virtual_depth_cm, 0.001)
            tag = _shift_tag(sx_pct, sy_pct)
            color = {"GOOD": "#2ecc71", "HIGH": "#f39c12", "DANGER": "#e74c3c"}[tag]
            self._vd.setText(f"{settings.virtual_depth_cm:.1f} cm")
            self._f.setText(f"{f_val:.3f}")
            self._sx.setText(f"{sx_pct:.2f}%")
            self._sy.setText(f"{sy_pct:.2f}%")
            self._sx.setStyleSheet(f"color:{color};")
            self._sy.setStyleSheet(f"color:{color};")

        metrics = compute_tracking_metrics(list(self._quality_samples))
        quality = classify_tracking_quality(metrics)
        qcolor = {"GOOD": "#2ecc71", "WARN": "#f39c12", "DANGER": "#e74c3c"}[quality]
        self._quality_vals["quality"].setText(quality)
        self._quality_vals["quality"].setStyleSheet(f"color:{qcolor}; font-weight:bold;")
        self._quality_vals["loss"].setText(f"{metrics.loss_rate * 100.0:.1f}%")
        self._quality_vals["jitter"].setText(f"{metrics.jitter_cm:.2f} cm")
        self._quality_vals["reacq"].setText(f"{metrics.max_reacquisition_ms} ms")

        sw = settings.screen_w_cm if settings.screen_w_cm > 0 else 119.3
        sh = settings.screen_h_cm if settings.screen_h_cm > 0 else 33.6
        self._set_vals["strengthX"].setText(f"{settings.strength_x:.2f}")
        self._set_vals["strengthY"].setText(f"{settings.strength_y:.2f}")
        self._set_vals["screenW cm"].setText(f"{sw:.1f}")
        self._set_vals["screenH cm"].setText(f"{sh:.1f}")
        self._set_vals["cameraFOV°"].setText(f"{settings.camera_fov_deg:.1f}")
        self._set_vals["ipd mm"].setText(f"{settings.ipd_mm:.1f}")
        self._set_vals["depthCurve"].setText(
            _DEPTH_CURVE_NAMES.get(settings.depth_curve, str(settings.depth_curve))
        )

    def closeEvent(self, event: object) -> None:
        self._timer.stop()
        self._pose_reader.close()
        self._settings_reader.close()
        super().closeEvent(event)  # type: ignore[arg-type]


def main() -> None:
    app = QApplication.instance() or QApplication([])
    w = MonitorWidget()
    w.show()
    app.exec()


if __name__ == "__main__":
    main()
