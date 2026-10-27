@echo off
rem Change to this script's directory so "uv run" can find pyproject.toml
cd /d "%~dp0"
echo ========================================
echo         CC Convert Multi-Instance
echo ========================================
echo.
echo [1/2] Ensuring CC Convert manager is running...
rem "start" is idempotent: it starts the manager only when it is not already
rem running, so double-clicking this while it runs does not kill live sessions.
uv run ccconvert start
if %errorlevel% neq 0 (
    echo Failed to start. Check: uv run ccconvert logs
    pause
    exit /b 1
)
echo.
echo [2/2] Opening Dashboard...
uv run ccconvert open
echo.
uv run ccconvert status
echo.
echo Dashboard opened. Press any key to exit...
pause >nul
