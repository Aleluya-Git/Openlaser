"""Biblioteca de formas y texto sobre trayectoria."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFontComboBox, QFormLayout, QLineEdit,
    QListWidget, QListWidgetItem, QVBoxLayout, QWidget,
)

from openlaser.canvas.qt_geometry import subpaths_to_qpath, text_to_subpaths
from openlaser.geometry import path as gp
from openlaser.geometry.shape_library import SHAPES, place_on_path
from openlaser.i18n import tr
from openlaser.ui import theme


def _thumb(shape_id: str) -> QIcon:
    pm = QPixmap(56, 56)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(theme.current().text), 1.6)
    pen.setCosmetic(True)
    p.setPen(pen)
    p.drawPath(subpaths_to_qpath(SHAPES[shape_id].build(28, 28, 46)))
    p.end()
    return QIcon(pm)


class ShapeLibraryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Biblioteca de formas"))
        root = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(56, 56))
        self.list.setGridSize(QSize(110, 96))
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setMinimumSize(480, 240)
        for sid, sh in SHAPES.items():
            it = QListWidgetItem(_thumb(sid), tr(sh.name))
            it.setData(Qt.ItemDataRole.UserRole, sid)
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        root.addWidget(self.list)
        form = QFormLayout()
        self.size_mm = QDoubleSpinBox()
        self.size_mm.setRange(1, 2000)
        self.size_mm.setValue(40)
        self.size_mm.setSuffix(" mm")
        form.addRow(tr("Tamaño"), self.size_mm)
        root.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def shape_id(self) -> str:
        it = self.list.currentItem()
        return str(it.data(Qt.ItemDataRole.UserRole)) if it is not None else "heart"


class TextOnPathDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Texto sobre trayectoria"))
        form = QFormLayout(self)
        self.text = QLineEdit(tr("Texto sobre trayectoria"))
        self.font_box = QFontComboBox()
        self.size_mm = QDoubleSpinBox()
        self.size_mm.setRange(1, 500)
        self.size_mm.setValue(8)
        self.size_mm.setSuffix(" mm")
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(-20, 100)
        self.spacing.setValue(0.5)
        self.spacing.setSuffix(" mm")
        self.offset = QDoubleSpinBox()
        self.offset.setRange(-200, 200)
        self.offset.setValue(0)
        self.offset.setSuffix(" mm")
        self.offset.setToolTip(tr("Distancia de la línea base a la trayectoria (positivo = por encima)"))
        self.align = QComboBox()
        for key, label in (("start", tr("Inicio")), ("center", tr("Centro")), ("end", tr("Final"))):
            self.align.addItem(label, key)
        self.align.setCurrentIndex(1)
        self.reverse = QCheckBox(tr("Invertir sentido de la trayectoria"))
        for label, w in ((tr("Texto"), self.text), (tr("Fuente del sistema"), self.font_box),
                         (tr("Altura del cuerpo"), self.size_mm), (tr("Espaciado extra"), self.spacing),
                         (tr("Desplazamiento"), self.offset), (tr("Alineación"), self.align)):
            form.addRow(label, w)
        form.addRow(self.reverse)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def build(self, path: gp.SubPath) -> list[gp.SubPath]:
        family = self.font_box.currentFont().family()
        size = self.size_mm.value()
        font = QFont(family)
        font.setPixelSize(100)
        fm = QFontMetricsF(font)
        glyphs = []
        for ch in self.text.text():
            adv = fm.horizontalAdvance(ch) * size / 100.0
            subs = text_to_subpaths(ch, family, size) if ch.strip() else []
            glyphs.append((subs, adv))
        sp = path.reversed() if self.reverse.isChecked() else path
        return place_on_path(glyphs, sp, 0.0, self.spacing.value(), self.offset.value(),
                             str(self.align.currentData()))
