// overlay/depth_infer.cpp — see depth_infer.h for pipeline overview.

#include "depth_infer.h"

// DirectML.h (via dml_provider_factory.h) and onnxruntime_c_api.h use MSVC
// SAL source annotations that MinGW's sal.h does not define. Stub every
// annotation used by these headers as a no-op so they parse under g++.
// Real MSVC defines these in its own sal.h; keeping them defined here is
// harmless because they're text-only decoration.
#ifndef _Maybenull_
#define _Maybenull_
#endif
#ifndef _Field_size_
#define _Field_size_(x)
#endif
#ifndef _Field_size_opt_
#define _Field_size_opt_(x)
#endif
#ifndef _Field_size_bytes_
#define _Field_size_bytes_(x)
#endif
#ifndef _Field_size_bytes_opt_
#define _Field_size_bytes_opt_(x)
#endif
#ifndef _Frees_ptr_
#define _Frees_ptr_
#endif
#ifndef _Frees_ptr_opt_
#define _Frees_ptr_opt_
#endif
#ifndef _In_reads_
#define _In_reads_(x)
#endif
#ifndef _In_reads_bytes_
#define _In_reads_bytes_(x)
#endif
#ifndef _Out_writes_
#define _Out_writes_(x)
#endif
#ifndef _Out_writes_bytes_
#define _Out_writes_bytes_(x)
#endif
#ifndef _Inout_updates_
#define _Inout_updates_(x)
#endif
#ifndef _Inout_updates_bytes_
#define _Inout_updates_bytes_(x)
#endif
#ifndef _Post_writable_byte_size_
#define _Post_writable_byte_size_(x)
#endif
#ifndef _Outptr_
#define _Outptr_
#endif
#ifndef _Outptr_result_maybenull_
#define _Outptr_result_maybenull_
#endif
#ifndef _Check_return_
#define _Check_return_
#endif

#include <onnxruntime_cxx_api.h>
#include <dml_provider_factory.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <limits>
#include <mutex>
#include <thread>
#include <vector>

// ── fp16 helpers ─────────────────────────────────────────────────────────────
// Minimal IEEE 754 round-to-nearest-even float->half. Good enough for
// normalized image values in [-3, 3] after ImageNet standardization.
static inline uint16_t float_to_half(float f) {
    uint32_t x;
    std::memcpy(&x, &f, sizeof(x));
    const uint32_t sign = (x >> 16) & 0x8000u;
    int32_t exp  = static_cast<int32_t>((x >> 23) & 0xff) - 127 + 15;
    uint32_t mant = x & 0x7fffffu;
    if (exp >= 31) {
        // Inf / NaN / overflow -> Inf
        return static_cast<uint16_t>(sign | 0x7c00u);
    }
    if (exp <= 0) {
        // Subnormal or underflow -> zero (good enough for our range).
        return static_cast<uint16_t>(sign);
    }
    // Round-to-nearest-even the 13 dropped mantissa bits.
    uint32_t rounded_mant = mant + 0x1000u;
    if (rounded_mant & 0x800000u) {
        rounded_mant = 0;
        exp += 1;
        if (exp >= 31) return static_cast<uint16_t>(sign | 0x7c00u);
    }
    return static_cast<uint16_t>(sign | (uint32_t(exp) << 10) | (rounded_mant >> 13));
}

static inline float half_to_float(uint16_t h) {
    const uint32_t sign = (uint32_t(h) & 0x8000u) << 16;
    uint32_t exp  = (h >> 10) & 0x1fu;
    uint32_t mant = h & 0x3ffu;
    uint32_t out;
    if (exp == 0) {
        if (mant == 0) {
            out = sign;
        } else {
            // Subnormal — normalize.
            while ((mant & 0x400u) == 0) { mant <<= 1; exp -= 1; }
            exp += 1;
            mant &= 0x3ffu;
            out = sign | ((exp + 127 - 15) << 23) | (mant << 13);
        }
    } else if (exp == 31) {
        out = sign | 0x7f800000u | (mant << 13);
    } else {
        out = sign | ((exp + 127 - 15) << 23) | (mant << 13);
    }
    float f;
    std::memcpy(&f, &out, sizeof(f));
    return f;
}

static inline bool IsLikelyHudUv(float u, float v) {
    // Conservative static HUD mask for screen-capture fallback depth.
    // Monocular depth models treat UI as scene geometry, which creates false
    // near planes and watery parallax around action bars, quest panes, chat,
    // minimaps, and unit frames. Leave these pixels at ImageNet mean grey so
    // the model gets the game view as the dominant signal.
    if (v < 0.08f) return true;                         // top bars/unit frames
    if (v > 0.78f) return true;                         // action bars/chat
    if (u < 0.18f && v > 0.30f) return true;            // left chat/quest stack
    if (u > 0.82f && (v < 0.28f || v > 0.62f)) return true; // minimap/side UI
    return false;
}

// ── Impl ─────────────────────────────────────────────────────────────────────
struct DepthInferImpl {
    // D3D11 resources (device/context NOT owned; borrowed from overlay).
    ID3D11Device*         dev          = nullptr;
    ID3D11DeviceContext*  ctx          = nullptr;

    // Full-resolution staging texture for CPU readback of the captured frame.
    ID3D11Texture2D*      stage_bgra   = nullptr;
    int                   cap_w        = 0;
    int                   cap_h        = 0;

    // Horizontal center-crop applied before letterboxing to limit the input
    // aspect ratio fed to the depth model. Depth Anything V2 was trained on
    // ~16:9 or closer-to-square images; feeding a 32:9 ultrawide (5120×1440)
    // as a thin letterboxed strip (518×145 content in 518×518) gives nearly
    // flat depth. We crop the center at most 16:9 of the capture first.
    int                   crop_x0      = 0;   // left pixel of the center crop
    int                   crop_w_eff   = 0;   // width of the crop (≤ cap_w)

