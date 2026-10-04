"""Native ports (native/): generated headers, golden field palettes, host tests.

The C++ core reads constants generated from finder/tuning.py and ui/field.py
and must reproduce the MicroPython renderer's palettes frame for frame
(native/README.md).
"""

import sys

from tests import Skip


def _cpython(why):
    if sys.implementation.name != "cpython":
        raise Skip(why)


def _load(path, name):
    """Run a native/ script as a module (native/ is not a package)."""
    ns = {"__name__": name, "__file__": path}
    with open(path) as f:
        exec(f.read(), ns)
    return ns


def test_headers_up_to_date():
    _cpython("the generator writes C++ headers (CPython)")
    import os
    g = _load("native/tools/gen_tuning_h.py", "gen_tuning_h")
    for name, gen in g["OUTPUTS"]:
        with open(os.path.join(g["INC"], name)) as f:
            assert f.read() == gen(), name + " is stale: run python3 native/tools/gen_tuning_h.py"


def test_golden_field_up_to_date():
    if sys.implementation.name == "cpython":
        raise Skip("the renderer needs framebuf (MicroPython)")
    g = _load("native/tools/golden_field.py", "golden_field")
    with open("native/test/golden_field.txt") as f:
        want = f.read()
    got = "\n".join(g["lines"]()) + "\n"
    assert got == want, ("native/test/golden_field.txt is stale: node tools/mpy/run.mjs "
                         "native/tools/golden_field.py > native/test/golden_field.txt")


def test_host_tests_pass():
    _cpython("builds C++ with g++ (CPython)")
    code, out = _load("native/test/run.py", "native_run")["run"]()
    if code == 2:
        raise Skip(out)
    assert code == 0, out


def test_capture_keeps_lines_until_done():
    # native/tools/capture.py on a pseudo-terminal: every line kept, CRs dropped,
    # partial lines held until their newline, stop at the --until text.
    _cpython("needs a pseudo-terminal (CPython)")
    import io
    import os
    cap = _load("native/tools/capture.py", "capture")
    master, slave = os.openpty()
    fd = cap["open_port"](os.ttyname(slave))
    try:
        os.write(master, b"boot junk\r\nHM hello variant=x\r\nHM run fps=3")
        os.write(master, b"0.0\r\nHM done\r\nafter\r\n")
        out, echoed = io.StringIO(), []
        assert cap["capture"](fd, out, 5, "HM done", echoed.append)
        assert out.getvalue() == "boot junk\nHM hello variant=x\nHM run fps=30.0\nHM done\n"
        assert echoed == ["HM hello variant=x", "HM run fps=30.0", "HM done"]
        assert not cap["capture"](fd, io.StringIO(), 0.3, "HM done")   # times out on silence
    finally:
        for f in (fd, master, slave):
            os.close(f)
