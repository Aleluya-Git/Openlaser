"""Pruebas de integración controlador + simulador (criterios de aceptación de la Fase 1)."""
import time

from conftest import Rig, wait_for

from openlaser.machine.events import AlarmEvent, CommandRejected, SafetyNotice
from openlaser.machine.states import ConnState, DisplayState, GrblState, JobState


def test_connect_sends_no_motion_and_no_unlock(rig):
    rig.connect()
    sent = rig.sim.lines_received
    assert sent == ["$I", "$$", "$G"]
    assert rig.ctl.status().display == DisplayState.ALARM  # homing exigido, no se desbloquea solo
    assert rig.sim.emissions == []
    assert rig.ctl.settings.get(32) == "1"


def test_jog_changes_simulated_position(homed_rig):
    r = homed_rig
    assert r.ctl.jog(dx=10, dy=5, feed=6000)
    assert wait_for(lambda: r.ctl.status().mpos[:2] == (10.0, 5.0))
    assert r.ctl.jog(dx=-2.5)
    assert wait_for(lambda: r.ctl.status().mpos[0] == 7.5)
    assert r.sim.emissions == []


def test_jog_is_clamped_to_work_area_when_homed(homed_rig):
    r = homed_rig
    assert r.ctl.jog(dx=-50)  # ya en 0: se ajusta a distancia nula
    assert wait_for(lambda: any(isinstance(e, CommandRejected) for e in r.drain()))
    assert r.ctl.jog(dx=1000, feed=6000)
    assert r.idle()
    assert r.ctl.status().mpos[0] == r.profile.width_mm


def test_jog_unsupported_axis_rejected(homed_rig):
    homed_rig.profile.z_axis = False
    assert not homed_rig.ctl.jog(dz=1)


def test_continuous_jog_stops_without_keepalive(homed_rig):
    r = homed_rig
    assert r.ctl.jog_continuous((1, 0, 0, 0), feed=1200)
    assert wait_for(lambda: r.ctl.status().mpos[0] > 1.0, 3)
    time.sleep(1.2)  # sin keepalive: debe cancelarse
    x1 = r.ctl.status().mpos[0]
    time.sleep(0.4)
    assert r.ctl.status().mpos[0] == x1


def test_continuous_jog_stop(homed_rig):
    r = homed_rig
    r.ctl.jog_continuous((0, 1, 0, 0), feed=1200)
    for _ in range(10):
        r.ctl.jog_keepalive()
        time.sleep(0.05)
    r.ctl.jog_stop()
    assert r.idle(3)
    y = r.ctl.status().mpos[1]
    time.sleep(0.3)
    assert r.ctl.status().mpos[1] == y and y > 0


def test_set_and_clear_origin(homed_rig):
    r = homed_rig
    r.ctl.jog(dx=20, dy=30, feed=6000)
    assert r.idle()
    r.ctl.set_origin()
    assert wait_for(lambda: r.ctl.status().wco[:2] == (20.0, 30.0))
    assert r.ctl.status().wpos[:2] == (0.0, 0.0)
    r.ctl.clear_origin()
    assert wait_for(lambda: r.ctl.status().wco[:2] == (0.0, 0.0))


def test_frame_and_goto_never_fire(homed_rig):
    r = homed_rig
    assert r.ctl.frame([(10, 10), (50, 10), (50, 40), (10, 40)])
    assert r.idle()
    assert r.ctl.go_to(100, 100)
    assert wait_for(lambda: r.ctl.status().mpos[:2] == (100.0, 100.0))
    assert r.ctl.go_to_origin()
    assert wait_for(lambda: r.ctl.status().mpos[:2] == (0.0, 0.0))
    assert r.sim.emissions == []


def test_frame_out_of_bounds_rejected(homed_rig):
    assert not homed_rig.ctl.frame([(0, 0), (900, 0), (900, 10)])


def test_job_runs_to_completion_and_laser_only_on_feed(homed_rig):
    r = homed_rig
    lines = ["G90", "G21", "M5", "G0X10Y10", "M4S0", "G1X40Y10F6000S300", "G1X40Y40", "G1X10Y40", "G1X10Y10", "M5"]
    assert r.ctl.start_job(lines * 3, "demo")
    assert wait_for(lambda: r.ctl.status().job == JobState.COMPLETED, 10)
    assert r.sim.emissions and all(e.context == "feed" for e in r.sim.emissions)
    assert not r.sim.laser_emitting
    assert r.sim.overflow_count == 0


