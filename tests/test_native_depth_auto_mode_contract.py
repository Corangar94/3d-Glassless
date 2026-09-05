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
    cmake = _source("overlay/CMakeLists.txt")

    assert '"uint32_t dm = 1;"' in cmake
    assert (
        '"uint32_t dm = g3d::depth_mode::DefaultRequestedMode();"'
        in cmake
    )
    assert '"dm = s.depthMode <= 2 ? s.depthMode : 1;"' in cmake
    assert (
        '"dm = g3d::depth_mode::NormalizeRequestedMode(s.depthMode);"'
        in cmake
    )
    assert (
        '"#include \\"depth_mode_policy.h\\"\\n${G3D_OVERLAY_SOURCE_TEXT}"'
        in cmake
    )
    assert "CMAKE_CONFIGURE_DEPENDS" in cmake
    assert "overlay.configured.cpp" in cmake


def test_overlay_build_fails_unless_each_mode_anchor_is_unique():
    cmake = _source("overlay/CMakeLists.txt")
    helper = cmake.split(
        "function(g3d_replace_overlay_once",
        1,
    )[1].split("endfunction()", 1)[0]

    assert "string(LENGTH" in helper
    assert "match_count" in helper
    assert "if(NOT match_count EQUAL 1)" in helper
    assert "message(FATAL_ERROR" in helper
    assert "PARENT_SCOPE" in helper
    assert cmake.count("g3d_replace_overlay_once(") == 2


def test_policy_header_injection_does_not_depend_on_source_line_endings():
    cmake = _source("overlay/CMakeLists.txt")
    setup = cmake.split(
        'file(READ "${CMAKE_CURRENT_SOURCE_DIR}/overlay.cpp"',
        1,
    )[1].split("g3d_replace_overlay_once(", 1)[0]

    assert "Prepending the small policy header" in setup
    assert '#include \\"depth_mode_policy.h\\"' in setup
    assert "capture_recovery.h" not in setup
    assert "depth_infer.h" not in setup


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
