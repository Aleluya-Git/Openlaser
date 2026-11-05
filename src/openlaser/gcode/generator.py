"""Generación de G-code GRBL para todas las capas (Fase 3).

Modos: Línea (contornos con orden interior→exterior, entrada/salida, puentes y
perforación), Relleno (barrido con ángulo, cruzado, bidireccional y overscan),
Relleno + Línea, e Imagen (tramado o escala de grises con potencia variable).

Reglas de seguridad aplicadas siempre:

* Cabecera con ``G90 G21`` y ``M5``; pie con ``M5`` y ``M9``.
* Los desplazamientos sin corte son ``G0`` y **siempre** van precedidos de
  ``S0`` modal: aunque el controlador no esté en modo láser ($32=0) no hay
  emisión durante un G0.
* S se limita a ``[0, s_max]`` del perfil y F al avance máximo.
"""
from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from openlaser.devices.profile import DeviceProfile
from openlaser.document.model import Document, Layer, LayerMode, Shape
from openlaser.geometry.coords import CoordinateSystem
from openlaser.i18n import tr
from openlaser.toolpath.cutpath import build_steps
from openlaser.toolpath.fill import hatch, rotate_point
from openlaser.toolpath.order import PathItem, order_paths

Point = tuple[float, float]
FLATTEN_TOL_MM = 0.02


def fmt(v: float, decimals: int = 3) -> str:
    text = f"{v:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "", "-") else text


@dataclass
class JobOrigin:
    """Dónde se coloca el diseño respecto al origen de trabajo.

    ``absolute``: las coordenadas del documento son coordenadas de máquina
    (requiere homing; WCO se tiene en cuenta). ``user``: la esquina inferior
    izquierda del diseño se coloca en el origen de trabajo (0,0).
    """
    mode: str = "absolute"  # "absolute" | "user"


@dataclass
class LayerSpan:
    """Tramo de líneas generado por una capa (para la simulación por capas)."""
    layer_id: str
    name: str
    color: str
    mode: str
    start: int
    end: int


class GeneratedJob:
    """Trabajo generado. Las imágenes van como :class:`RasterBlock` en ``parts`` y su texto se crea sólo
    cuando alguien lee :attr:`lines` (enviar, guardar, vista previa); el análisis las mide sin texto."""

    def __init__(self, parts: list, skipped_layers: list[str], work_points: list[Point],
                 layer_spans: list[LayerSpan] | None = None, warnings: list[str] | None = None) -> None:
        self.parts = parts
        self.skipped_layers = skipped_layers
        self.work_points = work_points
        self.layer_spans = layer_spans or []
        self.warnings = warnings or []
        self._lines: list[str] | None = None

    @property
    def lines(self) -> list[str]:
        if self._lines is None:
            out: list[str] = []
            for part in self.parts:
                if isinstance(part, RasterBlock):
                    out.extend(part.lines())
                else:
                    out.append(part)
            self._lines = out
        return self._lines

    @property
    def has_lazy_blocks(self) -> bool:
        return any(isinstance(p, RasterBlock) for p in self.parts)


def _count_lines(parts: list) -> int:
    return sum(p.n_lines if isinstance(p, RasterBlock) else 1 for p in parts)


# ------------------------------------------------------------------ caché ráster
_RASTER_CACHE: "OrderedDict[tuple, object]" = OrderedDict()
_RASTER_CACHE_SIZE = 8
_BLOCK_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()
_CACHE_LOCK = threading.Lock()  # el análisis se calcula también en un hilo de fondo


def _cache_get(cache: "OrderedDict", key: tuple):  # noqa: ANN202
    with _CACHE_LOCK:
        hit = cache.get(key)
        if hit is not None:
            cache.move_to_end(key)
        return hit


def _cache_put(cache: "OrderedDict", key: tuple, value: object) -> None:
    with _CACHE_LOCK:
        cache[key] = value
        while len(cache) > _RASTER_CACHE_SIZE:
            cache.popitem(last=False)


