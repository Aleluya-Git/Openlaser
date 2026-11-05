"""Panel de cámara: dispositivo, captura, superposición sobre el lienzo y calibración."""
from __future__ import annotations

from typing import Any

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from openlaser.camera.calibration import CameraProfile, CameraStore, undistort_image
from openlaser.geometry.coords import CoordinateSystem
from openlaser.i18n import tr
from openlaser.ui import icons
from openlaser.ui.camera_source import SIMULATED_ID, CameraSource, rgb_to_qimage


def overlay_transform(profile: CameraProfile, cs: CoordinateSystem) -> QTransform:
    """Píxel de la imagen corregida → documento: homografía (→ máquina) seguida del cambio máquina→documento."""
    o = cs.machine_to_doc((0.0, 0.0))
    ex = cs.machine_to_doc((1.0, 0.0))
    ey = cs.machine_to_doc((0.0, 1.0))
    a = np.array([[ex[0] - o[0], ey[0] - o[0], o[0]], [ex[1] - o[1], ey[1] - o[1], o[1]], [0.0, 0.0, 1.0]])
    m = a @ profile.h()
    return QTransform(m[0, 0], m[1, 0], m[2, 0], m[0, 1], m[1, 1], m[2, 1], m[0, 2], m[1, 2], m[2, 2])


