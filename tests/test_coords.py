import pytest

from openlaser.devices.profile import OriginCorner
from openlaser.geometry.coords import CoordinateSystem, bounds, convex_hull, rect_frame


@pytest.mark.parametrize("origin", list(OriginCorner))
@pytest.mark.parametrize("inv", [(False, False), (True, False), (False, True), (True, True)])
def test_round_trip(origin, inv):
    cs = CoordinateSystem(300, 200, origin, *inv)
    for p in [(0, 0), (10, 20), (300, 200), (123.4, 5.6)]:
        assert cs.machine_to_doc(cs.doc_to_machine(p)) == pytest.approx(p)
        wco = (7.0, -3.0)
        assert cs.work_to_doc(cs.doc_to_work(p, wco), wco) == pytest.approx(p)


def test_front_left_flips_y():
    cs = CoordinateSystem(300, 200, OriginCorner.FRONT_LEFT)
    assert cs.doc_to_machine((0, 200)) == (0, 0)  # esquina inferior izquierda de pantalla
    assert cs.doc_to_machine((0, 0)) == (0, 200)


def test_rear_right():
    cs = CoordinateSystem(300, 200, OriginCorner.REAR_RIGHT)
    assert cs.doc_to_machine((300, 0)) == (0, 0)


def test_work_offsets():
    assert CoordinateSystem.machine_to_work((50, 60), (10, 20)) == (40, 40)
    assert CoordinateSystem.work_to_machine((40, 40), (10, 20)) == (50, 60)


def test_inside_and_helpers():
    cs = CoordinateSystem(100, 100)
    assert cs.inside((0, 100)) and not cs.inside((100.1, 5))
    assert bounds([(1, 2), (3, -1)]) == (1, -1, 3, 2)
    assert bounds([]) is None
    assert rect_frame((0, 0, 1, 1))[0] == rect_frame((0, 0, 1, 1))[-1]
    hull = convex_hull([(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5)])
    assert (0.5, 0.5) not in hull and hull[0] == hull[-1] and len(hull) == 5
