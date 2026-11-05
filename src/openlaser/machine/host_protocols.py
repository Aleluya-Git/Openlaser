"""Protocolos «envío-respuesta» de Marlin y Smoothieware (Fase 6). Puro Python, sin Qt.

A diferencia de GRBL, estos firmwares no se pueden alimentar con conteo de caracteres: se envía una
línea y se espera su ``ok``. Además:

**Marlin** (``LASER_FEATURE``)
  * No tiene informe de estado en tiempo real: la posición se pide con ``M114`` (que con la
    configuración por defecto no sincroniza el planificador) y el estado lo deduce la aplicación
    (cola vacía tras ``M400`` = reposo).
  * ``M3``/``M4``/``M5`` esperan a que termine el movimiento. En **modo en línea** (``M3 I``) la
    potencia viaja con cada ``G1 S…``. Para no depender de cómo trate cada compilación los ``G0``, la
    aplicación convierte cada ``G0`` en ``G1 … S0`` al avance rápido del perfil y repone ``F``.
  * Potencia ``S`` por defecto en PWM 0–255 (``CUTTER_POWER_UNIT``).
  * Parada: ``M410`` (parada rápida) y ``M5``; parada de emergencia: ``M112``. Ambas son inmediatas
    sólo con ``EMERGENCY_PARSER`` (se comprueba en ``M115``: ``Cap:EMERGENCY_PARSER:1``).
  * Respuestas: ``ok``, ``echo:busy: processing``, ``Error:…``, ``echo:Unknown command``,
    ``start`` (reinicio), ``X:… Y:… Z:… E:… Count …`` (M114), ``FIRMWARE_NAME:Marlin …`` (M115).

**Smoothieware** (``grbl_mode true``)
  * Responde a ``?`` con un informe tipo GRBL (formato 1.1 ``<Idle|MPos:…>`` o 0.9 ``<Idle,MPos:…>``)
    y acepta ``$H`` y ``$X``. ``!``/``~`` están documentados como «en desarrollo»: la pausa la hace
    la aplicación dejando de enviar.
  * El láser sólo emite durante ``G1/G2/G3`` y **nunca en G0**; la potencia es una fracción 0–1
    (``laser_module_maximum_s_value``). Un ``G1`` sin ``S`` previo usa la potencia por defecto del
    módulo (p. ej. 0,8): la traducción añade ``S0`` explícito antes del primer ``G1``.
  * Ctrl-X (0x18) detiene todo y deja el controlador en alarma hasta ``$X`` o ``M999``.

Referencias: documentación de Marlin (M3/M4/M5, M114, M115, M410, M112, M400) y de Smoothieware
(«grbl-mode», «laser-cutter-guide»). Ninguna de estas reglas se ha comprobado con hardware real.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from openlaser.machine import grbl_protocol as gp

_WORD = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")


@dataclass(frozen=True)
class HostProtocol:
    id: str
    name: str
    realtime_status: bool          # responde a «?» (Smoothieware) o hay que deducir el estado (Marlin)
    s_out_max: float               # S máximo que entiende el firmware por defecto
    home_cmd: str
    unlock_cmd: str
    stop_lines: tuple[str, ...]    # se escriben directamente para detener (sin esperar a la cola)
    estop_bytes: bytes
    connect_cmds: tuple[str, ...]
    stationary_fire: bool          # admite el disparo de enfoque con el láser parado
    dwell_ms: bool                 # G4 P en milisegundos (Marlin) o segundos


MARLIN = HostProtocol(
    "marlin", "Marlin", realtime_status=False, s_out_max=255.0, home_cmd="G28XY", unlock_cmd="M999",
    stop_lines=("M410", "M5"), estop_bytes=b"M112\n", connect_cmds=("M115", "M5"),
    stationary_fire=True, dwell_ms=True)

SMOOTHIE = HostProtocol(
    "smoothie", "Smoothieware", realtime_status=True, s_out_max=1.0, home_cmd="$H", unlock_cmd="$X",
    stop_lines=(), estop_bytes=gp.RT_SOFT_RESET, connect_cmds=("version", "M5"),
    stationary_fire=False, dwell_ms=False)

PROTOCOLS: dict[str, HostProtocol] = {p.id: p for p in (MARLIN, SMOOTHIE)}


def is_host_firmware(firmware: str) -> bool:
    return (firmware or "").lower() in PROTOCOLS


# ------------------------------------------------------------------ respuestas
@dataclass(frozen=True)
class Busy:
    pass


@dataclass(frozen=True)
class Halted:
    raw: str


@dataclass(frozen=True)
class FirmwareInfo:
    name: str          # marlin / smoothie
    raw: str


@dataclass(frozen=True)
class Capability:
    name: str
    enabled: bool


@dataclass(frozen=True)
class Position:
    x: float
    y: float
    z: float


_POS_RE = re.compile(r"^X:(-?[\d.]+)\s+Y:(-?[\d.]+)\s+Z:(-?[\d.]+)")
_CAP_RE = re.compile(r"^Cap:([A-Z0-9_]+):([01])$")


def parse_marlin(line: str) -> object:
    text = line.strip()
    low = text.lower()
    if not text:
        return gp.Unknown("")
    if low == "ok" or low.startswith("ok "):
        return gp.Ok()
    if low.startswith("echo:busy") or low.startswith("busy:"):
        return Busy()
    if low == "start":
        return gp.Welcome("Marlin", text)
    if "kill() called" in low or low.startswith("!!") or "printer halted" in low:
        return Halted(text)
    if low.startswith("error:"):
        return gp.ErrorMsg(-1)
    if text.startswith("FIRMWARE_NAME:"):
        return FirmwareInfo("marlin" if "marlin" in low else ("smoothie" if "smoothie" in low else "otro"), text)
    m = _CAP_RE.match(text)
    if m:
        return Capability(m.group(1), m.group(2) == "1")
    m = _POS_RE.match(text)
    if m:
        return Position(float(m.group(1)), float(m.group(2)), float(m.group(3)))
    if low.startswith("<") and "|" in low:   # informe GRBL: el controlador no es Marlin
        return FirmwareInfo("grbl", text)
    if low.startswith("grbl"):
        return FirmwareInfo("grbl", text)
    return gp.Unknown(text)


def smoothie_status(line: str) -> str:
    """Convierte un informe de Smoothieware en formato 0.9 (``<Idle,MPos:1,2,3,WPos:…>``) al de 1.1."""
    text = line.strip()
    if not (text.startswith("<") and text.endswith(">")) or "|" in text:
        return text
    body = text[1:-1]
    out: list[str] = []
    for chunk in re.split(r",(?=[A-Za-z])", body):
        out.append(chunk)
    return "<" + "|".join(out) + ">"


def parse_smoothie(line: str) -> object:
    text = line.strip()
    low = text.lower()
    if text.startswith("<"):
        return gp.parse_status(smoothie_status(text))
    if low.startswith("build version:") or low.startswith("smoothie"):
        return FirmwareInfo("smoothie", text)
    if low.startswith("!!"):
        return Halted(text)
    if low.startswith("alarm") or "halted" in low or "kill" in low and "reset" in low:
        return Halted(text)
    if text.startswith("FIRMWARE_NAME:") or low == "start" or low.startswith("echo:marlin"):
        return FirmwareInfo("marlin", text)
    if low.startswith("grbl"):
        return FirmwareInfo("grbl", text)
    msg = gp.parse_line(text)
    if isinstance(msg, gp.Welcome):  # «Grbl …»: no es Smoothieware
        return FirmwareInfo("grbl", text)
    return msg


# ------------------------------------------------------------------ traducción
def _fmt(v: float, dec: int = 3) -> str:
    text = f"{v:.{dec}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _scale_s(line: str, factor: float, dec: int) -> str:
    def repl(m: re.Match[str]) -> str:
        return "S" + _fmt(max(0.0, float(m.group(1)) * factor), dec)
    return re.sub(r"S([-+]?(?:\d+\.?\d*|\.\d+))", repl, line)


def translate_job(lines: list[str], protocol: HostProtocol, s_in_max: float, s_out_max: float | None = None,
                  rapid_feed: float = 6000.0) -> list[str]:
    """Adapta un trabajo generado para GRBL al firmware.

    * Escala ``S`` de ``0…s_in_max`` (perfil) a ``0…s_out_max`` (firmware).
    * Marlin: ``M3``/``M4`` → modo en línea (``I``); ``G0`` → ``G1 … S0 F<rápido>`` y se repone el
      avance anterior en el siguiente ``G1``; ``G4 P`` en segundos → milisegundos.
    * Smoothieware: ``S0`` explícito antes del primer ``G1`` (si no, usaría su potencia por defecto).
    """
    out_max = s_out_max or protocol.s_out_max
    factor = out_max / s_in_max if s_in_max > 0 else 0.0
    dec = 0 if out_max >= 100 else 3
    result: list[str] = []
    feed: float | None = None          # último F de los G1 del trabajo
    motion = 0                         # modo de movimiento modal (0/1/2/3)
    s_set = False
    restore_feed = False
    last_s: float | None = None        # último S del trabajo (escala del perfil)
    for raw in lines:
        line = gp.sanitize_line(raw)
        if not line:
            continue
        words = _WORD.findall(line)
        gs = [float(v) for k, v in words if k == "G"]
        ms = [int(float(v)) for k, v in words if k == "M"]
        if any(k == "F" for k, _ in words):
            fval = float(next(v for k, v in words if k == "F"))
        else:
            fval = None
        for g in gs:
            if g in (0, 1, 2, 3):
                motion = int(g)
        has_axes = any(k in "XYZA" for k, _ in words)
        if any(k == "S" for k, _ in words):
            s_set = True
            sval = float(next(v for k, v in words if k == "S"))
        else:
            sval = None
        line = _scale_s(line, factor, dec)
        if sval is not None and not (protocol.id == "marlin" and motion == 0 and has_axes):
            last_s = sval
        if protocol.id == "marlin":
            if any(m in (3, 4) for m in ms) and "I" not in line:
                line += "I"
            if 4 in [int(g) for g in gs if float(g).is_integer()] and not has_axes:
                line = re.sub(r"P([-+]?(?:\d+\.?\d*|\.\d+))", lambda m: "P" + _fmt(float(m.group(1)) * 1000, 0), line)
            if motion == 0 and has_axes:
                # G0 → G1 con potencia 0 explícita al avance rápido
                body = re.sub(r"G0*0(?![\d.])", "", line)
                body = re.sub(r"[FS][-+]?(?:\d+\.?\d*|\.\d+)", "", body)
                result.append(f"G1{body}F{_fmt(rapid_feed, 0)}S0")
                restore_feed = True
                continue
            if motion in (1, 2, 3) and has_axes:
                if fval is not None:
                    feed = fval
                elif restore_feed and feed is not None:
                    line += f"F{_fmt(feed, 0)}"
                if sval is None and restore_feed and last_s is not None:
                    line += "S" + _fmt(last_s * factor, dec)  # el G1 S0 del rápido cambió la potencia modal
                restore_feed = False
                if not any(k == "G" and float(v) in (1, 2, 3) for k, v in words):
                    line = f"G{motion}" + line  # el modal de Marlin puede ser G1 tras convertir un G0
            elif fval is not None:
                feed = fval
        else:  # smoothie
            if motion in (1, 2, 3) and has_axes and not s_set:
                line += "S0"
                s_set = True
        result.append(line)
    return result
