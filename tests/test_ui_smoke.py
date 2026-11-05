"""Prueba de humo de la interfaz (se omite si PySide6 no está instalado)."""
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from openlaser.machine.states import ConnState, DisplayState, GrblState, JobState  # noqa: E402
from openlaser.persistence.settings import AppSettings  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from openlaser.ui import theme
    a = QApplication.instance() or QApplication([])
    theme.apply_theme(a, "dark")  # type: ignore[arg-type]
    yield a
    cb = a.clipboard()
    if cb is not None:
        cb.clear()  # Windows: datos OLE propios en el portapapeles al salir → violación de acceso
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    a.processEvents()


def pump(app, seconds: float, until=None) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until else True


@pytest.fixture
def win(app, tmp_path):
    from openlaser.ui.main_window import MainWindow
    settings = AppSettings()
    settings.safety.require_checklist = True
    w = MainWindow(settings, tmp_path / "settings.json", interactive=False)
    w._simulator.speed_factor = 40.0
    w.profile.status_poll_ms = 40
    w.show()
    pump(app, 0.2)
    yield w
    w.controller.shutdown()
    w.close()
    w.deleteLater()
    pump(app, 0.05)


def test_main_window_layout(win):
    assert win.banner.isVisible()
    assert win.dock_motion.isVisible() and win.dock_console.isVisible() and win.dock_job.isVisible()
    assert win.banner.btn_estop.isVisible() and win.banner.btn_stop.isVisible()
    assert win.banner.state.text().endswith(DisplayState.DISCONNECTED.label)
    titles = [m.title() for m in win.menuBar().findChildren(type(win.menu_devices)) if m.title()]
    for name in ("Archivo", "Editar", "Dibujar", "Organizar", "Trayectoria", "Capas", "Láser", "Máquina", "Cámara",
                 "Herramientas", "Ventana", "Ayuda"):
        assert any(name in t.replace("&", "") for t in titles), name


def test_toolbar_actions_have_icon_tooltip_and_accessible_text(win):
    for tb in (win.tb_main, win.tb_draw, win.tb_modify):
        for act in tb.actions():
            if act.isSeparator():
                continue
            assert not act.icon().isNull(), act.objectName()
            assert act.toolTip(), act.objectName()
            w = tb.widgetForAction(act)
            assert w is not None and w.accessibleName(), act.objectName()


def test_connect_home_jog_stop_disconnect(app, win):
    win._connect()
    assert pump(app, 6, lambda: win.status.conn == ConnState.CONNECTED)
    win.controller.home()
    assert pump(app, 6, lambda: win.status.homed and win.status.grbl == GrblState.IDLE)
    right = next(b for b in win.motion.jog_buttons if b.accessibleName() == "Mover X+")
    win.motion.step.setValue(10.0)
    assert right.isEnabled()
    right.click()
    assert pump(app, 5, lambda: abs(win.status.mpos[0] - 10.0) < 1e-6)
    assert win.motion.pos_labels["X"][1].text().strip() == "10.000"
    assert win._simulator.emissions == []
    win.controller.stop()
    pump(app, 0.5)
    win._disconnect()
    assert pump(app, 5, lambda: win.status.conn == ConnState.DISCONNECTED)
    assert win.status.job in (JobState.NONE, JobState.ABORTED)


def test_preview_and_analysis(app, win):
    win._preview()
    pump(app, 0.2)
    assert not win.canvas.view.preview_cut.path().isEmpty()
    assert "líneas" in win.job.analysis_label.text()


def test_estop_button_from_banner(app, win):
    win._connect()
    assert pump(app, 6, lambda: win.status.conn == ConnState.CONNECTED)
    win.banner.btn_estop.click()
    assert pump(app, 3, lambda: win.status.estop)
    assert win.banner.btn_ack.isVisible()
    assert not win.motion.jog_buttons[0].isEnabled()
    win.controller.acknowledge_estop()
    assert pump(app, 3, lambda: not win.status.estop)


def test_screenshot(app, win, tmp_path):
    pix = win.grab()
    out = tmp_path / "captura.png"
    assert pix.save(str(out)) and out.stat().st_size > 10_000


def test_every_button_has_text_or_accessible_name(win):
    for b in win.findChildren(QPushButton):
        assert b.text() or b.accessibleName() or b.toolTip(), b.objectName()


