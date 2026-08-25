from __future__ import annotations

from pathlib import Path
import re


CPP = Path("overlay/depth_infer.cpp")
HEADER = Path("overlay/depth_infer.h")
CMAKE = Path("overlay/CMakeLists.txt")
OVERLAY = Path("overlay/overlay.cpp")
cpp = CPP.read_text(encoding="utf-8")
header = HEADER.read_text(encoding="utf-8")
cmake = CMAKE.read_text(encoding="utf-8")
overlay = OVERLAY.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return updated


cpp = replace_once(
    cpp,
    '#include <d3dcompiler.h>\n',
    '#include <d3dcompiler.h>\n#include <d3d12.h>\n#include <wrl/client.h>\n',
    'D3D12/WRL includes',
)
cpp = replace_once(
    cpp,
    '#include <mutex>\n#include <thread>\n',
    '#include <mutex>\n#include <stdexcept>\n#include <thread>\n',
    'stdexcept include',
)
cpp = replace_once(
    cpp,
    '// ── fp16 helpers ─────────────────────────────────────────────────────────────\n',
    'using Microsoft::WRL::ComPtr;\n\n// ── fp16 helpers ─────────────────────────────────────────────────────────────\n',
    'ComPtr alias',
)

old_ort_state = '''    // ORT state
    std::unique_ptr<Ort::Env>            env;
    struct FixedProfileSession {
        std::unique_ptr<Ort::SessionOptions> options;
        std::unique_ptr<Ort::Session> session;
        std::unique_ptr<Ort::RunOptions> run_options;
    };
    std::array<FixedProfileSession, 3> profile_sessions;
    std::wstring model_path_copy;
    int dml_device_id = 0;
'''
new_ort_state = '''    // ORT state
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
'''
cpp = replace_once(cpp, old_ort_state, new_ort_state, 'ORT/DML state')
cpp = replace_once(
    cpp,
    '''    std::atomic<uint64_t>                last_depth_upload_ms{0};
''',
    '''    std::atomic<uint64_t>                last_depth_upload_ms{0};
    std::atomic<bool>                    gpu_io_active{false};
    std::atomic<uint64_t>                gpu_io_fallbacks{0};
''',
    'GPU I/O telemetry fields',
)

interop_methods = r'''
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
            __uuidof(IDMLDevice),
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

'''
cpp = replace_once(
    cpp,
    '    int resolve_dml_device_id() const {\n',
    interop_methods + '    int resolve_dml_device_id() const {\n',
    'DML interop methods',
)

cpp = replace_once(
    cpp,
    '''            OrtStatus* status = OrtSessionOptionsAppendExecutionProvider_DML(
                *fixed.options, dml_device_id);
''',
    '''            OrtStatus* status = dml_interop.ready && dml_interop.api
                ? dml_interop.api->SessionOptionsAppendExecutionProvider_DML1(
                    *fixed.options,
                    dml_interop.dml_device.Get(),
                    dml_interop.queue.Get())
                : OrtSessionOptionsAppendExecutionProvider_DML(
                    *fixed.options, dml_device_id);
''',
    'custom DML queue session setup',
)
cpp = replace_once(
    cpp,
    '''                input_name = input.get();
                output_name = output.get();
            }
            return true;
''',
    '''                input_name = input.get();
                output_name = output.get();
            }
            if (dml_interop.ready) {
                initialize_gpu_io(fixed, profile);
            }
            return true;
''',
    'profile GPU binding initialization',
)
cpp = replace_once(
    cpp,
    '''            model_path_copy = model_path;
            dml_device_id = resolve_dml_device_id();
''',
    '''            model_path_copy = model_path;
            dml_device_id = resolve_dml_device_id();
            initialize_dml_interop();
''',
    'DML interop startup',
)

