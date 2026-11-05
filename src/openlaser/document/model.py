"""Modelo de documento independiente de la interfaz (formato 2).

* :class:`Layer` — capa basada en color con todos los parámetros de salida.
* :class:`Shape` — objeto inmutable formado por subtrayectorias con curvas
  Bézier (:mod:`openlaser.geometry.path`). Las ediciones crean objetos nuevos.
* :class:`Document` — capas + formas en orden de apilado (la última, delante).

El formato 1 (Fase 1, polilíneas ``points``) se migra automáticamente.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, Iterable

from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine
from openlaser.image_processing.settings import ImageSettings

Point = tuple[float, float]
FORMAT_VERSION = 3  # 3: imágenes (Fase 3)


class LayerMode(Enum):
    LINE = "line"
    FILL = "fill"
    FILL_LINE = "fill_line"
    IMAGE = "image"


LAYER_COLORS = ["#1f6feb", "#d1242f", "#1a7f37", "#bf8700", "#8250df", "#0a7f86", "#e85aad", "#57606a",
                "#fb8500", "#2a9d8f", "#6a4c93", "#9c6644", "#4361ee", "#ef476f", "#06d6a0", "#ffb703"]


def _uid() -> str:
    return uuid.uuid4().hex[:10]


@dataclass
class Layer:
    name: str
    color: str
    id: str = field(default_factory=_uid)
    mode: LayerMode = LayerMode.LINE
    enabled: bool = True
    visible: bool = True
    locked: bool = False
    output: bool = True
    order: int = 0
    speed_mm_min: float = 1000.0
    power_min_pct: float = 0.0
    power_max_pct: float = 20.0
    passes: int = 1
    line_interval_mm: float = 0.1
    fill_angle_deg: float = 0.0
    cross_fill: bool = False
    bidirectional: bool = True
    overscan_mm: float = 2.5
    air_assist: bool = False
    z_offset_mm: float = 0.0
    pause_after: bool = False
    dynamic_power: bool = True  # True = M4, False = M3
    frequency: float = 0.0      # PPI / frecuencia; 0 = no se usa
    lead_in_mm: float = 0.0
    lead_out_mm: float = 0.0
    pierce_ms: float = 0.0
    inside_first: bool = True
    optimize_travel: bool = True
    kerf_mm: float = 0.0
    tab_count: int = 0          # puentes por contorno cerrado (modo Línea); 0 = sin puentes
    tab_width_mm: float = 1.0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Layer":
        data = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        if "mode" in data:
            data["mode"] = LayerMode(data["mode"])
        return cls(**data)

    def settings_dict(self) -> dict[str, Any]:
        """Parámetros de salida (sin identidad), para copiar entre capas o guardar preajustes."""
        d = self.to_dict()
        for k in ("id", "name", "color", "order", "visible", "locked"):
            d.pop(k, None)
        return d

    def apply_settings(self, settings: dict[str, Any]) -> None:
        for k, v in settings.items():
            if k in ("id", "name", "color", "order", "visible", "locked"):
                continue
            if k == "mode":
                v = LayerMode(v)
            if k in self.__dataclass_fields__:
                setattr(self, k, v)


@dataclass(frozen=True)
class ImageRef:
    """Imagen colocada en el documento.

    ``corners`` son las esquinas superior izquierda, superior derecha e inferior
    izquierda del original (en mm del documento): cualquier transformación del
    objeto (mover, escalar, girar, voltear) se aplica a ellas. Los píxeles están
    en ``Document.images[resource]``.
    """
    resource: str
    px_w: int
    px_h: int
    corners: tuple[Point, Point, Point]
    settings: ImageSettings = field(default_factory=ImageSettings)

    def transformed(self, m: Affine) -> "ImageRef":
        return replace(self, corners=tuple(m.apply(c) for c in self.corners))  # type: ignore[arg-type]

    def frame(self) -> gp.SubPath:
        c0, c1, c3 = self.corners
        c2 = (c1[0] + c3[0] - c0[0], c1[1] + c3[1] - c0[1])
        return gp.polyline([c0, c1, c2, c3], True)

    @property
    def width_mm(self) -> float:
        c0, c1, _ = self.corners
        return ((c1[0] - c0[0]) ** 2 + (c1[1] - c0[1]) ** 2) ** 0.5

    @property
    def height_mm(self) -> float:
        c0, _, c3 = self.corners
        return ((c3[0] - c0[0]) ** 2 + (c3[1] - c0[1]) ** 2) ** 0.5

    def to_dict(self) -> dict[str, Any]:
        return {"resource": self.resource, "px_w": self.px_w, "px_h": self.px_h,
                "corners": [[round(v, 6) for v in c] for c in self.corners], "settings": self.settings.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImageRef":
        corners = tuple((float(c[0]), float(c[1])) for c in d["corners"][:3])
        return cls(str(d["resource"]), int(d["px_w"]), int(d["px_h"]), corners,  # type: ignore[arg-type]
                   ImageSettings.from_dict(d.get("settings")))


@dataclass(frozen=True)
class Shape:
    layer_id: str
    subpaths: tuple[gp.SubPath, ...]
    name: str = ""
    id: str = field(default_factory=_uid)
    visible: bool = True
    locked: bool = False
    group_id: str | None = None
    kind: str = "path"
    image: ImageRef | None = None

    def __post_init__(self) -> None:
        subs = self.subpaths
        if isinstance(subs, gp.SubPath):
            object.__setattr__(self, "subpaths", (subs,))
        elif subs and not isinstance(next(iter(subs)), gp.SubPath):
            # Compatibilidad Fase 1: lista de puntos de una polilínea cerrada
            object.__setattr__(self, "subpaths", (gp.polyline([tuple(p) for p in subs], True),))  # type: ignore[misc]
        elif not isinstance(subs, tuple):
            object.__setattr__(self, "subpaths", tuple(subs))

    # --- compatibilidad y medidas -----------------------------------------------
    @property
    def points(self) -> list[Point]:
        return [p for sp in self.subpaths for p in sp.flatten(0.1)]

    @property
    def closed(self) -> bool:
        return all(sp.closed for sp in self.subpaths)

    def bounds(self) -> tuple[float, float, float, float]:
        b = gp.bounds_of(self.subpaths)
        return b if b is not None else (0.0, 0.0, 0.0, 0.0)

    def node_count(self) -> int:
        return sum(len(sp.nodes) for sp in self.subpaths)

    # --- ediciones (devuelven objetos nuevos) -------------------------------------
    def transformed(self, m: Affine) -> "Shape":
        if self.image is not None:
            image = self.image.transformed(m)
            return replace(self, subpaths=(image.frame(),), image=image)
        return replace(self, subpaths=tuple(sp.transformed(m) for sp in self.subpaths))

    @property
    def is_image(self) -> bool:
        return self.image is not None

    def with_subpaths(self, subpaths: Iterable[gp.SubPath]) -> "Shape":
        if self.image is not None:  # el marco de una imagen sólo cambia con transformaciones
            return self
        return replace(self, subpaths=tuple(subpaths))

    def copy(self, **changes: Any) -> "Shape":
        changes.setdefault("id", _uid())
        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "layer_id": self.layer_id, "name": self.name, "visible": self.visible,
             "locked": self.locked, "group_id": self.group_id, "kind": self.kind,
             "subpaths": [sp.to_dict() for sp in self.subpaths]}
        if self.image is not None:
            d["image"] = self.image.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Shape":
        if "subpaths" in d:
            subs = tuple(gp.SubPath.from_dict(s) for s in d["subpaths"])
        else:  # formato 1: polilínea simple
            subs = (gp.polyline([tuple(p) for p in d.get("points", [])], bool(d.get("closed", True))),)
        image = ImageRef.from_dict(d["image"]) if d.get("image") else None
        if image is not None:
            subs = (image.frame(),)
        return cls(layer_id=d["layer_id"], subpaths=subs, name=d.get("name", ""), id=d.get("id") or _uid(),
                   visible=d.get("visible", True), locked=d.get("locked", False), group_id=d.get("group_id"),
                   kind=d.get("kind", "path"), image=image)


def make_image_shape(layer_id: str, resource: str, px_w: int, px_h: int, x: float, y: float, w_mm: float,
                     h_mm: float, name: str = "", settings: ImageSettings | None = None) -> Shape:
    corners = ((x, y), (x + w_mm, y), (x, y + h_mm))
    ref = ImageRef(resource, px_w, px_h, corners, settings or ImageSettings())
    return Shape(layer_id=layer_id, subpaths=(ref.frame(),), name=name, kind="image", image=ref)


def make_shape(layer_id: str, subpaths: gp.SubPath | Iterable[gp.SubPath], name: str = "", kind: str = "path") -> Shape:
    subs = (subpaths,) if isinstance(subpaths, gp.SubPath) else tuple(subpaths)
    return Shape(layer_id=layer_id, subpaths=subs, name=name, kind=kind)


@dataclass
class Document:
    name: str = "Sin título"
    width_mm: float = 400.0
    height_mm: float = 400.0
    layers: list[Layer] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)
    format_version: int = FORMAT_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)
    # Píxeles de las imágenes (``RasterSource``) por id de recurso. No forman parte
    # de ``to_dict``: el proyecto los guarda como recursos binarios aparte.
    images: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    # --- consulta -----------------------------------------------------------------
    def layer(self, layer_id: str) -> Layer | None:
        return next((ly for ly in self.layers if ly.id == layer_id), None)

    def shape(self, shape_id: str) -> Shape | None:
        return next((s for s in self.shapes if s.id == shape_id), None)

    def shapes_by_ids(self, ids: Iterable[str]) -> list[Shape]:
        wanted = set(ids)
        return [s for s in self.shapes if s.id in wanted]

    def sorted_layers(self) -> list[Layer]:
        return sorted(self.layers, key=lambda ly: ly.order)

    def output_shapes(self) -> list[tuple[Layer, Shape]]:
        result: list[tuple[Layer, Shape]] = []
        for layer in self.sorted_layers():
            if not (layer.enabled and layer.output):
                continue
            result.extend((layer, s) for s in self.shapes if s.layer_id == layer.id and s.visible)
        return result

    def all_points(self, output_only: bool = True) -> list[Point]:
        shapes = [s for _, s in self.output_shapes()] if output_only else self.shapes
        return [p for s in shapes for p in s.points]

    def bounds(self, output_only: bool = True) -> tuple[float, float, float, float] | None:
        shapes = [s for _, s in self.output_shapes()] if output_only else self.shapes
        return gp.bounds_of(sp for s in shapes for sp in s.subpaths)

    def used_image_resources(self) -> set[str]:
        return {s.image.resource for s in self.shapes if s.image is not None}

    def image_layer(self, create_name: str = "Imagen") -> Layer:
        """Primera capa en modo Imagen (se crea si no hay ninguna)."""
        for ly in self.sorted_layers():
            if ly.mode == LayerMode.IMAGE:
                return ly
        layer = Layer(create_name, self.unused_color(), mode=LayerMode.IMAGE, order=self.next_order(),
                      speed_mm_min=3000.0, power_max_pct=30.0)
        self.layers.append(layer)
        return layer

    def layer_for_color(self, color: str, create: bool = True) -> Layer | None:
        c = color.lower()
        for ly in self.layers:
            if ly.color.lower() == c:
                return ly
        if not create:
            return None
        layer = Layer(f"Capa {len(self.layers) + 1}", c, order=self.next_order())
        self.layers.append(layer)
        return layer

    def next_order(self) -> int:
        return max((ly.order for ly in self.layers), default=-1) + 1

    def unused_color(self) -> str:
        used = {ly.color.lower() for ly in self.layers}
        return next((c for c in LAYER_COLORS if c not in used), LAYER_COLORS[len(self.layers) % len(LAYER_COLORS)])

    def default_layer(self) -> Layer:
        if not self.layers:
            self.layers.append(Layer("Capa 1", LAYER_COLORS[0], order=0))
        return self.sorted_layers()[0]

    # --- serialización --------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"format_version": FORMAT_VERSION, "name": self.name, "width_mm": self.width_mm,
                "height_mm": self.height_mm, "metadata": dict(self.metadata),
                "layers": [ly.to_dict() for ly in self.layers], "shapes": [s.to_dict() for s in self.shapes]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Document":
        version = int(d.get("format_version", 1))
        if version > FORMAT_VERSION:
            raise ValueError(f"Formato de documento {version} más nuevo que el admitido ({FORMAT_VERSION})")
        return cls(name=d.get("name", "Sin título"), width_mm=float(d.get("width_mm", 400.0)),
                   height_mm=float(d.get("height_mm", 400.0)),
                   layers=[Layer.from_dict(x) for x in d.get("layers", [])],
                   shapes=[Shape.from_dict(x) for x in d.get("shapes", [])],
                   metadata=dict(d.get("metadata", {})))


def new_document(width: float = 400.0, height: float = 400.0, name: str = "Sin título") -> Document:
    doc = Document(name=name, width_mm=width, height_mm=height)
    doc.default_layer()
    return doc


def demo_document(width: float = 400.0, height: float = 400.0) -> Document:
    """Documento de demostración con capas Línea, Relleno e Imagen."""
    cut = Layer("Corte (demo)", LAYER_COLORS[1], id="cut", mode=LayerMode.LINE, order=2,
                speed_mm_min=600.0, power_max_pct=60.0, passes=2)
    engrave = Layer("Grabado línea (demo)", LAYER_COLORS[0], id="line", mode=LayerMode.LINE, order=0,
                    speed_mm_min=2000.0, power_max_pct=15.0)
    fill = Layer("Relleno (demo)", LAYER_COLORS[2], id="fill", mode=LayerMode.FILL, order=1,
                 speed_mm_min=3000.0, power_max_pct=20.0, line_interval_mm=0.2, fill_angle_deg=0.0)
    image = Layer("Imagen (demo)", LAYER_COLORS[3], id="image", mode=LayerMode.IMAGE, order=3,
                  speed_mm_min=3000.0, power_min_pct=0.0, power_max_pct=25.0)
    doc = Document(name="Demostración", width_mm=width, height_mm=height, layers=[engrave, fill, cut, image])
    doc.shapes = [
        Shape("line", (gp.star(110, 110, 50, 22),), name="Estrella", kind="polygon"),
        Shape("line", (gp.ellipse(250, 110, 45, 45),), name="Círculo", kind="ellipse"),
        Shape("fill", (gp.rectangle(60, 200, 100, 60),), name="Rectángulo relleno", kind="rect"),
        Shape("cut", (gp.rectangle(40, 40, 280, 250, 8),), name="Contorno de corte", kind="rect"),
    ]
    src = demo_image()
    if src is not None:
        doc.images["demo-img"] = src
        doc.shapes.append(make_image_shape("image", "demo-img", src.width, src.height, 200, 190, 100, 80,
                                           "Imagen de demostración",
                                           ImageSettings(method="atkinson", lines_per_mm=5.0)))
    return doc


def demo_image() -> Any:
    """Imagen sintética (degradado y figuras) para el documento de demostración; None sin NumPy."""
    try:
        import numpy as np

        from openlaser.image_processing.raster import RasterSource
    except ImportError:
        return None
    h, w = 160, 200
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = 255.0 * (xx / (w - 1))                                       # degradado horizontal
    r = np.hypot(xx - w * 0.3, yy - h * 0.5)
    img = np.where(r < 45, 255.0 * (r / 45.0) ** 1.5, img)             # esfera sombreada
    img = np.where((abs(xx - w * 0.72) < 22) & (abs(yy - h * 0.5) < 22), 40.0, img)  # cuadrado oscuro
    img[:6, :] = 0.0                                                   # banda superior negra
    return RasterSource("demo", np.clip(img, 0, 255).astype(np.uint8))


# Compatibilidad con la API de la Fase 1 ----------------------------------------------
def rectangle(x: float, y: float, w: float, h: float) -> list[Point]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def ellipse(cx: float, cy: float, rx: float, ry: float, segments: int = 96) -> list[Point]:
    return gp.ellipse(cx, cy, rx, ry).flatten(max(rx, ry) / segments)


def star(cx: float, cy: float, r_out: float, r_in: float, n: int = 5) -> list[Point]:
    return list(gp.star(cx, cy, r_out, r_in, n).flatten())
