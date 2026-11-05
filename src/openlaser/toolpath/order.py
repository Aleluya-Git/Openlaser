"""Orden de corte: interiores antes que exteriores y desplazamientos mínimos."""
from __future__ import annotations

import math
from dataclasses import dataclass

from openlaser.geometry.path import containment_depth

Point = tuple[float, float]


@dataclass
class PathItem:
    points: list[Point]   # polilínea (sin repetir el primer punto si es cerrada)
    closed: bool
    tag: object = None


def _best_start(item: PathItem, cur: Point) -> tuple[float, list[Point]]:
    pts = item.points
    if not pts:
        return (math.inf, [])
    if item.closed:
        k = min(range(len(pts)), key=lambda i: math.dist(cur, pts[i]))
        rot = pts[k:] + pts[:k]
        return (math.dist(cur, rot[0]), rot + [rot[0]])
    d0, d1 = math.dist(cur, pts[0]), math.dist(cur, pts[-1])
    return (d0, list(pts)) if d0 <= d1 else (d1, list(reversed(pts)))


def _samples(item: PathItem) -> list[Point]:
    pts = item.points
    if not item.closed or len(pts) <= 12:
        return [pts[0], pts[-1]] if not item.closed and pts else list(pts)
    step = len(pts) / 12.0
    return [pts[int(k * step)] for k in range(12)]


class _NearestIndex:
    """Rejilla de puntos de muestra para buscar la siguiente trayectoria cercana."""

    def __init__(self, items: list[PathItem]) -> None:
        self.items = items
        self.samples = [_samples(it) for it in items]
        allp = [p for s in self.samples for p in s]
        self.alive = set(range(len(items)))
        self.x0 = min(p[0] for p in allp)
        self.y0 = min(p[1] for p in allp)
        span = max(max(p[0] for p in allp) - self.x0, max(p[1] for p in allp) - self.y0, 1e-6)
        self.cells = max(1, min(512, int(math.sqrt(len(items)) * 2)))
        self.size = span / self.cells
        self.grid: dict[tuple[int, int], set[int]] = {}
        for i, s in enumerate(self.samples):
            for p in s:
                self.grid.setdefault(self._cell(p), set()).add(i)

    def _cell(self, p: Point) -> tuple[int, int]:
        return (int((p[0] - self.x0) / self.size), int((p[1] - self.y0) / self.size))

    def pop_nearest(self, cur: Point) -> PathItem:
        cx, cy = self._cell(cur)
        best_i, best_d = -1, math.inf
        r = 0
        inside = -2 <= cx <= self.cells + 2 and -2 <= cy <= self.cells + 2
        limit = 2 * self.cells + 6 if inside else -1  # fuera de la rejilla: búsqueda lineal
        while r <= limit:
            seen: set[int] = set()
            for gx in range(cx - r, cx + r + 1):
                for gy in (range(cy - r, cy + r + 1) if gx in (cx - r, cx + r) else (cy - r, cy + r)):
                    seen |= self.grid.get((gx, gy), set())
            for i in seen & self.alive:
                d = min(math.dist(cur, p) for p in self.samples[i])
                if d < best_d:
                    best_i, best_d = i, d
            # cualquier punto fuera del anillo r está al menos a r·size de distancia
            if best_i >= 0 and best_d <= r * self.size:
                break
            r += 1
        if best_i < 0:  # no debería ocurrir; recorrido lineal de seguridad
            best_i = min(self.alive, key=lambda i: min(math.dist(cur, p) for p in self.samples[i]))
        self.alive.discard(best_i)
        return self.items[best_i]


def order_paths(items: list[PathItem], start: Point = (0.0, 0.0), inside_first: bool = True,
                optimize: bool = True) -> list[tuple[list[Point], object]]:
    """Devuelve ``(puntos a recorrer, tag)`` en orden de corte.

    * ``inside_first``: las trayectorias cerradas más anidadas se cortan antes que
      las que las contienen (evita que la pieza se suelte antes de tiempo).
    * ``optimize``: dentro de cada nivel, vecino más próximo (con índice espacial),
      eligiendo el punto de inicio de las cerradas y el sentido de las abiertas.
    """
    items = [it for it in items if it.points]
    if not items:
        return []
    if inside_first:
        depth = containment_depth([it.points if it.closed else [] for it in items])
        levels = sorted(set(depth), reverse=True)
        groups = [[it for it, d in zip(items, depth) if d == lv] for lv in levels]
    else:
        groups = [list(items)]
    out: list[tuple[list[Point], object]] = []
    cur = start
    for group in groups:
        if optimize:
            index = _NearestIndex(group)
            for _ in range(len(group)):
                item = index.pop_nearest(cur)
                _, pts = _best_start(item, cur)
                out.append((pts, item.tag))
                cur = pts[-1]
        else:
            for item in group:
                pts = item.points + ([item.points[0]] if item.closed else [])
                out.append((pts, item.tag))
                cur = pts[-1]
    return out


def travel_distance(paths: list[list[Point]], start: Point = (0.0, 0.0)) -> float:
    cur, total = start, 0.0
    for pts in paths:
        if pts:
            total += math.dist(cur, pts[0])
            cur = pts[-1]
    return total