# ============================================================ Fase 2: documento
def _rect(win, x, y, w, h):
    from openlaser.geometry import path as gp
    before = {s.id for s in win.doc.shapes}
    win.canvas.view.shape_created.emit([gp.rectangle(x, y, w, h)], "rect", "Rect")
    new = [s.id for s in win.doc.shapes if s.id not in before]
    assert len(new) == 1
    return new[0]


def test_tool_actions_are_exclusive(win):
    win.registry["draw.rect"].trigger()
    assert win.canvas.view.tool == "rect"
    assert win.registry["draw.rect"].isChecked() and not win.registry["draw.select"].isChecked()
    win.canvas.view.set_tool("select")
    assert win.registry["draw.select"].isChecked()


def test_draw_undo_redo_and_dirty(app, win):
    n = len(win.doc.shapes)
    assert not win.dirty
    sid = _rect(win, 10, 10, 30, 20)
    assert win.dirty and win.windowTitle().startswith("•")
    assert win.canvas.view.selected_shape_ids() == [sid]
    win.registry["edit.undo"].trigger()
    assert len(win.doc.shapes) == n
    win.registry["edit.redo"].trigger()
    assert len(win.doc.shapes) == n + 1 and win.doc.shape(sid) is not None


def test_properties_resize(win):
    sid = _rect(win, 10, 10, 30, 20)
    win.canvas.view.set_selection([sid])
    cur = {"x": 0.0, "y": 0.0, "w": 30.0, "h": 20.0}
    from openlaser.geometry.coords import CoordinateSystem
    b = win.doc.shape(sid).bounds()
    cs = CoordinateSystem.from_profile(win.profile)
    p1, p2 = cs.doc_to_machine((b[0], b[1])), cs.doc_to_machine((b[2], b[3]))
    cur["x"], cur["y"] = min(p1[0], p2[0]), min(p1[1], p2[1])
    win.properties.geometry_requested.emit({"current": cur, "key": "w", "value": 60.0, "keep": True})
    b = win.doc.shape(sid).bounds()
    assert abs((b[2] - b[0]) - 60) < 1e-6 and abs((b[3] - b[1]) - 40) < 1e-6


def test_boolean_union_and_group(win):
    a = _rect(win, 10, 10, 30, 30)
    b = _rect(win, 25, 25, 30, 30)
    win.canvas.view.set_selection([a, b])
    win._boolean("union")
    ids = win.canvas.view.selected_shape_ids()
    assert len(ids) == 1 and win.doc.shape(a) is None and win.doc.shape(b) is None
    bb = win.doc.shape(ids[0]).bounds()
    assert abs(bb[2] - bb[0] - 45) < 0.05
    c = _rect(win, 100, 100, 10, 10)
    win.canvas.view.set_selection([ids[0], c])
    win.registry["arrange.group"].trigger()
    assert win.doc.shape(c).group_id is not None and win.doc.shape(c).group_id == win.doc.shape(ids[0]).group_id


def test_copy_paste_duplicate_delete(app, win):
    sid = _rect(win, 10, 10, 10, 10)
    n = len(win.doc.shapes)
    win.canvas.view.set_selection([sid])
    win.registry["edit.copy"].trigger()
    win.registry["edit.paste"].trigger()
    assert len(win.doc.shapes) == n + 1
    win.registry["edit.duplicate"].trigger()
    assert len(win.doc.shapes) == n + 2
    win.registry["edit.delete"].trigger()
    assert len(win.doc.shapes) == n + 1
    app.clipboard().clear()


def test_layers_add_and_delete(win):
    n = len(win.doc.layers)
    win._layer_action("add", "")
    assert len(win.doc.layers) == n + 1
    new = max(win.doc.layers, key=lambda ly: ly.order)
    win._layer_action("delete", new.id)
    assert len(win.doc.layers) == n


def test_save_and_open_project(app, win, tmp_path):
    sid = _rect(win, 5, 5, 12, 12)
    count = len(win.doc.shapes)
    win.project_path = str(tmp_path / "prueba.olsp")
    assert win._save_project()
    assert not win.dirty
    win._new_document()
    assert len(win.doc.shapes) == 0
    win._open_project(str(tmp_path / "prueba.olsp"))
    assert len(win.doc.shapes) == count and win.doc.shape(sid) is not None
    assert not win.dirty and not win.undo.can_undo()


