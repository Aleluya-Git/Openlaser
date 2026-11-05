"""Calibración de cámara: lente (radial) y área de trabajo (perspectiva con marcadores)."""
from __future__ import annotations

from typing import Callable

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from openlaser.camera.calibration import (
    CameraProfile, LensModel, fit_k1_from_lines, refine_marker, undistort_image,
)
from openlaser.camera.lens import calibrate_chessboard, opencv_available
from openlaser.i18n import tr
from openlaser.ui.camera_source import rgb_to_qimage

Point = tuple[float, float]


class ClickImage(QWidget):
    """Muestra una imagen ajustada al widget; los clics se devuelven en píxeles de la imagen."""
    clicked = Signal(float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(560, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.pixmap = QPixmap()
        self.points: list[tuple[Point, str]] = []          # (punto, etiqueta)
        self.polylines: list[list[Point]] = []
        self.grid = False

    def set_image(self, arr: np.ndarray) -> None:
        self.pixmap = QPixmap.fromImage(rgb_to_qimage(arr))
        self.update()

    def _geom(self) -> tuple[float, float, float]:
        if self.pixmap.isNull():
            return 1.0, 0.0, 0.0
        k = min(self.width() / self.pixmap.width(), self.height() / self.pixmap.height())
        ox = (self.width() - self.pixmap.width() * k) / 2
        oy = (self.height() - self.pixmap.height() * k) / 2
        return k, ox, oy

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.pixmap.isNull() or event.button() != Qt.MouseButton.LeftButton:
            return
        k, ox, oy = self._geom()
        x, y = (event.position().x() - ox) / k, (event.position().y() - oy) / k
        if 0 <= x < self.pixmap.width() and 0 <= y < self.pixmap.height():
            self.clicked.emit(x, y)

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#202020"))
        if self.pixmap.isNull():
            p.setPen(QColor("#ccc"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("Sin imagen: pulse Capturar"))
            return
        k, ox, oy = self._geom()
        p.translate(ox, oy)
        p.scale(k, k)
        p.drawPixmap(0, 0, self.pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.grid:
            pen = QPen(QColor(0, 200, 255, 140), 1)
            pen.setCosmetic(True)
            p.setPen(pen)
            w, h = self.pixmap.width(), self.pixmap.height()
            for i in range(1, 10):
                p.drawLine(QPointF(w * i / 10, 0), QPointF(w * i / 10, h))
                p.drawLine(QPointF(0, h * i / 10), QPointF(w, h * i / 10))
        pen = QPen(QColor(255, 200, 0), 2)
        pen.setCosmetic(True)
        p.setPen(pen)
        for line in self.polylines:
            for a, b in zip(line, line[1:]):
                p.drawLine(QPointF(*a), QPointF(*b))
        r = 6 / k
        for (x, y), label in self.points:
            pen = QPen(QColor(255, 40, 40), 2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
            p.drawLine(QPointF(x, y - r), QPointF(x, y + r))
            if label:
                p.drawText(QPointF(x + r, y - r), label)


class LensDialog(QDialog):
    """Corrección radial: deslizadores, líneas rectas marcadas o tablero de ajedrez (OpenCV)."""

    def __init__(self, capture: Callable[[], np.ndarray | None], profile: CameraProfile,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Calibrar lente"))
        self.capture_fn = capture
        self.raw = capture()
        self.lines: list[list[Point]] = [[]]
        self.boards: list[np.ndarray] = []
        root = QHBoxLayout(self)
        self.view = ClickImage()
        self.view.grid = True
        self.view.clicked.connect(self._clicked)
        root.addWidget(self.view, 1)
        col = QVBoxLayout()
        root.addLayout(col)
        self.k1 = QDoubleSpinBox()
        self.k1.setRange(-0.8, 0.8)
        self.k1.setDecimals(4)
        self.k1.setSingleStep(0.005)
        self.k1.setValue(profile.k1)
        self.k2 = QDoubleSpinBox()
        self.k2.setRange(-0.8, 0.8)
        self.k2.setDecimals(4)
        self.k2.setSingleStep(0.005)
        self.k2.setValue(profile.k2)
        for label, w in ((tr("k1 (barril −, cojín +)"), self.k1), ("k2", self.k2)):
            col.addWidget(QLabel(label))
            col.addWidget(w)
            w.valueChanged.connect(lambda _v: self._render())
        self.show_corrected = QCheckBox(tr("Mostrar corregida"))
        self.show_corrected.setChecked(True)
        self.show_corrected.toggled.connect(lambda _v: self._render())
        col.addWidget(self.show_corrected)
        help_ = QLabel(tr("Con «Mostrar corregida» desactivado, haga clic a lo largo de bordes que deberían ser rectos "
                          "(al menos 3 puntos por línea) y pulse «Calcular k1»."))
        help_.setWordWrap(True)
        help_.setProperty("role", "muted")
        col.addWidget(help_)
        for text, fn in ((tr("Nueva línea"), self._new_line), (tr("Borrar líneas"), self._clear_lines),
                         (tr("Calcular k1 con las líneas"), self._fit), (tr("Capturar de nuevo"), self._recapture)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            col.addWidget(b)
        self.cv_box = QWidget()
        cvl = QVBoxLayout(self.cv_box)
        cvl.setContentsMargins(0, 8, 0, 0)
        cvl.addWidget(QLabel(tr("Tablero de ajedrez 9×6 (OpenCV)")))
        self.btn_board = QPushButton(tr("Añadir imagen del tablero"))
        self.btn_board.clicked.connect(self._add_board)
        self.btn_cv = QPushButton(tr("Calcular con el tablero"))
        self.btn_cv.clicked.connect(self._calibrate_cv)
        cvl.addWidget(self.btn_board)
        cvl.addWidget(self.btn_cv)
        col.addWidget(self.cv_box)
        if not opencv_available():
            self.cv_box.setEnabled(False)
            self.cv_box.setToolTip(tr("Instale opencv-python para calibrar con tablero"))
        self.info = QLabel("")
        self.info.setWordWrap(True)
        col.addWidget(self.info)
        col.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        col.addWidget(bb)
        self._render()

    def lens(self) -> LensModel:
        return LensModel(self.k1.value(), self.k2.value())

    def _render(self) -> None:
        if self.raw is None:
            return
        corrected = self.show_corrected.isChecked()
        self.view.set_image(undistort_image(self.raw, self.lens()) if corrected else self.raw)
        self.view.polylines = [] if corrected else [ln for ln in self.lines if ln]
        self.view.points = [] if corrected else [(p, "") for ln in self.lines for p in ln]
        self.view.update()

    def _clicked(self, x: float, y: float) -> None:
        if self.show_corrected.isChecked():
            self.show_corrected.setChecked(False)
        self.lines[-1].append((x, y))
        self._render()

    def _new_line(self) -> None:
        if self.lines[-1]:
            self.lines.append([])

    def _clear_lines(self) -> None:
        self.lines = [[]]
        self._render()

    def _fit(self) -> None:
        if self.raw is None:
            return
        h, w = self.raw.shape[:2]
        try:
            k = fit_k1_from_lines([ln for ln in self.lines if len(ln) >= 3], w, h)
        except ValueError as exc:
            QMessageBox.warning(self, tr("Calibrar lente"), tr(str(exc)))
            return
        self.k2.setValue(0.0)
        self.k1.setValue(k)
        self.show_corrected.setChecked(True)
        self.info.setText(tr("k1 calculado: {k:.4f}", k=k))

    def _recapture(self) -> None:
        img = self.capture_fn()
        if img is not None:
            self.raw = img
            self._render()

    def _add_board(self) -> None:
        img = self.capture_fn()
        if img is not None:
            self.boards.append(img)
            self.info.setText(tr("{n} imágenes del tablero", n=len(self.boards)))

    def _calibrate_cv(self) -> None:
        try:
            lens, rms, used = calibrate_chessboard(self.boards)
        except (RuntimeError, ValueError) as exc:
            QMessageBox.warning(self, tr("Calibrar lente"), str(exc))
            return
        self.k1.setValue(lens.k1)
        self.k2.setValue(lens.k2)
        self.info.setText(tr("Tablero: {n} imágenes, error {e:.2f} px", n=used, e=rms))


class AreaCalibrationDialog(QDialog):
    """Haga clic sobre cada marcador grabado, en el orden indicado, para calcular la perspectiva."""

    def __init__(self, image: np.ndarray, lens: LensModel, markers_machine: list[Point],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Calibrar área de trabajo"))
        self.lens = lens
        self.raw = image
        self.image = undistort_image(image, lens)
        self.gray = (self.image.mean(axis=2) if self.image.ndim == 3 else self.image).astype(np.uint8)
        self.markers = markers_machine
        self.clicks: list[Point] = []
        self.info_result: dict[str, float] = {}
        root = QHBoxLayout(self)
        self.view = ClickImage()
        self.view.set_image(self.image)
        self.view.clicked.connect(self._clicked)
        root.addWidget(self.view, 1)
        col = QVBoxLayout()
        root.addLayout(col)
        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        col.addWidget(self.list)
        self.refine = QCheckBox(tr("Ajustar al centro del marcador"))
        self.refine.setChecked(True)
        col.addWidget(self.refine)
        b_undo = QPushButton(tr("Deshacer último punto"))
        b_undo.clicked.connect(self._undo)
        col.addWidget(b_undo)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        col.addWidget(self.info)
        warn = QLabel(tr("La cámara sólo ayuda a colocar el diseño: no es un sistema de seguridad ni sustituye al "
                         "frame antes de grabar."))
        warn.setWordWrap(True)
        warn.setProperty("role", "muted")
        col.addWidget(warn)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        col.addWidget(self.buttons)
        self._update()

    def _clicked(self, x: float, y: float) -> None:
        if len(self.clicks) >= len(self.markers):
            return
        if self.refine.isChecked():
            x, y = refine_marker(self.gray, x, y, radius=max(8, self.gray.shape[1] // 60))
        self.clicks.append((x, y))
        self._update()

    def _undo(self) -> None:
        if self.clicks:
            self.clicks.pop()
            self._update()

    def image_points(self) -> list[Point]:
        """Puntos sobre la imagen ya corregida de lente (se usan con la homografía)."""
        return list(self.clicks)

    def _update(self) -> None:
        self.list.clear()
        for i, (mx, my) in enumerate(self.markers):
            done = i < len(self.clicks)
            self.list.addItem(f"{'✔' if done else ('▶' if i == len(self.clicks) else ' ')} {i + 1}: "
                              f"X{mx:.0f} Y{my:.0f}")
        self.view.points = [(p, str(i + 1)) for i, p in enumerate(self.clicks)]
        self.view.update()
        ok = len(self.clicks) == len(self.markers) and len(self.clicks) >= 4
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
        if ok:
            prof = CameraProfile(width=self.image.shape[1], height=self.image.shape[0])
            try:
                self.info_result = prof.calibrate_area(self.clicks, self.markers)
            except ValueError as exc:
                self.info.setText("⛔ " + tr(str(exc)))
                self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
                return
            r = self.info_result
            self.info.setText(tr("Error residual {e:.2f} mm (máx. {m:.2f}) · píxel {p:.2f} mm · precisión estimada "
                                 "±{x:.1f} mm", e=r["rms_mm"], m=r["max_mm"], p=r["px_mm"], x=r["estimate_mm"]))
        else:
            self.info.setText(tr("Haga clic en el marcador {n} de {t}", n=len(self.clicks) + 1, t=len(self.markers)))

