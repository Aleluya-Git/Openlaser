"""Puente entre la geometría propia y ``QPainterPath``: dibujo, booleanas,
offsets y texto a curvas (motor geométrico de Qt, LGPL)."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFont, QPainterPath, QPainterPathStroker

from openlaser.geometry import path as gp

Point = tuple[float, float]


def subpaths_to_qpath(subpaths: Iterable[gp.SubPath], fill_rule: Qt.FillRule = Qt.FillRule.OddEvenFill) -> QPainterPath:
    qp = QPainterPath()
    qp.setFillRule(fill_rule)
    for sp in subpaths:
        if not sp.nodes:
            continue
        qp.moveTo(QPointF(*sp.nodes[0].p))
        for _p0, c1, c2, p3 in sp.segments():
            if c1 is None or c2 is None:
                qp.lineTo(QPointF(*p3))
            else:
                qp.cubicTo(QPointF(*c1), QPointF(*c2), QPointF(*p3))
        if sp.closed:
            qp.closeSubpath()
    return qp


def qpath_to_subpaths(qp: QPainterPath, tol: float = 1e-6) -> list[gp.SubPath]:
    """Convierte un ``QPainterPath`` (líneas y cúbicas) en subtrayectorias."""
    b = gp.PathBuilder()
    start: Point | None = None
    i = 0
    n = qp.elementCount()
    while i < n:
        el = qp.elementAt(i)
        p = (el.x, el.y)
        if el.isMoveTo():
            if start is not None:
                _finish(b, start, tol)
            b.move_to(p)
            start = p
            i += 1
        elif el.isLineTo():
            b.line_to(p)
            i += 1
        elif el.isCurveTo() and i + 2 < n:
            c2 = qp.elementAt(i + 1)
            end = qp.elementAt(i + 2)
            b.cubic_to(p, (c2.x, c2.y), (end.x, end.y))
            i += 3
        else:
            i += 1
    if start is not None:
        _finish(b, start, tol)
    return [sp for sp in b.finish() if len(sp.nodes) >= 2]


def _finish(b: gp.PathBuilder, start: Point, tol: float) -> None:
    cur = b.current
    if cur is not None and abs(cur[0] - start[0]) <= tol and abs(cur[1] - start[1]) <= tol:
        b.close()


def boolean(op: str, a: Iterable[gp.SubPath], b: Iterable[gp.SubPath]) -> list[gp.SubPath]:
    """``op`` ∈ union, subtract, intersect, xor. Las trayectorias abiertas se cierran."""
    qa = subpaths_to_qpath([sp.with_closed(True) for sp in a], Qt.FillRule.WindingFill)
    qb = subpaths_to_qpath([sp.with_closed(True) for sp in b], Qt.FillRule.WindingFill)
    if op == "union":
        r = qa.united(qb)
    elif op == "subtract":
        r = qa.subtracted(qb)
    elif op == "intersect":
        r = qa.intersected(qb)
    elif op == "xor":
        r = qa.united(qb).subtracted(qa.intersected(qb))
    else:
        raise ValueError(op)
    return qpath_to_subpaths(r.simplified())


def offset(subpaths: Iterable[gp.SubPath], distance: float, round_joins: bool = True) -> list[gp.SubPath]:
    """Desplaza el contorno de la región: positivo hacia fuera, negativo hacia dentro.

    En una pieza con agujeros, «hacia fuera» agranda el contorno exterior y
    encoge los agujeros (lo que necesita la compensación de kerf).
    """
    subs = [sp for sp in subpaths if len(sp.nodes) >= 2]
    if abs(distance) < 1e-9 or not subs:
        return list(subs)
    closed = [sp for sp in subs if sp.closed]
    open_ = [sp for sp in subs if not sp.closed]
    stroker = QPainterPathStroker()
    stroker.setWidth(2 * abs(distance))
    stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin if round_joins else Qt.PenJoinStyle.MiterJoin)
    stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
    stroker.setCurveThreshold(0.01)
    result: list[gp.SubPath] = []
    if closed:
        region = subpaths_to_qpath(closed, Qt.FillRule.OddEvenFill)
        band = stroker.createStroke(region)
        out = region.united(band) if distance > 0 else region.subtracted(band)
        result += qpath_to_subpaths(out.simplified())
    if open_ and distance > 0:
        # una línea abierta se convierte en el contorno de su trazo
        band = stroker.createStroke(subpaths_to_qpath(open_))
        result += qpath_to_subpaths(band.simplified())
    return result


def text_to_subpaths(text: str, family: str, size_mm: float, bold: bool = False, italic: bool = False,
                     origin: Point = (0.0, 0.0), line_spacing: float = 1.2) -> list[gp.SubPath]:
    """Texto convertido a curvas. ``size_mm`` es la altura del cuerpo (em); origen = línea base."""
    font = QFont(family)
    font.setPixelSize(100)
    font.setBold(bold)
    font.setItalic(italic)
    qp = QPainterPath()
    scale = size_mm / 100.0
    for i, line in enumerate(text.splitlines() or [text]):
        if line:
            qp.addText(QPointF(0.0, i * 100.0 * line_spacing), font, line)
    subs = qpath_to_subpaths(qp)
    from openlaser.geometry.affine import Affine
    m = Affine.translate(origin[0], origin[1]) @ Affine.scale(scale)
    return [sp.transformed(m) for sp in subs]
