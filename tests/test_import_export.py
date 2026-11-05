import math
import textwrap

import pytest

from openlaser.document.model import demo_document, new_document
from openlaser.exporters import export_dxf, export_svg
from openlaser.geometry.coords import CoordinateSystem
from openlaser.importers import (
    UnsupportedFormat, apply_import, machine_to_doc_affine, read_file,
)
from openlaser.importers.dxf import parse_dxf
from openlaser.importers.svg import normalize_color, parse_length, parse_path_d, parse_svg, parse_transform

CS = CoordinateSystem(400, 400)


def test_units_and_colors():
    assert parse_length("10mm") == 10 and parse_length("1in") == 25.4 and parse_length("96") == pytest.approx(25.4)
    assert normalize_color("#F00") == "#ff0000" and normalize_color("rgb(0, 128, 255)") == "#0080ff"
    assert normalize_color("none") is None and normalize_color("red") == "#ff0000"


def test_transform_parsing():
    m = parse_transform("translate(10,5) scale(2) rotate(90)")
    assert m.apply((1, 0)) == pytest.approx((10, 7))


def test_path_parser_commands():
    subs = parse_path_d("M0,0 L10,0 10,10 Z m20 0 h5 v5 H20 z M0 50 c0 -5 10 -5 10 0 s 10 5 10 0 "
                        "Q 25 -5 30 0 T 40 0 A 5 5 0 0 1 50 0")
    assert len(subs) == 3
    assert subs[0].closed and len(subs[0].nodes) == 3
    assert subs[1].nodes[0].p == (20, 0) and subs[1].closed
    assert subs[2].nodes[-1].p == pytest.approx((50, 0))
    assert parse_path_d("M1.5.5L2-1") [0].nodes[0].p == (1.5, 0.5)
    assert parse_path_d("M0 0a5 5 0 1010 0")[0].nodes[-1].p == pytest.approx((10, 0))


def test_svg_full_document():
    svg = textwrap.dedent("""\
        <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
             width="100mm" height="50mm" viewBox="0 0 200 100">
          <defs><circle id="dot" r="5"/></defs>
          <g transform="translate(10,10)" stroke="red" fill="none">
            <rect x="0" y="0" width="20" height="10" rx="2"/>
            <line x1="0" y1="0" x2="10" y2="0" stroke="#00f"/>
            <polygon points="0,0 10,0 5,5"/>
          </g>
          <use xlink:href="#dot" x="100" y="50" style="fill:#00ff00"/>
          <ellipse cx="150" cy="50" rx="20" ry="10" fill="black"/>
          <text x="0" y="0">hola</text>
          <image href="a.png"/>
          <g style="display:none"><rect width="10" height="10"/></g>
        </svg>""")
    r = parse_svg(svg)
    assert r.count == 5
    assert r.unsupported["text"] == 1 and r.unsupported["image"] == 1
    colors = sorted(s.color for s in r.shapes)
    assert colors == ["#000000", "#0000ff", "#00ff00", "#ff0000", "#ff0000"]
    rect = r.shapes[0]
    from openlaser.geometry.path import bounds_of
    assert bounds_of(rect.subpaths) == pytest.approx((5, 5, 15, 10), abs=1e-6)  # escala 0.5 mm/unidad
    dot = next(s for s in r.shapes if s.color == "#00ff00")
    assert bounds_of(dot.subpaths) == pytest.approx((47.5, 22.5, 52.5, 27.5), abs=1e-6)


def test_svg_errors():
    with pytest.raises(ValueError):
        parse_svg("<nope")
    with pytest.raises(ValueError):
        parse_svg("<html/>")
    assert parse_svg('<svg xmlns="http://www.w3.org/2000/svg"/>').warnings


def _dxf(*tags):
    return "\n".join(str(t) for t in tags)