def processed_image(doc: Document, shape: Shape):  # noqa: ANN201 - SampledImage (import diferido)
    """Imagen procesada (fracción de potencia) con caché por recurso, marco y ajustes."""
    from openlaser.image_processing.raster import process
    ref = shape.image
    assert ref is not None
    src = doc.images.get(ref.resource)
    if src is None:
        return None
    from openlaser.image_processing.raster import SampledImage
    # La rejilla de salida se ancla al propio marco: mover la imagen no cambia los píxeles tramados, así que
    # la clave usa el marco relativo a su primera esquina y al reutilizarla sólo se desplaza el origen.
    c0, c1, c3 = ref.corners
    rel = (round(c1[0] - c0[0], 9), round(c1[1] - c0[1], 9), round(c3[0] - c0[0], 9), round(c3[1] - c0[1], 9))
    key = (ref.resource, id(src.gray), rel, ref.settings)
    hit = _cache_get(_RASTER_CACHE, key)
    if hit is None:
        result = process(src.gray, ref.corners, ref.settings)
        hit = (result, src.gray, c0)  # se retiene el array original para que su id no se reutilice
        _cache_put(_RASTER_CACHE, key, hit)
    base, _gray, b0 = hit
    if b0 == c0:
        return base
    a = math.radians(base.angle_deg)
    dx, dy = c0[0] - b0[0], c0[1] - b0[1]
    # desplazamiento expresado en el sistema girado de la rejilla
    rx, ry = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
    return SampledImage(base.values, base.x0 + rx, base.y0 + ry, base.pitch, base.angle_deg)


# ------------------------------------------------------------------- emisión
class _Emitter:
    def __init__(self, lines: list[str], to_work: Callable[[Point], Point], work_points: list[Point]) -> None:
        self.lines = lines
        self.to_work = to_work
        self.work_points = work_points
        self.cursor: Point = (0.0, 0.0)

    def rapid(self, p: Point) -> None:
        self.lines.append(f"G0X{fmt(p[0])}Y{fmt(p[1])}")
        self.cursor = p


def _s(profile: DeviceProfile, pct: float) -> float:
    return max(0.0, min(profile.s_max, profile.s_from_percent(pct)))


def _emit_line_layer(em: _Emitter, layer: Layer, shapes: list[Shape], profile: DeviceProfile, tol: float,
                     warnings: list[str]) -> bool:
    items: list[PathItem] = []
    for shape in shapes:
        if shape.image is not None:
            continue
        for sp in shape.subpaths:
            pts = [em.to_work(p) for p in sp.flatten(tol)]
            if len(pts) >= 2:
                items.append(PathItem(pts, sp.closed))
    if not items:
        return False
    s_val = _s(profile, layer.power_max_pct)
    spindle = "M4" if layer.dynamic_power else "M3"
    feed = min(layer.speed_mm_min, profile.max_feed_mm_min)
    pierce_s = layer.pierce_ms / 1000.0
    if pierce_s > 0 and layer.dynamic_power:
        warnings.append(tr("{n}: la perforación previa requiere M3; se usa M3 durante la pausa", n=layer.name))
    ordered = order_paths(items, em.cursor, inside_first=layer.inside_first, optimize=layer.optimize_travel)
    for _ in range(max(1, int(layer.passes))):
        for pts, _tag in ordered:
            closed = len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9
            start, steps = build_steps(pts, closed, layer.lead_in_mm, layer.lead_out_mm if closed else 0.0,
                                       layer.tab_count if closed else 0, layer.tab_width_mm)
            em.rapid(start)
            em.lines.append(f"{spindle}S0")
            if pierce_s > 0 and steps and steps[0][1]:
                em.lines.append(f"M3S{fmt(s_val, 1)}")
                em.lines.append(f"G4P{fmt(pierce_s, 3)}")
                em.lines.append(f"{spindle}S0")
            em.work_points.append(start)
            current_on: bool | None = None
            first = True
            for p, on in steps:
                cmd = f"G1X{fmt(p[0])}Y{fmt(p[1])}"
                if first:
                    cmd += f"F{fmt(feed, 0)}"
                    first = False
                if on != current_on:
                    cmd += f"S{fmt(s_val, 1) if on else '0'}"
                    current_on = on
                em.lines.append(cmd)
                em.work_points.append(p)
            em.lines.append("M5")
            em.cursor = steps[-1][0] if steps else start
    return True


def _closed_polygons(shape: Shape, tol: float) -> list[list[Point]]:
    return [sp.flatten(tol) for sp in shape.subpaths if sp.closed and len(sp.nodes) >= 2]


