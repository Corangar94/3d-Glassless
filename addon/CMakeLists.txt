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

set_target_properties(Glassless3D PROPERTIES
    SUFFIX ".addon"
    PREFIX "")

# Optimisation flags — MSVC and GCC/MinGW both supported
if(MSVC)
    target_compile_options(Glassless3D PRIVATE $<$<CONFIG:Release>:/O2 /GL>)
    target_link_options(Glassless3D   PRIVATE $<$<CONFIG:Release>:/LTCG>)
else()
    target_compile_options(Glassless3D PRIVATE $<$<CONFIG:Release>:-O2 -flto>)
    target_link_options(Glassless3D   PRIVATE $<$<CONFIG:Release>:-flto>)
endif()
