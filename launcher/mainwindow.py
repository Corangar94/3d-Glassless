"""Always-on-top two-mode tracker window."""
from __future__ import annotations

import threading
from typing import Optional

import dataclasses
import logging
import yaml
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap

_log = logging.getLogger(__name__)
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from tracker.shared_settings import OverlaySettings, SharedSettingsWriter
from launcher.presets import list_presets, save_preset, load_preset, delete_preset
from launcher.calibration import detect_screen_cm, measure_head_distance

from launcher.overlay_process import OverlayProcess, OverlayStartError
from launcher.tracker_thread import TrackerThread

# Window dimensions
_EXPANDED_W, _EXPANDED_H = 430, 440
_COMPACT_W,  _COMPACT_H  = 430, 100

_STATUS_TEXT = {
    "tracking":     "● TRACKING",
    "hold":         "● HOLD",
    "paused":       "● PAUSED",
    "stopped":      "● STOPPED",
    "initializing": "⟳ INITIALIZING",
    "error":        "✕ ERROR",
}
_STATUS_COLOR = {
    "tracking":     "#28c840",
    "hold":         "#febc2e",
    "paused":       "#888888",
    "stopped":      "#888888",
    "initializing": "#3ecfcf",
    "error":        "#e84040",
}
_DARK_BG = "#0d0d22"
_TITLE_BG = "#1a1a2e"


