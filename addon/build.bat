@echo off
setlocal

echo === Glassless3D Addon Build ===

if not exist "..\vendor\reshade\include\reshade.hpp" (
    echo ERROR: ReShade SDK headers not found.
    echo Run the Python one-liner in Task 8 of the plan first.
    exit /b 1
)

if not exist build mkdir build
cd build

cmake .. -G "Visual Studio 17 2022" -A x64
if errorlevel 1 ( echo ERROR: CMake configure failed. & exit /b 1 )

cmake --build . --config Release
if errorlevel 1 ( echo ERROR: Build failed. & exit /b 1 )

copy /Y Release\Glassless3D.addon ..\..\
echo.
echo === Build complete: Glassless3D.addon ===