def test_import_svg(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    svg = tmp_path / "a.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" viewBox="0 0 50 50">'
                   '<rect x="5" y="5" width="20" height="10" stroke="#ff0000" fill="none"/>'
                   '<circle cx="35" cy="35" r="8" stroke="#0000ff" fill="none"/></svg>', encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(svg)], "")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    n = len(win.doc.shapes)
    win._import()
    assert len(win.doc.shapes) == n + 2
    assert len(win.canvas.view.selected_shape_ids()) == 2
    win.registry["edit.undo"].trigger()
    assert len(win.doc.shapes) == n


def test_text_to_curves(win):
    from openlaser.canvas.qt_geometry import text_to_subpaths
    subs = text_to_subpaths("OL", "Arial", 10.0, origin=(20.0, 50.0))
    assert len(subs) >= 2
    from openlaser.geometry.path import bounds_of
    b = bounds_of(subs)
    assert b is not None and 5 < b[3] - b[1] < 12


def test_offset_outward(win):
    from openlaser.canvas.qt_geometry import offset
    from openlaser.geometry import path as gp
    out = offset([gp.rectangle(0, 0, 10, 10)], 1.0, round_joins=False)
    b = gp.bounds_of(out)
    assert b is not None and abs(b[0] + 1) < 0.05 and abs(b[2] - 11) < 0.05


# ================================================================ Fase 3: imagen y simulación
def _png(tmp_path, w=80, h=40):
    from PySide6.QtGui import QColor, QImage
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("white"))
    for x in range(10, 50):
        for y in range(5, 35):
            img.setPixelColor(x, y, QColor("black"))
    path = tmp_path / "prueba.png"
    assert img.save(str(path))
    return str(path)


def test_insert_image_creates_image_layer_and_preview(win, tmp_path):
    from openlaser.document.model import LayerMode
    sid = win._insert_image(_png(tmp_path))
    shape = win.doc.shape(sid)
    assert shape is not None and shape.image is not None
    assert win.doc.layer(shape.layer_id).mode == LayerMode.IMAGE
    assert shape.image.resource in win.doc.images and sid in win.canvas.view._pix
    assert win.canvas.view.selected_shape_ids() == [sid]
    win.registry["edit.undo"].trigger()
    assert win.doc.shape(sid) is None


def test_image_settings_dialog_crop_keeps_scale(win, tmp_path, monkeypatch):
    from openlaser.ui.dialogs import image_dialogs
    sid = win._insert_image(_png(tmp_path))
    before = win.doc.shape(sid).image

    def fake_exec(self):
        self.method.setCurrentIndex(self.method.findData("stucki"))
        self.crop[0].setValue(25)
        self.brightness.setValue(20)
        self._render()
        return 1
    monkeypatch.setattr(image_dialogs.ImageDialog, "exec", fake_exec)
    win._image_settings()
    after = win.doc.shape(sid).image
    assert after.settings.method == "stucki" and after.settings.crop[0] == pytest.approx(0.25)
    assert after.width_mm == pytest.approx(before.width_mm * 0.75)
    assert after.corners[0][0] == pytest.approx(before.corners[0][0] + before.width_mm * 0.25)
    win.registry["edit.undo"].trigger()
    assert win.doc.shape(sid).image == before


def test_vectorize_selected_image(win, tmp_path, monkeypatch):
    from openlaser.ui.dialogs import image_dialogs
    sid = win._insert_image(_png(tmp_path))
    frame = win.doc.shape(sid).bounds()
    monkeypatch.setattr(image_dialogs.VectorizeDialog, "exec", lambda self: 1)
    n = len(win.doc.shapes)
    win._vectorize()
    assert len(win.doc.shapes) == n + 1
    new = win.doc.shape(win.canvas.view.selected_shape_ids()[0])
    assert new.image is None and new.subpaths
    b = new.bounds()
    assert frame[0] - 0.5 <= b[0] and b[2] <= frame[2] + 0.5


def test_round_corners_and_tabs(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from openlaser.ui.dialogs import edit_dialogs
    sid = _rect(win, 10, 10, 40, 20)
    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (3.0, True)))
    win.canvas.view.set_selection([sid])
    win._round_corners()
    assert len(win.doc.shape(sid).subpaths[0].nodes) == 8
    monkeypatch.setattr(edit_dialogs.TabsDialog, "exec", lambda self: 1)
    win._tabs()
    assert win.doc.layer(win.doc.shape(sid).layer_id).tab_count == 4


