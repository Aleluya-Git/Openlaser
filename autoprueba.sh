#!/usr/bin/env bash
# OpenLaser Studio - pruebas automáticas + captura de la interfaz
set -uo pipefail
cd "$(dirname "$0")"
OUT=autoprueba_resultado.txt
{
  echo "OpenLaser Studio - autoprueba $(date)"
  .venv/bin/python -c "import numpy" 2>/dev/null || .venv/bin/python -m pip install -e ".[dev]"
  .venv/bin/python -c "import sys, PySide6, numpy; print('Python', sys.version.split()[0], '- PySide6', PySide6.__version__, '- NumPy', numpy.__version__)"
  echo; echo "=== Pruebas automáticas (pytest) ==="
  .venv/bin/python -m pytest -q; RC=$?
  echo; echo "=== Captura de la interfaz con el simulador ==="
  .venv/bin/python -m openlaser --screenshot docs/captura_fase6.png --settings "${TMPDIR:-/tmp}/openlaser_autoprueba.json"
  echo
  if [ "$RC" = 0 ]; then echo "RESULTADO: TODAS LAS PRUEBAS SUPERADAS"; else echo "RESULTADO: HAY PRUEBAS FALLIDAS"; fi
  exit $RC
} 2>&1 | tee "$OUT"
