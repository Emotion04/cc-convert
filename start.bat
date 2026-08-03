@echo off
echo ========================================
echo         CC Convert Multi-Instance
echo ========================================
echo.
echo [1/2] Starting CC Convert manager...
ccconvert restart
if %errorlevel% neq 0 (
    ccconvert start
    if %errorlevel% neq 0 (
        echo Failed to start. Check: ccconvert logs
        pause
        exit /b 1
    )
)
echo.
echo [2/2] Opening Dashboard...
ccconvert open
echo.
ccconvert status
echo.
echo Dashboard opened. Press any key to exit...
pause >nul
