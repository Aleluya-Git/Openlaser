"""Calibración de lente con tablero de ajedrez (opcional: requiere OpenCV).

OpenCV (Apache-2.0) no es obligatorio. Si no está instalado, la calibración de
lente se hace con el ajuste manual de ``k1`` o marcando líneas rectas
(:func:`openlaser.camera.calibration.fit_k1_from_lines`).
"""
from __future__ import annotations

import math

import numpy as np

from openlaser.camera.calibration import LensModel


def opencv_available() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def calibrate_chessboard(images: list[np.ndarray], pattern: tuple[int, int] = (9, 6)) -> tuple[LensModel, float, int]:
    """Devuelve (modelo, error RMS en píxeles, imágenes usadas). Requiere ≥ 3 imágenes válidas."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV no está instalado: pip install opencv-python") from exc
    objp = np.zeros((pattern[0] * pattern[1], 3), np.float32)
    objp[:, :2] = np.mgrid[0:pattern[0], 0:pattern[1]].T.reshape(-1, 2)
    obj_pts, img_pts = [], []
    size = None
    for img in images:
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        size = gray.shape[::-1]
        ok, corners = cv2.findChessboardCorners(gray, pattern)
        if ok:
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1),
                                       (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001))
            obj_pts.append(objp)
            img_pts.append(corners)
    if len(obj_pts) < 3 or size is None:
        raise ValueError(f"Tablero detectado en {len(obj_pts)} imágenes; se necesitan al menos 3")
    rms, mtx, dist, _r, _t = cv2.calibrateCamera(obj_pts, img_pts, size, None, None)
    f = float((mtx[0, 0] + mtx[1, 1]) / 2)
    hd = math.hypot(*size) / 2
    k1 = float(dist[0, 0]) * (hd / f) ** 2
    k2 = float(dist[0, 1]) * (hd / f) ** 4
    return LensModel(k1, k2), float(rms), len(obj_pts)
