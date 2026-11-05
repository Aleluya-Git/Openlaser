from openlaser.devices.profile import demo_profile
from openlaser.document.model import Layer, make_shape, new_document
from openlaser.gcode.analyzer import analyze, toolpath_segments
from openlaser.gcode.generator import generate_line_job
from openlaser.geometry import path as gp
from openlaser.toolpath.order import PathItem, order_paths, travel_distance


def sq(x, y, s):
    return gp.rectangle(x, y, s, s).flatten()


def test_inside_first():
    items = [PathItem(sq(0, 0, 100), True, "outer"), PathItem(sq(10, 10, 10), True, "inner"),
             PathItem(sq(12, 12, 2), True, "innermost")]
    order = [tag for _, tag in order_paths(items)]
    assert order == ["innermost", "inner", "outer"]
    order = [tag for _, tag in order_paths(items, inside_first=False, optimize=False)]
    assert order == ["outer", "inner", "innermost"]


def test_nearest_neighbour_reduces_travel():
    items = [PathItem([(x, 0.0), (x + 1, 0.0)], False, x) for x in (90, 10, 50, 30, 70)]
    opt = order_paths(items, inside_first=False, optimize=True)
    naive = order_paths(items, inside_first=False, optimize=False)
    assert [t for _, t in opt] == [10, 30, 50, 70, 90]
    assert travel_distance([p for p, _ in opt]) < travel_distance([p for p, _ in naive])


def test_closed_path_starts_at_nearest_vertex_and_returns():
    items = [PathItem(sq(10, 10, 10), True, "a")]
    pts, _ = order_paths(items, start=(20, 20))[0]
    assert pts[0] == (20, 20) and pts[-1] == (20, 20)


def test_generator_curves_passes_pause_and_z():
    prof = demo_profile()
    prof.z_axis = True
    d = new_document(400, 400)
    ly = d.layers[0]
    ly.passes, ly.pause_after, ly.z_offset_mm, ly.air_assist = 2, True, -1.5, True
    d.shapes = [make_shape(ly.id, gp.ellipse(100, 100, 20, 20)), make_shape(ly.id, gp.ellipse(100, 100, 5, 5))]
    lines = generate_line_job(d, prof).lines
    assert "M8" in lines and "G0Z-1.5" in lines and "G0Z0" in lines and "M0" in lines
    assert sum(1 for ln in lines if ln.startswith("G0X")) == 4
    segs = toolpath_segments(lines)
    cut = [s for s in segs if s[4]]
    first_cut_x = cut[0][0]
    assert abs(first_cut_x - 105) < 0.5 or abs(first_cut_x - 95) < 0.5 or 95 <= first_cut_x <= 105  # interior primero
    a = analyze(lines)
    assert a.cut_distance > 2 * (2 * 3.14159 * 25) * 0.99


def test_inside_first_in_job_cuts_inner_before_outer():
    prof = demo_profile()
    d = new_document(400, 400)
    ly = d.layers[0]
    d.shapes = [make_shape(ly.id, gp.rectangle(0, 0, 100, 100)), make_shape(ly.id, gp.rectangle(40, 40, 10, 10))]
    lines = generate_line_job(d, prof).lines
    first_g0 = next(ln for ln in lines if ln.startswith("G0X"))
    x = float(first_g0[3:].split("Y")[0])
    assert 40 <= x <= 50
    ly2 = Layer("x", "#000")
    assert ly2.inside_first and ly2.optimize_travel


def test_large_import_orders_quickly():
    import random
    import time
    random.seed(7)
    items = [PathItem(gp.ellipse(random.uniform(0, 400), random.uniform(0, 400), 2, 2).flatten(0.05), True, i)
             for i in range(3000)]
    items.append(PathItem(sq(-5, -5, 410), True, "outer"))
    t = time.perf_counter()
    order = order_paths(items, (5000.0, 5000.0))
    assert time.perf_counter() - t < 5.0
    assert len(order) == 3001 and order[-1][1] == "outer"
