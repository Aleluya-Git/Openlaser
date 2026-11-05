"""Diálogos de máquina de la Fase 4: rotary, Print and Cut, Center Finder y plugins."""
from __future__ import annotations

import math
from typing import Any, Callable

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from openlaser.devices.profile import DeviceProfile
from openlaser.devices.rotary import CylinderCorrection, RotaryConfig
from openlaser.geometry.registration import circle_from_three
from openlaser.i18n import tr
from openlaser.plugins import controllers
from openlaser.plugins.manager import API_VERSION, PluginManager

Point = tuple[float, float]


def _d(lo: float, hi: float, v: float, dec: int = 2, suffix: str = "") -> QDoubleSpinBox:
    w = QDoubleSpinBox()
    w.setRange(lo, hi)
    w.setDecimals(dec)
    w.setValue(v)
    if suffix:
        w.setSuffix(suffix)
    return w


# ===================================================================== rotary
class RotaryDialog(QDialog):
    """Configuración rotativa y corrección cilíndrica del perfil activo."""

    def __init__(self, profile: DeviceProfile, test_rotation: Callable[[float, str], None] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Configuración rotativa"))
        self.profile = profile
        self.test_rotation = test_rotation
        rot = RotaryConfig.from_dict(profile.rotary)
        cyl = CylinderCorrection.from_dict(profile.cylinder)
        self._was_enabled = rot.enabled
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        page = QWidget()
        form = QFormLayout(page)
        self.enabled = QCheckBox(tr("Rotary activado"))
        self.enabled.setChecked(rot.enabled)
        form.addRow(self.enabled)
        self.kind = QComboBox()
        self.kind.addItem(tr("Rodillos"), "roller")
        self.kind.addItem(tr("Mandril (plato)"), "chuck")
        self.kind.setCurrentIndex(max(0, self.kind.findData(rot.kind)))
        self.axis = QComboBox()
        self.axis.addItem(tr("Sustituye al eje Y"), "Y")
        self.axis.addItem(tr("Eje A independiente"), "A")
        self.axis.setCurrentIndex(max(0, self.axis.findData(rot.axis)))
        self.obj_d = _d(1, 1000, rot.object_diameter_mm, 2, " mm")
        self.roller_d = _d(1, 500, rot.roller_diameter_mm, 2, " mm")
        self.mm_rev = _d(0.001, 100000, rot.mm_per_rev, 3, tr(" unidades/vuelta"))
        self.steps_rev = _d(0, 1e6, rot.steps_per_rev, 0)
        self.steps_rev.setSpecialValueText(tr("no se usa"))
        self.steps_mm = _d(0, 1e5, rot.steps_per_mm, 3)
        self.steps_mm.setSpecialValueText(tr("no se usa"))
        self.gear = _d(0.01, 1000, rot.gear_ratio, 3)
        self.invert = QCheckBox(tr("Invertir dirección"))
        self.invert.setChecked(rot.invert)
        for label, w in ((tr("Tipo"), self.kind), (tr("Eje"), self.axis), (tr("Diámetro del objeto"), self.obj_d),
                         (tr("Diámetro del rodillo"), self.roller_d), (tr("Distancia por vuelta del motor"), self.mm_rev),
                         (tr("Pasos por vuelta"), self.steps_rev), (tr("Pasos por mm del eje ($101/$103)"), self.steps_mm),
                         (tr("Relación de transmisión"), self.gear)):
            form.addRow(label, w)
            if isinstance(w, (QDoubleSpinBox, QComboBox)):
                (w.valueChanged if isinstance(w, QDoubleSpinBox) else w.currentIndexChanged).connect(
                    lambda *_a: self._update())
        form.addRow(self.invert)
        self.invert.toggled.connect(lambda _v: self._update())
        self.factor = QLabel()
        self.factor.setWordWrap(True)
        form.addRow(self.factor)
        trow = QHBoxLayout()
        self.btn_test = QPushButton(tr("Prueba de rotación (una vuelta, láser apagado)"))
        self.btn_test.clicked.connect(self._test)
        self.btn_test.setEnabled(test_rotation is not None)
        trow.addWidget(self.btn_test)
        form.addRow(trow)
        note = QLabel(tr("Con el rotary activo el diseño se hace en milímetros de superficie: la altura de una vuelta "
                         "es la circunferencia del objeto. Use Herramientas → Pruebas → Prueba de rotary para grabar "
                         "un cilindro de prueba."))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        form.addRow(note)
        tabs.addTab(page, tr("Rotary"))

        cpage = QWidget()
        cform = QFormLayout(cpage)
        self.cyl_enabled = QCheckBox(tr("Corrección cilíndrica activada"))
        self.cyl_enabled.setChecked(cyl.enabled)
        self.cyl_d = _d(1, 1000, cyl.diameter_mm, 2, " mm")
        self.cyl_y = _d(-10000, 10000, cyl.axis_y_mm, 2, " mm")
        cform.addRow(self.cyl_enabled)
        cform.addRow(tr("Diámetro del cilindro"), self.cyl_d)
        cform.addRow(tr("Posición Y del eje (trabajo)"), self.cyl_y)
        cnote = QLabel(tr("Para grabar un cilindro tumbado sin rotary: el diseño se interpreta como longitud de arco "
                          "y se proyecta sobre el plano. Sólo es útil hasta unos ±75° del eje; ajuste el enfoque al "
                          "punto más alto."))
        cnote.setWordWrap(True)
        cnote.setProperty("role", "muted")
        cform.addRow(cnote)
        tabs.addTab(cpage, tr("Corrección cilíndrica"))

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)
        self._update()

    def rotary(self) -> RotaryConfig:
        return RotaryConfig(self.enabled.isChecked(), str(self.kind.currentData()), str(self.axis.currentData()),
                            self.obj_d.value(), self.roller_d.value(), self.mm_rev.value(), self.steps_rev.value(),
                            self.steps_mm.value(), self.gear.value(), self.invert.isChecked())

    def cylinder(self) -> CylinderCorrection:
        return CylinderCorrection(self.cyl_enabled.isChecked(), self.cyl_d.value(), self.cyl_y.value())

    def _update(self) -> None:
        r = self.rotary()
        self.roller_d.setEnabled(r.kind == "roller")
        try:
            f = r.factor()
            self.factor.setText(tr("{f:.4f} unidades de máquina por mm de superficie · una vuelta del objeto = "
                                   "{c:.2f} mm de diseño", f=f, c=r.object_circumference()))
        except ValueError as exc:
            self.factor.setText("⛔ " + tr(str(exc)))

    def _test(self) -> None:
        r = self.rotary()
        try:
            units = r.factor() * r.object_circumference()
        except ValueError as exc:
            QMessageBox.warning(self, tr("Prueba de rotación"), tr(str(exc)))
            return
        if QMessageBox.question(self, tr("Prueba de rotación"),
                                tr("El eje {a} se moverá {u:.2f} unidades (una vuelta del objeto) con el láser "
                                   "apagado. ¿Continuar?", a=r.axis, u=units)) == QMessageBox.StandardButton.Yes \
                and self.test_rotation is not None:
            self.test_rotation(units, r.axis)

    def _accept(self) -> None:
        r, c = self.rotary(), self.cylinder()
        if r.enabled and c.enabled:
            QMessageBox.warning(self, tr("Configuración rotativa"),
                                tr("El rotary y la corrección cilíndrica no pueden estar activos a la vez"))
            return
        if r.enabled and r.validate():
            QMessageBox.warning(self, tr("Configuración rotativa"), tr("Revise la configuración del rotary"))
            return
        if r.enabled and r.axis == "A" and not self.profile.a_axis:
            QMessageBox.warning(self, tr("Configuración rotativa"),
                                tr("El perfil no tiene eje A: active el eje A en el perfil o use «Sustituye al eje Y»"))
            return
        if r.enabled != self._was_enabled and r.axis == "Y":
            text = (tr("Al activar el rotary, el eje Y deja de mover la máquina en línea recta y pasa a girar el "
                       "objeto. Retire la base de trabajo si hace falta, compruebe que el homing de Y no choca y "
                       "recuerde que los límites de Y ya no corresponden a la mesa. ¿Activar?")
                    if r.enabled else
                    tr("Al desactivar el rotary, el eje Y vuelve a mover la máquina en línea recta. Desmonte el "
                       "rotary y vuelva a conectar el motor de Y antes de mover la máquina. ¿Desactivar?"))
            if QMessageBox.warning(self, tr("Cambio entre eje Y y rotary"), text,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self.profile.rotary = r.to_dict()
        self.profile.rotary_enabled = r.enabled
        self.profile.cylinder = c.to_dict()
        self.accept()


# ============================================================ Print and Cut
class PrintCutDialog(QDialog):
    """Alinea el diseño con un impreso usando 2 (giro + escala) o 3 marcas de registro (afín)."""

    def __init__(self, design_marks: list[Point], read_laser: Callable[[], Point], go_to: Callable[[Point], None],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Print and Cut"))
        self.design = design_marks          # coordenadas de máquina de las marcas en el diseño
        self.read_laser = read_laser
        self.go_to = go_to
        self.measured: list[Point | None] = [None] * len(design_marks)
        root = QVBoxLayout(self)
        intro = QLabel(tr("1. Mueva el láser (con el puntero o a baja potencia) hasta cada marca impresa. "
                          "2. Pulse «Registrar». Con dos marcas se corrige posición, giro y escala; con tres, también "
                          "la deformación."))
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.table = QTableWidget(len(design_marks), 4)
        self.table.setHorizontalHeaderLabels([tr("Marca"), tr("Diseño (máquina)"), tr("Medida"), ""])
        self.table.verticalHeader().setVisible(False)
        for i, (x, y) in enumerate(design_marks):
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(f"X{x:.2f} Y{y:.2f}"))
            self.table.setItem(i, 2, QTableWidgetItem("—"))
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(0, 0, 0, 0)
            b_go = QPushButton(tr("Ir"))
            b_go.setToolTip(tr("Lleva el láser (apagado) a la posición prevista de la marca"))
            b_go.clicked.connect(lambda _c=False, k=i: self.go_to(self.design[k]))
            b_reg = QPushButton(tr("Registrar"))
            b_reg.clicked.connect(lambda _c=False, k=i: self._register(k))
            lay.addWidget(b_go)
            lay.addWidget(b_reg)
            self.table.setCellWidget(i, 3, cell)
        self.table.resizeColumnsToContents()
        root.addWidget(self.table)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        root.addWidget(self.result_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Aplicar al documento"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.transform: Any = None
        self._update()

    def _register(self, k: int) -> None:
        self.measured[k] = self.read_laser()
        x, y = self.measured[k]  # type: ignore[misc]
        self.table.setItem(k, 2, QTableWidgetItem(f"X{x:.2f} Y{y:.2f}"))
        self._update()

    def set_measured(self, k: int, p: Point) -> None:
        self.measured[k] = p
        self.table.setItem(k, 2, QTableWidgetItem(f"X{p[0]:.2f} Y{p[1]:.2f}"))
        self._update()

    def _update(self) -> None:
        from openlaser.geometry.registration import affine_from_three, similarity_from_pairs
        ok = all(m is not None for m in self.measured)
        self.transform = None
        if ok:
            try:
                if len(self.design) == 2:
                    t, scale, ang = similarity_from_pairs((self.design[0], self.design[1]),
                                                          (self.measured[0], self.measured[1]))  # type: ignore[arg-type]
                    msg = tr("Giro {a:.2f}° · escala {s:.4f}", a=ang, s=scale)
                    if abs(scale - 1) > 0.02:
                        msg += " · ⚠ " + tr("la escala difiere más de un 2 %: compruebe la impresión y las marcas")
                else:
                    t = affine_from_three(tuple(self.design[:3]), tuple(self.measured[:3]))  # type: ignore[arg-type]
                    msg = tr("Transformación afín con tres marcas")
                p = self.design[0]
                moved = t.apply(p)
                msg += " · " + tr("desplazamiento de la marca 1: {d:.2f} mm", d=math.dist(p, moved))
                self.transform = t
                self.result_label.setText(msg)
            except ValueError as exc:
                self.result_label.setText("⛔ " + tr(str(exc)))
        else:
            self.result_label.setText(tr("Registre todas las marcas"))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.transform is not None)


# ============================================================= Center Finder
class CenterFinderDialog(QDialog):
    """Centro de una pieza circular a partir de tres puntos del borde tomados con el láser."""

    def __init__(self, read_laser: Callable[[], Point], go_to: Callable[[Point], None],
                 set_origin: Callable[[], None], center_selection: Callable[[Point], None] | None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Center Finder"))
        self.read_laser, self.go_to, self.set_origin = read_laser, go_to, set_origin
        self.center_selection = center_selection
        self.points: list[Point | None] = [None, None, None]
        self.center: tuple[float, float, float] | None = None
        root = QVBoxLayout(self)
        intro = QLabel(tr("Lleve el láser (apagado o con el puntero) a tres puntos separados del borde de la pieza y "
                          "regístrelos. Las coordenadas son de trabajo."))
        intro.setWordWrap(True)
        root.addWidget(intro)
        box = QGroupBox(tr("Puntos del borde"))
        bl = QFormLayout(box)
        self.labels: list[QLabel] = []
        for i in range(3):
            row = QHBoxLayout()
            lab = QLabel("—")
            b = QPushButton(tr("Registrar punto {n}", n=i + 1))
            b.clicked.connect(lambda _c=False, k=i: self.set_point(k, self.read_laser()))
            row.addWidget(lab, 1)
            row.addWidget(b)
            self.labels.append(lab)
            bl.addRow(f"{i + 1}", row)
        root.addWidget(box)
        self.result_label = QLabel(tr("Registre los tres puntos"))
        root.addWidget(self.result_label)
        row = QHBoxLayout()
        self.btn_go = QPushButton(tr("Ir al centro"))
        self.btn_go.setToolTip(tr("Movimiento G0 con el láser apagado"))
        self.btn_origin = QPushButton(tr("Fijar origen aquí"))
        self.btn_origin.setToolTip(tr("Fija el origen de trabajo en la posición actual (lleve antes el láser al centro)"))
        self.btn_sel = QPushButton(tr("Centrar la selección"))
        self.btn_sel.setEnabled(center_selection is not None)
        for b in (self.btn_go, self.btn_origin, self.btn_sel):
            row.addWidget(b)
        root.addLayout(row)
        self.btn_go.clicked.connect(lambda: self.center and self.go_to(self.center[:2]))
        self.btn_origin.clicked.connect(lambda: self.set_origin())
        self.btn_sel.clicked.connect(lambda: self.center and self.center_selection and
                                     self.center_selection(self.center[:2]))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)
        self._update()

    def set_point(self, k: int, p: Point) -> None:
        self.points[k] = p
        self.labels[k].setText(f"X{p[0]:.3f} Y{p[1]:.3f}")
        self._update()

    def _update(self) -> None:
        self.center = None
        if all(p is not None for p in self.points):
            try:
                self.center = circle_from_three(*self.points)  # type: ignore[arg-type]
                cx, cy, r = self.center
                self.result_label.setText(tr("Centro X{x:.3f} Y{y:.3f} · diámetro {d:.2f} mm", x=cx, y=cy, d=2 * r))
            except ValueError as exc:
                self.result_label.setText("⛔ " + tr(str(exc)))
        for b in (self.btn_go, self.btn_sel):
            b.setEnabled(self.center is not None and (b is not self.btn_sel or self.center_selection is not None))


# ================================================================== plugins
class PluginsDialog(QDialog):
    def __init__(self, manager: PluginManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Gestión de plugins"))
        self.resize(820, 520)
        self.manager = manager
        root = QVBoxLayout(self)
        warn = QLabel("⚠ " + tr("Los plugins ejecutan código con los mismos permisos que la aplicación. Active sólo "
                                "plugins de fuentes de confianza. Los plugins no pueden mover ni encender el láser "
                                "directamente: lo que generan pasa por la vista previa y la comprobación previa."))
        warn.setWordWrap(True)
        root.addWidget(warn)
        root.addWidget(QLabel(tr("Plugins en {p} (API {v})", p=str(manager.root), v=API_VERSION)))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels([tr("Activo"), tr("Plugin"), tr("Versión"), tr("Estado"),
                                              tr("Descripción")])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)
        row = QHBoxLayout()
        b_folder = QPushButton(tr("Abrir carpeta de plugins"))
        b_folder.clicked.connect(self._open_folder)
        b_rescan = QPushButton(tr("Buscar de nuevo"))
        b_rescan.clicked.connect(self._rescan)
        row.addWidget(b_folder)
        row.addWidget(b_rescan)
        row.addStretch(1)
        root.addLayout(row)
        rows = "".join(
            f"<tr><td>{a.name}</td><td>{tr('disponible') if a.status == 'available' else tr('previsto')}</td>"
            f"<td>{tr(a.notes) if a.notes else ', '.join(sorted(a.capabilities))}</td></tr>"
            for a in controllers.adapters())
        adapters = QLabel(f"<b>{tr('Adaptadores de controlador')}</b><table cellpadding='3'>{rows}</table>"
                          f"<i>{tr('Los protocolos propietarios sólo se integrarán mediante plugins autorizados.')}</i>")
        adapters.setWordWrap(True)
        root.addWidget(adapters)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)
        self.checks: dict[str, QCheckBox] = {}
        self._fill()

    def _fill(self) -> None:
        infos = list(self.manager.plugins.values())
        self.table.setRowCount(len(infos))
        self.checks.clear()
        for r, info in enumerate(infos):
            cb = QCheckBox()
            cb.setChecked(info.id in self.manager.enabled)
            cb.setEnabled(not info.error or info.id in self.manager.enabled)
            self.checks[info.id] = cb
            self.table.setCellWidget(r, 0, cb)
            self.table.setItem(r, 1, QTableWidgetItem(f"{info.name} ({info.id})"))
            self.table.setItem(r, 2, QTableWidgetItem(info.version))
            state = ("⛔ " + info.error) if info.error else (tr("cargado") if info.loaded else tr("inactivo"))
            self.table.setItem(r, 3, QTableWidgetItem(state))
            desc = QTableWidgetItem(info.description)
            desc.setToolTip(f"{info.description}\n{info.author} · {info.license}")
            self.table.setItem(r, 4, desc)
        if not infos:
            self.table.setRowCount(1)
            self.table.setItem(0, 1, QTableWidgetItem(tr("No hay plugins instalados")))
        self.table.resizeColumnsToContents()

    def _open_folder(self) -> None:
        self.manager.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.manager.root)))

    def _rescan(self) -> None:
        self.manager.discover()
        self._fill()

    def enabled_ids(self) -> set[str]:
        return {pid for pid, cb in self.checks.items() if cb.isChecked()}



