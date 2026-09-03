from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_each_cached_tile_carries_its_source_timestamp():
    source = _source("overlay/depth_infer.cpp")
    worker = source.split("    void worker_loop()", 1)[1].split(
        "    void cleanup()",
        1,
    )[0]

    update = worker.index("tile_source_ms[tile] = running_source.captured_ms")
    freshness = worker.index("EvaluateTileFreshness(")
    packing = worker.index("produced_upload.resize(")

    assert update < freshness < packing
    assert "std::vector<uint64_t>                tile_source_ms" in source


def test_aging_is_applied_only_during_composed_upload():
    source = _source("overlay/depth_infer.cpp")
    worker = source.split("    void worker_loop()", 1)[1].split(
        "    void cleanup()",
        1,
    )[0]

    assert "tile_contribution_scale[tile]" in worker
    assert "0.5f + (contrasted - 0.5f)" in worker
    postprocess = source.split("    bool postprocess(", 1)[1].split(
        "    // MAIN THREAD:",
        1,
    )[0]
    assert "tile_contribution_scale" not in postprocess


def test_expired_tile_is_neutralized_and_prior_smoothing_is_cleared():
    source = _source("overlay/depth_infer.cpp")
    worker = source.split("    void worker_loop()", 1)[1].split(
        "    void cleanup()",
        1,
    )[0]
    block = worker.split("if (!freshness.usable)", 1)[1].split(
        "continue;",
        1,
    )[0]

    assert "cached_tile_norm[tile].begin()" in block
    assert "0.5f" in block
    assert "prev_norm_tiles[tile].clear()" in block
    assert "tile_generation[tile] = 0" in block
    assert "tile_source_ms[tile] = 0" in block


def test_expired_generation_reenters_oldest_tile_scheduler():
    source = _source("overlay/depth_infer.cpp")
    scheduler = source.split("    int oldest_non_center_tile(", 1)[1].split(
        "    std::vector<int> select_tiles(",
        1,
    )[0]
    worker = source.split("    void worker_loop()", 1)[1].split(
        "    void cleanup()",
        1,
    )[0]

    assert "tile_generation[tile] = 0" in worker
    assert "generation < oldest" in scheduler


def test_tile_freshness_matches_native_parallax_age_window():
    tile = _source("overlay/depth_tile_freshness.h")
    health = _source("overlay/parallax_health.h")
    result = _source("overlay/depth_result_freshness.h")

    assert "full_strength_age_ms = 140" in tile
    assert "max_tile_age_ms = 750" in tile
    assert "AgeScale(inputs.depth_age_ms, 140, 750)" in health
    assert "max_source_age_ms = 750" in result


def test_stale_tile_telemetry_is_exposed_and_reset_per_session():
    source = _source("overlay/depth_infer.cpp")
    header = _source("overlay/depth_infer.h")

    assert "stale_depth_tile_neutralizations.fetch_add(" in source
    assert "stale_depth_tiles_neutralized() const" in header
    init = source.split("bool DepthInferencer::init(", 1)[1].split(
        "bool DepthInferencer::run(",
        1,
    )[0]
    assert "stale_depth_tile_neutralizations.store(" in init


def test_native_tile_freshness_suite_is_registered():
    cmake = _source("overlay/CMakeLists.txt")

    assert "depth_tile_freshness_tests.cpp" in cmake
    assert "NAME depth_tile_freshness_tests" in cmake


def test_documentation_distinguishes_whole_result_and_cached_tile_age():
    docs = _source("docs/DEPTH_TILE_FRESHNESS.md")

    assert "geometry from more than one desktop capture" in docs
    assert "linear attenuation toward neutral depth" in docs
    assert "existing result-publication gate remains authoritative" in docs