    // Letterbox dimensions: the content region inside the kModelSize×kModelSize
    // model input that holds the aspect-ratio-correct resize of crop_w_eff×cap_h.
    // Pixels outside this region are left at 0.0f (ImageNet-normalized grey).
    int                   lb_off_x     = 0;
    int                   lb_off_y     = 0;
    int                   lb_w         = 0;
    int                   lb_h         = 0;

    // Two R16F depth textures for render-rate interpolation.
    // When a new inference arrives, the old texture becomes "prev" and the new
    // one becomes "current". The shader lerps between them using depth_blend,
    // which advances 0→1 over kBlendFrames render frames (~200 ms at 60 fps)
    // — slightly wider than one full ~100 ms inference cycle so successive
    // inferences' blend windows overlap and there is never a discontinuity at
    // the transition. depth_blend() additionally applies smoothstep to `t`
    // so the depth update has zero first-derivative at both endpoints, hiding
    // the discrete update in the middle of head motion.
    // Crossfade window between successive inferences, in render frames.
    // 14 render frames at 60 Hz ≈ 230 ms. Wider than the inference interval
    // (≥100 ms at 10 Hz, currently 200-300 ms at 3-5 Hz) so consecutive
    // 0→1 transitions still overlap — no discrete "snap" moment — but
    // significantly tighter than the 400 ms we had, which introduced
    // perceptible lag between head motion and depth response ("wobbly
    // when I move my head"). Combined with smoothstep endpoints this
    // still reads as a continuous morph.
    static constexpr int kBlendFrames = 14;
    ID3D11Texture2D*          depth_tex      = nullptr;
    ID3D11ShaderResourceView* depth_srv      = nullptr;
    ID3D11Texture2D*          depth_prev_tex = nullptr;
    ID3D11ShaderResourceView* depth_prev_srv = nullptr;
    float                     blend_t        = 1.0f;  // 1.0 = fully on current tex

    // ORT state
    std::unique_ptr<Ort::Env>            env;
    std::unique_ptr<Ort::SessionOptions> opts;
    std::unique_ptr<Ort::Session>        session;
    std::unique_ptr<Ort::RunOptions>     run_options;
    Ort::AllocatorWithDefaultOptions     allocator;
    std::string                          input_name;
    std::string                          output_name;

    // Scratch buffers reused each frame — avoid allocator churn.
    // NOTE on precision: the depth_anything_v2_small_fp16 export stores WEIGHTS
    // as fp16 internally, but its ONNX input/output tensors are float32. The
    // DML EP converts to fp16 under the hood. So we marshal fp32 here and only
    // pack to fp16 when uploading to the shader-sampleable R16F depth texture.
    //
    // Main-thread preprocess scratch. Filled each capture by preprocess().
    // When a new capture is ready we move-swap this into pending_input_f32
    // for the worker to consume.
    std::vector<float>                   scratch_input_f32;
    // Worker-side tensor buffers (worker thread reads these; main thread only
    // touches them briefly under `m` during the swap).
    std::vector<float>                   pending_input_f32;   // handed from main → worker
    std::vector<float>                   running_input_f32;   // worker-owned while Run is in flight
    std::vector<float>                   output_f32;          // model output, worker-owned
    int                                  out_h = 0;
    int                                  out_w = 0;
    std::vector<uint16_t>                scratch_upload_fp16; // worker-local postprocess scratch
    std::vector<uint16_t>                ready_upload_fp16;   // worker → main once per Run
    // Worker-owned previous normalized depth, kept between runs so postprocess
    // can EMA-smooth the new inference against it. Empty on first run.
    std::vector<float>                   prev_norm_f32;
    // Scratch buffer for separable Gaussian blur of the depth map (worker-local).
    std::vector<float>                   blur_tmp_f32;

    // Whether depth_tex has been written at least once.
    bool                                 has_valid_depth = false;

    std::string                          last_err;          // init/Run errors (worker writes under m)

    // ── Async pipeline ──
    std::thread                          worker;
    std::mutex                           m;                 // guards flags + pending/ready buffers
    std::condition_variable              cv_work;           // main → worker wakeup
    bool                                 input_pending = false;   // new input waiting for worker
    bool                                 output_ready  = false;   // new depth waiting for main to upload
    std::atomic<bool>                    stop{false};
    std::atomic<uint64_t>                inferences{0};    // completed Run calls (for diagnostics)
    std::atomic<uint32_t>                performance_mode{1}; // 0=quality, 1=balanced, 2=fast

    // ImageNet normalization (Depth Anything V2 uses standard ImageNet stats)
    static constexpr float kMean[3] = {0.485f, 0.456f, 0.406f};
    static constexpr float kStd[3]  = {0.229f, 0.224f, 0.225f};

