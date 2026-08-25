from pathlib import Path
import re

path = Path("overlay/depth_infer.cpp")
text = path.read_text(encoding="utf-8")
original = text


def replace_once(old: str, new: str, label: str) -> None:
    global text
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


def regex_once(pattern: str, replacement: str, label: str) -> None:
    global text
    if replacement in text:
        return
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    text = updated


replace_once(
    '''    std::unique_ptr<Ort::SessionOptions> opts;
    std::unique_ptr<Ort::Session>        session;
    std::unique_ptr<Ort::RunOptions>     run_options;
''',
    '''    struct FixedProfileSession {
        std::unique_ptr<Ort::SessionOptions> options;
        std::unique_ptr<Ort::Session> session;
        std::unique_ptr<Ort::RunOptions> run_options;
    };
    std::array<FixedProfileSession, 3> profile_sessions;
    std::wstring model_path_copy;
    int dml_device_id = 0;
''',
    "ORT state",
)

regex_once(
    r'''    bool create_ort_session\(const std::wstring& model_path\) \{.*?\n    \}\n\n    // CPU: downsample''',
    '''    FixedProfileSession& fixed_session(uint32_t mode) {
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
            fixed.options->AddFreeDimensionOverrideByName("batch_size", 1);
            fixed.options->AddFreeDimensionOverrideByName("height", profile.height);
            fixed.options->AddFreeDimensionOverrideByName("width", profile.width);
            OrtApi const& api = Ort::GetApi();
            OrtStatus* status = OrtSessionOptionsAppendExecutionProvider_DML(
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
            // Balanced is the startup default. Fast and quality sessions are
            // created lazily only when selected, avoiding three copies of the
            // model weights on every machine.
            return ensure_fixed_session(1);
        } catch (const Ort::Exception& exception) {
            last_err = std::string("ORT init exception: ") + exception.what();
            return false;
        }
    }

    // CPU: downsample''',
    "ORT session factory",
)

replace_once(
    '''                    auto outputs = session->Run(
                        *run_options, input_names, &input, 1, output_names, 1);
''',
    '''                    if (!ensure_fixed_session(running_profile.mode)) {
                        error = last_err;
                        ok = false;
                        break;
                    }
                    FixedProfileSession& fixed = fixed_session(running_profile.mode);
                    auto outputs = fixed.session->Run(
                        *fixed.run_options, input_names, &input, 1, output_names, 1);
''',
    "profile Run call",
)

regex_once(
    r'''            if \(run_options\) \{\n                try \{\n                    run_options->SetTerminate\(\);\n                \} catch \(\.\.\.\) \{\n                    // cleanup/destruction must not throw; join remains the\n                    // final synchronization point for the session lifetime\.\n                \}\n            \}\n''',
    '''            for (auto& fixed : profile_sessions) {
                if (!fixed.run_options) continue;
                try {
                    fixed.run_options->SetTerminate();
                } catch (...) {
                    // cleanup/destruction must not throw; join remains the
                    // final synchronization point for the session lifetime.
                }
            }
''',
    "profile RunOptions termination",
)

replace_once(
    '''        session.reset();
        run_options.reset();
        opts.reset();
        env.reset();
''',
    '''        for (auto& fixed : profile_sessions) {
            fixed.run_options.reset();
            fixed.session.reset();
            fixed.options.reset();
        }
        model_path_copy.clear();
        env.reset();
''',
    "profile session cleanup",
)

if text == original:
    raise RuntimeError("fixed-profile session migration made no changes")
path.write_text(text, encoding="utf-8", newline="\n")
