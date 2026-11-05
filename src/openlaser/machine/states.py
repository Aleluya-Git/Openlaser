"""Máquina de estados de conexión, controlador y trabajo.

Tres ejes de estado independientes se combinan en un único estado visible
(:class:`DisplayState`) y en una tabla de acciones permitidas
(:func:`allowed_actions`). La interfaz sólo habilita lo que esta tabla
permite y el controlador vuelve a comprobarlo antes de actuar.
"""
from __future__ import annotations

from enum import Enum

from openlaser.i18n import tr


class ConnState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"


class GrblState(Enum):
    UNKNOWN = "Unknown"
    IDLE = "Idle"
    RUN = "Run"
    HOLD = "Hold"
    JOG = "Jog"
    HOME = "Home"
    CHECK = "Check"
    DOOR = "Door"
    ALARM = "Alarm"
    SLEEP = "Sleep"
    TOOL = "Tool"  # grblHAL: cambio de herramienta manual pendiente (equivale a una pausa)

    @classmethod
    def parse(cls, name: str) -> "GrblState":
        for member in cls:
            if member.value.lower() == name.lower():
                return member
        return cls.UNKNOWN


class JobState(Enum):
    NONE = "none"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    COMPLETED = "completed"
    ABORTED = "aborted"


class DisplayState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    IDLE = "idle"
    RUN = "run"
    HOLD = "hold"
    JOG = "jog"
    HOME = "home"
    CHECK = "check"
    DOOR = "door"
    ALARM = "alarm"
    SLEEP = "sleep"
    ERROR = "error"
    PAUSED = "paused"
    ESTOP = "estop"

    @property
    def label(self) -> str:
        return {
            DisplayState.DISCONNECTED: tr("Desconectado"),
            DisplayState.CONNECTING: tr("Conectando"),
            DisplayState.IDLE: tr("Reposo"),
            DisplayState.RUN: tr("Ejecutando"),
            DisplayState.HOLD: tr("Retenido"),
            DisplayState.JOG: tr("Jog"),
            DisplayState.HOME: tr("Homing"),
            DisplayState.CHECK: tr("Comprobación"),
            DisplayState.DOOR: tr("Puerta abierta"),
            DisplayState.ALARM: tr("Alarma"),
            DisplayState.SLEEP: tr("Suspendido"),
            DisplayState.ERROR: tr("Error"),
            DisplayState.PAUSED: tr("Pausado"),
            DisplayState.ESTOP: tr("Parada solicitada"),
        }[self]

    @property
    def symbol(self) -> str:
        """Símbolo textual para no depender sólo del color."""
        return {
            DisplayState.DISCONNECTED: "○", DisplayState.CONNECTING: "◌", DisplayState.IDLE: "●",
            DisplayState.RUN: "▶", DisplayState.HOLD: "‖", DisplayState.JOG: "✥", DisplayState.HOME: "⌂",
            DisplayState.CHECK: "✓", DisplayState.DOOR: "▯", DisplayState.ALARM: "⚠",
            DisplayState.SLEEP: "☾", DisplayState.ERROR: "✖", DisplayState.PAUSED: "‖",
            DisplayState.ESTOP: "■",
        }[self]


class Action(Enum):
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    HOME = "home"
    UNLOCK = "unlock"
    JOG = "jog"
    SET_ORIGIN = "set_origin"
    GO_ORIGIN = "go_origin"
    GOTO = "goto"
    FRAME = "frame"
    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    ESTOP = "estop"
    FIRE = "fire"
    SEND = "send"
    READ_SETTINGS = "read_settings"
    OVERRIDE = "override"


def derive_display_state(conn: ConnState, grbl: GrblState, job: JobState,
                         estop: bool, error: bool = False) -> DisplayState:
    if estop:
        return DisplayState.ESTOP
    if conn in (ConnState.DISCONNECTED, ConnState.DISCONNECTING):
        return DisplayState.ERROR if error else DisplayState.DISCONNECTED
    if conn == ConnState.CONNECTING:
        return DisplayState.CONNECTING
    if grbl == GrblState.ALARM:
        return DisplayState.ALARM
    if grbl == GrblState.DOOR:
        return DisplayState.DOOR
    if job == JobState.PAUSED or (job == JobState.RUNNING and grbl == GrblState.HOLD):
        return DisplayState.PAUSED
    mapping = {
        GrblState.IDLE: DisplayState.IDLE, GrblState.RUN: DisplayState.RUN, GrblState.HOLD: DisplayState.HOLD,
        GrblState.JOG: DisplayState.JOG, GrblState.HOME: DisplayState.HOME, GrblState.CHECK: DisplayState.CHECK,
        GrblState.SLEEP: DisplayState.SLEEP, GrblState.UNKNOWN: DisplayState.CONNECTING,
        GrblState.TOOL: DisplayState.HOLD,
    }
    return mapping.get(grbl, DisplayState.ERROR)


_MOTION_IDLE = {GrblState.IDLE}


def allowed_actions(conn: ConnState, grbl: GrblState, job: JobState, estop: bool,
                    fire_active: bool = False) -> set[Action]:
    """Acciones permitidas en una combinación de estados."""
    if conn == ConnState.DISCONNECTED:
        return {Action.CONNECT}
    if conn in (ConnState.CONNECTING, ConnState.DISCONNECTING):
        return {Action.DISCONNECT, Action.ESTOP, Action.STOP}
    acts: set[Action] = {Action.DISCONNECT, Action.ESTOP, Action.STOP}
    if estop:
        return acts  # hasta que el operador confirme la parada
    job_active = job in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING)
    if fire_active:
        return acts  # el disparo de prueba bloquea todo lo demás
    if grbl in (GrblState.IDLE, GrblState.ALARM) and not job_active:
        acts |= {Action.SEND, Action.READ_SETTINGS}
    if grbl == GrblState.ALARM and not job_active:
        acts |= {Action.UNLOCK, Action.HOME}
    if grbl in _MOTION_IDLE and not job_active:
        acts |= {Action.HOME, Action.JOG, Action.SET_ORIGIN, Action.GO_ORIGIN, Action.GOTO,
                 Action.FRAME, Action.START, Action.FIRE}
    if grbl == GrblState.JOG and not job_active:
        acts |= {Action.JOG}
    if grbl == GrblState.CHECK and not job_active:
        acts |= {Action.SEND, Action.START}
    if job == JobState.RUNNING and grbl in (GrblState.RUN, GrblState.IDLE):
        acts |= {Action.PAUSE, Action.OVERRIDE}
    if grbl in (GrblState.RUN, GrblState.JOG):
        acts |= {Action.OVERRIDE}
    if grbl in (GrblState.HOLD, GrblState.DOOR, GrblState.TOOL) or job == JobState.PAUSED:
        acts |= {Action.RESUME}
    return acts
