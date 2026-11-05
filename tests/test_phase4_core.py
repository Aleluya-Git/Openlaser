"""Fase 4 (núcleo): rotary, corrección cilíndrica, registro, materiales, pruebas, cámara, plugins, PDF."""
import json
import math
import zlib

import numpy as np
import pytest

from openlaser.devices.profile import demo_profile
from openlaser.devices.rotary import CylinderCorrection, RotaryConfig, apply_cylinder, apply_rotary, machine_transform
from openlaser.document.commands import UndoStack, edit_document
from openlaser.document.model import Layer, LayerMode, make_shape, new_document
from openlaser.gcode.analyzer import analyze
from openlaser.gcode.generator import generate_job
from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine
from openlaser.geometry.registration import affine_from_three, circle_from_three, similarity_from_pairs
from openlaser.geometry.shape_library import SHAPES, place_on_path
from openlaser.geometry.stroke_font import supported, text_subpaths, text_width
from openlaser.materials.library import MaterialEntry, MaterialLibrary, demo_library, load_or_create
from openlaser.materials.test_patterns import TEST_KINDS, apply_plan, build


# ------------------------------------------------------------------- rotary
def test_rotary_factor_roller_chuck_gear_and_steps():
    chuck = RotaryConfig(enabled=True, kind="chuck", object_diameter_mm=100 / math.pi, mm_per_rev=360)
    assert chuck.factor() == pytest.approx(3.6)
    roller = RotaryConfig(enabled=True, kind="roller", object_diameter_mm=999, roller_diameter_mm=50 / math.pi,
                          mm_per_rev=100, gear_ratio=2)
    assert roller.factor() == pytest.approx(4.0)  # el diámetro del objeto no influye en el rodillo
    steps = RotaryConfig(enabled=True, kind="chuck", object_diameter_mm=100 / math.pi, steps_per_rev=3200,
                         steps_per_mm=10, invert=True)
    assert steps.effective_mm_per_rev() == 320 and steps.factor() == pytest.approx(-3.2)
    assert RotaryConfig(roller_diameter_mm=0).validate()
    assert RotaryConfig.from_dict(chuck.to_dict()) == chuck


def test_apply_rotary_scales_y_or_moves_to_a():
    cfg = RotaryConfig(enabled=True, kind="chuck", object_diameter_mm=100 / math.pi, mm_per_rev=100)
    lines, _ = apply_rotary(["G90", "G21", "M5", "G0X10Y20", "G1X5F100S10", "G1Y50"], cfg)
    assert lines[3:] == ["G0X10Y20", "G1X5F100S10", "G1Y50"]
    cfg.mm_per_rev = 200
    lines, _ = apply_rotary(["G0X10Y20"], cfg)
    assert lines == ["G0X10Y40"]
    cfg.axis = "A"
    lines, warn = apply_rotary(["G0X10Y20"], cfg)
    assert lines == ["G0X10A40"] and warn
    with pytest.raises(ValueError):
        apply_rotary(["G2X1Y1I1J0"], cfg)
    with pytest.raises(ValueError):
        apply_rotary(["G91", "G0Y1"], cfg)


def test_cylinder_correction_projects_and_subdivides():
    cc = CylinderCorrection(True, 100, 50)
    assert cc.map_y(50) == pytest.approx(50)
    assert cc.map_y(50 + 50 * math.pi / 2) == pytest.approx(100)
    lines, warn = apply_cylinder(["G21", "G0X0Y50", "G1X0Y60F100S5"], cc)
    assert lines[2] == "G1X0Y50.5F100S5" and len(lines) == 22 and not warn
    _, warn = apply_cylinder(["G0X0Y200"], cc)
    assert warn
    assert apply_cylinder(["G0X1Y1"], CylinderCorrection(False))[0] == ["G0X1Y1"]


def test_machine_transform_on_generated_job_keeps_x_and_safety():
    prof = demo_profile()
    prof.rotary = RotaryConfig(enabled=True, kind="chuck", object_diameter_mm=100 / math.pi, mm_per_rev=200).to_dict()
    doc = new_document()
    doc.shapes = [make_shape(doc.layers[0].id, gp.rectangle(10, 10, 20, 20))]
    base = generate_job(doc, prof).lines
    out, _ = machine_transform(base, prof)
    a0, a1 = analyze(base), analyze(out)
    assert (a1.bounds_laser[2] - a1.bounds_laser[0]) == pytest.approx(a0.bounds_laser[2] - a0.bounds_laser[0])
    assert (a1.bounds_laser[3] - a1.bounds_laser[1]) == pytest.approx(2 * (a0.bounds_laser[3] - a0.bounds_laser[1]))
    assert all("S" not in ln for ln in out if ln.startswith("G0"))
    prof.cylinder = CylinderCorrection(True).to_dict()
    with pytest.raises(ValueError):
        machine_transform(base, prof)


