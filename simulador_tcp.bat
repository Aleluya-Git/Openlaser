@echo off
rem Arranca el simulador GRBL por TCP en 127.0.0.1:2323 (perfil de conexion TCP en la aplicacion)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m openlaser.simulator.tcp_server --port 2323
pause
