from pathlib import Path


def _source(relative: str) -> str:
    return Path(relative).read_text(encoding="utf-8")


def test_depth_sessions_use_custom_dml_queue_for_explicit_synchronization():
    source = _source("overlay/depth_infer.cpp")

    assert "struct DmlInterop" in source
    assert "D3D12CreateDevice" in source
    assert "DMLCreateDevice" in source
    assert "SessionOptionsAppendExecutionProvider_DML1" in source
    assert "dml_interop.queue.Get()" in source
    assert "execute_copy_commands_and_wait" in source


def test_mingw_uses_the_official_directml_device_iid_without_uuid_codegen():
    source = _source("overlay/depth_infer.cpp")

    assert "static const GUID kIID_IDMLDevice" in source
    assert "0x6dbd6437, 0x96fd, 0x423f" in source
    assert "{0xa9, 0x8c, 0xae, 0x5e, 0x7c, 0x2a, 0x57, 0x3f}" in source
    assert "kIID_IDMLDevice," in source
    # The explanatory comment may name the unsupported expression, but no
    # executable DMLCreateDevice call may rely on MinGW UUID code generation.
    assert "            __uuidof(IDMLDevice)," not in source


def test_depth_profiles_bind_persistent_dml_device_tensors():
    source = _source("overlay/depth_infer.cpp")

    assert 'Ort::MemoryInfo>(\n                "DML", OrtAllocatorType::OrtDeviceAllocator' in source
    assert "std::unique_ptr<Ort::Allocator> dml_allocator" in source
    assert "GetAllocation(fixed.input_bytes)" in source
    assert "GetAllocation(fixed.output_bytes)" in source
    assert "GetD3D12ResourceFromAllocation" in source
    assert "std::unique_ptr<Ort::IoBinding> binding" in source
    assert "BindInput(input_name.c_str()" in source
    assert "BindOutput(output_name.c_str()" in source


def test_gpu_input_and_output_use_persistent_upload_and_readback_buffers():
    source = _source("overlay/depth_infer.cpp")

    assert "D3D12_HEAP_TYPE_UPLOAD" in source
    assert "D3D12_HEAP_TYPE_READBACK" in source
    assert "fixed.upload_resource->Map" in source
    assert "CopyBufferRegion(\n            fixed.input_resource.Get()" in source
    assert "CopyBufferRegion(\n            fixed.readback_resource.Get()" in source
    assert "D3D12_RESOURCE_STATE_UNORDERED_ACCESS" in source


def test_gpu_bound_run_has_automatic_cpu_marshalling_fallback():
    source = _source("overlay/depth_infer.cpp")

    assert "run_gpu_bound(" in source
    assert "fixed.session->Run(*fixed.run_options, *fixed.binding)" in source
    assert "fixed.binding->SynchronizeInputs()" in source
    assert "fixed.binding->SynchronizeOutputs()" in source
    assert "Safe fallback: keep the proven CPU tensor marshalling path" in source
    assert "Ort::Value::CreateTensor<float>(\n                        memory" in source
    assert "gpu_io_fallbacks.fetch_add" in source


def test_gpu_binding_resources_are_destroyed_before_sessions_and_device_queue():
    source = _source("overlay/depth_infer.cpp")

    cleanup = source[source.index("void cleanup()") : source.index("};\n\n// Static member")]
    assert cleanup.index("reset_fixed_gpu_io(fixed)") < cleanup.index(
        "fixed.session.reset()"
    )
    assert cleanup.index("fixed.session.reset()") < cleanup.index(
        "reset_dml_interop()"
    )


def test_native_build_links_d3d12_and_exposes_runtime_path_telemetry():
    cmake = _source("overlay/CMakeLists.txt")
    header = _source("overlay/depth_infer.h")
    overlay = _source("overlay/overlay.cpp")

    assert "    d3d12\n" in cmake
    assert "bool gpu_io_active() const;" in header
    assert "uint64_t gpu_io_fallbacks() const;" in header
    assert "DepthIO path=%s fallbacks=%llu" in overlay
    assert '"persistent_dml_binding"' in overlay
    assert '"cpu_marshalling_fallback"' in overlay
