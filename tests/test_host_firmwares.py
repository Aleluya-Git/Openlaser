"""Fase 6: Marlin y Smoothieware (protocolo envío-respuesta) con sus simuladores."""
import pytest

from conftest import wait_for
from openlaser.devices.profile import demo_profile
from openlaser.devices.transport import SimulatorTransport, make_simulator
from openlaser.machine import host_protocols as hp
from openlaser.machine.controller import MachineController
from openlaser.machine.events import SafetyNotice
from openlaser.machine.factory import create_controller, matches
from openlaser.machine.host_controller import HostController
from openlaser.machine.states import ConnState, GrblState, JobState
from openlaser.plugins import controllers
from openlaser.simulator.grbl_simulator import GrblSimulator
from openlaser.simulator.marlin_simulator import MarlinSimulator

JOB = ["G90", "G21", "M5", "G0X10Y10", "M4S0", "G1X40Y10F6000S300", "G1X40Y40", "G0X60Y60", "G1X80Y60",
       "G0X20Y80", "G1X30Y80S500", "M5"]


class HostRig:
    def __init__(self, firmware: str, speed: float = 40.0, sim=None, profile_fw: str | None = None) -> None:
        self.sim = sim or make_simulator(firmware, speed)
        self.profile = demo_profile()
        self.profile.firmware = profile_fw or firmware
        self.profile.status_poll_ms = 30
        self.transport = SimulatorTransport(self.sim)
        self.ctl = create_controller(self.profile, transport_factory=lambda p: self.transport)
        self.notices: dict[str, SafetyNotice] = {}

    def drain(self):
        out = []
        while not self.ctl.events.empty():
            ev = self.ctl.events.get_nowait()
            if isinstance(ev, SafetyNotice):
                self.notices[ev.code] = ev
            out.append(ev)
        return out

    def connect(self) -> None:
        assert self.ctl.connect()
        assert wait_for(lambda: self.ctl.status().conn == ConnState.CONNECTED, 8), "no conecta"
        assert wait_for(lambda: self.ctl.queue_stats == (0, 0, 0) and self.ctl.status().grbl == GrblState.IDLE, 5)

    def home(self) -> None:
        assert self.ctl.home()
        assert wait_for(lambda: self.ctl.status().homed and self.ctl.status().grbl == GrblState.IDLE, 8)

    def idle(self, timeout: float = 8.0) -> bool:
        return wait_for(lambda: self.ctl.queue_stats == (0, 0, 0) and self.ctl.status().grbl == GrblState.IDLE
                        and self.ctl._inbox.empty(), timeout)

    def close(self) -> None:
        self.ctl.shutdown()


@pytest.fixture(params=["marlin", "smoothie"])
def host(request):
    r = HostRig(request.param)
    yield r
    r.close()


# --------------------------------------------------------------------- núcleo
def test_translate_marlin_inline_and_no_g0():
    out = hp.translate_job(JOB, hp.MARLIN, 1000.0, rapid_feed=6000)
    assert not any(ln.startswith("G0") for ln in out)
    assert "M4S0I" in out
    assert "G1X10Y10F6000S0" in out and "G1X60Y60F6000S0" in out
    # tras un rápido convertido se reponen F y S del trabajo
    assert "G1X80Y60F6000S76" in out
    assert "G1X40Y10F6000S76" in out and "G1X30Y80S128F6000" in out
    assert hp.translate_job(["G4P0.5"], hp.MARLIN, 1000.0) == ["G4P500"]


def test_translate_smoothie_fraction_and_explicit_s0():
    out = hp.translate_job(["G90", "G1X5F100", "G1X10S500", "G0X0"], hp.SMOOTHIE, 1000.0)
    assert out == ["G90", "G1X5F100S0", "G1X10S0.5", "G0X0"]


