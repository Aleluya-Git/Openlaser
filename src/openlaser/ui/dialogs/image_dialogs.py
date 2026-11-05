"""Diálogos de imagen: ajustes y tramado con vista previa en tiempo real, y vectorización."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from openlaser.canvas.qt_geometry import subpaths_to_qpath
from openlaser.canvas.raster_items import gray_to_qimage
from openlaser.geometry import path as gp
from openlaser.i18n import tr
from openlaser.image_processing import raster
from openlaser.image_processing.settings import DITHER_METHODS, ImageSettings
from openlaser.image_processing.trace import TraceSettings, trace

PREVIEW_PX = 520


def _dspin(lo: float, hi: float, value: float, dec: int = 2, step: float = 1.0, suffix: str = "") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setSingleStep(step)
    w.setValue(value)
    w.setKeyboardTracking(False)
    if suffix:
        w.setSuffix(suffix)
    return w


def _ispin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    w = QSpinBox()
    w.setRange(lo, hi)
    w.setValue(value)
    w.setKeyboardTracking(False)
    if suffix:
        w.setSuffix(suffix)
    return w


def _slider_row(spin: QDoubleSpinBox | QSpinBox, factor: float = 1.0) -> QWidget:
    """Control deslizante enlazado con su casilla numérica."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    sl = QSlider(Qt.Orientation.Horizontal)
    sl.setRange(int(spin.minimum() * factor), int(spin.maximum() * factor))
    sl.setValue(int(spin.value() * factor))
    sl.setAccessibleName(spin.accessibleName())
    if isinstance(spin, QSpinBox):
        sl.valueChanged.connect(lambda v: spin.setValue(int(round(v / factor))))
    else:
        sl.valueChanged.connect(lambda v: spin.setValue(v / factor))

    def follow(v: float) -> None:
        sl.blockSignals(True)
        sl.setValue(int(round(v * factor)))
        sl.blockSignals(False)
    spin.valueChanged.connect(follow)
    lay.addWidget(sl, 1)
    lay.addWidget(spin)
    return box


def _pixmap(arr: np.ndarray, max_side: int = PREVIEW_PX) -> QPixmap:
    pm = QPixmap.fromImage(gray_to_qimage(arr))
    if max(pm.width(), pm.height()) > max_side:
        pm = pm.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
    return pm


