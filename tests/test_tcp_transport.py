from conftest import wait_for

from openlaser.devices.profile import TransportKind, demo_profile
from openlaser.devices.transport import TcpTransport, _strip_telnet, create_transport
from openlaser.machine.controller import MachineController
from openlaser.machine.states import ConnState
from openlaser.simulator.grbl_simulator import GrblSimulator
from openlaser.simulator.tcp_server import SimulatorServer


def test_controller_over_tcp():
    server = SimulatorServer("127.0.0.1", 0, GrblSimulator(speed_factor=40)).start()
    try:
        prof = demo_profile()
        prof.transport = TransportKind.TCP
        prof.host, prof.tcp_port = "127.0.0.1", server.address[1]
        prof.status_poll_ms = 30
        assert isinstance(create_transport(prof), TcpTransport)
        ctl = MachineController(prof)
        assert ctl.connect()
        assert wait_for(lambda: ctl.status().conn == ConnState.CONNECTED, 5)
        ctl.home()
        assert wait_for(lambda: ctl.status().homed and ctl.status().grbl.value == "Idle", 5)
        ctl.jog(dx=3, dy=4, feed=6000)
        assert wait_for(lambda: ctl.status().mpos[:2] == (3.0, 4.0), 5)
        ctl.shutdown()
        assert ctl.status().conn == ConnState.DISCONNECTED
    finally:
        server.stop()


def test_server_close_is_detected_as_lost():
    server = SimulatorServer("127.0.0.1", 0, GrblSimulator(speed_factor=40)).start()
    prof = demo_profile()
    prof.transport = TransportKind.TCP
    prof.host, prof.tcp_port = "127.0.0.1", server.address[1]
    ctl = MachineController(prof)
    ctl.connect()
    assert wait_for(lambda: ctl.status().conn == ConnState.CONNECTED, 5)
    server.stop()
    assert wait_for(lambda: ctl.status().conn == ConnState.DISCONNECTED, 8)
    assert ctl.status().error


def test_telnet_negotiation_is_stripped():
    assert _strip_telnet(b"\xff\xfb\x01ok\r\n") == b"ok\r\n"
    assert _strip_telnet(b"plain") == b"plain"