def test_parsers():
    assert isinstance(hp.parse_marlin("ok P15 B3"), type(hp.parse_marlin("ok")))
    pos = hp.parse_marlin("X:10.00 Y:20.50 Z:0.00 E:0.00 Count X:0 Y:0 Z:0")
    assert isinstance(pos, hp.Position) and pos.y == 20.5
    assert isinstance(hp.parse_marlin("echo:busy: processing"), hp.Busy)
    assert isinstance(hp.parse_marlin("Error:Printer halted. kill() called!"), hp.Halted)
    assert hp.parse_marlin("Cap:EMERGENCY_PARSER:1") == hp.Capability("EMERGENCY_PARSER", True)
    assert hp.parse_marlin("FIRMWARE_NAME:Marlin 2.1.2").name == "marlin"
    assert hp.parse_marlin("Grbl 1.1h ['$' for help]").name == "grbl"
    rep = hp.parse_smoothie("<Idle,MPos:1.000,2.000,0.000,WPos:0.000,1.000,0.000>")
    assert rep.state == "Idle" and rep.mpos == (1.0, 2.0, 0.0) and rep.wpos == (0.0, 1.0, 0.0)
    assert hp.parse_smoothie("Build version: edge-1234, Build date: x").name == "smoothie"
    assert isinstance(hp.parse_smoothie("!!"), hp.Halted)


def test_factory_and_adapters():
    p = demo_profile()
    assert type(create_controller(p)) is MachineController
    p.firmware = "marlin"
    c = create_controller(p)
    assert isinstance(c, HostController) and matches(c, p)
    p.firmware = "grbl"
    assert not matches(c, p)
    ids = {a.id: a for a in controllers.adapters()}
    assert ids["marlin"].status == "available" and ids["smoothie"].status == "available"
    assert isinstance(make_simulator("marlin"), MarlinSimulator)
    assert make_simulator("smoothie").firmware == "smoothie"


# ------------------------------------------------------------- con simulador
def test_connect_home_jog_frame_without_laser(host):
    host.connect()
    host.drain()
    assert "host_protocol" in host.notices
    assert host.ctl.status().dialect == host.profile.firmware
    host.home()
    assert host.ctl.jog(dx=10, dy=5)
    assert host.idle()
    assert wait_for(lambda: abs(host.ctl.status().mpos[0] - 10) < 0.01 and abs(host.ctl.status().mpos[1] - 5) < 0.01)
    assert host.ctl.frame([(20, 20), (60, 20), (60, 50), (20, 50)])
    assert host.idle()
    assert host.sim.emissions == []  # conectar, Home, jog y frame nunca emiten
    assert host.ctl.override(b"\x91").ok is False


def test_job_laser_only_on_feed_moves(host):
    host.connect()
    host.home()
    assert host.ctl.start_job(JOB, "demo")
    assert wait_for(lambda: host.ctl.status().job == JobState.COMPLETED, 15)
    em = host.sim.emissions
    assert em and all(e.context == "feed" for e in em), em
    assert not host.sim.laser_emitting
    sent = host.sim.lines_received
    if host.profile.firmware == "marlin":
        job_part = sent[sent.index("M4S0I") - 4:]
        assert not any(ln.startswith("G0") for ln in job_part)  # los rápidos del trabajo van como G1 … S0
        assert "M400" in job_part
        assert host.sim.max_cmdq <= 1 and host.sim.overflow_count == 0  # envío-respuesta: una línea cada vez
    else:
        assert any("S0.3" in ln for ln in sent)


