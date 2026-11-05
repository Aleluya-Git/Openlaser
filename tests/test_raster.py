"""Procesamiento ráster: ajustes, remuestreo sobre el marco, tramados y persistencia."""
import numpy as np
import pytest

from openlaser.document.model import ImageRef, demo_document, make_image_shape, new_document
from openlaser.image_processing import raster as R
from openlaser.image_processing.settings import DITHER_METHODS, ImageSettings
from openlaser.persistence.project import ProjectData, load_project, save_project


def gradient(h=60, w=120):
    return np.tile(np.linspace(0, 255, w).astype(np.uint8), (h, 1))


def test_settings_round_trip_and_clamp():
    s = ImageSettings(brightness=500, gamma=0.01, method="stucki", crop=(0.1, 0.0, 0.9, 0.2), lines_per_mm=100)
    c = s.clamped()
    assert c.brightness == 100 and c.gamma == 0.2 and c.lines_per_mm == 40 and c.crop[2] == 0.45
    assert ImageSettings.from_dict(s.to_dict()) == s
    assert ImageSettings.from_dict({"method": "inexistente"}).method == "floyd"
    assert ImageSettings(lines_per_mm=10).dpi == pytest.approx(254)


def test_adjustments():
    g = gradient()
    base = R.adjust(g, ImageSettings())
    assert base.dtype == np.float32 and base.min() == 0 and base.max() == 1
    assert R.adjust(g, ImageSettings(brightness=50)).mean() > base.mean()
    assert R.adjust(g, ImageSettings(exposure=1)).mean() > base.mean()
    assert R.adjust(g, ImageSettings(gamma=2)).mean() > base.mean()
    inv = R.adjust(g, ImageSettings(invert=True))
    assert np.allclose(inv, 1 - base)
    hi = R.adjust(g, ImageSettings(contrast=80))
    assert (hi < 0.05).sum() > (base < 0.05).sum()
    assert R.adjust(g, ImageSettings(background=128)).max() == 1 and (R.adjust(g, ImageSettings(background=128)) == 1).sum() > (base == 1).sum()
    blurred = R.adjust(np.where(np.arange(120) < 60, 0, 255).astype(np.uint8)[None].repeat(10, 0), ImageSettings(blur=3))
    assert 0.1 < blurred[5, 60] < 0.9
    assert R.adjust(g, ImageSettings(crop=(0.25, 0, 0.25, 0))).shape == (60, 60)


def test_sample_frame_axis_aligned_and_rotated():
    img = R.adjust(gradient(), ImageSettings())
    s = R.sample_frame(img, ((10, 10), (70, 10), (10, 40)), 0.5)
    assert (s.rows, s.cols) == (60, 120) and (s.x0, s.y0) == (10, 10)
    assert s.values[:, 0].mean() < 0.05 and s.values[:, -1].mean() > 0.95
    # girado 90°: el degradado pasa a ser vertical
    r = R.sample_frame(img, ((50, 10), (50, 70), (20, 10)), 0.5)
    assert r.cols == 60 and r.rows == 120
    assert r.values[1, :].mean() < 0.1 and r.values[-2, :].mean() > 0.9
    # espejo: el degradado se invierte
    m = R.sample_frame(img, ((70, 10), (10, 10), (70, 40)), 0.5)
    assert m.values[:, 0].mean() > 0.95


def test_sample_frame_rejects_absurd_resolution():
    with pytest.raises(ValueError):
        R.sample_frame(np.ones((10, 10), np.float32), ((0, 0), (5000, 0), (0, 5000)), 0.01)


@pytest.mark.parametrize("method", list(DITHER_METHODS))
def test_every_dither_preserves_mean_tone(method):
    img = R.adjust(gradient(80, 160), ImageSettings())
    out = R.dither(img, ImageSettings(method=method))
    assert out.shape == img.shape and out.min() >= 0 and out.max() <= 1
    assert out.mean() == pytest.approx(1 - img.mean(), abs=0.06)
    if method != "grayscale":
        assert set(np.unique(out).tolist()) <= {0.0, 1.0}


def test_threshold_and_otsu():
    img = np.array([[10] * 50 + [200] * 50], np.uint8)
    t = R.otsu_threshold(img)
    assert 10 < t <= 200
    out = R.dither(R.adjust(img, ImageSettings()), ImageSettings(method="threshold", threshold=t))
    assert out[0, :50].all() and not out[0, 50:].any()


def test_encode_decode_and_limits():
    src = R.RasterSource("a", gradient())
    back = R.decode_source(R.encode_source(src))
    assert back.name == "a" and np.array_equal(back.gray, src.gray)
    with pytest.raises(ValueError):
        R.decode_source(b"no es un npz")
    assert R.limit_size(np.zeros((9000, 100), np.uint8)).shape[0] <= R.MAX_SOURCE_SIDE
    rgba = np.zeros((2, 2, 4), np.uint8)
    rgba[0, 0] = (0, 0, 0, 255)          # negro opaco
    rgba[0, 1] = (0, 0, 0, 0)            # transparente → blanco
    g = R.gray_from_rgba(rgba)
    assert g[0, 0] == 0 and g[0, 1] == 255


def test_image_shape_transforms_with_frame():
    from openlaser.document import operations as ops
    doc = new_document()
    doc.images["r"] = R.RasterSource("a", gradient())
    s = make_image_shape(doc.default_layer().id, "r", 120, 60, 10, 20, 60, 30)
    ops.add_shapes(doc, [s])
    ops.rotate(doc, [s.id], 90)
    ops.flip(doc, [s.id], True)
    moved = doc.shape(s.id)
    assert moved.image is not None
    b = moved.bounds()
    assert b[2] - b[0] == pytest.approx(30) and b[3] - b[1] == pytest.approx(60)
    # las operaciones de trayectoria no deforman una imagen
    ops.simplify(doc, [s.id], 5)
    ops.round_corners(doc, [s.id], 3)
    assert doc.shape(s.id).image == moved.image and ops.combine(doc, [s.id]) is None
    d = moved.to_dict()
    from openlaser.document.model import Shape
    back = Shape.from_dict(d).image
    assert back is not None and back.settings == moved.image.settings
    assert [c for p in back.corners for c in p] == pytest.approx([c for p in moved.image.corners for c in p])
    assert isinstance(ImageRef.from_dict(d["image"]), ImageRef)


def test_project_keeps_images(tmp_path):
    doc = demo_document()
    save_project(tmp_path / "p.olsp", ProjectData(doc))
    back = load_project(tmp_path / "p.olsp").document
    img = next(s for s in back.shapes if s.image is not None)
    assert np.array_equal(back.images[img.image.resource].gray, doc.images[img.image.resource].gray)
    assert img.image.settings.method == "atkinson"


def test_process_and_preview_sizes():
    g = gradient(200, 400)
    sampled = R.process(g, ((0, 0), (40, 0), (0, 20)), ImageSettings(lines_per_mm=5))
    assert sampled.values.shape == (100, 200)
    pv = R.preview(g, ImageSettings(), max_side=100)
    assert max(pv.shape) == 100 and pv.dtype == np.uint8
