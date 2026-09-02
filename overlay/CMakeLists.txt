cmake_minimum_required(VERSION 3.16)
project(Glassless3DOverlay CXX)

include(CTest)

if(BUILD_TESTING)
    add_executable(capture_recovery_tests
        capture_recovery.cpp
        capture_recovery_tests.cpp
    )
    target_compile_features(capture_recovery_tests PRIVATE cxx_std_17)
    add_test(NAME capture_recovery_tests COMMAND capture_recovery_tests)

    add_executable(parallax_health_tests
        parallax_health_tests.cpp
    )
    target_compile_features(parallax_health_tests PRIVATE cxx_std_17)
    add_test(NAME parallax_health_tests COMMAND parallax_health_tests)

    add_executable(pose_prediction_tests
        pose_prediction_tests.cpp
    )
    target_compile_features(pose_prediction_tests PRIVATE cxx_std_17)
    add_test(NAME pose_prediction_tests COMMAND pose_prediction_tests)

    add_executable(depth_result_freshness_tests
        depth_result_freshness_tests.cpp
    )
    target_compile_features(depth_result_freshness_tests PRIVATE cxx_std_17)
    add_test(
        NAME depth_result_freshness_tests
        COMMAND depth_result_freshness_tests
    )
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Vendor paths populated by scripts/bootstrap.py (steps 4/7 and 5/7).
set(ORT_DIR "${CMAKE_SOURCE_DIR}/../vendor/onnxruntime"
    CACHE PATH "ONNX Runtime (DirectML) root")
set(DML_DIR "${CMAKE_SOURCE_DIR}/../vendor/directml"
    CACHE PATH "DirectML root")

if(NOT EXISTS "${ORT_DIR}/lib/onnxruntime.dll")
    message(FATAL_ERROR
        "ONNX Runtime not found at ${ORT_DIR}.\n"
        "Run: python scripts/bootstrap.py")
endif()
if(NOT EXISTS "${DML_DIR}/lib/DirectML.dll")
    message(FATAL_ERROR
        "DirectML not found at ${DML_DIR}.\n"
        "Run: python scripts/bootstrap.py")
endif()

add_executable(Glassless3DOverlay WIN32 overlay.cpp depth_infer.cpp capture_recovery.cpp)

# Only overlay.cpp owns g_depth/g_capTex/g_hasFrame. Enable the visibility
# guard there without leaking its ShowWindow wrapper into depth_infer.cpp.
set_source_files_properties(overlay.cpp PROPERTIES
    COMPILE_DEFINITIONS G3D_OVERLAY_SHOWWINDOW_GUARD
)

target_compile_definitions(Glassless3DOverlay PRIVATE UNICODE _UNICODE)

target_include_directories(Glassless3DOverlay PRIVATE
    "${ORT_DIR}/include"
    "${DML_DIR}/include"
)

target_link_libraries(Glassless3DOverlay PRIVATE
    d3d11
    d3d12
    dxgi
    d3dcompiler
    user32
    gdi32
    shell32
    ole32
    runtimeobject
    windowscodecs
    "${ORT_DIR}/lib/onnxruntime.lib"
    "${DML_DIR}/lib/DirectML.lib"
)

if(MSVC)
    target_compile_options(Glassless3DOverlay PRIVATE $<$<CONFIG:Release>:/O2 /GL>)
    target_link_options(Glassless3DOverlay PRIVATE $<$<CONFIG:Release>:/LTCG>)
else()
    target_compile_options(Glassless3DOverlay PRIVATE $<$<CONFIG:Release>:-O2>)
endif()

# Copy exe + runtime DLLs to project root after build.
# Runtime DLLs must sit next to the exe: onnxruntime.dll, DirectML.dll.
# `exit 0` keeps the build green even if the target exe is running (locked).
add_custom_command(TARGET Glassless3DOverlay POST_BUILD
    COMMAND cmd /c "copy /Y \"$<TARGET_FILE:Glassless3DOverlay>\" \"${CMAKE_SOURCE_DIR}\\..\\Glassless3DOverlay.exe\" >nul 2>&1 & exit 0"
    COMMAND cmd /c "copy /Y \"${ORT_DIR}\\lib\\onnxruntime.dll\" \"${CMAKE_SOURCE_DIR}\\..\\onnxruntime.dll\" >nul 2>&1 & exit 0"
    COMMAND cmd /c "copy /Y \"${DML_DIR}\\lib\\DirectML.dll\" \"${CMAKE_SOURCE_DIR}\\..\\DirectML.dll\" >nul 2>&1 & exit 0"
    VERBATIM
)