def test_simulation_panel_plays_and_clears(app, win):
    win.registry["laser.simulate"].trigger()
    panel = win.sim_panel
    assert panel.tl is not None and panel.tl.count > 100 and win.dock_sim.isVisible()
    assert win.canvas.view.sim_item is not None and panel.issues.count() >= 1
    panel.seek(0.0)
    panel.toggle_play()
    pump(app, 0.3)
    assert panel.sim_time > 0
    panel.stop()
    panel.step(5)
    t = panel.sim_time
    panel.step(-2)
    assert panel.sim_time < t
    for i in range(panel.color_box.count()):
        panel.color_box.setCurrentIndex(i)
    panel.layer_box.setCurrentIndex(1)
    _rect(win, 5, 5, 5, 5)  # editar el documento invalida la simulación
    assert win.canvas.view.sim_item is None and panel.tl is None


def test_background_analysis_updates_panel(app, win):
    win.job.analysis_label.setText("—")
    win._refresh_analysis()
    assert pump(app, 10, lambda: "líneas" in win.job.analysis_label.text())


def test_layer_table_uses_short_mode_labels(win):
    combo = win.layers.table.cellWidget(0, 2)
    assert combo is not None and len(combo.currentText()) <= 7
    assert combo.itemData(0, 3)  # tooltip con el nombre completo


# ================================================================ Fase 4
def _accept_with(monkeypatch, cls, fn=None):
    def fake_exec(self):
        if fn is not None:
            fn(self)
        return 1
    monkeypatch.setattr(cls, "exec", fake_exec)


