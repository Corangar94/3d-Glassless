#include <cstdint>
#include <cstring>
#include "fp16.generated.inl"
// Shim around the exact production run_once method; no GPU or ONNX execution.
#include <algorithm>
#include <array>
#include <stdexcept>
#define CHECK(condition) do { if (!(condition)) throw std::runtime_error("check failed at line " + std::to_string(__LINE__) + ": " #condition); } while (false)
#include <limits>
#include <cmath>
#include <cstring>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <string>
#include <vector>
#include "depth_result_freshness.h"
#include "depth_progress_watchdog.h"
#include "parallax_health.h"
#include "capture_recovery.h"
#include "depth_tile_coverage.h"
#include "overlay_runtime_health.h"
static uint64_t test_now_ms = 1000;
using DepthSourceIdentity = g3d::depth::SourceIdentity;
using HRESULT = int;
constexpr HRESULT DXGI_ERROR_WAS_STILL_DRAWING = 1;
constexpr int D3D11_MAP_READ = 1, D3D11_MAP_FLAG_DO_NOT_WAIT = 1;
inline bool FAILED(HRESULT hr) { return hr < 0; }
struct ID3D11Texture2D { int scene_id; };
struct D3D11_MAPPED_SUBRESOURCE { void* pData = nullptr; unsigned RowPitch = 4; };
struct DepthInferImpl;
struct DepthInferencer {
    static constexpr int kModelSize = 2;
    DepthInferImpl* impl_;
    uint32_t depth_age_ms() const;
    uint32_t depth_upload_age_ms() const;
    uint64_t depth_updates_published() const;
    uint64_t stale_depth_results_dropped() const;
    uint64_t nonmonotonic_depth_results_dropped() const;
    uint64_t invalid_depth_results_dropped() const;
    uint64_t latest_depth_generation() const;
    uint64_t latest_capture_generation() const;
    uint64_t complete_depth_generation() const;
    uint64_t outstanding_work_timeouts() const;
    const char* last_error() const;
};
struct FakeContext {
    bool map_busy = false;
    std::vector<uint16_t> uploaded;
    int map_calls=0;
    bool map_fail=false;
    void CopyResource(void* dest, void* source) {
        *static_cast<ID3D11Texture2D*>(dest) = *static_cast<ID3D11Texture2D*>(source);
    }
    void UpdateSubresource(void*, int, void*, const void* data, size_t pitch, int) {
        const auto* values=static_cast<const uint16_t*>(data);
        uploaded.assign(values, values + pitch / sizeof(uint16_t) * DepthInferencer::kModelSize);
    }
    void Unmap(void*, int) {}
    HRESULT Map(void* texture, int, int, int, D3D11_MAPPED_SUBRESOURCE* out) {
        ++map_calls;
        if (map_fail) return -1;
        if (map_busy) return DXGI_ERROR_WAS_STILL_DRAWING;
        out->pData = &static_cast<ID3D11Texture2D*>(texture)->scene_id;
        return 0;
    }
};
struct DepthInferImpl {
    using Clock = std::chrono::steady_clock;
    struct DepthProfile { int width = 518, height = 294; uint32_t mode = 1; };
    static constexpr int kReadbackRingSize = 3;
    std::mutex m;
    std::condition_variable cv_work;
    std::atomic<bool> stop{false};
    std::atomic<uint64_t> outstanding_work_started_ms{0}, outstanding_timeout_count{0};
    bool input_pending = false, worker_running = false, worker_failed = false;
    bool output_ready = false, blend_active = false, has_valid_depth = false;
    std::string last_err;
    std::vector<uint16_t> ready_upload_fp16;
    std::vector<float> scratch_input_f32, pending_input_f32;
    DepthSourceIdentity ready_source{}, pending_source{};
    FakeContext context;
    FakeContext* ctx = &context;
    int tile_count = 1;
    ID3D11Texture2D depth_storage{0}, prev_storage{0}, compact_storage{0}, retained_storage{0};
    ID3D11Texture2D *depth_tex=&depth_storage, *depth_prev_tex=&prev_storage;
    ID3D11Texture2D *compact_bgra=&compact_storage, *retained_compact_bgra=&retained_storage;
    bool retained_compact_pending=false, retained_completion_pending=false;
    g3d::depth::TileCoverage tile_coverage;
    g3d::depth::TileCoverageSnapshot ready_coverage;
    std::atomic<uint64_t> complete_depth_generation{0}, oldest_depth_source_ms{0};
    std::atomic<uint64_t> published_depth_updates{0}, stale_depth_drops{0};
    std::atomic<uint64_t> nonmonotonic_depth_drops{0}, invalid_depth_drops{0};
    std::atomic<bool> all_depth_tiles_valid{false};
    DepthProfile retained_profile{};
    DepthSourceIdentity retained_source{};
    std::atomic<uint64_t> latest_capture_generation{0};
    Clock::time_point last_depth_arrival{}, blend_started{}, last_submit{};
    float blend_duration_sec = 0.12f;
    std::atomic<uint64_t> last_depth_upload_ms{0}, last_depth_source_ms{0};
    std::atomic<uint64_t> last_depth_source_generation{0};
    std::atomic<uint32_t> performance_mode{1};
    std::atomic<int> active_model_width{0}, active_model_height{0}, active_scheduled_tiles{0};
    std::array<void*, kReadbackRingSize> stage_bgra{};
    std::array<bool, kReadbackRingSize> stage_pending{};
    std::array<DepthProfile, kReadbackRingSize> stage_profiles{};
    std::array<DepthSourceIdentity, kReadbackRingSize> stage_sources{};
    std::array<std::vector<int>, kReadbackRingSize> stage_tiles;
    std::vector<int> pending_tiles;
    DepthProfile pending_profile{};
    int stage_count = 0, stage_write = 0, stage_read = 0;
    uint64_t generation=0;
    std::vector<int> staged_scene_ids;
    g3d::depth::ResultFreshnessGate result_freshness;
    static uint64_t steady_milliseconds() { return test_now_ms; }
    static Clock::time_point clock_now() { return Clock::time_point(std::chrono::milliseconds(test_now_ms)); }
    uint32_t resolve_performance_mode(uint32_t mode) { return mode; }
    DepthProfile profile_for_mode(uint32_t mode) { return {518, 294, mode}; }
    uint32_t adaptive_interval_ms(const DepthProfile&) { return 70; }
    uint64_t outstanding_work_deadline_ms() const { return 5000; }
    int termination_requests=0;
    void request_worker_termination() { ++termination_requests; }
#include "schedule.generated.inl"
    bool render_compact(ID3D11Texture2D* captured, const DepthProfile&) {
        staged_scene_ids.push_back(captured->scene_id); *compact_bgra=*captured; return true;
    }
    DepthSourceIdentity next_source_identity(uint64_t time) { return {++generation, time}; }
    void preprocess_compact(const uint8_t* data, int, const DepthProfile&, const std::vector<int>&) { scratch_input_f32 = {float(*reinterpret_cast<const int*>(data))}; }
#include "history_reset.generated.inl"
#include "publisher.generated.inl"
// Exact production method is inserted below, without alteration.

