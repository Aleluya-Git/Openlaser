"""Imágenes en el lienzo: conversión NumPy ↔ QImage y vista previa procesada."""
from __future__ import annotations

from collections import OrderedDict

import numpy as np
from PySide6.QtGui import QImage, QPixmap, QTransform

from openlaser.document.model import ImageRef

_PIXMAPS: "OrderedDict[tuple, QPixmap]" = OrderedDict()
_MAX = 24


def gray_to_qimage(arr: np.ndarray) -> QImage:
    a = np.ascontiguousarray(arr.astype(np.uint8))
    h, w = a.shape
    img = QImage(a.data, w, h, w, QImage.Format.Format_Grayscale8)
    return img.copy()  # el QImage no debe apuntar a la memoria de NumPy


def qimage_to_gray(img: QImage) -> np.ndarray:
    """Cualquier QImage → gris uint8 compuesto sobre blanco."""
    from openlaser.image_processing.raster import gray_from_rgba
    rgba = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = rgba.width(), rgba.height()
    bpl = rgba.bytesPerLine()
    buf = np.frombuffer(bytes(rgba.constBits()), dtype=np.uint8)[: bpl * h]
    arr = buf.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)
    return gray_from_rgba(arr)


def preview_pixmap(ref: ImageRef, gray: np.ndarray, dithered: bool = True) -> QPixmap:
    """Vista previa del resultado (tramado a la resolución de salida, acotado a 1200 px)."""
    from openlaser.image_processing.raster import crop_fraction, preview
    s = ref.settings.clamped()
    cropped_w = crop_fraction(gray, s.crop).shape[1]
    out_px = max(1.0, ref.width_mm * s.lines_per_mm)
    key = (id(gray), s, round(out_px), dithered)
    hit = _PIXMAPS.get(key)
    if hit is not None:
        _PIXMAPS.move_to_end(key)
        return hit
    arr = preview(gray, s, max_side=1200, dithered=dithered, out_px_per_src_px=out_px / max(cropped_w, 1))
    pm = QPixmap.fromImage(gray_to_qimage(arr))
    _PIXMAPS[key] = pm
    while len(_PIXMAPS) > _MAX:
        _PIXMAPS.popitem(last=False)
    return pm


def frame_transform(ref: ImageRef, pw: int, ph: int) -> QTransform:
    """Píxel (0,0)-(pw,ph) del mapa de píxeles → esquinas del marco en el documento."""
    (x0, y0), (x1, y1), (x3, y3) = ref.corners
    return QTransform((x1 - x0) / pw, (y1 - y0) / pw, (x3 - x0) / ph, (y3 - y0) / ph, x0, y0)
