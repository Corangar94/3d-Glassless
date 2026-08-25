from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_validated_rectangular_profiles_are_used_by_native_inference():
    source = _source("overlay/depth_infer.cpp")

    assert "profile.width = 392" in source
    assert "profile.height = 224" in source
    assert "profile.width = 518" in source
    assert "profile.height = 294" in source
    assert "profile.width = 686" in source
    assert "profile.height = 392" in source
    assert "std::array<int64_t, 4> shape" in source
    assert "running_profile.height, running_profile.width" in source


def test_adaptive_scheduler_uses_newest_frame_without_backlog():
    source = _source("overlay/depth_infer.cpp")

    assert "adaptive_interval_ms" in source
    assert "last_inference_ms" in source
    assert "last_submit" in source
    assert "select_tiles" in source
    assert "oldest_non_center_tile" in source
    assert "pending_tiles = selected" in source
    assert "worker_busy = input_pending || worker_running" in source


def test_ultrawide_scheduler_caches_unscheduled_tile_depth():
    source = _source("overlay/depth_infer.cpp")

    assert "cached_tile_norm" in source
    assert "tile_generation" in source
    assert "tile_generation[tile] = ++completion_generation" in source
    assert "tile_generation[tile] == 0" in source


def test_depth_history_is_motion_compensated_before_temporal_filtering():
    source = _source("overlay/depth_infer.cpp")

    assert "warp_previous_depth" in source
    assert "motion_warp_scratch" in source
    assert "best_dx" in source
    assert "best_dy" in source
    assert "motion_error" in source


def test_depth_normalization_and_contrast_are_temporally_stabilized():
    source = _source("overlay/depth_infer.cpp")

    assert "smoothed_global_lo" in source
    assert "smoothed_global_hi" in source
    assert "range_alpha" in source
    assert "smoothed_contrast_mean" in source
    assert "smoothed_contrast_gain" in source
    assert "contrast_alpha" in source


def test_depth_crossfade_tracks_measured_inference_arrival_interval():
    source = _source("overlay/depth_infer.cpp")

    assert "last_depth_arrival" in source
    assert "blend_duration_sec" in source
    assert "interval * 0.90f" in source
    assert "impl_->blend_duration_sec" in source


def test_max_profile_resources_are_reused_across_modes():
    source = _source("overlay/depth_infer.cpp")

    assert "kMaxModelWidth = 686" in source
    assert "kMaxModelHeight = 392" in source
    assert "sd.Width = kMaxModelWidth * tile_count" in source
    assert "sd.Height = kMaxModelHeight" in source
    assert "render_compact(captured, requested)" in source
