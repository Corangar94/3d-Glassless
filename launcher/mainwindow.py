"""Always-on-top two-mode tracker window."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import statistics
import os
from collections import deque
from pathlib import Path
from typing import Optional

import copy
import dataclasses
import logging
import re
import yaml
from PySide6.QtCore import Qt, QPoint, QTimer, Signal
from PySide6.QtGui import QPixmap

_log = logging.getLogger(__name__)
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QApplication,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from tracker.shared_settings import OverlaySettings, SharedSettingsWriter
from tracker.tilt import _save_tilt_to_config
from tracker.display_backends import backend_code, normalize_backend_id
from launcher.presets import (
    PresetConfigError,
    delete_preset,
    list_presets,
    load_preset,
    save_preset,
)
from launcher.calibration import detect_screen_cm, measure_head_distance_or_none
from launcher.diagnostics import (
    OverlayRuntimeSummary,
    _find_overlay_log,
    _latest_overlay_summary,
)

from launcher.overlay_process import OverlayProcess, OverlayStartError
from launcher.window_discovery import RunningGameWindow, discover_running_game_windows
from launcher.auto_tune import TrackingAutoTuner
from launcher.tracker_process import TrackerProcess
from launcher.game_profile_store import ProfileStoreError, load_profiles, save_profiles
from launcher.game_profiles import (
    Backend,
    GameProfile,
    PlayContext,
    RequestedMode,
    evaluate_profile,
)

# Window dimensions
_EXPANDED_W, _EXPANDED_H = 920, 760
_MIN_EXPANDED_W, _MIN_EXPANDED_H = 760, 620
_COMPACT_W,  _COMPACT_H  = 760, 120

_STATUS_TEXT = {
    "tracking":     "● TRACKING",
    "hold":         "● HOLD",
    "paused":       "● PAUSED",
    "stopped":      "● STOPPED",
    "initializing": "⟳ INITIALIZING",
    "restarting":   "⟳ RESTARTING",
    "error":        "✕ ERROR",
}


def _same_executable_path(left: str, right: str) -> bool:
    if not left.strip() or not right.strip():
        return False
    return os.path.normcase(os.path.abspath(os.path.expanduser(left))) == os.path.normcase(
        os.path.abspath(os.path.expanduser(right))
    )


_STATUS_COLOR = {
    "tracking":     "#28c840",
    "hold":         "#febc2e",
    "paused":       "#888888",
    "stopped":      "#888888",
    "initializing": "#3ecfcf",
    "restarting":   "#f0c15a",
    "error":        "#e84040",
}
_DARK_BG = "#08110f"
_TITLE_BG = "#10231f"
_CARD_BG = "#132b25"
_ACCENT = "#f0c15a"
_ADVANCED_BG = "#0d0d22"
_DEPTH_HZ_WARN = 6
_CAPTURE_LOSS_RESTART_THRESHOLD = 3
_DEPTH_MODES = {
    "quality": 0,
    "balanced": 1,
    "fast": 2,
    "auto": 3,
}
_STEREO_LAYOUTS = {"full_sbs": 0, "half_sbs": 1}
_EYE_ORDERS = {"left_right": 0, "right_left": 1}
_TRACKING_MODES = {"glassless3d_managed": 0, "vendor_managed": 1}
_DEPTH_MODE_LABELS = {
    0: "Quality",
    1: "Balanced",
    2: "Fast",
    3: "Auto",
}
_PROFILE_CONTEXT_LABELS = {
    PlayContext.ONLINE_MULTIPLAYER: "Online / multiplayer",
    PlayContext.OFFLINE_SINGLEPLAYER: "Offline / single-player",
}
_REQUESTED_MODE_LABELS = {
    RequestedMode.NON_INJECTING_DESKTOP: "Non-injecting desktop",
    RequestedMode.OFFLINE_ADVANCED: "Offline advanced",
    RequestedMode.PUBLISHER_APPROVED_INTEGRATION: "Publisher-approved integration",
}

_COMFORT_PRESETS = {
    "safe": {
        "label": "Safe",
        "strength_x": 0.75,
        "strength_y": 0.30,
        "virtual_depth_cm": 24.0,
        "depth_curve": 2,
        "depth_gamma": 1.8,
        "focus_radius": 0.12,
        "smoothing_alpha": 0.16,
        "deadzone_mm": 6.0,
    },
    "balanced": {
        "label": "Balanced",
        "strength_x": 1.00,
        "strength_y": 0.40,
        "virtual_depth_cm": 30.0,
        "depth_curve": 2,
        "depth_gamma": 2.0,
        "focus_radius": 0.10,
        "smoothing_alpha": 0.12,
        "deadzone_mm": 5.0,
    },
    "strong": {
        "label": "Strong",
        "strength_x": 1.25,
        "strength_y": 0.65,
        "virtual_depth_cm": 42.0,
        "depth_curve": 2,
        "depth_gamma": 2.2,
        "focus_radius": 0.08,
        "smoothing_alpha": 0.10,
        "deadzone_mm": 4.0,
    },
}


def _depth_mode_code(value: object) -> int:
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _DEPTH_MODES:
            return _DEPTH_MODES[key]
        try:
            value = int(key)
        except ValueError:
            return _DEPTH_MODES["balanced"]
    try:
        code = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEPTH_MODES["balanced"]
    return code if code in _DEPTH_MODE_LABELS else _DEPTH_MODES["balanced"]


def _depth_mode_name(code: int) -> str:
    for name, value in _DEPTH_MODES.items():
        if value == code:
            return name
    return "balanced"


class ConfigMappingError(ValueError):
    """Raised when config.yaml is valid YAML but not a mapping."""


def _load_yaml_mapping(path: str, fallback: dict[str, object] | None = None) -> dict[str, object]:
    try:
        with open(path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
    except FileNotFoundError:
        return copy.deepcopy(fallback) if fallback is not None else {}
    if isinstance(loaded, dict):
        return loaded
    raise ConfigMappingError("config root must be a mapping")


def _ensure_mapping_child(data: dict[str, object], key: str) -> dict[str, object]:
    child = data.get(key)
    if isinstance(child, dict):
        return child
    child = {}
    data[key] = child
    return child


def _positive_float(data: dict[str, object], key: str, default: float) -> float:
    raw = data.get(key, default)
    if not isinstance(raw, (int, float, str)):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0.0 else default


def _calibrated_float(
    overlay: dict[str, object],
    calibration: dict[str, object],
    calibration_key: str,
    overlay_key: str,
    default: float,
) -> float:
    value = _positive_float(calibration, calibration_key, 0.0)
    if value > 0.0:
        return value
    return _positive_float(overlay, overlay_key, default)


def _enum_code(data: dict[str, object], key: str, choices: dict[str, int], default: int) -> int:
    value = data.get(key)
    if isinstance(value, str):
        return choices.get(value.strip().lower(), default)
    try:
        code = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return code if code in choices.values() else default


def _positive_int(data: dict[str, object], key: str, default: int = 0) -> int:
    raw = data.get(key, default)
    if not isinstance(raw, (int, float, str)):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _scrollable_tab(inner: QWidget, background: str) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    scroll.setStyleSheet(f"QScrollArea{{border:none;background:{background};}}")
    scroll.setWidget(inner)
    return scroll


def _advanced_group_style() -> str:
    return (
        f"QGroupBox{{background:{_ADVANCED_BG};color:#3ecfcf;border:1px solid #213a54;"
        "border-radius:8px;margin-top:14px;padding-top:10px;}}"
        "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}"
    )


def _configure_form_layout(form: QFormLayout) -> None:
    form.setContentsMargins(12, 14, 12, 12)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)



class MainWindow(QMainWindow):
    _head_measurement_finished = Signal(object)

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
        self._thread: Optional[TrackerProcess] = None
        self._tracker_stop_pending = False
        self._live_tracking_distances: deque[float] = deque(maxlen=30)
        self._overlay = OverlayProcess()
        self._overlay_started = False
        self._selected_running_target: RunningGameWindow | None = None
        self._hidden_for_overlay = False
        self._capture_loss_count = 0
        self._debug_monitor_proc: Optional[subprocess.Popen[bytes]] = None
        self._drag_pos: Optional[QPoint] = None
        self._initialize_game_profiles()

        self._settings_writer = SharedSettingsWriter()
        trk = config.get("tracking", {})
        self._auto_tune_enabled = bool(trk.get("auto_tune", True))
        self._auto_tuner = TrackingAutoTuner()
        self._last_auto_tune_write_s = 0.0
        self._tracking_status = "stopped"
        self._camera_tilt_deg: float = float(trk.get("camera_tilt_deg", 0.0))
        ov = config.get("overlay", {})
        if not isinstance(ov, dict):
            ov = {}
        calibration = ov.get("display_calibration", {})
        if not isinstance(calibration, dict):
            calibration = {}
        self._display_backend_id = normalize_backend_id(ov.get("display_backend", "desktop_overlay"))
        self._settings = OverlaySettings(
            strength_x=float(ov.get("strength_x", 1.0)),
            strength_y=float(ov.get("strength_y", 1.0)),
            virtual_depth_cm=float(ov.get("virtual_depth_cm", 30.0)),
            screen_w_cm=_calibrated_float(ov, calibration, "panel_width_cm", "screen_w_cm", 0.0),
            screen_h_cm=_calibrated_float(ov, calibration, "panel_height_cm", "screen_h_cm", 0.0),
            depth_curve=int(ov.get("depth_curve", 1)),
            depth_gamma=float(ov.get("depth_gamma", 1.0)),
            focus_radius=float(ov.get("focus_radius", 0.1)),
            head_dist_cm=_calibrated_float(ov, calibration, "viewer_distance_cm", "head_dist_cm", 60.0),
            camera_fov_deg=float(ov.get("camera_fov_deg", 90.0)),
            ipd_mm=_calibrated_float(ov, calibration, "ipd_mm", "ipd_mm", 64.0),
            smoothing_alpha=float(ov.get("smoothing_alpha", 0.1)),
            deadzone_mm=float(ov.get("deadzone_mm", 5.0)),
            display_backend=backend_code(self._display_backend_id),
            depth_mode=_depth_mode_code(ov.get("depth_performance_mode", ov.get("depth_mode", "balanced"))),
            stereo_layout=_enum_code(calibration, "stereo_layout", _STEREO_LAYOUTS, 0),
            eye_order=_enum_code(calibration, "eye_order", _EYE_ORDERS, 0),
            panel_width_px=_positive_int(calibration, "panel_width_px"),
            panel_height_px=_positive_int(calibration, "panel_height_px"),
            focus_plane_cm=_positive_float(calibration, "focus_plane_cm", 0.0),
            tracking_mode=_enum_code(calibration, "tracking_mode", _TRACKING_MODES, 0),
        )
        self._settings_writer.write(self._settings)

        # NOTE: preload thread removed — it holds the Python import lock while
        # mediapipe loads and blocks TrackerThread.run() when the user clicks Start.

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._build_ui()
        self._apply_mode()
        self._on_status("stopped")
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(2000)
        self._health_timer.timeout.connect(self._safe_refresh_runtime_health)
        self._health_timer.start()
        self._safe_refresh_runtime_health()
        self._head_measurement_finished.connect(self._on_head_measurement_finished)

    def _initialize_game_profiles(self) -> None:
        self._profile_store_error: str | None = None
        try:
            profiles, active_profile_id = load_profiles(
                Path(self._config_path),
                fallback=self._config,
            )
        except ProfileStoreError as exc:
            profiles = {}
            active_profile_id = None
            self._profile_store_error = str(exc)

        if active_profile_id is None:
            default_profile = GameProfile(
                profile_id="default",
                display_name="Default profile",
                executable_path="",
            )
            profiles = {default_profile.profile_id: default_profile}
            active_profile_id = default_profile.profile_id

        self._profiles: dict[str, GameProfile] = profiles
        self._active_profile_id = active_profile_id
        self._active_profile = self._profiles[active_profile_id]
        self._policy_decision = evaluate_profile(self._active_profile)

        if self._profile_store_error is None:
            self._persist_game_profiles(fallback=self._config)

    def _persist_game_profiles(
        self,
        *,
        fallback: dict[str, object] | None = None,
        base_config: dict[str, object] | None = None,
    ) -> bool:
        if self._profile_store_error is not None:
            return False
        try:
            save_profiles(
                Path(self._config_path),
                self._profiles,
                self._active_profile_id,
                fallback=fallback,
                base_config=base_config,
            )
        except (OSError, ProfileStoreError) as exc:
            self._profile_store_error = str(exc)
            self._update_profile_mode_label()
            return False
        return True

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        root.setStyleSheet(f"background:{_DARK_BG};border-radius:14px;")
        self.setCentralWidget(root)
        self._root_layout = QVBoxLayout(root)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        self._root_layout.addWidget(self._make_titlebar())

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:none;background:{_DARK_BG};}}"
            f"QTabBar::tab{{background:{_TITLE_BG};color:#93a69c;padding:8px 18px;}}"
            f"QTabBar::tab:selected{{background:{_DARK_BG};color:{_ACCENT};}}"
        )

        self._tabs.addTab(self._make_runtime_tab(), "Runtime")
        self._tabs.addTab(self._make_advanced_tab(), "Advanced")
        self._root_layout.addWidget(self._tabs)

    def _make_runtime_tab(self) -> QWidget:
        tab = QWidget()
        tab.setStyleSheet(f"background:{_DARK_BG};")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self._hero_label = QLabel(
            "Overlay-first runtime\n"
            "Camera tracking drives the standalone Windows overlay. ReShade is experimental."
        )
        self._hero_label.setWordWrap(True)
        self._hero_label.setMinimumWidth(0)
        self._hero_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._hero_label.setStyleSheet(
            f"color:{_ACCENT};font-size:20px;font-weight:800;line-height:130%;"
        )
        layout.addWidget(self._hero_label)

        status_row = QHBoxLayout()
        status_row.setSpacing(10)
        self._tracker_tile = self._status_tile("Tracker", "Stopped")
        self._overlay_tile = self._status_tile("Overlay", "Idle")
        camera_index = self._config.get("camera", {}).get("index", 0)
        self._camera_tile = self._status_tile("Camera", f"Camera {camera_index}")
        self._backend_tile = self._status_tile("Backend", self._display_backend_id)
        for tile in (
            self._tracker_tile,
            self._overlay_tile,
            self._camera_tile,
            self._backend_tile,
        ):
            status_row.addWidget(tile)
        layout.addLayout(status_row)

        health_row = QHBoxLayout()
        health_row.setSpacing(10)
        self._shm_tile = self._status_tile("SHM", "Waiting")
        self._depth_tile = self._status_tile("Depth", "Waiting")
        self._capture_tile = self._status_tile("Capture", "Waiting")
        for tile in (self._shm_tile, self._depth_tile, self._capture_tile):
            health_row.addWidget(tile)
        layout.addLayout(health_row)

        layout.addWidget(self._make_game_profile_panel())

        mid_row = QHBoxLayout()
        mid_row.setSpacing(12)
        self._camera_label = QLabel("Camera preview is available in embedded tracker mode")
        self._camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_label.setMinimumHeight(210)
        self._camera_label.setMinimumWidth(0)
        self._camera_label.setWordWrap(True)
        self._camera_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._camera_label.setStyleSheet(
            "background:#020706;color:#7b8f86;border:1px solid #24443b;"
            "border-radius:10px;font-size:12px;"
        )
        mid_row.addWidget(self._camera_label, 3)

        side = QVBoxLayout()
        side.setSpacing(10)
        side.addLayout(self._make_xyz_row())
        side.addWidget(self._make_comfort_presets_panel())
        side.addWidget(self._make_action_button())
        side.addWidget(self._operator_button("Run diagnostics", self._run_diagnostics))
        side.addWidget(self._operator_button("Collect support bundle", self._collect_support_bundle))
        side.addWidget(self._operator_button("Open quality monitor", self._open_debug_monitor))
        side.addStretch()
        mid_row.addLayout(side, 2)
        layout.addLayout(mid_row)

        layout.addStretch()
        return _scrollable_tab(tab, _DARK_BG)

    def _status_tile(self, label: str, value: str) -> QLabel:
        tile = QLabel(f"{label}\n{value}")
        tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tile.setStyleSheet(
            f"background:{_CARD_BG};color:#dce8df;border:1px solid #254f45;"
            "border-radius:10px;padding:10px;font-size:12px;font-weight:700;"
        )
        tile.setWordWrap(True)
        tile.setMinimumWidth(0)
        tile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tile.setMinimumHeight(64)
        return tile

    def _make_game_profile_panel(self) -> QGroupBox:
        panel = QGroupBox("Game profile")
        panel.setStyleSheet(
            f"QGroupBox{{background:{_CARD_BG};color:{_ACCENT};font-weight:800;"
            "border:1px solid #254f45;border-radius:10px;margin-top:8px;padding:8px;}"
            "QGroupBox::title{subcontrol-origin:margin;left:10px;padding:0 4px;}"
        )
        layout = QFormLayout(panel)
        layout.setContentsMargins(10, 12, 10, 8)
        layout.setSpacing(6)

        profile_row = QWidget()
        profile_layout = QHBoxLayout(profile_row)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(6)
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(0)
        self._profile_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        for profile_id, profile in sorted(
            self._profiles.items(),
            key=lambda item: (item[1].display_name.casefold(), item[0]),
        ):
            self._profile_combo.addItem(profile.display_name, profile_id)
        self._profile_combo.setCurrentIndex(
            max(0, self._profile_combo.findData(self._active_profile_id))
        )
        profile_layout.addWidget(self._profile_combo)
        self._profile_add_button = QPushButton("Add game")
        self._profile_add_button.setMinimumWidth(96)
        self._profile_add_button.setToolTip(
            "Create a manually named profile. Glassless3D does not auto-classify games."
        )
        profile_layout.addWidget(self._profile_add_button)
        layout.addRow("Profile", profile_row)

        running_row = QWidget()
        running_layout = QHBoxLayout(running_row)
        running_layout.setContentsMargins(0, 0, 0, 0)
        running_layout.setSpacing(6)
        self._running_game_combo = QComboBox()
        self._running_game_combo.setMinimumWidth(0)
        self._running_game_combo.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self._running_game_combo.setToolTip(
            "Select the exact open game window. This is more reliable than browsing to an executable."
        )
        running_layout.addWidget(self._running_game_combo, 1)
        self._running_game_refresh_button = QPushButton("Refresh")
        self._running_game_refresh_button.setMinimumWidth(86)
        running_layout.addWidget(self._running_game_refresh_button)
        layout.addRow("Open game", running_row)

        executable_row = QWidget()
        executable_layout = QHBoxLayout(executable_row)
        executable_layout.setContentsMargins(0, 0, 0, 0)
        executable_layout.setSpacing(6)
        self._profile_executable_edit = QLineEdit()
        self._profile_executable_edit.setMinimumWidth(0)
        self._profile_executable_edit.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self._profile_executable_edit.setPlaceholderText("C:/Games/Title/Title.exe")
        executable_layout.addWidget(self._profile_executable_edit, 1)
        self._profile_browse_button = QPushButton("Browse…")
        self._profile_browse_button.setMinimumWidth(86)
        executable_layout.addWidget(self._profile_browse_button)
        layout.addRow("Executable", executable_row)

        self._profile_target_label = QLabel()
        self._profile_target_label.setWordWrap(True)
        self._profile_target_label.setStyleSheet("font-size:10px;")
        layout.addRow("Target", self._profile_target_label)

        self._play_context_combo = QComboBox()
        self._play_context_combo.setMinimumWidth(0)
        self._play_context_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        for context, label in _PROFILE_CONTEXT_LABELS.items():
            self._play_context_combo.addItem(label, context.value)
        layout.addRow("Play context", self._play_context_combo)

        self._requested_mode_combo = QComboBox()
        self._requested_mode_combo.setMinimumWidth(0)
        self._requested_mode_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        for mode, label in _REQUESTED_MODE_LABELS.items():
            self._requested_mode_combo.addItem(label, mode.value)
        layout.addRow("Requested mode", self._requested_mode_combo)

        self._advanced_ack_checkbox = QCheckBox(
            "I confirmed this game permits advanced integration"
        )
        self._advanced_ack_checkbox.setMinimumWidth(0)
        self._advanced_ack_checkbox.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._advanced_ack_checkbox.setStyleSheet("color:#dce8df;font-size:11px;")
        layout.addRow("", self._advanced_ack_checkbox)

        self._profile_mode_label = QLabel()
        self._profile_mode_label.setWordWrap(True)
        self._profile_mode_label.setMinimumWidth(0)
        self._profile_mode_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        layout.addRow("Active", self._profile_mode_label)

        self._profile_disclaimer_label = QLabel(
            "Online compatibility is title-specific and subject to the game publisher and anti-cheat policy."
        )
        self._profile_disclaimer_label.setWordWrap(True)
        self._profile_disclaimer_label.setMinimumWidth(0)
        self._profile_disclaimer_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._profile_disclaimer_label.setStyleSheet("color:#8ea69b;font-size:10px;")
        layout.addRow("", self._profile_disclaimer_label)

        self._profile_combo.currentIndexChanged.connect(self._on_profile_selection_changed)
        self._profile_add_button.clicked.connect(self._add_game_profile)
        self._running_game_combo.activated.connect(self._on_running_game_selected)
        self._running_game_refresh_button.clicked.connect(self._refresh_running_games)
        self._profile_executable_edit.editingFinished.connect(self._on_profile_controls_changed)
        self._profile_browse_button.clicked.connect(self._browse_game_executable)
        self._play_context_combo.currentIndexChanged.connect(self._on_profile_controls_changed)
        self._requested_mode_combo.currentIndexChanged.connect(self._on_profile_controls_changed)
        self._advanced_ack_checkbox.toggled.connect(self._on_profile_controls_changed)
        self._refresh_running_games()
        self._sync_profile_controls()
        return panel

    def _sync_profile_controls(self) -> None:
        if not hasattr(self, "_profile_combo"):
            return
        controls = (
            self._profile_combo,
            self._play_context_combo,
            self._requested_mode_combo,
            self._advanced_ack_checkbox,
            self._profile_executable_edit,
            self._profile_browse_button,
            self._running_game_combo,
            self._running_game_refresh_button,
        )
        for control in controls:
            control.blockSignals(True)
        try:
            profile_index = self._profile_combo.findData(self._active_profile_id)
            if profile_index >= 0:
                self._profile_combo.setCurrentIndex(profile_index)
            context_index = self._play_context_combo.findData(self._active_profile.play_context.value)
            if context_index >= 0:
                self._play_context_combo.setCurrentIndex(context_index)
            mode_index = self._requested_mode_combo.findData(self._active_profile.requested_mode.value)
            if mode_index >= 0:
                self._requested_mode_combo.setCurrentIndex(mode_index)
            self._advanced_ack_checkbox.setChecked(self._active_profile.advanced_acknowledged)
            self._profile_executable_edit.setText(self._active_profile.executable_path)
        finally:
            for control in controls:
                control.blockSignals(False)

        editable = self._profile_store_error is None
        advanced_controls_enabled = (
            editable and self._active_profile.play_context is PlayContext.OFFLINE_SINGLEPLAYER
        )
        self._profile_combo.setEnabled(editable)
        self._play_context_combo.setEnabled(editable)
        self._requested_mode_combo.setEnabled(advanced_controls_enabled)
        self._advanced_ack_checkbox.setEnabled(advanced_controls_enabled)
        self._profile_executable_edit.setEnabled(editable)
        self._profile_browse_button.setEnabled(editable)
        self._running_game_combo.setEnabled(editable)
        self._running_game_refresh_button.setEnabled(editable)
        self._profile_add_button.setEnabled(editable)
        self._update_target_feedback()
        self._update_profile_mode_label()

    def _update_target_feedback(self) -> None:
        if not hasattr(self, "_profile_target_label"):
            return
        raw = self._active_profile.executable_path.strip()
        live_target = self._selected_running_target
        if live_target is not None and _same_executable_path(
            raw, live_target.executable_path
        ):
            self._profile_target_label.setText(
                f"Selected live window: {live_target.title} (PID {live_target.pid})"
            )
            self._profile_target_label.setStyleSheet("color:#79d99b;font-size:10px;")
            return
        if not raw:
            text, color = "No game selected — desktop capture will be used", "#f0c15a"
        else:
            path = Path(raw).expanduser()
            if path.is_file() and path.suffix.casefold() == ".exe":
                text, color = f"Ready: {path.name}", "#79d99b"
            elif path.suffix.casefold() != ".exe":
                text, color = "Invalid target — select a Windows .exe", "#ff9b9b"
            else:
                text, color = "Executable not found — browse to the installed game", "#ff9b9b"
        self._profile_target_label.setText(text)
        self._profile_target_label.setStyleSheet(f"color:{color};font-size:10px;")

    def _browse_game_executable(self) -> None:
        current = self._profile_executable_edit.text().strip()
        start_dir = str(Path(current).parent) if current else str(Path.home())
        selected, _filter = QFileDialog.getOpenFileName(
            self, "Select game executable", start_dir, "Windows games (*.exe)"
        )
        if not selected:
            return
        self._selected_running_target = None
        self._running_game_combo.setCurrentIndex(0)
        self._profile_executable_edit.setText(str(Path(selected).resolve()))
        self._on_profile_controls_changed()

    def _refresh_running_games(self) -> None:
        if not hasattr(self, "_running_game_combo"):
            return
        candidates = discover_running_game_windows()
        combo = self._running_game_combo
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(
                "Select an open game window…" if candidates else "No open game windows found",
                None,
            )
            selected_index = 0
            selected_target: RunningGameWindow | None = None
            active_path = self._active_profile.executable_path.strip()
            for candidate in candidates:
                combo.addItem(candidate.label, candidate)
                if _same_executable_path(candidate.executable_path, active_path):
                    selected_index = combo.count() - 1
                    selected_target = candidate
            combo.setCurrentIndex(selected_index)
            self._selected_running_target = selected_target
        finally:
            combo.blockSignals(False)
        self._update_target_feedback()

    def _on_running_game_selected(self, index: int) -> None:
        candidate = self._running_game_combo.itemData(index)
        if not isinstance(candidate, RunningGameWindow):
            return
        self._selected_running_target = candidate
        self._profile_executable_edit.setText(candidate.executable_path)
        self._on_profile_controls_changed()

    def _rebind_overlay_for_active_profile(self) -> None:
        if not self._overlay_started or not self._tracker_is_running():
            return
        kwargs = self._overlay_target_kwargs()
        self._overlay.restart_async(self._active_profile.executable_path, **kwargs)
        self._overlay_tile.setText("Overlay\nRebinding")

    def _overlay_target_kwargs(self) -> dict[str, int]:
        target = self._selected_running_target
        if target is None:
            return {}
        if not _same_executable_path(
            target.executable_path, self._active_profile.executable_path
        ):
            return {}
        return {"target_pid": target.pid}

    def _update_profile_mode_label(self) -> None:
        if not hasattr(self, "_profile_mode_label"):
            return
        if self._profile_store_error is not None:
            self._profile_mode_label.setStyleSheet("color:#ff9b9b;font-size:11px;")
            self._profile_mode_label.setText(
                f"Profile configuration error: {self._profile_store_error}"
            )
            return

        active_mode = _REQUESTED_MODE_LABELS[self._policy_decision.active_mode]
        if self._policy_decision.reason:
            text = f"{active_mode} — {self._policy_decision.reason}"
        else:
            text = active_mode
        self._profile_mode_label.setStyleSheet("color:#b9d7c7;font-size:11px;")
        self._profile_mode_label.setText(text)

    def _on_profile_selection_changed(self, index: int) -> None:
        profile_id = self._profile_combo.itemData(index)
        if not isinstance(profile_id, str) or profile_id not in self._profiles:
            return
        previous_profile_id = self._active_profile_id
        previous_profile = self._active_profile
        self._selected_running_target = None
        self._active_profile_id = profile_id
        self._active_profile = self._profiles[profile_id]
        self._policy_decision = evaluate_profile(self._active_profile)
        if not self._persist_game_profiles():
            self._active_profile_id = previous_profile_id
            self._active_profile = previous_profile
            self._policy_decision = evaluate_profile(previous_profile)
        self._refresh_running_games()
        self._sync_profile_controls()
        if self._active_profile_id != previous_profile_id:
            self._rebind_overlay_for_active_profile()

    def _on_profile_controls_changed(self, *_: object) -> None:
        if self._profile_store_error is not None:
            return
        try:
            play_context = PlayContext(str(self._play_context_combo.currentData()))
        except ValueError:
            play_context = PlayContext.ONLINE_MULTIPLAYER
        try:
            requested_mode = RequestedMode(str(self._requested_mode_combo.currentData()))
        except ValueError:
            requested_mode = RequestedMode.NON_INJECTING_DESKTOP
        advanced_acknowledged = self._advanced_ack_checkbox.isChecked()
        if play_context is PlayContext.ONLINE_MULTIPLAYER:
            requested_mode = RequestedMode.NON_INJECTING_DESKTOP
            advanced_acknowledged = False

        edited_executable = self._profile_executable_edit.text().strip()
        if (
            self._selected_running_target is not None
            and not _same_executable_path(
                edited_executable, self._selected_running_target.executable_path
            )
        ):
            self._selected_running_target = None
            self._running_game_combo.setCurrentIndex(0)
        previous_profile = self._active_profile
        updated_profile = dataclasses.replace(
            previous_profile,
            play_context=play_context,
            requested_mode=requested_mode,
            advanced_acknowledged=advanced_acknowledged,
            executable_path=edited_executable,
        )
        self._profiles[self._active_profile_id] = updated_profile
        self._active_profile = updated_profile
        self._policy_decision = evaluate_profile(updated_profile)
        if not self._persist_game_profiles():
            self._profiles[self._active_profile_id] = previous_profile
            self._active_profile = previous_profile
            self._policy_decision = evaluate_profile(previous_profile)
            self._sync_profile_controls()
            return
        self._sync_profile_controls()
        if updated_profile != previous_profile:
            self._rebind_overlay_for_active_profile()

    def _add_game_profile(self) -> None:
        if self._profile_store_error is not None:
            return
        name, accepted = QInputDialog.getText(self, "Add game profile", "Game name:")
        display_name = name.strip()
        if not accepted or not display_name:
            return

        base_profile_id = re.sub(r"[^a-z0-9]+", "-", display_name.casefold()).strip("-")
        if not base_profile_id:
            base_profile_id = "game"
        profile_id = base_profile_id
        suffix = 2
        while profile_id in self._profiles:
            profile_id = f"{base_profile_id}-{suffix}"
            suffix += 1

        previous_profiles = self._profiles
        previous_profile_id = self._active_profile_id
        previous_profile = self._active_profile
        profile = GameProfile(
            profile_id=profile_id,
            display_name=display_name,
            executable_path="",
        )
        self._profiles = {**self._profiles, profile_id: profile}
        self._active_profile_id = profile_id
        self._active_profile = profile
        self._policy_decision = evaluate_profile(profile)
        if not self._persist_game_profiles():
            self._profiles = previous_profiles
            self._active_profile_id = previous_profile_id
            self._active_profile = previous_profile
            self._policy_decision = evaluate_profile(previous_profile)
            self._sync_profile_controls()
            return

        self._profile_combo.addItem(profile.display_name, profile_id)
        self._sync_profile_controls()

    def _operator_button(self, text: str, slot: object) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumWidth(0)
        btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        btn.setStyleSheet(
            "QPushButton{background:#1d3b34;color:#e6f0e9;font-weight:700;"
            "border:1px solid #315c51;border-radius:8px;padding:9px;}"
            "QPushButton:hover{background:#2a5147;}"
        )
        btn.clicked.connect(slot)  # type: ignore[arg-type]
        return btn

    def _make_comfort_presets_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(
            f"background:{_CARD_BG};border:1px solid #254f45;border-radius:10px;"
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel("Comfort presets")
        title.setStyleSheet(f"color:{_ACCENT};font-size:12px;font-weight:900;")
        layout.addWidget(title)

        self._auto_tune_checkbox = QCheckBox("Automatic head tracking (recommended)")
        self._auto_tune_checkbox.setChecked(self._auto_tune_enabled)
        self._auto_tune_checkbox.setToolTip(
            "Automatically balances stability and response from live head motion."
        )
        self._auto_tune_checkbox.toggled.connect(self._on_auto_tune_toggle)
        layout.addWidget(self._auto_tune_checkbox)

        self._auto_tune_status = QLabel("Auto tuning will calibrate when tracking starts")
        self._auto_tune_status.setWordWrap(True)
        self._auto_tune_status.setStyleSheet("color:#78cbb0;font-size:10px;")
        layout.addWidget(self._auto_tune_status)

        row = QHBoxLayout()
        row.setSpacing(6)
        for key, text in (
            ("safe", "Safe comfort"),
            ("balanced", "Balanced depth"),
            ("strong", "Strong depth"),
        ):
            btn = QPushButton(text)
            btn.setMinimumWidth(0)
            btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            btn.setStyleSheet(
                "QPushButton{background:#18342e;color:#dce8df;font-weight:700;"
                "border:1px solid #315c51;border-radius:7px;padding:7px;}"
                "QPushButton:hover{background:#285247;}"
            )
            btn.clicked.connect(lambda _checked=False, preset=key: self._apply_comfort_preset(preset))
            row.addWidget(btn)
        layout.addLayout(row)

        self._comfort_status = QLabel("Balanced reduces vertical parallax for comfort")
        self._comfort_status.setWordWrap(True)
        self._comfort_status.setMinimumWidth(0)
        self._comfort_status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self._comfort_status.setStyleSheet("color:#8ea69b;font-size:10px;")
        layout.addWidget(self._comfort_status)

        self._depth_mode_combo = QComboBox()
        for label, code in (("Auto depth", 3), ("Quality depth", 0), ("Balanced depth", 1), ("Fast depth", 2)):
            self._depth_mode_combo.addItem(label, code)
        self._depth_mode_combo.setMinimumWidth(0)
        self._depth_mode_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._depth_mode_combo.setCurrentIndex(max(0, self._depth_mode_combo.findData(self._settings.depth_mode)))
        self._depth_mode_combo.currentIndexChanged.connect(self._on_settings_change)
        self._depth_mode_combo.currentIndexChanged.connect(lambda _index: self._on_save_config())
        layout.addWidget(self._depth_mode_combo)
        return panel

    def _make_titlebar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(f"background:{_TITLE_BG};")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)

        logo = QLabel("● GLASSLESS 3D")
        logo.setStyleSheet(f"color:{_ACCENT};font-size:12px;font-weight:bold;")
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
            f"background:{_CARD_BG};color:#9fe6c4;font-family:monospace;"
            "font-size:16px;font-weight:bold;border-radius:8px;padding:10px;"
        )
        tile.setMinimumWidth(0)
        tile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return tile

    def _make_action_button(self) -> QPushButton:
        self._action_btn = QPushButton("▶ START TRACKING")
        self._action_btn.setMinimumWidth(0)
        self._action_btn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._action_btn.setStyleSheet(
            f"background:{_ACCENT};color:#111;font-weight:900;"
            "font-size:13px;padding:12px;border:none;border-radius:8px;"
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
            self._tabs.setVisible(False)
            self._toggle_btn.setText("▼")
        else:
            self.setMinimumSize(_MIN_EXPANDED_W, _MIN_EXPANDED_H)
            self.setMaximumSize(16777215, 16777215)
            if self.width() < _EXPANDED_W or self.height() < _EXPANDED_H:
                self.resize(_EXPANDED_W, _EXPANDED_H)
            self._tabs.setVisible(True)
            self._toggle_btn.setText("▲")

    def _save_compact_pref(self) -> None:
        try:
            cfg = _load_yaml_mapping(self._config_path)
            _ensure_mapping_child(cfg, "gui")["compact_mode"] = self._compact
            with open(self._config_path, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
        except (ConfigMappingError, OSError, yaml.YAMLError):
            pass

    # ── Tracking control ───────────────────────────────────────────────────────

    def _toggle_tracking(self) -> None:
        if self._thread and self._thread.isRunning():
            self._stop_tracking()
        else:
            self._start_tracking()

    def _start_tracking(self) -> None:
        if self._tracker_stop_pending:
            return
        self._policy_decision = evaluate_profile(self._active_profile)
        self._update_profile_mode_label()
        if not self._policy_decision.allows(Backend.DESKTOP_OVERLAY):
            self._on_status("error")
            self._status_label.setToolTip("The active game profile does not permit desktop overlay runtime.")
            return

        self._on_status("initializing")
        self._action_btn.setText("■ STOP TRACKING")
        self._action_btn.setStyleSheet(
            "background:#e84040;color:#fff;font-weight:bold;"
            "font-size:13px;padding:12px;border:none;border-radius:8px;"
        )
        self._overlay_started = False

        tracker = TrackerProcess(config_path=self._config_path)
        tracker.position_updated.connect(self._on_position)
        tracker.frame_ready.connect(self._on_frame)
        tracker.status_changed.connect(self._on_status)
        tracker.status_changed.connect(self._on_tracker_status_for_overlay)
        tracker.stopped.connect(self._on_tracker_stopped)
        if not tracker.start():
            self._overlay_started = False
            self._thread = None
            self._on_status("error")
            self._action_btn.setText("▶ START TRACKING")
            self._action_btn.setStyleSheet(
                f"background:{_ACCENT};color:#111;font-weight:900;"
                "font-size:13px;padding:12px;border:none;border-radius:8px;"
            )
            return
        self._thread = tracker

    def _on_tracker_status_for_overlay(self, status: str) -> None:
        """Start the overlay the first time the tracker is actually running."""
        if status not in ("tracking", "hold", "paused") or self._overlay_started:
            return
        self._overlay_started = True
        if self._thread:
            self._thread.status_changed.disconnect(self._on_tracker_status_for_overlay)
        try:
            self._overlay.start(
                self._active_profile.executable_path,
                **self._overlay_target_kwargs(),
            )
            self._overlay_tile.setText("Overlay\nRunning")
            self._hidden_for_overlay = True
            self.showMinimized()
        except OverlayStartError as e:
            self._on_status("error")
            self._status_label.setText("✕ OVERLAY ERROR")
            self._overlay_tile.setText("Overlay\nError")
            self._status_label.setToolTip(str(e))
            _log.warning("overlay launch failed: %s", e)

    def _stop_tracking(self) -> None:
        if self._thread:
            self._tracker_stop_pending = True
            self._thread.stop()
        self._overlay.stop_async()
        self._overlay_started = False
        if self._hidden_for_overlay:
            self._hidden_for_overlay = False
            self.showNormal()
        self._on_status("stopped")
        self._action_btn.setText("Stopping…" if self._tracker_stop_pending else "▶ START TRACKING")
        self._action_btn.setEnabled(not self._tracker_stop_pending)
        self._action_btn.setStyleSheet(
            f"background:{_ACCENT};color:#111;font-weight:900;"
            "font-size:13px;padding:12px;border:none;border-radius:8px;"
        )
        self._overlay_tile.setText("Overlay\nIdle")
        self._apply_runtime_health(None)

    def _on_tracker_stopped(self, tracker: TrackerProcess | None = None) -> None:
        """Release lifecycle ownership only after the camera child has exited."""
        if tracker is None or self._thread is tracker:
            self._thread = None
        self._tracker_stop_pending = False
        self._action_btn.setEnabled(True)
        self._action_btn.setText("▶ START TRACKING")

    # ── Signal slots ───────────────────────────────────────────────────────────

    def _on_position(self, x: float, y: float, z: float) -> None:
        self._label_x.setText(f"X\n{x:+.1f}")
        self._label_y.setText(f"Y\n{y:+.1f}")
        self._label_z.setText(f"Z\n{z:.1f}")
        if self._tracking_status == "tracking" and 20.0 <= z <= 200.0:
            self._live_tracking_distances.append(float(z))
        if not self._auto_tune_enabled or self._tracking_status != "tracking":
            return

        now_s = time.monotonic()
        tuned = self._auto_tuner.update(x, y, z, now_s)
        if now_s - self._last_auto_tune_write_s < 0.25:
            return
        self._last_auto_tune_write_s = now_s
        self._settings = dataclasses.replace(
            self._settings,
            head_dist_cm=tuned.head_dist_cm,
            smoothing_alpha=tuned.smoothing_alpha,
            deadzone_mm=tuned.deadzone_mm,
        )
        self._settings_writer.write(self._settings)

        for widget, value in (
            (getattr(self, "_head_dist_spin", None), tuned.head_dist_cm),
            (getattr(self, "_smoothing_slider", None), tuned.smoothing_alpha),
            (getattr(self, "_deadzone_slider", None), tuned.deadzone_mm),
        ):
            if widget is not None:
                widget.blockSignals(True)
                if isinstance(widget, QDoubleSpinBox):
                    widget.setValue(value)
                else:
                    lo = float(widget.property("_lo"))
                    step = float(widget.property("_step"))
                    widget.setValue(round((value - lo) / step))
                widget.blockSignals(False)
        if hasattr(self, "_auto_tune_status"):
            mode = "responsive" if tuned.speed_cm_s >= 8.0 else "stable"
            self._auto_tune_status.setText(
                f"Auto: {tuned.head_dist_cm:.0f} cm · {mode} · "
                f"dead-zone {tuned.deadzone_mm:.1f} mm"
            )

    def _on_frame(self, jpeg: bytes) -> None:
        pix = QPixmap()
        pix.loadFromData(jpeg, b"JPEG")
        self._camera_label.setPixmap(
            pix.scaled(
                self._camera_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _on_status(self, status: str) -> None:
        self._tracking_status = status
        text = _STATUS_TEXT.get(status, f"● {status.upper()}")
        color = _STATUS_COLOR.get(status, "#888")
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            f"color:{color};font-size:10px;font-weight:bold;"
        )
        if status != "error":
            self._status_label.setToolTip("")
        if hasattr(self, "_tracker_tile"):
            self._tracker_tile.setText(f"Tracker\n{text.replace('● ', '').replace('⟳ ', '').replace('✕ ', '')}")
        if status == "error":
            if self._thread:
                self._tracker_stop_pending = True
                self._thread.stop()
            self._overlay.stop_async()
            self._overlay_started = False
            if self._hidden_for_overlay:
                self._hidden_for_overlay = False
                self.showNormal()
            self._action_btn.setText(
                "Stopping…" if self._tracker_stop_pending else "▶ START TRACKING"
            )
            self._action_btn.setEnabled(not self._tracker_stop_pending)
            self._action_btn.setStyleSheet(
                f"background:{_ACCENT};color:#111;font-weight:900;"
                "font-size:13px;padding:12px;border:none;border-radius:8px;"
            )
            if hasattr(self, "_overlay_tile"):
                self._overlay_tile.setText("Overlay\nIdle")

    def _refresh_runtime_health(self) -> None:
        summary = self._read_overlay_summary()
        self._apply_runtime_health(summary)

    def _safe_refresh_runtime_health(self) -> None:
        try:
            self._refresh_runtime_health()
        except KeyboardInterrupt:
            self._shutdown_application()

    def _shutdown_application(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        close_all = getattr(app, "closeAllWindows", None)
        if callable(close_all):
            close_all()
        app.quit()

    def _read_overlay_summary(self) -> OverlayRuntimeSummary | None:
        overlay_log = _find_overlay_log(None)
        return _latest_overlay_summary(overlay_log) if overlay_log else None

    def _apply_runtime_health(self, summary: OverlayRuntimeSummary | None) -> None:
        # A fresh log line may belong to a just-finished diagnostic or an older
        # launcher instance. Never show it as current while this window is
        # explicitly stopped.
        if not self._overlay_started:
            self._shm_tile.setText("SHM\nIdle")
            self._depth_tile.setText("Depth\nIdle")
            self._capture_tile.setText("Capture\nIdle")
            self._update_target_feedback()
            return
        if summary is None:
            self._shm_tile.setText("SHM\nWaiting")
            self._depth_tile.setText("Depth\nWaiting")
            self._capture_tile.setText("Capture\nWaiting")
            return

        self._maybe_recover_overlay(summary)

        target_path = self._active_profile.executable_path.strip()
        if target_path and hasattr(self, "_profile_target_label"):
            target_name = Path(target_path).name
            target_capture_reasons = {
                "bound_target_wgc",
                "bound_target_duplication",
                # Compatibility with native builds that predate the explicit
                # target/desktop capture status contract.
                "bound_wgc",
            }
            if (
                summary.has_frame
                and summary.capture_state == "running"
                and summary.capture_reason in target_capture_reasons
            ):
                self._profile_target_label.setText(f"Captured: {target_name}")
                self._profile_target_label.setStyleSheet("color:#79d99b;font-size:10px;")
            elif Path(target_path).is_file():
                waiting_text = f"Waiting for game window: {target_name}"
                if summary.has_frame and summary.capture_reason == "desktop_fallback":
                    waiting_text += " (desktop preview active)"
                self._profile_target_label.setText(waiting_text)
                self._profile_target_label.setStyleSheet("color:#f0c15a;font-size:10px;")

        self._shm_tile.setText(f"SHM\n{summary.shm_status} {summary.shm_changes_per_sec}/s")
        depth_status = f"{summary.depth_hz} Hz"
        if summary.depth_hz < _DEPTH_HZ_WARN:
            depth_status = f"LOW {depth_status}"
        self._depth_tile.setText(f"Depth\n{depth_status}")
        if summary.capture_state == "unavailable":
            self._capture_tile.setText("Capture\nUnavailable")
            self._capture_tile.setToolTip(summary.capture_reason or "desktop capture unavailable")
        elif summary.capture_state in {"rebinding", "device_recovery"}:
            self._capture_tile.setText("Capture\nRecovering")
            self._capture_tile.setToolTip(summary.capture_reason or "native recovery in progress")
        else:
            self._capture_tile.setText(
                "Capture\nFrame OK" if summary.has_frame else "Capture\nNo frame"
            )
            self._capture_tile.setToolTip("")

        if summary.depth_hz < _DEPTH_HZ_WARN:
            self._comfort_status.setText(
                f"Depth LOW: {summary.depth_hz} Hz, keeping current preset"
            )
            return

        if summary.depth_hz >= _DEPTH_HZ_WARN and hasattr(self, "_comfort_status"):
            self._comfort_status.setText(
                f"Depth OK: {summary.depth_hz} Hz, SHM {summary.shm_changes_per_sec}/s"
            )

    def _tracker_is_running(self) -> bool:
        return bool(self._thread is not None and self._thread.isRunning())

    def _maybe_recover_overlay(self, summary: OverlayRuntimeSummary) -> None:
        if not self._overlay_started or not self._tracker_is_running():
            self._capture_loss_count = 0
            return

        if self._overlay.is_transitioning() is True:
            self._capture_loss_count = 0
            return

        if not self._overlay.is_running():
            self._restart_overlay_from_health("process exited")
            return

        if summary.capture_state in {"unavailable", "rebinding", "device_recovery"}:
            self._capture_loss_count = 0
            return

        if summary.has_frame:
            self._capture_loss_count = 0
            return

        self._capture_loss_count += 1
        if self._capture_loss_count >= _CAPTURE_LOSS_RESTART_THRESHOLD:
            self._restart_overlay_from_health("capture lost")

    def _restart_overlay_from_health(self, reason: str) -> None:
        self._capture_loss_count = 0
        self._overlay.restart_async(
            self._active_profile.executable_path,
            **self._overlay_target_kwargs(),
        )
        self._overlay_started = True
        if hasattr(self, "_overlay_tile"):
            self._overlay_tile.setText("Overlay\nRestarting")
        _log.warning("overlay restart queued after runtime health failure: %s", reason)

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
        inner = QWidget()
        inner.setObjectName("advancedTabRoot")
        inner.setStyleSheet(
            f"QWidget#advancedTabRoot{{background:{_ADVANCED_BG};color:#c8c8e8;}}"
            "QPushButton{min-height:24px;padding:4px 10px;}"
            "QComboBox,QDoubleSpinBox,QLineEdit{min-height:22px;}"
            "QSlider{min-height:20px;}"
        )
        inner.setMinimumWidth(_MIN_EXPANDED_W - 36)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        # Presets
        pg = QGroupBox("Presets")
        pg.setStyleSheet(_advanced_group_style())
        pl = QHBoxLayout(pg)
        pl.setContentsMargins(12, 14, 12, 12)
        pl.setSpacing(10)
        self._preset_combo = QComboBox()
        self._preset_combo.setMinimumWidth(180)
        self._preset_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._preset_combo.setEditable(True)
        self._refresh_presets()
        for label, slot in [("Save", self._on_preset_save),
                             ("Load", self._on_preset_load),
                             ("Delete", self._on_preset_delete)]:
            btn = QPushButton(label)
            btn.setMinimumWidth(88)
            btn.clicked.connect(slot)
            pl.addWidget(btn)
        pl.insertWidget(0, self._preset_combo)
        lay.addWidget(pg)

        # Diagnostics
        dg = QGroupBox("Diagnostics")
        dg.setStyleSheet(_advanced_group_style())
        dl = QVBoxLayout(dg)
        dl.setContentsMargins(12, 14, 12, 12)
        debug_btn = QPushButton("Open tracking quality monitor")
        debug_btn.setToolTip(
            "Shows live jitter, loss rate, reacquisition time, and parallax shift"
        )
        debug_btn.clicked.connect(self._open_debug_monitor)
        dl.addWidget(debug_btn)
        lay.addWidget(dg)

        # Shader
        sg = QGroupBox("Shader Tuning")
        sg.setStyleSheet(_advanced_group_style())
        sf = QFormLayout(sg)
        _configure_form_layout(sf)
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
        cg.setStyleSheet(_advanced_group_style())
        cf = QFormLayout(cg)
        _configure_form_layout(cf)
        self._screen_w_spin = QDoubleSpinBox()
        self._screen_w_spin.setMinimumWidth(120)
        self._screen_w_spin.setRange(0.0, 500.0)
        self._screen_w_spin.setDecimals(1)
        self._screen_w_spin.setSuffix(" cm")
        self._screen_w_spin.setValue(self._settings.screen_w_cm)
        self._screen_w_spin.valueChanged.connect(self._on_settings_change)
        self._screen_h_spin = QDoubleSpinBox()
        self._screen_h_spin.setMinimumWidth(120)
        self._screen_h_spin.setRange(0.0, 500.0)
        self._screen_h_spin.setDecimals(1)
        self._screen_h_spin.setSuffix(" cm")
        self._screen_h_spin.setValue(self._settings.screen_h_cm)
        self._screen_h_spin.valueChanged.connect(self._on_settings_change)
        detect_btn = QPushButton("Auto-detect screen size")
        detect_btn.clicked.connect(self._on_detect_screen)
        self._head_dist_spin = QDoubleSpinBox()
        self._head_dist_spin.setMinimumWidth(120)
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
        tg.setStyleSheet(_advanced_group_style())
        tf = QFormLayout(tg)
        _configure_form_layout(tf)
        self._fov_combo = QComboBox()
        self._fov_combo.setMinimumWidth(120)
        self._fov_combo.setEditable(True)
        for fov in ["60", "70", "78", "90", "100", "110", "120"]:
            self._fov_combo.addItem(f"{fov}\u00b0", float(fov))
        idx = self._fov_combo.findText(f"{int(self._settings.camera_fov_deg)}\u00b0")
        if idx >= 0:
            self._fov_combo.setCurrentIndex(idx)
        self._fov_combo.currentIndexChanged.connect(self._on_settings_change)
        tf.addRow("Camera FOV", self._fov_combo)
        self._ipd_spin = QDoubleSpinBox()
        self._ipd_spin.setMinimumWidth(120)
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
        self._camera_tilt_spin.setMinimumWidth(120)
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
        recal_btn.setMinimumWidth(110)
        recal_btn.setToolTip(
            "Reset the explicit camera-mount tilt correction to 0°"
        )
        recal_btn.clicked.connect(self._on_recalibrate_tilt)
        tilt_row_layout.addWidget(recal_btn)
        self._tilt_status = QLabel("")
        self._tilt_status.setStyleSheet("color:#4a4;font-size:10px;")
        tilt_row_layout.addWidget(self._tilt_status)
        tf.addRow("Camera tilt", tilt_row)
        lay.addWidget(tg)
        self._apply_auto_tune_control_state()
        lay.addStretch()

        save_cfg_btn = QPushButton("Save to config.yaml")
        save_cfg_btn.clicked.connect(self._on_save_config)
        lay.addWidget(save_cfg_btn)

        return _scrollable_tab(inner, _ADVANCED_BG)

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
            display_backend=self._settings.display_backend,
            depth_mode=int(self._depth_mode_combo.currentData() or 0),
            stereo_layout=self._settings.stereo_layout,
            eye_order=self._settings.eye_order,
            panel_width_px=self._settings.panel_width_px,
            panel_height_px=self._settings.panel_height_px,
            focus_plane_cm=self._settings.focus_plane_cm,
            tracking_mode=self._settings.tracking_mode,
        )

    def _on_settings_change(self, *_: object) -> None:
        self._settings = self._snapshot_settings()
        self._settings_writer.write(self._settings)

    def _apply_auto_tune_control_state(self) -> None:
        manual = not self._auto_tune_enabled
        for name in ("_head_dist_spin", "_smoothing_slider", "_deadzone_slider"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setEnabled(manual)

    def _on_auto_tune_toggle(self, checked: bool) -> None:
        self._auto_tune_enabled = bool(checked)
        self._auto_tuner = TrackingAutoTuner()
        self._apply_auto_tune_control_state()
        cfg = _load_yaml_mapping(self._config_path)
        _ensure_mapping_child(cfg, "tracking")["auto_tune"] = self._auto_tune_enabled
        try:
            with open(self._config_path, "w", encoding="utf-8") as handle:
                yaml.safe_dump(cfg, handle, default_flow_style=False, sort_keys=False)
        except OSError:
            _log.warning("could not save automatic tracking preference")
        if hasattr(self, "_auto_tune_status"):
            self._auto_tune_status.setText(
                "Auto tuning will calibrate when tracking starts"
                if checked else "Manual tracking controls enabled"
            )

    def _apply_comfort_preset(self, preset_id: str, reason: str = "manual") -> None:
        preset = _COMFORT_PRESETS.get(preset_id)
        if preset is None:
            return
        widgets = [
            self._strength_x_slider,
            self._strength_y_slider,
            self._virtual_depth_slider,
            self._focus_radius_slider,
            self._smoothing_slider,
            self._deadzone_slider,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self._set_slider_value(self._strength_x_slider, float(preset["strength_x"]))
            self._set_slider_value(self._strength_y_slider, float(preset["strength_y"]))
            self._set_slider_value(self._virtual_depth_slider, float(preset["virtual_depth_cm"]))
            self._depth_curve_combo.setCurrentIndex(int(preset["depth_curve"]))
            self._depth_gamma_spin.setValue(float(preset["depth_gamma"]))
            self._set_slider_value(self._focus_radius_slider, float(preset["focus_radius"]))
            self._set_slider_value(self._smoothing_slider, float(preset["smoothing_alpha"]))
            self._set_slider_value(self._deadzone_slider, float(preset["deadzone_mm"]))
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self._on_settings_change()
        self._on_save_config()
        label = str(preset["label"])
        if reason == "low_depth":
            self._comfort_status.setText(
                f"Safe preset applied automatically: depth < {_DEPTH_HZ_WARN} Hz"
            )
        else:
            self._comfort_status.setText(
                f"{label} preset applied: Y strength {self._settings.strength_y:.2f}"
            )

    def _on_camera_tilt_change(self, value: float) -> None:
        self._camera_tilt_deg = float(value)

    def _on_recalibrate_tilt(self) -> None:
        """Reset the explicit mount correction; never infer it from posture."""
        if not _save_tilt_to_config(self._config_path, 0.0):
            self._tilt_status.setText("Error: configuration could not be updated")
            return
        self._config.setdefault("tracking", {})["camera_tilt_deg"] = 0.0
        self._camera_tilt_spin.setValue(0.0)
        self._tilt_status.setText("Reset to 0° — adjust only for the physical camera mount")

    def _on_detect_screen(self) -> None:
        self._calib_status.setText("Detecting\u2026")
        w, h = detect_screen_cm()
        if w > 0 and h > 0:
            self._screen_w_spin.blockSignals(True)
            self._screen_h_spin.blockSignals(True)
            try:
                self._screen_w_spin.setValue(w)
                self._screen_h_spin.setValue(h)
            finally:
                self._screen_w_spin.blockSignals(False)
                self._screen_h_spin.blockSignals(False)
            self._on_settings_change()
            self._on_save_config()
            self._calib_status.setText(f"Detected: {w:.1f} \u00d7 {h:.1f} cm")
        else:
            self._calib_status.setText("Detection failed \u2014 enter manually")

    def _on_measure_head(self) -> None:
        """Measure on a worker so model loading and camera I/O never freeze Qt."""
        self._measure_btn.setEnabled(False)
        self._calib_status.setText("Measuring (hold still 3 s)\u2026")
        if self._tracking_status == "tracking" and len(self._live_tracking_distances) >= 5:
            self._on_head_measurement_finished(
                statistics.median(self._live_tracking_distances)
            )
            return
        ipd_mm = float(self._ipd_spin.value())
        camera_index = int(self._config.get("camera", {}).get("index", 0))

        def measure() -> None:
            try:
                result = measure_head_distance_or_none(
                    ipd_mm=ipd_mm,
                    camera_index=camera_index,
                    camera_fov_deg=float(self._settings.camera_fov_deg),
                )
            except Exception:  # noqa: BLE001
                _log.warning("head-distance measurement failed", exc_info=True)
                result = None
            self._head_measurement_finished.emit(result)

        threading.Thread(
            target=measure,
            name="g3d-head-calibration",
            daemon=True,
        ).start()

    def _on_head_measurement_finished(self, result: object) -> None:
        try:
            if not isinstance(result, (int, float)):
                self._calib_status.setText("Measurement failed \u2014 keeping current value")
                return
            dist = float(result)
            self._head_dist_spin.blockSignals(True)
            self._head_dist_spin.setValue(dist)
            self._head_dist_spin.blockSignals(False)
            self._on_settings_change()
            self._on_save_config()
            self._calib_status.setText(f"Measured: {dist:.1f} cm")
        finally:
            self._head_dist_spin.blockSignals(False)
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
        try:
            save_preset(self._config_path, name, dataclasses.asdict(s))
        except (OSError, PresetConfigError) as exc:
            self._comfort_status.setText(f"Configuration error: {exc}")
            return
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
            self._depth_mode_combo,
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
        depth_mode = _depth_mode_code(data.get("depth_performance_mode", data.get("depth_mode", 1)))
        depth_mode_idx = self._depth_mode_combo.findData(depth_mode)
        if depth_mode_idx >= 0:
            self._depth_mode_combo.setCurrentIndex(depth_mode_idx)
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
        if self._profile_store_error is not None:
            self._update_profile_mode_label()
            return
        s = self._snapshot_settings()
        try:
            cfg = _load_yaml_mapping(self._config_path, fallback=self._config)
            overlay = _ensure_mapping_child(cfg, "overlay")
            values = dataclasses.asdict(s)
            values.pop("display_backend", None)
            values.pop("depth_mode", None)
            for calibration_key in (
                "stereo_layout",
                "eye_order",
                "panel_width_px",
                "panel_height_px",
                "focus_plane_cm",
                "tracking_mode",
            ):
                values.pop(calibration_key, None)
            values["display_backend"] = self._display_backend_id
            values["depth_performance_mode"] = _depth_mode_name(s.depth_mode)
            overlay.update(values)
            tracking = _ensure_mapping_child(cfg, "tracking")
            tracking["camera_tilt_deg"] = self._camera_tilt_deg
            tracking["camera_fov_deg"] = s.camera_fov_deg
            tracking["ipd_cm"] = s.ipd_mm / 10.0
            display_calibration = _ensure_mapping_child(overlay, "display_calibration")
            display_calibration["ipd_mm"] = s.ipd_mm
            self._persist_game_profiles(base_config=cfg)
        except (ConfigMappingError, OSError, yaml.YAMLError):
            pass

    def _open_debug_monitor(self) -> None:
        proc = self._debug_monitor_proc
        if proc is not None and proc.poll() is None:
            return

        try:
            self._debug_monitor_proc = subprocess.Popen(
                [sys.executable, "-m", "tracker.debug_monitor"],
                cwd=str(Path(__file__).resolve().parent.parent),
            )
        except OSError as e:
            self._on_status("error")
            self._status_label.setToolTip(f"Could not launch debug monitor: {e}")

    def _run_diagnostics(self) -> None:
        try:
            subprocess.Popen(
                [sys.executable, "-m", "launcher.diagnostics", "--config", self._config_path],
                cwd=str(Path(__file__).resolve().parent.parent),
            )
        except OSError as e:
            self._on_status("error")
            self._status_label.setToolTip(f"Could not launch diagnostics: {e}")

    def _collect_support_bundle(self) -> None:
        try:
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "scripts.collect_support",
                    "--output-dir",
                    "support_bundle",
                    "--config",
                    self._config_path,
                    "--require-live-runtime",
                ],
                cwd=str(Path(__file__).resolve().parent.parent),
            )
        except OSError as e:
            self._on_status("error")
            self._status_label.setToolTip(f"Could not collect support bundle: {e}")

    def closeEvent(self, event: object) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        # The interpreter can exit as soon as this event is accepted. Reap the
        # detached native child synchronously so it cannot become an orphan.
        self._overlay.stop()
        proc = self._debug_monitor_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        self._settings_writer.close()
        event.accept()  # type: ignore[attr-defined]
