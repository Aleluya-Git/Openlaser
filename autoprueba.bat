@echo off
rem OpenLaser Studio - pruebas automaticas + captura de la interfaz con el simulador
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ejecute primero install.bat
  pause
  exit /b 1
)
set "OUT=autoprueba_resultado.txt"
echo OpenLaser Studio - autoprueba %DATE% %TIME% > "%OUT%"
rem Dependencias nuevas de una fase (p. ej. NumPy en la Fase 3): se instalan si faltan
".venv\Scripts\python.exe" -c "import numpy" 1>nul 2>nul || (
  echo Instalando dependencias nuevas...
  ".venv\Scripts\python.exe" -m pip install -e ".[dev]" >> "%OUT%" 2>&1
)
".venv\Scripts\python.exe" -c "import sys, PySide6, numpy; print('Python', sys.version.split()[0], '- PySide6', PySide6.__version__, '- NumPy', numpy.__version__)" >> "%OUT%" 2>&1
echo. >> "%OUT%"
echo === Pruebas automaticas (pytest) === >> "%OUT%"
".venv\Scripts\python.exe" -m pytest -q >> "%OUT%" 2>&1
set "RC=%ERRORLEVEL%"
echo Codigo de salida de pytest: %RC% >> "%OUT%"
echo. >> "%OUT%"
echo === Captura de la interfaz con el simulador === >> "%OUT%"
".venv\Scripts\python.exe" -m openlaser --screenshot "docs\captura_fase6.png" --settings "%TEMP%\openlaser_autoprueba.json" >> "%OUT%" 2>&1
echo Codigo de salida de la aplicacion: %ERRORLEVEL% >> "%OUT%"
echo. >> "%OUT%"
if "%RC%"=="0" (echo RESULTADO: TODAS LAS PRUEBAS SUPERADAS >> "%OUT%") else (echo RESULTADO: HAY PRUEBAS FALLIDAS >> "%OUT%")
type "%OUT%"
echo.
echo Resultado guardado en %OUT% y captura en docs\captura_fase6.png
pause
exit /b %RC%
