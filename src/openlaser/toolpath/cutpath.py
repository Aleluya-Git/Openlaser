"""Preparación de contornos de corte: entrada/salida (lead-in/out) y puentes (tabs).

Trabaja sobre polilíneas en mm (coordenadas de trabajo). El resultado es una
lista de tramos ``(punto, encendido)``: el booleano indica si el tramo que
termina en ese punto se hace con el láser encendido.
"""
from __future__ import annotations

import math

Point = tuple[float, float]
Step = tuple[Point, bool]


def _lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def walk(pts: list[Point], distance: float) -> list[Point]:
    """Puntos del recorrido desde el inicio hasta ``distance`` mm (incluye el punto final interpolado)."""
    out = [pts[0]]
    left = distance
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if seg <= 1e-12:
            continue
        if seg >= left:
            out.append(_lerp(a, b, left / seg))
            return out
        out.append(b)
        left -= seg
    return out


def lead_in_point(pts: list[Point], length: float) -> Point | None:
    """Punto de entrada en la prolongación hacia atrás del primer tramo."""
    if length <= 0 or len(pts) < 2:
        return None
    a = pts[0]
    for b in pts[1:]:
        d = math.dist(a, b)
        if d > 1e-9:
            return (a[0] - (b[0] - a[0]) / d * length, a[1] - (b[1] - a[1]) / d * length)
    return None


def tab_intervals(total: float, count: int, width: float) -> list[tuple[float, float]]:
    """Intervalos [a, b] (distancia a lo largo del contorno) donde el láser se apaga."""
    if count <= 0 or width <= 0 or total <= 0 or width * count >= total * 0.8:
        return []
    step = total / count
    return [(step * (i + 0.5) - width / 2, step * (i + 0.5) + width / 2) for i in range(count)]


def build_steps(pts: list[Point], closed: bool, lead_in: float = 0.0, lead_out: float = 0.0,
                tabs: int = 0, tab_width: float = 1.0) -> tuple[Point, list[Step]]:
    """Devuelve (punto de partida, tramos). ``pts`` ya incluye el cierre si es cerrado."""
    steps: list[Step] = []
    start = pts[0]
    entry = lead_in_point(pts, lead_in)
    if entry is not None:
        start = entry
        steps.append((pts[0], True))
    gaps = tab_intervals(sum(math.dist(a, b) for a, b in zip(pts, pts[1:])), tabs, tab_width) if closed else []
    pos = 0.0
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if seg <= 1e-12:
            continue
        cuts = sorted({t for g in gaps for t in g if pos < t < pos + seg})
        prev = 0.0
        for t in cuts + [pos + seg]:
            frac = (t - pos) / seg
            p = _lerp(a, b, frac) if t < pos + seg else b
            mid = pos + (prev + (t - pos)) / 2
            on = not any(g0 <= mid <= g1 for g0, g1 in gaps)
            steps.append((p, on))
            prev = t - pos
        pos += seg
    if closed and lead_out > 0:
        for p in walk(pts, lead_out)[1:]:
            steps.append((p, True))
    return start, steps
