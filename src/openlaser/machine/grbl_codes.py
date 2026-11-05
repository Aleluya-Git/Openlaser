"""Tablas de alarmas y errores de GRBL 1.1 con explicación para el operador.

Los textos son redacción propia basada en la documentación pública del
protocolo GRBL 1.1 (códigos numéricos estándar). ``recovery`` sólo se rellena
cuando existe un comando de recuperación apropiado; la aplicación nunca lo
envía automáticamente: el usuario debe pulsarlo de forma explícita.
"""
from __future__ import annotations

from dataclasses import dataclass

from openlaser.i18n import pick


@dataclass(frozen=True)
class CodeInfo:
    code: int
    kind: str  # "alarm" | "error"
    description: dict[str, str]
    cause: dict[str, str]
    action: dict[str, str]
    recovery: str | None = None

    @property
    def text(self) -> str:
        return pick(self.description)

    @property
    def cause_text(self) -> str:
        return pick(self.cause)

    @property
    def action_text(self) -> str:
        return pick(self.action)


def _a(code: int, es: str, en: str, ces: str, cen: str, aes: str, aen: str, rec: str | None) -> CodeInfo:
    return CodeInfo(code, "alarm", {"es": es, "en": en}, {"es": ces, "en": cen}, {"es": aes, "en": aen}, rec)


def _e(code: int, es: str, en: str, ces: str, cen: str, aes: str, aen: str) -> CodeInfo:
    return CodeInfo(code, "error", {"es": es, "en": en}, {"es": ces, "en": cen}, {"es": aes, "en": aen}, None)


ALARMS: dict[int, CodeInfo] = {a.code: a for a in [
    _a(1, "Final de carrera físico activado", "Hard limit triggered",
       "Un interruptor de límite se activó durante el movimiento; la posición ya no es fiable.",
       "A limit switch triggered during motion; machine position is no longer reliable.",
       "Compruebe que nada bloquea el cabezal, retire la causa y realice Home antes de continuar.",
       "Check nothing blocks the head, clear the cause and run Home before continuing.", "$H"),
    _a(2, "Límite de software: destino fuera del recorrido", "Soft limit: target exceeds machine travel",
       "El G-code pidió un movimiento fuera del área de trabajo configurada.",
       "The G-code requested a move outside the configured travel.",
       "Revise la posición del diseño y el origen. Desbloquee sólo tras corregirlo.",
       "Review design placement and origin. Unlock only after fixing it.", "$X"),
    _a(3, "Reinicio durante el movimiento", "Reset while in motion",
       "Se envió un reinicio con la máquina en movimiento; puede haberse perdido la posición.",
       "A reset was issued while moving; position may be lost.",
       "Verifique la máquina. Realice Home (recomendado) o desbloquee si conoce la posición.",
       "Inspect the machine. Run Home (recommended) or unlock if position is known.", "$H"),
    _a(4, "Fallo de sonda: estado inicial incorrecto", "Probe fail: initial state",
       "La sonda no estaba en el estado esperado antes del ciclo.",
       "The probe was not in the expected state before the cycle.",
       "Compruebe el cableado de la sonda.", "Check the probe wiring.", "$X"),
    _a(5, "Fallo de sonda: sin contacto", "Probe fail: no contact",
       "La sonda no hizo contacto dentro de la distancia programada.",
       "The probe did not make contact within the programmed travel.",
       "Revise la distancia de sondeo y la posición.", "Review probing distance and position.", "$X"),
    _a(6, "Fallo de homing: reinicio durante el ciclo", "Homing fail: reset during cycle",
       "El ciclo de homing se interrumpió con un reinicio.", "The homing cycle was interrupted by a reset.",
       "Repita el homing cuando la máquina esté despejada.", "Repeat homing once the machine is clear.", "$H"),
    _a(7, "Fallo de homing: puerta abierta", "Homing fail: door opened",
       "La puerta de seguridad se abrió durante el homing.", "The safety door opened during homing.",
       "Cierre la puerta y repita el homing.", "Close the door and repeat homing.", "$H"),
    _a(8, "Fallo de homing: no se liberó el final de carrera", "Homing fail: pull-off did not clear switch",
       "Tras el retroceso, el interruptor seguía activado.", "After pull-off the switch was still active.",
       "Aumente $27 (retroceso) o revise el interruptor.", "Increase $27 (pull-off) or check the switch.", "$H"),
    _a(9, "Fallo de homing: no se encontró el final de carrera", "Homing fail: limit switch not found",
       "El cabezal no encontró el interruptor dentro del recorrido de búsqueda.",
       "The head did not find the switch within the search travel.",
       "Revise interruptores, cableado y $130-$132.", "Check switches, wiring and $130-$132.", "$H"),
    _a(10, "Parada de emergencia del controlador (grblHAL)", "Controller emergency stop (grblHAL)",
       "La entrada de parada de emergencia está activa.", "The emergency stop input is active.",
       "Libere la seta de emergencia tras eliminar el riesgo y reinicie.",
       "Release the E-stop once the hazard is removed, then reset.", None),
]}

