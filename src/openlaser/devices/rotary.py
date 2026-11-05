"""Rotary (rodillo o mandril) y corrección cilíndrica.

El diseño se hace siempre en milímetros de *superficie* del objeto. Al generar
el trabajo, el eje que mueve el rotary (Y o A) recibe un factor de escala:

* **Mandril**: una vuelta del mandril = una vuelta del objeto ⇒ una vuelta
  recorre π·D_objeto de superficie.
* **Rodillo**: el objeto apoya sobre el rodillo; la superficie avanza lo mismo
  que la del rodillo ⇒ una vuelta del rodillo recorre π·D_rodillo (el diámetro
  del objeto no influye).

``mm_per_rev`` son las unidades de máquina que da el eje para una vuelta del
*motor*; la relación de transmisión ``gear_ratio`` = vueltas de motor por vuelta
de rodillo/mandril. Si el controlador está configurado en pasos/vuelta, se
calcula ``mm_per_rev = steps_per_rev / steps_per_mm``.

La **corrección cilíndrica** (sin rotary) sirve para grabar un cilindro tumbado
en una máquina plana: la coordenada de diseño a lo largo del arco se proyecta
sobre el plano con ``y = yc + R·sin(s/R)``.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, fields
from typing import Any
from openlaser.i18n import N_

_WORD = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")


@dataclass
class RotaryConfig:
    enabled: bool = False
    kind: str = "roller"             # "roller" | "chuck"
    axis: str = "Y"                  # eje que mueve el rotary: "Y" (sustituye a Y) o "A"
    object_diameter_mm: float = 60.0
    roller_diameter_mm: float = 30.0
    mm_per_rev: float = 360.0        # unidades de máquina por vuelta del motor
    steps_per_rev: float = 0.0       # alternativo: pasos por vuelta (0 = no usar)
    steps_per_mm: float = 0.0        # pasos/mm configurados en el eje ($101 o $103)
    gear_ratio: float = 1.0          # vueltas de motor por vuelta de rodillo/mandril
    invert: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "RotaryConfig":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def effective_mm_per_rev(self) -> float:
        if self.steps_per_rev > 0 and self.steps_per_mm > 0:
            return self.steps_per_rev / self.steps_per_mm
        return self.mm_per_rev

    def driven_diameter(self) -> float:
        return self.roller_diameter_mm if self.kind == "roller" else self.object_diameter_mm

    def object_circumference(self) -> float:
        return math.pi * self.object_diameter_mm

    def factor(self) -> float:
        """Unidades de máquina por mm de superficie (con signo si se invierte)."""
        d = self.driven_diameter()
        if d <= 0 or self.gear_ratio <= 0 or self.effective_mm_per_rev() <= 0:
            raise ValueError(N_("Configuración rotativa no válida: diámetro, relación y mm por vuelta deben ser > 0"))
        f = self.effective_mm_per_rev() * self.gear_ratio / (math.pi * d)
        return -f if self.invert else f

    def validate(self) -> list[str]:
        problems = []
        if self.kind not in ("roller", "chuck"):
            problems.append(N_("tipo"))
        if self.axis not in ("Y", "A"):
            problems.append(N_("eje"))
        try:
            self.factor()
        except ValueError as exc:
            problems.append(str(exc))
        return problems


@dataclass
class CylinderCorrection:
    enabled: bool = False
    diameter_mm: float = 80.0
    axis_y_mm: float = 0.0        # posición (trabajo) del eje del cilindro en Y

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "CylinderCorrection":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})

    def map_y(self, s: float) -> float:
        """Arco desde el eje (mm de superficie) → Y plana. Fuera de ±75° se avisa al validar."""
        r = self.diameter_mm / 2
        return self.axis_y_mm + r * math.sin((s - self.axis_y_mm) / r)

    def max_arc(self) -> float:
        return self.diameter_mm / 2 * math.radians(75)


def _fmt(v: float) -> str:
    t = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "", "-") else t


def _rewrite(words: list[tuple[str, str]], updates: dict[str, float], drop: set[str]) -> str:
    out = []
    done = set()
    for k, v in words:
        if k in drop:
            continue
        if k in updates:
            out.append(f"{k}{_fmt(updates[k])}")
            done.add(k)
        else:
            out.append(k + v)
    for k, v in updates.items():
        if k not in done:
            out.append(f"{k}{_fmt(v)}")
    return "".join(out)


def apply_rotary(lines: list[str], cfg: RotaryConfig) -> tuple[list[str], list[str]]:
    """Escala Y (o la pasa al eje A) en un trabajo absoluto. Devuelve (líneas, avisos).

    Los arcos con J no se pueden escalar de forma no uniforme: se rechazan.
    """
    if not cfg.enabled:
        return list(lines), []
    f = cfg.factor()
    out: list[str] = []
    warnings: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith((";", "(", "$")):
            out.append(raw)
            continue
        words = _WORD.findall(line.upper().replace(" ", ""))
        keys = {k for k, _ in words}
        gvals = {float(v) for k, v in words if k == "G"}
        if gvals & {91.0, 20.0}:
            raise ValueError(N_("El rotary requiere coordenadas absolutas en mm (G90 G21)"))
        if "J" in keys or "R" in keys or gvals & {2.0, 3.0}:
            raise ValueError(N_("El rotary no admite arcos G2/G3: convierta las curvas a segmentos"))
        if "Y" not in keys:
            out.append(line)
            continue
        y = float(next(v for k, v in words if k == "Y"))
        if cfg.axis == "Y":
            out.append(_rewrite(words, {"Y": y * f}, set()))
        else:
            out.append(_rewrite(words, {"A": y * f}, {"Y"}))
    if cfg.axis == "A":
        warnings.append(N_("Eje A: el movimiento en Y del diseño se envía al eje A; Y no se mueve"))
    return out, warnings


def apply_cylinder(lines: list[str], cc: CylinderCorrection, step_mm: float = 0.5) -> tuple[list[str], list[str]]:
    """Proyección cilíndrica de Y con subdivisión de los G1 (la función no es lineal)."""
    if not cc.enabled:
        return list(lines), []
    warnings: list[str] = []
    out: list[str] = []
    x = y = 0.0
    motion = 0
    limit = cc.max_arc()
    exceeded = False
    for raw in lines:
        line = raw.strip().upper().replace(" ", "")
        if not line or line.startswith((";", "(", "$")):
            out.append(raw)
            continue
        words = _WORD.findall(line)
        gvals = {float(v) for k, v in words if k == "G"}
        if gvals & {2.0, 3.0}:
            raise ValueError(N_("La corrección cilíndrica no admite arcos G2/G3"))
        if gvals & {91.0, 20.0}:
            raise ValueError(N_("La corrección cilíndrica requiere coordenadas absolutas en mm (G90 G21)"))
        for k, v in words:
            if k == "G" and float(v) in (0, 1):
                motion = int(float(v))
        vals = {k: float(v) for k, v in words if k in "XY"}
        if "Y" not in vals and "X" not in vals:
            out.append(raw.strip())
            continue
        nx, ny = vals.get("X", x), vals.get("Y", y)
        if abs(ny - cc.axis_y_mm) > limit:
            exceeded = True
        n = 1 if motion == 0 else max(1, int(math.ceil(abs(ny - y) / step_mm)))
        for i in range(1, n + 1):
            px = x + (nx - x) * i / n
            py = y + (ny - y) * i / n
            upd = {"X": px, "Y": cc.map_y(py)}
            if i == 1:  # F, S y demás palabras en el primer tramo: valen para todo el movimiento
                out.append(_rewrite(words, upd, set()))
            else:
                out.append(f"G{motion}X{_fmt(upd['X'])}Y{_fmt(upd['Y'])}")
        x, y = nx, ny
    if exceeded:
        warnings.append(N_("Parte del diseño supera ±75° del eje del cilindro: la distorsión será grande"))
    return out, warnings


def transform_active(profile: object) -> bool:
    """True si el rotary o la corrección cilíndrica cambian el trabajo enviado."""
    return RotaryConfig.from_dict(getattr(profile, "rotary", None)).enabled or \
        CylinderCorrection.from_dict(getattr(profile, "cylinder", None)).enabled


def machine_transform(lines: list[str], profile: object) -> tuple[list[str], list[str]]:
    """Aplica al trabajo generado el rotary o la corrección cilíndrica del perfil (si están activos)."""
    rot = RotaryConfig.from_dict(getattr(profile, "rotary", None))
    cyl = CylinderCorrection.from_dict(getattr(profile, "cylinder", None))
    if rot.enabled and cyl.enabled:
        raise ValueError(N_("El rotary y la corrección cilíndrica no pueden estar activos a la vez"))
    if rot.enabled:
        if rot.axis == "A" and not getattr(profile, "a_axis", False):
            raise ValueError(N_("El rotary usa el eje A pero el perfil no tiene eje A"))
        return apply_rotary(lines, rot)
    if cyl.enabled:
        return apply_cylinder(lines, cyl)
    return list(lines), []
