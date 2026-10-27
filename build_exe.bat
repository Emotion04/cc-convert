@echo off
rem Build the portable onedir executable.
rem Ship the whole dist\ccconvert folder; ccconvert.exe alone will not run.
cd /d "%~dp0"
echo ========================================
echo         Building ccconvert.exe
echo ========================================
echo.

uv sync --group build
if %errorlevel% neq 0 (
    echo Failed to install build dependencies.
    pause
    exit /b 1
)

uv run pyinstaller ccconvert.spec --noconfirm --clean
if %errorlevel% neq 0 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Built: %~dp0dist\ccconvert\ccconvert.exe
echo Copy the whole "dist\ccconvert" folder to distribute.
pause