# ----------------------------------------------------------------- registro
def test_print_and_cut_and_center_finder():
    m = Affine.translate(5, 7) @ Affine.rotate(12) @ Affine.scale(1.01)
    a = [(0.0, 0.0), (80.0, 10.0), (20.0, 60.0)]
    b = [m.apply(p) for p in a]
    t, scale, ang = similarity_from_pairs((a[0], a[1]), (b[0], b[1]))
    assert scale == pytest.approx(1.01) and ang == pytest.approx(12)
    assert t.apply(a[2]) == pytest.approx(b[2])
    assert affine_from_three(tuple(a), tuple(b)).apply((3, 4)) == pytest.approx(m.apply((3, 4)))
    with pytest.raises(ValueError):
        similarity_from_pairs(((0, 0), (1, 0)), ((0, 0), (1, 0)))
    cx, cy, r = circle_from_three((110, 50), (50, 110), (-10, 50))
    assert (cx, cy, r) == pytest.approx((50, 50, 60))
    with pytest.raises(ValueError):
        circle_from_three((0, 0), (1, 1), (2, 2))


# -------------------------------------------------------------- materiales
def test_material_library_round_trip_search_and_layers(tmp_path):
    lib = load_or_create(tmp_path / "m.json")
    assert lib.entries and all(e.demo for e in lib.entries) and (tmp_path / "m.json").exists()
    e = lib.add(MaterialEntry("Acrílico", "negro", 3, "cut", 250, 0, 95, 3, air_assist=True, machine="X"))
    assert lib.search("acril")[0].id == e.id and lib.search(operation="fill")
    assert not e.validated
    lib.mark_validated(e.id, "ok", "2026-10-30")
    assert lib.get(e.id).validated and lib.search(only_validated=True) == [lib.get(e.id)]
    dup = lib.duplicate(e.id)
    assert dup is not None and dup.id != e.id and not dup.validated
    lib.save()
    back = MaterialLibrary.load(tmp_path / "m.json")
    assert [x.to_dict() for x in back.entries] == [x.to_dict() for x in lib.entries]
    layer = Layer("c", "#000")
    e.apply_to_layer(layer)
    assert (layer.mode, layer.speed_mm_min, layer.power_max_pct, layer.passes, layer.air_assist) == \
        (LayerMode.LINE, 250, 95, 3, True) and "Acrílico" in layer.notes
    entry = MaterialEntry.from_layer(layer, "Otro", thickness_mm=2)
    assert entry.speed_mm_min == 250 and entry.operation == "line"
    lib.remove(e.id)
    assert lib.get(e.id) is None


def test_material_library_reads_phase1_demo_and_rejects_garbage(tmp_path):
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    lib = MaterialLibrary.load(root / "examples" / "materiales_DEMO.json")
    assert len(lib.entries) == 3 and all(e.demo for e in lib.entries)
    assert {e.operation for e in lib.entries} == {"cut", "line", "fill"}
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"hola": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        MaterialLibrary.load(bad)
    assert demo_library().merge(demo_library().entries) == 0


# ------------------------------------------------------------------ pruebas
@pytest.mark.parametrize("kind", list(TEST_KINDS))
def test_every_test_pattern_builds_inside_limits(kind):
    prof = demo_profile()
    doc = new_document(400, 400)
    plan = build(kind, {}, prof, doc)
    assert plan.layers and plan.shapes and plan.size[0] > 0
    for ly in plan.layers:
        assert 0 <= ly.power_max_pct <= 100 and 1 <= ly.speed_mm_min <= prof.max_feed_mm_min
    ids = apply_plan(doc, plan)
    assert len({doc.shape(i).group_id for i in ids}) == 1
    assert len({ly.color for ly in doc.layers}) == len(doc.layers)
    job = generate_job(doc, prof)
    assert not job.skipped_layers or kind == "rotary"
    a = analyze(job.lines)
    assert a.max_s <= prof.s_max


