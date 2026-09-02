from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_depth_source_identity_crosses_every_async_state():
    source = _source("overlay/depth_infer.cpp")

    for name in (
        "stage_sources",
        "pending_source",
        "running_source",
        "ready_source",
    ):
        assert name in source

    staged = source.index("stage_sources[stage_write]")
    pending = source.index("pending_source = source")
    running = source.index("running_source = pending_source")
    ready = source.index("ready_source = running_source")
    drained = source.index("drained_source = ready_source")

    assert staged < pending < running < ready < drained


def test_publication_gate_runs_before_depth_texture_upload():
    source = _source("overlay/depth_infer.cpp")
    run_once = source.split("    bool run_once(", 1)[1].split(
        "    // WORKER THREAD:",
        1,
    )[0]

    decision = run_once.index("result_freshness.consider(")
    upload = run_once.index("ctx->UpdateSubresource(")
    blend = run_once.index("blend_started = now")
    source_time = run_once.index("last_depth_source_ms.store(")

    assert decision < upload < blend
    assert decision < source_time


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


def test_documentation_matches_native_health_cutoff():
    docs = _source("docs/DEPTH_RESULT_FRESHNESS.md")
    health = _source("overlay/parallax_health.h")

    assert "exact 750 ms boundary is accepted" in docs
    assert "AgeScale(inputs.depth_age_ms, 140, 750)" in health
