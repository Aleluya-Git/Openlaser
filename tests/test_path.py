import math

import pytest

from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine


def test_affine_compose_and_rotate():
    m = Affine.translate(10, 0) @ Affine.scale(2)
    assert m.apply((1, 1)) == (12, 2)
    r = Affine.rotate(90, (1, 1))
    assert r.apply((2, 1)) == pytest.approx((1, 2))
    assert Affine().is_identity()


def test_ellipse_is_accurate_circle():
    c = gp.ellipse(0, 0, 10, 10)
    pts = c.flatten(0.01)
    assert all(abs(math.hypot(x, y) - 10) < 0.03 for x, y in pts)
    assert c.length(0.01) == pytest.approx(2 * math.pi * 10, rel=1e-3)
    assert abs(c.signed_area()) == pytest.approx(math.pi * 100, rel=1e-2)


def test_rounded_rectangle_bounds_and_length():
    r = gp.rectangle(0, 0, 20, 10, 2)
    assert gp.bounds_of([r]) == pytest.approx((0, 0, 20, 10), abs=1e-6)
    expected = 2 * (16 + 6) + 2 * math.pi * 2
    assert r.length(0.005) == pytest.approx(expected, rel=1e-3)
    assert gp.rectangle(5, 5, -5, -5).nodes[0].p == (0, 0)


def test_star_and_polygon():
    assert len(gp.star(0, 0, 10, 5, 5).nodes) == 10
    assert len(gp.regular_polygon(0, 0, 10, 6).nodes) == 6


def test_reverse_and_transform():
    sp = gp.polyline([(0, 0), (10, 0), (10, 10)], True)
    rv = sp.reversed()
    assert rv.nodes[0].p == (0, 0) and rv.nodes[1].p == (10, 10)
    assert abs(rv.signed_area()) == abs(sp.signed_area())
    assert rv.signed_area() == -sp.signed_area()
    moved = sp.transformed(Affine.translate(1, 2))
    assert moved.nodes[2].p == (11, 12)


def test_arc_to_cubics_semicircle():
    cubics = gp.arc_to_cubics((0, 0), 5, 5, 0, False, True, (10, 0))
    b = gp.PathBuilder()
    b.move_to((0, 0))
    for c1, c2, p in cubics:
        b.cubic_to(c1, c2, p)
    sp = b.finish()[0]
    assert sp.length(0.001) == pytest.approx(math.pi * 5, rel=1e-3)
    assert all(abs(math.hypot(x - 5, y) - 5) < 0.01 for x, y in sp.flatten(0.01))


def test_simplify_and_duplicates():
    pts = [(i * 0.1, 0.0) for i in range(101)]
    sp = gp.polyline(pts)
    assert len(gp.simplify(sp, 0.01).nodes) == 2
    dup = gp.polyline([(0, 0), (0, 0), (1, 0), (1, 0), (1, 1)])
    assert len(gp.remove_duplicate_nodes(dup).nodes) == 3


def test_join_open_subpaths_closes_loop():
    a = gp.polyline([(0, 0), (10, 0)])
    b = gp.polyline([(10, 10), (10, 0)])
    c = gp.polyline([(10, 10), (0, 10), (0, 0)])
    joined = gp.join_open_subpaths([a, b, c], 0.01)
    assert len(joined) == 1 and joined[0].closed and len(joined[0].nodes) == 4


def test_containment_depth():
    outer = gp.rectangle(0, 0, 100, 100).flatten()
    inner = gp.rectangle(10, 10, 10, 10).flatten()
    inner2 = gp.rectangle(12, 12, 2, 2).flatten()
    other = gp.rectangle(200, 0, 10, 10).flatten()
    assert gp.containment_depth([outer, inner, inner2, other]) == [0, 1, 2, 0]


def test_serialization_round_trip():
    sp = gp.ellipse(1, 2, 3, 4)
    again = gp.SubPath.from_dict(sp.to_dict())
    assert again.closed == sp.closed and len(again.nodes) == len(sp.nodes)
    for a, b in zip(again.nodes, sp.nodes):
        assert a.p == pytest.approx(b.p, abs=1e-6) and a.cin == pytest.approx(b.cin, abs=1e-6)
    assert gp.SubPath.from_dict(again.to_dict()) == again
