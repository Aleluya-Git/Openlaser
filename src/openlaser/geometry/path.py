"""Trayectorias vectoriales inmutables con segmentos rectos y Bézier cúbicos.

Un :class:`SubPath` es una secuencia de :class:`Node`. El tramo entre el nodo
``i`` y el ``i+1`` es recto si ninguno tiene manejador hacia el otro
(``cout`` del primero y ``cin`` del segundo); en otro caso es una cúbica.
Las cuadráticas y los arcos se convierten a cúbicas al importarlos.

Todas las estructuras son inmutables: cualquier edición devuelve objetos
nuevos, lo que hace que Deshacer/Rehacer sea barato (se guardan referencias).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Iterable, Iterator, Sequence

from openlaser.geometry.affine import Affine

Point = tuple[float, float]
KAPPA = 0.5522847498307936  # aproximación de un cuarto de círculo con una cúbica


@dataclass(frozen=True)
class Node:
    x: float
    y: float
    cin: Point | None = None   # manejador de entrada (absoluto)
    cout: Point | None = None  # manejador de salida (absoluto)

    @property
    def p(self) -> Point:
        return (self.x, self.y)

    def transformed(self, m: Affine) -> "Node":
        x, y = m.apply((self.x, self.y))
        return Node(x, y, m.apply(self.cin) if self.cin else None, m.apply(self.cout) if self.cout else None)

    def moved(self, dx: float, dy: float) -> "Node":
        return Node(self.x + dx, self.y + dy,
                    (self.cin[0] + dx, self.cin[1] + dy) if self.cin else None,
                    (self.cout[0] + dx, self.cout[1] + dy) if self.cout else None)

    def to_list(self) -> list:
        out: list = [round(self.x, 6), round(self.y, 6)]
        if self.cin or self.cout:
            out.append([round(v, 6) for v in self.cin] if self.cin else None)
            out.append([round(v, 6) for v in self.cout] if self.cout else None)
        return out

    @classmethod
    def from_list(cls, data: Sequence) -> "Node":
        cin = tuple(data[2]) if len(data) > 2 and data[2] is not None else None
        cout = tuple(data[3]) if len(data) > 3 and data[3] is not None else None
        return cls(float(data[0]), float(data[1]), cin, cout)  # type: ignore[arg-type]


Segment = tuple[Point, Point | None, Point | None, Point]


@dataclass(frozen=True)
class SubPath:
    nodes: tuple[Node, ...]
    closed: bool = False

    # --- recorrido ---------------------------------------------------------------
    def segments(self) -> Iterator[Segment]:
        n = len(self.nodes)
        count = n if self.closed else n - 1
        for i in range(max(0, count)):
            a = self.nodes[i]
            b = self.nodes[(i + 1) % n]
            if a.cout is None and b.cin is None:
                yield (a.p, None, None, b.p)
            else:
                yield (a.p, a.cout or a.p, b.cin or b.p, b.p)

    def is_curved(self) -> bool:
        return any(nd.cin or nd.cout for nd in self.nodes)

    def flatten(self, tol: float = 0.05) -> list[Point]:
        """Polilínea equivalente (sin repetir el primer punto si es cerrada)."""
        if not self.nodes:
            return []
        pts: list[Point] = [self.nodes[0].p]
        for p0, c1, c2, p3 in self.segments():
            if c1 is None or c2 is None:
                pts.append(p3)
            else:
                pts.extend(_flatten_cubic(p0, c1, c2, p3, tol))
        if self.closed and len(pts) > 1 and _close(pts[0], pts[-1]):
            pts.pop()
        return pts

    def flatten_closed(self, tol: float = 0.05) -> list[Point]:
        """Polilínea para recorrer: si es cerrada repite el primer punto al final."""
        pts = self.flatten(tol)
        if self.closed and pts:
            pts = pts + [pts[0]]
        return pts

    # --- transformaciones ---------------------------------------------------------
    def transformed(self, m: Affine) -> "SubPath":
        return SubPath(tuple(nd.transformed(m) for nd in self.nodes), self.closed)

    def reversed(self) -> "SubPath":
        nodes = [Node(nd.x, nd.y, nd.cout, nd.cin) for nd in reversed(self.nodes)]
        if self.closed and nodes:
            nodes = nodes[-1:] + nodes[:-1]
        return SubPath(tuple(nodes), self.closed)

    def with_closed(self, closed: bool) -> "SubPath":
        return replace(self, closed=closed)

    # --- medidas ------------------------------------------------------------------
    def length(self, tol: float = 0.05) -> float:
        pts = self.flatten_closed(tol)
        return sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))

    def signed_area(self, tol: float = 0.02) -> float:
        return polygon_area(self.flatten(tol))

    def to_dict(self) -> dict:
        return {"closed": self.closed, "nodes": [nd.to_list() for nd in self.nodes]}

    @classmethod
    def from_dict(cls, d: dict) -> "SubPath":
        return cls(tuple(Node.from_list(n) for n in d.get("nodes", [])), bool(d.get("closed", False)))


def _close(a: Point, b: Point, eps: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def _flatten_cubic(p0: Point, c1: Point, c2: Point, p3: Point, tol: float) -> list[Point]:
    # número de pasos según la desviación máxima de los puntos de control
    dd = max(math.hypot(p0[0] - 2 * c1[0] + c2[0], p0[1] - 2 * c1[1] + c2[1]),
             math.hypot(c1[0] - 2 * c2[0] + p3[0], c1[1] - 2 * c2[1] + p3[1]))
    steps = max(2, min(1000, int(math.ceil(math.sqrt(0.75 * dd / max(tol, 1e-6))))))
    out: list[Point] = []
    for i in range(1, steps + 1):
        out.append(cubic_point(p0, c1, c2, p3, i / steps))
    return out


def cubic_point(p0: Point, c1: Point, c2: Point, p3: Point, t: float) -> Point:
    mt = 1 - t
    a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
    return (a * p0[0] + b * c1[0] + c * c2[0] + d * p3[0], a * p0[1] + b * c1[1] + c * c2[1] + d * p3[1])


def polygon_area(pts: Sequence[Point]) -> float:
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def bounds_of(subpaths: Iterable[SubPath], tol: float = 0.2) -> tuple[float, float, float, float] | None:
    xs: list[float] = []
    ys: list[float] = []
    for sp in subpaths:
        for x, y in sp.flatten(tol):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# --- construcción ------------------------------------------------------------------
def polyline(points: Sequence[Point], closed: bool = False) -> SubPath:
    return SubPath(tuple(Node(float(x), float(y)) for x, y in points), closed)


def rectangle(x: float, y: float, w: float, h: float, radius: float = 0.0) -> SubPath:
    if w < 0:
        x, w = x + w, -w
    if h < 0:
        y, h = y + h, -h
    r = max(0.0, min(radius, w / 2, h / 2))
    if r <= 1e-9:
        return polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)
    k = r * KAPPA
    x0, x1, y0, y1 = x, x + w, y, y + h
    nodes = [
        Node(x0 + r, y0, cin=(x0 + r - k, y0)), Node(x1 - r, y0, cout=(x1 - r + k, y0)),
        Node(x1, y0 + r, cin=(x1, y0 + r - k)), Node(x1, y1 - r, cout=(x1, y1 - r + k)),
        Node(x1 - r, y1, cin=(x1 - r + k, y1)), Node(x0 + r, y1, cout=(x0 + r - k, y1)),
        Node(x0, y1 - r, cin=(x0, y1 - r + k)), Node(x0, y0 + r, cout=(x0, y0 + r - k)),
    ]
    return SubPath(tuple(nodes), True)


def ellipse(cx: float, cy: float, rx: float, ry: float) -> SubPath:
    rx, ry = abs(rx), abs(ry)
    kx, ky = rx * KAPPA, ry * KAPPA
    nodes = (
        Node(cx + rx, cy, cin=(cx + rx, cy - ky), cout=(cx + rx, cy + ky)),
        Node(cx, cy + ry, cin=(cx + kx, cy + ry), cout=(cx - kx, cy + ry)),
        Node(cx - rx, cy, cin=(cx - rx, cy + ky), cout=(cx - rx, cy - ky)),
        Node(cx, cy - ry, cin=(cx - kx, cy - ry), cout=(cx + kx, cy - ry)),
    )
    return SubPath(nodes, True)


def regular_polygon(cx: float, cy: float, r: float, sides: int, rotation_deg: float = -90.0) -> SubPath:
    sides = max(3, int(sides))
    a0 = math.radians(rotation_deg)
    return polyline([(cx + r * math.cos(a0 + 2 * math.pi * i / sides), cy + r * math.sin(a0 + 2 * math.pi * i / sides))
                     for i in range(sides)], True)


def star(cx: float, cy: float, r_out: float, r_in: float, points: int = 5, rotation_deg: float = -90.0) -> SubPath:
    points = max(3, int(points))
    a0 = math.radians(rotation_deg)
    pts = []
    for i in range(2 * points):
        r = r_out if i % 2 == 0 else r_in
        a = a0 + math.pi * i / points
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return polyline(pts, True)


def quad_to_cubic(p0: Point, q: Point, p3: Point) -> tuple[Point, Point]:
    c1 = (p0[0] + 2 / 3 * (q[0] - p0[0]), p0[1] + 2 / 3 * (q[1] - p0[1]))
    c2 = (p3[0] + 2 / 3 * (q[0] - p3[0]), p3[1] + 2 / 3 * (q[1] - p3[1]))
    return c1, c2


def arc_to_cubics(p0: Point, rx: float, ry: float, phi_deg: float, large: bool, sweep: bool,
                  p1: Point) -> list[tuple[Point, Point, Point]]:
    """Arco elíptico SVG (parametrización por extremos) a cúbicas ``(c1, c2, fin)``."""
    if _close(p0, p1):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12:
        return [(p0, p1, p1)]
    phi = math.radians(phi_deg % 360)
    cph, sph = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1p = cph * dx + sph * dy
    y1p = -sph * dx + cph * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cph * cxp - sph * cyp + (p0[0] + p1[0]) / 2
    cy = sph * cxp + cph * cyp + (p0[1] + p1[1]) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2) - 1e-9)))
    step = dt / n
    out: list[tuple[Point, Point, Point]] = []
    alpha = 4 / 3 * math.tan(step / 4)

    def ell(t: float) -> Point:
        return (cx + rx * math.cos(t) * cph - ry * math.sin(t) * sph, cy + rx * math.cos(t) * sph + ry * math.sin(t) * cph)

    def deriv(t: float) -> Point:
        return (-rx * math.sin(t) * cph - ry * math.cos(t) * sph, -rx * math.sin(t) * sph + ry * math.cos(t) * cph)

    t = t1
    for i in range(n):
        a, b = t, t + step
        pa, pb = ell(a), ell(b)
        da, db = deriv(a), deriv(b)
        c1 = (pa[0] + alpha * da[0], pa[1] + alpha * da[1])
        c2 = (pb[0] - alpha * db[0], pb[1] - alpha * db[1])
        end = p1 if i == n - 1 else pb
        out.append((c1, c2, end))
        t = b
    return out


class PathBuilder:
    """Construye subtrayectorias a partir de órdenes tipo SVG (en coordenadas absolutas)."""

    def __init__(self) -> None:
        self.subpaths: list[SubPath] = []
        self._nodes: list[Node] = []

    @property
    def current(self) -> Point | None:
        return self._nodes[-1].p if self._nodes else None

    def move_to(self, p: Point) -> None:
        self._flush(False)
        self._nodes = [Node(p[0], p[1])]

    def line_to(self, p: Point) -> None:
        if not self._nodes:
            self.move_to(p)
            return
        if _close(self._nodes[-1].p, p):
            return
        self._nodes.append(Node(p[0], p[1]))

    def cubic_to(self, c1: Point, c2: Point, p: Point) -> None:
        if not self._nodes:
            self.move_to(c1)
        last = self._nodes[-1]
        self._nodes[-1] = Node(last.x, last.y, last.cin, c1)
        self._nodes.append(Node(p[0], p[1], cin=c2))

    def close(self) -> None:
        if len(self._nodes) > 1:
            first, last = self._nodes[0], self._nodes[-1]
            if _close(first.p, last.p, 1e-7):
                # el último nodo coincide con el primero: fusionar manejadores
                self._nodes[0] = Node(first.x, first.y, last.cin, first.cout)
                self._nodes.pop()
        self._flush(True)

    def finish(self) -> list[SubPath]:
        self._flush(False)
        return self.subpaths

    def _flush(self, closed: bool) -> None:
        if len(self._nodes) >= 2:
            self.subpaths.append(SubPath(tuple(self._nodes), closed))
        self._nodes = []


# --- operaciones de reparación y simplificación --------------------------------------
def remove_duplicate_nodes(sp: SubPath, eps: float = 1e-4) -> SubPath:
    out: list[Node] = []
    for nd in sp.nodes:
        if out and math.dist(out[-1].p, nd.p) < eps and not (nd.cin or out[-1].cout):
            continue
        out.append(nd)
    if sp.closed and len(out) > 1 and math.dist(out[0].p, out[-1].p) < eps and not (out[-1].cout or out[0].cin):
        out.pop()
    return SubPath(tuple(out), sp.closed)


def rdp(points: Sequence[Point], tol: float) -> list[Point]:
    """Ramer–Douglas–Peucker iterativo."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = points[i]
        bx, by = points[j]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        best, idx = -1.0, -1
        for k in range(i + 1, j):
            px, py = points[k]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / norm if norm else math.dist(points[k], points[i])
            if d > best:
                best, idx = d, k
        if idx >= 0 and best > tol:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def simplify(sp: SubPath, tol: float = 0.05) -> SubPath:
    pts = sp.flatten(tol / 4)
    if sp.closed and pts:
        pts = pts + [pts[0]]
    simple = rdp(pts, tol)
    if sp.closed and len(simple) > 1 and _close(simple[0], simple[-1]):
        simple = simple[:-1]
    return polyline(simple, sp.closed)


