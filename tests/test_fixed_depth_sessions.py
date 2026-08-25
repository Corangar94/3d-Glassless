from pathlib import Path


def test_depth_profiles_use_supported_c_api_dimension_overrides():
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "OrtApi const& api = Ort::GetApi();" in source
    assert "Ort::ThrowOnError(api.AddFreeDimensionOverrideByName(" in source
    assert '"batch_size", 1' in source
    assert '"height", profile.height' in source
    assert '"width", profile.width' in source
    assert "fixed.options->AddFreeDimensionOverrideByName" not in source


def test_depth_profiles_have_lazy_session_cache():
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "struct FixedProfileSession" in source
    assert "std::array<FixedProfileSession, 3> profile_sessions" in source
    assert "ensure_fixed_session(1)" in source
    assert "ensure_fixed_session(running_profile.mode)" in source
    assert "created lazily only when selected" in source


def test_each_session_has_its_own_interruptible_run_options():
    source = Path("overlay/depth_infer.cpp").read_text(encoding="utf-8")

    assert "std::unique_ptr<Ort::RunOptions> run_options;" in source
    assert "fixed.run_options = std::make_unique<Ort::RunOptions>()" in source
    assert "fixed.session->Run(" in source
    assert "*fixed.run_options" in source
    assert "for (auto& fixed : profile_sessions)" in source
    assert "fixed.run_options->SetTerminate();" in source
    assert source.index("fixed.run_options->SetTerminate();") < source.index(
        "worker.join()"
    )