class CameraPanel(QWidget):
    markers_requested = Signal(int)   # número de marcadores a generar en el documento
    message = Signal(str, str)

    def __init__(self, host: Any, store: CameraStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.host = host            # MainWindow: profile, settings, canvas
        self.store = store
        self.source = CameraSource(self)
        self.source.error.connect(lambda msg: self.message.emit(msg, "warn"))
        self.source.frame_ready.connect(self.capture)
        self.profile: CameraProfile | None = None
        self.last: np.ndarray | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.device = QComboBox()
        self.device.setAccessibleName(tr("Cámara"))
        row.addWidget(self.device, 1)
        self.btn_detect = QPushButton(icons.icon("camera"), tr("Detectar"))
        self.btn_detect.setToolTip(tr("Busca cámaras conectadas"))
        self.btn_detect.clicked.connect(self.detect)
        row.addWidget(self.btn_detect)
        root.addLayout(row)
        self.preview = QLabel(tr("Sin imagen"))
        self.preview.setMinimumHeight(150)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet("border: 1px dashed palette(mid);")
        root.addWidget(self.preview)
        grid = QGridLayout()
        self.btn_capture = QPushButton(icons.icon("camera"), tr("Capturar"))
        self.btn_capture.setToolTip(tr("Captura una imagen y actualiza la superposición"))
        self.btn_capture.clicked.connect(self.capture)
        grid.addWidget(self.btn_capture, 0, 0)
        self.auto = QCheckBox(tr("Actualizar cada"))
        self.interval = QSpinBox()
        self.interval.setRange(1, 600)
        self.interval.setValue(5)
        self.interval.setSuffix(" s")
        arow = QHBoxLayout()
        arow.addWidget(self.auto)
        arow.addWidget(self.interval)
        grid.addLayout(arow, 0, 1)
        self.overlay = QCheckBox(tr("Superponer en el lienzo"))
        self.overlay.setChecked(True)
        grid.addWidget(self.overlay, 1, 0)
        orow = QHBoxLayout()
        orow.addWidget(QLabel(tr("Opacidad")))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(60)
        self.opacity.setAccessibleName(tr("Opacidad de la cámara"))
        orow.addWidget(self.opacity)
        grid.addLayout(orow, 1, 1)
        self.btn_lens = QPushButton(tr("Calibrar lente…"))
        self.btn_area = QPushButton(tr("Calibrar área…"))
        self.btn_markers = QPushButton(tr("Marcadores de calibración"))
        self.btn_markers.setToolTip(tr("Añade al documento cruces en posiciones conocidas para grabarlas con baja "
                                       "potencia sobre un material de prueba"))
        self.markers = QComboBox()
        for n in (4, 6, 9):
            self.markers.addItem(tr("{n} marcadores", n=n), n)
        self.markers.setCurrentIndex(1)
        grid.addWidget(self.btn_lens, 2, 0)
        grid.addWidget(self.btn_area, 2, 1)
        grid.addWidget(self.btn_markers, 3, 0)
        grid.addWidget(self.markers, 3, 1)
        root.addLayout(grid)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        warn = QLabel("⚠ " + tr("La cámara sólo ayuda a colocar el diseño. No es un sistema de seguridad: haga siempre "
                                "un frame antes de grabar y no deje la máquina sin supervisión."))
        warn.setWordWrap(True)
        warn.setProperty("role", "muted")
        root.addWidget(warn)
        root.addStretch(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.capture)
        self.auto.toggled.connect(self._auto_changed)
        self.interval.valueChanged.connect(lambda _v: self._auto_changed(self.auto.isChecked()))
        self.overlay.toggled.connect(lambda _v: self.update_overlay())
        self.opacity.valueChanged.connect(self._opacity_changed)
        self.device.currentIndexChanged.connect(lambda _i: self._device_changed())
        self.btn_markers.clicked.connect(lambda: self.markers_requested.emit(int(self.markers.currentData())))
        self.btn_lens.clicked.connect(lambda: self.calibrate_lens())
        self.btn_area.clicked.connect(lambda: self.calibrate_area())
        self.markers.currentIndexChanged.connect(lambda _i: setattr(self.source, "sim_markers",
                                                                    int(self.markers.currentData() or 6)))
        self.detect()

    # ------------------------------------------------------------- dispositivo
    def detect(self) -> None:
        current = self.host.settings.camera_device or SIMULATED_ID
        self.device.blockSignals(True)
        self.device.clear()
        for dev_id, name in CameraSource.devices():
            self.device.addItem(name, dev_id)
        self.device.setCurrentIndex(max(0, self.device.findData(current)))
        self.device.blockSignals(False)
        self._device_changed()

    def _device_changed(self) -> None:
        dev = str(self.device.currentData() or SIMULATED_ID)
        self.host.settings.camera_device = dev
        self.source.sim_markers = int(self.markers.currentData() or 6)
        if self.isVisible() or dev == SIMULATED_ID:
            self.source.open(dev)
        mid = self.host.profile.id
        self.profile = self.store.for_device(dev, mid) or CameraProfile(
            name=self.device.currentText(), device=dev, machine_profile_id=mid)
        self.opacity.setValue(int(self.profile.opacity * 100))
        self._show_status()
        self.update_overlay()

    def showEvent(self, event) -> None:  # noqa: ANN001
        super().showEvent(event)
        if not self.source.is_simulated and self.source._camera is None:  # noqa: SLF001
            self.source.open(self.source.device_id or str(self.device.currentData()))

    def hideEvent(self, event) -> None:  # noqa: ANN001
        self.timer.stop()
        if not self.source.is_simulated:
            self.source.close()  # libera la cámara cuando el panel no se ve
        super().hideEvent(event)

    def _auto_changed(self, on: bool) -> None:
        if on:
            self.timer.start(self.interval.value() * 1000)
        else:
            self.timer.stop()

    def _opacity_changed(self, v: int) -> None:
        if self.profile is not None:
            self.profile.opacity = v / 100.0
        self.host.canvas.view.set_camera_opacity(v / 100.0)

    # ---------------------------------------------------------------- captura
    def grab_frame(self) -> np.ndarray | None:
        p = self.host.profile
        self.source.sim_markers = int(self.markers.currentData() or 6)
        return self.source.capture((p.width_mm, p.height_mm))

    def capture(self) -> np.ndarray | None:
        img = self.grab_frame()
        if img is None:
            self.status.setText(tr("Esperando imagen de la cámara…"))
            return None
        self.last = img
        if self.profile is not None and (self.profile.width, self.profile.height) != (img.shape[1], img.shape[0]):
            if self.profile.calibrated:
                self.message.emit(tr("La resolución de la cámara cambió: vuelva a calibrar el área"), "warn")
                self.profile.homography = []
            self.profile.width, self.profile.height = img.shape[1], img.shape[0]
        pm = QPixmap.fromImage(rgb_to_qimage(img))
        self.preview.setPixmap(pm.scaled(self.preview.width(), 150, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        self.update_overlay()
        return img

    def update_overlay(self) -> None:
        view = self.host.canvas.view
        prof = self.profile
        if self.last is None or prof is None or not prof.calibrated or not self.overlay.isChecked():
            view.set_camera_overlay(None, None)
            return
        corrected = undistort_image(self.last, prof.lens)
        cs = CoordinateSystem.from_profile(self.host.profile)
        view.set_camera_overlay(rgb_to_qimage(corrected), overlay_transform(prof, cs), self.opacity.value() / 100.0)

    def _show_status(self) -> None:
        prof = self.profile
        if prof is None or not prof.calibrated:
            self.status.setText(tr("Sin calibrar: genere los marcadores, grábelos y pulse «Calibrar área…»"))
        else:
            self.status.setText(tr("Calibrada el {d} · precisión estimada ±{p:.1f} mm · lente k1 {k:.3f}",
                                   d=prof.calibrated_on or "—", p=prof.precision_mm, k=prof.k1))

    # ------------------------------------------------------------- calibración
    def calibrate_lens(self) -> bool:
        from openlaser.ui.dialogs.camera_dialogs import LensDialog
        if self.profile is None or self.capture() is None:
            return False
        dlg = LensDialog(self.grab_frame, self.profile, self)
        if not dlg.exec():
            return False
        lens = dlg.lens()
        if (lens.k1, lens.k2) != (self.profile.k1, self.profile.k2) and self.profile.calibrated:
            self.message.emit(tr("La lente cambió: vuelva a calibrar el área de trabajo"), "warn")
            self.profile.homography = []
        self.profile.k1, self.profile.k2 = lens.k1, lens.k2
        self.store.upsert(self.profile)
        self._show_status()
        self.update_overlay()
        return True

    def calibrate_area(self, dialog_hook: Any = None) -> bool:
        from openlaser.camera.calibration import marker_positions
        from openlaser.ui.dialogs.camera_dialogs import AreaCalibrationDialog
        img = self.capture()
        if img is None or self.profile is None:
            return False
        p = self.host.profile
        markers = marker_positions(p.width_mm, p.height_mm, int(self.markers.currentData() or 6))
        dlg = AreaCalibrationDialog(img, self.profile.lens, markers, self)
        if dialog_hook is not None:
            dialog_hook(dlg)
        if not dlg.exec():
            return False
        self.profile.width, self.profile.height = img.shape[1], img.shape[0]
        self.profile.machine_profile_id = p.id
        info = self.profile.calibrate_area(dlg.image_points(), markers)
        self.store.upsert(self.profile)
        self._show_status()
        self.update_overlay()
        level = "warn" if info["estimate_mm"] > 2.0 else "info"
        self.message.emit(tr("Cámara calibrada: precisión estimada ±{p:.1f} mm", p=info["estimate_mm"]), level)
        return True
