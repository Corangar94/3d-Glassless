"""Deterministic software-only replay quality and filter tuning.

The benchmark models camera capture, inference/delivery latency, measurement
noise, low-confidence outliers, dropouts, and a high-rate display loop. It is
not a substitute for physical acceptance; it is a reproducible regression gate
for the tracker state estimator and its camera-time prediction behavior.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from tracker.pose import FilteredPose, HeadPosition
from tracker.pose_filter import AdaptivePoseFilter


@dataclass(frozen=True)
class FilterSettings:
    process_noise: float = 2.0
    measurement_noise: float = 0.1
    prediction_horizon_ms: float = 0.0
    max_prediction_ms: float = 80.0


@dataclass(frozen=True)
class ScenarioSpec:
    name: str
    motion: str
    duration_s: float = 8.0
    camera_hz: float = 30.0
    display_hz: float = 120.0
    delivery_latency_ms: float = 32.0
    delivery_jitter_ms: float = 5.0
    noise_xy_cm: float = 0.18
    noise_z_cm: float = 0.35
    dropout_ranges_s: tuple[tuple[float, float], ...] = ()
    outlier_period: int = 67
    seed: int = 20260825


@dataclass(frozen=True)
class MeasurementEvent:
    delivery_timestamp_ms: int
    pose: HeadPosition


@dataclass(frozen=True)
class SeriesMetrics:
    position_rmse_cm: float
    position_p95_cm: float
    xy_rmse_cm: float
    z_rmse_cm: float
    x_lag_ms: float
    x_jitter_cm: float
    max_position_error_cm: float


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    filtered: SeriesMetrics
    raw_hold: SeriesMetrics
    improvement_ratio: float
    display_samples: int
    delivered_measurements: int


@dataclass(frozen=True)
class ReplayReport:
    settings: FilterSettings
    scenarios: tuple[ScenarioResult, ...]
    weighted_score: float
    passed: bool
    failures: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "settings": asdict(self.settings),
            "scenarios": [
                {
                    "name": result.name,
                    "filtered": asdict(result.filtered),
                    "raw_hold": asdict(result.raw_hold),
                    "improvement_ratio": result.improvement_ratio,
                    "display_samples": result.display_samples,
                    "delivered_measurements": result.delivered_measurements,
                }
                for result in self.scenarios
            ],
            "weighted_score": self.weighted_score,
            "passed": self.passed,
            "failures": list(self.failures),
        }

    def write_json(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.to_mapping(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def write_markdown(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            "# Glassless3D software replay report",
            "",
            f"- Result: **{'PASS' if self.passed else 'FAIL'}**",
            f"- Weighted filtered/raw score: `{self.weighted_score:.3f}`",
            (
                "- Settings: "
                f"`q={self.settings.process_noise:g}`, "
                f"`r={self.settings.measurement_noise:g}`, "
                f"`horizon={self.settings.prediction_horizon_ms:g} ms`, "
                f"`max_prediction={self.settings.max_prediction_ms:g} ms`"
            ),
            "",
            "| Scenario | Filter RMSE | Raw RMSE | Ratio | P95 | Lag | Jitter | Max error |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for result in self.scenarios:
            rows.append(
                "| "
                f"{result.name} | {result.filtered.position_rmse_cm:.3f} cm | "
                f"{result.raw_hold.position_rmse_cm:.3f} cm | "
                f"{result.improvement_ratio:.3f} | "
                f"{result.filtered.position_p95_cm:.3f} cm | "
                f"{result.filtered.x_lag_ms:.1f} ms | "
                f"{result.filtered.x_jitter_cm:.3f} cm | "
                f"{result.filtered.max_position_error_cm:.3f} cm |"
            )
        if self.failures:
            rows.extend(("", "## Gate failures", ""))
            rows.extend(f"- {failure}" for failure in self.failures)
        destination.write_text("\n".join(rows) + "\n", encoding="utf-8", newline="\n")


DEFAULT_SCENARIOS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec(name="smooth_lateral", motion="smooth"),
    ScenarioSpec(name="direction_reversal", motion="reversal"),
    ScenarioSpec(
        name="dropout_recovery",
        motion="smooth",
        dropout_ranges_s=((2.40, 3.00), (5.10, 5.50)),
    ),
    ScenarioSpec(
        name="stationary_jitter",
        motion="stationary",
        delivery_latency_ms=28.0,
        delivery_jitter_ms=4.0,
    ),
)


def truth_pose(motion: str, timestamp_s: float) -> np.ndarray:
    """Return deterministic x/y/z ground truth in centimeters."""
    if motion == "stationary":
        return np.array((0.0, 0.0, 60.0), dtype=np.float64)
    if motion == "reversal":
        return np.array(
            (
                7.0 * math.sin(2.0 * math.pi * 0.55 * timestamp_s),
                2.5 * math.sin(2.0 * math.pi * 0.18 * timestamp_s + 0.4),
                60.0 + 1.5 * math.sin(2.0 * math.pi * 0.22 * timestamp_s),
            ),
            dtype=np.float64,
        )
    if motion == "smooth":
        return np.array(
            (
                8.0 * math.sin(2.0 * math.pi * 0.32 * timestamp_s),
                3.0 * math.sin(2.0 * math.pi * 0.21 * timestamp_s + 0.6),
                60.0 + 2.0 * math.sin(2.0 * math.pi * 0.17 * timestamp_s + 0.2),
            ),
            dtype=np.float64,
        )
    raise ValueError(f"unknown replay motion: {motion}")


def _in_dropout(spec: ScenarioSpec, timestamp_s: float) -> bool:
    return any(start <= timestamp_s <= end for start, end in spec.dropout_ranges_s)


def generate_measurements(spec: ScenarioSpec) -> tuple[MeasurementEvent, ...]:
    """Generate deterministic noisy, delayed measurements for one scenario."""
    rng = np.random.default_rng(spec.seed)
    events: list[MeasurementEvent] = []
    count = int(math.floor(spec.duration_s * spec.camera_hz)) + 1
    for index in range(count):
        capture_s = index / spec.camera_hz
        if capture_s > spec.duration_s or _in_dropout(spec, capture_s):
            continue
        truth = truth_pose(spec.motion, capture_s)
        measured = truth + rng.normal(
            0.0,
            (spec.noise_xy_cm, spec.noise_xy_cm, spec.noise_z_cm),
        )
        confidence = 0.92
        if spec.outlier_period > 0 and index > 0 and index % spec.outlier_period == 0:
            measured += rng.normal(0.0, (2.5, 2.5, 3.5))
            confidence = 0.15
        latency = max(
            0.0,
            spec.delivery_latency_ms + rng.normal(0.0, spec.delivery_jitter_ms),
        )
        capture_ms = int(round(capture_s * 1000.0)) & 0xFFFF_FFFF
        delivery_ms = int(round(capture_s * 1000.0 + latency))
        events.append(
            MeasurementEvent(
                delivery_timestamp_ms=delivery_ms,
                pose=HeadPosition(
                    x_cm=float(measured[0]),
                    y_cm=float(measured[1]),
                    z_cm=float(measured[2]),
                    yaw_deg=float(8.0 * math.sin(2.0 * math.pi * 0.24 * capture_s)),
                    confidence=confidence,
                    capture_timestamp_ms=capture_ms,
                ),
            )
        )
    events.sort(key=lambda event: (event.delivery_timestamp_ms, event.pose.capture_timestamp_ms))
    return tuple(events)


def _estimate_lag_ms(
    timestamps_ms: np.ndarray,
    output_x: np.ndarray,
    truth_x: np.ndarray,
    *,
    maximum_lag_ms: float = 160.0,
) -> float:
    if len(timestamps_ms) < 4 or np.ptp(truth_x) < 1e-6:
        return 0.0
    step_ms = float(np.median(np.diff(timestamps_ms)))
    maximum_shift = max(1, int(round(maximum_lag_ms / max(step_ms, 1e-6))))
    best_error = float("inf")
    best_shift = 0
    for shift in range(-maximum_shift, maximum_shift + 1):
        if shift > 0:
            candidate = output_x[shift:]
            reference = truth_x[:-shift]
        elif shift < 0:
            candidate = output_x[:shift]
            reference = truth_x[-shift:]
        else:
            candidate = output_x
            reference = truth_x
        if len(candidate) < 4:
            continue
        error = float(np.mean(np.square(candidate - reference)))
        if error < best_error:
            best_error = error
            best_shift = shift
    return best_shift * step_ms


def series_metrics(
    timestamps_ms: np.ndarray,
    output: np.ndarray,
    truth: np.ndarray,
) -> SeriesMetrics:
    delta = output - truth
    position_error = np.linalg.norm(delta, axis=1)
    return SeriesMetrics(
        position_rmse_cm=float(np.sqrt(np.mean(np.square(position_error)))),
        position_p95_cm=float(np.quantile(position_error, 0.95)),
        xy_rmse_cm=float(np.sqrt(np.mean(np.sum(np.square(delta[:, :2]), axis=1)))),
        z_rmse_cm=float(np.sqrt(np.mean(np.square(delta[:, 2])))),
        x_lag_ms=float(
            _estimate_lag_ms(timestamps_ms, output[:, 0], truth[:, 0])
        ),
        x_jitter_cm=float(np.std(delta[:, 0])),
        max_position_error_cm=float(np.max(position_error)),
    )


def replay_scenario(
    spec: ScenarioSpec,
    settings: FilterSettings,
) -> ScenarioResult:
    filter_ = AdaptivePoseFilter(
        process_noise=settings.process_noise,
        measurement_noise=settings.measurement_noise,
        prediction_horizon_ms=settings.prediction_horizon_ms,
        max_prediction_ms=settings.max_prediction_ms,
    )
    events = generate_measurements(spec)
    event_index = 0
    latest_measurement = np.array((0.0, 0.0, 60.0), dtype=np.float64)
    have_measurement = False
    timestamps: list[int] = []
    filtered_values: list[tuple[float, float, float]] = []
    raw_values: list[np.ndarray] = []
    truth_values: list[np.ndarray] = []
    display_samples = int(math.floor(spec.duration_s * spec.display_hz)) + 1
    last_capture_timestamp = -1
    delivered_measurements = 0

    for index in range(display_samples):
        timestamp_ms = int(round(index * 1000.0 / spec.display_hz))
        output: FilteredPose | None = None
        while (
            event_index < len(events)
            and events[event_index].delivery_timestamp_ms <= timestamp_ms
        ):
            event = events[event_index]
            event_index += 1
            capture_timestamp = int(event.pose.capture_timestamp_ms)
            # LIVE_STREAM callbacks are monotonic. Discard a delayed result that
            # would move the state estimator backward in camera time.
            if capture_timestamp <= last_capture_timestamp:
                continue
            last_capture_timestamp = capture_timestamp
            latest_measurement = np.array(event.pose.xyz, dtype=np.float64)
            have_measurement = True
            delivered_measurements += 1
            output = filter_.update_pose(
                event.pose,
                publish_timestamp_ms=timestamp_ms,
            )
        if output is None:
            output = filter_.predict(publish_timestamp_ms=timestamp_ms)
        timestamps.append(timestamp_ms)
        filtered_values.append(output.xyz)
        raw_values.append(
            latest_measurement.copy()
            if have_measurement
            else np.array((0.0, 0.0, 60.0), dtype=np.float64)
        )
        truth_values.append(truth_pose(spec.motion, timestamp_ms / 1000.0))

    timestamp_array = np.asarray(timestamps, dtype=np.float64)
    filtered_array = np.asarray(filtered_values, dtype=np.float64)
    raw_array = np.asarray(raw_values, dtype=np.float64)
    truth_array = np.asarray(truth_values, dtype=np.float64)
    filtered_metrics = series_metrics(timestamp_array, filtered_array, truth_array)
    raw_metrics = series_metrics(timestamp_array, raw_array, truth_array)
    ratio = filtered_metrics.position_rmse_cm / max(
        1e-9, raw_metrics.position_rmse_cm
    )
    return ScenarioResult(
        name=spec.name,
        filtered=filtered_metrics,
        raw_hold=raw_metrics,
        improvement_ratio=float(ratio),
        display_samples=display_samples,
        delivered_measurements=delivered_measurements,
    )


def evaluate_gate(results: Sequence[ScenarioResult]) -> tuple[bool, tuple[str, ...]]:
    failures: list[str] = []
    by_name = {result.name: result for result in results}
    for result in results:
        if result.improvement_ratio > 1.02:
            failures.append(
                f"{result.name}: filtered RMSE is worse than raw hold "
                f"({result.improvement_ratio:.3f}x)"
            )
        if abs(result.filtered.x_lag_ms) > 40.0:
            failures.append(
                f"{result.name}: absolute lateral lag exceeds 40 ms "
                f"({result.filtered.x_lag_ms:.1f} ms)"
            )
    stationary = by_name.get("stationary_jitter")
    if stationary is not None:
        if stationary.filtered.x_jitter_cm > 0.45:
            failures.append(
                "stationary_jitter: filtered lateral jitter exceeds 0.45 cm "
                f"({stationary.filtered.x_jitter_cm:.3f} cm)"
            )
        if stationary.improvement_ratio > 0.90:
            failures.append(
                "stationary_jitter: filtering does not improve stationary noise "
                f"enough ({stationary.improvement_ratio:.3f}x)"
            )
    dropout = by_name.get("dropout_recovery")
    if dropout is not None and dropout.filtered.max_position_error_cm > 7.0:
        failures.append(
            "dropout_recovery: maximum position error exceeds 7 cm "
            f"({dropout.filtered.max_position_error_cm:.3f} cm)"
        )
    return not failures, tuple(failures)


def benchmark(
    settings: FilterSettings = FilterSettings(),
    scenarios: Iterable[ScenarioSpec] = DEFAULT_SCENARIOS,
) -> ReplayReport:
    results = tuple(replay_scenario(spec, settings) for spec in scenarios)
    weighted_score = float(
        sum(result.improvement_ratio for result in results) / max(1, len(results))
    )
    passed, failures = evaluate_gate(results)
    return ReplayReport(
        settings=settings,
        scenarios=results,
        weighted_score=weighted_score,
        passed=passed,
        failures=failures,
    )


def tune(
    scenarios: Iterable[ScenarioSpec] = DEFAULT_SCENARIOS,
    *,
    process_noise_values: Sequence[float] = (0.5, 1.0, 2.0, 3.0, 4.0),
    measurement_noise_values: Sequence[float] = (0.05, 0.1, 0.2),
    prediction_horizon_values_ms: Sequence[float] = (0.0, 5.0, 10.0, 15.0),
    max_prediction_ms: float = 80.0,
) -> ReplayReport:
    scenario_tuple = tuple(scenarios)
    best: ReplayReport | None = None
    for process_noise in process_noise_values:
        for measurement_noise in measurement_noise_values:
            for horizon in prediction_horizon_values_ms:
                candidate = benchmark(
                    FilterSettings(
                        process_noise=process_noise,
                        measurement_noise=measurement_noise,
                        prediction_horizon_ms=horizon,
                        max_prediction_ms=max_prediction_ms,
                    ),
                    scenario_tuple,
                )
                if best is None or candidate.weighted_score < best.weighted_score:
                    best = candidate
    if best is None:
        raise RuntimeError("replay tuning grid is empty")
    return best