    std::array<ID3D11Texture2D, kReadbackRingSize> stages{};
    std::vector<uint64_t> tile_generation;
    uint64_t scheduler_cycle=0, completion_generation=0;
    std::vector<std::vector<float>> cached_tile_norm, prev_norm_tiles;
    std::vector<float> output_f32, prev_norm_f32;
    float smoothed_global_lo=0.0f, smoothed_global_hi=1.0f;
    float smoothed_contrast_mean=0.5f, smoothed_contrast_gain=1.0f;
    bool global_range_valid=false, contrast_state_valid=false;
    std::vector<float> percentile_scratch, global_samples_scratch;
    std::vector<float> normalized_scratch, motion_warp_scratch;
    int completions=0;
    explicit DepthInferImpl(int tiles=1) {
        tile_count=tiles;
        tile_generation.resize(tiles);
        tile_coverage.reset(tiles);
        cached_tile_norm.assign(tiles, std::vector<float>(4, 0.5f));
        prev_norm_tiles.resize(tiles);
        for (int i=0;i<kReadbackRingSize;++i) stage_bgra[i]=&stages[i];
    }
    void tick(uint64_t t) { test_now_ms=t; }
    bool postprocess(std::vector<float>& values, const DepthProfile&, float, float, int) {
        values.assign(4, output_f32.front()/100.0f); return false;
    }
    void complete() {
        CHECK(input_pending);
        ++completions;
        const auto running_tiles=pending_tiles;
        const auto running_profile=pending_profile;
        auto running_source=pending_source;
        std::vector<std::vector<float>> raw_tiles(running_tiles.size(), pending_input_f32);
        const bool range_cut=false;
        float smoothed_global_lo=0, smoothed_global_hi=1;
#include "tile_update.generated.inl"
        (void)any_scene_cut;
        const int N=DepthInferencer::kModelSize;
        std::vector<uint16_t> produced_upload;
        const float smoothed_contrast_mean=0.5f, smoothed_contrast_gain=1.0f;
#include "atlas.generated.inl"
        const bool ok=true;
        std::string error;
        input_pending=false;
#include "handoff.generated.inl"
    }
#include "pipeline.generated.inl"
};