def test_material_library_apply_and_save(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from openlaser.ui.panels import materials_panel as mp
    panel = win.materials_panel
    assert panel.table.rowCount() >= 3
    panel.table.selectRow(0)
    entry = panel.current()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    panel.btn_apply.click()
    layer = win.doc.layer(win._current_layer_id())
    assert layer.speed_mm_min == entry.speed_mm_min and entry.material in layer.notes
    win.registry["edit.undo"].trigger()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Mi material", True)))
    _accept_with(monkeypatch, mp.MaterialDialog)
    n = len(win.materials.entries)
    win._material_save_layer()
    assert len(win.materials.entries) == n + 1 and win.materials.search("mi material")


def test_test_wizard_adds_editable_layers(win, monkeypatch):
    from openlaser.ui.dialogs import test_wizard
    _accept_with(monkeypatch, test_wizard.TestWizardDialog, lambda d: d.confirm.setChecked(True))
    n_layers, n_shapes = len(win.doc.layers), len(win.doc.shapes)
    win.registry["laser.material_test"].trigger()
    assert len(win.doc.layers) > n_layers + 5 and len(win.doc.shapes) > n_shapes
    win.registry["edit.undo"].trigger()
    assert len(win.doc.layers) == n_layers and len(win.doc.shapes) == n_shapes


def test_test_wizard_blocks_outside_area(app, win):
    from PySide6.QtWidgets import QDialogButtonBox

    from openlaser.ui.dialogs.test_wizard import TestWizardDialog
    dlg = TestWizardDialog("scale", win.profile, win.doc, {"x": 380})
    dlg.confirm.setChecked(True)
    assert dlg.plan is None and not dlg.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dlg.widgets["x"].setValue(10)
    dlg._rebuild()
    assert dlg.plan is not None and dlg.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_simulated_camera_calibration_and_overlay(app, win, monkeypatch):
    import numpy as np

    from openlaser.camera.calibration import marker_positions, undistort_points
    from openlaser.ui.dialogs import camera_dialogs
    cam = win.camera
    n_layers = len(win.doc.layers)
    cam.btn_markers.click()
    assert len(win.doc.layers) == n_layers + 1
    assert cam.capture() is not None and cam.last.shape[2] == 3
    cam.profile.k1 = cam.source.simulated.lens.k1

    def click(dlg):
        p = win.profile
        markers = marker_positions(p.width_mm, p.height_mm, 6)
        proj = cam.source.simulated.project(markers)
        for x, y in undistort_points(np.asarray(proj), cam.profile.lens, 800, 600):
            dlg._clicked(float(x) + 1.5, float(y) - 1.0)
    _accept_with(monkeypatch, camera_dialogs.AreaCalibrationDialog, click)
    assert cam.calibrate_area()
    assert cam.profile.calibrated and cam.profile.precision_mm < 3
    assert win.canvas.view.camera_item is not None and win.canvas.view.camera_item.isVisible()
    cam.opacity.setValue(30)
    assert abs(win.canvas.view.camera_item.opacity() - 0.3) < 1e-6
    cam.overlay.setChecked(False)
    assert not win.canvas.view.camera_item.isVisible()


def test_rotary_dialog_scales_job(win, monkeypatch):
    import math

    from PySide6.QtWidgets import QMessageBox

    from openlaser.ui.dialogs import machine_dialogs

    def setup(d):
        d.enabled.setChecked(True)
        d.kind.setCurrentIndex(d.kind.findData("chuck"))
        d.obj_d.setValue(100 / math.pi)
        d.mm_rev.setValue(200)
        d._accept()
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    _accept_with(monkeypatch, machine_dialogs.RotaryDialog, setup)
    plain = win._prepare_job(quiet=True)
    assert plain is not None
    win.registry["laser.rotary"].trigger()
    assert win.profile.rotary["enabled"] and win.sb_rotary.isVisibleTo(win)
    job = win._prepare_job(quiet=True)
    assert job.display_lines == plain.display_lines and job.lines != plain.lines
    b0, b1 = plain.analysis.bounds_laser, job.analysis.bounds_laser
    assert (b1[3] - b1[1]) == pytest.approx(2 * (b0[3] - b0[1]), rel=1e-3)
    win.profile.rotary = {}
    win._update_rotary_indicator()


def test_print_and_cut_moves_design(win, monkeypatch):
    from openlaser.ui.dialogs import machine_dialogs
    a = _rect(win, 20, 20, 4, 4)
    b = _rect(win, 120, 20, 4, 4)
    before = win.doc.shape(a).bounds()
    win.canvas.view.set_selection([a, b])

    def measure(d):
        for k, (x, y) in enumerate(d.design):
            d.set_measured(k, (x + 3.0, y + 2.0))
    _accept_with(monkeypatch, machine_dialogs.PrintCutDialog, measure)
    win.registry["laser.print_cut"].trigger()
    after = win.doc.shape(a).bounds()
    assert abs(abs(after[0] - before[0]) - 3.0) < 1e-6 and abs(abs(after[1] - before[1]) - 2.0) < 1e-6


def test_center_finder_centers_selection(win, monkeypatch):
    from openlaser.ui.dialogs import machine_dialogs
    sid = _rect(win, 10, 10, 10, 10)
    win.canvas.view.set_selection([sid])

    def run(d):
        for k, p in enumerate([(160.0, 100.0), (100.0, 160.0), (40.0, 100.0)]):
            d.set_point(k, p)
        d.btn_sel.click()
    _accept_with(monkeypatch, machine_dialogs.CenterFinderDialog, run)
    win.registry["laser.center_finder"].trigger()
    b = win.doc.shape(sid).bounds()
    c = win._cs().doc_to_work(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), (0.0, 0.0))
    assert c == pytest.approx((100.0, 100.0), abs=1e-6)


def test_plugin_action_edits_document(win, tmp_path):
    import pathlib
    import shutil
    root = pathlib.Path(__file__).resolve().parents[1] / "examples" / "plugins"
    shutil.copytree(root / "ejemplo_marco", tmp_path / "ejemplo_marco")
    win.plugins.root = tmp_path
    win.plugins.enabled = {"ejemplo.marco"}
    win._load_plugins()
    win._fill_plugins_menu()
    acts = [a for a in win.menu_plugins.actions() if a.text().startswith("Dibujar marco")]
    assert acts
    n = len(win.doc.shapes)
    acts[0].trigger()
    assert len(win.doc.shapes) == n + 1
    win.registry["edit.undo"].trigger()
    assert len(win.doc.shapes) == n


def test_shape_library_text_on_path_and_history(win, monkeypatch):
    from openlaser.ui.dialogs import shape_dialogs
    _accept_with(monkeypatch, shape_dialogs.ShapeLibraryDialog, lambda d: d.list.setCurrentRow(2))
    n = len(win.doc.shapes)
    win.registry["tools.shape_library"].trigger()
    assert len(win.doc.shapes) == n + 1
    from openlaser.document.model import make_shape
    from openlaser.geometry import path as gp
    guide = make_shape(win.doc.layers[0].id, gp.ellipse(200, 200, 60, 60))
    win._edit("guía", lambda: win.doc.shapes.append(guide))
    win.canvas.view.set_selection([guide.id])
    _accept_with(monkeypatch, shape_dialogs.TextOnPathDialog, lambda d: d.text.setText("OPENLASER"))
    win.registry["draw.text_on_path"].trigger()
    assert len(win.doc.shapes) == n + 3
    hist = win.history_panel
    applied = win.undo.history()[1]
    hist._clicked(hist.list.item(applied - 2))
    assert win.undo.history()[1] == applied - 2 and len(win.doc.shapes) == n + 1


