"""Panel permanente de movimiento manual.

Ningún control de este panel enciende el láser salvo el bloque «Disparo de
enfoque», que exige confirmación explícita, potencia limitada y temporizador.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDoubleSpinBox, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from openlaser.devices.profile import DeviceProfile
from openlaser.i18n import tr
from openlaser.machine.events import MachineStatus
from openlaser.machine.states import Action
from openlaser.persistence.settings import AppSettings, SavedPosition
from openlaser.ui import icons

STEPS = (0.1, 1.0, 10.0, 50.0, 100.0)

# (texto, dirección x, y, nombre accesible)
_JOG_GRID = [
    ("jog_nw", -1, 1, "X− Y+"), ("jog_n", 0, 1, "Y+"), ("jog_ne", 1, 1, "X+ Y+"),
    ("jog_w", -1, 0, "X−"), (None, 0, 0, ""), ("jog_e", 1, 0, "X+"),
    ("jog_sw", -1, -1, "X− Y−"), ("jog_s", 0, -1, "Y−"), ("jog_se", 1, -1, "X+ Y−"),
]


class MotionPanel(QScrollArea):
    jog_requested = Signal(float, float, float, float)          # incremento en mm
    jog_cont_start = Signal(float, float, float, float)         # dirección
    jog_cont_stop = Signal()
    home_requested = Signal()
    unlock_requested = Signal()
    go_origin_requested = Signal()
    set_origin_requested = Signal()
    clear_origin_requested = Signal()
    frame_rect_requested = Signal()
    frame_contour_requested = Signal()
    goto_requested = Signal(float, float)
    fire_requested = Signal(float, float)
    fire_off_requested = Signal()
    positions_changed = Signal()

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWidgetResizable(True)
        self.setMinimumWidth(330)
        body = QWidget()
        body.setMinimumWidth(300)
        self.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(6, 6, 6, 6)
        self._enabled_actions: set[Action] = set()
        self._status = MachineStatus()
        self._profile: DeviceProfile | None = None
        self._cont_active = False

        # --- posición -------------------------------------------------------
        pos_box = QGroupBox(tr("Posición (trabajo / máquina)"))
        pg = QGridLayout(pos_box)
        self.pos_labels: dict[str, tuple[QLabel, QLabel]] = {}
        for row, axis in enumerate("XYZA"):
            name = QLabel(axis)
            name.setProperty("role", "big-coord")
            work = QLabel("0.000")
            work.setProperty("role", "big-coord")
            mach = QLabel("0.000")
            mach.setProperty("role", "coord")
            for lab in (work, mach):
                lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            work.setAccessibleName(tr("Posición de trabajo {a}", a=axis))
            mach.setAccessibleName(tr("Posición de máquina {a}", a=axis))
            pg.addWidget(name, row, 0)
            pg.addWidget(work, row, 1)
            pg.addWidget(mach, row, 2)
            self.pos_labels[axis] = (work, mach)
        root.addWidget(pos_box)

        # --- jog -----------------------------------------------------------
        jog_box = QGroupBox(tr("Movimiento manual"))
        jv = QVBoxLayout(jog_box)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.jog_buttons: list[QPushButton] = []
        for idx, (text, dx, dy, acc) in enumerate(_JOG_GRID):
            r, c = divmod(idx, 3)
            if text is None:
                b = QPushButton(icons.icon("origin_go"), "")
                b.setToolTip(tr("Ir al origen de trabajo (G0, láser apagado)"))
                b.setAccessibleName(tr("Ir al origen de trabajo"))
                b.setProperty("role", "jog")
                b.setIconSize(QSize(20, 20))
                b.clicked.connect(lambda: self.go_origin_requested.emit())
                self.btn_center = b
            else:
                b = QPushButton(icons.icon(text), "")  # flecha a color (cian)
                b.setIconSize(QSize(22, 22))
                b.setProperty("role", "jog")
                b.setToolTip(tr("Mover {a}", a=acc))
                b.setAccessibleName(tr("Mover {a}", a=acc))
                self._wire_jog(b, (float(dx), float(dy), 0.0, 0.0))
                self.jog_buttons.append(b)
            grid.addWidget(b, r, c)
        # Z y A
        self.btn_zp = self._axis_button("Z+", (0, 0, 1, 0), "jog_zp")
        self.btn_zm = self._axis_button("Z−", (0, 0, -1, 0), "jog_zm")
        self.btn_ap = self._axis_button("A+", (0, 0, 0, 1), "jog_ap")
        self.btn_am = self._axis_button("A−", (0, 0, 0, -1), "jog_am")
        grid.addWidget(self.btn_zp, 0, 4)
        grid.addWidget(self.btn_zm, 2, 4)
        grid.addWidget(self.btn_ap, 0, 5)
        grid.addWidget(self.btn_am, 2, 5)
        jv.addLayout(grid)

        steps_row = QHBoxLayout()
        self.step_group = QButtonGroup(self)
        self.step_group.setExclusive(True)
        for s in STEPS:
            chip = QPushButton(f"{s:g}")
            chip.setCheckable(True)
            chip.setProperty("role", "chip")
            chip.setToolTip(tr("Paso de {s} mm", s=f"{s:g}"))
            chip.clicked.connect(lambda _c=False, v=s: self.step.setValue(v))
            self.step_group.addButton(chip)
            steps_row.addWidget(chip)
        jv.addLayout(steps_row)

        form = QGridLayout()
        form.addWidget(QLabel(tr("Paso (mm)")), 0, 0)
        self.step = QDoubleSpinBox()
        self.step.setRange(0.01, 1000.0)
        self.step.setDecimals(2)
        self.step.setValue(settings.jog_step_mm)
        self.step.setAccessibleName(tr("Distancia por paso"))
        self.step.valueChanged.connect(self._sync_step_chips)
        form.addWidget(self.step, 0, 1)
        form.addWidget(QLabel(tr("Velocidad (mm/min)")), 1, 0)
        self.feed = QDoubleSpinBox()
        self.feed.setRange(10.0, 50000.0)
        self.feed.setDecimals(0)
        self.feed.setValue(settings.jog_feed_mm_min)
        self.feed.setAccessibleName(tr("Velocidad de movimiento"))
        form.addWidget(self.feed, 1, 1)
        self.continuous = QCheckBox(tr("Continuo (mantener pulsado)"))
        self.continuous.setToolTip(tr("Incremental: un paso por clic. Continuo: se mueve mientras se mantiene pulsado."))
        self.continuous.setChecked(settings.jog_continuous)
        form.addWidget(self.continuous, 2, 0, 1, 2)
        jv.addLayout(form)
        self._sync_step_chips(self.step.value())

        mach = QGridLayout()
        self.btn_home = self._tool_button("home", tr("Home"), tr("Ciclo de homing ($H)"), self.home_requested)
        self.btn_unlock = self._tool_button("unlock", tr("Desbloquear"), tr("Desbloquear alarma ($X) tras confirmar"),
                                            self.unlock_requested)
        self.btn_set_origin = self._tool_button("origin_set", tr("Fijar origen"), tr("Establecer origen de trabajo aquí"),
                                                self.set_origin_requested)
        self.btn_clear_origin = self._tool_button("origin_clear", tr("Borrar origen"), tr("Borrar origen de trabajo"),
                                                  self.clear_origin_requested)
        self.btn_frame = self._tool_button("frame", tr("Frame"), tr("Frame rectangular con láser apagado"),
                                           self.frame_rect_requested)
        self.btn_frame_contour = self._tool_button("frame", tr("Contorno"), tr("Frame por contorno real con láser apagado"),
                                                   self.frame_contour_requested)
        for i, b in enumerate((self.btn_home, self.btn_unlock, self.btn_set_origin, self.btn_clear_origin,
                               self.btn_frame, self.btn_frame_contour)):
            mach.addWidget(b, i // 2, i % 2)
        jv.addLayout(mach)
        root.addWidget(jog_box)

        # --- ir a ----------------------------------------------------------
        goto_box = QGroupBox(tr("Ir a X/Y (trabajo)"))
        gl = QGridLayout(goto_box)
        self.goto_x = QDoubleSpinBox()
        self.goto_y = QDoubleSpinBox()
        for w, name in ((self.goto_x, "X"), (self.goto_y, "Y")):
            w.setRange(-10000, 10000)
            w.setDecimals(3)
            w.setAccessibleName(tr("Destino {a}", a=name))
        gl.addWidget(QLabel("X"), 0, 0)
        gl.addWidget(self.goto_x, 0, 1)
        gl.addWidget(QLabel("Y"), 0, 2)
        gl.addWidget(self.goto_y, 0, 3)
        self.btn_goto = QPushButton(icons.icon("goto"), tr("Ir a X/Y"))
        self.btn_goto.setToolTip(tr("Movimiento G0 con láser apagado"))
        self.btn_goto.clicked.connect(lambda: self.goto_requested.emit(self.goto_x.value(), self.goto_y.value()))
        gl.addWidget(self.btn_goto, 1, 0, 1, 4)
        root.addWidget(goto_box)

        # --- posiciones guardadas ------------------------------------------------
        saved_box = QGroupBox(tr("Posiciones guardadas"))
        sv = QVBoxLayout(saved_box)
        self.saved_list = QListWidget()
        self.saved_list.setMaximumHeight(110)
        self.saved_list.setAccessibleName(tr("Lista de posiciones guardadas"))
        self.saved_list.itemDoubleClicked.connect(lambda _it: self._go_saved())
        sv.addWidget(self.saved_list)
        sr = QHBoxLayout()
        self.btn_save_pos = QPushButton(icons.icon("save"), tr("Guardar actual"))
        self.btn_save_pos.clicked.connect(self._save_position)
        self.btn_go_pos = QPushButton(icons.icon("goto"), tr("Ir"))
        self.btn_go_pos.clicked.connect(self._go_saved)
        self.btn_del_pos = QPushButton(icons.icon("delete"), "")
        self.btn_del_pos.setToolTip(tr("Eliminar posición"))
        self.btn_del_pos.setAccessibleName(tr("Eliminar posición"))
        self.btn_del_pos.clicked.connect(self._delete_saved)
        for b in (self.btn_save_pos, self.btn_go_pos, self.btn_del_pos):
            sr.addWidget(b)
        sv.addLayout(sr)
        root.addWidget(saved_box)
        self._reload_saved()

        # --- disparo de enfoque ------------------------------------------------
        fire_box = QGroupBox(tr("Disparo de enfoque (baja potencia)"))
        fl = QGridLayout(fire_box)
        fl.addWidget(QLabel(tr("Potencia (%)")), 0, 0)
        self.fire_power = QDoubleSpinBox()
        self.fire_power.setDecimals(1)
        self.fire_power.setSingleStep(0.1)
        fl.addWidget(self.fire_power, 0, 1)
        fl.addWidget(QLabel(tr("Duración (s)")), 1, 0)
        self.fire_seconds = QDoubleSpinBox()
        self.fire_seconds.setDecimals(1)
        self.fire_seconds.setSingleStep(0.5)
        fl.addWidget(self.fire_seconds, 1, 1)
        self.btn_fire = QPushButton(icons.icon_on("fire"), tr("Disparar…"))
        self.btn_fire.setProperty("role", "danger")
        self.btn_fire.setToolTip(tr("Pide confirmación. Se apaga solo al terminar el tiempo."))
        self.btn_fire.clicked.connect(lambda: self.fire_requested.emit(self.fire_power.value(), self.fire_seconds.value()))
        self.btn_fire_off = QPushButton(icons.icon("stop"), tr("Apagar"))
        self.btn_fire_off.clicked.connect(lambda: self.fire_off_requested.emit())
        fl.addWidget(self.btn_fire, 2, 0)
        fl.addWidget(self.btn_fire_off, 2, 1)
        root.addWidget(fire_box)
        self.apply_safety_limits()
        root.addStretch(1)


    # --- construcción -------------------------------------------------------------
    def _axis_button(self, text: str, direction: tuple[float, float, float, float], icon_name: str) -> QPushButton:
        b = QPushButton(icons.icon(icon_name), text)  # Z en violeta, A en rosa
        b.setIconSize(QSize(16, 16))
        b.setProperty("role", "jog")
        b.setToolTip(tr("Mover {a}", a=text))
        b.setAccessibleName(tr("Mover {a}", a=text))
        self._wire_jog(b, direction)
        return b

    def _tool_button(self, icon_name: str, text: str, tip: str, signal: object) -> QPushButton:
        b = QPushButton(icons.icon(icon_name), text)
        b.setToolTip(tip)
        b.setAccessibleName(text)
        b.clicked.connect(lambda: signal.emit())  # type: ignore[attr-defined]
        return b

    def _wire_jog(self, b: QPushButton, direction: tuple[float, float, float, float]) -> None:
        b.pressed.connect(lambda d=direction: self._jog_pressed(d))
        b.released.connect(self._jog_released)
        b.clicked.connect(lambda _c=False, d=direction: self._jog_clicked(d))

    # --- jog ----------------------------------------------------------------------
    def _jog_pressed(self, d: tuple[float, float, float, float]) -> None:
        if self.continuous.isChecked():
            self._cont_active = True
            self.jog_cont_start.emit(*d)

    def _jog_released(self) -> None:
        if self._cont_active:
            self._cont_active = False
            self.jog_cont_stop.emit()

    def _jog_clicked(self, d: tuple[float, float, float, float]) -> None:
        if self.continuous.isChecked():
            return
        s = self.step.value()
        self.jog_requested.emit(d[0] * s, d[1] * s, d[2] * s, d[3] * s)

    def step_jog(self, d: tuple[float, float, float, float]) -> None:
        """Paso incremental desde el teclado."""
        s = self.step.value()
        self.jog_requested.emit(d[0] * s, d[1] * s, d[2] * s, d[3] * s)

    def _sync_step_chips(self, value: float) -> None:
        for b in self.step_group.buttons():
            b.setChecked(abs(float(b.text()) - value) < 1e-9)
        self.settings.jog_step_mm = value

    # --- posiciones ---------------------------------------------------------------
    def _reload_saved(self) -> None:
        self.saved_list.clear()
        for pos in self.settings.saved_positions:
            item = QListWidgetItem(f"{pos.name}  —  X{pos.x:.3f}  Y{pos.y:.3f}")
            self.saved_list.addItem(item)

    def _save_position(self) -> None:
        name, ok = QInputDialog.getText(self, tr("Guardar posición"), tr("Nombre de la posición:"),
                                        text=tr("Posición {n}", n=len(self.settings.saved_positions) + 1))
        if not ok or not name.strip():
            return
        x, y = self._status.wpos[0], self._status.wpos[1]
        self.settings.saved_positions.append(SavedPosition(name.strip(), round(x, 3), round(y, 3)))
        self._reload_saved()
        self.positions_changed.emit()

    def _go_saved(self) -> None:
        row = self.saved_list.currentRow()
        if 0 <= row < len(self.settings.saved_positions):
            pos = self.settings.saved_positions[row]
            self.goto_requested.emit(pos.x, pos.y)

    def _delete_saved(self) -> None:
        row = self.saved_list.currentRow()
        if 0 <= row < len(self.settings.saved_positions):
            del self.settings.saved_positions[row]
            self._reload_saved()
            self.positions_changed.emit()

    # --- estado -------------------------------------------------------------------
    def apply_safety_limits(self) -> None:
        cfg = self.settings.safety
        self.fire_power.setRange(0.1, cfg.max_fire_percent)
        self.fire_power.setValue(min(max(self.fire_power.value(), 0.5), cfg.max_fire_percent))
        self.fire_seconds.setRange(0.2, cfg.max_fire_seconds)
        self.fire_seconds.setValue(min(max(self.fire_seconds.value(), 1.0), cfg.max_fire_seconds))

    def set_profile(self, profile: DeviceProfile) -> None:
        self._profile = profile
        self.feed.setMaximum(max(profile.max_feed_mm_min, profile.jog_feed_mm_min))
        self._apply_enabled()

    def update_status(self, st: MachineStatus, allowed: set[Action]) -> None:
        self._status = st
        self._enabled_actions = allowed
        for axis, w, m in zip("XYZA", st.wpos, st.mpos):
            work, mach = self.pos_labels[axis]
            work.setText(f"{w:.3f}")
            mach.setText(f"{m:.3f}")
        self._apply_enabled()

    def _apply_enabled(self) -> None:
        a = self._enabled_actions
        prof = self._profile
        jog = Action.JOG in a
        for b in self.jog_buttons:
            b.setEnabled(jog)
        z_ok = bool(prof and prof.z_axis)
        a_ok = bool(prof and (prof.a_axis or prof.rotary_enabled))
        self.btn_zp.setEnabled(jog and z_ok)
        self.btn_zm.setEnabled(jog and z_ok)
        self.btn_ap.setEnabled(jog and a_ok)
        self.btn_am.setEnabled(jog and a_ok)
        for b in (self.btn_zp, self.btn_zm):
            b.setToolTip(tr("Mover {a}", a=b.text()) if z_ok else tr("El perfil no tiene eje Z"))
        for b in (self.btn_ap, self.btn_am):
            b.setToolTip(tr("Mover {a}", a=b.text()) if a_ok else tr("Eje A/rotativo no habilitado en el perfil"))
        self.pos_labels["Z"][0].setEnabled(z_ok)
        self.pos_labels["A"][0].setEnabled(a_ok)
        self.btn_center.setEnabled(Action.GO_ORIGIN in a)
        self.btn_home.setEnabled(Action.HOME in a and bool(prof and prof.homing_enabled))
        self.btn_unlock.setEnabled(Action.UNLOCK in a)
        self.btn_set_origin.setEnabled(Action.SET_ORIGIN in a)
        self.btn_clear_origin.setEnabled(Action.SET_ORIGIN in a)
        self.btn_frame.setEnabled(Action.FRAME in a)
        self.btn_frame_contour.setEnabled(Action.FRAME in a)
        self.btn_goto.setEnabled(Action.GOTO in a)
        self.btn_go_pos.setEnabled(Action.GOTO in a)
        self.btn_fire.setEnabled(Action.FIRE in a)
        self.btn_fire_off.setEnabled(self._status.fire_active)
        if not jog and self._cont_active:
            self._jog_released()
