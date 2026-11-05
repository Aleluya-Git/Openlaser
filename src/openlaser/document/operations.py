"""Operaciones de edición y organización sobre el documento (sin interfaz).

Todas modifican ``doc.shapes``/``doc.layers`` en el sitio; quien las llama las
envuelve en :class:`openlaser.document.commands.edit_document` para que sean
deshacibles. Las coordenadas son las del documento (mm, Y hacia abajo).
"""
from __future__ import annotations

import math
import uuid
from typing import Any, Callable, Iterable, Sequence

from openlaser.document.model import Document, Layer, Shape, _uid
from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine

Bounds = tuple[float, float, float, float]
Point = tuple[float, float]


# --- selección ---------------------------------------------------------------------
def expand_groups(doc: Document, ids: Iterable[str]) -> list[str]:
    ids = list(ids)
    groups = {s.group_id for s in doc.shapes if s.id in set(ids) and s.group_id}
    out = list(dict.fromkeys(ids))
    for s in doc.shapes:
        if s.group_id and s.group_id in groups and s.id not in out:
            out.append(s.id)
    return out


def editable_ids(doc: Document, ids: Iterable[str]) -> list[str]:
    """Filtra formas bloqueadas, ocultas o en capas bloqueadas."""
    locked_layers = {ly.id for ly in doc.layers if ly.locked}
    wanted = set(ids)
    return [s.id for s in doc.shapes if s.id in wanted and not s.locked and s.visible
            and s.layer_id not in locked_layers]


def selection_bounds(doc: Document, ids: Iterable[str]) -> Bounds | None:
    shapes = doc.shapes_by_ids(ids)
    return gp.bounds_of(sp for s in shapes for sp in s.subpaths)


def _map(doc: Document, ids: Iterable[str], fn: Callable[[Shape], Shape]) -> None:
    wanted = set(ids)
    doc.shapes = [fn(s) if s.id in wanted else s for s in doc.shapes]


# --- transformaciones ----------------------------------------------------------------
def transform(doc: Document, ids: Iterable[str], m: Affine) -> None:
    _map(doc, ids, lambda s: s.transformed(m))


def move(doc: Document, ids: Iterable[str], dx: float, dy: float) -> None:
    transform(doc, ids, Affine.translate(dx, dy))


def rotate(doc: Document, ids: Sequence[str], deg: float, center: Point | None = None) -> None:
    b = selection_bounds(doc, ids)
    if b is None:
        return
    c = center or ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    transform(doc, ids, Affine.rotate(deg, c))


def flip(doc: Document, ids: Sequence[str], horizontal: bool) -> None:
    b = selection_bounds(doc, ids)
    if b is None:
        return
    c = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    transform(doc, ids, Affine.scale(-1.0, 1.0, c) if horizontal else Affine.scale(1.0, -1.0, c))


def set_bounds(doc: Document, ids: Sequence[str], x: float | None = None, y: float | None = None,
               w: float | None = None, h: float | None = None, keep_aspect: bool = False) -> None:
    """Coloca/escala la selección para que su caja sea (x, y, w, h); ``None`` conserva el valor."""
    b = selection_bounds(doc, ids)
    if b is None:
        return
    bw, bh = b[2] - b[0], b[3] - b[1]
    nw = bw if w is None else max(1e-6, w)
    nh = bh if h is None else max(1e-6, h)
    sx = nw / bw if bw > 1e-9 else 1.0
    sy = nh / bh if bh > 1e-9 else 1.0
    if keep_aspect:
        if w is not None and h is None:
            sy = sx
        elif h is not None and w is None:
            sx = sy
    nx = b[0] if x is None else x
    ny = b[1] if y is None else y
    m = Affine.translate(nx, ny) @ Affine.scale(sx, sy) @ Affine.translate(-b[0], -b[1])
    transform(doc, ids, m)


def align(doc: Document, ids: Sequence[str], mode: str, reference: Bounds | None = None) -> None:
    """Alinea cada forma (o grupo) con la caja de la selección o con ``reference``."""
    ref = reference or selection_bounds(doc, ids)
    if ref is None:
        return
    for unit in _units(doc, ids):
        b = selection_bounds(doc, unit)
        if b is None:
            continue
        dx = dy = 0.0
        if mode == "left":
            dx = ref[0] - b[0]
        elif mode == "right":
            dx = ref[2] - b[2]
        elif mode == "hcenter":
            dx = (ref[0] + ref[2]) / 2 - (b[0] + b[2]) / 2
        elif mode == "top":
            dy = ref[1] - b[1]
        elif mode == "bottom":
            dy = ref[3] - b[3]
        elif mode == "vcenter":
            dy = (ref[1] + ref[3]) / 2 - (b[1] + b[3]) / 2
        elif mode == "center":
            dx = (ref[0] + ref[2]) / 2 - (b[0] + b[2]) / 2
            dy = (ref[1] + ref[3]) / 2 - (b[1] + b[3]) / 2
        else:
            raise ValueError(mode)
        if dx or dy:
            move(doc, unit, dx, dy)


