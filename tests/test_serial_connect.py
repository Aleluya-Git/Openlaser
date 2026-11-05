"""Conexión por USB/serie con un pyserial simulado: placas que tardan, que no hablan hasta recibir
un reinicio, y mensajes de diagnóstico cuando no hay respuesta."""
from __future__ import annotations

import sys
import threading
import time
import types

import pytest
from conftest import wait_for

from openlaser.devices.profile import demo_profile
from openlaser.devices.transport import SerialTransport
from openlaser.machine import controller as ctl_mod
from openlaser.machine.controller import MachineController
from openlaser.machine.grbl_protocol import parse_line
from openlaser.machine.states import ConnState


class FakeSerial:
    """Imita ``serial.Serial``; ``mode``: ``"grbl"``, ``"needs_reset"``, ``"silent"`` o ``"garbage"``."""

    mode = "grbl"
    instances: list["FakeSerial"] = []

    def __init__(self) -> None:
        self.port = ""
        self.baudrate = 0
        self.is_open = False
        self._timeout_sets_after_open = 0
        self._timeout = None
        self._out = bytearray()
        self._lock = threading.Lock()
        self.awake = self.mode in ("grbl", "mute_homing")
        self._mute_until = 0.0
        self._dtr = self._rts = None
        FakeSerial.instances.append(self)

    # «needs_lines»: USB nativo que sólo transmite con DTR activa y RTS cableada al reinicio
    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, v):
        self._dtr = v
        self._lines_changed()

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, v):
        self._rts = v
        self._lines_changed()

    def _lines_changed(self) -> None:
        if self.mode == "needs_lines" and self.is_open and self._dtr and not self._rts and not self.awake:
            self.awake = True
            with self._lock:
                self._out.extend(b"\r\nGrbl 1.1f ['$' for help]\r\n")

    @property
    def timeout(self):
        return self._timeout

    @timeout.setter
    def timeout(self, value):
        if self.is_open:
            self._timeout_sets_after_open += 1  # en Windows reconfigura el puerto entero
        self._timeout = value

    def open(self) -> None:
        self.is_open = True
        if self.mode == "garbage":
            self._out.extend(b"\xf0\x80\x12\xfe~~\x00x\xf8")

    def close(self) -> None:
        self.is_open = False

    def reset_input_buffer(self) -> None:
        pass

    @property
    def in_waiting(self) -> int:
        with self._lock:
            if self._mute_until and time.monotonic() >= self._mute_until:
                self._mute_until = 0.0
                self._out.extend(b"ok\r\n")       # fin del homing
            return len(self._out)

    def read(self, n: int = 1) -> bytes:
        with self._lock:
            data = bytes(self._out[:n])
            del self._out[:n]
            return data

    def write(self, data: bytes) -> int:
        with self._lock:
            if self.mode == "needs_reset" and (b"\x18" in data or b"\n" in data) and not self.awake:
                self.awake = True
                self._out.extend(b"\r\nGrbl 1.1h ['$' for help]\r\n")
            if not self.awake or self.mode in ("silent", "garbage"):
                return len(data)
            if self.mode == "mute_homing" and b"$H\n" in data:
                self._mute_until = time.monotonic() + 1.5  # como GRBL de 8 bits: mudo durante el homing
                return len(data)
            if self._mute_until:
                return len(data)
            for b in data:
                if b == ord("?"):
                    self._out.extend(b"<Idle|MPos:0.000,0.000,0.000|FS:0,0>\r\n")
            text = data.decode("ascii", "ignore")
            for _ in range(text.count("\n")):
                if text.strip():
                    self._out.extend(b"ok\r\n")
        return len(data)


