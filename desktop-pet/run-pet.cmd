@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3.11 bootstrap.py
) else (
  python bootstrap.py
)
if errorlevel 1 exit /b 1

set "PET_PY=%~dp0..\.validation\desktop-pet-venv\Scripts\pythonw.exe"
if not exist "%PET_PY%" set "PET_PY=%~dp0..\.validation\desktop-pet-venv\Scripts\python.exe"
start "" "%PET_PY%" "%~dp0pet.py"
exit /b 0
