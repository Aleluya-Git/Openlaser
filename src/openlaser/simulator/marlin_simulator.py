"""Simulador de Marlin 2.x con ``LASER_FEATURE`` (Fase 6) para probar el protocolo envío-respuesta.

Reproduce lo que importa a la aplicación, incluidos los comportamientos **peligrosos** para que las
pruebas comprueben que la aplicación los evita:

* Cola de comandos de 4 líneas (``BUFSIZE``) y planificador de 16 bloques; ``ok`` al encolar el
  movimiento. Si el anfitrión envía más líneas de las que caben, se cuentan en
  :attr:`overflow_count` (en una placa real se perderían datos).
* ``M3``/``M4``/``M5`` sincronizan el planificador. Sin modo en línea, ``M3 S…`` enciende el láser
  **parado y durante todos los movimientos, también G0**; con ``I`` (modo en línea) la potencia es
  la ``S`` de cada ``G1``, y un ``G0`` usa la potencia en línea vigente (el caso que la traducción
  de la aplicación evita convirtiendo los G0).
* ``M114`` (posición planificada), ``M115`` (firmware y capacidades), ``M400``, ``G4 P(ms)/S(s)``,
  ``G28``, ``G90/G91/G92/G21``, ``M410`` (parada rápida inmediata con ``EMERGENCY_PARSER``),
  ``M112`` (kill inmediato: hay que reiniciar la placa), ``M999``.
* ``echo:busy: processing`` durante esperas largas.
"""
from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from openlaser.simulator.grbl_simulator import Emission

BUFSIZE = 4
PLANNER = 16
_WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))?")


@dataclass
class _Move:
    kind: str                       # "rapid", "feed", "dwell", "home"
    target: list[float]
    feed: float
    power: float                    # 0…255 aplicada durante el bloque
    dwell: float = 0.0
    start: list[float] = field(default_factory=list)
    duration: float = 0.0
    elapsed: float = 0.0


