from openlaser.machine import grbl_protocol as gp


def test_ok_error_alarm():
    assert isinstance(gp.parse_line("ok"), gp.Ok)
    assert gp.parse_line("error:9") == gp.ErrorMsg(9)
    assert gp.parse_line("ALARM:2") == gp.AlarmMsg(2)


def test_welcome_and_settings():
    w = gp.parse_line("Grbl 1.1h ['$' for help]")
    assert isinstance(w, gp.Welcome) and w.version == "1.1h"
    assert gp.parse_line("$32=1") == gp.SettingMsg(32, "1")
    assert gp.parse_line("$130=400.000") == gp.SettingMsg(130, "400.000")


def test_feedback():
    fb = gp.parse_line("[MSG:'$H'|'$X' to unlock]")
    assert isinstance(fb, gp.Feedback) and fb.kind == "MSG"
    gc = gp.parse_line("[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]")
    assert gc.kind == "GC" and "M5" in gc.payload
    assert gp.parse_line("[G54:1.000,2.000,0.000]").kind == "G54"


def test_status_full():
    rep = gp.parse_line("<Hold:0|MPos:10.000,20.500,-1.000|Bf:15,127|FS:1200,500|Pn:XD|WCO:5.000,5.000,0.000>")
    assert isinstance(rep, gp.StatusReport)
    assert rep.state == "Hold" and rep.substate == 0
    assert rep.mpos == (10.0, 20.5, -1.0)
    assert rep.feed == 1200 and rep.spindle == 500
    assert rep.planner_free == 15 and rep.rx_free == 127
    assert rep.pins == "XD"
    assert rep.wco == (5.0, 5.0, 0.0)
    assert rep.accessories is None


def test_status_overrides_and_accessories():
    rep = gp.parse_line("<Run|WPos:1,2,3,4|FS:100,0|Ov:110,50,90|A:SF>")
    assert rep.wpos == (1.0, 2.0, 3.0, 4.0)
    assert rep.overrides == (110, 50, 90)
    assert rep.spindle_on is True
    rep2 = gp.parse_line("<Idle|MPos:0,0,0|FS:0,0|Ov:100,100,100>")
    assert rep2.accessories == "" and rep2.spindle_on is False


def test_status_malformed_field_is_tolerated():
    rep = gp.parse_line("<Idle|MPos:a,b,c|FS:0,0>")
    assert rep.state == "Idle" and "MPos" in rep.extra


def test_sanitize():
    assert gp.sanitize_line("g1 x10 (comentario) y5 ; fin") == "G1X10Y5"
    assert gp.sanitize_line("%") == ""
    assert gp.sanitize_line("$J=G91 X1 F100") == "$J=G91X1F100"
    assert gp.is_system_command("$H") and not gp.is_system_command("$J=G91X1F10")
