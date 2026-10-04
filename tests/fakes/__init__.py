"""Fake hardware modules so hal/ drivers can be unit-tested off-device.

Call ``install()`` before importing anything from ``hal``; it registers fake
``machine``, ``network`` and ``espnow`` modules in ``sys.modules`` (works on
CPython and MicroPython). Each fake records what the driver did so tests can
assert on register writes, SPI traffic and radio packets.

``install_socket()`` swaps in a fake ``socket`` for one test (hal/debuglink's
UDP sender) and returns the function that puts the old one back: CPython's
real socket must stay for the other tests. ``serial_port.Port`` stands in
for the USB serial port that hal/debuglink's ``SerialLink`` writes to.
"""

import sys


def install():
    from tests.fakes import machine, network, espnow
    sys.modules["machine"] = machine
    sys.modules["network"] = network
    sys.modules["espnow"] = espnow
    machine.reset_fakes()
    network.reset_fakes()
    espnow.reset_fakes()
    return machine


def install_socket():
    from tests.fakes import socket
    old = sys.modules.get("socket")
    sys.modules["socket"] = socket
    socket.reset_fakes()

    def restore():
        if old is None:
            del sys.modules["socket"]
        else:
            sys.modules["socket"] = old
    return restore
