// overlay/depth_infer.cpp — see depth_infer.h for pipeline overview.

#include "depth_infer.h"
#include <d3dcompiler.h>

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
#include <chrono>
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

    // GPU letterbox/tile reduction followed by a nonblocking staging ring.
    // For 5120x1440 this copies/maps 1036x518 instead of 5120x1440.
    static constexpr int kReadbackRingSize = 3;
    ID3D11Texture2D*      compact_bgra = nullptr;
    ID3D11RenderTargetView* compact_rtv = nullptr;
    ID3D11Texture2D*      stage_bgra[kReadbackRingSize] = {};
    bool                  stage_pending[kReadbackRingSize] = {};
    int                   stage_write = 0;
    int                   stage_read = 0;
    int                   stage_count = 0;
    ID3D11ShaderResourceView* compact_input_srv = nullptr;
    ID3D11Texture2D*      compact_input_tex = nullptr;
    ID3D11VertexShader*   compact_vs = nullptr;
    ID3D11PixelShader*    compact_ps = nullptr;
    ID3D11SamplerState*   compact_sampler = nullptr;
    ID3D11Buffer*         compact_cb = nullptr;
    int                   cap_w        = 0;
    int                   cap_h        = 0;

    // Ultrawide captures are split into adjacent <=16:9 tiles. Each tile keeps
    // useful vertical model resolution; their depth maps are stitched into a
    // full-width texture so peripheral pixels never reuse a clamped center edge.
    int                   tile_count   = 1;
    int                   tile_w       = 0;
    int                   tile_overlap = 0;
    int                   crop_x0      = 0;
    int                   crop_w_eff   = 0;

    // Letterbox dimensions: the content region inside the kModelSize×kModelSize
    // model input that holds the aspect-ratio-correct resize of crop_w_eff×cap_h.
    // Pixels outside this region are left at 0.0f (ImageNet-normalized grey).
    int                   lb_off_x     = 0;
    int                   lb_off_y     = 0;
    int                   lb_w         = 0;
    int                   lb_h         = 0;

    // Two R16F depth textures for time-based interpolation.
    // When a new inference arrives, the old texture becomes "prev" and the new
    // one becomes "current". The shader lerps between them using depth_blend,
    // which advances 0→1 over a fixed wall-clock interval
    // — slightly wider than one full ~100 ms inference cycle so successive
    // inferences' blend windows overlap and there is never a discontinuity at
    // the transition. depth_blend() additionally applies smoothstep to `t`
    // so the depth update has zero first-derivative at both endpoints, hiding
    // the discrete update in the middle of head motion.
    static constexpr float kBlendDurationSec = 0.20f;
    ID3D11Texture2D*          depth_tex      = nullptr;
    ID3D11ShaderResourceView* depth_srv      = nullptr;
    ID3D11Texture2D*          depth_prev_tex = nullptr;
    ID3D11ShaderResourceView* depth_prev_srv = nullptr;
    std::chrono::steady_clock::time_point blend_started{};
    bool                      blend_active = false;

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
    std::vector<std::vector<float>>      prev_norm_tiles;
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
    bool                                 worker_running = false;
    bool                                 output_ready  = false;   // new depth waiting for main to upload
    std::atomic<bool>                    stop{false};
    std::atomic<uint64_t>                inferences{0};    // completed Run calls (for diagnostics)
    std::atomic<uint32_t>                performance_mode{1}; // 0=quality, 1=balanced, 2=fast

    // ImageNet normalization (Depth Anything V2 uses standard ImageNet stats)
    static constexpr float kMean[3] = {0.485f, 0.456f, 0.406f};
    static constexpr float kStd[3]  = {0.229f, 0.224f, 0.225f};

    struct CompactCB {
        float cap_w, cap_h, tile_w, tile_count;
        float lb_off_x, lb_off_y, lb_w, lb_h;
        float model_size, overlap, padding[2];
    };

    bool create_compact_pipeline() {
        static constexpr const char* vs_src = R"(
struct O { float4 p:SV_Position; float2 uv:TEXCOORD0; };
O main(uint id:SV_VertexID) {
    O o; o.uv=float2((id&1)?1:0,(id&2)?1:0);
    o.p=float4(o.uv.x*2-1,1-o.uv.y*2,0,1); return o;
})";
        static constexpr const char* ps_src = R"(
