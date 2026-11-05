"""Cola de envío de G-code con conteo de caracteres y confirmación ``ok``.

GRBL dispone de un búfer de recepción de 128 bytes. El protocolo de
"character counting" envía líneas mientras la suma de bytes pendientes de
confirmar cabe en el búfer y retira la línea más antigua por cada ``ok`` o
``error:N``. Los comandos exclusivos (``$H``, ``$$``…) se envían sólo con la
cola vacía y bloquean el resto hasta su confirmación.

La clase no es segura entre hilos: sólo la usa el hilo de comunicación.
"""
from __future__ import annotations

import itertools
from collections import deque
from dataclasses import dataclass, field

from openlaser.machine.grbl_protocol import GRBL_LINE_MAX, GRBL_RX_BUFFER_SIZE, is_system_command

_ids = itertools.count(1)


@dataclass
class QueuedCommand:
    text: str
    source: str = "user"  # user, jog, job, frame, system, macro, fire
    job_index: int | None = None
    exclusive: bool = False
    sent_at: float | None = None
    tag_end: bool = False
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def size(self) -> int:
        return len(self.text.encode("ascii", "replace")) + 1  # + "\n"


class QueueError(ValueError):
    pass


class StreamingQueue:
    def __init__(self, rx_buffer_size: int = GRBL_RX_BUFFER_SIZE - 1, max_line: int = GRBL_LINE_MAX) -> None:
        if rx_buffer_size < max_line:
            raise ValueError("rx_buffer_size debe ser >= longitud máxima de línea")
        self.rx_buffer_size = rx_buffer_size
        self.max_line = max_line
        # Envío-respuesta (Marlin, Smoothieware): como máximo esta cantidad de líneas sin «ok».
        self.max_in_flight: int | None = None
        self._pending: deque[QueuedCommand] = deque()
        self._in_flight: deque[QueuedCommand] = deque()
        self._in_flight_bytes = 0

    # --- consulta ---
    @property
    def in_flight_bytes(self) -> int:
        return self._in_flight_bytes

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)

    def is_empty(self) -> bool:
        return not self._pending and not self._in_flight

    def count_source(self, source: str) -> int:
        return sum(1 for c in itertools.chain(self._pending, self._in_flight) if c.source == source)

    def in_flight(self) -> list[QueuedCommand]:
        return list(self._in_flight)

    # --- modificación ---
    def enqueue(self, text: str, source: str = "user", job_index: int | None = None,
                exclusive: bool | None = None) -> QueuedCommand:
        if "\n" in text or "\r" in text:
            raise QueueError("La línea no puede contener saltos de línea")
        if len(text) + 1 > self.max_line:
            raise QueueError(f"Línea demasiado larga ({len(text)} > {self.max_line - 1}): {text[:30]}…")
        if exclusive is None:
            exclusive = is_system_command(text)
        cmd = QueuedCommand(text=text, source=source, job_index=job_index, exclusive=exclusive)
        self._pending.append(cmd)
        return cmd

    def next_sendable(self) -> QueuedCommand | None:
        """Devuelve (sin retirar) el siguiente comando que cabe en el búfer."""
        if not self._pending:
            return None
        if self._in_flight and self._in_flight[0].exclusive:
            return None
        cmd = self._pending[0]
        if cmd.exclusive and self._in_flight:
            return None
        if self._in_flight_bytes + cmd.size > self.rx_buffer_size:
            return None
        if self.max_in_flight is not None and len(self._in_flight) >= self.max_in_flight:
            return None
        return cmd

    def mark_sent(self, cmd: QueuedCommand, now: float) -> None:
        if not self._pending or self._pending[0] is not cmd:
            raise QueueError("Sólo puede enviarse el primer comando pendiente")
        self._pending.popleft()
        cmd.sent_at = now
        self._in_flight.append(cmd)
        self._in_flight_bytes += cmd.size

    def acknowledge(self) -> QueuedCommand | None:
        """Procesa un ``ok``/``error``: retira y devuelve la línea más antigua."""
        if not self._in_flight:
            return None
        cmd = self._in_flight.popleft()
        self._in_flight_bytes -= cmd.size
        return cmd

    def clear_pending(self, source: str | None = None) -> int:
        if source is None:
            n = len(self._pending)
            self._pending.clear()
            return n
        keep = deque(c for c in self._pending if c.source != source)
        n = len(self._pending) - len(keep)
        self._pending = keep
        return n

    def pending_min_index(self, source: str) -> int | None:
        """Menor ``job_index`` de los comandos pendientes de ``source`` (para rebobinar un trabajo)."""
        idx = [c.job_index for c in self._pending if c.source == source and c.job_index is not None]
        return min(idx) if idx else None

    def clear_all(self) -> int:
        """Vacía todo (tras un reinicio del controlador, que descarta su búfer)."""
        n = len(self._pending) + len(self._in_flight)
        self._pending.clear()
        self._in_flight.clear()
        self._in_flight_bytes = 0
        return n
