"""Calibrate webcam intrinsics and camera-to-screen alignment."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

import cv2
import yaml

from tracker.calibration_runtime_sync import synchronize_runtime_projection
from tracker.camera_calibration import (
    calibrate_intrinsics,
    capture_checkerboard_observations,
    center_align_geometry,
    generated_checkerboard_image,
    load_checkerboard_observations,
    parse_pattern_size,
    update_config_camera_geometry,
)
from tracker.camera_geometry import CameraExtrinsics, CameraGeometry
from tracker.face_tracker import FaceTracker
from tracker.pose import monotonic_ms


def _load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, dict):
        raise ValueError("config top level must be a mapping")
    return loaded


def _intrinsics_command(args: argparse.Namespace) -> int:
    pattern = parse_pattern_size(args.pattern)
    if args.images:
        paths: list[Path] = []
        for expression in args.images:
            matches = sorted(Path().glob(expression))
            paths.extend(matches if matches else [Path(expression)])
        observations = load_checkerboard_observations(paths, pattern)
    else:
        observations = capture_checkerboard_observations(
            camera_index=args.camera_index,
            pattern_size=pattern,
            sample_count=args.samples,
            width=args.width,
            height=args.height,
            fps=args.fps,
            timeout_seconds=args.timeout,
            show_preview=not args.no_preview,
        )
    result = calibrate_intrinsics(
        observations,
        pattern_size=pattern,
        square_size_cm=args.square_mm / 10.0,
    )
    extrinsics = CameraExtrinsics.from_euler_and_translation(
        yaw_deg=args.mount_yaw_deg,
        pitch_deg=args.mount_pitch_deg,
        roll_deg=args.mount_roll_deg,
        translation_cm=(args.mount_x_cm, args.mount_y_cm, args.mount_z_cm),
    )
    geometry = CameraGeometry(
        intrinsics=result.intrinsics,
        extrinsics=extrinsics,
        mirror_x=not args.no_mirror_x,
    )
    update_config_camera_geometry(args.config, geometry, calibration_result=result)
    synchronize_runtime_projection(args.config, geometry)
    print(
        "Camera intrinsics saved:",
        f"{result.intrinsics.width}x{result.intrinsics.height}",
        f"fx={result.intrinsics.fx:.2f}",
        f"fy={result.intrinsics.fy:.2f}",
        f"mean_error={result.mean_reprojection_error_px:.3f}px",
        f"views={result.views_used}",
    )
    return 0


def _board_command(args: argparse.Namespace) -> int:
    pattern = parse_pattern_size(args.pattern)
    image = generated_checkerboard_image(
        pattern_size=pattern,
        square_pixels=args.square_pixels,
        margin_pixels=args.margin_pixels,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), image):
        raise RuntimeError(f"could not write {args.output}")
    print(
        f"Wrote {args.output}. Print without scaling. "
        f"The physical square size supplied during calibration must match the print."
    )
    return 0


def _center_command(args: argparse.Namespace) -> int:
    config = _load_config(args.config)
    camera = config.get("camera", {}) if isinstance(config.get("camera"), dict) else {}
    tracking = config.get("tracking", {}) if isinstance(config.get("tracking"), dict) else {}
    width = int(args.width or camera.get("width", 1280))
    height = int(args.height or camera.get("height", 720))
    fps = float(args.fps or camera.get("fps", 30.0))
    geometry = CameraGeometry.from_config(
        config,
        fallback_width=width,
        fallback_height=height,
    )
    if geometry is None or geometry.intrinsics is None:
        raise RuntimeError("run the intrinsics calibration before center alignment")
    ipd_cm = float(args.ipd_cm or tracking.get("ipd_cm", 6.4))
    fov = float(tracking.get("camera_fov_deg", 90.0))
    cap = cv2.VideoCapture(args.camera_index, cv2.CAP_MSMF)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        raise RuntimeError(f"could not open camera {args.camera_index}")
    for property_id, value in (
        (cv2.CAP_PROP_FRAME_WIDTH, width),
        (cv2.CAP_PROP_FRAME_HEIGHT, height),
        (cv2.CAP_PROP_FPS, fps),
        (getattr(cv2, "CAP_PROP_BUFFERSIZE", -1), 1),
    ):
        if property_id >= 0:
            cap.set(property_id, float(value))
    samples: list[tuple[float, float, float]] = []
    started = time.monotonic()
    print(
        "Sit in the normal viewing position, look at the screen center, and remain still."
    )
    try:
        with FaceTracker(
            real_ipd_cm=ipd_cm,
            screen_width_cm=float(config.get("screen", {}).get("width_cm", 60.0)),
            screen_height_cm=float(config.get("screen", {}).get("height_cm", 34.0)),
            camera_fov_deg=fov,
            async_mode=False,
            camera_geometry=CameraGeometry(
                intrinsics=geometry.intrinsics,
                extrinsics=CameraExtrinsics.from_euler_and_translation(
                    yaw_deg=args.mount_yaw_deg,
                    pitch_deg=args.mount_pitch_deg,
                    roll_deg=args.mount_roll_deg,
                ),
                mirror_x=geometry.mirror_x,
            ),
        ) as tracker:
            while len(samples) < args.samples and time.monotonic() - started < args.timeout:
                ok, frame = cap.read()
                if not ok:
                    continue
                pose = tracker.process_frame(frame, capture_timestamp_ms=monotonic_ms())
                if pose is not None and pose.confidence >= args.minimum_confidence:
                    samples.append(pose.xyz)
                    print(
                        f"\rcenter samples {len(samples)}/{args.samples}",
                        end="",
                        flush=True,
                    )
    finally:
        cap.release()
    print()
    if len(samples) < max(10, args.samples // 2):
        raise RuntimeError(f"only collected {len(samples)} reliable center-pose samples")
    base = CameraGeometry(
        intrinsics=geometry.intrinsics,
        extrinsics=CameraExtrinsics.from_euler_and_translation(
            yaw_deg=args.mount_yaw_deg,
            pitch_deg=args.mount_pitch_deg,
            roll_deg=args.mount_roll_deg,
        ),
        mirror_x=geometry.mirror_x,
    )
    aligned = center_align_geometry(
        base,
        samples,
        viewer_distance_cm=args.viewer_distance_cm,
    )
    update_config_camera_geometry(args.config, aligned)
    synchronize_runtime_projection(
        args.config,
        aligned,
        viewer_distance_cm=args.viewer_distance_cm,
    )
    print(
        "Camera-to-screen alignment saved:",
        aligned.extrinsics.translation_camera_origin_cm,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate Glassless3D webcam geometry"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    board = subparsers.add_parser("board", help="generate a printable checkerboard")
    board.add_argument("output", type=Path)
    board.add_argument("--pattern", default="9x6", help="inner corners, e.g. 9x6")
    board.add_argument("--square-pixels", type=int, default=120)
    board.add_argument("--margin-pixels", type=int, default=120)
    board.set_defaults(func=_board_command)

    intrinsics = subparsers.add_parser(
        "intrinsics", help="calibrate lens and focal parameters"
    )
    intrinsics.add_argument("--config", type=Path, default=Path("config.yaml"))
    intrinsics.add_argument("--images", nargs="*", help="image paths or globs; omit for live capture")
    intrinsics.add_argument("--camera-index", type=int, default=0)
    intrinsics.add_argument("--width", type=int, default=1280)
    intrinsics.add_argument("--height", type=int, default=720)
    intrinsics.add_argument("--fps", type=float, default=30.0)
    intrinsics.add_argument("--samples", type=int, default=18)
    intrinsics.add_argument("--timeout", type=float, default=90.0)
    intrinsics.add_argument("--pattern", default="9x6")
    intrinsics.add_argument("--square-mm", type=float, required=True)
    intrinsics.add_argument("--mount-x-cm", type=float, default=0.0)
    intrinsics.add_argument("--mount-y-cm", type=float, default=0.0)
    intrinsics.add_argument("--mount-z-cm", type=float, default=0.0)
    intrinsics.add_argument("--mount-yaw-deg", type=float, default=0.0)
    intrinsics.add_argument("--mount-pitch-deg", type=float, default=0.0)
    intrinsics.add_argument("--mount-roll-deg", type=float, default=0.0)
    intrinsics.add_argument("--no-mirror-x", action="store_true")
    intrinsics.add_argument("--no-preview", action="store_true")
    intrinsics.set_defaults(func=_intrinsics_command)

    center = subparsers.add_parser(
        "center", help="align the calibrated camera origin to the screen center"
    )
    center.add_argument("--config", type=Path, default=Path("config.yaml"))
    center.add_argument("--camera-index", type=int, default=0)
    center.add_argument("--width", type=int)
    center.add_argument("--height", type=int)
    center.add_argument("--fps", type=float)
    center.add_argument("--samples", type=int, default=40)
    center.add_argument("--timeout", type=float, default=45.0)
    center.add_argument("--minimum-confidence", type=float, default=0.6)
    center.add_argument("--viewer-distance-cm", type=float, required=True)
    center.add_argument("--ipd-cm", type=float)
    center.add_argument("--mount-yaw-deg", type=float, default=0.0)
    center.add_argument("--mount-pitch-deg", type=float, default=0.0)
    center.add_argument("--mount-roll-deg", type=float, default=0.0)
    center.set_defaults(func=_center_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Calibration cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"Calibration failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
