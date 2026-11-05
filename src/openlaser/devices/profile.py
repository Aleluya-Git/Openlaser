"""Perfiles de dispositivo (máquina + conexión)."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any


class OriginCorner(Enum):
    FRONT_LEFT = "front_left"
    REAR_LEFT = "rear_left"
    FRONT_RIGHT = "front_right"
    REAR_RIGHT = "rear_right"


class TransportKind(Enum):
    SIMULATOR = "simulator"
    SERIAL = "serial"
    TCP = "tcp"
    WEBSOCKET = "websocket"


COMMON_BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400, 250000, 500000, 921600)


@dataclass
class DeviceProfile:
    name: str = "Simulador GRBL (demo)"
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    firmware: str = "grbl"
    transport: TransportKind = TransportKind.SIMULATOR
    port: str = ""
    baud_rate: int = 115200
    # Señales DTR/RTS al abrir el puerto. Desactivadas por defecto (como LaserGRBL/LightBurn): en muchas
    # placas una de ellas va al reinicio del microcontrolador y, activa, lo deja parado sin responder.
    serial_dtr: bool = False
    serial_rts: bool = False
    host: str = "192.168.0.1"
    tcp_port: int = 23
    websocket_url: str = "ws://192.168.0.1:81/"
    sd_http_port: int = 80          # envío a tarjeta SD por HTTP (FluidNC / ESP3D)
    sd_endpoint: str = "/upload"
    sd_folder: str = "/"
    width_mm: float = 400.0
    height_mm: float = 400.0
    origin: OriginCorner = OriginCorner.FRONT_LEFT
    invert_x: bool = False
    invert_y: bool = False
    homing_enabled: bool = True
    enforce_limits: bool = True
    z_axis: bool = False
    z_min_mm: float = -50.0
    z_max_mm: float = 0.0
    a_axis: bool = False
    rotary_enabled: bool = False      # compatibilidad: el estado real está en ``rotary["enabled"]``
    rotary: dict[str, Any] = field(default_factory=dict)     # RotaryConfig.to_dict()
    cylinder: dict[str, Any] = field(default_factory=dict)   # CylinderCorrection.to_dict()
    s_max: float = 1000.0
    laser_mode: bool = True
    units: str = "mm"
    jog_feed_mm_min: float = 3000.0
    frame_feed_mm_min: float = 3000.0
    max_feed_mm_min: float = 6000.0
    rapid_mm_min: float = 6000.0
    start_commands: list[str] = field(default_factory=list)
    end_commands: list[str] = field(default_factory=lambda: ["M5"])
    pause_commands: list[str] = field(default_factory=list)
    resume_commands: list[str] = field(default_factory=list)
    rx_buffer_size: int = 127
    fw_s_max: float = 0.0           # Marlin/Smoothieware: S máximo del firmware (0 = 255 en Marlin, 1 en Smoothie)
    rx_buffer_auto: bool = True     # usar el tamaño que informa el controlador en $I ([OPT:…,búfer])
    status_poll_ms: int = 200
    reset_on_connect: bool = False
    demo: bool = False

    # --- serialización -------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for k, v in list(data.items()):
            if isinstance(v, Enum):
                data[k] = v.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceProfile":
        known = {f.name: f for f in fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "transport":
                value = TransportKind(value)
            elif key == "origin":
                value = OriginCorner(value)
            kwargs[key] = value
        return cls(**kwargs)

    # --- utilidades ----------------------------------------------------------
    def s_from_percent(self, percent: float) -> float:
        percent = max(0.0, min(100.0, percent))
        return round(self.s_max * percent / 100.0, 3)

    def connection_label(self) -> str:
        if self.transport == TransportKind.SERIAL:
            return f"{self.port or '?'} @ {self.baud_rate}"
        if self.transport == TransportKind.TCP:
            return f"tcp://{self.host}:{self.tcp_port}"
        if self.transport == TransportKind.WEBSOCKET:
            return self.websocket_url
        return "simulador"


def demo_profile() -> DeviceProfile:
    """Perfil GRBL simulado incluido como dato de demostración."""
    return DeviceProfile(
        name="Simulador GRBL (demo)", id="demo-sim", transport=TransportKind.SIMULATOR,
        width_mm=400.0, height_mm=400.0, homing_enabled=True, z_axis=True, a_axis=True,
        s_max=1000.0, laser_mode=True, demo=True,
    )
