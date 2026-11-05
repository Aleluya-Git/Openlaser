"""Fuentes de imagen para la cámara: dispositivos reales (Qt Multimedia) y una cámara simulada."""
from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from openlaser.camera.calibration import SimulatedCamera, marker_positions
from openlaser.i18n import tr

_log = logging.getLogger(__name__)
SIMULATED_ID = "simulada"


def qimage_to_rgb(img: QImage) -> np.ndarray:
    rgb = img.convertToFormat(QImage.Format.Format_RGB888)
    w, h, bpl = rgb.width(), rgb.height(), rgb.bytesPerLine()
    buf = np.frombuffer(bytes(rgb.constBits()), dtype=np.uint8)[: bpl * h]
    return buf.reshape(h, bpl)[:, : w * 3].reshape(h, w, 3).copy()


def rgb_to_qimage(arr: np.ndarray) -> QImage:
    a = np.ascontiguousarray(arr if arr.ndim == 3 else np.repeat(arr[..., None], 3, axis=2)).astype(np.uint8)
    h, w = a.shape[:2]
    return QImage(a.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def multimedia_available() -> bool:
    try:
        from PySide6.QtMultimedia import QMediaDevices  # noqa: F401
    except ImportError:
        return False
    return True


class CameraSource(QObject):
    """Abre un dispositivo y conserva el último fotograma. La cámara simulada genera la imagen al capturar."""
    frame_ready = Signal()
    error = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.device_id = ""
        self._camera = None
        self._session = None
        self._sink = None
        self._last: QImage | None = None
        self.simulated = SimulatedCamera()
        self.sim_markers = 6

    @staticmethod
    def devices() -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if multimedia_available():
            try:
                from PySide6.QtMultimedia import QMediaDevices
                for dev in QMediaDevices.videoInputs():
                    out.append((bytes(dev.id().data()).decode("latin-1"), dev.description()))
            except Exception:  # noqa: BLE001 - controladores de vídeo defectuosos no deben impedir trabajar
                _log.exception("No se pudieron enumerar las cámaras")
        out.append((SIMULATED_ID, tr("Cámara simulada (demostración)")))
        return out

    def open(self, device_id: str) -> bool:
        self.close()
        self.device_id = device_id
        if device_id == SIMULATED_ID or not device_id:
            return True
        if not multimedia_available():
            self.error.emit(tr("Qt Multimedia no está disponible en esta instalación"))
            return False
        from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices, QVideoSink
        dev = next((d for d in QMediaDevices.videoInputs() if bytes(d.id().data()).decode("latin-1") == device_id), None)
        if dev is None:
            self.error.emit(tr("La cámara ya no está conectada"))
            return False
        self._camera = QCamera(dev)
        self._session = QMediaCaptureSession()
        self._sink = QVideoSink()
        self._session.setCamera(self._camera)
        self._session.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_frame)
        self._camera.errorOccurred.connect(lambda _e, msg: self.error.emit(msg))
        self._camera.start()
        return True

    def _on_frame(self, frame) -> None:  # noqa: ANN001 - QVideoFrame
        img = frame.toImage()
        if not img.isNull():
            first = self._last is None
            self._last = img
            if first:
                self.frame_ready.emit()

    def close(self) -> None:
        if self._camera is not None:
            try:
                self._camera.stop()
            except RuntimeError:
                pass
        self._camera = self._session = self._sink = None
        self._last = None

    @property
    def is_simulated(self) -> bool:
        return self.device_id in ("", SIMULATED_ID)

    def capture(self, bed: tuple[float, float] = (400.0, 400.0)) -> np.ndarray | None:
        """Imagen RGB uint8 del último fotograma (o una nueva si es la simulada)."""
        if self.is_simulated:
            sim = self.simulated
            if (sim.bed_w, sim.bed_h) != bed:
                self.simulated = sim = SimulatedCamera(bed_w=bed[0], bed_h=bed[1])
            sim.markers = marker_positions(bed[0], bed[1], self.sim_markers)
            gray = sim.capture([(bed[0] * 0.3, bed[1] * 0.3, bed[0] * 0.6, bed[1] * 0.55)])
            return np.repeat(gray[..., None], 3, axis=2)
        if self._last is None:
            return None
        return qimage_to_rgb(self._last)