def test_test_patterns_clamp_and_reject_outside():
    prof = demo_profile()
    prof.max_feed_mm_min = 4000
    plan = build("speed_power", {"speed_max": 9000, "power_max": 150, "cols": 3, "rows": 2}, prof, new_document())
    assert max(ly.speed_mm_min for ly in plan.layers) == 4000
    assert any("4000" in n for n in plan.notes)
    with pytest.raises(ValueError):
        build("scale", {"x": 350, "size": 100}, prof, new_document(400, 400))
    focus = build("focus", {"z_min": -80, "z_max": 5}, prof, new_document())
    assert all(prof.z_min_mm <= ly.z_offset_mm <= prof.z_max_mm for ly in focus.layers)
    prof.z_axis = False
    ramp = build("focus", {}, prof, new_document())
    assert any("rampa" in n.lower() for n in ramp.notes)
    nolabels = build("interval", {"labels": False}, prof, new_document())
    assert all("Etiqueta" not in ly.name for ly in nolabels.layers)


def test_stroke_font_and_shape_library_and_text_on_path():
    subs = text_subpaths("V2000 P50%", 10, 20, 6)
    b = gp.bounds_of(subs)
    assert b is not None and b[1] >= 14 - 1e-6 and b[3] <= 20 + 1e-6
    assert text_width("ABC", 6) == pytest.approx(14)
    assert supported("ñ") and supported("7") and not supported("€")
    center = gp.bounds_of(text_subpaths("12", 50, 10, 6, "center"))
    assert (center[0] + center[2]) / 2 == pytest.approx(50, abs=0.5)
    for shape in SHAPES.values():
        bb = gp.bounds_of(shape.build(50, 50, 40))
        assert bb is not None and bb[2] - bb[0] <= 40.01 and bb[3] - bb[1] <= 40.01
    glyph = ([gp.polyline([(0, 0), (0, -3)])], 2.0)
    circle = gp.ellipse(0, 0, 20, 20)
    placed = place_on_path([glyph] * 6, circle)
    assert len(placed) == 6
    for sp in placed:
        x, y = sp.nodes[0].p
        assert math.hypot(x, y) == pytest.approx(20, abs=0.1)  # la base de cada glifo sobre el círculo
    assert place_on_path([glyph] * 50, gp.polyline([(0, 0), (10, 0)])) and \
        len(place_on_path([glyph] * 50, gp.polyline([(0, 0), (10, 0)]))) == 5


def test_undo_history_go_to():
    doc = new_document()
    undo = UndoStack()
    for i in range(3):
        with edit_document(doc, undo, f"paso {i}"):
            doc.shapes.append(make_shape(doc.layers[0].id, gp.rectangle(i, 0, 1, 1)))
    texts, applied = undo.history()
    assert texts == ["paso 0", "paso 1", "paso 2"] and applied == 3
    calls = []
    undo.listeners.append(lambda: calls.append(1))
    undo.go_to(1)
    assert len(doc.shapes) == 1 and undo.history()[1] == 1 and len(calls) == 1
    undo.go_to(3)
    assert len(doc.shapes) == 3


# ------------------------------------------------------------------- cámara
def test_camera_lens_homography_and_precision(tmp_path):
    from openlaser.camera.calibration import (
        CameraProfile, CameraStore, LensModel, SimulatedCamera, apply_h, distort_points, fit_k1_from_lines,
        homography, marker_positions, refine_marker, undistort_image, undistort_points,
    )
    pts = np.array([[10.0, 20.0], [700.0, 500.0], [400.0, 300.0]])
    lens = LensModel(-0.1, 0.02)
    assert undistort_points(distort_points(pts, lens, 800, 600), lens, 800, 600) == pytest.approx(pts, abs=1e-6)
    cam = SimulatedCamera(markers=marker_positions(400, 400, 9))
    img = cam.capture()
    assert img.shape == (600, 800) and img.dtype == np.uint8
    proj = cam.project(cam.markers)
    clicks = [refine_marker(img, x + 3, y - 2) for x, y in proj]
    assert np.abs(np.asarray(clicks) - proj).max() < 1.0
    edges = [[tuple(p) for p in cam.project([(x, 0.0) for x in np.linspace(10, 390, 9)])],
             [tuple(p) for p in cam.project([(0.0, y) for y in np.linspace(10, 390, 9)])]]
    k1 = fit_k1_from_lines(edges, 800, 600)
    assert k1 == pytest.approx(cam.lens.k1, abs=0.005)
    und = undistort_points(np.asarray(clicks), LensModel(k1), 800, 600)
    prof = CameraProfile(width=800, height=600, k1=k1, device="sim")
    info = prof.calibrate_area([tuple(p) for p in und], cam.markers)
    assert info["rms_mm"] < 0.5 and 0 < prof.precision_mm < 3 and prof.calibrated
    assert apply_h(prof.h(), und[4:5])[0] == pytest.approx(cam.markers[4], abs=0.5)
    assert undistort_image(img, LensModel(k1)).shape == img.shape
    store = CameraStore(tmp_path / "cameras.json")
    store.upsert(prof)
    again = CameraStore(tmp_path / "cameras.json").for_device("sim")
    assert again is not None and again.homography == prof.homography
    with pytest.raises(ValueError):
        homography([(0, 0), (1, 1), (2, 2), (3, 3)], [(0, 0), (1, 1), (2, 2), (3, 3)])


