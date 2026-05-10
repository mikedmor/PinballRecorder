@echo off
title Pinball Screen Recorder - Launcher
cd /d "%~dp0"

echo ============================================
echo   Pinball Screen Recorder - Launcher
echo ============================================
echo.

:: Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Please install Python from https://python.org and ensure
    echo "Add Python to PATH" is checked during installation.
    echo.
    pause
    exit /b 1
)

echo [OK] Python found.

:: Launch the recorder
echo Launching Pinball Screen Recorder...
echo.
python "%~dp0PinballRecorder.py"

if errorlevel 1 (
    echo.
    echo [ERROR] The application exited with an error.
    pause
)
