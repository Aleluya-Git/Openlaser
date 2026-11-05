"""Descripción, validación y comparación de los parámetros ``$$`` de GRBL 1.1."""
from __future__ import annotations

from dataclasses import dataclass

from openlaser.i18n import pick


@dataclass(frozen=True)
class SettingInfo:
    number: int
    name: dict[str, str]
    unit: str
    kind: str  # "int" | "float" | "bool" | "mask"
    minimum: float
    maximum: float
    help: dict[str, str]
    dangerous: bool = False

    @property
    def label(self) -> str:
        return pick(self.name)

    @property
    def help_text(self) -> str:
        return pick(self.help)


def _s(n: int, es: str, en: str, unit: str, kind: str, lo: float, hi: float,
       hes: str, hen: str, dangerous: bool = False) -> SettingInfo:
    return SettingInfo(n, {"es": es, "en": en}, unit, kind, lo, hi, {"es": hes, "en": hen}, dangerous)


SETTINGS: dict[int, SettingInfo] = {s.number: s for s in [
    _s(0, "Pulso de paso", "Step pulse", "µs", "int", 3, 255,
       "Duración del pulso de paso enviado a los drivers.", "Length of the step pulse sent to the drivers."),
    _s(1, "Retardo de reposo de pasos", "Step idle delay", "ms", "int", 0, 255,
       "Tiempo antes de desactivar motores en reposo (255 = siempre activos).",
       "Time before disabling idle motors (255 = always on)."),
    _s(2, "Inversión de pulso", "Step port invert", "máscara", "mask", 0, 255,
       "Invierte la señal de paso por eje.", "Inverts the step signal per axis."),
    _s(3, "Inversión de dirección", "Direction port invert", "máscara", "mask", 0, 255,
       "Invierte el sentido de giro por eje.", "Inverts motion direction per axis.", True),
    _s(4, "Inversión de habilitación", "Step enable invert", "bool", "bool", 0, 1,
       "Invierte la señal de habilitación de los drivers.", "Inverts the driver enable signal."),
    _s(5, "Inversión de finales de carrera", "Limit pins invert", "bool", "bool", 0, 1,
       "Invierte la lógica de los finales de carrera.", "Inverts limit switch logic.", True),
    _s(6, "Inversión de sonda", "Probe pin invert", "bool", "bool", 0, 1,
       "Invierte la lógica de la sonda.", "Inverts probe logic."),
    _s(10, "Informe de estado", "Status report", "máscara", "mask", 0, 255,
       "1 = posición de máquina (MPos), 0 = posición de trabajo (WPos). Bit 2 = búfer.",
       "1 = machine position (MPos), 0 = work position (WPos). Bit 2 = buffer state."),
    _s(11, "Desviación en uniones", "Junction deviation", "mm", "float", 0.0, 1.0,
       "Controla la velocidad en esquinas.", "Controls cornering speed."),
    _s(12, "Tolerancia de arcos", "Arc tolerance", "mm", "float", 0.0, 1.0,
       "Precisión al dividir arcos en segmentos.", "Precision when splitting arcs into segments."),
    _s(13, "Informar en pulgadas", "Report in inches", "bool", "bool", 0, 1,
       "Si es 1 los informes usan pulgadas. La aplicación espera 0 (mm).",
       "If 1, reports are in inches. The application expects 0 (mm)."),
    _s(20, "Límites de software", "Soft limits", "bool", "bool", 0, 1,
       "Rechaza movimientos fuera del recorrido. Requiere homing.",
       "Rejects moves outside travel. Requires homing."),
    _s(21, "Límites físicos", "Hard limits", "bool", "bool", 0, 1,
       "Detiene la máquina si se activa un final de carrera.", "Stops the machine if a limit switch triggers."),
    _s(22, "Ciclo de homing", "Homing cycle", "bool", "bool", 0, 1,
       "Habilita $H. Con 1 la máquina arranca en alarma hasta hacer Home.",
       "Enables $H. With 1 the machine boots in alarm until homed."),
    _s(23, "Dirección de homing", "Homing direction invert", "máscara", "mask", 0, 255,
       "Esquina hacia la que busca el homing.", "Corner the homing cycle seeks.", True),
    _s(24, "Avance de búsqueda fina", "Homing locate feed", "mm/min", "float", 1, 10000,
       "Velocidad lenta para localizar el interruptor.", "Slow rate to locate the switch."),
    _s(25, "Avance de búsqueda", "Homing seek rate", "mm/min", "float", 1, 20000,
       "Velocidad de búsqueda rápida.", "Fast search rate."),
    _s(26, "Antirrebote de homing", "Homing debounce", "ms", "int", 0, 255,
       "Retardo antirrebote de los interruptores.", "Switch debounce delay."),
    _s(27, "Retroceso de homing", "Homing pull-off", "mm", "float", 0, 50,
       "Distancia de retroceso tras tocar el interruptor.", "Retract distance after touching the switch."),
    _s(30, "Valor S máximo", "Max spindle/laser S", "S", "float", 1, 100000,
       "Valor S que equivale a potencia máxima. Debe coincidir con el perfil.",
       "S value equal to full power. Must match the profile.", True),
    _s(31, "Valor S mínimo", "Min spindle/laser S", "S", "float", 0, 100000,
       "Valor S mínimo efectivo.", "Minimum effective S value."),
    _s(32, "Modo láser", "Laser mode", "bool", "bool", 0, 1,
       "1 = modo láser: el láser se apaga en G0 y sin movimiento. Muy recomendado.",
       "1 = laser mode: laser off on G0 and when stopped. Strongly recommended.", True),
    _s(100, "Pasos/mm X", "X steps/mm", "pasos/mm", "float", 0.001, 100000,
       "Resolución del eje X.", "X axis resolution."),
    _s(101, "Pasos/mm Y", "Y steps/mm", "pasos/mm", "float", 0.001, 100000,
       "Resolución del eje Y.", "Y axis resolution."),
    _s(102, "Pasos/mm Z", "Z steps/mm", "pasos/mm", "float", 0.001, 100000,
       "Resolución del eje Z.", "Z axis resolution."),
    _s(110, "Velocidad máx. X", "X max rate", "mm/min", "float", 1, 200000,
       "Velocidad máxima del eje X.", "X axis maximum rate."),
    _s(111, "Velocidad máx. Y", "Y max rate", "mm/min", "float", 1, 200000,
       "Velocidad máxima del eje Y.", "Y axis maximum rate."),
    _s(112, "Velocidad máx. Z", "Z max rate", "mm/min", "float", 1, 200000,
       "Velocidad máxima del eje Z.", "Z axis maximum rate."),
    _s(120, "Aceleración X", "X acceleration", "mm/s²", "float", 1, 100000,
       "Aceleración del eje X.", "X axis acceleration."),
    _s(121, "Aceleración Y", "Y acceleration", "mm/s²", "float", 1, 100000,
       "Aceleración del eje Y.", "Y axis acceleration."),
    _s(122, "Aceleración Z", "Z acceleration", "mm/s²", "float", 1, 100000,
       "Aceleración del eje Z.", "Z axis acceleration."),
    _s(130, "Recorrido máx. X", "X max travel", "mm", "float", 1, 100000,
       "Recorrido útil del eje X (límites de software).", "X usable travel (soft limits)."),
    _s(131, "Recorrido máx. Y", "Y max travel", "mm", "float", 1, 100000,
       "Recorrido útil del eje Y (límites de software).", "Y usable travel (soft limits)."),
    _s(132, "Recorrido máx. Z", "Z max travel", "mm", "float", 0, 100000,
       "Recorrido útil del eje Z.", "Z usable travel."),
]}