#include "ages.generated.inl"
#include "diagnostics.generated.inl"
uint64_t DepthInferencer::outstanding_work_timeouts() const { return impl_->outstanding_timeout_count.load(); }
const char* DepthInferencer::last_error() const { return impl_->last_err.c_str(); }
using BOOL=int; using HWND=void*; using DWORD=uint32_t;
constexpr int SW_HIDE=0, SW_SHOWNOACTIVATE=4;
bool os_window_visible=false;
int show_calls=0;
BOOL ShowWindow(HWND, int command) {
    ++show_calls;
    os_window_visible=(command==SW_SHOWNOACTIVATE);
    return 1;
}
HWND g_hwnd=reinterpret_cast<HWND>(1), g_targetWindow=nullptr;
std::wstring g_targetExePath;
uint32_t g_targetPid=0;
bool g_hasFrame=false, g_overlayVisible=false, g_depthRecoveryPending=false;
const char* g_depthFailureReason="depth_failed";
std::string last_capture_reason;
uint64_t g_lastCaptureFrameMs=0;
DepthInferencer* g_depth=nullptr;
using CaptureState=g3d::capture::CaptureState;
using CaptureSignal=g3d::capture::CaptureSignal;
CaptureState g_captureState=CaptureState::Running;
g3d::runtime_health::DepthRecoveryEpisode g_depthRecovery;
g3d::capture::RetrySchedule g_rebindRetry;
HWND GetForegroundWindow() { return nullptr; }
void GetWindowThreadProcessId(HWND, DWORD*) {}
uint64_t GetTickCount64() { return test_now_ms; }
void Log(const char*, ...) {}

void QueueCaptureSignal(CaptureSignal signal, const char* reason) {
    last_capture_reason = reason ? reason : "";
    const auto action=g3d::capture::AdvanceCaptureState(g_captureState, signal);
    g_captureState=action.next_state;
    g_rebindRetry.RecordFailure(test_now_ms);
    g_hasFrame=false;
    g_depth=nullptr;
}
#include "visibility.generated.inl"
#include "mark_failure.generated.inl"
#include "depth_match.generated.inl"
#include "recovery.generated.inl"

void attach(DepthInferencer& facade) {
    g_depth=&facade;
    g_captureState=CaptureState::Running;
    g_hasFrame=g_overlayVisible=os_window_visible=g_depthRecoveryPending=false;
    show_calls=0; g_lastCaptureFrameMs=0; g_targetExePath.clear();
    g_depthFailureReason="depth_failed"; last_capture_reason.clear();
    g_depthRecovery={}; g_depthRecovery.SessionStarted();
    g_rebindRetry.Reset(test_now_ms);
}
void settle(DepthInferImpl& pipeline, uint64_t start) {
    for(int i=0;i<1000;++i) {
        pipeline.tick(start+static_cast<uint64_t>(i)*10);
        if(pipeline.input_pending) pipeline.complete();
        CHECK(pipeline.run_once(nullptr));
    }
    CHECK(!pipeline.input_pending && pipeline.stage_count==0);
    CHECK(!pipeline.retained_compact_pending && !pipeline.retained_completion_pending);
}

