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


def _image(n, size_code=4):
    # An ESP32 image header (0xE9 magic, flash size code in the high nibble) padded to n bytes.
    return bytes([0xE9, 3, 2, (size_code << 4) | 0xF]) + b"\0" * (n - 4)


def _table(*parts):
    # ESP-IDF partition entries: (label, type, subtype, offset, size); 0xFF ends the table.
    out = b""
    for label, typ, sub, at, size in parts:
        out += b"\xaa\x50" + bytes([typ, sub]) + at.to_bytes(4, "little") + size.to_bytes(4, "little")
        out += label.encode().ljust(16, b"\0") + b"\0" * 4
    return out + b"\xff" * (3072 - len(out))


def _qemu_run():
    _cpython("native/tools/qemu_run.py is a host tool (CPython)")
    return _load("native/tools/qemu_run.py", "qemu_run")


def test_qemu_layout_places_bootloader_table_and_app():
    q = _qemu_run()
    table = _table(("nvs", 1, 2, 0x9000, 0x6000), ("factory", 0, 0, 0x10000, 0x100000))
    flash, parts = q["layout"](_image(26112), table, _image(240000))
    assert flash == 16 << 20
    assert [(at, name) for at, _, name in parts] == [(0x1000, "bootloader"), (0x8000, "partitions"), (0x10000, "factory")]
    img = q["merge"](flash, parts)
    assert len(img) == 16 << 20 and img[0x1000] == 0xE9 and img[0x8000:0x8002] == b"\xaa\x50" and img[0x10000] == 0xE9
    assert img[0x9000] == 0xFF   # untouched flash reads as erased
    # Arduino's table has no factory app: the bootloader boots ota_0 while otadata is blank.
    ard = _table(("otadata", 1, 0, 0xE000, 0x2000), ("app0", 0, 0x10, 0x10000, 0x140000),
                 ("app1", 0, 0x11, 0x150000, 0x140000))
    assert q["layout"](_image(17568), ard, _image(364320))[1][2][2] == "app0"


def test_qemu_layout_rejects_what_would_not_boot():
    q = _qemu_run()
    table = _table(("factory", 0, 0, 0x10000, 0x100000))

    def why(boot, tab, app):
        try:
            q["layout"](boot, tab, app)
        except ValueError as e:
            return str(e)
        return None

    # The unoptimised ESP-IDF bootloader (44,400 bytes) ran past the table at 0x8000.
    assert "past the partition table" in why(_image(44400), table, _image(1000))
    assert why(_image(28672), table, _image(1000)) is None   # ends exactly at 0x8000
    assert "holds" in why(_image(26112), table, _image(0x100001))
    assert "4 MB flash" in why(_image(26112, 2), _table(("factory", 0, 0, 0x10000, 0x400000)), _image(1000))
    assert "not an ESP32 image" in why(b"\0" * 100, table, _image(1000))
    assert "no app partition" in why(_image(26112), _table(("nvs", 1, 2, 0x9000, 0x6000)), _image(1000))


def test_qemu_verdict_reads_the_boot_log():
    q = _qemu_run()
    V = q["Verdict"]
    v = V("HM hello")
    assert v.feed("rst:0x8 (TG1WDT_SYS_RESET),boot:0x12") is None   # resets before the app are tolerated
    assert v.feed("HM hello variant=x") == (True, "saw 'HM hello'")
    v = V("HM done")
    v.feed("HM hello variant=x")
    assert v.feed("rst:0x c (SW_CPU_RESET)")[0] is False
    assert V("HM done").feed("HM error what=axp202") == (False, "HM error what=axp202")
    assert V("HM done", allow_error=True).feed("HM error what=axp202") is None
    assert V("HM done").feed("Guru Meditation Error: Core  1 panic'ed (LoadProhibited)")[0] is False


def test_qemu_watch_reads_lines_from_a_pipe():
    q = _qemu_run()
    import io
    import os
    r, w = os.pipe()
    try:
        os.write(w, b"boot\r\nHM hel")
        os.write(w, b"lo variant=x\r\n")
        out, echoed = io.StringIO(), []
        assert q["watch"](r, q["Verdict"]("HM hello"), 5, out, echoed.append) == (True, "saw 'HM hello'")
        assert out.getvalue() == "boot\nHM hello variant=x\n" and echoed == ["HM hello variant=x"]
        os.write(w, b"HM error what=axp202\r\n")
        assert q["watch"](r, q["Verdict"]("HM done"), 5) == (False, "HM error what=axp202")
        assert q["watch"](r, q["Verdict"]("HM done"), 0.3)[0] is False   # times out on silence
        os.close(w)
        w = None
        assert q["watch"](r, q["Verdict"]("HM done"), 5) == (False, "QEMU exited")
    finally:
        os.close(r)
        if w is not None:
            os.close(w)
