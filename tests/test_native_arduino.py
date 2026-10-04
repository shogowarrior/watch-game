"""native/arduino host checks that need only g++ (the env builds need PlatformIO).

The radio-pingpong env's pinger runs beside tools/radio_pingpong.py's own
classes and must send the same bytes and report the same numbers
(native/arduino/test/pingpong_host.py).
"""

import sys

from tests import Skip


def test_pingpong_matches_python():
    if sys.implementation.name != "cpython":
        raise Skip("builds C++ with g++ (CPython)")
    path = "native/arduino/test/pingpong_host.py"
    ns = {"__name__": "pingpong_host", "__file__": path}
    with open(path) as f:
        exec(f.read(), ns)
    code, out = ns["run"]()
    if code == 2:
        raise Skip(out)
    assert code == 0, out
