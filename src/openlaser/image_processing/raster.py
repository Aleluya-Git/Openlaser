"""Procesamiento ráster con NumPy: ajustes, remuestreo sobre el marco y tramados.

Convenciones: las imágenes de trabajo son ``float32`` en ``[0, 1]`` con
1 = blanco (no se graba) y 0 = negro (potencia máxima). El resultado de un
tramado es la *fracción de potencia* por píxel en ``[0, 1]`` (binaria salvo en
escala de grises).
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

import numpy as np

from openlaser.i18n import tr
from openlaser.image_processing.settings import ImageSettings

Point = tuple[float, float]
MAX_SOURCE_SIDE = 4000           # las imágenes mayores se reducen al importar
MAX_OUTPUT_PIXELS = 12_000_000   # protección frente a resoluciones absurdas


@dataclass
class RasterSource:
    """Píxeles originales (escala de grises 8 bits, compuestos sobre blanco)."""
    name: str
    gray: np.ndarray  # uint8 (alto, ancho)

    @property
    def width(self) -> int:
        return int(self.gray.shape[1])

    @property
    def height(self) -> int:
        return int(self.gray.shape[0])


# ------------------------------------------------------------------ conversión y E/S
def gray_from_rgba(rgba: np.ndarray) -> np.ndarray:
    """RGBA uint8 (alto, ancho, 4) → gris uint8 compuesto sobre fondo blanco (luminancia BT.601)."""
    rgb = rgba[..., :3].astype(np.float32)
    lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    if rgba.shape[-1] == 4:
        a = rgba[..., 3].astype(np.float32) / 255.0
        lum = lum * a + 255.0 * (1.0 - a)
    return np.clip(np.rint(lum), 0, 255).astype(np.uint8)


def limit_size(gray: np.ndarray, max_side: int = MAX_SOURCE_SIDE) -> np.ndarray:
    h, w = gray.shape
    f = int(math.ceil(max(h, w) / max_side))
    if f <= 1:
        return gray
    return np.clip(np.rint(block_mean(gray.astype(np.float32), f)), 0, 255).astype(np.uint8)


def encode_source(src: RasterSource) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, gray=src.gray, name=np.array(src.name))
    return buf.getvalue()


def decode_source(data: bytes) -> RasterSource:
    """Lee un recurso de imagen; cualquier daño se notifica como ``ValueError``."""
    try:
        return _decode_source(data)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - zip/npz dañado: el llamante sólo necesita saber que falló
        raise ValueError(tr("Imagen dañada")) from exc


def _decode_source(data: bytes) -> RasterSource:
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        with z.zip.open("gray.npy") as fh:  # cabecera: se comprueba el tamaño antes de cargar
            version = np.lib.format.read_magic(fh)
            reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                      else np.lib.format.read_array_header_2_0)
            dims, _fortran, _dtype = reader(fh)
        if int(np.prod(dims)) > MAX_SOURCE_SIDE * MAX_SOURCE_SIDE * 2:
            raise ValueError(tr("Imagen demasiado grande"))
        gray = np.asarray(z["gray"])
        name = str(z["name"]) if "name" in z.files else ""
    if gray.ndim != 2 or gray.dtype != np.uint8 or min(gray.shape) < 1:
        raise ValueError(tr("Imagen dañada"))
    return RasterSource(name, gray)


# --------------------------------------------------------------------------- filtros
def block_mean(img: np.ndarray, f: int) -> np.ndarray:
    """Reducción por promedio de bloques f×f (bordes rellenados con el último valor)."""
    if f <= 1:
        return img
    h, w = img.shape
    ph, pw = (-h) % f, (-w) % f
    if ph or pw:
        img = np.pad(img, ((0, ph), (0, pw)), mode="edge")
    hh, ww = img.shape
    return img.reshape(hh // f, f, ww // f, f).mean(axis=(1, 3))


def _gauss_kernel(sigma: float) -> np.ndarray:
    r = max(1, int(math.ceil(sigma * 3)))
    x = np.arange(-r, r + 1, dtype=np.float32)
    k = np.exp(-(x * x) / (2 * sigma * sigma))
    return k / k.sum()


def gaussian_blur(img: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0.05:
        return img
    k = _gauss_kernel(sigma)
    r = len(k) // 2
    out = img.astype(np.float32)
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        acc = np.zeros_like(out)
        n = out.shape[axis]
        for i, w in enumerate(k):
            sl = [slice(None), slice(None)]
            sl[axis] = slice(i, i + n)
            acc += w * p[tuple(sl)]
        out = acc
    return out


def crop_fraction(img: np.ndarray, crop: tuple[float, float, float, float]) -> np.ndarray:
    h, w = img.shape
    left, top, right, bottom = crop
    x0, x1 = int(round(w * left)), w - int(round(w * right))
    y0, y1 = int(round(h * top)), h - int(round(h * bottom))
    if x1 - x0 < 1 or y1 - y0 < 1:
        return img
    return img[y0:y1, x0:x1]


def adjust(gray_u8: np.ndarray, s: ImageSettings, crop: bool = True) -> np.ndarray:
    """Aplica los ajustes en orden fijo y devuelve float32 [0, 1]."""
    s = s.clamped()
    v = gray_u8.astype(np.float32) / 255.0
    if crop:
        v = crop_fraction(v, s.crop)
    if s.background < 255:
        v = np.where(v >= s.background / 255.0, 1.0, v).astype(np.float32)
    if s.exposure:
        v = v * (2.0 ** s.exposure)
    if s.brightness:
        v = v + s.brightness / 200.0
    if s.contrast:
        f = math.tan((s.contrast / 100.0 + 1.0) * math.pi / 4.0)
        v = (v - 0.5) * f + 0.5
    v = np.clip(v, 0.0, 1.0)
    if abs(s.gamma - 1.0) > 1e-6:
        v = np.power(v, 1.0 / s.gamma)
    if s.blur > 0:
        v = gaussian_blur(v, s.blur)
    if s.sharpen > 0:
        v = v + s.sharpen * (v - gaussian_blur(v, 1.0))
    if s.invert:
        v = 1.0 - v
    return np.clip(v, 0.0, 1.0).astype(np.float32)


# -------------------------------------------------------------- remuestreo al marco
@dataclass
class SampledImage:
    """Rejilla de salida en un sistema girado ``angle_deg`` respecto a los ejes del documento.

    ``values[i, j]`` corresponde al punto girado (x0 + (j+0.5)·pitch, y0 + (i+0.5)·pitch); el punto del
    documento es ese punto girado ``angle_deg`` alrededor del origen (0° = rejilla alineada con X/Y).
    """
    values: np.ndarray
    x0: float
    y0: float
    pitch: float
    angle_deg: float = 0.0

    def to_doc(self, x: float, y: float) -> Point:
        if not self.angle_deg:
            return (x, y)
        a = math.radians(self.angle_deg)
        c, s = math.cos(a), math.sin(a)
        return (x * c - y * s, x * s + y * c)

    @property
    def rows(self) -> int:
        return int(self.values.shape[0])

    @property
    def cols(self) -> int:
        return int(self.values.shape[1])


def frame_bounds(corners: tuple[Point, Point, Point]) -> tuple[float, float, float, float]:
    c0, c1, c3 = corners
    c2 = (c1[0] + c3[0] - c0[0], c1[1] + c3[1] - c0[1])
    xs = [c0[0], c1[0], c2[0], c3[0]]
    ys = [c0[1], c1[1], c2[1], c3[1]]
    return min(xs), min(ys), max(xs), max(ys)


def _rotate_corners(corners: tuple[Point, Point, Point], deg: float) -> tuple[Point, Point, Point]:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return tuple((p[0] * c - p[1] * s, p[0] * s + p[1] * c) for p in corners)  # type: ignore[return-value]


def sample_frame(img: np.ndarray, corners: tuple[Point, Point, Point], pitch: float,
                 angle_deg: float = 0.0) -> SampledImage:
    """Remuestrea ``img`` (float [0,1]) sobre su marco (posible giro/espejo) con paso ``pitch`` mm.

    Con ``angle_deg`` ≠ 0 la rejilla (y por tanto las líneas de barrido) se gira ese ángulo: el marco
    se expresa en el sistema girado, se muestrea allí alineado con los ejes y ``SampledImage.to_doc``
    devuelve cada punto al documento.
    """
    h, w = img.shape
    angle_deg = float(angle_deg) % 360.0
    if angle_deg > 180.0:
        angle_deg -= 360.0
    if abs(angle_deg) < 1e-9:
        angle_deg = 0.0
    if angle_deg:
        corners = _rotate_corners(corners, -angle_deg)
    c0, c1, c3 = corners
    bx0, by0, bx1, by1 = frame_bounds(corners)
    cols = max(1, int(math.ceil((bx1 - bx0) / pitch)))
    rows = max(1, int(math.ceil((by1 - by0) / pitch)))
    if rows * cols > MAX_OUTPUT_PIXELS:
        raise ValueError(tr("Resolución demasiado alta ({c}×{r} píxeles); reduzca las líneas por mm", c=cols, r=rows))
    e1 = (c1[0] - c0[0], c1[1] - c0[1])
    e3 = (c3[0] - c0[0], c3[1] - c0[1])
    det = e1[0] * e3[1] - e1[1] * e3[0]
    if abs(det) < 1e-12:
        return SampledImage(np.ones((rows, cols), np.float32), bx0, by0, pitch, angle_deg)
    # Reducción previa por bloques si la imagen tiene mucha más resolución que la salida.
    src_px_per_mm = w / max(math.hypot(*e1), 1e-9)
    f = int(src_px_per_mm * pitch)
    if f >= 2:
        img = block_mean(img, f)
        h, w = img.shape
    xs = bx0 + (np.arange(cols, dtype=np.float64) + 0.5) * pitch
    ys = by0 + (np.arange(rows, dtype=np.float64) + 0.5) * pitch
    gx, gy = np.meshgrid(xs - c0[0], ys - c0[1])
    a = (gx * e3[1] - gy * e3[0]) / det   # fracción a lo largo de e1 (ancho)
    b = (e1[0] * gy - e1[1] * gx) / det   # fracción a lo largo de e3 (alto)
    inside = (a >= 0) & (a <= 1) & (b >= 0) & (b <= 1)
    u = np.clip(a * w - 0.5, 0, w - 1)
    v = np.clip(b * h - 0.5, 0, h - 1)
    u0 = np.floor(u).astype(np.int64)
    v0 = np.floor(v).astype(np.int64)
    u1 = np.minimum(u0 + 1, w - 1)
    v1 = np.minimum(v0 + 1, h - 1)
    fu = (u - u0).astype(np.float32)
    fv = (v - v0).astype(np.float32)
    top = img[v0, u0] * (1 - fu) + img[v0, u1] * fu
    bot = img[v1, u0] * (1 - fu) + img[v1, u1] * fu
    out = top * (1 - fv) + bot * fv
    out = np.where(inside, out, 1.0).astype(np.float32)
    return SampledImage(out, bx0, by0, pitch, angle_deg)


# ------------------------------------------------------------------------ tramados
_KERNELS: dict[str, tuple[list[tuple[int, int, int]], int]] = {
    "floyd": ([(1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)], 16),
    "atkinson": ([(1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)], 8),
    "jarvis": ([(1, 0, 7), (2, 0, 5), (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
                (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1)], 48),
    "stucki": ([(1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
                (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1)], 42),
    "sierra": ([(1, 0, 5), (2, 0, 3), (-2, 1, 2), (-1, 1, 4), (0, 1, 5), (1, 1, 4), (2, 1, 2),
                (-1, 2, 2), (0, 2, 3), (1, 2, 2)], 32),
    "burkes": ([(1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2)], 32),
}

_BAYER8 = np.array([
    [0, 32, 8, 40, 2, 34, 10, 42], [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38], [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41], [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37], [63, 31, 55, 23, 61, 29, 53, 21]], dtype=np.float32)


def error_diffusion(img: np.ndarray, method: str, serpentine: bool = True) -> np.ndarray:
    """Difusión de error genérica. Devuelve uint8 con 1 = grabar."""
    kernel, div = _KERNELS[method]
    h, w = img.shape
    work = img.astype(np.float64).copy()
    out = np.zeros((h, w), dtype=np.uint8)
    same = [(dx, wt / div) for dx, dy, wt in kernel if dy == 0]
    below: dict[int, list[tuple[int, float]]] = {}
    for dx, dy, wt in kernel:
        if dy > 0:
            below.setdefault(dy, []).append((dx, wt / div))
    for y in range(h):
        rev = serpentine and (y % 2 == 1)
        sgn = -1 if rev else 1
        row = work[y].tolist()
        errs = [0.0] * w
        burn = [0] * w
        for x in (range(w - 1, -1, -1) if rev else range(w)):
            val = row[x]
            if val < 0.5:
                burn[x] = 1
                e = val
            else:
                e = val - 1.0
            errs[x] = e
            for dx, k in same:
                xx = x + sgn * dx
                if 0 <= xx < w:
                    row[xx] += e * k
        out[y] = burn
        e_arr = np.asarray(errs)
        for dy, lst in below.items():
            yy = y + dy
            if yy >= h:
                continue
            target = work[yy]
            for dx, k in lst:
                sh = sgn * dx
                if sh > 0:
                    target[sh:] += e_arr[:-sh] * k
                elif sh < 0:
                    target[:sh] += e_arr[-sh:] * k
                else:
                    target += e_arr * k
    return out


def halftone(img: np.ndarray, cell: int, angle_deg: float) -> np.ndarray:
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = math.radians(angle_deg)
    u = (xx * math.cos(a) + yy * math.sin(a)) / cell
    v = (-xx * math.sin(a) + yy * math.cos(a)) / cell
    spot = 0.5 + 0.25 * (np.cos(2 * np.pi * u) + np.cos(2 * np.pi * v))  # 0 en el centro de la celda
    return ((1.0 - img) > spot).astype(np.uint8)


def dither(img: np.ndarray, s: ImageSettings) -> np.ndarray:
    """Fracción de potencia por píxel (float32 [0, 1])."""
    m = s.method
    if m == "grayscale":
        return (1.0 - img).astype(np.float32)
    if m == "threshold":
        return (img < s.threshold / 255.0).astype(np.float32)
    if m == "bayer":
        h, w = img.shape
        t = (_BAYER8 + 0.5) / 64.0
        tile = np.tile(t, (h // 8 + 1, w // 8 + 1))[:h, :w]
        return (img < tile).astype(np.float32)
    if m == "halftone":
        return halftone(img, max(3, int(s.halftone_cell)), s.halftone_angle).astype(np.float32)
    if m in _KERNELS:
        return error_diffusion(img, m).astype(np.float32)
    raise ValueError(tr("Método de tramado desconocido: {m}", m=m))


def process(src_gray: np.ndarray, corners: tuple[Point, Point, Point], s: ImageSettings) -> SampledImage:
    """Original → ajustes → remuestreo a la resolución de salida → tramado (fracción de potencia)."""
    s = s.clamped()
    adjusted = adjust(src_gray, s)
    sampled = sample_frame(adjusted, corners, s.interval_mm, s.scan_angle)
    sampled.values = dither(sampled.values, s)
    return sampled


def preview(src_gray: np.ndarray, s: ImageSettings, max_side: int = 900, dithered: bool = True,
            out_px_per_src_px: float = 1.0) -> np.ndarray:
    """Vista previa uint8 (255 = blanco) de tamaño acotado para la interfaz.

    ``out_px_per_src_px`` es la relación entre la resolución de salida y la del
    original; el tramado se calcula a la resolución de salida (limitada a
    ``max_side``) para que la vista previa refleje el resultado real.
    """
    s = s.clamped()
    adjusted = adjust(src_gray, s)
    h, w = adjusted.shape
    target = max(1, min(max_side, int(round(max(h, w) * out_px_per_src_px))))
    scale = target / max(h, w)
    th, tw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    ys = np.clip(((np.arange(th) + 0.5) / scale - 0.5).round().astype(np.int64), 0, h - 1)
    xs = np.clip(((np.arange(tw) + 0.5) / scale - 0.5).round().astype(np.int64), 0, w - 1)
    f = int(1 / scale) if scale < 1 else 1
    base = block_mean(adjusted, f) if f >= 2 else adjusted
    if f >= 2:
        bh, bw = base.shape
        ys = np.clip((ys / f).astype(np.int64), 0, bh - 1)
        xs = np.clip((xs / f).astype(np.int64), 0, bw - 1)
    small = base[np.ix_(ys, xs)]
    if dithered:
        power = dither(small, s)
        return np.clip(np.rint(255.0 * (1.0 - power)), 0, 255).astype(np.uint8)
    return np.clip(np.rint(small * 255.0), 0, 255).astype(np.uint8)


def otsu_threshold(gray_u8: np.ndarray) -> int:
    """Umbral automático de Otsu: los grises < valor son primer plano (0-256)."""
    hist = np.bincount(gray_u8.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 128
    omega = np.cumsum(hist) / total
    mu = np.cumsum(hist * np.arange(256)) / total
    mu_t = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sigma_b = (mu_t * omega - mu) ** 2 / (omega * (1 - omega))
    sigma_b = np.nan_to_num(sigma_b)
    best = float(sigma_b.max())
    if best <= 0:
        return 128
    idx = np.flatnonzero(sigma_b >= best * (1 - 1e-9))
    # «gris < umbral» = primer plano; con una meseta de máximos se toma su centro
    return int((idx[0] + idx[-1]) // 2 + 1)