@pytest.fixture
def fake_serial(monkeypatch):
    mod = types.ModuleType("serial")
    mod.Serial = FakeSerial  # type: ignore[attr-defined]
    mod.SerialException = OSError  # type: ignore[attr-defined]
    mod.EIGHTBITS, mod.PARITY_NONE, mod.STOPBITS_ONE = 8, "N", 1  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "serial", mod)
    FakeSerial.instances.clear()
    monkeypatch.setattr(ctl_mod, "WAKE_AFTER_S", 0.3)
    monkeypatch.setattr(ctl_mod, "FLIP_LINES_AFTER_S", 0.5)
    monkeypatch.setattr(ctl_mod, "LINES_STEP_S", 0.5)
    monkeypatch.setattr(ctl_mod, "WAKE_AFTER_FLIP_S", 0.2)
    return FakeSerial


def _controller() -> MachineController:
    profile = demo_profile()
    profile.status_poll_ms = 50
    return MachineController(profile, transport_factory=lambda p: SerialTransport("COM6", 115200))


def test_serial_connects_without_reconfiguring_port(fake_serial):
    fake_serial.mode = "grbl"
    ctl = _controller()
    try:
        assert ctl.connect()
        assert wait_for(lambda: ctl.status().conn == ConnState.CONNECTED, 5)
        ser = fake_serial.instances[-1]
        assert ser.port == "COM6" and ser.dtr is False and ser.rts is False
        wait_for(lambda: False, 0.3)
        assert ser._timeout_sets_after_open == 0
    finally:
        ctl.shutdown()


def test_board_that_needs_reset_before_answering(fake_serial):
    fake_serial.mode = "needs_reset"
    ctl = _controller()
    try:
        assert ctl.connect()
        assert wait_for(lambda: ctl.status().conn == ConnState.CONNECTED, 6)
    finally:
        ctl.shutdown()


def test_board_that_needs_other_dtr_rts(fake_serial):
    fake_serial.mode = "needs_lines"
    ctl = _controller()
    try:
        assert ctl.connect()
        assert wait_for(lambda: ctl.status().conn == ConnState.CONNECTED, 6)
        events = []
        wait_for(lambda: False, 0.2)
        while not ctl.events.empty():
            events.append(ctl.events.get_nowait())
        assert any(getattr(e, "code", "") == "serial_lines" for e in events)
    finally:
        ctl.shutdown()


@pytest.mark.parametrize("mode, expected", [("silent", "ningún dato"), ("garbage", "bytes que no se reconocen")])
def test_no_answer_explains_why(fake_serial, monkeypatch, mode, expected):
    fake_serial.mode = mode
    monkeypatch.setattr(ctl_mod, "CONNECT_TIMEOUT_S", 1.0)
    ctl = _controller()
    try:
        assert ctl.connect()
        assert wait_for(lambda: ctl.status().conn == ConnState.DISCONNECTED and bool(ctl.status().error), 6)
        assert expected in ctl.status().error
    finally:
        ctl.shutdown()


def test_homing_without_status_reports_does_not_drop_connection(fake_serial, monkeypatch):
    fake_serial.mode = "mute_homing"
    monkeypatch.setattr(ctl_mod, "STATUS_TIMEOUT_S", 0.5)
    ctl = _controller()
    try:
        assert ctl.connect()
        assert wait_for(lambda: ctl.status().conn == ConnState.CONNECTED, 5)
        assert wait_for(lambda: ctl.queue_stats == (0, 0, 0), 3)
        assert ctl.home()
        wait_for(lambda: False, 2.2)                     # 1,5 s mudo > STATUS_TIMEOUT_S
        assert ctl.status().conn == ConnState.CONNECTED, ctl.status().error
        assert wait_for(lambda: ctl.status().homed, 2)
        assert ctl.queue_stats == (0, 0, 0)
    finally:
        ctl.shutdown()


def test_grbl_09_status_report():
    rep = parse_line("<Idle,MPos:5.000,6.000,0.000,WPos:1.000,2.000,0.000,Buf:0,RX:0>")
    assert rep.state == "Idle"
    assert rep.mpos == (5.0, 6.0, 0.0) and rep.wpos == (1.0, 2.0, 0.0)
