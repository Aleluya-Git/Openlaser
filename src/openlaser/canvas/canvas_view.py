"""Lienzo de diseño: área de trabajo, cuadrícula, formas, herramientas y vista previa.

Las coordenadas de escena son las del documento (mm, Y hacia abajo). La
cuadrícula y las reglas se alinean con el origen de máquina del perfil.

El lienzo no modifica el documento: emite señales (crear forma, transformar,
editar nodo…) que la ventana principal aplica como pasos deshacibles. Mientras
se arrastra se muestra una vista previa temporal sobre los propios elementos.

Herramientas: seleccionar (mover, escalar con tiradores, girar), editar nodos,
línea/polilínea, rectángulo, elipse, polígono/estrella, texto y medir.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QLineF, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QResizeEvent, QTransform, QWheelEvent,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout,
    QWidget,
)

from openlaser.canvas.qt_geometry import subpaths_to_qpath
from openlaser.document.model import Document, LayerMode, Shape
from openlaser.gcode.analyzer import Segment
from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine
from openlaser.geometry.coords import CoordinateSystem
from openlaser.i18n import tr
from openlaser.ui import theme

MIN_SCALE = 0.05
MAX_SCALE = 400.0
HANDLE_PX = 8
HIT_PX = 6
DRAW_TOOLS = ("line", "rect", "ellipse", "polygon", "text", "measure")
Point = tuple[float, float]


def nice_step(px_per_mm: float, min_px: float) -> float:
    """Paso de cuadrícula/regla (1-2-5 × 10^n mm) que ocupe al menos ``min_px``."""
    if px_per_mm <= 0:
        return 10.0
    raw = min_px / px_per_mm
    exp = math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        step = m * 10 ** exp
        if step >= raw:
            return float(step)
    return float(10 ** (exp + 1))


class _Marker(QGraphicsPathItem):
    def __init__(self, path: QPainterPath, color: str, width: float = 2.0) -> None:
        super().__init__(path)
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(100)


def _crosshair(r: float = 11.0) -> QPainterPath:
    p = QPainterPath()
    p.addEllipse(QPointF(0, 0), r * 0.55, r * 0.55)
    p.moveTo(-r, 0)
    p.lineTo(-r * 0.3, 0)
    p.moveTo(r * 0.3, 0)
    p.lineTo(r, 0)
    p.moveTo(0, -r)
    p.lineTo(0, -r * 0.3)
    p.moveTo(0, r * 0.3)
    p.lineTo(0, r)
    return p


def _origin_mark(r: float = 9.0) -> QPainterPath:
    p = QPainterPath()
    p.moveTo(0, 0)
    p.lineTo(r * 1.6, 0)
    p.moveTo(0, 0)
    p.lineTo(0, -r * 1.6)
    p.addRect(QRectF(-3, -3, 6, 6))
    return p


def affine_to_qtransform(m: Affine) -> QTransform:
    return QTransform(m.a, m.b, m.c, m.d, m.e, m.f)


class CanvasView(QGraphicsView):
    cursor_moved = Signal(QPointF)
    view_changed = Signal()
    selection_changed = Signal(list)
    transform_requested = Signal(list, object)          # ids, Affine
    node_edit_requested = Signal(str, int, int, object)  # shape, subtrayectoria, nodo, Node | None
    node_insert_requested = Signal(str, int, int, float)
    shape_created = Signal(object, str, str)            # subtrayectorias, tipo, nombre
    text_requested = Signal(QPointF)
    measure_changed = Signal(str)
    tool_changed = Signal(str)
    image_double_clicked = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.ItemSelectionMode.IntersectsItemShape)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName(tr("Lienzo de diseño"))
        self.cs = CoordinateSystem(400, 400)
        self.doc: Document | None = None
        self.grid_mm = 10.0
        self.show_grid = True
        self.snap_grid = False
        self.snap_nodes = True
        self.snap_centers = False
        self.tool = "select"
        self.tool_options: dict[str, float] = {"radius": 0.0, "sides": 6, "star": 0, "inner": 0.5}
        self._items: dict[str, tuple[QGraphicsPathItem, Shape, tuple]] = {}
        self._pix: dict[str, QGraphicsPixmapItem] = {}
        self.show_processed_images = True
        self.sim_item: QGraphicsItem | None = None  # SimulationItem (canvas/sim_item.py) en simulación
        self.camera_item: QGraphicsPixmapItem | None = None  # imagen de cámara superpuesta (Fase 4)
        self._pan_start: QPoint | None = None
        self._syncing_selection = False
        self._drag: dict | None = None
        self._draft_pts: list[Point] = []
        self._active_node: tuple[str, int, int] | None = None
        t = theme.current()
        self.head = _Marker(_crosshair(), t.danger, 2.0)
        self.origin = _Marker(_origin_mark(), t.info, 2.0)
        self.scene().addItem(self.head)
        self.scene().addItem(self.origin)
        self.preview_cut = QGraphicsPathItem()
        self.preview_travel = QGraphicsPathItem()
        self.frame_item = QGraphicsPathItem()
        self.draft = QGraphicsPathItem()
        for item, z in ((self.preview_travel, 50), (self.preview_cut, 51), (self.frame_item, 60), (self.draft, 90)):
            item.setZValue(z)
            self.scene().addItem(item)
        self.apply_theme()
        self.scene().selectionChanged.connect(self._on_scene_selection)
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self.view_changed.emit())
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.view_changed.emit())

    # ===================================================================== aspecto
    def apply_theme(self) -> None:
        t = theme.current()
        for item, color, width, style in ((self.preview_cut, t.danger, 1.6, Qt.PenStyle.SolidLine),
                                          (self.preview_travel, t.muted, 1.0, Qt.PenStyle.DashLine),
                                          (self.frame_item, t.accent, 1.6, Qt.PenStyle.DashDotLine),
                                          (self.draft, t.accent, 1.4, Qt.PenStyle.DashLine)):
            pen = QPen(QColor(color), width, style)
            pen.setCosmetic(True)
            item.setPen(pen)
        self.head.setPen(self._cpen(t.danger, 2.0))
        self.origin.setPen(self._cpen(t.info, 2.0))
        for sid in list(self._items):
            item, shape, _key = self._items[sid]
            self._items[sid] = (item, shape, ())
        if self.doc is not None:
            self.set_document(self.doc)
        self.viewport().update()

    @staticmethod
    def _cpen(color: str, width: float) -> QPen:
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        return pen

    def set_coordinate_system(self, cs: CoordinateSystem) -> None:
        self.cs = cs
        margin = max(cs.width, cs.height) * 0.15
        self.scene().setSceneRect(QRectF(-margin, -margin, cs.width + 2 * margin, cs.height + 2 * margin))
        self.viewport().update()
        self.view_changed.emit()

    # ==================================================================== documento
    def set_document(self, doc: Document) -> None:
        """Sincroniza los elementos gráficos con el documento reutilizando los que no cambian."""
        self.doc = doc
        selected = set(self.selected_shape_ids())
        layers = {ly.id: ly for ly in doc.layers}
        alive: set[str] = set()
        total = max(1, len(doc.shapes))
        self._syncing_selection = True
        try:
            for index, shape in enumerate(doc.shapes):
                layer = layers.get(shape.layer_id)
                if layer is None:
                    continue
                key = (layer.color, layer.output, layer.visible, layer.locked, layer.mode, layer.name,
                       shape.visible, shape.locked, shape.name)
                entry = self._items.get(shape.id)
                if entry is None:
                    item = QGraphicsPathItem()
                    item.setData(0, shape.id)
                    self.scene().addItem(item)
                else:
                    item = entry[0]
                if entry is None or entry[1] is not shape:
                    item.setPath(subpaths_to_qpath(shape.subpaths))
                    item.setPos(0, 0)
                    item.setTransform(QTransform())
                    self._update_image(item, shape)
                if entry is None or entry[2] != key:
                    self._style_item(item, layer, shape)
                item.setZValue(10 + index / total)
                self._items[shape.id] = (item, shape, key)
                alive.add(shape.id)
            for sid in [s for s in self._items if s not in alive]:
                self._pix.pop(sid, None)
                self.scene().removeItem(self._items.pop(sid)[0])
            for sid, (item, _s, _k) in self._items.items():
                item.setSelected(sid in selected and bool(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable))
        finally:
            self._syncing_selection = False
        if self._active_node and self._active_node[0] not in alive:
            self._active_node = None
        self.selection_changed.emit(self.selected_shape_ids())
        self.viewport().update()

    def _update_image(self, item: QGraphicsPathItem, shape: Shape) -> None:
        """Mapa de píxeles hijo del marco (vista previa del resultado procesado)."""
        child = self._pix.get(shape.id)
        ref = shape.image
        src = self.doc.images.get(ref.resource) if (ref is not None and self.doc is not None) else None
        if ref is None or src is None:
            if child is not None:
                self.scene().removeItem(child)
                del self._pix[shape.id]
            return
        from openlaser.canvas.raster_items import frame_transform, preview_pixmap
        try:
            pm = preview_pixmap(ref, src.gray, self.show_processed_images)
        except (ValueError, MemoryError):
            return
        if child is None:
            child = QGraphicsPixmapItem(item)
            child.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            child.setFlag(QGraphicsItem.GraphicsItemFlag.ItemStacksBehindParent, True)
            self._pix[shape.id] = child
        child.setPixmap(pm)
        child.setTransform(frame_transform(ref, pm.width(), pm.height()))

    def refresh_images(self) -> None:
        for item, shape, _key in self._items.values():
            if shape.image is not None:
                self._update_image(item, shape)

    def _style_item(self, item: QGraphicsPathItem, layer, shape: Shape) -> None:  # noqa: ANN001
        color = QColor(layer.color)
        pen = QPen(color, 1.5)
        pen.setCosmetic(True)
        if not layer.output:
            pen.setStyle(Qt.PenStyle.DotLine)
        if shape.image is not None:
            pen.setStyle(Qt.PenStyle.DashLine)
        item.setPen(pen)
        child = self._pix.get(shape.id)
        if child is not None:
            child.setOpacity(1.0 if layer.output else 0.45)
        if shape.image is not None:
            item.setBrush(QBrush(QColor(0, 0, 0, 1)))  # casi transparente: el interior es seleccionable
        elif layer.mode in (LayerMode.FILL, LayerMode.FILL_LINE, LayerMode.IMAGE):
            fill = QColor(color)
            fill.setAlpha(45)
            item.setBrush(QBrush(fill))
        else:
            item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        item.setVisible(layer.visible and shape.visible)
        item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, not (layer.locked or shape.locked))
        item.setToolTip(f"{shape.name or shape.kind} — {layer.name}")

    # ===================================================================== selección
    def selected_shape_ids(self) -> list[str]:
        if self.doc is None:
            return [str(it.data(0)) for it in self.scene().selectedItems() if it.data(0)]
        sel = {str(it.data(0)) for it in self.scene().selectedItems() if it.data(0)}
        return [s.id for s in self.doc.shapes if s.id in sel]   # orden de apilado

    def set_selection(self, ids: list[str]) -> None:
        wanted = set(ids)
        self._syncing_selection = True
        try:
            for sid, (item, _s, _k) in self._items.items():
                item.setSelected(sid in wanted and bool(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable))
        finally:
            self._syncing_selection = False
        self._on_scene_selection()

    def select_all(self, value: bool = True) -> None:
        self.set_selection(list(self._items) if value else [])

    def invert_selection(self) -> None:
        current = set(self.selected_shape_ids())
        self.set_selection([sid for sid in self._items if sid not in current])

    def select_layer(self, doc: Document, layer_id: str) -> None:
        self.set_selection([s.id for s in doc.shapes if s.layer_id == layer_id])

    def select_kind(self, doc: Document, kind: str) -> None:
        self.set_selection([s.id for s in doc.shapes if s.kind == kind])

    def _on_scene_selection(self) -> None:
        if self._syncing_selection:
            return
        if self.doc is not None:
            # un clic en un miembro de grupo selecciona el grupo completo
            sel = set(self.selected_shape_ids())
            groups = {s.group_id for s in self.doc.shapes if s.id in sel and s.group_id}
            if groups:
                extra = [s.id for s in self.doc.shapes if s.group_id in groups and s.id not in sel]
                if extra:
                    self._syncing_selection = True
                    try:
                        for sid in extra:
                            entry = self._items.get(sid)
                            if entry and entry[0].flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                                entry[0].setSelected(True)
                    finally:
                        self._syncing_selection = False
        self.selection_changed.emit(self.selected_shape_ids())
        self.viewport().update()

    def selection_bounds(self) -> tuple[float, float, float, float] | None:
        if self.doc is None:
            return None
        shapes = self.doc.shapes_by_ids(self.selected_shape_ids())
        return gp.bounds_of(sp for s in shapes for sp in s.subpaths)

    # ===================================================================== herramientas
    def set_tool(self, tool: str) -> None:
        self._cancel_draft()
        self.tool = tool
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if tool in ("select", "nodes")
                         else QGraphicsView.DragMode.NoDrag)
        cursor = Qt.CursorShape.ArrowCursor if tool in ("select", "nodes") else Qt.CursorShape.CrossCursor
        self.viewport().setCursor(cursor)
        self._active_node = None
        self.tool_changed.emit(tool)
        self.viewport().update()

    def _cancel_draft(self) -> None:
        self._draft_pts = []
        self._drag = None
        self.draft.setPath(QPainterPath())

    # --------------------------------------------------------------- utilidades
    def to_doc(self, event: QMouseEvent) -> Point:
        p = self.mapToScene(event.position().toPoint())
        return (p.x(), p.y())

    def px(self, mm: float) -> float:
        return mm * self.px_per_mm()

    def mm(self, px: float) -> float:
        ppm = self.px_per_mm()
        return px / ppm if ppm > 0 else px

    def snap(self, p: Point, exclude: set[str] | None = None) -> Point:
        tol = self.mm(8)
        best: tuple[float, Point] | None = None
        if self.doc is not None and (self.snap_nodes or self.snap_centers):
            for s in self.doc.shapes:
                if exclude and s.id in exclude:
                    continue
                cands: list[Point] = []
                if self.snap_nodes:
                    cands += [nd.p for sp in s.subpaths for nd in sp.nodes]
                if self.snap_centers:
                    b = s.bounds()
                    cands.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
                for c in cands:
                    d = math.dist(p, c)
                    if d <= tol and (best is None or d < best[0]):
                        best = (d, c)
        if best is not None:
            return best[1]
        if self.snap_grid and self.grid_mm > 0:
            g = self.grid_mm
            mx, my = self.cs.doc_to_machine(p)
            return self.cs.machine_to_doc((round(mx / g) * g, round(my / g) * g))
        return p

    def _shape_at(self, event: QMouseEvent) -> str | None:
        pos = event.position().toPoint()
        rect = QRect(pos.x() - HIT_PX, pos.y() - HIT_PX, 2 * HIT_PX, 2 * HIT_PX)
        for it in self.items(rect):
            sid = it.data(0)
            if sid and it.isVisible() and it.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:
                return str(sid)
        return None

    # ------------------------------------------------------------ tiradores
    def _handle_points(self) -> list[tuple[str, QPointF]]:
        b = self.selection_bounds()
        if b is None:
            return []
        tl = self.mapFromScene(QPointF(b[0], b[1]))
        br = self.mapFromScene(QPointF(b[2], b[3]))
        x0, y0, x1, y1 = tl.x() - 4, tl.y() - 4, br.x() + 4, br.y() + 4
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        return [("nw", QPointF(x0, y0)), ("n", QPointF(xm, y0)), ("ne", QPointF(x1, y0)), ("e", QPointF(x1, ym)),
                ("se", QPointF(x1, y1)), ("s", QPointF(xm, y1)), ("sw", QPointF(x0, y1)), ("w", QPointF(x0, ym)),
                ("rot", QPointF(xm, y0 - 22))]

    def _handle_at(self, event: QMouseEvent) -> str | None:
        pos = event.position()
        for name, hp in self._handle_points():
            if abs(pos.x() - hp.x()) <= HANDLE_PX and abs(pos.y() - hp.y()) <= HANDLE_PX:
                return name
        return None

    def _node_at(self, event: QMouseEvent) -> tuple[str, int, int, str] | None:
        """(forma, subtrayectoria, nodo, 'node'|'cin'|'cout') bajo el cursor."""
        if self.doc is None:
            return None
        pos = event.position()
        for s in self.doc.shapes_by_ids(self.selected_shape_ids()):
            for si, sp in enumerate(s.subpaths):
                for ni, nd in enumerate(sp.nodes):
                    for kind, pt in (("node", nd.p), ("cin", nd.cin), ("cout", nd.cout)):
                        if pt is None:
                            continue
                        vp = self.mapFromScene(QPointF(*pt))
                        if abs(vp.x() - pos.x()) <= HANDLE_PX and abs(vp.y() - pos.y()) <= HANDLE_PX:
                            return (s.id, si, ni, kind)
        return None

    def _preview_transform(self, ids: list[str], m: Affine) -> None:
        qt = affine_to_qtransform(m)
        for sid in ids:
            entry = self._items.get(sid)
            if entry:
                entry[0].setTransform(qt)

    def _reset_preview(self, ids: list[str]) -> None:
        for sid in ids:
            entry = self._items.get(sid)
            if entry:
                entry[0].setTransform(QTransform())
                entry[0].setPath(subpaths_to_qpath(entry[1].subpaths))

    # =================================================================== ratón
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton and self.tool == "line" and self._draft_pts:
            self._finish_polyline(False)
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        p = self.to_doc(event)
        if self.tool == "select":
            self._press_select(event, p)
        elif self.tool == "nodes":
            self._press_nodes(event, p)
        elif self.tool == "line":
            sp = self.snap(p)
            if len(self._draft_pts) >= 3 and math.dist(sp, self._draft_pts[0]) <= self.mm(8):
                self._finish_polyline(True)
            else:
                self._draft_pts.append(sp)
                self._update_line_draft(sp)
        elif self.tool in ("rect", "ellipse", "polygon", "measure"):
            self._drag = {"kind": self.tool, "start": self.snap(p), "cur": self.snap(p)}
        elif self.tool == "text":
            self.text_requested.emit(QPointF(*self.snap(p)))
        event.accept()

    def _press_select(self, event: QMouseEvent, p: Point) -> None:
        handle = self._handle_at(event)
        ids = self.selected_shape_ids()
        if handle and ids:
            b = self.selection_bounds()
            self._drag = {"kind": "rotate" if handle == "rot" else "scale", "handle": handle, "start": p,
                          "ids": ids, "bounds": b, "m": Affine()}
            return
        hit = self._shape_at(event)
        mods = event.modifiers()
        if hit is None:
            if not mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                self.set_selection([])
            super().mousePressEvent(event)  # selección por recuadro
            return
        if mods & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
            current = set(ids)
            group = self._group_of(hit)
            if hit in current:
                self.set_selection([i for i in ids if i not in group])
            else:
                self.set_selection(ids + group)
            return
        if hit not in ids:
            self.set_selection(self._group_of(hit))
        ids = self.selected_shape_ids()
        b = self.selection_bounds()
        self._drag = {"kind": "move", "start": p, "ids": ids, "bounds": b, "m": Affine()}

    def _group_of(self, sid: str) -> list[str]:
        if self.doc is None:
            return [sid]
        s = self.doc.shape(sid)
        if s is None or not s.group_id:
            return [sid]
        return [o.id for o in self.doc.shapes if o.group_id == s.group_id]

    def _press_nodes(self, event: QMouseEvent, p: Point) -> None:
        hit_node = self._node_at(event)
        if hit_node is not None:
            sid, si, ni, kind = hit_node
            self._active_node = (sid, si, ni)
            self._drag = {"kind": "node", "target": hit_node, "start": p}
            self.viewport().update()
            return
        hit = self._shape_at(event)
        self._active_node = None
        if hit is not None:
            self.set_selection([hit])
        else:
            self.set_selection([])
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pan_start is not None:
            pos = event.position().toPoint()
            d = pos - self._pan_start
            self._pan_start = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            event.accept()
            return
        p = self.to_doc(event)
        self.cursor_moved.emit(QPointF(*p))
        drag = self._drag
        if drag is None:
            if self.tool == "line" and self._draft_pts:
                self._update_line_draft(self.snap(p))
            elif self.tool == "select":
                handle = self._handle_at(event)
                cursors = {"nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
                           "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
                           "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
                           "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
                           "rot": Qt.CursorShape.CrossCursor}
                self.viewport().setCursor(cursors.get(handle or "", Qt.CursorShape.ArrowCursor))
            super().mouseMoveEvent(event)
            return
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        kind = drag["kind"]
        if kind == "move":
            dx, dy = p[0] - drag["start"][0], p[1] - drag["start"][1]
            if shift:
                if abs(dx) > abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            b = drag["bounds"]
            if b is not None and (self.snap_grid or self.snap_nodes or self.snap_centers):
                ref = (b[0] + dx, b[1] + dy)
                snapped = self.snap(ref, exclude=set(drag["ids"]))
                dx, dy = dx + snapped[0] - ref[0], dy + snapped[1] - ref[1]
            drag["m"] = Affine.translate(dx, dy)
            self._preview_transform(drag["ids"], drag["m"])
        elif kind == "scale":
            drag["m"] = self._scale_affine(drag, p, keep=not shift)
            self._preview_transform(drag["ids"], drag["m"])
        elif kind == "rotate":
            b = drag["bounds"]
            c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            a0 = math.atan2(drag["start"][1] - c[1], drag["start"][0] - c[0])
            a1 = math.atan2(p[1] - c[1], p[0] - c[0])
            deg = math.degrees(a1 - a0)
            if shift:
                deg = round(deg / 15.0) * 15.0
            drag["m"] = Affine.rotate(deg, c)
            drag["deg"] = deg
            self._preview_transform(drag["ids"], drag["m"])
            self.measure_changed.emit(tr("Giro {a:.1f}°", a=deg))
        elif kind == "node":
            self._drag_node(drag, p if shift else self.snap(p, exclude={drag["target"][0]}))
        else:  # rect, ellipse, polygon, measure
            drag["cur"] = self.snap(p)
            drag["ctrl"] = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            drag["shift"] = shift
            subs = self._draft_geometry(drag)
            self.draft.setPath(subpaths_to_qpath(subs))
            if kind == "measure":
                (x0, y0), (x1, y1) = drag["start"], drag["cur"]
                d = math.hypot(x1 - x0, y1 - y0)
                ang = math.degrees(math.atan2(-(y1 - y0), x1 - x0))
                self.measure_changed.emit(tr("Distancia {d:.3f} mm · ΔX {dx:.3f} · ΔY {dy:.3f} · ángulo {a:.1f}°",
                                             d=d, dx=x1 - x0, dy=y0 - y1, a=ang))
        event.accept()

    def _scale_affine(self, drag: dict, p: Point, keep: bool) -> Affine:
        b = drag["bounds"]
        h = drag["handle"]
        x0, y0, x1, y1 = b
        ax = x1 if "w" in h else (x0 if "e" in h else (x0 + x1) / 2)
        ay = y1 if "n" in h else (y0 if "s" in h else (y0 + y1) / 2)
        w, hgt = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
        sx = sy = 1.0
        if "e" in h:
            sx = (p[0] - x0) / w
        elif "w" in h:
            sx = (x1 - p[0]) / w
        if "s" in h:
            sy = (p[1] - y0) / hgt
        elif "n" in h:
            sy = (y1 - p[1]) / hgt
        if h in ("n", "s"):
            sx = 1.0
        if h in ("e", "w"):
            sy = 1.0
        if keep and len(h) == 2:
            s = max(abs(sx), abs(sy))
            sx = math.copysign(s, sx)
            sy = math.copysign(s, sy)
        sx = sx if abs(sx) > 1e-4 else 1e-4
        sy = sy if abs(sy) > 1e-4 else 1e-4
        self.measure_changed.emit(tr("Escala {x:.1f} % × {y:.1f} %", x=sx * 100, y=sy * 100))
        return Affine.translate(ax, ay) @ Affine.scale(sx, sy) @ Affine.translate(-ax, -ay)

    def _drag_node(self, drag: dict, p: Point) -> None:
        if self.doc is None:
            return
        sid, si, ni, kind = drag["target"]
        shape = self.doc.shape(sid)
        if shape is None:
            return
        nd = shape.subpaths[si].nodes[ni]
        if kind == "node":
            new = nd.moved(p[0] - nd.x, p[1] - nd.y)
        elif kind == "cin":
            new = gp.Node(nd.x, nd.y, p, nd.cout)
        else:
            new = gp.Node(nd.x, nd.y, nd.cin, p)
        drag["new"] = new
        subs = list(shape.subpaths)
        nodes = list(subs[si].nodes)
        nodes[ni] = new
        subs[si] = gp.SubPath(tuple(nodes), subs[si].closed)
        entry = self._items.get(sid)
        if entry:
            entry[0].setPath(subpaths_to_qpath(subs))
        drag["preview_subs"] = subs
        self.viewport().update()

    def _draft_geometry(self, drag: dict) -> list[gp.SubPath]:
        (x0, y0), (x1, y1) = drag["start"], drag["cur"]
        kind = drag["kind"]
        if kind == "measure":
            return [gp.polyline([(x0, y0), (x1, y1)])]
        if kind == "polygon":
            r = math.hypot(x1 - x0, y1 - y0)
            rot = math.degrees(math.atan2(y1 - y0, x1 - x0))
            sides = int(self.tool_options.get("sides", 6))
            if self.tool_options.get("star"):
                return [gp.star(x0, y0, r, r * float(self.tool_options.get("inner", 0.5)), sides, rot)]
            return [gp.regular_polygon(x0, y0, r, sides, rot)]
        w, h = x1 - x0, y1 - y0
        if drag.get("ctrl"):
            s = max(abs(w), abs(h))
            w, h = math.copysign(s, w or 1), math.copysign(s, h or 1)
        if drag.get("shift"):
            x0, y0, w, h = x0 - w, y0 - h, 2 * w, 2 * h
        if kind == "rect":
            return [gp.rectangle(x0, y0, w, h, float(self.tool_options.get("radius", 0.0)))]
        return [gp.ellipse(x0 + w / 2, y0 + h / 2, abs(w) / 2, abs(h) / 2)]

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor if self.tool in ("select", "nodes")
                                      else Qt.CursorShape.CrossCursor)
            event.accept()
            return
        drag = self._drag
        if drag is None or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self._drag = None
        kind = drag["kind"]
        if kind in ("move", "scale", "rotate"):
            m: Affine = drag["m"]
            self._reset_preview(drag["ids"])
            if not m.is_identity(1e-9):
                self.transform_requested.emit(drag["ids"], m)
        elif kind == "node":
            if "new" in drag:
                sid, si, ni, _k = drag["target"]
                self.node_edit_requested.emit(sid, si, ni, drag["new"])
        elif kind == "measure":
            pass  # la medida queda dibujada hasta el siguiente clic
        else:
            subs = self._draft_geometry(drag)
            self.draft.setPath(QPainterPath())
            b = gp.bounds_of(subs)
            if b is not None and (b[2] - b[0] > 0.05 or b[3] - b[1] > 0.05):
                names = {"rect": tr("Rectángulo"), "ellipse": tr("Elipse"), "polygon": tr("Polígono")}
                self.shape_created.emit(subs, kind, names.get(kind, kind))
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self.tool == "line" and self._draft_pts:
            self._finish_polyline(False)
            event.accept()
            return
        if self.tool == "nodes" and self.doc is not None:
            p = self.to_doc(event)
            best: tuple[float, str, int, int, float] | None = None
            for s in self.doc.shapes_by_ids(self.selected_shape_ids()):
                for si, sp in enumerate(s.subpaths):
                    for gi, seg in enumerate(sp.segments()):
                        d, t = gp.nearest_on_segment(seg, p)
                        if best is None or d < best[0]:
                            best = (d, s.id, si, gi, t)
            if best is not None and best[0] <= self.mm(HIT_PX) and 0.0 < best[4] < 1.0:
                self.node_insert_requested.emit(best[1], best[2], best[3], best[4])
            event.accept()
            return
        if self.tool == "select":
            hit = self._shape_at(event)
            if hit is not None:
                self.set_selection([hit])
                shape = self.doc.shape(hit) if self.doc is not None else None
                if shape is not None and shape.image is not None:
                    self.image_double_clicked.emit(hit)  # las imágenes no tienen nodos: ajustes de imagen
                else:
                    self.set_tool("nodes")
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def _update_line_draft(self, cur: Point) -> None:
        pts = self._draft_pts + [cur]
        self.draft.setPath(subpaths_to_qpath([gp.polyline(pts)]) if len(pts) >= 2 else QPainterPath())
        if len(self._draft_pts) >= 1:
            x0, y0 = self._draft_pts[-1]
            self.measure_changed.emit(tr("Tramo {d:.3f} mm", d=math.dist((x0, y0), cur)))

    def _finish_polyline(self, closed: bool) -> None:
        pts = []
        for p in self._draft_pts:
            if not pts or math.dist(pts[-1], p) > 1e-6:
                pts.append(p)
        self._draft_pts = []
        self.draft.setPath(QPainterPath())
        if len(pts) >= 2:
            self.shape_created.emit([gp.polyline(pts, closed and len(pts) >= 3)], "path", tr("Línea"))

    # ================================================================== teclado
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            if self._draft_pts or self._drag:
                if self._drag and self._drag.get("ids"):
                    self._reset_preview(self._drag["ids"])
                self._cancel_draft()
            elif self.tool != "select":
                self.set_tool("select")
            else:
                self.set_selection([])
            event.accept()
            return
        if self.tool == "line" and self._draft_pts:
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._finish_polyline(bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
                event.accept()
                return
            if key == Qt.Key.Key_Backspace:
                self._draft_pts.pop()
                if self._draft_pts:
                    self._update_line_draft(self._draft_pts[-1])
                else:
                    self.draft.setPath(QPainterPath())
                event.accept()
                return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if key in arrows and self.tool == "select" and self.selected_shape_ids() \
                and not event.modifiers() & (Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ControlModifier):
            step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            dx, dy = arrows[Qt.Key(key)]
            self.transform_requested.emit(self.selected_shape_ids(), Affine.translate(dx * step, dy * step))
            event.accept()
            return
        super().keyPressEvent(event)

    # ================================================================ superposición
    def drawForeground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        t = theme.current()
        painter.save()
        painter.resetTransform()
        ids = self.selected_shape_ids()
        if ids and self.tool == "select" and not (self._drag and self._drag.get("kind") in ("move", "scale", "rotate")):
            pts = self._handle_points()
            if pts:
                box = QRectF(pts[0][1], pts[4][1])
                pen = QPen(QColor(t.accent), 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(box)
                painter.drawLine(pts[1][1], pts[8][1])
                painter.setPen(QPen(QColor(t.accent), 1))
                painter.setBrush(QColor(t.surface))
                for name, hp in pts:
                    if name == "rot":
                        painter.drawEllipse(hp, HANDLE_PX / 2 + 1, HANDLE_PX / 2 + 1)
                    else:
                        painter.drawRect(QRectF(hp.x() - HANDLE_PX / 2, hp.y() - HANDLE_PX / 2, HANDLE_PX, HANDLE_PX))
        if self.tool == "nodes" and self.doc is not None:
            node_pen = QPen(QColor(t.info), 1)
            for s in self.doc.shapes_by_ids(ids):
                subs = s.subpaths
                if self._drag and self._drag.get("kind") == "node" and self._drag["target"][0] == s.id \
                        and "preview_subs" in self._drag:
                    subs = tuple(self._drag["preview_subs"])
                for si, sp in enumerate(subs):
                    for ni, nd in enumerate(sp.nodes):
                        vp = self.mapFromScene(QPointF(nd.x, nd.y))
                        for h in (nd.cin, nd.cout):
                            if h is not None:
                                hv = self.mapFromScene(QPointF(*h))
                                painter.setPen(QPen(QColor(t.muted), 1))
                                painter.drawLine(vp, hv)
                                painter.setBrush(QColor(t.muted))
                                painter.drawEllipse(QPointF(hv), 3, 3)
                        active = self._active_node == (s.id, si, ni)
                        painter.setPen(node_pen)
                        painter.setBrush(QColor(t.accent if active else t.surface))
                        painter.drawRect(QRectF(vp.x() - 3.5, vp.y() - 3.5, 7, 7))
        painter.restore()

    # ================================================================ marcadores
    def set_head(self, machine_xy: tuple[float, float], wco_xy: tuple[float, float], visible: bool) -> None:
        self.head.setPos(QPointF(*self.cs.machine_to_doc(machine_xy)))
        self.head.setVisible(visible)
        self.origin.setPos(QPointF(*self.cs.machine_to_doc(wco_xy)))
        self.origin.setVisible(visible)

    def set_preview(self, segments: list[Segment], wco: tuple[float, float]) -> None:
        cut, travel = QPainterPath(), QPainterPath()
        for x0, y0, x1, y1, on in segments:
            a = self.cs.work_to_doc((x0, y0), wco)
            b = self.cs.work_to_doc((x1, y1), wco)
            target = cut if on else travel
            target.moveTo(QPointF(*a))
            target.lineTo(QPointF(*b))
        self.preview_cut.setPath(cut)
        self.preview_travel.setPath(travel)

    def set_camera_overlay(self, image: object, transform: QTransform | None, opacity: float = 0.6) -> None:
        """Imagen de cámara bajo los objetos (``image``: QImage; None la quita)."""
        from PySide6.QtGui import QImage, QPixmap
        if image is None or transform is None or not isinstance(image, QImage):
            if self.camera_item is not None:
                self.camera_item.setVisible(False)
            return
        if self.camera_item is None:
            self.camera_item = QGraphicsPixmapItem()
            self.camera_item.setZValue(1)
            self.camera_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self.camera_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.scene().addItem(self.camera_item)
        self.camera_item.setPixmap(QPixmap.fromImage(image))
        self.camera_item.setTransform(transform)
        self.camera_item.setOpacity(max(0.05, min(1.0, opacity)))
        self.camera_item.setVisible(True)

    def set_camera_opacity(self, opacity: float) -> None:
        if self.camera_item is not None:
            self.camera_item.setOpacity(max(0.05, min(1.0, opacity)))

    def show_simulation(self, item: QGraphicsItem) -> None:
        """Muestra la simulación en lugar de la vista previa estática."""
        if self.sim_item is not None and self.sim_item is not item and self.sim_item.scene() is not None:
            self.scene().removeItem(self.sim_item)
        self.sim_item = item
        if item.scene() is None:
            self.scene().addItem(item)
        self.preview_cut.setVisible(False)
        self.preview_travel.setVisible(False)

    def clear_simulation(self) -> None:
        if self.sim_item is not None and self.sim_item.scene() is not None:
            self.scene().removeItem(self.sim_item)
        self.sim_item = None
        self.preview_cut.setVisible(True)
        self.preview_travel.setVisible(True)

    def clear_preview(self) -> None:
        self.preview_cut.setPath(QPainterPath())
        self.preview_travel.setPath(QPainterPath())

    def set_frame(self, points_work: list[tuple[float, float]], wco: tuple[float, float]) -> None:
        path = QPainterPath()
        if points_work:
            pts = [self.cs.work_to_doc(p, wco) for p in points_work]
            path.moveTo(QPointF(*pts[0]))
            for p in pts[1:]:
                path.lineTo(QPointF(*p))
        self.frame_item.setPath(path)

    # ================================================================ navegación
    def px_per_mm(self) -> float:
        return abs(self.transform().m11())

    def fit_workspace(self) -> None:
        rect = QRectF(0, 0, self.cs.width, self.cs.height).adjusted(-10, -10, 10, 10)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.view_changed.emit()

    def fit_selection(self) -> None:
        b = self.selection_bounds()
        if b is None:
            self.fit_workspace()
            return
        rect = QRectF(b[0], b[1], max(b[2] - b[0], 1), max(b[3] - b[1], 1)).adjusted(-5, -5, 5, 5)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.view_changed.emit()

    def center_on_doc(self, p: tuple[float, float]) -> None:
        self.centerOn(QPointF(*p))
        self.view_changed.emit()

    def zoom(self, factor: float) -> None:
        current = self.px_per_mm()
        target = max(MIN_SCALE, min(MAX_SCALE, current * factor))
        if current > 0:
            f = target / current
            self.scale(f, f)
        self.view_changed.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.15 ** (delta / 120.0))
        event.accept()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.view_changed.emit()

    # ================================================================ fondo
    def drawBackground(self, painter: QPainter, rect: QRectF | QRect) -> None:
        rect = QRectF(rect)
        t = theme.current()
        painter.fillRect(rect, QColor(t.canvas_bg))
        area = QRectF(0, 0, self.cs.width, self.cs.height)
        painter.fillRect(area, QColor(t.canvas_area))
        if self.show_grid:
            ppm = self.px_per_mm()
            step = max(self.grid_mm, nice_step(ppm, 8.0))
            major = step * 5
            visible = rect.intersected(area)
            if not visible.isEmpty():
                self._grid(painter, visible, step, QColor(t.grid_minor))
                self._grid(painter, visible, major, QColor(t.grid_major))
        border = QPen(QColor(t.border), 1.5)
        border.setCosmetic(True)
        painter.setPen(border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(area)

    def _grid(self, painter: QPainter, visible: QRectF, step: float, color: QColor) -> None:
        pen = QPen(color, 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        lines: list[QLineF] = []
        w, h = self.cs.width, self.cs.height
        k = 0
        while k * step <= w + 1e-9 and len(lines) < 4000:
            dx, _ = self.cs.machine_to_doc((k * step, 0))
            if visible.left() <= dx <= visible.right():
                lines.append(QLineF(dx, visible.top(), dx, visible.bottom()))
            k += 1
        k = 0
        while k * step <= h + 1e-9 and len(lines) < 8000:
            _, dy = self.cs.machine_to_doc((0, k * step))
            if visible.top() <= dy <= visible.bottom():
                lines.append(QLineF(visible.left(), dy, visible.right(), dy))
            k += 1
        if lines:
            painter.drawLines(lines)


class CanvasArea(QWidget):
    """Lienzo con reglas horizontal y vertical."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from openlaser.canvas.rulers import Ruler
        self.view = CanvasView(self)
        self.hruler = Ruler(self.view, Qt.Orientation.Horizontal, self)
        self.vruler = Ruler(self.view, Qt.Orientation.Vertical, self)
        corner = QWidget(self)
        corner.setFixedSize(Ruler.THICKNESS, Ruler.THICKNESS)
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(corner, 0, 0)
        lay.addWidget(self.hruler, 0, 1)
        lay.addWidget(self.vruler, 1, 0)
        lay.addWidget(self.view, 1, 1)
        self.view.view_changed.connect(self.hruler.update)
        self.view.view_changed.connect(self.vruler.update)
        self.view.cursor_moved.connect(self.hruler.set_cursor)
        self.view.cursor_moved.connect(self.vruler.set_cursor)
