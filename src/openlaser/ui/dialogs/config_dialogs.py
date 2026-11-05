"""Diálogos de configuración: dispositivos, parámetros GRBL, preferencias y macros."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from openlaser.devices.profile import COMMON_BAUD_RATES, DeviceProfile, OriginCorner, TransportKind
from openlaser.devices.transport import list_serial_ports
from openlaser import i18n
from openlaser.i18n import N_, tr
from openlaser.machine import grbl_settings
from openlaser.persistence.atomic import atomic_write_json, read_json
from openlaser.persistence.settings import AppSettings, Macro
from openlaser.safety.manager import HARD_MAX_FIRE_PERCENT, HARD_MAX_FIRE_SECONDS
from openlaser.ui import icons

ORIGIN_LABELS = {
    OriginCorner.FRONT_LEFT: N_("Delante izquierda"), OriginCorner.REAR_LEFT: N_("Detrás izquierda"),
    OriginCorner.FRONT_RIGHT: N_("Delante derecha"), OriginCorner.REAR_RIGHT: N_("Detrás derecha"),
}
TRANSPORT_LABELS = {
    TransportKind.SIMULATOR: N_("Simulador GRBL"), TransportKind.SERIAL: N_("USB / serie"),
    TransportKind.TCP: N_("TCP / Telnet"), TransportKind.WEBSOCKET: N_("WebSocket"),
}


# ----------------------------------------------------------------- dispositivos
class DeviceManagerDialog(QDialog):
    def __init__(self, settings: AppSettings, connected: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Administrador de dispositivos"))
        self.resize(760, 560)
        self.profiles = [copy.deepcopy(p) for p in settings.profiles]
        self.active_id = settings.active_profile_id
        self.connected = connected
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setAccessibleName(tr("Perfiles"))
        self.list.currentRowChanged.connect(self._load)
        left.addWidget(self.list)
        row = QHBoxLayout()
        for text, fn in ((tr("Nuevo"), self._new), (tr("Duplicar"), self._dup), (tr("Eliminar"), self._delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        self.btn_active = QPushButton(icons.icon("device"), tr("Usar este perfil"))
        self.btn_active.clicked.connect(self._make_active)
        self.btn_active.setEnabled(not connected)
        if connected:
            self.btn_active.setToolTip(tr("Desconecte para cambiar de dispositivo"))
        left.addWidget(self.btn_active)
        root.addLayout(left, 1)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 2)
        conn = QWidget()
        fc = QFormLayout(conn)
        self.f_name = QLineEdit()
        fc.addRow(tr("Nombre"), self.f_name)
        self.f_fw = QComboBox()
        from openlaser.plugins import controllers
        for a in controllers.adapters():
            if a.status == "available":
                self.f_fw.addItem(a.name, a.id)
                self.f_fw.setItemData(self.f_fw.count() - 1, tr(a.notes) if a.notes else a.name,
                                      Qt.ItemDataRole.ToolTipRole)
        self.f_fw.setToolTip(tr("Se comprueba al conectar: si el controlador es otro, se avisa y se usa el detectado"))
        fc.addRow(tr("Firmware"), self.f_fw)
        self.f_transport = QComboBox()
        for k, v in TRANSPORT_LABELS.items():
            self.f_transport.addItem(tr(v), k.value)
        fc.addRow(tr("Conexión"), self.f_transport)
        port_row = QHBoxLayout()
        self.f_port = QComboBox()
        self.f_port.setEditable(True)
        btn_scan = QPushButton(tr("Detectar"))
        btn_scan.clicked.connect(self._scan_ports)
        port_row.addWidget(self.f_port, 1)
        port_row.addWidget(btn_scan)
        fc.addRow(tr("Puerto serie"), port_row)
        self.f_baud = QComboBox()
        for b in COMMON_BAUD_RATES:
            self.f_baud.addItem(str(b), b)
        fc.addRow(tr("Baudios"), self.f_baud)
        lines_row = QHBoxLayout()
        self.f_dtr = QCheckBox(tr("DTR activa"))
        self.f_rts = QCheckBox(tr("RTS activa"))
        for cb in (self.f_dtr, self.f_rts):
            cb.setToolTip(tr("Señales del puerto serie al conectar. Normalmente desactivadas (como LaserGRBL). "
                             "Si la placa no responde, OpenLaser prueba la combinación contraria y le indica "
                             "cuál ha funcionado."))
            lines_row.addWidget(cb)
        lines_row.addStretch(1)
        fc.addRow(tr("Señales"), lines_row)
        self.f_host = QLineEdit()
        fc.addRow(tr("Host / IP"), self.f_host)
        self.f_tcp = QSpinBox()
        self.f_tcp.setRange(1, 65535)
        fc.addRow(tr("Puerto TCP"), self.f_tcp)
        self.f_ws = QLineEdit()
        fc.addRow(tr("URL WebSocket"), self.f_ws)
        self.f_rxauto = QCheckBox(tr("Usar el búfer de recepción que informa el controlador ($I)"))
        self.f_rxauto.setToolTip(tr("grblHAL suele tener 1024 bytes: enviar más datos a la vez evita pausas en "
                                    "grabados de imagen. En GRBL y FluidNC sólo puede reducir el valor del perfil."))
        fc.addRow("", self.f_rxauto)
        self.f_fwsmax = QDoubleSpinBox()
        self.f_fwsmax.setRange(0.0, 100000.0)
        self.f_fwsmax.setDecimals(3)
        self.f_fwsmax.setSpecialValueText(tr("predeterminado"))
        self.f_fwsmax.setToolTip(tr("Sólo Marlin y Smoothieware: valor de S que da el 100 % de potencia en el firmware. "
                                    "Predeterminado: 255 en Marlin (PWM) y 1 en Smoothieware. Use 100 si Marlin usa "
                                    "CUTTER_POWER_UNIT PERCENT."))
        fc.addRow(tr("S máximo del firmware"), self.f_fwsmax)
        self.tabs.addTab(conn, tr("Conexión"))

        mach = QWidget()
        fm = QFormLayout(mach)
        self.f_w = self._dspin(1, 5000, 1)
        self.f_h = self._dspin(1, 5000, 1)
        fm.addRow(tr("Anchura útil (mm)"), self.f_w)
        fm.addRow(tr("Altura útil (mm)"), self.f_h)
        self.f_origin = QComboBox()
        for k, v in ORIGIN_LABELS.items():
            self.f_origin.addItem(tr(v), k.value)
        fm.addRow(tr("Origen de máquina"), self.f_origin)
        self.f_invx = QCheckBox(tr("Invertir X"))
        self.f_invy = QCheckBox(tr("Invertir Y"))
        inv = QHBoxLayout()
        inv.addWidget(self.f_invx)
        inv.addWidget(self.f_invy)
        fm.addRow(tr("Orientación de ejes"), inv)
        self.f_homing = QCheckBox(tr("Homing habilitado"))
        self.f_limits = QCheckBox(tr("Comprobar límites físicos"))
        fm.addRow("", self.f_homing)
        fm.addRow("", self.f_limits)
        self.f_z = QCheckBox(tr("Eje Z"))
        self.f_a = QCheckBox(tr("Eje A"))
        self.f_rot = QCheckBox(tr("Rotary"))
        axes = QHBoxLayout()
        for w in (self.f_z, self.f_a, self.f_rot):
            axes.addWidget(w)
        fm.addRow(tr("Ejes"), axes)
        self.f_smax = self._dspin(1, 100000, 0)
        fm.addRow(tr("Potencia máxima S ($30)"), self.f_smax)
        self.f_lasermode = QCheckBox(tr("Modo láser ($32=1) esperado"))
        fm.addRow("", self.f_lasermode)
        self.f_units = QComboBox()
        self.f_units.addItem("mm", "mm")
        self.f_units.addItem(tr("pulgadas"), "in")
        fm.addRow(tr("Unidades"), self.f_units)
        self.f_jogfeed = self._dspin(10, 50000, 0)
        self.f_framefeed = self._dspin(10, 50000, 0)
        self.f_maxfeed = self._dspin(10, 100000, 0)
        fm.addRow(tr("Velocidad de jog (mm/min)"), self.f_jogfeed)
        fm.addRow(tr("Velocidad de frame (mm/min)"), self.f_framefeed)
        fm.addRow(tr("Avance máximo (mm/min)"), self.f_maxfeed)
        self.tabs.addTab(mach, tr("Máquina"))

        cmds = QWidget()
        fcmd = QFormLayout(cmds)
        self.f_start = QPlainTextEdit()
        self.f_end = QPlainTextEdit()
        self.f_pause = QPlainTextEdit()
        self.f_resume = QPlainTextEdit()
        for w, label in ((self.f_start, N_("Comandos de inicio")), (self.f_end, N_("Comandos de finalización")),
                         (self.f_pause, N_("Comandos de pausa")), (self.f_resume, N_("Comandos de reanudación"))):
            w.setMaximumHeight(70)
            fcmd.addRow(tr(label), w)
        hint = QLabel(tr("Una línea por comando. Se rechazan comandos '$' y M3/M4 con potencia en inicio/fin."))
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        fcmd.addRow(hint)
        self.tabs.addTab(cmds, tr("Comandos"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        right = QVBoxLayout()
        right.addWidget(buttons)
        root.addLayout(right)
        self._current = -1
        self._refresh_list()

    @staticmethod
    def _dspin(lo: float, hi: float, dec: int) -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setDecimals(dec)
        return w

    def _refresh_list(self, select: int | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.profiles:
            mark = "● " if p.id == self.active_id else "   "
            self.list.addItem(f"{mark}{p.name}  ({p.connection_label()})")
        self.list.blockSignals(False)
        idx = select if select is not None else next(
            (i for i, p in enumerate(self.profiles) if p.id == self.active_id), 0)
        self._current = -1
        self.list.setCurrentRow(idx)
        self._load(idx)

    def _scan_ports(self) -> None:
        current = self.f_port.currentText()
        self.f_port.clear()
        ports = list_serial_ports()
        for p in ports:
            self.f_port.addItem(f"{p.device}", p.device)
            self.f_port.setItemData(self.f_port.count() - 1, p.description, Qt.ItemDataRole.ToolTipRole)
        if not ports:
            self.f_port.addItem(current or "")
        self.f_port.setEditText(current)

    def _load(self, row: int) -> None:
        if self._current >= 0:
            self._store(self._current)
        if not 0 <= row < len(self.profiles):
            return
        self._current = row
        p = self.profiles[row]
        self.f_name.setText(p.name)
        self.f_fw.setCurrentIndex(max(0, self.f_fw.findData(p.firmware)))
        self.f_rxauto.setChecked(p.rx_buffer_auto)
        self.f_fwsmax.setValue(p.fw_s_max)
        self.f_transport.setCurrentIndex(max(0, self.f_transport.findData(p.transport.value)))
        self.f_port.setEditText(p.port)
        idx = self.f_baud.findData(p.baud_rate)
        if idx < 0:
            self.f_baud.addItem(str(p.baud_rate), p.baud_rate)
            idx = self.f_baud.count() - 1
        self.f_baud.setCurrentIndex(idx)
        self.f_dtr.setChecked(p.serial_dtr)
        self.f_rts.setChecked(p.serial_rts)
        self.f_host.setText(p.host)
        self.f_tcp.setValue(p.tcp_port)
        self.f_ws.setText(p.websocket_url)
        self.f_w.setValue(p.width_mm)
        self.f_h.setValue(p.height_mm)
        self.f_origin.setCurrentIndex(max(0, self.f_origin.findData(p.origin.value)))
        self.f_invx.setChecked(p.invert_x)
        self.f_invy.setChecked(p.invert_y)
        self.f_homing.setChecked(p.homing_enabled)
        self.f_limits.setChecked(p.enforce_limits)
        self.f_z.setChecked(p.z_axis)
        self.f_a.setChecked(p.a_axis)
        self.f_rot.setChecked(p.rotary_enabled)
        self.f_smax.setValue(p.s_max)
        self.f_lasermode.setChecked(p.laser_mode)
        self.f_units.setCurrentIndex(max(0, self.f_units.findData(p.units)))
        self.f_jogfeed.setValue(p.jog_feed_mm_min)
        self.f_framefeed.setValue(p.frame_feed_mm_min)
        self.f_maxfeed.setValue(p.max_feed_mm_min)
        self.f_start.setPlainText("\n".join(p.start_commands))
        self.f_end.setPlainText("\n".join(p.end_commands))
        self.f_pause.setPlainText("\n".join(p.pause_commands))
        self.f_resume.setPlainText("\n".join(p.resume_commands))

    def _store(self, row: int) -> None:
        if not 0 <= row < len(self.profiles):
            return
        p = self.profiles[row]
        p.name = self.f_name.text().strip() or p.name
        p.firmware = str(self.f_fw.currentData() or "grbl")
        p.rx_buffer_auto = self.f_rxauto.isChecked()
        p.fw_s_max = self.f_fwsmax.value()
        p.transport = TransportKind(self.f_transport.currentData())
        p.port = self.f_port.currentText().strip()
        p.baud_rate = int(self.f_baud.currentData() or 115200)
        p.serial_dtr = self.f_dtr.isChecked()
        p.serial_rts = self.f_rts.isChecked()
        p.host = self.f_host.text().strip()
        p.tcp_port = self.f_tcp.value()
        p.websocket_url = self.f_ws.text().strip()
        p.width_mm, p.height_mm = self.f_w.value(), self.f_h.value()
        p.origin = OriginCorner(self.f_origin.currentData())
        p.invert_x, p.invert_y = self.f_invx.isChecked(), self.f_invy.isChecked()
        p.homing_enabled, p.enforce_limits = self.f_homing.isChecked(), self.f_limits.isChecked()
        p.z_axis, p.a_axis, p.rotary_enabled = self.f_z.isChecked(), self.f_a.isChecked(), self.f_rot.isChecked()
        p.s_max, p.laser_mode = self.f_smax.value(), self.f_lasermode.isChecked()
        p.units = str(self.f_units.currentData())
        p.jog_feed_mm_min, p.frame_feed_mm_min = self.f_jogfeed.value(), self.f_framefeed.value()
        p.max_feed_mm_min = self.f_maxfeed.value()

        def lines(w: QPlainTextEdit) -> list[str]:
            return [ln.strip() for ln in w.toPlainText().splitlines() if ln.strip()]

        p.start_commands, p.end_commands = lines(self.f_start), lines(self.f_end)
        p.pause_commands, p.resume_commands = lines(self.f_pause), lines(self.f_resume)

    def _validate(self) -> str:
        from openlaser.safety.manager import SafetyManager
        sm = SafetyManager()
        for p in self.profiles:
            for cmd in p.start_commands + p.end_commands:
                if cmd.startswith("$") or sm.classify_command(cmd).fires_laser:
                    return tr("Perfil «{n}»: comando no permitido en inicio/fin: {c}", n=p.name, c=cmd)
        return ""

    def _new(self) -> None:
        self._store(self._current)
        self.profiles.append(DeviceProfile(name=tr("Nuevo dispositivo"), transport=TransportKind.SERIAL))
        self._refresh_list(len(self.profiles) - 1)

    def _dup(self) -> None:
        self._store(self._current)
        if 0 <= self._current < len(self.profiles):
            clone = copy.deepcopy(self.profiles[self._current])
            clone.id = DeviceProfile().id
            clone.name += " " + tr("(copia)")
            clone.demo = False
            self.profiles.append(clone)
            self._refresh_list(len(self.profiles) - 1)

    def _delete(self) -> None:
        if len(self.profiles) <= 1 or not 0 <= self._current < len(self.profiles):
            return
        if self.profiles[self._current].id == self.active_id:
            QMessageBox.information(self, tr("Perfil activo"), tr("No se puede eliminar el perfil activo."))
            return
        del self.profiles[self._current]
        self._current = -1
        self._refresh_list(0)

    def _make_active(self) -> None:
        self._store(self._current)
        if 0 <= self._current < len(self.profiles):
            self.active_id = self.profiles[self._current].id
            self._refresh_list(self._current)

    def _accept(self) -> None:
        self._store(self._current)
        err = self._validate()
        if err:
            QMessageBox.warning(self, tr("Comandos no válidos"), err)
            return
        self.accept()


# ------------------------------------------------------------- parámetros GRBL
class GrblSettingsDialog(QDialog):
    """Lee, explica, valida, compara, respalda y restaura parámetros ``$$``."""

    def __init__(self, current: dict[int, str], writer, reader, parent: QWidget | None = None,
                 writable: bool = True, firmware: str = "GRBL") -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Parámetros GRBL ($$)"))
        self.writable = writable
        self.resize(820, 600)
        self.current = dict(current)
        self.loaded_snapshot = dict(current)
        self.writer = writer
        self.reader = reader
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        for text, fn, ic in ((tr("Leer $$"), self._read, "import"), (tr("Copia de seguridad…"), self._backup, "save"),
                             (tr("Comparar con copia…"), self._compare, "list"),
                             (tr("Restaurar seleccionados…"), self._restore, "undo")):
            b = QPushButton(icons.icon(ic), text)
            b.clicked.connect(fn)
            top.addWidget(b)
        top.addStretch(1)
        root.addLayout(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["$", tr("Parámetro"), tr("Valor"), tr("Unidad"), tr("Copia")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._explain(r))
        root.addWidget(self.table, 1)
        self.help = QLabel("")
        self.help.setWordWrap(True)
        self.help.setMinimumHeight(48)
        root.addWidget(self.help)
        edit = QHBoxLayout()
        self.value = QLineEdit()
        self.value.setPlaceholderText(tr("Nuevo valor"))
        self.btn_write = QPushButton(icons.icon("save"), tr("Escribir valor"))
        self.btn_write.clicked.connect(self._write)
        edit.addWidget(QLabel(tr("Valor:")))
        edit.addWidget(self.value, 1)
        edit.addWidget(self.btn_write)
        root.addLayout(edit)
        if not writable:
            self.value.setEnabled(False)
            self.btn_write.setEnabled(False)
            ro = QLabel("ℹ " + tr("{f} guarda su configuración en un archivo YAML: estos valores son de sólo lectura. "
                                  "Cámbielos desde la WebUI del controlador o con sus comandos $/…; «$CD» en la "
                                  "consola muestra la configuración completa.", f=firmware))
            ro.setWordWrap(True)
            ro.setProperty("role", "muted")
            root.addWidget(ro)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        root.addWidget(close)
        self.backup: dict[int, str] = {}
        self.populate(current)

    def populate(self, values: dict[int, str]) -> None:
        self.current = dict(values)
        keys = sorted(set(values) | set(self.backup))
        self.table.setRowCount(len(keys))
        for row, n in enumerate(keys):
            info = grbl_settings.describe(n)
            cells = [f"${n}", info.label if info else "", values.get(n, "—"), info.unit if info else "",
                     self.backup.get(n, "")]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setData(Qt.ItemDataRole.UserRole, n)
                if col == 4 and text and n in values and grbl_settings.diff({n: values[n]}, {n: text}):
                    item.setText(f"≠ {text}")
                self.table.setItem(row, col, item)
        if not keys:
            self.help.setText(tr("Conecte la máquina y pulse «Leer $$»."))

    def _selected_number(self, row: int | None = None) -> int | None:
        r = self.table.currentRow() if row is None else row
        item = self.table.item(r, 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def _explain(self, row: int) -> None:
        n = self._selected_number(row)
        if n is None:
            return
        info = grbl_settings.describe(n)
        text = f"<b>${n}</b> — " + (f"{info.label}: {info.help_text}" if info else tr("Sin descripción"))
        if info and info.dangerous:
            text += "<br>⚠ " + tr("Cambiar este parámetro afecta a la seguridad o al movimiento.")
        self.help.setText(text)
        self.value.setText(self.current.get(n, ""))

    def _read(self) -> None:
        if not self.reader():
            QMessageBox.information(self, tr("Parámetros"), tr("No se pueden leer en el estado actual."))

    def _write(self) -> None:
        n = self._selected_number()
        if n is None or not self.writable:
            return
        val = self.value.text().strip()
        ok, msg = grbl_settings.validate(n, val)
        if not ok:
            QMessageBox.warning(self, tr("Valor no válido"), msg)
            return
        info = grbl_settings.describe(n)
        warn = tr("¿Escribir ${n}={v} en el controlador?", n=n, v=val)
        if info and info.dangerous:
            warn += "\n\n" + tr("Este parámetro afecta a la seguridad o al movimiento.")
        if msg:
            warn += "\n\n" + msg
        if QMessageBox.question(self, tr("Confirmar escritura"), warn) == QMessageBox.StandardButton.Yes:
            self.writer(n, val)

    def _backup(self) -> None:
        if not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Guardar copia de parámetros"), "grbl_settings.json", "JSON (*.json)")
        if path:
            atomic_write_json(path, {"grbl_settings": {str(k): v for k, v in self.current.items()}})

    def _compare(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Abrir copia de parámetros"), "", "JSON (*.json)")
        if not path:
            return
        data = read_json(path, default={})
        raw = data.get("grbl_settings", {}) if isinstance(data, dict) else {}
        self.backup = {int(k): str(v) for k, v in raw.items()}
        self.populate(self.current)
        changes = grbl_settings.diff(self.current, self.backup)
        self.help.setText(tr("{n} parámetros difieren de la copia.", n=len(changes)))

    def _restore(self) -> None:
        if not self.writable:
            return
        if not self.backup:
            QMessageBox.information(self, tr("Restaurar"), tr("Primero cargue una copia con «Comparar con copia…»."))
            return
        rows = {i.row() for i in self.table.selectedIndexes()}
        targets = []
        for r in sorted(rows):
            n = self._selected_number(r)
            if n is not None and n in self.backup and grbl_settings.diff({n: self.current.get(n, "")}, {n: self.backup[n]}):
                targets.append(n)
        if not targets:
            QMessageBox.information(self, tr("Restaurar"), tr("Seleccione filas marcadas con ≠."))
            return
        text = "\n".join(f"${n}: {self.current.get(n)} → {self.backup[n]}" for n in targets)
        if QMessageBox.question(self, tr("Confirmar restauración"), text) == QMessageBox.StandardButton.Yes:
            for n in targets:
                self.writer(n, self.backup[n])


# ------------------------------------------------------------------ preferencias
class PreferencesDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Preferencias"))
        self.resize(560, 520)
        self.settings = settings
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs)

        general = QWidget()
        fg = QFormLayout(general)
        self.lang = QComboBox()
        self._fill_languages(settings.language)
        fg.addRow(tr("Idioma (requiere reiniciar)"), self.lang)
        lang_row = QHBoxLayout()
        open_dir = QPushButton(tr("Abrir carpeta de idiomas"))
        open_dir.setToolTip(tr("Carpeta donde puede añadir archivos de idioma (.json) sin modificar el programa"))
        open_dir.clicked.connect(self._open_language_dir)
        new_lang = QPushButton(tr("Crear idioma nuevo…"))
        new_lang.setToolTip(tr("Copia la plantilla con todas las frases del programa para traducirla"))
        new_lang.clicked.connect(self._new_language)
        lang_row.addWidget(open_dir)
        lang_row.addWidget(new_lang)
        lang_row.addStretch(1)
        fg.addRow("", lang_row)
        self.theme = QComboBox()
        for code, name in (("dark", N_("Oscuro")), ("light", N_("Claro")), ("contrast", N_("Alto contraste"))):
            self.theme.addItem(tr(name), code)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.theme)))
        fg.addRow(tr("Tema"), self.theme)
        self.units = QComboBox()
        self.units.addItem("mm", "mm")
        self.units.addItem(tr("pulgadas"), "in")
        self.units.setCurrentIndex(max(0, self.units.findData(settings.units)))
        self.units.setToolTip(tr("La Fase 1 muestra coordenadas en mm; las pulgadas se aplican a la interfaz en la Fase 2."))
        fg.addRow(tr("Unidades"), self.units)
        self.icon_text = QCheckBox(tr("Mostrar texto junto a los iconos"))
        self.icon_text.setChecked(settings.show_icon_text)
        fg.addRow("", self.icon_text)
        self.color_icons = QCheckBox(tr("Iconos a color"))
        self.color_icons.setToolTip(tr("Iconos bicolor por familias; el tema de alto contraste los muestra siempre "
                                       "en monocromo. Algunos paneles se actualizan al reiniciar."))
        self.color_icons.setChecked(settings.color_icons)
        fg.addRow("", self.color_icons)
        self.grid = QDoubleSpinBox()
        self.grid.setRange(0.1, 100)
        self.grid.setValue(settings.grid_mm)
        fg.addRow(tr("Cuadrícula (mm)"), self.grid)
        self.undo_limit = QSpinBox()
        self.undo_limit.setRange(0, 10000)
        self.undo_limit.setSpecialValueText(tr("Ilimitado"))
        self.undo_limit.setValue(settings.undo_limit)
        fg.addRow(tr("Niveles de deshacer"), self.undo_limit)
        tabs.addTab(general, tr("General"))

        safety = QWidget()
        fs = QFormLayout(safety)
        self.fire_pct = QDoubleSpinBox()
        self.fire_pct.setRange(0.1, HARD_MAX_FIRE_PERCENT)
        self.fire_pct.setDecimals(1)
        self.fire_pct.setValue(settings.safety.max_fire_percent)
        fs.addRow(tr("Potencia máx. de enfoque (%)"), self.fire_pct)
        self.fire_s = QDoubleSpinBox()
        self.fire_s.setRange(0.2, HARD_MAX_FIRE_SECONDS)
        self.fire_s.setDecimals(1)
        self.fire_s.setValue(settings.safety.max_fire_seconds)
        fs.addRow(tr("Duración máx. de disparo (s)"), self.fire_s)
        self.req_check = QCheckBox(tr("Exigir lista de comprobación antes de iniciar"))
        self.req_check.setChecked(settings.safety.require_checklist)
        fs.addRow("", self.req_check)
        self.block_home = QCheckBox(tr("Impedir iniciar sin homing"))
        self.block_home.setChecked(settings.safety.block_without_homing)
        fs.addRow("", self.block_home)
        self.confirm_cmd = QCheckBox(tr("Confirmar comandos peligrosos en consola"))
        self.confirm_cmd.setChecked(True)
        self.confirm_cmd.setEnabled(False)
        self.confirm_cmd.setToolTip(tr("Siempre activo por seguridad"))
        fs.addRow("", self.confirm_cmd)
        self.checklist = QPlainTextEdit("\n".join(tr(c) for c in settings.safety.checklist))
        fs.addRow(tr("Lista de comprobación\n(una por línea)"), self.checklist)
        tabs.addTab(safety, tr("Seguridad"))

        keys = QWidget()
        fk = QFormLayout(keys)
        self.key_edits: dict[str, QLineEdit] = {}
        names = {"jog_x_plus": "Jog X+", "jog_x_minus": "Jog X−", "jog_y_plus": "Jog Y+", "jog_y_minus": "Jog Y−",
                 "jog_z_plus": "Jog Z+", "jog_z_minus": "Jog Z−", "jog_a_plus": "Jog A+", "jog_a_minus": "Jog A−"}
        for key, label in names.items():
            e = QLineEdit(settings.keymap.get(key, ""))
            e.setPlaceholderText(tr("p. ej. Ctrl+Right"))
            self.key_edits[key] = e
            fk.addRow(label, e)
        hint = QLabel(tr("El teclado mueve un paso por pulsación. Los atajos no actúan mientras se escribe en un campo."))
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        fk.addRow(hint)
        tabs.addTab(keys, tr("Atajos"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)


    def _fill_languages(self, current: str) -> None:
        self.lang.clear()
        for code, name in i18n.available_languages().items():
            self.lang.addItem(f"{name} ({code})", code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(current)))

    def _open_language_dir(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        try:
            folder = i18n.user_locales_dir(create=True)
        except OSError as exc:
            QMessageBox.warning(self, tr("Idioma"), str(exc))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _new_language(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QInputDialog
        code, ok = QInputDialog.getText(
            self, tr("Crear idioma nuevo…"),
            tr("Código del idioma (por ejemplo it, pt, ca):"))
        if not ok:
            return
        try:
            path = i18n.create_language_file(code)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, tr("Crear idioma nuevo…"), str(exc))
            return
        QMessageBox.information(
            self, tr("Crear idioma nuevo…"),
            tr("Se ha creado {p}.\nTraduzca los valores de «strings» (la clave es la frase en español y no se "
               "cambia), guarde el archivo y reinicie la aplicación para elegir el idioma.", p=str(path)))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        self._fill_languages(str(self.lang.currentData()))
    def _accept(self) -> None:
        s = self.settings
        s.language = str(self.lang.currentData())
        s.theme = str(self.theme.currentData())
        s.units = str(self.units.currentData())
        s.show_icon_text = self.icon_text.isChecked()
        s.color_icons = self.color_icons.isChecked()
        s.grid_mm = self.grid.value()
        s.undo_limit = self.undo_limit.value()
        s.safety.max_fire_percent = self.fire_pct.value()
        s.safety.max_fire_seconds = self.fire_s.value()
        s.safety.require_checklist = self.req_check.isChecked()
        s.safety.block_without_homing = self.block_home.isChecked()
        items = [ln.strip() for ln in self.checklist.toPlainText().splitlines() if ln.strip()]
        s.safety.checklist = items
        s.safety.clamp()
        for key, e in self.key_edits.items():
            s.keymap[key] = e.text().strip()
        self.accept()


# ------------------------------------------------------------------------ macros
class MacroEditorDialog(QDialog):
    def __init__(self, macros: list[Macro], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Editor de macros"))
        self.resize(640, 420)
        self.macros = [Macro(m.name, list(m.lines), m.description) for m in macros]
        root = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load)
        left.addWidget(self.list)
        row = QHBoxLayout()
        for text, fn in ((tr("Nueva"), self._new), (tr("Eliminar"), self._delete)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        left.addLayout(row)
        root.addLayout(left, 1)
        right = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit()
        self.desc = QLineEdit()
        self.body = QPlainTextEdit()
        form.addRow(tr("Nombre"), self.name)
        form.addRow(tr("Descripción"), self.desc)
        form.addRow(tr("Comandos"), self.body)
        right.addLayout(form)
        hint = QLabel(tr("Variables: {x} {y} {z} {a} (trabajo), {mx} {my} (máquina), {cx} {cy} (centro), "
                         "{width} {height} {smax}. Las macros que encienden el láser piden confirmación."))
        hint.setWordWrap(True)
        hint.setProperty("role", "muted")
        right.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        right.addWidget(buttons)
        root.addLayout(right, 2)
        self._current = -1
        self._refresh()

    def _refresh(self, select: int = 0) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for m in self.macros:
            self.list.addItem(m.name)
        self.list.blockSignals(False)
        self._current = -1
        if self.macros:
            self.list.setCurrentRow(min(select, len(self.macros) - 1))
            self._load(self.list.currentRow())

    def _store(self) -> None:
        if 0 <= self._current < len(self.macros):
            m = self.macros[self._current]
            m.name = self.name.text().strip() or m.name
            m.description = self.desc.text().strip()
            m.lines = [ln.strip() for ln in self.body.toPlainText().splitlines() if ln.strip()]

    def _load(self, row: int) -> None:
        self._store()
        if not 0 <= row < len(self.macros):
            return
        self._current = row
        m = self.macros[row]
        self.name.setText(m.name)
        self.desc.setText(m.description)
        self.body.setPlainText("\n".join(m.lines))

    def _new(self) -> None:
        self._store()
        self.macros.append(Macro(tr("Nueva macro"), ["$G"], ""))
        self._refresh(len(self.macros) - 1)

    def _delete(self) -> None:
        if 0 <= self._current < len(self.macros):
            del self.macros[self._current]
            self._current = -1
            self._refresh(0)

    def _accept(self) -> None:
        self._store()
        self.accept()


def export_settings_file(parent: QWidget, settings: AppSettings) -> None:
    path, _ = QFileDialog.getSaveFileName(parent, tr("Exportar configuración"), "openlaser_config.json",
                                          "JSON (*.json)")
    if path:
        atomic_write_json(path, settings.to_dict())


def import_settings_file(parent: QWidget) -> AppSettings | None:
    path, _ = QFileDialog.getOpenFileName(parent, tr("Importar configuración"), "", "JSON (*.json)")
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return AppSettings.from_dict(data)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        QMessageBox.warning(parent, tr("Importar configuración"), tr("Archivo no válido: {e}", e=exc))
        return None
