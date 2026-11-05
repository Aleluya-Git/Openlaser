"""Ventana principal de OpenLaser Studio (Fases 1-2)."""
from __future__ import annotations

import copy
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path

from PySide6.QtCore import QByteArray, QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox, QProgressBar, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from openlaser.app.config import app_name
from openlaser.canvas.canvas_view import CanvasArea
from openlaser.devices.profile import DeviceProfile, TransportKind
from openlaser.devices.rotary import machine_transform, transform_active
from openlaser.devices.transport import SimulatorTransport, Transport, create_transport
from openlaser.document.commands import UndoStack
from openlaser.document.model import Document, demo_document
from openlaser.gcode.analyzer import Analysis, analyze, format_duration, prepare_lines, toolpath_segments
from openlaser.gcode.generator import GeneratedJob, JobOrigin, generate_job
from openlaser.geometry.coords import CoordinateSystem, rect_frame
from openlaser.i18n import tr
from openlaser.machine.controller import MachineController
from openlaser.machine.events import ConsoleEvent, ErrorEvent, JobProgressEvent, LineKind, MachineStatus, SafetyNotice
from openlaser.machine.grbl_codes import error_info
from openlaser.machine.states import Action, ConnState, GrblState, JobState
from openlaser.persistence.atomic import atomic_write_text
from openlaser.persistence.settings import AppSettings, Macro, save_settings
from openlaser.safety.manager import Risk, SafetyManager
from openlaser.simulator.grbl_simulator import GrblSimulator
from openlaser.ui import icons, theme
from openlaser.ui.actions import ActionRegistry
from openlaser.ui.bridge import MachineBridge
from openlaser.ui.dialogs import config_dialogs as cfg
from openlaser.ui.dialogs import info_dialogs as info
from openlaser.ui.dialogs.safety_dialogs import AlarmDialog, FireDialog, PreflightDialog
from openlaser.ui.document_actions import DocumentActionsMixin
from openlaser.ui.phase4_actions import Phase4ActionsMixin
from openlaser.ui.panels.console_panel import ConsolePanel
from openlaser.ui.panels.camera_panel import CameraPanel
from openlaser.ui.panels.info_panels import PropertiesPanel
from openlaser.ui.panels.job_panel import JobPanel
from openlaser.ui.panels.layers_panel import LayersPanel
from openlaser.ui.panels.motion_panel import MotionPanel
from openlaser.ui.panels.simulation_panel import SimulationPanel
from openlaser.ui.widgets.safety_banner import SafetyBanner
from openlaser.ui.widgets.state_badge import StateBadge

_log = logging.getLogger(__name__)
_HAS_XY = re.compile(r"[XY][-+.\d]")

_JOG_KEYS = {
    "jog_x_plus": (1, 0, 0, 0), "jog_x_minus": (-1, 0, 0, 0), "jog_y_plus": (0, 1, 0, 0),
    "jog_y_minus": (0, -1, 0, 0), "jog_z_plus": (0, 0, 1, 0), "jog_z_minus": (0, 0, -1, 0),
    "jog_a_plus": (0, 0, 0, 1), "jog_a_minus": (0, 0, 0, -1),
}


class PreparedJob:
    """Trabajo listo para enviar. Con imágenes, el texto G-code se crea la primera vez que se pide
    (``lines``/``raw_lines``/``display_lines``) y queda guardado; el análisis no lo necesita."""

    def __init__(self, name: str, lines: "list[str] | Callable[[], list[str]]", analysis: Analysis,
                 skipped: list[str], raw_lines: "list[str] | Callable[[], list[str]] | None" = None,
                 warnings: list[str] | None = None) -> None:
        self.name, self.analysis, self.skipped = name, analysis, skipped
        self._lines = lines
        self._raw = raw_lines if raw_lines is not None else lines  # con comentarios «; capa:»
        self._display: "list[str] | Callable[[], list[str]] | None" = None  # sin rotary (vista previa)
        self.sim_parts: list | None = None
        self.warnings = warnings or []

    @property
    def lines(self) -> list[str]:
        if callable(self._lines):
            self._lines = self._lines()
        return self._lines

    @property
    def raw_lines(self) -> list[str]:
        if callable(self._raw):
            self._raw = self._raw()
        return self._raw

    @property
    def display_lines(self) -> list[str]:
        if self._display is None:
            return self.lines
        if callable(self._display):
            self._display = self._display()
        return self._display

    @display_lines.setter
    def display_lines(self, value: "list[str] | Callable[[], list[str]]") -> None:
        self._display = value


@dataclass
class JobInputs:
    """Todo lo necesario para preparar el trabajo, copiado para poder calcularlo en segundo plano."""
    source: str
    gcode: list[str] | None
    gcode_name: str
    doc: Document
    profile: DeviceProfile
    wco: tuple[float, float]
    origin: str
    copies: int


def compute_job(inp: JobInputs) -> tuple[PreparedJob | None, str]:
    """Genera (o toma del archivo), limpia y analiza el trabajo. Devuelve (trabajo, motivo si no hay)."""
    raw: list[str]
    warnings: list[str] = []
    if inp.source == "file":
        if not inp.gcode:
            return None, tr("Cargue primero un archivo G-code")
        raw = list(inp.gcode)
        name, skipped = inp.gcode_name, []
    else:
        gen = generate_job(inp.doc, inp.profile, wco=inp.wco, origin=JobOrigin(inp.origin))
        name, skipped, warnings = inp.doc.name, gen.skipped_layers, gen.warnings
        if gen.has_lazy_blocks and not transform_active(inp.profile):
            return _compute_lazy(gen, inp, name, skipped, warnings)
        raw = gen.lines
    base = prepare_lines(raw)
    if not any(_HAS_XY.search(ln) for ln in base if not ln.startswith("$")):
        return None, tr("No hay geometría con salida en ninguna capa")
    display = base * inp.copies
    if inp.source != "file":
        # rotary / corrección cilíndrica: sólo cambia lo que se envía; vista previa y simulación en mm de superficie
        machine, extra = machine_transform(base, inp.profile)
        warnings = warnings + [tr(w) for w in extra]
    else:
        machine = base
    lines = machine * inp.copies
    a = analyze(lines, rapid_mm_min=inp.profile.rapid_mm_min,
                clean=inp.source != "file" and not transform_active(inp.profile))
    job = PreparedJob(name, lines, a, skipped, raw * inp.copies, warnings)
    job.display_lines = display
    return job, ""


def _compute_lazy(gen: GeneratedJob, inp: JobInputs, name: str, skipped: list[str],
                  warnings: list[str]) -> tuple[PreparedJob | None, str]:
    """Trabajo con imágenes: se analiza midiendo los bloques ráster (sin crear millones de líneas de texto).

    Mover una imagen sólo cambia su posición: los píxeles tramados y sus tramos ya están en memoria, así que
    el análisis tarda décimas de segundo. El texto se crea al enviar, guardar o mostrar la vista previa.
    """
    parts: list = []
    for p in gen.parts:
        if isinstance(p, str):
            parts.extend(prepare_lines([p]))
        else:
            parts.append(p)
    a = analyze(parts * inp.copies, rapid_mm_min=inp.profile.rapid_mm_min, clean=True)
    copies = inp.copies

    def base() -> list[str]:
        return prepare_lines(gen.lines)

    job = PreparedJob(name, lambda: base() * copies, a, skipped, lambda: gen.lines * copies, warnings)
    job.sim_parts = list(gen.parts) * copies  # la simulación dibuja las imágenes sin crear su texto
    return job, ""


