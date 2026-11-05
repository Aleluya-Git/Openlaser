"""G-code de relleno e imagen, entrada/salida, puentes, perforación y redondeo."""
import math

import numpy as np
import pytest

from openlaser.devices.profile import demo_profile
from openlaser.document.model import Document, Layer, LayerMode, make_image_shape, make_shape, new_document
from openlaser.gcode.analyzer import analyze, toolpath_segments
from openlaser.gcode.generator import generate_job
from openlaser.geometry import path as gp
from openlaser.image_processing.raster import RasterSource
from openlaser.image_processing.settings import ImageSettings
from openlaser.safety.manager import SafetyManager
from openlaser.toolpath.cutpath import build_steps, lead_in_point, tab_intervals
from openlaser.toolpath.fill import hatch


def no_power_on_rapids(lines):
    s = 0.0
    for ln in lines:
        if "S" in ln and not ln.startswith(";"):
            s = float(ln.split("S")[1].split("F")[0] or 0)
        if ln.startswith("G0"):
            assert s == 0.0, f"G0 con S={s} activo: {ln}"
            assert "S" not in ln


def test_hatch_even_odd_union_and_angle():
    outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
    hole = [(3, 3), (7, 3), (7, 7), (3, 7)]
    rows = hatch([[outer, hole]], 1.0)
    assert len(rows) == 10
    mid = rows[5]
    assert mid.intervals == [(0, 3), (7, 10)]
    other = [(5, 0), (15, 0), (15, 10), (5, 10)]
    merged = hatch([[outer], [other]], 1.0)
    assert merged[0].intervals == [(0, 15)]  # el solape no se graba dos veces
    rot = hatch([[outer]], 1.0, 45)
    assert len(rot) == pytest.approx(14, abs=1)


def fill_doc(**kw):
    ly = Layer("f", "#000", id="f", mode=LayerMode.FILL, line_interval_mm=0.5, speed_mm_min=3000,
               power_max_pct=40, overscan_mm=2.0, **kw)
    doc = Document(layers=[ly])
    doc.shapes = [make_shape("f", gp.rectangle(10, 10, 20, 10))]
    return doc


def test_fill_gcode_overscan_bidirectional_and_safety():
    prof = demo_profile()
    job = generate_job(fill_doc(), prof)
    no_power_on_rapids(job.lines)
    segs = toolpath_segments(job.lines)
    cut = [s for s in segs if s[4]]
    assert len(cut) == 20  # 10 mm / 0,5 mm
    assert {round(abs(s[2] - s[0]), 3) for s in cut} == {20.0}
    assert cut[0][2] - cut[0][0] == pytest.approx(20) and cut[1][2] - cut[1][0] == pytest.approx(-20)
    xs = [s[0] for s in segs[1:]] + [s[2] for s in segs[1:]]  # sin el G0 inicial desde (0,0)
    assert min(xs) == pytest.approx(8.0) and max(xs) == pytest.approx(32.0)  # overscan 2 mm
    a = analyze(job.lines)
    assert a.cut_distance == pytest.approx(400)
    assert a.max_s == pytest.approx(400)
    risky = [ln for ln in job.lines if SafetyManager().classify_command(ln).fires_laser]
    assert all(ln.startswith("G1") for ln in risky)


def test_cross_fill_and_unidirectional():
    prof = demo_profile()
    job = generate_job(fill_doc(cross_fill=True, bidirectional=False), prof)
    cut = [s for s in toolpath_segments(job.lines) if s[4]]
    horiz = [s for s in cut if abs(s[1] - s[3]) < 1e-6]
    vert = [s for s in cut if abs(s[0] - s[2]) < 1e-6]
    assert len(horiz) == 20 and len(vert) == 40
    assert all(s[2] > s[0] for s in horiz)  # siempre en el mismo sentido


def test_fill_line_mode_does_both():
    prof = demo_profile()
    doc = fill_doc()
    doc.layers[0].mode = LayerMode.FILL_LINE
    a = analyze(generate_job(doc, prof).lines)
    assert a.cut_distance == pytest.approx(400 + 60, rel=1e-3)


def image_doc(method="threshold", power_min=0.0, **settings):
    doc = new_document()
    ly = doc.layers[0]
    ly.mode, ly.power_min_pct, ly.power_max_pct, ly.overscan_mm = LayerMode.IMAGE, power_min, 50.0, 1.0
    img = np.full((10, 20), 255, np.uint8)
    img[:, 5:15] = 0
    doc.images["i"] = RasterSource("x", img)
    doc.shapes = [make_image_shape(ly.id, "i", 20, 10, 100, 100, 20, 10, "img",
                                   ImageSettings(method=method, lines_per_mm=1, **settings))]
    return doc


