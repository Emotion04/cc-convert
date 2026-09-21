@echo off
rem Change to this script's directory so "uv run" can find pyproject.toml
cd /d "%~dp0"
echo ========================================
echo         CC Convert Multi-Instance
echo ========================================
echo.
echo [1/2] Starting CC Convert manager...
uv run ccconvert restart
if %errorlevel% neq 0 (
    uv run ccconvert start
    if %errorlevel% neq 0 (
        echo Failed to start. Check: uv run ccconvert logs
        pause
        exit /b 1
    )
)
echo.
echo [2/2] Opening Dashboard...
uv run ccconvert open
echo.
uv run ccconvert status
echo.
echo Dashboard opened. Press any key to exit...
pause >nul