def _emit_rows(em: _Emitter, rows: list[list[tuple[float, float, float]]], y_of: Callable[[int], float],
               to_doc: Callable[[float, float], Point], spindle: str, feed: float, overscan: float,
               bidirectional: bool) -> None:
    """Emite filas de tramos ``(x0, x1, s)`` (s = 0 → apagado) en un sistema girado.

    Cada fila: G0 al comienzo menos el overscan, G1 de aceleración con S0,
    tramos con su potencia y G1 de salida con S0. Ninguna fila termina con S≠0.
    """
    em.lines.append(f"{spindle}S0")
    first_feed = True
    for k, runs in enumerate(rows):
        if not runs:
            continue
        reverse = bidirectional and (k % 2 == 1)
        y = y_of(k)
        seq = list(reversed(runs)) if reverse else runs
        d = -1.0 if reverse else 1.0
        x_start = seq[0][1] if reverse else seq[0][0]
        x_end = seq[-1][0] if reverse else seq[-1][1]
        em.rapid(em.to_work(to_doc(x_start - d * overscan, y)))
        p_start = em.to_work(to_doc(x_start, y))
        cmd = f"G1X{fmt(p_start[0])}Y{fmt(p_start[1])}"
        if first_feed:
            cmd += f"F{fmt(feed, 0)}"
            first_feed = False
        em.lines.append(cmd + "S0")
        em.cursor = p_start
        current = 0.0
        for x0, x1, s in seq:
            a, b = (x1, x0) if reverse else (x0, x1)
            pa = em.to_work(to_doc(a, y))
            if math.dist(pa, em.cursor) > 1e-6 and current != 0.0:
                em.lines.append(f"G1X{fmt(pa[0])}Y{fmt(pa[1])}S0")
                current = 0.0
            elif math.dist(pa, em.cursor) > 1e-6:
                em.lines.append(f"G1X{fmt(pa[0])}Y{fmt(pa[1])}")
            pb = em.to_work(to_doc(b, y))
            cmd = f"G1X{fmt(pb[0])}Y{fmt(pb[1])}"
            if s != current:
                cmd += f"S{fmt(s, 1)}"
                current = s
            em.lines.append(cmd)
            em.cursor = pb
            if s > 0:
                em.work_points.append(pa)
                em.work_points.append(pb)
        out = em.to_work(to_doc(x_end + d * overscan, y))
        if overscan > 0:
            em.lines.append(f"G1X{fmt(out[0])}Y{fmt(out[1])}S0")
            em.cursor = out
        else:
            em.lines.append("S0")
    em.lines.append("M5")


def _emit_fill_layer(em: _Emitter, layer: Layer, shapes: list[Shape], profile: DeviceProfile, tol: float,
                     warnings: list[str]) -> bool:
    polys = [p for p in (_closed_polygons(s, tol) for s in shapes if s.image is None) if p]
    open_count = sum(1 for s in shapes if s.image is None for sp in s.subpaths if not sp.closed)
    if open_count:
        warnings.append(tr("{n}: {c} trayectorias abiertas no se rellenan", n=layer.name, c=open_count))
    if not polys:
        return False
    s_val = _s(profile, layer.power_max_pct)
    spindle = "M4" if layer.dynamic_power else "M3"
    feed = min(layer.speed_mm_min, profile.max_feed_mm_min)
    interval = max(layer.line_interval_mm, 0.01)
    angles = [layer.fill_angle_deg] + ([layer.fill_angle_deg + 90.0] if layer.cross_fill else [])
    for _ in range(max(1, int(layer.passes))):
        for angle in angles:
            rows = hatch(polys, interval, angle)
            ang = math.radians(angle)
            run_rows = [[(a, b, s_val) for a, b in r.intervals] for r in rows]
            _emit_rows(em, run_rows, lambda k, rows=rows: rows[k].y,
                       lambda x, y, ang=ang: rotate_point((x, y), ang), spindle, feed, layer.overscan_mm,
                       layer.bidirectional)
    return True


def _quantize(values: np.ndarray, s_min: float, s_max: float, grayscale: bool) -> np.ndarray:
    """Fracción de potencia → S entero por píxel (0 = apagado)."""
    if grayscale:
        s = np.where(values > 1e-3, s_min + values * (s_max - s_min), 0.0)
    else:
        s = np.where(values > 0.5, s_max, 0.0)
    return np.rint(s).astype(np.int64)


def _row_runs(q: np.ndarray) -> list[tuple[np.ndarray, np.ndarray, np.ndarray] | None]:
    """Por fila: (inicios, finales, S) de los tramos entre el primer y el último píxel encendido (vectorizado)."""
    rows, cols = q.shape
    on = q != 0
    any_on = on.any(axis=1)
    lo = np.argmax(on, axis=1)
    hi = cols - np.argmax(on[:, ::-1], axis=1)
    if cols > 1:
        ch_r, ch_c = np.nonzero(q[:, 1:] != q[:, :-1])
        ch_c = ch_c + 1
    else:
        ch_r = ch_c = np.zeros(0, dtype=np.int64)
    split = np.searchsorted(ch_r, np.arange(rows + 1))
    out: list[tuple[np.ndarray, np.ndarray, np.ndarray] | None] = []
    for r in range(rows):
        if not any_on[r]:
            out.append(None)
            continue
        a, b = int(lo[r]), int(hi[r])
        c = ch_c[split[r]:split[r + 1]]
        c = c[(c > a) & (c < b)]
        starts = np.concatenate(([a], c))
        ends = np.concatenate((c, [b]))
        out.append((starts, ends, q[r, starts]))
    return out


