"""Puente entre el controlador (hilo de máquina) y la interfaz Qt.

Un ``QTimer`` en el hilo de la interfaz vacía la cola de eventos del
controlador y los reemite como señales Qt. El hilo de máquina nunca toca
widgets y la interfaz nunca se bloquea esperando a la máquina.
"""
from __future__ import annotations

import queue

from PySide6.QtCore import QObject, QTimer, Signal

from openlaser.machine.controller import MachineController
from openlaser.machine.events import (
    AlarmEvent, CommandRejected, ConsoleEvent, ErrorEvent, JobProgressEvent, SafetyNotice, SettingsEvent,
    StatusEvent,
)


class MachineBridge(QObject):
    status = Signal(object)
    console = Signal(object)
    alarm = Signal(int)
    error = Signal(object)
    settings = Signal(object)
    notice = Signal(object)
    progress = Signal(object)
    rejected = Signal(object)

    MAX_EVENTS_PER_TICK = 800

    def __init__(self, controller: MachineController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._drain)
        self._timer.start()

    def set_controller(self, controller: MachineController) -> None:
        self._drain()
        self.controller = controller
        self.status.emit(controller.status())

    def _drain(self) -> None:
        q = self.controller.events
        last_status = None
        for _ in range(self.MAX_EVENTS_PER_TICK):
            try:
                ev = q.get_nowait()
            except queue.Empty:
                break
            if isinstance(ev, StatusEvent):
                last_status = ev.status  # sólo interesa el más reciente
            elif isinstance(ev, ConsoleEvent):
                self.console.emit(ev)
            elif isinstance(ev, AlarmEvent):
                self.alarm.emit(ev.code)
            elif isinstance(ev, ErrorEvent):
                self.error.emit(ev)
            elif isinstance(ev, SettingsEvent):
                self.settings.emit(ev.settings)
            elif isinstance(ev, SafetyNotice):
                self.notice.emit(ev)
            elif isinstance(ev, JobProgressEvent):
                self.progress.emit(ev)
            elif isinstance(ev, CommandRejected):
                self.rejected.emit(ev)
        if last_status is not None:
            self.status.emit(last_status)