def join_open_subpaths(subpaths: Sequence[SubPath], tol: float = 0.01) -> list[SubPath]:
    """Une trayectorias abiertas cuyos extremos coinciden (reparación tras DXF) y cierra las que se tocan."""
    pending = [sp for sp in subpaths if not sp.closed and len(sp.nodes) >= 2]
    result = [sp for sp in subpaths if sp.closed or len(sp.nodes) < 2]
    while pending:
        cur = pending.pop(0)
        changed = True
        while changed:
            changed = False
            for i, other in enumerate(pending):
                a0, a1 = cur.nodes[0].p, cur.nodes[-1].p
                b0, b1 = other.nodes[0].p, other.nodes[-1].p
                cand = None
                if math.dist(a1, b0) <= tol:
                    cand = _concat(cur, other)
                elif math.dist(a1, b1) <= tol:
                    cand = _concat(cur, other.reversed())
                elif math.dist(a0, b1) <= tol:
                    cand = _concat(other, cur)
                elif math.dist(a0, b0) <= tol:
                    cand = _concat(cur.reversed(), other)
                if cand is not None:
                    cur = cand
                    pending.pop(i)
                    changed = True
                    break
        if len(cur.nodes) > 2 and math.dist(cur.nodes[0].p, cur.nodes[-1].p) <= tol:
            first, last = cur.nodes[0], cur.nodes[-1]
            nodes = (Node(first.x, first.y, last.cin, first.cout),) + cur.nodes[1:-1]
            cur = SubPath(nodes, True)
        result.append(cur)
    return result


