"""Eventos que el hilo de comunicación publica para la interfaz.

Son objetos inmutables; la interfaz los recoge de una cola con un temporizador
(no bloquea nunca el hilo de la máquina).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from openlaser.machine.states import ConnState, DisplayState, GrblState, JobState


class LineKind(Enum):
    TX = "tx"
    RX = "rx"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    STATUS = "status"


@dataclass(frozen=True)
class MachineStatus:
    conn: ConnState = ConnState.DISCONNECTED
    grbl: GrblState = GrblState.UNKNOWN
    substate: int | None = None
    job: JobState = JobState.NONE
    display: DisplayState = DisplayState.DISCONNECTED
    mpos: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    wpos: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    wco: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    feed: float = 0.0
    spindle: float = 0.0
    spindle_modal: str = "M5"  # M3/M4/M5 según A: o $G (estimación)
    laser_emitting: bool = False
    overrides: tuple[int, int, int] = (100, 100, 100)
    homed: bool = False
    estop: bool = False
    fire_active: bool = False
    door_open: bool = False
    pins: str = ""
    firmware: str = ""
    dialect: str = "grbl"          # dialecto detectado (grbl / grblhal / fluidnc)
    error: str = ""
    planner_free: int | None = None
    rx_free: int | None = None


@dataclass(frozen=True)
class StatusEvent:
    status: MachineStatus


@dataclass(frozen=True)
class ConsoleEvent:
    kind: LineKind
    text: str
    source: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class AlarmEvent:
    code: int


@dataclass(frozen=True)
class ErrorEvent:
    code: int
    line: str
    source: str


@dataclass(frozen=True)
class SettingsEvent:
    settings: dict[int, str]


@dataclass(frozen=True)
class SafetyNotice:
    severity: str  # info | warn | block
    code: str
    message: str


@dataclass(frozen=True)
class JobProgressEvent:
    name: str
    total: int
    sent: int
    acked: int
    elapsed: float
    remaining: float | None
    current_line: str
    state: JobState


@dataclass(frozen=True)
class CommandRejected:
    action: str
    reason: str


Event = (StatusEvent | ConsoleEvent | AlarmEvent | ErrorEvent | SettingsEvent | SafetyNotice
         | JobProgressEvent | CommandRejected)