def test_import_pdf(win, tmp_path, monkeypatch):
    import zlib

    from PySide6.QtWidgets import QFileDialog, QMessageBox
    data = zlib.compress(b"1 0 0 RG 72 72 m 144 72 l 144 144 l h S")
    head = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] "
            b"/Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R >>\n"
            b"endobj\n4 0 obj\n")
    stream = b"<< /Length " + str(len(data)).encode() + b" /Filter /FlateDecode >>\nstream\n"
    pdf = head + stream + data + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    path = tmp_path / "a.pdf"
    path.write_bytes(pdf)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([str(path)], "")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    n = len(win.doc.shapes)
    win._import()
    assert len(win.doc.shapes) == n + 1


def test_material_table_compact_with_params_in_detail(win):
    panel = win.materials_panel
    assert panel.table.columnCount() == 4  # nombre con todo el ancho; parámetros en el detalle
    panel.table.selectRow(0)
    e = panel.current()
    assert f"{e.speed_mm_min:g} mm/min" in panel.detail.text()
    assert f"{e.speed_mm_min:g}" in panel.table.item(0, 0).toolTip()


def test_image_dialog_scan_angle(win, tmp_path, monkeypatch):
    from openlaser.ui.dialogs import image_dialogs
    sid = win._insert_image(_png(tmp_path))

    def fake_exec(self):
        self.sangle.setValue(45)
        return 1
    monkeypatch.setattr(image_dialogs.ImageDialog, "exec", fake_exec)
    win._image_settings()
    assert win.doc.shape(sid).image.settings.scan_angle == pytest.approx(45)


def test_send_gcode_to_sd(win, monkeypatch):
    from test_sd_upload import FakeController

    from PySide6.QtWidgets import QMessageBox
    _rect(win, 20, 20, 30, 20)
    ctrl = FakeController()
    try:
        def fill(d):
            assert not d.btn_send.isEnabled()  # exige confirmar que se ejecuta sin supervisión
            d.host.setText("127.0.0.1")
            d.port.setValue(ctrl.port)
            d.folder.setText("/trabajos")
            d.name.setText("prueba sd")
            d._do_test()
            assert d.status.text().startswith("✓")
            d.ack.setChecked(True)
            assert d.btn_send.isEnabled()
        monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: pytest.fail(str(a))))
        from openlaser.ui.dialogs import machine_dialogs
        _accept_with(monkeypatch, machine_dialogs.SdUploadDialog)
        assert win._send_gcode_sd(dialog_hook=fill)
        data = ctrl.files["/trabajos/prueba_sd.gcode"].decode()
        tail = data.rstrip().splitlines()[-3:]
        assert "M5" in tail and "G1" in data  # el trabajo termina con el láser apagado (después puede ir M9)
        assert "?" not in data and "; Maquina:" in data  # cabecera en ASCII sin acentos
        assert win.profile.sd_folder == "/trabajos" and win.profile.sd_http_port == ctrl.port
    finally:
        ctrl.close()


@pytest.mark.parametrize("fw,name", [("grblhal", "grblHAL"), ("fluidnc", "FluidNC")])
def test_connect_other_firmwares(app, win, fw, name):
    win.profile.firmware = fw
    win._connect()
    assert pump(app, 6, lambda: win.status.conn == ConnState.CONNECTED)
    assert pump(app, 4, lambda: win.status.dialect == fw)
    assert win._simulator.firmware == fw
    assert win.sb_conn.text().startswith(name)
    win._grbl_settings()
    dlg = win._grbl_dialog
    assert dlg is not None
    assert dlg.btn_write.isEnabled() == (fw != "fluidnc")  # FluidNC: parámetros de sólo lectura
    dlg.close()
    win.controller.home()
    assert pump(app, 6, lambda: win.status.homed and win.status.grbl == GrblState.IDLE)
    assert win._simulator.emissions == []
    win._disconnect()
    assert pump(app, 5, lambda: win.status.conn == ConnState.DISCONNECTED)