def _concat(a: SubPath, b: SubPath) -> SubPath:
    last, first = a.nodes[-1], b.nodes[0]
    merged = Node(last.x, last.y, last.cin, first.cout)
    return SubPath(a.nodes[:-1] + (merged,) + b.nodes[1:], False)


def containment_depth(polys: Sequence[Sequence[Point]]) -> list[int]:
    """Profundidad de anidamiento de cada polígono (0 = exterior).

    Usa una rejilla de cajas envolventes para no comparar todos con todos
    (miles de contornos importados de un DXF se resuelven en milisegundos).
    """
    n = len(polys)
    depth = [0] * n
    boxes: list[tuple[float, float, float, float] | None] = []
    for poly in polys:
        if len(poly) >= 1:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        else:
            boxes.append(None)
    valid = [b for b in boxes if b is not None]
    if not valid:
        return depth
    areas = [abs(polygon_area(p)) if len(p) >= 3 else 0.0 for p in polys]
    gx0 = min(b[0] for b in valid)
    gy0 = min(b[1] for b in valid)
    span = max(max(b[2] for b in valid) - gx0, max(b[3] for b in valid) - gy0, 1e-9)
    cells = max(1, min(256, int(math.sqrt(n))))
    size = span / cells
    grid: dict[tuple[int, int], list[int]] = {}
    big: list[int] = []
    for j, b in enumerate(boxes):
        if b is None or len(polys[j]) < 3:
            continue
        cx0, cy0 = int((b[0] - gx0) / size), int((b[1] - gy0) / size)
        cx1, cy1 = int((b[2] - gx0) / size), int((b[3] - gy0) / size)
        if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) > 64:
            big.append(j)
            continue
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                grid.setdefault((cx, cy), []).append(j)
    for i, pi in enumerate(polys):
        bi = boxes[i]
        if bi is None:
            continue
        probe = pi[0]
        cell = (int((probe[0] - gx0) / size), int((probe[1] - gy0) / size))
        for j in grid.get(cell, []) + big:
            if i == j:
                continue
            bj = boxes[j]
            assert bj is not None
            if bi[0] < bj[0] or bi[1] < bj[1] or bi[2] > bj[2] or bi[3] > bj[3]:
                continue
            if areas[j] > areas[i] and point_in_polygon(probe, polys[j]):
                depth[i] += 1
    return depth


