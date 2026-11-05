"""Simulador de controlador GRBL 1.1 para láser.

Reproduce lo necesario para probar la aplicación sin máquina:

* Búfer de recepción de 128 bytes con detección de desbordamiento.
* Planificador de 15 bloques: ``ok`` al planificar, no al ejecutar.
* Comandos de tiempo real (``?``, ``!``, ``~``, ``0x18``, ``0x84``, ``0x85``
  y overrides).
* Estados Idle, Run, Hold, Jog, Home, Check, Door, Alarm y Sleep.
* G0/G1/G2/G3/G4, G10 L2/L20, G20/G21, G28, G53, G54, G90/G91, G92,
  M3/M4/M5, M7/M8/M9, M2/M30, F, S.
* ``$$``, ``$#``, ``$G``, ``$I``, ``$N``, ``$H``, ``$X``, ``$J=``, ``$C``,
  ``$SLP``, ``$RST=`` y escritura ``$n=v``.
* Modo láser ($32): G0 nunca emite; M4 no emite sin movimiento; M3 con modo
  de movimiento G1/G2/G3 emite aunque esté parado (disparo de enfoque).
* Registro de emisiones (:attr:`emissions`) para que las pruebas comprueben
  que nada enciende el láser sin una orden explícita.

Variantes de firmware (Fase 5, ``firmware=``):

* ``grbl``: GRBL 1.1h, búfer de 128 bytes.
* ``grblhal``: bienvenida ``GrblHAL``, búfer de 1024 bytes, ``$I`` con ``[NEWOPT:…]`` y
  ``[FIRMWARE:grblHAL]``, campo ``H:`` (homing hecho) en el estado, entrada de parada de emergencia
  (``Pn:E`` y ``ALARM:10``) con :meth:`set_estop`.
* ``smoothie`` (Fase 6): Smoothieware en ``grbl_mode``: bienvenida ``Smoothie``, comando
  ``version``, sólo ``$H``/``$X`` (y ``M999``), ``!``/``~`` ignorados, Ctrl-X detiene y deja en alarma
  (las líneas reciben ``!!`` hasta ``$X``). Láser: emite **sólo** durante G1/G2/G3 con ``S`` > 0, aunque
  no haya ``M3`` (como el módulo láser de Smoothie), con potencia por defecto 0,8 si nunca se envió ``S``.
* ``fluidnc``: bienvenida ``Grbl 3.7 [FluidNC v3.7.8 …]``, mensajes ``[MSG:INFO: …]``, ``$CD``
  (configuración YAML) y parámetros ``$n`` de sólo lectura.

Modelo de coordenadas simplificado: espacio de máquina positivo
``0..$130`` × ``0..$131`` con el homing en (0, 0). El movimiento es lineal a
velocidad constante (sin aceleración). Los arcos se recorren como su cuerda,
aunque la posición final es exacta.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

VERSION = "1.1h"
BANNER = f"Grbl {VERSION} ['$' for help]"
PLANNER_SIZE = 15
RX_SIZE = 128

FIRMWARES: dict[str, dict[str, object]] = {
    "grbl": {"banner": BANNER, "rx": RX_SIZE, "planner": PLANNER_SIZE},
    "grblhal": {"banner": "GrblHAL 1.1f ['$' or '$$' for help]", "rx": 1024, "planner": 35},
    "smoothie": {"banner": "Smoothie", "rx": 128, "planner": 32},
    "fluidnc": {"banner": "Grbl 3.7 [FluidNC v3.7.8 (OpenLaser SIM) '$' for help]", "rx": 128, "planner": 16},
}
FLUIDNC_YAML = """board: OpenLaser SIM
name: Laser simulado
kinematics: {}
axes:
  x:
    steps_per_mm: 80
    max_rate_mm_per_min: 6000
    max_travel_mm: 400
  y:
    steps_per_mm: 80
    max_rate_mm_per_min: 6000
    max_travel_mm: 400
