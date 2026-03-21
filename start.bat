@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0"
set "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True"

set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

echo Starting Last War Bot...
"%PYTHON_EXE%" -u -m lastwar_bot --config config.yaml
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="1" (
    echo.
    echo Bot已有实例正在运行，按任意键退出。
    pause >nul
) else if not "%EXIT_CODE%"=="0" (
    echo.
    echo Last War Bot exited with code %EXIT_CODE%.
    pause
)

endlocal & exit /b %EXIT_CODE%