def _preview_label() -> QLabel:
    lab = QLabel()
    lab.setMinimumSize(PREVIEW_PX, PREVIEW_PX * 3 // 4)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lab.setStyleSheet("QLabel { background: #ffffff; border: 1px solid #888; }")
    return lab


class ImageDialog(QDialog):
    """Ajustes no destructivos de una imagen con vista previa del resultado tramado."""

    def __init__(self, gray: np.ndarray, settings: ImageSettings, width_mm: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Ajustes de imagen"))
        self.gray = gray
        self.width_mm = width_mm
        s = settings.clamped()
        self._crop_now = s.crop  # recorte con el que se midió ``width_mm``
        root = QHBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(380)
        body = QWidget()
        scroll.setWidget(body)
        col = QVBoxLayout(body)
        root.addWidget(scroll)

        g_out = QGroupBox(tr("Salida"))
        f_out = QFormLayout(g_out)
        self.method = QComboBox()
        for key, label in DITHER_METHODS.items():
            self.method.addItem(tr(label), key)
        self.method.setCurrentIndex(max(0, self.method.findData(s.method)))
        self.lpm = _dspin(1, 40, s.lines_per_mm, 2, 0.5, tr(" líneas/mm"))
        self.dpi = _dspin(25, 1016, s.dpi, 0, 10, " DPI")
        self.lpm.valueChanged.connect(lambda v: self._sync_dpi(v * 25.4))
        self.dpi.valueChanged.connect(lambda v: self._sync_lpm(v / 25.4))
        self.threshold = _ispin(0, 255, s.threshold)
        self.cell = _ispin(3, 40, s.halftone_cell, " px")
        self.hangle = _dspin(-90, 90, s.halftone_angle, 0, 5, " °")
        self.sangle = _dspin(-90, 90, s.scan_angle, 1, 5, " °")
        self.sangle.setToolTip(tr("Dirección de las líneas de grabado: 0° horizontal, 90° vertical, 45° en "
                                  "diagonal. Un ángulo distinto de 0 puede alargar el trabajo."))
        f_out.addRow(tr("Tramado"), self.method)
        f_out.addRow(tr("Resolución"), self.lpm)
        f_out.addRow("", self.dpi)
        f_out.addRow(tr("Umbral"), _slider_row(self.threshold))
        f_out.addRow(tr("Celda halftone"), self.cell)
        f_out.addRow(tr("Ángulo halftone"), self.hangle)
        f_out.addRow(tr("Ángulo de barrido"), self.sangle)
        col.addWidget(g_out)

        g_adj = QGroupBox(tr("Ajustes"))
        f_adj = QFormLayout(g_adj)
        self.brightness = _dspin(-100, 100, s.brightness, 0, 1)
        self.contrast = _dspin(-100, 99, s.contrast, 0, 1)
        self.gamma = _dspin(0.2, 5, s.gamma, 2, 0.05)
        self.exposure = _dspin(-3, 3, s.exposure, 2, 0.1, " EV")
        self.sharpen = _dspin(0, 3, s.sharpen, 2, 0.1)
        self.blur = _dspin(0, 10, s.blur, 1, 0.2, " px")
        self.background = _ispin(1, 255, s.background)
        self.background.setToolTip(tr("Los tonos más claros que este valor pasan a blanco (255 = desactivado)"))
        self.invert = QCheckBox(tr("Invertir"))
        self.invert.setChecked(s.invert)
        f_adj.addRow(tr("Brillo"), _slider_row(self.brightness))
        f_adj.addRow(tr("Contraste"), _slider_row(self.contrast))
        f_adj.addRow(tr("Gamma"), _slider_row(self.gamma, 100))
        f_adj.addRow(tr("Exposición"), _slider_row(self.exposure, 100))
        f_adj.addRow(tr("Nitidez"), _slider_row(self.sharpen, 100))
        f_adj.addRow(tr("Desenfoque"), _slider_row(self.blur, 10))
        f_adj.addRow(tr("Eliminar fondo"), _slider_row(self.background))
        f_adj.addRow("", self.invert)
        col.addWidget(g_adj)

        g_crop = QGroupBox(tr("Recorte (% de cada borde)"))
        f_crop = QFormLayout(g_crop)
        self.crop = [_dspin(0, 45, v * 100, 1, 1, " %") for v in s.crop]
        for label, w in zip((tr("Izquierda"), tr("Arriba"), tr("Derecha"), tr("Abajo")), self.crop):
            f_crop.addRow(label, w)
        col.addWidget(g_crop)
        note = QLabel(tr("Girar, voltear, escalar y corregir el aspecto se hace con las herramientas del lienzo "
                         "sobre el marco de la imagen. El original no se modifica."))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        col.addWidget(note)
        reset = QPushButton(tr("Restablecer ajustes"))
        reset.clicked.connect(self._reset)
        col.addWidget(reset)
        col.addStretch(1)

        right = QVBoxLayout()
        cmp = QHBoxLayout()
        self.show_before = QRadioButton(tr("Antes"))
        self.show_after = QRadioButton(tr("Después (resultado)"))
        self.show_after.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.show_before)
        grp.addButton(self.show_after)
        cmp.addWidget(self.show_before)
        cmp.addWidget(self.show_after)
        cmp.addStretch(1)
        right.addLayout(cmp)
        self.view = _preview_label()
        right.addWidget(self.view, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        right.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        root.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._render)
        for w in [self.method, self.lpm, self.threshold, self.cell, self.hangle, self.sangle, self.brightness, self.contrast,
                  self.gamma, self.exposure, self.sharpen, self.blur, self.background, *self.crop]:
            sig = w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged
            sig.connect(lambda *_a: self._schedule())
        self.invert.toggled.connect(lambda _v: self._schedule())
        self.show_before.toggled.connect(lambda _v: self._render())
        self.method.currentIndexChanged.connect(lambda _i: self._enable_fields())
        self._enable_fields()
        self._render()

    def _sync_dpi(self, v: float) -> None:
        self.dpi.blockSignals(True)
        self.dpi.setValue(v)
        self.dpi.blockSignals(False)

    def _sync_lpm(self, v: float) -> None:
        self.lpm.setValue(v)

    def _enable_fields(self) -> None:
        m = self.method.currentData()
        self.threshold.setEnabled(m == "threshold")
        self.cell.setEnabled(m == "halftone")
        self.hangle.setEnabled(m == "halftone")

    def _reset(self) -> None:
        d = ImageSettings()
        for w, v in ((self.brightness, d.brightness), (self.contrast, d.contrast), (self.gamma, d.gamma),
                     (self.exposure, d.exposure), (self.sharpen, d.sharpen), (self.blur, d.blur)):
            w.setValue(v)
        self.background.setValue(d.background)
        self.threshold.setValue(d.threshold)
        for w in self.crop:
            w.setValue(0)
        self.invert.setChecked(False)

    def settings(self) -> ImageSettings:
        return ImageSettings(
            brightness=self.brightness.value(), contrast=self.contrast.value(), gamma=self.gamma.value(),
            exposure=self.exposure.value(), invert=self.invert.isChecked(), background=self.background.value(),
            sharpen=self.sharpen.value(), blur=self.blur.value(), threshold=self.threshold.value(),
            method=str(self.method.currentData()), halftone_cell=self.cell.value(),
            halftone_angle=self.hangle.value(), scan_angle=self.sangle.value(),
            lines_per_mm=self.lpm.value(),
            crop=tuple(w.value() / 100.0 for w in self.crop)).clamped()  # type: ignore[arg-type]

    def _schedule(self) -> None:
        self._timer.start()

    def _render(self) -> None:
        s = self.settings()
        if self.show_before.isChecked():
            arr = raster.preview(self.gray, ImageSettings(), PREVIEW_PX, dithered=False)
        else:
            # el marco conserva la escala: al recortar, la anchura de salida se reduce en proporción
            full_w = self.gray.shape[1]
            out_w = self.width_mm * s.lines_per_mm / max(1e-6, 1 - self._crop_now[0] - self._crop_now[2])
            arr = raster.preview(self.gray, s, PREVIEW_PX, dithered=True, out_px_per_src_px=out_w / max(full_w, 1))
        self.view.setPixmap(_pixmap(arr))
        rows = int(round(self.width_mm * s.lines_per_mm))
        self.info.setText(tr("Salida: {w} píxeles de ancho a {d:.0f} DPI ({i:.3f} mm entre líneas). La vista previa "
                             "se limita a {p} px.", w=rows, d=s.dpi, i=s.interval_mm, p=PREVIEW_PX))


class VectorizeDialog(QDialog):
    """Vectorización con vista previa antes de aplicar y comparación antes/después."""

    def __init__(self, gray: np.ndarray, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Vectorizar imagen"))
        self.gray = gray
        h, w = gray.shape
        self._pscale = min(1.0, 700.0 / max(h, w))
        self.small = self._scaled(self._pscale)
        self.result_subpaths: list[gp.SubPath] = []
        self.result_size = (w, h)
        root = QHBoxLayout(self)
        form_box = QGroupBox(tr("Parámetros"))
        form = QFormLayout(form_box)
        self.auto = QCheckBox(tr("Umbral automático (Otsu)"))
        self.auto.setChecked(True)
        self.threshold = _ispin(0, 255, 128)
        self.invert = QCheckBox(tr("Invertir (vectorizar las zonas claras)"))
        self.noise = _ispin(0, 5, 1, " px")
        self.noise.setToolTip(tr("Supresión de ruido: elimina motas y poros de hasta este radio"))
        self.min_area = _dspin(0, 10000, 12, 0, 1, " px²")
        self.smooth = _ispin(0, 6, 2)
        self.tol = _dspin(0.1, 5, 0.8, 2, 0.1, " px")
        self.corner = _dspin(10, 170, 55, 0, 5, " °")
        self.curves = QCheckBox(tr("Optimizar curvas (Bézier)"))
        self.curves.setChecked(True)
        self.outer = QCheckBox(tr("Sólo contorno exterior"))
        self.mode = QComboBox()
        self.mode.addItem(tr("Línea"), "line")
        self.mode.addItem(tr("Relleno"), "fill")
        self.keep = QCheckBox(tr("Conservar la imagen"))
        self.keep.setChecked(True)
        form.addRow(self.auto)
        form.addRow(tr("Umbral"), _slider_row(self.threshold))
        form.addRow(self.invert)
        form.addRow(tr("Supresión de ruido"), self.noise)
        form.addRow(tr("Área mínima"), self.min_area)
        form.addRow(tr("Suavizado"), self.smooth)
        form.addRow(tr("Tolerancia"), self.tol)
        form.addRow(tr("Ángulo de esquina"), self.corner)
        form.addRow(self.curves)
        form.addRow(self.outer)
        form.addRow(tr("Crear como"), self.mode)
        form.addRow(self.keep)
        root.addWidget(form_box)

        right = QVBoxLayout()
        cmp = QHBoxLayout()
        self.show_before = QRadioButton(tr("Antes"))
        self.show_after = QRadioButton(tr("Después"))
        self.show_overlay = QRadioButton(tr("Superpuesto"))
        self.show_overlay.setChecked(True)
        grp = QButtonGroup(self)
        for b in (self.show_before, self.show_after, self.show_overlay):
            grp.addButton(b)
            cmp.addWidget(b)
            b.toggled.connect(lambda _v: self._draw())
        cmp.addStretch(1)
        right.addLayout(cmp)
        self.view = _preview_label()
        right.addWidget(self.view, 1)
        self.info = QLabel("")
        right.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        right.addWidget(bb)
        root.addLayout(right, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._preview)
        for wdg in (self.threshold, self.noise, self.min_area, self.smooth, self.tol, self.corner):
            wdg.valueChanged.connect(lambda *_a: self._timer.start())
        for chk in (self.auto, self.invert, self.curves, self.outer):
            chk.toggled.connect(lambda _v: self._timer.start())
        self.auto.toggled.connect(lambda v: self.threshold.setEnabled(not v))
        self.threshold.setEnabled(False)
        self._preview_paths: list[gp.SubPath] = []
        self._preview()

    def _scaled(self, f: float) -> np.ndarray:
        if f >= 0.999:
            return self.gray
        h, w = self.gray.shape
        th, tw = max(1, int(h * f)), max(1, int(w * f))
        ys = np.clip(((np.arange(th) + 0.5) / f).astype(np.int64), 0, h - 1)
        xs = np.clip(((np.arange(tw) + 0.5) / f).astype(np.int64), 0, w - 1)
        k = int(1 / f)
        base = raster.block_mean(self.gray.astype(np.float32), k) if k >= 2 else self.gray.astype(np.float32)
        if k >= 2:
            ys = np.clip(ys // k, 0, base.shape[0] - 1)
            xs = np.clip(xs // k, 0, base.shape[1] - 1)
        return np.clip(np.rint(base[np.ix_(ys, xs)]), 0, 255).astype(np.uint8)

    def trace_settings(self, scale: float) -> TraceSettings:
        return TraceSettings(threshold=self.threshold.value(), auto_threshold=self.auto.isChecked(),
                             invert=self.invert.isChecked(), noise=max(0, round(self.noise.value() * scale)),
                             min_area_px=self.min_area.value() * scale * scale, smoothing=self.smooth.value(),
                             tolerance_px=max(0.05, self.tol.value() * scale), corner_deg=self.corner.value(),
                             curves=self.curves.isChecked(), outer_only=self.outer.isChecked())

    def _preview(self) -> None:
        res = trace(self.small, self.trace_settings(self._pscale))
        self._preview_paths = res.subpaths
        if self.auto.isChecked():
            self.threshold.blockSignals(True)
            self.threshold.setValue(min(255, res.threshold))
            self.threshold.blockSignals(False)
        nodes = sum(len(sp.nodes) for sp in res.subpaths)
        self.info.setText(tr("{n} contornos ({o} exteriores, {h} interiores) · {k} nodos · umbral {t}",
                             n=len(res.subpaths), o=res.outer, h=res.holes, k=nodes, t=res.threshold))
        self._draw()

    def _draw(self) -> None:
        h, w = self.small.shape
        if self.show_after.isChecked():
            pm = QPixmap(w, h)
            pm.fill(QColor("white"))
        else:
            pm = QPixmap.fromImage(gray_to_qimage(self.small))
        if not self.show_before.isChecked():
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(220, 30, 40), 1.2)
            pen.setCosmetic(True)
            painter.setPen(pen)
            if self.show_after.isChecked():
                painter.setBrush(QColor(0, 0, 0, 200) if self.mode.currentData() == "fill" else Qt.BrushStyle.NoBrush)
            painter.drawPath(subpaths_to_qpath(self._preview_paths))
            painter.end()
        if max(w, h) > PREVIEW_PX:
            pm = pm.scaled(PREVIEW_PX, PREVIEW_PX, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
        self.view.setPixmap(pm)

    def compute(self) -> tuple[list[gp.SubPath], tuple[int, int]]:
        """Vectorización final (hasta 1600 px de lado) en coordenadas de esa imagen."""
        h, w = self.gray.shape
        f = min(1.0, 1600.0 / max(h, w))
        img = self._scaled(f) if f < 0.999 else self.gray
        res = trace(img, self.trace_settings(f))
        ih, iw = img.shape
        return res.subpaths, (iw, ih)


def map_pixels_to_doc(subpaths: list[gp.SubPath], size: tuple[int, int],
                      corners: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]) -> list[gp.SubPath]:
    """Píxeles (0..w, 0..h) → mm del documento según las esquinas del marco."""
    from openlaser.geometry.affine import Affine
    w, h = size
    (x0, y0), (x1, y1), (x3, y3) = corners
    m = Affine(a=(x1 - x0) / w, b=(y1 - y0) / w, c=(x3 - x0) / h, d=(y3 - y0) / h, e=x0, f=y0)
    return [sp.transformed(m) for sp in subpaths]

