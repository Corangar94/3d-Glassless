// overlay/depth_infer.h
// Real-time monocular depth inference for the Glassless3D overlay.
//
// Runs Depth Anything V2 Small (~50MB fp16 ONNX) on the captured desktop
// frame via ONNX Runtime + DirectML execution provider. Produces a depth
// texture the parallax shader can sample for per-pixel offset.
//
// Pipeline per frame:
//   1. Reduce captured BGRA8 desktop on the GPU and copy into a staging ring
//   2. Map without waiting; BGRA->RGB + normalize into a float NCHW tensor
//   3. ORT inference with DirectML EP on one worker thread
//   4. Normalize depth to [0,1], retain source generation/time, and publish
//      only a monotonic result no older than the depth-health budget
//   5. Update a R16F texture and expose both current/previous SRVs for blending
//
// The worker is sequential, but source identity still travels through staging,
// pending, running, ready, and uploaded states. A delayed inference therefore
// cannot masquerade as fresh merely because its texture upload happened now.

#pragma once

#include <d3d11.h>
#include <cstdint>
#include <memory>
#include <string>

// Forward-declared so we don't pull ORT headers into overlay.cpp.
struct DepthInferImpl;

class DepthInferencer {
public:
    // Input resolution the model consumes. Depth Anything V2 Small is
    // trained at multiples of 14 (patch size); 518 is the canonical size
    // used by the onnx-community export. We also produce 518x518 output.
    static constexpr int kModelSize = 518;

    DepthInferencer();
    ~DepthInferencer();

    // Load the ONNX model and create GPU resources.
    // model_path: absolute path to depth_anything_v2_small_fp16.onnx.
    // capture_w, capture_h: dimensions of the captured desktop texture.
    // Returns false on failure; call last_error() for details.
    bool init(ID3D11Device* dev, ID3D11DeviceContext* ctx,
              const std::wstring& model_path,
              int capture_w, int capture_h);

    // Advance the async pipeline for the given captured frame. This can upload
    // a completed earlier inference and, when idle, stage the current capture.
    // Returns false on a pipeline/device failure; controlled stale-result drops
    // keep the previous valid depth and return true.
    bool run(ID3D11Texture2D* captured_bgra8);

    // Runtime performance/quality mode from G3D_Settings:
    // 0=quality, 1=balanced, 2=fast, 3=auto.
    void set_performance_mode(uint32_t mode);
    uint32_t performance_mode() const;
    uint32_t active_performance_mode() const;

    // Feed recent render cost to the automatic controller. Negative values are
    // ignored, allowing unsupported GPU timing queries to degrade safely.
    void set_runtime_load(float frame_cpu_ms, float gpu_ms);

    int active_model_width() const;
    int active_model_height() const;
    int active_scheduled_tiles() const;
    float last_inference_ms() const;
    float blend_duration_ms() const;

    // Age of the desktop capture that produced the currently published depth,
    // not merely time since its GPU upload. Returns UINT32_MAX until a real
    // source-aware depth update has been published.
    uint32_t depth_age_ms() const;
    uint32_t depth_upload_age_ms() const;

    bool gpu_io_active() const;
    uint64_t gpu_io_fallbacks() const;

    // Source-aware publication diagnostics.
    uint64_t depth_updates_published() const;
    uint64_t stale_depth_results_dropped() const;
    uint64_t nonmonotonic_depth_results_dropped() const;
    uint64_t invalid_depth_results_dropped() const;
    uint64_t latest_depth_generation() const;

    // Two depth SRVs for render-rate interpolation.
    // The shader lerps between prev_srv (depth at last inference) and
    // depth_srv (depth at current/latest inference) using depth_blend().
    // This hides the 10Hz inference update rate — transitions appear at 60Hz.
    ID3D11ShaderResourceView* depth_srv()      const;  // latest inference
    ID3D11ShaderResourceView* depth_prev_srv() const;  // previous inference

    // Blend factor [0,1] computed from wall-clock elapsed time after each new
    // accepted inference. It is independent of monitor refresh and render cadence.
    float depth_blend() const;

    const char* last_error() const;

    // Number of successful worker inferences since init, including a result that
    // was later dropped at publication because its source frame had become stale.
    uint64_t inferences_completed() const;

    // UV transform to convert screen UV X → depth texture UV X.
    // The depth texture only covers the center crop of the captured frame
    // (the capture is cropped to 16:9 before letterboxing into the model).
    // Correct depth sample UV: depthUV.x = (screenUV.x - crop_x0_uv) / crop_w_uv
    // For 5120×1440: crop_x0_uv=0.25, crop_w_uv=0.5  →  depthUV.x = (x-0.25)*2
    float depth_crop_x0_uv()  const;   // left edge of crop in screen UV [0,1]
    float depth_crop_w_uv()   const;   // width of crop in screen UV [0,1]

    // Disable copy/move — owns GPU + ORT resources.
    DepthInferencer(const DepthInferencer&) = delete;
    DepthInferencer& operator=(const DepthInferencer&) = delete;

private:
    std::unique_ptr<DepthInferImpl> impl_;
};

#ifdef G3D_OVERLAY_SHOWWINDOW_GUARD
// overlay.cpp marks a captured frame available before calling run(). Without
// this gate, its first visibility update can expose the initial flat 0.5 depth
// texture while the asynchronous worker is still producing the first result.
//
// Once the worker reports a completion, call run() here to drain it. Reveal the
// window only if that result survived source-age/generation admission and was
// actually uploaded. While no valid publication exists, clear has_frame; the
// render loop's second visibility pass restores its bookkeeping to hidden and
// the next captured frame retries normally.
inline BOOL G3DShowWindowAfterDepthUpload(
    HWND window,
    int command,
    DepthInferencer* depth,
    ID3D11Texture2D* captured_frame,
    bool& has_frame) {
    if (command == SW_SHOWNOACTIVATE) {
        const bool depth_ready = depth
            && depth->inferences_completed() > 0
            && captured_frame
            && depth->run(captured_frame)
            && depth->depth_updates_published() > 0;
        if (!depth_ready) {
            has_frame = false;
            return ::ShowWindow(window, SW_HIDE);
        }
    }
    return ::ShowWindow(window, command);
}

#define ShowWindow(window, command) \
    G3DShowWindowAfterDepthUpload( \
        (window), (command), g_depth, g_capTex, g_hasFrame)
#endif
