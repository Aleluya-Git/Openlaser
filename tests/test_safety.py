from openlaser.safety.manager import (
    HARD_MAX_FIRE_PERCENT, HARD_MAX_FIRE_SECONDS, Risk, SafetyConfig, SafetyManager, Severity, has_blocking,
)


def test_classify_laser_commands():
    sm = SafetyManager()
    assert sm.classify_command("M3 S100").fires_laser
    assert sm.classify_command("m4s1").level == Risk.DANGER
    assert not sm.classify_command("M3 S0").fires_laser
    assert sm.classify_command("M5").level == Risk.SAFE
    assert sm.classify_command("M30").level == Risk.SAFE  # M30 no es M3
    assert sm.classify_command("G1 X10 S200").fires_laser
    assert sm.classify_command("G0 X10").level == Risk.CAUTION


def test_classify_system_commands():
    sm = SafetyManager()
    assert sm.classify_command("$X").level == Risk.DANGER
    assert sm.classify_command("$RST=*").level == Risk.DANGER
    r = sm.classify_command("$32=0")
    assert r.level == Risk.DANGER and len(r.reasons) == 2
    assert sm.classify_command("$H").level == Risk.CAUTION
    assert sm.classify_command("$$").level == Risk.SAFE


def test_fire_limits_are_hard_capped():
    cfg = SafetyConfig(max_fire_percent=80, max_fire_seconds=600).clamp()
    assert cfg.max_fire_percent == HARD_MAX_FIRE_PERCENT
    assert cfg.max_fire_seconds == HARD_MAX_FIRE_SECONDS
    sm = SafetyManager(SafetyConfig(max_fire_percent=1, max_fire_seconds=2))
    assert sm.validate_fire(1, 2)[0]
    assert not sm.validate_fire(1.5, 1)[0]
    assert not sm.validate_fire(1, 3)[0]
    assert not sm.validate_fire(0, 1)[0]


def test_preflight_blocks_and_warns():
    sm = SafetyManager()
    issues = sm.preflight(connected=True, idle=True, estop=False, door_open=False, homed=False,
                          homing_enabled=True, bounds_ok=True,
                          grbl_settings={32: "0", 30: "255", 13: "1"}, s_max_profile=1000, max_s_in_job=2000)
    codes = {i.code for i in issues}
    assert {"not_homed", "laser_mode", "s_max", "units", "s_over"} <= codes
    assert not has_blocking(issues)
    issues = sm.preflight(connected=True, idle=False, estop=True, door_open=True, homed=True,
                          homing_enabled=True, bounds_ok=False, has_system_commands=True)
    assert {i.code for i in issues if i.severity == Severity.BLOCK} == {
        "estop", "door", "not_idle", "bounds", "system_cmd"}


def test_block_without_homing_option():
    sm = SafetyManager(SafetyConfig(block_without_homing=True))
    issues = sm.preflight(connected=True, idle=True, estop=False, door_open=False, homed=False,
                          homing_enabled=True, bounds_ok=None)
    assert has_blocking(issues)


def test_event_log():
    sm = SafetyManager()
    sm.record("x", "prueba")
    assert sm.events()[-1].kind == "x"
