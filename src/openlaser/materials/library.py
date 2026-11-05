"""Biblioteca de materiales: combinaciones validadas por el usuario.

Ningún valor es universal: cada entrada indica la máquina y el módulo con que
se probó, la fecha de validación y el resultado. Las entradas de demostración
llevan ``demo = True`` y la interfaz las marca como ficticias.
"""
from __future__ import annotations

import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable

from openlaser.document.model import Layer, LayerMode
from openlaser.persistence.atomic import atomic_write_json, read_json

LIBRARY_VERSION = 1
OPERATIONS = ("cut", "line", "fill", "image")
RESULTS = ("", "ok", "regular", "fail")
_OP_MODE = {"cut": LayerMode.LINE, "line": LayerMode.LINE, "fill": LayerMode.FILL, "image": LayerMode.IMAGE}
# Claves del archivo de demostración de la Fase 1 (examples/materiales_DEMO.json)
_LEGACY = {"material": "material", "variante": "variant", "espesor_mm": "thickness_mm", "velocidad_mm_min": "speed_mm_min",
           "potencia_pct": "power_max_pct", "pasadas": "passes", "intervalo_mm": "interval_mm",
           "air_assist": "air_assist", "frecuencia": "frequency", "maquina": "machine", "modulo": "laser_module",
           "fecha_validacion": "validated_on", "resultado": "result", "notas": "notes", "foto": "photo"}
_LEGACY_OPS = {"corte": "cut", "grabado linea": "line", "grabado línea": "line", "relleno": "fill", "imagen": "image"}
_LEGACY_RESULTS = {"sin validar": "", "correcto": "ok", "bien": "ok", "regular": "regular", "mal": "fail"}