def test_image_gcode_runs_and_power():
    prof = demo_profile()
    job = generate_job(image_doc(), prof)
    no_power_on_rapids(job.lines)
    cut = [s for s in toolpath_segments(job.lines) if s[4]]
    assert len(cut) == 10
    assert {round(abs(s[2] - s[0]), 3) for s in cut} == {10.0}
    assert analyze(job.lines).max_s == pytest.approx(500)
    gray = generate_job(image_doc("grayscale", power_min=10.0), prof)
    assert analyze(gray.lines).max_s == pytest.approx(500)


@pytest.mark.parametrize("angle", [90.0, 45.0, -30.0])
def test_image_scan_angle(angle):
    prof = demo_profile()
    base = analyze(generate_job(image_doc(), prof).lines).cut_distance
    job = generate_job(image_doc(scan_angle=angle), prof)
    no_power_on_rapids(job.lines)
    cut = [s for s in toolpath_segments(job.lines) if s[4]]
    assert cut
    ang = math.radians(angle)
    for x0, y0, x1, y1, _ in cut:
        # todas las líneas que graban siguen la dirección pedida (el eje Y de la máquina puede invertirse)
        cross = abs((x1 - x0) * math.sin(ang)) - abs((y1 - y0) * math.cos(ang))
        assert abs(cross) < 1e-3 * max(1.0, math.dist((x0, y0), (x1, y1)))
    # la superficie grabada (10×10 mm a 1 línea/mm) es la misma con cualquier ángulo
    assert analyze(job.lines).cut_distance == pytest.approx(base, rel=0.2)
    xs = [v for s in cut for v in (s[0], s[2])]
    ys = [v for s in cut for v in (s[1], s[3])]
    ref = [s for s in toolpath_segments(generate_job(image_doc(), prof).lines) if s[4]]
    rx = [v for s in ref for v in (s[0], s[2])]
    ry = [v for s in ref for v in (s[1], s[3])]
    # lo grabado queda dentro de la banda negra de la imagen (± una línea)
    assert min(xs) >= min(rx) - 1.01 and max(xs) <= max(rx) + 1.01
    assert min(ys) >= min(ry) - 1.01 and max(ys) <= max(ry) + 1.01


def test_scan_angle_settings_roundtrip_and_clamp():
    s = ImageSettings(scan_angle=45.0)
    assert ImageSettings.from_dict(s.to_dict()).scan_angle == 45.0
    assert ImageSettings(scan_angle=200.0).clamped().scan_angle == 90.0
    assert ImageSettings.from_dict({}).scan_angle == 0.0  # proyectos anteriores


def test_image_grayscale_power_levels():
    prof = demo_profile()
    doc = image_doc("grayscale", power_min=10.0)
    img = np.tile(np.array([255, 191, 128, 64, 0], np.uint8), (4, 4))
    doc.images["i"] = RasterSource("x", img)
    job = generate_job(doc, prof)
    s_vals = sorted({float(ln.split("S")[1]) for ln in job.lines if ln.startswith("G1") and "S" in ln} - {0.0})
    assert len(s_vals) == 4 and s_vals[0] >= 100 and s_vals[-1] == 500


def test_image_cache_reuses_lines():
    prof = demo_profile()
    doc = image_doc("floyd")
    a = generate_job(doc, prof).lines
    b = generate_job(doc, prof).lines
    assert a == b


def test_lead_in_out_tabs_and_pierce():
    pts = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    assert lead_in_point(pts, 2) == (-2.0, 0.0)
    start, steps = build_steps(pts, True, lead_in=2, lead_out=3, tabs=4, tab_width=1)
    assert start == (-2.0, 0.0) and steps[0] == ((0.0, 0.0), True)
    assert steps[-1] == ((3.0, 0.0), True)
    off = sum(math.dist(a[0], b[0]) for a, b in zip(steps, steps[1:]) if not b[1])
    assert off == pytest.approx(4.0)
    assert tab_intervals(40, 4, 1)[0] == (4.5, 5.5)
    assert tab_intervals(10, 10, 1) == []  # puentes que ocuparían casi todo: se ignoran
    prof = demo_profile()
    doc = new_document()
    ly = doc.layers[0]
    ly.tab_count, ly.tab_width_mm, ly.pierce_ms, ly.lead_in_mm = 2, 2.0, 250, 1.0
    doc.shapes = [make_shape(ly.id, gp.rectangle(10, 10, 20, 20))]
    job = generate_job(doc, prof)
    assert "G4P0.25" in job.lines and job.warnings  # M4 + perforación: aviso
    no_power_on_rapids(job.lines)
    a = analyze(job.lines)
    assert a.cut_distance == pytest.approx(80 - 4 + 1, rel=1e-3)


