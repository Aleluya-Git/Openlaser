"""Exportadores del documento: SVG (curvas) y DXF R12 (polilíneas)."""
from __future__ import annotations

from xml.sax.saxutils import escape

from openlaser.document.model import Document
from openlaser.geometry.coords import CoordinateSystem
from openlaser.geometry.path import SubPath


def _f(v: float) -> str:
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def subpath_d(sp: SubPath) -> str:
    if not sp.nodes:
        return ""
    parts = [f"M{_f(sp.nodes[0].x)} {_f(sp.nodes[0].y)}"]
    for _p0, c1, c2, p3 in sp.segments():
        if c1 is None or c2 is None:
            parts.append(f"L{_f(p3[0])} {_f(p3[1])}")
        else:
            parts.append(f"C{_f(c1[0])} {_f(c1[1])} {_f(c2[0])} {_f(c2[1])} {_f(p3[0])} {_f(p3[1])}")
    if sp.closed:
        if parts[-1].startswith("L"):
            parts.pop()  # el cierre Z traza el último tramo recto
        parts.append("Z")
    return " ".join(parts)


def export_svg(doc: Document, only_ids: set[str] | None = None) -> str:
    """SVG en milímetros (1 unidad de usuario = 1 mm), una capa por grupo."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(doc.width_mm)}mm" height="{_f(doc.height_mm)}mm" '
        f'viewBox="0 0 {_f(doc.width_mm)} {_f(doc.height_mm)}">',
        f"  <title>{escape(doc.name)}</title>",
    ]
    for layer in doc.sorted_layers():
        shapes = [s for s in doc.shapes if s.layer_id == layer.id and s.image is None
                  and (only_ids is None or s.id in only_ids)]
        if not shapes:
            continue
        out.append(f'  <g id="layer-{escape(layer.id)}" data-name="{escape(layer.name)}" fill="none" '
                   f'stroke="{layer.color}" stroke-width="0.1">')
        for s in shapes:
            d = " ".join(subpath_d(sp) for sp in s.subpaths)
            out.append(f'    <path id="{escape(s.id)}" d="{d}"/>')
        out.append("  </g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _aci(color: str) -> int:
    table = {"#ff0000": 1, "#ffff00": 2, "#00ff00": 3, "#00ffff": 4, "#0000ff": 5, "#ff00ff": 6}
    return table.get(color.lower(), 7)


def export_dxf(doc: Document, cs: CoordinateSystem, only_ids: set[str] | None = None, tol: float = 0.02) -> str:
    """DXF R12 ASCII en coordenadas de máquina (Y hacia arriba); las curvas se aplanan."""
    lines: list[str] = []

    def tag(code: int, value: object) -> None:
        lines.append(str(code))
        lines.append(str(value))

    tag(0, "SECTION")
    tag(2, "HEADER")
    tag(9, "$ACADVER")
    tag(1, "AC1009")
    tag(9, "$INSUNITS")
    tag(70, 4)
    tag(0, "ENDSEC")
    tag(0, "SECTION")
    tag(2, "TABLES")
    tag(0, "TABLE")
    tag(2, "LAYER")
    tag(70, len(doc.layers))
    for layer in doc.layers:
        tag(0, "LAYER")
        tag(2, _layer_name(layer.name))
        tag(70, 0)
        tag(62, _aci(layer.color))
        tag(6, "CONTINUOUS")
    tag(0, "ENDTAB")
    tag(0, "ENDSEC")
    tag(0, "SECTION")
    tag(2, "ENTITIES")
    names = {ly.id: _layer_name(ly.name) for ly in doc.layers}
    for s in doc.shapes:
        if s.image is not None:  # las imágenes no tienen representación vectorial
            continue
        if only_ids is not None and s.id not in only_ids:
            continue
        for sp in s.subpaths:
            pts = [cs.doc_to_machine(p) for p in sp.flatten(tol)]
            if len(pts) < 2:
                continue
            tag(0, "POLYLINE")
            tag(8, names.get(s.layer_id, "0"))
            tag(66, 1)
            tag(70, 1 if sp.closed else 0)
            for x, y in pts:
                tag(0, "VERTEX")
                tag(8, names.get(s.layer_id, "0"))
                tag(10, _f(x))
                tag(20, _f(y))
            tag(0, "SEQEND")
    tag(0, "ENDSEC")
    tag(0, "EOF")
    return "\n".join(lines) + "\n"


def _layer_name(name: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)
    return clean[:31] or "0"
