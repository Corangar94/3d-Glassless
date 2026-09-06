from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_zero_strength_is_exposed_by_ui_and_applied_by_configured_overlay():
    gui = _source("launcher/settings_gui.py")
    cmake = _source("overlay/CMakeLists.txt")

    assert "lo=0.0, hi=5.0" in gui
    assert '"if (s.strengthX    > 0.0f)    sx = s.strengthX;"' in cmake
    assert '"if (s.strengthX    >= 0.0f)   sx = s.strengthX;"' in cmake
    assert '"if (s.strengthY    > 0.0f)    sy = s.strengthY;"' in cmake
    assert '"if (s.strengthY    >= 0.0f)   sy = s.strengthY;"' in cmake


def test_stable_snapshot_is_validated_before_any_field_is_applied():
    cmake = _source("overlay/CMakeLists.txt")
    validation = cmake.index(
        "const auto settingsValidation = "
        "g3d::settings::ValidateSharedSettings({"
    )
    rejection = cmake.index("if (!settingsValidation.accepted())")
    horizontal = cmake.index('"if (s.strengthX    > 0.0f)')
    curve = cmake.index('"dc = s.depthCurve;"')
    backend = cmake.index('"db = s.displayBackend;"')

    assert validation < rejection < horizontal < curve < backend
    assert "Rejected G3D_Settings version=%u" in cmake
    assert "lastRejectedSettingsVersion != s.version" in cmake


def test_unknown_native_enums_use_existing_safe_fallbacks():
    cmake = _source("overlay/CMakeLists.txt")
    policy = _source("overlay/settings_policy.h")
    depth_mode = _source("overlay/depth_mode_policy.h")

    assert '"dc = g3d::settings::NormalizeDepthCurve(s.depthCurve);"' in cmake
    assert (
        '"db = g3d::settings::NormalizeDisplayBackend(s.displayBackend);"'
        in cmake
    )
    assert (
        '"dm = g3d::depth_mode::NormalizeRequestedMode(s.depthMode);"'
        in cmake
    )
    assert "return value <= 2u ? value : 1u;" in policy
    assert "return value <= 2u ? value : 0u;" in policy
    assert "IsSupported(mode) ? mode : kBalanced" in depth_mode


def test_native_policy_rejects_nonfinite_and_pathological_numeric_values():
    policy = _source("overlay/settings_policy.h")

    assert "std::isfinite(value)" in policy
    assert "kMaxStrength = 16.0f" in policy
    assert "kMaxVirtualDepthCm = 1000.0f" in policy
    assert "kMaxScreenDimensionCm = 1000.0f" in policy
    assert "kMaxPanelDimensionPx = 65535u" in policy
    assert "rejected_fields |= kPanelWidth" in policy
    assert "rejected_fields |= kPanelHeight" in policy


def test_settings_abi_and_stable_reader_are_unchanged():
    overlay = _source("overlay/overlay.cpp")
    shared = _source("tracker/shared_settings.py")

    assert 'SHM_SETTINGS  = L"G3D_Settings"' in overlay
    assert "static_assert(sizeof(Settings) == 88" in overlay
    assert 'STRUCT_FORMAT = "<fffffIfffffffIII" "IIIIfI"' in shared
    assert "STRUCT_SIZE = struct.calcsize(STRUCT_FORMAT)  # == 88" in shared
    assert "first.version == second.version" in overlay
    assert "memcmp(&first, &second, sizeof(first)) == 0" in overlay


def test_native_settings_suite_is_registered_with_ctest():
    cmake = _source("overlay/CMakeLists.txt")

    assert "settings_policy_tests.cpp" in cmake
    assert "NAME settings_policy_tests" in cmake


def test_documentation_describes_atomic_rejection_zero_strength_and_abi():
    docs = _source("docs/NATIVE_SETTINGS_VALIDATION.md")

    assert "retains the last complete valid settings state" in docs
    assert "Zero is now a valid value for both axes" in docs
    assert "depth mode → balanced" in docs
    assert "structure size remains 88 bytes" in docs