def distribute(doc: Document, ids: Sequence[str], horizontal: bool) -> None:
    units = _units(doc, ids)
    if len(units) < 3:
        return
    boxes = [(u, selection_bounds(doc, u)) for u in units]
    idx = 0 if horizontal else 1
    boxes = [(u, b) for u, b in boxes if b is not None]
    boxes.sort(key=lambda ub: (ub[1][idx] + ub[1][idx + 2]) / 2)  # type: ignore[index]
    first = (boxes[0][1][idx] + boxes[0][1][idx + 2]) / 2  # type: ignore[index]
    last = (boxes[-1][1][idx] + boxes[-1][1][idx + 2]) / 2  # type: ignore[index]
    step = (last - first) / (len(boxes) - 1)
    for i, (u, b) in enumerate(boxes[1:-1], start=1):
        center = (b[idx] + b[idx + 2]) / 2  # type: ignore[index]
        delta = first + step * i - center
        move(doc, u, delta if horizontal else 0.0, 0.0 if horizontal else delta)


def equalize(doc: Document, ids: Sequence[str], width: bool) -> None:
    units = _units(doc, ids)
    if len(units) < 2:
        return
    ref = selection_bounds(doc, units[0])
    if ref is None:
        return
    target = (ref[2] - ref[0]) if width else (ref[3] - ref[1])
    for u in units[1:]:
        if width:
            set_bounds(doc, u, w=target)
        else:
            set_bounds(doc, u, h=target)


def _units(doc: Document, ids: Sequence[str]) -> list[list[str]]:
    """Agrupa la selección en unidades (cada grupo cuenta como una)."""
    seen: dict[str, list[str]] = {}
    out: list[list[str]] = []
    wanted = list(dict.fromkeys(ids))
    by_id = {s.id: s for s in doc.shapes}
    for sid in wanted:
        s = by_id.get(sid)
        if s is None:
            continue
        if s.group_id:
            if s.group_id not in seen:
                seen[s.group_id] = []
                out.append(seen[s.group_id])
            seen[s.group_id].append(sid)
        else:
            out.append([sid])
    return out


def move_to_point(doc: Document, ids: Sequence[str], target: Point, anchor: str = "center") -> None:
    b = selection_bounds(doc, ids)
    if b is None:
        return
    if anchor == "center":
        ax, ay = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    elif anchor == "bottom_left":
        ax, ay = b[0], b[3]
    elif anchor == "top_left":
        ax, ay = b[0], b[1]
    else:
        raise ValueError(anchor)
    move(doc, ids, target[0] - ax, target[1] - ay)


# --- orden de apilado ---------------------------------------------------------------
def reorder(doc: Document, ids: Iterable[str], how: str) -> None:
    wanted = set(ids)
    sel = [s for s in doc.shapes if s.id in wanted]
    rest = [s for s in doc.shapes if s.id not in wanted]
    if how == "front":
        doc.shapes = rest + sel
    elif how == "back":
        doc.shapes = sel + rest
    elif how in ("raise", "lower"):
        shapes = list(doc.shapes)
        rng = range(len(shapes) - 2, -1, -1) if how == "raise" else range(1, len(shapes))
        for i in rng:
            j = i + 1 if how == "raise" else i - 1
            if shapes[i].id in wanted and shapes[j].id not in wanted:
                shapes[i], shapes[j] = shapes[j], shapes[i]
        doc.shapes = shapes
    else:
        raise ValueError(how)


# --- altas y bajas ------------------------------------------------------------------
def add_shapes(doc: Document, shapes: Iterable[Shape]) -> list[str]:
    new = list(shapes)
    doc.shapes.extend(new)
    return [s.id for s in new]


def delete(doc: Document, ids: Iterable[str]) -> None:
    wanted = set(ids)
    doc.shapes = [s for s in doc.shapes if s.id not in wanted]


def duplicate(doc: Document, ids: Sequence[str], dx: float = 0.0, dy: float = 0.0) -> list[str]:
    group_map: dict[str, str] = {}
    copies = []
    for s in doc.shapes_by_ids(ids):
        gid = None
        if s.group_id:
            gid = group_map.setdefault(s.group_id, uuid.uuid4().hex[:10])
        copies.append(s.copy(group_id=gid).transformed(Affine.translate(dx, dy)) if (dx or dy)
                      else s.copy(group_id=gid))
    return add_shapes(doc, copies)


def group(doc: Document, ids: Sequence[str]) -> str | None:
    if len(ids) < 2:
        return None
    gid = uuid.uuid4().hex[:10]
    _map(doc, ids, lambda s: s.copy(id=s.id, group_id=gid))
    return gid