def test_round_corners_geometry():
    sp = gp.round_corners(gp.rectangle(0, 0, 20, 10), 2)
    assert len(sp.nodes) == 8 and gp.bounds_of([sp]) == pytest.approx((0, 0, 20, 10), abs=1e-6)
    assert sp.length() == pytest.approx(2 * (16 + 6) + 2 * math.pi * 2, rel=5e-3)
    tiny = gp.round_corners(gp.rectangle(0, 0, 2, 2), 10)
    assert gp.bounds_of([tiny]) == pytest.approx((0, 0, 2, 2), abs=1e-6)
    curve = gp.ellipse(0, 0, 5, 5)
    assert gp.round_corners(curve, 1) == curve


def test_fast_image_emitter_matches_general_path():
    """La emisión rápida (imagen sin giro) produce exactamente las mismas líneas que la general."""
    from openlaser.gcode import generator as g
    from openlaser.image_processing.raster import SampledImage
    rng = np.random.default_rng(3)
    for _ in range(12):
        r, c = int(rng.integers(1, 10)), int(rng.integers(1, 14))
        vals = rng.random((r, c))
        vals[vals < 0.4] = 0
        sa = SampledImage(vals.astype(np.float32), float(rng.random() * 50), float(rng.random() * 50),
                          0.1 + float(rng.random()))
        for gray in (True, False):
            for bid in (True, False):
                for ov in (0.0, 2.0):
                    for aff in [(0, 1, 0, 1), (5, 1, 400, -1), (3, -1, 7, 1)]:
                        def to_work(p, a=aff):
                            return (a[0] + a[1] * p[0], a[2] + a[3] * p[1])
                        q = g._quantize(sa.values, 10, 500, gray)
                        fast, _pts, cur = g._emit_image_fast(sa, q, g._axis_affine(to_work), "M4", 3000, ov, bid, 2)
                        block: list[str] = []
                        sub = g._Emitter(block, to_work, [])
                        rows = g._image_rows(sa.values, 10, 500, gray, sa.x0, sa.pitch)
                        for _p in range(2):
                            g._emit_rows(sub, rows, lambda k, s=sa: s.y0 + (k + 0.5) * s.pitch, sa.to_doc, "M4",
                                         3000, ov, bid)
                        assert fast == block
                        assert cur == pytest.approx(sub.cursor)


def test_analyze_fast_path_matches_general_path():
    job = generate_job(image_doc("grayscale", power_min=10.0), demo_profile())
    lines = [ln for ln in job.lines if ln and not ln.startswith(";")]
    fast = analyze(lines, clean=True)
    slow = analyze([ln.replace("X", " X") for ln in lines])  # con espacios no entra en la vía rápida
    for f in ("line_count", "motion_lines", "max_s", "max_feed"):
        assert getattr(fast, f) == getattr(slow, f), f
    for f in ("cut_distance", "travel_distance", "estimated_seconds"):
        assert getattr(fast, f) == pytest.approx(getattr(slow, f)), f
    assert fast.bounds_all == pytest.approx(slow.bounds_all)
    assert fast.bounds_laser == pytest.approx(slow.bounds_laser)
    flat = [v for pt in sorted(set(fast.frame_points)) for v in pt]
    assert flat == pytest.approx([v for pt in sorted(set(slow.frame_points)) for v in pt])
    assert fast.cumulative_seconds == pytest.approx(slow.cumulative_seconds)


def test_large_photo_generates_and_analyzes_quickly():
    """Fotografía tramada de 200×120 mm a 10 líneas/mm (≈2,4 Mpx de salida): antes tardaba ~20 s."""
    import time

    from openlaser.gcode.analyzer import prepare_lines
    h, w = 1200, 2000
    yy, xx = np.mgrid[0:h, 0:w]
    img = ((np.sin(xx / 40) + np.cos(yy / 55)) * 60 + 128).clip(0, 255).astype(np.uint8)
    doc = new_document()
    ly = doc.layers[0]
    ly.mode = LayerMode.IMAGE
    doc.images["i"] = RasterSource("x", img)
    doc.shapes = [make_image_shape(ly.id, "i", w, h, 20, 20, 200, 120, "img", ImageSettings(method="floyd"))]
    t = time.perf_counter()
    job = generate_job(doc, demo_profile())
    a = analyze(prepare_lines(job.lines), clean=True)
    elapsed = time.perf_counter() - t
    assert a.cut_distance > 0 and a.frame_points
    assert elapsed < 15.0, elapsed


