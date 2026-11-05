"""Controlador para firmwares de protocolo envío-respuesta: Marlin y Smoothieware (Fase 6).

Hereda de :class:`MachineController` (hilo, cola, trabajo, eventos, seguridad) y sustituye lo que en
GRBL depende del conteo de caracteres y de los comandos de tiempo real:

* Una línea en vuelo cada vez (``max_in_flight = 1``).
* Estado: Smoothieware responde a ``?``; en Marlin lo deduce la aplicación (cola vacía = reposo,
  porque cada movimiento manual termina con ``M400`` y el trabajo también) y la posición se pide con
  ``M114``.
* Pausa: la aplicación deja de enviar, rebobina las líneas no enviadas y envía ``M5``; al reanudar
  repone el último ``M3``/``M4``. Los movimientos ya enviados (planificador) se terminan.
* Parar: Marlin ``M410`` + ``M5`` (inmediato con ``EMERGENCY_PARSER``); Smoothieware Ctrl-X (queda en
  alarma hasta ``$X``). Parada de emergencia: Marlin ``M112`` (hay que reiniciar la placa).
* Jog, frame e ir a: siempre ``G0`` (Smoothieware nunca emite en G0; Marlin con ``M5``).
* El trabajo se traduce con :func:`host_protocols.translate_job` (escala de S, modo en línea, G0).
* Si el controlador resulta ser otro firmware, se desconecta con un aviso.
"""
from __future__ import annotations

import time
from typing import Callable

from openlaser.devices.profile import DeviceProfile
from openlaser.devices.transport import Transport, TransportError
from openlaser.i18n import tr
from openlaser.machine import grbl_protocol as gp
from openlaser.machine import host_protocols as hp
from openlaser.machine.command_queue import QueuedCommand
from openlaser.machine.controller import (
    CONNECT_TIMEOUT_S, FIRE_MARGIN_S, MachineController, Result, _fmt, no_answer_message,
)
from openlaser.machine.events import AlarmEvent, ConsoleEvent, LineKind
from openlaser.machine.states import Action, ConnState, GrblState, JobState
from openlaser.safety.manager import SafetyManager

HOST_TIMEOUT_S = 12.0      # Marlin puede tardar en G28/M400 (envía «busy» cada ~2 s)
M114_INTERVAL_S = 0.5
CONNECT_PROBE_S = 2.0


