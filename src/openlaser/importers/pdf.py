"""Importador PDF vectorial (y AI guardado con compatibilidad PDF). Código propio.

Lee objetos (también dentro de flujos de objetos ``/ObjStm``), flujos con
``/FlateDecode``, las páginas y sus ``/Contents`` y ``/Resources /XObject`` de
tipo formulario, e interpreta los operadores de trazado:

``m l c v y h re`` (construcción), ``S s f F f* B B* b b* n`` (pintado),
``cm q Q`` (matriz y pila gráfica), ``RG rg G g K k`` (color de trazo/relleno
→ capa) y ``Do`` (formularios). Texto, imágenes, sombreados y recortes no se
importan y se informa de ellos.

Unidades: puntos PDF (1/72 in) → mm. Y hacia arriba (se voltea al aplicar).
"""
from __future__ import annotations

import re
import zlib
from collections import Counter
from dataclasses import dataclass

from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine
from openlaser.importers.svg import ImportedShape, ImportResult

PT = 25.4 / 72.0
MAX_PAGES = 1
MAX_OPS = 2_000_000

_OBJ = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_TOKEN = re.compile(rb"\s*(?:(%[^\r\n]*)|(<<|>>|\[|\]|\{|\})|(/[^\s/<>\[\]()%{}]*)|(\((?:\\.|[^\\)])*\))"
                    rb"|(<[0-9A-Fa-f\s]*>)|([^\s/<>\[\]()%{}]+))", re.S)


@dataclass(frozen=True)
class Ref:
    num: int


class Name(str):
    pass


def _parse_value(tokens: list[bytes], i: int) -> tuple[object, int]:
    t = tokens[i]
    if t == b"<<":
        d: dict[str, object] = {}
        i += 1
        while i < len(tokens) and tokens[i] != b">>":
            key = tokens[i].decode("latin-1")[1:]
            val, i = _parse_value(tokens, i + 1)
            d[key] = val
        return d, i + 1
    if t == b"[":
        arr: list[object] = []
        i += 1
        while i < len(tokens) and tokens[i] != b"]":
            val, i = _parse_value(tokens, i)
            arr.append(val)
        return arr, i + 1
    if t.startswith(b"/"):
        return Name(t[1:].decode("latin-1")), i + 1
    if t.startswith(b"(") or (t.startswith(b"<") and t != b"<<"):
        return t, i + 1
    # entero seguido de «G R» = referencia
    if re.fullmatch(rb"[+-]?\d+", t) and i + 2 < len(tokens) and tokens[i + 2] == b"R" \
            and re.fullmatch(rb"\d+", tokens[i + 1]):
        return Ref(int(t)), i + 3
    try:
        return (float(t) if b"." in t else int(t)), i + 1
    except ValueError:
        return t.decode("latin-1"), i + 1


def _tokenize(data: bytes) -> list[bytes]:
    out = []
    for m in _TOKEN.finditer(data):
        if m.group(1):
            continue
        tok = next(g for g in m.groups()[1:] if g is not None)
        out.append(tok)
    return out


