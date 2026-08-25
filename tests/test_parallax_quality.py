import json

import cv2
import numpy as np
import pytest

from scripts import software_acceptance
from tracker.parallax_quality import (
    ParallaxSettings,
    evaluate_parallax_gate,
    focus_depth,
    inverse_lookup_uv,
    parallax_shift_uv,
    render_validation_frame,
    write_validation_sequence,
)


def test_focus_plane_is_neutral_and_depth_order_is_monotonic():
    settings = ParallaxSettings(
        virtual_depth_cm=30.0,
        focus_plane_cm=10.0,
    )
    focus = focus_depth(settings)
    shallow = parallax_shift_uv(
        focus * 0.25,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )[0]
    neutral = parallax_shift_uv(
        focus,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )[0]
    deep = parallax_shift_uv(
        0.9,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )[0]

    assert shallow < 0.0
    assert neutral == pytest.approx(0.0, abs=1e-9)
    assert deep > 0.0
    assert shallow < neutral < deep


def test_inverse_lookup_moves_source_opposite_the_projected_virtual_point():
    settings = ParallaxSettings(focus_plane_cm=0.0)
    shift = parallax_shift_uv(
        0.9,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )
    lookup = inverse_lookup_uv(
        (0.5, 0.5),
        0.9,
        head_x_cm=8.0,
        head_y_cm=0.0,
        settings=settings,
    )

    assert shift[0] > 0.0
    assert lookup[0] == pytest.approx(0.5 - shift[0])


def test_reference_parallax_gate_passes():
    result = evaluate_parallax_gate()

    assert result.passed, result.failures
    assert result.shallow_shift_x < 0.0
    assert result.focus_shift_x == pytest.approx(0.0, abs=1e-7)
    assert result.deep_shift_x > 0.0
    assert result.maximum_shift_uv < 0.30


def test_validation_frame_contains_distinct_layers():
    frame = render_validation_frame(
        width=640,
        height=360,
        head_x_cm=8.0,
    )

    assert frame.shape == (360, 640, 3)
    assert frame.dtype == np.uint8
    assert len(np.unique(frame.reshape(-1, 3), axis=0)) > 20
    assert float(np.std(frame)) > 20.0


def test_validation_sequence_writes_deterministic_pngs(tmp_path):
    outputs = write_validation_sequence(
        tmp_path,
        head_positions_cm=((-8.0, 0.0), (0.0, 0.0), (8.0, 0.0)),
        width=640,
        height=360,
    )

    assert len(outputs) == 3
    assert all(path.exists() for path in outputs)
    frames = [cv2.imread(str(path), cv2.IMREAD_COLOR) for path in outputs]
    assert all(frame is not None for frame in frames)
    assert not np.array_equal(frames[0], frames[2])


def test_consolidated_software_acceptance_writes_reports_and_demo(tmp_path):
    output_dir = tmp_path / "acceptance"

    exit_code = software_acceptance.main(
        [
            "--output-dir",
            str(output_dir),
            "--generate-demo",
            "--fail-on-regression",
        ]
    )

    assert exit_code == 0
    combined = json.loads(
        (output_dir / "software_acceptance.json").read_text(encoding="utf-8")
    )
    assert combined["passed"] is True
    assert (output_dir / "software_acceptance.md").exists()
    assert len(list((output_dir / "virtual_window_demo").glob("*.png"))) == 5
