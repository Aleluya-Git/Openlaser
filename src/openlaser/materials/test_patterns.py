"""Generadores de pruebas: material, enfoque, intervalo, escala, precisión, círculos,
backlash, kerf, corte, grabado, potencia mínima y rotary.

Cada prueba produce geometría **editable** (capas y formas normales del
documento) con etiquetas en una capa propia. Los valores se limitan al perfil
(potencia 0–100 %, avance ≤ máximo, Z dentro del rango) y se informa de cada
ajuste. La prueba no se ejecuta sola: pasa por la vista previa y la
comprobación previa como cualquier trabajo.
"""
from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from openlaser.devices.profile import DeviceProfile
from openlaser.document.model import Document, Layer, LayerMode, Shape, _uid, make_shape
from openlaser.geometry import path as gp
from openlaser.geometry.stroke_font import text_subpaths
from openlaser.i18n import N_, tr

Point = tuple[float, float]


@dataclass
class Param:
    key: str
    label: str
    kind: str                    # "float" | "int" | "bool" | "choice" | "text" | "list"
    default: Any
    lo: float = 0.0
    hi: float = 1e6
    choices: tuple[tuple[str, str], ...] = ()
    suffix: str = ""


@dataclass
class TestPlan:
    title: str
    layers: list[Layer] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    size: tuple[float, float] = (0.0, 0.0)

    def summary(self) -> str:
        out = [tr("{t}: {l} capas, {s} objetos, {w:.0f} × {h:.0f} mm", t=self.title, l=len(self.layers),
                  s=len(self.shapes), w=self.size[0], h=self.size[1])]
        return "\n".join(out + ["• " + n for n in self.notes])


@dataclass
class TestKind:
    id: str
    title: str
    description: str
    params: list[Param]
    build: Callable[["_Builder", dict[str, Any]], None]


def _color(i: int, n: int) -> str:
    r, g, b = colorsys.hsv_to_rgb((i / max(n, 1)) * 0.85, 0.75, 0.9)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def steps(lo: float, hi: float, n: int) -> list[float]:
    n = max(1, int(n))
    if n == 1:
        return [lo]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


class _Builder:
    """Acumula capas/formas aplicando los límites del perfil."""

    def __init__(self, profile: DeviceProfile, params: dict[str, Any], title: str, used_colors: set[str]) -> None:
        self.profile = profile
        self.p = params
        self.plan = TestPlan(title)
        self.group = _uid()
        self._used = {c.lower() for c in used_colors}
        self._count = 0
        self._label_layer: Layer | None = None

    # ------------------------------------------------------------------ límites
    def speed(self, v: float) -> float:
        hi = self.profile.max_feed_mm_min
        c = min(max(float(v), 1.0), hi)
        if abs(c - v) > 1e-9:
            self.note(tr("Velocidad {v:g} ajustada a {c:g} mm/min (límite del perfil)", v=v, c=c))
        return c

    def power(self, v: float) -> float:
        c = min(max(float(v), 0.0), 100.0)
        if abs(c - v) > 1e-9:
            self.note(tr("Potencia {v:g} % ajustada a {c:g} %", v=v, c=c))
        return c

    def z(self, v: float) -> float:
        lo, hi = self.profile.z_min_mm, self.profile.z_max_mm
        c = min(max(float(v), lo), hi)
        if abs(c - v) > 1e-9:
            self.note(tr("Z {v:g} ajustado a {c:g} mm (rango del perfil)", v=v, c=c))
        return c

    def note(self, text: str) -> None:
        if text not in self.plan.notes:
            self.plan.notes.append(text)

    # ------------------------------------------------------------------ capas
    def _next_color(self) -> str:
        while True:
            c = _color(self._count * 7 % 31, 31)
            self._count += 1
            if c not in self._used:
                self._used.add(c)
                return c

    def layer(self, name: str, mode: LayerMode, speed: float, power: float, passes: int = 1, **kw: Any) -> Layer:
        ly = Layer(name, self._next_color(), mode=mode, speed_mm_min=self.speed(speed),
                   power_max_pct=self.power(power), passes=max(1, int(passes)), **kw)
        if mode in (LayerMode.FILL, LayerMode.FILL_LINE):
            ly.line_interval_mm = max(0.01, float(kw.get("line_interval_mm", self.p.get("interval", 0.1))))
        self.plan.layers.append(ly)
        return ly

    def label_layer(self) -> Layer:
        if self._label_layer is None:
            self._label_layer = self.layer(tr("Etiquetas (prueba)"), LayerMode.LINE, self.p.get("label_speed", 1500),
                                           self.p.get("label_power", 10))
            self._label_layer.notes = tr("Textos de la prueba; desactive la salida si no quiere grabarlos")
        return self._label_layer

    def add(self, layer: Layer, subpaths: list[gp.SubPath] | gp.SubPath, name: str = "") -> None:
        subs = [subpaths] if isinstance(subpaths, gp.SubPath) else subpaths
        if subs:
            s = make_shape(layer.id, subs, name or layer.name, "path")
            self.plan.shapes.append(replace(s, group_id=self.group))

    def text(self, text: str, x: float, y: float, h: float | None = None, anchor: str = "left") -> None:
        if not self.p.get("labels", True) or not text:
            return
        self.add(self.label_layer(), text_subpaths(text, x, y, h or float(self.p.get("label_mm", 3.0)), anchor),
                 tr("Etiqueta: {t}", t=text))


