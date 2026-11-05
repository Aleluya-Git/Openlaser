"""Línea temporal de un trabajo G-code para la simulación visual.

Cada movimiento lleva su instante de inicio y fin (modelo sin aceleración,
el mismo que el análisis), potencia, avance, capa y número de línea. Las capas
se reconocen por los comentarios ``; capa: Nombre`` que escribe el generador.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from openlaser.machine.grbl_protocol import sanitize_line

_WORD = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")
_LAYER = re.compile(r"^\s*(?:;\s*capa:\s*(.+?)|\(\s*capa:\s*(.+?)\))\s*$", re.IGNORECASE)


@dataclass
class Timeline:
    x0: np.ndarray
    y0: np.ndarray
    x1: np.ndarray
    y1: np.ndarray
    t0: np.ndarray
    t1: np.ndarray
    s: np.ndarray        # potencia S
    feed: np.ndarray     # mm/min (rápidos: velocidad de rápido)
    on: np.ndarray       # bool: láser encendido
    layer: np.ndarray    # índice en ``layers`` (-1 = sin capa)
    line: np.ndarray     # número de línea (0-based) en las líneas recibidas
    layers: list[str] = field(default_factory=list)
    s_max: float = 0.0
    # Imágenes (Fase 6): cada fila de una imagen es un solo movimiento («raster» = True) y la imagen quemada
    # se dibuja aparte con :class:`RasterOverlay`. ``gap`` = tramos apagados dentro de esas filas.
    raster: np.ndarray | None = None
    overlays: list["RasterOverlay"] = field(default_factory=list)
    gap: float = 0.0

    @property
    def count(self) -> int:
        return int(self.t1.size)

    @property
    def total_time(self) -> float:
        return float(self.t1[-1]) if self.count else 0.0

    @property
    def cut_distance(self) -> float:
        d = np.hypot(self.x1 - self.x0, self.y1 - self.y0)
        return float(d[self.on].sum()) - self.gap

    @property
    def travel_distance(self) -> float:
        d = np.hypot(self.x1 - self.x0, self.y1 - self.y0)
        return float(d[~self.on].sum()) + self.gap

    @property
    def layer_changes(self) -> int:
        if self.count == 0:
            return 0
        return int(np.count_nonzero(np.diff(self.layer[self.on])) if self.on.any() else 0)

    def index_at(self, t: float) -> int:
        """Índice del movimiento en curso en el instante ``t`` (count si ya terminó)."""
        return int(np.searchsorted(self.t1, t, side="right"))

    def position_at(self, t: float) -> tuple[float, float, int]:
        """Posición interpolada (x, y) e índice del movimiento en ``t``."""
        if self.count == 0:
            return (0.0, 0.0, 0)
        i = self.index_at(t)
        if i >= self.count:
            return (float(self.x1[-1]), float(self.y1[-1]), self.count)
        t0, t1 = float(self.t0[i]), float(self.t1[i])
        f = 0.0 if t1 <= t0 else min(1.0, max(0.0, (t - t0) / (t1 - t0)))
        return (float(self.x0[i] + (self.x1[i] - self.x0[i]) * f),
                float(self.y0[i] + (self.y1[i] - self.y0[i]) * f), i)

    def time_of_move(self, i: int) -> float:
        if self.count == 0:
            return 0.0
        i = max(0, min(i, self.count - 1))
        return float(self.t0[i])


@dataclass
class RasterOverlay:
    """Imagen quemada por un bloque ráster: S por píxel y en qué movimiento se graba cada fila."""
    q: np.ndarray            # filas × columnas, S entero (0 = no se graba)
    x_edge: float            # X (trabajo) del borde de la columna 0
    x_step: float            # avance en X por columna (con signo)
    y_edge: float            # Y (trabajo) del borde de la fila 0
    y_step: float            # avance en Y por fila (con signo)
    row_move: np.ndarray     # por fila: índice del movimiento que la graba (-1 = fila vacía)
    layer: int
    feed: float


def build_timeline(lines: "list[str] | list[object]", rapid_mm_min: float = 6000.0, time_factor: float = 1.15,
                   max_moves: int = 2_000_000) -> Timeline:
    """``lines`` puede incluir bloques ráster (``generator.RasterBlock``): se añaden fila a fila."""
    xs0: list[float] = []
    ys0: list[float] = []
    xs1: list[float] = []
    ys1: list[float] = []
    ts0: list[float] = []
    ts1: list[float] = []
    ss: list[float] = []
    fs: list[float] = []
    ons: list[bool] = []
    lys: list[int] = []
    lns: list[int] = []
    layers: list[str] = []
    chunks: list[dict[str, np.ndarray]] = []
    overlays: list[RasterOverlay] = []
    gap = 0.0
    x = y = 0.0
    absolute, unit, motion, feed, s, spindle, t = True, 1.0, 0, 0.0, 0.0, "M5", 0.0
    layer = -1

    def add(nx: float, ny: float, on: bool, speed: float, line_no: int) -> None:
        nonlocal x, y, t
        dist = math.hypot(nx - x, ny - y)
        dt = dist / max(speed / 60.0, 1e-6) * (1.0 if not on and motion == 0 else time_factor)
        xs0.append(x)
        ys0.append(y)
        xs1.append(nx)
        ys1.append(ny)
        ts0.append(t)
        t += dt
        ts1.append(t)
        ss.append(s if on else 0.0)
        fs.append(speed)
        ons.append(on)
        lys.append(layer)
        lns.append(line_no)
        x, y = nx, ny

    def flush() -> None:
        if not ts1:
            return
        chunks.append({"x0": np.asarray(xs0), "y0": np.asarray(ys0), "x1": np.asarray(xs1), "y1": np.asarray(ys1),
                       "t0": np.asarray(ts0), "t1": np.asarray(ts1), "s": np.asarray(ss), "feed": np.asarray(fs),
                       "on": np.asarray(ons, bool), "layer": np.asarray(lys, np.int32),
                       "line": np.asarray(lns, np.int64), "raster": np.zeros(len(ts1), bool)})
        for lst in (xs0, ys0, xs1, ys1, ts0, ts1, ss, fs, ons, lys, lns):
            lst.clear()

    moves_before = 0
    line_no = -1
    for raw in lines:
        if not isinstance(raw, str):
            blk: Any = raw
            flush()
            tl_b = blk.timeline(x, y, rapid_mm_min, time_factor)
            n = int(tl_b["dt"].size)
            first = sum(c["t1"].size for c in chunks)
            t_end = t + np.cumsum(tl_b["dt"])
            chunks.append({"x0": tl_b["x0"], "y0": tl_b["y0"], "x1": tl_b["x1"], "y1": tl_b["y1"],
                           "t0": np.concatenate(([t], t_end[:-1])) if n else np.zeros(0), "t1": t_end,
                           "s": tl_b["s"], "feed": tl_b["feed"], "on": tl_b["on"],
                           "layer": np.full(n, layer, np.int32), "line": tl_b["line"] + line_no + 1,
                           "raster": np.ones(n, bool)})
            if n:
                t = float(t_end[-1])
            x, y = tl_b["end"]
            gap += float(tl_b["gap"])
            motion, s, spindle, feed = 1, 0.0, "M5", blk.feed
            q = getattr(blk, "q", None)
            if q is not None:
                rows = tl_b["row"]
                row_move = np.full(q.shape[0], -1, np.int64)
                hit = np.flatnonzero(rows >= 0)
                row_move[rows[hit]] = first + hit
                xe, xs, ye, ys = blk.raster_geometry()
                overlays.append(RasterOverlay(q, xe, xs, ye, ys, row_move, layer, blk.feed))
            line_no += blk.n_lines
            moves_before = first + n
            if moves_before >= max_moves:
                break
            continue
        line_no += 1
        idx = line_no
        m = _LAYER.match(raw)
        if m:
            layers.append((m.group(1) or m.group(2)).strip())
            layer = len(layers) - 1
            continue
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
                mi = int(fv)
                if mi in (3, 4, 5):
                    spindle = f"M{mi}"
                elif mi in (2, 30):
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
            elif g == 4:
                t += vals.get("P", 0.0)
        if "F" in vals:
            feed = vals["F"] * unit
        if "S" in vals:
            s = vals["S"]
        if not ("X" in vals or "Y" in vals) or any(g in (4, 10, 92, 28, 30, 53) for g in gs):
            continue
        nx = (vals["X"] * unit if absolute else x + vals["X"] * unit) if "X" in vals else x
        ny = (vals["Y"] * unit if absolute else y + vals["Y"] * unit) if "Y" in vals else y
        on = motion != 0 and spindle != "M5" and s > 0
        speed = rapid_mm_min if motion == 0 else max(feed, 1e-6)
        if motion in (2, 3) and ("I" in vals or "J" in vals):
            cx, cy = x + vals.get("I", 0.0) * unit, y + vals.get("J", 0.0) * unit
            r = math.hypot(x - cx, y - cy)
            a0 = math.atan2(y - cy, x - cx)
            sweep = math.atan2(ny - cy, nx - cx) - a0
            if motion == 2 and sweep >= 0:
                sweep -= 2 * math.pi
            if motion == 3 and sweep <= 0:
                sweep += 2 * math.pi
            steps = max(4, int(abs(sweep) * r / 0.5))
            for k in range(1, steps + 1):
                ang = a0 + sweep * k / steps
                add(cx + r * math.cos(ang), cy + r * math.sin(ang), on, speed, idx)
        else:
            add(nx, ny, on, speed, idx)
        if moves_before + len(ts1) >= max_moves:
            break
    flush()

    def cat(key: str, dtype: Any) -> np.ndarray:
        return np.concatenate([c[key] for c in chunks]).astype(dtype) if chunks else np.zeros(0, dtype)

    tl = Timeline(cat("x0", np.float64), cat("y0", np.float64), cat("x1", np.float64), cat("y1", np.float64),
                  cat("t0", np.float64), cat("t1", np.float64), cat("s", np.float64), cat("feed", np.float64),
                  cat("on", bool), cat("layer", np.int32), cat("line", np.int64), layers)
    tl.raster = cat("raster", bool)
    tl.overlays = overlays
    tl.gap = gap
    tl.s_max = float(tl.s.max()) if tl.count else 0.0
    return tl
