"""Calibración de cámara sin dependencias pesadas (NumPy).

* **Lente**: modelo radial ``p_d = c + (p_u − c)·(1 + k1·r² + k2·r⁴)`` con ``r``
  normalizado por la semidiagonal de la imagen. ``k1`` se puede ajustar a mano,
  calcular a partir de líneas rectas marcadas sobre la imagen o, si OpenCV está
  instalado, con un tablero de ajedrez (:mod:`openlaser.camera.lens`).
* **Área de trabajo**: homografía imagen (sin distorsión) → máquina a partir de
  marcadores grabados en posiciones conocidas (mínimo 4; con más se estima la
  precisión por el error residual). Corrige la perspectiva.

La cámara **nunca** es un sistema de seguridad: sólo ayuda a colocar el diseño.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import numpy as np

from openlaser.persistence.atomic import atomic_write_json, read_json
from openlaser.i18n import N_

Point = tuple[float, float]


# --------------------------------------------------------------------- lente
@dataclass(frozen=True)
class LensModel:
    k1: float = 0.0
    k2: float = 0.0

    @property
    def identity(self) -> bool:
        return abs(self.k1) < 1e-9 and abs(self.k2) < 1e-9


def _norm(w: int, h: int) -> tuple[float, float, float]:
    return (w - 1) / 2.0, (h - 1) / 2.0, math.hypot(w, h) / 2.0


def distort_points(pts: np.ndarray, lens: LensModel, w: int, h: int) -> np.ndarray:
    cx, cy, rn = _norm(w, h)
    d = (pts - (cx, cy)) / rn
    r2 = (d ** 2).sum(axis=-1, keepdims=True)
    return (cx, cy) + d * (1 + lens.k1 * r2 + lens.k2 * r2 * r2) * rn


def undistort_points(pts: np.ndarray, lens: LensModel, w: int, h: int, iters: int = 20) -> np.ndarray:
    """Inversa del modelo radial por iteración de punto fijo."""
    if lens.identity:
        return np.asarray(pts, dtype=np.float64)
    cx, cy, rn = _norm(w, h)
    dd = (np.asarray(pts, dtype=np.float64) - (cx, cy)) / rn
    u = dd.copy()
    for _ in range(iters):
        r2 = (u ** 2).sum(axis=-1, keepdims=True)
        u = dd / (1 + lens.k1 * r2 + lens.k2 * r2 * r2)
    return (cx, cy) + u * rn


def bilinear(img: np.ndarray, xs: np.ndarray, ys: np.ndarray, fill: float = 0.0) -> np.ndarray:
    h, w = img.shape[:2]
    inside = (xs >= 0) & (xs <= w - 1) & (ys >= 0) & (ys <= h - 1)
    x = np.clip(xs, 0, w - 1)
    y = np.clip(ys, 0, h - 1)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)
    fy = (y - y0)
    if img.ndim == 3:
        fx, fy, inside = fx[..., None], fy[..., None], inside[..., None]
    top = img[y0, x0] * (1 - fx) + img[y0, x1] * fx
    bot = img[y1, x0] * (1 - fx) + img[y1, x1] * fx
    return np.where(inside, top * (1 - fy) + bot * fy, fill)


def undistort_image(img: np.ndarray, lens: LensModel) -> np.ndarray:
    """Imagen corregida del mismo tamaño (gris o RGB/RGBA, uint8)."""
    if lens.identity:
        return img
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    src = distort_points(np.stack([xx, yy], axis=-1), lens, w, h)
    out = bilinear(img.astype(np.float32), src[..., 0], src[..., 1], 0.0)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _line_residual(pts: np.ndarray) -> float:
    c = pts - pts.mean(axis=0)
    if len(c) < 3:
        return 0.0
    s = np.linalg.svd(c, compute_uv=False)
    return float(s[-1] ** 2)


def fit_k1_from_lines(lines: list[list[Point]], w: int, h: int, lo: float = -0.6, hi: float = 0.6) -> float:
    """k1 que hace más rectas las polilíneas marcadas sobre bordes rectos (búsqueda de sección áurea)."""
    arrs = [np.asarray(ln, dtype=np.float64) for ln in lines if len(ln) >= 3]
    if not arrs:
        raise ValueError(N_("Marque al menos una línea recta con 3 puntos o más"))

    def cost(k: float) -> float:
        lens = LensModel(k, 0.0)
        return sum(_line_residual(undistort_points(a, lens, w, h)) for a in arrs)

    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    fc, fd = cost(c), cost(d)
    for _ in range(60):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - g * (b - a)
            fc = cost(c)
        else:
            a, c, fc = c, d, fd
            d = a + g * (b - a)
            fd = cost(d)
    return (a + b) / 2


# --------------------------------------------------------------- homografía
def homography(src: list[Point], dst: list[Point]) -> np.ndarray:
    """H (3×3) con ``dst ~ H·src`` por DLT normalizado (≥ 4 puntos, no alineados)."""
    if len(src) != len(dst) or len(src) < 4:
        raise ValueError(N_("Se necesitan al menos 4 pares de puntos"))
    s = np.asarray(src, dtype=np.float64)
    d = np.asarray(dst, dtype=np.float64)

    def normalizer(p: np.ndarray) -> np.ndarray:
        c = p.mean(axis=0)
        dist = np.sqrt(((p - c) ** 2).sum(axis=1)).mean()
        k = math.sqrt(2) / dist if dist > 1e-12 else 1.0
        return np.array([[k, 0, -k * c[0]], [0, k, -k * c[1]], [0, 0, 1]])

    ts, td = normalizer(s), normalizer(d)
    sh = (ts @ np.c_[s, np.ones(len(s))].T).T
    dh = (td @ np.c_[d, np.ones(len(d))].T).T
    rows = []
    for (x, y, _), (u, v, _) in zip(sh, dh):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    a = np.asarray(rows)
    _u, sv, vt = np.linalg.svd(a)
    if sv[-2] < 1e-10:
        raise ValueError(N_("Puntos degenerados (alineados o repetidos)"))
    hn = vt[-1].reshape(3, 3)
    hm = np.linalg.inv(td) @ hn @ ts
    if abs(hm[2, 2]) < 1e-12:
        raise ValueError(N_("Homografía degenerada"))
    return hm / hm[2, 2]


def apply_h(hm: np.ndarray, pts: np.ndarray | list[Point]) -> np.ndarray:
    p = np.asarray(pts, dtype=np.float64)
    ph = np.c_[p.reshape(-1, 2), np.ones(p.reshape(-1, 2).shape[0])] @ hm.T
    return (ph[:, :2] / ph[:, 2:3]).reshape(p.shape)


def precision_estimate(hm: np.ndarray, img_pts: list[Point], machine_pts: list[Point], w: int, h: int) -> dict[str, float]:
    """Error residual (mm) y tamaño de píxel (mm) en el centro de la imagen."""
    pred = apply_h(hm, img_pts)
    err = np.sqrt(((pred - np.asarray(machine_pts)) ** 2).sum(axis=1))
    c = np.array([[w / 2, h / 2], [w / 2 + 1, h / 2], [w / 2, h / 2 + 1]])
    m = apply_h(hm, c)
    px = float((np.hypot(*(m[1] - m[0])) + np.hypot(*(m[2] - m[0]))) / 2)
    rms = float(np.sqrt((err ** 2).mean()))
    redundant = len(img_pts) > 4
    estimate = max(2 * rms if redundant else 0.0, 1.5 * px)
    return {"rms_mm": rms, "max_mm": float(err.max()), "px_mm": px, "estimate_mm": estimate,
            "redundant": float(redundant)}


def refine_marker(gray: np.ndarray, x: float, y: float, radius: int = 12) -> Point:
    """Centro del grupo de píxeles oscuros cercano al clic (centroide ponderado)."""
    h, w = gray.shape
    x0, x1 = max(0, int(x) - radius), min(w, int(x) + radius + 1)
    y0, y1 = max(0, int(y) - radius), min(h, int(y) + radius + 1)
    win = gray[y0:y1, x0:x1].astype(np.float64)
    if win.size == 0:
        return (x, y)
    thr = (win.min() + win.max()) / 2
    weight = np.clip(thr - win, 0, None)
    if weight.sum() < 1e-9 or win.max() - win.min() < 20:
        return (x, y)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    return (float((xx * weight).sum() / weight.sum()), float((yy * weight).sum() / weight.sum()))


def marker_positions(width: float, height: float, count: int = 4, margin: float = 20.0) -> list[Point]:
    """Posiciones de máquina de los marcadores de calibración (4, 6 o 9)."""
    xs = [margin, width - margin] if count in (4, 6) else [margin, width / 2, width - margin]
    ys = [margin, height - margin] if count == 4 else [margin, height / 2, height - margin]
    if count == 6:
        ys = [margin, height / 2, height - margin]
    return [(x, y) for y in ys for x in xs]


# --------------------------------------------------------------- perfil de cámara
@dataclass
class CameraProfile:
    name: str = "Cámara"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    device: str = ""                 # identificador del dispositivo de vídeo
    machine_profile_id: str = ""     # perfil de máquina asociado
    width: int = 0
    height: int = 0
    k1: float = 0.0
    k2: float = 0.0
    homography: list[float] = field(default_factory=list)   # 9 valores, imagen → máquina (mm)
    image_points: list[list[float]] = field(default_factory=list)
    machine_points: list[list[float]] = field(default_factory=list)
    precision_mm: float = 0.0
    opacity: float = 0.6
    calibrated_on: str = ""

    @property
    def lens(self) -> LensModel:
        return LensModel(self.k1, self.k2)

    @property
    def calibrated(self) -> bool:
        return len(self.homography) == 9

    def h(self) -> np.ndarray:
        return np.asarray(self.homography, dtype=np.float64).reshape(3, 3)

    def calibrate_area(self, image_pts: list[Point], machine_pts: list[Point]) -> dict[str, float]:
        if not self.width or not self.height:
            raise ValueError(N_("Capture primero una imagen con la cámara"))
        hm = homography(image_pts, machine_pts)
        info = precision_estimate(hm, image_pts, machine_pts, self.width, self.height)
        self.homography = [float(v) for v in hm.ravel()]
        self.image_points = [list(map(float, p)) for p in image_pts]
        self.machine_points = [list(map(float, p)) for p in machine_pts]
        self.precision_mm = info["estimate_mm"]
        self.calibrated_on = time.strftime("%Y-%m-%d %H:%M")
        return info

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CameraProfile":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


class CameraStore:
    """Perfiles de cámara en ``cameras.json`` (escritura atómica)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        data = read_json(path, {}) or {}
        self.profiles = [CameraProfile.from_dict(p) for p in data.get("profiles", []) if isinstance(p, dict)]

    def for_device(self, device: str, machine_profile_id: str = "") -> CameraProfile | None:
        cands = [p for p in self.profiles if p.device == device]
        exact = [p for p in cands if p.machine_profile_id == machine_profile_id]
        return (exact or cands or [None])[0]

    def upsert(self, profile: CameraProfile) -> None:
        self.profiles = [p for p in self.profiles if p.id != profile.id] + [profile]
        self.save()

    def save(self) -> None:
        atomic_write_json(self.path, {"format": "openlaser-cameras", "version": 1,
                                      "profiles": [p.to_dict() for p in self.profiles]})


