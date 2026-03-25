@echo off
setlocal
chcp 65001 >nul

cd /d "%~dp0"
echo Starting Last War Bot...
"%~dp0LastWarBot.exe" --config config.yaml
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="100" (
    echo.
    echo Bot instance is already running. Press any key to exit.
    pause >nul
) else if not "%EXIT_CODE%"=="0" (
    echo.
    echo Last War Bot exited with code %EXIT_CODE%.
    pause
)

endlocal & exit /b %EXIT_CODE%
