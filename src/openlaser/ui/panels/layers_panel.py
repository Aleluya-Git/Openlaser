"""Panel de capas y operaciones (parámetros editables con Deshacer/Rehacer)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGridLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from openlaser.document.commands import SetAttribute, UndoStack
from openlaser.document.model import Document, Layer, LayerMode
from openlaser.i18n import N_, tr
from openlaser.ui import icons

# Etiqueta corta (tabla) por modo; el nombre completo va en el desplegable y el tooltip.
MODE_SHORT = {
    LayerMode.LINE: N_("Línea"), LayerMode.FILL: N_("Relleno"), LayerMode.FILL_LINE: N_("R+Lín."),
    LayerMode.IMAGE: N_("Imagen"),
}
MODE_LABELS = {
    LayerMode.LINE: N_("Línea"), LayerMode.FILL: N_("Relleno"), LayerMode.FILL_LINE: N_("Relleno + Línea"),
    LayerMode.IMAGE: N_("Imagen"),
}


class LayersPanel(QWidget):
    changed = Signal()
    layer_selected = Signal(str)
    layer_action = Signal(str, str)   # acción, id de capa
    color_requested = Signal(str)

    COLUMNS = ["", N_("Nombre"), N_("Modo"), "mm/min", "%", "×", N_("Sal."), N_("Vis."), N_("Bloq.")]
    COLUMN_TIPS = ["", N_("Nombre de la capa (doble clic para editar)"), N_("Modo de la capa"),
                   N_("Velocidad (mm/min)"), N_("Potencia máxima %"), N_("Pasadas"),
                   N_("Incluir la capa en el trabajo"), N_("Mostrar u ocultar la capa"),
                   N_("Bloquear la capa (no se puede seleccionar ni editar)")]
    # columna → (atributo, tipo, mínimo, máximo, texto de deshacer)
    NUMERIC = {3: ("speed_mm_min", float, 1.0, 100000.0, N_("Velocidad")),
               4: ("power_max_pct", float, 0.0, 100.0, N_("Potencia")),
               5: ("passes", int, 1, 100, N_("Pasadas"))}
    CHECKS = {6: ("output", N_("Salida de capa")), 7: ("visible", N_("Visibilidad")), 8: ("locked", N_("Bloqueo de capa"))}
    ACTIONS = [("add", "new", N_("Nueva capa")), ("delete", "delete", N_("Eliminar capa (sus objetos pasan a otra)")),
               ("up", None, N_("Subir en el orden de ejecución")), ("down", None, N_("Bajar en el orden de ejecución")),
               ("settings", "settings", N_("Todos los ajustes de la capa…")),
               ("assign", "layers", N_("Asignar la selección a esta capa")),
               ("copy", "copy", N_("Copiar ajustes de esta capa a otras…")),
               ("save_preset", "save", N_("Guardar ajustes como preajuste…")),
               ("apply_preset", "open", N_("Aplicar un preajuste a esta capa…"))]

    def __init__(self, undo: UndoStack, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.undo = undo
        self.doc: Document | None = None
        self._building = False
        self._pushing = False
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) if c else "" for c in self.COLUMNS])
        for col, tip in enumerate(self.COLUMN_TIPS):
            header_item = self.table.horizontalHeaderItem(col)
            if header_item is not None and tip:
                header_item.setToolTip(tr(tip))
        for col, icon_name in ((6, "laser"), (7, "eye"), (8, "lock")):  # cabeceras con icono: columnas estrechas
            header_item = self.table.horizontalHeaderItem(col)
            if header_item is not None:
                header_item.setText("")
                header_item.setIcon(icons.icon(icon_name))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed
                                   | QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAccessibleName(tr("Tabla de capas"))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hh.setMinimumSectionSize(18)
        self.table.viewport().installEventFilter(self)
        self.table.itemChanged.connect(self._item_changed)
        self.table.currentCellChanged.connect(self._row_changed)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        root.addWidget(self.table)
        bar = QGridLayout()
        bar.setSpacing(3)
        self.action_buttons: dict[str, QPushButton] = {}
        for i, (key, icon_name, tip) in enumerate(self.ACTIONS):
            text = {"up": "↑", "down": "↓"}.get(key, "")
            b = QPushButton(icons.icon(icon_name), text) if icon_name else QPushButton(text)
            b.setToolTip(tr(tip))
            b.setAccessibleName(tr(tip))
            b.clicked.connect(lambda _c=False, k=key: self._emit_action(k))
            self.action_buttons[key] = b
            bar.addWidget(b, 0, i)
        root.addLayout(bar)
        note = QLabel(tr("Doble clic en el color para cambiarlo y en un valor para editarlo. Los valores de "
                         "potencia y velocidad deben validarse en su máquina y material."))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        root.addWidget(note)
        undo.listeners.append(self.refresh)

    def set_document(self, doc: Document) -> None:
        self.doc = doc
        self.refresh()

    def _emit_action(self, key: str) -> None:
        lid = self.current_layer_id()
        if lid is None and self.doc is not None and self.doc.layers:
            lid = self.doc.sorted_layers()[0].id
        self.layer_action.emit(key, lid or "")

    def _double_clicked(self, row: int, col: int) -> None:
        item = self.table.item(row, 0)
        if col == 0 and item is not None:
            self.color_requested.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def refresh(self) -> None:
        if self.doc is None or self._pushing:
            return  # el propio panel originó el cambio: no se reconstruyen los widgets
        current = self.current_layer_id()
        self._building = True
        layers = sorted(self.doc.layers, key=lambda ly: ly.order)
        self.table.setRowCount(len(layers))
        for row, layer in enumerate(layers):
            swatch = QTableWidgetItem("■")
            swatch.setForeground(QColor(layer.color))
            swatch.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            swatch.setToolTip(layer.color)
            swatch.setData(Qt.ItemDataRole.UserRole, layer.id)
            self.table.setItem(row, 0, swatch)
            name = QTableWidgetItem(layer.name)
            name.setData(Qt.ItemDataRole.UserRole, layer.id)
            self.table.setItem(row, 1, name)

            mode = QComboBox()
            for i, (m, label) in enumerate(MODE_LABELS.items()):
                mode.addItem(tr(MODE_SHORT[m]), m.value)
                mode.setItemData(i, tr(label), Qt.ItemDataRole.ToolTipRole)
            mode.setToolTip(tr(MODE_LABELS[layer.mode]))
            mode.setCurrentIndex(list(MODE_LABELS).index(layer.mode))
            mode.currentIndexChanged.connect(
                lambda _i, ly=layer, w=mode: self._set(ly, "mode", LayerMode(w.currentData()), tr("Modo de capa")))
            self.table.setCellWidget(row, 2, mode)

            for col, (attr, kind, _lo, _hi, _t) in self.NUMERIC.items():
                value = getattr(layer, attr)
                item = QTableWidgetItem(self._fmt(value, kind))
                item.setData(Qt.ItemDataRole.UserRole, layer.id)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setToolTip(tr(self.COLUMN_TIPS[col]))
                self.table.setItem(row, col, item)
            for col, (attr, _t) in self.CHECKS.items():
                item = QTableWidgetItem("")
                item.setData(Qt.ItemDataRole.UserRole, layer.id)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if getattr(layer, attr) else Qt.CheckState.Unchecked)
                item.setToolTip(tr(self.COLUMN_TIPS[col]))
                self.table.setItem(row, col, item)
            if layer.id == current:
                self.table.setCurrentCell(row, 1)
        if current is None and layers:
            self.table.setCurrentCell(0, 1)
        self._building = False
        self._fit_columns()

    def _fit_columns(self) -> None:
        """Anchos deterministas: columnas fijas compactas y el nombre ocupa el resto."""
        fm = self.table.fontMetrics()
        hfm = self.table.horizontalHeader().fontMetrics()

        def header(col: int) -> int:
            item = self.table.horizontalHeaderItem(col)
            return hfm.horizontalAdvance(item.text()) + 14 if item is not None else 0

        mode_w = max(fm.horizontalAdvance(tr(label)) for label in MODE_SHORT.values()) + 30
        widths = {0: 18, 2: max(mode_w, header(2)),
                  3: max(fm.horizontalAdvance("10000") + 12, header(3)),
                  4: max(fm.horizontalAdvance("99.5") + 10, header(4)),
                  5: max(fm.horizontalAdvance("99") + 12, header(5))}
        for col in self.CHECKS:
            widths[col] = 24
        for col, w in widths.items():
            self.table.setColumnWidth(col, w)
        free = self.table.viewport().width() - sum(widths.values())
        self.table.setColumnWidth(1, max(64, free))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self._fit_columns()
        return super().eventFilter(watched, event)

    def _set(self, layer: Layer, attr: str, value: object, text: str, merge: bool = False) -> None:
        if self._building or getattr(layer, attr) == value:
            return
        self._pushing = True
        try:
            self.undo.push(SetAttribute(layer, attr, value, text), merge=merge)
        finally:
            self._pushing = False
        self.changed.emit()

    @staticmethod
    def _fmt(value: float, kind: type) -> str:
        return str(int(value)) if kind is int else f"{value:g}"

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._building or self.doc is None:
            return
        layer = self.doc.layer(str(item.data(Qt.ItemDataRole.UserRole)))
        if layer is None:
            return
        col = item.column()
        if col == 1:
            if item.text().strip() and item.text() != layer.name:
                self._set(layer, "name", item.text().strip(), tr("Nombre de capa"))
            return
        if col in self.CHECKS:
            attr, text = self.CHECKS[col]
            self._set(layer, attr, item.checkState() == Qt.CheckState.Checked, tr(text))
            return
        if col in self.NUMERIC:
            attr, kind, lo, hi, text = self.NUMERIC[col]
            try:
                value = kind(float(item.text().replace(",", ".").strip()))
            except ValueError:
                value = getattr(layer, attr)
            value = kind(min(max(value, lo), hi))
            self._set(layer, attr, value, tr(text))
            self._building = True
            item.setText(self._fmt(value, kind))  # normaliza (límites, separador decimal)
            self._building = False

    def _row_changed(self, row: int, _col: int, _prow: int, _pcol: int) -> None:
        item = self.table.item(row, 0)
        if item is not None:
            self.layer_selected.emit(str(item.data(Qt.ItemDataRole.UserRole)))

    def current_layer_id(self) -> str | None:
        item = self.table.item(self.table.currentRow(), 0)
        return str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None
