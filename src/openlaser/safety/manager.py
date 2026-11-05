"""Gestor de seguridad: clasificación de comandos, comprobaciones previas,
límites del disparo de prueba y registro de eventos de seguridad.

El software NO sustituye interlocks físicos, parada de emergencia cableada,
supervisión, ventilación ni protección ocular. Este módulo reduce errores de
operación; no certifica la seguridad de la máquina.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from openlaser.i18n import N_, tr

_safety_log = logging.getLogger("openlaser.safety")

DISCLAIMER_ES = N_("OpenLaser Studio no sustituye los interlocks físicos, la parada de emergencia cableada, la supervisión permanente, la extracción de humos, la protección ocular certificada para la longitud de onda ni el resto de medidas de seguridad de la máquina.")

HARD_MAX_FIRE_PERCENT = 5.0
HARD_MAX_FIRE_SECONDS = 10.0


class Severity(Enum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


@dataclass
class Issue:
    severity: Severity
    code: str
    message: str


class Risk(Enum):
    SAFE = "safe"
    CAUTION = "caution"
    DANGER = "danger"


@dataclass
class CommandRisk:
    level: Risk
    reasons: list[str] = field(default_factory=list)
    fires_laser: bool = False


@dataclass
class SafetyConfig:
    max_fire_percent: float = 1.0
    max_fire_seconds: float = 3.0
    require_checklist: bool = True
    confirm_start: bool = True
    confirm_dangerous_commands: bool = True
    block_without_homing: bool = False
    checklist: list[str] = field(default_factory=lambda: list(DEFAULT_CHECKLIST))

    def clamp(self) -> "SafetyConfig":
        self.max_fire_percent = max(0.1, min(HARD_MAX_FIRE_PERCENT, float(self.max_fire_percent)))
        self.max_fire_seconds = max(0.2, min(HARD_MAX_FIRE_SECONDS, float(self.max_fire_seconds)))
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SafetyConfig":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known).clamp()


DEFAULT_CHECKLIST = [
    N_("Material colocado y sujeto"),
    N_("Extracción de humos activa"),
    N_("Protección ocular adecuada a la longitud de onda"),
    N_("Carcasa cerrada o zona controlada"),
    N_("Sin materiales inflamables no previstos en la zona"),
    N_("Extintor adecuado disponible"),
    N_("Operador presente durante todo el trabajo"),
    N_("Origen y límites verificados"),
]


@dataclass
class SafetyEvent:
    timestamp: float
    kind: str
    message: str


_S_WORD = re.compile(r"S([-+]?\d*\.?\d+)")
_M_LASER_ON = re.compile(r"M0*([34])(?!\d)")


class SafetyManager:
    def __init__(self, config: SafetyConfig | None = None) -> None:
        self.config = (config or SafetyConfig()).clamp()
        self._events: deque[SafetyEvent] = deque(maxlen=500)
        self._lock = threading.Lock()

    # --- registro ------------------------------------------------------------
    def record(self, kind: str, message: str) -> SafetyEvent:
        ev = SafetyEvent(time.time(), kind, message)
        with self._lock:
            self._events.append(ev)
        _safety_log.warning("%s: %s", kind, message, extra={"safety_kind": kind})
        return ev

    def events(self) -> list[SafetyEvent]:
        with self._lock:
            return list(self._events)

    # --- clasificación -------------------------------------------------------
    def classify_command(self, line: str) -> CommandRisk:
        text = line.strip().upper().replace(" ", "")
        reasons: list[str] = []
        level = Risk.SAFE
        fires = False
        if not text:
            return CommandRisk(Risk.SAFE)
        if text.startswith("$"):
            if text == "$X":
                level = Risk.DANGER
                reasons.append(tr("Desbloquear omite la protección de alarma; la posición puede no ser fiable."))
            elif text.startswith("$RST"):
                level = Risk.DANGER
                reasons.append(tr("Restablece parámetros del controlador."))
            elif re.fullmatch(r"\$\d+=.*", text):
                level = Risk.DANGER
                reasons.append(tr("Modifica un parámetro permanente del controlador."))
                if text.startswith("$32=0"):
                    reasons.append(tr("Desactivar el modo láser puede mantener el láser encendido sin movimiento."))
            elif text == "$H":
                level = Risk.CAUTION
                reasons.append(tr("El homing mueve la máquina."))
            elif text.startswith("$J="):
                level = Risk.CAUTION
                reasons.append(tr("Movimiento manual."))
            elif text == "$SLP":
                level = Risk.CAUTION
                reasons.append(tr("Suspende el controlador; requiere reinicio."))
            return CommandRisk(level, reasons, False)
        m_on = _M_LASER_ON.search(text)
        s_match = _S_WORD.search(text)
        s_val = float(s_match.group(1)) if s_match else None
        if m_on and (s_val is None or s_val > 0):
            level = Risk.DANGER
            fires = True
            reasons.append(tr("Activa el láser (M3/M4)."))
        elif s_val is not None and s_val > 0:
            level = Risk.DANGER
            fires = True
            reasons.append(tr("Fija potencia S>0; si el láser está armado puede emitir."))
        if re.search(r"G0*[0123](?!\d)", text) and re.search(r"[XYZA][-+]?\d", text):
            if level == Risk.SAFE:
                level = Risk.CAUTION
            reasons.append(tr("Mueve la máquina."))
        if re.search(r"G10|G92", text):
            if level == Risk.SAFE:
                level = Risk.CAUTION
            reasons.append(tr("Cambia el origen de trabajo."))
        return CommandRisk(level, reasons, fires)

    # --- disparo de prueba ---------------------------------------------------
    def validate_fire(self, percent: float, seconds: float) -> tuple[bool, str]:
        if percent <= 0:
            return False, tr("La potencia debe ser mayor que 0")
        if percent > self.config.max_fire_percent:
            return False, tr("Potencia máxima de enfoque: {p} %", p=self.config.max_fire_percent)
        if seconds <= 0 or seconds > self.config.max_fire_seconds:
            return False, tr("Duración máxima de disparo: {s} s", s=self.config.max_fire_seconds)
        return True, ""

    # --- comprobaciones previas ----------------------------------------------
    def preflight(self, *, connected: bool, idle: bool, estop: bool, door_open: bool, homed: bool,
                  homing_enabled: bool, bounds_ok: bool | None, bounds_message: str = "",
                  grbl_settings: dict[int, str] | None = None, s_max_profile: float | None = None,
                  max_s_in_job: float | None = None, has_system_commands: bool = False) -> list[Issue]:
        issues: list[Issue] = []
        if not connected:
            issues.append(Issue(Severity.BLOCK, "not_connected", tr("No hay máquina conectada.")))
        if estop:
            issues.append(Issue(Severity.BLOCK, "estop", tr("Parada de emergencia pendiente de confirmar.")))
        if door_open:
            issues.append(Issue(Severity.BLOCK, "door", tr("La puerta está abierta.")))
        if connected and not idle:
            issues.append(Issue(Severity.BLOCK, "not_idle", tr("La máquina no está en reposo (Idle).")))
        if bounds_ok is False:
            issues.append(Issue(Severity.BLOCK, "bounds", bounds_message or tr("El trabajo sale del área.")))
        if has_system_commands:
            issues.append(Issue(Severity.BLOCK, "system_cmd",
                                tr("El G-code contiene comandos '$'; no se permiten dentro de un trabajo.")))
        if homing_enabled and not homed:
            sev = Severity.BLOCK if self.config.block_without_homing else Severity.WARN
            issues.append(Issue(sev, "not_homed", tr("No se ha realizado Home: los límites no están verificados.")))
        if grbl_settings:
            issues.extend(self.check_settings(grbl_settings, s_max_profile))
        if max_s_in_job is not None and s_max_profile and max_s_in_job > s_max_profile + 1e-6:
            issues.append(Issue(Severity.WARN, "s_over",
                                tr("El G-code usa S={s} por encima del máximo del perfil ({m}).",
                                   s=max_s_in_job, m=s_max_profile)))
        return issues

    def check_settings(self, settings: dict[int, str], s_max_profile: float | None) -> list[Issue]:
        issues: list[Issue] = []

        def num(n: int) -> float | None:
            try:
                return float(settings[n])
            except (KeyError, ValueError):
                return None

        laser = num(32)
        if laser is not None and laser != 1:
            issues.append(Issue(Severity.WARN, "laser_mode",
                                tr("Laser Mode ($32) no está activado: el láser podría quedar encendido en paradas.")))
        smax = num(30)
        if smax is not None and s_max_profile and abs(smax - s_max_profile) > 1e-6:
            issues.append(Issue(Severity.WARN, "s_max",
                                tr("S máximo de GRBL ($30={g}) no coincide con el perfil ({p}).",
                                   g=smax, p=s_max_profile)))
        inches = num(13)
        if inches is not None and inches != 0:
            issues.append(Issue(Severity.WARN, "units",
                                tr("GRBL informa en pulgadas ($13=1); la aplicación trabaja en mm.")))
        return issues


def has_blocking(issues: list[Issue]) -> bool:
    return any(i.severity == Severity.BLOCK for i in issues)
