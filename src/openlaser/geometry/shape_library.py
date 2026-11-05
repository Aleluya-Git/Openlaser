"""Biblioteca de formas paramétricas (originales) y colocación de glifos sobre una trayectoria."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine
from openlaser.i18n import N_

Point = tuple[float, float]
K = gp.KAPPA


@dataclass(frozen=True)
class LibraryShape:
    id: str
    name: str
    build: Callable[[float, float, float], list[gp.SubPath]]   # (cx, cy, tamaño) → subtrayectorias


def _heart(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    h = s / 2
    pts = []
    for i in range(96):
        t = 2 * math.pi * i / 96
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append((cx + x / 17 * h, cy - y / 17 * h))
    return [gp.simplify(gp.polyline(pts, True), s * 0.002)]


def _arrow(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    w, h = s, s * 0.5
    x0, y0 = cx - w / 2, cy - h / 2
    pts = [(x0, y0 + h * 0.3), (x0 + w * 0.6, y0 + h * 0.3), (x0 + w * 0.6, y0), (x0 + w, y0 + h / 2),
           (x0 + w * 0.6, y0 + h), (x0 + w * 0.6, y0 + h * 0.7), (x0, y0 + h * 0.7)]
    return [gp.polyline(pts, True)]


def _gear(cx: float, cy: float, s: float, teeth: int = 12) -> list[gp.SubPath]:
    ro, ri = s / 2, s / 2 * 0.8
    pts = []
    n = teeth * 4
    for i in range(n):
        a = 2 * math.pi * i / n
        r = ro if i % 4 in (1, 2) else ri
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return [gp.polyline(pts, True), gp.ellipse(cx, cy, s * 0.12, s * 0.12)]


def _ring(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    return [gp.ellipse(cx, cy, s / 2, s / 2), gp.ellipse(cx, cy, s * 0.3, s * 0.3).reversed()]


def _cross(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    a, b = s / 2, s / 6
    pts = [(-b, -a), (b, -a), (b, -b), (a, -b), (a, b), (b, b), (b, a), (-b, a), (-b, b), (-a, b), (-a, -b), (-b, -b)]
    return [gp.polyline([(cx + x, cy + y) for x, y in pts], True)]


def _hexagon(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    return [gp.regular_polygon(cx, cy, s / 2, 6, 0)]


def _bubble(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    w, h = s, s * 0.6
    body = gp.rectangle(cx - w / 2, cy - h / 2, w, h, h * 0.25)
    x0, y1 = cx - w * 0.2, cy + h / 2
    tail = gp.polyline([(x0, y1 - 0.01), (x0 - w * 0.12, y1 + h * 0.3), (x0 + w * 0.15, y1 - 0.01)], True)
    return [body, tail]


def _tag(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    w, h = s, s * 0.45
    x0, y0 = cx - w / 2, cy - h / 2
    body = gp.polyline([(x0 + h / 2, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0 + h / 2, y0 + h), (x0, y0 + h / 2)], True)
    return [body, gp.ellipse(x0 + h * 0.55, cy, h * 0.12, h * 0.12)]


def _rounded_square(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    return [gp.rectangle(cx - s / 2, cy - s / 2, s, s, s * 0.15)]


def _star5(cx: float, cy: float, s: float) -> list[gp.SubPath]:
    return [gp.star(cx, cy, s / 2, s * 0.2, 5)]


SHAPES: dict[str, LibraryShape] = {sh.id: sh for sh in [
    LibraryShape("heart", N_("Corazón"), _heart), LibraryShape("arrow", N_("Flecha"), _arrow),
    LibraryShape("gear", N_("Engranaje"), _gear), LibraryShape("ring", N_("Arandela"), _ring),
    LibraryShape("cross", N_("Cruz"), _cross), LibraryShape("hexagon", N_("Hexágono"), _hexagon),
    LibraryShape("bubble", N_("Bocadillo"), _bubble), LibraryShape("tag", N_("Etiqueta colgante"), _tag),
    LibraryShape("rounded", N_("Cuadrado redondeado"), _rounded_square), LibraryShape("star", N_("Estrella"), _star5),
]}


# ---------------------------------------------------------------- texto en trayectoria
def _arc_table(pts: list[Point]) -> list[float]:
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    return cum


def point_and_angle(pts: list[Point], cum: list[float], s: float) -> tuple[Point, float]:
    s = min(max(s, 0.0), cum[-1])
    lo, hi = 0, len(cum) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    a, b = pts[lo], pts[hi]
    seg = cum[hi] - cum[lo]
    t = (s - cum[lo]) / seg if seg > 1e-12 else 0.0
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def place_on_path(glyphs: list[tuple[list[gp.SubPath], float]], path: gp.SubPath, start: float = 0.0,
                  spacing: float = 0.0, baseline_offset: float = 0.0, align: str = "start") -> list[gp.SubPath]:
    """Coloca glifos (subtrayectorias con origen en su línea base, avance) a lo largo de ``path``.

    Cada glifo se gira según la tangente en el centro de su avance. ``align``:
    start | center | end. Los glifos que no caben se omiten.
    """
    pts = path.flatten(0.05)
    if path.closed and pts and pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    if len(pts) < 2:
        return []
    cum = _arc_table(pts)
    total = sum(adv for _g, adv in glyphs) + spacing * max(0, len(glyphs) - 1)
    if align == "center":
        start += (cum[-1] - total) / 2
    elif align == "end":
        start += cum[-1] - total
    out: list[gp.SubPath] = []
    s = start
    for subs, adv in glyphs:
        mid = s + adv / 2
        if mid < 0 or mid > cum[-1]:
            s += adv + spacing
            continue
        (px, py), ang = point_and_angle(pts, cum, mid)
        m = Affine.translate(px, py) @ Affine.rotate(ang) @ Affine.translate(-adv / 2, -baseline_offset)
        out.extend(sp.transformed(m) for sp in subs)
        s += adv + spacing
    return out
