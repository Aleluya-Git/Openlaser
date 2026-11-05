"""Registro de adaptadores de controlador.

Cada firmware se integra mediante un adaptador que declara sus capacidades y
crea el controlador de máquina. ``grbl`` (GRBL 1.1 o posterior), ``grblhal`` y
``fluidnc`` (Fase 5) comparten el controlador GRBL con un dialecto propio
(:mod:`openlaser.machine.dialects`); Smoothieware y Marlin necesitan un
adaptador propio. Ruida, Trocen y los controladores galvo usan protocolos
propietarios: sólo se admitirán mediante plugins basados en documentación
autorizada por el fabricante, nunca por ingeniería inversa.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from openlaser.devices.profile import DeviceProfile
from openlaser.i18n import N_


class MachineBackend(Protocol):
    def connect(self) -> object: ...
    def disconnect(self) -> object: ...
    def emergency_stop(self) -> object: ...


@dataclass(frozen=True)
class ControllerAdapter:
    id: str
    name: str
    status: str  # "available" | "planned"
    capabilities: frozenset[str] = field(default_factory=frozenset)
    factory: Callable[[DeviceProfile], MachineBackend] | None = None
    notes: str = ""


_REGISTRY: dict[str, ControllerAdapter] = {}


def register(adapter: ControllerAdapter) -> None:
    _REGISTRY[adapter.id] = adapter


def get(adapter_id: str) -> ControllerAdapter | None:
    return _REGISTRY.get(adapter_id)


def adapters() -> list[ControllerAdapter]:
    return sorted(_REGISTRY.values(), key=lambda a: (a.status != "available", a.name))


def _grbl_factory(profile: DeviceProfile) -> MachineBackend:
    from openlaser.machine.controller import MachineController
    return MachineController(profile)


GRBL_CAPS = frozenset({"jog", "home", "realtime_overrides", "laser_mode", "status_report", "settings", "door"})

register(ControllerAdapter("grbl", "GRBL 1.1+", "available", GRBL_CAPS, _grbl_factory))
register(ControllerAdapter(
    "grblhal", "grblHAL", "available", GRBL_CAPS | {"large_rx_buffer", "homed_report", "estop_input"},
    _grbl_factory, N_("Búfer de recepción grande (leído de $I), alarmas 10–17, estado «Tool» y homing informado (H:).")))
register(ControllerAdapter(
    "fluidnc", "FluidNC", "available", (GRBL_CAPS - {"settings"}) | {"yaml_config", "sd_upload", "fw_messages"},
    _grbl_factory, N_("Serie, Telnet o WebSocket. Configuración en YAML (parámetros $n de sólo lectura); envío "
                      "a la tarjeta SD por HTTP.")))


def _host_factory(profile: DeviceProfile) -> MachineBackend:
    from openlaser.machine.host_controller import HostController
    return HostController(profile)


HOST_CAPS = frozenset({"jog", "home", "status_report", "host_pause"})
register(ControllerAdapter(
    "marlin", "Marlin", "available", HOST_CAPS | {"laser_inline", "stationary_fire"}, _host_factory,
    N_("Experimental. Envío-respuesta, estado deducido (M114/M400), láser en línea (M3 I), S 0–255; requiere "
       "LASER_FEATURE y, para Stop inmediato, EMERGENCY_PARSER.")))
register(ControllerAdapter(
    "smoothie", "Smoothieware", "available", HOST_CAPS | {"realtime_status"}, _host_factory,
    N_("Experimental. grbl_mode: informe «?», $H/$X; envío-respuesta; el láser sólo emite en G1 (S 0–1).")))
for _id, _name, _note in [
    ("ruida", "Ruida", N_("Protocolo propietario: sólo mediante plugin autorizado.")),
    ("trocen", "Trocen", N_("Protocolo propietario: sólo mediante plugin autorizado.")),
    ("galvo", "Galvo", N_("Requiere SDK del fabricante: sólo mediante plugin autorizado.")),
]:
    register(ControllerAdapter(_id, _name, "planned", frozenset(), None, _note))
