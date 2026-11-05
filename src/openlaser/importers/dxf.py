"""Importador DXF ASCII propio (sin dependencias externas).

Entidades admitidas: LINE, LWPOLYLINE (con arcos por *bulge*), POLYLINE/VERTEX,
CIRCLE, ARC, ELLIPSE, SPLINE (evaluada por De Boor) e INSERT de bloques (con
escala, giro y anidamiento). Unidades por ``$INSUNITS``. Los colores ACI o
verdaderos (también *por capa*) determinan la capa del documento.

No admitidas (se notifican): TEXT, MTEXT, DIMENSION, HATCH, SOLID, IMAGE,
3DFACE y otras. Los DXF binarios no se admiten.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine
from openlaser.importers.svg import ImportedShape, ImportResult

Point = tuple[float, float]

INSUNITS_MM = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 0.0000254, 9: 0.0254, 10: 914.4,
               13: 0.001, 14: 100.0}
ACI = {1: "#ff0000", 2: "#ffff00", 3: "#00ff00", 4: "#00ffff", 5: "#0000ff", 6: "#ff00ff", 7: "#000000",
       8: "#808080", 9: "#c0c0c0"}
SUPPORTED = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE", "INSERT"}
IGNORED = {"POINT", "VIEWPORT", "SEQEND", "VERTEX", "ATTDEF", "ATTRIB"}


def aci_color(index: int) -> str:
    if index in ACI:
        return ACI[index]
    # paleta estable para el resto de índices
    h = (index * 37) % 360
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h / 360, 0.85, 0.85)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


@dataclass
class _Entity:
    kind: str
    tags: list[tuple[int, str]] = field(default_factory=list)
    children: list["_Entity"] = field(default_factory=list)

    def get(self, code: int, default: str | None = None) -> str | None:
        for c, v in self.tags:
            if c == code:
                return v
        return default

    def num(self, code: int, default: float = 0.0) -> float:
        v = self.get(code)
        try:
            return float(v) if v is not None else default
        except ValueError:
            return default

    def all(self, code: int) -> list[str]:
        return [v for c, v in self.tags if c == code]


def _pairs(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    out = []
    for i in range(0, len(lines) - 1, 2):
        try:
            code = int(lines[i].strip())
        except ValueError:
            raise ValueError(f"DXF no válido cerca de la línea {i + 1}") from None
        out.append((code, lines[i + 1].strip()))
    return out


def _entities(pairs: list[tuple[int, str]], start: int, end: int) -> list[_Entity]:
    ents: list[_Entity] = []
    cur: _Entity | None = None
    for code, val in pairs[start:end]:
        if code == 0:
            if val == "VERTEX" and cur is not None and cur.kind == "POLYLINE":
                cur.children.append(_Entity("VERTEX"))
                continue
            if val == "SEQEND" and cur is not None and cur.kind == "POLYLINE":
                continue
            cur = _Entity(val)
            ents.append(cur)
        elif cur is not None:
            target = cur.children[-1] if cur.kind == "POLYLINE" and cur.children else cur
            target.tags.append((code, val))
    return ents


def parse_dxf(text: str, source: str = "") -> ImportResult:
    if text.startswith("AutoCAD Binary DXF"):
        raise ValueError("DXF binario no admitido; guárdelo como DXF ASCII")
    pairs = _pairs(text)
    res = ImportResult(source=source, y_up=True)
    sections: dict[str, tuple[int, int]] = {}
    i = 0
    while i < len(pairs):
        if pairs[i] == (0, "SECTION") and i + 1 < len(pairs) and pairs[i + 1][0] == 2:
            name = pairs[i + 1][1]
            j = i + 2
            while j < len(pairs) and pairs[j] != (0, "ENDSEC"):
                j += 1
            sections[name] = (i + 2, j)
            i = j
        i += 1
    if "ENTITIES" not in sections and not sections:
        sections["ENTITIES"] = (0, len(pairs))  # fragmento sin secciones

    scale = 1.0
    if "HEADER" in sections:
        a, b = sections["HEADER"]
        for k in range(a, b - 1):
            if pairs[k] == (9, "$INSUNITS"):
                try:
                    units = int(pairs[k + 1][1])
                except ValueError:
                    units = 0
                scale = INSUNITS_MM.get(units, 1.0)
                if units == 0:
                    res.warnings.append("DXF sin unidades: se asumen milímetros")
    layer_colors: dict[str, str] = {}
    if "TABLES" in sections:
        a, b = sections["TABLES"]
        for e in _entities(pairs, a, b):
            if e.kind == "LAYER":
                name = e.get(2, "0") or "0"
                c = int(e.num(62, 7))
                layer_colors[name] = aci_color(abs(c))
    blocks: dict[str, list[_Entity]] = {}
    if "BLOCKS" in sections:
        a, b = sections["BLOCKS"]
        current: list[_Entity] | None = None
        for e in _entities(pairs, a, b):
            if e.kind == "BLOCK":
                current = []
                blocks[e.get(2, "") or ""] = current
            elif e.kind == "ENDBLK":
                current = None
            elif current is not None:
                current.append(e)
    a, b = sections.get("ENTITIES", (0, 0))
    ctx = _Ctx(res, blocks, layer_colors)
    for e in _entities(pairs, a, b):
        ctx.entity(e, Affine.scale(scale), "#000000", 0)
    if not res.shapes and not res.unsupported:
        res.warnings.append("El DXF no contiene geometría importable")
    return res


class _Ctx:
    def __init__(self, res: ImportResult, blocks: dict[str, list[_Entity]], layer_colors: dict[str, str]) -> None:
        self.res, self.blocks, self.layer_colors = res, blocks, layer_colors

    def color(self, e: _Entity, inherited: str) -> str:
        true = e.get(420)
        if true is not None:
            try:
                return f"#{int(true) & 0xFFFFFF:06x}"
            except ValueError:
                pass
        c = int(e.num(62, 256))
        if c == 0:  # BYBLOCK
            return inherited
        if c == 256:  # BYLAYER
            return self.layer_colors.get(e.get(8, "0") or "0", "#000000")
        return aci_color(abs(c))

    def entity(self, e: _Entity, m: Affine, inherited: str, depth: int) -> None:
        kind = e.kind
        if kind in IGNORED:
            return
        if kind not in SUPPORTED:
            self.res.unsupported[kind] += 1
            return
        color = self.color(e, inherited)
        try:
            if kind == "INSERT":
                self.insert(e, m, color, depth)
                return
            subs = self.geometry(e)
        except (ValueError, ZeroDivisionError, IndexError) as exc:
            self.res.warnings.append(f"{kind}: {exc}")
            return
        if subs:
            layer = e.get(8, "0") or "0"
            self.res.shapes.append(ImportedShape(color, [sp.transformed(m) for sp in subs], f"{kind} ({layer})"))

    def insert(self, e: _Entity, m: Affine, color: str, depth: int) -> None:
        if depth > 16:
            self.res.warnings.append("Bloques anidados demasiado profundos")
            return
        name = e.get(2, "") or ""
        ents = self.blocks.get(name)
        if ents is None:
            self.res.unsupported["INSERT sin bloque"] += 1
            return
        t = (Affine.translate(e.num(10), e.num(20)) @ Affine.rotate(e.num(50))
             @ Affine.scale(e.num(41, 1.0) or 1.0, e.num(42, 1.0) or 1.0))
        cols, rows = int(e.num(70, 1)) or 1, int(e.num(71, 1)) or 1
        for r in range(rows):
            for c in range(cols):
                off = Affine.translate(c * e.num(44), r * e.num(45))
                for child in ents:
                    self.entity(child, m @ off @ t, color, depth + 1)

    def geometry(self, e: _Entity) -> list[gp.SubPath]:
        k = e.kind
        if k == "LINE":
            return [gp.polyline([(e.num(10), e.num(20)), (e.num(11), e.num(21))])]
        if k == "CIRCLE":
            r = e.num(40)
            return [gp.ellipse(e.num(10), e.num(20), r, r)] if r > 0 else []
        if k == "ARC":
            return [_arc(e.num(10), e.num(20), e.num(40), e.num(50), e.num(51))]
        if k == "LWPOLYLINE":
            xs = [float(v) for v in e.all(10)]
            ys = [float(v) for v in e.all(20)]
            bulges = _per_vertex(e.tags, 42, len(xs))
            return [_bulge_poly(list(zip(xs, ys)), bulges, bool(int(e.num(70)) & 1))]
        if k == "POLYLINE":
            pts = [(v.num(10), v.num(20)) for v in e.children if int(v.num(70)) & 16 == 0]
            bulges = [v.num(42) for v in e.children if int(v.num(70)) & 16 == 0]
            if int(e.num(70)) & (16 | 64):
                self.res.unsupported["POLYLINE malla/3D"] += 1
                return []
            return [_bulge_poly(pts, bulges, bool(int(e.num(70)) & 1))] if len(pts) >= 2 else []
        if k == "ELLIPSE":
            return [_ellipse(e)]
        if k == "SPLINE":
            return [_spline(e)]
        return []


def _per_vertex(tags: list[tuple[int, str]], code: int, n: int) -> list[float]:
    """Valores opcionales por vértice (p. ej. bulge 42) alineados con los 10 de LWPOLYLINE."""
    out: list[float] = []
    for c, v in tags:
        if c == 10:
            out.append(0.0)
        elif c == code and out:
            out[-1] = float(v)
    return (out + [0.0] * n)[:n]


def _arc(cx: float, cy: float, r: float, a0: float, a1: float) -> gp.SubPath:
    if a1 <= a0:
        a1 += 360.0
    start = (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
    end = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
    b = gp.PathBuilder()
    b.move_to(start)
    large = (a1 - a0) > 180
    # En DXF el ángulo crece en sentido antihorario con Y hacia arriba (= sweep positivo).
    for c1, c2, p in gp.arc_to_cubics(start, r, r, 0.0, large, True, end):
        b.cubic_to(c1, c2, p)
    subs = b.finish()
    return subs[0] if subs else gp.polyline([start, end])


def _bulge_poly(pts: list[Point], bulges: list[float], closed: bool) -> gp.SubPath:
    b = gp.PathBuilder()
    if not pts:
        return gp.SubPath((), False)
    b.move_to(pts[0])
    n = len(pts)
    count = n if closed else n - 1
    for i in range(count):
        p0, p1 = pts[i], pts[(i + 1) % n]
        bulge = bulges[i] if i < len(bulges) else 0.0
        if abs(bulge) < 1e-12 or p0 == p1:
            b.line_to(p1)
            continue
        theta = 4 * math.atan(bulge)
        chord = math.dist(p0, p1)
        r = abs(chord / (2 * math.sin(theta / 2)))
        arcs = gp.arc_to_cubics(p0, r, r, 0.0, abs(theta) > math.pi, bulge > 0, p1)
        for c1, c2, p in arcs:
            b.cubic_to(c1, c2, p)
    if closed:
        b.close()
    subs = b.finish()
    return subs[0] if subs else gp.polyline(pts, closed)


def _ellipse(e: _Entity) -> gp.SubPath:
    cx, cy = e.num(10), e.num(20)
    mx, my = e.num(11), e.num(21)
    ratio = e.num(40, 1.0)
    t0, t1 = e.num(41, 0.0), e.num(42, 2 * math.pi)
    a = math.hypot(mx, my)
    rot = math.atan2(my, mx)
    if t1 <= t0:
        t1 += 2 * math.pi
    full = abs((t1 - t0) - 2 * math.pi) < 1e-6
    if full:
        sp = gp.ellipse(0, 0, a, a * ratio)
        return sp.transformed(Affine.translate(cx, cy) @ Affine.rotate(math.degrees(rot)))
    steps = max(8, int(abs(t1 - t0) / (math.pi / 32)))
    pts = []
    for i in range(steps + 1):
        t = t0 + (t1 - t0) * i / steps
        x, y = a * math.cos(t), a * ratio * math.sin(t)
        pts.append((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)))
    return gp.polyline(pts, False)


def _spline(e: _Entity) -> gp.SubPath:
    degree = int(e.num(71, 3))
    knots = [float(v) for v in e.all(40)]
    ctrl = list(zip([float(v) for v in e.all(10)], [float(v) for v in e.all(20)]))
    closed = bool(int(e.num(70)) & 1)
    if len(ctrl) < 2:
        fit = list(zip([float(v) for v in e.all(11)], [float(v) for v in e.all(21)]))
        if len(fit) >= 2:
            return gp.polyline(fit, closed)
        raise ValueError("SPLINE sin puntos")
    n = len(ctrl)
    if len(knots) != n + degree + 1:
        knots = [0.0] * (degree + 1) + [i / (n - degree) for i in range(1, n - degree)] + [1.0] * (degree + 1)
    lo, hi = knots[degree], knots[n]
    steps = max(16, n * 12)
    pts = [_de_boor(degree, knots, ctrl, lo + (hi - lo) * i / steps) for i in range(steps + 1)]
    return gp.polyline(pts, closed)


def _de_boor(p: int, t: list[float], c: list[Point], x: float) -> Point:
    n = len(c)
    k = p
    while k < n - 1 and x >= t[k + 1]:
        k += 1
    d = [c[j + k - p] for j in range(p + 1)]
    for r in range(1, p + 1):
        for j in range(p, r - 1, -1):
            den = t[j + 1 + k - r] - t[j + k - p]
            alpha = (x - t[j + k - p]) / den if den else 0.0
            d[j] = ((1 - alpha) * d[j - 1][0] + alpha * d[j][0], (1 - alpha) * d[j - 1][1] + alpha * d[j][1])
    return d[p]


def parse_dxf_file(path: str) -> ImportResult:
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"AutoCAD Binary DXF"):
        raise ValueError("DXF binario no admitido; guárdelo como DXF ASCII")
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return parse_dxf(raw.decode(enc), source=path)
        except UnicodeDecodeError:
            continue
    raise ValueError("Codificación DXF no reconocida")