_GEN_CAUSE = {"es": "Línea de G-code o comando no válido para el controlador.",
              "en": "G-code line or command not valid for the controller."}
_GEN_ACTION = {"es": "Revise la línea indicada en la consola y el generador de G-code.",
               "en": "Check the line shown in the console and the G-code generator."}

_ERR_TEXTS: list[tuple[int, str, str]] = [
    (1, "Palabra G-code sin letra", "G-code word without a letter"),
    (2, "Valor numérico ausente o con formato incorrecto", "Missing or malformed numeric value"),
    (3, "Comando '$' no reconocido o no compatible", "'$' system command not recognised or supported"),
    (4, "Valor negativo donde se esperaba positivo", "Negative value where a positive one was expected"),
    (5, "Homing no habilitado ($22)", "Homing is not enabled ($22)"),
    (6, "Pulso de paso mínimo debe ser mayor de 3 µs", "Minimum step pulse must be greater than 3 µs"),
    (7, "Fallo al leer EEPROM; valores restaurados", "EEPROM read failed; defaults restored"),
    (8, "Comando '$' sólo admitido en reposo (Idle)", "'$' command only allowed while Idle"),
    (9, "G-code bloqueado en estado de alarma o jog", "G-code locked out during alarm or jog"),
    (10, "Límites de software requieren homing habilitado", "Soft limits require homing enabled"),
    (11, "Línea demasiado larga", "Line too long"),
    (12, "Valor supera la velocidad de pasos máxima", "Value exceeds maximum step rate"),
    (13, "Puerta de seguridad abierta", "Safety door opened"),
    (14, "Línea de arranque o información demasiado larga", "Startup/build line too long"),
    (15, "Destino de jog fuera del recorrido", "Jog target exceeds machine travel"),
    (16, "Comando de jog sin '=' o con G-code no permitido", "Jog command without '=' or with prohibited G-code"),
    (17, "Modo láser requiere salida PWM", "Laser mode requires PWM output"),
    (20, "G-code no compatible o no válido", "Unsupported or invalid G-code"),
    (21, "Dos comandos del mismo grupo modal", "Two commands from the same modal group"),
    (22, "Velocidad de avance no definida", "Feed rate not set"),
    (23, "El comando requiere un valor entero", "Command requires an integer value"),
    (24, "Dos comandos que usan palabras de eje", "Two commands both using axis words"),
    (25, "Palabra repetida en el bloque", "Repeated word in block"),
    (26, "Faltan palabras de eje necesarias", "Required axis words missing"),
    (27, "Número de línea fuera de rango", "Line number out of range"),
    (28, "Falta un valor P o L obligatorio", "Missing required P or L value"),
    (29, "Sistema de coordenadas no compatible", "Unsupported work coordinate system"),
    (30, "G53 requiere modo G0 o G1", "G53 requires G0 or G1 motion mode"),
    (31, "Palabras de eje sin uso con G80 activo", "Unused axis words with G80 active"),
    (32, "Arco sin palabras de eje en el plano", "Arc without axis words in plane"),
    (33, "Destino de movimiento no válido", "Invalid motion target"),
    (34, "Error de radio de arco", "Arc radius error"),
    (35, "Arco sin I/J/K en el plano", "Arc without I/J/K in plane"),
    (36, "Palabras G-code sobrantes en el bloque", "Unused G-code words left in block"),
    (37, "Compensación de herramienta no asignada al eje", "Tool length offset not on configured axis"),
    (38, "Número de herramienta demasiado alto", "Tool number above maximum"),
]

