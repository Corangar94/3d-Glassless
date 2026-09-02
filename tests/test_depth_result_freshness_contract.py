from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_depth_source_identity_crosses_every_async_state():
    source = _source("overlay/depth_infer.cpp")
    run_once = source.split("    bool run_once(", 1)[1].split(
        "    // WORKER THREAD:",
        1,
    )[0]
    worker = source.split("    void worker_loop()", 1)[1].split(
        "    void cleanup()",
        1,
    )[0]

    for name in (
        "stage_sources",
        "pending_source",
        "running_source",
        "ready_source",
    ):
        assert name in source

    # run_once first drains the prior completed generation, then stages and
    # queues the current captured frame.
    drained = run_once.index("drained_source = ready_source")
    staged = run_once.index("stage_sources[stage_write]")
    pending = run_once.index("pending_source = source")
    assert drained < staged < pending

    # The worker then transfers the same identity with its tensor and output.
    running = worker.index("running_source = pending_source")
    ready = worker.index("ready_source = running_source")
    assert running < ready


def test_publication_gate_runs_before_depth_texture_upload_and_commit_marker():
    source = _source("overlay/depth_infer.cpp")
    run_once = source.split("    bool run_once(", 1)[1].split(
        "    // WORKER THREAD:",
        1,
    )[0]

    decision = run_once.index("result_freshness.consider(")
    upload = run_once.index("ctx->UpdateSubresource(")
    blend = run_once.index("blend_started = now")
    source_time = run_once.index("last_depth_source_ms.store(")
    commit = run_once.index("publish_freshness_snapshot()")

    assert decision < upload < blend < commit
    assert decision < source_time < commit


def test_publication_counter_is_release_acquire_commit_marker():
    source = _source("overlay/depth_infer.cpp")
    publisher = source.split(
        "    void publish_freshness_snapshot()",
        1,
    )[1].split(
        "    void reset_temporal_depth_history_after_rejection()",
        1,
    )[0]
    accessor = source.split(
        "uint64_t DepthInferencer::depth_updates_published() const",
        1,
    )[1].split(
        "uint64_t DepthInferencer::stale_depth_results_dropped() const",
        1,
    )[0]

    source_metadata = publisher.index("last_depth_source_generation.store(")
    accepted_count = publisher.index("published_depth_updates.store(")
    assert source_metadata < accepted_count
    assert "std::memory_order_release" in publisher
    assert "std::memory_order_acquire" in accessor


def test_rejected_completion_resets_temporal_history_before_next_stage():
    source = _source("overlay/depth_infer.cpp")
    run_once = source.split("    bool run_once(", 1)[1].split(
        "    // WORKER THREAD:",
        1,
    )[0]

    decision = run_once.index("result_freshness.consider(")
    reset = run_once.index(
        "reset_temporal_depth_history_after_rejection()"
    )
    commit = run_once.index("publish_freshness_snapshot()")
    worker_gate = run_once.index("if (worker_busy) return true;")
    staging = run_once.index("stage_sources[stage_write]")

    assert decision < reset < commit < worker_gate < staging


def test_temporal_history_reset_is_in_place_and_clears_every_cache():
    source = _source("overlay/depth_infer.cpp")
    reset = source.split(
        "    void reset_temporal_depth_history_after_rejection()",
        1,
    )[1].split("    uint32_t resolve_performance_mode", 1)[0]

    for statement in (
        "prev_norm_f32.clear()",
        "for (auto& previous : prev_norm_tiles) previous.clear()",
        "std::fill(cached.begin(), cached.end(), 0.5f)",
        "std::fill(tile_generation.begin(), tile_generation.end(), 0)",
        "scheduler_cycle = 0",
        "completion_generation = 0",
        "global_range_valid = false",
        "contrast_state_valid = false",
        "percentile_scratch.clear()",
        "global_samples_scratch.clear()",
        "normalized_scratch.clear()",
        "motion_warp_scratch.clear()",
    ):
        assert statement in reset

    # Controlled rejection must not allocate replacement tile vectors on the
    # render thread. Existing capacities are reused via clear/fill.
    assert "assign(" not in reset

    # Publication history remains on the GPU and is not reset by a controlled
    # drop; only worker-side state touched by the rejected postprocess is cleared.
    assert "depth_tex" not in reset
    assert "depth_prev_tex" not in reset
    assert "blend_active" not in reset


def test_stale_result_does_not_relabel_upload_time_as_source_time():
    source = _source("overlay/depth_infer.cpp")
    age_method = source.split(
        "uint32_t DepthInferencer::depth_age_ms() const",
        1,
    )[1].split(
        "uint32_t DepthInferencer::depth_upload_age_ms() const",
        1,
    )[0]

    assert "last_depth_source_ms" in age_method
    assert "last_depth_upload_ms" not in age_method
    assert "SaturatingAgeU32" in age_method


def test_first_window_visibility_requires_accepted_publication():
    header = _source("overlay/depth_infer.h")

    assert "depth->depth_updates_published() > 0" in header
    assert "has_frame = false" in header


def test_native_freshness_suite_is_registered_with_ctest():
    cmake = _source("overlay/CMakeLists.txt")

    assert "depth_result_freshness_tests.cpp" in cmake
    assert "add_test(" in cmake
    assert "NAME depth_result_freshness_tests" in cmake


def test_documentation_matches_native_health_cutoff_and_history_reset():
    docs = _source("docs/DEPTH_RESULT_FRESHNESS.md")
    health = _source("overlay/parallax_health.h")

    assert "exact 750 ms boundary is accepted" in docs
    assert "clears that CPU-side temporal history" in docs
    assert "AgeScale(inputs.depth_age_ms, 140, 750)" in health
