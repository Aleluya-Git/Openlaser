import pytest

from openlaser.devices.profile import demo_profile
from openlaser.document.model import Document, Layer, LayerMode, Shape, demo_document, rectangle
from openlaser.gcode.analyzer import analyze, format_duration, prepare_lines
from openlaser.gcode.generator import JobOrigin, generate_line_job
from openlaser.safety.manager import SafetyManager


def test_generator_header_footer_and_laser_off_travel():
    prof = demo_profile()
    job = generate_line_job(demo_document(), prof)
    lines = job.lines
    assert lines[:3] == ["G90", "G21", "M5"]
    assert lines[-2:] == ["M5", "M9"]
    for ln in lines:
        if ln.startswith("G0"):
            assert "S" not in ln.replace("G0", "")  # nunca potencia en G0
    assert job.skipped_layers == []
    sm = SafetyManager()
    risky = [ln for ln in lines if sm.classify_command(ln).fires_laser]
    assert all(ln.startswith("G1") for ln in risky)


def test_power_clamped_to_profile_s_max():
    prof = demo_profile()
    layer = Layer("x", "#000", id="x", power_max_pct=150)
    doc = Document(layers=[layer], shapes=[Shape("x", rectangle(10, 10, 10, 10))])
    job = generate_line_job(doc, prof)
    s_vals = [float(ln.split("S")[1]) for ln in job.lines if ln.startswith("G1") and "S" in ln]
    assert s_vals and max(s_vals) <= prof.s_max


def test_passes_and_closed_paths():
    prof = demo_profile()
    layer = Layer("x", "#000", id="x", passes=3, power_max_pct=10)
    doc = Document(layers=[layer], shapes=[Shape("x", rectangle(0, 0, 10, 10))])
    job = generate_line_job(doc, prof)
    assert sum(1 for ln in job.lines if ln.startswith("G0")) == 3
    assert job.work_points[0] == job.work_points[4]


def test_all_layer_modes_are_generated():
    prof = demo_profile()
    doc = demo_document()
    job = generate_line_job(doc, prof)
    assert [sp.mode for sp in job.layer_spans] == ["line", "fill", "line", "image"]
    assert job.skipped_layers == [] and job.warnings == []
    for sp in job.layer_spans:
        assert job.lines[sp.start] == f"; capa: {sp.name}"


def test_layer_without_output_geometry_is_skipped():
    prof = demo_profile()
    layer = Layer("img", "#000", id="i", mode=LayerMode.IMAGE)
    doc = Document(layers=[layer], shapes=[Shape("i", rectangle(0, 0, 10, 10))])
    job = generate_line_job(doc, prof)
    assert job.skipped_layers == ["img"] and job.warnings


def test_user_origin_mode_starts_at_zero():
    prof = demo_profile()
    doc = Document(layers=[Layer("x", "#000", id="x")], shapes=[Shape("x", rectangle(100, 100, 20, 10))])
    job = generate_line_job(doc, prof, origin=JobOrigin("user"))
    xs = [p[0] for p in job.work_points]
    ys = [p[1] for p in job.work_points]
    assert min(xs) == pytest.approx(0) and min(ys) == pytest.approx(0)
    assert max(xs) == pytest.approx(20) and max(ys) == pytest.approx(10)


def test_analyzer_bounds_time_and_frame():
    lines = ["G90 G21", "M4 S0", "G0 X10 Y10", "G1 X110 Y10 F6000 S500", "G1 X110 Y60", "M5", "G0 X0 Y0"]
    a = analyze(lines, rapid_mm_min=6000, time_factor=1.0)
    assert a.bounds_laser == (10, 10, 110, 60)
    assert a.bounds_all == (0, 0, 110, 60)
    assert a.cut_distance == pytest.approx(150)
    assert a.estimated_seconds == pytest.approx(150 / 100 + (14.1421 + 125.2996) / 100, rel=1e-3)
    assert a.max_s == 500 and len(a.cumulative_seconds) == len(lines)
    assert a.frame_points and a.frame_points[0] == a.frame_points[-1]


def test_analyzer_arcs_inches_and_system_commands():
    a = analyze(["G20", "G0X0Y0", "M3S100", "G2X2Y0I1J0F10", "$H"])
    assert a.uses_inches and a.has_system_commands
    assert a.bounds_laser[2] == pytest.approx(50.8, rel=1e-3)
    assert a.cut_distance == pytest.approx(3.14159 * 25.4, rel=1e-3)


def test_prepare_and_duration_format():
    assert prepare_lines(["(c)", "", "g0 x1 ; c", "%"]) == ["G0X1"]
    assert format_duration(65) == "01:05" and format_duration(3725) == "1:02:05"
    assert format_duration(None) == "--:--"


def test_number_formatting_keeps_integer_zeros():
    from openlaser.gcode.generator import fmt
    from openlaser.machine.controller import _fmt
    assert fmt(2000, 0) == "2000" and fmt(100.0, 1) == "100" and fmt(0.1000) == "0.1"
    assert fmt(-0.0001) == "0" and fmt(1500.25, 0) == "1500"
    assert _fmt(3000) == "3000" and _fmt(-0.0) == "0" and _fmt(12.5) == "12.5"


def test_generated_feed_matches_layer_speed():
    prof = demo_profile()
    layer = Layer("x", "#000", id="x", speed_mm_min=2000, power_max_pct=15)
    doc = Document(layers=[layer], shapes=[Shape("x", rectangle(10, 10, 10, 10))])
    job = generate_line_job(doc, prof)
    feeds = [ln for ln in job.lines if "F" in ln]
    assert feeds and all("F2000" in ln for ln in feeds)
    a = analyze(job.lines)
    assert a.max_feed == 2000 and a.max_s == 150
