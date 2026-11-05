"""Vectorización: contornos, agujeros, ruido y curvas."""
import numpy as np
import pytest

from openlaser.geometry import path as gp
from openlaser.image_processing.trace import TraceSettings, contours, denoise, trace


def figure():
    img = np.full((120, 160), 255, np.uint8)
    img[20:100, 20:100] = 0          # cuadrado
    img[45:75, 45:75] = 255          # agujero
    yy, xx = np.mgrid[0:120, 0:160]
    img[(xx - 130) ** 2 + (yy - 60) ** 2 < 15 ** 2] = 0  # círculo aparte
    return img


def test_contours_are_closed_and_oriented():
    loops = contours(figure() < 128)
    assert len(loops) == 3
    areas = sorted(gp.polygon_area(lp) for lp in loops)
    assert areas[0] < 0 < areas[1]           # el agujero tiene el sentido contrario
    assert max(abs(a) for a in areas) == pytest.approx(80 * 80, rel=0.03)


def test_trace_outer_only_and_bounds():
    res = trace(figure(), TraceSettings(noise=0, smoothing=0))
    assert res.outer == 2 and res.holes == 1 and len(res.subpaths) == 3
    sq = max(res.subpaths, key=lambda sp: abs(gp.polygon_area(sp.flatten(0.1))))
    b = gp.bounds_of([sq])
    assert b == pytest.approx((20, 20, 100, 100), abs=0.6)
    outer = trace(figure(), TraceSettings(noise=0, outer_only=True))
    assert len(outer.subpaths) == 2 and outer.holes == 0


def test_square_keeps_corners_and_circle_gets_curves():
    res = trace(figure(), TraceSettings(noise=0, smoothing=1, corner_deg=50))
    sq = max(res.subpaths, key=lambda sp: abs(gp.polygon_area(sp.flatten(0.1))))
    corners = [n for n in sq.nodes if n.cin is None and n.cout is None]
    assert len(corners) >= 4
    circle = min(res.subpaths, key=lambda sp: gp.bounds_of([sp])[0] * -1)
    assert any(n.cin is not None for n in circle.nodes)
    poly = trace(figure(), TraceSettings(noise=0, curves=False))
    assert all(n.cin is None and n.cout is None for sp in poly.subpaths for n in sp.nodes)


def test_noise_and_min_area():
    img = figure()
    rng = np.random.default_rng(1)
    noisy = img.copy()
    noisy[rng.random(img.shape) < 0.01] = 0
    dirty = trace(noisy, TraceSettings(noise=0, min_area_px=0))
    clean = trace(noisy, TraceSettings(noise=1, min_area_px=20))
    assert len(dirty.subpaths) > 20 and len(clean.subpaths) == 3
    m = np.zeros((20, 20), bool)
    m[10, 10] = True
    assert not denoise(m, 1).any()


def test_invert_and_threshold():
    img = figure()
    inv = trace(img, TraceSettings(noise=0, invert=True, outer_only=True))
    b = gp.bounds_of(inv.subpaths)
    assert b is not None and b[2] - b[0] > 150  # el fondo claro abarca toda la imagen
    manual = trace(img, TraceSettings(auto_threshold=False, threshold=1, noise=0))
    assert manual.threshold == 1 and len(manual.subpaths) == 3
