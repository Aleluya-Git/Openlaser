"""Línea temporal de la simulación y diagnósticos."""
import pytest

from openlaser.devices.profile import demo_profile
from openlaser.document.model import demo_document, make_shape, new_document
from openlaser.gcode.analyzer import analyze
from openlaser.gcode.generator import generate_job
from openlaser.geometry import path as gp
from openlaser.simulation.checks import check_document, check_moves, find_intersections
from openlaser.simulation.timeline import build_timeline


def test_timeline_matches_analysis_and_layers():
    job = generate_job(demo_document(), demo_profile())
    tl = build_timeline(job.lines)
    a = analyze(job.lines)
    assert tl.layers == [sp.name for sp in job.layer_spans]
    assert tl.cut_distance == pytest.approx(a.cut_distance, rel=1e-6)
    assert tl.total_time == pytest.approx(a.estimated_seconds, rel=1e-3)
    assert tl.s_max == a.max_s and tl.layer_changes == 3
    x, y, i = tl.position_at(0.0)
    assert i == 0
    assert tl.position_at(tl.total_time + 10)[2] == tl.count


def test_position_interpolates():
    tl = build_timeline(["G90", "G1 X10 Y0 F600 M4 S100", "G1 X10 Y10"], time_factor=1.0)
    assert tl.count == 2 and tl.total_time == pytest.approx(2.0)
    x, y, i = tl.position_at(0.5)
    assert (x, y, i) == pytest.approx((5.0, 0.0, 0))
    x, y, i = tl.position_at(1.5)
    assert (x, y, i) == pytest.approx((10.0, 5.0, 1))
    arc = build_timeline(["G0X0Y0", "M3S10", "G2X2Y0I1J0F60"])
    assert arc.count > 4 and arc.on[1:].all() and not arc.on[0]


def test_layer_comment_formats():
    tl = build_timeline(["; capa: Uno (demo)", "G0X1Y1", "(capa: Dos)", "G1X2Y2F100M4S5"])
    assert tl.layers == ["Uno (demo)", "Dos"]
    assert tl.layer.tolist() == [0, 1]


def test_checks_document():
    doc = new_document(100, 100)
    lid = doc.layers[0].id
    doc.shapes = [make_shape(lid, gp.polyline([(0, 0), (10, 10), (10, 0), (0, 10)], True)),
                  make_shape(lid, gp.rectangle(20, 20, 10, 10)), make_shape(lid, gp.rectangle(25, 25, 10, 10)),
                  make_shape(lid, gp.rectangle(20, 20, 10, 10)), make_shape(lid, gp.rectangle(95, 95, 10, 10)),
                  make_shape(lid, gp.polyline([(50, 50), (60, 60)]))]
    kinds = {i.message for i in check_document(doc)}
    assert any("fuera del área" in m for m in kinds)
    assert any("duplicados" in m for m in kinds)
    assert any("abiertas" in m for m in kinds)
    inter = find_intersections(doc)
    assert any(len(i.shape_ids) == 1 for i in inter) and any(len(i.shape_ids) == 2 for i in inter)
    assert check_document(demo_document()) == []


def test_check_moves_outside_limits():
    tl = build_timeline(["G0X10Y10", "G1X500Y10F1000M4S10"])
    issues = check_moves(tl, (0, 0, 400, 400))
    assert issues and issues[0].line == 1
    assert check_moves(tl, (0, 0, 600, 600)) == []
