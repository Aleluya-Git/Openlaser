"""Vectorización de imágenes: umbral → limpieza → contornos → curvas.

1. Umbral manual u Otsu (automático); opcionalmente invertido.
2. Supresión de ruido con apertura y cierre morfológicos.
3. Contornos por *marching squares* sobre los centros de píxel (contornos
   cerrados y orientados: exteriores y agujeros con sentido opuesto).
4. Simplificación (Ramer-Douglas-Peucker), detección de esquinas, suavizado
   de los vértices que no son esquina y ajuste de curvas Bézier (Catmull-Rom).

Las coordenadas de salida son píxeles de la imagen recibida: el origen es la
esquina superior izquierda del primer píxel y cada píxel mide 1×1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from openlaser.geometry import path as gp
from openlaser.image_processing.raster import otsu_threshold

Point = tuple[float, float]


@dataclass(frozen=True)
class TraceSettings:
    threshold: int = 128         # grises < umbral = primer plano (oscuro)
    auto_threshold: bool = True  # Otsu
    invert: bool = False         # vectorizar las zonas claras
    noise: int = 1               # radio (px) de apertura/cierre morfológico; 0 = sin limpieza
    min_area_px: float = 12.0    # se descartan contornos con menos área (px²)
    smoothing: int = 2           # iteraciones de suavizado
    tolerance_px: float = 0.8    # tolerancia de simplificación
    corner_deg: float = 55.0     # giro mínimo para considerar esquina
    curves: bool = True          # ajustar curvas Bézier (si no, polilíneas)
    outer_only: bool = False     # sólo contornos exteriores (sin agujeros ni islas interiores)


@dataclass
class TraceResult:
    subpaths: list[gp.SubPath] = field(default_factory=list)
    threshold: int = 128
    mask: np.ndarray | None = None
    outer: int = 0
    holes: int = 0


# ------------------------------------------------------------------- morfología
def _shift_or(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1, mode="constant", constant_values=False)
    out = m.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dy:
                out |= p[1 + dy:1 + dy + m.shape[0], 1 + dx:1 + dx + m.shape[1]]
    return out


def _shift_and(m: np.ndarray) -> np.ndarray:
    p = np.pad(m, 1, mode="constant", constant_values=False)
    out = m.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx or dy:
                out &= p[1 + dy:1 + dy + m.shape[0], 1 + dx:1 + dx + m.shape[1]]
    return out


def dilate(m: np.ndarray, r: int) -> np.ndarray:
    for _ in range(r):
        m = _shift_or(m)
    return m


def erode(m: np.ndarray, r: int) -> np.ndarray:
    for _ in range(r):
        m = _shift_and(m)
    return m


def denoise(mask: np.ndarray, r: int) -> np.ndarray:
    """Apertura (quita motas) y cierre (tapa poros) de radio ``r``."""
    if r <= 0:
        return mask
    opened = dilate(erode(mask, r), r)
    return erode(dilate(opened, r), r)


def make_mask(gray_u8: np.ndarray, ts: TraceSettings) -> tuple[np.ndarray, int]:
    thr = otsu_threshold(gray_u8) if ts.auto_threshold else int(ts.threshold)
    mask = gray_u8 < thr
    if ts.invert:
        mask = ~mask
    return denoise(mask, int(ts.noise)), thr


# --------------------------------------------------------------- marching squares
# Esquinas de la celda: tl=8, tr=4, br=2, bl=1. Aristas: T, R, B, L.
_EDGE_CORNERS = {"T": ("tl", "tr"), "R": ("tr", "br"), "B": ("br", "bl"), "L": ("bl", "tl")}
_CORNER_POS = {"tl": (0.0, 0.0), "tr": (1.0, 0.0), "br": (1.0, 1.0), "bl": (0.0, 1.0)}
_EDGE_POS = {"T": (0.5, 0.0), "R": (1.0, 0.5), "B": (0.5, 1.0), "L": (0.0, 0.5)}
_BITS = {"tl": 8, "tr": 4, "br": 2, "bl": 1}


def _orient(a: str, b: str, fg: list[str]) -> tuple[str, str]:
    """Ordena el segmento a→b para que el primer plano quede a la derecha (Y hacia abajo)."""
    pa, pb = _EDGE_POS[a], _EDGE_POS[b]
    mx, my = (pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2
    cx = sum(_CORNER_POS[c][0] for c in fg) / len(fg) - mx
    cy = sum(_CORNER_POS[c][1] for c in fg) / len(fg) - my
    dx, dy = pb[0] - pa[0], pb[1] - pa[1]
    return (a, b) if (-dy * cx + dx * cy) > 0 else (b, a)


def _build_table() -> dict[int, list[tuple[str, str]]]:
    table: dict[int, list[tuple[str, str]]] = {}
    for case in range(1, 15):
        fg = [c for c, bit in _BITS.items() if case & bit]
        if case in (5, 10):  # puntos de silla: esquinas diagonales separadas
            segs = []
            for c in fg:
                edges = [e for e, (p, q) in _EDGE_CORNERS.items() if c in (p, q)]
                segs.append(_orient(edges[0], edges[1], [c]))
            table[case] = segs
            continue
        crossing = [e for e, (p, q) in _EDGE_CORNERS.items() if bool(case & _BITS[p]) != bool(case & _BITS[q])]
        table[case] = [_orient(crossing[0], crossing[1], fg)]
    return table


_TABLE = _build_table()
_EDGE_KEY = {"T": (1, 0), "R": (2, 1), "B": (1, 2), "L": (0, 1)}  # desplazamiento en coordenadas ×2


def contours(mask: np.ndarray) -> list[list[Point]]:
    """Contornos cerrados (coordenadas de píxel) de una máscara booleana."""
    h, w = mask.shape
    p = np.pad(mask.astype(np.uint8), 1, mode="constant")
    idx = (p[:-1, :-1] * 8 + p[:-1, 1:] * 4 + p[1:, 1:] * 2 + p[1:, :-1]).astype(np.int64)
    big = 2 * max(w, h) + 8  # mayor que cualquier coordenada ×2: clave única X·big + Y
    starts: list[np.ndarray] = []
    ends: list[np.ndarray] = []
    for case, segs in _TABLE.items():
        rr, cc = np.nonzero(idx == case)
        if rr.size == 0:
            continue
        for a, b in segs:
            ax, ay = _EDGE_KEY[a]
            bx, by = _EDGE_KEY[b]
            starts.append((2 * cc + ax) * big + (2 * rr + ay))
            ends.append((2 * cc + bx) * big + (2 * rr + by))
    if not starts:
        return []
    nxt = dict(zip(np.concatenate(starts).tolist(), np.concatenate(ends).tolist()))
    loops: list[list[Point]] = []
    visited: set[int] = set()
    for start in nxt:
        if start in visited:
            continue
        loop_keys = []
        k = start
        while k not in visited:
            visited.add(k)
            loop_keys.append(k)
            k = nxt.get(k, start)
            if k == start:
                break
        if len(loop_keys) >= 3:
            # clave → coordenadas: índice ×2 en la rejilla con relleno; centro de píxel = índice - 0.5
            loops.append([((key // big) / 2.0 - 0.5, (key % big) / 2.0 - 0.5) for key in loop_keys])
    return loops


# ------------------------------------------------------------------ geometría
def _remove_collinear(pts: list[Point]) -> list[Point]:
    out: list[Point] = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-9:
            out.append(b)
    return out if len(out) >= 3 else pts


def smooth_closed(pts: list[Point], iterations: int, fixed: list[bool] | None = None) -> list[Point]:
    """Promedio ponderado con los vecinos; los vértices ``fixed`` (esquinas) no se mueven."""
    for _ in range(iterations):
        n = len(pts)
        pts = [pts[i] if fixed and fixed[i] else
               ((pts[i - 1][0] + 2 * pts[i][0] + pts[(i + 1) % n][0]) / 4,
                (pts[i - 1][1] + 2 * pts[i][1] + pts[(i + 1) % n][1]) / 4) for i in range(n)]
    return pts


def corner_flags(pts: list[Point], corner_deg: float) -> list[bool]:
    corner_rad = math.radians(corner_deg)
    n = len(pts)
    flags = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        a1 = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        a2 = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
        flags.append(abs((a2 - a1 + math.pi) % (2 * math.pi) - math.pi) >= corner_rad)
    return flags


def _rdp_closed(pts: list[Point], tol: float) -> list[Point]:
    if len(pts) < 4 or tol <= 0:
        return pts
    # se parte por el punto más alejado del primero para no fijar un vértice arbitrario
    far = max(range(len(pts)), key=lambda i: (pts[i][0] - pts[0][0]) ** 2 + (pts[i][1] - pts[0][1]) ** 2)
    a = gp.rdp(pts[:far + 1], tol)
    b = gp.rdp(pts[far:] + [pts[0]], tol)
    out = a[:-1] + b[:-1]
    return out if len(out) >= 3 else pts


def fit_curve(pts: list[Point], corners: list[bool], curves: bool) -> gp.SubPath:
    """Polígono cerrado → subtrayectoria con Bézier (Catmull-Rom) y esquinas vivas."""
    n = len(pts)
    if not curves or n < 4:
        return gp.polyline(pts, True)
    nodes: list[gp.Node] = []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        if corners[i]:
            nodes.append(gp.Node(p1[0], p1[1]))
            continue
        tx, ty = (p2[0] - p0[0]) / 6.0, (p2[1] - p0[1]) / 6.0
        # los manejadores no deben superar un tercio de cada tramo
        l_in = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        l_out = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        tl = math.hypot(tx, ty)
        k_in = min(1.0, (l_in / 3) / tl) if tl > 1e-12 else 0.0
        k_out = min(1.0, (l_out / 3) / tl) if tl > 1e-12 else 0.0
        nodes.append(gp.Node(p1[0], p1[1], (p1[0] - tx * k_in, p1[1] - ty * k_in),
                             (p1[0] + tx * k_out, p1[1] + ty * k_out)))
    return gp.SubPath(tuple(nodes), True)


def trace(gray_u8: np.ndarray, ts: TraceSettings) -> TraceResult:
    mask, thr = make_mask(gray_u8, ts)
    loops = contours(mask)
    kept: list[list[Point]] = []
    for loop in loops:
        if abs(gp.polygon_area(loop)) < ts.min_area_px:
            continue
        kept.append(loop)
    depth = gp.containment_depth(kept) if kept else []
    result = TraceResult(threshold=thr, mask=mask)
    for loop, d in zip(kept, depth):
        if ts.outer_only and d > 0:
            continue
        if d % 2 == 0:
            result.outer += 1
        else:
            result.holes += 1
        pts = _rdp_closed(_remove_collinear(loop), ts.tolerance_px)
        if len(pts) < 3:
            continue
        corners = corner_flags(pts, ts.corner_deg)
        pts = smooth_closed(pts, int(ts.smoothing), corners)
        result.subpaths.append(fit_curve(pts, corners, ts.curves))
    return result
