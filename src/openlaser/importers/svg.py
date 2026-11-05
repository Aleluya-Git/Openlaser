"""Importador SVG propio (sin dependencias externas).

Admite ``path`` (M L H V C S Q T A Z, absolutos y relativos), ``rect`` (con
esquinas redondeadas), ``circle``, ``ellipse``, ``line``, ``polyline``,
``polygon``, grupos ``g``, ``use`` con referencias internas, transformaciones
(``matrix``, ``translate``, ``scale``, ``rotate``, ``skewX``, ``skewY``),
``viewBox`` y unidades (mm, cm, in, pt, pc, px a 96 ppp). Los colores de trazo
(o de relleno si no hay trazo) determinan la capa.

Los elementos no admitidos (texto, imágenes, máscaras, recortes, patrones,
estilos CSS por clase…) se cuentan y se notifican al usuario.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field

from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine

Point = tuple[float, float]

UNIT_MM = {"mm": 1.0, "cm": 10.0, "in": 25.4, "pt": 25.4 / 72, "pc": 25.4 / 6, "px": 25.4 / 96, "": 25.4 / 96,
           "q": 0.25}
NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "green": "#008000", "lime": "#00ff00",
    "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff", "aqua": "#00ffff", "magenta": "#ff00ff",
    "fuchsia": "#ff00ff", "gray": "#808080", "grey": "#808080", "orange": "#ffa500", "purple": "#800080",
    "brown": "#a52a2a", "navy": "#000080", "maroon": "#800000", "olive": "#808000", "teal": "#008080",
    "silver": "#c0c0c0", "pink": "#ffc0cb",
}
UNSUPPORTED = {"text", "image", "foreignObject", "pattern", "clipPath", "mask", "filter", "linearGradient",
               "radialGradient", "marker", "switch", "a", "textPath", "tspan"}
IGNORED = {"title", "desc", "metadata", "defs", "symbol", "style", "namedview", "sodipodi:namedview"}


@dataclass
class ImportedShape:
    color: str
    subpaths: list[gp.SubPath]
    name: str = ""


@dataclass
class ImportResult:
    shapes: list[ImportedShape] = field(default_factory=list)
    unsupported: Counter = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)
    y_up: bool = False
    source: str = ""

    @property
    def count(self) -> int:
        return len(self.shapes)

    def bounds(self) -> tuple[float, float, float, float] | None:
        return gp.bounds_of(sp for s in self.shapes for sp in s.subpaths)


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def parse_length(value: str | None, default: float | None = None) -> float | None:
    """Longitud SVG a mm (sin unidad = px de 96 ppp)."""
    if value is None:
        return default
    m = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)\s*", value)
    if not m:
        return default
    num, unit = float(m.group(1)), m.group(2).lower()
    if unit == "%":
        return default
    return num * UNIT_MM.get(unit, 25.4 / 96)


def _num(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    m = re.match(r"\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)", value)
    return float(m.group(1)) if m else default


def normalize_color(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("none", "transparent", ""):
        return None
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) >= 6 and all(c in "0123456789abcdef" for c in h[:6]):
            return "#" + h[:6]
        return None
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        vals = []
        for p in parts[:3]:
            vals.append(round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p)))
        return "#" + "".join(f"{max(0, min(255, c)):02x}" for c in vals)
    if v.startswith("url("):
        return "#000000"
    return NAMED_COLORS.get(v)


def parse_transform(text: str | None) -> Affine:
    m = Affine()
    if not text:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        nums = [float(x) for x in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", args)]
        t = Affine()
        if name == "matrix" and len(nums) == 6:
            t = Affine(*nums)
        elif name == "translate" and nums:
            t = Affine.translate(nums[0], nums[1] if len(nums) > 1 else 0.0)
        elif name == "scale" and nums:
            t = Affine.scale(nums[0], nums[1] if len(nums) > 1 else nums[0])
        elif name == "rotate" and nums:
            t = Affine.rotate(nums[0], (nums[1], nums[2]) if len(nums) >= 3 else (0.0, 0.0))
        elif name == "skewX" and nums:
            t = Affine.skew(nums[0], 0.0)
        elif name == "skewY" and nums:
            t = Affine.skew(0.0, nums[0])
        m = m @ t
    return m


_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_FLAG = re.compile(r"\s*,?\s*([01])")


def parse_path_d(d: str) -> list[gp.SubPath]:
    """Convierte un atributo ``d`` en subtrayectorias (coordenadas de usuario SVG)."""
    b = gp.PathBuilder()
    pos = 0
    cmd = ""
    cur: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_c2: Point | None = None   # último manejador cúbico (para S)
    last_q: Point | None = None    # último control cuadrático (para T)
    n = len(d)

    def number() -> float:
        nonlocal pos
        m = _TOKEN.match(d, _skip(pos))
        if not m or m.group(0).isalpha():
            raise ValueError(f"número esperado en {pos}")
        pos = m.end()
        return float(m.group(0))

    def flag() -> bool:
        nonlocal pos
        m = _FLAG.match(d, pos)
        if not m:
            raise ValueError(f"indicador de arco esperado en {pos}")
        pos = m.end()
        return m.group(1) == "1"

    def _skip(p: int) -> int:
        while p < n and d[p] in " \t\r\n,":
            p += 1
        return p

    while True:
        pos = _skip(pos)
        if pos >= n:
            break
        if d[pos].isalpha():
            cmd = d[pos]
            pos += 1
        elif not cmd:
            raise ValueError("el trazado no empieza por una orden")
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = cur if rel else (0.0, 0.0)
        if c == "Z":
            b.close()
            cur = start
            last_c2 = last_q = None
            cmd = ""
            continue
        if c == "M":
            x, y = number() + ox, number() + oy
            b.move_to((x, y))
            cur = start = (x, y)
            cmd = "l" if rel else "L"   # coordenadas extra = lineto implícito
            last_c2 = last_q = None
            continue
        if c == "L":
            x, y = number() + ox, number() + oy
            b.line_to((x, y))
            cur = (x, y)
            last_c2 = last_q = None
        elif c == "H":
            x = number() + ox
            cur = (x, cur[1])
            b.line_to(cur)
            last_c2 = last_q = None
        elif c == "V":
            y = number() + (cur[1] if rel else 0.0)
            cur = (cur[0], y)
            b.line_to(cur)
            last_c2 = last_q = None
        elif c == "C":
            c1 = (number() + ox, number() + oy)
            c2 = (number() + ox, number() + oy)
            p = (number() + ox, number() + oy)
            _ensure(b, cur)
            b.cubic_to(c1, c2, p)
            cur, last_c2, last_q = p, c2, None
        elif c == "S":
            c1 = (2 * cur[0] - last_c2[0], 2 * cur[1] - last_c2[1]) if last_c2 else cur
            c2 = (number() + ox, number() + oy)
            p = (number() + ox, number() + oy)
            _ensure(b, cur)
            b.cubic_to(c1, c2, p)
            cur, last_c2, last_q = p, c2, None
        elif c == "Q":
            q = (number() + ox, number() + oy)
            p = (number() + ox, number() + oy)
            c1, c2 = gp.quad_to_cubic(cur, q, p)
            _ensure(b, cur)
            b.cubic_to(c1, c2, p)
            cur, last_q, last_c2 = p, q, None
        elif c == "T":
            q = (2 * cur[0] - last_q[0], 2 * cur[1] - last_q[1]) if last_q else cur
            p = (number() + ox, number() + oy)
            c1, c2 = gp.quad_to_cubic(cur, q, p)
            _ensure(b, cur)
            b.cubic_to(c1, c2, p)
            cur, last_q, last_c2 = p, q, None
        elif c == "A":
            rx, ry, phi = number(), number(), number()
            large, sweep = flag(), flag()
            p = (number() + ox, number() + oy)
            _ensure(b, cur)
            arcs = gp.arc_to_cubics(cur, rx, ry, phi, large, sweep, p)
            for c1, c2, end in arcs:
                b.cubic_to(c1, c2, end)
            if not arcs:
                b.line_to(p)
            cur = p
            last_c2 = last_q = None
        else:
            raise ValueError(f"orden desconocida {cmd}")
    return b.finish()


def _ensure(b: gp.PathBuilder, cur: Point) -> None:
    if b.current is None:
        b.move_to(cur)


class _Ctx:
    def __init__(self, m: Affine, style: dict[str, str]) -> None:
        self.m = m
        self.style = style


def _style_of(el: ET.Element, parent: dict[str, str]) -> dict[str, str]:
    style = {k: v for k, v in parent.items() if k not in ("display", "transform")}
    for key in ("stroke", "fill", "display", "visibility", "stroke-width", "opacity"):
        if key in el.attrib:
            style[key] = el.attrib[key]
    for part in el.attrib.get("style", "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            style[k.strip()] = v.strip()
    return style


def parse_svg(data: bytes | str, source: str = "") -> ImportResult:
    res = ImportResult(source=source)
    head = data[:4096] if isinstance(data, str) else data[:4096].decode("utf-8", "replace")
    if "<!ENTITY" in head.upper():  # entidades XML: riesgo de expansión masiva; los SVG reales no las necesitan
        raise ValueError("SVG no válido: contiene declaraciones <!ENTITY>")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"SVG no válido: {exc}") from exc
    if _local(root.tag) != "svg":
        raise ValueError("El archivo no es un SVG")

    vb = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    width_mm = parse_length(root.attrib.get("width"))
    height_mm = parse_length(root.attrib.get("height"))
    m = Affine()
    if vb:
        vals = [float(v) for v in re.split(r"[\s,]+", vb.strip()) if v]
        if len(vals) == 4 and vals[2] > 0 and vals[3] > 0:
            vx, vy, vw, vh = vals
            sx = (width_mm / vw) if width_mm else 25.4 / 96
            sy = (height_mm / vh) if height_mm else sx
            if width_mm and not height_mm:
                sy = sx
            if height_mm and not width_mm:
                sx = sy
            par = root.attrib.get("preserveAspectRatio", "xMidYMid meet")
            if "none" not in par and abs(sx - sy) > 1e-9:
                s = min(sx, sy)
                res.warnings.append("viewBox con proporción distinta del tamaño: se usa escala uniforme")
                sx = sy = s
            m = Affine.scale(sx, sy) @ Affine.translate(-vx, -vy)
    else:
        m = Affine.scale(25.4 / 96)
        if root.attrib.get("width", "").strip().endswith("mm"):
            m = Affine()
            res.warnings.append("SVG sin viewBox en mm: se asume 1 unidad = 1 mm")

    ids = {el.attrib["id"]: el for el in root.iter() if "id" in el.attrib}
    _walk(root, _Ctx(m, {}), res, ids, depth=0, skip_self=True)
    if not res.shapes and not res.unsupported:
        res.warnings.append("El SVG no contiene geometría importable")
    return res


def _walk(el: ET.Element, ctx: _Ctx, res: ImportResult, ids: dict[str, ET.Element], depth: int,
          skip_self: bool = False) -> None:
    if depth > 40:
        res.warnings.append("Anidamiento demasiado profundo; se ignora el resto")
        return
    tag = _local(el.tag)
    style = _style_of(el, ctx.style)
    if style.get("display") == "none" or style.get("visibility") == "hidden":
        return
    m = ctx.m @ parse_transform(el.attrib.get("transform")) if not skip_self else ctx.m
    if "class" in el.attrib and "class_css" not in res.unsupported:
        res.unsupported["class_css"] += 1
    if tag in ("svg", "g") or skip_self:
        if tag == "svg" and not skip_self:
            m = m @ Affine.translate(_num(el.attrib.get("x")), _num(el.attrib.get("y")))
        for child in el:
            _walk(child, _Ctx(m, style), res, ids, depth + 1)
        return
    if tag in IGNORED or tag.startswith("sodipodi") or tag.startswith("inkscape"):
        return
    if tag in UNSUPPORTED:
        res.unsupported[tag] += 1
        return
    if tag == "use":
        href = el.attrib.get("{http://www.w3.org/1999/xlink}href") or el.attrib.get("href", "")
        target = ids.get(href.lstrip("#"))
        if target is None:
            res.unsupported["use"] += 1
            return
        mu = m @ Affine.translate(_num(el.attrib.get("x")), _num(el.attrib.get("y")))
        if _local(target.tag) == "symbol":
            for child in target:
                _walk(child, _Ctx(mu, style), res, ids, depth + 1)
        else:
            _walk(target, _Ctx(mu, style), res, ids, depth + 1)
        return
    subs = _geometry(tag, el, res)
    if subs is None:
        if tag not in IGNORED:
            res.unsupported[tag] += 1
        return
    if not subs:
        return
    color = normalize_color(style.get("stroke")) or normalize_color(style.get("fill", "#000000")) or "#000000"
    subs = [sp.transformed(m) for sp in subs]
    name = el.attrib.get("id") or el.attrib.get("{http://www.inkscape.org/namespaces/inkscape}label") or tag
    res.shapes.append(ImportedShape(color, subs, name))


def _geometry(tag: str, el: ET.Element, res: ImportResult) -> list[gp.SubPath] | None:
    a = el.attrib
    try:
        if tag == "path":
            return parse_path_d(a.get("d", ""))
        if tag == "rect":
            w, h = _num(a.get("width")), _num(a.get("height"))
            if w <= 0 or h <= 0:
                return []
            rx = _num(a.get("rx"), -1)
            ry = _num(a.get("ry"), -1)
            r = rx if rx >= 0 else (ry if ry >= 0 else 0.0)
            if rx >= 0 and ry >= 0 and abs(rx - ry) > 1e-9:
                res.warnings.append("rect con rx≠ry: se usa rx")
            return [gp.rectangle(_num(a.get("x")), _num(a.get("y")), w, h, r)]
        if tag == "circle":
            r = _num(a.get("r"))
            return [gp.ellipse(_num(a.get("cx")), _num(a.get("cy")), r, r)] if r > 0 else []
        if tag == "ellipse":
            rx, ry = _num(a.get("rx")), _num(a.get("ry"))
            return [gp.ellipse(_num(a.get("cx")), _num(a.get("cy")), rx, ry)] if rx > 0 and ry > 0 else []
        if tag == "line":
            return [gp.polyline([(_num(a.get("x1")), _num(a.get("y1"))), (_num(a.get("x2")), _num(a.get("y2")))])]
        if tag in ("polyline", "polygon"):
            nums = [float(v) for v in re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", a.get("points", ""))]
            pts = list(zip(nums[0::2], nums[1::2]))
            return [gp.polyline(pts, tag == "polygon")] if len(pts) >= 2 else []
    except (ValueError, IndexError) as exc:
        res.warnings.append(f"{tag}: {exc}")
        return []
    return None


def parse_svg_file(path: str) -> ImportResult:
    with open(path, "rb") as fh:
        return parse_svg(fh.read(), source=path)