class HostController(MachineController):
    def __init__(self, profile: DeviceProfile, safety: SafetyManager | None = None,
                 transport_factory: Callable[[DeviceProfile], Transport] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.protocol = hp.PROTOCOLS[profile.firmware]
        super().__init__(profile, safety=safety, transport_factory=transport_factory, clock=clock)
        self._queue.max_in_flight = 1
        self._queue.max_line = 96  # Marlin MAX_CMD_SIZE; Smoothieware admite más

    # ------------------------------------------------------------- estado propio
    def _reset_runtime(self) -> None:
        super()._reset_runtime()
        self._queue.max_in_flight = 1
        self._caps: dict[str, bool] = {}
        self._halted = False
        self._last_rx = 0.0
        self._last_m114 = 0.0
        self._last_probe = -1e9
        self._spindle_line = "M5"
        self._seen_reply = False
        self._reply_at = 0.0

    @property
    def s_out_max(self) -> float:
        return self.profile.fw_s_max or self.protocol.s_out_max

    # ----------------------------------------------------------------- API pública
    def override(self, code: bytes) -> Result:
        return self._reject("override", tr("{f} no admite ajustes en tiempo real (overrides).", f=self.protocol.name))

    def read_settings(self) -> Result:
        return self._reject("read_settings", tr("{f}: los parámetros se consultan en la consola (Marlin: M503; "
                                                "Smoothieware: config-get).", f=self.protocol.name))

    def resume(self) -> Result:
        with self._lock:
            if self._halted:
                return self._reject("resume", tr("No se reanuda tras una parada del controlador"))
        return self._request(Action.RESUME, "resume")

    def send_line(self, text: str, confirmed: bool = False) -> Result:
        if text.strip() in ("!", "~"):
            return self._reject("send", tr("{f} no admite «!»/«~»: use Pausa y Reanudar.", f=self.protocol.name))
        if text.strip() == "?" and not self.protocol.realtime_status:
            return self._request(Action.SEND, "send", "M114", "user")
        return super().send_line(text, confirmed)

    # --------------------------------------------------------------- escritura
    def _estop_bytes(self) -> bytes:
        return self.protocol.estop_bytes

    def _write_rt(self, t: Transport, code: bytes) -> None:
        if code == gp.RT_STATUS:
            if self.protocol.realtime_status:
                t.write(gp.RT_STATUS)
            return
        if code == gp.RT_SOFT_RESET:
            if self.protocol.stop_lines:
                data = "".join(f"{ln}\n" for ln in self.protocol.stop_lines)
                t.write(data.encode("ascii"))
                self.events.put(ConsoleEvent(LineKind.TX, " · ".join(self.protocol.stop_lines), "realtime"))
            else:
                t.write(gp.RT_SOFT_RESET)
                self.events.put(ConsoleEvent(LineKind.TX, "<0x18>", "realtime"))
            return
        # Retención, reanudación, cancelación de jog y overrides: no existen; lo gestiona la aplicación.

    def _enqueue_jog(self, delta: list[float], feed: float) -> None:
        parts = "".join(f"{ax}{_fmt(d)}" for ax, d in zip("XYZ", delta) if abs(d) > 1e-6)
        if not parts:
            return
        self._enqueue(f"G91G0{parts}F{_fmt(feed)}", "jog")
        self._enqueue("G90", "jog")
        if self.protocol.id == "marlin" and self._cont_jog is None:
            self._enqueue("M400", "jog")

    def _sync(self, source: str) -> None:
        if self.protocol.id == "marlin":
            self._enqueue("M400", source)

    # -------------------------------------------------------------- peticiones
    def _do_home(self, t: Transport) -> None:
        self._enqueue(self.protocol.home_cmd, "system")
        self._sync("system")
        self._console(LineKind.INFO, tr("Homing solicitado"))

    def _do_unlock(self, t: Transport) -> None:
        self._enqueue(self.protocol.unlock_cmd, "system")
        self._halted = False
        self.safety.record("unlock", tr("Desbloqueo manual ({c}) solicitado por el operador", c=self.protocol.unlock_cmd))

    def _do_set_origin(self, t: Transport) -> None:
        self._enqueue("G92X0Y0" + ("Z0" if self.profile.z_axis else ""), "system")
        self._wco = (self._mpos[0], self._mpos[1], self._mpos[2] if self.profile.z_axis else self._wco[2], 0.0)
        self._console(LineKind.INFO, tr("Origen de trabajo establecido en la posición actual"))

    def _do_clear_origin(self, t: Transport) -> None:
        m = self._mpos
        self._enqueue(f"G92X{_fmt(m[0])}Y{_fmt(m[1])}", "system")
        self._wco = (0.0, 0.0, self._wco[2], 0.0)
        self._console(LineKind.INFO, tr("Origen de trabajo borrado (coincide con el de máquina)"))

    def _do_goto(self, t: Transport, x: float, y: float) -> None:
        ok, msg = self.check_bounds_work([(x, y)])
        if not ok:
            self._reject("goto", msg)
            return
        self._enqueue("M5", "goto")
        self._enqueue(f"G90G0X{_fmt(x)}Y{_fmt(y)}F{_fmt(self.profile.max_feed_mm_min)}", "goto")
        self._sync("goto")

    def _do_frame(self, t: Transport, pts: list[tuple[float, float]], feed: float) -> None:
        # Siempre G0: en Smoothieware un G1 emitiría con la potencia por defecto del módulo.
        self._enqueue("M5", "frame")
        self._enqueue("G90G21", "frame")
        for i, (x, y) in enumerate(pts + ([pts[0]] if pts[-1] != pts[0] else [])):
            self._enqueue(f"G0X{_fmt(x)}Y{_fmt(y)}" + (f"F{_fmt(feed)}" if i == 0 else ""), "frame")
        self._sync("frame")
        self._console(LineKind.INFO, tr("Frame con láser apagado ({n} puntos)", n=len(pts)))

    def _do_fire(self, t: Transport, percent: float, seconds: float) -> None:
        if not self.protocol.stationary_fire:
            self._reject("fire", tr("{f} sólo emite durante movimientos G1: el disparo de enfoque con el cabezal "
                                    "parado no está disponible. Use una prueba de enfoque.", f=self.protocol.name))
            return
        if self._grbl != GrblState.IDLE or not self._queue.is_empty() or self._job_state in (
                JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            self._reject("fire", tr("El disparo sólo está disponible en reposo y sin cola"))
            return
        s = max(0.0, min(100.0, percent)) / 100.0 * self.s_out_max
        dwell = f"G4P{round(seconds * 1000)}" if self.protocol.dwell_ms else f"G4P{_fmt(seconds)}"
        self._enqueue(f"M3S{_fmt(s)}", "fire")
        self._enqueue(dwell, "fire")
        self._enqueue("M5", "fire", tag_end=True)
        self._fire_active = True
        self._fire_deadline = self._clock() + seconds + FIRE_MARGIN_S + 1.0
        self.safety.record("fire", tr("Disparo de enfoque {p} % durante {s} s", p=_fmt(percent), s=_fmt(seconds)))
        self._emit_status(force=True)

    def _do_fire_off(self, t: Transport, reason: str) -> None:
        if self._fire_active:
            self._kill_now(t)
            self._end_fire(reason)

    def _kill_now(self, t: Transport) -> None:
        """Último recurso: Marlin M112 (G4 bloquea su cola, así que M410/M5 llegarían tarde)."""
        t.write(self.protocol.estop_bytes)
        self._queue.clear_all()
        self.events.put(ConsoleEvent(LineKind.TX, self.protocol.estop_bytes.decode("ascii", "replace").strip()
                                     or "<0x18>", "realtime"))

    def _do_start(self, t: Transport, lines: list[str], name: str, est: list[float] | None) -> None:
        translated = hp.translate_job(lines, self.protocol, self.profile.s_max, self.s_out_max,
                                      rapid_feed=self.profile.max_feed_mm_min)
        too_long = [ln for ln in translated if len(ln) + 1 > self._queue.max_line]
        if too_long:
            self._reject("start", tr("Línea demasiado larga para {f}: {l}", f=self.protocol.name, l=too_long[0][:40]))
            return
        super()._do_start(t, translated, name, est)
        job = self._job
        if job is not None and self._job_state == JobState.RUNNING and self.protocol.id == "marlin":
            job.lines.append("M400")  # el trabajo termina cuando termina el movimiento
            if job.estimate_cumulative:
                job.estimate_cumulative.append(job.estimate_cumulative[-1])

    def _do_pause(self, t: Transport) -> None:
        job = self._job
        if self._job_state == JobState.RUNNING and job is not None:
            self._rewind_job()
            self._job_state = JobState.PAUSED
            job.pause_started = self._clock()
            self._enqueue("M5", "system")
            self._sync("system")
        self._console(LineKind.INFO, tr("Pausa: no se envían más líneas; la máquina termina los movimientos ya "
                                        "enviados y apaga el láser (M5)"))
        self._emit_status(force=True)

    def _do_resume(self, t: Transport) -> None:
        if self._halted:
            self._reject("resume", tr("No se reanuda tras una parada del controlador"))
            return
        job = self._job
        if job is not None and self._job_state == JobState.PAUSED:
            if job.pause_started:
                job.paused_total += self._clock() - job.pause_started
            job.pause_started = None
            job.error_paused = False
            if self._spindle_line != "M5":
                self._enqueue(self._spindle_line, "system")
            self._job_state = JobState.RUNNING
        self._console(LineKind.INFO, tr("Reanudar"))
        self._emit_status(force=True)

    def _do_stop(self, t: Transport) -> None:
        self._cont_jog = None
        job_active = self._job_state in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING)
        busy = not self._queue.is_empty() or job_active or self._grbl in (GrblState.RUN, GrblState.JOG,
                                                                             GrblState.HOME)
        self._queue.clear_all()
        if self._fire_active:
            self._kill_now(t)
            self._end_fire(tr("Disparo cancelado por Stop"))
        elif busy:
            self._write_rt(t, gp.RT_SOFT_RESET)
        if job_active:
            self._finish_job(JobState.ABORTED, tr("Trabajo detenido"))
        self._console(LineKind.WARN, tr("Stop: cola de comandos cancelada"))
        self.safety.record("stop", tr("Stop solicitado"))
        self._emit_status(force=True)

    def _do_estop(self, t: Transport) -> None:
        super()._do_estop(t)
        if self.protocol.id == "marlin":
            self._notice("block", "marlin_killed", tr("Marlin se ha detenido con M112: reinicie la placa (botón de "
                                                      "reset o apagado) y vuelva a conectar."))

    # --------------------------------------------------------------- recepción
    def _handle_line(self, line: str, t: Transport) -> None:
        self._last_rx = self._clock()
        text = line.strip()
        if not text:
            return
        msg = hp.parse_marlin(text) if self.protocol.id == "marlin" else hp.parse_smoothie(text)
        if isinstance(msg, gp.StatusReport):
            if msg.mpos is not None and msg.wpos is not None and msg.wco is None:
                msg.wco = tuple(m - w for m, w in zip(msg.mpos, msg.wpos))
            if self._halted and msg.state.lower() != "alarm":
                msg.state = "Alarm"
            self._seen_reply = True
            self._on_status(msg, t)
            self.events.put(ConsoleEvent(LineKind.STATUS, text))
            return
        if isinstance(msg, hp.Position):
            self.events.put(ConsoleEvent(LineKind.STATUS, text))
            self._mpos = (msg.x + self._wco[0], msg.y + self._wco[1], msg.z + self._wco[2], self._mpos[3])
            self._synth_status(t)
            return
        if isinstance(msg, hp.Busy):
            self.events.put(ConsoleEvent(LineKind.STATUS, text))
            return
        kind = LineKind.ERROR if isinstance(msg, (gp.ErrorMsg, hp.Halted)) else LineKind.RX
        self.events.put(ConsoleEvent(kind, text))
        if isinstance(msg, gp.Ok):
            if self._conn == ConnState.CONNECTING:
                self._seen_reply, self._reply_at = True, self._clock()  # «ok» de una sonda M115
                return
            self._on_ack(self._queue.acknowledge(), None, t)
        elif isinstance(msg, gp.ErrorMsg):
            if self.protocol.id == "marlin":
                # Marlin informa el error y después envía «ok» para esa línea
                self._notice("warn", "fw_error", tr("Mensaje de error del controlador: {m}", m=text))
            else:
                self._on_ack(self._queue.acknowledge(), msg.code, t)
        elif isinstance(msg, hp.Halted):
            if self.protocol.id == "smoothie" and text.startswith("!!"):
                self._queue.acknowledge()  # Smoothieware responde «!!» en lugar de «ok» mientras está detenido
            self._on_halt(text)
        elif isinstance(msg, gp.Welcome):
            self._on_welcome(msg)
            if self._conn == ConnState.CONNECTING:
                self._last_probe = self._clock() - CONNECT_PROBE_S + 0.5  # la placa acaba de arrancar
        elif isinstance(msg, hp.FirmwareInfo):
            self._on_firmware(msg, t)
        elif isinstance(msg, hp.Capability):
            self._caps[msg.name] = msg.enabled
            if msg.name == "EMERGENCY_PARSER" and not msg.enabled:
                self._notice_once("no_emergency_parser", "warn", tr(
                    "Marlin sin EMERGENCY_PARSER: Stop (M410) y la parada de software (M112) esperan a que se "
                    "procese la línea en curso. Active EMERGENCY_PARSER en el firmware y use siempre la seta física."))

    def _on_firmware(self, msg: hp.FirmwareInfo, t: Transport) -> None:
        if self._conn == ConnState.CONNECTING:
            self._seen_reply, self._reply_at = True, self._clock()
        if msg.name != self.protocol.id:
            names = {"grbl": "GRBL/grblHAL/FluidNC", "marlin": "Marlin", "smoothie": "Smoothieware"}
            raise TransportError(tr("El controlador parece {f}, no {p}: elija ese firmware en el perfil y vuelva a "
                                    "conectar.", f=names.get(msg.name, msg.name), p=self.protocol.name))
        self._firmware = msg.raw[:120]
        self._console(LineKind.INFO, tr("Firmware detectado: {f} ({s})", f=self.protocol.name, s=msg.raw[:60]))

    def _on_halt(self, text: str) -> None:
        if self._halted:
            return
        self._halted = True
        self._grbl = GrblState.ALARM
        self._homed = False
        self._queue.clear_all()
        self._cont_jog = None
        if self._fire_active:
            self._end_fire(tr("Disparo cancelado por parada del controlador"))
        if self._job_state in (JobState.RUNNING, JobState.PAUSED, JobState.STOPPING):
            self._finish_job(JobState.ABORTED, tr("Trabajo abortado: el controlador se detuvo"))
        how = (tr("reinicie la placa y vuelva a conectar") if self.protocol.id == "marlin"
               else tr("pulse Desbloquear ($X) cuando sea seguro y repita Home"))
        self._notice("block", "fw_halted", tr("{f} se ha detenido ({m}): {h}.", f=self.protocol.name, m=text, h=how))
        self.events.put(AlarmEvent(-1))
        self.safety.record("alarm", text)
        self._emit_status(force=True)

    def _cancel_jog(self, t: Transport) -> None:
        super()._cancel_jog(t)
        self._enqueue("G90", "system")  # el «G90» del último jog relativo pudo quedar sin enviar

    def _rewind_job(self) -> None:
        job = self._job
        if job is None:
            return
        first = self._queue.pending_min_index("job")
        if first is not None:
            job.next_index = first
        self._queue.clear_pending("job")
        self._queue.clear_pending("status")

    def _on_ack(self, cmd: QueuedCommand | None, err: int | None, t: Transport) -> None:
        was_paused = self._job_state == JobState.PAUSED
        super()._on_ack(cmd, err, t)
        if cmd is not None and cmd.text.lower() == "version" and err is not None:
            raise TransportError(tr("El controlador no responde a «version»: no parece Smoothieware. Elija el "
                                    "firmware correcto en el perfil."))
        if err is not None and not was_paused and self._job_state == JobState.PAUSED and self._job is not None:
            # error en el trabajo: la base lo ha pausado; aquí además se deja de enviar y se apaga el láser
            self._rewind_job()
            self._job.pause_started = self._clock()
            self._enqueue("M5", "system")
            self._sync("system")
        if cmd is not None and err is None:
            up = cmd.text.upper()
            if up == self.protocol.home_cmd.upper():
                self._homed = True
                self._wco = (0.0, 0.0, self._wco[2], 0.0)
                if self.protocol.id == "marlin":
                    self._enqueue("G92X0Y0", "system")  # el origen de máquina es el punto de homing
                self._console(LineKind.INFO, tr("Home completado"))
            if up == self.protocol.unlock_cmd.upper() or up == "M999":
                self._halted = False
        if self.protocol.id == "marlin":
            self._synth_status(t)

    def _on_connected(self, t: Transport) -> None:
        self._conn = ConnState.CONNECTED
        self._connected_once = True
        self.safety.record("connect", tr("Conectado: {c}", c=self.profile.connection_label()))
        self._console(LineKind.INFO, tr("Conectado. No se envía ningún movimiento automático."))
        for cmd in self.protocol.connect_cmds:
            if self.protocol.id == "marlin" and cmd == "M115" and self._firmware:
                continue
            self._enqueue(cmd, "system")
        self._notice("info", "host_protocol", tr(
            "{f} (experimental): una línea cada vez; Pausa deja de enviar y apaga el láser al terminar los "
            "movimientos ya enviados; Stop y la parada de software no sustituyen a la seta física.",
            f=self.protocol.name))
        if abs(self.s_out_max - self.protocol.s_out_max) > 1e-9:
            self._console(LineKind.INFO, tr("S máximo del firmware: {s}", s=_fmt(self.s_out_max)))
        if self.profile.homing_enabled:
            self._notice("info", "not_homed", tr("Recuerde hacer Home antes de trabajar: los límites no están verificados."))

    # -------------------------------------------------------------- estado deducido
    def _synth_status(self, t: Transport) -> None:
        """Marlin: el estado se deduce de la cola (cada movimiento termina con M400)."""
        if self.protocol.realtime_status:
            return
        if self._halted:
            state = "Alarm"
        elif (self._queue.pending_count + self._queue.in_flight_count) == self._queue.count_source("status"):
            state = "Idle"  # las consultas M114 no cuentan como actividad
        elif any(c.text.upper().startswith("G28") for c in self._queue.in_flight()):
            state = "Home"
        elif self._queue.count_source("jog"):
            state = "Jog"
        else:
            state = "Run"
        rep = gp.StatusReport(state=state, mpos=self._mpos, wco=self._wco, raw="")
        self._on_status(rep, t)

    def _feed_job(self) -> None:
        job = self._job
        before = job.next_index if job is not None else 0
        super()._feed_job()
        if job is None:
            return
        for ln in job.lines[before:job.next_index]:
            up = ln.upper()
            if up.startswith(("M3", "M4", "M5")):
                self._spindle_line = ln
        now = self._clock()
        if (self.protocol.id == "marlin" and self._job_state == JobState.RUNNING
                and now - self._last_m114 > 1.0 and not self._queue.count_source("status")):
            self._last_m114 = now
            self._enqueue("M114", "status")

    # ----------------------------------------------------------------- tiempos
    def _timers(self, t: Transport, now: float) -> None:
        p = self.protocol
        if self._conn == ConnState.CONNECTING:
            if now - self._last_probe >= CONNECT_PROBE_S:
                self._last_probe = now
                if p.realtime_status:
                    t.write(gp.RT_STATUS)
                else:
                    t.write(b"M115\n")  # sin cola: si la placa aún arranca, se repite
            if p.realtime_status and now - self._last_poll >= 0.5:
                self._last_poll = now
                t.write(gp.RT_STATUS)
            # Se conecta cuando las sondas han dejado de responder (así ningún «ok» tardío se confunde con
            # el de una orden posterior).
            if not p.realtime_status and self._seen_reply and now - max(self._reply_at, self._last_probe) > 0.5:
                self._synth_status(t)
            if now > self._connect_deadline + (4.0 if not p.realtime_status else 0.0):
                raise TransportError(no_answer_message(p.name, CONNECT_TIMEOUT_S, t))
            return
        interval = max(0.05, self.profile.status_poll_ms / 1000.0)
        if p.realtime_status:
            if now - self._last_poll >= interval:
                self._last_poll = now
                t.write(gp.RT_STATUS)
            if self._conn == ConnState.CONNECTED and now - self._last_status_rx > HOST_TIMEOUT_S:
                raise TransportError(tr("El controlador no responde a '?'"))
        else:
            if self._queue.is_empty() and now - self._last_m114 >= max(M114_INTERVAL_S, interval):
                self._last_m114 = now
                self._enqueue("M114", "status")
            if self._conn == ConnState.CONNECTED and now - self._last_rx > HOST_TIMEOUT_S:
                raise TransportError(tr("{f} no responde", f=p.name))
            self._synth_status(t)
        if self._fire_active and self._fire_deadline is not None and now > self._fire_deadline:
            self._kill_now(t)
            self._end_fire(tr("Vigilante: tiempo de disparo superado; parada enviada"))
        cj = self._cont_jog
        if cj is not None and now - cj.keepalive > 0.6:
            self._cancel_jog(t)
            self._console(LineKind.WARN, tr("Jog continuo detenido: sin confirmación de la interfaz"))