def _preload_face_tracker() -> None:
    """Import tracker.face_tracker (and mediapipe) in the background on startup.

    This warms the import cache so the first Start Tracking click is instant
    instead of waiting 30+ seconds for mediapipe to initialise.
    """
    try:
        import tracker.face_tracker  # noqa: F401, PLC0415
    except Exception:
        pass  # will fail again at start-tracking time with a proper error message


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: dict,
        config_path: str,
        parent: Optional[object] = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[call-overload]
        self._config = config
        self._config_path = config_path
        self._compact: bool = config.get("gui", {}).get("compact_mode", False)
        self._thread: Optional[TrackerThread] = None
        self._overlay = OverlayProcess()
        self._drag_pos: Optional[QPoint] = None

        self._settings_writer = SharedSettingsWriter()
        trk = config.get("tracking", {})
        self._camera_tilt_deg: float = float(trk.get("camera_tilt_deg", 0.0))
        ov = config.get("overlay", {})
        self._settings = OverlaySettings(
            strength_x=float(ov.get("strength_x", 1.0)),
            strength_y=float(ov.get("strength_y", 1.0)),
            virtual_depth_cm=float(ov.get("virtual_depth_cm", 30.0)),
            screen_w_cm=float(ov.get("screen_w_cm", 0.0)),
            screen_h_cm=float(ov.get("screen_h_cm", 0.0)),
            depth_curve=int(ov.get("depth_curve", 1)),
            depth_gamma=float(ov.get("depth_gamma", 1.0)),
            focus_radius=float(ov.get("focus_radius", 0.1)),
            head_dist_cm=float(ov.get("head_dist_cm", 60.0)),
            camera_fov_deg=float(ov.get("camera_fov_deg", 90.0)),
            ipd_mm=float(ov.get("ipd_mm", 64.0)),
            smoothing_alpha=float(ov.get("smoothing_alpha", 0.1)),
            deadzone_mm=float(ov.get("deadzone_mm", 5.0)),
        )
        self._settings_writer.write(self._settings)

        # Pre-warm mediapipe in the background so the first Start Tracking is instant.
        threading.Thread(target=_preload_face_tracker, daemon=True).start()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._build_ui()
        self._apply_mode()
        self._on_status("stopped")

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet(f"background:{_DARK_BG};border-radius:8px;")
        self.setCentralWidget(root)
        self._root_layout = QVBoxLayout(root)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._root_layout.addWidget(self._make_titlebar())

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            "QTabWidget::pane{border:none;background:#0d0d22;}"
            "QTabBar::tab{background:#1a1a2e;color:#888;padding:5px 14px;}"
            "QTabBar::tab:selected{background:#0d0d22;color:#c8c8e8;}"
        )

        tracker_tab = QWidget()
        tl = QVBoxLayout(tracker_tab)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        self._camera_label = QLabel()
        self._camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_label.setStyleSheet("background:#0a0a0a;")
        tl.addWidget(self._camera_label)
        tl.addLayout(self._make_xyz_row())
        tl.addWidget(self._make_action_button())
        self._tabs.addTab(tracker_tab, "Tracker")
        self._tabs.addTab(self._make_advanced_tab(), "Advanced")
        self._root_layout.addWidget(self._tabs)

    def _make_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(f"background:{_TITLE_BG};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)

        logo = QLabel("● GLASSLESS 3D")
        logo.setStyleSheet("color:#c8c8e8;font-size:11px;font-weight:bold;")
        self._status_label = QLabel("● STOPPED")
        self._status_label.setStyleSheet("color:#888;font-size:10px;")
        self._toggle_btn = QPushButton("▲")
        self._toggle_btn.setFixedSize(24, 18)
        self._toggle_btn.setStyleSheet(
            "background:transparent;color:#555;font-size:10px;border:none;"
        )
        self._toggle_btn.clicked.connect(self._toggle_mode)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 18)
        close_btn.setStyleSheet(
            "QPushButton{background:transparent;color:#555;font-size:11px;border:none;}"
            "QPushButton:hover{color:#e84040;}"
        )
        close_btn.clicked.connect(self.close)

        layout.addWidget(logo)
        layout.addStretch()
        layout.addWidget(self._status_label)
        layout.addWidget(self._toggle_btn)
        layout.addWidget(close_btn)
        return bar

    def _make_xyz_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        self._label_x = self._xyz_tile("X")
        self._label_y = self._xyz_tile("Y")
        self._label_z = self._xyz_tile("Z")
        for lbl in (self._label_x, self._label_y, self._label_z):
            row.addWidget(lbl)
        return row

    def _xyz_tile(self, axis: str) -> QLabel:
        tile = QLabel(f"{axis}\n0.0")
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            "background:#111128;color:#3ecfcf;font-family:monospace;"
            "font-size:13px;font-weight:bold;border-radius:3px;padding:4px;"
        )
        return tile

    def _make_action_button(self) -> QPushButton:
        self._action_btn = QPushButton("▶ START TRACKING")
        self._action_btn.setStyleSheet(
            "background:#28c840;color:#111;font-weight:bold;"
            "font-size:11px;padding:8px;border:none;"
        )
        self._action_btn.clicked.connect(self._toggle_tracking)
        return self._action_btn

    # ── Mode switching ─────────────────────────────────────────────────────────

    def _toggle_mode(self) -> None:
        self._compact = not self._compact
        self._apply_mode()
        self._save_compact_pref()

    def _apply_mode(self) -> None:
        if self._compact:
            self.setFixedSize(_COMPACT_W, _COMPACT_H)
            self._camera_label.setFixedHeight(72)
            self._toggle_btn.setText("▼")
        else:
            self.setFixedSize(_EXPANDED_W, _EXPANDED_H)
            self._camera_label.setFixedHeight(150)
            self._toggle_btn.setText("▲")

    def _save_compact_pref(self) -> None:
        try:
            try:
                with open(self._config_path) as f:
                    cfg = yaml.safe_load(f) or {}
            except FileNotFoundError:
                cfg = {}
            cfg.setdefault("gui", {})["compact_mode"] = self._compact
            with open(self._config_path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
        except OSError:
            pass

    # ── Tracking control ───────────────────────────────────────────────────────

    def _toggle_tracking(self) -> None:
        if self._thread and self._thread.isRunning():
            self._stop_tracking()
        else:
            self._start_tracking()

    def _start_tracking(self) -> None:
        self._on_status("initializing")
        self._action_btn.setText("■ STOP TRACKING")
        self._action_btn.setStyleSheet(
            "background:#e84040;color:#fff;font-weight:bold;"
            "font-size:11px;padding:8px;border:none;"
        )

        cam_idx = self._config["camera"]["index"]
        thread = TrackerThread(
            camera_index=cam_idx,
            config=self._config,
            config_path=self._config_path,
        )
        thread.position_updated.connect(self._on_position)
        thread.frame_ready.connect(self._on_frame)
        thread.status_changed.connect(self._on_status)
        thread.start()
        self._thread = thread

        # Launch the overlay process alongside the tracker. A missing binary
        # is surfaced in overlay.log; the tracker keeps running so shared-memory
        # consumers still get head pose.
        try:
            self._overlay.start()
        except OverlayStartError as e:
            _log.warning("overlay launch failed: %s", e)

    def _stop_tracking(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None
        self._overlay.stop()
        self._on_status("stopped")
        self._action_btn.setText("▶ START TRACKING")
        self._action_btn.setStyleSheet(
            "background:#28c840;color:#111;font-weight:bold;"
            "font-size:11px;padding:8px;border:none;"
        )

    # ── Signal slots ───────────────────────────────────────────────────────────

    def _on_position(self, x: float, y: float, z: float) -> None:
        self._label_x.setText(f"X\n{x:+.1f}")
        self._label_y.setText(f"Y\n{y:+.1f}")
        self._label_z.setText(f"Z\n{z:.1f}")

    def _on_frame(self, jpeg: bytes) -> None:
        pix = QPixmap()
        pix.loadFromData(jpeg, "JPEG")
        self._camera_label.setPixmap(
            pix.scaled(
                self._camera_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_status(self, status: str) -> None:
        text = _STATUS_TEXT.get(status, f"● {status.upper()}")
        color = _STATUS_COLOR.get(status, "#888")
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            f"color:{color};font-size:10px;font-weight:bold;"
        )

    # ── Drag to move ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event: object) -> None:
        if event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
            self._drag_pos = (
                event.globalPosition().toPoint()  # type: ignore[attr-defined]
                - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event: object) -> None:
        if (
            self._drag_pos is not None
            and event.buttons() == Qt.MouseButton.LeftButton  # type: ignore[attr-defined]
        ):
            self.move(
                event.globalPosition().toPoint()  # type: ignore[attr-defined]
                - self._drag_pos
            )

    def mouseReleaseEvent(self, event: object) -> None:
        if event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
            self._drag_pos = None

    # ── Slider helper ──────────────────────────────────────────────────────────

    def _make_slider(self, lo: float, hi: float, value: float, step: float) -> QSlider:
        s = QSlider(Qt.Orientation.Horizontal)
        s.setMinimum(0)
        s.setMaximum(int(round((hi - lo) / step)))
        s.setValue(int(round((value - lo) / step)))
        s.setProperty("_lo", lo)
        s.setProperty("_step", step)
        return s

    def _slider_value(self, s: QSlider) -> float:
        return s.property("_lo") + s.value() * s.property("_step")

    # ── Advanced tab ───────────────────────────────────────────────────────────

    def _make_advanced_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:#0d0d22;}")
        inner = QWidget()
        inner.setStyleSheet("background:#0d0d22;color:#c8c8e8;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        # Presets
        pg = QGroupBox("Presets")
        pg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
        pl = QHBoxLayout(pg)
        self._preset_combo = QComboBox()
        self._preset_combo.setEditable(True)
        self._refresh_presets()
        for label, slot in [("Save", self._on_preset_save),
                             ("Load", self._on_preset_load),
                             ("Delete", self._on_preset_delete)]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            pl.addWidget(btn)
        pl.insertWidget(0, self._preset_combo)
        lay.addWidget(pg)

        # Shader
        sg = QGroupBox("Shader Tuning")
        sg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
        sf = QFormLayout(sg)
        self._depth_curve_combo = QComboBox()
        self._depth_curve_combo.addItems(["Linear", "\u221a sqrt", "Gamma \u03b3"])
        self._depth_curve_combo.setCurrentIndex(int(self._settings.depth_curve))
        self._depth_curve_combo.currentIndexChanged.connect(self._on_settings_change)
        sf.addRow("Depth curve", self._depth_curve_combo)
        self._depth_gamma_spin = QDoubleSpinBox()
        self._depth_gamma_spin.setRange(0.3, 3.0)
        self._depth_gamma_spin.setSingleStep(0.1)
        self._depth_gamma_spin.setValue(self._settings.depth_gamma)
        self._depth_gamma_spin.valueChanged.connect(self._on_settings_change)
        sf.addRow("Gamma \u03b3", self._depth_gamma_spin)
        self._strength_x_slider = self._make_slider(0.0, 5.0, self._settings.strength_x, 0.05)
        self._strength_x_slider.valueChanged.connect(self._on_settings_change)
        sf.addRow("Strength X", self._strength_x_slider)
        self._strength_y_slider = self._make_slider(0.0, 5.0, self._settings.strength_y, 0.05)
        self._strength_y_slider.valueChanged.connect(self._on_settings_change)
        sf.addRow("Strength Y", self._strength_y_slider)
        self._focus_radius_slider = self._make_slider(0.0, 0.5, self._settings.focus_radius, 0.01)
        self._focus_radius_slider.valueChanged.connect(self._on_settings_change)
        sf.addRow("Focus radius", self._focus_radius_slider)
        self._virtual_depth_slider = self._make_slider(0.0, 200.0, self._settings.virtual_depth_cm, 1.0)
        self._virtual_depth_slider.valueChanged.connect(self._on_settings_change)
        sf.addRow("Virtual depth cm", self._virtual_depth_slider)
        lay.addWidget(sg)

        # Calibration
        cg = QGroupBox("Auto-Calibration")
        cg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
        cf = QFormLayout(cg)
        self._screen_w_spin = QDoubleSpinBox()
        self._screen_w_spin.setRange(0.0, 500.0)
        self._screen_w_spin.setDecimals(1)
        self._screen_w_spin.setSuffix(" cm")
        self._screen_w_spin.setValue(self._settings.screen_w_cm)
        self._screen_w_spin.valueChanged.connect(self._on_settings_change)
        self._screen_h_spin = QDoubleSpinBox()
        self._screen_h_spin.setRange(0.0, 500.0)
        self._screen_h_spin.setDecimals(1)
        self._screen_h_spin.setSuffix(" cm")
        self._screen_h_spin.setValue(self._settings.screen_h_cm)
        self._screen_h_spin.valueChanged.connect(self._on_settings_change)
        detect_btn = QPushButton("Auto-detect screen size")
        detect_btn.clicked.connect(self._on_detect_screen)
        self._head_dist_spin = QDoubleSpinBox()
        self._head_dist_spin.setRange(20.0, 200.0)
        self._head_dist_spin.setDecimals(1)
        self._head_dist_spin.setSuffix(" cm")
        self._head_dist_spin.setValue(self._settings.head_dist_cm)
        self._head_dist_spin.valueChanged.connect(self._on_settings_change)
        self._measure_btn = QPushButton("Measure head distance from camera")
        self._measure_btn.clicked.connect(self._on_measure_head)
        measure_btn = self._measure_btn
        self._calib_status = QLabel("")
        self._calib_status.setStyleSheet("color:#4a4;font-size:10px;")
        cf.addRow("Screen W", self._screen_w_spin)
        cf.addRow("Screen H", self._screen_h_spin)
        cf.addRow("", detect_btn)
        cf.addRow("Head dist", self._head_dist_spin)
        cf.addRow("", measure_btn)
        cf.addRow("", self._calib_status)
        lay.addWidget(cg)

        # Tracker
        tg = QGroupBox("Tracker Calibration")
        tg.setStyleSheet("QGroupBox{color:#3ecfcf;}")
        tf = QFormLayout(tg)
        self._fov_combo = QComboBox()
        self._fov_combo.setEditable(True)
        for fov in ["60", "70", "78", "90", "100", "110", "120"]:
            self._fov_combo.addItem(f"{fov}\u00b0", float(fov))
        idx = self._fov_combo.findText(f"{int(self._settings.camera_fov_deg)}\u00b0")
        if idx >= 0:
            self._fov_combo.setCurrentIndex(idx)
        self._fov_combo.currentIndexChanged.connect(self._on_settings_change)
        tf.addRow("Camera FOV", self._fov_combo)
        self._ipd_spin = QDoubleSpinBox()
        self._ipd_spin.setRange(50.0, 80.0)
        self._ipd_spin.setDecimals(1)
        self._ipd_spin.setSuffix(" mm")
        self._ipd_spin.setValue(self._settings.ipd_mm)
        self._ipd_spin.valueChanged.connect(self._on_settings_change)
        tf.addRow("IPD", self._ipd_spin)
        self._smoothing_slider = self._make_slider(0.01, 1.0, self._settings.smoothing_alpha, 0.01)
        self._smoothing_slider.valueChanged.connect(self._on_settings_change)
        tf.addRow("Smoothing \u03b1", self._smoothing_slider)
        self._deadzone_slider = self._make_slider(0.0, 30.0, self._settings.deadzone_mm, 0.5)
        self._deadzone_slider.valueChanged.connect(self._on_settings_change)
        tf.addRow("Deadzone mm", self._deadzone_slider)
        self._camera_tilt_spin = QDoubleSpinBox()
        self._camera_tilt_spin.setRange(-45.0, 45.0)
        self._camera_tilt_spin.setSingleStep(1.0)
        self._camera_tilt_spin.setDecimals(1)
        self._camera_tilt_spin.setSuffix(" °")
        self._camera_tilt_spin.setValue(self._camera_tilt_deg)
        self._camera_tilt_spin.valueChanged.connect(self._on_camera_tilt_change)
        tilt_row = QWidget()
        tilt_row_layout = QHBoxLayout(tilt_row)
        tilt_row_layout.setContentsMargins(0, 0, 0, 0)
        tilt_row_layout.setSpacing(4)
        tilt_row_layout.addWidget(self._camera_tilt_spin)
        recal_btn = QPushButton("Re-calibrate")
        recal_btn.setToolTip(
            "Reset tilt to 0° and restart tracker — auto-calibration runs continuously"
        )
        recal_btn.clicked.connect(self._on_recalibrate_tilt)
        tilt_row_layout.addWidget(recal_btn)
        self._tilt_status = QLabel("")
        self._tilt_status.setStyleSheet("color:#4a4;font-size:10px;")
        tilt_row_layout.addWidget(self._tilt_status)
        tf.addRow("Camera tilt", tilt_row)
        lay.addWidget(tg)
        lay.addStretch()

        save_cfg_btn = QPushButton("Save to config.yaml")
        save_cfg_btn.clicked.connect(self._on_save_config)
        lay.addWidget(save_cfg_btn)

        scroll.setWidget(inner)
        return scroll

    # ── Settings slots ─────────────────────────────────────────────────────────

    def _snapshot_settings(self) -> OverlaySettings:
        fov_text = self._fov_combo.currentText().replace("\u00b0", "").strip()
        try:
            fov = float(fov_text)
        except ValueError:
            fov = 90.0
        return OverlaySettings(
            strength_x=self._slider_value(self._strength_x_slider),
            strength_y=self._slider_value(self._strength_y_slider),
            virtual_depth_cm=self._slider_value(self._virtual_depth_slider),
            screen_w_cm=float(self._screen_w_spin.value()),
            screen_h_cm=float(self._screen_h_spin.value()),
            depth_curve=self._depth_curve_combo.currentIndex(),
            depth_gamma=float(self._depth_gamma_spin.value()),
            focus_radius=self._slider_value(self._focus_radius_slider),
            head_dist_cm=float(self._head_dist_spin.value()),
            camera_fov_deg=fov,
            ipd_mm=float(self._ipd_spin.value()),
            smoothing_alpha=self._slider_value(self._smoothing_slider),
            deadzone_mm=self._slider_value(self._deadzone_slider),
        )

    def _on_settings_change(self, *_: object) -> None:
        self._settings = self._snapshot_settings()
        self._settings_writer.write(self._settings)

    def _on_camera_tilt_change(self, value: float) -> None:
        self._camera_tilt_deg = float(value)

    def _on_recalibrate_tilt(self) -> None:
        """Reset tilt to 0 and restart tracker — it will auto-detect continuously."""
        import yaml  # local import to avoid shadowing module-level yaml
        try:
            with open(self._config_path) as f:
                cfg = yaml.safe_load(f) or {}
            cfg.setdefault("tracking", {})["camera_tilt_deg"] = 0.0
            with open(self._config_path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
        except OSError as e:
            self._tilt_status.setText(f"Error: {e}")
            return
        self._config.setdefault("tracking", {})["camera_tilt_deg"] = 0.0
        self._camera_tilt_spin.setValue(0.0)
        if self._thread and self._thread.isRunning():
            self._stop_tracking()
        self._start_tracking()
        self._tilt_status.setText("Auto-calibrating\u2026 (updates every ~30 s)")

    def _on_detect_screen(self) -> None:
        self._calib_status.setText("Detecting\u2026")
        w, h = detect_screen_cm()
        if w > 0 and h > 0:
            self._screen_w_spin.setValue(w)
            self._screen_h_spin.setValue(h)
            self._calib_status.setText(f"Detected: {w:.1f} \u00d7 {h:.1f} cm")
        else:
            self._calib_status.setText("Detection failed \u2014 enter manually")

    def _on_measure_head(self) -> None:
        # Disable button to prevent re-entrant calls while the webcam grab runs.
        self._measure_btn.setEnabled(False)
        self._calib_status.setText("Measuring (hold still 3 s)\u2026")
        try:
            dist = measure_head_distance(ipd_mm=self._ipd_spin.value())
            self._head_dist_spin.setValue(dist)
            self._calib_status.setText(f"Measured: {dist:.1f} cm")
        finally:
            self._measure_btn.setEnabled(True)

    def _refresh_presets(self) -> None:
        self._preset_combo.clear()
        for name in list_presets(self._config_path):
            self._preset_combo.addItem(name)

    def _on_preset_save(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name:
            return
        s = self._snapshot_settings()
        save_preset(self._config_path, name, dataclasses.asdict(s))
        self._refresh_presets()

    def _set_slider_value(self, sl: QSlider, v: float) -> None:
        """Set a slider created by `_make_slider` to the float value `v`."""
        sl.setValue(int(round((v - sl.property("_lo")) / sl.property("_step"))))

    def _on_preset_load(self) -> None:
        name = self._preset_combo.currentText().strip()
        try:
            data = load_preset(self._config_path, name)
        except KeyError:
            return
        widgets = [
            self._strength_x_slider, self._strength_y_slider,
            self._virtual_depth_slider, self._focus_radius_slider,
            self._smoothing_slider, self._deadzone_slider,
            self._depth_gamma_spin, self._ipd_spin,
            self._screen_w_spin, self._screen_h_spin,
            self._head_dist_spin, self._depth_curve_combo, self._fov_combo,
        ]
        for w in widgets:
            w.blockSignals(True)
        self._set_slider_value(self._strength_x_slider,    data.get("strength_x",      1.0))
        self._set_slider_value(self._strength_y_slider,    data.get("strength_y",      1.0))
        self._set_slider_value(self._virtual_depth_slider, data.get("virtual_depth_cm", 30.0))
        self._set_slider_value(self._focus_radius_slider,  data.get("focus_radius",    0.1))
        self._set_slider_value(self._smoothing_slider,     data.get("smoothing_alpha", 0.1))
        self._set_slider_value(self._deadzone_slider,      data.get("deadzone_mm",     5.0))
        self._depth_gamma_spin.setValue(data.get("depth_gamma", 1.0))
        self._ipd_spin.setValue(data.get("ipd_mm", 64.0))
        self._screen_w_spin.setValue(data.get("screen_w_cm", 0.0))
        self._screen_h_spin.setValue(data.get("screen_h_cm", 0.0))
        self._head_dist_spin.setValue(data.get("head_dist_cm", 60.0))
        self._depth_curve_combo.setCurrentIndex(int(data.get("depth_curve", 1)))
        fov_val = data.get("camera_fov_deg", 90)
        idx = self._fov_combo.findText(f"{round(fov_val)}\u00b0")
        if idx >= 0:
            self._fov_combo.setCurrentIndex(idx)
        else:
            self._fov_combo.setCurrentText(str(fov_val))
        for w in widgets:
            w.blockSignals(False)
        self._on_settings_change()

    def _on_preset_delete(self) -> None:
        name = self._preset_combo.currentText().strip()
        if not name:
            return
        delete_preset(self._config_path, name)
        self._refresh_presets()

    def _on_save_config(self) -> None:
        s = self._snapshot_settings()
        try:
            try:
                with open(self._config_path) as f:
                    cfg = yaml.safe_load(f) or {}
            except FileNotFoundError:
                cfg = {}
            cfg.setdefault("overlay", {}).update(dataclasses.asdict(s))
            cfg.setdefault("tracking", {})["camera_tilt_deg"] = self._camera_tilt_deg
            with open(self._config_path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
        except OSError:
            pass

    def closeEvent(self, event: object) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        self._overlay.stop()
        self._settings_writer.close()
        event.accept()  # type: ignore[attr-defined]
