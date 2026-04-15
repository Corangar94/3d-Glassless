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

// ── Impl ─────────────────────────────────────────────────────────────────────
struct DepthInferImpl {
    // D3D11 resources (device/context NOT owned; borrowed from overlay).
    ID3D11Device*         dev          = nullptr;
    ID3D11DeviceContext*  ctx          = nullptr;

    // Full-resolution staging texture for CPU readback of the captured frame.
    ID3D11Texture2D*      stage_bgra   = nullptr;
    int                   cap_w        = 0;
    int                   cap_h        = 0;

    // Letterbox dimensions: the content region inside the kModelSize×kModelSize
    // model input that holds the aspect-ratio-correct resize of cap_w×cap_h.
    // Pixels outside this region are left at 0.0f (ImageNet-normalized grey).
    int                   lb_off_x     = 0;
    int                   lb_off_y     = 0;
    int                   lb_w         = 0;
    int                   lb_h         = 0;

    // Output: R16F depth texture (kModelSize x kModelSize) + SRV.
    ID3D11Texture2D*      depth_tex    = nullptr;
    ID3D11ShaderResourceView* depth_srv = nullptr;

    // ORT state
    std::unique_ptr<Ort::Env>            env;
    std::unique_ptr<Ort::SessionOptions> opts;
    std::unique_ptr<Ort::Session>        session;
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

    // ImageNet normalization (Depth Anything V2 uses standard ImageNet stats)
    static constexpr float kMean[3] = {0.485f, 0.456f, 0.406f};
    static constexpr float kStd[3]  = {0.229f, 0.224f, 0.225f};

    bool create_d3d_resources() {
        // Compute letterbox dimensions: aspect-ratio-correct resize of cap into
        // kModelSize×kModelSize.  Uniform 0.0f padding (ImageNet-normalised grey).
        // For a 5120×1440 ultrawide: scale=0.101 → lb_w=518, lb_h=145, off=(0,186).
        {
            const int N = DepthInferencer::kModelSize;
            float scale = std::min((float)N / cap_w, (float)N / cap_h);
            lb_w = std::max(1, (int)(cap_w * scale));
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

        // Depth texture at model output resolution. Shader reads R16F.
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
        if (FAILED(hr)) { last_err = "CreateTexture2D(depth R16F) failed"; return false; }

        D3D11_SHADER_RESOURCE_VIEW_DESC srv = {};
        srv.Format = dd.Format;
        srv.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        srv.Texture2D.MipLevels = 1;
        hr = dev->CreateShaderResourceView(depth_tex, &srv, &depth_srv);
        if (FAILED(hr)) { last_err = "CreateShaderResourceView(depth) failed"; return false; }

        // Initialize depth texture to 0.5 so the parallax shader produces
        // a sensible fallback on the first frame before inference completes.
        const int N = DepthInferencer::kModelSize * DepthInferencer::kModelSize;
        std::vector<uint16_t> half_filled(N, float_to_half(0.5f));
        ctx->UpdateSubresource(depth_tex, 0, nullptr,
                               half_filled.data(),
                               DepthInferencer::kModelSize * sizeof(uint16_t),
                               0);
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

    // CPU: downsample full-res captured BGRA8 -> NCHW fp32 RGB tensor with
    // ImageNet normalization and aspect-ratio-correct letterboxing.
    //
    // Instead of squashing cap_w×cap_h into N×N (which distorts a 32:9
    // ultrawide by 3.6× horizontally vs vertically), we scale uniformly so the
    // larger dimension fills N pixels, centre the result, and leave the bars at
    // 0.0f — which equals ImageNet-normalised mean grey, a neutral value for the
    // depth model.  For a 5120×1440 capture:
    //   scale=0.101 → content 518×145 at offset (0,186) in 518×518.
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
                int sx = (ox * cap_w) / lb_w;
                if (sx >= cap_w) sx = cap_w - 1;
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
    // Depth Anything V2 outputs relative (unbounded, monotonic) depth —
    // higher = farther. Naive per-frame min/max remapping causes visible
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
                float v = (in_row[sx] - vlo) / range;
                if (v < 0.0f) v = 0.0f;
                else if (v > 1.0f) v = 1.0f;
                out_row[ox] = v;
            }
        }

        // ── 3. EMA blend against previous frame (if any). ──
        // alpha = weight of the NEW frame. 0.4 gives noticeable responsiveness
        // but still suppresses high-freq flicker between 10Hz inferences.
        // We store the UNBLURRED result so spatial blur doesn't compound
        // across frames (→ increasingly-smeared depth).
        // α=0.2 → heavy temporal smoothing. Depth from monocular models
        // flickers pixel-to-pixel between frames; that flicker is the
        // primary source of "watery shimmer" in the warped output. A
        // stronger EMA trades a small lag (~4 frames at 10 Hz = 400 ms)
        // for visibly steadier depth at object edges.
        constexpr float kAlpha = 0.2f;
        if (prev_norm_f32.size() == new_norm_f32.size()) {
            for (size_t i = 0; i < new_norm_f32.size(); ++i) {
                new_norm_f32[i] = kAlpha * new_norm_f32[i]
                                + (1.0f - kAlpha) * prev_norm_f32[i];
            }
        }
        prev_norm_f32 = new_norm_f32;   // save BEFORE spatial blur

