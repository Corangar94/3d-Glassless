cmake_minimum_required(VERSION 3.16)
project(Glassless3DOverlay CXX)

include(CTest)
option(G3D_BUILD_OVERLAY "Build the Windows desktop overlay" ${WIN32})
option(G3D_BUILD_GPU_TESTS "Build real Windows D3D shader/inference tests" ${WIN32})

if(BUILD_TESTING)
    add_executable(audit_policy_tests audit_policy_tests.cpp)
    target_compile_features(audit_policy_tests PRIVATE cxx_std_17)
    add_test(NAME audit_policy_tests COMMAND audit_policy_tests)
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

    add_executable(depth_mode_policy_tests
        depth_mode_policy_tests.cpp
    )
    target_compile_features(depth_mode_policy_tests PRIVATE cxx_std_17)
    add_test(NAME depth_mode_policy_tests COMMAND depth_mode_policy_tests)
endif()

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT G3D_BUILD_OVERLAY)
    return()  # Portable policy tests need no Windows SDK or downloaded models.
endif()

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

# Compile canonical source directly: no string replacements or Win32 API macros.
add_executable(Glassless3DOverlay WIN32
    overlay.cpp
    depth_infer.cpp
    capture_recovery.cpp
)
execute_process(COMMAND git rev-parse HEAD
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/.."
    OUTPUT_VARIABLE G3D_SOURCE_COMMIT OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET)
if(NOT G3D_SOURCE_COMMIT MATCHES "^[0-9a-f]+$")
    set(G3D_SOURCE_COMMIT "unknown")
endif()
target_compile_definitions(Glassless3DOverlay PRIVATE
    G3D_SOURCE_COMMIT="${G3D_SOURCE_COMMIT}")

target_compile_definitions(Glassless3DOverlay PRIVATE UNICODE _UNICODE)

target_include_directories(Glassless3DOverlay PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
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

# Deployment is part of build success; locked/stale destinations must fail.
add_custom_command(TARGET Glassless3DOverlay POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "$<TARGET_FILE:Glassless3DOverlay>"
        "${CMAKE_CURRENT_SOURCE_DIR}/../Glassless3DOverlay.exe"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${ORT_DIR}/lib/onnxruntime.dll"
        "${CMAKE_CURRENT_SOURCE_DIR}/../onnxruntime.dll"
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${DML_DIR}/lib/DirectML.dll"
        "${CMAKE_CURRENT_SOURCE_DIR}/../DirectML.dll"
    COMMAND ${CMAKE_COMMAND}
        "-DROOT=${CMAKE_CURRENT_SOURCE_DIR}/.."
        "-DSOURCE_COMMIT=${G3D_SOURCE_COMMIT}"
        "-DCOMPILER=${CMAKE_CXX_COMPILER_ID}-${CMAKE_CXX_COMPILER_VERSION}"
        -P "${CMAKE_CURRENT_SOURCE_DIR}/write_build_manifest.cmake"
    VERBATIM
)

if(BUILD_TESTING AND G3D_BUILD_GPU_TESTS)
    add_executable(shader_numeric_tests shader_numeric_tests.cpp)
    target_compile_features(shader_numeric_tests PRIVATE cxx_std_17)
    target_link_libraries(shader_numeric_tests PRIVATE d3d11 d3dcompiler dxgi)
    add_test(NAME shader_numeric_tests
        COMMAND shader_numeric_tests "${CMAKE_CURRENT_SOURCE_DIR}/overlay.cpp")
    set_tests_properties(shader_numeric_tests PROPERTIES TIMEOUT 30)

    add_executable(depth_integration_tests depth_integration_tests.cpp depth_infer.cpp)
    target_compile_features(depth_integration_tests PRIVATE cxx_std_17)
    target_compile_definitions(depth_integration_tests PRIVATE UNICODE _UNICODE)
    target_include_directories(depth_integration_tests PRIVATE
        "${ORT_DIR}/include" "${DML_DIR}/include")
    target_link_libraries(depth_integration_tests PRIVATE
        d3d11 d3d12 d3dcompiler dxgi ole32
        "${ORT_DIR}/lib/onnxruntime.lib" "${DML_DIR}/lib/DirectML.lib")
    add_custom_command(TARGET depth_integration_tests POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different "${ORT_DIR}/lib/onnxruntime.dll"
            "$<TARGET_FILE_DIR:depth_integration_tests>"
        COMMAND ${CMAKE_COMMAND} -E copy_if_different "${DML_DIR}/lib/DirectML.dll"
            "$<TARGET_FILE_DIR:depth_integration_tests>"
        VERBATIM)
    add_test(NAME depth_integration_tests COMMAND depth_integration_tests)
    set_tests_properties(depth_integration_tests PROPERTIES TIMEOUT 90)
endif()
