"""Relleno por barrido (hatch): intervalos por línea de exploración.

Cada forma se rellena con la regla par-impar (sus agujeros quedan vacíos) y
las formas de una misma capa se unen por línea, de modo que el solapamiento de
dos formas no se graba dos veces.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from openlaser.i18n import tr

Point = tuple[float, float]
Polygon = list[Point]
MAX_ROWS = 200_000


@dataclass
class HatchRow:
    y: float                                  # coordenada en el sistema girado
    intervals: list[tuple[float, float]]      # (x0, x1) ordenados, sin solapes


def _rotate(pts: list[Point], ang: float) -> list[Point]:
    c, s = math.cos(ang), math.sin(ang)
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def rotate_point(p: Point, ang: float) -> Point:
    c, s = math.cos(ang), math.sin(ang)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def _edges(polys: list[Polygon]) -> np.ndarray:
    segs = []
    for poly in polys:
        n = len(poly)
        if n < 3:
            continue
        arr = np.asarray(poly, dtype=np.float64)
        nxt = np.roll(arr, -1, axis=0)
        segs.append(np.hstack([arr, nxt]))
    if not segs:
        return np.zeros((0, 4))
    e = np.vstack(segs)
    return e[e[:, 1] != e[:, 3]]  # las aristas horizontales no cortan


def _merge(intervals: list[tuple[float, float]], min_len: float) -> list[tuple[float, float]]:
    intervals.sort()
    out: list[tuple[float, float]] = []
    for a, b in intervals:
        if out and a <= out[-1][1] + 1e-9:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return [(a, b) for a, b in out if b - a >= min_len]


def hatch(shapes: list[list[Polygon]], interval: float, angle_deg: float = 0.0,
          min_len: float = 0.0) -> list[HatchRow]:
    """``shapes``: por forma, sus polígonos (contorno y agujeros) en mm del documento.

    Devuelve las filas en el sistema girado ``-angle``; use :func:`rotate_point`
    con ``+angle`` para volver al documento.
    """
    if interval <= 0:
        raise ValueError(tr("El intervalo de relleno debe ser positivo"))
    ang = math.radians(angle_deg)
    rotated = [[_rotate(poly, -ang) for poly in polys] for polys in shapes]
    edge_sets = [_edges(polys) for polys in rotated]
    edge_sets = [e for e in edge_sets if len(e)]
    if not edge_sets:
        return []
    ymin = min(float(min(e[:, 1].min(), e[:, 3].min())) for e in edge_sets)
    ymax = max(float(max(e[:, 1].max(), e[:, 3].max())) for e in edge_sets)
    n_rows = int(math.floor((ymax - ymin) / interval))
    if n_rows > MAX_ROWS:
        raise ValueError(tr("Demasiadas líneas de relleno; aumente el intervalo"))
    rows: list[HatchRow] = []
    prepared = []
    for e in edge_sets:
        y0, y1 = e[:, 1], e[:, 3]
        lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
        slope = (e[:, 2] - e[:, 0]) / (y1 - y0)
        prepared.append((e, lo, hi, slope))
    for k in range(n_rows + 1):
        y = ymin + (k + 0.5) * interval
        if y > ymax:
            break
        intervals: list[tuple[float, float]] = []
        for e, lo, hi, slope in prepared:
            m = (lo <= y) & (hi > y)
            if not m.any():
                continue
            xs = np.sort(e[m, 0] + (y - e[m, 1]) * slope[m])
            for i in range(0, len(xs) - 1, 2):
                if xs[i + 1] > xs[i]:
                    intervals.append((float(xs[i]), float(xs[i + 1])))
        merged = _merge(intervals, min_len)
        if merged:
            rows.append(HatchRow(y, merged))
    return rows
