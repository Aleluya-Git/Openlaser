"""Ajustes de una imagen ráster del documento (sin dependencias: se usan en el modelo)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Any

from openlaser.i18n import N_

# Métodos de tramado: clave → etiqueta (traducible). El orden es el del desplegable.
DITHER_METHODS: dict[str, str] = {
    "threshold": N_("Umbral"),
    "atkinson": N_("Atkinson"),
    "floyd": N_("Floyd-Steinberg"),
    "jarvis": N_("Jarvis-Judice-Ninke"),
    "stucki": N_("Stucki"),
    "sierra": N_("Sierra"),
    "burkes": N_("Burkes"),
    "bayer": N_("Bayer ordenado"),
    "halftone": N_("Halftone (puntos)"),
    "grayscale": N_("Escala de grises (potencia variable)"),
}


@dataclass(frozen=True)
class ImageSettings:
    """Ajustes no destructivos: el original se conserva y se procesa al mostrar o generar."""
    brightness: float = 0.0       # -100 … 100
    contrast: float = 0.0         # -100 … 100
    gamma: float = 1.0            # 0.2 … 5 (>1 aclara los tonos medios)
    exposure: float = 0.0         # -3 … 3 EV
    invert: bool = False
    background: int = 255         # niveles ≥ este valor pasan a blanco (255 = desactivado)
    sharpen: float = 0.0          # 0 … 3 (máscara de enfoque)
    blur: float = 0.0             # 0 … 10 px (sigma del desenfoque gaussiano)
    threshold: int = 128          # umbral del método «Umbral»
    method: str = "floyd"
    halftone_cell: int = 6        # tamaño de celda del halftone en píxeles de salida
    halftone_angle: float = 45.0
    lines_per_mm: float = 10.0    # resolución de salida (254 DPI)
    scan_angle: float = 0.0       # ángulo de las líneas de barrido respecto al eje X del documento (-90 … 90)
    crop: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)  # fracción izq., arriba, der., abajo

    @property
    def dpi(self) -> float:
        return self.lines_per_mm * 25.4

    @property
    def interval_mm(self) -> float:
        return 1.0 / max(self.lines_per_mm, 1e-6)

    def with_changes(self, **changes: Any) -> "ImageSettings":
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["crop"] = list(self.crop)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ImageSettings":
        if not d:
            return cls()
        names = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in names}
        if "crop" in data:
            c = list(data["crop"])[:4] + [0.0] * 4
            data["crop"] = tuple(float(v) for v in c[:4])
        if data.get("method") not in DITHER_METHODS:
            data.pop("method", None)
        return cls(**data)

    def clamped(self) -> "ImageSettings":
        def cl(v: float, lo: float, hi: float) -> float:
            return min(max(float(v), lo), hi)
        crop = tuple(cl(v, 0.0, 0.45) for v in self.crop)
        return replace(self, brightness=cl(self.brightness, -100, 100), contrast=cl(self.contrast, -100, 99),
                       gamma=cl(self.gamma, 0.2, 5.0), exposure=cl(self.exposure, -3, 3),
                       background=int(cl(self.background, 1, 255)), sharpen=cl(self.sharpen, 0, 3),
                       blur=cl(self.blur, 0, 10), threshold=int(cl(self.threshold, 0, 255)),
                       halftone_cell=int(cl(self.halftone_cell, 3, 40)),
                       halftone_angle=cl(self.halftone_angle, -90, 90),
                       scan_angle=cl(self.scan_angle, -90, 90), lines_per_mm=cl(self.lines_per_mm, 1, 40),
                       crop=crop)  # type: ignore[arg-type]
