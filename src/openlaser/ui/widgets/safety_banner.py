"""Franja de seguridad permanente sobre el lienzo (no se puede ocultar).

Muestra estado de máquina, estado del láser, homing, puerta, conexión,
coordenadas X/Y/Z/A, el último aviso de seguridad, los botones Conectar,
Desbloquear y Home, y Pausa, Reanudar, Detener y la PARADA DE SOFTWARE,
claramente diferenciada.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from openlaser.i18n import tr
from openlaser.machine.events import MachineStatus
from openlaser.machine.states import ConnState, DisplayState
from openlaser.ui import icons, theme
from openlaser.ui.widgets.state_badge import Pill, StateBadge


class SafetyBanner(QFrame):
    pause_clicked = Signal()
    resume_clicked = Signal()
    stop_clicked = Signal()
    estop_clicked = Signal()
    estop_ack_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "banner")
        self.setAccessibleName(tr("Indicador de seguridad"))
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 4, 8, 4)
        root.setSpacing(2)
        row = QHBoxLayout()
        row.setSpacing(8)
        root.addLayout(row)

        self.state = StateBadge()
        self.laser = Pill()
        self.laser.setMinimumWidth(160)
        self.homed = Pill()
        self.door = Pill()
        self.conn = QLabel()
        self.conn.setProperty("role", "muted")
        self.conn.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.conn.setMinimumWidth(40)
        self.conn.hide()  # firmware y último error: en la barra de estado (tooltip) y en los avisos
        for w in (self.state, self.laser, self.homed, self.door):
            row.addWidget(w)
        row.addStretch(1)

        self.coords: dict[str, QLabel] = {}
        for axis in "XYZA":
            lab = QLabel(f"{axis} 0.000")
            lab.setProperty("role", "big-coord")
            lab.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            mono = lab.font()
            mono.setFamilies(["Consolas", "DejaVu Sans Mono", "monospace"])
            mono.setPixelSize(16)
            mono.setBold(True)
            lab.setMinimumWidth(QFontMetrics(mono).horizontalAdvance("X -0000.000") + 8)
            lab.setAccessibleName(tr("Coordenada {a}", a=axis))
            self.coords[axis] = lab
            row.addWidget(lab)

        def btn(text: str, icon_name: str, tip: str, role: str | None = None) -> QPushButton:
            # sobre fondo rojo (Detener, PARADA) el icono va en blanco; el resto, a color
            b = QPushButton(icons.icon_on(icon_name) if role in ("danger", "estop") else icons.icon(icon_name), text)
            b.setToolTip(tip)
            b.setAccessibleName(text)
            b.setIconSize(QSize(18, 18))
            if role:
                b.setProperty("role", role)
            return b

        self.btn_pause = btn(tr("Pausa"), "pause", tr("Retención de avance (feed hold)"))
        self.btn_resume = btn(tr("Reanudar"), "resume", tr("Reanuda tras una pausa"))
        self.btn_stop = btn(tr("Detener"), "stop", tr("Detiene el trabajo y vacía la cola (Esc)"), "danger")
        self.btn_estop = btn(tr("PARADA SOFTWARE"), "estop",
                             tr("Reinicio inmediato del controlador (Ctrl+Mayús+Espacio). "
                                "NO sustituye la seta de emergencia física."), "estop")
        self.btn_estop.setMinimumHeight(38)
        self.btn_ack = btn(tr("Confirmar parada"), "unlock", tr("Confirma que la situación es segura para continuar"))
        self.btn_ack.hide()
        self.btn_pause.clicked.connect(lambda: self.pause_clicked.emit())
        self.btn_resume.clicked.connect(lambda: self.resume_clicked.emit())
        self.btn_stop.clicked.connect(lambda: self.stop_clicked.emit())
        self.btn_estop.clicked.connect(lambda: self.estop_clicked.emit())
        self.btn_ack.clicked.connect(lambda: self.estop_ack_clicked.emit())

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        root.addLayout(row2)
        self.message = QLabel(tr("El software no sustituye interlocks físicos, parada de emergencia ni supervisión."))
        self.message.setProperty("role", "muted")
        self.message.setWordWrap(True)
        self.message.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        row2.addWidget(self.message, 1)
        # Conectar/Desconectar, Desbloquear y Home: se enlazan con las acciones de la ventana (mismo
        # comportamiento, confirmaciones y estado habilitado que en el menú Máquina).
        self.btn_connect = btn(tr("Conectar"), "connect", tr("Conecta con el dispositivo activo"))
        self.btn_disconnect = btn(tr("Desconectar"), "disconnect", tr("Cierra la conexión"))
        self.btn_unlock = btn(tr("Desbloquear"), "unlock", tr("Desbloquea la alarma ($X) tras confirmación"))
        self.btn_home = btn(tr("Home"), "home", tr("Ciclo de homing ($H)"))
        self.btn_disconnect.hide()
        self._machine_buttons = (self.btn_connect, self.btn_disconnect, self.btn_unlock, self.btn_home)
        for b in self._machine_buttons:
            row2.addWidget(b)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setProperty("role", "muted")
        row2.addWidget(sep)
        for b in (self.btn_pause, self.btn_resume, self.btn_stop, self.btn_ack, self.btn_estop):
            row2.addWidget(b)
        self.update_status(MachineStatus())

    def bind_machine_actions(self, connect: QAction, disconnect: QAction, unlock: QAction, home: QAction) -> None:
        """Los botones disparan las acciones de la ventana y copian su estado habilitado y visible."""
        for button, action in zip(self._machine_buttons, (connect, disconnect, unlock, home)):
            button.clicked.connect(lambda _c=False, a=action: a.trigger())

            def sync(b: QPushButton = button, a: QAction = action) -> None:
                b.setEnabled(a.isEnabled())
                if a in (connect, disconnect):
                    b.setVisible(a.isVisible())

            action.changed.connect(sync)
            sync()

    def set_message(self, text: str, severity: str = "info") -> None:
        t = theme.current()
        color = {"info": t.muted, "warn": t.warn, "block": t.danger}.get(severity, t.muted)
        prefix = {"info": "ℹ", "warn": "⚠", "block": "⛔"}.get(severity, "ℹ")
        self.message.setText(f"{prefix} {text}")
        css = f"color: {color}; font-weight: {'700' if severity != 'info' else '400'};"
        if self.message.styleSheet() != css:
            self.message.setStyleSheet(css)

    def _fit_pills(self) -> None:
        """Si no cabe todo en la fila, «Home» y «Puerta» muestran sólo su símbolo (texto en el tooltip)."""
        pills = [p for p in (self.homed, self.door) if not p.isHidden()]
        needed = (self.state.sizeHint().width() + self.laser.sizeHint().width()
                  + sum(p.full_width() for p in pills)
                  + sum(lab.minimumWidth() for lab in self.coords.values()) + 8 * 8 + 24)
        compact = self.width() < needed
        for p in (self.homed, self.door):
            p.set_compact(compact)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._fit_pills()

    def update_status(self, st: MachineStatus) -> None:
        t = theme.current()
        self.state.set_state(st.display)
        if st.conn != ConnState.CONNECTED:
            self.laser.setText("◌  " + tr("Láser: sin conexión"))
            self.laser.set_style(t.surface2, t.muted)
        elif st.laser_emitting or st.fire_active:
            self.laser.setText("✹  " + tr("LÁSER EMITIENDO"))
            self.laser.set_style(t.danger, "#ffffff", "#ffd400")
        elif st.spindle_modal in ("M3", "M4"):
            self.laser.setText("◐  " + tr("Láser armado ({m})", m=st.spindle_modal))
            self.laser.set_style(t.warn, "#000000")
        else:
            self.laser.setText("○  " + tr("Láser apagado"))
            self.laser.set_style(t.ok, "#ffffff")
        if st.conn == ConnState.CONNECTED:
            self.homed.set_label(("⌂ " + tr("Home hecho")) if st.homed else ("⌂ " + tr("Sin homing")))
            self.homed.set_style(t.surface2 if st.homed else t.warn, t.text if st.homed else "#000000")
            self.door.setVisible(True)
            self.door.set_label(("▯ " + tr("Puerta abierta")) if st.door_open else ("▮ " + tr("Puerta cerrada")))
            self.door.set_style(t.danger if st.door_open else t.surface2, "#ffffff" if st.door_open else t.text)
        else:
            self.homed.set_label("⌂ —")
            self.homed.set_style(t.surface2, t.muted)
            self.door.setVisible(False)
        for axis, val in zip("XYZA", st.wpos):
            self.coords[axis].setText(f"{axis} {val:9.3f}")
        self._fit_pills()
        connected = st.conn == ConnState.CONNECTED
        self.btn_estop.setEnabled(st.conn != ConnState.DISCONNECTED)
        self.btn_stop.setEnabled(connected)
        self.btn_ack.setVisible(st.estop)
        self.btn_pause.setEnabled(connected and st.display in (DisplayState.RUN, DisplayState.JOG))
        self.btn_resume.setEnabled(connected and st.display in (DisplayState.HOLD, DisplayState.PAUSED, DisplayState.DOOR)
                                   and st.substate in (0, None))
        if st.error and not connected:
            self.conn.setText(tr("Último error: {e}", e=st.error))
            self.conn.setToolTip(self.conn.text())
        else:
            self.conn.setText(st.firmware if connected and st.firmware else "")
            self.conn.setToolTip(self.conn.text())
        self.conn.setAlignment(Qt.AlignmentFlag.AlignVCenter)