def test_dxf_entities_units_and_colors():
    text = _dxf(0, "SECTION", 2, "HEADER", 9, "$INSUNITS", 70, 1, 0, "ENDSEC",
                0, "SECTION", 2, "TABLES", 0, "LAYER", 2, "CUT", 62, 1, 0, "ENDSEC",
                0, "SECTION", 2, "BLOCKS", 0, "BLOCK", 2, "B", 0, "CIRCLE", 8, "0", 10, 0, 20, 0, 40, 1, 0, "ENDBLK",
                0, "ENDSEC", 0, "SECTION", 2, "ENTITIES",
                0, "LINE", 8, "CUT", 10, 0, 20, 0, 11, 1, 21, 0,
                0, "LWPOLYLINE", 8, "0", 62, 5, 90, 2, 70, 1, 10, 0, 20, 0, 42, 1, 10, 2, 20, 0, 42, 1,
                0, "ARC", 8, "0", 10, 0, 20, 0, 40, 1, 50, 0, 51, 90,
                0, "INSERT", 8, "0", 2, "B", 10, 5, 20, 5, 41, 2, 42, 2,
                0, "POLYLINE", 8, "0", 66, 1, 70, 1, 0, "VERTEX", 10, 0, 20, 0, 0, "VERTEX", 10, 1, 20, 0,
                0, "VERTEX", 10, 1, 20, 1, 0, "SEQEND",
                0, "ELLIPSE", 8, "0", 10, 0, 20, 0, 11, 2, 21, 0, 40, 0.5,
                0, "SPLINE", 8, "0", 70, 0, 71, 3, 10, 0, 20, 0, 10, 1, 20, 1, 10, 2, 20, 0, 10, 3, 20, 1,
                0, "MTEXT", 8, "0", 0, "ENDSEC", 0, "EOF")
    r = parse_dxf(text)
    assert r.y_up and r.count == 7 and r.unsupported["MTEXT"] == 1
    line = r.shapes[0]
    assert line.color == "#ff0000"  # BYLAYER -> capa CUT roja
    assert line.subpaths[0].nodes[-1].p == pytest.approx((25.4, 0))
    circle = r.shapes[1]
    assert circle.subpaths[0].closed and circle.subpaths[0].length(0.001) == pytest.approx(2 * math.pi * 25.4, rel=2e-3)
    arc = r.shapes[2]
    assert arc.subpaths[0].nodes[-1].p == pytest.approx((0, 25.4), abs=1e-6)
    insert = r.shapes[3]
    from openlaser.geometry.path import bounds_of
    assert bounds_of(insert.subpaths) == pytest.approx((76.2, 76.2, 177.8, 177.8), abs=1e-4)


def test_dxf_errors():
    with pytest.raises(ValueError):
        parse_dxf("no\nes\ndxf")
    with pytest.raises(ValueError):
        parse_dxf("AutoCAD Binary DXF\r\n")


def test_apply_import_flips_y_up_and_creates_layers():
    text = _dxf(0, "SECTION", 2, "ENTITIES", 0, "LINE", 8, "0", 62, 1, 10, 0, 20, 0, 11, 10, 21, 10,
                0, "LINE", 8, "0", 62, 3, 10, 0, 20, 0, 11, 5, 21, 0, 0, "ENDSEC", 0, "EOF")
    doc = new_document(400, 400)
    s = apply_import(doc, parse_dxf(text), CS)
    assert len(s.shape_ids) == 2 and len(doc.layers) == 3 and len(s.layers_created) == 2
    first = doc.shape(s.shape_ids[0])
    assert first.subpaths[0].nodes[0].p == pytest.approx((0, 400))  # máquina (0,0) = abajo izquierda
    assert first.group_id is not None and first.group_id == doc.shape(s.shape_ids[1]).group_id
    assert "2" in s.text()
    m = machine_to_doc_affine(CS)
    assert m.apply((10, 10)) == pytest.approx(CS.machine_to_doc((10, 10)))


def test_export_round_trips():
    d = demo_document()
    r = parse_svg(export_svg(d))
    assert r.count == len([s for s in d.shapes if s.image is None])  # las imágenes no se exportan
    assert r.bounds() == pytest.approx(d.bounds(False), abs=1e-3)
    doc2 = new_document()
    apply_import(doc2, parse_dxf(export_dxf(d, CS)), CS)
    assert doc2.bounds(False) == pytest.approx(d.bounds(False), abs=0.05)


def test_read_file_dispatch(tmp_path):
    p = tmp_path / "a.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 0 10 10">'
                 '<rect width="5" height="5"/></svg>')
    assert read_file(str(p)).count == 1
    with pytest.raises(UnsupportedFormat):
        read_file(str(tmp_path / "a.png"))
    with pytest.raises(UnsupportedFormat):
        read_file(str(tmp_path / "a.xyz"))


def test_svg_rejects_entities():
    import pytest
    from openlaser.importers.svg import parse_svg
    evil = '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>'
    with pytest.raises(ValueError):
        parse_svg(evil)