    bool create_d3d_resources() {
        // Center-crop the capture to at most 16:9 aspect ratio before letterboxing.
        // For 5120×1440 (32:9): crop_w = 2560, crop_x0 = 1280.
        //   Old: 518×145 content strip (28% fill) → flat depth map.
        //   New: 518×291 content region  (56% fill) → proper depth variation.
        // Narrower displays (≤16:9) are unaffected (crop_w == cap_w).
        crop_w_eff = std::min(cap_w, cap_h * 16 / 9);
        crop_x0    = (cap_w - crop_w_eff) / 2;

        // Compute letterbox dimensions: aspect-ratio-correct resize of the crop
        // into kModelSize×kModelSize. Uniform 0.0f padding (ImageNet-normalised grey).
        // For 5120×1440: scale=0.202 → lb_w=518, lb_h=291, off=(0,113).
        {
            const int N = DepthInferencer::kModelSize;
            float scale = std::min((float)N / crop_w_eff, (float)N / cap_h);
            lb_w = std::max(1, (int)(crop_w_eff * scale));
            lb_h = std::max(1, (int)(cap_h * scale));
            lb_off_x = (N - lb_w) / 2;
            lb_off_y = (N - lb_h) / 2;
        }

        // Staging for full-res capture readback (CPU read).
        D3D11_TEXTURE2D_DESC sd = {};
        sd.Width = cap_w;
        sd.Height = cap_h;
        sd.MipLevels = 1;
        sd.ArraySize = 1;
        sd.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        sd.SampleDesc.Count = 1;
        sd.Usage = D3D11_USAGE_STAGING;
        sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        HRESULT hr = dev->CreateTexture2D(&sd, nullptr, &stage_bgra);
        if (FAILED(hr)) { last_err = "CreateTexture2D(staging BGRA) failed"; return false; }

        // Two R16F depth textures for render-rate interpolation.
        D3D11_TEXTURE2D_DESC dd = {};
        dd.Width = DepthInferencer::kModelSize;
        dd.Height = DepthInferencer::kModelSize;
        dd.MipLevels = 1;
        dd.ArraySize = 1;
        dd.Format = DXGI_FORMAT_R16_FLOAT;
        dd.SampleDesc.Count = 1;
        dd.Usage = D3D11_USAGE_DEFAULT;
        dd.BindFlags = D3D11_BIND_SHADER_RESOURCE;

        hr = dev->CreateTexture2D(&dd, nullptr, &depth_tex);
        if (FAILED(hr)) { last_err = "CreateTexture2D(depth current) failed"; return false; }
        hr = dev->CreateTexture2D(&dd, nullptr, &depth_prev_tex);
        if (FAILED(hr)) { last_err = "CreateTexture2D(depth prev) failed"; return false; }

        D3D11_SHADER_RESOURCE_VIEW_DESC srv = {};
        srv.Format = dd.Format;
        srv.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        srv.Texture2D.MipLevels = 1;
        hr = dev->CreateShaderResourceView(depth_tex,      &srv, &depth_srv);
        if (FAILED(hr)) { last_err = "CreateShaderResourceView(depth current) failed"; return false; }
        hr = dev->CreateShaderResourceView(depth_prev_tex, &srv, &depth_prev_srv);
        if (FAILED(hr)) { last_err = "CreateShaderResourceView(depth prev) failed"; return false; }

        // Initialise both textures to 0.5 (flat depth = no parallax on first frame).
        const int N = DepthInferencer::kModelSize * DepthInferencer::kModelSize;
        std::vector<uint16_t> half_filled(N, float_to_half(0.5f));
        ctx->UpdateSubresource(depth_tex,      0, nullptr, half_filled.data(), DepthInferencer::kModelSize * sizeof(uint16_t), 0);
        ctx->UpdateSubresource(depth_prev_tex, 0, nullptr, half_filled.data(), DepthInferencer::kModelSize * sizeof(uint16_t), 0);
        blend_t = 1.0f;
        return true;
    }

    bool create_ort_session(const std::wstring& model_path) {
        try {
            env = std::make_unique<Ort::Env>(ORT_LOGGING_LEVEL_WARNING, "Glassless3D");
            opts = std::make_unique<Ort::SessionOptions>();
            opts->SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
            // DirectML EP — GPU inference on NVIDIA/AMD/Intel.
            // device_id 0 is the default adapter; matches our D3D11 device.
            OrtApi const& api = Ort::GetApi();
            OrtStatus* st = OrtSessionOptionsAppendExecutionProvider_DML(*opts, 0);
            if (st != nullptr) {
                last_err = std::string("Append DML EP failed: ") + api.GetErrorMessage(st);
                api.ReleaseStatus(st);
                return false;
            }
            // DML requires these on session options.
            opts->DisableMemPattern();
            opts->SetExecutionMode(ORT_SEQUENTIAL);

            session = std::make_unique<Ort::Session>(*env, model_path.c_str(), *opts);
            // Keep one RunOptions object alive for the worker lifetime so
            // cleanup() can interrupt a device-stalled Run before joining.
            run_options = std::make_unique<Ort::RunOptions>();

            size_t num_in = session->GetInputCount();
            size_t num_out = session->GetOutputCount();
            if (num_in != 1 || num_out != 1) {
                char buf[128];
                std::snprintf(buf, sizeof(buf),
                    "Unexpected model I/O: inputs=%zu outputs=%zu", num_in, num_out);
                last_err = buf;
                return false;
            }
            Ort::AllocatedStringPtr in_name  = session->GetInputNameAllocated(0, allocator);
            Ort::AllocatedStringPtr out_name = session->GetOutputNameAllocated(0, allocator);
            input_name  = in_name.get();
            output_name = out_name.get();
        } catch (const Ort::Exception& e) {
            last_err = std::string("ORT init exception: ") + e.what();
            return false;
        }
        return true;
    }

