"""Configuración común de pruebas: rutas y utilidades con el simulador."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OPENLASER_HOME", str(ROOT / ".pytest_home"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openlaser.devices.profile import demo_profile  # noqa: E402
from openlaser.devices.transport import SimulatorTransport  # noqa: E402
from openlaser.machine.controller import MachineController  # noqa: E402
from openlaser.machine.states import ConnState, GrblState  # noqa: E402
from openlaser.simulator.grbl_simulator import GrblSimulator  # noqa: E402


def wait_for(pred: Callable[[], bool], timeout: float = 5.0, step: float = 0.01) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(step)
    return pred()


class Rig:
    def __init__(self, speed: float = 40.0, **sim_kwargs: object) -> None:
        self.sim = GrblSimulator(speed_factor=speed, **sim_kwargs)  # type: ignore[arg-type]
        self.profile = demo_profile()
        self.profile.status_poll_ms = 30
        self.transport = SimulatorTransport(self.sim)
        self.ctl = MachineController(self.profile, transport_factory=lambda p: self.transport)

    def connect(self) -> None:
        assert self.ctl.connect()
        assert wait_for(lambda: self.ctl.status().conn == ConnState.CONNECTED), "no conecta"
        assert wait_for(lambda: self.ctl.queue_stats == (0, 0, 0)), "cola de arranque no vacía"

    def home(self) -> None:
        assert self.ctl.home()
        assert wait_for(lambda: self.ctl.status().homed and self.ctl.status().grbl == GrblState.IDLE)

    def idle(self, timeout: float = 5.0) -> bool:
        """Espera a que la petición se procese y la máquina vuelva a reposo."""
        time.sleep(0.12)

        def done() -> bool:
            snap = self.sim.snapshot()
            return (self.ctl._inbox.empty() and self.ctl.queue_stats == (0, 0, 0)
                    and snap["state"] == "Idle" and not snap["planner"]
                    and self.ctl.status().grbl == GrblState.IDLE)

        ok = wait_for(done, timeout)
        time.sleep(0.08)  # al menos un informe de estado nuevo
        return ok

    def drain(self) -> list[object]:
        out = []
        while not self.ctl.events.empty():
            out.append(self.ctl.events.get_nowait())
        return out

    def close(self) -> None:
        self.ctl.shutdown()


@pytest.fixture
def rig():
    r = Rig()
    yield r
    r.close()


@pytest.fixture
def homed_rig(rig):
    rig.connect()
    rig.home()
    return rig


# ---------------------------------------------------------------------------
# Salida limpia con Qt: al terminar el intérprete, la destrucción de objetos Qt
# en orden arbitrario puede abortar el proceso *después* de que todas las
# pruebas hayan pasado (el resumen dice «passed» pero el código de salida no es
# 0). Si se cargó PySide6, se sale directamente con el resultado real de pytest.
_EXIT: dict[str, int | None] = {"status": None}


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001
    _EXIT["status"] = int(exitstatus)


def pytest_unconfigure(config):  # noqa: ANN001
    status = _EXIT["status"]
    if status is None or "PySide6.QtWidgets" not in sys.modules or os.environ.get("OPENLASER_QT_HARD_EXIT", "1") != "1":
        return
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        # TerminateProcess no ejecuta la descarga de DLL (DLL_PROCESS_DETACH) de Qt,
        # que es donde se produce la violación de acceso en Windows. Los tipos se
        # declaran explícitamente: el pseudo-handle es un puntero de 64 bits.
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.TerminateProcess.restype = wintypes.BOOL
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), status)
    os._exit(status)