def _image_rows(values: np.ndarray, s_min: float, s_max: float, grayscale: bool, x0: float,
                pitch: float) -> list[list[tuple[float, float, float]]]:
    """Fracciones de potencia → filas de tramos (x0, x1, S) con S cuantizado a enteros."""
    rows: list[list[tuple[float, float, float]]] = []
    for item in _row_runs(_quantize(values, s_min, s_max, grayscale)):
        if item is None:
            rows.append([])
            continue
        starts, ends, vals = item
        xa = (x0 + starts * pitch).tolist()
        xb = (x0 + ends * pitch).tolist()
        rows.append(list(zip(xa, xb, vals.astype(float).tolist())))
    return rows


def _axis_affine(to_work: Callable[[Point], Point]) -> tuple[float, float, float, float] | None:
    """(ox, sx, oy, sy) si ``to_work`` es x' = ox + sx·x, y' = oy + sy·y (sin giro); si no, None."""
    o, ex, ey = to_work((0.0, 0.0)), to_work((1.0, 0.0)), to_work((0.0, 1.0))
    sx, kx = ex[0] - o[0], ex[1] - o[1]
    ky, sy = ey[0] - o[0], ey[1] - o[1]
    if abs(kx) > 1e-12 or abs(ky) > 1e-12 or abs(sx) < 1e-12 or abs(sy) < 1e-12:
        return None
    return o[0], sx, o[1], sy


def _emit_image_fast(sampled, q: np.ndarray | None, aff: tuple[float, float, float, float],  # noqa: ANN001
                     spindle: str, feed: float, overscan: float, bidirectional: bool,
                     passes: int, runs: list | None = None) -> tuple[list[str], list[Point], Point]:
    """Emisión de una imagen sin giro: mismas líneas que ``_emit_rows`` pero con coordenadas preformateadas.

    Los X de la rejilla se formatean una sola vez y cada tramo es una única línea ``G1X…Y…S…``; es
    10–20 veces más rápido en fotografías tramadas (millones de tramos).
    """
    ox, sx, oy, sy = aff
    cols = int(sampled.values.shape[1])
    x0, y0, pitch = sampled.x0, sampled.y0, sampled.pitch
    grid = [fmt(ox + sx * (x0 + j * pitch)) for j in range(cols + 1)]
    s_text: dict[int, str] = {}
    lines: list[str] = []
    points: list[Point] = []
    cursor: Point = (0.0, 0.0)
    if runs is None:
        assert q is not None
        runs = _row_runs(q)

    def sfmt(v: int) -> str:
        t = s_text.get(v)
        if t is None:
            t = s_text[v] = fmt(float(v), 1)
        return t

    for _ in range(max(1, passes)):
        lines.append(f"{spindle}S0")
        first_feed = True
        for k, item in enumerate(runs):
            if item is None:
                continue
            starts, ends, vals = item
            yk = fmt(oy + sy * (y0 + (k + 0.5) * pitch))
            reverse = bidirectional and (k % 2 == 1)
            a_idx, b_idx = int(starts[0]), int(ends[-1])
            xd_start, xd_end = (x0 + b_idx * pitch, x0 + a_idx * pitch) if reverse else \
                (x0 + a_idx * pitch, x0 + b_idx * pitch)
            d = -1.0 if reverse else 1.0
            lines.append(f"G0X{fmt(ox + sx * (xd_start - d * overscan))}Y{yk}")
            start_txt = grid[b_idx] if reverse else grid[a_idx]
            cmd = f"G1X{start_txt}Y{yk}"
            if first_feed:
                cmd += f"F{fmt(feed, 0)}"
                first_feed = False
            lines.append(cmd + "S0")
            if reverse:
                idx = starts[::-1].tolist()
                sv = vals[::-1].tolist()
            else:
                idx = ends.tolist()
                sv = vals.tolist()
            lines.extend([f"G1X{grid[j]}Y{yk}S{sfmt(v)}" for j, v in zip(idx, sv)])
            yw = oy + sy * (y0 + (k + 0.5) * pitch)
            on_x = [x0 + int(st) * pitch for st, v in zip(starts.tolist(), vals.tolist()) if v > 0]
            if on_x:
                last_on = max(int(e) for e, v in zip(ends.tolist(), vals.tolist()) if v > 0)
                points.append((ox + sx * min(on_x), yw))
                points.append((ox + sx * (x0 + last_on * pitch), yw))
            if overscan > 0:
                xo = ox + sx * (xd_end + d * overscan)
                lines.append(f"G1X{fmt(xo)}Y{yk}S0")
                cursor = (xo, yw)
            else:
                lines.append("S0")
                cursor = (ox + sx * xd_end, yw)
        lines.append("M5")
    return lines, points, cursor


