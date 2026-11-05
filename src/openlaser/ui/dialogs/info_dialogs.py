"""Diálogos informativos: acerca de, seguridad, licencias, atajos, plugins, diagnóstico e ir a."""
from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel, QPlainTextEdit, QTextBrowser, QVBoxLayout,
    QWidget,
)

from openlaser.app.config import APP_VERSION, app_name
from openlaser.i18n import N_, tr
from openlaser.machine.controller import MachineController
from openlaser.plugins import controllers
from openlaser.safety.manager import DISCLAIMER_ES
from openlaser.ui.actions import ActionRegistry

DEPENDENCIES = [
    ("Python 3.12", "PSF License", N_("Intérprete")),
    ("PySide6 / Qt 6", "LGPL-3.0 (o GPL / comercial)", N_("Interfaz gráfica; enlace dinámico")),
    ("pyserial", "BSD-3-Clause", N_("USB / serie")),
    ("websockets", "BSD-3-Clause", N_("Conexiones WebSocket")),
    ("pytest", "MIT", N_("Pruebas (sólo desarrollo)")),
    ("NumPy", "BSD-3-Clause", N_("Fase 3: ráster y geometría")),
    ("Pillow", "MIT-CMU (HPND)", N_("Fase 3: imágenes")),
    ("OpenCV (opencv-python)", "Apache-2.0", N_("Fase 4: cámara y calibración")),
    ("Shapely", "BSD-3-Clause", N_("Fase 2: booleanas y offsets")),
    ("ezdxf", "MIT", N_("Fase 2: DXF")),
    ("svgpathtools", "MIT", N_("Fase 2: SVG")),
]


def _text_dialog(parent: QWidget | None, title: str, html: str, width: int = 620, height: int = 520) -> QDialog:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(width, height)
    lay = QVBoxLayout(dlg)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setHtml(html)
    lay.addWidget(view)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    return dlg


def about_dialog(parent: QWidget | None) -> QDialog:
    html = (f"<h2>{app_name()} {APP_VERSION}</h2>"
            f"<p>{tr('Diseño, preparación, simulación y ejecución de trabajos de grabado y corte láser.')}</p>"
            f"<p>{tr('Fase 1: interfaz principal, movimiento, simulador GRBL, consola, estados y seguridad.')}</p>"
            f"<p><b>{tr('Seguridad')}</b>: {tr(DISCLAIMER_ES)}</p>"
            f"<p>{tr('Nombre, iconos y textos originales. No contiene código ni recursos de LaserGRBL ni LightBurn.')}</p>")
    return _text_dialog(parent, tr("Acerca de"), html, 520, 380)


def safety_dialog(parent: QWidget | None) -> QDialog:
    items = [
        tr("Al conectar no se envía ningún movimiento, ni $H ni $X."),
        tr("Jog, frame e «ir a» usan G0/$J o G1 S0 precedidos de M5: nunca emiten."),
        tr("El disparo de enfoque exige confirmación, potencia ≤ límite y duración ≤ límite; se envía como "
           "M3 → G4 → M5 para que el controlador lo apague aunque se pierda la conexión, y hay un vigilante."),
        tr("Detener, la parada de software, desconectar o cualquier error de comunicación envían reinicio "
           "(0x18) y vacían la cola."),
        tr("Tras una alarma nunca se desbloquea ni se reanuda automáticamente."),
        tr("Antes de iniciar se comprueban estado, puerta, límites, homing, $32, $30 y unidades, y se pide la "
           "lista de comprobación."),
        tr("Todos los eventos de seguridad quedan registrados."),
    ]
    html = (f"<h3>{tr('Medidas de seguridad del software')}</h3><ul>" + "".join(f"<li>{i}</li>" for i in items) +
            f"</ul><p><b>{tr(DISCLAIMER_ES)}</b></p>")
    return _text_dialog(parent, tr("Seguridad"), html)