old_worker_run = '''                    std::array<int64_t, 4> shape = {
                        1, 3, running_profile.height, running_profile.width};
                    Ort::Value input = Ort::Value::CreateTensor<float>(
                        memory,
                        running_input_f32.data() + batch * tile_input_count,
                        tile_input_count, shape.data(), shape.size());
                    const char* input_names[] = {input_name.c_str()};
                    const char* output_names[] = {output_name.c_str()};
                    if (!ensure_fixed_session(running_profile.mode)) {
                        error = last_err;
                        ok = false;
                        break;
                    }
                    FixedProfileSession& fixed = fixed_session(running_profile.mode);
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
'''
new_worker_run = '''                    if (!ensure_fixed_session(running_profile.mode)) {
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
'''
cpp = replace_once(cpp, old_worker_run, new_worker_run, 'worker GPU-bound path')

cpp = replace_once(
    cpp,
    '''        for (auto& fixed : profile_sessions) {
            fixed.run_options.reset();
            fixed.session.reset();
            fixed.options.reset();
        }
        model_path_copy.clear();
        env.reset();
''',
    '''        for (auto& fixed : profile_sessions) {
            reset_fixed_gpu_io(fixed);
            fixed.run_options.reset();
            fixed.session.reset();
            fixed.options.reset();
        }
        reset_dml_interop();
        model_path_copy.clear();
        env.reset();
''',
    'GPU binding cleanup',
)
cpp = replace_once(
    cpp,
    '''    impl_->last_depth_upload_ms.store(0, std::memory_order_relaxed);
''',
    '''    impl_->last_depth_upload_ms.store(0, std::memory_order_relaxed);
    impl_->gpu_io_active.store(false, std::memory_order_relaxed);
    impl_->gpu_io_fallbacks.store(0, std::memory_order_relaxed);
''',
    'GPU I/O telemetry reset',
)

header = replace_once(
    header,
    '''    uint32_t depth_age_ms() const;
''',
    '''    uint32_t depth_age_ms() const;
    bool gpu_io_active() const;
    uint64_t gpu_io_fallbacks() const;
''',
    'GPU I/O public telemetry',
)
cpp = replace_once(
    cpp,
    '''uint32_t DepthInferencer::depth_age_ms() const {
    if (!impl_) return 0;
    const uint64_t uploaded = impl_->last_depth_upload_ms.load(std::memory_order_relaxed);
    if (uploaded == 0) return 0;
    const uint64_t now = DepthInferImpl::steady_milliseconds();
    return static_cast<uint32_t>(std::min<uint64_t>(UINT32_MAX, now - uploaded));
}
''',
    '''uint32_t DepthInferencer::depth_age_ms() const {
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
''',
    'GPU I/O public implementation',
)

cmake = replace_once(
    cmake,
    '''    d3d11
    dxgi
''',
    '''    d3d11
    d3d12
    dxgi
''',
    'D3D12 link dependency',
)

overlay = replace_once(
    overlay,
    '''        if (usingPoseV2) {
            Log("PoseV2 source=predicted confidence=%.3f velocity=(%.2f,%.2f,%.2f) orientation=(%.1f,%.1f,%.1f) capture_ts=%u publish_ts=%u flags=0x%X",
''',
    '''        if (g_depth) {
            Log("DepthIO path=%s fallbacks=%llu",
                g_depth->gpu_io_active() ? "persistent_dml_binding" : "cpu_marshalling_fallback",
                static_cast<unsigned long long>(g_depth->gpu_io_fallbacks()));
        }
        if (usingPoseV2) {
            Log("PoseV2 source=predicted confidence=%.3f velocity=(%.2f,%.2f,%.2f) orientation=(%.1f,%.1f,%.1f) capture_ts=%u publish_ts=%u flags=0x%X",
''',
    'Depth I/O periodic log',
)

CPP.write_text(cpp, encoding="utf-8", newline="\n")
HEADER.write_text(header, encoding="utf-8", newline="\n")
CMAKE.write_text(cmake, encoding="utf-8", newline="\n")
OVERLAY.write_text(overlay, encoding="utf-8", newline="\n")