Laser:
  pwm_hz: 5000
  speed_map: 0=0% 1000=100%
  disable_with_s0: false
  s0_with_disable: true"""
LINE_MAX = 80

DEFAULT_SETTINGS: dict[int, float] = {
    0: 10, 1: 25, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 10: 1, 11: 0.010, 12: 0.002, 13: 0,
    20: 1, 21: 0, 22: 1, 23: 0, 24: 25.0, 25: 500.0, 26: 250, 27: 1.0,
    30: 1000, 31: 0, 32: 1,
    100: 80.0, 101: 80.0, 102: 250.0, 110: 6000.0, 111: 6000.0, 112: 500.0,
    120: 500.0, 121: 500.0, 122: 10.0, 130: 400.0, 131: 400.0, 132: 50.0,
}

_WORD_RE = re.compile(r"([A-Z])([-+]?(?:\d+\.?\d*|\.\d+))")
_SUPPORTED_G = {0, 1, 2, 3, 4, 10, 17, 18, 19, 20, 21, 28, 30, 53, 54, 80, 90, 91, 92, 94}
_SUPPORTED_M = {0, 1, 2, 3, 4, 5, 7, 8, 9, 30}
_MOTION_G = {0, 1, 2, 3, 80}


class GCodeError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class Block:
    kind: str  # "rapid", "feed", "dwell", "jog", "home", "sync"
    target: list[float]
    feed: float = 0.0
    spindle: str = "M5"
    s: float = 0.0
    dwell: float = 0.0
    line_no: int | None = None
    start: list[float] = field(default_factory=list)
    duration: float = 0.0
    elapsed: float = 0.0
    on_done: Callable[[], None] | None = None


@dataclass
class Emission:
    sim_time: float
    s: float
    context: str  # "feed", "stationary"
    state: str


class GrblSimulator:
    """Controlador simulado. Seguro entre hilos (un único cerrojo)."""

    def __init__(self, axes: int = 4, speed_factor: float = 1.0, homing_required: bool | None = None,
                 settings: dict[int, float] | None = None, clock: Callable[[], float] = time.monotonic,
                 firmware: str = "grbl") -> None:
        if firmware not in FIRMWARES:
            raise ValueError(f"firmware desconocido: {firmware}")
        self.firmware = firmware
        fw = FIRMWARES[firmware]
        self.banner = str(fw["banner"])
        self.rx_size = int(fw["rx"])  # type: ignore[arg-type]
        self.planner_size = int(fw["planner"])  # type: ignore[arg-type]
        self.estop = False
        self.axes = axes
        self.speed_factor = speed_factor
        self.settings: dict[int, float] = dict(DEFAULT_SETTINGS)
        if settings:
            self.settings.update(settings)
        if homing_required is not None:
            self.settings[22] = 1 if homing_required else 0
        self._clock = clock
        self._lock = threading.RLock()
        self.out: deque[str] = deque()
        self.rx = bytearray()
        self.overflow_count = 0
        self.emissions: list[Emission] = []
        self.lines_received: list[str] = []
        self.powered = False
        self._reset_state(power_on=True)

    # ------------------------------------------------------------------ estado
    def _reset_state(self, power_on: bool = False) -> None:
        self.state = "Idle"
        self.substate = 0
        self.pos = [0.0] * 4
        if power_on:
            self.wco = [0.0] * 4
            self.g92 = [0.0] * 4
            self.homed = False
        self.planned = list(self.pos)
        self.planner: deque[Block] = deque()
        self.current: Block | None = None
        self.motion = 0
        self.absolute = True
        self.inches = False
        self.feed = 0.0
        self.spindle = "M5"
        self.s = 0.8 if getattr(self, "firmware", "grbl") == "smoothie" else 0.0  # Smoothie: potencia por defecto
        self.coolant = "M9"
        self.check_mode = False
        self.feed_ov = 100
        self.rapid_ov = 100
        self.spindle_ov = 100
        self.sim_time = 0.0
        self.door_open = False
        self._resume_state = "Idle"
        self._hold_timer = 0.0
        self._report_count = 0
        self._defer_ok = False
        self._emitting = False
        self._last_update = self._clock()

    def power_on(self) -> None:
        """Equivale a conectar el USB de una placa Arduino (se reinicia)."""
        with self._lock:
            pos, wco, g92 = self.pos, self.wco, self.g92
            self._reset_state(power_on=not self.powered)
            if self.powered:
                self.pos, self.wco, self.g92, self.homed = pos, wco, g92, False
            self.powered = True
            self.rx.clear()
            self._emit_banner()

    def _emit_banner(self) -> None:
        self.out.append("")
        self.out.append(self.banner)
        if self.firmware == "smoothie":
            self.out.append("ok")
            return
        if self.settings.get(22, 0) and not self.homed:
            self.state = "Alarm"
            self.out.append(self._msg("'$H'|'$X' to unlock"))

    # --------------------------------------------------------- entrada/salida
    def receive(self, data: bytes) -> None:
        with self._lock:
            self._update_locked()
            self._process_rx()
            for b in data:
                if b in (0x3F, 0x21, 0x7E, 0x18) or b >= 0x80:
                    self._realtime(b)
                    continue
                if len(self.rx) >= self.rx_size - 1:
                    self.overflow_count += 1
                    continue
                self.rx.append(b)
            self._process_rx()

    def read_output(self) -> list[str]:
        with self._lock:
            self._update_locked()
            self._process_rx()
            lines = list(self.out)
            self.out.clear()
            return lines

    def update(self) -> None:
        with self._lock:
            self._update_locked()
            self._process_rx()

    # ------------------------------------------------------------ tiempo real
    def _realtime(self, b: int) -> None:
        if self.firmware == "smoothie" and b in (0x21, 0x7E):
            return  # «!» y «~» en desarrollo en Smoothieware: se ignoran
        if self.firmware == "smoothie" and b == 0x18:
            self._update_emission(force_off=True)
            self.planner.clear()
            self.current = None
            self.planned = list(self.pos)
            self.rx.clear()
            self.state = "Alarm"
            self.out.append("ALARM: Kill - $X or M999 to clear")
            return
        if b == 0x3F:
            self.out.append(self._status_report())
        elif b == 0x18:
            self._soft_reset()
        elif b == 0x21:  # feed hold
            if self.state == "Run":
                self.state, self.substate = "Hold", 1
                self._hold_timer = 0.15
            elif self.state == "Jog":
                self._cancel_jog()
        elif b == 0x7E:  # cycle start / resume
            if self.state == "Hold" and self.substate == 0:
                self.state = "Run" if (self.current or self.planner) else "Idle"
            elif self.state == "Door" and self.substate == 0 and not self.door_open:
                self.state = "Run" if (self.current or self.planner) else "Idle"
            elif self.state == "Idle" and self.planner:
                self.state = "Run"
        elif b == 0x84:
            self.set_door(True)
        elif b == 0x85:
            if self.state == "Jog":
                self._cancel_jog()
        elif b == 0x90:
            self.feed_ov = 100
        elif b == 0x91:
            self.feed_ov = min(200, self.feed_ov + 10)
        elif b == 0x92:
            self.feed_ov = max(10, self.feed_ov - 10)
        elif b == 0x93:
            self.feed_ov = min(200, self.feed_ov + 1)
        elif b == 0x94:
            self.feed_ov = max(10, self.feed_ov - 1)
        elif b == 0x95:
            self.rapid_ov = 100
        elif b == 0x96:
            self.rapid_ov = 50
        elif b == 0x97:
            self.rapid_ov = 25
        elif b == 0x99:
            self.spindle_ov = 100
        elif b == 0x9A:
            self.spindle_ov = min(200, self.spindle_ov + 10)
        elif b == 0x9B:
            self.spindle_ov = max(10, self.spindle_ov - 10)
        elif b == 0x9C:
            self.spindle_ov = min(200, self.spindle_ov + 1)
        elif b == 0x9D:
            self.spindle_ov = max(10, self.spindle_ov - 1)
        elif b == 0xA0:
            self.coolant = "M9" if self.coolant == "M8" else "M8"
        elif b == 0xA1:
            self.coolant = "M9" if self.coolant == "M7" else "M7"

    def _soft_reset(self) -> None:
        moving = self.state in ("Run", "Jog", "Home") or (self.state == "Hold" and self.substate == 1)
        pos, wco, g92 = list(self.pos), self.wco, self.g92
        homed = self.homed and not moving
        self._update_emission(force_off=True)
        self._reset_state()
        self.pos, self.planned, self.wco, self.g92, self.homed = pos, list(pos), wco, g92, homed
        self.rx.clear()
        if moving:
            self.out.append("ALARM:3")
        self.out.append("")
        self.out.append(self.banner)
        if moving or (self.settings.get(22, 0) and not self.homed):
            self.state = "Alarm"
            self.out.append(self._msg("'$H'|'$X' to unlock"))
        if moving:
            self.out.append(self._msg("Reset to continue"))

    def _cancel_jog(self) -> None:
        self.planner = deque(b for b in self.planner if b.kind != "jog")
        if self.current and self.current.kind == "jog":
            self.current = None
        self.planned = list(self.pos)
        self.state = "Idle"

    def set_door(self, open_: bool) -> None:
        with self._lock:
            self.door_open = open_
            if open_:
                if self.state in ("Run", "Hold", "Idle", "Jog"):
                    if self.state == "Jog":
                        self._cancel_jog()
                    self.state, self.substate = "Door", 1
                    self._update_emission(force_off=True)
            elif self.state == "Door":
                self.substate = 0

    # ------------------------------------------------------------- simulación
    def _update_locked(self) -> None:
        now = self._clock()
        dt = max(0.0, now - self._last_update) * self.speed_factor
        self._last_update = now
        self._advance(dt)

    def _advance(self, dt: float) -> None:
        if self.state == "Hold" and self.substate == 1:
            self._hold_timer -= dt
            if self._hold_timer <= 0:
                self.substate = 0
            self._update_emission()
            return
        while dt > 0:
            if self.state in ("Hold", "Door", "Alarm", "Sleep"):
                break
            if self.current is None:
                if not self.planner:
                    if self.state in ("Run", "Jog", "Home"):
                        self.state = "Idle"
                    break
                self.current = self.planner.popleft()
                self.current.start = list(self.pos)
                self.current.duration = self._duration(self.current)
                self.current.elapsed = 0.0
                if self.current.kind == "jog":
                    self.state = "Jog"
                elif self.current.kind == "home":
                    self.state = "Home"
                elif self.state not in ("Run",):
                    self.state = "Run"
            blk = self.current
            step = min(dt, max(0.0, blk.duration - blk.elapsed))
            blk.elapsed += step
            dt -= step
            self.sim_time += step
            frac = 1.0 if blk.duration <= 0 else min(1.0, blk.elapsed / blk.duration)
            if blk.kind in ("rapid", "feed", "jog", "home"):
                self.pos = [s + (t - s) * frac for s, t in zip(blk.start, blk.target)]
            self._update_emission()
            if blk.elapsed >= blk.duration:
                self.pos = list(blk.target) if blk.kind != "dwell" else self.pos
                if blk.on_done:
                    blk.on_done()
                self.current = None
                if not self.planner and self.state in ("Run", "Jog", "Home"):
                    self.state = "Idle"
                    self._update_emission()
            if step == 0 and blk.duration > 0 and blk.elapsed < blk.duration:
                break
        self.sim_time += max(0.0, dt) if self.state == "Idle" else 0.0

    def _duration(self, blk: Block) -> float:
        if blk.kind == "dwell":
            return blk.dwell
        if blk.kind == "home":
            return 1.0
        dist = math.dist(blk.start[:3], blk.target[:3])
        if dist == 0 and len(blk.start) > 3:
            dist = abs(blk.target[3] - blk.start[3])
        if blk.kind == "rapid":
            rate = min(self.settings[110], self.settings[111]) * self.rapid_ov / 100.0
        elif blk.kind == "jog":
            rate = blk.feed
        else:
            rate = blk.feed * self.feed_ov / 100.0
        rate = max(rate, 1.0)
        return dist / (rate / 60.0)

    def _laser_mode(self) -> bool:
        return bool(self.settings.get(32, 0))

    def _is_emitting(self) -> tuple[bool, str, float]:
        if self.state in ("Door", "Alarm", "Sleep") or self.check_mode:
            return False, "", 0.0
        blk = self.current
        if self.firmware == "smoothie":
            moving = blk is not None and self.state in ("Run", "Jog") and blk.kind == "feed"
            if moving and blk is not None and blk.s > 0:
                return True, "feed", blk.s
            return False, "", 0.0
        moving = blk is not None and self.state in ("Run", "Jog") and blk.kind in ("feed", "rapid", "jog")
        if blk is not None and moving:
            spindle, s = blk.spindle, blk.s
        else:
            spindle, s = self.spindle, self.s
        s = s * self.spindle_ov / 100.0
        if spindle == "M5" or s <= 0:
            return False, "", 0.0
        if not self._laser_mode():
            return True, "stationary" if not moving else "feed", s
        if moving and blk is not None and blk.kind == "feed" and not (self.state == "Hold"):
            return True, "feed", s
        stationary = self.state == "Idle" or (blk is not None and blk.kind == "dwell" and self.state == "Run")
        if not moving and spindle == "M3" and self.motion in (1, 2, 3) and stationary:
            return True, "stationary", s
        return False, "", 0.0

    def _update_emission(self, force_off: bool = False) -> None:
        on, ctx, s = (False, "", 0.0) if force_off else self._is_emitting()
        if on and not self._emitting:
            self.emissions.append(Emission(self.sim_time, s, ctx, self.state))
        self._emitting = on

    @property
    def laser_emitting(self) -> bool:
        with self._lock:
            self._update_locked()
            return self._emitting

    # -------------------------------------------------------------- recepción
    def _process_rx(self) -> None:
        while True:
            if self.state == "Sleep":
                self.rx.clear()
                return
            idx = self.rx.find(b"\n")
            if idx < 0:
                return
            if len(self.planner) >= self.planner_size:
                return  # el planificador está lleno: la línea espera en el búfer
            if self.state == "Home" or self._dwelling():
                return  # GRBL bloquea durante el homing y las pausas G4
            raw = bytes(self.rx[:idx]).decode("ascii", "replace").strip("\r ")
            if self._needs_sync(raw) and (self.planner or self.current is not None):
                return  # GRBL sincroniza el búfer antes de cambios de husillo o pausas
            del self.rx[: idx + 1]
            if not raw.strip():
                continue
            self.lines_received.append(raw)
            self._defer_ok = False
            if (self.firmware == "smoothie" and self.state == "Alarm"
                    and raw.strip().upper().replace(" ", "") not in ("$X", "M999", "VERSION", "$H")):
                self.out.append("!!")
                continue
            try:
                self._execute_line(raw)
                if not self._defer_ok:
                    self.out.append("ok")
            except GCodeError as exc:
                self.out.append(f"error:{exc.code}")

    def _dwelling(self) -> bool:
        return (self.current is not None and self.current.kind == "dwell") or any(
            b.kind == "dwell" for b in self.planner)

    def _needs_sync(self, raw: str) -> bool:
        text = raw.upper().replace(" ", "")
        if text.startswith("$") or re.search(r"[XYZA][-+.\d]", text):
            return False
        if re.search(r"M0*[012789](?!\d)|M30|G0*4(?![\d.])", text):
            return True
        # En modo láser los cambios M3/M4/M5/S no detienen el movimiento.
        return not self._laser_mode() and bool(re.search(r"M0*[345](?!\d)", text))

    def _execute_line(self, raw: str) -> None:
        line = raw.strip()
        if self.firmware == "smoothie":
            up = line.upper().replace(" ", "")
            if up == "VERSION":
                self.out.append("Build version: edge-OpenLaserSIM, Build date: Jan 1 2026 00:00:00, MCU: LPC1769, "
                                "System Clock: 120MHz")
                return
            if up == "M999":
                self._system_command("$X")
                return
        if line.startswith("$"):
            self._system_command(line)
            return
        clean = re.sub(r"\([^)]*\)", "", line)
        clean = clean.split(";", 1)[0].replace(" ", "").replace("\t", "").upper()
        if not clean:
            return
        if len(clean) >= LINE_MAX:
            raise GCodeError(11)
        if self.state == "Alarm" or self.state == "Jog":
            raise GCodeError(9)
        self._gcode(clean, jog=False)

    # ----------------------------------------------------------- comandos $
    def _system_command(self, line: str) -> None:
        cmd = line.upper()
        if self.firmware == "smoothie" and cmd not in ("$X", "$H"):
            raise GCodeError(3)
        idle_like = self.state in ("Idle", "Alarm") and not self.planner and self.current is None
        if cmd == "$":
            self.out.append("[HLP:$$ $# $G $I $N $x=val $Nx=line $J=line $SLP $C $X $H ~ ! ? ctrl-x]")
            return
        if cmd.startswith("$J="):
            if self.state not in ("Idle", "Jog"):
                raise GCodeError(8)
            self._gcode(cmd[3:], jog=True)
            return
        if cmd == "$G":
            self.out.append(self._gc_state())
            return
        if cmd == "$C":
            if self.state not in ("Idle", "Check"):
                raise GCodeError(8)
            self.check_mode = not self.check_mode
            self.state = "Check" if self.check_mode else "Idle"
            if not self.check_mode:
                self.out.append("[MSG:Disabled]")
                self._soft_reset()
            else:
                self.out.append("[MSG:Enabled]")
            return
        if not idle_like:
            raise GCodeError(8)
        if cmd == "$$":
            for n in sorted(self.settings):
                v = self.settings[n]
                text = f"{v:.3f}" if isinstance(v, float) and not float(v).is_integer() else f"{int(v)}"
                if n in (11, 12, 24, 25, 27, 100, 101, 102, 110, 111, 112, 120, 121, 122, 130, 131, 132):
                    text = f"{float(v):.3f}"
                self.out.append(f"${n}={text}")
            return
        if cmd == "$#":
            for name in ("G54", "G55", "G56", "G57", "G58", "G59"):
                vals = self.wco if name == "G54" else [0.0] * 4
                self.out.append(f"[{name}:{self._fmt(vals)}]")
            self.out.append(f"[G28:{self._fmt([0.0] * 4)}]")
            self.out.append(f"[G30:{self._fmt([0.0] * 4)}]")
            self.out.append(f"[G92:{self._fmt(self.g92)}]")
            self.out.append("[TLO:0.000]")
            self.out.append(f"[PRB:{self._fmt([0.0] * 4)}:0]")
            return
        if cmd == "$I":
            if self.firmware == "grblhal":
                self.out.append("[VER:1.1f.20240101:OpenLaser SIM]")
                self.out.append(f"[OPT:VNMHL,{self.planner_size},{self.rx_size - 1},4]")
                self.out.append("[AXS:4:XYZA]")
                self.out.append("[NEWOPT:ENUMS,RT+,HOME,ES]")
                self.out.append("[FIRMWARE:grblHAL]")
            elif self.firmware == "fluidnc":
                self.out.append("[VER:3.7 FluidNC v3.7.8:]")
                self.out.append("[OPT:PHS]")
                self.out.append("[MSG:INFO: Machine Laser simulado]")
            else:
                self.out.append(f"[VER:{VERSION}.20190825:OpenLaser SIM]")
                self.out.append(f"[OPT:VL,{self.planner_size},{self.rx_size}]")
            return
        if cmd in ("$CD", "$CONFIG/DUMP") and self.firmware == "fluidnc":
            self.out.extend(FLUIDNC_YAML.splitlines())
            return
        if cmd == "$N":
            self.out.append("$N0=")
            self.out.append("$N1=")
            return
        if cmd == "$X":
            if self.state == "Alarm":
                self.out.append("[MSG:Caution: Unlocked]")
            self.state = "Idle"
            return
        if cmd == "$H":
            if not self.settings.get(22, 0):
                raise GCodeError(5)
            self.state = "Home"
            target = [0.0, 0.0, 0.0, self.pos[3]]

            self._defer_ok = True

            def done() -> None:
                self.homed = True
                self.planned = list(target)
                self.out.append("ok")

            self.planner.append(Block("home", target, on_done=done))
            return
        if cmd == "$SLP":
            self._update_emission(force_off=True)
            self.state = "Sleep"
            self.out.append("[MSG:Sleeping]")
            return
        if cmd.startswith("$RST="):
            which = cmd[5:]
            if which in ("$", "*"):
                self.settings = dict(DEFAULT_SETTINGS)
            if which in ("#", "*"):
                self.wco = [0.0] * 4
                self.g92 = [0.0] * 4
            self.out.append("[MSG:Restoring defaults]")
            return
        m = re.fullmatch(r"\$(\d+)=([-+]?\d*\.?\d+)", cmd)
        if m and self.firmware == "fluidnc":
            self.out.append(self._msg("Use the YAML config to change settings", "ERR"))
            raise GCodeError(3)
        if m:
            n = int(m.group(1))
            if n not in DEFAULT_SETTINGS:
                raise GCodeError(3)
            val = float(m.group(2))
            if n in (20,) and val and not self.settings.get(22):
                raise GCodeError(10)
            self.settings[n] = val
            return
        raise GCodeError(3)

    # -------------------------------------------------------------- G-code
    def _gcode(self, text: str, jog: bool) -> None:
        pos = 0
        words: list[tuple[str, float]] = []
        for m in _WORD_RE.finditer(text):
            if m.start() != pos:
                raise GCodeError(1)
            words.append((m.group(1), float(m.group(2))))
            pos = m.end()
        if pos != len(text):
            raise GCodeError(2 if text[pos:pos + 1].isalpha() else 1)

        gs: list[float] = [v for k, v in words if k == "G"]
        ms: list[float] = [v for k, v in words if k == "M"]
        vals: dict[str, float] = {}
        for k, v in words:
            if k in ("G", "M"):
                continue
            if k in vals:
                raise GCodeError(25)
            vals[k] = v
        for g in gs:
            if g == 92.1:
                continue
            if not float(g).is_integer() or int(g) not in _SUPPORTED_G:
                raise GCodeError(20)
        for mm in ms:
            if not float(mm).is_integer() or int(mm) not in _SUPPORTED_M:
                raise GCodeError(20)
        motion_words = [int(g) for g in gs if g in _MOTION_G]
        if len(motion_words) > 1:
            raise GCodeError(21)
        if jog:
            allowed = {20, 21, 53, 90, 91}
            if any(int(g) not in allowed for g in gs) or ms or "F" not in vals or "S" in vals:
                raise GCodeError(16)

        unit = 25.4 if self.inches else 1.0
        absolute = self.absolute
        use_g53 = False
        for g in gs:
            gi = int(g) if g != 92.1 else -1
            if gi == 20:
                self.inches, unit = True, 25.4
            elif gi == 21:
                self.inches, unit = False, 1.0
            elif gi == 90:
                absolute = True
            elif gi == 91:
                absolute = False
            elif gi == 53:
                use_g53 = True
        if not jog:
            self.absolute = absolute
        if "F" in vals:
            if vals["F"] < 0:
                raise GCodeError(4)
            if not jog:
                self.feed = vals["F"] * unit
        if "S" in vals:
            if vals["S"] < 0:
                raise GCodeError(4)
            self.s = min(vals["S"], float(self.settings.get(30, 1000)))
        for mm in ms:
            mi = int(mm)
            if mi in (3, 4, 5):
                self.spindle = f"M{mi}"
            elif mi in (7, 8):
                self.coolant = f"M{mi}"
            elif mi == 9:
                self.coolant = "M9"
            elif mi in (0, 1):
                self.state, self.substate = "Hold", 0
                self._update_emission(force_off=True)
            elif mi in (2, 30):
                self.spindle, self.coolant, self.motion = "M5", "M9", 1
                self.absolute = True

        axes = [k for k in ("X", "Y", "Z", "A") if k in vals]
        if self.axes < 4 and "A" in vals:
            raise GCodeError(20)

        # Comandos no modales con palabras de eje
        if 10 in [int(g) for g in gs if g != 92.1]:
            if "L" not in vals or "P" not in vals:
                raise GCodeError(28)
            if int(vals["P"]) not in (0, 1):
                raise GCodeError(29)
            for i, k in enumerate("XYZA"):
                if k in vals:
                    if int(vals["L"]) == 2:
                        self.wco[i] = vals[k] * unit
                    elif int(vals["L"]) == 20:
                        self.wco[i] = self.planned[i] - self.g92[i] - vals[k] * unit
                    else:
                        raise GCodeError(20)
            self._update_emission()
            return
        if 92.1 in gs:
            self.g92 = [0.0] * 4
            return
        if 92 in gs:
            for i, k in enumerate("XYZA"):
                if k in vals:
                    self.g92[i] = self.planned[i] - self.wco[i] - vals[k] * unit
            return
        if 4 in gs:
            if "P" not in vals:
                raise GCodeError(28)
            self._defer_ok = True
            self._plan(Block("dwell", list(self.planned), dwell=vals["P"], spindle=self.spindle, s=self.s,
                             on_done=lambda: self.out.append("ok")))
            return
        if 28 in gs or 30 in gs:
            self._plan(Block("rapid", [0.0, 0.0, self.planned[2], self.planned[3]],
                             spindle=self.spindle, s=self.s))
            return

        if motion_words and not jog:
            self.motion = motion_words[0]
        motion = 0 if jog else self.motion
        if not axes:
            self._update_emission()
            return
        if not jog and motion == 80:
            raise GCodeError(31)
        target = list(self.planned)
        for i, k in enumerate("XYZA"):
            if k not in vals:
                continue
            v = vals[k] * (unit if k != "A" else 1.0)
            if not absolute:
                target[i] = self.planned[i] + v
            elif use_g53:
                target[i] = v
            else:
                target[i] = v + self.wco[i] + self.g92[i]
        if motion in (2, 3) and not jog:
            if not any(k in vals for k in ("I", "J", "R")):
                raise GCodeError(35)
        if jog:
            feed = vals["F"] * unit
            if self._out_of_travel(target):
                raise GCodeError(15)
            self._plan(Block("jog", target, feed=feed))
            return
        if motion in (1, 2, 3) and self.feed <= 0:
            raise GCodeError(22)
        if self._out_of_travel(target):
            self._soft_limit_alarm()
            return
        if self.check_mode:
            self.planned = target
            return
        kind = "rapid" if motion == 0 else "feed"
        self._plan(Block(kind, target, feed=self.feed, spindle=self.spindle, s=self.s))

    def _out_of_travel(self, target: list[float]) -> bool:
        if not self.settings.get(20, 0):
            return False
        mx, my, mz = self.settings[130], self.settings[131], self.settings[132]
        eps = 1e-6
        return (target[0] < -eps or target[0] > mx + eps or target[1] < -eps or target[1] > my + eps
                or target[2] < -mz - eps or target[2] > eps)

    def _soft_limit_alarm(self) -> None:
        self._update_emission(force_off=True)
        self.planner.clear()
        self.current = None
        self.planned = list(self.pos)
        self.rx.clear()
        self.state = "Alarm"
        self.out.append("ALARM:2")
        self.out.append(self._msg("Reset to continue"))

    def _plan(self, blk: Block) -> None:
        self.planner.append(blk)
        self.planned = list(blk.target)
        if self.state == "Idle":
            self.state = "Jog" if blk.kind == "jog" else "Run"

    # ------------------------------------------------------------- informes
    def _msg(self, text: str, level: str = "INFO") -> str:
        """Mensaje en el formato del firmware: FluidNC antepone el nivel ([MSG:INFO: …])."""
        return f"[MSG:{level}: {text}]" if self.firmware == "fluidnc" else f"[MSG:{text}]"

    def set_estop(self, active: bool) -> None:
        """grblHAL: entrada de parada de emergencia (Pn:E). Al activarse: alarma 10 y láser apagado."""
        with self._lock:
            self.estop = active
            if active:
                self._update_emission(force_off=True)
                self.planner.clear()
                self.current = None
                self.planned = list(self.pos)
                self.homed = False
                self.state = "Alarm"
                self.out.append("ALARM:10")

    def _fmt(self, vals: list[float]) -> str:
        return ",".join(f"{v:.3f}" for v in vals[: max(3, self.axes)])

    def _status_report(self) -> str:
        self._report_count += 1
        state = self.state
        if state in ("Hold", "Door"):
            state = f"{state}:{self.substate}"
        parts = [state]
        if int(self.settings.get(10, 1)) & 1:
            parts.append("MPos:" + self._fmt(self.pos))
        else:
            wpos = [p - w - g for p, w, g in zip(self.pos, self.wco, self.g92)]
            parts.append("WPos:" + self._fmt(wpos))
        parts.append(f"Bf:{self.planner_size - len(self.planner)},{self.rx_size - 1 - len(self.rx)}")
        blk = self.current
        moving = blk is not None and self.state in ("Run", "Jog", "Home")
        feed = 0.0
        if moving and blk is not None:
            if blk.kind == "rapid":
                feed = min(self.settings[110], self.settings[111]) * self.rapid_ov / 100.0
            elif blk.kind in ("feed", "jog"):
                feed = blk.feed * (self.feed_ov / 100.0 if blk.kind == "feed" else 1.0)
        on, _ctx, s_now = self._is_emitting()
        parts.append(f"FS:{feed:.0f},{s_now if on else 0:.0f}")
        pins = ("D" if self.door_open else "") + ("E" if self.estop else "")
        if pins:
            parts.append("Pn:" + pins)
        if self.firmware == "grblhal" and self.settings.get(22, 0):
            parts.append(f"H:{1 if self.homed else 0}")
        if self._report_count % 10 == 1:
            parts.append("WCO:" + self._fmt([w + g for w, g in zip(self.wco, self.g92)]))
        elif self._report_count % 10 == 2:
            parts.append(f"Ov:{self.feed_ov},{self.rapid_ov},{self.spindle_ov}")
            acc = ""
            if self.spindle == "M3":
                acc += "S"
            elif self.spindle == "M4":
                acc += "C"
            if self.coolant == "M8":
                acc += "F"
            elif self.coolant == "M7":
                acc += "M"
            if acc:
                parts.append("A:" + acc)
        return "<" + "|".join(parts) + ">"

    def _gc_state(self) -> str:
        return (f"[GC:G{self.motion} G54 G17 {'G20' if self.inches else 'G21'} "
                f"{'G90' if self.absolute else 'G91'} G94 {self.spindle} {self.coolant} T0 "
                f"F{self.feed:.0f} S{self.s:.0f}]")

    # ---------------------------------------------------------- inspección
    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._update_locked()
            return {"state": self.state, "substate": self.substate, "pos": list(self.pos),
                    "wco": list(self.wco), "homed": self.homed, "spindle": self.spindle, "s": self.s,
                    "planner": len(self.planner), "rx": len(self.rx), "emitting": self._emitting}

    def wait_idle(self, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            snap = self.snapshot()
            if snap["state"] in ("Idle", "Alarm") and not snap["planner"] and self.current is None:
                return True
            time.sleep(0.005)
        return False
