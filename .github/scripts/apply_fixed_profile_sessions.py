from pathlib import Path
import re

path = Path("overlay/depth_infer.cpp")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"expected one match for {old[:100]!r}")
    text = text.replace(old, new, 1)


def regex_once(pattern: str, replacement: str) -> None:
    global text
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"expected one regex match for {pattern[:100]!r}, got {count}")
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
            DepthProfile profile = profile_for_mode(mode);
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
)

replace_once(
    '''        if (run_options) run_options->SetTerminate();
''',
    '''        for (auto& fixed : profile_sessions) {
            if (fixed.run_options) fixed.run_options->SetTerminate();
        }
''',
)
replace_once(
    '''        run_options.reset();
        session.reset();
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
)

path.write_text(text, encoding="utf-8", newline="\n")
