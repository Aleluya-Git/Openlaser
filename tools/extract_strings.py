"""Frases traducibles del programa y archivos de idioma.

Uso:
  python tools/extract_strings.py                  lista las frases (tr/N_ con literal)
  python tools/extract_strings.py --missing [xx]   frases sin traducir en el idioma xx (por defecto: todos)
  python tools/extract_strings.py --template       regenera src/openlaser/i18n/locales/plantilla_idioma.json
  python tools/extract_strings.py --update xx      añade al archivo del idioma xx las frases nuevas (vacías)

Las frases son las del código (``tr("…")``/``N_("…")``) más las de las tablas bilingües (alarmas,
errores y parámetros GRBL, alarmas de grblHAL/FluidNC), que se traducen igual a otros idiomas.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "openlaser"
LOCALES = ROOT / "i18n" / "locales"
TEMPLATE = LOCALES / "plantilla_idioma.json"

INSTRUCTIONS = (
    "Plantilla para un idioma nuevo de OpenLaser Studio. 1) Copie este archivo con el nombre del código "
    "del idioma (p. ej. it.json). 2) Cambie 'language' (nombre que verá el usuario, p. ej. 'Italiano') y "
    "'code' (p. ej. 'it'). 3) Sustituya cada valor de 'strings' por su traducción: la clave (izquierda) es la "
    "frase original en español y NO se cambia; el valor trae el texto inglés como referencia. Conserve las "
    "marcas entre llaves ({n}, {f:.1f}…), los '&' de los menús y los saltos de línea. 4) Copie el archivo en "
    "la carpeta de idiomas del usuario (Preferencias > Idioma > Abrir carpeta de idiomas) y elija el idioma "
    "en Preferencias. Las frases que deje vacías o borre se mostrarán en inglés."
)


def extract() -> tuple[set[str], list[tuple[str, int]]]:
    found: set[str] = set()
    dynamic: list[tuple[str, int]] = []
    for path in ROOT.rglob("*.py"):
        if "i18n" in path.parts and path.name != "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("tr", "N_"):
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    found.add(node.args[0].value)
                elif node.args:
                    dynamic.append((str(path.relative_to(ROOT)), node.lineno))
    return found, dynamic


def table_strings() -> dict[str, str]:
    """Frases de las tablas bilingües: español → inglés (el inglés ya viene en la propia tabla)."""
    sys.path.insert(0, str(ROOT.parent))
    from openlaser.machine import dialects
    from openlaser.machine.grbl_codes import ALARMS, ERRORS
    from openlaser.machine.grbl_settings import SETTINGS
    out: dict[str, str] = {}
    for table in (ALARMS, ERRORS):
        for info in table.values():
            for d in (info.description, info.cause, info.action):
                out[d["es"]] = d["en"]
    for s in SETTINGS.values():
        for d in (s.name, s.help):
            out[d["es"]] = d["en"]
    for dia in dialects.DIALECTS.values():
        for a in dia.alarms.values():
            for k in (0, 2, 4):
                out[a[k]] = a[k + 1]
    return out


def all_strings() -> dict[str, str]:
    """Todas las frases del programa: español → referencia inglesa ("" si aún no hay)."""
    code, _ = extract()
    en = load(LOCALES / "en.json")["strings"]
    out = {s: en.get(s, "") for s in sorted(code)}
    for es, eng in sorted(table_strings().items()):
        out.setdefault(es, eng)
    return out


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def write_template() -> Path:
    save(TEMPLATE, {"_instrucciones": INSTRUCTIONS, "language": "", "code": "", "author": "",
                    "strings": all_strings()})
    return TEMPLATE


def missing(code: str) -> list[str]:
    strings = load(LOCALES / f"{code}.json")["strings"]
    needed = set(extract()[0]) if code == "en" else set(all_strings())  # el inglés de las tablas va en ellas
    return sorted(s for s in needed if not strings.get(s, "").strip())


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--missing"]:
        codes = args[1:] or sorted(p.stem for p in LOCALES.glob("*.json") if p.name != TEMPLATE.name)
        total = 0
        for c in codes:
            miss = missing(c)
            total += len(miss)
            for s in miss:
                print(f"{c}: {s!r}")
            print(f"# {c}: faltan {len(miss)}", file=sys.stderr)
        print(f"# faltan {total} de {len(all_strings())}", file=sys.stderr)
    elif args[:1] == ["--template"]:
        print(f"Plantilla escrita en {write_template()}")
    elif args[:1] == ["--update"] and len(args) > 1:
        path = LOCALES / f"{args[1]}.json"
        data = load(path)
        added = [s for s in all_strings() if s not in data["strings"]]
        data["strings"].update({s: "" for s in added})
        save(path, data)
        print(f"{len(added)} frases nuevas añadidas a {path}")
    elif args[:1] == ["--dynamic"]:
        for f, ln in extract()[1]:
            print(f"{f}:{ln}")
    else:
        strings, _ = extract()
        for s in sorted(strings):
            print(repr(s))
        print(f"# {len(strings)} cadenas", file=sys.stderr)