def licenses_dialog(parent: QWidget | None) -> QDialog:
    rows = "".join(f"<tr><td>{n}</td><td>{lic}</td><td>{tr(use)}</td></tr>" for n, lic, use in DEPENDENCIES)
    html = (f"<h3>{tr('Dependencias y licencias')}</h3><table border='1' cellpadding='4' cellspacing='0'>"
            f"<tr><th>{tr('Componente')}</th><th>{tr('Licencia')}</th><th>{tr('Uso')}</th></tr>{rows}</table>"
            f"<p>{tr('Detalle completo en docs/LICENCIAS.md.')}</p>")
    return _text_dialog(parent, tr("Licencias"), html)


def shortcuts_dialog(parent: QWidget | None, registry: ActionRegistry, keymap: dict[str, str]) -> QDialog:
    rows = []
    for act in registry.actions.values():
        seq = act.shortcut().toString()
        if seq and act.isEnabled():
            rows.append(f"<tr><td>{act.text()}</td><td><code>{seq}</code></td></tr>")
    for key, seq in keymap.items():
        if seq:
            rows.append(f"<tr><td>{key.replace('jog_', 'Jog ').replace('_plus', '+').replace('_minus', '−').upper()}"
                        f"</td><td><code>{seq}</code></td></tr>")
    html = f"<h3>{tr('Atajos de teclado')}</h3><table cellpadding='3'>" + "".join(rows) + "</table>"
    return _text_dialog(parent, tr("Atajos de teclado"), html, 480, 560)


def plugins_dialog(parent: QWidget | None) -> QDialog:
    rows = "".join(
        f"<tr><td>{a.name}</td><td>{tr('disponible') if a.status == 'available' else tr('previsto')}</td>"
        f"<td>{tr(a.notes) if a.notes else ', '.join(sorted(a.capabilities))}</td></tr>"
        for a in controllers.adapters())
    html = (f"<h3>{tr('Adaptadores de controlador')}</h3><table border='1' cellpadding='4' cellspacing='0'>"
            f"<tr><th>{tr('Controlador')}</th><th>{tr('Estado')}</th><th>{tr('Notas')}</th></tr>{rows}</table>"
            f"<p>{tr('Los protocolos propietarios sólo se integrarán mediante plugins autorizados.')}</p>")
    return _text_dialog(parent, tr("Gestión de plugins"), html)


def diagnostics_dialog(parent: QWidget | None, ctl: MachineController) -> QDialog:
    st = ctl.status()
    pending, inflight, nbytes = ctl.queue_stats
    lines = [
        f"{tr('Perfil')}: {ctl.profile.name} ({ctl.profile.connection_label()})",
        f"{tr('Conexión')}: {st.conn.value} · GRBL: {st.grbl.value} · {tr('Trabajo')}: {st.job.value}",
        f"{tr('Firmware')}: {st.firmware or '—'}",
        f"{tr('Cola')}: {pending} {tr('pendientes')}, {inflight} {tr('en vuelo')} ({nbytes} bytes)",
        f"{tr('Búfer del controlador')}: planner {st.planner_free} · rx {st.rx_free}",
        f"{tr('Homing')}: {st.homed} · {tr('Puerta abierta')}: {st.door_open} · E-stop: {st.estop}",
        f"MPos: {st.mpos}  WCO: {st.wco}",
        "",
        tr("Eventos de seguridad recientes:"),
    ]
    for ev in ctl.safety.events()[-40:]:
        lines.append(f"  {time.strftime('%H:%M:%S', time.localtime(ev.timestamp))}  {ev.kind}: {ev.message}")
    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("Diagnóstico"))
    dlg.resize(700, 480)
    lay = QVBoxLayout(dlg)
    text = QPlainTextEdit("\n".join(lines))
    text.setReadOnly(True)
    lay.addWidget(text)
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    return dlg


class GotoDialog(QDialog):
    def __init__(self, x: float, y: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Ir a coordenadas"))
        form = QFormLayout(self)
        self.spin_x = QDoubleSpinBox()
        self.spin_y = QDoubleSpinBox()
        for w, v in ((self.spin_x, x), (self.spin_y, y)):
            w.setRange(-10000, 10000)
            w.setDecimals(3)
            w.setValue(v)
        form.addRow("X", self.spin_x)
        form.addRow("Y", self.spin_y)
        form.addRow(QLabel(tr("Coordenadas de trabajo. Movimiento G0 con láser apagado.")))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)