def test_device_manager_firmware_choice(app, win):
    from openlaser.ui.dialogs.config_dialogs import DeviceManagerDialog
    dlg = DeviceManagerDialog(win.settings, False, win)
    ids = [dlg.f_fw.itemData(i) for i in range(dlg.f_fw.count())]
    assert ids[:3] == ["grbl", "grblhal", "fluidnc"] or set(ids) >= {"grbl", "grblhal", "fluidnc"}
    dlg.f_fw.setCurrentIndex(dlg.f_fw.findData("grblhal"))
    dlg.f_rxauto.setChecked(False)
    dlg._store(dlg._current)
    p = dlg.profiles[dlg._current]
    assert p.firmware == "grblhal" and p.rx_buffer_auto is False
    dlg.close()


@pytest.mark.parametrize("fw,name", [("marlin", "Marlin"), ("smoothie", "Smoothieware")])
def test_connect_host_firmwares(app, win, fw, name):
    from openlaser.machine.host_controller import HostController
    win.profile.firmware = fw
    win._connect()
    assert isinstance(win.controller, HostController)  # el controlador se adapta al firmware del perfil
    assert pump(app, 10, lambda: win.status.conn == ConnState.CONNECTED)
    assert win._simulator.firmware == fw
    assert pump(app, 3, lambda: win.sb_conn.text().startswith(name))
    win.controller.home()
    assert pump(app, 8, lambda: win.status.homed and win.status.grbl == GrblState.IDLE)
    right = next(b for b in win.motion.jog_buttons if b.accessibleName() == "Mover X+")
    win.motion.step.setValue(10.0)
    assert pump(app, 3, lambda: right.isEnabled())
    right.click()
    assert pump(app, 6, lambda: abs(win.status.mpos[0] - 10.0) < 0.05)
    assert win._simulator.emissions == []
    win._disconnect()
    assert pump(app, 5, lambda: win.status.conn == ConnState.DISCONNECTED)
    win.profile.firmware = "grbl"
    win._connect()
    assert not isinstance(win.controller, HostController)
    assert pump(app, 6, lambda: win.status.conn == ConnState.CONNECTED)
    win._disconnect()
    assert pump(app, 5, lambda: win.status.conn == ConnState.DISCONNECTED)


def test_laser_panel_fits_narrow_dock_without_horizontal_scroll(app, win):
    from PySide6.QtCore import Qt as _Qt
    panel = win.job
    from PySide6.QtWidgets import QWidget
    assert panel.horizontalScrollBarPolicy() == _Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    panel.resize(270, 500)
    pump(app, 0.2)
    widest = sorted(((w.minimumSizeHint().width(), type(w).__name__, getattr(w, "text", lambda: "")())
                     for w in panel.widget().findChildren(QWidget)), reverse=True)[:5]
    # el contenido se ajusta al ancho visible (el mensaje lista los controles más anchos si falla)
    assert panel.widget().width() <= panel.viewport().width() + 1, widest


def test_prepared_job_is_reused_until_document_changes(app, win):
    first = win._prepare_job(quiet=True)
    assert first is not None
    assert win._prepare_job(quiet=True) is first  # Vista previa / Frame / Iniciar no regeneran
    _rect(win, 30, 30, 10, 10)
    second = win._prepare_job(quiet=True)
    assert second is not None and second is not first
    win._refresh_analysis()
    assert pump(app, 10, lambda: win._analysis_future is None)
    assert win._prepare_job(quiet=True) is second  # el análisis en segundo plano usa la misma caché


def test_simulation_draws_images_as_burn_map(app, win, tmp_path):
    sid = win._insert_image(_png(tmp_path))
    assert sid
    win._simulate()
    item = win.sim_panel.item
    assert item is not None and item._overlays  # la imagen se dibuja como imagen quemada, no como líneas
    tl = win.sim_panel.tl
    drawn = sum(len(lines) for _pen, lines, _sel in item._groups)
    assert drawn <= int((~tl.raster).sum())   # ninguna fila de la imagen va como línea
    win.sim_panel.seek(tl.total_time / 2)
    pump(app, 0.05)
    win.sim_panel.step(1)                       # el texto de la línea se crea sólo al pedirlo
    assert win.sim_panel.current.text()


