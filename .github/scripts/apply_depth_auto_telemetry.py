from pathlib import Path
import re


CPP = Path("overlay/depth_infer.cpp")
HEADER = Path("overlay/depth_infer.h")
OVERLAY = Path("overlay/overlay.cpp")
cpp = CPP.read_text(encoding="utf-8")
header = HEADER.read_text(encoding="utf-8")
overlay = OVERLAY.read_text(encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected one match for {old[:100]!r}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, got {count}")
    return updated


header = replace_once(
    header,
    '''    // Runtime performance/quality mode from G3D_Settings:\n    // 0=quality, 1=balanced, 2=fast.\n    void set_performance_mode(uint32_t mode);\n    uint32_t performance_mode() const;\n''',
    '''    // Runtime performance/quality mode from G3D_Settings:\n    // 0=quality, 1=balanced, 2=fast, 3=auto.\n    void set_performance_mode(uint32_t mode);\n    uint32_t performance_mode() const;\n    uint32_t active_performance_mode() const;\n\n    // Feed recent render cost to the automatic controller. Negative values are\n    // ignored, allowing unsupported GPU timing queries to degrade safely.\n    void set_runtime_load(float frame_cpu_ms, float gpu_ms);\n\n    int active_model_width() const;\n    int active_model_height() const;\n    int active_scheduled_tiles() const;\n    float last_inference_ms() const;\n    float blend_duration_ms() const;\n    uint32_t depth_age_ms() const;\n''',
    "depth_infer.h performance API",
)

cpp = replace_once(
    cpp,
    '''    std::atomic<int>                     active_scheduled_tiles{1};\n''',
    '''    std::atomic<int>                     active_scheduled_tiles{1};\n    std::atomic<uint32_t>                active_performance_mode{1};\n    std::atomic<float>                   runtime_frame_cpu_ms{0.0f};\n    std::atomic<float>                   runtime_gpu_ms{0.0f};\n    std::atomic<uint64_t>                last_depth_upload_ms{0};\n    uint32_t                             auto_candidate_mode = 1;\n    uint32_t                             auto_candidate_streak = 0;\n''',
    "depth telemetry fields",
)

cpp = replace_once(
    cpp,
    '''    uint32_t adaptive_interval_ms(const DepthProfile& profile) const {\n        const float measured = last_inference_ms.load(std::memory_order_relaxed);\n        const float factor = profile.mode == 2 ? 0.55f : (profile.mode == 1 ? 0.70f : 0.82f);\n        const uint32_t measured_floor = measured > 0.0f\n            ? static_cast<uint32_t>(std::min(240.0f, measured * factor)) : 0u;\n        return std::max(profile.minimum_interval_ms, measured_floor);\n    }\n''',
    '''    static uint64_t steady_milliseconds() {\n        return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::milliseconds>(\n            std::chrono::steady_clock::now().time_since_epoch()).count());\n    }\n\n    uint32_t resolve_performance_mode(uint32_t requested) {\n        if (requested <= 2) {\n            active_performance_mode.store(requested, std::memory_order_relaxed);\n            auto_candidate_mode = requested;\n            auto_candidate_streak = 0;\n            return requested;\n        }\n        const uint32_t current = active_performance_mode.load(std::memory_order_relaxed);\n        const float inference = last_inference_ms.load(std::memory_order_relaxed);\n        const float frame_cpu = runtime_frame_cpu_ms.load(std::memory_order_relaxed);\n        const float gpu = runtime_gpu_ms.load(std::memory_order_relaxed);\n        const float render_cost = std::max(frame_cpu, gpu);\n        uint32_t target = 1;\n        if ((inference > 105.0f && inference > 0.0f) || render_cost > 13.0f\n            || tile_count >= 4) {\n            target = 2;\n        } else if (inference > 0.0f && inference < 48.0f\n                   && render_cost < 7.0f && tile_count <= 2) {\n            target = 0;\n        } else if (inference > 80.0f || render_cost > 10.0f) {\n            target = 2;\n        }\n        if (target == current) {\n            auto_candidate_mode = target;\n            auto_candidate_streak = 0;\n            return current;\n        }\n        if (target != auto_candidate_mode) {\n            auto_candidate_mode = target;\n            auto_candidate_streak = 1;\n            return current;\n        }\n        const uint32_t required = target == 2 ? 2u : 5u;\n        if (++auto_candidate_streak >= required) {\n            active_performance_mode.store(target, std::memory_order_relaxed);\n            auto_candidate_streak = 0;\n            return target;\n        }\n        return current;\n    }\n\n    uint32_t adaptive_interval_ms(const DepthProfile& profile) const {\n        const float measured = last_inference_ms.load(std::memory_order_relaxed);\n        const float factor = profile.mode == 2 ? 0.55f : (profile.mode == 1 ? 0.70f : 0.82f);\n        const uint32_t measured_floor = measured > 0.0f\n            ? static_cast<uint32_t>(std::min(240.0f, measured * factor)) : 0u;\n        return std::max(profile.minimum_interval_ms, measured_floor);\n    }\n''',
    "automatic depth controller",
)

cpp = replace_once(
    cpp,
    '''        const DepthProfile requested = profile_for_mode(\n            performance_mode.load(std::memory_order_relaxed));\n''',
    '''        const uint32_t requested_mode = performance_mode.load(std::memory_order_relaxed);\n        const uint32_t resolved_mode = resolve_performance_mode(requested_mode);\n        const DepthProfile requested = profile_for_mode(resolved_mode);\n''',
    "resolved depth profile",
)

cpp = replace_once(
    cpp,
    '''            has_valid_depth = true;\n''',
    '''            has_valid_depth = true;\n            last_depth_upload_ms.store(steady_milliseconds(), std::memory_order_relaxed);\n''',
    "depth upload timestamp",
)

cpp = replace_once(
    cpp,
    '''    if (!impl_ || !impl_->session) {\n''',
    '''    if (!impl_ || !impl_->env || !impl_->fixed_session(1).session) {\n''',
    "fixed profile initialized check",
)

cpp = replace_once(
    cpp,
    '''void DepthInferencer::set_performance_mode(uint32_t mode) {\n    if (!impl_) return;\n    if (mode > 2) mode = 1;\n    impl_->performance_mode.store(mode, std::memory_order_relaxed);\n}\n''',
    '''void DepthInferencer::set_performance_mode(uint32_t mode) {\n    if (!impl_) return;\n    if (mode > 3) mode = 3;\n    impl_->performance_mode.store(mode, std::memory_order_relaxed);\n    if (mode <= 2) {\n        impl_->active_performance_mode.store(mode, std::memory_order_relaxed);\n    }\n}\n''',
    "allow auto mode",
)

cpp = replace_once(
    cpp,
    '''uint32_t DepthInferencer::performance_mode() const {\n    if (!impl_) return 1;\n    return impl_->performance_mode.load(std::memory_order_relaxed);\n}\n''',
    '''uint32_t DepthInferencer::performance_mode() const {\n    if (!impl_) return 1;\n    return impl_->performance_mode.load(std::memory_order_relaxed);\n}\n\nuint32_t DepthInferencer::active_performance_mode() const {\n    if (!impl_) return 1;\n    return impl_->active_performance_mode.load(std::memory_order_relaxed);\n}\n\nvoid DepthInferencer::set_runtime_load(float frame_cpu_ms, float gpu_ms) {\n    if (!impl_) return;\n    if (std::isfinite(frame_cpu_ms) && frame_cpu_ms >= 0.0f)\n        impl_->runtime_frame_cpu_ms.store(frame_cpu_ms, std::memory_order_relaxed);\n    if (std::isfinite(gpu_ms) && gpu_ms >= 0.0f)\n        impl_->runtime_gpu_ms.store(gpu_ms, std::memory_order_relaxed);\n}\n\nint DepthInferencer::active_model_width() const {\n    return impl_ ? impl_->active_model_width.load(std::memory_order_relaxed) : 0;\n}\n\nint DepthInferencer::active_model_height() const {\n    return impl_ ? impl_->active_model_height.load(std::memory_order_relaxed) : 0;\n}\n\nint DepthInferencer::active_scheduled_tiles() const {\n    return impl_ ? impl_->active_scheduled_tiles.load(std::memory_order_relaxed) : 0;\n}\n\nfloat DepthInferencer::last_inference_ms() const {\n    return impl_ ? impl_->last_inference_ms.load(std::memory_order_relaxed) : 0.0f;\n}\n\nfloat DepthInferencer::blend_duration_ms() const {\n    return impl_ ? impl_->blend_duration_sec * 1000.0f : 0.0f;\n}\n\nuint32_t DepthInferencer::depth_age_ms() const {\n    if (!impl_) return 0;\n    const uint64_t uploaded = impl_->last_depth_upload_ms.load(std::memory_order_relaxed);\n    if (uploaded == 0) return 0;\n    const uint64_t now = DepthInferImpl::steady_milliseconds();\n    return static_cast<uint32_t>(std::min<uint64_t>(UINT32_MAX, now - uploaded));\n}\n''',
    "depth telemetry facade",
)

cpp = replace_once(
    cpp,
    '''    impl_->inferences.store(0, std::memory_order_relaxed);\n''',
    '''    impl_->inferences.store(0, std::memory_order_relaxed);\n    impl_->active_performance_mode.store(1, std::memory_order_relaxed);\n    impl_->last_inference_ms.store(0.0f, std::memory_order_relaxed);\n    impl_->last_depth_upload_ms.store(0, std::memory_order_relaxed);\n''',
    "telemetry reset",
)

overlay = replace_once(
    overlay,
    '''static uint32_t g_depthMode = 1;       // 0=quality, 1=balanced, 2=fast\n''',
    '''static uint32_t g_depthMode = 3;       // 0=quality, 1=balanced, 2=fast, 3=auto\n''',
    "auto depth default",
)
overlay = replace_once(
    overlay,
    '''        case 2: return "fast";\n        default: return "balanced";\n''',
    '''        case 2: return "fast";\n        case 3: return "auto";\n        default: return "balanced";\n''',
    "depth mode name",
)
overlay = replace_once(
    overlay,
    '''    if (g_depth) g_depth->set_performance_mode(g_depthMode);\n''',
    '''    if (g_depth) {\n        g_depth->set_performance_mode(g_depthMode);\n        g_depth->set_runtime_load(\n            static_cast<float>(g_lastFrameCpuMs),\n            static_cast<float>(g_lastGpuMs));\n    }\n''',
    "runtime load feed",
)
overlay = replace_once(
    overlay,
    '''    Log("InitDepth: depth inference online (capture %ux%u, model 518x518)",\n        cd.Width, cd.Height);\n''',
    '''    Log("InitDepth: depth inference online (capture %ux%u, requested_mode=%s)",\n        cd.Width, cd.Height, DepthModeName(g_depthMode));\n''',
    "depth startup telemetry",
)
overlay = replace_once(
    overlay,
    '''            "depth[total=%llu %dHz mode=%s] timing[capture_cpu=%.3f draw_gpu=%.3f present_cpu=%.3f frame_cpu=%.3f] backend=%u layout=%u eye_order=%u ipd=%.2f focus=%.2f panel=%ux%u tracking=%u "\n''',
    '''            "depth[total=%llu %dHz mode=%s active=%s profile=%dx%d tiles=%d inference_ms=%.2f blend_ms=%.1f age_ms=%u] timing[capture_cpu=%.3f draw_gpu=%.3f present_cpu=%.3f frame_cpu=%.3f] backend=%u layout=%u eye_order=%u ipd=%.2f focus=%.2f panel=%ux%u tracking=%u "\n''',
    "depth summary format",
)
overlay = replace_once(
    overlay,
    '''            (unsigned long long)infNow, depthHz, DepthModeName(g_depthMode),\n            g_lastCaptureCpuMs, g_lastGpuMs, g_lastPresentCpuMs, g_lastFrameCpuMs, g_displayBackend,\n''',
    '''            (unsigned long long)infNow, depthHz, DepthModeName(g_depthMode),\n            g_depth ? DepthModeName(g_depth->active_performance_mode()) : "balanced",\n            g_depth ? g_depth->active_model_width() : 0,\n            g_depth ? g_depth->active_model_height() : 0,\n            g_depth ? g_depth->active_scheduled_tiles() : 0,\n            g_depth ? g_depth->last_inference_ms() : 0.0f,\n            g_depth ? g_depth->blend_duration_ms() : 0.0f,\n            g_depth ? g_depth->depth_age_ms() : 0u,\n            g_lastCaptureCpuMs, g_lastGpuMs, g_lastPresentCpuMs, g_lastFrameCpuMs, g_displayBackend,\n''',
    "depth summary values",
)

CPP.write_text(cpp, encoding="utf-8", newline="\n")
HEADER.write_text(header, encoding="utf-8", newline="\n")
OVERLAY.write_text(overlay, encoding="utf-8", newline="\n")