_SPECIAL_ERR: dict[int, tuple[dict[str, str], dict[str, str]]] = {
    8: ({"es": "Se envió un comando '$' mientras la máquina se movía o ejecutaba un trabajo.",
         "en": "A '$' command was sent while moving or running a job."},
        {"es": "Espere a que el estado sea Idle y repita el comando.",
         "en": "Wait until the state is Idle and retry."}),
    9: ({"es": "La máquina está en alarma; el G-code se rechaza por seguridad.",
         "en": "The machine is in alarm; G-code is rejected for safety."},
        {"es": "Resuelva la causa de la alarma y realice Home o desbloquee manualmente.",
         "en": "Resolve the alarm cause, then Home or unlock manually."}),
    13: ({"es": "Se detectó la puerta abierta.", "en": "The door was detected open."},
         {"es": "Cierre la puerta y reanude sólo si es seguro.", "en": "Close the door and resume only if safe."}),
    15: ({"es": "El movimiento manual saldría del área de trabajo.", "en": "The jog would leave the work area."},
         {"es": "Use un paso menor o compruebe el homing.", "en": "Use a smaller step or check homing."}),
    22: ({"es": "Falta F en el primer movimiento G1/G2/G3.", "en": "F missing on the first G1/G2/G3 move."},
         {"es": "Revise el generador de G-code.", "en": "Check the G-code generator."}),
}

ERRORS: dict[int, CodeInfo] = {}
for _code, _es, _en in _ERR_TEXTS:
    _cause, _action = _SPECIAL_ERR.get(_code, (_GEN_CAUSE, _GEN_ACTION))
    ERRORS[_code] = _e(_code, _es, _en, _cause["es"], _cause["en"], _action["es"], _action["en"])


def alarm_info(code: int, dialect: str = "grbl") -> CodeInfo:
    """Alarma con explicación; las propias de grblHAL/FluidNC tienen prioridad para su firmware."""
    from openlaser.machine import dialects
    d = dialects.get(dialect)
    extra = d.alarms.get(code)
    if extra is not None:
        return _a(code, *extra)
    if code in ALARMS and (code <= 9 or d.id == "grbl"):
        return ALARMS[code]
    return _a(
        code, f"Alarma desconocida {code}", f"Unknown alarm {code}",
        f"Código no documentado para {d.name}.", f"Code not documented for {d.name}.",
        "Consulte la documentación de su firmware.", "Check your firmware documentation.", None)


def error_info(code: int, dialect: str = "grbl") -> CodeInfo:
    from openlaser.machine import dialects
    d = dialects.get(dialect)
    extra = d.errors.get(code)
    if extra is not None:
        return _e(code, extra[0], extra[1], _GEN_CAUSE["es"], _GEN_CAUSE["en"], _GEN_ACTION["es"], _GEN_ACTION["en"])
    if code in ERRORS:
        return ERRORS[code]
    hint_es = "Consulte la documentación de su firmware."
    hint_en = "Check your firmware documentation."
    if d.error_help:
        from openlaser.i18n import tr
        hint_es = hint_en = tr(d.error_help)
    return _e(
        code, f"Error desconocido {code}", f"Unknown error {code}",
        f"Código no documentado para {d.name}.", f"Code not documented for {d.name}.", hint_es, hint_en)
