"""Fase 5: dialectos grblHAL y FluidNC (detección, búfer, alarmas, parámetros, seguridad)."""
import pytest

from conftest import Rig, wait_for
from openlaser.devices.profile import demo_profile
from openlaser.devices.transport import SimulatorTransport, create_transport
from openlaser.machine import dialects
from openlaser.machine import grbl_protocol as gp
from openlaser.machine.events import SafetyNotice
from openlaser.machine.grbl_codes import alarm_info, error_info
from openlaser.machine.states import Action, ConnState, DisplayState, GrblState, JobState, allowed_actions, \
    derive_display_state
from openlaser.plugins import controllers


def make_rig(firmware: str, profile_fw: str | None = None, **kw) -> Rig:
    r = Rig(firmware=firmware, **kw)
    r.profile.firmware = profile_fw or firmware
    return r


def notices(r: Rig) -> dict[str, SafetyNotice]:
    """Avisos recibidos hasta ahora (se acumulan en el propio Rig entre llamadas)."""
    seen = r.__dict__.setdefault("_notices", {})
    seen.update({e.code: e for e in r.drain() if isinstance(e, SafetyNotice)})
    return seen


def wait_notice(r: Rig, code: str, timeout: float = 3.0) -> SafetyNotice:
    assert wait_for(lambda: code in notices(r), timeout), f"sin aviso {code}"
    return notices(r)[code]


# ------------------------------------------------------------------ núcleo puro
def test_detection_rules():
    assert dialects.detect_welcome("Grbl 1.1h ['$' for help]") == "grbl"
    assert dialects.detect_welcome("GrblHAL 1.1f ['$' or '$$' for help]") == "grblhal"
    assert dialects.detect_welcome("Grbl 3.7 [FluidNC v3.7.8 (wifi) '$' for help]") == "fluidnc"
    assert dialects.detect_welcome("Marlin 2.1") is None
    assert dialects.detect_feedback("FIRMWARE", "grblHAL") == "grblhal"
    assert dialects.detect_feedback("VER", "3.7 FluidNC v3.7.8:") == "fluidnc"
    assert dialects.detect_feedback("NEWOPT", "ENUMS,RT+") == "grblhal"
    assert dialects.detect_feedback("VER", "1.1h.20190825:") is None
    assert dialects.rx_buffer_from_opt("VL,15,128") == 128
    assert dialects.rx_buffer_from_opt("VNMHL,35,1023,4") == 1023
    assert dialects.rx_buffer_from_opt("PHS") is None
    assert dialects.usable_rx(1023) == 1022 and dialects.usable_rx(10) == 80 and dialects.usable_rx(10 ** 6) == 4096
    assert dialects.get("desconocido").id == "grbl"


def test_status_extensions_and_tool_state():
    rep = gp.parse_status("<Tool|MPos:1.000,2.000,0.000|FS:0,0|Pn:XE|H:1,3|SD:1>")
    assert rep.state == "Tool" and rep.extra["H"] == "1,3" and "E" in rep.pins
    assert GrblState.parse("Tool") == GrblState.TOOL
    disp = derive_display_state(ConnState.CONNECTED, GrblState.TOOL, JobState.NONE, False)
    assert disp == DisplayState.HOLD
    acts = allowed_actions(ConnState.CONNECTED, GrblState.TOOL, JobState.PAUSED, False)
    assert Action.RESUME in acts and Action.START not in acts and Action.JOG not in acts


def test_alarm_and_error_texts_per_dialect():
    assert alarm_info(10, "grblhal").description["en"] == "E-stop asserted"
    assert alarm_info(14, "fluidnc").description["en"] == "Unhomed" and alarm_info(14, "fluidnc").recovery == "$H"
    assert alarm_info(1, "fluidnc").description == alarm_info(1, "grbl").description  # 1–9 comunes
    assert "Unknown" in alarm_info(42, "grblhal").description["en"]
    assert "$E" in error_info(152, "fluidnc").action["es"]
    assert error_info(22, "grblhal").description == error_info(22).description
    assert 10 in dialects.GRBLHAL.lost_position_alarms and 14 in dialects.FLUIDNC.lost_position_alarms


def test_adapters_available():
    ids = {a.id: a for a in controllers.adapters()}
    for fw in ("grbl", "grblhal", "fluidnc"):
        assert ids[fw].status == "available" and ids[fw].factory is not None
    assert "settings" not in ids["fluidnc"].capabilities and "sd_upload" in ids["fluidnc"].capabilities
    assert ids["ruida"].status == "planned"


def test_simulator_transport_follows_profile_firmware():
    p = demo_profile()
    p.firmware = "fluidnc"
    t = create_transport(p)
    assert isinstance(t, SimulatorTransport) and t.simulator.firmware == "fluidnc"


