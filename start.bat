@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

cd /d "%~dp0"
set "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True"

set "PYTHON_EXE="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if defined PYTHON_EXE (
    "%PYTHON_EXE%" -c "import sys" >nul 2>nul
    if errorlevel 1 set "PYTHON_EXE="
)
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYTHON_EXE if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PYTHON_EXE set "PYTHON_EXE=python"

for /f "usebackq delims=" %%A in (`"%PYTHON_EXE%" -c "import platform; print(platform.machine())" 2^>nul`) do set "PYTHON_ARCH=%%A"
if /i "%PYTHON_ARCH%"=="ARM64" (
    echo.
    echo Detected ARM64 Python: "%PYTHON_EXE%"
    echo LastWarBot currently requires x64 Python on Windows because PaddlePaddle does not provide Windows ARM64 wheels.
    echo Install an x64 Python 3.11/3.12/3.13 build or create a x64 .venv, then run this script again.
    pause
    endlocal & exit /b 2
)

echo Starting Last War Bot...
"%PYTHON_EXE%" -u -m lastwar_bot --config config.yaml
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