def ungroup(doc: Document, ids: Sequence[str]) -> None:
    _map(doc, ids, lambda s: s.copy(id=s.id, group_id=None))


def set_layer(doc: Document, ids: Iterable[str], layer_id: str) -> None:
    _map(doc, ids, lambda s: s.copy(id=s.id, layer_id=layer_id))


def set_flags(doc: Document, ids: Iterable[str], **flags: bool) -> None:
    _map(doc, ids, lambda s: s.copy(id=s.id, **flags))


def rename(doc: Document, sid: str, name: str) -> None:
    _map(doc, [sid], lambda s: s.copy(id=s.id, name=name))


# --- matrices -----------------------------------------------------------------------
def rect_array(doc: Document, ids: Sequence[str], cols: int, rows: int, dx: float, dy: float) -> list[str]:
    """Copias en rejilla; ``dx``/``dy`` son la separación entre cajas (mm)."""
    b = selection_bounds(doc, ids)
    if b is None:
        return []
    w, h = b[2] - b[0], b[3] - b[1]
    created: list[str] = []
    for r in range(max(1, rows)):
        for c in range(max(1, cols)):
            if r == 0 and c == 0:
                continue
            created += duplicate(doc, ids, c * (w + dx), r * (h + dy))
    return created


def circular_array(doc: Document, ids: Sequence[str], count: int, center: Point, total_deg: float = 360.0,
                   rotate_copies: bool = True) -> list[str]:
    b = selection_bounds(doc, ids)
    if b is None or count < 2:
        return []
    created: list[str] = []
    full = abs(abs(total_deg) - 360.0) < 1e-9
    step = total_deg / (count if full else count - 1)
    bc = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    for i in range(1, count):
        ang = step * i
        new_ids = duplicate(doc, ids)
        if rotate_copies:
            transform(doc, new_ids, Affine.rotate(ang, center))
        else:
            r = math.radians(ang)
            ox, oy = bc[0] - center[0], bc[1] - center[1]
            nx = center[0] + ox * math.cos(r) - oy * math.sin(r)
            ny = center[1] + ox * math.sin(r) + oy * math.cos(r)
            move(doc, new_ids, nx - bc[0], ny - bc[1])
        created += new_ids
    return created


def copies_along_path(doc: Document, ids: Sequence[str], path_shape_id: str, count: int,
                      rotate_copies: bool = True) -> list[str]:
    """Reparte ``count`` copias de la selección a lo largo de la forma ``path_shape_id``."""
    guide = doc.shape(path_shape_id)
    b = selection_bounds(doc, ids)
    if guide is None or b is None or count < 1:
        return []
    pts = guide.subpaths[0].flatten_closed(0.05)
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg)
    if total <= 0:
        return []
    closed = guide.subpaths[0].closed
    spacing = total / (count if closed else max(1, count - 1))
    bc = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    created: list[str] = []
    for i in range(count):
        target = spacing * i
        acc, k = 0.0, 0
        while k < len(seg) - 1 and acc + seg[k] < target:
            acc += seg[k]
            k += 1
        t = (target - acc) / seg[k] if seg[k] else 0.0
        (x0, y0), (x1, y1) = pts[k], pts[k + 1]
        px, py = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
        new_ids = duplicate(doc, ids)
        m = Affine.translate(px - bc[0], py - bc[1])
        if rotate_copies:
            ang = math.degrees(math.atan2(y1 - y0, x1 - x0))
            m = Affine.rotate(ang, (px, py)) @ m
        transform(doc, new_ids, m)
        created += new_ids
    return created


# --- operaciones de trayectoria -------------------------------------------------------
def map_subpaths(doc: Document, ids: Iterable[str], fn: Callable[[gp.SubPath], gp.SubPath]) -> None:
    _map(doc, ids, lambda s: s.with_subpaths(fn(sp) for sp in s.subpaths))


def close_paths(doc: Document, ids: Iterable[str]) -> None:
    map_subpaths(doc, ids, lambda sp: sp.with_closed(True) if len(sp.nodes) >= 3 else sp)


def simplify(doc: Document, ids: Iterable[str], tol: float = 0.05) -> None:
    map_subpaths(doc, ids, lambda sp: gp.simplify(sp, tol))


def remove_duplicate_nodes(doc: Document, ids: Iterable[str]) -> None:
    map_subpaths(doc, ids, gp.remove_duplicate_nodes)


def reverse(doc: Document, ids: Iterable[str]) -> None:
    map_subpaths(doc, ids, lambda sp: sp.reversed())