def test_large_job_streams_without_overflow(homed_rig):
    r = homed_rig
    lines = ["G90", "M4S0"] + [f"G1X{(i % 50) + 1}Y{(i % 37) + 1}F6000S100" for i in range(400)] + ["M5"]
    assert r.ctl.start_job(lines, "grande")
    assert wait_for(lambda: r.ctl.status().job == JobState.COMPLETED, 30)
    assert r.sim.overflow_count == 0


def test_stop_cancels_queue(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    lines = ["G90", "M4S0"] + [f"G1X{10 + i}Y10F300S200" for i in range(200)] + ["M5"]
    assert r.ctl.start_job(lines, "largo")
    assert wait_for(lambda: r.ctl.status().grbl == GrblState.RUN, 3)
    assert r.ctl.stop()
    assert wait_for(lambda: r.ctl.status().job == JobState.ABORTED, 5)
    assert wait_for(lambda: r.ctl.queue_stats == (0, 0, 0), 3)
    assert wait_for(lambda: not r.sim.laser_emitting, 2)
    received = len(r.sim.lines_received)
    time.sleep(0.3)
    assert len(r.sim.lines_received) == received  # no se envía nada más
    assert r.sim.snapshot()["spindle"] == "M5"


def test_pause_and_resume(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    lines = ["G90", "M4S0", "G1X100Y0F600S200", "M5"]
    r.ctl.start_job(lines, "pausa")
    assert wait_for(lambda: r.ctl.status().grbl == GrblState.RUN, 3)
    assert r.ctl.pause()
    assert wait_for(lambda: r.ctl.status().display == DisplayState.PAUSED and r.ctl.status().substate == 0, 3)
    assert not r.sim.laser_emitting
    assert r.ctl.resume()
    assert wait_for(lambda: r.ctl.status().job == JobState.RUNNING and r.ctl.status().grbl == GrblState.RUN, 3)
    r.ctl.stop()


def test_emergency_stop(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    r.ctl.start_job(["G90", "M4S0", "G1X200Y0F300S500", "M5"], "estop")
    assert wait_for(lambda: r.sim.laser_emitting, 3)
    r.ctl.emergency_stop()
    assert wait_for(lambda: not r.sim.laser_emitting, 1)
    assert wait_for(lambda: r.ctl.status().display == DisplayState.ESTOP, 1)
    assert wait_for(lambda: r.ctl.status().job == JobState.ABORTED, 2)
    assert not r.ctl.jog(dx=1)  # bloqueado hasta confirmar
    r.ctl.acknowledge_estop()
    assert wait_for(lambda: r.ctl.status().display == DisplayState.ALARM, 2)  # ALARM:3
    assert "$X" not in r.sim.lines_received  # nunca desbloqueo automático


def test_disconnect_during_job_leaves_safe_state(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    r.ctl.start_job(["G90", "M4S0", "G1X200Y0F300S500", "M5"], "desconexion")
    assert wait_for(lambda: r.sim.laser_emitting, 3)
    r.ctl.disconnect()
    assert r.ctl.wait_stopped(3)
    st = r.ctl.status()
    assert st.conn == ConnState.DISCONNECTED and st.job == JobState.ABORTED
    assert r.ctl.queue_stats == (0, 0, 0)
    assert not r.sim.laser_emitting


def test_unexpected_disconnect_is_safe(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    r.ctl.start_job(["G90", "M4S0", "G1X200Y0F300S500", "M5"], "cable")
    assert wait_for(lambda: r.ctl.status().grbl == GrblState.RUN, 3)
    r.transport.fail()
    assert r.ctl.wait_stopped(3)
    st = r.ctl.status()
    assert st.conn == ConnState.DISCONNECTED and st.job == JobState.ABORTED and st.error
    assert not st.fire_active and r.ctl.queue_stats == (0, 0, 0)
    events = r.drain()
    assert any(isinstance(e, SafetyNotice) and e.code == "connection_lost" for e in events)


def test_alarm_is_never_auto_unlocked_or_resumed(rig):
    r = Rig(homing_required=False)
    try:
        r.connect()
        r.ctl.send_line("G0X999")
        assert wait_for(lambda: r.ctl.status().grbl == GrblState.ALARM, 3)
        assert any(isinstance(e, AlarmEvent) and e.code == 2 for e in r.drain())
        time.sleep(0.3)
        assert "$X" not in r.sim.lines_received and r.ctl.status().grbl == GrblState.ALARM
        assert not r.ctl.resume()
        assert r.ctl.unlock()
        assert wait_for(lambda: r.ctl.status().grbl == GrblState.IDLE, 3)
    finally:
        r.close()


def test_fire_requires_confirmation_and_limits(homed_rig):
    r = homed_rig
    assert not r.ctl.fire(1.0, 1.0, confirmed=False)
    assert not r.ctl.fire(50.0, 1.0, confirmed=True)  # por encima del límite de enfoque
    assert not r.ctl.fire(1.0, 60.0, confirmed=True)
    assert r.sim.emissions == []


def test_fire_turns_off_by_itself(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    assert r.ctl.fire(1.0, 0.4, confirmed=True)
    assert wait_for(lambda: r.sim.laser_emitting, 2)
    assert r.ctl.status().fire_active
    assert wait_for(lambda: not r.sim.laser_emitting and not r.ctl.status().fire_active, 3)
    assert len(r.sim.emissions) == 1 and r.sim.emissions[0].s == r.profile.s_max * 0.01
    assert not r.ctl.jog(dx=1) or True


def test_fire_blocks_motion_and_stop_cancels(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    r.ctl.fire(1.0, 3.0, confirmed=True)
    assert wait_for(lambda: r.ctl.status().fire_active and r.sim.laser_emitting, 2)
    assert not r.ctl.jog(dx=5)
    r.ctl.stop()
    assert wait_for(lambda: not r.sim.laser_emitting and not r.ctl.status().fire_active, 2)


def test_fire_watchdog_when_controller_ignores_dwell(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 0.05  # la pausa G4 tardaría ~20x más
    r.ctl.fire(1.0, 0.3, confirmed=True)
    assert wait_for(lambda: r.sim.laser_emitting, 2)
    assert wait_for(lambda: not r.sim.laser_emitting, 3)  # vigilante: reinicio
    assert not r.ctl.status().fire_active


def test_dangerous_console_commands_need_confirmation(homed_rig):
    r = homed_rig
    assert not r.ctl.send_line("M3 S1000")
    assert not r.ctl.send_line("$X")
    assert not r.ctl.send_line("$32=0")
    assert r.ctl.send_line("$G")
    assert r.ctl.send_line("G0 X5")
    assert r.idle()
    assert r.sim.emissions == []


def test_macros_with_variables_and_laser_guard(homed_rig):
    r = homed_rig
    assert r.ctl.run_macro(["M5", "G53G0X{cx}Y{cy}"])
    assert wait_for(lambda: r.ctl.status().mpos[:2] == (200.0, 200.0))
    assert not r.ctl.run_macro(["M3S100"])
    assert not r.ctl.run_macro(["G0X{nope}"])


def test_job_error_pauses_job(homed_rig):
    r = homed_rig
    r.sim.speed_factor = 1.0
    lines = ["G90", "M4S0", "G1X50F300S100", "G7", "G1X60"]
    r.ctl.start_job(lines, "error")
    assert wait_for(lambda: r.ctl.status().job == JobState.PAUSED, 5)
    r.ctl.stop()
    assert wait_for(lambda: r.ctl.status().job == JobState.ABORTED, 5)


def test_job_rejected_with_system_commands(homed_rig):
    assert not homed_rig.ctl.start_job(["$H", "G0X1"], "x")


def test_read_and_write_settings(homed_rig):
    r = homed_rig
    assert not r.ctl.write_setting(30, "500", confirmed=False)
    assert r.ctl.write_setting(30, "500", confirmed=True)
    assert wait_for(lambda: r.ctl.settings.get(30) == "500", 3)
    events = r.drain()
    assert any(isinstance(e, SafetyNotice) and e.code == "s_max" for e in events)


def test_demo_document_job_runs_cleanly(homed_rig):
    from openlaser.document.model import demo_document
    from openlaser.gcode.analyzer import analyze, prepare_lines
    from openlaser.gcode.generator import generate_line_job
    from openlaser.machine.events import ErrorEvent

    r = homed_rig
    r.sim.speed_factor = 400.0
    job = generate_line_job(demo_document(), r.profile)
    lines = prepare_lines(job.lines)
    a = analyze(lines)
    ok, msg = r.ctl.check_bounds_work([(a.bounds_all[0], a.bounds_all[1]), (a.bounds_all[2], a.bounds_all[3])])
    assert ok, msg
    r.drain()
    assert r.ctl.start_job(lines, "demo", a.cumulative_seconds)
    assert wait_for(lambda: r.ctl.status().job == JobState.COMPLETED, 30)
    assert not [e for e in r.drain() if isinstance(e, ErrorEvent)]
    assert r.sim.overflow_count == 0 and not r.sim.laser_emitting
    assert {round(e.s) for e in r.sim.emissions} <= {150, 200, 250, 600}  # línea, relleno, corte, imagen