class MarlinSimulator:
    firmware = "marlin"

    def __init__(self, speed_factor: float = 1.0, width: float = 400.0, height: float = 400.0,
                 emergency_parser: bool = True, clock: Callable[[], float] = time.monotonic) -> None:
        self.speed_factor = speed_factor
        self.width, self.height = width, height
        self.emergency_parser = emergency_parser
        self._clock = clock
        self._lock = threading.RLock()
        self.out: deque[str] = deque()
        self.rx = bytearray()
        self.cmdq: deque[str] = deque()
        self.overflow_count = 0
        self.emissions: list[Emission] = []
        self.lines_received: list[str] = []
        self.max_cmdq = 0
        self.powered = False
        self._reset()

    def _reset(self) -> None:
        self.pos = [0.0, 0.0, 0.0]
        self.planned = list(self.pos)
        self.planner: deque[_Move] = deque()
        self.current: _Move | None = None
        self.absolute = True
        self.feed = 1500.0
        self.cutter = "M5"
        self.inline = False
        self.power = 0.0              # potencia fuera de línea (M3 S…)
        self.inline_power = 0.0
        self.killed = False
        self.homed = False
        self.sim_time = 0.0
        self._emitting = False
        self._waiting: str | None = None   # "sync" mientras M400/M3/M5/G28 esperan
        self._pending_ok = False
        self._busy_t = 0.0
        self._last = self._clock()

    # ------------------------------------------------------------- E/S
    def power_on(self) -> None:
        with self._lock:
            self._reset()
            self.cmdq.clear()
            self.rx.clear()
            self.powered = True
            self.out.extend(["start", "echo:Marlin 2.1.2.4 (OpenLaser SIM)", "echo: Last Updated: 2026-01-01"])

    def set_door(self, open_: bool) -> None:  # Marlin no tiene puerta de seguridad estándar
        pass

    def receive(self, data: bytes) -> None:
        with self._lock:
            self._update()
            self.rx.extend(data)
            while b"\n" in self.rx:
                i = self.rx.index(b"\n")
                line = bytes(self.rx[:i]).decode("ascii", "replace").strip()
                del self.rx[: i + 1]
                if not line:
                    continue
                self.lines_received.append(line)
                up = line.upper().replace(" ", "")
                if self.emergency_parser and up.startswith("M112"):
                    self._kill()
                    continue
                if self.emergency_parser and up.startswith("M410"):
                    self._quickstop()
                    self.out.append("ok")
                    continue
                if len(self.cmdq) >= BUFSIZE:
                    self.overflow_count += 1
                    continue
                self.cmdq.append(line)
                self.max_cmdq = max(self.max_cmdq, len(self.cmdq))
            self._process()

    def read_output(self) -> list[str]:
        with self._lock:
            self._update()
            self._process()
            lines = list(self.out)
            self.out.clear()
            return lines

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            self._update()
            return {"pos": list(self.pos), "planner": len(self.planner) + (1 if self.current else 0),
                    "killed": self.killed, "emitting": self._emitting, "homed": self.homed}

    @property
    def laser_emitting(self) -> bool:
        with self._lock:
            self._update()
            return self._emitting

    # ------------------------------------------------------------ ejecución
    def _kill(self) -> None:
        self.planner.clear()
        self.current = None
        self.cmdq.clear()
        self.power = self.inline_power = 0.0
        self.cutter = "M5"
        self.killed = True
        self._set_emission(False, "")
        self.out.append("Error:Printer halted. kill() called!")

    def _quickstop(self) -> None:
        self.planner.clear()
        self.current = None
        self.planned = list(self.pos)
        self._waiting = None
        self._set_emission(self.cutter != "M5" and not self.inline and self.power > 0, "stationary")

    def _process(self) -> None:
        if self.killed:
            self.cmdq.clear()
            return
        while self.cmdq:
            if self._waiting == "sync":
                if self.planner or self.current:
                    return
                self._waiting = None
                if self._pending_ok:
                    self._pending_ok = False
                    self.out.append("ok")
                continue
            if len(self.planner) >= PLANNER:
                return
            line = self.cmdq.popleft()
            try:
                done = self._execute(line)
            except ValueError:
                self.out.append(f'echo:Unknown command: "{line}"')
                done = True
            if done:
                self.out.append("ok")
            else:
                self._pending_ok = True
                self._waiting = "sync"
        if self._waiting == "sync" and not (self.planner or self.current):
            self._waiting = None
            if self._pending_ok:
                self._pending_ok = False
                self.out.append("ok")

    def _words(self, line: str) -> list[tuple[str, float | None]]:
        text = line.upper().split(";", 1)[0]
        return [(m.group(1), float(m.group(2)) if m.group(2) is not None else None) for m in _WORD.finditer(text)]

    def _execute(self, line: str) -> bool:
        """Ejecuta una línea. Devuelve False si el «ok» debe esperar a que el planificador se vacíe."""
        if line.strip().upper() == "VERSION" or line.startswith("$"):
            raise ValueError(line)
        words = self._words(line)
        if not words:
            return True
        vals = {k: v for k, v in words}
        g = [v for k, v in words if k == "G"]
        m = [v for k, v in words if k == "M"]
        if m:
            code = int(m[0] or 0)
            if code == 115:
                self.out.append("FIRMWARE_NAME:Marlin 2.1.2.4 (OpenLaser SIM) SOURCE_CODE_URL:github.com/MarlinFirmware/"
                                "Marlin PROTOCOL_VERSION:1.0 MACHINE_TYPE:Laser EXTRUDER_COUNT:0")
                self.out.append(f"Cap:EMERGENCY_PARSER:{1 if self.emergency_parser else 0}")
                self.out.append("Cap:AUTOREPORT_POS:1")
                return True
            if code == 114:
                self.out.append(f"X:{self.planned[0]:.2f} Y:{self.planned[1]:.2f} Z:{self.planned[2]:.2f} E:0.00 "
                                "Count X:0 Y:0 Z:0")
                return True
            if code in (3, 4):
                self.inline = "I" in vals
                self.cutter = f"M{code}"
                s = vals.get("S")
                if s is not None:
                    if self.inline:
                        self.inline_power = min(255.0, s)
                    else:
                        self.power = min(255.0, s)
                elif not self.inline:
                    self.power = 255.0
                if self.planner or self.current:
                    return False  # M3/M4 sincronizan; se aplica al terminar (simplificación)
                self._apply_static()
                return True
            if code == 5:
                self.cutter, self.power, self.inline_power, self.inline = "M5", 0.0, 0.0, False
                if self.planner or self.current:
                    return False
                self._apply_static()
                return True
            if code == 400:
                return not (self.planner or self.current)
            if code in (999, 154, 17, 18, 84, 220, 221):
                return True
            raise ValueError(line)
        if g:
            for gm in g:  # modales en la misma línea («G91G0X10», «G90G21»)
                if gm in (90, 91):
                    self.absolute = gm == 90
            motion = [gm for gm in g if gm not in (90, 91, 21, 20, 17)]
            if not motion:
                return True
            code = motion[0]
            if code == 92:
                return True
            if code == 28:
                self.planner.append(_Move("home", [0.0, 0.0, self.planned[2]], 3000.0, 0.0))
                self.planned = [0.0, 0.0, self.planned[2]]
                self.homed = True
                return False
            if code == 4:
                secs = (vals.get("P") or 0.0) / 1000.0 if "P" in vals else (vals.get("S") or 0.0)
                self.planner.append(_Move("dwell", list(self.planned), 0.0, self._block_power(1), dwell=secs))
                return True
            if code in (0, 1):
                if "F" in vals and vals["F"]:
                    self.feed = vals["F"]  # en Marlin G0 también usa F
                if "S" in vals and self.inline and vals["S"] is not None:
                    self.inline_power = min(255.0, vals["S"])
                target = list(self.planned)
                for i, ax in enumerate("XYZ"):
                    v = vals.get(ax)
                    if v is not None:
                        target[i] = v if self.absolute else self.planned[i] + v
                if target != self.planned:
                    kind = "rapid" if code == 0 else "feed"
                    self.planner.append(_Move(kind, target, self.feed, self._block_power(int(code))))
                    self.planned = target
                return True
        raise ValueError(line)

    def _block_power(self, gcode: int) -> float:
        if self.cutter == "M5":
            return 0.0
        return self.inline_power if self.inline else self.power

    def _apply_static(self) -> None:
        on = self.cutter != "M5" and not self.inline and self.power > 0
        self._set_emission(on, "stationary")

    def _set_emission(self, on: bool, ctx: str, s: float = 0.0) -> None:
        if on and not self._emitting:
            self.emissions.append(Emission(self.sim_time, s or self.power, ctx, "Run" if self.current else "Idle"))
        self._emitting = on

    def _update(self) -> None:
        now = self._clock()
        dt = max(0.0, now - self._last) * self.speed_factor
        self._last = now
        if self.killed:
            return
        self.sim_time += dt
        if self._waiting == "sync" and (self.planner or self.current):
            self._busy_t += dt
            if self._busy_t > 2.0 * self.speed_factor:
                self._busy_t = 0.0
                self.out.append("echo:busy: processing")
        while dt > 0:
            if self.current is None:
                if not self.planner:
                    self._apply_static()
                    break
                self.current = self.planner.popleft()
                self.current.start = list(self.pos)
                if self.current.kind == "dwell":
                    self.current.duration = self.current.dwell
                else:
                    dist = math.dist(self.current.start, self.current.target)
                    self.current.duration = dist / max(self.current.feed / 60.0, 1e-6)
                blk = self.current
                on = blk.power > 0 and (blk.kind in ("feed", "rapid") or not self.inline)
                ctx = {"feed": "feed", "rapid": "rapid"}.get(blk.kind, "stationary")
                self._set_emission(on, ctx, blk.power)
            blk = self.current
            step = min(dt, blk.duration - blk.elapsed)
            blk.elapsed += step
            dt -= step
            f = 1.0 if blk.duration <= 0 else blk.elapsed / blk.duration
            if blk.kind not in ("dwell",):
                self.pos = [a + (b - a) * f for a, b in zip(blk.start, blk.target)]
            if blk.elapsed >= blk.duration - 1e-12:
                self.pos = list(blk.target) if blk.kind != "dwell" else self.pos
                self.current = None
            elif step == 0:
                break
        self._process()