    // CPU: downsample a center-cropped BGRA8 captured frame -> NCHW fp32 RGB
    // tensor with ImageNet normalization and aspect-ratio-correct letterboxing.
    //
    // We first center-crop to at most 16:9 aspect ratio (crop_w_eff × cap_h)
    // so Depth Anything V2 receives a naturally-proportioned image. Without
    // this, a 5120×1440 (32:9) capture letterboxes to a 518×145 content strip
    // (28% fill), which gives nearly flat monocular depth output. The 16:9 crop
    // (2560×1440 → 518×291, 56% fill) produces proper depth variation.
    //
    // Depth texture UV [0,1]×[0,1] still maps to full-screen UV [0,1]×[0,1]
    // via the postprocess stretch. The outer horizontal bands beyond the crop
    // use the edge depth value (clamped sampling) — acceptable for peripheral
    // areas of an ultrawide display.
    void preprocess(const uint8_t* src, int src_pitch) {
        const int N = DepthInferencer::kModelSize;
        scratch_input_f32.assign(3 * N * N, 0.0f);   // 0.0f = ImageNet mean grey
        float* dstR = scratch_input_f32.data() + 0 * N * N;
        float* dstG = scratch_input_f32.data() + 1 * N * N;
        float* dstB = scratch_input_f32.data() + 2 * N * N;

        for (int oy = 0; oy < lb_h; ++oy) {
            int sy = (oy * cap_h) / lb_h;
            if (sy >= cap_h) sy = cap_h - 1;
            const uint8_t* row = src + sy * src_pitch;
            for (int ox = 0; ox < lb_w; ++ox) {
                // Sample from the center-cropped horizontal region.
                int sx = crop_x0 + (ox * crop_w_eff) / lb_w;
                if (sx >= cap_w) sx = cap_w - 1;
                float u = (float)sx / (float)std::max(1, cap_w - 1);
                float v = (float)sy / (float)std::max(1, cap_h - 1);
                if (IsLikelyHudUv(u, v)) {
                    // leave HUD-like pixels at ImageNet mean grey
                    continue;
                }
                const uint8_t* px = row + sx * 4;   // BGRA8
                float b = px[0] / 255.0f;
                float g = px[1] / 255.0f;
                float r = px[2] / 255.0f;
                int idx = (lb_off_y + oy) * N + (lb_off_x + ox);
                dstR[idx] = (r - kMean[0]) / kStd[0];
                dstG[idx] = (g - kMean[1]) / kStd[1];
                dstB[idx] = (b - kMean[2]) / kStd[2];
            }
        }
    }

