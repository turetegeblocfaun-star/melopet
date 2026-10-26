@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 scripts\start-all.py
  goto done
)
where python >nul 2>nul
if %errorlevel% equ 0 (
  python scripts\start-all.py
  goto done
)
if /I "%PROCESSOR_ARCHITECTURE%"=="ARM64" (
  set "UV_EXE=%~dp0.bootstrap\uv\windows-arm64\uv.exe"
) else (
  set "UV_EXE=%~dp0.bootstrap\uv\windows-x64\uv.exe"
)
if not exist "%UV_EXE%" (
  echo [ERROR] Python and bundled uv bootstrap are both unavailable.
  pause
  exit /b 1
)
"%UV_EXE%" run --python 3.11 --no-project scripts\start-all.py
:done
if errorlevel 1 pause
