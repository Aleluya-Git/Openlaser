"""Formato de proyecto nativo ``.olsp`` (ZIP con JSON y recursos).

Estructura::

    mimetype               application/x-openlaser-project (primero, sin comprimir)
    manifest.json          formato, versión, aplicación, fechas
    document.json          capas, formas (curvas), página, metadatos  (formato de documento 2)
    device_profile.json    perfil de dispositivo asociado (instantánea)
    layout.json            distribución de paneles de la ventana (opcional)
    resources/             imágenes incrustadas (Fase 3)

Se guarda de forma atómica (archivo temporal + ``os.replace`` + copia
``.bak``). Al abrir se validan versión y contenido y se migran los formatos
antiguos; también se aceptan los JSON de documento de la Fase 1.
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openlaser.app.config import APP_VERSION, app_name
from openlaser.document.model import FORMAT_VERSION, Document
from openlaser.persistence.atomic import atomic_write_bytes

MIMETYPE = "application/x-openlaser-project"
PROJECT_FORMAT = "openlaser-project"
PROJECT_VERSION = 1
EXTENSION = ".olsp"
MAX_MEMBER_BYTES = 200 * 1024 * 1024


class ProjectError(ValueError):
    pass


@dataclass
class ProjectData:
    document: Document
    device_profile: dict[str, Any] | None = None
    layout: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)
    resources: dict[str, bytes] = field(default_factory=dict)


def project_bytes(data: ProjectData) -> bytes:
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest = dict(data.manifest)
    manifest.update({"format": PROJECT_FORMAT, "version": PROJECT_VERSION, "document_format": FORMAT_VERSION,
                     "application": app_name(), "application_version": APP_VERSION, "modified": now})
    manifest.setdefault("created", now)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False),
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("document.json", json.dumps(data.document.to_dict(), ensure_ascii=False),
                    compress_type=zipfile.ZIP_DEFLATED)
        if data.device_profile is not None:
            zf.writestr("device_profile.json", json.dumps(data.device_profile, indent=2, ensure_ascii=False),
                        compress_type=zipfile.ZIP_DEFLATED)
        if data.layout:
            zf.writestr("layout.json", json.dumps(data.layout, indent=2), compress_type=zipfile.ZIP_DEFLATED)
        for name, blob in {**data.resources, **_image_resources(data.document)}.items():
            # Los .npz ya van comprimidos: se almacenan sin volver a comprimir.
            method = zipfile.ZIP_STORED if name.endswith(".npz") else zipfile.ZIP_DEFLATED
            zf.writestr(f"resources/{name}", blob, compress_type=method)
    data.manifest = manifest
    return buf.getvalue()


def _image_resources(doc: Document) -> dict[str, bytes]:
    used = doc.used_image_resources()
    if not used:
        return {}
    from openlaser.image_processing.raster import encode_source
    return {f"images/{rid}.npz": encode_source(doc.images[rid]) for rid in sorted(used) if rid in doc.images}


def _attach_images(doc: Document, resources: dict[str, bytes]) -> list[str]:
    """Decodifica ``images/*.npz``; devuelve los recursos que faltan o están dañados."""
    problems: list[str] = []
    used = doc.used_image_resources()
    if not used:
        return problems
    try:
        from openlaser.image_processing.raster import decode_source
    except ImportError:
        return sorted(used)
    for rid in sorted(used):
        blob = resources.get(f"images/{rid}.npz")
        if blob is None:
            problems.append(rid)
            continue
        try:
            doc.images[rid] = decode_source(blob)
        except (ValueError, OSError, KeyError):
            problems.append(rid)
    return problems


def save_project(path: str | os.PathLike[str], data: ProjectData) -> Path:
    return atomic_write_bytes(path, project_bytes(data))


def _read_json(zf: zipfile.ZipFile, name: str) -> Any:
    info = zf.getinfo(name)
    if info.file_size > MAX_MEMBER_BYTES:
        raise ProjectError(f"{name} demasiado grande")
    return json.loads(zf.read(name).decode("utf-8"))


def load_project(path: str | os.PathLike[str]) -> ProjectData:
    p = Path(path)
    if p.suffix.lower() == ".json":
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ProjectError(f"JSON no válido: {exc}") from exc
        raw.pop("_nota", None)
        return ProjectData(document=Document.from_dict(raw), manifest={"migrated_from": "json"})
    try:
        with zipfile.ZipFile(p) as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names or "document.json" not in names:
                raise ProjectError("Falta manifest.json o document.json")
            manifest = _read_json(zf, "manifest.json")
            if manifest.get("format") != PROJECT_FORMAT:
                raise ProjectError("No es un proyecto de OpenLaser Studio")
            if int(manifest.get("version", 0)) > PROJECT_VERSION:
                raise ProjectError("Proyecto creado con una versión más nueva de la aplicación")
            try:
                doc = Document.from_dict(_read_json(zf, "document.json"))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProjectError(f"Documento dañado: {exc}") from exc
            profile = _read_json(zf, "device_profile.json") if "device_profile.json" in names else None
            layout = _read_json(zf, "layout.json") if "layout.json" in names else {}
            resources = {}
            for n in names:
                if n.startswith("resources/") and not n.endswith("/"):
                    if zf.getinfo(n).file_size > MAX_MEMBER_BYTES:
                        raise ProjectError(f"{n} demasiado grande")
                    resources[n[len("resources/"):]] = zf.read(n)
    except zipfile.BadZipFile as exc:
        raise ProjectError(f"Archivo de proyecto dañado: {exc}") from exc
    missing = _attach_images(doc, resources)
    if missing:
        manifest["missing_images"] = missing
    resources = {k: v for k, v in resources.items() if not k.startswith("images/")}
    return ProjectData(document=doc, device_profile=profile, layout=layout, manifest=manifest, resources=resources)


def autosave_path(user_dir: Path) -> Path:
    d = user_dir / "autosave"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"autoguardado{EXTENSION}"


def autosave_info(path: Path) -> dict[str, Any] | None:
    """Datos del autoguardado pendiente de recuperar (o None)."""
    if not path.exists():
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = _read_json(zf, "manifest.json")
    except (zipfile.BadZipFile, KeyError, ValueError, OSError):
        return None
    manifest["path"] = str(path)
    return manifest