    // CPU: normalize raw depth to [0,1] fp16 for the parallax shader.
    // Depth Anything V2 outputs relative inverse-depth (disparity) —
    // higher raw value = CLOSER to the camera.  The downstream shader
    // assumes the opposite convention (0=near, 1=far) for its parallax
    // formula `oz = virtualDepth * depth; f = oz/(hz+oz)` where far pixels
    // must shift more than near pixels.  We therefore flip the sign during
    // percentile normalization: v = (vhi - raw) / range → vlo→1 (far),
    // vhi→0 (near).  Naive per-frame min/max remapping causes visible
    // pulsing at 10Hz inference cadence: a single bright/dark outlier pixel
    // shifts the whole depth range.
    //
    // Phase 3 improvements:
    //   1. Percentile (2nd/98th) instead of min/max — outlier-robust.
    //      Subsampled via nth_element on every 4th pixel to keep O(N) cheap.
    //   2. Temporal EMA blend against prev_norm_f32 — smooths depth jitter
    //      frame-to-frame so parallax doesn't "breathe" at inference cadence.
    //
    // upload ends up at (out_h, out_w); if the model output size differs
    // from kModelSize we rescale with nearest-neighbor into a kModelSize^2
    // buffer so the shader SRV stays fixed-size.
    // Runs on the worker thread. Writes into `dst` (kModelSize^2 fp16).
    void postprocess(std::vector<uint16_t>& dst) {
        const int pixels = out_h * out_w;
        if (pixels <= 0) return;

        // ── 1. Percentile range via nth_element on a 1/4 subsample. ──
        // Only sample pixels from the letterbox content region — the grey
        // padding bars produce constant model output that would otherwise
        // collapse the useful depth range toward the bar value.
        // Stride 2 in each dim ⇒ ~¼ the work within the content rect.
        const int cy0 = lb_off_y, cy1 = lb_off_y + lb_h;
        const int cx0 = lb_off_x, cx1 = lb_off_x + lb_w;
        std::vector<float> samples;
        samples.reserve(((cy1 - cy0) / 2 + 1) * ((cx1 - cx0) / 2 + 1));
        for (int y = cy0; y < cy1 && y < out_h; y += 2) {
            const float* row = output_f32.data() + y * out_w;
            for (int x = cx0; x < cx1 && x < out_w; x += 2) {
                samples.push_back(row[x]);
            }
        }
        float vlo, vhi;
        if (samples.empty()) {
            vlo = 0.0f;
            vhi = 1.0f;
        } else {
            // 2nd / 98th percentile: trim the 2% tails on either side.
            size_t n   = samples.size();
            size_t klo = std::min<size_t>(n - 1, n * 2 / 100);
            size_t khi = std::min<size_t>(n - 1, n * 98 / 100);
            std::nth_element(samples.begin(), samples.begin() + klo, samples.end());
            vlo = samples[klo];
            std::nth_element(samples.begin() + klo + 1, samples.begin() + khi, samples.end());
            vhi = samples[khi];
        }
        float range = vhi - vlo;
        if (range < 1e-6f) range = 1.0f;   // degenerate frame guard

        // ── 2. Build downsampled normalised depth (floats) for this frame. ──
        // Depth texture UV [0,1]×[0,1] maps to screen UV [0,1]×[0,1].
        // We pull values only from the letterbox content region so the grey
        // padding bars don't bleed into the depth map used by the shader.
        const int N = DepthInferencer::kModelSize;
        std::vector<float> new_norm_f32(N * N);
        for (int oy = 0; oy < N; ++oy) {
            // Map depth-texture row → content row in model output (clamped).
            int sy = cy0 + (oy * lb_h) / N;
            if (sy < cy0) sy = cy0;
            if (sy >= cy1) sy = cy1 - 1;
            if (sy >= out_h) sy = out_h - 1;
            const float* in_row = output_f32.data() + sy * out_w;
            float* out_row = new_norm_f32.data() + oy * N;
            for (int ox = 0; ox < N; ++ox) {
                int sx = cx0 + (ox * lb_w) / N;
                if (sx < cx0) sx = cx0;
                if (sx >= cx1) sx = cx1 - 1;
                if (sx >= out_w) sx = out_w - 1;
                // Flip sign: DAv2 outputs disparity (higher=nearer).
                // Remap so vhi→0 (near) and vlo→1 (far) per shader convention.
                float v = (vhi - in_row[sx]) / range;
                if (v < 0.0f) v = 0.0f;
                else if (v > 1.0f) v = 1.0f;
                out_row[ox] = v;
            }
        }

        // ── 3. Per-pixel edge-preserving EMA in disparity space. ──
        //
        // Previous approach: one global alpha derived from the frame's mean
        // depth difference. Problem: a fast-moving object in a static scene
        // raises the global mean → static background blends fast too → noise.
        //
        // New approach (Intel RealSense temporal filter pattern):
        //   1. Compute global mean-abs-diff on a 1/4 subsample.
        //      If > kSnapThresh (15%): scene cut / zone load — accept new
        //      frame instantly (skip the EMA loop entirely, alpha=1 everywhere).
        //   2. Otherwise: per-pixel alpha decision.
        //      |new[i] - prev[i]| > kEdgeDelta → HARD RESET (discard history,
        //                                         use new sample verbatim)
        //      else                             → kAlphaSlow (stable → suppress noise)
        //
        // The hard reset on the edge branch is the key difference from a plain
        // two-alpha blend. Soft-blending across a real depth edge (object moved
        // past a pixel) creates ghosting / smearing which reads as "watery"
        // under head motion. The RealSense filter keeps history ONLY where the
        // disparity is stable, which is exactly where noise suppression pays
        // off and motion-blur does not.
        //
        // Blending in disparity (1/depth) space rather than depth space gives
        // perceptually uniform filtering.  The parallax formula scales with
        // oz/(hz+oz) where oz=vd*depth, so a fixed depth-noise at a near pixel
        // (small depth) produces much more visible jitter than the same noise at
        // a far pixel.  Converting to 1/depth normalises this: equal disparity
        // deltas produce roughly equal parallax errors regardless of distance.
        //
        //   kAlphaSlow = 0.04 → ~2.4 s  to reach 63% of a step at 10 Hz
        //   edge branch: alpha = 1 (hard reset) — no smearing across depth edges
        //
        // Tuning note (2026-04-17): α lowered 0.08 → 0.04 after observing that
        // when the head is near-static the only thing changing between frames
        // is the depth map itself. Each 10 Hz refresh shifts band boundaries on
        // flat surfaces by a pixel or two; the parallax shader warps the scene
        // with a slightly different depth, producing the "watery" look. A
        // longer time constant trades latency we don't care about (static
        // viewer) for much stronger suppression of per-inference wobble.
        const uint32_t mode = performance_mode.load(std::memory_order_relaxed);
        const float kAlphaSlow = mode >= 2 ? 0.08f : mode == 1 ? 0.05f : 0.04f;
        constexpr float kSnapThresh = 0.15f;  // global scene-cut threshold
        constexpr float kEdgeDelta  = 0.05f;  // per-pixel edge threshold (~3 depth levels)

        if (prev_norm_f32.size() == new_norm_f32.size()) {
            const int N2 = DepthInferencer::kModelSize;

            // Global mean-abs-diff on 1/4 subsample → scene-cut detection.
            float sum_diff = 0.0f;
            int   cnt      = 0;
            for (int y = 0; y < N2; y += 2) {
                const float* cur = new_norm_f32.data()  + y * N2;
                const float* prv = prev_norm_f32.data() + y * N2;
                for (int x = 0; x < N2; x += 2) {
                    float d = cur[x] - prv[x];
                    sum_diff += d < 0.0f ? -d : d;
                    ++cnt;
                }
            }
            float mean_diff = cnt > 0 ? sum_diff / cnt : 0.0f;

            if (mean_diff <= kSnapThresh) {
                // Normal frame: per-pixel delta-gated EMA in disparity space.
                // Clamp depth away from 0 before inverting (HUD/near pixels
                // can be exactly 0 after percentile normalisation).
                constexpr float kDepthMin = 0.01f;
                for (int i = 0; i < N2 * N2; ++i) {
                    float new_d = new_norm_f32[i];
                    float prv_d = prev_norm_f32[i];
                    float delta = new_d - prv_d;
                    if (delta < 0.0f) delta = -delta;

                    if (delta > kEdgeDelta) {
                        // Hard reset: real change at this pixel (object
                        // edge moved, HUD popped on/off). Keep new_d as-is.
                        // new_norm_f32[i] already holds new_d — nothing to do.
                        continue;
                    }

                    // Stable pixel: slow EMA in disparity (1/depth) space for
                    // uniform noise budget. Parallax shift scales with
                    // oz/(hz+oz) where oz=vd*depth, so fixed depth-noise at a
                    // near pixel produces much more visible jitter than the
                    // same noise far. Inverting normalises this: equal
                    // disparity deltas ≈ equal parallax errors at any range.
                    float disp_new   = 1.0f / (new_d > kDepthMin ? new_d : kDepthMin);
                    float disp_prv   = 1.0f / (prv_d > kDepthMin ? prv_d : kDepthMin);
                    float disp_blend = kAlphaSlow * disp_new + (1.0f - kAlphaSlow) * disp_prv;
                    float blended    = 1.0f / disp_blend;
                    new_norm_f32[i]  = blended < 0.0f ? 0.0f :
                                       blended > 1.0f ? 1.0f : blended;
                }
            }
            // else: mean_diff > kSnapThresh → scene cut, keep new_norm_f32 as-is (alpha=1)
        }
        prev_norm_f32 = new_norm_f32;   // save BEFORE spatial processing

        // (No 3×3 median filter.)
        // A previous revision ran std::nth_element on a 9-element window for
        // every one of 268k pixels per inference. Profiled cost: ~20–30 ms of
        // postprocess time, which by itself was enough to drop the depth rate
        // from 10 Hz to ~2 Hz on our target machine. Depth Anything V2 Small
        // does not emit salt-and-pepper impulses in practice (its disparity
        // output is already spatially continuous on a fine grid), so the
        // median was removing noise that wasn't there while eating the
        // inference budget. Residual noise on flat surfaces is now absorbed
        // by the per-pixel edge-gated temporal EMA (step 3) which does the
        // job without touching spatial detail and without the nth_element
        // cost.

        // (No post-median spatial smoothing.)
        // The 3×3 median above already suppresses single-pixel outliers.
        // An earlier 9-tap joint-bilateral starved the worker (→ 2 Hz
        // depth), and the follow-up 5-tap Gaussian softened silhouettes
        // enough that the parallax shader had nothing to grip — "no depth,
        // a bit blurry". The per-pixel edge-gated temporal EMA (step 3)
        // already removes inference-to-inference noise on flat surfaces;
        // we rely on it as the primary denoiser so depth detail survives.

        // ── 5. Adaptive std-based contrast normalisation. ──
        //
        // WHY: Depth Anything V2 compresses distant regions — past a
        // ~scene-dependent distance the model outputs near-identical
        // disparity values for everything. The percentile norm in step 1
        // maps [p2, p98] to [0,1] but does nothing about *variance within*
        // that range. On wall-dominated scenes the wall cluster sits in a
        // narrow band (std ≈ 0.05) → every wall pixel shifts by the same
        // fraction → the wall reads as a flat translating sheet under head
        // motion ("the wall has no depth").
        //
        // A fixed contrast factor (previously kContrast=1.6) doesn't solve
        // this: if the wall IS the mean, (wall_d − mean) is already ~0, so
        // 1.6× of ~0 is still ~0. Fixed gain only helps scenes whose depth
        // spread is already large, which is exactly when it's not needed.
        //
        // FIX: measure the scene's depth std on a 1/4 subsample and apply
        // the gain needed to reach a target output std (kTargetStd). Scenes
        // with a tight depth cluster (a wall, a closeup) get aggressive
        // stretching; scenes with a naturally wide distribution get left
        // alone. Gain is capped at kMaxGain to avoid blowing up model noise
        // on pathologically flat views.
        //
        // Stretching around the mean (not 0.5) keeps the dominant depth
        // centred while fanning deviations outward — on a mostly-far scene
        // the wall cluster spreads into a visible range AND the closer
        // foreground gets pushed toward 0 so every depth layer gains shift
        // differentiation. Clamping to [0,1] is fine: clipped pixels were
        // outer-tail outliers that the shift formula would saturate anyway.
        //
        // Target std 0.22 was picked as ~σ of well-layered outdoor scenes —
        // enough to resolve wall depth variation without amplifying noise
        // on flat regions past the kMaxGain ceiling.
        if (mode <= 2) {
            const int N2 = DepthInferencer::kModelSize;

            // Mean + variance on 1/4 subsample (single pass, Welford-style
            // by pulling Σx and Σx² then computing σ² = E[x²] − E[x]²).
            double sum   = 0.0;
            double sum_sq = 0.0;
            int    cnt   = 0;
            for (int y = 0; y < N2; y += 2) {
                const float* row = new_norm_f32.data() + y * N2;
                for (int x = 0; x < N2; x += 2) {
                    double v = row[x];
                    sum    += v;
                    sum_sq += v * v;
                    ++cnt;
                }
            }
            float mean = cnt > 0 ? static_cast<float>(sum / cnt) : 0.5f;
            float var  = cnt > 0
                ? static_cast<float>(sum_sq / cnt - (sum / cnt) * (sum / cnt))
                : 0.0f;
            if (var < 0.0f) var = 0.0f;       // fp round-off
            float std_ = std::sqrt(var);

            const float kTargetStd = mode == 2 ? 0.20f : (mode == 1 ? 0.18f : 0.22f);
            constexpr float kMinGain = 1.0f;
            const float kMaxGain = mode == 2 ? 2.2f : (mode == 1 ? 2.5f : 3.5f);
            constexpr float kStdFloor  = 1e-4f;

            float gain = kTargetStd / (std_ > kStdFloor ? std_ : kStdFloor);
            if (gain < kMinGain) gain = kMinGain;
            if (gain > kMaxGain) gain = kMaxGain;

            for (int i = 0; i < N2 * N2; ++i) {
                float v = (new_norm_f32[i] - mean) * gain + mean;
                new_norm_f32[i] = v < 0.0f ? 0.0f :
                                  v > 1.0f ? 1.0f : v;
            }
        }

        // ── 6. Pack to fp16 for GPU upload. ──
        dst.assign(N * N, 0);
        for (size_t i = 0; i < new_norm_f32.size(); ++i) {
            dst[i] = float_to_half(new_norm_f32[i]);
        }
    }