def _fold(text: str) -> str:
    """Minúsculas sin acentos (búsqueda «acril» encuentra «Acrílico»)."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


@dataclass
class MaterialEntry:
    material: str
    variant: str = ""
    thickness_mm: float = 0.0
    operation: str = "cut"
    speed_mm_min: float = 1000.0
    power_min_pct: float = 0.0
    power_max_pct: float = 50.0
    passes: int = 1
    interval_mm: float = 0.1
    air_assist: bool = False
    frequency: float = 0.0
    notes: str = ""
    machine: str = ""
    laser_module: str = ""
    validated_on: str = ""        # AAAA-MM-DD; vacío = sin validar
    result: str = ""              # "", ok, regular, fail
    photo: str = ""               # ruta de la fotografía (opcional)
    demo: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    @property
    def validated(self) -> bool:
        return bool(self.validated_on) and self.result == "ok" and not self.demo

    def title(self) -> str:
        parts = [self.material]
        if self.variant:
            parts.append(self.variant)
        if self.thickness_mm:
            parts.append(f"{self.thickness_mm:g} mm")
        return " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MaterialEntry":
        names = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in names}
        for k in ("thickness_mm", "speed_mm_min", "power_min_pct", "power_max_pct", "interval_mm", "frequency"):
            if data.get(k) is None:
                data.pop(k, None)
            elif k in data:
                data[k] = float(data[k])
        if "passes" in data:
            data["passes"] = max(1, int(data["passes"] or 1))
        for k in ("variant", "notes", "machine", "laser_module", "validated_on", "result", "photo"):
            if data.get(k) is None:
                data[k] = ""
        if data.get("operation") not in OPERATIONS:
            data["operation"] = "cut"
        if data.get("result") not in RESULTS:
            data["result"] = ""
        return cls(**data)

    # --- capas ----------------------------------------------------------------
    def apply_to_layer(self, layer: Layer) -> None:
        layer.mode = _OP_MODE[self.operation]
        layer.speed_mm_min = self.speed_mm_min
        layer.power_min_pct = min(self.power_min_pct, self.power_max_pct)
        layer.power_max_pct = self.power_max_pct
        layer.passes = self.passes
        layer.air_assist = self.air_assist
        layer.frequency = self.frequency
        if self.operation in ("fill", "image"):
            layer.line_interval_mm = max(0.01, self.interval_mm)
        note = f"Material: {self.title()}"
        if note not in layer.notes:
            layer.notes = (layer.notes + "\n" + note).strip()

    @classmethod
    def from_layer(cls, layer: Layer, material: str, **kw: Any) -> "MaterialEntry":
        op = {LayerMode.LINE: "line", LayerMode.FILL: "fill", LayerMode.FILL_LINE: "fill",
              LayerMode.IMAGE: "image"}[layer.mode]
        return cls(material=material, operation=kw.pop("operation", op), speed_mm_min=layer.speed_mm_min,
                   power_min_pct=layer.power_min_pct, power_max_pct=layer.power_max_pct, passes=layer.passes,
                   interval_mm=layer.line_interval_mm, air_assist=layer.air_assist, frequency=layer.frequency, **kw)


def _from_legacy(d: dict[str, Any], demo: bool) -> MaterialEntry:
    data: dict[str, Any] = {}
    for k, v in d.items():
        if k in _LEGACY:
            data[_LEGACY[k]] = v
    op = str(d.get("operacion", "")).strip().lower()
    data["operation"] = _LEGACY_OPS.get(op, "cut")
    data["result"] = _LEGACY_RESULTS.get(str(d.get("resultado") or "").strip().lower(), "")
    data["demo"] = demo
    return MaterialEntry.from_dict(data)


@dataclass
class MaterialLibrary:
    entries: list[MaterialEntry] = field(default_factory=list)
    path: Path | None = None

    # --- consulta ---------------------------------------------------------------
    def get(self, entry_id: str) -> MaterialEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def materials(self) -> list[str]:
        return sorted({e.material for e in self.entries}, key=str.lower)

    def search(self, text: str = "", operation: str = "", machine: str = "",
               only_validated: bool = False) -> list[MaterialEntry]:
        t = _fold(text.strip())
        out = []
        for e in self.entries:
            hay = _fold(" ".join((e.material, e.variant, e.notes, e.machine, e.laser_module, f"{e.thickness_mm:g}")))
            if t and not all(w in hay for w in t.split()):
                continue
            if operation and e.operation != operation:
                continue
            if machine and e.machine and e.machine != machine:
                continue
            if only_validated and not e.validated:
                continue
            out.append(e)
        return sorted(out, key=lambda e: (e.material.lower(), e.thickness_mm, e.operation))

    # --- edición ------------------------------------------------------------------
    def add(self, entry: MaterialEntry) -> MaterialEntry:
        if self.get(entry.id) is not None:
            entry.id = uuid.uuid4().hex[:10]
        self.entries.append(entry)
        return entry

    def update(self, entry: MaterialEntry) -> None:
        self.entries = [entry if e.id == entry.id else e for e in self.entries]

    def remove(self, entry_id: str) -> None:
        self.entries = [e for e in self.entries if e.id != entry_id]

    def duplicate(self, entry_id: str) -> MaterialEntry | None:
        src = self.get(entry_id)
        if src is None:
            return None
        copy = MaterialEntry.from_dict({**src.to_dict(), "id": uuid.uuid4().hex[:10], "validated_on": "",
                                        "result": "", "demo": False})
        return self.add(copy)

    def mark_validated(self, entry_id: str, result: str = "ok", date: str | None = None) -> None:
        e = self.get(entry_id)
        if e is not None:
            e.result = result if result in RESULTS else ""
            e.validated_on = date or time.strftime("%Y-%m-%d")
            if result == "ok":
                e.demo = False

    # --- persistencia -------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"format": "openlaser-materials", "version": LIBRARY_VERSION,
                "entries": [e.to_dict() for e in self.entries]}

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path or self.path or "materiales.json")
        atomic_write_json(target, self.to_dict())
        if path is None or self.path is None:
            self.path = target
        return target

    @classmethod
    def from_data(cls, data: Any) -> "MaterialLibrary":
        if not isinstance(data, dict):
            raise ValueError("Biblioteca de materiales no válida")
        if data.get("format") == "openlaser-materials":
            if int(data.get("version", 0)) > LIBRARY_VERSION:
                raise ValueError("Biblioteca creada con una versión más nueva")
            return cls([MaterialEntry.from_dict(e) for e in data.get("entries", []) if isinstance(e, dict)])
        if isinstance(data.get("materiales"), list):  # formato de demostración de la Fase 1
            demo = bool(data.get("demo", False))
            return cls([_from_legacy(e, demo) for e in data["materiales"] if isinstance(e, dict)])
        raise ValueError("Biblioteca de materiales no válida")

    @classmethod
    def load(cls, path: str | Path) -> "MaterialLibrary":
        data = read_json(path, None)
        if data is None:
            raise ValueError(f"No se pudo leer {path}")
        lib = cls.from_data(data)
        lib.path = Path(path)
        return lib

    def merge(self, other: Iterable[MaterialEntry]) -> int:
        """Importa entradas nuevas (por id); devuelve cuántas se añadieron."""
        have = {e.id for e in self.entries}
        n = 0
        for e in other:
            if e.id not in have:
                self.entries.append(e)
                n += 1
        return n


def demo_library() -> MaterialLibrary:
    """Entradas ficticias de demostración (claramente marcadas)."""
    m, mod = "Simulador GRBL (demo)", "diodo 10 W (ficticio)"
    entries = [
        MaterialEntry("Contrachapado DEMO", "abedul", 3, "cut", 300, 0, 90, 2, air_assist=True, machine=m,
                      laser_module=mod, notes="Valor inventado para la demostración", demo=True, id="demo-ply-cut"),
        MaterialEntry("MDF DEMO", "crudo", 3, "line", 2000, 0, 30, 1, air_assist=True, machine=m, laser_module=mod,
                      notes="Valor inventado para la demostración", demo=True, id="demo-mdf-line"),
        MaterialEntry("Cartón DEMO", "gris", 1.5, "fill", 3000, 0, 20, 1, 0.1, machine=m, laser_module=mod,
                      notes="Valor inventado para la demostración", demo=True, id="demo-card-fill"),
        MaterialEntry("Madera DEMO", "pino", 4, "image", 3000, 5, 35, 1, 0.1, machine=m, laser_module=mod,
                      notes="Valor inventado para la demostración", demo=True, id="demo-pine-image"),
    ]
    return MaterialLibrary(entries)


def load_or_create(path: Path) -> MaterialLibrary:
    """Biblioteca del usuario; la primera vez se crea con las entradas de demostración."""
    if path.exists():
        try:
            return MaterialLibrary.load(path)
        except (ValueError, OSError):
            backup = path.with_suffix(path.suffix + ".bak")
            if backup.exists():
                lib = MaterialLibrary.load(backup)
                lib.path = path
                return lib
            raise
    lib = demo_library()
    lib.path = path
    lib.save()
    return lib