# ============================================================================ pruebas
def _origin(p: dict[str, Any]) -> Point:
    return (float(p.get("x", 10.0)), float(p.get("y", 10.0)))


def _cell_mode(p: dict[str, Any]) -> LayerMode:
    return {"fill": LayerMode.FILL, "line": LayerMode.LINE, "fill_line": LayerMode.FILL_LINE}[p.get("mode", "fill")]


def _matrix(b: _Builder, cols: list[float], rows: list[float], col_label: str, row_label: str,
            make: Callable[[float, float], tuple[str, dict[str, Any]]]) -> None:
    p = b.p
    x0, y0 = _origin(p)
    cell, gap = float(p.get("cell", 10.0)), float(p.get("gap", 3.0))
    lh = float(p.get("label_mm", 3.0))
    left = x0 + (5.5 * lh * 5 / 6 if p.get("labels", True) else 0)
    top = y0 + (lh * 2.6 if p.get("labels", True) else 0)
    if p.get("title"):
        b.text(str(p["title"]), x0, y0 + lh)
    b.text(col_label, left, top - lh * 0.3, lh * 0.8)
    y_cells = top + lh * 1.3
    for ci, cv in enumerate(cols):
        b.text(f"{cv:g}", left + ci * (cell + gap) + cell / 2, y_cells - 0.8, lh * 0.8, "center")
    for ri, rv in enumerate(rows):
        y = y_cells + ri * (cell + gap)
        b.text(f"{rv:g}", left - 1.0, y + cell / 2 + lh * 0.4, lh * 0.8, "right")
        for ci, cv in enumerate(cols):
            name, kw = make(cv, rv)
            ly = b.layer(name, _cell_mode(p), **kw)
            x = left + ci * (cell + gap)
            b.add(ly, gp.rectangle(x, y, cell, cell), name)
    b.text(row_label, x0, y_cells + len(rows) * (cell + gap) + lh * 1.2, lh * 0.8)


def _speed_power(b: _Builder, p: dict[str, Any]) -> None:
    speeds = steps(p["speed_min"], p["speed_max"], p["cols"])
    powers = steps(p["power_min"], p["power_max"], p["rows"])
    _matrix(b, speeds, powers, tr("Velocidad (mm/min) →"), tr("↓ Potencia (%)"),
            lambda v, pw: (f"V{v:g} P{pw:g}", {"speed": v, "power": pw, "passes": int(p.get("passes", 1))}))


def _power_passes(b: _Builder, p: dict[str, Any]) -> None:
    powers = steps(p["power_min"], p["power_max"], p["cols"])
    passes = [int(round(v)) for v in steps(p["passes_min"], p["passes_max"], p["rows"])]
    _matrix(b, powers, [float(v) for v in passes], tr("Potencia (%) →"), tr("↓ Pasadas"),
            lambda pw, n: (f"P{pw:g} x{int(n)}", {"speed": p["speed"], "power": pw, "passes": int(n)}))