def describe(number: int) -> SettingInfo | None:
    return SETTINGS.get(number)


def validate(number: int, value: str) -> tuple[bool, str]:
    """Valida un valor antes de escribirlo. Devuelve (válido, motivo)."""
    from openlaser.i18n import tr
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False, tr("El valor debe ser numérico")
    info = SETTINGS.get(number)
    if info is None:
        return True, tr("Parámetro sin descripción: se enviará tal cual")
    if info.kind in ("int", "bool", "mask") and not numeric.is_integer():
        return False, tr("El valor debe ser entero")
    if info.kind == "bool" and numeric not in (0, 1):
        return False, tr("El valor debe ser 0 o 1")
    if not info.minimum <= numeric <= info.maximum:
        return False, tr("Fuera de rango ({lo} – {hi})", lo=info.minimum, hi=info.maximum)
    return True, ""


def diff(before: dict[int, str], after: dict[int, str]) -> list[tuple[int, str | None, str | None]]:
    """Lista ordenada de (parámetro, antes, después) con los valores distintos."""
    keys = sorted(set(before) | set(after))
    out: list[tuple[int, str | None, str | None]] = []
    for k in keys:
        a, b = before.get(k), after.get(k)
        if a is None or b is None or _norm(a) != _norm(b):
            out.append((k, a, b))
    return out


def _norm(v: str) -> str:
    try:
        return repr(float(v))
    except ValueError:
        return v.strip()
