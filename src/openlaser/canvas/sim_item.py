"""Elemento gráfico de la simulación: recorrido progresivo coloreado, cabezal y dirección.

Los segmentos se agrupan por color (potencia, velocidad o capa) y se dibujan
con ``drawLines`` hasta el movimiento actual; así se reproducen trabajos de
decenas de miles de líneas con fluidez.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

BUCKETS = 10
LAYER_FALLBACK = ["#1f6feb", "#d1242f", "#1a7f37", "#bf8700", "#8250df", "#0a7f86"]


RAMP_STOPS = [(0.0, (40, 90, 255)), (0.33, (0, 200, 210)), (0.66, (255, 200, 0)), (1.0, (230, 30, 40))]


def ramp(t: float) -> QColor:
    """Escala azul (bajo) → cian → ámbar → rojo (alto)."""
    t = min(1.0, max(0.0, t))
    stops = RAMP_STOPS
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return QColor(*(int(a + (b - a) * f) for a, b in zip(c0, c1)))
    return QColor(*stops[-1][1])


class SimulationItem(QGraphicsItem):
    def __init__(self) -> None:
        super().__init__()
        self.setZValue(55)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.current = 0          # índice del movimiento en curso
        self.head: tuple[float, float] | None = None
        self.show_travel = True
        self.show_direction = True
        self._bounds = QRectF()
        self._groups: list[tuple[QPen, list[QLineF], np.ndarray]] = []
        self._travel: tuple[list[QLineF], np.ndarray] = ([], np.zeros(0, np.int64))
        self._arrows_src: list[tuple[int, float, float, float, float]] = []
        self._pos: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = None
        # imágenes quemadas: (imagen, rectángulo en el documento, movimiento de cada fila, Y del borde de la
        # fila 0 en el documento, alto con signo de una fila)
        self._overlays: list[tuple[QImage, QRectF, np.ndarray, float, float]] = []

    # ------------------------------------------------------------------- datos
    def set_data(self, x0: np.ndarray, y0: np.ndarray, x1: np.ndarray, y1: np.ndarray, on: np.ndarray,
                 value: np.ndarray, color_mode: str, layer_colors: list[str], visible: np.ndarray) -> None:
        """Coordenadas ya en el documento. ``value``: potencia o velocidad normalizada [0,1] o índice de capa."""
        self.prepareGeometryChange()
        self._pos = (x0, y0, x1, y1)
        idx = np.arange(x0.size)
        groups: list[tuple[QPen, list[QLineF], np.ndarray]] = []
        cut = on & visible
        if color_mode == "layer":
            keys = value.astype(np.int64)
            colors = {k: QColor(layer_colors[k] if 0 <= k < len(layer_colors) and layer_colors[k]
                                else LAYER_FALLBACK[k % 6]) for k in np.unique(keys[cut]).tolist()}
        else:
            keys = np.minimum((value * BUCKETS).astype(np.int64), BUCKETS - 1)
            colors = {k: ramp((k + 0.5) / BUCKETS) for k in np.unique(keys[cut]).tolist()}
        lx0, ly0, lx1, ly1 = x0.tolist(), y0.tolist(), x1.tolist(), y1.tolist()
        for k, color in colors.items():
            sel = idx[cut & (keys == k)]
            pen = QPen(color, 1.6)
            pen.setCosmetic(True)
            lines = [QLineF(lx0[i], ly0[i], lx1[i], ly1[i]) for i in sel.tolist()]
            groups.append((pen, lines, sel))
        self._groups = groups
        tsel = idx[~on & visible]
        self._travel = ([QLineF(lx0[i], ly0[i], lx1[i], ly1[i]) for i in tsel.tolist()], tsel)
        if x0.size:
            xs = np.concatenate([x0, x1])
            ys = np.concatenate([y0, y1])
            self._bounds = QRectF(float(xs.min()) - 5, float(ys.min()) - 5,
                                 float(xs.max() - xs.min()) + 10, float(ys.max() - ys.min()) + 10)
        else:
            self._bounds = QRectF()
        # candidatos a flecha de dirección: el tramo de corte más largo de cada celda de una rejilla
        # 40×40 sobre el trabajo, para repartirlas y no amontonarlas en los rellenos
        lengths = np.hypot(x1 - x0, y1 - y0)
        cand = idx[cut & (lengths > 0)]
        if cand.size:
            mx, my = (x0[cand] + x1[cand]) / 2, (y0[cand] + y1[cand]) / 2
            span = max(float(mx.max() - mx.min()), float(my.max() - my.min()), 1e-6) / 40.0
            cell = np.floor((mx - mx.min()) / span) * 1000 + np.floor((my - my.min()) / span)
            order = np.lexsort((-lengths[cand], cell))
            first = np.ones(order.size, bool)
            first[1:] = cell[order][1:] != cell[order][:-1]
            cand = cand[order[first]]
        self._arrows_src = [(i, lx0[i], ly0[i], lx1[i], ly1[i]) for i in np.sort(cand).tolist()]
        self.update()

    def set_overlays(self, overlays: list[tuple[QImage, QRectF, np.ndarray, float, float]]) -> None:
        """Imágenes de los bloques ráster: se dibujan píxel a píxel hasta la fila en curso."""
        self.prepareGeometryChange()
        self._overlays = overlays
        for _img, rect, _rows, _y0, _dy in overlays:
            self._bounds = rect.adjusted(-5, -5, 5, 5) if self._bounds.isNull() else self._bounds.united(rect)
        self.update()

    def set_progress(self, current: int, head: tuple[float, float] | None) -> None:
        self.current = current
        self.head = head
        self.update()

    # ----------------------------------------------------------------- dibujo
    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None) -> None:
        n = self.current
        for img, rect, row_move, y0, dy in self._overlays:
            done = np.flatnonzero((row_move >= 0) & (row_move < n))
            if not done.size:
                continue
            y1 = y0 + dy * (int(done[-1]) + 1)
            painter.save()
            painter.setClipRect(QRectF(rect.left(), min(y0, y1), rect.width(), abs(y1 - y0)))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(rect, img)
            painter.restore()
        if self.show_travel:
            lines, sel = self._travel
            k = int(np.searchsorted(sel, n))
            if k:
                pen = QPen(QColor(150, 150, 150, 150), 0.8, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLines(lines[:k])
        for pen, lines, sel in self._groups:
            k = int(np.searchsorted(sel, n))
            if k:
                painter.setPen(pen)
                painter.drawLines(lines[:k])
        scale = abs(painter.worldTransform().m11()) or 1.0
        if self.show_direction and self._arrows_src:
            pen = QPen(QColor(255, 255, 255, 200), 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(QColor(255, 255, 255, 200))
            size = 6.0 / scale
            taken: set[tuple[int, int]] = set()   # una flecha como máximo cada ~40 px de pantalla
            for i, ax, ay, bx, by in self._arrows_src:
                if i >= n:
                    break
                length = math.hypot(bx - ax, by - ay)
                if length * scale < 14:
                    continue
                key = (int((ax + bx) / 2 * scale // 40), int((ay + by) / 2 * scale // 40))
                if key in taken:
                    continue
                taken.add(key)
                ux, uy = (bx - ax) / length, (by - ay) / length
                mx, my = (ax + bx) / 2, (ay + by) / 2
                tip = QPointF(mx + ux * size, my + uy * size)
                left = QPointF(mx - uy * size * 0.6, my + ux * size * 0.6)
                right = QPointF(mx + uy * size * 0.6, my - ux * size * 0.6)
                painter.drawPolygon(QPolygonF([tip, left, right]))
        if self.head is not None and self._pos is not None:
            x0, y0, _x1, _y1 = self._pos
            if n < x0.size:
                pen = QPen(QColor(255, 255, 255), 1.6)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawLine(QLineF(float(x0[n]), float(y0[n]), self.head[0], self.head[1]))
            r = 7.0 / scale
            path = QPainterPath()
            path.addEllipse(QPointF(*self.head), r, r)
            pen = QPen(QColor(255, 60, 60), 2.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
            painter.drawLine(QLineF(self.head[0] - r * 1.6, self.head[1], self.head[0] + r * 1.6, self.head[1]))
            painter.drawLine(QLineF(self.head[0], self.head[1] - r * 1.6, self.head[0], self.head[1] + r * 1.6))
