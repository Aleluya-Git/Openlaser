"""Transportes de comunicación: simulador, USB/serie, TCP/Telnet y WebSocket.

Todos comparten la misma interfaz mínima (abrir, cerrar, escribir bytes y
leer líneas con tiempo límite). Las escrituras están protegidas por un
cerrojo para que la parada de emergencia pueda enviar ``0x18`` desde el hilo
de la interfaz sin intercalarse con una línea a medias.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from openlaser.devices.profile import DeviceProfile, TransportKind

_log = logging.getLogger(__name__)


class TransportError(ConnectionError):
    """Fallo de comunicación: el controlador debe pasar a estado seguro."""


class Transport(ABC):
    name = "transport"

    def __init__(self) -> None:
        self._write_lock = threading.Lock()
        self._rxbuf = bytearray()
        self._open = False
        self.rx_bytes = 0                 # bytes recibidos desde que se abrió (diagnóstico de conexión)
        self.rx_sample = bytearray()      # primeros bytes recibidos, para mostrarlos si no se entienden

    # --- interfaz pública ----------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        if self._open:
            return
        try:
            self._do_open()
        except TransportError:
            raise
        except Exception as exc:  # noqa: BLE001 - se normaliza a TransportError
            raise TransportError(str(exc)) from exc
        self._rxbuf.clear()
        self.rx_bytes = 0
        self.rx_sample = bytearray()
        self._open = True

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        try:
            self._do_close()
        except Exception:  # noqa: BLE001
            _log.exception("Error al cerrar transporte %s", self.name)

    def write(self, data: bytes) -> None:
        if not self._open:
            raise TransportError("Transporte cerrado")
        with self._write_lock:
            try:
                self._do_write(data)
            except TransportError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise TransportError(str(exc)) from exc

    def readline(self, timeout: float) -> str | None:
        """Devuelve una línea sin fin de línea, o ``None`` si vence el tiempo."""
        deadline = time.monotonic() + timeout
        while True:
            idx = self._rxbuf.find(b"\n")
            if idx >= 0:
                raw = bytes(self._rxbuf[:idx])
                del self._rxbuf[: idx + 1]
                return raw.decode("utf-8", "replace").rstrip("\r")
            if not self._open:
                raise TransportError("Transporte cerrado")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                chunk = self._do_read(min(remaining, 0.05))
            except TransportError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise TransportError(str(exc)) from exc
            if chunk:
                self._rxbuf.extend(chunk)
                self.rx_bytes += len(chunk)
                if len(self.rx_sample) < 64:
                    self.rx_sample.extend(chunk[: 64 - len(self.rx_sample)])

    # --- implementación ------------------------------------------------------
    @abstractmethod
    def _do_open(self) -> None: ...

    @abstractmethod
    def _do_close(self) -> None: ...

    @abstractmethod
    def _do_write(self, data: bytes) -> None: ...

    @abstractmethod
    def _do_read(self, timeout: float) -> bytes: ...


class SimulatorTransport(Transport):
    """Conecta en el mismo proceso con :class:`GrblSimulator`."""

    name = "simulator"

    def __init__(self, simulator: "object | None" = None, speed_factor: float = 1.0) -> None:
        super().__init__()
        from openlaser.simulator.grbl_simulator import GrblSimulator
        self.simulator: GrblSimulator = simulator or GrblSimulator(speed_factor=speed_factor)  # type: ignore[assignment]
        self._failed = False

    def fail(self) -> None:
        """Simula una desconexión física (cable USB retirado)."""
        self._failed = True

    def _check(self) -> None:
        if self._failed:
            raise TransportError("Dispositivo simulado desconectado")

    def _do_open(self) -> None:
        self._failed = False
        self.simulator.power_on()

    def _do_close(self) -> None:
        pass

    def _do_write(self, data: bytes) -> None:
        self._check()
        self.simulator.receive(data)

    def _do_read(self, timeout: float) -> bytes:
        self._check()
        lines = self.simulator.read_output()
        if not lines:
            time.sleep(min(timeout, 0.002))
            lines = self.simulator.read_output()
        if not lines:
            return b""
        return "".join(f"{ln}\r\n" for ln in lines).encode("ascii", "replace")


class SerialTransport(Transport):
    """USB/serie con pyserial.

    El tiempo de espera del puerto se fija una sola vez al abrir: cambiarlo en cada lectura obliga a
    pyserial a reconfigurar el puerto (``SetCommState`` en Windows) cientos de veces por segundo, y
    algunos adaptadores USB-serie (CH340, CP210x) pierden datos o no llegan a entregar la respuesta.
    """

    name = "serial"

    def __init__(self, port: str, baud_rate: int = 115200, dtr: bool = False, rts: bool = False) -> None:
        super().__init__()
        self.port, self.baud_rate, self.dtr, self.rts = port, baud_rate, dtr, rts
        self._ser = None

    def _do_open(self) -> None:
        try:
            import serial  # pyserial
        except ImportError as exc:
            raise TransportError("pyserial no está instalado") from exc
        if not self.port:
            raise TransportError("No se ha indicado el puerto serie")
        ser = serial.Serial()
        ser.port = self.port.strip()
        ser.baudrate = self.baud_rate
        ser.bytesize = serial.EIGHTBITS
        ser.parity = serial.PARITY_NONE
        ser.stopbits = serial.STOPBITS_ONE
        ser.xonxoff = False
        ser.rtscts = False
        ser.dsrdtr = False
        ser.timeout = 0            # lectura no bloqueante: la espera la hace _do_read
        ser.write_timeout = 2.0
        ser.dtr = self.dtr
        ser.rts = self.rts
        try:
            ser.open()
        except serial.SerialException as exc:
            text = str(exc)
            if "PermissionError" in text or "Access is denied" in text or "Acceso denegado" in text:
                raise TransportError(f"{self.port}: el puerto está ocupado por otro programa ({text})") from exc
            raise TransportError(f"{self.port}: {text}") from exc
        try:
            ser.reset_input_buffer()
        except Exception:  # noqa: BLE001 - no todos los controladores lo admiten
            pass
        self._ser = ser

    def set_lines(self, dtr: bool, rts: bool) -> None:
        """Cambia DTR/RTS con el puerto abierto (para placas que necesitan otra combinación)."""
        if self._ser is not None:
            self._ser.dtr = dtr
            self._ser.rts = rts
        self.dtr, self.rts = dtr, rts

    def _do_close(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None

    def _do_write(self, data: bytes) -> None:
        assert self._ser is not None
        self._ser.write(data)

    def _do_read(self, timeout: float) -> bytes:
        ser = self._ser
        assert ser is not None
        end = time.monotonic() + max(0.0, timeout)
        while True:
            waiting = ser.in_waiting
            if waiting:
                return ser.read(waiting)
            if time.monotonic() >= end:
                return b""
            time.sleep(0.002)


class TcpTransport(Transport):
    """Conexión TCP en bruto o Telnet (ESP3D, FluidNC, grblHAL Ethernet...)."""

    name = "tcp"

    def __init__(self, host: str, port: int = 23, connect_timeout: float = 3.0) -> None:
        super().__init__()
        self.host, self.port, self.connect_timeout = host, port, connect_timeout
        self._sock: socket.socket | None = None

    def _do_open(self) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(0.05)
        self._sock = sock

    def _do_close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self._sock.close()
            self._sock = None

    def _do_write(self, data: bytes) -> None:
        assert self._sock is not None
        self._sock.sendall(data)

    def _do_read(self, timeout: float) -> bytes:
        assert self._sock is not None
        self._sock.settimeout(max(timeout, 0.001))
        try:
            data = self._sock.recv(4096)
        except (socket.timeout, TimeoutError):
            return b""
        if data == b"":
            raise TransportError("El dispositivo cerró la conexión TCP")
        return _strip_telnet(data)


def _strip_telnet(data: bytes) -> bytes:
    """Elimina secuencias de negociación Telnet (IAC) del flujo."""
    if b"\xff" not in data:
        return data
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0xFF and i + 1 < len(data):
            cmd = data[i + 1]
            if cmd in (0xFB, 0xFC, 0xFD, 0xFE) and i + 2 < len(data):
                i += 3
                continue
            if cmd == 0xFF:
                out.append(0xFF)
            i += 2
            continue
        out.append(b)
        i += 1
    return bytes(out)


class WebSocketTransport(Transport):
    name = "websocket"

    def __init__(self, url: str, connect_timeout: float = 3.0) -> None:
        super().__init__()
        self.url, self.connect_timeout = url, connect_timeout
        self._ws = None

    def _do_open(self) -> None:
        try:
            from websockets.sync.client import connect
        except ImportError as exc:
            raise TransportError("websockets (>=12) no está instalado") from exc
        self._ws = connect(self.url, open_timeout=self.connect_timeout, close_timeout=1.0)

    def _do_close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None

    def _do_write(self, data: bytes) -> None:
        assert self._ws is not None
        self._ws.send(data)

    def _do_read(self, timeout: float) -> bytes:
        assert self._ws is not None
        try:
            msg = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return b""
        except Exception as exc:  # ConnectionClosed y similares
            raise TransportError(f"WebSocket cerrado: {exc}") from exc
        if isinstance(msg, str):
            return msg.encode("utf-8")
        return bytes(msg)


@dataclass
class PortInfo:
    device: str
    description: str
    hwid: str


def list_serial_ports() -> list[PortInfo]:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [PortInfo(p.device, p.description or "", p.hwid or "") for p in sorted(list_ports.comports())]


def create_transport(profile: DeviceProfile, simulator: object | None = None) -> Transport:
    if profile.transport == TransportKind.SERIAL:
        return SerialTransport(profile.port, profile.baud_rate, profile.serial_dtr, profile.serial_rts)
    if profile.transport == TransportKind.TCP:
        return TcpTransport(profile.host, profile.tcp_port)
    if profile.transport == TransportKind.WEBSOCKET:
        return WebSocketTransport(profile.websocket_url)
    if simulator is None:
        simulator = make_simulator(profile.firmware)  # el simulador imita el firmware del perfil
    return SimulatorTransport(simulator)


def make_simulator(firmware: str, speed_factor: float = 1.0) -> object:
    """Simulador del firmware: GRBL/grblHAL/FluidNC/Smoothieware (GrblSimulator) o Marlin."""
    if firmware == "marlin":
        from openlaser.simulator.marlin_simulator import MarlinSimulator
        return MarlinSimulator(speed_factor=speed_factor)
    from openlaser.simulator.grbl_simulator import FIRMWARES, GrblSimulator
    return GrblSimulator(speed_factor=speed_factor, firmware=firmware if firmware in FIRMWARES else "grbl")
