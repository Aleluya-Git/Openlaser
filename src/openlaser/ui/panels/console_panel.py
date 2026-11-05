"""Consola GRBL: tráfico en tiempo real, filtros, historial, macros y exportación."""
from __future__ import annotations

import html
import time
from collections import deque

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from openlaser.i18n import tr
from openlaser.machine.events import ConsoleEvent, LineKind
from openlaser.persistence.settings import AppSettings, Macro
from openlaser.ui import icons, theme

MAX_LINES = 4000


class ConsolePanel(QWidget):
    send_requested = Signal(str)
    macro_requested = Signal(object)
    edit_macros_requested = Signal()
    export_requested = Signal()

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._log: deque[ConsoleEvent] = deque(maxlen=20000)
        self._history: list[str] = []
        self._hist_pos = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        filters = QHBoxLayout()
        self.f_tx = QCheckBox(tr("TX"))
        self.f_rx = QCheckBox(tr("RX"))
        self.f_info = QCheckBox(tr("Info"))
        self.f_err = QCheckBox(tr("Avisos/errores"))
        self.f_status = QCheckBox(tr("Estado (?)"))
        self.f_job = QCheckBox(tr("Líneas del trabajo"))
        self.f_time = QCheckBox(tr("Marcas de tiempo"))
        for cb in (self.f_tx, self.f_rx, self.f_info, self.f_err, self.f_time):
            cb.setChecked(True)
        self.f_status.setChecked(settings.console_show_status)
        self.f_job.setChecked(settings.console_show_job)
        for cb in (self.f_tx, self.f_rx, self.f_info, self.f_err, self.f_status, self.f_job, self.f_time):
            cb.toggled.connect(self._refilter)
            filters.addWidget(cb)
        filters.addStretch(1)
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText(tr("Filtrar texto…"))
        self.filter_text.setClearButtonEnabled(True)
        self.filter_text.setMaximumWidth(180)
        self.filter_text.textChanged.connect(self._refilter)
        filters.addWidget(self.filter_text)
        btn_clear = QPushButton(icons.icon("delete"), "")
        btn_clear.setToolTip(tr("Vaciar consola"))
        btn_clear.setAccessibleName(tr("Vaciar consola"))
        btn_clear.clicked.connect(self.clear)
        btn_export = QPushButton(icons.icon("export"), "")
        btn_export.setToolTip(tr("Exportar registro para diagnóstico"))
        btn_export.setAccessibleName(tr("Exportar registro"))
        btn_export.clicked.connect(lambda: self.export_requested.emit())
        filters.addWidget(btn_clear)
        filters.addWidget(btn_export)
        root.addLayout(filters)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setAccessibleName(tr("Salida de la consola GRBL"))
        self.view.setStyleSheet(f"font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 12px;"
                                f" background: {theme.current().console_bg};")
        root.addWidget(self.view, 1)

        self.macro_row = QHBoxLayout()
        root.addLayout(self.macro_row)
        self.reload_macros()

        inp = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText(tr("Comando G-code o $ (↑/↓ historial). Los comandos peligrosos piden confirmación."))
        self.input.setAccessibleName(tr("Entrada de comandos"))
        self.input.returnPressed.connect(self._send)
        self.input.installEventFilter(self)
        self.btn_send = QPushButton(icons.icon("start"), tr("Enviar"))
        self.btn_send.clicked.connect(self._send)
        inp.addWidget(self.input, 1)
        inp.addWidget(self.btn_send)
        root.addLayout(inp)

    # --- macros --------------------------------------------------------------------
    def reload_macros(self) -> None:
        while self.macro_row.count():
            item = self.macro_row.takeAt(0)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        for macro in self.settings.macros:
            b = QPushButton(icons.icon("macro"), macro.name)
            b.setToolTip(macro.description + "\n" + "\n".join(macro.lines))
            b.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _c=False, m=macro: self.macro_requested.emit(m))
            self.macro_row.addWidget(b)
        edit = QPushButton(icons.icon("settings"), tr("Macros…"))
        edit.setToolTip(tr("Editar macros"))
        edit.clicked.connect(lambda: self.edit_macros_requested.emit())
        self.macro_row.addWidget(edit)
        self.macro_row.addStretch(1)

    def set_input_enabled(self, enabled: bool) -> None:
        self.input.setEnabled(enabled)
        self.btn_send.setEnabled(enabled)
        for i in range(self.macro_row.count()):
            item = self.macro_row.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, QPushButton) and w.text() != tr("Macros…"):
                w.setEnabled(enabled)

    # --- entrada ---------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.input and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Up and self._history:
                self._hist_pos = max(0, self._hist_pos - 1)
                self.input.setText(self._history[self._hist_pos])
                return True
            if event.key() == Qt.Key.Key_Down and self._history:
                self._hist_pos = min(len(self._history), self._hist_pos + 1)
                self.input.setText(self._history[self._hist_pos] if self._hist_pos < len(self._history) else "")
                return True
        return super().eventFilter(watched, event)

    def _send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._hist_pos = len(self._history)
        self.input.clear()
        self.send_requested.emit(text)

    # --- salida ----------------------------------------------------------------------
    def append(self, ev: ConsoleEvent) -> None:
        self._log.append(ev)
        if self._visible(ev):
            self.view.appendHtml(self._format(ev))

    def _visible(self, ev: ConsoleEvent) -> bool:
        k = ev.kind
        if k == LineKind.STATUS and not self.f_status.isChecked():
            return False
        if k == LineKind.TX and ev.source == "job" and not self.f_job.isChecked():
            return False
        if k == LineKind.RX and ev.text == "ok" and not self.f_job.isChecked() and self._log_job_active():
            return False
        if k == LineKind.TX and not self.f_tx.isChecked():
            return False
        if k == LineKind.RX and not self.f_rx.isChecked():
            return False
        if k == LineKind.INFO and not self.f_info.isChecked():
            return False
        if k in (LineKind.WARN, LineKind.ERROR) and not self.f_err.isChecked():
            return False
        needle = self.filter_text.text().strip().lower()
        return not needle or needle in ev.text.lower()

    def _log_job_active(self) -> bool:
        # Oculta los "ok" masivos mientras se transmiten líneas de trabajo ocultas.
        for ev in reversed(self._log):
            if ev.kind == LineKind.TX:
                return ev.source == "job"
        return False

    def _format(self, ev: ConsoleEvent) -> str:
        t = theme.current()
        color = {LineKind.TX: t.info, LineKind.RX: t.text, LineKind.INFO: t.muted, LineKind.WARN: t.warn,
                 LineKind.ERROR: t.danger, LineKind.STATUS: t.muted}[ev.kind]
        prefix = {LineKind.TX: "»", LineKind.RX: "«", LineKind.INFO: "·", LineKind.WARN: "⚠",
                  LineKind.ERROR: "✖", LineKind.STATUS: "?"}[ev.kind]
        stamp = ""
        if self.f_time.isChecked():
            stamp = time.strftime("%H:%M:%S", time.localtime(ev.timestamp)) + f".{int(ev.timestamp * 1000) % 1000:03d} "
        return (f'<span style="color:{t.muted}">{stamp}</span>'
                f'<span style="color:{color}">{prefix} {html.escape(ev.text)}</span>')

    def _refilter(self) -> None:
        self.settings.console_show_status = self.f_status.isChecked()
        self.settings.console_show_job = self.f_job.isChecked()
        self.view.clear()
        shown = [self._format(ev) for ev in list(self._log)[-MAX_LINES:] if self._visible(ev)]
        if shown:
            self.view.appendHtml("<br>".join(shown))

    def clear(self) -> None:
        self._log.clear()
        self.view.clear()

    def export_text(self) -> str:
        lines = []
        for ev in self._log:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ev.timestamp))
            lines.append(f"{stamp}\t{ev.kind.value}\t{ev.source}\t{ev.text}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def macro_from(name: str, lines: list[str], description: str = "") -> Macro:
        return Macro(name, lines, description)