def _emit_image_layer(em: _Emitter, layer: Layer, shapes: list[Shape], profile: DeviceProfile, doc: Document,
                      warnings: list[str]) -> bool:
    done = False
    spindle = "M4" if layer.dynamic_power else "M3"
    feed = min(layer.speed_mm_min, profile.max_feed_mm_min)
    s_lo = _s(profile, min(layer.power_min_pct, layer.power_max_pct))
    s_hi = _s(profile, layer.power_max_pct)
    vector_count = sum(1 for s in shapes if s.image is None)
    if vector_count:
        warnings.append(tr("{n}: {c} objetos vectoriales en una capa de imagen no se graban", n=layer.name, c=vector_count))
    for shape in shapes:
        if shape.image is None:
            continue
        try:
            sampled = processed_image(doc, shape)
        except ValueError as exc:
            warnings.append(f"{shape.name or tr('imagen')}: {exc}")
            continue
        if sampled is None:
            warnings.append(tr("{n}: faltan los píxeles de la imagen", n=shape.name or tr("imagen")))
            continue
        grayscale = shape.image.settings.method == "grayscale"
        aff = _axis_affine(em.to_work)
        if aff is not None and not sampled.angle_deg:
            # Vía rápida: bloque perezoso (sin texto hasta que haga falta) y medido con NumPy.
            runs, q = _cached_runs(sampled.values, s_lo, s_hi, grayscale)
            block_obj = RasterBlock(sampled, runs, aff, spindle, feed, layer.overscan_mm, layer.bidirectional,
                                    int(layer.passes))
            block_obj.q = q
            em.lines.append(block_obj)  # type: ignore[arg-type]
            em.work_points.extend(block_obj.points)
            em.cursor = block_obj.end
            done = True
            continue
        # Las líneas de una imagen sólo dependen de estos datos: se reutilizan entre análisis.
        signature = (em.to_work((0.0, 0.0)), em.to_work((1.0, 0.0)), em.to_work((0.0, 1.0)))
        key = (id(sampled), s_lo, s_hi, grayscale, spindle, feed, layer.overscan_mm, layer.bidirectional,
               int(layer.passes), signature)
        cached = _cache_get(_BLOCK_CACHE, key)
        if cached is None:  # imagen con ángulo de barrido o máquina girada: emisión general
            block: list[str] = []
            points: list[Point] = []
            sub = _Emitter(block, em.to_work, points)
            rows = _image_rows(sampled.values, s_lo, s_hi, grayscale, sampled.x0, sampled.pitch)
            for _ in range(max(1, int(layer.passes))):
                _emit_rows(sub, rows, lambda k, sa=sampled: sa.y0 + (k + 0.5) * sa.pitch, sampled.to_doc,
                           spindle, feed, layer.overscan_mm, layer.bidirectional)
            cached = (block, points, sub.cursor, sampled)  # se guarda ``sampled`` para que su id no se reutilice
            _cache_put(_BLOCK_CACHE, key, cached)
        em.lines.extend(cached[0])
        em.work_points.extend(cached[1])
        em.cursor = cached[2]
        done = True
    return done


# ------------------------------------------------------------------ bloques ráster
_RUNS_CACHE: "OrderedDict[tuple, tuple]" = OrderedDict()


def _cached_runs(values: np.ndarray, s_lo: float, s_hi: float, grayscale: bool) -> tuple[list, np.ndarray]:
    """Tramos por fila y potencia S por píxel de una imagen procesada (no dependen de su posición)."""
    key = (id(values), s_lo, s_hi, grayscale)
    hit = _cache_get(_RUNS_CACHE, key)
    if hit is None:
        q = _quantize(values, s_lo, s_hi, grayscale).astype(np.int32)
        hit = (_row_runs(q), q, values)
        _cache_put(_RUNS_CACHE, key, hit)
    return hit[0], hit[1]