def _interval(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    cell, gap, lh = float(p["cell"]), float(p["gap"]), float(p["label_mm"])
    vals = steps(p["interval_min"], p["interval_max"], p["count"])
    b.text(tr("Intervalo (mm)"), x0, y0 + lh)
    for i, iv in enumerate(vals):
        x = x0 + i * (cell + gap)
        ly = b.layer(f"I{iv:.3f}", LayerMode.FILL, p["speed"], p["power"], line_interval_mm=iv)
        b.add(ly, gp.rectangle(x, y0 + lh * 2, cell, cell))
        b.text(f"{iv:.2f}", x + cell / 2, y0 + lh * 3.2 + cell, lh * 0.8, "center")


def _focus(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    lh = float(p["label_mm"])
    length = float(p["length"])
    if b.profile.z_axis and p.get("method", "z") == "z":
        zs = steps(p["z_min"], p["z_max"], p["count"])
        b.text(tr("Enfoque: Z (mm)"), x0, y0 + lh)
        for i, z in enumerate(zs):
            y = y0 + lh * 2.5 + i * float(p["gap"])
            ly = b.layer(f"Z{z:+.2f}", LayerMode.LINE, p["speed"], p["power"], z_offset_mm=b.z(z))
            b.add(ly, gp.polyline([(x0 + 18, y), (x0 + 18 + length, y)]))
            b.text(f"{z:+.2f}", x0, y + lh * 0.4, lh * 0.8)
        b.note(tr("Cada línea se graba con su desplazamiento Z; la más fina indica el foco"))
    else:
        if p.get("method", "z") == "z":
            b.note(tr("El perfil no tiene eje Z: se genera la prueba en rampa"))
        ly = b.layer(tr("Rampa de enfoque"), LayerMode.LINE, p["speed"], p["power"])
        y = y0 + lh * 2.5
        b.add(ly, gp.polyline([(x0, y), (x0 + length, y)]))
        tick = b.layer(tr("Marcas de la rampa"), LayerMode.LINE, p["speed"], p["power"])
        n = int(length // 10)
        for k in range(n + 1):
            x = x0 + k * 10
            b.add(tick, gp.polyline([(x, y + 2), (x, y + (5 if k % 5 == 0 else 3.5))]))
            if k % 5 == 0:
                b.text(str(k * 10), x, y + 5 + lh * 1.2, lh * 0.8, "center")
        b.note(tr("Incline el material a lo largo de la línea: el punto más fino marca la distancia focal"))


def _scale(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    size = float(p["size"])
    ly = b.layer(tr("Escala"), LayerMode.LINE, p["speed"], p["power"])
    b.add(ly, gp.rectangle(x0, y0, size, size))
    for k in range(1, int(size // 10)):
        d = k * 10.0
        tl = 4.0 if k % 5 == 0 else 2.0
        b.add(ly, [gp.polyline([(x0 + d, y0), (x0 + d, y0 + tl)]), gp.polyline([(x0, y0 + d), (x0 + tl, y0 + d)])])
    b.text(tr("{s:g} MM", s=size), x0 + size / 2, y0 + size / 2, float(p["label_mm"]), "center")
    b.note(tr("Mida los lados con un calibre: el error de escala es (medido − {s:g}) / {s:g}", s=size))


def _precision(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    size = float(p["size"])
    ly = b.layer(tr("Precisión"), LayerMode.LINE, p["speed"], p["power"], optimize_travel=False, inside_first=False)
    c = (x0 + size / 2, y0 + size / 2)
    for i in range(int(p["count"])):
        r = size / 2 * (1 - i / max(1, int(p["count"])))
        b.add(ly, gp.rectangle(c[0] - r, c[1] - r, 2 * r, 2 * r))
        b.add(ly, [gp.polyline([(c[0] - 3, c[1]), (c[0] + 3, c[1])]), gp.polyline([(c[0], c[1] - 3), (c[0], c[1] + 3)])])
    b.note(tr("Las cruces centrales deben coincidir: una cruz doble indica pérdida de pasos u holguras"))


def _circles(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    lh = float(p["label_mm"])
    ly = b.layer(tr("Círculos"), LayerMode.LINE, p["speed"], p["power"])
    x = x0
    for d in [float(v) for v in str(p["diameters"]).replace(";", ",").split(",") if v.strip()]:
        b.add(ly, gp.ellipse(x + d / 2, y0 + lh * 2 + d / 2, d / 2, d / 2))
        b.text(f"{d:g}", x + d / 2, y0 + lh * 3.4 + d, lh * 0.8, "center")
        x += d + 4
    b.note(tr("Círculos ovalados indican holgura o correas con distinta tensión en X e Y"))


def _backlash(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    ly = b.layer(tr("Backlash"), LayerMode.LINE, p["speed"], p["power"], optimize_travel=False, inside_first=False)
    length, n, pitch = float(p["length"]), int(p["count"]), float(p["pitch"])
    cx = x0 + length
    for i in range(n):
        y = y0 + i * pitch
        if i % 2 == 0:
            b.add(ly, gp.polyline([(x0, y), (cx, y)]))
        else:
            b.add(ly, gp.polyline([(cx + length, y), (cx, y)]))
        b.add(ly, gp.polyline([(cx, y - pitch * 0.3), (cx, y + pitch * 0.3)]) if i == 0 else [])
    b.note(tr("Las líneas que llegan desde la izquierda y la derecha deben terminar alineadas en el centro"))


def _kerf(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    w, h, n = float(p["width"]), float(p["height"]), int(p["count"])
    ly = b.layer(tr("Kerf"), LayerMode.LINE, p["speed"], p["power"], passes=int(p["passes"]))
    b.add(ly, gp.rectangle(x0, y0, w * n, h))
    for i in range(1, n):
        b.add(ly, gp.polyline([(x0 + i * w, y0), (x0 + i * w, y0 + h)]))
    b.note(tr("Empuje las {n} tiras contra un lado y mida el hueco total: kerf = hueco / {n}", n=n))


def _cut(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    lh, d = float(p["label_mm"]), float(p["cell"])
    for i, v in enumerate(steps(p["speed_min"], p["speed_max"], p["count"])):
        x = x0 + i * (d + float(p["gap"]))
        ly = b.layer(f"{tr('Corte')} V{v:g}", LayerMode.LINE, v, p["power"], passes=int(p["passes"]))
        b.add(ly, gp.rectangle(x, y0 + lh * 2, d, d))
        b.text(f"{v:g}", x + d / 2, y0 + lh * 3.2 + d, lh * 0.8, "center")
    b.text(tr("Velocidad de corte (mm/min)"), x0, y0 + lh)


def _engrave(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    lh, d = float(p["label_mm"]), float(p["cell"])
    for i, pw in enumerate(steps(p["power_min"], p["power_max"], p["count"])):
        x = x0 + i * d
        ly = b.layer(f"{tr('Grabado')} P{pw:g}", LayerMode.FILL, p["speed"], pw)
        b.add(ly, gp.rectangle(x, y0 + lh * 2, d, d))
        b.text(f"{pw:g}", x + d / 2, y0 + lh * 3.2 + d, lh * 0.7, "center")
    b.text(tr("Potencia de grabado (%)"), x0, y0 + lh)


def _min_power(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    lh, length, gap = float(p["label_mm"]), float(p["length"]), float(p["gap"])
    for i, pw in enumerate(steps(p["power_min"], p["power_max"], p["count"])):
        y = y0 + lh * 2 + i * gap
        ly = b.layer(f"Pmin {pw:g}", LayerMode.LINE, p["speed"], pw, dynamic_power=False)
        b.add(ly, gp.polyline([(x0 + 14, y), (x0 + 14 + length, y)]))
        b.text(f"{pw:g}", x0, y + lh * 0.4, lh * 0.8)
    b.note(tr("La primera línea visible indica la potencia mínima que marca (se usa M3 constante)"))


def _rotary(b: _Builder, p: dict[str, Any]) -> None:
    x0, y0 = _origin(p)
    circ = math.pi * float(p["diameter"])
    w = float(p["width"])
    ly = b.layer(tr("Cilindro de prueba"), LayerMode.LINE, p["speed"], p["power"])
    b.add(ly, gp.rectangle(x0, y0, w, circ))
    for k in range(1, 4):
        y = y0 + circ * k / 4
        b.add(ly, gp.polyline([(x0, y), (x0 + w * 0.25, y)]))
    b.text(tr("D{d:g} C{c:.1f}", d=float(p["diameter"]), c=circ), x0 + w / 2, y0 + circ / 2, float(p["label_mm"]),
           "center")
    b.note(tr("Con el rotary activado el rectángulo debe dar exactamente una vuelta: los bordes superior e "
              "inferior deben coincidir"))


def _common(speed: float, power: float, extra: list[Param] | None = None) -> list[Param]:
    return (extra or []) + [
        Param("speed", N_("Velocidad"), "float", speed, 1, 100000, suffix=" mm/min"),
        Param("power", N_("Potencia"), "float", power, 0, 100, suffix=" %"),
    ]


_POS = [Param("x", N_("Posición X"), "float", 10.0, -10000, 10000, suffix=" mm"),
        Param("y", N_("Posición Y"), "float", 10.0, -10000, 10000, suffix=" mm"),
        Param("labels", N_("Etiquetas"), "bool", True),
        Param("label_mm", N_("Altura de etiqueta"), "float", 3.0, 1, 30, suffix=" mm"),
        Param("label_power", N_("Potencia de etiquetas"), "float", 10.0, 0, 100, suffix=" %"),
        Param("label_speed", N_("Velocidad de etiquetas"), "float", 1500.0, 1, 100000, suffix=" mm/min")]
_MODE = Param("mode", N_("Modo de celda"), "choice", "fill", choices=(("fill", N_("Relleno")), ("line", N_("Línea")),
                                                                       ("fill_line", N_("Relleno + Línea"))))

TEST_KINDS: dict[str, TestKind] = {}


def _reg(k: TestKind) -> None:
    TEST_KINDS[k.id] = k


_reg(TestKind("speed_power", N_("Matriz velocidad/potencia"), N_("Cuadrícula de celdas: cada columna una velocidad y "
              "cada fila una potencia."), [
    Param("title", N_("Título"), "text", "MATERIAL"), _MODE,
    Param("speed_min", N_("Velocidad mínima"), "float", 1000, 1, 100000, suffix=" mm/min"),
    Param("speed_max", N_("Velocidad máxima"), "float", 5000, 1, 100000, suffix=" mm/min"),
    Param("cols", N_("Columnas"), "int", 5, 1, 20),
    Param("power_min", N_("Potencia mínima"), "float", 10, 0, 100, suffix=" %"),
    Param("power_max", N_("Potencia máxima"), "float", 90, 0, 100, suffix=" %"),
    Param("rows", N_("Filas"), "int", 5, 1, 20),
    Param("passes", N_("Pasadas"), "int", 1, 1, 50),
    Param("interval", N_("Intervalo"), "float", 0.1, 0.01, 5, suffix=" mm"),
    Param("cell", N_("Tamaño de celda"), "float", 10, 2, 100, suffix=" mm"),
    Param("gap", N_("Separación"), "float", 3, 0, 50, suffix=" mm")] + _POS, _speed_power))
_reg(TestKind("power_passes", N_("Matriz potencia/pasadas"), N_("Columnas de potencia y filas de pasadas a velocidad "
              "fija; útil para corte."), [
    Param("title", N_("Título"), "text", "CORTE"), Param("mode", N_("Modo de celda"), "choice", "line",
                                                         choices=_MODE.choices),
    Param("speed", N_("Velocidad"), "float", 400, 1, 100000, suffix=" mm/min"),
    Param("power_min", N_("Potencia mínima"), "float", 50, 0, 100, suffix=" %"),
    Param("power_max", N_("Potencia máxima"), "float", 100, 0, 100, suffix=" %"),
    Param("cols", N_("Columnas"), "int", 4, 1, 20),
    Param("passes_min", N_("Pasadas mínimas"), "int", 1, 1, 50),
    Param("passes_max", N_("Pasadas máximas"), "int", 4, 1, 50),
    Param("rows", N_("Filas"), "int", 4, 1, 20),
    Param("interval", N_("Intervalo"), "float", 0.1, 0.01, 5, suffix=" mm"),
    Param("cell", N_("Tamaño de celda"), "float", 10, 2, 100, suffix=" mm"),
    Param("gap", N_("Separación"), "float", 3, 0, 50, suffix=" mm")] + _POS, _power_passes))
_reg(TestKind("interval", N_("Prueba de intervalo"), N_("Rellenos con distinto intervalo entre líneas."), _common(
    3000, 30, [Param("interval_min", N_("Intervalo mínimo"), "float", 0.05, 0.01, 5, suffix=" mm"),
               Param("interval_max", N_("Intervalo máximo"), "float", 0.3, 0.01, 5, suffix=" mm"),
               Param("count", N_("Número de muestras"), "int", 6, 1, 20),
               Param("cell", N_("Tamaño de celda"), "float", 10, 2, 100, suffix=" mm"),
               Param("gap", N_("Separación"), "float", 3, 0, 50, suffix=" mm")]) + _POS, _interval))
_reg(TestKind("focus", N_("Prueba de enfoque"), N_("Líneas a distintas alturas Z, o una rampa si no hay eje Z."),
              _common(1500, 30, [
                  Param("method", N_("Método"), "choice", "z", choices=(("z", N_("Eje Z")), ("ramp", N_("Rampa")))),
                  Param("z_min", N_("Z mínimo"), "float", -3, -100, 100, suffix=" mm"),
                  Param("z_max", N_("Z máximo"), "float", 0, -100, 100, suffix=" mm"),
                  Param("count", N_("Número de líneas"), "int", 7, 2, 30),
                  Param("gap", N_("Separación"), "float", 4, 1, 50, suffix=" mm"),
                  Param("length", N_("Longitud"), "float", 40, 5, 500, suffix=" mm")]) + _POS, _focus))
_reg(TestKind("scale", N_("Prueba de escala"), N_("Cuadrado con marcas cada 10 mm para medir la escala."),
              _common(1500, 30, [Param("size", N_("Lado"), "float", 100, 10, 1000, suffix=" mm")]) + _POS, _scale))
_reg(TestKind("precision", N_("Prueba de precisión"), N_("Cuadrados concéntricos que vuelven a la misma cruz."),
              _common(3000, 30, [Param("size", N_("Lado"), "float", 60, 10, 1000, suffix=" mm"),
                                 Param("count", N_("Repeticiones"), "int", 5, 1, 20)]) + _POS, _precision))
_reg(TestKind("circles", N_("Prueba de círculos"), N_("Círculos de varios diámetros para comprobar la redondez."),
              _common(1500, 30, [Param("diameters", N_("Diámetros"), "text", "5, 10, 20, 40")]) + _POS, _circles))
_reg(TestKind("backlash", N_("Prueba de backlash"), N_("Líneas que llegan al mismo punto desde lados opuestos."),
              _common(2000, 30, [Param("length", N_("Longitud"), "float", 20, 5, 200, suffix=" mm"),
                                 Param("count", N_("Número de líneas"), "int", 10, 2, 50),
                                 Param("pitch", N_("Separación"), "float", 1.5, 0.3, 20, suffix=" mm")]) + _POS,
              _backlash))
_reg(TestKind("kerf", N_("Prueba de kerf"), N_("Tiras cortadas que se juntan para medir el ancho de corte."),
              _common(400, 90, [Param("width", N_("Ancho de tira"), "float", 10, 2, 100, suffix=" mm"),
                                Param("height", N_("Alto"), "float", 30, 5, 200, suffix=" mm"),
                                Param("count", N_("Número de tiras"), "int", 5, 2, 20),
                                Param("passes", N_("Pasadas"), "int", 1, 1, 50)]) + _POS, _kerf))
_reg(TestKind("cut", N_("Prueba de corte"), N_("Cuadrados cortados a velocidades crecientes."),
              [Param("power", N_("Potencia"), "float", 90, 0, 100, suffix=" %"),
               Param("speed_min", N_("Velocidad mínima"), "float", 200, 1, 100000, suffix=" mm/min"),
               Param("speed_max", N_("Velocidad máxima"), "float", 800, 1, 100000, suffix=" mm/min"),
               Param("count", N_("Número de muestras"), "int", 5, 1, 20),
               Param("passes", N_("Pasadas"), "int", 1, 1, 50),
               Param("cell", N_("Tamaño de celda"), "float", 10, 2, 100, suffix=" mm"),
               Param("gap", N_("Separación"), "float", 4, 0, 50, suffix=" mm")] + _POS, _cut))
_reg(TestKind("engrave", N_("Prueba de grabado"), N_("Escala de potencias en relleno a velocidad fija."),
              [Param("speed", N_("Velocidad"), "float", 3000, 1, 100000, suffix=" mm/min"),
               Param("power_min", N_("Potencia mínima"), "float", 5, 0, 100, suffix=" %"),
               Param("power_max", N_("Potencia máxima"), "float", 50, 0, 100, suffix=" %"),
               Param("count", N_("Número de muestras"), "int", 10, 2, 30),
               Param("interval", N_("Intervalo"), "float", 0.1, 0.01, 5, suffix=" mm"),
               Param("cell", N_("Tamaño de celda"), "float", 8, 2, 100, suffix=" mm")] + _POS, _engrave))
_reg(TestKind("min_power", N_("Prueba de potencia mínima"), N_("Líneas con potencias muy bajas para encontrar el "
              "umbral de marcado."),
              [Param("speed", N_("Velocidad"), "float", 2000, 1, 100000, suffix=" mm/min"),
               Param("power_min", N_("Potencia mínima"), "float", 0.5, 0, 100, suffix=" %"),
               Param("power_max", N_("Potencia máxima"), "float", 10, 0, 100, suffix=" %"),
               Param("count", N_("Número de líneas"), "int", 10, 2, 40),
               Param("length", N_("Longitud"), "float", 30, 5, 300, suffix=" mm"),
               Param("gap", N_("Separación"), "float", 3, 1, 50, suffix=" mm")] + _POS, _min_power))
_reg(TestKind("rotary", N_("Prueba de rotary"), N_("Rectángulo de una vuelta completa del objeto (cilindro de prueba)."),
              _common(1500, 20, [Param("diameter", N_("Diámetro del objeto"), "float", 60, 5, 500, suffix=" mm"),
                                 Param("width", N_("Anchura"), "float", 20, 2, 300, suffix=" mm")]) + _POS, _rotary))


def defaults(kind: str) -> dict[str, Any]:
    return {p.key: p.default for p in TEST_KINDS[kind].params}


def build(kind: str, params: dict[str, Any], profile: DeviceProfile, doc: Document | None = None) -> TestPlan:
    """Genera la prueba; lanza ``ValueError`` si no cabe en el área de trabajo."""
    tk = TEST_KINDS[kind]
    full = defaults(kind)
    full.update(params)
    for prm in tk.params:  # límites de cada parámetro
        v = full.get(prm.key)
        if prm.kind in ("float", "int") and v is not None:
            c = min(max(float(v), prm.lo), prm.hi)
            full[prm.key] = int(round(c)) if prm.kind == "int" else c
    used = {ly.color for ly in doc.layers} if doc is not None else set()
    b = _Builder(profile, full, tr(tk.title), used)
    tk.build(b, full)
    bounds = gp.bounds_of(sp for s in b.plan.shapes for sp in s.subpaths)
    if bounds is None:
        raise ValueError(tr("La prueba no generó geometría"))
    b.plan.size = (bounds[2] - bounds[0], bounds[3] - bounds[1])
    w, h = (doc.width_mm, doc.height_mm) if doc is not None else (profile.width_mm, profile.height_mm)
    if kind == "rotary":
        b.note(tr("La altura corresponde a la circunferencia del objeto (superficie)"))
    elif bounds[0] < -1e-6 or bounds[1] < -1e-6 or bounds[2] > w + 1e-6 or bounds[3] > h + 1e-6:
        raise ValueError(tr("La prueba ({a:.0f} × {b:.0f} mm desde X{x:.0f} Y{y:.0f}) no cabe en el área de trabajo "
                            "({w:.0f} × {h:.0f} mm)", a=b.plan.size[0], b=b.plan.size[1], x=bounds[0], y=bounds[1],
                            w=w, h=h))
    return b.plan


def apply_plan(doc: Document, plan: TestPlan) -> list[str]:
    """Añade capas y formas de la prueba al documento (dentro de una edición deshacible)."""
    order = doc.next_order()
    for i, ly in enumerate(plan.layers):
        ly.order = order + i
        doc.layers.append(ly)
    doc.shapes.extend(plan.shapes)
    return [s.id for s in plan.shapes]
