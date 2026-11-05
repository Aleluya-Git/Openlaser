"""Acciones de la Fase 4 para la ventana principal: materiales, pruebas, cámara, rotary,
Print and Cut, Center Finder, plugins, biblioteca de formas, texto sobre trayectoria e historial."""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QMenu, QMessageBox

from openlaser.app.config import app_name, user_dir
from openlaser.camera.calibration import CameraStore, marker_positions
from openlaser.devices.rotary import CylinderCorrection, RotaryConfig
from openlaser.document import operations as ops
from openlaser.document.model import Document, LayerMode, make_shape
from openlaser.geometry import path as gp
from openlaser.geometry.stroke_font import text_subpaths
from openlaser.i18n import tr
from openlaser.importers import machine_to_doc_affine
from openlaser.machine.states import Action
from openlaser.materials.library import MaterialEntry, MaterialLibrary, load_or_create
from openlaser.materials.test_patterns import TEST_KINDS
from openlaser.plugins.manager import PluginManager

_log = logging.getLogger(__name__)
Point = tuple[float, float]

# Orden y agrupación del submenú Herramientas → Pruebas
TEST_MENU = [["speed_power", "power_passes", "interval", "focus", "min_power"],
             ["engrave", "cut", "kerf"],
             ["scale", "precision", "circles", "backlash"],
             ["rotary"]]


class PluginContext:
    """Lo que un plugin puede hacer en la interfaz (siempre deshacible y sin tocar la máquina)."""

    def __init__(self, window: Any) -> None:
        self._w = window

    @property
    def doc(self) -> Document:
        return self._w.doc

    def edit(self, text: str, fn: Callable[[], Any]) -> Any:
        return self._w._edit(text, fn)

    def select(self, ids: list[str]) -> None:
        self._w.canvas.view.set_selection(list(ids))

    def message(self, text: str, severity: str = "info") -> None:
        self._w._flash(text, severity)