def repair(doc: Document, ids: Iterable[str], tol: float = 0.05) -> None:
    """Elimina nodos duplicados, une tramos abiertos contiguos y cierra los que se tocan."""
    _map(doc, ids, lambda s: s.with_subpaths(
        gp.join_open_subpaths([gp.remove_duplicate_nodes(sp) for sp in s.subpaths if len(sp.nodes) >= 2], tol)))


def round_corners(doc: Document, ids: Iterable[str], radius: float) -> None:
    """Redondea las esquinas entre tramos rectos (las curvas no se modifican)."""
    map_subpaths(doc, ids, lambda sp: gp.round_corners(sp, radius))


def vector_ids(doc: Document, ids: Iterable[str]) -> list[str]:
    """Ids que no son imágenes (las operaciones de trayectoria no se aplican a imágenes)."""
    wanted = set(ids)
    return [s.id for s in doc.shapes if s.id in wanted and s.image is None]


def image_ids(doc: Document, ids: Iterable[str]) -> list[str]:
    wanted = set(ids)
    return [s.id for s in doc.shapes if s.id in wanted and s.image is not None]


def set_image_settings(doc: Document, ids: Iterable[str], settings: Any) -> None:
    from dataclasses import replace as _replace
    _map(doc, image_ids(doc, ids), lambda s: _replace(s, image=_replace(s.image, settings=settings)))  # type: ignore[type-var]


def combine(doc: Document, ids: Sequence[str]) -> str | None:
    shapes = [s for s in doc.shapes_by_ids(ids) if s.image is None]
    if len(shapes) < 2:
        return None
    first = shapes[0]
    merged = first.copy(id=first.id, subpaths=tuple(sp for s in shapes for sp in s.subpaths), group_id=None,
                        kind="path")
    rest = {s.id for s in shapes[1:]}
    doc.shapes = [merged if s.id == first.id else s for s in doc.shapes if s.id not in rest]
    return merged.id


def break_apart(doc: Document, ids: Sequence[str]) -> list[str]:
    out: list[Shape] = []
    created: list[str] = []
    wanted = set(ids)
    for s in doc.shapes:
        if s.id in wanted and len(s.subpaths) > 1 and s.image is None:
            for i, sp in enumerate(s.subpaths):
                part = s.copy(subpaths=(sp,), name=f"{s.name} {i + 1}".strip(), group_id=None, kind="path")
                out.append(part)
                created.append(part.id)
        else:
            out.append(s)
    doc.shapes = out
    return created


def edit_node(doc: Document, sid: str, sub_index: int, node_index: int, node: gp.Node | None) -> None:
    """Sustituye (o elimina si ``node`` es None) un nodo concreto."""
    def fn(s: Shape) -> Shape:
        subs = list(s.subpaths)
        sp = subs[sub_index]
        nodes = list(sp.nodes)
        if node is None:
            del nodes[node_index]
        else:
            nodes[node_index] = node
        if len(nodes) < 2:
            del subs[sub_index]
        else:
            subs[sub_index] = gp.SubPath(tuple(nodes), sp.closed and len(nodes) >= 3)
        return s.with_subpaths(subs)
    _map(doc, [sid], fn)
    doc.shapes = [s for s in doc.shapes if s.subpaths]


# --- capas -----------------------------------------------------------------------------
def add_layer(doc: Document, name: str | None = None, color: str | None = None) -> Layer:
    layer = Layer(name or f"Capa {len(doc.layers) + 1}", color or doc.unused_color(), order=doc.next_order())
    doc.layers.append(layer)
    return layer


def delete_layer(doc: Document, layer_id: str, move_shapes_to: str | None = None) -> None:
    if len(doc.layers) <= 1:
        raise ValueError("El documento necesita al menos una capa")
    if move_shapes_to:
        set_layer(doc, [s.id for s in doc.shapes if s.layer_id == layer_id], move_shapes_to)
    else:
        doc.shapes = [s for s in doc.shapes if s.layer_id != layer_id]
    doc.layers = [ly for ly in doc.layers if ly.id != layer_id]
    renumber_layers(doc)


def move_layer(doc: Document, layer_id: str, delta: int) -> None:
    layers = doc.sorted_layers()
    idx = next((i for i, ly in enumerate(layers) if ly.id == layer_id), None)
    if idx is None:
        return
    j = max(0, min(len(layers) - 1, idx + delta))
    layers.insert(j, layers.pop(idx))
    for i, ly in enumerate(layers):
        ly.order = i


def renumber_layers(doc: Document) -> None:
    for i, ly in enumerate(doc.sorted_layers()):
        ly.order = i


def copy_layer_settings(doc: Document, source_id: str, target_ids: Iterable[str]) -> None:
    src = doc.layer(source_id)
    if src is None:
        return
    settings = src.settings_dict()
    for tid in target_ids:
        tgt = doc.layer(tid)
        if tgt is not None and tgt is not src:
            tgt.apply_settings(settings)


def new_id() -> str:
    return _uid()