void test_first_frame_visibility() {
    DepthInferImpl p; DepthInferencer facade{&p}; attach(facade);
    ID3D11Texture2D frame{20}; p.tick(1000);
    g_hasFrame=true; g_lastCaptureFrameMs=1000;
    UpdateOverlayVisibility();
    CHECK(g_hasFrame && !os_window_visible && p.generation==0);
    CHECK(p.run_once(&frame)); p.complete();
    p.tick(1050); CHECK(p.run_once(nullptr));
    UpdateOverlayVisibility();
    CHECK(os_window_visible && g_hasFrame && facade.depth_updates_published()==1);
    CHECK(p.generation==1); // Showing the window never resubmits/restamps pixels.
    const int calls=show_calls;
    for(int i=0;i<1000;++i) {
        p.tick(1100+i); CHECK(p.run_once(nullptr)); UpdateOverlayVisibility();
    }
    CHECK(show_calls==calls && p.completions==1);
    g_targetExePath=L"background.exe"; UpdateOverlayVisibility();
    CHECK(!os_window_visible && g_hasFrame);
    g_targetExePath.clear(); UpdateOverlayVisibility(); CHECK(os_window_visible);
    g_captureState=CaptureState::Unavailable; UpdateOverlayVisibility();
    CHECK(!os_window_visible);
}
void test_no_capture_and_failures() {
    DepthInferImpl p;
    for(int i=0;i<1000;++i) { p.tick(1000+i); CHECK(p.run_once(nullptr)); }
    CHECK(p.generation==0 && p.context.map_calls==0 && p.completions==0);
    p.worker_failed=true; CHECK(!p.run_once(nullptr));
    p.worker_failed=false; p.stop=true; CHECK(!p.run_once(nullptr));
}

void test_fast_atlas_provenance_and_completion() {
    for(int tiles : {2, 3, 5}) {
        DepthInferImpl p(tiles); DepthInferencer f{&p}; p.performance_mode=2;
        attach(f); g_hasFrame=true;
        ID3D11Texture2D a{20}, b{80};
        p.tick(1000); CHECK(p.run_once(&a)); p.complete();
        p.tick(1010); CHECK(p.run_once(nullptr));
        CHECK(f.complete_depth_generation()==0 && f.depth_age_ms()==UINT32_MAX);
        CHECK(!PublishedDepthMatchesHeldCapture());
        settle(p,1020); CHECK(f.complete_depth_generation()==1);
        p.tick(12000); CHECK(p.run_once(&b)); p.complete();
        p.tick(12010); CHECK(p.run_once(nullptr));
        CHECK(f.latest_depth_generation()==2 && f.latest_capture_generation()==2);
        CHECK(f.complete_depth_generation()==0 && f.depth_age_ms()>=11010);
        CHECK(!PublishedDepthMatchesHeldCapture());
        CHECK(g3d::parallax::DepthAgeForHealth(f.depth_age_ms(),201,PublishedDepthMatchesHeldCapture())!=0);
        settle(p,12020);
        CHECK(f.complete_depth_generation()==2 && f.latest_capture_generation()==2);
        CHECK(p.completions==4); // One selective + one all-tile pass per held image.
        for(uint16_t sample : p.context.uploaded) CHECK(std::fabs(half_to_float(sample)-.8f)<.001f);
        CHECK(p.retained_source.captured_ms==12000);
        CHECK(PublishedDepthMatchesHeldCapture());
        CHECK(g3d::parallax::DepthAgeForHealth(f.depth_age_ms(),90000,PublishedDepthMatchesHeldCapture())==0);
    }
}
void test_late_current_results() {
    for(uint64_t latency : {750u,751u,90000u}) {
        DepthInferImpl p; DepthInferencer f{&p}; ID3D11Texture2D a{20};
        p.tick(1000); CHECK(p.run_once(&a)); p.complete();
        p.tick(1000+latency); CHECK(p.run_once(nullptr));
        CHECK(f.depth_updates_published()==1 && f.depth_age_ms()==latency);
        CHECK(f.complete_depth_generation()==1 && !p.retained_completion_pending);
    }
}
void test_late_partial_and_obsolete_results() {
    DepthInferImpl fast(2); DepthInferencer f{&fast}; fast.performance_mode=2;
    ID3D11Texture2D a{20}, b{60}, c{80};
    fast.tick(1000); CHECK(fast.run_once(&a)); fast.complete();
    fast.tick(1751); CHECK(fast.run_once(nullptr));
    CHECK(f.stale_depth_results_dropped()==1 && f.depth_updates_published()==0);
    CHECK(fast.input_pending && fast.pending_tiles.size()==2);
    CHECK(fast.pending_source.captured_ms==1000);
    fast.complete(); fast.tick(2502); CHECK(fast.run_once(nullptr));
    CHECK(f.complete_depth_generation()==1 && f.depth_updates_published()==1);
    CHECK(!fast.retained_completion_pending);

    DepthInferImpl p; DepthInferencer g{&p};
    p.tick(1000); CHECK(p.run_once(&a)); p.complete();
    p.tick(1050); CHECK(p.run_once(nullptr));
    p.tick(2000); CHECK(p.run_once(&b));
    p.tick(2020); CHECK(p.run_once(&c));
    p.complete(); p.tick(2801); CHECK(p.run_once(nullptr));
    CHECK(g.stale_depth_results_dropped()==1 && g.latest_depth_generation()==1);
    CHECK(p.pending_source.generation==3 && p.pending_source.captured_ms==2020);
    p.complete(); p.tick(3500); CHECK(p.run_once(nullptr));
    CHECK(g.latest_depth_generation()==3 && g.complete_depth_generation()==3);
    CHECK(g.depth_age_ms()==1480 && g.depth_updates_published()==2);
    settle(p,3510); CHECK(p.completions==3);
}

