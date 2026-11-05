"""Analizador de respuestas del protocolo GRBL 1.1 y constantes de tiempo real.

Es puro Python y no depende de la interfaz, por lo que se prueba de forma
aislada (``tests/test_grbl_parser.py``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- Comandos de tiempo real (no ocupan el búfer de recepción) ---------------
RT_STATUS = b"?"
RT_CYCLE_START = b"~"
RT_FEED_HOLD = b"!"
RT_SOFT_RESET = b"\x18"
RT_SAFETY_DOOR = b"\x84"
RT_JOG_CANCEL = b"\x85"
RT_FEED_OV_RESET = b"\x90"
RT_FEED_OV_PLUS10 = b"\x91"
RT_FEED_OV_MINUS10 = b"\x92"
RT_FEED_OV_PLUS1 = b"\x93"
RT_FEED_OV_MINUS1 = b"\x94"
RT_RAPID_OV_100 = b"\x95"
RT_RAPID_OV_50 = b"\x96"
RT_RAPID_OV_25 = b"\x97"
RT_SPINDLE_OV_RESET = b"\x99"
RT_SPINDLE_OV_PLUS10 = b"\x9a"
RT_SPINDLE_OV_MINUS10 = b"\x9b"
RT_SPINDLE_OV_PLUS1 = b"\x9c"
RT_SPINDLE_OV_MINUS1 = b"\x9d"

REALTIME_BYTES = frozenset(b"?~!\x18") | frozenset(range(0x80, 0xA2))

GRBL_RX_BUFFER_SIZE = 128
GRBL_LINE_MAX = 80


# --- Mensajes analizados -----------------------------------------------------
@dataclass(frozen=True)
class Ok:
    pass


@dataclass(frozen=True)
class ErrorMsg:
    code: int


@dataclass(frozen=True)
class AlarmMsg:
    code: int


@dataclass(frozen=True)
class Welcome:
    version: str
    raw: str


@dataclass(frozen=True)
class Feedback:
    kind: str  # MSG, GC, HLP, VER, OPT, G54..G59, G28, G30, G92, TLO, PRB, echo
    payload: str


@dataclass(frozen=True)
class SettingMsg:
    number: int
    value: str


@dataclass(frozen=True)
class StartupBlock:
    line: str
    ok: bool


@dataclass(frozen=True)
class Unknown:
    raw: str


@dataclass
class StatusReport:
    state: str
    substate: int | None = None
    mpos: tuple[float, ...] | None = None
    wpos: tuple[float, ...] | None = None
    wco: tuple[float, ...] | None = None
    feed: float | None = None
    spindle: float | None = None
    planner_free: int | None = None
    rx_free: int | None = None
    line_number: int | None = None
    pins: str = ""
    overrides: tuple[int, int, int] | None = None
    accessories: str | None = None  # None = campo ausente; "" = ninguno activo
    raw: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def spindle_on(self) -> bool | None:
        if self.accessories is None:
            return None
        return "S" in self.accessories or "C" in self.accessories


Message = Ok | ErrorMsg | AlarmMsg | Welcome | Feedback | SettingMsg | StartupBlock | StatusReport | Unknown

_WELCOME_RE = re.compile(r"^Grbl(?:HAL)?\s+([\w.]+)", re.IGNORECASE)
_SETTING_RE = re.compile(r"^\$(\d+)=(.*)$")
_FEEDBACK_RE = re.compile(r"^\[([A-Za-z0-9.]+)[:]?(.*)\]$")


def _floats(text: str) -> tuple[float, ...]:
    return tuple(float(v) for v in text.split(","))


_V09_FIELD_RE = re.compile(r"([A-Za-z]+):([-\d.,]*?)(?=,[A-Za-z]+:|$)")


def _v09_to_v11(body: str) -> str:
    """GRBL 0.9 separa con comas: ``Idle,MPos:0.000,0.000,0.000,WPos:…,Buf:0,RX:0``."""
    state, _, rest = body.partition(",")
    fields = [f"{k}:{v}" for k, v in _V09_FIELD_RE.findall(rest)]
    rename = {"Buf": "_Buf", "RX": "_RX"}
    return "|".join([state] + [rename.get(f.split(":", 1)[0], f.split(":", 1)[0]) + ":" + f.split(":", 1)[1]
                               for f in fields])


def parse_status(line: str) -> StatusReport:
    body = line.strip()[1:-1]
    if "|" not in body and "," in body.split(":", 1)[0]:
        body = _v09_to_v11(body)
    parts = body.split("|")
    state_part = parts[0]
    if ":" in state_part:
        name, sub = state_part.split(":", 1)
        substate: int | None = int(sub) if sub.isdigit() else None
    else:
        name, substate = state_part, None
    rep = StatusReport(state=name, substate=substate, raw=line.strip())
    for part in parts[1:]:
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        try:
            if key == "MPos":
                rep.mpos = _floats(val)
            elif key == "WPos":
                rep.wpos = _floats(val)
            elif key == "WCO":
                rep.wco = _floats(val)
            elif key == "FS":
                vals = _floats(val)
                rep.feed = vals[0]
                rep.spindle = vals[1] if len(vals) > 1 else None
            elif key == "F":
                rep.feed = float(val)
            elif key == "Bf":
                vals = _floats(val)
                rep.planner_free, rep.rx_free = int(vals[0]), int(vals[1])
            elif key == "Ln":
                rep.line_number = int(val)
            elif key == "Pn":
                rep.pins = val
            elif key == "Ov":
                vals = _floats(val)
                rep.overrides = (int(vals[0]), int(vals[1]), int(vals[2]))
            elif key == "A":
                rep.accessories = val
            else:
                rep.extra[key] = val
        except (ValueError, IndexError):
            rep.extra[key] = val
    # GRBL omite "A:" cuando no hay accesorios activos, pero sólo en los
    # informes que incluyen "Ov:". Si hay Ov y falta A => nada activo.
    if rep.overrides is not None and rep.accessories is None:
        rep.accessories = ""
    return rep


def parse_line(line: str) -> Message:
    """Clasifica una línea recibida del controlador."""
    text = line.strip()
    if not text:
        return Unknown("")
    low = text.lower()
    if low == "ok":
        return Ok()
    if low.startswith("error:"):
        try:
            return ErrorMsg(int(text.split(":", 1)[1]))
        except ValueError:
            return ErrorMsg(-1)
    if text.upper().startswith("ALARM:"):
        try:
            return AlarmMsg(int(text.split(":", 1)[1]))
        except ValueError:
            return AlarmMsg(-1)
    if text.startswith("<") and text.endswith(">"):
        return parse_status(text)
    m = _WELCOME_RE.match(text)
    if m:
        return Welcome(m.group(1), text)
    m = _SETTING_RE.match(text)
    if m:
        return SettingMsg(int(m.group(1)), m.group(2).strip())
    if text.startswith(">"):
        return StartupBlock(text[1:], text.endswith(":ok"))
    m = _FEEDBACK_RE.match(text)
    if m:
        return Feedback(m.group(1), m.group(2))
    return Unknown(text)


# --- Utilidades de líneas a enviar -------------------------------------------
_COMMENT_PAREN = re.compile(r"\([^)]*\)")


def sanitize_line(line: str) -> str:
    """Elimina comentarios y espacios de una línea de G-code.

    Los comandos ``$`` se devuelven sin modificar salvo espacios externos
    (por ejemplo ``$J=...`` o ``$RST=*``) porque algunos admiten texto.
    """
    text = line.strip()
    if text.startswith("$"):
        return text.replace(" ", "") if text.upper().startswith("$J=") else text
    text = _COMMENT_PAREN.sub("", text)
    if ";" in text:
        text = text.split(";", 1)[0]
    text = text.replace(" ", "").replace("\t", "")
    if text.startswith("%"):
        return ""
    return text.upper()


def is_system_command(line: str) -> bool:
    return line.startswith("$") and not line.upper().startswith("$J=")
