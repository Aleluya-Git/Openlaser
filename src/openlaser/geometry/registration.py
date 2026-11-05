"""Registro de piezas: Print and Cut (marcas de registro) y Center Finder (centro de un círculo)."""
from __future__ import annotations

import math

from openlaser.geometry.affine import Affine
from openlaser.i18n import N_

Point = tuple[float, float]


def similarity_from_pairs(src: tuple[Point, Point], dst: tuple[Point, Point]) -> tuple[Affine, float, float]:
    """Transformación (giro + escala uniforme + traslación) que lleva ``src`` a ``dst``.

    Devuelve (transformación, escala, ángulo en grados). Las marcas deben estar
    separadas al menos 5 mm para que el resultado sea fiable.
    """
    (a1, a2), (b1, b2) = src, dst
    va = (a2[0] - a1[0], a2[1] - a1[1])
    vb = (b2[0] - b1[0], b2[1] - b1[1])
    la, lb = math.hypot(*va), math.hypot(*vb)
    if la < 5.0 or lb < 5.0:
        raise ValueError(N_("Las marcas de registro deben estar separadas al menos 5 mm"))
    scale = lb / la
    ang = math.atan2(vb[1], vb[0]) - math.atan2(va[1], va[0])
    c, s = math.cos(ang) * scale, math.sin(ang) * scale
    # x' = c·x − s·y + e ; y' = s·x + c·y + f
    e = b1[0] - (c * a1[0] - s * a1[1])
    f = b1[1] - (s * a1[0] + c * a1[1])
    return Affine(a=c, b=s, c=-s, d=c, e=e, f=f), scale, math.degrees(ang)


def affine_from_three(src: tuple[Point, Point, Point], dst: tuple[Point, Point, Point]) -> Affine:
    """Transformación afín completa con tres pares de puntos (corrige también cizalla)."""
    (x1, y1), (x2, y2), (x3, y3) = src
    det = (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(det) < 1e-6:
        raise ValueError(N_("Las tres marcas no pueden estar alineadas"))

    def solve(v1: float, v2: float, v3: float) -> tuple[float, float, float]:
        a = (v1 * (y2 - y3) + v2 * (y3 - y1) + v3 * (y1 - y2)) / det
        b = (v1 * (x3 - x2) + v2 * (x1 - x3) + v3 * (x2 - x1)) / det
        c = (v1 * (x2 * y3 - x3 * y2) + v2 * (x3 * y1 - x1 * y3) + v3 * (x1 * y2 - x2 * y1)) / det
        return a, b, c

    ax, bx, cx = solve(dst[0][0], dst[1][0], dst[2][0])
    ay, by, cy = solve(dst[0][1], dst[1][1], dst[2][1])
    return Affine(a=ax, b=ay, c=bx, d=by, e=cx, f=cy)


def circle_from_three(p1: Point, p2: Point, p3: Point) -> tuple[float, float, float]:
    """Centro y radio del círculo que pasa por tres puntos del borde."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        raise ValueError(N_("Los tres puntos están alineados: tome puntos separados del borde"))
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return ux, uy, math.hypot(ax - ux, ay - uy)