void test_latest_pixels_survive_backpressure() {
    for(uint32_t mode : {0u,1u,2u}) {
        DepthInferImpl p(3); DepthInferencer f{&p}; p.performance_mode=mode;
        ID3D11Texture2D a{20}, b{60}, c{80};
        p.context.map_busy=true;
        p.tick(1000); CHECK(p.run_once(&a)); CHECK(!p.input_pending);
        p.tick(1010); CHECK(p.run_once(&b));
        p.tick(1020); CHECK(p.run_once(&c));
        c.scene_id=99; // Retained compact pixels must not alias the caller's frame.
        p.context.map_busy=false;
        settle(p,1030);
        CHECK(f.latest_capture_generation()==3 && f.complete_depth_generation()==3);
        CHECK(p.retained_source.captured_ms==1020);
        for(uint16_t sample : p.context.uploaded) CHECK(std::fabs(half_to_float(sample)-.8f)<.001f);
    }
    DepthInferImpl p; DepthInferencer f{&p}; ID3D11Texture2D a{20}, b{80};
    p.tick(1000); CHECK(p.run_once(&a)); p.complete();
    p.tick(1010); CHECK(p.run_once(nullptr));
    p.tick(1020); CHECK(p.run_once(&b));
    CHECK(p.retained_compact_pending && !p.input_pending); // Rate limited.
    settle(p,1030);
    CHECK(p.completions==2 && f.complete_depth_generation()==2);
    CHECK(p.retained_source.captured_ms==1020);
    DepthInferImpl bad; bad.context.map_fail=true;
    CHECK(!bad.run_once(&a));
}

