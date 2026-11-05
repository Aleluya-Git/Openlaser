"""Expone el simulador GRBL por TCP (estilo Telnet) para probar conexiones de red.

Uso::

    python -m openlaser.simulator.tcp_server --port 2323

y en la aplicación cree un perfil TCP con host 127.0.0.1 y puerto 2323.
"""
from __future__ import annotations

import argparse
import socket
import threading
import time

from openlaser.simulator.grbl_simulator import GrblSimulator


class SimulatorServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 2323, simulator: GrblSimulator | None = None) -> None:
        self.simulator = simulator or GrblSimulator()
        self._srv = socket.create_server((host, port))
        self._srv.settimeout(0.2)
        self.address = self._srv.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="grbl-sim-tcp", daemon=True)

    def start(self) -> "SimulatorServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(2.0)
        self._srv.close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self._srv.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            self._handle(conn)

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(0.01)
        self.simulator.power_on()
        with conn:
            while not self._stop.is_set():
                try:
                    data = conn.recv(4096)
                    if data == b"":
                        return
                    self.simulator.receive(data)
                except (socket.timeout, TimeoutError):
                    pass
                except OSError:
                    return
                out = self.simulator.read_output()
                if out:
                    try:
                        conn.sendall("".join(f"{ln}\r\n" for ln in out).encode("ascii", "replace"))
                    except OSError:
                        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulador GRBL por TCP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2323)
    parser.add_argument("--speed", type=float, default=1.0, help="Factor de velocidad de la simulación")
    args = parser.parse_args()
    server = SimulatorServer(args.host, args.port, GrblSimulator(speed_factor=args.speed)).start()
    print(f"Simulador GRBL escuchando en {server.address[0]}:{server.address[1]} (Ctrl+C para salir)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