# --------------------------------------------------------------- cámara simulada
@dataclass
class SimulatedCamera:
    """Genera imágenes sintéticas del área de trabajo vista en perspectiva y con distorsión.

    Sirve para la demostración y las pruebas: ``true_h`` (máquina → imagen sin
    distorsión) y ``lens`` son conocidos, así que se puede comprobar la calibración.
    """
    bed_w: float = 400.0
    bed_h: float = 400.0
    width: int = 800
    height: int = 600
    lens: LensModel = LensModel(-0.08, 0.0)
    markers: list[Point] = field(default_factory=list)
    true_h: np.ndarray | None = None

    def __post_init__(self) -> None:
        if self.true_h is None:
            mach = [(0.0, 0.0), (self.bed_w, 0.0), (self.bed_w, self.bed_h), (0.0, self.bed_h)]
            img = [(90.0, 560.0), (730.0, 545.0), (660.0, 70.0), (150.0, 60.0)]  # vista algo inclinada
            self.true_h = homography(mach, img)

    def project(self, machine_pts: list[Point]) -> np.ndarray:
        """Máquina → píxel de la imagen distorsionada."""
        assert self.true_h is not None
        und = apply_h(self.true_h, machine_pts)
        return distort_points(np.asarray(und), self.lens, self.width, self.height)

    def capture(self, extra: list[tuple[float, float, float, float]] | None = None) -> np.ndarray:
        """Imagen gris uint8. ``extra``: rectángulos (x0, y0, x1, y1) en mm dibujados oscuros (material)."""
        assert self.true_h is not None
        hinv = np.linalg.inv(self.true_h)
        yy, xx = np.mgrid[0:self.height, 0:self.width].astype(np.float64)
        und = undistort_points(np.stack([xx, yy], axis=-1), self.lens, self.width, self.height)
        m = apply_h(hinv, und)
        mx, my = m[..., 0], m[..., 1]
        img = np.full((self.height, self.width), 60.0)
        bed = (mx >= 0) & (mx <= self.bed_w) & (my >= 0) & (my <= self.bed_h)
        img[bed] = 205.0
        grid = bed & ((np.abs((mx + 5) % 50 - 5) < 0.8) | (np.abs((my + 5) % 50 - 5) < 0.8))
        img[grid] = 175.0
        for x0, y0, x1, y1 in extra or []:
            img[(mx >= x0) & (mx <= x1) & (my >= y0) & (my <= y1)] = 150.0
        for px, py in self.markers:
            arm = ((np.abs(mx - px) < 0.9) & (np.abs(my - py) < 6)) | ((np.abs(my - py) < 0.9) & (np.abs(mx - px) < 6))
            img[arm] = 25.0
        rng = np.random.default_rng(0)
        img += rng.normal(0, 3, img.shape)
        return np.clip(np.rint(img), 0, 255).astype(np.uint8)
