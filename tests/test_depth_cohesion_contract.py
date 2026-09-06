from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_production_shader_uses_shared_three_sample_trimmed_mean():
    overlay = _source("overlay/overlay.cpp")
    sample = overlay.split("DepthSample SampleDepthCohesive(", 1)[1].split(
        "// Parallax shift", 1
    )[0]
    assert '#include "depth_cohesion_shader.h"' in overlay
    assert 'static const char PS_SRC[] = G3D_DEPTH_COHESION_HLSL R"hlsl(' in overlay
    assert "G3DTrimmedMean5(d0, dl, dr, du, dd)" in sample
    assert "localMin" not in sample
    assert "localMax" not in sample
    assert "* 0.25f" not in sample


def test_shared_hlsl_and_portable_oracle_both_divide_by_three():
    header = _source("overlay/depth_cohesion_shader.h")
    assert "inline float TrimmedMean5(" in header
    assert " / 3.0f" in header
    assert "#define G3D_DEPTH_COHESION_HLSL" in header
    assert " / 3.0);" in header
    assert "* 0.25" not in header


def test_portable_and_warp_numeric_suites_are_registered():
    cmake = _source("overlay/CMakeLists.txt")
    assert "depth_cohesion_tests.cpp" in cmake
    assert "NAME depth_cohesion_tests" in cmake
    assert "shader_numeric_tests.cpp" in cmake
    assert "NAME shader_numeric_tests" in cmake


def test_warp_suite_executes_production_shader_against_middle_three_oracle():
    test = _source("overlay/shader_numeric_tests.cpp")
    assert 'Extract(source,"PS_SRC")' in test
    assert "std::string(G3D_DEPTH_COHESION_HLSL)" in test
    assert "D3D_DRIVER_TYPE_WARP" in test
    assert "(samples[1]+samples[2]+samples[3])/3.0f" in test
    assert "six WARP numeric cases" in test


def test_documentation_records_visual_failure_and_exact_correction():
    docs = _source("docs/DEPTH_COHESION_FILTER.md")
    assert "divided those three" in docs
    assert "samples by four" in docs
    assert "biased toward zero" in docs
    assert "divides by three" in docs
    assert "silhouettes" in docs