class Phase4ActionsMixin:
    """Se mezcla con ``MainWindow`` (usa doc, profile, settings, controller, canvas…)."""

    # ----------------------------------------------------------------- arranque
    def _init_phase4(self) -> None:
        w = self  # type: Any
        base = user_dir()
        try:
            self.materials: MaterialLibrary = load_or_create(base / "materiales.json")
        except (ValueError, OSError):
            _log.exception("Biblioteca de materiales dañada; se usa una vacía")
            self.materials = MaterialLibrary(path=base / "materiales.json")
        self.camera_store = CameraStore(base / "cameras.json")
        self.plugins = PluginManager(base / "plugins", set(w.settings.plugins_enabled), PluginContext(self))

    def _build_phase4_widgets(self) -> None:
        w = self  # type: Any
        from openlaser.ui.panels.history_panel import HistoryPanel
        from openlaser.ui.panels.materials_panel import MaterialsPanel
        self.materials_panel = MaterialsPanel(self.materials, user_dir() / "material_photos", w.profile.name)
        self.materials_panel.apply_requested.connect(self._material_apply)
        self.materials_panel.save_layer_requested.connect(self._material_save_layer)
        self.history_panel = HistoryPanel(w.undo)
        self.sb_rotary = QLabel("")
        self.sb_rotary.setStyleSheet("QLabel { color: #000; background: #ffc400; border-radius: 4px; padding: 0 6px;"
                                     " font-weight: 700; }")
        self.sb_rotary.setToolTip(tr("Rotary o corrección cilíndrica activos: el eje Y no se mueve en línea recta"))
        self.sb_rotary.hide()

    def _tests_menu(self) -> QMenu:
        w = self  # type: Any
        menu = QMenu(tr("Pruebas"), w)
        for gi, group in enumerate(TEST_MENU + [[k for k in TEST_KINDS if not any(k in g for g in TEST_MENU)]]):
            if not group:
                continue
            if gi:
                menu.addSeparator()
            for kind in group:
                act = menu.addAction(tr(TEST_KINDS[kind].title) + "…")
                act.setObjectName(f"tests.{kind}")
                act.setToolTip(tr(TEST_KINDS[kind].description))
                act.triggered.connect(lambda _c=False, k=kind: self._test_wizard(k))
        return menu

    def _fill_plugins_menu(self) -> None:
        menu: QMenu = self.menu_plugins  # type: ignore[attr-defined]
        menu.clear()
        if not self.plugins.actions:
            act = menu.addAction(tr("(ningún plugin activo)"))
            act.setEnabled(False)
        for pa in self.plugins.actions:
            act = menu.addAction(pa.text)
            act.setToolTip(pa.plugin_id)
            act.triggered.connect(lambda _c=False, a=pa: self._run_plugin_action(a))
        menu.addSeparator()
        mng = QAction(tr("Gestión de plugins…"), menu)
        mng.triggered.connect(lambda: self._plugins_dialog())
        menu.addAction(mng)

    def _load_plugins(self) -> None:
        self.plugins.discover()
        for info in self.plugins.load_enabled():
            _log.info("Plugin cargado: %s %s", info.id, info.version)
        for info in self.plugins.plugins.values():
            if info.id in self.plugins.enabled and info.error:
                self._flash(tr("Plugin «{n}» no cargado: {e}", n=info.name, e=info.error), "warn")  # type: ignore[attr-defined]

    def _run_plugin_action(self, pa: Any) -> None:
        try:
            pa.callback()
        except Exception as exc:  # noqa: BLE001 - un error en un plugin no debe cerrar la aplicación
            _log.exception("Acción del plugin %s", pa.plugin_id)
            QMessageBox.warning(self, tr("Plugin"), tr("Error en el plugin {p}: {e}", p=pa.plugin_id, e=exc))  # type: ignore[arg-type]

    def _phase4_handlers(self) -> dict[str, Callable[[], Any]]:
        w = self  # type: Any
        return {
            "laser.material_test": lambda: self._test_wizard("speed_power"),
            "laser.focus_test": lambda: self._test_wizard("focus"),
            "laser.interval_test": lambda: self._test_wizard("interval"),
            "laser.print_cut": self._print_cut, "laser.center_finder": self._center_finder,
            "laser.rotary": lambda: self._rotary_dialog(0), "laser.cylinder": lambda: self._rotary_dialog(1),
            "file.send_gcode": self._send_gcode_sd,
            "tools.material_library": lambda: w._show_dock(w.dock_materials),
            "tools.shape_library": self._shape_library, "draw.text_on_path": self._text_on_path,
            "camera.detect": lambda: (w._show_dock(w.dock_camera), w.camera.detect()),
            "camera.calibrate_lens": lambda: (w._show_dock(w.dock_camera), w.camera.calibrate_lens()),
            "camera.calibrate_area": lambda: (w._show_dock(w.dock_camera), w.camera.calibrate_area()),
            "camera.capture": lambda: (w._show_dock(w.dock_camera), w.camera.capture()),
            "edit.plugins": self._plugins_dialog,
        }

    # -------------------------------------------------------------- materiales
    def _material_apply(self, entry: MaterialEntry) -> None:
        w = self  # type: Any
        layer = w.doc.layer(w._current_layer_id())
        if layer is None:
            return
        w._edit(tr("Aplicar material"), lambda: entry.apply_to_layer(layer))
        w.layers.refresh()
        w._flash(tr("«{m}» aplicado a la capa «{c}»", m=entry.title(), c=layer.name))

    def _material_save_layer(self) -> None:
        w = self  # type: Any
        layer = w.doc.layer(w._current_layer_id())
        if layer is None:
            return
        name, ok = QInputDialog.getText(w, tr("Guardar capa como material"), tr("Material"), text=layer.name)
        if ok and name.strip():
            entry = MaterialEntry.from_layer(layer, name.strip(), machine=w.profile.name)
            self.materials_panel.add_entry(entry, edit=True)

    # ------------------------------------------------------------------ pruebas
    def _test_wizard(self, kind: str) -> None:
        w = self  # type: Any
        from openlaser.materials.test_patterns import apply_plan
        from openlaser.ui.dialogs.test_wizard import TestWizardDialog
        dlg = TestWizardDialog(kind, w.profile, w.doc, w.settings.test_params.get(kind), w)
        if not dlg.exec() or dlg.plan is None:
            return
        w.settings.test_params[kind] = dlg.values()
        plan = dlg.plan
        ids = w._edit(tr(TEST_KINDS[kind].title), lambda: apply_plan(w.doc, plan))
        w.layers.refresh()
        w.canvas.view.set_selection(ids)
        w._flash(tr("Prueba añadida: {n} capas. Revise con Vista previa antes de iniciar.", n=len(plan.layers)))

    # ------------------------------------------------------------------- cámara
    def _camera_markers(self, count: int) -> None:
        w = self  # type: Any
        from openlaser.geometry.coords import CoordinateSystem
        p = w.profile
        cs = CoordinateSystem.from_profile(p)
        pts = marker_positions(p.width_mm, p.height_mm, count)

        def do() -> list[str]:
            layer = ops.add_layer(w.doc, tr("Marcadores de cámara"))
            layer.mode, layer.speed_mm_min, layer.power_max_pct = LayerMode.LINE, 1500.0, 10.0
            layer.notes = tr("Grabe estas cruces con baja potencia sobre un material de prueba y calibre el área")
            subs: list[gp.SubPath] = []
            for i, mp in enumerate(pts):
                x, y = cs.machine_to_doc(mp)
                subs += [gp.polyline([(x - 6, y), (x + 6, y)]), gp.polyline([(x, y - 6), (x, y + 6)])]
                subs += text_subpaths(str(i + 1), x + 2, y - 2, 4)
            shape = make_shape(layer.id, subs, tr("Marcadores de cámara"))
            w.doc.shapes.append(shape)
            return [shape.id]
        ids = w._edit(tr("Marcadores de cámara"), do)
        w.layers.refresh()
        w.canvas.view.set_selection(ids)
        w._flash(tr("Marcadores añadidos. Grábelos, capture la imagen y pulse «Calibrar área…»"))

    # ------------------------------------------------------------------- rotary
    def _rotary_dialog(self, tab: int) -> None:
        w = self  # type: Any
        from openlaser.ui.dialogs.machine_dialogs import RotaryDialog
        from PySide6.QtWidgets import QTabWidget

        from openlaser.machine.states import JobState
        if w.status.job in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            QMessageBox.information(w, tr("Configuración rotativa"), tr("No se puede cambiar durante un trabajo"))
            return
        dlg = RotaryDialog(w.profile, self._test_rotation, w)
        tw = dlg.findChild(QTabWidget)
        if tw is not None:
            tw.setCurrentIndex(tab)
        if dlg.exec():
            for p in w.settings.profiles:
                if p.id == w.profile.id:
                    p.rotary, p.cylinder, p.rotary_enabled = w.profile.rotary, w.profile.cylinder, \
                        w.profile.rotary_enabled
            w._save_settings()
            self._update_rotary_indicator()
            w._refresh_analysis()

    def _test_rotation(self, units: float, axis: str) -> None:
        w = self  # type: Any
        if Action.JOG not in w.allowed:
            w._flash(tr("Conecte y deje la máquina en reposo para la prueba de rotación"), "warn")
            return
        if axis == "A":
            w.controller.jog(0.0, 0.0, 0.0, units)
        else:
            w.controller.jog(0.0, units, 0.0, 0.0)

    def _update_rotary_indicator(self) -> None:
        w = self  # type: Any
        rot = RotaryConfig.from_dict(w.profile.rotary)
        cyl = CylinderCorrection.from_dict(w.profile.cylinder)
        if rot.enabled:
            self.sb_rotary.setText(tr("ROTARY {k} · ⌀{d:g} mm", k=tr("rodillos") if rot.kind == "roller" else
                                      tr("mandril"), d=rot.object_diameter_mm))
        elif cyl.enabled:
            self.sb_rotary.setText(tr("CILINDRO ⌀{d:g} mm", d=cyl.diameter_mm))
        self.sb_rotary.setVisible(rot.enabled or cyl.enabled)

    # ------------------------------------------------------------ Print and Cut
    def _print_cut(self) -> None:
        w = self  # type: Any
        from openlaser.ui.dialogs.machine_dialogs import PrintCutDialog
        ids = w.canvas.view.selected_shape_ids()
        marks = w.doc.shapes_by_ids(ids)
        if len(marks) not in (2, 3):
            QMessageBox.information(w, tr("Print and Cut"),
                                    tr("Seleccione 2 o 3 marcas de registro del diseño (por ejemplo, cruces o círculos "
                                       "pequeños impresos junto a la pieza)."))
            return
        cs = w._cs()
        design = []
        for s in marks:
            b = s.bounds()
            design.append(cs.doc_to_machine(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)))
        dlg = PrintCutDialog(design, lambda: (w.status.mpos[0], w.status.mpos[1]), self._goto_machine, w)
        if not dlg.exec() or dlg.transform is None:
            return
        a = machine_to_doc_affine(cs)          # máquina → documento (la inversa es la misma)
        t_doc = a @ dlg.transform @ a
        all_ids = [s.id for s in w.doc.shapes if not s.locked]
        w._edit(tr("Print and Cut"), lambda: ops.transform(w.doc, all_ids, t_doc))
        w._flash(tr("Diseño alineado con las marcas registradas. Compruébelo con un frame."))

    def _goto_machine(self, p: Point) -> None:
        w = self  # type: Any
        wco = w.status.wco
        w.controller.go_to(p[0] - wco[0], p[1] - wco[1])

    # ------------------------------------------------------------ Center Finder
    def _center_finder(self) -> None:
        w = self  # type: Any
        from openlaser.ui.dialogs.machine_dialogs import CenterFinderDialog
        ids = ops.editable_ids(w.doc, w.canvas.view.selected_shape_ids())

        def center_selection(p: Point) -> None:
            target = w._cs().work_to_doc(p, (w.status.wco[0], w.status.wco[1]))
            w._edit(tr("Centrar en la pieza"), lambda: ops.move_to_point(w.doc, ids, target))

        dlg = CenterFinderDialog(lambda: (w.status.wpos[0], w.status.wpos[1]),
                                 lambda p: w.controller.go_to(p[0], p[1]), lambda: w.controller.set_origin(),
                                 center_selection if ids else None, w)
        dlg.exec()

    # --------------------------------------------------------- formas y texto
    def _shape_library(self) -> None:
        w = self  # type: Any
        from openlaser.geometry.shape_library import SHAPES
        from openlaser.ui.dialogs.shape_dialogs import ShapeLibraryDialog
        dlg = ShapeLibraryDialog(w)
        if dlg.exec():
            sh = SHAPES[dlg.shape_id()]
            subs = sh.build(w.doc.width_mm / 2, w.doc.height_mm / 2, dlg.size_mm.value())
            w._on_shape_created(subs, "library", tr(sh.name))

    def _text_on_path(self) -> None:
        w = self  # type: Any
        from openlaser.ui.dialogs.shape_dialogs import TextOnPathDialog
        ids = ops.vector_ids(w.doc, w.canvas.view.selected_shape_ids())
        if len(ids) != 1:
            w._flash(tr("Seleccione una única trayectoria guía"), "warn")
            return
        guide = w.doc.shape(ids[0])
        dlg = TextOnPathDialog(w)
        if not dlg.exec():
            return
        subs = dlg.build(guide.subpaths[0])
        if not subs:
            w._flash(tr("El texto no cabe en la trayectoria o está vacío"), "warn")
            return
        w._on_shape_created(subs, "text", dlg.text.text()[:40])

    # ------------------------------------------------------------------- varios
    def _send_gcode_sd(self, dialog_hook: Any = None) -> bool:
        """Envía el trabajo preparado a la SD del controlador (FluidNC/ESP3D) sin ponerlo en marcha."""
        w = self  # type: Any
        from openlaser.devices import sd_upload
        from openlaser.ui.dialogs.machine_dialogs import SdUploadDialog
        from openlaser.gcode.analyzer import format_duration
        job = w._prepare_job()
        if job is None:
            return False
        a = job.analysis
        header = [f"{app_name()} — {job.name}", f"{tr('Máquina')}: {w.profile.name}",
                  f"{tr('Tiempo estimado')}: {format_duration(a.estimated_seconds)}",
                  tr("Generado para esta máquina y este origen: no lo use en otra")]
        data = sd_upload.job_text(job.lines, header)
        warnings = list(job.warnings)
        ok, why = w._bounds_check(a)
        if not ok:
            warnings.append(tr("Con el origen actual el trabajo sale del área: {r}", r=why))
        if a.unsupported:
            warnings.append(tr("{n} líneas no reconocidas se enviarán tal cual", n=len(a.unsupported)))
        if w.profile.rotary_enabled:
            warnings.append(tr("Trabajo transformado para el rotary: ejecútelo sólo con el rotary montado"))
        summary = [tr("Tiempo estimado: {t}", t=format_duration(a.estimated_seconds)),
                   tr("Potencia máxima: S{s:g}", s=a.max_s)]

        def test(target: Any) -> str:
            try:
                res = sd_upload.list_files(target)
            except sd_upload.UploadError as exc:
                return "✗ " + exc.translated()
            return "✓ " + tr("Conectado: {n} archivos en la carpeta", n=len(res.files))

        dlg = SdUploadDialog(w.profile, job.name, len(data), summary, warnings, test, w)
        if dialog_hook is not None:
            dialog_hook(dlg)
        if not dlg.exec():
            return False
        dlg.store(w.profile)
        for p in w.settings.profiles:
            if p.id == w.profile.id:
                dlg.store(p)
        w._save_settings()
        target, name = dlg.target(), dlg.filename()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            res = sd_upload.upload(target, name, data)
        except sd_upload.UploadError as exc:
            QMessageBox.warning(w, tr("Enviar G-code a la tarjeta SD"), exc.translated())
            return False
        finally:
            QApplication.restoreOverrideCursor()
        msg = tr("«{f}» guardado en la tarjeta SD ({k:.1f} KB)", f=res.remote_path, k=res.size / 1024.0)
        if not res.confirmed:
            msg += " — " + tr("el controlador no confirmó el archivo en el listado: compruébelo antes de usarlo")
        w._flash(msg, "info" if res.confirmed else "warn")
        return True

    def _plugins_dialog(self) -> None:
        w = self  # type: Any
        from openlaser.ui.dialogs.machine_dialogs import PluginsDialog
        self.plugins.discover()
        dlg = PluginsDialog(self.plugins, w)
        if not dlg.exec():
            return
        enabled = dlg.enabled_ids()
        newly = enabled - self.plugins.enabled
        removed = self.plugins.enabled - enabled
        self.plugins.enabled = enabled
        w.settings.plugins_enabled = sorted(enabled)
        w._save_settings()
        if newly:
            self.plugins.load_enabled()
            self._fill_plugins_menu()
        if removed & {pid for pid, i in self.plugins.plugins.items() if i.loaded}:
            QMessageBox.information(w, tr("Plugins"), tr("Los plugins desactivados se descargarán al reiniciar."))