Texture2D Src:register(t0); SamplerState Smp:register(s0);
cbuffer C:register(b0) {
 float capW,capH,tileW,tileCount;
 float lbOffX,lbOffY,lbW,lbH;
 float modelSize,overlap; float2 padding;
};
struct I { float4 p:SV_Position; float2 uv:TEXCOORD0; };
float4 main(I i):SV_Target {
 float scaledX=i.uv.x*tileCount;
 float tile=min(floor(scaledX),tileCount-1);
 float2 local=float2(frac(scaledX),i.uv.y);
 float2 pixel=local*modelSize;
 if(pixel.x<lbOffX || pixel.x>=lbOffX+lbW ||
    pixel.y<lbOffY || pixel.y>=lbOffY+lbH)
   return float4(0.485,0.456,0.406,1);
 float logicalStart=tile*tileW;
 float logicalEnd=min(capW,logicalStart+tileW);
 float srcStart=max(0.0,logicalStart-overlap);
 float srcEnd=min(capW,logicalEnd+overlap);
 float actualW=srcEnd-srcStart;
 float2 content=(pixel-float2(lbOffX,lbOffY))/float2(lbW,lbH);
 float2 srcUv=float2((srcStart+content.x*actualW)/capW,content.y);
 return Src.SampleLevel(Smp,saturate(srcUv),0);
})";
        ID3DBlob* vs_blob = nullptr; ID3DBlob* ps_blob = nullptr; ID3DBlob* errors = nullptr;
        HRESULT hr = D3DCompile(vs_src, std::strlen(vs_src), "depth_compact_vs", nullptr,
            nullptr, "main", "vs_5_0", D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &vs_blob, &errors);
        if (errors) errors->Release();
        if (FAILED(hr)) { last_err = "Compile compact VS failed"; return false; }
        hr = D3DCompile(ps_src, std::strlen(ps_src), "depth_compact_ps", nullptr,
            nullptr, "main", "ps_5_0", D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &ps_blob, &errors);
        if (errors) errors->Release();
        if (FAILED(hr)) { vs_blob->Release(); last_err = "Compile compact PS failed"; return false; }
        hr = dev->CreateVertexShader(vs_blob->GetBufferPointer(), vs_blob->GetBufferSize(), nullptr, &compact_vs);
        if (SUCCEEDED(hr)) hr = dev->CreatePixelShader(ps_blob->GetBufferPointer(), ps_blob->GetBufferSize(), nullptr, &compact_ps);
        vs_blob->Release(); ps_blob->Release();
        if (FAILED(hr)) { last_err = "Create compact shaders failed"; return false; }
        D3D11_SAMPLER_DESC sm = {};
        sm.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
        sm.AddressU = sm.AddressV = sm.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
        sm.MaxLOD = D3D11_FLOAT32_MAX;
        hr = dev->CreateSamplerState(&sm, &compact_sampler);
        if (FAILED(hr)) { last_err = "Create compact sampler failed"; return false; }
        D3D11_BUFFER_DESC bd = {};
        bd.ByteWidth = sizeof(CompactCB); bd.Usage = D3D11_USAGE_DEFAULT;
        bd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
        hr = dev->CreateBuffer(&bd, nullptr, &compact_cb);
        if (FAILED(hr)) { last_err = "Create compact constants failed"; return false; }
        return true;
    }

    int resolve_dml_device_id() const {
        IDXGIDevice* dxgi_device = nullptr;
        IDXGIAdapter* selected = nullptr;
        DXGI_ADAPTER_DESC selected_desc = {};
        if (!dev || FAILED(dev->QueryInterface(__uuidof(IDXGIDevice),
                reinterpret_cast<void**>(&dxgi_device)))) return 0;
        HRESULT hr = dxgi_device->GetAdapter(&selected);
        dxgi_device->Release();
        if (FAILED(hr) || !selected || FAILED(selected->GetDesc(&selected_desc))) {
            if (selected) selected->Release();
            return 0;
        }
        selected->Release();

        IDXGIFactory1* factory = nullptr;
        if (FAILED(CreateDXGIFactory1(__uuidof(IDXGIFactory1),
                reinterpret_cast<void**>(&factory)))) return 0;
        int result = 0;
        for (UINT index = 0;; ++index) {
            IDXGIAdapter1* candidate = nullptr;
            if (factory->EnumAdapters1(index, &candidate) == DXGI_ERROR_NOT_FOUND) break;
            if (!candidate) continue;
            DXGI_ADAPTER_DESC1 desc = {};
            if (SUCCEEDED(candidate->GetDesc1(&desc))
                && desc.AdapterLuid.HighPart == selected_desc.AdapterLuid.HighPart
                && desc.AdapterLuid.LowPart == selected_desc.AdapterLuid.LowPart) {
                result = static_cast<int>(index);
                candidate->Release();
                break;
            }
            candidate->Release();
        }
        factory->Release();
        return result;
    }

    bool create_d3d_resources() {
        const int max_tile_w = std::max(1, cap_h * 16 / 9);
        tile_count = std::max(1, (cap_w + max_tile_w - 1) / max_tile_w);
        tile_w = (cap_w + tile_count - 1) / tile_count;
        tile_overlap = tile_count > 1 ? std::max(1, tile_w / 10) : 0;
        crop_w_eff = tile_w;
        crop_x0 = 0;
        prev_norm_tiles.resize(tile_count);

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

        if (!create_compact_pipeline()) return false;

        // Exact model-sized stitched render target and nonblocking staging ring.
        D3D11_TEXTURE2D_DESC sd = {};
        sd.Width = DepthInferencer::kModelSize * tile_count;
        sd.Height = DepthInferencer::kModelSize;
        sd.MipLevels = 1;
        sd.ArraySize = 1;
        sd.Format = DXGI_FORMAT_B8G8R8A8_UNORM;
        sd.SampleDesc.Count = 1;
        sd.Usage = D3D11_USAGE_DEFAULT;
        sd.BindFlags = D3D11_BIND_RENDER_TARGET;
        HRESULT hr = dev->CreateTexture2D(&sd, nullptr, &compact_bgra);
        if (FAILED(hr)) { last_err = "CreateTexture2D(compact BGRA) failed"; return false; }
        hr = dev->CreateRenderTargetView(compact_bgra, nullptr, &compact_rtv);
        if (FAILED(hr)) { last_err = "CreateRenderTargetView(compact BGRA) failed"; return false; }
        sd.Usage = D3D11_USAGE_STAGING;
        sd.BindFlags = 0;
        sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
        for (auto& stage : stage_bgra) {
            hr = dev->CreateTexture2D(&sd, nullptr, &stage);
            if (FAILED(hr)) { last_err = "CreateTexture2D(staging ring) failed"; return false; }
        }

        // Two R16F depth textures for render-rate interpolation.
        D3D11_TEXTURE2D_DESC dd = {};
        dd.Width = DepthInferencer::kModelSize * tile_count;
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
        const int N = DepthInferencer::kModelSize * DepthInferencer::kModelSize * tile_count;
        std::vector<uint16_t> half_filled(N, float_to_half(0.5f));
        ctx->UpdateSubresource(depth_tex, 0, nullptr, half_filled.data(),
                               DepthInferencer::kModelSize * tile_count * sizeof(uint16_t), 0);
        ctx->UpdateSubresource(depth_prev_tex, 0, nullptr, half_filled.data(),
                               DepthInferencer::kModelSize * tile_count * sizeof(uint16_t), 0);
        blend_active = false;
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
            const int dml_device_id = resolve_dml_device_id();
            OrtStatus* st = OrtSessionOptionsAppendExecutionProvider_DML(*opts, dml_device_id);
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
    void preprocess_compact(const uint8_t* src, int src_pitch) {
        const int N = DepthInferencer::kModelSize;
        const size_t tile_stride = 3ull * N * N;
        scratch_input_f32.assign(tile_stride * tile_count, 0.0f);
        for (int tile = 0; tile < tile_count; ++tile) {
            float* tile_base = scratch_input_f32.data() + tile * tile_stride;
            float* dstR = tile_base + 0 * N * N;
            float* dstG = tile_base + 1 * N * N;
            float* dstB = tile_base + 2 * N * N;
            for (int y = 0; y < N; ++y) {
                const uint8_t* row = src + y * src_pitch;
                for (int x = 0; x < N; ++x) {
                    const uint8_t* px = row + (tile * N + x) * 4;
                    int idx = y * N + x;
                    dstR[idx] = (px[2] / 255.0f - kMean[0]) / kStd[0];
                    dstG[idx] = (px[1] / 255.0f - kMean[1]) / kStd[1];
                    dstB[idx] = (px[0] / 255.0f - kMean[2]) / kStd[2];
                }
            }
        }
    }

    bool render_compact(ID3D11Texture2D* captured) {
        if (captured != compact_input_tex) {
            if (compact_input_srv) { compact_input_srv->Release(); compact_input_srv = nullptr; }
            if (compact_input_tex) { compact_input_tex->Release(); compact_input_tex = nullptr; }
            HRESULT hr = dev->CreateShaderResourceView(captured, nullptr, &compact_input_srv);
            if (FAILED(hr)) { last_err = "Create compact input SRV failed"; return false; }
            compact_input_tex = captured;
            compact_input_tex->AddRef();
        }
        const int N = DepthInferencer::kModelSize;
        const CompactCB constants = {
            static_cast<float>(cap_w), static_cast<float>(cap_h),
            static_cast<float>(tile_w), static_cast<float>(tile_count),
            static_cast<float>(lb_off_x), static_cast<float>(lb_off_y),
            static_cast<float>(lb_w), static_cast<float>(lb_h),
            static_cast<float>(N), static_cast<float>(tile_overlap), {0,0}
        };
        ctx->UpdateSubresource(compact_cb, 0, nullptr, &constants, 0, 0);
        UINT viewport_count = 1;
        D3D11_VIEWPORT old_viewport = {};
        ctx->RSGetViewports(&viewport_count, &old_viewport);
        const D3D11_VIEWPORT viewport = {0, 0, static_cast<float>(N * tile_count),
            static_cast<float>(N), 0, 1};
        ctx->RSSetViewports(1, &viewport);
        ctx->OMSetRenderTargets(1, &compact_rtv, nullptr);
        ctx->VSSetShader(compact_vs, nullptr, 0);
        ctx->PSSetShader(compact_ps, nullptr, 0);
        ctx->PSSetConstantBuffers(0, 1, &compact_cb);
        ctx->PSSetShaderResources(0, 1, &compact_input_srv);
        ctx->PSSetSamplers(0, 1, &compact_sampler);
        ctx->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLESTRIP);
        ctx->Draw(4, 0);
        ID3D11ShaderResourceView* null_srv = nullptr;
        ctx->PSSetShaderResources(0, 1, &null_srv);
        ctx->OMSetRenderTargets(0, nullptr, nullptr);
        if (viewport_count) ctx->RSSetViewports(1, &old_viewport);
        return true;
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
    void postprocess(std::vector<uint16_t>& dst,
                     float shared_vlo = std::numeric_limits<float>::quiet_NaN(),
                     float shared_vhi = std::numeric_limits<float>::quiet_NaN(),
                     bool apply_contrast = true,
                     int tile_index = 0) {
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
        if (std::isfinite(shared_vlo) && std::isfinite(shared_vhi)) {
            vlo = shared_vlo;
            vhi = shared_vhi;
        } else if (samples.empty()) {
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
        const float logical_start = static_cast<float>(tile_index * tile_w);
        const float logical_end = std::min(static_cast<float>(cap_w), logical_start + tile_w);
        const float source_start = std::max(0.0f, logical_start - tile_overlap);
        const float source_end = std::min(static_cast<float>(cap_w), logical_end + tile_overlap);
        const float source_width = std::max(1.0f, source_end - source_start);
        const float tile_crop_x = (logical_start - source_start) / source_width;
        const float tile_crop_w = (logical_end - logical_start) / source_width;
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
                const float logical_u = tile_crop_x + (static_cast<float>(ox) / N) * tile_crop_w;
                int sx = cx0 + static_cast<int>(logical_u * lb_w);
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
        if (apply_contrast && mode <= 2) {
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
            worker_busy = input_pending || worker_running;
            if (output_ready) {
                drained_upload.swap(ready_upload_fp16);
                output_ready = false;
            }
        }
        if (!drained_upload.empty()) {
            // New inference arrived: copy current → prev, upload new → current,
            // then restart a wall-clock crossfade. This stays 200 ms at 60,
            // 144, or 240 Hz and also remains correct when rendering is idle.
            const int N = DepthInferencer::kModelSize;
            ctx->CopyResource(depth_prev_tex, depth_tex);
            ctx->UpdateSubresource(depth_tex, 0, nullptr,
                                   drained_upload.data(),
                                   N * tile_count * sizeof(uint16_t), 0);
            blend_started = std::chrono::steady_clock::now();
            blend_active = true;
            has_valid_depth = true;
        }

        // 2. Skip readback if worker is still chewing on the previous frame.
        //    This prevents a D3D11_MAP_READ GPU-pipeline stall at 60fps.
        if (worker_busy) return true;

        // 3. Reduce on GPU, enqueue into a three-slot staging ring, then map the
        // oldest copy without waiting. A busy GPU drops this inference input
        // rather than stalling the game's immediate context.
        // Keep at most one queued input while inference is idle. Enqueuing all
        // three slots before the worker starts would make subsequent runs use
        // frames captured ~100 ms ago instead of the newest game frame.
        if (stage_count == 0) {
            if (!render_compact(captured)) return false;
            ctx->CopyResource(stage_bgra[stage_write], compact_bgra);
            stage_pending[stage_write] = true;
            stage_write = (stage_write + 1) % kReadbackRingSize;
            ++stage_count;
        }
        if (stage_count == 0 || !stage_pending[stage_read]) return true;
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        HRESULT hr = ctx->Map(stage_bgra[stage_read], 0, D3D11_MAP_READ,
                              D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (hr == DXGI_ERROR_WAS_STILL_DRAWING) return true;
        if (FAILED(hr)) { last_err = "Map(staging ring) failed"; return false; }
        preprocess_compact(static_cast<const uint8_t*>(mapped.pData), int(mapped.RowPitch));
        ctx->Unmap(stage_bgra[stage_read], 0);
        stage_pending[stage_read] = false;
        stage_read = (stage_read + 1) % kReadbackRingSize;
        --stage_count;

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
                worker_running = true;
            }

            // --- ORT Run (slow, ~100ms). OFF the critical path. ---
            std::vector<uint16_t> produced_upload;
            bool ok = true;
            std::string err_copy;
            try {
                const int N = DepthInferencer::kModelSize;
                const size_t tile_input_count = 3ull * N * N;
                produced_upload.assign(static_cast<size_t>(N) * N * tile_count, 0);
                std::vector<std::vector<float>> raw_tiles(tile_count);
                for (int tile = 0; tile < tile_count && ok; ++tile) {
                    std::array<int64_t, 4> in_shape = {1, 3, N, N};
                    Ort::Value in_tensor = Ort::Value::CreateTensor<float>(
                        mem, running_input_f32.data() + tile * tile_input_count,
                        tile_input_count, in_shape.data(), in_shape.size());

                    const char* in_names[]  = {input_name.c_str()};
                    const char* out_names[] = {output_name.c_str()};
                    auto outs = session->Run(*run_options,
                                             in_names, &in_tensor, 1,
                                             out_names, 1);
                    if (outs.size() != 1) {
                        err_copy = "Run returned no outputs";
                        ok = false;
                        break;
                    }
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
                        raw_tiles[tile].assign(count, 0.0f);
                        std::memcpy(raw_tiles[tile].data(),
                                    o.GetTensorData<float>(),
                                    count * sizeof(float));
                    }
                }
                if (ok) {
                    // One percentile range for every tile prevents adjacent
                    // views from assigning different depths to the same scene.
                    std::vector<float> global_samples;
                    for (const auto& raw : raw_tiles) {
                        for (int y = lb_off_y; y < lb_off_y + lb_h && y < out_h; y += 2) {
                            for (int x = lb_off_x; x < lb_off_x + lb_w && x < out_w; x += 2) {
                                global_samples.push_back(raw[static_cast<size_t>(y) * out_w + x]);
                            }
                        }
                    }
                    float global_lo = 0.0f, global_hi = 1.0f;
                    if (!global_samples.empty()) {
                        const size_t n = global_samples.size();
                        const size_t lo = std::min<size_t>(n - 1, n * 2 / 100);
                        const size_t hi = std::min<size_t>(n - 1, n * 98 / 100);
                        std::nth_element(global_samples.begin(), global_samples.begin() + lo, global_samples.end());
                        global_lo = global_samples[lo];
                        std::nth_element(global_samples.begin() + lo + 1, global_samples.begin() + hi, global_samples.end());
                        global_hi = global_samples[hi];
                    }
                    for (int tile = 0; tile < tile_count; ++tile) {
                        output_f32 = std::move(raw_tiles[tile]);
                        std::vector<uint16_t> tile_upload;
                        prev_norm_f32.swap(prev_norm_tiles[tile]);
                        postprocess(tile_upload, global_lo, global_hi, tile_count == 1, tile);
                        prev_norm_f32.swap(prev_norm_tiles[tile]);
                        for (int y = 0; y < N; ++y) {
                            std::copy_n(tile_upload.data() + static_cast<size_t>(y) * N, N,
                                produced_upload.data() + static_cast<size_t>(y) * N * tile_count
                                    + static_cast<size_t>(tile) * N);
                        }
                    }
                    if (tile_count > 1) {
                        // Apply one contrast transform over the stitched frame.
                        double sum = 0.0, sum_sq = 0.0; size_t count = 0;
                        for (size_t i = 0; i < produced_upload.size(); i += 4) {
                            const double v = half_to_float(produced_upload[i]);
                            sum += v; sum_sq += v * v; ++count;
                        }
                        const float mean = count ? static_cast<float>(sum / count) : 0.5f;
                        const float var = count ? std::max(0.0f,
                            static_cast<float>(sum_sq / count - (sum / count) * (sum / count))) : 0.0f;
                        const uint32_t mode = performance_mode.load(std::memory_order_relaxed);
                        const float target = mode == 2 ? 0.20f : (mode == 1 ? 0.18f : 0.22f);
                        const float max_gain = mode == 2 ? 2.2f : (mode == 1 ? 2.5f : 3.5f);
                        const float gain = std::min(max_gain, std::max(1.0f,
                            target / std::max(1e-4f, std::sqrt(var))));
                        for (auto& h : produced_upload) {
                            const float v = std::clamp((half_to_float(h) - mean) * gain + mean, 0.0f, 1.0f);
                            h = float_to_half(v);
                        }
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
                worker_running = false;
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
        if (compact_input_srv) { compact_input_srv->Release(); compact_input_srv = nullptr; }
        if (compact_input_tex) { compact_input_tex->Release(); compact_input_tex = nullptr; }
        if (compact_cb) { compact_cb->Release(); compact_cb = nullptr; }
        if (compact_sampler) { compact_sampler->Release(); compact_sampler = nullptr; }
        if (compact_ps) { compact_ps->Release(); compact_ps = nullptr; }
        if (compact_vs) { compact_vs->Release(); compact_vs = nullptr; }
        if (compact_rtv) { compact_rtv->Release(); compact_rtv = nullptr; }
        if (compact_bgra) { compact_bgra->Release(); compact_bgra = nullptr; }
        for (auto& stage : stage_bgra) {
            if (stage) { stage->Release(); stage = nullptr; }
        }
        session.reset();
        run_options.reset();
        opts.reset();
        env.reset();
        input_pending = false;
        worker_running = false;
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
    if (!impl_->blend_active) return 1.0f;
    const auto elapsed = std::chrono::duration<float>(
        std::chrono::steady_clock::now() - impl_->blend_started).count();
    float t = elapsed / DepthInferImpl::kBlendDurationSec;
    if (t < 0.0f) t = 0.0f;
    else if (t > 1.0f) t = 1.0f;
    return t * t * (3.0f - 2.0f * t);
}

const char* DepthInferencer::last_error() const {
    return impl_ ? impl_->last_err.c_str() : "";
}

uint64_t DepthInferencer::inferences_completed() const {
    return impl_ ? impl_->inferences.load(std::memory_order_relaxed) : 0;
}

float DepthInferencer::depth_crop_x0_uv() const {
    return 0.0f;
}

float DepthInferencer::depth_crop_w_uv() const {
    return 1.0f;
}