    // MAIN THREAD: kick off one frame's depth update. Does the (fast) GPU→CPU
    // staging copy + preprocess synchronously, hands fp32 tensor off to the
    // worker, and uploads the worker's latest finished depth (if any) into
    // depth_tex. ORT Run itself runs on the worker and does NOT block Present.
    //
    // Frame-skip policy: if the worker is still processing the previous frame
    // (input_pending is true), we SKIP the expensive GPU→CPU readback entirely.
    // Doing the readback+map every frame (60fps) while the worker only consumes
    // at ~10fps stalls the GPU pipeline 60×/s for nothing. By skipping the
    // readback when busy, we reduce the D3D11_MAP_READ stalls to ~10×/s.
    bool run_once(ID3D11Texture2D* captured) {
        // 1. Always drain any finished output first, regardless of worker state.
        std::vector<uint16_t> drained_upload;
        bool worker_busy;
        {
            std::lock_guard<std::mutex> lk(m);
            if (stop.load()) {
                last_err = "DepthInferencer is stopping";
                return false;
            }
            worker_busy = input_pending;
            if (output_ready) {
                drained_upload.swap(ready_upload_fp16);
                output_ready = false;
            }
        }
        if (!drained_upload.empty()) {
            // New inference arrived: copy current → prev, upload new → current,
            // then reset blend_t to 0 so the shader interpolates from prev to
            // current over kBlendFrames render frames (~167ms at 60fps).
            // This hides the 10Hz depth update as a smooth 60Hz transition.
            const int N = DepthInferencer::kModelSize;
            ctx->CopyResource(depth_prev_tex, depth_tex);
            ctx->UpdateSubresource(depth_tex, 0, nullptr,
                                   drained_upload.data(),
                                   N * sizeof(uint16_t), 0);
            blend_t = 0.0f;
            has_valid_depth = true;
        }

        // 2. Skip readback if worker is still chewing on the previous frame.
        //    This prevents a D3D11_MAP_READ GPU-pipeline stall at 60fps.
        if (worker_busy) return true;

        // 3. GPU -> CPU staging copy + map. Runs only at inference cadence (~10Hz).
        //    Map(D3D11_MAP_READ) stalls until the copy completes — acceptable at
        //    10Hz (~100ms budget) but fatal for game perf at 60Hz.
        ctx->CopyResource(stage_bgra, captured);
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        HRESULT hr = ctx->Map(stage_bgra, 0, D3D11_MAP_READ, 0, &mapped);
        if (FAILED(hr)) { last_err = "Map(staging) failed"; return false; }
        preprocess(static_cast<const uint8_t*>(mapped.pData), int(mapped.RowPitch));
        ctx->Unmap(stage_bgra, 0);

        // 4. Hand off freshest input to worker.
        {
            std::lock_guard<std::mutex> lk(m);
            if (stop.load()) {
                last_err = "DepthInferencer is stopping";
                return false;
            }
            pending_input_f32.swap(scratch_input_f32);
            input_pending = true;
        }
        cv_work.notify_one();
        return true;
    }

