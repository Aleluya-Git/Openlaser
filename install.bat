@echo off
rem OpenLaser Studio - instalacion del entorno (Windows)
setlocal
cd /d "%~dp0"
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] No se encuentra Python 3.12 o superior. Instalelo desde https://www.python.org/downloads/
  pause
  exit /b 1
)
echo Usando: %PY%
if not exist .venv (
  %PY% -m venv .venv || goto :error
)
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
".venv\Scripts\python.exe" -m pip install -e ".[dev]" || goto :error
echo.
echo Instalacion completada. Ejecute autoprueba.bat y despues run.bat
pause
exit /b 0
:error
echo [ERROR] La instalacion ha fallado. Revise los mensajes anteriores.
pause
exit /b 1
