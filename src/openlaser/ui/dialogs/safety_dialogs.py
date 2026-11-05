"""Diálogos de seguridad: comprobación previa, disparo de prueba y alarmas."""
from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from openlaser.i18n import tr
from openlaser.machine.grbl_codes import alarm_info
from openlaser.safety.manager import DISCLAIMER_ES, Issue, SafetyEvent, Severity, has_blocking
from openlaser.ui import icons, theme


def disclaimer() -> str:
    return tr(DISCLAIMER_ES)


class PreflightDialog(QDialog):
    """Lista de comprobación previa al inicio. Bloquea si hay problemas graves."""

    def __init__(self, job_name: str, summary: str, issues: list[Issue], checklist: list[str],
                 require_checklist: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Comprobación antes de iniciar"))
        self.setMinimumWidth(560)
        t = theme.current()
        root = QVBoxLayout(self)
        head = QLabel(f"<b>{job_name}</b><br>{summary}")
        head.setWordWrap(True)
        root.addWidget(head)
        if issues:
            box = QListWidget()
            box.setAccessibleName(tr("Problemas detectados"))
            for issue in issues:
                sym = {"block": "⛔", "warn": "⚠", "info": "ℹ"}[issue.severity.value]
                item = QListWidgetItem(f"{sym}  {issue.message}")
                color = {Severity.BLOCK: t.danger, Severity.WARN: t.warn, Severity.INFO: t.muted}[issue.severity]
                item.setForeground(QColor("#ffffff") if t.name == "contrast" else QColor(color))
                box.addItem(item)
            box.setMaximumHeight(140)
            root.addWidget(box)
        self.blocked = has_blocking(issues)
        if self.blocked:
            stop = QLabel(tr("No se puede iniciar: resuelva los problemas marcados con ⛔."))
            stop.setStyleSheet(f"color: {t.danger}; font-weight: 700;")
            root.addWidget(stop)
        self.checks: list[QCheckBox] = []
        if checklist:
            root.addWidget(QLabel(tr("Confirme cada punto antes de iniciar:")))
            for text in checklist:
                cb = QCheckBox(tr(text))
                cb.toggled.connect(self._update)
                self.checks.append(cb)
                root.addWidget(cb)
        self.require_checklist = require_checklist
        warn = QLabel(disclaimer())
        warn.setWordWrap(True)
        warn.setProperty("role", "muted")
        root.addWidget(warn)
        self.buttons = QDialogButtonBox()
        self.btn_ok = self.buttons.addButton(tr("Iniciar trabajo"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_ok.setIcon(icons.icon_on("start", theme.current().accent_text))
        self.btn_ok.setProperty("role", "accent")
        self.buttons.addButton(tr("Cancelar"), QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._update()

    def _update(self) -> None:
        all_checked = all(cb.isChecked() for cb in self.checks) or not self.require_checklist
        self.btn_ok.setEnabled(all_checked and not self.blocked)


class FireDialog(QDialog):
    def __init__(self, percent: float, seconds: float, s_value: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Confirmar disparo de enfoque"))
        root = QVBoxLayout(self)
        msg = QLabel(tr("Se encenderá el láser al {p:.1f} % (S{s:g}) durante {t:.1f} s sin movimiento.\n"
                        "Se apagará automáticamente al terminar. Pulse Detener o la parada de software para "
                        "cortarlo antes.", p=percent, s=s_value, t=seconds))
        msg.setWordWrap(True)
        root.addWidget(msg)
        self.ck_eyes = QCheckBox(tr("Llevo protección ocular adecuada y no hay nadie expuesto al haz"))
        self.ck_material = QCheckBox(tr("Hay material no inflamable o de prueba bajo el cabezal"))
        for cb in (self.ck_eyes, self.ck_material):
            cb.toggled.connect(self._update)
            root.addWidget(cb)
        root.addWidget(QLabel(disclaimer()))
        self.buttons = QDialogButtonBox()
        self.btn_fire = self.buttons.addButton(tr("Disparar"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_fire.setProperty("role", "danger")
        self.btn_fire.setIcon(icons.icon_on("fire"))
        self.buttons.addButton(tr("Cancelar"), QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._update()

    def _update(self) -> None:
        self.btn_fire.setEnabled(self.ck_eyes.isChecked() and self.ck_material.isChecked())


class AlarmDialog(QDialog):
    """Explica una alarma. El comando de recuperación sólo se envía si el usuario lo pulsa."""

    recovery_requested = Signal(str)

    def __init__(self, code: int, recent: list[SafetyEvent], parent: QWidget | None = None,
                 dialect: str = "grbl") -> None:
        super().__init__(parent)
        info = alarm_info(code, dialect)
        self.setWindowTitle(tr("Alarma {c}", c=code))
        self.setMinimumWidth(520)
        t = theme.current()
        root = QVBoxLayout(self)
        title = QLabel(f"⚠  ALARM:{code} — {info.text}")
        title.setStyleSheet(f"color: {t.danger}; font-size: 15px; font-weight: 700;")
        title.setWordWrap(True)
        root.addWidget(title)
        for label, text in ((tr("Causa probable"), info.cause_text), (tr("Acción segura recomendada"), info.action_text)):
            lab = QLabel(f"<b>{label}:</b> {text}")
            lab.setWordWrap(True)
            root.addWidget(lab)
        root.addWidget(QLabel(tr("La aplicación no desbloquea ni reanuda automáticamente.")))
        root.addWidget(QLabel(f"<b>{tr('Últimos eventos')}</b>"))
        lst = QListWidget()
        for ev in recent[-10:]:
            lst.addItem(f"{time.strftime('%H:%M:%S', time.localtime(ev.timestamp))}  {ev.kind}: {ev.message}")
        lst.setMaximumHeight(150)
        root.addWidget(lst)
        row = QHBoxLayout()
        if info.recovery:
            label = tr("Hacer Home ($H)") if info.recovery == "$H" else tr("Desbloquear ($X)")
            btn = QPushButton(icons.icon("home" if info.recovery == "$H" else "unlock"), label)
            btn.clicked.connect(lambda: self._recover(info.recovery or ""))
            row.addWidget(btn)
        row.addStretch(1)
        close = QPushButton(tr("Cerrar"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    def _recover(self, cmd: str) -> None:
        text = (tr("¿Ha eliminado la causa de la alarma y es seguro mover la máquina?") if cmd == "$H" else
                tr("Desbloquear sin Home deja la posición sin verificar. ¿Continuar?"))
        if QMessageBox.question(self, tr("Confirmar recuperación"), text) == QMessageBox.StandardButton.Yes:
            self.recovery_requested.emit(cmd)
            self.accept()