    // WORKER THREAD: blocks on cv_work for new input, runs ORT, publishes
    // postprocessed depth back to main thread. Runs until stop is set.
    void worker_loop() {
        Ort::MemoryInfo mem = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemTypeDefault);

        while (true) {
            // Wait for input or shutdown.
            {
                std::unique_lock<std::mutex> lk(m);
                cv_work.wait(lk, [&]{ return input_pending || stop.load(); });
                if (stop.load()) return;
                running_input_f32.swap(pending_input_f32);
                input_pending = false;
            }

            // --- ORT Run (slow, ~100ms). OFF the critical path. ---
            std::vector<uint16_t> produced_upload;
            bool ok = true;
            std::string err_copy;
            try {
                const int N = DepthInferencer::kModelSize;
                std::array<int64_t, 4> in_shape = {1, 3, N, N};
                Ort::Value in_tensor = Ort::Value::CreateTensor<float>(
                    mem, running_input_f32.data(), running_input_f32.size(),
                    in_shape.data(), in_shape.size());

                const char* in_names[]  = {input_name.c_str()};
                const char* out_names[] = {output_name.c_str()};
                auto outs = session->Run(*run_options,
                                         in_names, &in_tensor, 1,
                                         out_names, 1);
                if (outs.size() != 1) {
                    err_copy = "Run returned no outputs";
                    ok = false;
                } else {
                    auto& o = outs[0];
                    auto ti = o.GetTensorTypeAndShapeInfo();
                    auto shape = ti.GetShape();
                    if (shape.size() == 3 && shape[0] == 1) {
                        out_h = static_cast<int>(shape[1]);
                        out_w = static_cast<int>(shape[2]);
                    } else if (shape.size() == 4 && shape[0] == 1 && shape[1] == 1) {
                        out_h = static_cast<int>(shape[2]);
                        out_w = static_cast<int>(shape[3]);
                    } else {
                        char buf[128];
                        std::snprintf(buf, sizeof(buf), "Unexpected output rank %zu", shape.size());
                        err_copy = buf;
                        ok = false;
                    }
                    if (ok) {
                        const size_t count = static_cast<size_t>(out_h) * out_w;
                        output_f32.assign(count, 0.0f);
                        std::memcpy(output_f32.data(),
                                    o.GetTensorData<float>(),
                                    count * sizeof(float));
                        postprocess(produced_upload);
                    }
                }
            } catch (const Ort::Exception& e) {
                err_copy = std::string("ORT Run exception: ") + e.what();
                ok = false;
            } catch (const std::exception& e) {
                err_copy = std::string("Worker exception: ") + e.what();
                ok = false;
            }

            // Publish result (or error) back to main thread.
            {
                std::lock_guard<std::mutex> lk(m);
                if (ok) {
                    ready_upload_fp16 = std::move(produced_upload);
                    output_ready = true;
                } else {
                    last_err = std::move(err_copy);
                }
            }
            if (ok) inferences.fetch_add(1, std::memory_order_relaxed);
        }
    }

    void cleanup() {
        // Stop worker first — it holds a reference to the ORT Session.
        if (worker.joinable()) {
            {
                std::lock_guard<std::mutex> lk(m);
                stop.store(true);
                input_pending = false;
            }
            // DirectML may be blocked in Run while its D3D device is being
            // removed. ORT's termination flag is thread-safe and makes that
            // Run return, allowing the worker join to complete.
            if (run_options) {
                try {
                    run_options->SetTerminate();
                } catch (...) {
                    // cleanup/destruction must not throw; join remains the
                    // final synchronization point for the session lifetime.
                }
            }
            cv_work.notify_all();
            worker.join();
        }
        if (depth_prev_srv) { depth_prev_srv->Release(); depth_prev_srv = nullptr; }
        if (depth_srv) { depth_srv->Release(); depth_srv = nullptr; }
        if (depth_prev_tex) { depth_prev_tex->Release(); depth_prev_tex = nullptr; }
        if (depth_tex) { depth_tex->Release(); depth_tex = nullptr; }
        if (stage_bgra) { stage_bgra->Release(); stage_bgra = nullptr; }
        session.reset();
        run_options.reset();
        opts.reset();
        env.reset();
        input_pending = false;
        output_ready = false;
        pending_input_f32.clear();
        running_input_f32.clear();
        ready_upload_fp16.clear();
        dev = nullptr;
        ctx = nullptr;
        cap_w = 0;
        cap_h = 0;
    }
};

