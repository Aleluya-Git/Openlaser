"""Paneles de propiedades y de cámara."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from openlaser.document.model import Document
from openlaser.geometry import path as gp
from openlaser.geometry.coords import CoordinateSystem
from openlaser.image_processing.settings import DITHER_METHODS
from openlaser.i18n import N_, tr
from openlaser.ui import icons


class PropertiesPanel(QWidget):
    """Propiedades editables de la selección (coordenadas de máquina, mm)."""

    geometry_requested = Signal(dict)
    rotate_requested = Signal(float)
    layer_requested = Signal(str)
    name_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel(tr("Sin selección"))
        self.title.setStyleSheet("font-weight: 700;")
        root.addWidget(self.title)
        form = QFormLayout()
        root.addLayout(form)
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("Nombre del objeto"))
        self.name.editingFinished.connect(lambda: self.name_requested.emit(self.name.text().strip()))
        form.addRow(tr("Nombre"), self.name)
        self.layer = QComboBox()
        self.layer.activated.connect(lambda _i: self.layer_requested.emit(str(self.layer.currentData())))
        form.addRow(tr("Capa"), self.layer)
        self.spins: dict[str, QDoubleSpinBox] = {}
        for key, label in (("x", N_("X (máquina)")), ("y", N_("Y (máquina)")), ("w", N_("Anchura")), ("h", N_("Altura"))):
            sp = QDoubleSpinBox()
            sp.setRange(-100000, 100000)
            sp.setDecimals(3)
            sp.setSuffix(" mm")
            sp.setKeyboardTracking(False)
            sp.editingFinished.connect(lambda k=key: self._geometry_edited(k))
            self.spins[key] = sp
            form.addRow(tr(label), sp)
        self.keep = QCheckBox(tr("Mantener proporción"))
        self.keep.setChecked(True)
        form.addRow("", self.keep)
        rot = QHBoxLayout()
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-360, 360)
        self.angle.setDecimals(2)
        self.angle.setSuffix(" °")
        self.angle.setValue(90)
        btn_rot = QPushButton(icons.icon("rotate"), tr("Girar"))
        btn_rot.clicked.connect(lambda: self.rotate_requested.emit(self.angle.value()))
        rot.addWidget(self.angle, 1)
        rot.addWidget(btn_rot)
        form.addRow(tr("Rotación"), rot)
        self.info = QLabel("—")
        self.info.setProperty("role", "muted")
        self.info.setWordWrap(True)
        form.addRow(tr("Detalles"), self.info)
        self.doc_label = QLabel("—")
        self.doc_label.setProperty("role", "muted")
        form.addRow(tr("Documento"), self.doc_label)
        root.addStretch(1)
        self._loading = False
        self._last: dict[str, float] = {}
        self._set_enabled(False)

    def _set_enabled(self, on: bool) -> None:
        for w in (self.name, self.layer, self.keep, self.angle, *self.spins.values()):
            w.setEnabled(on)

    def _geometry_edited(self, key: str) -> None:
        if self._loading:
            return
        value = self.spins[key].value()
        if abs(value - self._last.get(key, value + 1)) < 1e-9:
            return
        self.geometry_requested.emit({"key": key, "value": value, "keep": self.keep.isChecked(),
                                      "current": dict(self._last)})

    def show_selection(self, doc: Document, cs: CoordinateSystem, shape_ids: list[str]) -> None:
        self._loading = True
        try:
            self.doc_label.setText(tr("{n} objetos · {w:g} × {h:g} mm", n=len(doc.shapes), w=doc.width_mm,
                                      h=doc.height_mm))
            shapes = [s for s in doc.shapes if s.id in set(shape_ids)]
            self.layer.clear()
            for ly in doc.sorted_layers():
                self.layer.addItem(icons.color_swatch(ly.color), ly.name, ly.id)
            if not shapes:
                self.title.setText(tr("Sin selección"))
                self.name.clear()
                self.info.setText("—")
                self._set_enabled(False)
                return
            self._set_enabled(True)
            b = gp.bounds_of(sp for s in shapes for sp in s.subpaths) or (0.0, 0.0, 0.0, 0.0)
            c1, c2 = cs.doc_to_machine((b[0], b[1])), cs.doc_to_machine((b[2], b[3]))
            mx0, my0 = min(c1[0], c2[0]), min(c1[1], c2[1])
            self._last = {"x": mx0, "y": my0, "w": b[2] - b[0], "h": b[3] - b[1]}
            for k, v in self._last.items():
                self.spins[k].setValue(v)
            self.title.setText(shapes[0].name or shapes[0].kind if len(shapes) == 1 else tr("{n} objetos", n=len(shapes)))
            self.name.setText(shapes[0].name if len(shapes) == 1 else "")
            self.name.setEnabled(len(shapes) == 1)
            layers = {s.layer_id for s in shapes}
            if len(layers) == 1:
                self.layer.setCurrentIndex(max(0, self.layer.findData(next(iter(layers)))))
            else:
                self.layer.setCurrentIndex(-1)
            nodes = sum(s.node_count() for s in shapes)
            subs = sum(len(s.subpaths) for s in shapes)
            open_ = sum(1 for s in shapes for sp in s.subpaths if not sp.closed)
            grouped = sum(1 for s in shapes if s.group_id)
            text = tr("{s} trayectorias · {n} nodos · {o} abiertas · {g} agrupados",
                      s=subs, n=nodes, o=open_, g=grouped)
            images = [s.image for s in shapes if s.image is not None]
            if len(images) == 1:
                ref = images[0]
                st = ref.settings
                text = tr("Imagen {w}×{h} px · {d:.0f} DPI · tramado {m}", w=ref.px_w, h=ref.px_h, d=st.dpi,
                          m=tr(DITHER_METHODS.get(st.method, st.method))) + "\n" + \
                    tr("Doble clic o Ctrl+Alt+I: ajustes de imagen")
            elif images:
                text += "\n" + tr("{n} imágenes", n=len(images))
            self.info.setText(text)
        finally:
            self._loading = False