void test_failure_wins_and_backoff_survives_old_success() {
    ID3D11Texture2D frame{20};
    g_depthRecovery={}; g_rebindRetry.Reset(1000);
    uint64_t now=1000;
    const uint64_t expected[]={250,500,1000,2000,2000,2000};
    for(uint64_t delay : expected) {
        DepthInferImpl p; DepthInferencer f{&p};
        g_depth=&f; g_captureState=CaptureState::Running;
        g_depthRecovery.SessionStarted();
        if(!g_depthRecovery.active()) g_rebindRetry.Reset(now);
        p.tick(now); g_hasFrame=true; CHECK(p.run_once(&frame)); p.complete();
        p.tick(now+10); CHECK(p.run_once(nullptr)); TickDepthRecovery();
        CHECK(f.depth_updates_published()==1);
        p.worker_failed=true; CHECK(!p.run_once(nullptr));
        MarkDepthFailure(); TickDepthRecovery();
        CHECK(g_depthRecovery.active() && !g_depth && !g_hasFrame);
        CHECK(g_rebindRetry.next_attempt_ms()-test_now_ms==delay);
        // Even explicit observations of the retired session cannot heal it.
        CHECK(!g_depthRecovery.Observe(now+10000,100,true,true));
        now=g_rebindRetry.next_attempt_ms();
    }
    // A new session must supply sustained health, not merely initialize.
    DepthInferImpl p; DepthInferencer f{&p}; g_depth=&f;
    g_captureState=CaptureState::Running; g_hasFrame=true;
    g_depthRecovery.SessionStarted(); p.tick(now); TickDepthRecovery();
    CHECK(g_depthRecovery.active());
    CHECK(p.run_once(&frame)); p.complete(); p.tick(now+10);
    CHECK(p.run_once(nullptr)); TickDepthRecovery(); CHECK(g_depthRecovery.active());

    p.tick(now+2009); TickDepthRecovery(); CHECK(g_depthRecovery.active());
    p.tick(now+2010); TickDepthRecovery();
    CHECK(!g_depthRecovery.active() && g_rebindRetry.failures()==0);
}
void test_outstanding_work_timeout_and_idle_control() {
    DepthInferImpl p; DepthInferencer f{&p}; attach(f);
    p.tick(1000);
    p.input_pending = true;
    p.outstanding_work_started_ms.store(1000);
    p.tick(6000); CHECK(p.run_once(nullptr));
    CHECK(!p.worker_failed && p.termination_requests==0);
    p.tick(6001); CHECK(!p.run_once(nullptr));
    CHECK(p.worker_failed && p.termination_requests==1);
    CHECK(p.outstanding_timeout_count.load()==1);
    MarkDepthFailure(); TickDepthRecovery();
    CHECK(g_depthRecovery.active());
    CHECK(last_capture_reason == "depth_timeout");

    DepthInferImpl idle;
    idle.tick(60000); CHECK(idle.run_once(nullptr));
    CHECK(!idle.worker_failed && idle.outstanding_timeout_count.load()==0);
}

void test_fast_scheduler_meets_supported_freshness_budget() {
    constexpr uint64_t inference_ms = 160;
    for (int tiles : {2, 3, 4}) {
        DepthInferImpl p(tiles);
        const auto profile = p.profile_for_mode(2);
        std::vector<uint64_t> last(static_cast<size_t>(tiles), 0);
        uint64_t now=1000;
        for (int step=0; step<tiles*4; ++step) {
            const auto selected=p.select_tiles(profile);
            CHECK(selected.size()==1);
            const int tile=selected.front();
            CHECK(tile>=0 && tile<tiles);
            p.tile_generation[tile]=++p.completion_generation;
            last[static_cast<size_t>(tile)]=now;
            now += inference_ms;
            if (step >= tiles-1) {
                const uint64_t oldest=*std::min_element(last.begin(),last.end());
                CHECK(oldest!=0 && now-oldest <= 750);
            }
        }
    }
}

void test_freshness_upgrade_not_duplicate_or_retiming() {
    using namespace g3d::depth;
    ResultFreshnessGate gate;
    CHECK(gate.consider({1,1000},1010)==PublishDecision::Accept);
    CHECK(gate.consider({1,1001},2000,{1,1001},true)==PublishDecision::NonmonotonicGeneration);
    CHECK(gate.consider({1,1000},2000,{1,1000},true)==PublishDecision::Accept);
    CHECK(gate.consider({1,1000},2001,{1,1000},true)==PublishDecision::NonmonotonicGeneration);
    CHECK(gate.consider({2,1001},2000,{3,1900},true)==PublishDecision::StaleSource);
    CHECK(gate.consider({0,2000},2000,{},true)==PublishDecision::InvalidGeneration);
    CHECK(gate.snapshot().last_published_source_ms==1000);
}
int main() {
    try {
        test_first_frame_visibility();
        test_no_capture_and_failures();
        test_fast_atlas_provenance_and_completion();
        test_late_current_results();
        test_late_partial_and_obsolete_results();
        test_latest_pixels_survive_backpressure();
        test_failure_wins_and_backoff_survives_old_success();
        test_outstanding_work_timeout_and_idle_control();
        test_fast_scheduler_meets_supported_freshness_budget();
        test_freshness_upgrade_not_duplicate_or_retiming();
        std::cout << "10 production-path integration groups passed\n";
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n'; return 1;
    }
}
