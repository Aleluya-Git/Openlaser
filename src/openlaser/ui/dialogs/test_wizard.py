"""Asistente de pruebas: parámetros, vista previa del resultado y confirmación."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from openlaser.canvas.qt_geometry import subpaths_to_qpath
from openlaser.devices.profile import DeviceProfile
from openlaser.document.model import Document
from openlaser.i18n import tr
from openlaser.materials.test_patterns import TEST_KINDS, TestPlan, build, defaults


class TestWizardDialog(QDialog):
    """Genera la prueba en el documento; la ejecución sigue el flujo normal (vista previa + comprobación previa)."""

    def __init__(self, kind: str, profile: DeviceProfile, doc: Document, initial: dict[str, Any] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        tk = TEST_KINDS[kind]
        self.kind, self.profile, self.doc = kind, profile, doc
        self.plan: TestPlan | None = None
        self.setWindowTitle(tr(tk.title))
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        desc = QLabel(tr(tk.description))
        desc.setWordWrap(True)
        left.addWidget(desc)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        body = QWidget()
        form = QFormLayout(body)
        scroll.setWidget(body)
        left.addWidget(scroll, 1)
        root.addLayout(left)
        values = defaults(kind)
        values.update(initial or {})
        self.widgets: dict[str, QWidget] = {}
        for p in tk.params:
            v = values.get(p.key, p.default)
            w: QWidget
            if p.kind == "bool":
                cb = QCheckBox()
                cb.setChecked(bool(v))
                cb.toggled.connect(lambda _v: self._schedule())
                w = cb
            elif p.kind == "choice":
                combo = QComboBox()
                for key, label in p.choices:
                    combo.addItem(tr(label), key)
                combo.setCurrentIndex(max(0, combo.findData(v)))
                combo.currentIndexChanged.connect(lambda _i: self._schedule())
                w = combo
            elif p.kind == "int":
                sp = QSpinBox()
                sp.setRange(int(p.lo), int(p.hi))
                sp.setValue(int(v))
                sp.setSuffix(p.suffix)
                sp.valueChanged.connect(lambda _v: self._schedule())
                w = sp
            elif p.kind == "float":
                dsp = QDoubleSpinBox()
                dsp.setRange(p.lo, p.hi)
                dsp.setDecimals(3 if p.hi <= 10 else 1)
                dsp.setValue(float(v))
                dsp.setSuffix(p.suffix)
                dsp.valueChanged.connect(lambda _v: self._schedule())
                w = dsp
            else:
                le = QLineEdit(str(v))
                le.textChanged.connect(lambda _t: self._schedule())
                w = le
            w.setAccessibleName(tr(p.label))
            self.widgets[p.key] = w
            form.addRow(tr(p.label), w)

        right = QVBoxLayout()
        self.preview = QLabel()
        self.preview.setMinimumSize(420, 320)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("QLabel { background: #fdfdf8; border: 1px solid #888; }")
        right.addWidget(self.preview, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)
        note = QLabel(tr("La prueba se añade al documento como geometría editable en capas nuevas. Para grabarla use "
                         "Vista previa y después Iniciar: se pedirá la comprobación previa de seguridad."))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        right.addWidget(note)
        self.confirm = QCheckBox(tr("He revisado los parámetros y entiendo que debo validarlos en mi máquina"))
        right.addWidget(self.confirm)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Añadir al documento"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        right.addWidget(self.buttons)
        root.addLayout(right, 1)
        self.confirm.toggled.connect(lambda _v: self._update_ok())
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._rebuild)
        self._rebuild()

    def values(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, w in self.widgets.items():
            if isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            elif isinstance(w, QComboBox):
                out[key] = w.currentData()
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                out[key] = w.value()
            elif isinstance(w, QLineEdit):
                out[key] = w.text()
        return out

    def _schedule(self) -> None:
        self._timer.start()

    def _update_ok(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.plan is not None
                                                                           and self.confirm.isChecked())

    def _rebuild(self) -> None:
        try:
            self.plan = build(self.kind, self.values(), self.profile, self.doc)
            self.summary.setStyleSheet("")
            self.summary.setText(self.plan.summary())
            self._draw(self.plan)
        except ValueError as exc:
            self.plan = None
            self.summary.setStyleSheet("color: #d1242f; font-weight: 700;")
            self.summary.setText("⛔ " + str(exc))
            self.preview.setPixmap(QPixmap())
        self._update_ok()

    def _draw(self, plan: TestPlan) -> None:
        from openlaser.geometry import path as gp
        b = gp.bounds_of(sp for s in plan.shapes for sp in s.subpaths)
        pm = QPixmap(420, 320)
        pm.fill(QColor("#fdfdf8"))
        if b is None:
            self.preview.setPixmap(pm)
            return
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = max(b[2] - b[0], 1e-3), max(b[3] - b[1], 1e-3)
        k = min(400 / w, 300 / h)
        painter.translate(210 - (b[0] + w / 2) * k, 160 - (b[1] + h / 2) * k)
        painter.scale(k, k)
        layers = {ly.id: ly for ly in plan.layers}
        for s in plan.shapes:
            ly = layers.get(s.layer_id)
            color = QColor(ly.color if ly else "#000")
            pen = QPen(color, 1.2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            if ly is not None and ly.mode.value in ("fill", "fill_line"):
                fill = QColor(color)
                fill.setAlpha(int(60 + 170 * ly.power_max_pct / 100))
                painter.setBrush(fill)
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(subpaths_to_qpath(s.subpaths))
        painter.resetTransform()
        painter.setPen(QPen(QColor("#888")))
        painter.drawText(QRectF(4, 300, 412, 18), Qt.AlignmentFlag.AlignRight,
                         f"{w:.0f} × {h:.0f} mm")
        painter.end()
        self.preview.setPixmap(pm)