class MainWindow(Phase4ActionsMixin, DocumentActionsMixin, QMainWindow):
    def __init__(self, settings: AppSettings, settings_path: Path, interactive: bool = True) -> None:
        super().__init__()
        self.interactive = interactive  # False: pruebas/captura, sin diálogos modales de guardado/recuperación
        icons.set_color_mode(settings.color_icons)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analisis")
        self._analysis_future: Future | None = None
        self._analysis_seq = 0
        self._analysis_future_seq = -1
        # Último trabajo calculado y su clave: Vista previa, Frame, Iniciar y Guardar lo reutilizan en vez de
        # volver a generar (con imágenes grandes generar y analizar tarda varios segundos).
        self._doc_rev = 0
        self._job_cache: tuple[tuple, PreparedJob | None, str] | None = None
        self._analysis_key: tuple | None = None
        self._analysis_poll = QTimer(self)
        self._analysis_poll.setInterval(60)
        self._analysis_poll.timeout.connect(self._poll_analysis)
        self._pending_timer = QTimer(self)
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(400)
        self._pending_timer.timeout.connect(self._maybe_show_pending)
        self.settings = settings
        self.settings_path = settings_path
        self.setWindowIcon(icons.app_icon())
        self.safety = SafetyManager(settings.safety)
        self.profile: DeviceProfile = settings.active_profile()
        self._simulator: Any = GrblSimulator()  # GrblSimulator o MarlinSimulator según el perfil
        self._last_transport: Transport | None = None
        self.controller = self._make_controller()
        self.bridge = MachineBridge(self.controller, self)
        self.undo = UndoStack(settings.undo_limit)
        self.doc: Document = demo_document(self.profile.width_mm, self.profile.height_mm)
        self.loaded_gcode: list[str] | None = None
        self.loaded_name = ""
        self.status = MachineStatus()
        self.allowed: set[Action] = set()
        self.grbl_values: dict[int, str] = {}
        self._grbl_dialog: cfg.GrblSettingsDialog | None = None
        self._alarm_dialog: AlarmDialog | None = None
        self._jog_shortcuts: list[QShortcut] = []
        self._keepalive = QTimer(self)
        self._keepalive.setInterval(100)
        self._keepalive.timeout.connect(lambda: self.controller.jog_keepalive())

        self.registry = ActionRegistry(self)
        self._init_document_state()
        self._init_phase4()
        self._build_central()
        self._build_docks()
        self._build_toolbars()
        self._build_menus()
        self._build_statusbar()
        self._connect_actions()
        self._connect_signals()
        self._install_jog_shortcuts()
        self._apply_profile()
        self._default_state = self.saveState()
        self._restore_window()
        self._on_status(self.controller.status())
        self.undo.listeners.append(self._on_undo_changed)
        self._loading_doc = True
        self._on_undo_changed()
        self._loading_doc = False
        self._update_title()
        self.setAcceptDrops(True)
        self._load_plugins()
        self._fill_plugins_menu()
        self._update_rotary_indicator()
        QTimer.singleShot(0, self._initial_sizes)
        QTimer.singleShot(400, self._check_autosave_on_start)

    # ================================================================ construcción
    def _make_controller(self) -> MachineController:
        def factory(p: DeviceProfile) -> Transport:
            from openlaser.devices.transport import make_simulator
            if self._simulator.firmware != p.firmware:  # el simulador imita el firmware del perfil
                self._simulator = make_simulator(p.firmware, self._simulator.speed_factor)
            t = create_transport(p, simulator=self._simulator)
            self._last_transport = t
            return t

        from openlaser.machine.factory import create_controller
        return create_controller(self.profile, safety=self.safety, transport_factory=factory)

    def _build_central(self) -> None:
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.banner = SafetyBanner()
        lay.addWidget(self.banner)
        self.canvas = CanvasArea()
        lay.addWidget(self.canvas, 1)
        self.setCentralWidget(central)

    def _dock(self, name: str, title: str, widget: QWidget, area: Qt.DockWidgetArea) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setWidget(widget)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.addDockWidget(area, dock)
        return dock

    def _build_docks(self) -> None:
        self.motion = MotionPanel(self.settings)
        self.job = JobPanel()
        self.layers = LayersPanel(self.undo)
        self.properties = PropertiesPanel()
        self.camera = CameraPanel(self, self.camera_store)
        self.camera.message.connect(lambda text, sev: self._flash(text, sev))
        self.camera.markers_requested.connect(self._camera_markers)
        self._build_phase4_widgets()
        self.console = ConsolePanel(self.settings)
        R, L, B = (Qt.DockWidgetArea.RightDockWidgetArea, Qt.DockWidgetArea.LeftDockWidgetArea,
                   Qt.DockWidgetArea.BottomDockWidgetArea)
        self.dock_motion = self._dock("dock.motion", tr("Movimiento"), self.motion, L)
        self.dock_layers = self._dock("dock.layers", tr("Capas y operaciones"), self.layers, R)
        self.dock_props = self._dock("dock.properties", tr("Propiedades"), self.properties, R)
        self.dock_camera = self._dock("dock.camera", tr("Cámara"), self.camera, R)
        self.dock_job = self._dock("dock.laser", tr("Láser"), self.job, R)
        self.dock_console = self._dock("dock.console", tr("Consola GRBL"), self.console, B)
        self.sim_panel = SimulationPanel(self.canvas.view)
        self.dock_sim = self._dock("dock.simulation", tr("Simulación"), self.sim_panel, B)
        self.dock_materials = self._dock("dock.materials", tr("Biblioteca de materiales"), self.materials_panel, R)
        self.dock_history = self._dock("dock.history", tr("Historial"), self.history_panel, R)
        self.tabifyDockWidget(self.dock_console, self.dock_sim)
        self.dock_console.raise_()
        self.dock_sim.hide()
        self.tabifyDockWidget(self.dock_layers, self.dock_props)
        self.tabifyDockWidget(self.dock_props, self.dock_camera)
        self.tabifyDockWidget(self.dock_camera, self.dock_materials)
        self.tabifyDockWidget(self.dock_materials, self.dock_history)
        self.dock_layers.raise_()
        self.dock_camera.hide()
        self.dock_history.hide()
        self.resizeDocks([self.dock_motion], [300], Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_layers, self.dock_job], [360, 360], Qt.Orientation.Horizontal)
        self.resizeDocks([self.dock_console], [190], Qt.Orientation.Vertical)
        self.setDockOptions(QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.AllowTabbedDocks
                            | QMainWindow.DockOption.AllowNestedDocks)

    def _toolbar(self, name: str, title: str, ids: list[str], area: Qt.ToolBarArea) -> QToolBar:
        tb = QToolBar(title, self)
        tb.setObjectName(name)
        tb.setMovable(True)
        if self.settings.show_icon_text:
            tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        for aid in ids:
            if aid == "-":
                tb.addSeparator()
                continue
            act = self.registry[aid]
            tb.addAction(act)
            w = tb.widgetForAction(act)
            if w is not None:
                w.setAccessibleName(act.text())
                w.setAccessibleDescription(act.statusTip())
        self.addToolBar(area, tb)
        return tb

    def _build_toolbars(self) -> None:
        top, left = Qt.ToolBarArea.TopToolBarArea, Qt.ToolBarArea.LeftToolBarArea
        self.tb_main = self._toolbar("toolbar.main", tr("Barra principal"), [
            "file.new", "file.open", "file.save", "file.import", "file.export", "-",
            "edit.undo", "edit.redo", "edit.cut", "edit.copy", "edit.paste", "edit.delete", "-",
            "laser.preview", "laser.simulate", "laser.frame", "laser.start", "laser.pause", "laser.resume",
            "laser.stop", "-",
            "machine.home", "machine.connect", "machine.disconnect", "machine.camera", "laser.rotary",
            "laser.material_test", "machine.settings"], top)
        self.tb_draw = self._toolbar("toolbar.draw", tr("Herramientas de dibujo"), [
            "draw.select", "draw.nodes", "draw.line", "draw.rect", "draw.ellipse", "draw.polygon", "draw.text",
            "draw.image", "draw.vectorize", "draw.measure"], left)
        self.addToolBarBreak(top)
        self.tb_modify = self._toolbar("toolbar.modify", tr("Herramientas de modificación"), [
            "view.zoom_in", "view.fit", "view.fit_selection", "view.center", "-", "arrange.align",
            "arrange.distribute", "arrange.group", "arrange.ungroup", "-", "arrange.union", "arrange.subtract",
            "arrange.intersect", "arrange.offset", "-", "arrange.array", "arrange.mirror", "arrange.rotate"], top)
        self.registry["draw.select"].setChecked(True)
        self.addToolBarBreak(top)
        self.tb_options = self._build_tool_options()
        self.addToolBar(top, self.tb_options)

    def _menu(self, title: str, ids: list[object]) -> QMenu:
        menu = self.menuBar().addMenu(title)
        for aid in ids:
            if aid == "-":
                menu.addSeparator()
            elif isinstance(aid, QMenu):
                menu.addMenu(aid)
            elif isinstance(aid, QAction):
                menu.addAction(aid)
            else:
                menu.addAction(self.registry[str(aid)])
        return menu

    def _build_menus(self) -> None:
        self.menu_devices = QMenu(tr("Seleccionar dispositivo"), self)
        self.menu_devices.aboutToShow.connect(self._fill_device_menu)
        self.menu_recent = QMenu(tr("Abrir reciente"), self)
        self.menu_recent.aboutToShow.connect(self._fill_recent_menu)
        self._menu(tr("&Archivo"), [
            "file.new", "file.open", self.menu_recent, "file.save", "file.save_as", "file.autosave", "file.recover", "-",
            "file.import", "file.export", "-", "file.open_gcode", "file.save_gcode", "file.send_gcode", "-",
            "file.import_settings", "file.export_settings", "file.backup", "file.restore", "-",
            "file.doc_props", "file.page", "file.print_summary", "-", "file.quit"])
        self._menu(tr("&Editar"), [
            "edit.undo", "edit.redo", "-", "edit.cut", "edit.copy", "edit.paste", "edit.paste_in_place",
            "edit.duplicate", "edit.delete", "-", "edit.select_all", "edit.deselect", "edit.invert_selection",
            "edit.select_layer", "edit.select_type", "-", "edit.lock", "edit.toggle_visible", "-",
            "edit.preferences", "edit.devices", "edit.machine", "edit.plugins"])
        self._menu(tr("&Dibujar"), [
            "draw.select", "draw.nodes", "-", "draw.line", "draw.rect", "draw.ellipse", "draw.polygon", "draw.text",
            "-", "draw.image", "image.settings", "draw.vectorize", "draw.measure"])
        self.menu_align = self._submenu(tr("Alinear"), [
            "arrange.align_left", "arrange.align_hcenter", "arrange.align_right", "-", "arrange.align_top",
            "arrange.align_vcenter", "arrange.align_bottom", "-", "arrange.align_center"])
        self.menu_distribute = self._submenu(tr("Distribuir"), [
            "arrange.distribute_h", "arrange.distribute_v", "-", "arrange.same_width", "arrange.same_height"])
        order = self._submenu(tr("Orden"), ["arrange.front", "arrange.raise", "arrange.lower", "arrange.back"])
        boolean = self._submenu(tr("Operaciones booleanas"), [
            "arrange.union", "arrange.subtract", "arrange.intersect", "arrange.xor", "-", "arrange.combine",
            "arrange.break_apart"])
        transform = self._submenu(tr("Transformar"), [
            "arrange.rotate", "arrange.rotate_cw", "arrange.rotate_ccw", "-", "arrange.mirror", "arrange.flip_v"])
        arrays = self._submenu(tr("Matrices y copias"), ["arrange.array", "arrange.array_circular",
                                                          "arrange.copies_path"])
        position = self._submenu(tr("Posición"), [
            "arrange.move_to_page", "arrange.move_to_origin", "-", "arrange.move_to_laser", "arrange.center_on_laser",
            "arrange.laser_to_selection"])
        snap = self._submenu(tr("Ajuste magnético"), ["arrange.snap_grid", "arrange.snap_nodes", "arrange.snap_centers"])
        self._menu(tr("&Organizar"), [
            "arrange.group", "arrange.ungroup", "-", self.menu_align, self.menu_distribute, order, "-", boolean,
            "arrange.offset", "arrange.kerf", "-", transform, arrays, position, "-", snap])
        self._menu(tr("&Trayectoria"), [
            "path.close", "path.join", "path.simplify", "path.dedupe", "path.reverse", "-", "path.delete_duplicates",
            "-", "path.round_corners", "path.tabs", "draw.text_on_path"])
        self._menu(tr("&Capas"), [
            "layers.add", "layers.delete", "layers.settings", "-", "layers.assign", "edit.select_layer", "-",
            "layers.copy_settings", "layers.save_preset", "layers.apply_preset"])
        tb_align = self.tb_modify.widgetForAction(self.registry["arrange.align"])
        tb_dist = self.tb_modify.widgetForAction(self.registry["arrange.distribute"])
        for widget, menu in ((tb_align, self.menu_align), (tb_dist, self.menu_distribute)):
            if isinstance(widget, QToolButton):
                widget.setMenu(menu)
                widget.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._menu(tr("&Láser"), [
            "laser.preview", "laser.frame", "laser.frame_contour", "-", "laser.start", "laser.pause", "laser.resume",
            "laser.stop", "-", "laser.simulate", "file.open_gcode", "file.save_gcode", "laser.optimize", "-",
            "laser.material_test",
            "laser.focus_test", "laser.interval_test", "laser.print_cut", "laser.center_finder", "-",
            "laser.rotary", "laser.cylinder"])
        self._menu(tr("&Máquina"), [
            "machine.connect", "machine.disconnect", self.menu_devices, "edit.devices", "-",
            "machine.home", "machine.unlock", "machine.set_origin", "machine.clear_origin", "machine.go_origin",
            "machine.read_position", "machine.goto", "machine.positions", "-", "machine.grbl_settings",
            "machine.macros", "machine.console", "machine.diagnostics", "machine.backup_config", "machine.comm_log",
            "-", "machine.estop"])
        self._menu(tr("Cá&mara"), ["machine.camera", "-", "camera.detect", "camera.calibrate_lens",
                                   "camera.calibrate_area", "camera.capture"])
        self.menu_plugins = QMenu(tr("Plugins"), self)
        self._menu(tr("&Herramientas"), [self._tests_menu(), "laser.material_test", "laser.focus_test",
                                         "laser.interval_test", "-", "tools.material_library", "tools.shape_library",
                                         "draw.text_on_path", "-", self.menu_plugins, "edit.plugins", "-",
                                         "tools.sim_door_open", "tools.sim_door_close", "tools.sim_unplug"])
        win = [self.tb_main.toggleViewAction(), self.tb_draw.toggleViewAction(), self.tb_modify.toggleViewAction(),
               self.tb_options.toggleViewAction(), "-"]
        for dock in (self.dock_layers, self.dock_props, self.dock_job, self.dock_motion, self.dock_console,
                     self.dock_sim, self.dock_camera, self.dock_materials, self.dock_history):
            win.append(dock.toggleViewAction())
        win += ["-", "view.zoom_in", "view.zoom_out", "view.fit", "view.center", "-", "view.fullscreen",
                "view.reset_layout"]
        self._menu(tr("&Ventana"), win)
        self._menu(tr("A&yuda"), ["help.safety", "help.shortcuts", "help.licenses", "-", "help.about"])

    def _submenu(self, title: str, ids: list[str]) -> QMenu:
        menu = QMenu(title, self)
        for aid in ids:
            if aid == "-":
                menu.addSeparator()
            else:
                menu.addAction(self.registry[aid])
        return menu

    def _build_statusbar(self) -> None:
        sb = self.statusBar()
        self.sb_state = StateBadge()
        self.sb_state.setMinimumWidth(130)
        self.sb_conn = QLabel()
        self.sb_pos = QLabel()
        self.sb_pos.setProperty("role", "coord")
        self.sb_fs = QLabel()
        self.sb_fs.setProperty("role", "coord")
        self.sb_progress = QProgressBar()
        self.sb_progress.setRange(0, 1000)
        self.sb_progress.setMaximumWidth(160)
        self.sb_progress.setAccessibleName(tr("Progreso del trabajo"))
        self.sb_time = QLabel("--:-- / --:--")
        self.sb_cursor = QLabel()
        self.sb_cursor.setProperty("role", "coord")
        sb.addPermanentWidget(self.sb_rotary)
        for w in (self.sb_cursor, self.sb_conn, self.sb_pos, self.sb_fs, self.sb_progress, self.sb_time, self.sb_state):
            sb.addPermanentWidget(w)

    # =============================================================== conexiones
    def _connect_actions(self) -> None:
        a = self.registry
        c = self
        handlers = {
            "file.open_gcode": c._load_gcode, "file.save_gcode": c._save_gcode,
            "file.import_settings": c._import_settings, "file.export_settings": c._export_settings,
            "file.backup": c._export_settings, "file.restore": c._import_settings, "file.quit": c.close,
            "edit.undo": c.undo.undo, "edit.redo": c.undo.redo,
            "edit.select_all": lambda: c.canvas.view.select_all(True),
            "edit.deselect": lambda: c.canvas.view.select_all(False),
            "edit.invert_selection": c.canvas.view.invert_selection,
            "edit.preferences": c._preferences,
            "edit.devices": c._devices, "edit.machine": c._grbl_settings,
            "view.zoom_in": lambda: c.canvas.view.zoom(1.25), "view.zoom_out": lambda: c.canvas.view.zoom(0.8),
            "view.fit": c.canvas.view.fit_workspace, "view.center": c._center_on_head,
            "view.fullscreen": c._toggle_fullscreen, "view.reset_layout": c._reset_layout,
            "laser.preview": c._preview, "laser.frame": lambda: c._frame(False),
            "laser.frame_contour": lambda: c._frame(True), "laser.start": c._start,
            "laser.pause": lambda: c.controller.pause(), "laser.resume": lambda: c.controller.resume(),
            "laser.stop": lambda: c.controller.stop(),
            "machine.connect": c._connect, "machine.disconnect": c._disconnect,
            "machine.home": lambda: c.controller.home(), "machine.unlock": c._unlock,
            "machine.set_origin": lambda: c.controller.set_origin(),
            "machine.clear_origin": lambda: c.controller.clear_origin(),
            "machine.go_origin": lambda: c.controller.go_to_origin(),
            "machine.read_position": lambda: c.controller.send_line("?"), "machine.goto": c._goto_dialog,
            "machine.positions": lambda: c._show_dock(c.dock_motion), "machine.grbl_settings": c._grbl_settings,
            "machine.macros": c._edit_macros, "machine.console": lambda: c._show_dock(c.dock_console),
            "machine.diagnostics": lambda: info.diagnostics_dialog(c, c.controller).exec(),
            "machine.backup_config": c._grbl_settings, "machine.comm_log": c._export_comm_log,
            "machine.estop": c._estop, "machine.camera": lambda: c._show_dock(c.dock_camera),
            "machine.settings": c._devices,
            "tools.sim_door_open": lambda: c._sim_door(True), "tools.sim_door_close": lambda: c._sim_door(False),
            "tools.sim_unplug": c._sim_unplug,
            "help.safety": lambda: info.safety_dialog(c).exec(),
            "help.shortcuts": lambda: info.shortcuts_dialog(c, c.registry, c.settings.keymap).exec(),
            "help.licenses": lambda: info.licenses_dialog(c).exec(),
            "help.about": lambda: info.about_dialog(c).exec(),
        }
        handlers.update(self._document_handlers())
        handlers.update(self._phase4_handlers())
        for aid, fn in handlers.items():
            a[aid].triggered.connect(lambda _checked=False, f=fn: f())

    def _connect_signals(self) -> None:
        b = self.bridge
        b.status.connect(self._on_status)
        b.console.connect(self._on_console)
        b.alarm.connect(self._on_alarm)
        b.error.connect(self._on_error)
        b.settings.connect(self._on_grbl_settings)
        b.notice.connect(self._on_notice)
        b.progress.connect(self._on_progress)
        b.rejected.connect(lambda ev: self._flash(ev.reason, "warn"))

        bn = self.banner
        bn.pause_clicked.connect(lambda: self.controller.pause())
        bn.resume_clicked.connect(lambda: self.controller.resume())
        bn.stop_clicked.connect(lambda: self.controller.stop())
        bn.estop_clicked.connect(self._estop)
        bn.estop_ack_clicked.connect(self._estop_ack)
        r = self.registry
        bn.bind_machine_actions(r["machine.connect"], r["machine.disconnect"], r["machine.unlock"], r["machine.home"])

        m = self.motion
        m.jog_requested.connect(lambda dx, dy, dz, da: self.controller.jog(dx, dy, dz, da, m.feed.value()))
        m.jog_cont_start.connect(self._jog_cont_start)
        m.jog_cont_stop.connect(self._jog_cont_stop)
        m.home_requested.connect(lambda: self.controller.home())
        m.unlock_requested.connect(self._unlock)
        m.go_origin_requested.connect(lambda: self.controller.go_to_origin())
        m.set_origin_requested.connect(lambda: self.controller.set_origin())
        m.clear_origin_requested.connect(lambda: self.controller.clear_origin())
        m.frame_rect_requested.connect(lambda: self._frame(False))
        m.frame_contour_requested.connect(lambda: self._frame(True))
        m.goto_requested.connect(lambda x, y: self.controller.go_to(x, y))
        m.fire_requested.connect(self._fire)
        m.fire_off_requested.connect(lambda: self.controller.fire_off())
        m.positions_changed.connect(self._save_settings)

        j = self.job
        j.source_changed.connect(self._refresh_analysis)
        j.load_gcode_requested.connect(self._load_gcode)
        j.preview_requested.connect(self._preview)
        j.simulate_requested.connect(lambda: self.registry["laser.simulate"].trigger())
        self.sim_panel.select_shapes.connect(self.canvas.view.set_selection)
        self.sim_panel.rebuild_requested.connect(lambda: self.registry["laser.simulate"].trigger())
        j.frame_requested.connect(lambda: self._frame(False))
        j.start_requested.connect(self._start)
        j.pause_requested.connect(lambda: self.controller.pause())
        j.resume_requested.connect(lambda: self.controller.resume())
        j.stop_requested.connect(lambda: self.controller.stop())
        j.override_requested.connect(lambda code: self.controller.override(code))

        cons = self.console
        cons.send_requested.connect(self._console_send)
        cons.macro_requested.connect(self._run_macro)
        cons.edit_macros_requested.connect(self._edit_macros)
        cons.export_requested.connect(self._export_comm_log)

        self.layers.changed.connect(self._on_document_changed)
        view = self.canvas.view
        view.cursor_moved.connect(self._on_cursor)
        self._connect_document_signals()

    def _install_jog_shortcuts(self) -> None:
        for sc in self._jog_shortcuts:
            sc.setEnabled(False)
            sc.deleteLater()
        self._jog_shortcuts = []
        for key, direction in _JOG_KEYS.items():
            seq = self.settings.keymap.get(key, "")
            if not seq:
                continue
            sc = QShortcut(QKeySequence(seq), self.canvas.view)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=direction: self._key_jog(d))
            self._jog_shortcuts.append(sc)
        self.canvas.view.setToolTip(tr("Con el lienzo activo: Alt+flechas mueven la máquina (Alt+RePág/AvPág el eje Z); "
                                       "las flechas solas desplazan la selección (Mayús = 10 mm)."))

    # ================================================================== perfil
    def _apply_profile(self) -> None:
        p = self.profile
        cs = CoordinateSystem.from_profile(p)
        self.canvas.view.grid_mm = self.settings.grid_mm
        self.canvas.view.set_coordinate_system(cs)
        self.doc.width_mm, self.doc.height_mm = p.width_mm, p.height_mm
        self.canvas.view.set_document(self.doc)
        self.layers.set_document(self.doc)
        self.motion.set_profile(p)
        sim = p.transport == TransportKind.SIMULATOR
        for aid in ("tools.sim_door_open", "tools.sim_door_close", "tools.sim_unplug"):
            self.registry[aid].setVisible(sim)
        self._update_title()
        if hasattr(self, "sb_rotary"):
            self._update_rotary_indicator()
            self.materials_panel.machine_name = p.name
            self.camera._device_changed()  # el perfil de cámara se asocia a la máquina
        self._refresh_analysis()

    def _switch_profile(self, profile_id: str) -> None:
        if self.controller.is_running():
            QMessageBox.information(self, tr("Dispositivo"), tr("Desconecte antes de cambiar de dispositivo."))
            return
        self.settings.active_profile_id = profile_id
        self.profile = self.settings.active_profile()
        self.controller = self._make_controller()
        self.bridge.set_controller(self.controller)
        self._apply_profile()
        self._save_settings()

    def _fill_device_menu(self) -> None:
        self.menu_devices.clear()
        for p in self.settings.profiles:
            act = self.menu_devices.addAction(f"{p.name}  ({p.connection_label()})")
            act.setCheckable(True)
            act.setChecked(p.id == self.profile.id)
            act.setEnabled(not self.controller.is_running())
            act.triggered.connect(lambda _c=False, pid=p.id: self._switch_profile(pid))

    # ============================================================ estado/eventos
    def _on_status(self, st: MachineStatus) -> None:
        self.status = st
        self.allowed = self.controller.allowed()
        self.banner.update_status(st)
        self.motion.update_status(st, self.allowed)
        self.job.update_status(st, self.allowed)
        self.console.set_input_enabled(Action.SEND in self.allowed)
        connected = st.conn == ConnState.CONNECTED
        self.canvas.view.set_head((st.mpos[0], st.mpos[1]), (st.wco[0], st.wco[1]), connected)
        self.sb_state.set_state(st.display)
        if st.conn == ConnState.CONNECTED:
            from openlaser.machine import dialects
            self.sb_conn.setText(f"{dialects.get(st.dialect).name} · {self.profile.connection_label()}")
        else:
            self.sb_conn.setText(self.profile.connection_label() if st.conn != ConnState.DISCONNECTED
                                 else tr("sin conexión"))
        self.sb_conn.setToolTip(st.firmware or st.error or "")
        self.sb_pos.setText("X{:8.3f}  Y{:8.3f}  Z{:7.3f}  A{:7.3f}".format(*st.wpos))
        self.sb_fs.setText(tr("F {f:.0f} mm/min · S {s:.0f}", f=st.feed, s=st.spindle))
        self._update_actions()

    def _update_actions(self) -> None:
        a, r, st = self.allowed, self.registry, self.status
        connected = st.conn != ConnState.DISCONNECTED
        r["machine.connect"].setVisible(not connected)
        r["machine.connect"].setEnabled(Action.CONNECT in a)
        r["machine.disconnect"].setVisible(connected)
        r["machine.disconnect"].setEnabled(Action.DISCONNECT in a)
        mapping = {
            "machine.home": Action.HOME, "machine.unlock": Action.UNLOCK, "machine.set_origin": Action.SET_ORIGIN,
            "machine.clear_origin": Action.SET_ORIGIN, "machine.go_origin": Action.GO_ORIGIN,
            "machine.goto": Action.GOTO, "laser.frame": Action.FRAME, "laser.frame_contour": Action.FRAME,
            "laser.start": Action.START, "laser.pause": Action.PAUSE, "laser.resume": Action.RESUME,
            "laser.stop": Action.STOP, "arrange.laser_to_selection": Action.GOTO,
        }
        for aid, act in mapping.items():
            r[aid].setEnabled(act in a)
        if not self.profile.homing_enabled:
            r["machine.home"].setEnabled(False)
        r["machine.read_position"].setEnabled(st.conn == ConnState.CONNECTED)
        r["machine.estop"].setEnabled(connected)
        sim_ok = self.profile.transport == TransportKind.SIMULATOR and st.conn == ConnState.CONNECTED
        for aid in ("tools.sim_door_open", "tools.sim_door_close", "tools.sim_unplug"):
            r[aid].setEnabled(sim_ok)

    def _on_console(self, ev: ConsoleEvent) -> None:
        self.console.append(ev)

    def _on_alarm(self, code: int) -> None:
        if self._alarm_dialog is not None:
            self._alarm_dialog.close()
        dlg = AlarmDialog(code, self.safety.events(), self, dialect=self.controller.dialect.id)
        dlg.recovery_requested.connect(self._alarm_recovery)
        dlg.setModal(False)
        dlg.show()
        self._alarm_dialog = dlg

    def _alarm_recovery(self, cmd: str) -> None:
        if cmd == "$H":
            self.controller.home()
        elif cmd == "$X":
            self.controller.unlock()

    def _on_error(self, ev: ErrorEvent) -> None:
        self._flash(f"error:{ev.code} — {error_info(ev.code, self.controller.dialect.id).text} ({ev.line})", "warn")

    def _on_grbl_settings(self, values: dict[int, str]) -> None:
        self.grbl_values = dict(values)
        if self._grbl_dialog is not None:
            self._grbl_dialog.populate(values)

    def _on_notice(self, n: SafetyNotice) -> None:
        self.banner.set_message(n.message, n.severity)
        kind = LineKind.ERROR if n.severity == "block" else (LineKind.WARN if n.severity == "warn" else LineKind.INFO)
        self.console.append(ConsoleEvent(kind, n.message, "safety"))

    def _on_progress(self, ev: JobProgressEvent) -> None:
        self.job.update_progress(ev)
        frac = ev.acked / ev.total if ev.total else 0
        self.sb_progress.setValue(int(frac * 1000))
        self.sb_time.setText(f"{format_duration(ev.elapsed)} / {format_duration(ev.remaining)}")
        if ev.state == JobState.COMPLETED:
            self._flash(tr("Trabajo completado en {t}", t=format_duration(ev.elapsed)), "info")

    def _flash(self, text: str, severity: str = "info") -> None:
        self.statusBar().showMessage(text, 8000)
        if severity != "info":
            self.banner.set_message(text, severity)

    def _on_selection(self) -> None:
        cs = CoordinateSystem.from_profile(self.profile)
        self.properties.show_selection(self.doc, cs, self.canvas.view.selected_shape_ids())

    def _on_cursor(self, p: QPointF) -> None:
        cs = CoordinateSystem.from_profile(self.profile)
        mx, my = cs.doc_to_machine((p.x(), p.y()))
        self.sb_cursor.setText(tr("Cursor X{x:.1f} Y{y:.1f}", x=mx, y=my))

    def _on_undo_changed(self) -> None:
        self.registry["edit.undo"].setEnabled(self.undo.can_undo())
        self.registry["edit.redo"].setEnabled(self.undo.can_redo())
        self._on_document_changed()

    def _on_document_changed(self) -> None:
        self._doc_rev += 1
        if self.sim_panel.tl is not None:
            self.sim_panel.clear()  # la simulación mostrada ya no corresponde al documento
        self.canvas.view.set_document(self.doc)
        self._on_selection()
        self._mark_dirty()

    # ================================================================== trabajo
    def _job_inputs(self) -> JobInputs:
        snap = copy.copy(self.doc)
        snap.layers = [copy.copy(ly) for ly in self.doc.layers]
        snap.shapes = list(self.doc.shapes)
        st = self.status
        return JobInputs(self.job.source(), list(self.loaded_gcode) if self.loaded_gcode else None, self.loaded_name,
                         snap, copy.deepcopy(self.profile), (st.wco[0], st.wco[1]), self.job.origin_mode(),
                         self.job.copies.value())

    def _job_key(self, inp: JobInputs) -> tuple:
        """Todo lo que cambia el trabajo: revisión del documento, origen, copias, archivo y perfil."""
        return (self._doc_rev, id(self.doc), inp.source, inp.gcode_name, id(self.loaded_gcode),
                len(inp.gcode or ()), inp.wco, inp.origin, inp.copies, repr(sorted(vars(inp.profile).items())))

    @staticmethod
    def _compute_safe(inp: JobInputs) -> tuple[PreparedJob | None, str]:
        try:
            return compute_job(inp)
        except ValueError as exc:  # p. ej. resolución de imagen excesiva o rotary mal configurado
            return None, tr(str(exc))

    def _prepare_job(self, quiet: bool = False) -> PreparedJob | None:
        inputs = self._job_inputs()
        key = self._job_key(inputs)
        cache = self._job_cache
        fut = self._analysis_future
        if cache is not None and cache[0] == key:
            job, reason = cache[1], cache[2]
        elif fut is not None and self._analysis_key == key and not fut.cancelled():
            # el análisis en segundo plano ya calcula exactamente este trabajo: se espera a que termine
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                job, reason = fut.result()
            except Exception as exc:  # noqa: BLE001
                job, reason = None, str(exc)
            finally:
                QApplication.restoreOverrideCursor()
            self._job_cache = (key, job, reason)
        else:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                job, reason = self._compute_safe(inputs)
            finally:
                QApplication.restoreOverrideCursor()
            self._job_cache = (key, job, reason)
        if job is None and not quiet:
            self._flash(reason, "warn")
        return job

    def _bounds_check(self, a: Analysis) -> tuple[bool, str]:
        b = a.bounds_all
        if b is None:
            return True, ""
        return self.controller.check_bounds_work(rect_frame(b))

    def _refresh_analysis(self) -> None:
        """Recalcula el análisis en segundo plano (los trabajos ráster grandes tardan)."""
        self._analysis_seq += 1
        if self._analysis_future is not None:
            self._analysis_future.cancel()
        try:
            inputs = self._job_inputs()
        except RuntimeError:
            return
        key = self._job_key(inputs)
        cache = self._job_cache
        if cache is not None and cache[0] == key:
            self._analysis_future = None
            self._show_job_analysis(cache[1])  # nada ha cambiado: no se vuelve a calcular
            return
        try:
            self._analysis_future = self._executor.submit(self._compute_safe, inputs)
        except RuntimeError:  # ventana cerrándose: el ejecutor ya está detenido
            self._analysis_future = None
            return
        self._analysis_key = key
        self._analysis_future_seq = self._analysis_seq
        self._pending_timer.start()  # si tarda, el panel indica «Calculando…»
        self._analysis_poll.start()

    def _maybe_show_pending(self) -> None:
        if self._analysis_future is not None and not self._analysis_future.done():
            self.job.show_pending()

    def _poll_analysis(self) -> None:
        fut = self._analysis_future
        if fut is None or not fut.done():
            return
        self._analysis_poll.stop()
        self._analysis_future = None
        if self._analysis_future_seq != self._analysis_seq or fut.cancelled():
            return
        try:
            job, reason = fut.result()
        except Exception as exc:  # noqa: BLE001 - se informa en el panel, nunca rompe la interfaz
            _log.exception("Análisis del trabajo")
            self.job.analysis_label.setText("⚠ " + str(exc))
            return
        if self._analysis_key is not None:
            self._job_cache = (self._analysis_key, job, reason)
        self._show_job_analysis(job)

    def _show_job_analysis(self, job: PreparedJob | None) -> None:
        if job is None:
            self.job.show_analysis(None)
            self.canvas.view.clear_preview()
            self.canvas.view.set_frame([], (0.0, 0.0))
            return
        ok, msg = self._bounds_check(job.analysis)
        self.job.show_analysis(job.analysis, job.skipped, "" if ok else msg, job.warnings)

    def _preview(self) -> None:
        self.sim_panel.clear()
        job = self._prepare_job()
        if job is None:
            return
        wco = (self.status.wco[0], self.status.wco[1])
        self.canvas.view.set_preview(toolpath_segments(job.display_lines), wco)
        if job.analysis.frame_points:
            self.canvas.view.set_frame(job.analysis.frame_points, wco)
        ok, msg = self._bounds_check(job.analysis)
        self.job.show_analysis(job.analysis, job.skipped, "" if ok else msg, job.warnings)
        self._flash(tr("Vista previa: rojo = corte, discontinuo = desplazamiento sin láser"))

    def _frame(self, contour: bool) -> None:
        job = self._prepare_job()
        if job is None:
            return
        a = job.analysis
        if contour and a.frame_points:
            pts = a.frame_points
        else:
            b = a.bounds_laser or a.bounds_all
            if b is None:
                self._flash(tr("No hay geometría para el frame"), "warn")
                return
            pts = rect_frame(b)
        self.canvas.view.set_frame(pts, (self.status.wco[0], self.status.wco[1]))
        self.controller.frame(pts, self.profile.frame_feed_mm_min)

    def _start(self) -> None:
        job = self._prepare_job()
        if job is None:
            return
        st = self.status
        a = job.analysis
        ok, msg = self._bounds_check(a)
        issues = self.safety.preflight(
            connected=st.conn == ConnState.CONNECTED, idle=st.grbl in (GrblState.IDLE, GrblState.CHECK),
            estop=st.estop, door_open=st.door_open, homed=st.homed, homing_enabled=self.profile.homing_enabled,
            bounds_ok=ok, bounds_message=msg, grbl_settings=self.controller.settings, s_max_profile=self.profile.s_max,
            max_s_in_job=a.max_s, has_system_commands=a.has_system_commands)
        summary = tr("{n} líneas · tiempo estimado {t} · copias {c}", n=a.line_count,
                     t=format_duration(a.estimated_seconds), c=self.job.copies.value())
        dlg = PreflightDialog(job.name, summary, issues, self.settings.safety.checklist,
                              self.settings.safety.require_checklist, self)
        if not dlg.exec():
            return
        self.controller.start_job(job.lines, job.name, a.cumulative_seconds)
        self.sb_progress.setValue(0)

    def _load_gcode(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Abrir G-code"), "",
                                              tr("G-code (*.gcode *.nc *.gc *.ngc *.tap *.txt);;Todos (*)"))
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, tr("Abrir G-code"), str(exc))
            return
        self.loaded_gcode = text.splitlines()
        self.loaded_name = Path(path).name
        self.job.set_file(f"{self.loaded_name} ({len(self.loaded_gcode)} {tr('líneas')})")
        recent = [path] + [p for p in self.settings.recent_gcode if p != path]
        self.settings.recent_gcode = recent[:10]
        self._refresh_analysis()
        a = analyze(prepare_lines(self.loaded_gcode))
        if a.unsupported:
            QMessageBox.information(self, tr("Importación de G-code"),
                                    tr("{n} líneas no se reconocen y se enviarán tal cual; GRBL podría rechazarlas.\n"
                                       "Primera: línea {l}: {t}", n=len(a.unsupported), l=a.unsupported[0][0],
                                       t=a.unsupported[0][1]))

    def _save_gcode(self) -> None:
        job = self._prepare_job()
        if job is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar G-code"), f"{job.name}.gcode", "G-code (*.gcode *.nc)")
        if path:
            header = [f"; {app_name()} — {job.name}", f"; {tr('Tiempo estimado')}: {format_duration(job.analysis.estimated_seconds)}"]
            atomic_write_text(path, "\n".join(header + job.lines) + "\n")
            self._flash(tr("G-code guardado en {p}", p=path))

    # =================================================================== máquina
    def _connect(self) -> None:
        from openlaser.machine.factory import matches
        if not matches(self.controller, self.profile) and not self.controller.is_running():
            # el firmware del perfil cambió: GRBL/grblHAL/FluidNC y Marlin/Smoothieware usan controladores distintos
            self.controller = self._make_controller()
            self.bridge.set_controller(self.controller)
        self.controller.connect()

    def _disconnect(self) -> None:
        if self.status.job in (JobState.RUNNING, JobState.PAUSED):
            if QMessageBox.question(self, tr("Desconectar"),
                                    tr("Hay un trabajo en curso. Desconectar lo detendrá con un reinicio. ¿Continuar?")) \
                    != QMessageBox.StandardButton.Yes:
                return
        self.controller.disconnect()

    def _unlock(self) -> None:
        text = tr("Desbloquear ($X) ignora la alarma sin verificar la posición de la máquina.\n"
                  "Hágalo sólo si conoce la causa y la máquina está despejada. ¿Desbloquear?")
        if QMessageBox.question(self, tr("Desbloquear alarma"), text) == QMessageBox.StandardButton.Yes:
            self.controller.unlock()

    def _estop(self) -> None:
        self.controller.emergency_stop()
        self._flash(tr("PARADA DE SOFTWARE enviada. Verifique la máquina y confirme para continuar."), "block")

    def _estop_ack(self) -> None:
        if QMessageBox.question(self, tr("Confirmar parada"),
                                tr("¿Ha comprobado que la máquina está en estado seguro y el láser apagado?")) \
                == QMessageBox.StandardButton.Yes:
            self.controller.acknowledge_estop()

    def _fire(self, percent: float, seconds: float) -> None:
        dlg = FireDialog(percent, seconds, self.profile.s_from_percent(percent), self)
        if dlg.exec():
            self.controller.fire(percent, seconds, confirmed=True)

    def _jog_cont_start(self, dx: float, dy: float, dz: float, da: float) -> None:
        if self.controller.jog_continuous((dx, dy, dz, da), self.motion.feed.value()):
            self._keepalive.start()

    def _jog_cont_stop(self) -> None:
        self._keepalive.stop()
        self.controller.jog_stop()

    def _key_jog(self, d: tuple[float, float, float, float]) -> None:
        if Action.JOG in self.allowed:
            self.motion.step_jog(d)

    def _goto_dialog(self) -> None:
        dlg = info.GotoDialog(self.status.wpos[0], self.status.wpos[1], self)
        if dlg.exec():
            self.controller.go_to(dlg.spin_x.value(), dlg.spin_y.value())


    def _console_send(self, text: str) -> None:
        risk = self.safety.classify_command(text)
        confirmed = False
        if risk.level == Risk.DANGER:
            msg = tr("El comando «{c}» es peligroso:\n\n{r}\n\n¿Enviarlo?", c=text, r="\n".join("• " + x for x in risk.reasons))
            if QMessageBox.warning(self, tr("Confirmar comando"), msg,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            confirmed = True
        self.controller.send_line(text, confirmed=confirmed)

    def _run_macro(self, macro: Macro) -> None:
        risky = [ln for ln in macro.lines if self.safety.classify_command(ln).level == Risk.DANGER]
        confirmed = False
        if risky:
            msg = tr("La macro «{n}» contiene comandos peligrosos:\n{l}\n¿Ejecutarla?", n=macro.name, l="\n".join(risky))
            if QMessageBox.warning(self, tr("Confirmar macro"), msg,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            confirmed = True
        self.controller.run_macro(macro.lines, confirmed=confirmed)

    def _sim_door(self, open_: bool) -> None:
        self._simulator.set_door(open_)

    def _sim_unplug(self) -> None:
        t = self._last_transport
        if isinstance(t, SimulatorTransport):
            t.fail()

    # ================================================================ diálogos


    def _preferences(self) -> None:
        old_lang, old_theme = self.settings.language, self.settings.theme
        old_icons = self.settings.color_icons
        dlg = cfg.PreferencesDialog(self.settings, self)
        if not dlg.exec():
            return
        self.undo.limit = self.settings.undo_limit
        self.canvas.view.grid_mm = self.settings.grid_mm
        self.canvas.view.viewport().update()
        self.motion.apply_safety_limits()
        self._install_jog_shortcuts()
        style = (Qt.ToolButtonStyle.ToolButtonTextUnderIcon if self.settings.show_icon_text
                 else Qt.ToolButtonStyle.ToolButtonIconOnly)
        for tb in (self.tb_main, self.tb_draw, self.tb_modify):
            tb.setToolButtonStyle(style)
        if self.settings.theme != old_theme or self.settings.color_icons != old_icons:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if isinstance(app, QApplication) and self.settings.theme != old_theme:
                theme.apply_theme(app, self.settings.theme)
            icons.set_color_mode(self.settings.color_icons)
            icons.clear_cache()
            for aid, spec in self.registry.specs.items():
                if spec.icon:
                    self.registry[aid].setIcon(icons.icon(spec.icon))
            self.canvas.view.apply_theme()
        self._save_settings()
        if self.settings.language != old_lang:
            QMessageBox.information(self, tr("Idioma"), tr("El idioma se aplicará al reiniciar la aplicación."))

    def _devices(self) -> None:
        dlg = cfg.DeviceManagerDialog(self.settings, self.controller.is_running(), self)
        if not dlg.exec():
            return
        self.settings.profiles = dlg.profiles
        if self.controller.is_running():
            self.settings.active_profile_id = self.profile.id
            for p in dlg.profiles:
                if p.id == self.profile.id:
                    self.profile.__dict__.update(p.__dict__)
            self._flash(tr("Cambios guardados; los de conexión se aplicarán al reconectar."))
            self._save_settings()
            return
        self._switch_profile(dlg.active_id)

    def _grbl_settings(self) -> None:
        if self._grbl_dialog is None:
            self._grbl_dialog = cfg.GrblSettingsDialog(
                self.grbl_values or self.controller.settings,
                writer=lambda n, v: self.controller.write_setting(n, v, confirmed=True),
                reader=lambda: bool(self.controller.read_settings()), parent=self,
                writable=self.controller.settings_writable, firmware=self.controller.dialect.name)
            self._grbl_dialog.finished.connect(lambda _r: setattr(self, "_grbl_dialog", None))
        self._grbl_dialog.show()
        self._grbl_dialog.raise_()

    def _edit_macros(self) -> None:
        dlg = cfg.MacroEditorDialog(self.settings.macros, self)
        if dlg.exec():
            self.settings.macros = dlg.macros
            self.console.reload_macros()
            self._save_settings()

    def _export_comm_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("Exportar registro"), "openlaser_registro.tsv",
                                              tr("Texto (*.tsv *.txt)"))
        if path:
            events = "\n".join(f"{e.timestamp:.3f}\t{e.kind}\t{e.message}" for e in self.safety.events())
            atomic_write_text(path, self.console.export_text() + "\n# Eventos de seguridad\n" + events + "\n")
            self._flash(tr("Registro exportado en {p}", p=path))

    def _export_settings(self) -> None:
        self._capture_window()
        cfg.export_settings_file(self, self.settings)

    def _import_settings(self) -> None:
        new = cfg.import_settings_file(self)
        if new is None:
            return
        if self.controller.is_running():
            QMessageBox.information(self, tr("Importar configuración"), tr("Desconecte antes de importar."))
            return
        for field in ("profiles", "macros", "saved_positions", "keymap", "grid_mm", "jog_step_mm", "jog_feed_mm_min"):
            setattr(self.settings, field, getattr(new, field))
        self.settings.safety.__dict__.update(new.safety.__dict__)
        self.settings.active_profile_id = new.active_profile_id
        self.console.reload_macros()
        self._switch_profile(self.settings.active_profile().id)
        self._flash(tr("Configuración importada"))

    # ================================================================= ventana
    def _show_dock(self, dock: QDockWidget) -> None:
        dock.show()
        dock.raise_()

    def _center_on_head(self) -> None:
        cs = CoordinateSystem.from_profile(self.profile)
        self.canvas.view.center_on_doc(cs.machine_to_doc((self.status.mpos[0], self.status.mpos[1])))

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _reset_layout(self) -> None:
        self.restoreState(self._default_state)
        self.settings.window_state = ""
        QTimer.singleShot(0, self._initial_sizes)
        self.dock_camera.hide()
        self.dock_history.hide()
        for tb in (self.tb_main, self.tb_draw, self.tb_modify):
            tb.show()

    def _initial_sizes(self) -> None:
        if not self.settings.window_state:
            self.resizeDocks([self.dock_motion], [340], Qt.Orientation.Horizontal)
            self.resizeDocks([self.dock_layers], [470], Qt.Orientation.Horizontal)
            self.resizeDocks([self.dock_console], [200], Qt.Orientation.Vertical)
        self.canvas.view.fit_workspace()

    def _restore_window(self) -> None:
        try:
            if self.settings.window_geometry:
                self.restoreGeometry(QByteArray.fromHex(self.settings.window_geometry.encode()))
            else:
                self.resize(1500, 920)
            if self.settings.window_state:
                self.restoreState(QByteArray.fromHex(self.settings.window_state.encode()))
        except (ValueError, TypeError):
            _log.warning("No se pudo restaurar la distribución de la ventana")

    def _capture_window(self) -> None:
        self.settings.window_geometry = bytes(self.saveGeometry().toHex().data()).decode()
        self.settings.window_state = bytes(self.saveState().toHex().data()).decode()

    def _save_settings(self) -> None:
        try:
            self.settings.jog_feed_mm_min = self.motion.feed.value()
            self.settings.jog_continuous = self.motion.continuous.isChecked()
            save_settings(self.settings_path, self.settings)
        except OSError:
            _log.exception("No se pudieron guardar las preferencias")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.status.job in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            if QMessageBox.question(self, tr("Salir"), tr("Hay un trabajo en curso. Salir lo detendrá. ¿Salir?")) \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        if not self._maybe_save():
            event.ignore()
            return
        self._keepalive.stop()
        self._autosave_timer.stop()
        if self.interactive:
            self._discard_autosave()
        self.controller.shutdown()
        self.sim_panel.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.camera.timer.stop()
        self.camera.source.close()  # libera la cámara real
        self._capture_window()
        self._save_settings()
        event.accept()

    # ============================================================ arrastrar y soltar
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        from openlaser.importers import UnsupportedFormat, apply_import, is_image, read_file
        from openlaser.persistence.project import EXTENSION
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        projects = [p for p in paths if p.lower().endswith(EXTENSION)]
        if projects:
            self._open_project(projects[0])
            return
        ids: list[str] = []
        for path in paths:
            if path.lower().endswith((".gcode", ".nc", ".gc", ".ngc", ".tap")):
                self._flash(tr("Use Archivo → Abrir G-code para cargar archivos G-code"), "warn")
                continue
            if is_image(path):
                sid = self._insert_image(path, select=False)
                if sid:
                    ids.append(sid)
                continue
            try:
                result = read_file(path)
            except (UnsupportedFormat, ValueError, OSError) as exc:
                self._flash(str(exc), "warn")
                continue
            summary = self._edit(tr("Importar"), lambda r=result: apply_import(self.doc, r, self._cs()))
            ids += summary.shape_ids
            for w in summary.warnings[:3]:
                self._flash(w, "warn")
        if ids:
            self.canvas.view.set_selection(ids)
        event.acceptProposedAction()