# ============================================================ envío a la SD
class SdUploadDialog(QDialog):
    """Datos del envío a la tarjeta SD (FluidNC/ESP3D) con avisos y confirmación explícita."""

    def __init__(self, profile: DeviceProfile, job_name: str, size: int, summary: list[str],
                 warnings: list[str], test_connection: Callable[[Any], str] | None = None,
                 parent: QWidget | None = None) -> None:
        from PySide6.QtWidgets import QLineEdit, QSpinBox

        from openlaser.devices.sd_upload import ENDPOINTS, safe_filename
        super().__init__(parent)
        self.setWindowTitle(tr("Enviar G-code a la tarjeta SD"))
        self._test = test_connection
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.host = QLineEdit(profile.host)
        self.host.setAccessibleName(tr("Dirección del controlador"))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(profile.sd_http_port or 80))
        self.endpoint = QComboBox()
        self.endpoint.setEditable(True)
        for ep, fw in ENDPOINTS.items():
            self.endpoint.addItem(f"{ep}", ep)
            self.endpoint.setItemData(self.endpoint.count() - 1, fw, Qt.ItemDataRole.ToolTipRole)
        self.endpoint.setCurrentText(profile.sd_endpoint or "/upload")
        self.endpoint.setToolTip(tr("FluidNC: /upload · ESP3D 3.x: /sdfiles"))
        self.folder = QLineEdit(profile.sd_folder or "/")
        self.name = QLineEdit(safe_filename(job_name))
        form.addRow(tr("Dirección (IP o nombre)"), self.host)
        form.addRow(tr("Puerto HTTP"), self.port)
        form.addRow(tr("Ruta de subida"), self.endpoint)
        form.addRow(tr("Carpeta en la SD"), self.folder)
        form.addRow(tr("Nombre del archivo"), self.name)
        root.addLayout(form)
        info = QLabel("<br>".join([tr("Tamaño: {k:.1f} KB", k=size / 1024.0), *summary]))
        info.setWordWrap(True)
        root.addWidget(info)
        if warnings:
            wl = QLabel("<br>".join("⚠ " + x for x in warnings))
            wl.setWordWrap(True)
            wl.setStyleSheet("color:#d4a000")
            root.addWidget(wl)
        note = QLabel("⚠ " + tr("El archivo sólo se guarda en la tarjeta; OpenLaser no lo pone en marcha. Si lo "
                                "ejecuta desde la pantalla o la WebUI del controlador, OpenLaser no puede pausarlo ni "
                                "detenerlo: tenga a mano la parada de emergencia física, haga un frame antes y no deje "
                                "la máquina sin supervisión. Un archivo con el mismo nombre se sustituye."))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        root.addWidget(note)
        self.ack = QCheckBox(tr("Entiendo que el trabajo se ejecutará fuera de la supervisión de OpenLaser"))
        root.addWidget(self.ack)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_test = QPushButton(tr("Probar conexión"))
        self.btn_test.setToolTip(tr("Pide el listado de la carpeta sin enviar nada"))
        self.btn_test.clicked.connect(self._do_test)
        self.btn_test.setEnabled(test_connection is not None)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        root.addLayout(row)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.btn_send = self.buttons.addButton(tr("Enviar"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.ack.toggled.connect(lambda _v: self._update())
        self.host.textChanged.connect(lambda _t: self._update())
        self.name.textChanged.connect(lambda _t: self._update())
        self._update()

    def _update(self) -> None:
        self.btn_send.setEnabled(self.ack.isChecked() and bool(self.host.text().strip())
                                 and bool(self.name.text().strip()))

    def target(self) -> Any:
        from openlaser.devices.sd_upload import UploadTarget
        return UploadTarget(self.host.text().strip(), self.port.value(), self.endpoint.currentText().strip() or "/upload",
                            self.folder.text().strip() or "/")

    def filename(self) -> str:
        from openlaser.devices.sd_upload import safe_filename
        return safe_filename(self.name.text())

    def store(self, profile: DeviceProfile) -> None:
        t = self.target()
        profile.host, profile.sd_http_port, profile.sd_endpoint, profile.sd_folder = t.host, t.port, t.endpoint, t.folder

    def _do_test(self) -> None:
        if self._test is not None:
            self.status.setText(self._test(self.target()))