def split_cubic(p0: Point, c1: Point, c2: Point, p3: Point, t: float) -> tuple[tuple[Point, Point, Point, Point],
                                                                                  tuple[Point, Point, Point, Point]]:
    """De Casteljau: divide una cúbica en ``t``."""
    def lerp(a: Point, b: Point) -> Point:
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    p01, p12, p23 = lerp(p0, c1), lerp(c1, c2), lerp(c2, p3)
    p012, p123 = lerp(p01, p12), lerp(p12, p23)
    mid = lerp(p012, p123)
    return (p0, p01, p012, mid), (mid, p123, p23, p3)


def nearest_on_segment(seg: Segment, p: Point, samples: int = 64) -> tuple[float, float]:
    """(distancia, t) del punto del segmento más cercano a ``p``."""
    p0, c1, c2, p3 = seg
    if c1 is None or c2 is None:
        dx, dy = p3[0] - p0[0], p3[1] - p0[1]
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else max(0.0, min(1.0, ((p[0] - p0[0]) * dx + (p[1] - p0[1]) * dy) / den))
        q = (p0[0] + dx * t, p0[1] + dy * t)
        return math.dist(p, q), t
    best = (math.inf, 0.0)
    for i in range(samples + 1):
        t = i / samples
        d = math.dist(p, cubic_point(p0, c1, c2, p3, t))
        if d < best[0]:
            best = (d, t)
    return best


