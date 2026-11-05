"""Importadores de archivos vectoriales (SVG y DXF en la Fase 2).

PDF vectorial, AI (compatibles PDF) y PLT/HPGL llegan más adelante; las
imágenes ráster en la Fase 3. :func:`import_file` informa siempre de lo que
no se ha podido importar.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from openlaser.document.model import Document, Shape
from openlaser.geometry.affine import Affine
from openlaser.geometry.coords import CoordinateSystem
from openlaser.i18n import tr
from openlaser.importers.dxf import parse_dxf_file
from openlaser.importers.svg import ImportResult, parse_svg_file

VECTOR_EXTENSIONS = {".svg": "SVG", ".dxf": "DXF"}
PLANNED_EXTENSIONS: dict[str, int] = {}
# Las imágenes ráster las decodifica la interfaz (Qt) y se colocan como objetos imagen.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp")


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


class UnsupportedFormat(ValueError):
    pass


@dataclass
class ImportSummary:
    path: str
    shape_ids: list[str] = field(default_factory=list)
    layers_created: list[str] = field(default_factory=list)
    unsupported: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    outside_area: bool = False

    def text(self) -> str:
        lines = [tr("{n} objetos importados de {f}", n=len(self.shape_ids), f=os.path.basename(self.path))]
        if self.layers_created:
            lines.append(tr("Capas nuevas: {l}", l=", ".join(self.layers_created)))
        if self.unsupported:
            lines.append(tr("Elementos no importados (no compatibles):"))
            lines += [f"  • {k}: {v}" for k, v in sorted(self.unsupported.items())]
        for w in self.warnings:
            lines.append("⚠ " + w)
        if self.outside_area:
            lines.append("⚠ " + tr("Parte del diseño queda fuera del área de trabajo."))
        return "\n".join(lines)


def read_file(path: str) -> ImportResult:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".svg":
        return parse_svg_file(path)
    if ext == ".dxf":
        return parse_dxf_file(path)
    if ext in (".pdf", ".ai"):
        from openlaser.importers.pdf import parse_pdf_file
        try:
            return parse_pdf_file(path)
        except ValueError as exc:
            if ext == ".ai":
                raise ValueError(tr("Archivo AI sin compatibilidad PDF: guárdelo en Illustrator con «Crear archivo "
                                    "compatible con PDF» o expórtelo como SVG/PDF ({e})", e=exc)) from exc
            raise
    if ext in (".plt", ".hpgl"):
        raise UnsupportedFormat(tr("HPGL no está admitido; exporte el diseño como SVG, DXF o PDF."))
    if ext in PLANNED_EXTENSIONS:
        raise UnsupportedFormat(tr("El formato {e} se admitirá en una fase posterior.", e=ext))
    raise UnsupportedFormat(tr("Formato no reconocido: {e}", e=ext or "?"))


def machine_to_doc_affine(cs: CoordinateSystem) -> Affine:
    """La transformación máquina→documento es afín: se construye a partir de tres puntos."""
    o = cs.machine_to_doc((0.0, 0.0))
    ex = cs.machine_to_doc((1.0, 0.0))
    ey = cs.machine_to_doc((0.0, 1.0))
    return Affine(ex[0] - o[0], ex[1] - o[1], ey[0] - o[0], ey[1] - o[1], o[0], o[1])


def apply_import(doc: Document, result: ImportResult, cs: CoordinateSystem, group: bool = True) -> ImportSummary:
    summary = ImportSummary(path=result.source, unsupported=dict(result.unsupported), warnings=list(result.warnings))
    m = machine_to_doc_affine(cs) if result.y_up else Affine()
    gid = uuid.uuid4().hex[:10] if group and len(result.shapes) > 1 else None
    before = {ly.id for ly in doc.layers}
    for imp in result.shapes:
        layer = doc.layer_for_color(imp.color)
        assert layer is not None
        subs = tuple(sp.transformed(m) for sp in imp.subpaths if len(sp.nodes) >= 2)
        if not subs:
            continue
        shape = Shape(layer.id, subs, name=imp.name, group_id=gid, kind="import")
        doc.shapes.append(shape)
        summary.shape_ids.append(shape.id)
    summary.layers_created = [ly.name for ly in doc.layers if ly.id not in before]
    from openlaser.geometry.path import bounds_of
    b = bounds_of(sp for s in doc.shapes_by_ids(summary.shape_ids) for sp in s.subpaths)
    if b is not None and (b[0] < -1e-6 or b[1] < -1e-6 or b[2] > doc.width_mm + 1e-6 or b[3] > doc.height_mm + 1e-6):
        summary.outside_area = True
    return summary


def import_file(doc: Document, path: str, cs: CoordinateSystem) -> ImportSummary:
    return apply_import(doc, read_file(path), cs)


def file_filter() -> str:
    images = " ".join(f"*{e}" for e in IMAGE_EXTENSIONS)
    return (tr("Todos los compatibles") + f" (*.svg *.dxf *.pdf *.ai {images});;"
            + tr("Vectores (*.svg *.dxf *.pdf *.ai)") + ";;SVG (*.svg);;DXF (*.dxf);;" + tr("PDF / AI (*.pdf *.ai)")
            + ";;" + tr("Imágenes") + f" ({images});;" + tr("Todos (*)"))


def image_filter() -> str:
    return tr("Imágenes") + " (" + " ".join(f"*{e}" for e in IMAGE_EXTENSIONS) + ");;" + tr("Todos (*)")
