"""Reglas en milímetros de máquina, sincronizadas con la vista del lienzo."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from openlaser.i18n import tr
from openlaser.ui import theme

if TYPE_CHECKING:
    from openlaser.canvas.canvas_view import CanvasView


class Ruler(QWidget):
    THICKNESS = 24

    def __init__(self, view: "CanvasView", orientation: Qt.Orientation, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = view
        self.orientation = orientation
        self._cursor: QPointF | None = None
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(self.THICKNESS)
        else:
            self.setFixedWidth(self.THICKNESS)
        self.setAccessibleName(tr("Regla horizontal") if orientation == Qt.Orientation.Horizontal else tr("Regla vertical"))

    def set_cursor(self, scene_pos: QPointF) -> None:
        self._cursor = scene_pos
        self.update()

    def _scene_coord(self, pixel: int) -> float:
        vp = self.view.viewport()
        if self.orientation == Qt.Orientation.Horizontal:
            return self.view.mapToScene(QPoint(pixel, vp.height() // 2)).x()
        return self.view.mapToScene(QPoint(vp.width() // 2, pixel)).y()

    def _pixel(self, scene_value: float) -> float:
        if self.orientation == Qt.Orientation.Horizontal:
            return float(self.view.mapFromScene(QPointF(scene_value, 0)).x())
        return float(self.view.mapFromScene(QPointF(0, scene_value)).y())

    def paintEvent(self, event: QPaintEvent) -> None:
        from openlaser.canvas.canvas_view import nice_step

        t = theme.current()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(t.surface))
        horiz = self.orientation == Qt.Orientation.Horizontal
        length = self.width() if horiz else self.height()
        ppm = self.view.px_per_mm()
        if ppm <= 0:
            p.end()
            return
        major = nice_step(ppm, 60.0)
        minor = major / 5.0
        cs = self.view.cs
        a = self._scene_coord(0)
        b = self._scene_coord(length)
        lo, hi = min(a, b), max(a, b)
        # Convertir el rango visible de documento a coordenadas de máquina.
        if horiz:
            m0, m1 = cs.doc_to_machine((lo, 0))[0], cs.doc_to_machine((hi, 0))[0]
        else:
            m0, m1 = cs.doc_to_machine((0, lo))[1], cs.doc_to_machine((0, hi))[1]
        mlo, mhi = min(m0, m1), max(m0, m1)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        p.setFont(font)
        pen_tick = QPen(QColor(t.muted))
        p.setPen(pen_tick)
        start = math.floor(mlo / minor)
        end = math.ceil(mhi / minor)
        if end - start > 2000:
            end = start + 2000
        for k in range(start, end + 1):
            mval = k * minor
            doc = cs.machine_to_doc((mval, mval))
            pix = self._pixel(doc[0] if horiz else doc[1])
            is_major = k % 5 == 0
            size = self.THICKNESS * (0.55 if is_major else 0.25)
            if horiz:
                p.drawLine(QPointF(pix, self.THICKNESS - size), QPointF(pix, self.THICKNESS))
                if is_major:
                    p.drawText(QPointF(pix + 3, 11), f"{mval:g}")
            else:
                p.drawLine(QPointF(self.THICKNESS - size, pix), QPointF(self.THICKNESS, pix))
                if is_major:
                    p.save()
                    p.translate(11, pix - 3)
                    p.rotate(-90)
                    p.drawText(QPointF(0, 0), f"{mval:g}")
                    p.restore()
        if self._cursor is not None:
            p.setPen(QPen(QColor(t.accent), 1.5))
            pix = self._pixel(self._cursor.x() if horiz else self._cursor.y())
            if horiz:
                p.drawLine(QPointF(pix, 0), QPointF(pix, self.THICKNESS))
            else:
                p.drawLine(QPointF(0, pix), QPointF(self.THICKNESS, pix))
        p.setPen(QPen(QColor(t.border)))
        r = QRectF(self.rect())
        if horiz:
            p.drawLine(r.bottomLeft(), r.bottomRight())
        else:
            p.drawLine(r.topRight(), r.bottomRight())
        p.end()