class PdfDocument:
    def __init__(self, data: bytes) -> None:
        if not data.lstrip()[:5].startswith(b"%PDF"):
            raise ValueError("No es un archivo PDF (o AI sin compatibilidad PDF)")
        if b"/Encrypt" in data:
            raise ValueError("PDF cifrado: no se puede importar")
        self.data = data
        self.objects: dict[int, object] = {}
        self.streams: dict[int, bytes] = {}
        self._scan()

    def _scan(self) -> None:
        for m in _OBJ.finditer(self.data):
            num = int(m.group(1))
            start = m.end()
            end = self.data.find(b"endobj", start)
            if end < 0:
                continue
            body = self.data[start:end]
            sidx = body.find(b"stream")
            if sidx >= 0 and body[:sidx].rstrip().endswith(b">>"):
                head = body[:sidx]
                raw = body[sidx + 6:]
                raw = raw[2:] if raw.startswith(b"\r\n") else raw[1:] if raw[:1] in (b"\n", b"\r") else raw
                eidx = raw.rfind(b"endstream")
                raw = raw[:eidx] if eidx >= 0 else raw
                dic = self._value(head)
                self.objects[num] = dic
                self.streams[num] = raw
            else:
                try:
                    self.objects[num] = self._value(body)
                except (IndexError, StopIteration):
                    continue
        for num, obj in list(self.objects.items()):
            if isinstance(obj, dict) and obj.get("Type") == "ObjStm":
                self._expand_objstm(num, obj)

    def _value(self, data: bytes) -> object:
        toks = _tokenize(data)
        if not toks:
            return None
        return _parse_value(toks, 0)[0]

    def _expand_objstm(self, num: int, dic: dict) -> None:
        content = self.stream(num)
        n, first = int(self.resolve(dic.get("N", 0)) or 0), int(self.resolve(dic.get("First", 0)) or 0)  # type: ignore[arg-type]
        header = _tokenize(content[:first])
        pairs = [(int(header[2 * k]), int(header[2 * k + 1])) for k in range(min(n, len(header) // 2))]
        for k, (onum, off) in enumerate(pairs):
            end = pairs[k + 1][1] if k + 1 < len(pairs) else len(content) - first
            if onum not in self.objects:
                try:
                    self.objects[onum] = self._value(content[first + off:first + end])
                except (IndexError, StopIteration):
                    continue

    def resolve(self, v: object) -> object:
        seen = 0
        while isinstance(v, Ref) and seen < 32:
            v = self.objects.get(v.num)
            seen += 1
        return v

    def stream(self, num: int) -> bytes:
        dic = self.objects.get(num)
        raw = self.streams.get(num, b"")
        if not isinstance(dic, dict):
            return raw
        filters = self.resolve(dic.get("Filter"))
        flist = filters if isinstance(filters, list) else [filters] if filters else []
        for f in flist:
            f = self.resolve(f)
            if f == "FlateDecode":
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    raw = zlib.decompressobj().decompress(raw)
            elif f in (None, ""):
                continue
            else:
                raise ValueError(f"Filtro PDF no admitido: {f}")
        return raw

    def pages(self) -> list[dict]:
        out = []
        root = None
        for obj in self.objects.values():
            if isinstance(obj, dict) and obj.get("Type") == "Catalog":
                root = obj
                break
        if root is not None:
            self._collect(self.resolve(root.get("Pages")), out, {})
        if not out:
            out = [o for o in self.objects.values() if isinstance(o, dict) and o.get("Type") == "Page"]
        return out

    def _collect(self, node: object, out: list[dict], inherited: dict) -> None:
        if not isinstance(node, dict) or len(out) > 1000:
            return
        inh = dict(inherited)
        for k in ("MediaBox", "Resources"):
            if k in node:
                inh[k] = node[k]
        if node.get("Type") == "Page":
            page = dict(inh)
            page.update(node)
            out.append(page)
            return
        kids = self.resolve(node.get("Kids"))
        for kid in kids if isinstance(kids, list) else []:
            self._collect(self.resolve(kid), out, inh)

    def content_of(self, page: dict) -> bytes:
        c = page.get("Contents")
        refs = c if isinstance(c, list) else [c]
        parts = []
        for r in refs:
            if isinstance(r, Ref):
                parts.append(self.stream(r.num))
            else:
                inner = self.resolve(r)
                if isinstance(inner, list):
                    parts += [self.stream(x.num) for x in inner if isinstance(x, Ref)]
        return b"\n".join(parts)


def _color_hex(vals: list[float], space: str) -> str:
    if space == "gray" and vals:
        r = g = b = vals[0]
    elif space == "rgb" and len(vals) >= 3:
        r, g, b = vals[:3]
    elif space == "cmyk" and len(vals) >= 4:
        c, m, y, k = vals[:4]
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    else:
        r = g = b = 0.0
    return "#{:02x}{:02x}{:02x}".format(*(int(round(max(0.0, min(1.0, v)) * 255)) for v in (r, g, b)))


class _Interp:
    def __init__(self, pdf: PdfDocument, result: ImportResult) -> None:
        self.pdf = pdf
        self.res = result
        self.ops = 0

    def run(self, content: bytes, resources: dict, ctm: Affine, depth: int = 0) -> None:
        toks = _tokenize(content)
        stack: list[object] = []
        gs = {"ctm": ctm, "stroke": "#000000", "fill": "#000000"}
        gstack: list[dict] = []
        builder = gp.PathBuilder()
        cur: tuple[float, float] | None = None
        start: tuple[float, float] | None = None
        i = 0
        in_text = False
        xobjects = self.pdf.resolve(resources.get("XObject")) if isinstance(resources, dict) else None

        def nums(n: int) -> list[float]:
            vals = [float(v) for v in stack[-n:] if isinstance(v, (int, float))]
            return vals if len(vals) == n else [0.0] * n

        def paint(color: str) -> None:
            nonlocal builder, cur, start
            subs = [sp for sp in builder.finish() if len(sp.nodes) >= 2]
            if subs:
                m = Affine.scale(PT) @ gs["ctm"]  # type: ignore[operator]
                self.res.shapes.append(ImportedShape(color, [sp.transformed(m) for sp in subs], "pdf"))
            builder = gp.PathBuilder()
            cur = start = None

        while i < len(toks):
            tok = toks[i]
            i += 1
            if tok in (b"<<", b"["):
                val, i = _parse_value(toks, i - 1)
                stack.append(val)
                continue
            if tok == b"BI":  # imagen en línea: se salta hasta EI
                end = content.find(b"EI", content.find(b"ID"))
                self.res.unsupported["imagen"] += 1
                while i < len(toks) and toks[i] != b"EI":
                    i += 1
                i += 1
                _ = end
                continue
            if re.fullmatch(rb"[+-]?(\d+\.?\d*|\.\d+)", tok):
                stack.append(float(tok))
                continue
            if tok.startswith(b"/") or tok.startswith(b"(") or tok.startswith(b"<"):
                stack.append(Name(tok[1:].decode("latin-1")) if tok.startswith(b"/") else tok)
                continue
            op = tok.decode("latin-1")
            self.ops += 1
            if self.ops > MAX_OPS:
                raise ValueError("PDF demasiado complejo")
            if in_text:
                if op == "ET":
                    in_text = False
                stack.clear()
                continue
            if op == "m":
                x, y = nums(2)
                builder.move_to((x, y))
                cur = start = (x, y)
            elif op == "l":
                x, y = nums(2)
                if cur is None:
                    builder.move_to((x, y))
                    start = (x, y)
                else:
                    builder.line_to((x, y))
                cur = (x, y)
            elif op == "c":
                x1, y1, x2, y2, x3, y3 = nums(6)
                if cur is not None:
                    builder.cubic_to((x1, y1), (x2, y2), (x3, y3))
                cur = (x3, y3)
            elif op == "v":
                x2, y2, x3, y3 = nums(4)
                if cur is not None:
                    builder.cubic_to(cur, (x2, y2), (x3, y3))
                cur = (x3, y3)
            elif op == "y":
                x1, y1, x3, y3 = nums(4)
                if cur is not None:
                    builder.cubic_to((x1, y1), (x3, y3), (x3, y3))
                cur = (x3, y3)
            elif op == "h":
                if cur is not None:
                    builder.close()
                    cur = start
            elif op == "re":
                x, y, w, h = nums(4)
                builder.move_to((x, y))
                builder.line_to((x + w, y))
                builder.line_to((x + w, y + h))
                builder.line_to((x, y + h))
                builder.close()
                cur = start = (x, y)
            elif op in ("S", "s"):
                if op == "s":
                    builder.close()
                paint(str(gs["stroke"]))
            elif op in ("f", "F", "f*"):
                builder.close()
                paint(str(gs["fill"]))
            elif op in ("B", "B*", "b", "b*"):
                builder.close()
                paint(str(gs["stroke"]))
            elif op == "n":
                builder = gp.PathBuilder()
                cur = start = None
            elif op == "q":
                gstack.append(dict(gs))
            elif op == "Q":
                if gstack:
                    gs = gstack.pop()
            elif op == "cm":
                a, b, c, d, e, f = nums(6)
                gs["ctm"] = gs["ctm"] @ Affine(a, b, c, d, e, f)  # type: ignore[operator]
            elif op in ("RG", "rg", "G", "g", "K", "k"):
                space = {"RG": "rgb", "rg": "rgb", "G": "gray", "g": "gray", "K": "cmyk", "k": "cmyk"}[op]
                n = {"rgb": 3, "gray": 1, "cmyk": 4}[space]
                col = _color_hex(nums(n), space)
                gs["stroke" if op.isupper() else "fill"] = col
            elif op in ("SC", "SCN", "sc", "scn"):
                vals = [float(v) for v in stack if isinstance(v, (int, float))]
                space = "rgb" if len(vals) >= 3 else "gray"
                gs["stroke" if op.startswith("S") else "fill"] = _color_hex(vals, space if len(vals) != 4 else "cmyk")
            elif op == "BT":
                in_text = True
                self.res.unsupported["texto"] += 1
            elif op == "Do":
                name = stack[-1] if stack else None
                xo = self.pdf.resolve(xobjects.get(str(name))) if isinstance(xobjects, dict) else None
                ref = xobjects.get(str(name)) if isinstance(xobjects, dict) else None
                if isinstance(xo, dict) and xo.get("Subtype") == "Form" and isinstance(ref, Ref) and depth < 8:
                    mat = xo.get("Matrix")
                    m = Affine(*[float(v) for v in mat]) if isinstance(mat, list) and len(mat) == 6 else Affine()
                    res = self.pdf.resolve(xo.get("Resources")) or resources
                    self.run(self.pdf.stream(ref.num), res if isinstance(res, dict) else {}, gs["ctm"] @ m,  # type: ignore[operator]
                             depth + 1)
                else:
                    self.res.unsupported["imagen"] += 1
            elif op == "sh":
                self.res.unsupported["sombreado"] += 1
            elif op in ("W", "W*"):
                pass  # recorte: se ignora (se importa la geometría completa)
            stack.clear()


def parse_pdf(data: bytes, source: str = "") -> ImportResult:
    pdf = PdfDocument(data)
    pages = pdf.pages()
    if not pages:
        raise ValueError("El PDF no tiene páginas")
    res = ImportResult(source=source, y_up=True)
    res.unsupported = Counter()
    if len(pages) > MAX_PAGES:
        res.warnings.append(f"El PDF tiene {len(pages)} páginas; sólo se importa la primera")
    page = pages[0]
    box = pdf.resolve(page.get("MediaBox"))
    ox = oy = 0.0
    if isinstance(box, list) and len(box) == 4:
        vx, vy = pdf.resolve(box[0]), pdf.resolve(box[1])
        if isinstance(vx, (int, float)) and isinstance(vy, (int, float)):
            ox, oy = float(vx), float(vy)
    res_dict = pdf.resolve(page.get("Resources"))
    interp = _Interp(pdf, res)
    interp.run(pdf.content_of(page), res_dict if isinstance(res_dict, dict) else {}, Affine.translate(-ox, -oy))
    if not res.shapes:
        res.warnings.append("No se encontró geometría vectorial (¿texto sin convertir a curvas o sólo imágenes?)")
    return res


def parse_pdf_file(path: str) -> ImportResult:
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) > 200 * 1024 * 1024:
        raise ValueError("Archivo demasiado grande")
    return parse_pdf(data, source=path)
