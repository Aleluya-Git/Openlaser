"""Escritura atómica de archivos: nunca deja un archivo a medias.

Se escribe en un temporal del mismo directorio, se fuerza a disco (fsync) y
se sustituye con ``os.replace`` (atómico en Windows, Linux y macOS). La
versión anterior se conserva como ``.bak``.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes, keep_backup: bool = True) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if keep_backup and target.exists():
            try:
                shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
            except OSError:
                pass
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def atomic_write_text(path: str | os.PathLike[str], text: str, keep_backup: bool = True) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"), keep_backup)


def atomic_write_json(path: str | os.PathLike[str], data: Any, keep_backup: bool = True) -> Path:
    return atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False), keep_backup)


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    """Lee JSON; si el archivo está dañado intenta la copia ``.bak``."""
    target = Path(path)
    for candidate in (target, target.with_suffix(target.suffix + ".bak")):
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
    return default