def _photo_doc(method="floyd", ov=1.0, bid=True, passes=1, angle=0.0):
    rng = np.random.default_rng(0)
    img = (rng.random((60, 90)) * 255).astype(np.uint8)
    doc = new_document()
    ly = doc.layers[0]
    ly.mode, ly.overscan_mm, ly.bidirectional, ly.passes = LayerMode.IMAGE, ov, bid, passes
    doc.images["i"] = RasterSource("x", img)
    doc.shapes = [make_image_shape(ly.id, "i", 90, 60, 30, 40, 45, 30, "img",
                                   ImageSettings(method=method, lines_per_mm=3, scan_angle=angle))]
    doc.layers.append(Layer("v", "#f00", id="v"))
    doc.shapes.append(make_shape("v", gp.rectangle(100, 100, 20, 20)))
    return doc


@pytest.mark.parametrize("method,ov,bid", [("floyd", 0.0, True), ("floyd", 1.5, False), ("grayscale", 1.5, True)])
def test_lazy_raster_analysis_matches_text_analysis(method, ov, bid):
    """El análisis por bloques (sin crear texto) da lo mismo que analizar todas las líneas."""
    from openlaser.gcode.analyzer import prepare_lines
    from openlaser.gcode.generator import RasterBlock
    job = generate_job(_photo_doc(method, ov, bid, passes=2), demo_profile())
    assert any(isinstance(p, RasterBlock) for p in job.parts)
    parts: list = []
    for p in job.parts:
        parts += prepare_lines([p]) if isinstance(p, str) else [p]
    lazy = analyze(parts * 2, clean=True)
    text = analyze(prepare_lines(job.lines) * 2, clean=True)
    for f in ("line_count", "motion_lines", "max_s", "max_feed"):
        assert getattr(lazy, f) == getattr(text, f), f
    for f in ("cut_distance", "travel_distance", "estimated_seconds"):
        assert getattr(lazy, f) == pytest.approx(getattr(text, f)), f
    assert lazy.bounds_all == pytest.approx(text.bounds_all)
    assert lazy.bounds_laser == pytest.approx(text.bounds_laser)
    assert lazy.cumulative_seconds == pytest.approx(text.cumulative_seconds)
    flat = [v for pt in sorted(set(lazy.frame_points)) for v in pt]
    assert flat == pytest.approx([v for pt in sorted(set(text.frame_points)) for v in pt])


@pytest.mark.parametrize("angle", [0.0, 30.0])
def test_moving_image_reuses_processed_pixels(monkeypatch, angle):
    """Mover la imagen no vuelve a tramar: se reutilizan los píxeles y el G-code es idéntico al recalculado."""
    import dataclasses

    from openlaser.gcode import generator as g
    from openlaser.image_processing import raster
    doc = _photo_doc(angle=angle)
    generate_job(doc, demo_profile()).lines  # noqa: B018 - llena la caché
    calls = []
    real = raster.process
    monkeypatch.setattr(raster, "process", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    sh = doc.shapes[0]
    moved = tuple((x + 7.3, y - 4.1) for x, y in sh.image.corners)
    doc.shapes[0] = dataclasses.replace(sh, image=dataclasses.replace(sh.image, corners=moved))
    cached_lines = generate_job(doc, demo_profile()).lines
    assert calls == []  # sin volver a procesar la imagen
    for cache in (g._RASTER_CACHE, g._RUNS_CACHE, g._BLOCK_CACHE):
        cache.clear()
    fresh_lines = generate_job(doc, demo_profile()).lines
    assert calls == [1]
    assert cached_lines == fresh_lines


@pytest.mark.parametrize("method,ov,bid,passes", [("floyd", 0.0, True, 1), ("floyd", 1.5, False, 2),
                                                  ("grayscale", 1.5, True, 1)])
def test_simulation_timeline_from_blocks(method, ov, bid, passes):
    """La simulación con bloques (una fila = un movimiento + imagen quemada) da los mismos tiempos y
    distancias que recorrer todas las líneas, con muchos menos movimientos."""
    from openlaser.simulation.timeline import build_timeline
    job = generate_job(_photo_doc(method, ov, bid, passes), demo_profile())
    text = build_timeline(job.lines)
    blocks = build_timeline(job.parts)
    assert blocks.count < text.count / 5
    assert blocks.total_time == pytest.approx(text.total_time)
    assert blocks.cut_distance == pytest.approx(text.cut_distance)
    assert blocks.travel_distance == pytest.approx(text.travel_distance)
    assert (blocks.x1[-1], blocks.y1[-1]) == pytest.approx((text.x1[-1], text.y1[-1]))
    assert blocks.layers == text.layers and int(blocks.line.max()) == int(text.line.max())
    assert blocks.raster is not None and blocks.raster.any()
    (ov_,) = blocks.overlays
    burned_rows = np.flatnonzero((ov_.q > 0).any(axis=1))
    assert (ov_.row_move[burned_rows] >= 0).all()          # cada fila con píxeles tiene su movimiento
    assert np.all(np.diff(ov_.row_move[burned_rows]) > 0)  # y se graban en orden
    assert blocks.on[ov_.row_move[burned_rows]].all()
