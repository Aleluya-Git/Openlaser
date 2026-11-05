@echo off
rem OpenLaser Studio - genera el instalador de Windows
rem   1. pruebas  2. ejecutable (PyInstaller)  3. prueba del ejecutable  4. instalador (Inno Setup 6)
rem Resultado: dist\OpenLaserStudio-0.6.0-setup.exe  (registro en instalador_resultado.txt)
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
set "OUT=instalador_resultado.txt"
set "VER=0.6.0"
if not exist "%PY%" (
  echo Ejecute primero install.bat
  pause
  exit /b 1
)
echo OpenLaser Studio - instalador %DATE% %TIME% > "%OUT%"
"%PY%" -c "import sys, PySide6; print('Python', sys.version.split()[0], '- PySide6', PySide6.__version__)" >> "%OUT%" 2>&1

echo [1/4] Pruebas automaticas...
echo. >> "%OUT%"
echo === 1. Pruebas (pytest) === >> "%OUT%"
"%PY%" -m pytest -q >> "%OUT%" 2>&1
if errorlevel 1 (
  echo [ERROR] Hay pruebas fallidas: no se genera el instalador.
  echo RESULTADO: PRUEBAS FALLIDAS >> "%OUT%"
  goto :end_fail
)

echo [2/4] Generando el ejecutable con PyInstaller (puede tardar unos minutos)...
echo. >> "%OUT%"
echo === 2. PyInstaller === >> "%OUT%"
"%PY%" -m pip install "pyinstaller>=6.10" >> "%OUT%" 2>&1
if errorlevel 1 (
  echo [ERROR] No se pudo instalar PyInstaller.
  goto :end_fail
)
if exist "dist\OpenLaserStudio" rmdir /s /q "dist\OpenLaserStudio"
"%PY%" -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\openlaser.spec >> "%OUT%" 2>&1
if errorlevel 1 (
  echo [ERROR] PyInstaller ha fallado. Revise %OUT%.
  goto :end_fail
)
if not exist "dist\OpenLaserStudio\OpenLaserStudio.exe" (
  echo [ERROR] No se ha generado dist\OpenLaserStudio\OpenLaserStudio.exe
  goto :end_fail
)

echo [3/4] Probando el ejecutable con el simulador...
echo. >> "%OUT%"
echo === 3. Prueba del ejecutable === >> "%OUT%"
if exist "docs\captura_instalador.png" del "docs\captura_instalador.png"
start "" /wait "dist\OpenLaserStudio\OpenLaserStudio.exe" --screenshot "%CD%\docs\captura_instalador.png" --settings "%TEMP%\openlaser_instalador.json"
echo Codigo de salida del ejecutable: %ERRORLEVEL% >> "%OUT%"
if not exist "docs\captura_instalador.png" (
  echo [ERROR] El ejecutable no ha arrancado correctamente: no hay captura.
  echo El ejecutable no genero la captura >> "%OUT%"
  goto :end_fail
)
echo Captura del ejecutable en docs\captura_instalador.png >> "%OUT%"

echo [4/4] Creando el instalador con Inno Setup...
echo. >> "%OUT%"
echo === 4. Inno Setup === >> "%OUT%"
call :find_iscc
if not defined ISCC (
  echo Inno Setup 6 no esta instalado: se intenta instalar con winget...
  winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements >> "%OUT%" 2>&1
  call :find_iscc
)
if not defined ISCC (
  echo [AVISO] No se encuentra Inno Setup 6. Se genera una version portable en ZIP.
  echo Instale Inno Setup 6 desde https://jrsoftware.org/isdl.php y repita para obtener el instalador.
  powershell -NoProfile -Command "Compress-Archive -Path 'dist\OpenLaserStudio\*' -DestinationPath 'dist\OpenLaserStudio-%VER%-portable.zip' -Force" >> "%OUT%" 2>&1
  echo RESULTADO: ZIP PORTABLE dist\OpenLaserStudio-%VER%-portable.zip ^(sin Inno Setup^) >> "%OUT%"
  goto :end_ok
)
"%ISCC%" packaging\installer.iss >> "%OUT%" 2>&1
if errorlevel 1 (
  echo [ERROR] Inno Setup ha fallado. Revise %OUT%.
  goto :end_fail
)
echo RESULTADO: INSTALADOR dist\OpenLaserStudio-%VER%-setup.exe >> "%OUT%"
goto :end_ok

:find_iscc
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
exit /b 0

:end_ok
echo.
type "%OUT%" | findstr /b /c:"RESULTADO" /c:"Python" /c:"Captura"
echo.
echo Listo. Registro completo en %OUT%
pause
exit /b 0

:end_fail
echo RESULTADO: ERROR >> "%OUT%"
echo.
echo Ha habido un error. Registro completo en %OUT%
pause
exit /b 1