# ------------------------------------------------------------- con simulador
def test_grblhal_detected_large_buffer_and_homed_report():
    r = make_rig("grblhal")
    try:
        r.connect()
        assert wait_for(lambda: r.ctl.dialect.id == "grblhal")
        assert wait_for(lambda: r.ctl._queue.rx_buffer_size == 1022)  # [OPT:…,1023] − 1
        assert r.ctl.status().dialect == "grblhal"
        assert not r.ctl.status().homed
        r.home()
        assert r.ctl.status().homed
        n = notices(r)
        assert "dialect_mismatch" not in n
        # trabajo largo: con 1 KB de búfer no hay desbordamiento y el láser sólo emite al avanzar
        lines = ["G90", "M4S0", "G0X5Y5"] + [f"G1X{(i % 50) + 1}Y{(i % 37) + 1}F6000S100" for i in range(400)] + ["M5"]
        assert r.ctl.start_job(lines, "grande")
        assert wait_for(lambda: r.ctl.status().job == JobState.COMPLETED, 30)
        assert r.sim.overflow_count == 0
        assert r.sim.emissions and all(e.context == "feed" for e in r.sim.emissions)
    finally:
        r.close()


def test_grblhal_estop_input_alarm():
    r = make_rig("grblhal")
    try:
        r.connect()
        r.home()
        r.sim.set_estop(True)
        assert wait_for(lambda: r.ctl.status().grbl == GrblState.ALARM)
        assert wait_for(lambda: not r.ctl.status().homed)
        assert wait_notice(r, "estop_input").severity == "block"
        assert not r.sim.laser_emitting
    finally:
        r.close()


def test_grblhal_in_compatibility_mode_detected_by_info():
    r = make_rig("grblhal")
    r.sim.banner = "Grbl 1.1f ['$' for help]"  # grblHAL con COMPATIBILITY_LEVEL alto
    try:
        r.connect()
        assert wait_for(lambda: r.ctl.dialect.id == "grblhal")
        assert "dialect_mismatch" not in notices(r)
    finally:
        r.close()


def test_fluidnc_detected_settings_read_only_and_messages():
    r = make_rig("fluidnc")
    try:
        r.connect()
        assert wait_for(lambda: r.ctl.dialect.id == "fluidnc")
        assert r.ctl._queue.rx_buffer_size == 127  # sin tamaño en [OPT:] → el del perfil
        assert not r.ctl.settings_writable
        res = r.ctl.write_setting(100, "80", confirmed=True)
        assert not res and "YAML" in res.reason
        r.sim.out.append("[MSG:ERR: Bad config value]")
        wait_notice(r, "fw_error")
        assert r.ctl.read_settings()  # $$ sigue funcionando (lectura)
        assert wait_for(lambda: r.ctl.settings.get(32) is not None)
    finally:
        r.close()


def test_fluidnc_info_notice_and_mismatch_warning():
    r = make_rig("fluidnc", profile_fw="grbl")
    try:
        r.connect()
        assert wait_for(lambda: r.ctl.dialect.id == "fluidnc")
        mm = wait_notice(r, "dialect_mismatch")
        assert mm.severity == "warn" and "FluidNC" in mm.message
        wait_notice(r, "fluidnc_laser")
    finally:
        r.close()


def test_profile_says_grblhal_but_controller_is_grbl():
    r = make_rig("grbl", profile_fw="grblhal")
    try:
        r.connect()
        assert wait_for(lambda: r.ctl.dialect.id == "grbl")
        assert r.ctl._queue.rx_buffer_size == 127  # nunca se amplía sin grblHAL
        wait_notice(r, "dialect_mismatch")
    finally:
        r.close()


@pytest.mark.parametrize("fw", ["grbl", "grblhal", "fluidnc"])
def test_laser_off_guarantees_on_every_dialect(fw):
    r = make_rig(fw)
    try:
        r.connect()
        r.home()
        assert r.sim.emissions == []  # conectar y hacer Home nunca encienden el láser
        assert r.ctl.jog(dx=10, dy=5)
        assert r.idle()
        lines = ["G90", "G21", "M5", "G0X10Y10", "M4S0", "G1X40Y10F6000S300", "G0X60Y60", "G1X80Y60S300", "M5"]
        assert r.ctl.start_job(lines, "demo")
        assert wait_for(lambda: r.ctl.status().job == JobState.COMPLETED, 10)
        assert r.sim.emissions and all(e.context == "feed" for e in r.sim.emissions)
        assert not r.sim.laser_emitting
        # una alarma de cualquier firmware detiene todo y exige acción del usuario
        assert r.ctl.emergency_stop()
        assert wait_for(lambda: r.ctl.status().estop)
        assert not r.sim.laser_emitting
    finally:
        r.close()