        // ── 4. 3x3 median filter. ──
        // Previous attempts:
        //   - Gaussian blur: bled foreground depth into background across
        //     edges → "watery halo" around foreground silhouettes.
        //   - Max filter (foreground dilation): pushed the pixels *just
        //     outside* a foreground edge to the foreground's depth. The
        //     shader then warped those background-colored pixels with
        //     foreground parallax → a ghost-copy of each FG edge dragging
        //     with head motion (the "doubling").
        //
        // Median removes salt-and-pepper outliers from the model's raw
        // depth predictions WITHOUT shifting edge positions: the median
        // of a 3x3 patch straddling an edge is whichever side has 5+
        // pixels, so the edge stays exactly where the model placed it.
        //
        // Single pass, ~9 comparisons/pixel via partial-sort nth_element;
        // ~2.4M ops per 518² frame at 10 Hz — still trivial.
        blur_tmp_f32.assign(N * N, 0.0f);
        for (int y = 0; y < N; ++y) {
            int ym = (y > 0)       ? (y - 1) : 0;
            int yp = (y < N - 1)   ? (y + 1) : N - 1;
            const float* r_m = new_norm_f32.data() + ym * N;
            const float* r_0 = new_norm_f32.data() + y  * N;
            const float* r_p = new_norm_f32.data() + yp * N;
            float*       out_row = blur_tmp_f32.data() + y * N;
            for (int x = 0; x < N; ++x) {
                int xm = (x > 0)     ? (x - 1) : 0;
                int xp = (x < N - 1) ? (x + 1) : N - 1;
                float v[9] = {
                    r_m[xm], r_m[x], r_m[xp],
                    r_0[xm], r_0[x], r_0[xp],
                    r_p[xm], r_p[x], r_p[xp],
                };
                std::nth_element(v, v + 4, v + 9);
                out_row[x] = v[4];
            }
        }
        std::swap(new_norm_f32, blur_tmp_f32);

        // ── 5. Pack to fp16 for GPU upload. ──
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
    // Frame-drop policy: if the worker is still chewing on a previous frame
    // (input_pending already true), we OVERWRITE the pending input with the
    // newer preprocess — the worker will pick the freshest input when it's
    // ready. Dropping stale inputs is preferable to queueing a backlog.
    bool run_once(ID3D11Texture2D* captured) {
        // 1. GPU -> CPU staging copy (cheap enqueue) + map (may briefly wait
        //    on GPU to finish the copy; typically <1–2ms for desktop sizes).
        ctx->CopyResource(stage_bgra, captured);
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        HRESULT hr = ctx->Map(stage_bgra, 0, D3D11_MAP_READ, 0, &mapped);
        if (FAILED(hr)) { last_err = "Map(staging) failed"; return false; }
        preprocess(static_cast<const uint8_t*>(mapped.pData), int(mapped.RowPitch));
        ctx->Unmap(stage_bgra, 0);

        // 2. Hand off freshest input to worker; drain any finished output.
        std::vector<uint16_t> drained_upload;
        {
            std::lock_guard<std::mutex> lk(m);
            pending_input_f32.swap(scratch_input_f32);
            input_pending = true;
            if (output_ready) {
                drained_upload.swap(ready_upload_fp16);
                output_ready = false;
            }
        }
        cv_work.notify_one();

        // 3. Upload finished depth (done OUTSIDE the lock so we don't hold it
        //    across a GPU call — UpdateSubresource is effectively a memcpy on
        //    DEFAULT textures but the DC is shared with the render pass).
        if (!drained_upload.empty()) {
            const int N = DepthInferencer::kModelSize;
            ctx->UpdateSubresource(depth_tex, 0, nullptr,
                                   drained_upload.data(),
                                   N * sizeof(uint16_t),
                                   0);
            has_valid_depth = true;
        }
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
                auto outs = session->Run(Ort::RunOptions{nullptr},
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
            }
            cv_work.notify_all();
            worker.join();
        }
        if (depth_srv) { depth_srv->Release(); depth_srv = nullptr; }
        if (depth_tex) { depth_tex->Release(); depth_tex = nullptr; }
        if (stage_bgra){ stage_bgra->Release(); stage_bgra = nullptr; }
        session.reset();
        opts.reset();
        env.reset();
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
    impl_->dev = dev;
    impl_->ctx = ctx;
    impl_->cap_w = capture_w;
    impl_->cap_h = capture_h;
    if (!impl_->create_d3d_resources()) return false;
    if (!impl_->create_ort_session(model_path)) return false;
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

ID3D11ShaderResourceView* DepthInferencer::depth_srv() const {
    return impl_ ? impl_->depth_srv : nullptr;
}

const char* DepthInferencer::last_error() const {
    return impl_ ? impl_->last_err.c_str() : "";
}

uint64_t DepthInferencer::inferences_completed() const {
    return impl_ ? impl_->inferences.load(std::memory_order_relaxed) : 0;
}
