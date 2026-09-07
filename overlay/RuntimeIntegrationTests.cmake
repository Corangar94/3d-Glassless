# Compile the actual production control paths with deterministic GPU/worker
# boundaries. Source edits reconfigure these includes; no copied implementation
# can silently diverge from the code shipped in the overlay.
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
    "${CMAKE_CURRENT_SOURCE_DIR}/depth_infer.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/overlay.cpp")
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/depth_infer.cpp" depth_source)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/overlay.cpp" overlay_source)
string(REPLACE "\r\n" "\n" depth_source "${depth_source}")
string(REPLACE "\r\n" "\n" overlay_source "${overlay_source}")
function(g3d_test_extract name source start_marker end_marker)
    string(FIND "${source}" "${start_marker}" first)
    string(FIND "${source}" "${start_marker}" last REVERSE)
    if(first LESS 0 OR NOT first EQUAL last)
        message(FATAL_ERROR "Missing or ambiguous runtime test start: ${name}")
    endif()
    string(SUBSTRING "${source}" ${first} -1 tail)
    string(FIND "${tail}" "${end_marker}" length)
    if(length LESS 1)
        message(FATAL_ERROR "Missing runtime test end: ${name}")
    endif()
    string(SUBSTRING "${tail}" 0 ${length} extracted)
    file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/${name}.generated.inl" "${extracted}")
endfunction()
g3d_test_extract(fp16 "${depth_source}"
    "static inline uint16_t float_to_half" "struct DepthInferImpl")
g3d_test_extract(schedule "${depth_source}"
    "    int oldest_tile(" "    static std::chrono::steady_clock::time_point clock_now()")
g3d_test_extract(pipeline "${depth_source}"
    "    bool run_once(" "    // WORKER THREAD:")

g3d_test_extract(tile_update "${depth_source}"
    "                    bool any_scene_cut = range_cut;" "                    double sum = 0.0, sum_sq = 0.0;")
g3d_test_extract(atlas "${depth_source}"
    "                    produced_upload.resize(" "                }\n            } catch (const Ort::Exception&")
g3d_test_extract(handoff "${depth_source}"
    "            {\n                std::lock_guard<std::mutex> lock(m);\n                worker_running = false;"
    "            if (ok) inferences.fetch_add")
g3d_test_extract(publisher "${depth_source}"
    "    void publish_freshness_snapshot()" "    void reset_temporal_depth_history_after_rejection()")
g3d_test_extract(history_reset "${depth_source}"
    "    void reset_temporal_depth_history_after_rejection()" "    uint32_t resolve_performance_mode(")
g3d_test_extract(visibility "${overlay_source}"
    "static void UpdateOverlayVisibility() {" "static void SetCaptureState(")
g3d_test_extract(mark_failure "${overlay_source}"
    "static void MarkDepthFailure() {" "static void DestroyCaptureResources()")
g3d_test_extract(depth_match "${overlay_source}"
    "static bool PublishedDepthMatchesHeldCapture()" "static void TickDepthRecovery()")
g3d_test_extract(recovery "${overlay_source}"
    "static void TickDepthRecovery() {" "static bool IsUnavailableDuplicationFailure(")
g3d_test_extract(ages "${depth_source}"
    "uint32_t DepthInferencer::depth_age_ms() const" "bool DepthInferencer::gpu_io_active() const")
g3d_test_extract(diagnostics "${depth_source}"
    "uint64_t DepthInferencer::depth_updates_published() const" "ID3D11ShaderResourceView* DepthInferencer::depth_srv() const")
add_executable(runtime_integration_tests runtime_integration_tests.cpp capture_recovery.cpp)
target_compile_features(runtime_integration_tests PRIVATE cxx_std_17)
target_include_directories(runtime_integration_tests PRIVATE "${CMAKE_CURRENT_BINARY_DIR}")
add_test(NAME runtime_integration_tests COMMAND runtime_integration_tests)
set_tests_properties(runtime_integration_tests PROPERTIES TIMEOUT 20)
