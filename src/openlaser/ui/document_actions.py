"""Acciones sobre el documento para la ventana principal (Fase 2).

Archivo (proyecto, importar/exportar, autoguardado, resumen), edición
(portapapeles, duplicar, bloquear…), herramientas del lienzo, organización,
booleanas, offsets, operaciones de trayectoria y gestión de capas. Todas las
modificaciones pasan por :class:`edit_document` y son deshacibles.
"""
from __future__ import annotations

import html
import json
import logging
import os
import time
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QMimeData, QPointF, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor, QImage, QTextDocument
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QSpinBox, QToolBar, QVBoxLayout,
)

from openlaser.app.config import user_dir
from openlaser.document import operations as ops
from openlaser.document.commands import edit_document
from openlaser.document.model import (
    Document, ImageRef, LayerMode, Shape, make_image_shape, make_shape, new_document,
)
from openlaser.exporters import export_dxf, export_svg
from openlaser.gcode.analyzer import format_duration
from openlaser.geometry import path as gp
from openlaser.geometry.affine import Affine
from openlaser.geometry.coords import CoordinateSystem
from openlaser.i18n import tr
from openlaser.importers import UnsupportedFormat, apply_import, file_filter, image_filter, is_image, read_file
from openlaser.persistence.atomic import atomic_write_text
from openlaser.persistence.project import (
    EXTENSION, ProjectData, ProjectError, autosave_info, autosave_path, load_project, save_project,
)
from openlaser.ui.dialogs import edit_dialogs as ed

_log = logging.getLogger(__name__)
Point2 = tuple[float, float]
CLIP_MIME = "application/x-openlaser-shapes"
TOOLS = {"draw.select": "select", "draw.nodes": "nodes", "draw.line": "line", "draw.rect": "rect",
         "draw.ellipse": "ellipse", "draw.polygon": "polygon", "draw.text": "text", "draw.measure": "measure"}


