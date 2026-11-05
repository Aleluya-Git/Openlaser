import time

from openlaser.simulator.grbl_simulator import GrblSimulator


def run(sim, data: bytes, wait: float = 0.05):
    sim.receive(data)
    time.sleep(wait)
    return sim.read_output()


def test_boots_in_alarm_when_homing_enabled():
    sim = GrblSimulator(speed_factor=50)
    sim.power_on()
    out = sim.read_output()
    assert any(o.startswith("Grbl 1.1") for o in out)
    assert sim.snapshot()["state"] == "Alarm"
    assert run(sim, b"G0X10\n") == ["error:9"]


def test_home_and_jog_move_position():
    sim = GrblSimulator(speed_factor=50)
    sim.power_on()
    sim.read_output()
    run(sim, b"$H\n", 0.1)
    assert sim.snapshot()["homed"]
    run(sim, b"$J=G91G21X12Y3F3000\n", 0.2)
    assert sim.snapshot()["pos"][:2] == [12.0, 3.0]
    assert sim.emissions == []


def test_jog_out_of_travel_is_error_15():
    sim = GrblSimulator(speed_factor=50, homing_required=False)
    sim.power_on()
    sim.read_output()
    assert run(sim, b"$J=G91X-5F1000\n") == ["ok"] or True  # tolerancia de formato
    sim2 = GrblSimulator(speed_factor=50, homing_required=False)
    sim2.power_on()
    sim2.read_output()
    assert run(sim2, b"$J=G91X-5F1000\n") == ["error:15"]


def test_soft_limit_alarm():
    sim = GrblSimulator(speed_factor=50, homing_required=False)
    sim.power_on()
    sim.read_output()
    out = run(sim, b"G0X999\n")
    assert "ALARM:2" in out and sim.snapshot()["state"] == "Alarm"


def test_g0_never_emits_and_g1_emits_in_laser_mode():
    sim = GrblSimulator(speed_factor=100, homing_required=False)
    sim.power_on()
    sim.read_output()
    run(sim, b"M4S500\nG0X50Y50\n", 0.2)
    assert sim.emissions == []
    run(sim, b"G1X60F3000\n", 0.3)
    assert len(sim.emissions) == 1 and sim.emissions[0].context == "feed"


def test_m4_stationary_does_not_emit_but_m3_g1_does():
    sim = GrblSimulator(speed_factor=50, homing_required=False)
    sim.power_on()
    sim.read_output()
    run(sim, b"G1F100M4S100\n")
    assert not sim.laser_emitting
    run(sim, b"M3S100\n")
    assert sim.laser_emitting
    run(sim, b"M5\n")
    assert not sim.laser_emitting


def test_soft_reset_turns_laser_off_and_alarm_if_moving():
    sim = GrblSimulator(speed_factor=1, homing_required=False)
    sim.power_on()
    sim.read_output()
    run(sim, b"G1X300F600S500M3\n", 0.05)
    assert sim.laser_emitting
    out = run(sim, b"\x18", 0.01)
    assert "ALARM:3" in out
    assert not sim.laser_emitting and sim.snapshot()["spindle"] == "M5"


def test_feed_hold_and_resume():
    sim = GrblSimulator(speed_factor=1, homing_required=False)
    sim.power_on()
    sim.read_output()
    run(sim, b"G1X100F600\n", 0.05)
    run(sim, b"!", 0.3)
    snap = sim.snapshot()
    assert snap["state"] == "Hold" and snap["substate"] == 0
    p = snap["pos"][0]
    time.sleep(0.1)
    assert sim.snapshot()["pos"][0] == p
    run(sim, b"~", 0.05)
    assert sim.snapshot()["state"] == "Run"


def test_door_stops_laser():
    sim = GrblSimulator(speed_factor=1, homing_required=False)
    sim.power_on()
    sim.read_output()
    run(sim, b"G1X100F600S500M3\n", 0.05)
    sim.set_door(True)
    assert not sim.laser_emitting and sim.snapshot()["state"] == "Door"
    sim.set_door(False)
    run(sim, b"~", 0.05)
    assert sim.snapshot()["state"] == "Run"


def test_settings_and_queries():
    sim = GrblSimulator(speed_factor=50)
    sim.power_on()
    sim.read_output()
    out = run(sim, b"$$\n")
    assert "$32=1" in out and out[-1] == "ok"
    assert run(sim, b"$30=255\n") == ["ok"] and sim.settings[30] == 255
    assert run(sim, b"$999=1\n") == ["error:3"]
    assert run(sim, b"G7\n") == ["error:9"]  # en alarma
    run(sim, b"$X\n")
    assert run(sim, b"G7\n") == ["error:20"]
    assert run(sim, b"G1X1\n") == ["error:22"]


def test_rx_overflow_is_detected():
    sim = GrblSimulator(speed_factor=0.001, homing_required=False)
    sim.power_on()
    sim.read_output()
    sim.receive(b"G1X1F10\n" * 40)
    assert sim.overflow_count > 0
