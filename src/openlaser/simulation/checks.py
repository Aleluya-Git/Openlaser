"""Diagnósticos previos al trabajo: geometría y movimientos problemáticos."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from openlaser.document.model import Document, LayerMode
from openlaser.i18n import tr

Point = tuple[float, float]
MAX_SEGMENTS = 60_000
MAX_REPORTED = 40


@dataclass
class Issue:
    severity: str                  # "error" | "warn" | "info"
    message: str
    shape_ids: list[str] = field(default_factory=list)
    line: int | None = None        # línea de G-code (0-based), si procede


def _outside(doc: Document) -> list[Issue]:
    out = []
    ids = []
    for _ly, s in doc.output_shapes():
        b = s.bounds()
        if b[0] < -1e-6 or b[1] < -1e-6 or b[2] > doc.width_mm + 1e-6 or b[3] > doc.height_mm + 1e-6:
            ids.append(s.id)
    if ids:
        out.append(Issue("error", tr("{n} objetos quedan fuera del área de trabajo", n=len(ids)), ids))
    return out


def _open_paths(doc: Document) -> list[Issue]:
    fill_ids, line_ids = [], []
    for ly, s in doc.output_shapes():
        if s.image is not None or all(sp.closed for sp in s.subpaths):
            continue
        (fill_ids if ly.mode in (LayerMode.FILL, LayerMode.FILL_LINE) else line_ids).append(s.id)
    out = []
    if fill_ids:
        out.append(Issue("warn", tr("{n} objetos abiertos en capas de relleno (no se rellenan)", n=len(fill_ids)),
                         fill_ids))
    if line_ids:
        out.append(Issue("info", tr("{n} trayectorias abiertas en capas de línea", n=len(line_ids)), line_ids))
    return out


def _duplicates(doc: Document) -> list[Issue]:
    seen: dict[tuple, str] = {}
    dups: list[str] = []
    for s in doc.shapes:
        if s.image is not None:
            continue
        key = (s.layer_id, tuple(tuple((round(nd.x, 3), round(nd.y, 3)) for nd in sp.nodes) for sp in s.subpaths))
        if key in seen:
            dups.append(s.id)
        else:
            seen[key] = s.id
    if not dups:
        return []
    return [Issue("warn", tr("{n} objetos duplicados (se grabarían dos veces)", n=len(dups)), dups)]


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orient(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2 = orient(c, d, a), orient(c, d, b)
    d3, d4 = orient(a, b, c), orient(a, b, d)
    return ((d1 > 1e-12 and d2 < -1e-12) or (d1 < -1e-12 and d2 > 1e-12)) and \
        ((d3 > 1e-12 and d4 < -1e-12) or (d3 < -1e-12 and d4 > 1e-12))


def find_intersections(doc: Document, tol: float = 0.2) -> list[Issue]:
    """Cruces entre contornos de la misma capa (incluidas autointersecciones)."""
    segs: list[tuple[Point, Point, str, int, int, int]] = []  # a, b, forma, subtray., índice, nº tramos
    for ly, s in doc.output_shapes():
        if s.image is not None or ly.mode == LayerMode.IMAGE:
            continue
        for si, sp in enumerate(s.subpaths):
            pts = sp.flatten(tol)
            if sp.closed and len(pts) > 2:
                pts = pts + [pts[0]]
            n = len(pts) - 1
            for k in range(n):
                segs.append((pts[k], pts[k + 1], s.id, si, k, n))
                if len(segs) > MAX_SEGMENTS:
                    return [Issue("info", tr("Demasiada geometría para buscar intersecciones"))]
    if len(segs) < 2:
        return []
    total = sum(math.dist(a, b) for a, b, *_ in segs)
    cell = max(total / len(segs) * 4, 0.5)
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (a, b, *_rest) in enumerate(segs):
        for gx in range(int(min(a[0], b[0]) // cell), int(max(a[0], b[0]) // cell) + 1):
            for gy in range(int(min(a[1], b[1]) // cell), int(max(a[1], b[1]) // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    layer_of = {s.id: s.layer_id for s in doc.shapes}
    hits: dict[tuple[str, str], Point] = {}
    tested: set[tuple[int, int]] = set()
    for cell_items in grid.values():
        for ii in range(len(cell_items)):
            i = cell_items[ii]
            a, b, sid_a, sub_a, k_a, n_a = segs[i]
            for jj in range(ii + 1, len(cell_items)):
                j = cell_items[jj]
                pair = (i, j) if i < j else (j, i)
                if pair in tested:
                    continue
                tested.add(pair)
                c, d, sid_b, sub_b, k_b, n_b = segs[j]
                if layer_of.get(sid_a) != layer_of.get(sid_b):
                    continue
                if sid_a == sid_b and sub_a == sub_b and (abs(k_a - k_b) <= 1 or abs(k_a - k_b) == n_a - 1):
                    continue  # tramos contiguos de la misma subtrayectoria
                if _segments_intersect(a, b, c, d):
                    key = (sid_a, sid_b) if sid_a <= sid_b else (sid_b, sid_a)
                    hits.setdefault(key, a)
                    if len(hits) >= MAX_REPORTED:
                        break
    out = []
    for (sa, sb), _p in hits.items():
        msg = tr("Autointersección en un contorno") if sa == sb else tr("Dos contornos de la misma capa se cruzan")
        out.append(Issue("warn", msg, sorted({sa, sb})))
    return out


def _images(doc: Document) -> list[Issue]:
    missing = [s.id for _ly, s in doc.output_shapes() if s.image is not None and s.image.resource not in doc.images]
    return [Issue("error", tr("{n} imágenes sin píxeles (archivo dañado o incompleto)", n=len(missing)), missing)] \
        if missing else []


def check_document(doc: Document) -> list[Issue]:
    return _outside(doc) + _images(doc) + _duplicates(doc) + _open_paths(doc) + find_intersections(doc)


def check_moves(timeline: object, work_limits: tuple[float, float, float, float]) -> list[Issue]:
    """Movimientos fuera de los límites (en coordenadas de trabajo)."""
    import numpy as np
    tl = timeline
    x0, y0, x1, y1 = work_limits
    xs = np.concatenate([tl.x0, tl.x1])  # type: ignore[attr-defined]
    ys = np.concatenate([tl.y0, tl.y1])  # type: ignore[attr-defined]
    if xs.size == 0:
        return []
    bad = (xs < x0 - 1e-6) | (xs > x1 + 1e-6) | (ys < y0 - 1e-6) | (ys > y1 + 1e-6)
    if not bad.any():
        return []
    n = tl.count  # type: ignore[attr-defined]
    idx = np.flatnonzero(bad[:n] | bad[n:])
    first_line = int(tl.line[idx[0]])  # type: ignore[attr-defined]
    return [Issue("error", tr("{n} movimientos fuera de los límites de la máquina (primera línea {l})",
                               n=int(idx.size), l=first_line + 1), line=first_line)]