class DocumentActionsMixin:
    """Se mezcla con ``MainWindow``; usa sus atributos (doc, undo, canvas, layers…)."""

    # Los atributos los aporta MainWindow; se declaran para el analizador de tipos.
    doc: Document
    project_path: str | None
    dirty: bool

    # ============================================================== inicialización
    def _init_document_state(self) -> None:
        self.project_path = None
        self.dirty = False
        self._loading_doc = False
        self._analysis_timer = QTimer(self)  # type: ignore[arg-type]
        self._analysis_timer.setSingleShot(True)
        self._analysis_timer.setInterval(300)
        self._analysis_timer.timeout.connect(self._refresh_analysis)  # type: ignore[attr-defined]
        self._autosave_timer = QTimer(self)  # type: ignore[arg-type]
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._restart_autosave()

    def _build_tool_options(self) -> QToolBar:
        w = self  # type: Any
        tb = QToolBar(tr("Opciones de herramienta"), w)
        tb.setObjectName("toolbar.tool_options")
        opts = w.settings.tool_options
        tb.addWidget(QLabel(" " + tr("Radio de esquina") + " "))
        self.opt_radius = QDoubleSpinBox()
        self.opt_radius.setRange(0, 1000)
        self.opt_radius.setSuffix(" mm")
        self.opt_radius.setValue(float(opts.get("radius", 0.0)))
        tb.addWidget(self.opt_radius)
        tb.addSeparator()
        tb.addWidget(QLabel(" " + tr("Lados / puntas") + " "))
        self.opt_sides = QSpinBox()
        self.opt_sides.setRange(3, 100)
        self.opt_sides.setValue(int(opts.get("sides", 6)))
        tb.addWidget(self.opt_sides)
        self.opt_star = QCheckBox(tr("Estrella"))
        self.opt_star.setChecked(bool(opts.get("star", 0)))
        tb.addWidget(self.opt_star)
        tb.addWidget(QLabel(" " + tr("Radio interior") + " "))
        self.opt_inner = QDoubleSpinBox()
        self.opt_inner.setRange(0.05, 0.95)
        self.opt_inner.setSingleStep(0.05)
        self.opt_inner.setValue(float(opts.get("inner", 0.5)))
        tb.addWidget(self.opt_inner)
        tb.addSeparator()
        self.measure_label = QLabel("")
        self.measure_label.setProperty("role", "coord")
        tb.addWidget(self.measure_label)
        for sig in (self.opt_radius.valueChanged, self.opt_sides.valueChanged, self.opt_inner.valueChanged):
            sig.connect(lambda _v: self._tool_options_changed())
        self.opt_star.toggled.connect(lambda _v: self._tool_options_changed())
        self._tool_options_changed()
        return tb

    def _tool_options_changed(self) -> None:
        w = self  # type: Any
        opts = {"radius": self.opt_radius.value(), "sides": self.opt_sides.value(),
                "star": 1 if self.opt_star.isChecked() else 0, "inner": self.opt_inner.value()}
        w.settings.tool_options = opts
        w.canvas.view.tool_options = dict(opts)

    def _connect_document_signals(self) -> None:
        w = self  # type: Any
        view = w.canvas.view
        view.selection_changed.connect(lambda _ids: w._on_selection())
        view.transform_requested.connect(self._on_transform)
        view.node_edit_requested.connect(self._on_node_edit)
        view.node_insert_requested.connect(self._on_node_insert)
        view.shape_created.connect(self._on_shape_created)
        view.text_requested.connect(self._on_text_requested)
        view.measure_changed.connect(lambda text: self.measure_label.setText(text))
        view.tool_changed.connect(self._on_tool_changed)
        view.image_double_clicked.connect(lambda _sid: self._image_settings())
        props = w.properties
        props.geometry_requested.connect(self._on_properties_geometry)
        props.rotate_requested.connect(lambda deg: self._arrange(lambda d, ids: ops.rotate(d, ids, deg), tr("Rotar")))
        props.layer_requested.connect(self._assign_layer)
        props.name_requested.connect(self._rename_selection)
        w.layers.layer_action.connect(self._layer_action)
        w.layers.color_requested.connect(self._layer_color)
        group = QActionGroup(w)
        group.setExclusive(True)
        for aid in TOOLS:
            group.addAction(w.registry[aid])
        for flag, aid in (("snap_grid", "arrange.snap_grid"), ("snap_nodes", "arrange.snap_nodes"),
                          ("snap_centers", "arrange.snap_centers")):
            act = w.registry[aid]
            act.setChecked(bool(getattr(w.settings, flag)))
            setattr(view, flag, act.isChecked())
            act.toggled.connect(lambda on, f=flag: self._set_snap(f, on))
        w.registry["file.autosave"].setChecked(w.settings.autosave_minutes > 0)

    def _document_handlers(self) -> dict[str, Callable[[], Any]]:
        w = self  # type: Any
        h: dict[str, Callable[[], Any]] = {
            "file.new": self._new_document, "file.open": self._open_project, "file.save": self._save_project,
            "file.save_as": self._save_project_as, "file.autosave": self._toggle_autosave,
            "file.recover": self._recover_project, "file.import": self._import, "file.export": self._export,
            "file.doc_props": self._doc_props, "file.page": self._doc_props, "file.print_summary": self._print_summary,
            "edit.cut": self._cut, "edit.copy": self._copy, "edit.paste": lambda: self._paste(False),
            "edit.paste_in_place": lambda: self._paste(True), "edit.duplicate": self._duplicate,
            "edit.delete": self._delete, "edit.select_type": self._select_type, "edit.lock": self._toggle_lock,
            "edit.toggle_visible": self._toggle_visible, "edit.select_layer": self._select_current_layer,
            "view.fit_selection": lambda: w.canvas.view.fit_selection(),
            "arrange.group": lambda: self._arrange(lambda d, ids: ops.group(d, ids), tr("Agrupar"), min_count=2),
            "arrange.ungroup": lambda: self._arrange(lambda d, ids: ops.ungroup(d, ids), tr("Desagrupar")),
            "arrange.align": lambda: self._align("left"), "arrange.distribute": lambda: self._distribute(True),
            "arrange.distribute_h": lambda: self._distribute(True),
            "arrange.distribute_v": lambda: self._distribute(False),
            "arrange.same_width": lambda: self._arrange(lambda d, ids: ops.equalize(d, ids, True), tr("Igualar anchura"), 2),
            "arrange.same_height": lambda: self._arrange(lambda d, ids: ops.equalize(d, ids, False), tr("Igualar altura"), 2),
            "arrange.front": lambda: self._arrange(lambda d, ids: ops.reorder(d, ids, "front"), tr("Orden")),
            "arrange.back": lambda: self._arrange(lambda d, ids: ops.reorder(d, ids, "back"), tr("Orden")),
            "arrange.raise": lambda: self._arrange(lambda d, ids: ops.reorder(d, ids, "raise"), tr("Orden")),
            "arrange.lower": lambda: self._arrange(lambda d, ids: ops.reorder(d, ids, "lower"), tr("Orden")),
            "arrange.union": lambda: self._boolean("union"), "arrange.subtract": lambda: self._boolean("subtract"),
            "arrange.intersect": lambda: self._boolean("intersect"), "arrange.xor": lambda: self._boolean("xor"),
            "arrange.combine": lambda: self._arrange(lambda d, ids: ops.combine(d, ids), tr("Combinar"), 2),
            "arrange.break_apart": lambda: self._arrange(lambda d, ids: ops.break_apart(d, ids), tr("Separar")),
            "arrange.offset": self._offset, "arrange.kerf": self._kerf,
            "arrange.array": self._rect_array, "arrange.array_circular": self._circular_array,
            "arrange.copies_path": self._copies_path,
            "arrange.mirror": lambda: self._arrange(lambda d, ids: ops.flip(d, ids, True), tr("Voltear")),
            "arrange.flip_v": lambda: self._arrange(lambda d, ids: ops.flip(d, ids, False), tr("Voltear")),
            "arrange.rotate": self._rotate_dialog,
            "arrange.rotate_cw": lambda: self._arrange(lambda d, ids: ops.rotate(d, ids, 90), tr("Rotar")),
            "arrange.rotate_ccw": lambda: self._arrange(lambda d, ids: ops.rotate(d, ids, -90), tr("Rotar")),
            "arrange.move_to_page": self._center_on_page, "arrange.move_to_origin": self._move_to_origin,
            "arrange.move_to_laser": lambda: self._move_to_laser(False),
            "arrange.center_on_laser": lambda: self._move_to_laser(True),
            "arrange.laser_to_selection": self._laser_to_selection,
            "path.close": lambda: self._arrange(lambda d, ids: ops.close_paths(d, ids), tr("Cerrar trayectorias")),
            "path.join": lambda: self._arrange(lambda d, ids: ops.repair(d, ids), tr("Reparar")),
            "path.simplify": self._simplify,
            "path.dedupe": lambda: self._arrange(lambda d, ids: ops.remove_duplicate_nodes(d, ids), tr("Nodos duplicados")),
            "path.reverse": lambda: self._arrange(lambda d, ids: ops.reverse(d, ids), tr("Invertir sentido")),
            "path.delete_duplicates": self._delete_duplicate_shapes,
            "layers.add": lambda: self._layer_action("add", ""), "layers.delete": lambda: self._layer_action("delete", ""),
            "layers.settings": lambda: self._layer_action("settings", ""),
            "layers.copy_settings": lambda: self._layer_action("copy", ""),
            "layers.save_preset": lambda: self._layer_action("save_preset", ""),
            "layers.apply_preset": lambda: self._layer_action("apply_preset", ""),
            "layers.assign": lambda: self._layer_action("assign", ""),
            "draw.image": lambda: self._insert_image(), "draw.vectorize": self._vectorize,
            "image.settings": self._image_settings, "path.round_corners": self._round_corners,
            "path.tabs": self._tabs, "laser.optimize": self._optimize_report, "laser.simulate": self._simulate,
        }
        for mode in ("left", "right", "top", "bottom", "hcenter", "vcenter", "center"):
            h[f"arrange.align_{mode}"] = lambda m=mode: self._align(m)
        for aid, tool in TOOLS.items():
            h[aid] = lambda t=tool: w.canvas.view.set_tool(t)
        return h

    # ================================================================== utilidades
    def _cs(self) -> CoordinateSystem:
        return CoordinateSystem.from_profile(self.profile)  # type: ignore[attr-defined]

    def _selection(self, editable: bool = True, quiet: bool = False) -> list[str]:
        w = self  # type: Any
        ids = w.canvas.view.selected_shape_ids()
        if editable:
            ids = ops.editable_ids(self.doc, ids)
        if not ids and not quiet:
            w._flash(tr("Seleccione algún objeto"), "warn")
        return ids

    def _edit(self, text: str, fn: Callable[[], Any]) -> Any:
        w = self  # type: Any
        result: Any = None
        with edit_document(self.doc, w.undo, text):
            result = fn()
        return result

    def _arrange(self, fn: Callable[[Document, list[str]], Any], text: str, min_count: int = 1) -> Any:
        ids = self._selection()
        if len(ids) < min_count:
            if ids:
                self._flash_warn(tr("Seleccione al menos {n} objetos", n=min_count))
            return None
        result = self._edit(text, lambda: fn(self.doc, ids))
        new = [result] if isinstance(result, str) else (result if isinstance(result, list) else [])
        shown = [i for i in new if isinstance(i, str) and self.doc.shape(i) is not None]
        if not shown:  # p. ej. agrupar devuelve el id del grupo: se mantiene la selección
            shown = [i for i in ids if self.doc.shape(i) is not None]
        self.canvas.view.set_selection(shown)  # type: ignore[attr-defined]
        return result

    def _flash_warn(self, text: str) -> None:
        self._flash(text, "warn")  # type: ignore[attr-defined]

    def _current_layer_id(self) -> str:
        w = self  # type: Any
        lid = w.layers.current_layer_id()
        if lid and self.doc.layer(lid):
            return lid
        return self.doc.default_layer().id

    def _set_snap(self, flag: str, on: bool) -> None:
        w = self  # type: Any
        setattr(w.settings, flag, on)
        setattr(w.canvas.view, flag, on)

    def _on_tool_changed(self, tool: str) -> None:
        w = self  # type: Any
        for aid, t in TOOLS.items():
            if t == tool:
                w.registry[aid].setChecked(True)
        self.measure_label.setText("")

    # ============================================================ estado del documento
    def _set_document(self, doc: Document, path: str | None) -> None:
        w = self  # type: Any
        self._loading_doc = True
        try:
            self.doc = doc
            w._doc_rev += 1  # documento nuevo: el trabajo calculado ya no vale
            doc.width_mm, doc.height_mm = w.profile.width_mm, w.profile.height_mm
            w.undo.clear()
            self.project_path = path
            w.canvas.view.set_selection([])
            w.canvas.view.set_document(doc)
            w.layers.set_document(doc)
            w._on_selection()
        finally:
            self._loading_doc = False
        self.dirty = False
        self._update_title()
        w._refresh_analysis()

    def _mark_dirty(self) -> None:
        if self._loading_doc:
            return
        if not self.dirty:
            self.dirty = True
            self._update_title()
        self._analysis_timer.start()

    def _update_title(self) -> None:
        w = self  # type: Any
        from openlaser.app.config import app_name
        name = os.path.basename(self.project_path) if self.project_path else (self.doc.name or tr("Sin título"))
        w.setWindowTitle(f"{'• ' if self.dirty else ''}{name} — {app_name()} — {w.profile.name}")

    def _maybe_save(self) -> bool:
        """True si se puede continuar (guardado o descartado)."""
        if not self.dirty or not getattr(self, "interactive", True):
            return True
        w = self  # type: Any
        res = QMessageBox.question(w, tr("Cambios sin guardar"), tr("¿Guardar los cambios del documento?"),
                                   QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                   | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save)
        if res == QMessageBox.StandardButton.Save:
            return self._save_project()
        return res == QMessageBox.StandardButton.Discard

    # ===================================================================== archivo
    def _new_document(self) -> None:
        if not self._maybe_save():
            return
        w = self  # type: Any
        self._set_document(new_document(w.profile.width_mm, w.profile.height_mm, tr("Sin título")), None)

    def _open_project(self, path: str | None = None) -> None:
        w = self  # type: Any
        if not self._maybe_save():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                w, tr("Abrir proyecto"), "", tr("Proyectos (*{e} *.json);;Todos (*)", e=EXTENSION))
        if not path:
            return
        try:
            data = load_project(path)
        except (ProjectError, OSError) as exc:
            QMessageBox.warning(w, tr("Abrir proyecto"), str(exc))
            return
        self._set_document(data.document, path if path.lower().endswith(EXTENSION) else None)
        prof = data.device_profile
        if prof and prof.get("id") and prof.get("id") != w.profile.id:
            w._flash(tr("El proyecto se creó con el perfil «{n}»; se mantiene el perfil activo.", n=prof.get("name", "?")))
        self._add_recent(path)
        w._flash(tr("Proyecto abierto: {p}", p=os.path.basename(path)))

    def _project_data(self) -> ProjectData:
        w = self  # type: Any
        self.doc.metadata["saved_by"] = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        return ProjectData(self.doc, w.profile.to_dict(),
                           {"window_state": bytes(w.saveState().toHex().data()).decode()})

    def _save_project(self) -> bool:
        if not self.project_path:
            return self._save_project_as()
        w = self  # type: Any
        try:
            save_project(self.project_path, self._project_data())
        except OSError as exc:
            QMessageBox.warning(w, tr("Guardar"), str(exc))
            return False
        self.dirty = False
        self._update_title()
        self._add_recent(self.project_path)
        w._flash(tr("Guardado: {p}", p=self.project_path))
        return True

    def _save_project_as(self) -> bool:
        w = self  # type: Any
        suggested = self.project_path or f"{self.doc.name or 'proyecto'}{EXTENSION}"
        path, _ = QFileDialog.getSaveFileName(w, tr("Guardar proyecto como"), suggested,
                                              tr("Proyecto OpenLaser (*{e})", e=EXTENSION))
        if not path:
            return False
        if not path.lower().endswith(EXTENSION):
            path += EXTENSION
        self.project_path = path
        if self.doc.name in ("", "Sin título", tr("Sin título")):
            self.doc.name = Path(path).stem
        return self._save_project()

    def _add_recent(self, path: str) -> None:
        w = self  # type: Any
        rec = [path] + [p for p in w.settings.recent_projects if p != path]
        w.settings.recent_projects = rec[:10]

    def _fill_recent_menu(self) -> None:
        w = self  # type: Any
        menu = w.menu_recent
        menu.clear()
        for p in w.settings.recent_projects:
            act = menu.addAction(os.path.basename(p))
            act.setToolTip(p)
            act.setEnabled(os.path.exists(p))
            act.triggered.connect(lambda _c=False, path=p: self._open_project(path))
        if not w.settings.recent_projects:
            act = menu.addAction(tr("(vacío)"))
            act.setEnabled(False)

    # --- autoguardado ---
    def _restart_autosave(self) -> None:
        w = self  # type: Any
        minutes = int(w.settings.autosave_minutes)
        if minutes > 0:
            self._autosave_timer.start(minutes * 60_000)
        else:
            self._autosave_timer.stop()

    def _toggle_autosave(self) -> None:
        w = self  # type: Any
        on = w.registry["file.autosave"].isChecked()
        w.settings.autosave_minutes = max(1, w.settings.autosave_minutes or 3) if on else 0
        self._restart_autosave()

    def _autosave_path(self) -> Path:
        return autosave_path(user_dir())

    def _autosave_tick(self) -> None:
        if not self.dirty or not getattr(self, "interactive", True):
            return
        try:
            data = self._project_data()
            data.manifest["source_path"] = self.project_path or ""
            save_project(self._autosave_path(), data)
        except OSError:
            _log.exception("Autoguardado fallido")

    def _discard_autosave(self) -> None:
        p = self._autosave_path()
        for f in (p, p.with_suffix(p.suffix + ".bak")):
            try:
                f.unlink()
            except OSError:
                pass

    def _check_autosave_on_start(self) -> None:
        w = self  # type: Any
        if not getattr(self, "interactive", True):
            return
        info = autosave_info(self._autosave_path())
        if info is None:
            return
        when = info.get("modified", "?")
        res = QMessageBox.question(w, tr("Recuperar proyecto"),
                                   tr("Hay un autoguardado del {t} que no se cerró correctamente. ¿Recuperarlo?", t=when))
        if res == QMessageBox.StandardButton.Yes:
            self._load_recovery(info)
        else:
            self._discard_autosave()

    def _load_recovery(self, info: dict) -> None:
        w = self  # type: Any
        try:
            data = load_project(info["path"])
        except (ProjectError, OSError) as exc:
            QMessageBox.warning(w, tr("Recuperar proyecto"), str(exc))
            return
        self._set_document(data.document, data.manifest.get("source_path") or None)
        self.dirty = True
        self._update_title()
        w._flash(tr("Proyecto recuperado del autoguardado. Guárdelo para conservarlo."), "warn")

    def _recover_project(self) -> None:
        w = self  # type: Any
        info = autosave_info(self._autosave_path())
        if info is not None:
            if self._maybe_save():
                self._load_recovery(info)
            return
        path, _ = QFileDialog.getOpenFileName(w, tr("Recuperar desde copia"), "",
                                              tr("Copias de seguridad (*.bak);;Todos (*)"))
        if path:
            try:
                data = load_project(path)
            except (ProjectError, OSError) as exc:
                QMessageBox.warning(w, tr("Recuperar proyecto"), str(exc))
                return
            self._set_document(data.document, None)
            self.dirty = True
            self._update_title()

    # --- importar / exportar ---
    def _import(self) -> None:
        w = self  # type: Any
        paths, _ = QFileDialog.getOpenFileNames(w, tr("Importar"), "", file_filter())
        all_ids: list[str] = []
        reports: list[str] = []
        for path in paths:
            if is_image(path):
                sid = self._insert_image(path, select=False)
                if sid:
                    all_ids.append(sid)
                    reports.append(tr("Imagen importada: {f}", f=os.path.basename(path)))
                continue
            try:
                result = read_file(path)
            except (UnsupportedFormat, ValueError, OSError) as exc:
                reports.append(f"{os.path.basename(path)}: {exc}")
                continue
            summary = self._edit(tr("Importar"), lambda r=result: apply_import(self.doc, r, self._cs()))
            all_ids += summary.shape_ids
            reports.append(summary.text())
        if all_ids:
            w.canvas.view.set_selection(all_ids)
        if reports:
            QMessageBox.information(w, tr("Importación"), "\n\n".join(reports))

    def _export(self) -> None:
        w = self  # type: Any
        sel = set(w.canvas.view.selected_shape_ids())
        path, flt = QFileDialog.getSaveFileName(w, tr("Exportar"), f"{self.doc.name or 'diseño'}.svg",
                                                tr("SVG (*.svg);;DXF R12 (*.dxf)"))
        if not path:
            return
        only = sel or None
        try:
            if path.lower().endswith(".dxf") or "DXF" in flt:
                if not path.lower().endswith(".dxf"):
                    path += ".dxf"
                atomic_write_text(path, export_dxf(self.doc, self._cs(), only))
            else:
                if not path.lower().endswith(".svg"):
                    path += ".svg"
                atomic_write_text(path, export_svg(self.doc, only))
        except OSError as exc:
            QMessageBox.warning(w, tr("Exportar"), str(exc))
            return
        w._flash(tr("Exportado {n} objetos a {p}", n=len(only) if only else len(self.doc.shapes), p=path))

    def _doc_props(self) -> None:
        w = self  # type: Any
        dlg = ed.DocumentPropertiesDialog(self.doc.name, str(self.doc.metadata.get("notes", "")),
                                          self.doc.width_mm, self.doc.height_mm, w)
        if dlg.exec():
            self.doc.name = dlg.name.text().strip() or self.doc.name
            self.doc.metadata["notes"] = dlg.notes.toPlainText()
            self._mark_dirty()
            self._update_title()

    def _print_summary(self) -> None:
        w = self  # type: Any
        from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog
        job = w._prepare_job(quiet=True)
        rows = "".join(
            f"<tr><td style='color:{ly.color}'>■</td><td>{html.escape(ly.name)}</td><td>{tr(ly.mode.value)}</td>"
            f"<td>{ly.speed_mm_min:g}</td><td>{ly.power_min_pct:g}–{ly.power_max_pct:g} %</td><td>{ly.passes}</td>"
            f"<td>{'✔' if ly.output else '—'}</td><td>{sum(1 for s in self.doc.shapes if s.layer_id == ly.id)}</td></tr>"
            for ly in self.doc.sorted_layers())
        b = self.doc.bounds(False)
        size = f"{b[2] - b[0]:.1f} × {b[3] - b[1]:.1f} mm" if b else "—"
        job_html = ""
        if job is not None:
            a = job.analysis
            job_html = (f"<p>{tr('Líneas de G-code')}: {a.line_count} · {tr('Tiempo estimado')}: "
                        f"{format_duration(a.estimated_seconds)} · {tr('Corte')}: {a.cut_distance:.0f} mm · "
                        f"{tr('Desplazamiento')}: {a.travel_distance:.0f} mm</p>")
        doc_html = (f"<h2>{html.escape(self.doc.name)}</h2><p>{time.strftime('%Y-%m-%d %H:%M')} · "
                    f"{html.escape(w.profile.name)} · {len(self.doc.shapes)} {tr('objetos')} · {size}</p>{job_html}"
                    f"<table border='1' cellspacing='0' cellpadding='4'><tr><th></th><th>{tr('Capa')}</th>"
                    f"<th>{tr('Modo')}</th><th>mm/min</th><th>{tr('Potencia')}</th><th>{tr('Pasadas')}</th>"
                    f"<th>{tr('Salida')}</th><th>{tr('Objetos')}</th></tr>{rows}</table>"
                    f"<p><i>{tr('Valores a validar en la máquina y el material reales.')}</i></p>")
        text = QTextDocument()
        text.setHtml(doc_html)
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintPreviewDialog(printer, w)
        dlg.paintRequested.connect(lambda p: text.print_(p))
        dlg.exec()

    # ===================================================================== edición
    def _copy(self) -> None:
        ids = self._selection(editable=False)
        if not ids:
            return
        shapes = self.doc.shapes_by_ids(ids)
        layer_ids = {s.layer_id for s in shapes}
        payload = {"shapes": [s.to_dict() for s in shapes],
                   "layers": [ly.to_dict() for ly in self.doc.layers if ly.id in layer_ids]}
        mime = QMimeData()
        mime.setData(CLIP_MIME, json.dumps(payload).encode("utf-8"))
        mime.setText(export_svg(self.doc, set(ids)))
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setMimeData(mime)

    def _cut(self) -> None:
        ids = self._selection()
        if ids:
            self._copy()
            self._edit(tr("Cortar"), lambda: ops.delete(self.doc, ids))

    def _paste(self, in_place: bool) -> None:
        w = self  # type: Any
        cb = QApplication.clipboard()
        mime = cb.mimeData() if cb is not None else None
        if mime is None:
            return
        if mime.hasFormat(CLIP_MIME):
            payload = json.loads(bytes(mime.data(CLIP_MIME).data()).decode("utf-8"))
            from openlaser.document.model import Layer
            offset = Affine() if in_place else Affine.translate(10, 10)

            def do() -> list[str]:
                layer_map: dict[str, str] = {}
                for ld in payload.get("layers", []):
                    src = Layer.from_dict(ld)
                    existing = self.doc.layer(src.id) or self.doc.layer_for_color(src.color, create=False)
                    if existing is None:
                        src.order = self.doc.next_order()
                        self.doc.layers.append(src)
                        existing = src
                    layer_map[ld["id"]] = existing.id
                groups: dict[str, str] = {}
                new: list[Shape] = []
                for sd in payload.get("shapes", []):
                    s = Shape.from_dict(sd)
                    gid = groups.setdefault(s.group_id, ops.new_id()) if s.group_id else None
                    new.append(s.copy(layer_id=layer_map.get(s.layer_id, self._current_layer_id()),
                                      group_id=gid).transformed(offset))
                return ops.add_shapes(self.doc, new)

            ids = self._edit(tr("Pegar"), do)
            w.canvas.view.set_selection(ids)
            return
        text = mime.text() if mime.hasText() else ""
        if text.lstrip().startswith("<") and "<svg" in text[:2000]:
            from openlaser.importers.svg import parse_svg
            try:
                result = parse_svg(text, source=tr("portapapeles"))
            except ValueError as exc:
                w._flash(str(exc), "warn")
                return
            summary = self._edit(tr("Pegar"), lambda: apply_import(self.doc, result, self._cs()))
            w.canvas.view.set_selection(summary.shape_ids)

    def _duplicate(self) -> None:
        self._arrange(lambda d, ids: ops.duplicate(d, ids, 10, 10), tr("Duplicar"))

    def _delete(self) -> None:
        w = self  # type: Any
        view = w.canvas.view
        if view.tool == "nodes" and view._active_node is not None:
            sid, si, ni = view._active_node
            view._active_node = None
            self._edit(tr("Eliminar nodo"), lambda: ops.edit_node(self.doc, sid, si, ni, None))
            return
        ids = self._selection()
        if ids:
            self._edit(tr("Eliminar"), lambda: ops.delete(self.doc, ids))

    def _select_type(self) -> None:
        w = self  # type: Any
        kinds = sorted({s.kind for s in self.doc.shapes})
        if not kinds:
            return
        kind, ok = QInputDialog.getItem(w, tr("Seleccionar por tipo"), tr("Tipo de objeto"), kinds, 0, False)
        if ok:
            w.canvas.view.select_kind(self.doc, kind)

    def _select_current_layer(self) -> None:
        w = self  # type: Any
        w.canvas.view.select_layer(self.doc, self._current_layer_id())

    def _toggle_lock(self) -> None:
        w = self  # type: Any
        ids = w.canvas.view.selected_shape_ids()
        if ids:
            self._edit(tr("Bloquear"), lambda: ops.set_flags(self.doc, ids, locked=True))
            w.canvas.view.set_selection([])
            w._flash(tr("Objetos bloqueados. Para desbloquear todos, vuelva a usar la orden sin selección."))
        else:
            locked = [s.id for s in self.doc.shapes if s.locked]
            if locked:
                self._edit(tr("Desbloquear"), lambda: ops.set_flags(self.doc, locked, locked=False))

    def _toggle_visible(self) -> None:
        w = self  # type: Any
        ids = w.canvas.view.selected_shape_ids()
        if ids:
            self._edit(tr("Ocultar"), lambda: ops.set_flags(self.doc, ids, visible=False))
        else:
            hidden = [s.id for s in self.doc.shapes if not s.visible]
            if hidden:
                self._edit(tr("Mostrar"), lambda: ops.set_flags(self.doc, hidden, visible=True))

    # ============================================================= lienzo → documento
    def _on_shape_created(self, subpaths: list[gp.SubPath], kind: str, name: str) -> None:
        w = self  # type: Any
        shape = make_shape(self._current_layer_id(), subpaths, name, kind)
        self._edit(name, lambda: ops.add_shapes(self.doc, [shape]))
        w.canvas.view.set_selection([shape.id])

    def _on_transform(self, ids: list[str], m: Affine) -> None:
        ids = ops.editable_ids(self.doc, ids)
        if ids:
            self._edit(tr("Transformar"), lambda: ops.transform(self.doc, ids, m))

    def _on_node_edit(self, sid: str, si: int, ni: int, node: gp.Node | None) -> None:
        self._edit(tr("Editar nodo"), lambda: ops.edit_node(self.doc, sid, si, ni, node))

    def _on_node_insert(self, sid: str, si: int, seg: int, t: float) -> None:
        def do() -> None:
            shape = self.doc.shape(sid)
            if shape is None:
                return
            subs = list(shape.subpaths)
            subs[si] = gp.insert_node(subs[si], seg, t)
            self.doc.shapes = [s.with_subpaths(subs) if s.id == sid else s for s in self.doc.shapes]
        self._edit(tr("Insertar nodo"), do)

    def _on_text_requested(self, pos: QPointF) -> None:
        w = self  # type: Any
        dlg = ed.TextDialog(w)
        if not dlg.exec():
            return
        from openlaser.canvas.qt_geometry import text_to_subpaths
        text = dlg.text.toPlainText()
        subs = text_to_subpaths(text, dlg.font_box.currentFont().family(), dlg.size_mm.value(),
                                dlg.bold.isChecked(), dlg.italic.isChecked(), (pos.x(), pos.y()))
        if not subs:
            w._flash(tr("El texto no produjo ninguna curva"), "warn")
            return
        self._on_shape_created(subs, "text", text.splitlines()[0][:40] if text else tr("Texto"))

    # ================================================================= organizar
    def _align(self, mode: str) -> None:
        ids = self._selection()
        if not ids:
            return
        reference = None
        if len(ops._units(self.doc, ids)) < 2:  # un solo objeto/grupo: se alinea con el área de trabajo
            reference = (0.0, 0.0, self.doc.width_mm, self.doc.height_mm)
        self._edit(tr("Alinear"), lambda: ops.align(self.doc, ids, mode, reference))

    def _distribute(self, horizontal: bool) -> None:
        self._arrange(lambda d, ids: ops.distribute(d, ids, horizontal), tr("Distribuir"), 3)

    def _rotate_dialog(self) -> None:
        w = self  # type: Any
        if not self._selection():
            return
        dlg = ed.RotateDialog(w)
        if dlg.exec():
            deg = -dlg.angle.value()  # el documento tiene Y hacia abajo: positivo = antihorario en pantalla
            self._arrange(lambda d, ids: ops.rotate(d, ids, deg), tr("Rotar"))

    def _rect_array(self) -> None:
        w = self  # type: Any
        if not self._selection():
            return
        dlg = ed.RectArrayDialog(w)
        if dlg.exec():
            self._arrange(lambda d, ids: ops.rect_array(d, ids, dlg.cols.value(), dlg.rows.value(), dlg.dx.value(),
                                                        dlg.dy.value()), tr("Matriz rectangular"))

    def _circular_array(self) -> None:
        w = self  # type: Any
        ids = self._selection()
        if not ids:
            return
        b = ops.selection_bounds(self.doc, ids)
        assert b is not None
        dlg = ed.CircularArrayDialog(((b[0] + b[2]) / 2, b[3] + (b[3] - b[1])), w)
        if dlg.exec():
            self._arrange(lambda d, sel: ops.circular_array(d, sel, dlg.count.value(), (dlg.cx.value(), dlg.cy.value()),
                                                            dlg.total.value(), dlg.rotate.isChecked()),
                          tr("Matriz circular"))

    def _copies_path(self) -> None:
        w = self  # type: Any
        ids = self._selection()
        if len(ids) < 2:
            self._flash_warn(tr("Seleccione los objetos y, por último, la trayectoria guía"))
            return
        guide = ids[-1]
        items = ids[:-1]
        dlg = ed.CopiesPathDialog(w)
        if dlg.exec():
            created = self._edit(tr("Copias sobre trayectoria"),
                                 lambda: ops.copies_along_path(self.doc, items, guide, dlg.count.value(),
                                                               dlg.rotate.isChecked()))
            w.canvas.view.set_selection(created)

    def _center_on_page(self) -> None:
        self._arrange(lambda d, ids: ops.move_to_point(d, ids, (d.width_mm / 2, d.height_mm / 2)),
                      tr("Centrar en el área"))

    def _doc_delta_for_machine(self, mdx: float, mdy: float) -> tuple[float, float]:
        cs = self._cs()
        a = cs.machine_to_doc((0.0, 0.0))
        b = cs.machine_to_doc((mdx, mdy))
        return (b[0] - a[0], b[1] - a[1])

    def _move_to_origin(self) -> None:
        ids = self._selection()
        b = ops.selection_bounds(self.doc, ids) if ids else None
        if b is None:
            return
        cs = self._cs()
        corners = [cs.doc_to_machine(p) for p in ((b[0], b[1]), (b[2], b[3]))]
        mx, my = min(c[0] for c in corners), min(c[1] for c in corners)
        dx, dy = self._doc_delta_for_machine(-mx, -my)
        self._edit(tr("Mover al origen"), lambda: ops.move(self.doc, ids, dx, dy))

    def _move_to_laser(self, center: bool) -> None:
        w = self  # type: Any
        ids = self._selection()
        b = ops.selection_bounds(self.doc, ids) if ids else None
        if b is None:
            return
        cs = self._cs()
        head = cs.machine_to_doc((w.status.mpos[0], w.status.mpos[1]))
        if center:
            target_ref = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        else:
            corners = [(b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3])]
            target_ref = min(corners, key=lambda p: cs.doc_to_machine(p))  # esquina más cercana al origen
        dx, dy = head[0] - target_ref[0], head[1] - target_ref[1]
        self._edit(tr("Mover a la posición del láser"), lambda: ops.move(self.doc, ids, dx, dy))

    def _laser_to_selection(self) -> None:
        w = self  # type: Any
        ids = self._selection(editable=False)
        b = ops.selection_bounds(self.doc, ids) if ids else None
        if b is None:
            return
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        wx, wy = self._cs().doc_to_work((cx, cy), (w.status.wco[0], w.status.wco[1]))
        w.controller.go_to(wx, wy)

    # ======================================================== booleanas y offsets
    def _boolean(self, op: str) -> None:
        ids = ops.vector_ids(self.doc, self._selection())
        if len(ids) < 2:
            self._flash_warn(tr("Seleccione al menos {n} objetos", n=2))
            return
        from openlaser.canvas.qt_geometry import boolean
        shapes = self.doc.shapes_by_ids(ids)  # en orden de apilado: el primero es el de abajo
        base = shapes[0]
        result = list(base.subpaths)
        if op == "subtract":
            others = [sp for s in shapes[1:] for sp in s.subpaths]
            result = boolean("subtract", result, others)
        else:
            for s in shapes[1:]:
                result = boolean(op, result, s.subpaths)
        names = {"union": tr("Unir"), "subtract": tr("Restar"), "intersect": tr("Intersección"), "xor": tr("Exclusión")}
        if not result:
            self._flash_warn(tr("El resultado de la operación está vacío"))
            return
        new = base.copy(subpaths=tuple(result), kind="path", group_id=None, name=names[op])

        def do() -> None:
            ops.delete(self.doc, ids)
            self.doc.shapes.append(new)
        self._edit(names[op], do)
        self.canvas.view.set_selection([new.id])  # type: ignore[attr-defined]

    def _offset(self) -> None:
        w = self  # type: Any
        ids = ops.vector_ids(self.doc, self._selection())
        if not ids:
            return
        dlg = ed.OffsetDialog(1.0, None, w)
        if dlg.exec():
            self._apply_offset(ids, dlg.value(), dlg.round.isChecked(), dlg.keep.isChecked(), tr("Offset"))

    def _kerf(self) -> None:
        ids = ops.vector_ids(self.doc, self._selection())
        if not ids:
            return
        shapes = self.doc.shapes_by_ids(ids)
        kerfs = {self.doc.layer(s.layer_id).kerf_mm for s in shapes if self.doc.layer(s.layer_id)}  # type: ignore[union-attr]
        if not kerfs or max(kerfs) <= 0:
            self._flash_warn(tr("Defina el kerf en los ajustes de la capa (Capas → Ajustes de capa…)"))
            return
        from openlaser.canvas.qt_geometry import offset

        def do() -> list[str]:
            created: list[str] = []
            for s in shapes:
                layer = self.doc.layer(s.layer_id)
                k = layer.kerf_mm if layer else 0.0
                if k <= 0:
                    continue
                subs = offset(s.subpaths, k / 2, True)
                if subs:
                    self.doc.shapes = [s.with_subpaths(subs) if o.id == s.id else o for o in self.doc.shapes]
                    created.append(s.id)
            return created
        self._edit(tr("Compensación de kerf"), do)

    def _apply_offset(self, ids: list[str], dist: float, round_joins: bool, keep: bool, text: str) -> None:
        from openlaser.canvas.qt_geometry import offset
        shapes = self.doc.shapes_by_ids(ids)

        def do() -> list[str]:
            created: list[str] = []
            for s in shapes:
                subs = offset(s.subpaths, dist, round_joins)
                if not subs:
                    continue
                if keep:
                    new = s.copy(subpaths=tuple(subs), group_id=None, kind="path", name=f"{s.name} offset".strip())
                    self.doc.shapes.append(new)
                    created.append(new.id)
                else:
                    self.doc.shapes = [s.with_subpaths(subs) if o.id == s.id else o for o in self.doc.shapes]
                    created.append(s.id)
            return created
        created = self._edit(text, do)
        if created:
            self.canvas.view.set_selection(created)  # type: ignore[attr-defined]
        else:
            self._flash_warn(tr("El offset no produjo geometría (distancia demasiado grande hacia dentro)"))

    def _simplify(self) -> None:
        w = self  # type: Any
        if not self._selection():
            return
        tol, ok = QInputDialog.getDouble(w, tr("Simplificar trayectoria"), tr("Tolerancia (mm)"), 0.05, 0.001, 10, 3)
        if ok:
            self._arrange(lambda d, ids: ops.simplify(d, ids, tol), tr("Simplificar"))

    def _delete_duplicate_shapes(self) -> None:
        def key(s: Shape) -> tuple:
            return (s.layer_id, tuple(tuple((round(nd.x, 3), round(nd.y, 3)) for nd in sp.nodes) for sp in s.subpaths))
        seen: set[tuple] = set()
        dups: list[str] = []
        for s in self.doc.shapes:
            k = key(s)
            if k in seen:
                dups.append(s.id)
            else:
                seen.add(k)
        if dups:
            self._edit(tr("Eliminar duplicados"), lambda: ops.delete(self.doc, dups))
        self._flash(tr("{n} objetos duplicados eliminados", n=len(dups)))  # type: ignore[attr-defined]

    # ================================================================ propiedades
    def _on_properties_geometry(self, req: dict) -> None:
        ids = self._selection()
        if not ids:
            return
        cur = dict(req["current"])
        key, value = req["key"], float(req["value"])
        new = dict(cur)
        new[key] = value
        if req.get("keep") and key in ("w", "h") and cur["w"] > 1e-9 and cur["h"] > 1e-9:
            ratio = cur["h"] / cur["w"]
            if key == "w":
                new["h"] = value * ratio
            else:
                new["w"] = value / ratio
        new["w"] = max(new["w"], 1e-3)
        new["h"] = max(new["h"], 1e-3)
        cs = self._cs()
        p1 = cs.machine_to_doc((new["x"], new["y"]))
        p2 = cs.machine_to_doc((new["x"] + new["w"], new["y"] + new["h"]))
        x0, y0 = min(p1[0], p2[0]), min(p1[1], p2[1])
        self._edit(tr("Posición y tamaño"),
                   lambda: ops.set_bounds(self.doc, ids, x=x0, y=y0, w=new["w"], h=new["h"]))

    def _assign_layer(self, layer_id: str) -> None:
        ids = self._selection()
        if ids and self.doc.layer(layer_id):
            self._edit(tr("Cambiar de capa"), lambda: ops.set_layer(self.doc, ids, layer_id))

    def _rename_selection(self, name: str) -> None:
        ids = self._selection(quiet=True)
        if len(ids) == 1 and name and self.doc.shape(ids[0]) and self.doc.shape(ids[0]).name != name:  # type: ignore[union-attr]
            self._edit(tr("Renombrar"), lambda: ops.rename(self.doc, ids[0], name))

    # ===================================================================== capas
    def _layer_action(self, action: str, layer_id: str) -> None:
        w = self  # type: Any
        lid = layer_id or self._current_layer_id()
        layer = self.doc.layer(lid)
        if action == "add":
            new = self._edit(tr("Nueva capa"), lambda: ops.add_layer(self.doc))
            w.layers.refresh()
            w._flash(tr("Capa creada: {n}", n=new.name))
            return
        if layer is None:
            return
        if action == "delete":
            if len(self.doc.layers) <= 1:
                self._flash_warn(tr("El documento necesita al menos una capa"))
                return
            target = next(ly for ly in self.doc.sorted_layers() if ly.id != lid)
            count = sum(1 for s in self.doc.shapes if s.layer_id == lid)
            move_to: str | None = target.id
            if count:
                res = QMessageBox.question(
                    w, tr("Eliminar capa"),
                    tr("La capa «{n}» tiene {c} objetos. ¿Moverlos a «{t}»? (No = eliminarlos)", n=layer.name, c=count,
                       t=target.name),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
                if res == QMessageBox.StandardButton.Cancel:
                    return
                move_to = target.id if res == QMessageBox.StandardButton.Yes else None
            self._edit(tr("Eliminar capa"), lambda: ops.delete_layer(self.doc, lid, move_to))
        elif action in ("up", "down"):
            self._edit(tr("Orden de capas"), lambda: ops.move_layer(self.doc, lid, -1 if action == "up" else 1))
        elif action == "settings":
            dlg = ed.LayerSettingsDialog(layer, True, w)
            if dlg.exec():
                self._edit(tr("Ajustes de capa"), lambda: dlg.apply_to(layer))
        elif action == "assign":
            self._assign_layer(lid)
        elif action == "copy":
            targets = self._choose_layers(tr("Copiar ajustes de «{n}» a…", n=layer.name), exclude=lid)
            if targets:
                self._edit(tr("Copiar ajustes de capa"), lambda: ops.copy_layer_settings(self.doc, lid, targets))
        elif action == "save_preset":
            name, ok = QInputDialog.getText(w, tr("Guardar preajuste"), tr("Nombre del preajuste"), text=layer.name)
            if ok and name.strip():
                w.settings.layer_presets[name.strip()] = layer.settings_dict()
                w._save_settings()
                w._flash(tr("Preajuste guardado: {n}", n=name.strip()))
        elif action == "apply_preset":
            names = sorted(w.settings.layer_presets)
            if not names:
                self._flash_warn(tr("No hay preajustes guardados"))
                return
            name, ok = QInputDialog.getItem(w, tr("Aplicar preajuste"), tr("Preajuste"), names, 0, False)
            if ok:
                preset = w.settings.layer_presets[name]
                self._edit(tr("Aplicar preajuste"), lambda: layer.apply_settings(preset))
        w.layers.refresh()

    def _choose_layers(self, title: str, exclude: str) -> list[str]:
        w = self  # type: Any
        dlg = QDialog(w)
        dlg.setWindowTitle(title)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        for ly in self.doc.sorted_layers():
            if ly.id == exclude:
                continue
            item = QListWidgetItem(ly.name)
            item.setData(256, ly.id)
            from PySide6.QtCore import Qt
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            lst.addItem(item)
        lay.addWidget(lst)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if not dlg.exec():
            return []
        from PySide6.QtCore import Qt
        out = []
        for i in range(lst.count()):
            it = lst.item(i)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                out.append(str(it.data(256)))
        return out

    def _layer_color(self, layer_id: str) -> None:
        w = self  # type: Any
        layer = self.doc.layer(layer_id)
        if layer is None:
            return
        color = QColorDialog.getColor(QColor(layer.color), w, tr("Color de la capa"))
        if color.isValid():
            hexc = color.name()
            if any(ly.color.lower() == hexc.lower() and ly.id != layer_id for ly in self.doc.layers):
                self._flash_warn(tr("Ese color ya lo usa otra capa"))
                return
            self._edit(tr("Color de capa"), lambda: setattr(layer, "color", hexc))
            w.layers.refresh()

    # ============================================================ imágenes (Fase 3)
    def _layer_with_mode(self, mode: LayerMode, name: str) -> str:
        """Id de la primera capa con ``mode`` (se crea si no existe; llamar dentro de una edición)."""
        for ly in self.doc.sorted_layers():
            if ly.mode == mode:
                return ly.id
        layer = ops.add_layer(self.doc, name)
        layer.mode = mode
        return layer.id

    def _load_image_file(self, path: str | None) -> tuple[str, Any] | None:
        w = self  # type: Any
        if not path:
            path, _ = QFileDialog.getOpenFileName(w, tr("Insertar imagen"), "", image_filter())
        if not path:
            return None
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(w, tr("Insertar imagen"), tr("No se pudo leer la imagen: {p}", p=path))
            return None
        from openlaser.canvas.raster_items import qimage_to_gray
        from openlaser.image_processing.raster import RasterSource, limit_size
        gray = qimage_to_gray(img)
        dpm = img.dotsPerMeterX()
        px_per_mm = dpm / 1000.0 if 2.0 <= dpm / 1000.0 <= 100.0 else 10.0  # sin DPI fiable: 254 DPI
        w_mm, h_mm = gray.shape[1] / px_per_mm, gray.shape[0] / px_per_mm
        gray = limit_size(gray)
        return path, (RasterSource(os.path.basename(path), gray), w_mm, h_mm)

    def _insert_image(self, path: str | None = None, select: bool = True) -> str | None:
        w = self  # type: Any
        loaded = self._load_image_file(path)
        if loaded is None:
            return None
        path, (src, w_mm, h_mm) = loaded
        f = min(1.0, 0.8 * self.doc.width_mm / w_mm, 0.8 * self.doc.height_mm / h_mm)
        w_mm, h_mm = w_mm * f, h_mm * f
        x, y = (self.doc.width_mm - w_mm) / 2, (self.doc.height_mm - h_mm) / 2
        rid = ops.new_id()

        def do() -> str:
            self.doc.images[rid] = src
            lid = self._layer_with_mode(LayerMode.IMAGE, tr("Imagen"))
            shape = make_image_shape(lid, rid, src.width, src.height, x, y, w_mm, h_mm, src.name)
            ops.add_shapes(self.doc, [shape])
            return shape.id
        sid = self._edit(tr("Insertar imagen"), do)
        if select:
            w.canvas.view.set_selection([sid])
            if f < 1.0:
                w._flash(tr("Imagen reducida al {p:.0f} % para que quepa en el área de trabajo", p=f * 100))
        return sid

    def _selected_image(self) -> Shape | None:
        ids = ops.image_ids(self.doc, self._selection(editable=False, quiet=True))
        return self.doc.shape(ids[0]) if ids else None

    @staticmethod
    def _recrop(ref: ImageRef, crop: tuple[float, float, float, float]) -> tuple[Point2, Point2, Point2]:
        """Esquinas del marco para un recorte nuevo conservando la escala y la posición del original."""
        l0, t0, r0, b0 = ref.settings.crop
        c0, c1, c3 = ref.corners
        fw, fh = max(1e-6, 1 - l0 - r0), max(1e-6, 1 - t0 - b0)
        e1 = ((c1[0] - c0[0]) / fw, (c1[1] - c0[1]) / fw)
        e3 = ((c3[0] - c0[0]) / fh, (c3[1] - c0[1]) / fh)
        o = (c0[0] - l0 * e1[0] - t0 * e3[0], c0[1] - l0 * e1[1] - t0 * e3[1])
        left, top, right, bottom = crop
        n0 = (o[0] + left * e1[0] + top * e3[0], o[1] + left * e1[1] + top * e3[1])
        n1 = (n0[0] + (1 - left - right) * e1[0], n0[1] + (1 - left - right) * e1[1])
        n3 = (n0[0] + (1 - top - bottom) * e3[0], n0[1] + (1 - top - bottom) * e3[1])
        return n0, n1, n3

    def _image_settings(self) -> None:
        w = self  # type: Any
        shape = self._selected_image()
        if shape is None or shape.image is None:
            self._flash_warn(tr("Seleccione una imagen"))
            return
        ref = shape.image
        src = self.doc.images.get(ref.resource)
        if src is None:
            self._flash_warn(tr("Faltan los píxeles de la imagen"))
            return
        from openlaser.ui.dialogs.image_dialogs import ImageDialog
        dlg = ImageDialog(src.gray, ref.settings, ref.width_mm, w)
        if not dlg.exec():
            return
        new_settings = dlg.settings()
        corners = self._recrop(ref, new_settings.crop) if new_settings.crop != ref.settings.crop else ref.corners
        new_ref = dc_replace(ref, settings=new_settings, corners=corners)

        def do() -> None:
            self.doc.shapes = [dc_replace(s, image=new_ref, subpaths=(new_ref.frame(),)) if s.id == shape.id else s
                               for s in self.doc.shapes]
        self._edit(tr("Ajustes de imagen"), do)

    def _vectorize(self) -> None:
        w = self  # type: Any
        from openlaser.image_processing.raster import adjust
        from openlaser.ui.dialogs.image_dialogs import VectorizeDialog, map_pixels_to_doc
        shape = self._selected_image()
        if shape is not None and shape.image is not None and shape.image.resource in self.doc.images:
            ref = shape.image
            src = self.doc.images[ref.resource]
            gray = (adjust(src.gray, dc_replace(ref.settings, invert=False)) * 255).round().astype("uint8")
            corners = ref.corners
        else:
            loaded = self._load_image_file(None)
            if loaded is None:
                return
            _path, (src, w_mm, h_mm) = loaded
            f = min(1.0, 0.8 * self.doc.width_mm / w_mm, 0.8 * self.doc.height_mm / h_mm)
            w_mm, h_mm = w_mm * f, h_mm * f
            x, y = (self.doc.width_mm - w_mm) / 2, (self.doc.height_mm - h_mm) / 2
            gray = src.gray
            corners = ((x, y), (x + w_mm, y), (x, y + h_mm))
            shape = None
        dlg = VectorizeDialog(gray, w)
        if not dlg.exec():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            subs, size = dlg.compute()
        finally:
            QApplication.restoreOverrideCursor()
        if not subs:
            self._flash_warn(tr("La vectorización no produjo contornos"))
            return
        doc_subs = map_pixels_to_doc(subs, size, corners)
        fill = dlg.mode.currentData() == "fill"
        keep = dlg.keep.isChecked()

        def do() -> str:
            mode = LayerMode.FILL if fill else LayerMode.LINE
            current = self.doc.layer(self._current_layer_id())
            lid = current.id if current is not None and current.mode == mode else \
                self._layer_with_mode(mode, tr("Relleno") if fill else tr("Vectorizado"))
            new = make_shape(lid, doc_subs, tr("Vectorizado"), "path")
            ops.add_shapes(self.doc, [new])
            if shape is not None and not keep:
                ops.delete(self.doc, [shape.id])
            return new.id
        sid = self._edit(tr("Vectorizar"), do)
        w.canvas.view.set_selection([sid])
        w._flash(tr("{n} contornos creados", n=len(doc_subs)))

    # ================================================================ trayectorias
    def _round_corners(self) -> None:
        w = self  # type: Any
        if not ops.vector_ids(self.doc, self._selection()):
            return
        radius, ok = QInputDialog.getDouble(w, tr("Redondear esquinas"), tr("Radio (mm)"), 2.0, 0.01, 500, 2)
        if ok:
            self._arrange(lambda d, ids: ops.round_corners(d, ops.vector_ids(d, ids), radius), tr("Redondear esquinas"))

    def _tabs(self) -> None:
        w = self  # type: Any
        ids = ops.vector_ids(self.doc, self._selection(editable=False))
        layers = [ly for ly in self.doc.sorted_layers()
                  if ly.mode in (LayerMode.LINE, LayerMode.FILL_LINE) and any(s.layer_id == ly.id
                                                                             for s in self.doc.shapes_by_ids(ids))]
        if not layers:
            self._flash_warn(tr("Seleccione objetos de una capa en modo Línea"))
            return
        dlg = ed.TabsDialog(layers[0].tab_count or 4, layers[0].tab_width_mm, w)
        if not dlg.exec():
            return

        def do() -> None:
            for ly in layers:
                ly.tab_count = dlg.count.value()
                ly.tab_width_mm = dlg.tab_w.value()
        self._edit(tr("Puentes"), do)
        w.layers.refresh()
        w._flash(tr("Puentes aplicados a: {l}", l=", ".join(ly.name for ly in layers)))

    def _optimize_report(self) -> None:
        w = self  # type: Any
        import copy as _copy

        from openlaser.gcode.analyzer import analyze
        from openlaser.gcode.generator import generate_job

        def travel(optimize: bool) -> float:
            snap = _copy.copy(self.doc)
            snap.layers = [_copy.copy(ly) for ly in self.doc.layers]
            snap.shapes = list(self.doc.shapes)
            for ly in snap.layers:
                ly.optimize_travel = optimize
                ly.inside_first = optimize or ly.inside_first
            return analyze(generate_job(snap, w.profile).parts).travel_distance  # imágenes medidas sin texto

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            before, after = travel(False), travel(True)
        finally:
            QApplication.restoreOverrideCursor()

        def do() -> None:
            for ly in self.doc.layers:
                ly.optimize_travel = True
                ly.inside_first = True
        self._edit(tr("Optimizar trayectorias"), do)
        w.layers.refresh()
        saved = (1 - after / before) * 100 if before > 0 else 0.0
        QMessageBox.information(w, tr("Optimizar trayectorias"),
                                tr("Desplazamiento sin láser: {b:.0f} mm sin optimizar → {a:.0f} mm optimizado "
                                   "({p:.0f} % menos). Se ha activado el orden interior→exterior y la optimización "
                                   "en todas las capas.", b=before, a=after, p=saved))

    # ================================================================== simulación
    def _simulate(self) -> None:
        w = self  # type: Any
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            job = w._prepare_job()
            if job is None:
                return
            from openlaser.simulation.checks import check_document, check_moves
            st = w.status
            wco = (st.wco[0], st.wco[1])
            cs = self._cs()
            limits = (-wco[0], -wco[1], cs.width - wco[0], cs.height - wco[1])
            doc_issues = check_document(self.doc) if w.job.source() == "doc" else []
            parts = getattr(job, "sim_parts", None)  # imágenes como bloques: sin crear millones de líneas
            w.sim_panel.load(parts if parts is not None else job.raw_lines, cs, wco,
                             {ly.name: ly.color for ly in self.doc.layers}, w.profile.rapid_mm_min, doc_issues,
                             lambda tl: check_moves(tl, limits), text_lines=lambda: job.raw_lines)
        finally:
            QApplication.restoreOverrideCursor()
        w.dock_sim.show()
        w.dock_sim.raise_()