def test_banner_connect_unlock_home_buttons(app, win):
    bn = win.banner
    for b in (bn.btn_connect, bn.btn_unlock, bn.btn_home):
        assert not b.icon().isNull() and b.toolTip()
    assert bn.btn_connect.isVisible() and bn.btn_connect.isEnabled() and not bn.btn_disconnect.isVisible()
    assert not bn.btn_home.isEnabled()  # sin conexión no hay Home
    bn.btn_connect.click()
    assert pump(app, 6, lambda: win.status.conn == ConnState.CONNECTED)
    assert pump(app, 2, lambda: bn.btn_disconnect.isVisible() and not bn.btn_connect.isVisible())
    assert pump(app, 3, lambda: bn.btn_home.isEnabled())
    bn.btn_home.click()
    assert pump(app, 6, lambda: win.status.homed and win.status.grbl == GrblState.IDLE)
    assert win._simulator.emissions == []
    bn.btn_disconnect.click()
    assert pump(app, 5, lambda: win.status.conn == ConnState.DISCONNECTED)
    assert pump(app, 2, lambda: bn.btn_connect.isVisible())


def test_color_icon_set_renders_in_every_theme(app):
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    from openlaser.ui import icons, theme
    old = theme.current().name
    try:
        for name in ("dark", "light"):
            theme.apply_theme(app, name)  # type: ignore[arg-type]
            icons.set_color_mode(True)
            assert icons.color_mode()
            for key in icons.ICONS:
                assert QSvgRenderer(QByteArray(icons.color_svg(key))).isValid(), (name, key)
                assert not icons.icon(key).isNull()
            # cada familia tiene su color y los iconos clave tienen diseño propio
            assert icons.color_svg("start") != icons.color_svg("stop")
            assert icons.PALETTES[name]["green"].encode() in icons.color_svg("start")
            assert icons.PALETTES[name]["red"].encode() in icons.color_svg("estop")
        theme.apply_theme(app, "contrast")  # type: ignore[arg-type]
        assert not icons.color_mode()  # alto contraste: siempre monocromo
        icons.set_color_mode(False)
        theme.apply_theme(app, "dark")  # type: ignore[arg-type]
        assert not icons.color_mode()
    finally:
        icons.set_color_mode(True)
        icons.clear_cache()
        theme.apply_theme(app, old)  # type: ignore[arg-type]


def test_motion_and_banner_icons_colored(app, win):
    from openlaser.ui import icons
    assert icons.color_mode()
    for b in win.motion.jog_buttons + [win.motion.btn_zp, win.motion.btn_ap]:
        assert not b.icon().isNull() and b.accessibleName()
    assert all(not b.text() for b in win.motion.jog_buttons)  # flechas como iconos
    for name in ("jog_n", "jog_se", "jog_zp", "jog_am"):
        assert icons.FAMILY_OF[name] in ("cyan", "violet", "pink")
    # deshabilitado: el icono a color se ve translúcido, no gris
    from PySide6.QtCore import QSize as _QSize
    from PySide6.QtGui import QIcon as _QIcon
    disabled = icons.icon("home").pixmap(_QSize(24, 24), _QIcon.Mode.Disabled).toImage()
    normal = icons.icon("home").pixmap(_QSize(24, 24), _QIcon.Mode.Normal).toImage()
    px = [(x, y) for x in range(24) for y in range(24) if normal.pixelColor(x, y).alpha() > 200]
    assert px
    x, y = px[0]
    c_n, c_d = normal.pixelColor(x, y), disabled.pixelColor(x, y)
    assert c_d.alpha() < c_n.alpha() and abs(c_d.hue() - c_n.hue()) <= 6
    # botones con fondo rojo: icono blanco (no se confunde con el fondo)
    assert not win.banner.btn_stop.icon().isNull() and not win.banner.btn_estop.icon().isNull()


def test_about_dialog_shows_logo_and_app_icon(app, win):
    from PySide6.QtWidgets import QLabel

    from openlaser.ui import icons
    from openlaser.ui.dialogs.info_dialogs import about_dialog

    assert not icons.app_icon().isNull()
    assert not win.windowIcon().isNull()
    dlg = about_dialog(win)
    logo = dlg.findChild(QLabel, "aboutLogo")
    assert logo is not None and logo.pixmap() is not None and not logo.pixmap().isNull()
    dlg.show()
    pump(app, 0.2)
    dlg.grab().save(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "docs" / "captura_acerca_de.png"))
    dlg.close()
