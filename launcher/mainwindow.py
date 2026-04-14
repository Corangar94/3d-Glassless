"""Always-on-top two-mode tracker window."""
from __future__ import annotations

from typing import Optional

import yaml
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher.tracker_thread import TrackerThread

# Window dimensions
_EXPANDED_W, _EXPANDED_H = 270, 310
_COMPACT_W, _COMPACT_H = 400, 100

_STATUS_TEXT = {
    "tracking": "● TRACKING",
    "hold":     "● HOLD",
    "paused":   "● PAUSED",
    "stopped":  "● STOPPED",
    "error":    "✕ NO CAMERA",
}
_STATUS_COLOR = {
    "tracking": "#28c840",
    "hold":     "#febc2e",
    "paused":   "#888888",
    "stopped":  "#888888",
    "error":    "#e84040",
}
_DARK_BG = "#0d0d22"
_TITLE_BG = "#1a1a2e"


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
        self._drag_pos: Optional[QPoint] = None

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
        self._camera_label = QLabel()
        self._camera_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_label.setStyleSheet("background:#0a0a0a;")
        self._root_layout.addWidget(self._camera_label)
        self._root_layout.addLayout(self._make_xyz_row())
        self._root_layout.addWidget(self._make_action_button())

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

        layout.addWidget(logo)
        layout.addStretch()
        layout.addWidget(self._status_label)
        layout.addWidget(self._toggle_btn)
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
            with open(self._config_path) as f:
                cfg = yaml.safe_load(f)
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
        cam_idx = self._config["camera"]["index"]
        thread = TrackerThread(camera_index=cam_idx, config=self._config)
        thread.position_updated.connect(self._on_position)
        thread.frame_ready.connect(self._on_frame)
        thread.status_changed.connect(self._on_status)
        thread.start()
        self._thread = thread
        self._action_btn.setText("■ STOP TRACKING")
        self._action_btn.setStyleSheet(
            "background:#e84040;color:#fff;font-weight:bold;"
            "font-size:11px;padding:8px;border:none;"
        )

    def _stop_tracking(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread = None
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

    def closeEvent(self, event: object) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        event.accept()  # type: ignore[attr-defined]
