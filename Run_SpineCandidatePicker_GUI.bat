@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%spine_candidate_picker_gui.py"
set "TARGET=%~1"

if not exist "%SCRIPT%" (
  echo [ERR] Missing script: "%SCRIPT%"
  pause
  exit /b 1
)

where pythonw.exe >nul 2>nul
if not errorlevel 1 (
  set "PYTHON=pythonw.exe"
  set "PYTHON_ARGS="
) else (
  where pyw.exe >nul 2>nul
  if errorlevel 1 (
    echo [ERR] Python 3 was not found on PATH.
    echo [INFO] Install Python 3.10 or newer from https://www.python.org/downloads/
    pause
    exit /b 1
  )
  set "PYTHON=pyw.exe"
  set "PYTHON_ARGS=-3"
)

if "%TARGET%"=="" (
  start "" "%PYTHON%" %PYTHON_ARGS% "%SCRIPT%"
) else (
  start "" "%PYTHON%" %PYTHON_ARGS% "%SCRIPT%" "%TARGET%"
)
