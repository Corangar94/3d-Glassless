cmake_minimum_required(VERSION 3.20)
project(Glassless3D LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(RESHADE_INCLUDE "${CMAKE_SOURCE_DIR}/../vendor/reshade/include"
    CACHE PATH "Path to ReShade SDK include directory")

add_library(Glassless3D SHARED Glassless3D.cpp)

target_include_directories(Glassless3D PRIVATE ${RESHADE_INCLUDE})

target_compile_definitions(Glassless3D PRIVATE
    WIN32_LEAN_AND_MEAN NOMINMAX UNICODE _UNICODE)

if(CMAKE_SIZEOF_VOID_P EQUAL 8)
    set(GLASSLESS3D_ADDON_SUFFIX ".addon64")
elseif(CMAKE_SIZEOF_VOID_P EQUAL 4)
    set(GLASSLESS3D_ADDON_SUFFIX ".addon32")
else()
    message(FATAL_ERROR "Unsupported pointer size for ReShade addon")
endif()
set_target_properties(Glassless3D PROPERTIES
    SUFFIX "${GLASSLESS3D_ADDON_SUFFIX}"
    PREFIX "")

# Optimisation flags — MSVC and GCC/MinGW both supported
if(MSVC)
    target_compile_options(Glassless3D PRIVATE $<$<CONFIG:Release>:/O2 /GL>)
    target_link_options(Glassless3D   PRIVATE $<$<CONFIG:Release>:/LTCG>)
else()
    target_compile_options(Glassless3D PRIVATE -O2 -flto)
    # Add-ons are copied into arbitrary game folders. Keep the MinGW runtime
    # self-contained so libgcc_s_* and libstdc++-6.dll are never prerequisites.
    target_link_options(Glassless3D PRIVATE
        -flto -static -static-libgcc -static-libstdc++)
endif()