@dataclass
class BlockMeasure:
    """Lo que el análisis necesita de un bloque ráster, calculado con NumPy sin generar texto."""
    n_lines: int
    motion_lines: int
    rel_times: np.ndarray        # tiempo acumulado tras cada línea, desde la entrada al bloque
    cut: float
    travel: float
    bounds_all: tuple[float, float, float, float]
    bounds_laser: tuple[float, float, float, float] | None
    hull: list[Point]
    max_s: float
    end: Point


class RasterBlock:
    """Imagen sin giro: las líneas G-code se generan bajo demanda; las medidas se calculan con NumPy."""

    def __init__(self, sampled, runs: list, aff: tuple[float, float, float, float], spindle: str,  # noqa: ANN001
                 feed: float, overscan: float, bidirectional: bool, passes: int) -> None:
        self.sampled, self.runs, self.aff = sampled, runs, aff
        self.spindle, self.feed, self.overscan = spindle, feed, overscan
        self.bidirectional, self.passes = bidirectional, max(1, int(passes))
        self._lines: list[str] | None = None
        self.q: np.ndarray | None = None      # S por píxel (para la simulación)
        rows_on = [r for r in runs if r is not None]
        per_pass = 2 + sum(3 + len(r[0]) for r in rows_on)
        self.n_lines = per_pass * self.passes
        self.points, self.end = self._geometry()

    def _row_y(self, k: int) -> float:
        ox, sx, oy, sy = self.aff
        return round(oy + sy * (self.sampled.y0 + (k + 0.5) * self.sampled.pitch), 3)

    def _x(self, xd: Any) -> Any:  # float o array: X de trabajo redondeado como en el texto
        ox, sx, _oy, _sy = self.aff
        return np.round(ox + sx * xd, 3)

    def _geometry(self) -> tuple[list[Point], Point]:
        sa, x0, pitch = self.sampled, self.sampled.x0, self.sampled.pitch
        pts: list[Point] = []
        end: Point = (0.0, 0.0)
        for k, item in enumerate(self.runs):
            if item is None:
                continue
            starts, ends, vals = item
            on = vals > 0
            y = self._row_y(k)
            if on.any():
                xa = self._x(x0 + starts[on] * pitch)
                xb = self._x(x0 + ends[on] * pitch)
                pts.append((float(min(xa.min(), xb.min())), y))
                pts.append((float(max(xa.max(), xb.max())), y))
            reverse = self.bidirectional and (k % 2 == 1)
            a_idx, b_idx = int(starts[0]), int(ends[-1])
            d = -1.0 if reverse else 1.0
            xd_end = x0 + (a_idx if reverse else b_idx) * pitch
            end = (float(self._x(xd_end + d * self.overscan)), y)
        del sa
        return pts, end

    def lines(self) -> list[str]:
        if self._lines is None:
            self._lines, _pts, _cur = _emit_image_fast(self.sampled, None, self.aff, self.spindle, self.feed,
                                                       self.overscan, self.bidirectional, self.passes, self.runs)
        return self._lines

    def timeline(self, x: float, y: float, rapid_mm_min: float, time_factor: float) -> dict:
        """Movimientos para la simulación agrupando cada fila en un solo tramo de corte.

        Una fotografía tiene millones de tramos; dibujarlos uno a uno satura el lienzo (se ven bloques de
        color) y consume mucha memoria. Aquí cada fila son 3–4 movimientos (rápido, aceleración, fila,
        salida) y la imagen quemada se dibuja aparte, píxel a píxel, a partir de ``q``.
        Devuelve arrays (x0, y0, x1, y1, dt, s, feed, on, row) y el desajuste de distancias de corte.
        """
        x0g, pitch, ov = self.sampled.x0, self.sampled.pitch, self.overscan
        cols: dict[str, list] = {k: [] for k in ("x0", "y0", "x1", "y1", "dt", "s", "feed", "on", "row", "line")}
        rapid_s = max(rapid_mm_min / 60.0, 1e-6)
        k_feed = time_factor / (self.feed / 60.0) if self.feed > 0 else 0.0
        gap = 0.0      # longitud apagada dentro de las filas (se resta del corte y se suma al desplazamiento)
        line = 0
        for p in range(self.passes):
            line += 1                                   # «M4S0»
            for k, item in enumerate(self.runs):
                if item is None:
                    continue
                starts, ends, vals = item
                y_row = self._row_y(k)
                reverse = self.bidirectional and (k % 2 == 1)
                d = -1.0 if reverse else 1.0
                a_idx, b_idx = int(starts[0]), int(ends[-1])
                xs_d, xe_d = (x0g + b_idx * pitch, x0g + a_idx * pitch) if reverse else \
                    (x0g + a_idx * pitch, x0g + b_idx * pitch)
                rx = float(self._x(xs_d - d * ov))
                sx_ = float(self._x(xs_d))
                ex_ = float(self._x(xe_d))
                on_any = bool((vals > 0).any())
                seg_len = np.abs(self._x(x0g + ends * pitch) - self._x(x0g + starts * pitch))
                off_len = float(seg_len[vals <= 0].sum())
                if on_any:
                    gap += off_len
                moves = [(x, y, rx, y_row, math.hypot(rx - x, y_row - y) / rapid_s, 0.0, 0.0, False, -1, line),
                         (rx, y_row, sx_, y_row, abs(sx_ - rx) * k_feed, 0.0, self.feed, False, -1, line + 1),
                         (sx_, y_row, ex_, y_row, abs(ex_ - sx_) * k_feed, float(vals.max()), self.feed, on_any,
                          k if p == 0 else -2, line + 2)]
                x, y = ex_, y_row
                if ov > 0:
                    ox_ = float(self._x(xe_d + d * ov))
                    moves.append((x, y, ox_, y, abs(ox_ - x) * k_feed, 0.0, self.feed, False, -1,
                                  line + 2 + len(starts)))
                    x = ox_
                line += 3 + len(starts)
                for mv in moves:
                    for key, v in zip(cols, mv):
                        cols[key].append(v)
            line += 1                                   # «M5»
        out: dict[str, Any] = {k: np.asarray(v, dtype=bool if k == "on" else
                                             (np.int64 if k in ("row", "line") else np.float64))
                               for k, v in cols.items()}
        out["gap"] = gap
        out["end"] = (x, y)
        return out

    def raster_geometry(self) -> tuple[float, float, float, float]:
        """(x_borde_columna_0, paso_x, y_borde_fila_0, paso_y) de la imagen quemada en coordenadas de trabajo."""
        ox, sx, oy, sy = self.aff
        sa = self.sampled
        return ox + sx * sa.x0, sx * sa.pitch, oy + sy * sa.y0, sy * sa.pitch

    def measure(self, x: float, y: float, rapid_s: float, time_factor: float) -> BlockMeasure:
        """Recorre el bloque como lo haría :func:`analyze` sobre sus líneas, pero por filas con NumPy."""
        x0, pitch, ov = self.sampled.x0, self.sampled.pitch, self.overscan
        feed_s = self.feed / 60.0
        k_feed = time_factor / feed_s if feed_s > 0 else 0.0
        chunks: list[np.ndarray] = []
        t = cut = travel = 0.0
        bx0 = by0 = math.inf
        bx1 = by1 = -math.inf
        lx0 = ly0 = math.inf
        lx1 = ly1 = -math.inf
        max_s = 0.0
        hull: list[Point] = []
        for _ in range(self.passes):
            chunks.append(np.array([t]))            # «M4S0»
            for k, item in enumerate(self.runs):
                if item is None:
                    continue
                starts, ends, vals = item
                y_row = self._row_y(k)
                reverse = self.bidirectional and (k % 2 == 1)
                d = -1.0 if reverse else 1.0
                a_idx, b_idx = int(starts[0]), int(ends[-1])
                xs_d, xe_d = (x0 + b_idx * pitch, x0 + a_idx * pitch) if reverse else \
                    (x0 + a_idx * pitch, x0 + b_idx * pitch)
                rx = float(self._x(xs_d - d * ov))
                sx_ = float(self._x(xs_d))
                # G0 al comienzo menos el overscan
                dist = math.hypot(rx - x, y_row - y)
                travel += dist
                t += dist / rapid_s
                bx0, bx1 = min(bx0, x, rx), max(bx1, x, rx)
                by0, by1 = min(by0, y, y_row), max(by1, y, y_row)
                t_rapid = t
                # G1 de aceleración con S0
                dist = abs(sx_ - rx)
                travel += dist
                t += dist * k_feed
                t_acc = t
                if reverse:
                    idx, sv = starts[::-1], vals[::-1]
                else:
                    idx, sv = ends, vals
                xr = self._x(x0 + idx * pitch)
                prev = np.concatenate(([sx_], xr[:-1]))
                seg = np.abs(xr - prev)
                on = sv > 0
                cut += float(seg[on].sum())
                travel += float(seg[~on].sum())
                run_t = t + np.cumsum(seg) * k_feed
                t = float(run_t[-1]) if run_t.size else t
                if on.any():
                    xs_on = np.concatenate((prev[on], xr[on]))
                    lo, hi = float(xs_on.min()), float(xs_on.max())
                    lx0, lx1 = min(lx0, lo), max(lx1, hi)
                    ly0, ly1 = min(ly0, y_row), max(ly1, y_row)
                    hull.append((lo, y_row))
                    hull.append((hi, y_row))
                    max_s = max(max_s, float(sv.max()))
                # salida con S0 (o «S0» sin movimiento)
                if ov > 0:
                    ox_ = float(self._x((xe_d) + d * ov))
                    dist = abs(ox_ - float(xr[-1]))
                    travel += dist
                    t += dist * k_feed
                    x = ox_
                else:
                    x = float(xr[-1])
                bx0 = min(bx0, x, sx_, float(xr.min()))
                bx1 = max(bx1, x, sx_, float(xr.max()))
                y = y_row
                chunks.append(np.concatenate(([t_rapid, t_acc], run_t, [t])))
            chunks.append(np.array([t]))            # «M5»
        rel = np.concatenate(chunks) if chunks else np.zeros(0)
        laser = (lx0, ly0, lx1, ly1) if lx0 != math.inf else None
        if bx0 == math.inf:
            bx0 = bx1 = x
            by0 = by1 = y
        rows_on = sum(1 for r in self.runs if r is not None)
        runs_total = sum(len(r[0]) for r in self.runs if r is not None)
        motion = self.passes * (rows_on * (2 + (1 if ov > 0 else 0)) + runs_total)
        return BlockMeasure(self.n_lines, motion, rel, cut, travel, (bx0, by0, bx1, by1), laser, hull, max_s, (x, y))


