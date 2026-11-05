#!/usr/bin/env bash
# Genera un ejecutable autónomo con PyInstaller (Linux / macOS)
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pip install "pyinstaller>=6"
.venv/bin/python -m PyInstaller --noconfirm --windowed --name OpenLaserStudio --paths src \
  --collect-submodules openlaser src/openlaser/__main__.py
echo "Ejecutable en dist/OpenLaserStudio/"
