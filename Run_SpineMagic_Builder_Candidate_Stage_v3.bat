@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "SCRIPT=%SCRIPT_DIR%spine_magic_builder_candidate_materializer_v3.py"

set "TARGET=%~1"

if "%TARGET%"=="" (
  echo [ERR] No source folder was provided.
  echo [INFO] Drag a folder onto this BAT, or pass a folder path as argument 1.
  pause
  exit /b 1
)

if not exist "%SCRIPT%" (
  echo [ERR] Missing script: "%SCRIPT%"
  pause
  exit /b 1
)

where py.exe >nul 2>nul
if not errorlevel 1 (
  set "PYTHON=py.exe"
  set "PYTHON_ARGS=-3"
) else (
  where python.exe >nul 2>nul
  if errorlevel 1 (
    echo [ERR] Python 3 was not found on PATH.
    echo [INFO] Install Python 3.10 or newer from https://www.python.org/downloads/
    pause
    exit /b 1
  )
  set "PYTHON=python.exe"
  set "PYTHON_ARGS="
)

"%PYTHON%" %PYTHON_ARGS% "%SCRIPT%" ^
  --root "%TARGET%" ^
  --dims-fallback ^
  --min-hits 40 ^
  --prefer-nearby-textures ^
  --prefer-consistent-texture-dir ^
  --aggressive-atlas ^
  --rewrite-pages-to-match-source ^
  --entity-mode childdirs ^
  --link-mode symlink ^
  --stage-dim-candidates ^
  --stage-dim-candidates-limit 0

set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [INFO] Candidate builder exit code: %EXIT_CODE%
pause
exit /b %EXIT_CODE%
