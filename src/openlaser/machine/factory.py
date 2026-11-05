"""Elige el controlador según el firmware del perfil (Fase 6)."""
from __future__ import annotations

from typing import Callable

from openlaser.devices.profile import DeviceProfile
from openlaser.devices.transport import Transport
from openlaser.machine.controller import MachineController
from openlaser.safety.manager import SafetyManager


def create_controller(profile: DeviceProfile, safety: SafetyManager | None = None,
                      transport_factory: Callable[[DeviceProfile], Transport] | None = None) -> MachineController:
    """GRBL, grblHAL y FluidNC → :class:`MachineController`; Marlin y Smoothieware → :class:`HostController`."""
    from openlaser.machine.host_protocols import is_host_firmware
    if is_host_firmware(profile.firmware):
        from openlaser.machine.host_controller import HostController
        return HostController(profile, safety=safety, transport_factory=transport_factory)
    return MachineController(profile, safety=safety, transport_factory=transport_factory)


def matches(controller: MachineController, profile: DeviceProfile) -> bool:
    from openlaser.machine.host_controller import HostController
    from openlaser.machine.host_protocols import is_host_firmware
    if is_host_firmware(profile.firmware):
        return isinstance(controller, HostController) and controller.protocol.id == profile.firmware
    return not isinstance(controller, HostController)