def insert_node(sp: SubPath, seg_index: int, t: float) -> SubPath:
    """Inserta un nodo en el segmento ``seg_index`` sin cambiar la forma."""
    segs = list(sp.segments())
    p0, c1, c2, p3 = segs[seg_index]
    n = len(sp.nodes)
    i, j = seg_index, (seg_index + 1) % n
    nodes = list(sp.nodes)
    if c1 is None or c2 is None:
        q = (p0[0] + (p3[0] - p0[0]) * t, p0[1] + (p3[1] - p0[1]) * t)
        new = Node(q[0], q[1])
    else:
        (a0, a1, a2, a3), (b0, b1, b2, b3) = split_cubic(p0, c1, c2, p3, t)
        nodes[i] = Node(nodes[i].x, nodes[i].y, nodes[i].cin, a1)
        nodes[j] = Node(nodes[j].x, nodes[j].y, b2, nodes[j].cout)
        new = Node(a3[0], a3[1], a2, b1)
    nodes.insert(i + 1, new)
    return SubPath(tuple(nodes), sp.closed)


def round_corners(sp: SubPath, radius: float) -> SubPath:
    """Sustituye cada esquina entre dos tramos rectos por un arco de radio ``radius``.

    El radio se reduce si los tramos son demasiado cortos (cada esquina usa como
    máximo la mitad de cada tramo). Los nodos con curvas no se modifican.
    """
    nodes = sp.nodes
    n = len(nodes)
    if radius <= 0 or n < 3:
        return sp
    out: list[Node] = []
    for i, nd in enumerate(nodes):
        if not sp.closed and (i == 0 or i == n - 1):
            out.append(nd)
            continue
        prev, nxt = nodes[i - 1], nodes[(i + 1) % n]
        if nd.cin or nd.cout or prev.cout or nxt.cin:
            out.append(nd)
            continue
        v1 = (prev.x - nd.x, prev.y - nd.y)
        v2 = (nxt.x - nd.x, nxt.y - nd.y)
        l1, l2 = math.hypot(*v1), math.hypot(*v2)
        if l1 < 1e-9 or l2 < 1e-9:
            out.append(nd)
            continue
        cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (l1 * l2)))
        theta = math.acos(cosang)  # ángulo interior entre los dos tramos
        if theta > math.pi - 1e-3 or theta < 1e-3:
            out.append(nd)
            continue
        t = min(radius / math.tan(theta / 2), l1 / 2, l2 / 2)
        r_eff = t * math.tan(theta / 2)
        u1 = (v1[0] / l1, v1[1] / l1)
        u2 = (v2[0] / l2, v2[1] / l2)
        h = 4.0 / 3.0 * math.tan((math.pi - theta) / 4) * r_eff
        p1 = (nd.x + u1[0] * t, nd.y + u1[1] * t)
        p2 = (nd.x + u2[0] * t, nd.y + u2[1] * t)
        out.append(Node(p1[0], p1[1], None, (p1[0] - u1[0] * h, p1[1] - u1[1] * h)))
        out.append(Node(p2[0], p2[1], (p2[0] - u2[0] * h, p2[1] - u2[1] * h), None))
    return SubPath(tuple(out), sp.closed)
