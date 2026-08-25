// overlay/depth_infer.cpp — see depth_infer.h for pipeline overview.

#include "depth_infer.h"
#include <d3dcompiler.h>
#include <d3d12.h>
#include <wrl/client.h>

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
#include <stdexcept>
#include <thread>
#include <vector>

using Microsoft::WRL::ComPtr;

// The DirectML NuGet header carries the COM interface declaration but MinGW
// does not synthesize __uuidof(IDMLDevice). Keep the official interface IID
// local so DMLCreateDevice links without relying on compiler-specific UUID data.
static const GUID kIID_IDMLDevice = {
    0x6dbd6437, 0x96fd, 0x423f,
    {0xa9, 0x8c, 0xae, 0x5e, 0x7c, 0x2a, 0x57, 0x3f}
};

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

    struct DepthProfile {
        int width = 518;
        int height = 294;
        int lb_off_x = 0;
        int lb_off_y = 0;
        int lb_w = 518;
        int lb_h = 294;
        uint32_t minimum_interval_ms = 70;
        uint32_t mode = 1;
    };

    static constexpr int kMaxModelWidth = 686;
    static constexpr int kMaxModelHeight = 392;

    // Two R16F depth textures for time-based interpolation.
    // When a new inference arrives, the old texture becomes "prev" and the new
    // one becomes "current". The shader lerps between them using depth_blend,
    // which advances 0→1 over a fixed wall-clock interval
    // — slightly wider than one full ~100 ms inference cycle so successive
    // inferences' blend windows overlap and there is never a discontinuity at
    // the transition. depth_blend() additionally applies smoothstep to `t`
    // so the depth update has zero first-derivative at both endpoints, hiding
    // the discrete update in the middle of head motion.
    float                     blend_duration_sec = 0.12f;
    std::chrono::steady_clock::time_point last_depth_arrival{};
    ID3D11Texture2D*          depth_tex      = nullptr;
    ID3D11ShaderResourceView* depth_srv      = nullptr;
    ID3D11Texture2D*          depth_prev_tex = nullptr;
    ID3D11ShaderResourceView* depth_prev_srv = nullptr;
    std::chrono::steady_clock::time_point blend_started{};
    bool                      blend_active = false;

    // ORT state
    std::unique_ptr<Ort::Env>            env;
    struct DmlInterop {
        ComPtr<ID3D12Device> device;
        ComPtr<IDMLDevice> dml_device;
        ComPtr<ID3D12CommandQueue> queue;
        ComPtr<ID3D12CommandAllocator> copy_allocator;
        ComPtr<ID3D12GraphicsCommandList> copy_list;
        ComPtr<ID3D12Fence> fence;
        HANDLE fence_event = nullptr;
        uint64_t fence_value = 0;
        const OrtDmlApi* api = nullptr;
        bool ready = false;
    } dml_interop;
    struct FixedProfileSession {
        std::unique_ptr<Ort::SessionOptions> options;
        std::unique_ptr<Ort::Session> session;
        std::unique_ptr<Ort::RunOptions> run_options;
        std::unique_ptr<Ort::MemoryInfo> dml_memory_info;
        std::unique_ptr<Ort::Allocator> dml_allocator;
        std::unique_ptr<Ort::MemoryAllocation> input_allocation;
        std::unique_ptr<Ort::MemoryAllocation> output_allocation;
        std::unique_ptr<Ort::Value> input_value;
        std::unique_ptr<Ort::Value> output_value;
        std::unique_ptr<Ort::IoBinding> binding;
        ComPtr<ID3D12Resource> input_resource;
        ComPtr<ID3D12Resource> output_resource;
        ComPtr<ID3D12Resource> upload_resource;
        ComPtr<ID3D12Resource> readback_resource;
        size_t input_elements = 0;
        size_t output_elements = 0;
        size_t input_bytes = 0;
        size_t output_bytes = 0;
        bool input_in_uav_state = false;
        bool gpu_io_ready = false;
    };
    std::array<FixedProfileSession, 3> profile_sessions;
    std::wstring model_path_copy;
    int dml_device_id = 0;
    std::string gpu_io_note;
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
    std::atomic<float>                   last_inference_ms{0.0f};
    std::atomic<int>                     active_model_width{518};
    std::atomic<int>                     active_model_height{294};
    std::atomic<int>                     active_scheduled_tiles{1};
    std::atomic<uint32_t>                active_performance_mode{1};
    std::atomic<float>                   runtime_frame_cpu_ms{0.0f};
    std::atomic<float>                   runtime_gpu_ms{0.0f};
    std::atomic<uint64_t>                last_depth_upload_ms{0};
    std::atomic<bool>                    gpu_io_active{false};
    std::atomic<uint64_t>                gpu_io_fallbacks{0};
    uint32_t                             auto_candidate_mode = 1;
    uint32_t                             auto_candidate_streak = 0;
    DepthProfile                         pending_profile{};
    DepthProfile                         running_profile{};
    std::vector<int>                     pending_tiles;
    std::vector<int>                     running_tiles;
    std::array<DepthProfile, kReadbackRingSize> stage_profiles{};
    std::array<std::vector<int>, kReadbackRingSize> stage_tiles;
    std::vector<std::vector<float>>      cached_tile_norm;
    std::vector<uint64_t>                tile_generation;
    uint64_t                             scheduler_cycle = 0;
    uint64_t                             completion_generation = 0;
    std::chrono::steady_clock::time_point last_submit{};
    float                                smoothed_global_lo = 0.0f;
    float                                smoothed_global_hi = 1.0f;
    bool                                 global_range_valid = false;
    float                                smoothed_contrast_mean = 0.5f;
    float                                smoothed_contrast_gain = 1.0f;
    bool                                 contrast_state_valid = false;
    std::vector<float>                   percentile_scratch;
    std::vector<float>                   global_samples_scratch;
    std::vector<float>                   normalized_scratch;
    std::vector<float>                   motion_warp_scratch;

    // ImageNet normalization (Depth Anything V2 uses standard ImageNet stats)
    static constexpr float kMean[3] = {0.485f, 0.456f, 0.406f};
    static constexpr float kStd[3]  = {0.229f, 0.224f, 0.225f};

    struct CompactCB {
        float cap_w, cap_h, tile_w, tile_count;
        float lb_off_x, lb_off_y, lb_w, lb_h;
        float model_w, model_h, overlap, padding;
    };

    DepthProfile profile_for_mode(uint32_t mode) const {
        DepthProfile profile;
        profile.mode = mode > 2 ? 1 : mode;
        if (profile.mode == 2) {
            profile.width = 392;
            profile.height = 224;
            profile.minimum_interval_ms = 40;
        } else if (profile.mode == 0) {
            profile.width = 686;
            profile.height = 392;
            profile.minimum_interval_ms = 85;
        } else {
            profile.width = 518;
            profile.height = 294;
            profile.minimum_interval_ms = 60;
        }
        const float scale = std::min(
            static_cast<float>(profile.width) / std::max(1, crop_w_eff),
            static_cast<float>(profile.height) / std::max(1, cap_h));
        profile.lb_w = std::max(1, static_cast<int>(std::round(crop_w_eff * scale)));
        profile.lb_h = std::max(1, static_cast<int>(std::round(cap_h * scale)));
        profile.lb_off_x = (profile.width - profile.lb_w) / 2;
        profile.lb_off_y = (profile.height - profile.lb_h) / 2;
        return profile;
    }

    int oldest_non_center_tile(int center) const {
        int selected = -1;
        uint64_t oldest = std::numeric_limits<uint64_t>::max();
        for (int tile = 0; tile < tile_count; ++tile) {
            if (tile == center) continue;
            const uint64_t generation = tile < static_cast<int>(tile_generation.size())
                ? tile_generation[tile] : 0;
            if (selected < 0 || generation < oldest) {
                selected = tile;
                oldest = generation;
            }
        }
        return selected;
    }

    std::vector<int> select_tiles(const DepthProfile& profile) {
        std::vector<int> selected;
        if (tile_count <= 1) return {0};
        const int center = tile_count / 2;
        if (profile.mode == 0) {
            selected.resize(tile_count);
            for (int tile = 0; tile < tile_count; ++tile) selected[tile] = tile;
        } else if (profile.mode == 1) {
            selected.push_back(center);
            const int oldest = oldest_non_center_tile(center);
            if (oldest >= 0) selected.push_back(oldest);
        } else {
            if ((scheduler_cycle % 3u) != 2u) {
                selected.push_back(center);
            } else {
                const int oldest = oldest_non_center_tile(center);
                selected.push_back(oldest >= 0 ? oldest : center);
            }
        }
        ++scheduler_cycle;
        return selected;
    }

    static uint64_t steady_milliseconds() {
        return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    }

    uint32_t resolve_performance_mode(uint32_t requested) {
        if (requested <= 2) {
            active_performance_mode.store(requested, std::memory_order_relaxed);
            auto_candidate_mode = requested;
            auto_candidate_streak = 0;
            return requested;
        }
        const uint32_t current = active_performance_mode.load(std::memory_order_relaxed);
        const float inference = last_inference_ms.load(std::memory_order_relaxed);
        const float frame_cpu = runtime_frame_cpu_ms.load(std::memory_order_relaxed);
        const float gpu = runtime_gpu_ms.load(std::memory_order_relaxed);
        const float render_cost = std::max(frame_cpu, gpu);
        uint32_t target = 1;
        if ((inference > 105.0f && inference > 0.0f) || render_cost > 13.0f
            || tile_count >= 4) {
            target = 2;
        } else if (inference > 0.0f && inference < 48.0f
                   && render_cost < 7.0f && tile_count <= 2) {
            target = 0;
        } else if (inference > 80.0f || render_cost > 10.0f) {
            target = 2;
        }
        if (target == current) {
            auto_candidate_mode = target;
            auto_candidate_streak = 0;
            return current;
        }
        if (target != auto_candidate_mode) {
            auto_candidate_mode = target;
            auto_candidate_streak = 1;
            return current;
        }
        const uint32_t required = target == 2 ? 2u : 5u;
        if (++auto_candidate_streak >= required) {
            active_performance_mode.store(target, std::memory_order_relaxed);
            auto_candidate_streak = 0;
            return target;
        }
        return current;
    }

    uint32_t adaptive_interval_ms(const DepthProfile& profile) const {
        const float measured = last_inference_ms.load(std::memory_order_relaxed);
        const float factor = profile.mode == 2 ? 0.55f : (profile.mode == 1 ? 0.70f : 0.82f);
        const uint32_t measured_floor = measured > 0.0f
            ? static_cast<uint32_t>(std::min(240.0f, measured * factor)) : 0u;
        return std::max(profile.minimum_interval_ms, measured_floor);
    }

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
 float modelW,modelH,overlap,padding;
};
struct I { float4 p:SV_Position; float2 uv:TEXCOORD0; };
float4 main(I i):SV_Target {
 float scaledX=i.uv.x*tileCount;
 float tile=min(floor(scaledX),tileCount-1);
 float2 local=float2(frac(scaledX),i.uv.y);
 float2 pixel=local*float2(modelW,modelH);
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


    void reset_fixed_gpu_io(FixedProfileSession& fixed) {
        fixed.binding.reset();
        fixed.input_value.reset();
        fixed.output_value.reset();
        fixed.input_resource.Reset();
        fixed.output_resource.Reset();
        fixed.upload_resource.Reset();
        fixed.readback_resource.Reset();
        fixed.input_allocation.reset();
        fixed.output_allocation.reset();
        fixed.dml_allocator.reset();
        fixed.dml_memory_info.reset();
        fixed.input_elements = 0;
        fixed.output_elements = 0;
        fixed.input_bytes = 0;
        fixed.output_bytes = 0;
        fixed.input_in_uav_state = false;
        fixed.gpu_io_ready = false;
    }

    void reset_dml_interop() {
        if (dml_interop.fence_event) {
            CloseHandle(dml_interop.fence_event);
            dml_interop.fence_event = nullptr;
        }
        dml_interop.copy_list.Reset();
        dml_interop.copy_allocator.Reset();
        dml_interop.fence.Reset();
        dml_interop.queue.Reset();
        dml_interop.dml_device.Reset();
        dml_interop.device.Reset();
        dml_interop.api = nullptr;
        dml_interop.fence_value = 0;
        dml_interop.ready = false;
    }

    bool initialize_dml_interop() {
        reset_dml_interop();
        ComPtr<IDXGIDevice> dxgi_device;
        HRESULT hr = dev->QueryInterface(
            __uuidof(IDXGIDevice),
            reinterpret_cast<void**>(dxgi_device.GetAddressOf()));
        if (FAILED(hr)) {
            gpu_io_note = "D3D11 device does not expose IDXGIDevice";
            return false;
        }
        ComPtr<IDXGIAdapter> adapter;
        hr = dxgi_device->GetAdapter(adapter.GetAddressOf());
        if (FAILED(hr)) {
            gpu_io_note = "could not resolve D3D11 adapter for D3D12 interop";
            return false;
        }
        hr = D3D12CreateDevice(
            adapter.Get(), D3D_FEATURE_LEVEL_11_0,
            __uuidof(ID3D12Device),
            reinterpret_cast<void**>(dml_interop.device.GetAddressOf()));
        if (FAILED(hr)) {
            gpu_io_note = "D3D12 device creation unavailable; using CPU-marshalled ORT I/O";
            reset_dml_interop();
            return false;
        }
        hr = DMLCreateDevice(
            dml_interop.device.Get(), DML_CREATE_DEVICE_FLAG_NONE,
            kIID_IDMLDevice,
            reinterpret_cast<void**>(dml_interop.dml_device.GetAddressOf()));
        if (FAILED(hr)) {
            gpu_io_note = "DirectML device creation unavailable; using CPU-marshalled ORT I/O";
            reset_dml_interop();
            return false;
        }
        D3D12_COMMAND_QUEUE_DESC queue_desc = {};
        queue_desc.Type = D3D12_COMMAND_LIST_TYPE_DIRECT;
        queue_desc.Priority = D3D12_COMMAND_QUEUE_PRIORITY_NORMAL;
        hr = dml_interop.device->CreateCommandQueue(
            &queue_desc, __uuidof(ID3D12CommandQueue),
            reinterpret_cast<void**>(dml_interop.queue.GetAddressOf()));
        if (SUCCEEDED(hr)) {
            hr = dml_interop.device->CreateCommandAllocator(
                D3D12_COMMAND_LIST_TYPE_DIRECT,
                __uuidof(ID3D12CommandAllocator),
                reinterpret_cast<void**>(dml_interop.copy_allocator.GetAddressOf()));
        }
        if (SUCCEEDED(hr)) {
            hr = dml_interop.device->CreateCommandList(
                0, D3D12_COMMAND_LIST_TYPE_DIRECT,
                dml_interop.copy_allocator.Get(), nullptr,
                __uuidof(ID3D12GraphicsCommandList),
                reinterpret_cast<void**>(dml_interop.copy_list.GetAddressOf()));
        }
        if (SUCCEEDED(hr)) hr = dml_interop.copy_list->Close();
        if (SUCCEEDED(hr)) {
            hr = dml_interop.device->CreateFence(
                0, D3D12_FENCE_FLAG_NONE,
                __uuidof(ID3D12Fence),
                reinterpret_cast<void**>(dml_interop.fence.GetAddressOf()));
        }
        if (FAILED(hr)) {
            gpu_io_note = "D3D12 copy queue setup failed; using CPU-marshalled ORT I/O";
            reset_dml_interop();
            return false;
        }
        dml_interop.fence_event = CreateEventW(nullptr, FALSE, FALSE, nullptr);
        if (!dml_interop.fence_event) {
            gpu_io_note = "D3D12 fence event creation failed; using CPU-marshalled ORT I/O";
            reset_dml_interop();
            return false;
        }
        const OrtApi& api = Ort::GetApi();
        const void* provider_api = nullptr;
        OrtStatus* status = api.GetExecutionProviderApi(
            "DML", ORT_API_VERSION, &provider_api);
        if (status != nullptr) {
            gpu_io_note = std::string("DML provider API unavailable: ")
                + api.GetErrorMessage(status);
            api.ReleaseStatus(status);
            reset_dml_interop();
            return false;
        }
        dml_interop.api = static_cast<const OrtDmlApi*>(provider_api);
        dml_interop.ready = dml_interop.api != nullptr;
        gpu_io_note = dml_interop.ready
            ? "persistent DirectML I/O binding available"
            : "DML provider API returned null; using CPU-marshalled ORT I/O";
        return dml_interop.ready;
    }

    static size_t aligned_tensor_bytes(size_t bytes) {
        bytes = std::max<size_t>(bytes, 16u);
        return (bytes + 3u) & ~size_t(3u);
    }

    bool create_d3d12_buffer(
        size_t byte_count,
        D3D12_HEAP_TYPE heap_type,
        D3D12_RESOURCE_STATES initial_state,
        D3D12_RESOURCE_FLAGS flags,
        ComPtr<ID3D12Resource>& resource) {
        if (!dml_interop.device) return false;
        D3D12_HEAP_PROPERTIES heap = {};
        heap.Type = heap_type;
        heap.CreationNodeMask = 1;
        heap.VisibleNodeMask = 1;
        D3D12_RESOURCE_DESC desc = {};
        desc.Dimension = D3D12_RESOURCE_DIMENSION_BUFFER;
        desc.Width = aligned_tensor_bytes(byte_count);
        desc.Height = 1;
        desc.DepthOrArraySize = 1;
        desc.MipLevels = 1;
        desc.SampleDesc.Count = 1;
        desc.Layout = D3D12_TEXTURE_LAYOUT_ROW_MAJOR;
        desc.Flags = flags;
        return SUCCEEDED(dml_interop.device->CreateCommittedResource(
            &heap, D3D12_HEAP_FLAG_NONE, &desc, initial_state, nullptr,
            __uuidof(ID3D12Resource),
            reinterpret_cast<void**>(resource.ReleaseAndGetAddressOf())));
    }

    static D3D12_RESOURCE_BARRIER transition_barrier(
        ID3D12Resource* resource,
        D3D12_RESOURCE_STATES before,
        D3D12_RESOURCE_STATES after) {
        D3D12_RESOURCE_BARRIER barrier = {};
        barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        barrier.Transition.pResource = resource;
        barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        barrier.Transition.StateBefore = before;
        barrier.Transition.StateAfter = after;
        return barrier;
    }

    bool begin_copy_commands() {
        if (!dml_interop.ready) return false;
        HRESULT hr = dml_interop.copy_allocator->Reset();
        if (SUCCEEDED(hr)) {
            hr = dml_interop.copy_list->Reset(
                dml_interop.copy_allocator.Get(), nullptr);
        }
        return SUCCEEDED(hr);
    }

    bool execute_copy_commands_and_wait() {
        HRESULT hr = dml_interop.copy_list->Close();
        if (FAILED(hr)) return false;
        ID3D12CommandList* lists[] = {dml_interop.copy_list.Get()};
        dml_interop.queue->ExecuteCommandLists(1, lists);
        const uint64_t value = ++dml_interop.fence_value;
        hr = dml_interop.queue->Signal(dml_interop.fence.Get(), value);
        if (FAILED(hr)) return false;
        if (dml_interop.fence->GetCompletedValue() < value) {
            hr = dml_interop.fence->SetEventOnCompletion(
                value, dml_interop.fence_event);
            if (FAILED(hr)) return false;
            if (WaitForSingleObject(dml_interop.fence_event, INFINITE)
                != WAIT_OBJECT_0) return false;
        }
        return true;
    }

    bool initialize_gpu_io(
        FixedProfileSession& fixed,
        const DepthProfile& profile) {
        reset_fixed_gpu_io(fixed);
        if (!dml_interop.ready || !fixed.session || input_name.empty()
            || output_name.empty()) return false;
        try {
            fixed.input_elements = 3ull
                * static_cast<size_t>(profile.height) * profile.width;
            fixed.output_elements = static_cast<size_t>(profile.height)
                * profile.width;
            fixed.input_bytes = fixed.input_elements * sizeof(float);
            fixed.output_bytes = fixed.output_elements * sizeof(float);
            fixed.dml_memory_info = std::make_unique<Ort::MemoryInfo>(
                "DML", OrtAllocatorType::OrtDeviceAllocator,
                0, OrtMemTypeDefault);
            fixed.dml_allocator = std::make_unique<Ort::Allocator>(
                *fixed.session, *fixed.dml_memory_info);
            fixed.input_allocation = std::make_unique<Ort::MemoryAllocation>(
                fixed.dml_allocator->GetAllocation(fixed.input_bytes));
            fixed.output_allocation = std::make_unique<Ort::MemoryAllocation>(
                fixed.dml_allocator->GetAllocation(fixed.output_bytes));
            ID3D12Resource* input_resource = nullptr;
            ID3D12Resource* output_resource = nullptr;
            Ort::ThrowOnError(dml_interop.api->GetD3D12ResourceFromAllocation(
                *fixed.dml_allocator, fixed.input_allocation->get(),
                &input_resource));
            Ort::ThrowOnError(dml_interop.api->GetD3D12ResourceFromAllocation(
                *fixed.dml_allocator, fixed.output_allocation->get(),
                &output_resource));
            fixed.input_resource.Attach(input_resource);
            fixed.output_resource.Attach(output_resource);
            // DirectML provider allocations are exposed for unordered-access
            // execution. Track that initial state so the very first upload
            // emits UAV -> COPY_DEST -> UAV barriers rather than depending on
            // implicit COMMON-state promotion.
            fixed.input_in_uav_state = true;
            const std::array<int64_t, 4> input_shape = {
                1, 3, profile.height, profile.width};
            const std::array<int64_t, 3> output_shape = {
                1, profile.height, profile.width};
            fixed.input_value = std::make_unique<Ort::Value>(
                Ort::Value::CreateTensor<float>(
                    *fixed.dml_memory_info,
                    static_cast<float*>(fixed.input_allocation->get()),
                    fixed.input_elements,
                    input_shape.data(), input_shape.size()));
            fixed.output_value = std::make_unique<Ort::Value>(
                Ort::Value::CreateTensor<float>(
                    *fixed.dml_memory_info,
                    static_cast<float*>(fixed.output_allocation->get()),
                    fixed.output_elements,
                    output_shape.data(), output_shape.size()));
            fixed.binding = std::make_unique<Ort::IoBinding>(*fixed.session);
            fixed.binding->BindInput(input_name.c_str(), *fixed.input_value);
            fixed.binding->BindOutput(output_name.c_str(), *fixed.output_value);
            if (!create_d3d12_buffer(
                    fixed.input_bytes, D3D12_HEAP_TYPE_UPLOAD,
                    D3D12_RESOURCE_STATE_GENERIC_READ,
                    D3D12_RESOURCE_FLAG_NONE, fixed.upload_resource)
                || !create_d3d12_buffer(
                    fixed.output_bytes, D3D12_HEAP_TYPE_READBACK,
                    D3D12_RESOURCE_STATE_COPY_DEST,
                    D3D12_RESOURCE_FLAG_NONE, fixed.readback_resource)) {
                throw std::runtime_error("could not create persistent DML transfer buffers");
            }
            fixed.gpu_io_ready = true;
            return true;
        } catch (const std::exception& exception) {
            gpu_io_note = std::string("persistent DML binding unavailable: ")
                + exception.what();
            reset_fixed_gpu_io(fixed);
            return false;
        }
    }

    bool upload_gpu_input(
        FixedProfileSession& fixed,
        const float* input,
        size_t input_elements) {
        if (!fixed.gpu_io_ready || input_elements != fixed.input_elements)
            return false;
        void* mapped = nullptr;
        const D3D12_RANGE no_read = {0, 0};
        HRESULT hr = fixed.upload_resource->Map(0, &no_read, &mapped);
        if (FAILED(hr) || !mapped) return false;
        std::memcpy(mapped, input, fixed.input_bytes);
        const D3D12_RANGE written = {0, fixed.input_bytes};
        fixed.upload_resource->Unmap(0, &written);
        if (!begin_copy_commands()) return false;
        if (fixed.input_in_uav_state) {
            D3D12_RESOURCE_BARRIER to_copy = transition_barrier(
                fixed.input_resource.Get(),
                D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
                D3D12_RESOURCE_STATE_COPY_DEST);
            dml_interop.copy_list->ResourceBarrier(1, &to_copy);
        }
        dml_interop.copy_list->CopyBufferRegion(
            fixed.input_resource.Get(), 0,
            fixed.upload_resource.Get(), 0,
            fixed.input_bytes);
        D3D12_RESOURCE_BARRIER to_uav = transition_barrier(
            fixed.input_resource.Get(),
            D3D12_RESOURCE_STATE_COPY_DEST,
            D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        dml_interop.copy_list->ResourceBarrier(1, &to_uav);
        if (!execute_copy_commands_and_wait()) return false;
        fixed.input_in_uav_state = true;
        return true;
    }

    bool download_gpu_output(
        FixedProfileSession& fixed,
        std::vector<float>& output) {
        if (!fixed.gpu_io_ready) return false;
        if (!begin_copy_commands()) return false;
        D3D12_RESOURCE_BARRIER to_copy = transition_barrier(
            fixed.output_resource.Get(),
            D3D12_RESOURCE_STATE_UNORDERED_ACCESS,
            D3D12_RESOURCE_STATE_COPY_SOURCE);
        dml_interop.copy_list->ResourceBarrier(1, &to_copy);
        dml_interop.copy_list->CopyBufferRegion(
            fixed.readback_resource.Get(), 0,
            fixed.output_resource.Get(), 0,
            fixed.output_bytes);
        D3D12_RESOURCE_BARRIER to_uav = transition_barrier(
            fixed.output_resource.Get(),
            D3D12_RESOURCE_STATE_COPY_SOURCE,
            D3D12_RESOURCE_STATE_UNORDERED_ACCESS);
        dml_interop.copy_list->ResourceBarrier(1, &to_uav);
        if (!execute_copy_commands_and_wait()) return false;
        const D3D12_RANGE read_range = {0, fixed.output_bytes};
        void* mapped = nullptr;
        HRESULT hr = fixed.readback_resource->Map(0, &read_range, &mapped);
        if (FAILED(hr) || !mapped) return false;
        output.resize(fixed.output_elements);
        std::memcpy(output.data(), mapped, fixed.output_bytes);
        const D3D12_RANGE no_write = {0, 0};
        fixed.readback_resource->Unmap(0, &no_write);
        return true;
    }

    bool run_gpu_bound(
        FixedProfileSession& fixed,
        const float* input,
        size_t input_elements,
        std::vector<float>& output) {
        if (!fixed.gpu_io_ready) return false;
        try {
            if (!upload_gpu_input(fixed, input, input_elements))
                throw std::runtime_error("DML input upload failed");
            fixed.binding->SynchronizeInputs();
            fixed.session->Run(*fixed.run_options, *fixed.binding);
            fixed.binding->SynchronizeOutputs();
            if (!download_gpu_output(fixed, output))
                throw std::runtime_error("DML output readback failed");
            gpu_io_active.store(true, std::memory_order_relaxed);
            return true;
        } catch (const std::exception& exception) {
            gpu_io_note = std::string("DML I/O binding failed; CPU fallback active: ")
                + exception.what();
            gpu_io_active.store(false, std::memory_order_relaxed);
            gpu_io_fallbacks.fetch_add(1, std::memory_order_relaxed);
            reset_fixed_gpu_io(fixed);
            return false;
        }
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
        prev_norm_tiles.assign(tile_count, {});
        cached_tile_norm.assign(
            tile_count,
            std::vector<float>(
                static_cast<size_t>(DepthInferencer::kModelSize)
                    * DepthInferencer::kModelSize,
                0.5f));
        tile_generation.assign(tile_count, 0);
        pending_tiles.clear();
        running_tiles.clear();
        for (auto& tiles : stage_tiles) tiles.clear();

        if (!create_compact_pipeline()) return false;

        // Allocate once for the largest validated rectangular profile. Smaller
        // modes render/map only their active viewport, avoiding device-resource
        // churn when the operator or adaptive controller changes quality.
        D3D11_TEXTURE2D_DESC sd = {};
        sd.Width = kMaxModelWidth * tile_count;
        sd.Height = kMaxModelHeight;
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
        hr = dev->CreateShaderResourceView(depth_tex, &srv, &depth_srv);
        if (FAILED(hr)) { last_err = "CreateShaderResourceView(depth current) failed"; return false; }
        hr = dev->CreateShaderResourceView(depth_prev_tex, &srv, &depth_prev_srv);
        if (FAILED(hr)) { last_err = "CreateShaderResourceView(depth prev) failed"; return false; }

        const int pixels = DepthInferencer::kModelSize
            * DepthInferencer::kModelSize * tile_count;
        std::vector<uint16_t> half_filled(pixels, float_to_half(0.5f));
        ctx->UpdateSubresource(
            depth_tex, 0, nullptr, half_filled.data(),
            DepthInferencer::kModelSize * tile_count * sizeof(uint16_t), 0);
        ctx->UpdateSubresource(
            depth_prev_tex, 0, nullptr, half_filled.data(),
            DepthInferencer::kModelSize * tile_count * sizeof(uint16_t), 0);
        blend_active = false;
        global_range_valid = false;
        contrast_state_valid = false;
        last_submit = {};
        last_depth_arrival = {};
        scheduler_cycle = 0;
        completion_generation = 0;
        return true;
    }

    FixedProfileSession& fixed_session(uint32_t mode) {
        return profile_sessions[mode > 2 ? 1 : mode];
    }

    bool ensure_fixed_session(uint32_t mode) {
        mode = mode > 2 ? 1 : mode;
        FixedProfileSession& fixed = fixed_session(mode);
        if (fixed.session) return true;
        try {
            const DepthProfile profile = profile_for_mode(mode);
            fixed.options = std::make_unique<Ort::SessionOptions>();
            fixed.options->SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
            fixed.options->DisableMemPattern();
            fixed.options->SetExecutionMode(ORT_SEQUENTIAL);
            OrtApi const& api = Ort::GetApi();
            // ONNX Runtime 1.20.1 exposes symbolic-dimension overrides through
            // the stable C API. The MinGW C++ wrapper in the NuGet package does
            // not project these methods, so use the supported C entry point and
            // retain normal C++ exception semantics via Ort::ThrowOnError.
            Ort::ThrowOnError(api.AddFreeDimensionOverrideByName(
                *fixed.options, "batch_size", 1));
            Ort::ThrowOnError(api.AddFreeDimensionOverrideByName(
                *fixed.options, "height", profile.height));
            Ort::ThrowOnError(api.AddFreeDimensionOverrideByName(
                *fixed.options, "width", profile.width));
            OrtStatus* status = dml_interop.ready && dml_interop.api
                ? dml_interop.api->SessionOptionsAppendExecutionProvider_DML1(
                    *fixed.options,
                    dml_interop.dml_device.Get(),
                    dml_interop.queue.Get())
                : OrtSessionOptionsAppendExecutionProvider_DML(
                    *fixed.options, dml_device_id);
            if (status != nullptr) {
                last_err = std::string("Append DML EP failed: ")
                    + api.GetErrorMessage(status);
                api.ReleaseStatus(status);
                fixed.options.reset();
                return false;
            }
            fixed.session = std::make_unique<Ort::Session>(
                *env, model_path_copy.c_str(), *fixed.options);
            fixed.run_options = std::make_unique<Ort::RunOptions>();
            if (input_name.empty() || output_name.empty()) {
                if (fixed.session->GetInputCount() != 1
                    || fixed.session->GetOutputCount() != 1) {
                    last_err = "Unexpected model input/output count";
                    return false;
                }
                Ort::AllocatedStringPtr input = fixed.session->GetInputNameAllocated(0, allocator);
                Ort::AllocatedStringPtr output = fixed.session->GetOutputNameAllocated(0, allocator);
                input_name = input.get();
                output_name = output.get();
            }
            if (dml_interop.ready) {
                initialize_gpu_io(fixed, profile);
            }
            return true;
        } catch (const Ort::Exception& exception) {
            last_err = std::string("Fixed-profile ORT session exception: ")
                + exception.what();
            fixed.run_options.reset();
            fixed.session.reset();
            fixed.options.reset();
            return false;
        }
    }

    bool create_ort_session(const std::wstring& model_path) {
        try {
            env = std::make_unique<Ort::Env>(
                ORT_LOGGING_LEVEL_WARNING, "Glassless3D");
            model_path_copy = model_path;
            dml_device_id = resolve_dml_device_id();
            initialize_dml_interop();
            // Balanced is the startup default. Fast and quality sessions are
            // created lazily only when selected, avoiding three copies of the
            // model weights on every machine.
            return ensure_fixed_session(1);
        } catch (const Ort::Exception& exception) {
            last_err = std::string("ORT init exception: ") + exception.what();
            return false;
        }
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
    void preprocess_compact(
        const uint8_t* src, int src_pitch,
        const DepthProfile& profile,
        const std::vector<int>& selected_tiles) {
        const size_t plane = static_cast<size_t>(profile.width) * profile.height;
        const size_t tile_stride = 3ull * plane;
        scratch_input_f32.resize(tile_stride * selected_tiles.size());
        for (size_t batch = 0; batch < selected_tiles.size(); ++batch) {
            const int tile = selected_tiles[batch];
            float* tile_base = scratch_input_f32.data() + batch * tile_stride;
            float* dstR = tile_base;
            float* dstG = tile_base + plane;
            float* dstB = tile_base + 2 * plane;
            for (int y = 0; y < profile.height; ++y) {
                const uint8_t* row = src + y * src_pitch;
                for (int x = 0; x < profile.width; ++x) {
                    const uint8_t* px = row + (tile * profile.width + x) * 4;
                    const size_t index = static_cast<size_t>(y) * profile.width + x;
                    dstR[index] = (px[2] / 255.0f - kMean[0]) / kStd[0];
                    dstG[index] = (px[1] / 255.0f - kMean[1]) / kStd[1];
                    dstB[index] = (px[0] / 255.0f - kMean[2]) / kStd[2];
                }
            }
        }
    }

    bool render_compact(ID3D11Texture2D* captured, const DepthProfile& profile) {
        if (captured != compact_input_tex) {
            if (compact_input_srv) { compact_input_srv->Release(); compact_input_srv = nullptr; }
            if (compact_input_tex) { compact_input_tex->Release(); compact_input_tex = nullptr; }
            HRESULT hr = dev->CreateShaderResourceView(captured, nullptr, &compact_input_srv);
            if (FAILED(hr)) { last_err = "Create compact input SRV failed"; return false; }
            compact_input_tex = captured;
            compact_input_tex->AddRef();
        }
        const CompactCB constants = {
            static_cast<float>(cap_w), static_cast<float>(cap_h),
            static_cast<float>(tile_w), static_cast<float>(tile_count),
            static_cast<float>(profile.lb_off_x), static_cast<float>(profile.lb_off_y),
            static_cast<float>(profile.lb_w), static_cast<float>(profile.lb_h),
            static_cast<float>(profile.width), static_cast<float>(profile.height),
            static_cast<float>(tile_overlap), 0.0f
        };
        ctx->UpdateSubresource(compact_cb, 0, nullptr, &constants, 0, 0);
        UINT viewport_count = 1;
        D3D11_VIEWPORT old_viewport = {};
        ctx->RSGetViewports(&viewport_count, &old_viewport);
        const D3D11_VIEWPORT viewport = {
            0, 0, static_cast<float>(profile.width * tile_count),
            static_cast<float>(profile.height), 0, 1};
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
    float warp_previous_depth(
        const std::vector<float>& current,
        const std::vector<float>& previous,
        std::vector<float>& warped,
        uint32_t mode) {
        const int N = DepthInferencer::kModelSize;
        if (current.size() != previous.size() || current.size() != static_cast<size_t>(N * N)) {
            warped = previous;
            return 1.0f;
        }
        warped.resize(current.size());
        const int block = mode == 2 ? 86 : 64;
        const int radius = mode == 2 ? 4 : (mode == 1 ? 6 : 8);
        const int search_step = 2;
        double total_error = 0.0;
        size_t total_samples = 0;
        for (int by = 0; by < N; by += block) {
            for (int bx = 0; bx < N; bx += block) {
                int best_dx = 0, best_dy = 0;
                float best_error = std::numeric_limits<float>::max();
                for (int dy = -radius; dy <= radius; dy += search_step) {
                    for (int dx = -radius; dx <= radius; dx += search_step) {
                        double error = 0.0;
                        int samples = 0;
                        const int y_end = std::min(N, by + block);
                        const int x_end = std::min(N, bx + block);
                        for (int y = by; y < y_end; y += 8) {
                            const int py = std::max(0, std::min(N - 1, y + dy));
                            for (int x = bx; x < x_end; x += 8) {
                                const int px = std::max(0, std::min(N - 1, x + dx));
                                error += std::fabs(
                                    current[static_cast<size_t>(y) * N + x]
                                    - previous[static_cast<size_t>(py) * N + px]);
                                ++samples;
                            }
                        }
                        const float mean = samples ? static_cast<float>(error / samples) : 1.0f;
                        if (mean < best_error) {
                            best_error = mean;
                            best_dx = dx;
                            best_dy = dy;
                        }
                    }
                }
                const int y_end = std::min(N, by + block);
                const int x_end = std::min(N, bx + block);
                for (int y = by; y < y_end; ++y) {
                    const int py = std::max(0, std::min(N - 1, y + best_dy));
                    for (int x = bx; x < x_end; ++x) {
                        const int px = std::max(0, std::min(N - 1, x + best_dx));
                        warped[static_cast<size_t>(y) * N + x]
                            = previous[static_cast<size_t>(py) * N + px];
                    }
                }
                total_error += best_error;
                ++total_samples;
            }
        }
        return total_samples ? static_cast<float>(total_error / total_samples) : 1.0f;
    }

    bool postprocess(
        std::vector<float>& dst,
        const DepthProfile& profile,
        float shared_vlo,
        float shared_vhi,
        int tile_index) {
        const int pixels = out_h * out_w;
        if (pixels <= 0) return true;
        const int cy0 = profile.lb_off_y;
        const int cy1 = profile.lb_off_y + profile.lb_h;
        const int cx0 = profile.lb_off_x;
        const int cx1 = profile.lb_off_x + profile.lb_w;
        float vlo = shared_vlo;
        float vhi = shared_vhi;
        if (!std::isfinite(vlo) || !std::isfinite(vhi) || vhi <= vlo) {
            percentile_scratch.clear();
            percentile_scratch.reserve(
                static_cast<size_t>((cy1 - cy0) / 2 + 1)
                * static_cast<size_t>((cx1 - cx0) / 2 + 1));
            for (int y = cy0; y < cy1 && y < out_h; y += 2) {
                const float* row = output_f32.data() + static_cast<size_t>(y) * out_w;
                for (int x = cx0; x < cx1 && x < out_w; x += 2)
                    percentile_scratch.push_back(row[x]);
            }
            if (percentile_scratch.empty()) {
                vlo = 0.0f; vhi = 1.0f;
            } else {
                const size_t count = percentile_scratch.size();
                const size_t lo = std::min(count - 1, count * 2 / 100);
                const size_t hi = std::min(count - 1, count * 98 / 100);
                std::nth_element(
                    percentile_scratch.begin(), percentile_scratch.begin() + lo,
                    percentile_scratch.end());
                vlo = percentile_scratch[lo];
                std::nth_element(
                    percentile_scratch.begin() + lo + 1,
                    percentile_scratch.begin() + hi,
                    percentile_scratch.end());
                vhi = percentile_scratch[hi];
            }
        }
        const float range = std::max(1e-6f, vhi - vlo);
        const int N = DepthInferencer::kModelSize;
        normalized_scratch.resize(static_cast<size_t>(N) * N);
        const float logical_start = static_cast<float>(tile_index * tile_w);
        const float logical_end = std::min(static_cast<float>(cap_w), logical_start + tile_w);
        const float source_start = std::max(0.0f, logical_start - tile_overlap);
        const float source_end = std::min(static_cast<float>(cap_w), logical_end + tile_overlap);
        const float source_width = std::max(1.0f, source_end - source_start);
        const float tile_crop_x = (logical_start - source_start) / source_width;
        const float tile_crop_w = (logical_end - logical_start) / source_width;
        for (int oy = 0; oy < N; ++oy) {
            int sy = cy0 + (oy * profile.lb_h) / N;
            sy = std::max(cy0, std::min(std::min(cy1 - 1, out_h - 1), sy));
            const float* input_row = output_f32.data() + static_cast<size_t>(sy) * out_w;
            float* output_row = normalized_scratch.data() + static_cast<size_t>(oy) * N;
            for (int ox = 0; ox < N; ++ox) {
                const float logical_u = tile_crop_x
                    + (static_cast<float>(ox) / N) * tile_crop_w;
                int sx = cx0 + static_cast<int>(logical_u * profile.lb_w);
                sx = std::max(cx0, std::min(std::min(cx1 - 1, out_w - 1), sx));
                output_row[ox] = std::max(0.0f, std::min(1.0f,
                    (vhi - input_row[sx]) / range));
            }
        }

        const uint32_t mode = profile.mode;
        const float alpha = mode == 2 ? 0.16f : (mode == 1 ? 0.10f : 0.065f);
        const float edge_delta = mode == 2 ? 0.075f : (mode == 1 ? 0.060f : 0.050f);
        bool scene_cut = true;
        if (prev_norm_f32.size() == normalized_scratch.size()) {
            const float motion_error = warp_previous_depth(
                normalized_scratch, prev_norm_f32, motion_warp_scratch, mode);
            scene_cut = motion_error > (mode == 2 ? 0.19f : 0.16f);
            if (!scene_cut) {
                constexpr float kDepthMin = 0.01f;
                for (size_t index = 0; index < normalized_scratch.size(); ++index) {
                    const float current = normalized_scratch[index];
                    const float previous = motion_warp_scratch[index];
                    if (std::fabs(current - previous) > edge_delta) continue;
                    const float current_disparity = 1.0f / std::max(kDepthMin, current);
                    const float previous_disparity = 1.0f / std::max(kDepthMin, previous);
                    const float blended = 1.0f / (
                        alpha * current_disparity
                        + (1.0f - alpha) * previous_disparity);
                    normalized_scratch[index]
                        = std::max(0.0f, std::min(1.0f, blended));
                }
            }
        }
        prev_norm_f32 = normalized_scratch;
        dst = normalized_scratch;
        return scene_cut;
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
        std::vector<uint16_t> drained_upload;
        bool worker_busy = false;
        {
            std::lock_guard<std::mutex> lock(m);
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
        const auto now = std::chrono::steady_clock::now();
        if (!drained_upload.empty()) {
            const int N = DepthInferencer::kModelSize;
            ctx->CopyResource(depth_prev_tex, depth_tex);
            ctx->UpdateSubresource(
                depth_tex, 0, nullptr, drained_upload.data(),
                N * tile_count * sizeof(uint16_t), 0);
            if (last_depth_arrival.time_since_epoch().count() != 0) {
                const float interval = std::chrono::duration<float>(
                    now - last_depth_arrival).count();
                blend_duration_sec = std::max(
                    0.04f, std::min(0.22f, interval * 0.90f));
            }
            last_depth_arrival = now;
            blend_started = now;
            blend_active = true;
            has_valid_depth = true;
            last_depth_upload_ms.store(steady_milliseconds(), std::memory_order_relaxed);
        }
        if (worker_busy) return true;

        const uint32_t requested_mode = performance_mode.load(std::memory_order_relaxed);
        const uint32_t resolved_mode = resolve_performance_mode(requested_mode);
        const DepthProfile requested = profile_for_mode(resolved_mode);
        if (last_submit.time_since_epoch().count() != 0) {
            const uint32_t elapsed_ms = static_cast<uint32_t>(
                std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - last_submit).count());
            if (elapsed_ms < adaptive_interval_ms(requested)) return true;
        }

        if (stage_count == 0) {
            const std::vector<int> selected = select_tiles(requested);
            if (!render_compact(captured, requested)) return false;
            ctx->CopyResource(stage_bgra[stage_write], compact_bgra);
            stage_profiles[stage_write] = requested;
            stage_tiles[stage_write] = selected;
            stage_pending[stage_write] = true;
            stage_write = (stage_write + 1) % kReadbackRingSize;
            ++stage_count;
        }
        if (stage_count == 0 || !stage_pending[stage_read]) return true;
        D3D11_MAPPED_SUBRESOURCE mapped = {};
        const HRESULT map_hr = ctx->Map(
            stage_bgra[stage_read], 0, D3D11_MAP_READ,
            D3D11_MAP_FLAG_DO_NOT_WAIT, &mapped);
        if (map_hr == DXGI_ERROR_WAS_STILL_DRAWING) return true;
        if (FAILED(map_hr)) { last_err = "Map(staging ring) failed"; return false; }
        const DepthProfile profile = stage_profiles[stage_read];
        const std::vector<int> selected = stage_tiles[stage_read];
        preprocess_compact(
            static_cast<const uint8_t*>(mapped.pData),
            static_cast<int>(mapped.RowPitch), profile, selected);
        ctx->Unmap(stage_bgra[stage_read], 0);
        stage_pending[stage_read] = false;
        stage_tiles[stage_read].clear();
        stage_read = (stage_read + 1) % kReadbackRingSize;
        --stage_count;

        {
            std::lock_guard<std::mutex> lock(m);
            if (stop.load()) {
                last_err = "DepthInferencer is stopping";
                return false;
            }
            pending_input_f32.swap(scratch_input_f32);
            pending_profile = profile;
            pending_tiles = selected;
            input_pending = true;
        }
        active_model_width.store(profile.width, std::memory_order_relaxed);
        active_model_height.store(profile.height, std::memory_order_relaxed);
        active_scheduled_tiles.store(
            static_cast<int>(selected.size()), std::memory_order_relaxed);
        last_submit = now;
        cv_work.notify_one();
        return true;
    }

    // WORKER THREAD: blocks on cv_work for new input, runs ORT, publishes
    // postprocessed depth back to main thread. Runs until stop is set.
    void worker_loop() {
        Ort::MemoryInfo memory = Ort::MemoryInfo::CreateCpu(
            OrtAllocatorType::OrtArenaAllocator, OrtMemTypeDefault);
        while (true) {
            {
                std::unique_lock<std::mutex> lock(m);
                cv_work.wait(lock, [&]{ return input_pending || stop.load(); });
                if (stop.load()) return;
                running_input_f32.swap(pending_input_f32);
                running_profile = pending_profile;
                running_tiles = pending_tiles;
                pending_tiles.clear();
                input_pending = false;
                worker_running = true;
            }

            const auto inference_started = std::chrono::steady_clock::now();
            std::vector<uint16_t> produced_upload;
            bool ok = true;
            std::string error;
            try {
                const int N = DepthInferencer::kModelSize;
                const size_t plane = static_cast<size_t>(running_profile.width)
                    * running_profile.height;
                const size_t tile_input_count = 3ull * plane;
                std::vector<std::vector<float>> raw_tiles(running_tiles.size());
                int output_height = 0, output_width = 0;
                for (size_t batch = 0; batch < running_tiles.size() && ok; ++batch) {
                    if (!ensure_fixed_session(running_profile.mode)) {
                        error = last_err;
                        ok = false;
                        break;
                    }
                    FixedProfileSession& fixed = fixed_session(running_profile.mode);
                    const float* tile_input = running_input_f32.data()
                        + batch * tile_input_count;
                    if (run_gpu_bound(
                            fixed, tile_input, tile_input_count,
                            raw_tiles[batch])) {
                        output_height = running_profile.height;
                        output_width = running_profile.width;
                        continue;
                    }

                    // Safe fallback: keep the proven CPU tensor marshalling path
                    // for adapters/drivers where external DML allocations or
                    // D3D12 synchronization are unavailable.
                    gpu_io_active.store(false, std::memory_order_relaxed);
                    std::array<int64_t, 4> shape = {
                        1, 3, running_profile.height, running_profile.width};
                    Ort::Value input = Ort::Value::CreateTensor<float>(
                        memory, const_cast<float*>(tile_input),
                        tile_input_count, shape.data(), shape.size());
                    const char* input_names[] = {input_name.c_str()};
                    const char* output_names[] = {output_name.c_str()};
                    auto outputs = fixed.session->Run(
                        *fixed.run_options, input_names, &input, 1, output_names, 1);
                    if (outputs.size() != 1) {
                        error = "Run returned no outputs"; ok = false; break;
                    }
                    auto info = outputs[0].GetTensorTypeAndShapeInfo();
                    const auto output_shape = info.GetShape();
                    if (output_shape.size() == 3 && output_shape[0] == 1) {
                        output_height = static_cast<int>(output_shape[1]);
                        output_width = static_cast<int>(output_shape[2]);
                    } else if (output_shape.size() == 4
                               && output_shape[0] == 1 && output_shape[1] == 1) {
                        output_height = static_cast<int>(output_shape[2]);
                        output_width = static_cast<int>(output_shape[3]);
                    } else {
                        error = "Unexpected output shape"; ok = false; break;
                    }
                    const size_t output_count
                        = static_cast<size_t>(output_height) * output_width;
                    raw_tiles[batch].resize(output_count);
                    std::memcpy(
                        raw_tiles[batch].data(),
                        outputs[0].GetTensorData<float>(),
                        output_count * sizeof(float));
                }

                if (ok) {
                    out_h = output_height;
                    out_w = output_width;
                    global_samples_scratch.clear();
                    for (const auto& raw : raw_tiles) {
                        for (int y = running_profile.lb_off_y;
                             y < running_profile.lb_off_y + running_profile.lb_h
                                 && y < out_h; y += 2) {
                            for (int x = running_profile.lb_off_x;
                                 x < running_profile.lb_off_x + running_profile.lb_w
                                     && x < out_w; x += 2) {
                                global_samples_scratch.push_back(
                                    raw[static_cast<size_t>(y) * out_w + x]);
                            }
                        }
                    }
                    float raw_lo = 0.0f, raw_hi = 1.0f;
                    if (!global_samples_scratch.empty()) {
                        const size_t count = global_samples_scratch.size();
                        const size_t lo = std::min(count - 1, count * 2 / 100);
                        const size_t hi = std::min(count - 1, count * 98 / 100);
                        std::nth_element(
                            global_samples_scratch.begin(),
                            global_samples_scratch.begin() + lo,
                            global_samples_scratch.end());
                        raw_lo = global_samples_scratch[lo];
                        std::nth_element(
                            global_samples_scratch.begin() + lo + 1,
                            global_samples_scratch.begin() + hi,
                            global_samples_scratch.end());
                        raw_hi = global_samples_scratch[hi];
                    }
                    const float span = std::max(1e-5f, raw_hi - raw_lo);
                    const bool range_cut = !global_range_valid
                        || std::fabs(raw_lo - smoothed_global_lo) > span * 0.55f
                        || std::fabs(raw_hi - smoothed_global_hi) > span * 0.55f;
                    const float range_alpha = range_cut ? 1.0f
                        : (running_profile.mode == 2 ? 0.24f
                           : (running_profile.mode == 1 ? 0.15f : 0.09f));
                    smoothed_global_lo += range_alpha * (raw_lo - smoothed_global_lo);
                    smoothed_global_hi += range_alpha * (raw_hi - smoothed_global_hi);
                    global_range_valid = true;

                    bool any_scene_cut = range_cut;
                    for (size_t batch = 0; batch < running_tiles.size(); ++batch) {
                        const int tile = running_tiles[batch];
                        output_f32.swap(raw_tiles[batch]);
                        prev_norm_f32.swap(prev_norm_tiles[tile]);
                        const bool tile_cut = postprocess(
                            cached_tile_norm[tile], running_profile,
                            smoothed_global_lo, smoothed_global_hi, tile);
                        prev_norm_f32.swap(prev_norm_tiles[tile]);
                        any_scene_cut = any_scene_cut || tile_cut;
                        tile_generation[tile] = ++completion_generation;
                    }

                    double sum = 0.0, sum_sq = 0.0;
                    size_t sample_count = 0;
                    for (int tile = 0; tile < tile_count; ++tile) {
                        if (tile_generation[tile] == 0) continue;
                        const auto& values = cached_tile_norm[tile];
                        for (size_t index = 0; index < values.size(); index += 4) {
                            const double value = values[index];
                            sum += value;
                            sum_sq += value * value;
                            ++sample_count;
                        }
                    }
                    const float mean = sample_count
                        ? static_cast<float>(sum / sample_count) : 0.5f;
                    const float variance = sample_count
                        ? std::max(0.0f, static_cast<float>(
                            sum_sq / sample_count - (sum / sample_count) * (sum / sample_count)))
                        : 0.0f;
                    const float target_std = running_profile.mode == 2 ? 0.19f
                        : (running_profile.mode == 1 ? 0.18f : 0.21f);
                    const float max_gain = running_profile.mode == 2 ? 2.0f
                        : (running_profile.mode == 1 ? 2.5f : 3.25f);
                    const float desired_gain = std::min(
                        max_gain, std::max(1.0f,
                            target_std / std::max(1e-4f, std::sqrt(variance))));
                    const float contrast_alpha = any_scene_cut || !contrast_state_valid
                        ? 1.0f
                        : (running_profile.mode == 2 ? 0.22f
                           : (running_profile.mode == 1 ? 0.14f : 0.09f));
                    smoothed_contrast_mean += contrast_alpha
                        * (mean - smoothed_contrast_mean);
                    smoothed_contrast_gain += contrast_alpha
                        * (desired_gain - smoothed_contrast_gain);
                    contrast_state_valid = true;

                    produced_upload.resize(
                        static_cast<size_t>(N) * N * tile_count);
                    const uint16_t neutral = float_to_half(0.5f);
                    for (int y = 0; y < N; ++y) {
                        for (int tile = 0; tile < tile_count; ++tile) {
                            for (int x = 0; x < N; ++x) {
                                const size_t destination
                                    = static_cast<size_t>(y) * N * tile_count
                                      + static_cast<size_t>(tile) * N + x;
                                if (tile_generation[tile] == 0) {
                                    produced_upload[destination] = neutral;
                                    continue;
                                }
                                const float raw = cached_tile_norm[tile][
                                    static_cast<size_t>(y) * N + x];
                                const float adjusted = std::max(0.0f, std::min(1.0f,
                                    (raw - smoothed_contrast_mean)
                                        * smoothed_contrast_gain
                                        + smoothed_contrast_mean));
                                produced_upload[destination] = float_to_half(adjusted);
                            }
                        }
                    }
                }
            } catch (const Ort::Exception& exception) {
                error = std::string("ORT Run exception: ") + exception.what();
                ok = false;
            } catch (const std::exception& exception) {
                error = std::string("Worker exception: ") + exception.what();
                ok = false;
            }

            const float elapsed_ms = std::chrono::duration<float, std::milli>(
                std::chrono::steady_clock::now() - inference_started).count();
            last_inference_ms.store(elapsed_ms, std::memory_order_relaxed);
            {
                std::lock_guard<std::mutex> lock(m);
                worker_running = false;
                if (ok) {
                    ready_upload_fp16 = std::move(produced_upload);
                    output_ready = true;
                } else {
                    last_err = std::move(error);
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
            for (auto& fixed : profile_sessions) {
                if (!fixed.run_options) continue;
                try {
                    fixed.run_options->SetTerminate();
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
        for (auto& fixed : profile_sessions) {
            reset_fixed_gpu_io(fixed);
            fixed.run_options.reset();
            fixed.session.reset();
            fixed.options.reset();
        }
        reset_dml_interop();
        model_path_copy.clear();
        env.reset();
        input_pending = false;
        worker_running = false;
        output_ready = false;
        pending_input_f32.clear();
        running_input_f32.clear();
        ready_upload_fp16.clear();
        pending_tiles.clear();
        running_tiles.clear();
        cached_tile_norm.clear();
        tile_generation.clear();
        percentile_scratch.clear();
        global_samples_scratch.clear();
        normalized_scratch.clear();
        motion_warp_scratch.clear();
        for (auto& tiles : stage_tiles) tiles.clear();
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
    impl_->active_performance_mode.store(1, std::memory_order_relaxed);
    impl_->last_inference_ms.store(0.0f, std::memory_order_relaxed);
    impl_->last_depth_upload_ms.store(0, std::memory_order_relaxed);
    impl_->gpu_io_active.store(false, std::memory_order_relaxed);
    impl_->gpu_io_fallbacks.store(0, std::memory_order_relaxed);
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
    if (!impl_ || !impl_->env || !impl_->fixed_session(1).session) {
        impl_->last_err = "DepthInferencer not initialized";
        return false;
    }
    return impl_->run_once(captured_bgra8);
}

void DepthInferencer::set_performance_mode(uint32_t mode) {
    if (!impl_) return;
    if (mode > 3) mode = 3;
    impl_->performance_mode.store(mode, std::memory_order_relaxed);
    if (mode <= 2) {
        impl_->active_performance_mode.store(mode, std::memory_order_relaxed);
    }
}

uint32_t DepthInferencer::performance_mode() const {
    if (!impl_) return 1;
    return impl_->performance_mode.load(std::memory_order_relaxed);
}

uint32_t DepthInferencer::active_performance_mode() const {
    if (!impl_) return 1;
    return impl_->active_performance_mode.load(std::memory_order_relaxed);
}

void DepthInferencer::set_runtime_load(float frame_cpu_ms, float gpu_ms) {
    if (!impl_) return;
    if (std::isfinite(frame_cpu_ms) && frame_cpu_ms >= 0.0f)
        impl_->runtime_frame_cpu_ms.store(frame_cpu_ms, std::memory_order_relaxed);
    if (std::isfinite(gpu_ms) && gpu_ms >= 0.0f)
        impl_->runtime_gpu_ms.store(gpu_ms, std::memory_order_relaxed);
}

int DepthInferencer::active_model_width() const {
    return impl_ ? impl_->active_model_width.load(std::memory_order_relaxed) : 0;
}

int DepthInferencer::active_model_height() const {
    return impl_ ? impl_->active_model_height.load(std::memory_order_relaxed) : 0;
}

int DepthInferencer::active_scheduled_tiles() const {
    return impl_ ? impl_->active_scheduled_tiles.load(std::memory_order_relaxed) : 0;
}

float DepthInferencer::last_inference_ms() const {
    return impl_ ? impl_->last_inference_ms.load(std::memory_order_relaxed) : 0.0f;
}

float DepthInferencer::blend_duration_ms() const {
    return impl_ ? impl_->blend_duration_sec * 1000.0f : 0.0f;
}

uint32_t DepthInferencer::depth_age_ms() const {
    if (!impl_) return 0;
    const uint64_t uploaded = impl_->last_depth_upload_ms.load(std::memory_order_relaxed);
    if (uploaded == 0) return 0;
    const uint64_t now = DepthInferImpl::steady_milliseconds();
    return static_cast<uint32_t>(std::min<uint64_t>(UINT32_MAX, now - uploaded));
}

bool DepthInferencer::gpu_io_active() const {
    return impl_ && impl_->gpu_io_active.load(std::memory_order_relaxed);
}

uint64_t DepthInferencer::gpu_io_fallbacks() const {
    return impl_ ? impl_->gpu_io_fallbacks.load(std::memory_order_relaxed) : 0;
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
    float t = elapsed / std::max(0.001f, impl_->blend_duration_sec);
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
