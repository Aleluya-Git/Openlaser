"""Análisis estático de G-code: límites, distancias, tiempo estimado y avisos.

El tiempo estimado ignora la aceleración y aplica un factor de corrección
configurable; sirve para mostrar progreso y tiempo restante, no como cifra
exacta.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from openlaser.machine.grbl_protocol import is_system_command, sanitize_line

_WORD = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")
_NUM = r"([-+]?(?:\d+\.?\d*|\.\d+))"
# Línea típica de grabado ráster: «G1X…Y…» con S opcional (millones en una fotografía).
_SIMPLE = re.compile(r"[GMSFXYZAIJKPL][-+.0-9A-Z]*$")
_FAST = re.compile(rf"G([01])X{_NUM}Y{_NUM}(?:S{_NUM})?$")


@dataclass
class Analysis:
    line_count: int = 0
    motion_lines: int = 0
    cut_distance: float = 0.0
    travel_distance: float = 0.0
    estimated_seconds: float = 0.0
    cumulative_seconds: list[float] = field(default_factory=list)
    bounds_all: tuple[float, float, float, float] | None = None
    bounds_laser: tuple[float, float, float, float] | None = None
    max_s: float = 0.0
    max_feed: float = 0.0
    uses_inches: bool = False
    relative_moves: bool = False
    has_system_commands: bool = False
    unsupported: list[tuple[int, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    frame_points: list[tuple[float, float]] = field(default_factory=list)


def _grow(b: tuple[float, float, float, float] | None, x: float, y: float) -> tuple[float, float, float, float]:
    if b is None:
        return (x, y, x, y)
    return (min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y))


def analyze(lines: "list[str] | list[object]", rapid_mm_min: float = 6000.0, time_factor: float = 1.15,
            clean: bool = False) -> Analysis:
    """``clean=True``: las líneas ya pasaron por :func:`prepare_lines` (no se vuelven a limpiar)."""
    from array import array

    from openlaser.i18n import tr

    a = Analysis()
    x = y = 0.0
    absolute = True
    unit = 1.0
    motion = 0
    feed = 0.0
    s = 0.0
    spindle = "M5"
    t = 0.0
    hull_x: array = array("d")
    hull_y: array = array("d")
    cum = a.cumulative_seconds
    rapid_s = max(rapid_mm_min / 60.0, 1e-6)
    fast_match = _FAST.match
    hypot = math.hypot
    inf = float("inf")
    # límites acumulados en variables locales (más rápido que tuplas); se vuelcan al final
    ax0 = ay0 = lx0 = ly0 = inf
    ax1 = ay1 = lx1 = ly1 = -inf
    for idx, raw in enumerate(lines):
        if not isinstance(raw, str):
            # Bloque ráster perezoso (generator.RasterBlock): se mide con NumPy sin generar su texto.
            blk: Any = raw
            m = blk.measure(x, y, rapid_s, time_factor)
            cum.extend((m.rel_times + t).tolist())
            t += float(m.rel_times[-1]) if m.rel_times.size else 0.0
            a.line_count += m.n_lines
            a.motion_lines += m.motion_lines
            a.cut_distance += m.cut
            a.travel_distance += m.travel
            bx0, by0, bx1, by1 = m.bounds_all
            ax0, ay0, ax1, ay1 = min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)
            if m.bounds_laser is not None:
                bx0, by0, bx1, by1 = m.bounds_laser
                lx0, ly0, lx1, ly1 = min(lx0, bx0), min(ly0, by0), max(lx1, bx1), max(ly1, by1)
            for hx, hy in m.hull:
                hull_x.append(hx)
                hull_y.append(hy)
            a.max_s = max(a.max_s, m.max_s)
            feed = blk.feed
            a.max_feed = max(a.max_feed, feed)
            x, y = m.end
            motion, s, spindle = 1, 0.0, "M5"
            continue
        line = raw if clean else sanitize_line(raw)
        if not line:
            cum.append(t)
            continue
        a.line_count += 1
        fm = fast_match(line) if absolute else None
        if fm is not None:
            motion = 1 if fm.group(1) == "1" else 0
            nx, ny = float(fm.group(2)) * unit, float(fm.group(3)) * unit
            sv = fm.group(4)
            if sv is not None:
                s = float(sv)
                if s > a.max_s:
                    a.max_s = s
            dist = hypot(nx - x, ny - y)
            a.motion_lines += 1
            if x < ax0: ax0 = x  # noqa: E701
            if x > ax1: ax1 = x  # noqa: E701
            if y < ay0: ay0 = y  # noqa: E701
            if y > ay1: ay1 = y  # noqa: E701
            if nx < ax0: ax0 = nx  # noqa: E701
            if nx > ax1: ax1 = nx  # noqa: E701
            if ny < ay0: ay0 = ny  # noqa: E701
            if ny > ay1: ay1 = ny  # noqa: E701
            if motion == 0:
                a.travel_distance += dist
                t += dist / rapid_s
            else:
                if spindle != "M5" and s > 0:
                    if x < lx0: lx0 = x  # noqa: E701
                    if x > lx1: lx1 = x  # noqa: E701
                    if y < ly0: ly0 = y  # noqa: E701
                    if y > ly1: ly1 = y  # noqa: E701
                    if nx < lx0: lx0 = nx  # noqa: E701
                    if nx > lx1: lx1 = nx  # noqa: E701
                    if ny < ly0: ly0 = ny  # noqa: E701
                    if ny > ly1: ly1 = ny  # noqa: E701
                    hull_x.append(x)
                    hull_y.append(y)
                    hull_x.append(nx)
                    hull_y.append(ny)
                    a.cut_distance += dist
                else:
                    a.travel_distance += dist
                if feed <= 0:
                    a.warnings.append(tr("Línea {n}: movimiento sin avance F", n=idx + 1))
                else:
                    t += dist / (feed / 60.0) * time_factor
            x, y = nx, ny
            cum.append(t)
            continue
        if line.startswith("$"):
            if is_system_command(line):
                a.has_system_commands = True
            a.cumulative_seconds.append(t)
            continue
        words = _WORD.findall(line)
        if "".join(k + v for k, v in words) != line:
            a.unsupported.append((idx + 1, raw.strip()))
        vals: dict[str, float] = {}
        gs: list[float] = []
        ms: list[int] = []
        for k, v in words:
            fv = float(v)
            if k == "G":
                gs.append(fv)
            elif k == "M":
                ms.append(int(fv))
            else:
                vals[k] = fv
        for g in gs:
            if g == 20:
                unit, a.uses_inches = 25.4, True
            elif g == 21:
                unit = 1.0
            elif g == 90:
                absolute = True
            elif g == 91:
                absolute, a.relative_moves = False, True
            elif g in (0, 1, 2, 3):
                motion = int(g)
            elif g == 4:
                t += vals.get("P", 0.0)
        for m in ms:
            if m in (3, 4, 5):
                spindle = f"M{m}"
            elif m in (2, 30):
                spindle = "M5"
        if "F" in vals:
            feed = vals["F"] * unit
            a.max_feed = max(a.max_feed, feed)
        if "S" in vals:
            s = vals["S"]
            a.max_s = max(a.max_s, s)
        if any(k in vals for k in ("X", "Y")) and not any(g in (4, 10, 92, 28, 30, 53) for g in gs):
            nx = (vals["X"] * unit if absolute else x + vals["X"] * unit) if "X" in vals else x
            ny = (vals["Y"] * unit if absolute else y + vals["Y"] * unit) if "Y" in vals else y
            dist = math.hypot(nx - x, ny - y)
            if motion in (2, 3) and ("I" in vals or "J" in vals):
                cx, cy = x + vals.get("I", 0.0) * unit, y + vals.get("J", 0.0) * unit
                r = math.hypot(x - cx, y - cy)
                a0 = math.atan2(y - cy, x - cx)
                a1 = math.atan2(ny - cy, nx - cx)
                sweep = a1 - a0
                if motion == 2 and sweep >= 0:
                    sweep -= 2 * math.pi
                if motion == 3 and sweep <= 0:
                    sweep += 2 * math.pi
                dist = abs(sweep) * r
                laser_on_arc = spindle != "M5" and s > 0
                for i in range(1, 17):
                    ang = a0 + sweep * i / 16
                    px, py = cx + r * math.cos(ang), cy + r * math.sin(ang)
                    ax0, ay0, ax1, ay1 = min(ax0, px), min(ay0, py), max(ax1, px), max(ay1, py)
                    if laser_on_arc:
                        lx0, ly0, lx1, ly1 = min(lx0, px), min(ly0, py), max(lx1, px), max(ly1, py)
                        hull_x.append(px)
                        hull_y.append(py)
            laser_on = motion != 0 and spindle != "M5" and s > 0
            a.motion_lines += 1
            ax0, ay0 = min(ax0, x, nx), min(ay0, y, ny)
            ax1, ay1 = max(ax1, x, nx), max(ay1, y, ny)
            if laser_on:
                lx0, ly0 = min(lx0, x, nx), min(ly0, y, ny)
                lx1, ly1 = max(lx1, x, nx), max(ly1, y, ny)
                hull_x.extend((x, nx))
                hull_y.extend((y, ny))
                a.cut_distance += dist
            if motion == 0:
                a.travel_distance += dist
                t += dist / max(rapid_mm_min / 60.0, 1e-6)
            else:
                if not laser_on:
                    a.travel_distance += dist
                if feed <= 0:
                    a.warnings.append(tr("Línea {n}: movimiento sin avance F", n=idx + 1))
                else:
                    t += dist / (feed / 60.0) * time_factor
            x, y = nx, ny
        cum.append(t)
    a.estimated_seconds = t
    if ax0 != inf:
        a.bounds_all = (ax0, ay0, ax1, ay1)
    if lx0 != inf:
        a.bounds_laser = (lx0, ly0, lx1, ly1)
    if a.uses_inches:
        a.warnings.append(tr("El archivo usa pulgadas (G20)."))
    if a.relative_moves:
        a.warnings.append(tr("El archivo usa coordenadas relativas (G91); los límites son aproximados."))
    if a.unsupported:
        a.warnings.append(tr("{n} líneas con formato no reconocido.", n=len(a.unsupported)))
    if hull_x:
        a.frame_points = _hull_from_arrays(hull_x, hull_y)
    return a


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _hull_from_arrays(xs: "object", ys: "object") -> list[tuple[float, float]]:
    """Envolvente convexa de muchos puntos: sólo pueden ser vértices el Y mínimo y máximo de cada X."""
    import numpy as np

    from openlaser.geometry.coords import convex_hull
    px = np.frombuffer(xs, dtype=np.float64)  # type: ignore[call-overload]
    py = np.frombuffer(ys, dtype=np.float64)  # type: ignore[call-overload]
    if px.size > 2000:
        order = np.lexsort((py, px))
        px, py = px[order], py[order]
        first = np.concatenate(([True], px[1:] != px[:-1]))
        last = np.concatenate((px[1:] != px[:-1], [True]))
        keep = first | last
        px, py = px[keep], py[keep]
    return convex_hull(list(zip(px.tolist(), py.tolist())))


def prepare_lines(lines: list[str]) -> list[str]:
    """Limpia comentarios/espacios y descarta líneas vacías (alineado con el envío)."""
    out = []
    simple = _SIMPLE.match
    for ln in lines:
        if simple(ln):  # ya limpia (G-code generado): sin comentarios, espacios ni minúsculas
            out.append(ln)
            continue
        clean = sanitize_line(ln)
        if clean:
            out.append(clean)
    return out


Segment = tuple[float, float, float, float, bool]


def toolpath_segments(lines: list[str], max_segments: int = 200_000) -> list[Segment]:
    """Segmentos (x0, y0, x1, y1, láser) en coordenadas de trabajo para la vista previa."""
    segs: list[Segment] = []
    x = y = 0.0
    absolute, unit, motion, s, spindle = True, 1.0, 0, 0.0, "M5"
    for raw in lines:
        line = sanitize_line(raw)
        if not line or line.startswith("$"):
            continue
        vals: dict[str, float] = {}
        gs: list[float] = []
        for k, v in _WORD.findall(line):
            fv = float(v)
            if k == "G":
                gs.append(fv)
            elif k == "M":
                if int(fv) in (3, 4, 5):
                    spindle = f"M{int(fv)}"
                elif int(fv) in (2, 30):
                    spindle = "M5"
            else:
                vals[k] = fv
        for g in gs:
            if g == 20:
                unit = 25.4
            elif g == 21:
                unit = 1.0
            elif g == 90:
                absolute = True
            elif g == 91:
                absolute = False
            elif g in (0, 1, 2, 3):
                motion = int(g)
        if "S" in vals:
            s = vals["S"]
        if not ("X" in vals or "Y" in vals) or any(g in (4, 10, 92, 28, 30, 53) for g in gs):
            continue
        nx = (vals["X"] * unit if absolute else x + vals["X"] * unit) if "X" in vals else x
        ny = (vals["Y"] * unit if absolute else y + vals["Y"] * unit) if "Y" in vals else y
        on = motion != 0 and spindle != "M5" and s > 0
        segs.append((x, y, nx, ny, on))
        if len(segs) >= max_segments:
            break
        x, y = nx, ny
    return segs
