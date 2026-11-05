"""Dialectos de firmware compatibles con el protocolo GRBL: GRBL 1.1, grblHAL y FluidNC.

Los tres usan el mismo protocolo de flujo (``ok``/``error:``, conteo de caracteres, comandos de
tiempo real, ``$J=``, ``$H``, ``$X``) y el mismo modo láser, así que comparten el controlador de
máquina. Este módulo recoge **sólo las diferencias** que afectan a la aplicación:

* cómo se reconoce cada firmware (mensaje de bienvenida, ``$I``, campo ``FW:`` del estado);
* el tamaño real del búfer de recepción (``[OPT:…,<bloques>,<búfer>]``);
* alarmas y errores adicionales;
* si los parámetros numerados ``$n=v`` se pueden escribir (FluidNC los toma de su archivo YAML);
* campos adicionales del informe de estado (``H:`` de grblHAL = homing hecho).

Referencias: protocolo GRBL 1.1 (wiki de gnea/grbl), «Report extensions» del wiki de grblHAL/core
y la documentación de comandos de FluidNC. Los textos son redacción propia.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from openlaser.i18n import N_


@dataclass(frozen=True)
class Dialect:
    id: str
    name: str
    numbered_settings_writable: bool = True
    # Alarmas tras las que la posición deja de ser fiable (exigen Home).
    lost_position_alarms: frozenset[int] = frozenset({1, 3, 6, 7, 8, 9})
    # Alarmas propias: código → (texto es, texto en, causa es, causa en, acción es, acción en, recuperación)
    alarms: dict[int, tuple[str, str, str, str, str, str, str | None]] = field(default_factory=dict)
    errors: dict[int, tuple[str, str]] = field(default_factory=dict)
    error_help: str = ""        # cómo consultar un error no documentado aquí
    notes: str = ""


GRBL = Dialect("grbl", "GRBL 1.1+", lost_position_alarms=frozenset({1, 3, 6, 7, 8, 9, 10}))

GRBLHAL = Dialect(
    "grblhal", "grblHAL",
    lost_position_alarms=frozenset({1, 3, 6, 7, 8, 9, 10, 11, 12, 15, 17}),
    alarms={
        10: ("Parada de emergencia activada", "E-stop asserted",
             "La entrada de parada de emergencia de la placa está activa.",
             "The board's emergency stop input is active.",
             "Libere la seta de emergencia, compruebe la máquina y reinicie; después haga Home.",
             "Release the emergency stop, check the machine and reset; then run Home.", None),
        11: ("Se requiere Home", "Homing required",
             "El controlador exige un ciclo de homing antes de mover.",
             "The controller requires a homing cycle before any motion.",
             "Despeje la máquina y pulse Home.", "Clear the machine and press Home.", "$H"),
        12: ("Final de carrera activado", "Limit switch engaged",
             "Un final de carrera sigue activo.", "A limit switch is still engaged.",
             "Libere el interruptor (mueva el cabezal a mano con la máquina apagada) antes de continuar.",
             "Release the switch (move the head by hand with power off) before continuing.", None),
        13: ("Protección de la sonda activada", "Probe protection triggered",
             "La sonda se activó fuera de un ciclo de sondeo.", "The probe triggered outside a probing cycle.",
             "Revise la sonda y su cableado.", "Check the probe and its wiring.", None),
        14: ("Tiempo agotado esperando la velocidad del husillo", "Spindle at-speed timeout",
             "El husillo no confirmó su velocidad a tiempo.", "The spindle did not report at-speed in time.",
             "Revise la configuración del husillo/láser.", "Check the spindle/laser configuration.", None),
        15: ("Fallo de homing", "Homing failed",
             "No se encontró un final de carrera (por ejemplo, el segundo de un eje autoescuadrado).",
             "A limit switch was not found (e.g. the second switch of an auto-squared axis).",
             "Revise los finales de carrera y repita Home.", "Check the limit switches and retry Home.", "$H"),
        16: ("Fallo en la autoprueba de arranque", "Power-on self test failed",
             "El controlador detectó un fallo al arrancar.", "The controller detected a fault at start-up.",
             "Revise los mensajes del controlador y reinícielo.", "Check the controller messages and reset it.", None),
        17: ("Fallo de motor", "Motor fault",
             "Un driver de motor informó de un fallo.", "A motor driver reported a fault.",
             "Apague la máquina y revise motores, drivers y alimentación.",
             "Power off and check motors, drivers and power supply.", None),
    },
    error_help=N_("grblHAL lista sus códigos de error con $EE si se compiló con enumeraciones (ENUMS en $I)."),
    notes=N_("Búfer de recepción grande (se lee de $I), estado «Tool» y campo H: de homing."),
)

FLUIDNC = Dialect(
    "fluidnc", "FluidNC",
    numbered_settings_writable=False,
    lost_position_alarms=frozenset({1, 3, 6, 7, 8, 9, 13, 14}),
    alarms={
        10: ("Fallo del control del husillo/láser", "Spindle control failure",
             "El controlador detectó un fallo en el husillo o láser.", "The controller detected a spindle/laser fault.",
             "Revise la sección del husillo en la configuración YAML.", "Check the spindle section of the YAML config.",
             None),
        11: ("Entrada de control activa", "Control pin active",
             "Una entrada de control (parada, puerta, pausa…) estaba activa al arrancar.",
             "A control input (stop, door, hold…) was active at start-up.",
             "Libere la entrada y reinicie.", "Release the input and reset.", None),
        12: ("Finales de carrera ambiguos en homing", "Ambiguous homing switch",
             "Se activó un final de carrera que no corresponde al eje en homing.",
             "A switch not belonging to the homing axis was triggered.",
             "Revise el cableado de los finales de carrera.", "Check the limit switch wiring.", "$H"),
        13: ("Parada brusca", "Hard stop",
             "El movimiento se detuvo sin desaceleración; la posición no es fiable.",
             "Motion stopped without deceleration; position is not reliable.",
             "Revise la máquina y haga Home.", "Check the machine and run Home.", "$H"),
        14: ("Máquina sin referencia (Home)", "Unhomed",
             "La configuración exige homing antes de mover.", "The configuration requires homing before motion.",
             "Despeje la máquina y pulse Home.", "Clear the machine and press Home.", "$H"),
        15: ("Fallo de inicialización", "Initialisation failure",
             "La configuración YAML tiene errores o falta hardware.",
             "The YAML configuration has errors or hardware is missing.",
             "Lea los mensajes [MSG:ERR] de la consola y corrija la configuración.",
             "Read the [MSG:ERR] console messages and fix the configuration.", None),
    },
    error_help=N_("FluidNC explica cada error con el comando $E=<número> (o la lista completa con $E)."),
    notes=N_("Parámetros en un archivo YAML ($CD para verlo); los $n numerados son de sólo lectura."),
)

# Fase 6: firmwares de protocolo envío-respuesta (ver host_protocols.py y host_controller.py).
MARLIN = Dialect(
    "marlin", "Marlin", numbered_settings_writable=False, lost_position_alarms=frozenset({-1}),
    error_help=N_("Marlin describe el error en la propia línea «Error:…» de la consola."),
    notes=N_("Envío-respuesta (una línea, un «ok»), estado deducido por la aplicación, láser en modo en línea "
             "(M3 I) y S 0–255. Experimental."))
SMOOTHIE = Dialect(
    "smoothie", "Smoothieware", numbered_settings_writable=False, lost_position_alarms=frozenset({-1}),
    error_help=N_("Smoothieware describe el error en la propia línea de la consola."),
    notes=N_("grbl_mode: informe «?», $H y $X; envío-respuesta; el láser sólo emite en G1 con S 0–1. Experimental."))

DIALECTS: dict[str, Dialect] = {d.id: d for d in (GRBL, GRBLHAL, FLUIDNC, MARLIN, SMOOTHIE)}

_FLUID_RE = re.compile(r"FluidNC", re.IGNORECASE)
_HAL_RE = re.compile(r"^\s*GrblHAL\b", re.IGNORECASE)


def get(dialect_id: str) -> Dialect:
    return DIALECTS.get((dialect_id or "grbl").lower(), GRBL)


def detect_welcome(raw: str) -> str | None:
    """``Grbl 1.1h […]`` → grbl · ``GrblHAL 1.1f […]`` → grblhal · ``Grbl 3.7 [FluidNC v3.7.8 …]`` → fluidnc."""
    if _FLUID_RE.search(raw):
        return "fluidnc"
    if _HAL_RE.match(raw):
        return "grblhal"
    if raw.strip().lower().startswith("grbl"):
        return "grbl"
    return None


def detect_feedback(kind: str, payload: str) -> str | None:
    """``[FIRMWARE:grblHAL]``, ``[VER:3.7 FluidNC v3.7.8:]`` u otras líneas de ``$I``."""
    k = kind.upper()
    if k == "FIRMWARE":
        if "grblhal" in payload.lower():
            return "grblhal"
        if "fluidnc" in payload.lower():
            return "fluidnc"
    if k == "VER" and _FLUID_RE.search(payload):
        return "fluidnc"
    if k == "NEWOPT":
        return "grblhal"  # extensión exclusiva de grblHAL
    return None


def rx_buffer_from_opt(payload: str) -> int | None:
    """``VL,15,128`` / ``VNMHL,35,1023,4`` → tamaño del búfer de recepción (None si no viene)."""
    parts = [p.strip() for p in payload.split(",")]
    if len(parts) < 3:
        return None
    try:
        size = int(parts[2])
    except ValueError:
        return None
    return size if size > 0 else None


def usable_rx(reported: int) -> int:
    """Bytes que se pueden tener en vuelo: uno menos que el búfer (GRBL reserva un byte) y entre 80 y 4096."""
    return max(80, min(4096, reported - 1))