def test_pause_stops_sending_and_turns_laser_off_then_resume(host):
    host.connect()
    host.home()
    host.sim.speed_factor = 4.0
    lines = ["G90", "M4S0"] + [f"G1X{10 + (i % 30)}Y{10 + i // 30}F3000S200" for i in range(120)] + ["M5"]
    assert host.ctl.start_job(lines, "pausa")
    assert wait_for(lambda: host.ctl.status().job == JobState.RUNNING and host.ctl._job.acked > 10, 8)
    assert host.ctl.pause()
    assert wait_for(lambda: host.ctl.status().job == JobState.PAUSED)
    assert wait_for(lambda: host.ctl.queue_stats == (0, 0, 0), 15)
    sent_at_pause = len(host.sim.lines_received)
    assert wait_for(lambda: not host.sim.laser_emitting, 5)
    assert len(host.sim.lines_received) == sent_at_pause  # nada más se envía en pausa
    host.sim.speed_factor = 60.0
    assert host.ctl.resume()
    assert wait_for(lambda: host.ctl.status().job == JobState.COMPLETED, 20)
    received = [ln for ln in host.sim.lines_received if ln.startswith("G1X") and "F3000" in ln]
    assert len(received) >= 120  # ninguna línea del trabajo se pierde al reanudar
    assert all(("S51" in ln) if host.profile.firmware == "marlin" else ("S0.2" in ln) for ln in received)


def test_stop_aborts_and_laser_off(host):
    host.connect()
    host.home()
    host.sim.speed_factor = 2.0
    lines = ["G90", "M4S0"] + [f"G1X{10 + i}Y10F600S200" for i in range(100)] + ["M5"]
    assert host.ctl.start_job(lines, "stop")
    assert wait_for(lambda: host.ctl._job is not None and host.ctl._job.acked > 5, 8)
    assert host.ctl.stop()
    assert wait_for(lambda: host.ctl.status().job == JobState.ABORTED, 5)
    assert wait_for(lambda: not host.sim.laser_emitting, 3)
    if host.profile.firmware == "marlin":
        assert "M410" in host.sim.lines_received
    else:
        assert wait_for(lambda: host.ctl.status().grbl == GrblState.ALARM, 3)  # Ctrl-X: alarma hasta $X
        host.drain()
        assert "fw_halted" in host.notices
        assert host.ctl.unlock()
        assert wait_for(lambda: host.ctl.status().grbl == GrblState.IDLE, 5)


def test_fire_marlin_stationary_and_smoothie_refused(host):
    host.connect()
    res = host.ctl.fire(1, 0.3, confirmed=True)
    if host.profile.firmware == "smoothie":
        assert res.ok  # se acepta la petición, pero el hilo la rechaza
        assert host.idle()
        assert host.sim.emissions == []
        return
    assert wait_for(lambda: any(e.context == "stationary" for e in host.sim.emissions), 5)
    assert wait_for(lambda: not host.sim.laser_emitting and not host.ctl.status().fire_active, 5)
    assert "M5" in host.sim.lines_received[-3:]


def test_marlin_estop_kills():
    r = HostRig("marlin")
    try:
        r.connect()
        assert r.ctl.emergency_stop()
        assert wait_for(lambda: r.sim.snapshot()["killed"], 3)
        assert wait_for(lambda: r.ctl.status().grbl == GrblState.ALARM, 3)
        r.drain()
        assert "fw_halted" in r.notices or "marlin_killed" in r.notices
        assert not r.sim.laser_emitting
    finally:
        r.close()


def test_marlin_without_emergency_parser_warns():
    r = HostRig("marlin", sim=MarlinSimulator(speed_factor=40, emergency_parser=False))
    try:
        r.connect()
        assert wait_for(lambda: (r.drain(), "no_emergency_parser" in r.notices)[1], 5)
    finally:
        r.close()


@pytest.mark.parametrize("profile_fw,sim_fw", [("marlin", "grbl"), ("smoothie", "marlin"), ("grbl", "marlin"),
                                               ("grbl", "smoothie")])
def test_wrong_firmware_disconnects_with_hint(profile_fw, sim_fw):
    sim = make_simulator(sim_fw, 40.0)
    if isinstance(sim, GrblSimulator):
        sim.settings[22] = 0
    r = HostRig(sim_fw, sim=sim, profile_fw=profile_fw)
    try:
        assert r.ctl.connect()
        assert wait_for(lambda: r.ctl.status().conn == ConnState.DISCONNECTED and r.ctl.status().error, 15)
        assert "parece" in r.ctl.status().error or "no parece" in r.ctl.status().error
        assert not getattr(r.sim, "laser_emitting", False)
    finally:
        r.close()
