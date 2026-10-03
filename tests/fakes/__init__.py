"""Fake hardware modules so hal/ drivers can be unit-tested off-device.

Call ``install()`` before importing anything from ``hal``; it registers fake
``machine``, ``network`` and ``espnow`` modules in ``sys.modules`` (works on
CPython and MicroPython). Each fake records what the driver did so tests can
assert on register writes, SPI traffic and radio packets.
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
