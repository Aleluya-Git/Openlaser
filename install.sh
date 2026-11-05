#!/usr/bin/env bash
# OpenLaser Studio - instalacion del entorno (Linux / macOS)
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3.12}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
"$PY" -c 'import sys; assert sys.version_info >= (3, 12), "Se necesita Python 3.12+"'
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"
echo "Instalación completada. Ejecute ./autoprueba.sh y después ./run.sh"
