@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM Blender Add-on Tests - Runner Script
REM Runs all tests in separate Blender processes.
REM
REM Requirements:
REM   - Blender must be available in PATH, or BLENDER_EXE must be set
REM   - The add-on must be installed in Blender's add-ons path
REM
REM Usage:
REM   scripts\tests\run_tests.bat
REM   scripts\tests\run_tests.bat "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"
REM ============================================================================

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%..\.."

REM Resolve Blender path
if not "%~1"=="" (
    set "BLENDER=%~1"
) else if defined BLENDER_EXE (
    set "BLENDER=%BLENDER_EXE%"
) else (
    set "BLENDER=blender"
)

echo ============================================
echo  CityGML Importer/Exporter - Blender Tests
echo ============================================
echo Blender: %BLENDER%
echo Project: %PROJECT_DIR%
echo.

set PASS=0
set FAIL=0

REM --- Test 1: Add-on activation ---
echo [1/3] Test: Enable add-on...
"%BLENDER%" --background --python "%SCRIPT_DIR%test_activate.py" 2>&1 | findstr /C:"[TEST]"
if !ERRORLEVEL! equ 0 (
    set /a PASS+=1
) else (
    set /a FAIL+=1
    echo        FAILED
)
echo.

REM --- Test 2: Import all feature types ---
echo [2/3] Test: Import all feature types...
"%BLENDER%" --background --addons city_modeler --python "%SCRIPT_DIR%test_import.py" -- --dir "%PROJECT_DIR%\test_files" 2>&1 | findstr /C:"[TEST]" /C:"PASS" /C:"FAIL"
if !ERRORLEVEL! equ 0 (
    set /a PASS+=1
) else (
    set /a FAIL+=1
    echo        FAILED
)
echo.

REM --- Test 3: Export all feature types ---
echo [3/3] Test: Export all feature types...
"%BLENDER%" --background --addons city_modeler --python "%SCRIPT_DIR%test_export.py" -- --dir "%PROJECT_DIR%\test_files" 2>&1 | findstr /C:"[TEST]" /C:"PASS" /C:"FAIL"
if !ERRORLEVEL! equ 0 (
    set /a PASS+=1
) else (
    set /a FAIL+=1
    echo        FAILED
)
echo.

REM --- Summary ---
echo ============================================
echo  Result: %PASS% passed, %FAIL% failed
echo ============================================

if !FAIL! gtr 0 (
    exit /b 1
)
exit /b 0
