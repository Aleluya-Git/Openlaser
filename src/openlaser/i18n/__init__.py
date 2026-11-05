"""Internacionalización sin dependencias, con idiomas en archivos JSON editables.

Las cadenas fuente del código están en **español**. Cada idioma es un archivo
``<código>.json`` con esta forma::

    {
      "language": "Français",          nombre que aparece en Preferencias
      "code": "fr",                    código del idioma (2–5 letras)
      "author": "…",                   quién lo ha traducido (opcional)
      "strings": {                     frase española → traducción
        "Conectar": "Connecter",
        "Mover {a}": "Déplacer {a}",   las {marcas} deben conservarse
        …
      }
    }

Se buscan en dos sitios (el segundo tiene prioridad si repite el código):

* ``openlaser/i18n/locales/`` — idiomas incluidos (inglés, francés, alemán).
* ``<carpeta de usuario>/idiomas/`` — idiomas añadidos por el usuario, sin tocar el programa.

Para crear un idioma nuevo se copia la plantilla ``locales/plantilla_idioma.json`` (todas las frases
del programa, con el texto inglés de referencia; se regenera con ``tools/extract_strings.py --template``),
se cambian ``language`` y ``code`` y se traducen los valores. Las frases que falten se muestran en
inglés y, si tampoco están, en español.

Las tablas que ya incluyen varios idiomas (alarmas, parámetros GRBL) usan :func:`pick`: toma la entrada
del idioma activo si existe y, si no, busca la frase española en el archivo del idioma.
"""
from __future__ import annotations

import json
import re
import logging
from pathlib import Path
from typing import Mapping

_log = logging.getLogger(__name__)

SOURCE_LANGUAGE = "es"
SOURCE_NAME = "Español"
FALLBACK_LANGUAGE = "en"
LOCALES_DIR = Path(__file__).with_name("locales")
TEMPLATE_NAME = "plantilla_idioma.json"

_current = SOURCE_LANGUAGE
_catalog: dict[str, str] = {}


def user_locales_dir(create: bool = False) -> Path:
    """Carpeta de idiomas del usuario (``…/OpenLaserStudio/idiomas``)."""
    from openlaser.app.config import user_dir
    path = user_dir() / "idiomas"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _language_files() -> dict[str, Path]:
    """Código → archivo; los del usuario sustituyen a los incluidos con el mismo código."""
    files: dict[str, Path] = {}
    dirs = [LOCALES_DIR]
    try:
        dirs.append(user_locales_dir())
    except OSError:
        pass
    for folder in dirs:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if path.name == TEMPLATE_NAME:
                continue
            try:
                data = load_language_file(path)
            except (OSError, ValueError) as exc:
                _log.warning("Archivo de idioma no válido %s: %s", path, exc)
                continue
            files[data["code"]] = path
    return files


def load_language_file(path: Path) -> dict:
    """Lee y valida un archivo de idioma. Lanza ``ValueError`` si el formato no es correcto."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("strings"), dict):
        raise ValueError("falta el objeto «strings»")
    code = str(data.get("code") or Path(path).stem).strip().lower()
    if not code or code == SOURCE_LANGUAGE or len(code) > 10:
        raise ValueError(f"código de idioma no válido: {code!r}")
    data["code"] = code
    data["language"] = str(data.get("language") or code)
    data["strings"] = {str(k): str(v) for k, v in data["strings"].items() if isinstance(v, str) and v.strip()}
    return data


def available_languages() -> dict[str, str]:
    """Idiomas disponibles: código → nombre (el español, idioma fuente, siempre)."""
    out = {SOURCE_LANGUAGE: SOURCE_NAME}
    for code, path in _language_files().items():
        try:
            out[code] = load_language_file(path)["language"]
        except (OSError, ValueError):
            continue
    return out


_CODE_RE = re.compile(r"^[a-z]{2,3}([-_][a-z0-9]{2,4})?$")


def create_language_file(code: str, folder: Path | None = None) -> Path:
    """Crea ``<carpeta de idiomas del usuario>/<código>.json`` a partir de la plantilla.

    El archivo trae todas las frases del programa con el texto inglés como punto de partida.
    Lanza ``ValueError`` si el código no es válido o ya existe un archivo con ese nombre.
    """
    code = (code or "").strip().lower()
    if not _CODE_RE.match(code) or code == SOURCE_LANGUAGE:
        raise ValueError(f"Código de idioma no válido: {code!r} (use 2–3 letras, p. ej. «it» o «pt-br»)")
    folder = Path(folder) if folder is not None else user_locales_dir(create=True)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{code}.json"
    if target.exists():
        raise ValueError(f"Ya existe {target}")
    template = json.loads((LOCALES_DIR / TEMPLATE_NAME).read_text(encoding="utf-8"))
    template["language"] = code
    template["code"] = code
    target.write_text(json.dumps(template, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


def _strings(code: str) -> dict[str, str]:
    path = _language_files().get(code)
    if path is None:
        raise LookupError(code)
    return load_language_file(path)["strings"]


def set_language(code: str) -> str:
    """Activa un idioma. Devuelve el código efectivamente activado."""
    global _current, _catalog
    code = (code or SOURCE_LANGUAGE).strip().lower()
    if code == SOURCE_LANGUAGE:
        _current, _catalog = code, {}
        return code
    try:
        chosen = _strings(code)
    except (LookupError, OSError, ValueError):
        _log.warning("Idioma no disponible: %s; se usa español", code)
        _current, _catalog = SOURCE_LANGUAGE, {}
        return _current
    catalog: dict[str, str] = {}
    if code != FALLBACK_LANGUAGE:
        try:
            catalog.update(_strings(FALLBACK_LANGUAGE))  # lo que falte, en inglés
        except (LookupError, OSError, ValueError):
            pass
    catalog.update(chosen)
    _current, _catalog = code, catalog
    return code


def language() -> str:
    return _current


def tr(text: str, **kwargs: object) -> str:
    """Traduce ``text`` y aplica ``str.format(**kwargs)`` si se indican."""
    translated = _catalog.get(text, text) if _catalog else text
    if kwargs:
        try:
            return translated.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text.format(**kwargs)
    return translated


def N_(text: str) -> str:
    """Marca una cadena para traducción sin traducirla aún (se traduce al mostrarla)."""
    return text


def pick(entry: Mapping[str, str]) -> str:
    """Texto del idioma activo de un diccionario ``{"es": …, "en": …}``.

    Si el diccionario no trae el idioma activo, se traduce la frase española con el archivo del idioma;
    si tampoco está, se usa la inglesa y, por último, la española.
    """
    if _current in entry and entry[_current]:
        return entry[_current]
    es = entry.get(SOURCE_LANGUAGE, "")
    if _current != SOURCE_LANGUAGE:
        if es and es in _catalog:
            return _catalog[es]
        if entry.get(FALLBACK_LANGUAGE):
            return entry[FALLBACK_LANGUAGE]
    return es or next(iter(entry.values()), "")


def __getattr__(name: str) -> object:
    # Compatibilidad: SUPPORTED_LANGUAGES era un diccionario fijo; ahora se calcula con los archivos.
    if name == "SUPPORTED_LANGUAGES":
        return available_languages()
    raise AttributeError(name)
