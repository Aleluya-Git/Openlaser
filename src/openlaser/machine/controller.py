"""Controlador de máquina GRBL: hilo de comunicación, cola, trabajo y seguridad.

Toda la comunicación ocurre en un hilo propio. La interfaz llama a los
métodos públicos (que validan con una instantánea del estado y encolan la
petición) y recoge eventos de :attr:`MachineController.events`.

Garantías de láser apagado implementadas aquí:

* Al conectar no se envía ningún movimiento ni ``$X``/``$H``; si el informe
  indica husillo/láser activo en reposo se envía ``M5``.
* Jog, frame, ir a origen e ir a X/Y usan ``$J=``/``G0``/``G1 S0`` precedidos
  de ``M5``; nunca llevan potencia.
* El disparo de enfoque exige confirmación, potencia y duración limitadas y
  se envía como un bloque ``M3 → G4 → M5`` para que el propio controlador lo
  apague aunque se pierda la comunicación; además hay un vigilante que hace
  un reinicio por software si se supera el tiempo.
* Parar, la parada de software, una desconexión o cualquier excepción del
  hilo envían reinicio (``0x18``) cuando hay actividad, lo que apaga el láser
  en GRBL, y vacían la cola.
* Nunca se desbloquea ni se reanuda automáticamente tras una alarma.

Dialectos (Fase 5): GRBL 1.1, grblHAL y FluidNC comparten este controlador. El firmware se detecta
en la bienvenida, en ``$I`` y en el informe de estado; las diferencias (búfer de recepción, alarmas,
parámetros de sólo lectura, homing informado por el controlador) están en :mod:`dialects`.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from openlaser.devices.profile import DeviceProfile
from openlaser.devices.transport import Transport, TransportError, create_transport
from openlaser.i18n import tr
from openlaser.machine import dialects
from openlaser.machine import grbl_protocol as gp
from openlaser.machine.command_queue import QueuedCommand, QueueError, StreamingQueue
from openlaser.machine.events import (
    AlarmEvent, CommandRejected, ConsoleEvent, ErrorEvent, JobProgressEvent, LineKind,
    MachineStatus, SafetyNotice, SettingsEvent, StatusEvent,
)
from openlaser.machine.grbl_codes import alarm_info, error_info
from openlaser.machine.states import (
    Action, ConnState, GrblState, JobState, allowed_actions, derive_display_state,
)
from openlaser.safety.manager import Risk, SafetyManager

_log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 14.0  # placas ESP32 y Arduino con reinicio por DTR tardan varios segundos en arrancar
WAKE_AFTER_S = 2.5        # sin ningún dato: se envía un salto de línea y un reinicio por software (0x18)
FLIP_LINES_AFTER_S = 4.0  # sigue sin datos: se prueban las otras combinaciones de DTR/RTS
LINES_STEP_S = 3.0        # tiempo para cada combinación
WAKE_AFTER_FLIP_S = 1.5   # dentro de cada combinación: reinicio por software tras dejar arrancar la placa


def _onoff(v: bool) -> str:
    return tr("activa") if v else tr("inactiva")


def no_answer_message(firmware: str, seconds: float, t: object) -> str:
    """Mensaje de «sin respuesta» que distingue «no llega nada» de «llegan datos que no se entienden»."""
    n = int(getattr(t, "rx_bytes", 0) or 0)
    if n == 0:
        return tr("Sin respuesta de {f} en {s} s: no se ha recibido ningún dato. Compruebe que el puerto es el "
                  "correcto, que ningún otro programa lo tiene abierto (cierre el otro software) y la velocidad "
                  "(baudios) del perfil.", f=firmware, s=int(seconds))
    sample = bytes(getattr(t, "rx_sample", b""))
    shown = "".join(chr(b) if 32 <= b < 127 else "·" for b in sample[:48])
    return tr("Sin respuesta de {f} en {s} s: se recibieron {n} bytes que no se reconocen («{x}»). Lo más "
              "probable es que la velocidad (baudios) no sea la correcta o que el firmware del perfil no sea el "
              "de la máquina.", f=firmware, s=int(seconds), n=n, x=shown)
STATUS_TIMEOUT_S = 5.0
# Durante $H muchas versiones de GRBL (las de 8 bits y numerosos clones) no contestan a '?' hasta terminar:
# mientras el homing está en curso se espera hasta este tiempo antes de dar la conexión por perdida.
HOMING_TIMEOUT_S = 120.0
CONT_JOG_SEGMENT_S = 0.2
CONT_JOG_KEEPALIVE_S = 0.6
FIRE_MARGIN_S = 0.75
STOP_HOLD_TIMEOUT_S = 1.5
LOST_POSITION_ALARMS = dialects.GRBL.lost_position_alarms  # compatibilidad: GRBL 1.1


@dataclass
class Result:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class Job:
    name: str
    lines: list[str]
    estimate_cumulative: list[float] | None = None
    next_index: int = 0
    sent: int = 0
    acked: int = 0
    started: float = 0.0
    paused_total: float = 0.0
    pause_started: float | None = None
    error_paused: bool = False
    last_line: str = ""

    def elapsed(self, now: float) -> float:
        paused = self.paused_total + ((now - self.pause_started) if self.pause_started else 0.0)
        return max(0.0, now - self.started - paused)

    def remaining(self, now: float) -> float | None:
        est = self.estimate_cumulative
        if not est:
            return None
        total = est[-1]
        done = est[min(self.acked, len(est)) - 1] if self.acked else 0.0
        elapsed = self.elapsed(now)
        factor = (elapsed / done) if done > 5.0 else 1.0
        return max(0.0, (total - done) * factor)


@dataclass
class _ContJog:
    direction: tuple[float, float, float, float]
    feed: float
    keepalive: float
    next_send: float = 0.0
    planned: list[float] = field(default_factory=list)


def _pad4(vals: Iterable[float]) -> tuple[float, float, float, float]:
    v = list(vals)[:4]
    while len(v) < 4:
        v.append(0.0)
    return (v[0], v[1], v[2], v[3])


def _fmt(v: float) -> str:
    text = f"{v:.3f}".rstrip("0").rstrip(".")  # siempre hay punto decimal con .3f
    return "0" if text in ("-0", "") else text


class MachineController:
    def __init__(self, profile: DeviceProfile, safety: SafetyManager | None = None,
                 transport_factory: Callable[[DeviceProfile], Transport] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.profile = profile
        self.safety = safety or SafetyManager()
        self._factory = transport_factory or (lambda p: create_transport(p))
        self._clock = clock
        self.events: "queue.Queue[object]" = queue.Queue()
        self._inbox: "queue.Queue[tuple]" = queue.Queue()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._transport: Transport | None = None
        self._queue = StreamingQueue(profile.rx_buffer_size)
        self._reset_runtime()
        self._conn = ConnState.DISCONNECTED
        self._last_error = ""

    def _reset_runtime(self) -> None:
        self._grbl = GrblState.UNKNOWN
        self._sub: int | None = None
        self._job_state = JobState.NONE
        self._job: Job | None = None
        self._estop = False
        self._homed = False
        self._fire_active = False
        self._fire_deadline: float | None = None
        self._mpos = (0.0, 0.0, 0.0, 0.0)
        self._wco = (0.0, 0.0, 0.0, 0.0)
        self._feed = 0.0
        self._spindle = 0.0
        self._spindle_modal = "M5"
        self._ov = (100, 100, 100)
        self._pins = ""
        self._planner_free: int | None = None
        self._rx_free: int | None = None
        self._firmware = ""
        self._dialect = dialects.get(self.profile.firmware)
        self._dialect_detected = False
        self._welcome_dialect: str | None = None
        self._rx_reported: int | None = None
        self._queue.rx_buffer_size = max(80, int(self.profile.rx_buffer_size))
        self._settings: dict[int, str] = {}
        self._collecting: dict[int, str] | None = None
        self._last_status_rx = 0.0
        self._last_poll = 0.0
        self._connect_deadline = 0.0
        self._woke = 0
        self._homing_seen = 0.0
        self._flipped: tuple[bool, bool] | None = None
        self._lines0: tuple[bool, bool] | None = None
        self._stop_deadline: float | None = None
        self._cont_jog: _ContJog | None = None
        self._jog_cancel_pending = False
        self._expect_reset = False
        self._last_status: MachineStatus | None = None
        self._last_status_emit = 0.0
        self._last_progress_emit = 0.0
        self._connected_once = False
        self._notified: set[str] = set()

    # ================================================================ consulta
    @property
    def settings(self) -> dict[int, str]:
        with self._lock:
            return dict(self._settings)

    @property
    def queue_stats(self) -> tuple[int, int, int]:
        with self._lock:
            return self._queue.pending_count, self._queue.in_flight_count, self._queue.in_flight_bytes

    def status(self) -> MachineStatus:
        with self._lock:
            return self._build_status()

    @property
    def dialect(self) -> dialects.Dialect:
        with self._lock:
            return self._dialect

    @property
    def settings_writable(self) -> bool:
        return self.dialect.numbered_settings_writable

    def allowed(self) -> set[Action]:
        with self._lock:
            return allowed_actions(self._conn, self._grbl, self._job_state, self._estop, self._fire_active)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _build_status(self) -> MachineStatus:
        wpos = tuple(m - w for m, w in zip(self._mpos, self._wco))
        door = self._grbl == GrblState.DOOR or "D" in self._pins
        emitting = self._fire_active or (self._spindle > 0 and self._grbl == GrblState.RUN)
        return MachineStatus(
            conn=self._conn, grbl=self._grbl, substate=self._sub, job=self._job_state,
            display=derive_display_state(self._conn, self._grbl, self._job_state, self._estop,
                                         bool(self._last_error) and self._conn == ConnState.DISCONNECTED),
            mpos=self._mpos, wpos=_pad4(wpos), wco=self._wco, feed=self._feed, spindle=self._spindle,
            spindle_modal=self._spindle_modal, laser_emitting=emitting, overrides=self._ov,
            homed=self._homed, estop=self._estop, fire_active=self._fire_active, door_open=door,
            pins=self._pins, firmware=self._firmware, dialect=self._dialect.id, error=self._last_error,
            planner_free=self._planner_free, rx_free=self._rx_free,
        )

    # ============================================================ API pública
    def connect(self) -> Result:
        with self._lock:
            if self._conn != ConnState.DISCONNECTED or self.is_running():
                return self._reject("connect", tr("Ya hay una conexión activa"))
            self._reset_runtime()
            self._queue.clear_all()
            self._last_error = ""
            self._conn = ConnState.CONNECTING
            self._stop_evt.clear()
            while not self._inbox.empty():
                self._inbox.get_nowait()
            self._thread = threading.Thread(target=self._run, name="openlaser-machine", daemon=True)
            self._emit_status(force=True)
        self._thread.start()
        return Result(True)

    def disconnect(self) -> Result:
        if not self.is_running():
            return Result(True)
        self._inbox.put(("disconnect",))
        return Result(True)

    def wait_stopped(self, timeout: float = 3.0) -> bool:
        t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()

    def home(self) -> Result:
        return self._request(Action.HOME, "home")

    def unlock(self) -> Result:
        return self._request(Action.UNLOCK, "unlock")

    def jog(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, da: float = 0.0,
            feed: float | None = None) -> Result:
        if dz and not self.profile.z_axis:
            return self._reject("jog", tr("El perfil no tiene eje Z"))
        if da and not self.profile.a_axis:
            return self._reject("jog", tr("El perfil no tiene eje A"))
        if not any((dx, dy, dz, da)):
            return self._reject("jog", tr("Distancia de jog nula"))
        return self._request(Action.JOG, "jog", (dx, dy, dz, da), feed or self.profile.jog_feed_mm_min)

    def jog_continuous(self, direction: tuple[float, float, float, float], feed: float | None = None) -> Result:
        return self._request(Action.JOG, "jog_cont", direction, feed or self.profile.jog_feed_mm_min)

    def jog_keepalive(self) -> None:
        self._inbox.put(("jog_keepalive",))

    def jog_stop(self) -> Result:
        self._inbox.put(("jog_stop",))
        return Result(True)

    def set_origin(self) -> Result:
        return self._request(Action.SET_ORIGIN, "set_origin")

    def clear_origin(self) -> Result:
        return self._request(Action.SET_ORIGIN, "clear_origin")

    def go_to_origin(self) -> Result:
        return self._request(Action.GO_ORIGIN, "goto", 0.0, 0.0)

    def go_to(self, x: float, y: float) -> Result:
        return self._request(Action.GOTO, "goto", x, y)

    def frame(self, points_work: list[tuple[float, float]], feed: float | None = None) -> Result:
        if len(points_work) < 2:
            return self._reject("frame", tr("No hay geometría para el frame"))
        ok, msg = self.check_bounds_work(points_work)
        if not ok:
            return self._reject("frame", msg)
        return self._request(Action.FRAME, "frame", list(points_work), feed or self.profile.frame_feed_mm_min)

    def start_job(self, lines: list[str], name: str = "job",
                  estimate_cumulative: list[float] | None = None) -> Result:
        clean = [gp.sanitize_line(ln) for ln in lines]
        clean = [ln for ln in clean if ln]
        if not clean:
            return self._reject("start", tr("El trabajo no contiene G-code"))
        if any(gp.is_system_command(ln) for ln in clean):
            return self._reject("start", tr("El G-code contiene comandos '$'; no se permiten dentro de un trabajo."))
        too_long = [ln for ln in clean if len(ln) + 1 > self._queue.max_line]
        if too_long:
            return self._reject("start", tr("Línea demasiado larga para GRBL: {l}", l=too_long[0][:40]))
        return self._request(Action.START, "start", clean, name, estimate_cumulative)

    def pause(self) -> Result:
        return self._request(Action.PAUSE, "pause")

    def resume(self) -> Result:
        with self._lock:
            if self._grbl in (GrblState.HOLD, GrblState.DOOR) and self._sub not in (0, None):
                return self._reject("resume", tr("Espere a que la máquina se detenga o cierre la puerta"))
            if self._grbl == GrblState.ALARM:
                return self._reject("resume", tr("No se reanuda tras una alarma"))
        return self._request(Action.RESUME, "resume")

    def stop(self) -> Result:
        """Parada controlada: retención, reinicio y vaciado de la cola."""
        if not self.is_running():
            return Result(True)
        self._inbox.put(("stop",))
        return Result(True)

    def emergency_stop(self) -> Result:
        """Parada de software inmediata (no sustituye la seta física)."""
        t = self._transport
        sent = False
        if t is not None and t.is_open:
            try:
                t.write(self._estop_bytes())
                sent = True
            except TransportError:
                sent = False
        with self._lock:
            self._estop = True
            self._expect_reset = True
            self._emit_status(force=True)
        self.safety.record("estop", tr("Parada de software solicitada") + ("" if sent else " (sin enlace)"))
        self._inbox.put(("estop",))
        return Result(True)

    def acknowledge_estop(self) -> Result:
        with self._lock:
            self._estop = False
            self._emit_status(force=True)
        self.safety.record("estop_ack", tr("Parada de software confirmada por el operador"))
        return Result(True)

    def fire(self, percent: float, seconds: float, confirmed: bool) -> Result:
        if not confirmed:
            return self._reject("fire", tr("El disparo requiere confirmación explícita"))
        ok, msg = self.safety.validate_fire(percent, seconds)
        if not ok:
            return self._reject("fire", msg)
        return self._request(Action.FIRE, "fire", percent, seconds)

    def fire_off(self) -> Result:
        self._inbox.put(("fire_off", tr("Disparo detenido por el operador")))
        return Result(True)

    def send_line(self, text: str, confirmed: bool = False) -> Result:
        raw = text.strip()
        if raw in ("?", "!", "~"):
            self._inbox.put(("realtime", raw.encode()))
            return Result(True)
        line = gp.sanitize_line(raw)
        if not line:
            return self._reject("send", tr("Línea vacía"))
        risk = self.safety.classify_command(line)
        if risk.level == Risk.DANGER and not confirmed:
            return self._reject("send", tr("Comando peligroso: requiere confirmación") + " — " + "; ".join(risk.reasons))
        return self._request(Action.SEND, "send", line, "user")

    def run_macro(self, lines: list[str], variables: dict[str, float] | None = None,
                  confirmed: bool = False) -> Result:
        expanded: list[str] = []
        status = self.status()
        env = {"x": status.wpos[0], "y": status.wpos[1], "z": status.wpos[2], "a": status.wpos[3],
               "mx": status.mpos[0], "my": status.mpos[1], "width": self.profile.width_mm,
               "height": self.profile.height_mm, "smax": self.profile.s_max,
               "cx": self.profile.width_mm / 2, "cy": self.profile.height_mm / 2}
        env.update(variables or {})
        for ln in lines:
            try:
                text = ln.format(**{k: _fmt(float(v)) for k, v in env.items()})
            except (KeyError, ValueError, IndexError) as exc:
                return self._reject("macro", tr("Variable de macro desconocida: {v}", v=exc))
            text = gp.sanitize_line(text)
            if not text:
                continue
            risk = self.safety.classify_command(text)
            if risk.fires_laser and not confirmed:
                return self._reject("macro", tr("La macro activa el láser: requiere confirmación"))
            if risk.level == Risk.DANGER and not confirmed:
                return self._reject("macro", tr("La macro contiene comandos peligrosos: requiere confirmación"))
            expanded.append(text)
        if not expanded:
            return self._reject("macro", tr("Macro vacía"))
        return self._request(Action.SEND, "macro", expanded)

    def read_settings(self) -> Result:
        return self._request(Action.READ_SETTINGS, "read_settings")

    def write_setting(self, number: int, value: str, confirmed: bool) -> Result:
        if not confirmed:
            return self._reject("write_setting", tr("Cambiar parámetros requiere confirmación"))
        if not self.settings_writable:
            return self._reject("write_setting", tr("{f} guarda su configuración en un archivo YAML: los parámetros $n "
                                                    "son de sólo lectura. Cambie la configuración desde su WebUI o con "
                                                    "comandos $/… del firmware.", f=self.dialect.name))
        return self._request(Action.READ_SETTINGS, "write_setting", number, value)

    def override(self, code: bytes) -> Result:
        if len(code) != 1 or code[0] < 0x90:
            return self._reject("override", tr("Código de override no válido"))
        return self._request(Action.OVERRIDE, "realtime", code)

    # --- comprobación de límites --------------------------------------------
    def check_bounds_work(self, points_work: Iterable[tuple[float, float]]) -> tuple[bool, str]:
        """Comprueba puntos en coordenadas de trabajo contra el área de la máquina."""
        prof = self.profile
        with self._lock:
            wco, homed = self._wco, self._homed
        pts = list(points_work)
        if not pts or not prof.enforce_limits:
            return True, ""
        if prof.homing_enabled and not homed:
            return True, tr("Sin homing: los límites no se pueden verificar")
        eps = 1e-6
        for x, y in pts:
            mx, my = x + wco[0], y + wco[1]
            if mx < -eps or my < -eps or mx > prof.width_mm + eps or my > prof.height_mm + eps:
                return False, tr("Fuera del área de trabajo: X{x} Y{y} (máquina X{mx} Y{my})",
                                 x=_fmt(x), y=_fmt(y), mx=_fmt(mx), my=_fmt(my))
        return True, ""

    # ======================================================== utilidades API
    def _reject(self, action: str, reason: str) -> Result:
        self.events.put(CommandRejected(action, reason))
        return Result(False, reason)

    def _request(self, action: Action, name: str, *args: object) -> Result:
        allowed = self.allowed()
        if action not in allowed:
            st = self.status()
            return self._reject(name, tr("No disponible en el estado actual ({s})", s=st.display.label))
        self._inbox.put((name, *args))
        return Result(True)

    # ======================================================== hilo de trabajo
    def _run(self) -> None:
        reason = ""
        try:
            transport = self._factory(self.profile)
            with self._lock:
                self._transport = transport
            self._console(LineKind.INFO, tr("Abriendo {c}…", c=self.profile.connection_label()))
            transport.open()
            now = self._clock()
            with self._lock:
                self._connect_deadline = now + CONNECT_TIMEOUT_S
                self._last_status_rx = now
                self._woke = 0
                self._flipped = None
                self._lines0 = None
            self._loop(transport)
        except TransportError as exc:
            reason = str(exc) or tr("Error de comunicación")
        except Exception as exc:  # noqa: BLE001 - cualquier fallo lleva a estado seguro
            _log.exception("Fallo interno en el hilo de máquina")
            reason = tr("Error interno: {e}", e=exc)
        finally:
            self._finalize(reason)

    def _loop(self, t: Transport) -> None:
        while not self._stop_evt.is_set():
            with self._lock:
                self._process_inbox(t)
            if self._stop_evt.is_set():
                break
            line = t.readline(0.01)
            count = 0
            while line is not None and count < 500:
                with self._lock:
                    self._handle_line(line, t)
                count += 1
                line = t.readline(0.0)
            with self._lock:
                now = self._clock()
                self._timers(t, now)
                self._feed_job()
                self._feed_cont_jog(now)
                self._pump(t, now)
                self._emit_progress(now)

    # --- bandeja de peticiones ----------------------------------------------
    def _process_inbox(self, t: Transport) -> None:
        while True:
            try:
                item = self._inbox.get_nowait()
            except queue.Empty:
                return
            name, args = item[0], item[1:]
            try:
                handler = getattr(self, f"_do_{name}")
                handler(t, *args)
            except QueueError as exc:
                self._reject(name, str(exc))

    def _do_disconnect(self, t: Transport) -> None:
        active = (self._job_state in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING)
                  or self._fire_active or self._grbl in (GrblState.RUN, GrblState.JOG, GrblState.HOME, GrblState.HOLD))
        if active:
            self._write_rt(t, gp.RT_SOFT_RESET)
            self.safety.record("disconnect_reset", tr("Desconexión con actividad: reinicio enviado"))
        self._conn = ConnState.DISCONNECTING
        self._stop_evt.set()

    def _do_home(self, t: Transport) -> None:
        self._enqueue("$H", "system")
        self._console(LineKind.INFO, tr("Homing solicitado"))

    def _do_unlock(self, t: Transport) -> None:
        self._enqueue("$X", "system")
        self.safety.record("unlock", tr("Desbloqueo manual ($X) solicitado por el operador"))

    def _do_jog(self, t: Transport, delta: tuple[float, float, float, float], feed: float) -> None:
        if self._job_state != JobState.NONE and self._job_state not in (JobState.COMPLETED, JobState.ABORTED):
            return
        delta_l = list(delta)
        if self._homed and self.profile.enforce_limits:
            delta_l = self._clamp_delta(delta_l, list(self._mpos))
            if not any(abs(d) > 1e-6 for d in delta_l):
                self._reject("jog", tr("Límite de área alcanzado"))
                return
        elif self.profile.homing_enabled and not self._homed:
            self._notice_once("jog_unhomed", "warn", tr("Sin homing: el jog no puede comprobar los límites"))
        self._enqueue_jog(delta_l, feed)

    def _do_jog_cont(self, t: Transport, direction: tuple[float, float, float, float], feed: float) -> None:
        now = self._clock()
        if self.profile.homing_enabled and not self._homed:
            self._notice_once("jog_unhomed", "warn", tr("Sin homing: el jog no puede comprobar los límites"))
        self._cont_jog = _ContJog(direction=direction, feed=feed, keepalive=now, next_send=now,
                                  planned=list(self._mpos))

    def _do_jog_keepalive(self, t: Transport) -> None:
        if self._cont_jog is not None:
            self._cont_jog.keepalive = self._clock()

    def _do_jog_stop(self, t: Transport) -> None:
        self._cancel_jog(t)

    def _cancel_jog(self, t: Transport) -> None:
        self._cont_jog = None
        self._queue.clear_pending("jog")
        if self._grbl == GrblState.JOG or self._queue.count_source("jog"):
            self._write_rt(t, gp.RT_JOG_CANCEL)
        self._jog_cancel_pending = self._queue.count_source("jog") > 0

    def _do_set_origin(self, t: Transport) -> None:
        cmd = "G10L20P1X0Y0" + ("Z0" if self.profile.z_axis else "")
        self._enqueue(cmd, "system")
        self._console(LineKind.INFO, tr("Origen de trabajo establecido en la posición actual"))

    def _do_clear_origin(self, t: Transport) -> None:
        self._enqueue("G10L2P1X0Y0Z0", "system")
        self._enqueue("G92.1", "system")
        self._console(LineKind.INFO, tr("Origen de trabajo borrado (coincide con el de máquina)"))

    def _do_goto(self, t: Transport, x: float, y: float) -> None:
        ok, msg = self.check_bounds_work([(x, y)])
        if not ok:
            self._reject("goto", msg)
            return
        self._enqueue("M5", "goto")
        self._enqueue(f"G90G0X{_fmt(x)}Y{_fmt(y)}", "goto")

    def _do_frame(self, t: Transport, pts: list[tuple[float, float]], feed: float) -> None:
        self._enqueue("M5", "frame")
        self._enqueue("G90G21", "frame")
        x0, y0 = pts[0]
        self._enqueue(f"G0X{_fmt(x0)}Y{_fmt(y0)}", "frame")
        first = True
        for x, y in pts[1:]:
            if first:
                self._enqueue(f"G1X{_fmt(x)}Y{_fmt(y)}F{_fmt(feed)}S0", "frame")
                first = False
            else:
                self._enqueue(f"G1X{_fmt(x)}Y{_fmt(y)}", "frame")
        if pts[-1] != pts[0]:
            self._enqueue(f"G1X{_fmt(x0)}Y{_fmt(y0)}", "frame")
        self._console(LineKind.INFO, tr("Frame con láser apagado ({n} puntos)", n=len(pts)))

    def _do_start(self, t: Transport, lines: list[str], name: str, est: list[float] | None) -> None:
        if self._grbl not in (GrblState.IDLE, GrblState.CHECK) or self._estop or self._fire_active:
            self._reject("start", tr("La máquina no está en reposo (Idle)."))
            return
        prof = self.profile
        header = [gp.sanitize_line(c) for c in prof.start_commands if gp.sanitize_line(c)]
        footer = ["M5"] + [gp.sanitize_line(c) for c in prof.end_commands if gp.sanitize_line(c)]
        all_lines = header + lines + footer
        cumulative = None
        if est and len(est) == len(lines):
            cumulative = [0.0] * len(header) + list(est) + [est[-1]] * len(footer)
        self._job = Job(name=name, lines=all_lines, estimate_cumulative=cumulative, started=self._clock())
        self._job_state = JobState.RUNNING
        self.safety.record("job_start", tr("Trabajo iniciado: {n} ({c} líneas)", n=name, c=len(all_lines)))
        self._console(LineKind.INFO, tr("Trabajo iniciado: {n} ({c} líneas)", n=name, c=len(all_lines)))
        self._emit_status(force=True)

    def _do_pause(self, t: Transport) -> None:
        self._write_rt(t, gp.RT_FEED_HOLD)
        if self._job_state == JobState.RUNNING and self._job:
            self._job_state = JobState.PAUSED
            self._job.pause_started = self._clock()
            for c in self.profile.pause_commands:
                _log.debug("Comando de pausa configurado (no se envía en retención): %s", c)
        self._console(LineKind.INFO, tr("Pausa (retención de avance)"))
        self._emit_status(force=True)

    def _do_resume(self, t: Transport) -> None:
        if self._grbl == GrblState.ALARM:
            self._reject("resume", tr("No se reanuda tras una alarma"))
            return
        if self._grbl in (GrblState.HOLD, GrblState.DOOR):
            if self._sub not in (0, None):
                self._reject("resume", tr("Espere a que la máquina se detenga o cierre la puerta"))
                return
            self._write_rt(t, gp.RT_CYCLE_START)
        if self._job and self._job_state == JobState.PAUSED:
            if self._job.pause_started:
                self._job.paused_total += self._clock() - self._job.pause_started
            self._job.pause_started = None
            self._job.error_paused = False
            self._job_state = JobState.RUNNING
        self._console(LineKind.INFO, tr("Reanudar"))
        self._emit_status(force=True)

    def _do_stop(self, t: Transport) -> None:
        self._cont_jog = None
        moving = self._grbl in (GrblState.RUN, GrblState.HOME)
        job_active = self._job_state in (JobState.RUNNING, JobState.PAUSED)
        self._queue.clear_pending()
        if self._fire_active:
            self._write_rt(t, gp.RT_SOFT_RESET)
            self._end_fire(tr("Disparo cancelado por Stop"))
        if self._grbl == GrblState.JOG:
            self._write_rt(t, gp.RT_JOG_CANCEL)
        if moving and self._grbl == GrblState.RUN:
            self._write_rt(t, gp.RT_FEED_HOLD)
            self._stop_deadline = self._clock() + STOP_HOLD_TIMEOUT_S
            if job_active:
                self._job_state = JobState.STOPPING
        elif moving or job_active or self._queue.in_flight_count:
            self._soft_reset(t)
            if job_active:
                self._finish_job(JobState.ABORTED, tr("Trabajo detenido"))
        self._console(LineKind.WARN, tr("Stop: cola de comandos cancelada"))
        self.safety.record("stop", tr("Stop solicitado"))
        self._emit_status(force=True)

    def _do_estop(self, t: Transport) -> None:
        self._cont_jog = None
        self._queue.clear_all()
        self._fire_active = False
        self._fire_deadline = None
        self._homed = False
        if self._job_state in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            self._finish_job(JobState.ABORTED, tr("Trabajo abortado por parada de software"))
        self._console(LineKind.ERROR, tr("PARADA DE SOFTWARE: reinicio enviado, cola vaciada"))
        self._emit_status(force=True)

    def _do_fire(self, t: Transport, percent: float, seconds: float) -> None:
        if self._grbl != GrblState.IDLE or not self._queue.is_empty() or self._job_state in (
                JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            self._reject("fire", tr("El disparo sólo está disponible en reposo y sin cola"))
            return
        s = self.profile.s_from_percent(percent)
        self._enqueue(f"G1F1000M3S{_fmt(s)}", "fire")
        self._enqueue(f"G4P{_fmt(seconds)}", "fire")
        self._enqueue("M5G0", "fire", tag_end=True)
        self._fire_active = True
        self._fire_deadline = self._clock() + seconds + FIRE_MARGIN_S
        self.safety.record("fire", tr("Disparo de enfoque {p} % durante {s} s", p=_fmt(percent), s=_fmt(seconds)))
        self._emit_status(force=True)

    def _do_fire_off(self, t: Transport, reason: str) -> None:
        if self._fire_active:
            self._write_rt(t, gp.RT_SOFT_RESET)
            self._end_fire(reason)

    def _do_realtime(self, t: Transport, code: bytes) -> None:
        self._write_rt(t, code)

    def _do_send(self, t: Transport, line: str, source: str) -> None:
        self._enqueue(line, source)

    def _do_macro(self, t: Transport, lines: list[str]) -> None:
        for ln in lines:
            self._enqueue(ln, "macro")

    def _do_read_settings(self, t: Transport) -> None:
        self._collecting = {}
        self._enqueue("$$", "system")

    def _do_write_setting(self, t: Transport, number: int, value: str) -> None:
        self._enqueue(f"${int(number)}={value}", "system")
        self.safety.record("setting", tr("Parámetro ${n} cambiado a {v}", n=number, v=value))
        self._collecting = {}
        self._enqueue("$$", "system")

    # --- procesamiento de líneas --------------------------------------------
    def _handle_line(self, line: str, t: Transport) -> None:
        msg = gp.parse_line(line)
        if isinstance(msg, gp.StatusReport):
            self._on_status(msg, t)
            self.events.put(ConsoleEvent(LineKind.STATUS, line.strip()))
            return
        if not line.strip():
            return
        kind = LineKind.RX
        if isinstance(msg, (gp.ErrorMsg, gp.AlarmMsg)):
            kind = LineKind.ERROR
        self.events.put(ConsoleEvent(kind, line.strip()))
        if isinstance(msg, gp.Ok):
            self._on_ack(self._queue.acknowledge(), None, t)
        elif isinstance(msg, gp.ErrorMsg):
            self._on_ack(self._queue.acknowledge(), msg.code, t)
        elif isinstance(msg, gp.AlarmMsg):
            self._on_alarm(msg.code)
        elif isinstance(msg, gp.Welcome):
            self._on_welcome(msg)
        elif isinstance(msg, gp.SettingMsg):
            if self._collecting is not None:
                self._collecting[msg.number] = msg.value
        elif isinstance(msg, gp.Feedback):
            self._on_feedback(msg)
        elif isinstance(msg, gp.Unknown):
            self._check_other_firmware(msg.raw)

    def _check_other_firmware(self, text: str) -> None:
        """Marlin o Smoothieware no hablan GRBL: se desconecta con una indicación clara."""
        low = text.lower()
        found = None
        if low.startswith("firmware_name:marlin") or low == "start" or low.startswith("echo:marlin"):
            found = "Marlin"
        elif low.startswith("smoothie") or low.startswith("build version:"):
            found = "Smoothieware"
        if found and self._conn == ConnState.CONNECTING:
            raise TransportError(tr("El controlador parece {f}, no {p}: elija ese firmware en el perfil y vuelva a "
                                    "conectar.", f=found, p=self._dialect.name))

    def _set_dialect(self, dialect_id: str | None, source: str) -> None:
        if dialect_id is None:
            return
        new = dialects.get(dialect_id)
        first = not self._dialect_detected
        self._dialect_detected = True
        if new.id == self._dialect.id and not first:
            return
        changed = new.id != self._dialect.id
        self._dialect = new
        if first or changed:
            self._console(LineKind.INFO, tr("Firmware detectado: {f} ({s})", f=new.name, s=source))
        if changed and self._rx_reported is not None:
            self._apply_rx_buffer(self._rx_reported)
        configured = dialects.get(self.profile.firmware)
        if new.id != configured.id:
            self._notice_once("dialect_mismatch", "warn",
                              tr("El perfil indica {p} pero el controlador es {f}. Se usa {f}; corrija el perfil.",
                                 p=configured.name, f=new.name))
        if new.id == "fluidnc":
            self._notice_once("fluidnc_laser", "info",
                              tr("FluidNC: compruebe en su configuración YAML que el husillo es de tipo láser "
                                 "(Laser) y que «disable_with_s0» y la velocidad máxima coinciden con el perfil."))
        self._emit_status(force=True)

    def _apply_rx_buffer(self, reported: int) -> None:
        self._rx_reported = reported
        if not self.profile.rx_buffer_auto:
            return
        size = dialects.usable_rx(reported)
        if self._dialect.id != "grblhal":
            # Sólo grblHAL informa de forma fiable su búfer en todos los canales; en los demás el valor
            # informado sólo puede reducir el del perfil, nunca ampliarlo.
            size = min(size, max(80, int(self.profile.rx_buffer_size)))
        if size != self._queue.rx_buffer_size:
            self._queue.rx_buffer_size = size
            self._console(LineKind.INFO, tr("Búfer de recepción del controlador: {n} bytes (se usan {u})",
                                            n=reported, u=size))

    def _on_feedback(self, msg: gp.Feedback) -> None:
        self._set_dialect(dialects.detect_feedback(msg.kind, msg.payload), "$I")
        if msg.kind == "OPT":
            rx = dialects.rx_buffer_from_opt(msg.payload)
            if rx is not None:
                self._apply_rx_buffer(rx)
        if msg.kind == "VER":
            self._firmware = msg.payload
        elif msg.kind == "GC":
            for tok in msg.payload.split():
                if tok in ("M3", "M4", "M5"):
                    self._spindle_modal = tok
        elif msg.kind == "MSG" and "unlock" in msg.payload.lower():
            self._notice("warn", "alarm_boot",
                         tr("El controlador exige Home ($H) o desbloqueo ($X). No se envía automáticamente."))
        elif msg.kind == "MSG":
            # FluidNC: [MSG:ERR: …] / [MSG:WARN: …]; grblHAL y GRBL usan [MSG:texto]
            level, _, text = msg.payload.partition(":")
            if level.strip().upper() in ("ERR", "ERROR") and text:
                self._notice("warn", "fw_error", tr("Mensaje de error del controlador: {m}", m=text.strip()))

    def _on_ack(self, cmd: QueuedCommand | None, err: int | None, t: Transport) -> None:
        if cmd is None:
            return
        if cmd.source == "job" and self._job is not None:
            self._job.acked += 1
            self._job.last_line = cmd.text
        if cmd.source == "jog" and self._jog_cancel_pending:
            self._write_rt(t, gp.RT_JOG_CANCEL)
            self._jog_cancel_pending = self._queue.count_source("jog") > 0
        if cmd.text == "$I" and not self._dialect_detected:
            # $I terminado sin extensiones de grblHAL ni FluidNC: GRBL clásico
            self._set_dialect(self._welcome_dialect or "grbl", "$I")
        if cmd.text == "$$" and self._collecting is not None:
            if err is None:
                self._settings = dict(self._collecting)
                self.events.put(SettingsEvent(dict(self._settings)))
                for issue in self.safety.check_settings(self._settings, self.profile.s_max):
                    self._notice(issue.severity.value, issue.code, issue.message)
            self._collecting = None
        if cmd.text.upper() == "$H":
            if err is None:
                self._homed = True
                self._console(LineKind.INFO, tr("Home completado"))
            else:
                self._homed = False
        if cmd.source == "fire" and cmd.tag_end and err is None:
            self._end_fire(tr("Disparo de enfoque finalizado"))
        if err is None:
            return
        info = error_info(err, self._dialect.id)
        self.events.put(ErrorEvent(err, cmd.text, cmd.source))
        self._console(LineKind.ERROR, f"error:{err} — {info.text} ← {cmd.text}")
        if cmd.source == "job" and self._job_state == JobState.RUNNING and self._job:
            self._write_rt(t, gp.RT_FEED_HOLD)
            self._job_state = JobState.PAUSED
            self._job.error_paused = True
            self._job.pause_started = self._clock()
            self._notice("warn", "job_error", tr("Error en el trabajo (línea «{l}»): pausado. Revise antes de reanudar o detener.",
                                                  l=cmd.text))
        elif cmd.source in ("frame", "goto", "macro"):
            self._queue.clear_pending(cmd.source)
        elif cmd.source == "fire":
            self._write_rt(t, gp.RT_SOFT_RESET)
            self._end_fire(tr("Disparo cancelado por error"))

    def _on_alarm(self, code: int) -> None:
        info = alarm_info(code, self._dialect.id)
        self._grbl = GrblState.ALARM
        if code in self._dialect.lost_position_alarms:
            self._homed = False
        self._queue.clear_all()
        self._cont_jog = None
        self._jog_cancel_pending = False
        if self._fire_active:
            self._end_fire(tr("Disparo cancelado por alarma"))
        if self._job_state in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            self._finish_job(JobState.ABORTED, tr("Trabajo abortado por alarma {c}", c=code))
        self.events.put(AlarmEvent(code))
        self.safety.record("alarm", f"ALARM:{code} — {info.text}")
        self._emit_status(force=True)

    def _on_welcome(self, msg: gp.Welcome) -> None:
        self._queue.clear_all()
        self._spindle_modal = "M5"
        self._cont_jog = None
        self._jog_cancel_pending = False
        if self._fire_active:
            self._end_fire(tr("Disparo cancelado por reinicio"))
        if self._job_state in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            self._finish_job(JobState.ABORTED, tr("Trabajo abortado: el controlador se reinició"))
        if self._conn == ConnState.CONNECTED and not self._expect_reset:
            self._notice("warn", "unexpected_reset", tr("El controlador se reinició de forma inesperada"))
        self._expect_reset = False
        self._console(LineKind.INFO, tr("Controlador: {v}", v=msg.raw))
        found = dialects.detect_welcome(msg.raw)
        if found == "grbl":
            # «Grbl 1.1f» también lo muestra grblHAL en modo de compatibilidad: se confirma con $I.
            self._welcome_dialect = found
        else:
            self._set_dialect(found, tr("bienvenida"))

    def _on_status(self, rep: gp.StatusReport, t: Transport) -> None:
        now = self._clock()
        self._last_status_rx = now
        grbl = GrblState.parse(rep.state)
        self._grbl, self._sub = grbl, rep.substate
        if rep.wco is not None:
            self._wco = _pad4(rep.wco)
        if rep.mpos is not None:
            self._mpos = _pad4(rep.mpos)
        elif rep.wpos is not None:
            self._mpos = _pad4(w + c for w, c in zip(_pad4(rep.wpos), self._wco))
        if rep.feed is not None:
            self._feed = rep.feed
        if rep.spindle is not None:
            self._spindle = rep.spindle
        if rep.overrides is not None:
            self._ov = rep.overrides
        if rep.accessories is not None:
            acc = rep.accessories
            self._spindle_modal = "M3" if "S" in acc else ("M4" if "C" in acc else "M5")
        self._pins = rep.pins
        self._planner_free, self._rx_free = rep.planner_free, rep.rx_free
        if "FW" in rep.extra:
            self._set_dialect(dialects.detect_feedback("FIRMWARE", rep.extra["FW"]), "FW")
        home = rep.extra.get("H")
        if home is not None and self._dialect.id == "grblhal":
            # grblHAL informa si el homing está hecho: es más fiable que deducirlo de «ok» a $H
            self._homed = home.split(",", 1)[0].strip() == "1"
        if "E" in rep.pins and self._dialect.id == "grblhal":
            self._notice_once("estop_input", "block",
                              tr("La entrada de parada de emergencia del controlador está activa."))
        if self._conn == ConnState.CONNECTING:
            self._on_connected(t)
        job = self._job
        if (job is not None and self._job_state == JobState.RUNNING and job.next_index >= len(job.lines)
                and self._queue.count_source("job") == 0 and grbl == GrblState.IDLE):
            self._finish_job(JobState.COMPLETED, tr("Trabajo completado"))
        self._emit_status()

    def _wake(self, t: Transport) -> None:
        self._expect_reset = True
        t.write(b"\r\n")
        self._write_rt(t, gp.RT_SOFT_RESET)

    def _on_connected(self, t: Transport) -> None:
        self._conn = ConnState.CONNECTED
        if self._flipped is not None:
            d, r = self._flipped
            self._notice("warn", "serial_lines",
                         tr("Ha conectado con DTR={d} y RTS={r}. Marque esas opciones en el perfil "
                            "(Máquina → Ajustes del dispositivo) para que conecte a la primera.",
                            d=_onoff(d), r=_onoff(r)))
        self._connected_once = True
        self.safety.record("connect", tr("Conectado: {c}", c=self.profile.connection_label()))
        self._console(LineKind.INFO, tr("Conectado. No se envía ningún movimiento automático."))
        if self._grbl in (GrblState.IDLE, GrblState.ALARM):
            self._enqueue("$I", "system")
            self._collecting = {}
            self._enqueue("$$", "system")
            self._enqueue("$G", "system")
        else:
            self._notice("warn", "busy_on_connect",
                         tr("La máquina está en estado {s} al conectar; no se envían comandos.", s=self._grbl.value))
        if self._grbl == GrblState.IDLE and self._spindle_modal in ("M3", "M4"):
            self._enqueue("M5", "system")
            self._notice("warn", "laser_on_connect", tr("Láser/husillo activo al conectar: se ha enviado M5"))
        if self.profile.homing_enabled:
            self._notice("info", "not_homed", tr("Recuerde hacer Home antes de trabajar: los límites no están verificados."))

    # --- temporizadores -------------------------------------------------------
    def _timers(self, t: Transport, now: float) -> None:
        interval = max(0.05, self.profile.status_poll_ms / 1000.0)
        if now - self._last_poll >= (0.5 if self._conn == ConnState.CONNECTING else interval):
            self._last_poll = now
            self._write_rt(t, gp.RT_STATUS)
        if self._conn == ConnState.CONNECTING:
            opened = self._connect_deadline - CONNECT_TIMEOUT_S
            silent = getattr(t, "rx_bytes", 1) == 0
            if self._woke == 0 and now - opened > WAKE_AFTER_S and silent:
                # Algunas placas no contestan hasta recibir un fin de línea o un reinicio (como hace
                # LaserGRBL al conectar). El reinicio por software no mueve nada: detiene y vacía GRBL.
                self._woke = 1
                self._wake(t)
            elif self._woke >= 1 and silent and hasattr(t, "set_lines"):
                # Una placa con DTR o RTS cableado al reinicio (o un USB nativo que sólo transmite con DTR)
                # se queda muda con las señales en el estado equivocado: se prueban las otras tres
                # combinaciones, dando a cada una tiempo para arrancar y un reinicio por software.
                step = self._woke - 1            # 0,1 = 1.ª combinación (cambio, reinicio); 2,3 = 2.ª…
                combo, phase = divmod(step, 2)
                due = FLIP_LINES_AFTER_S + LINES_STEP_S * combo + (WAKE_AFTER_FLIP_S if phase else 0.0)
                if combo < 3 and now - opened > due:
                    self._woke += 1
                    if phase == 0:
                        if self._lines0 is None:
                            self._lines0 = (bool(getattr(t, "dtr", False)), bool(getattr(t, "rts", False)))
                        d0, r0 = self._lines0
                        dtr, rts = ((not d0, r0), (d0, not r0), (not d0, not r0))[combo]
                        t.set_lines(dtr, rts)  # type: ignore[attr-defined]
                        self._flipped = (dtr, rts)
                        self._console(LineKind.INFO, tr("Sin datos: se prueba con DTR={d} y RTS={r}…",
                                                        d=_onoff(dtr), r=_onoff(rts)))
                    else:
                        self._wake(t)
            if now > self._connect_deadline:
                raise TransportError(no_answer_message("GRBL", CONNECT_TIMEOUT_S, t))
        homing = any(c.text.strip().upper().startswith("$H") for c in self._queue.in_flight())
        if homing:
            self._homing_seen = now      # al terminar, el plazo normal cuenta desde aquí
        last = max(self._last_status_rx, getattr(self, "_homing_seen", 0.0))
        limit = HOMING_TIMEOUT_S if homing else STATUS_TIMEOUT_S
        if self._conn == ConnState.CONNECTED and now - (self._last_status_rx if homing else last) > limit:
            raise TransportError(tr("El controlador no responde a '?'"))
        if self._fire_active and self._fire_deadline is not None:
            if now > self._fire_deadline:
                self._write_rt(t, gp.RT_SOFT_RESET)
                self._end_fire(tr("Vigilante: tiempo de disparo superado; reinicio enviado"))
            elif self._grbl not in (GrblState.IDLE, GrblState.RUN):
                self._write_rt(t, gp.RT_SOFT_RESET)
                self._end_fire(tr("Disparo cancelado: estado inesperado"))
        if self._job_state == JobState.STOPPING:
            held = (self._grbl == GrblState.HOLD and self._sub == 0) or self._grbl in (
                GrblState.IDLE, GrblState.DOOR, GrblState.ALARM)
            if held or (self._stop_deadline is not None and now > self._stop_deadline):
                self._soft_reset(t)
                self._stop_deadline = None
                self._finish_job(JobState.ABORTED, tr("Trabajo detenido"))
        elif self._stop_deadline is not None:
            held = (self._grbl == GrblState.HOLD and self._sub == 0) or self._grbl == GrblState.IDLE
            if held or now > self._stop_deadline:
                self._soft_reset(t)
                self._stop_deadline = None
        cj = self._cont_jog
        if cj is not None and now - cj.keepalive > CONT_JOG_KEEPALIVE_S:
            self._cancel_jog(t)
            self._console(LineKind.WARN, tr("Jog continuo detenido: sin confirmación de la interfaz"))

    def _feed_job(self) -> None:
        job = self._job
        if job is None or self._job_state != JobState.RUNNING:
            return
        while self._queue.pending_count < 16 and job.next_index < len(job.lines):
            self._enqueue(job.lines[job.next_index], "job", job_index=job.next_index)
            job.next_index += 1

    def _feed_cont_jog(self, now: float) -> None:
        cj = self._cont_jog
        if cj is None or now < cj.next_send or self._grbl not in (GrblState.IDLE, GrblState.JOG):
            return
        if self._queue.count_source("jog") > 2:
            return
        dist = cj.feed / 60.0 * CONT_JOG_SEGMENT_S
        norm = sum(d * d for d in cj.direction) ** 0.5 or 1.0
        delta = [d / norm * dist for d in cj.direction]
        if self._homed and self.profile.enforce_limits:
            delta = self._clamp_delta(delta, cj.planned)
        if not any(abs(d) > 0.005 for d in delta):
            return
        cj.planned = [p + d for p, d in zip(cj.planned, delta)]
        self._enqueue_jog(delta, cj.feed)
        lead = CONT_JOG_SEGMENT_S * (0.5 if cj.next_send == cj.keepalive else 0.9)
        cj.next_send = now + lead

    def _pump(self, t: Transport, now: float) -> None:
        while True:
            cmd = self._queue.next_sendable()
            if cmd is None:
                return
            if cmd.exclusive and self._grbl not in (GrblState.IDLE, GrblState.ALARM, GrblState.CHECK,
                                                     GrblState.UNKNOWN, GrblState.SLEEP):
                return
            self._queue.mark_sent(cmd, now)
            t.write((cmd.text + "\n").encode("ascii", "replace"))
            if cmd.source == "job" and self._job is not None:
                self._job.sent += 1
            self.events.put(ConsoleEvent(LineKind.TX, cmd.text, cmd.source))

    # --- auxiliares -----------------------------------------------------------
    def _enqueue(self, text: str, source: str, job_index: int | None = None, tag_end: bool = False) -> None:
        cmd = self._queue.enqueue(text, source, job_index)
        cmd.tag_end = tag_end

    def _write_rt(self, t: Transport, code: bytes) -> None:
        t.write(code)
        if code not in (gp.RT_STATUS,):
            self.events.put(ConsoleEvent(LineKind.TX, f"<0x{code[0]:02X}>" if code[0] >= 0x80 or code == gp.RT_SOFT_RESET
                                         else code.decode(), "realtime"))

    def _soft_reset(self, t: Transport) -> None:
        self._expect_reset = True
        self._write_rt(t, gp.RT_SOFT_RESET)
        self._queue.clear_all()
        self._cont_jog = None
        self._jog_cancel_pending = False

    def _end_fire(self, reason: str) -> None:
        if not self._fire_active:
            return
        self._fire_active = False
        self._fire_deadline = None
        self._queue.clear_pending("fire")
        self._console(LineKind.INFO, reason)
        self.safety.record("fire_end", reason)
        self._emit_status(force=True)

    def _finish_job(self, state: JobState, message: str) -> None:
        self._job_state = state
        now = self._clock()
        job = self._job
        if job is not None:
            self.events.put(JobProgressEvent(job.name, len(job.lines), job.sent, job.acked, job.elapsed(now),
                                             0.0 if state == JobState.COMPLETED else None, job.last_line, state))
        self._queue.clear_pending("job")
        self.safety.record("job_end", message)
        self._console(LineKind.INFO if state == JobState.COMPLETED else LineKind.WARN, message)
        self._emit_status(force=True)

    def _emit_progress(self, now: float) -> None:
        job = self._job
        if job is None or self._job_state not in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            return
        if now - self._last_progress_emit < 0.2:
            return
        self._last_progress_emit = now
        self.events.put(JobProgressEvent(job.name, len(job.lines), job.sent, job.acked, job.elapsed(now),
                                         job.remaining(now), job.last_line, self._job_state))

    def _clamp_delta(self, delta: list[float], start: list[float]) -> list[float]:
        prof = self.profile
        lo = [0.0, 0.0, prof.z_min_mm, -1e9]
        hi = [prof.width_mm, prof.height_mm, prof.z_max_mm, 1e9]
        scale = 1.0
        for i in range(3):
            if abs(delta[i]) < 1e-9:
                continue
            target = start[i] + delta[i]
            if target > hi[i]:
                scale = min(scale, max(0.0, (hi[i] - start[i]) / delta[i]))
            elif target < lo[i]:
                scale = min(scale, max(0.0, (lo[i] - start[i]) / delta[i]))
        return [d * scale for d in delta]

    def _estop_bytes(self) -> bytes:
        """Bytes que detienen el controlador de inmediato (GRBL: reinicio 0x18)."""
        return gp.RT_SOFT_RESET

    def _enqueue_jog(self, delta: list[float], feed: float) -> None:
        self._enqueue(self._jog_cmd(delta, feed), "jog")

    @staticmethod
    def _jog_cmd(delta: list[float], feed: float) -> str:
        parts = "".join(f"{ax}{_fmt(d)}" for ax, d in zip("XYZA", delta) if abs(d) > 1e-6)
        return f"$J=G91G21{parts}F{_fmt(feed)}"

    def _console(self, kind: LineKind, text: str) -> None:
        self.events.put(ConsoleEvent(kind, text, "app"))

    def _notice(self, severity: str, code: str, message: str) -> None:
        self.events.put(SafetyNotice(severity, code, message))
        if severity != "info":
            self.safety.record(code, message)

    def _notice_once(self, key: str, severity: str, message: str) -> None:
        if key in self._notified:
            return
        self._notified.add(key)
        self._notice(severity, key, message)

    def _emit_status(self, force: bool = False) -> None:
        st = self._build_status()
        now = self._clock()
        if force or st != self._last_status or now - self._last_status_emit > 1.0:
            self._last_status = st
            self._last_status_emit = now
            self.events.put(StatusEvent(st))

    def _finalize(self, reason: str) -> None:
        t = self._transport
        with self._lock:
            lost = bool(reason)
            if lost and t is not None and t.is_open:
                try:
                    t.write(self._estop_bytes())
                except TransportError:
                    pass
            if t is not None:
                t.close()
            self._transport = None
            self._queue.clear_all()
            if self._job_state in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
                self._job_state = JobState.ABORTED
            self._fire_active = False
            self._fire_deadline = None
            self._cont_jog = None
            self._homed = False
            self._conn = ConnState.DISCONNECTED
            self._grbl = GrblState.UNKNOWN
            self._feed = 0.0
            self._spindle = 0.0
            self._last_error = reason
            if lost:
                msg = tr("Conexión perdida: {r}. Estado seguro: cola vaciada, trabajo abortado. "
                         "Verifique físicamente que el láser está apagado.", r=reason.rstrip(". "))
                self._notice("block", "connection_lost", msg)  # la interfaz lo muestra también en la consola
            else:
                self._console(LineKind.INFO, tr("Desconectado"))
                self.safety.record("disconnect", tr("Desconectado por el operador"))
            self._notified.clear()
            self._emit_status(force=True)

    def shutdown(self, timeout: float = 2.0) -> None:
        """Cierra la conexión al salir de la aplicación."""
        if self.is_running():
            self.disconnect()
            if not self.wait_stopped(timeout):
                self._stop_evt.set()
                self.wait_stopped(1.0)