def test_opencv_lens_is_optional():
    from openlaser.camera.lens import calibrate_chessboard, opencv_available
    if not opencv_available():
        with pytest.raises(RuntimeError):
            calibrate_chessboard([np.zeros((10, 10), np.uint8)])


# ------------------------------------------------------------------ plugins
def _plugin(root, pid, code, api=1, entry="plugin.py"):
    folder = root / pid
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({"id": pid, "name": pid.title(), "version": "1.0", "api": api,
                                                    "entry": entry}), encoding="utf-8")
    (folder / "plugin.py").write_text(code, encoding="utf-8")


def test_plugin_manager_discovery_enable_and_errors(tmp_path):
    from openlaser.plugins.manager import PluginManager
    _plugin(tmp_path, "saludo", "def register(api):\n    api.add_action('Hola', lambda: 'hola')\n")
    _plugin(tmp_path, "roto", "raise RuntimeError('mal')\n")
    _plugin(tmp_path, "viejo", "def register(api): pass\n", api=99)
    _plugin(tmp_path, "escape", "def register(api): pass\n", entry="../x.py")
    (tmp_path / "sin_manifest").mkdir()
    m = PluginManager(tmp_path)
    infos = {i.id: i for i in m.discover()}
    assert set(infos) == {"saludo", "roto", "viejo", "escape"}
    assert "API" in infos["viejo"].error and infos["escape"].error
    assert m.load_enabled() == [] and not m.actions  # nada habilitado: nada se ejecuta
    m.enabled = {"saludo", "roto", "viejo"}
    loaded = m.load_enabled()
    assert [i.id for i in loaded] == ["saludo"]
    assert m.actions_for("saludo")[0].callback() == "hola"
    assert "RuntimeError" in infos["roto"].error and not infos["viejo"].loaded


def test_example_plugin_loads():
    import pathlib

    from openlaser.plugins.manager import PluginManager
    root = pathlib.Path(__file__).resolve().parents[1] / "examples" / "plugins"
    m = PluginManager(root, enabled={"ejemplo.marco"})
    m.discover()
    assert [i.id for i in m.load_enabled()] == ["ejemplo.marco"]
    assert m.actions


# ---------------------------------------------------------------------- PDF
def _pdf(content: bytes, compress: bool = True) -> bytes:
    data = zlib.compress(content) if compress else content
    filt = b" /Filter /FlateDecode" if compress else b""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 595 842] >>",
            b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R /Resources << >> >>",
            b"<< /Length %d%s >>\nstream\n" % (len(data), filt) + data + b"\nendstream"]
    out = b"%PDF-1.4\n"
    for i, o in enumerate(objs, 1):
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    return out + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"


def test_pdf_import_paths_colors_and_units(tmp_path):
    from openlaser.importers import read_file
    from openlaser.importers.pdf import parse_pdf
    c = (b"q 2 0 0 2 0 0 cm 1 0 0 RG 36 36 m 72 36 l 72 72 l h S Q\n0 0 1 rg 100 100 72 36 re f\n"
         b"BT /F1 12 Tf (x) Tj ET\n0 G 200 200 m 200 300 300 300 300 200 c S\n")
    for compress in (True, False):
        r = parse_pdf(_pdf(c, compress))
        assert r.y_up and [s.color for s in r.shapes] == ["#ff0000", "#0000ff", "#000000"]
        b = gp.bounds_of(r.shapes[1].subpaths)
        assert b == pytest.approx((100 * 25.4 / 72, 100 * 25.4 / 72, 172 * 25.4 / 72, 136 * 25.4 / 72))
        assert gp.bounds_of(r.shapes[0].subpaths)[0] == pytest.approx(25.4)  # «cm» escala ×2
        assert r.unsupported["texto"] == 1
    with pytest.raises(ValueError):
        parse_pdf(b"no soy un pdf")
    (tmp_path / "a.ai").write_bytes(b"%!PS-Adobe-3.0 illustrator antiguo")
    with pytest.raises(ValueError):
        read_file(str(tmp_path / "a.ai"))
    (tmp_path / "b.pdf").write_bytes(_pdf(c))
    assert read_file(str(tmp_path / "b.pdf")).count == 3