// Static member definitions (out-of-class storage required pre-C++17 for non-inline).
constexpr float DepthInferImpl::kMean[3];
constexpr float DepthInferImpl::kStd[3];

// ── Public facade ────────────────────────────────────────────────────────────
DepthInferencer::DepthInferencer() : impl_(std::make_unique<DepthInferImpl>()) {}

DepthInferencer::~DepthInferencer() {
    if (impl_) impl_->cleanup();
}

bool DepthInferencer::init(ID3D11Device* dev, ID3D11DeviceContext* ctx,
                           const std::wstring& model_path,
                           int capture_w, int capture_h) {
    if (!impl_ || !dev || !ctx || capture_w <= 0 || capture_h <= 0) return false;
    impl_->cleanup();
    {
        std::lock_guard<std::mutex> lock(impl_->m);
        impl_->input_pending = false;
        impl_->output_ready = false;
        impl_->last_err.clear();
    }
    impl_->stop.store(false, std::memory_order_relaxed);
    impl_->inferences.store(0, std::memory_order_relaxed);
    impl_->dev = dev;
    impl_->ctx = ctx;
    impl_->cap_w = capture_w;
    impl_->cap_h = capture_h;
    if (!impl_->create_d3d_resources()) {
        impl_->cleanup();
        return false;
    }
    if (!impl_->create_ort_session(model_path)) {
        impl_->cleanup();
        return false;
    }
    // Session is ready — spin up the async inference worker.
    impl_->worker = std::thread([impl = impl_.get()] { impl->worker_loop(); });
    return true;
}

bool DepthInferencer::run(ID3D11Texture2D* captured_bgra8) {
    if (!impl_ || !impl_->session) {
        impl_->last_err = "DepthInferencer not initialized";
        return false;
    }
    return impl_->run_once(captured_bgra8);
}

void DepthInferencer::set_performance_mode(uint32_t mode) {
    if (!impl_) return;
    if (mode > 2) mode = 1;
    impl_->performance_mode.store(mode, std::memory_order_relaxed);
}

uint32_t DepthInferencer::performance_mode() const {
    if (!impl_) return 1;
    return impl_->performance_mode.load(std::memory_order_relaxed);
}

ID3D11ShaderResourceView* DepthInferencer::depth_srv() const {
    return impl_ ? impl_->depth_srv : nullptr;
}

ID3D11ShaderResourceView* DepthInferencer::depth_prev_srv() const {
    return impl_ ? impl_->depth_prev_srv : nullptr;
}

float DepthInferencer::depth_blend() const {
    if (!impl_) return 1.0f;
    // Smoothstep: 3t² − 2t³. Zero first-derivative at t=0 and t=1, so the
    // depth texture morph starts and ends with no visible velocity — the
    // discrete 10 Hz update slides past the eye instead of snapping.
    float t = impl_->blend_t;
    if (t < 0.0f) t = 0.0f;
    else if (t > 1.0f) t = 1.0f;
    return t * t * (3.0f - 2.0f * t);
}

void DepthInferencer::advance_blend() {
    if (!impl_) return;
    impl_->blend_t += 1.0f / DepthInferImpl::kBlendFrames;
    if (impl_->blend_t > 1.0f) impl_->blend_t = 1.0f;
}

const char* DepthInferencer::last_error() const {
    return impl_ ? impl_->last_err.c_str() : "";
}

uint64_t DepthInferencer::inferences_completed() const {
    return impl_ ? impl_->inferences.load(std::memory_order_relaxed) : 0;
}

float DepthInferencer::depth_crop_x0_uv() const {
    if (!impl_ || impl_->cap_w <= 0) return 0.0f;
    return (float)impl_->crop_x0 / (float)impl_->cap_w;
}

float DepthInferencer::depth_crop_w_uv() const {
    if (!impl_ || impl_->cap_w <= 0) return 1.0f;
    return (float)impl_->crop_w_eff / (float)impl_->cap_w;
}
