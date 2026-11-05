"""Diálogos de edición: ajustes de capa, matrices, giro, offset, texto, documento y puentes."""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFontComboBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget,
)

from openlaser.document.model import Layer, LayerMode
from openlaser.i18n import N_, tr

MODES = [(LayerMode.LINE, N_("Línea")), (LayerMode.FILL, N_("Relleno")), (LayerMode.FILL_LINE, N_("Relleno + Línea")),
         (LayerMode.IMAGE, N_("Imagen"))]


def _dspin(lo: float, hi: float, value: float, dec: int = 2, suffix: str = "") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    return w


def _ispin(lo: int, hi: int, value: int) -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setValue(value)
    return w


def _buttons(dlg: QDialog) -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


class LayerSettingsDialog(QDialog):
    """Todos los parámetros de salida de una capa."""

    def __init__(self, layer: Layer, phase_note: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Ajustes de capa: {n}", n=layer.name))
        root = QVBoxLayout(self)
        top = QFormLayout()
        self.name = QLineEdit(layer.name)
        top.addRow(tr("Nombre"), self.name)
        self.mode = QComboBox()
        for m, label in MODES:
            self.mode.addItem(tr(label), m.value)
        self.mode.setCurrentIndex(max(0, self.mode.findData(layer.mode.value)))
        top.addRow(tr("Modo"), self.mode)
        root.addLayout(top)

        row = QHBoxLayout()
        g1 = QGroupBox(tr("Velocidad y potencia"))
        f1 = QFormLayout(g1)
        self.speed = _dspin(1, 100000, layer.speed_mm_min, 0, " mm/min")
        self.pmin = _dspin(0, 100, layer.power_min_pct, 1, " %")
        self.pmax = _dspin(0, 100, layer.power_max_pct, 1, " %")
        self.passes = _ispin(1, 100, layer.passes)
        self.m4 = QComboBox()
        self.m4.addItem(tr("M4 dinámico (recomendado)"), True)
        self.m4.addItem(tr("M3 constante"), False)
        self.m4.setCurrentIndex(0 if layer.dynamic_power else 1)
        self.freq = _dspin(0, 100000, layer.frequency, 0)
        self.freq.setSpecialValueText(tr("no se usa"))
        for label, w in ((N_("Velocidad"), self.speed), (N_("Potencia mínima"), self.pmin),
                         (N_("Potencia máxima"), self.pmax), (N_("Pasadas"), self.passes),
                         (N_("Modo láser"), self.m4), (N_("Frecuencia / PPI"), self.freq)):
            f1.addRow(tr(label), w)
        row.addWidget(g1)

        g2 = QGroupBox(tr("Relleno e imagen"))
        f2 = QFormLayout(g2)
        self.interval = _dspin(0.01, 10, layer.line_interval_mm, 3, " mm")
        self.angle = _dspin(-360, 360, layer.fill_angle_deg, 1, " °")
        self.cross = QCheckBox()
        self.cross.setChecked(layer.cross_fill)
        self.bidir = QCheckBox()
        self.bidir.setChecked(layer.bidirectional)
        self.overscan = _dspin(0, 100, layer.overscan_mm, 2, " mm")
        for label, w in ((N_("Intervalo entre líneas"), self.interval), (N_("Ángulo de relleno"), self.angle),
                         (N_("Relleno cruzado"), self.cross), (N_("Bidireccional"), self.bidir),
                         (N_("Overscan"), self.overscan)):
            f2.addRow(tr(label), w)
        row.addWidget(g2)
        root.addLayout(row)

        row2 = QHBoxLayout()
        g3 = QGroupBox(tr("Corte"))
        f3 = QFormLayout(g3)
        self.inside = QCheckBox()
        self.inside.setChecked(layer.inside_first)
        self.optimize = QCheckBox()
        self.optimize.setChecked(layer.optimize_travel)
        self.kerf = _dspin(0, 5, layer.kerf_mm, 3, " mm")
        self.lead_in = _dspin(0, 50, layer.lead_in_mm, 2, " mm")
        self.lead_out = _dspin(0, 50, layer.lead_out_mm, 2, " mm")
        self.pierce = _dspin(0, 10000, layer.pierce_ms, 0, " ms")
        self.pierce.setToolTip(tr("Pausa con el láser encendido (M3) antes de cada contorno"))
        self.lead_in.setToolTip(tr("Entrada en línea recta, prolongando el primer tramo hacia atrás"))
        self.lead_out.setToolTip(tr("Sobrecorte: continúa sobre el contorno cerrado tras volver al inicio"))
        self.tabs = _ispin(0, 50, layer.tab_count)
        self.tabs.setSpecialValueText(tr("sin puentes"))
        self.tab_width = _dspin(0.1, 20, layer.tab_width_mm, 2, " mm")
        for label, w in ((N_("Interior antes que exterior"), self.inside),
                         (N_("Optimizar desplazamientos"), self.optimize), (N_("Kerf"), self.kerf),
                         (N_("Lead-in"), self.lead_in), (N_("Lead-out"), self.lead_out),
                         (N_("Perforación previa"), self.pierce), (N_("Puentes por contorno"), self.tabs),
                         (N_("Ancho de puente"), self.tab_width)):
            f3.addRow(tr(label), w)
        row2.addWidget(g3)
        g4 = QGroupBox(tr("Máquina"))
        f4 = QFormLayout(g4)
        self.air = QCheckBox()
        self.air.setChecked(layer.air_assist)
        self.z = _dspin(-100, 100, layer.z_offset_mm, 2, " mm")
        self.pause = QCheckBox()
        self.pause.setChecked(layer.pause_after)
        self.output = QCheckBox()
        self.output.setChecked(layer.output)
        for label, w in ((N_("Air Assist (M8)"), self.air), (N_("Desplazamiento Z"), self.z),
                         (N_("Pausa al terminar la capa (M0)"), self.pause), (N_("Salida habilitada"), self.output)):
            f4.addRow(tr(label), w)
        row2.addWidget(g4)
        root.addLayout(row2)
        self.notes = QPlainTextEdit(layer.notes)
        self.notes.setMaximumHeight(60)
        self.notes.setPlaceholderText(tr("Notas de la capa"))
        root.addWidget(self.notes)
        if phase_note:
            note = QLabel(tr("GRBL no admite frecuencia/PPI: el valor se guarda para controladores que lo usen. "
                             "Valide siempre los valores en su máquina y material."))
            note.setWordWrap(True)
            note.setProperty("role", "muted")
            root.addWidget(note)
        root.addWidget(_buttons(self))

    def apply_to(self, layer: Layer) -> None:
        layer.name = self.name.text().strip() or layer.name
        layer.mode = LayerMode(self.mode.currentData())
        layer.speed_mm_min = self.speed.value()
        layer.power_min_pct = min(self.pmin.value(), self.pmax.value())
        layer.power_max_pct = self.pmax.value()
        layer.passes = self.passes.value()
        layer.dynamic_power = bool(self.m4.currentData())
        layer.frequency = self.freq.value()
        layer.line_interval_mm = self.interval.value()
        layer.fill_angle_deg = self.angle.value()
        layer.cross_fill = self.cross.isChecked()
        layer.bidirectional = self.bidir.isChecked()
        layer.overscan_mm = self.overscan.value()
        layer.inside_first = self.inside.isChecked()
        layer.optimize_travel = self.optimize.isChecked()
        layer.kerf_mm = self.kerf.value()
        layer.lead_in_mm = self.lead_in.value()
        layer.lead_out_mm = self.lead_out.value()
        layer.pierce_ms = self.pierce.value()
        layer.tab_count = self.tabs.value()
        layer.tab_width_mm = self.tab_width.value()
        layer.air_assist = self.air.isChecked()
        layer.z_offset_mm = self.z.value()
        layer.pause_after = self.pause.isChecked()
        layer.output = self.output.isChecked()
        layer.notes = self.notes.toPlainText()


class RectArrayDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Matriz rectangular"))
        f = QFormLayout(self)
        self.cols = _ispin(1, 500, 3)
        self.rows = _ispin(1, 500, 2)
        self.dx = _dspin(-1000, 1000, 5.0, 2, " mm")
        self.dy = _dspin(-1000, 1000, 5.0, 2, " mm")
        f.addRow(tr("Columnas"), self.cols)
        f.addRow(tr("Filas"), self.rows)
        f.addRow(tr("Separación horizontal"), self.dx)
        f.addRow(tr("Separación vertical"), self.dy)
        f.addRow(_buttons(self))


class CircularArrayDialog(QDialog):
    def __init__(self, center: tuple[float, float], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Matriz circular"))
        f = QFormLayout(self)
        self.count = _ispin(2, 1000, 6)
        self.cx = _dspin(-10000, 10000, center[0], 2, " mm")
        self.cy = _dspin(-10000, 10000, center[1], 2, " mm")
        self.total = _dspin(-360, 360, 360, 1, " °")
        self.rotate = QCheckBox(tr("Girar las copias"))
        self.rotate.setChecked(True)
        f.addRow(tr("Número de elementos"), self.count)
        f.addRow(tr("Centro X (documento)"), self.cx)
        f.addRow(tr("Centro Y (documento)"), self.cy)
        f.addRow(tr("Ángulo total"), self.total)
        f.addRow("", self.rotate)
        f.addRow(_buttons(self))


class CopiesPathDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Copias sobre trayectoria"))
        f = QFormLayout(self)
        self.count = _ispin(1, 1000, 8)
        self.rotate = QCheckBox(tr("Orientar según la trayectoria"))
        self.rotate.setChecked(True)
        f.addRow(QLabel(tr("La última forma seleccionada es la trayectoria guía.")))
        f.addRow(tr("Número de copias"), self.count)
        f.addRow("", self.rotate)
        f.addRow(_buttons(self))


class RotateDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Rotar"))
        f = QFormLayout(self)
        self.angle = _dspin(-360, 360, 90, 2, " °")
        f.addRow(tr("Ángulo (positivo = antihorario)"), self.angle)
        f.addRow(_buttons(self))


class OffsetDialog(QDialog):
    def __init__(self, default: float = 1.0, title: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or tr("Offset"))
        f = QFormLayout(self)
        self.distance = _dspin(0.001, 500, abs(default) or 1.0, 3, " mm")
        self.direction = QComboBox()
        self.direction.addItem(tr("Exterior"), 1.0)
        self.direction.addItem(tr("Interior"), -1.0)
        self.round = QCheckBox(tr("Esquinas redondeadas"))
        self.round.setChecked(True)
        self.keep = QCheckBox(tr("Conservar el original"))
        self.keep.setChecked(True)
        f.addRow(tr("Distancia"), self.distance)
        f.addRow(tr("Dirección"), self.direction)
        f.addRow("", self.round)
        f.addRow("", self.keep)
        f.addRow(_buttons(self))

    def value(self) -> float:
        return self.distance.value() * float(self.direction.currentData())


class TextDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Texto"))
        f = QFormLayout(self)
        self.text = QPlainTextEdit(tr("Texto"))
        self.text.setMaximumHeight(90)
        self.font_box = QFontComboBox()
        self.font_box.setCurrentFont(QFont("Arial"))
        self.size_mm = _dspin(0.5, 500, 10.0, 2, " mm")
        self.bold = QCheckBox(tr("Negrita"))
        self.italic = QCheckBox(tr("Cursiva"))
        f.addRow(tr("Texto"), self.text)
        f.addRow(tr("Fuente del sistema"), self.font_box)
        f.addRow(tr("Altura del cuerpo"), self.size_mm)
        style = QHBoxLayout()
        style.addWidget(self.bold)
        style.addWidget(self.italic)
        f.addRow(tr("Estilo"), style)
        note = QLabel(tr("El texto se convierte en curvas al insertarlo. Texto sobre trayectoria: Fase 3."))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        f.addRow(note)
        f.addRow(_buttons(self))


class DocumentPropertiesDialog(QDialog):
    def __init__(self, name: str, notes: str, width: float, height: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Propiedades del documento"))
        f = QFormLayout(self)
        self.name = QLineEdit(name)
        self.notes = QPlainTextEdit(notes)
        self.notes.setMaximumHeight(90)
        f.addRow(tr("Nombre"), self.name)
        f.addRow(tr("Notas"), self.notes)
        f.addRow(tr("Área de trabajo"), QLabel(f"{width:g} × {height:g} mm"))
        hint = QLabel(tr("El área de trabajo procede del perfil de dispositivo (Máquina → Configuración de dispositivo)."))
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        f.addRow(hint)
        f.addRow(_buttons(self))


class TabsDialog(QDialog):
    """Puentes (tabs) para las capas de la selección."""

    def __init__(self, count: int, width: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Puentes / tabs"))
        form = QFormLayout(self)
        self.count = _ispin(0, 50, count)
        self.count.setSpecialValueText(tr("sin puentes"))
        self.tab_w = _dspin(0.1, 20, width, 2, " mm")
        form.addRow(tr("Puentes por contorno"), self.count)
        form.addRow(tr("Ancho de puente"), self.tab_w)
        note = QLabel(tr("Se aplican a las capas en modo Línea de los objetos seleccionados: en cada contorno "
                         "cerrado el láser se apaga en puntos repartidos por igual."))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        form.addRow(note)
        form.addRow(_buttons(self))
