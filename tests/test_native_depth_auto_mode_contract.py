from pathlib import Path

import yaml


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_depth_mode_policy_preserves_auto_and_fails_unknown_to_balanced():
    policy = _source("overlay/depth_mode_policy.h")

    assert "inline constexpr uint32_t kBalanced = 1" in policy
    assert "inline constexpr uint32_t kAuto = 3" in policy
    assert "return kAuto;" in policy
    assert "return mode <= kAuto;" in policy
    assert "IsSupported(mode) ? mode : kBalanced" in policy


def test_configured_overlay_uses_policy_for_default_and_shared_request():
    source = _source("overlay/overlay.cpp")
    cmake = _source("overlay/CMakeLists.txt")
    assert 'uint32_t dm = g3d::depth_mode::DefaultRequestedMode();' in source
    assert 'dm = g3d::depth_mode::NormalizeRequestedMode(s.depthMode);' in source
    assert '#include "depth_mode_policy.h"' in source
    assert 'overlay.configured.cpp' not in cmake
    assert 'add_executable(Glassless3DOverlay WIN32\n    overlay.cpp' in cmake


def test_overlay_build_fails_unless_each_mode_anchor_is_unique():
    cmake = _source("overlay/CMakeLists.txt")
    assert "g3d_replace_overlay_once" not in cmake
    assert "G3D_OVERLAY_SOURCE_TEXT" not in cmake
    assert 'depth_mode_policy_tests.cpp' in cmake
    assert 'NAME depth_mode_policy_tests' in cmake


def test_policy_header_injection_does_not_depend_on_source_line_endings():
    source = _source("overlay/overlay.cpp")
    for text in (source, source.replace("\n", "\r\n")):
        assert text.count('#include "depth_mode_policy.h"') == 1
        assert text.count('NormalizeRequestedMode(s.depthMode)') == 1
    assert 'file(READ' not in _source("overlay/CMakeLists.txt")


def test_inferencer_accepts_auto_and_resolves_it_at_run_time():
    source = _source("overlay/depth_infer.cpp")
    setter = source.split(
        "void DepthInferencer::set_performance_mode(uint32_t mode)",
        1,
    )[1].split(
        "uint32_t DepthInferencer::performance_mode() const",
        1,
    )[0]
    run_once = source.split("    bool run_once(", 1)[1].split(
        "    // WORKER THREAD:",
        1,
    )[0]

    assert "if (mode > 3) mode = 3;" in setter
    assert "if (mode <= 2)" in setter
    assert "resolve_performance_mode(requested_mode)" in run_once


def test_shared_settings_and_repository_default_request_auto():
    shared = _source("tracker/shared_settings.py")
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))

    assert "depth_mode: int = 3" in shared
    assert (
        '_enum_uint32(s.depth_mode, "depth_mode", (0, 1, 2, 3))'
        in shared
    )
    assert config["overlay"]["depth_performance_mode"] == "auto"


def test_production_overlay_build_uses_configured_cmake_target():
    bootstrap = _source("scripts/_bootstrap_core.py")
    build = bootstrap.split("def step_build_overlay()", 1)[1].split(
        "\n\n# -- Main",
        1,
    )[0]

    assert '[cmake, OVERLAY_SRC, "-B", build_dir' in build
    assert '[cmake, "--build", build_dir' in build
    assert "[gpp, " not in build
    assert "overlay.cpp" not in build


def test_native_depth_mode_suite_is_registered_with_ctest():
    cmake = _source("overlay/CMakeLists.txt")

    assert "depth_mode_policy_tests.cpp" in cmake
    assert "NAME depth_mode_policy_tests" in cmake


def test_documentation_describes_requested_and_active_modes():
    docs = _source("docs/NATIVE_DEPTH_AUTO_MODE.md")

    assert "Mode `3` delegates" in docs
    assert "mode=auto" in docs
    assert "active=" in docs
    assert "Unknown values" in docs