# ------------------------------------------------------------------------ trabajo
def generate_job(doc: Document, profile: DeviceProfile, wco: tuple[float, float] = (0.0, 0.0),
                 origin: JobOrigin | None = None, tol: float = FLATTEN_TOL_MM) -> GeneratedJob:
    origin = origin or JobOrigin()
    cs = CoordinateSystem.from_profile(profile)
    lines: list[str] = ["G90", "G21", "M5"]
    skipped: list[str] = []
    warnings: list[str] = []
    work_points: list[Point] = []
    spans: list[LayerSpan] = []

    entries = doc.output_shapes()
    shift = (0.0, 0.0)
    if origin.mode == "user" and entries:
        mpts = [cs.doc_to_machine(p) for _, s in entries for sp in s.subpaths for p in sp.flatten(tol)]
        if mpts:
            shift = (-min(p[0] for p in mpts), -min(p[1] for p in mpts))

    def to_work(p: Point) -> Point:
        if origin.mode == "user":
            mx, my = cs.doc_to_machine(p)
            return (mx + shift[0], my + shift[1])
        return cs.doc_to_work(p, wco)

    em = _Emitter(lines, to_work, work_points)
    by_layer: dict[str, list[Shape]] = {}
    for layer, shape in entries:
        by_layer.setdefault(layer.id, []).append(shape)

    for layer in doc.sorted_layers():
        shapes = by_layer.get(layer.id)
        if not shapes:
            continue
        start = len(lines)
        start_line = _count_lines(lines)
        lines.append(f"; capa: {layer.name}")
        lines.append("M8" if layer.air_assist else "M9")
        z_move = bool(profile.z_axis and abs(layer.z_offset_mm) > 1e-9)
        if z_move:
            lines.append(f"G0Z{fmt(layer.z_offset_mm)}")
        body_start = len(lines)
        if layer.mode in (LayerMode.FILL, LayerMode.FILL_LINE):
            produced = _emit_fill_layer(em, layer, shapes, profile, tol, warnings)
            if layer.mode == LayerMode.FILL_LINE:
                produced = _emit_line_layer(em, layer, shapes, profile, tol, warnings) or produced
        elif layer.mode == LayerMode.IMAGE:
            produced = _emit_image_layer(em, layer, shapes, profile, doc, warnings)
        else:
            produced = _emit_line_layer(em, layer, shapes, profile, tol, warnings)
        if not produced and len(lines) == body_start:
            del lines[start:]
            skipped.append(layer.name)
            continue
        if z_move:
            lines.append("G0Z0")
        if layer.pause_after:
            lines.append("M0")  # pausa de programa: el operador reanuda manualmente
        spans.append(LayerSpan(layer.id, layer.name, layer.color, layer.mode.value, start_line,
                               start_line + _count_lines(lines[start:])))
    lines += ["M5", "M9"]
    return GeneratedJob(lines, skipped, work_points, spans, warnings)


# Nombre de la Fase 1-2 (sólo Línea); hoy genera todas las capas.
generate_line_job = generate_job
