@echo off
rem OpenLaser Studio - ejecutar la aplicacion
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ejecute primero install.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m openlaser %*
if errorlevel 1 pause
