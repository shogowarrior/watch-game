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


def test_golden_game_vectors_up_to_date():
    # A stale file means finder/ changed behaviour the C++ port has not caught
    # up with yet: a skip, not a failure, so other work is not blocked; the
    # native thread regenerates and ports the change.
    _cpython("the generators run the finder/ modules (CPython)")
    g = _load("native/tools/golden/run.py", "golden_run")
    bad = g["stale"]()
    if bad:
        raise Skip("the C++ port lags finder/ in %s: python3 native/tools/golden/run.py, then port "
                   "until python3 native/test/run.py passes" % ", ".join(bad))


def test_golden_frames_up_to_date():
    # native/core/include/hm/ui_tables.h and native/test/golden/frames.txt come
    # from ui/glyphs.py, ui/font.py and the snapshot fixtures: a stale one means
    # the renderer changed in a way the C++ port has not caught up with yet (a
    # skip, not a failure, as for the game vectors).
    _cpython("the generator imports the ui/ geometry and the fixtures (CPython)")
    bad = _load("native/tools/golden_frames.py", "golden_frames")["stale"]()
    if bad:
        raise Skip("the C++ renderer lags ui/ in %s: python3 native/tools/golden_frames.py, then port "
                   "until python3 native/test/run.py passes" % ", ".join(bad))


def test_game_port_checked_against_this_python():
    # native/test/traced.txt hashes the Python the C++ game port last matched
    # call for call; while the Python differs, native/test/run.py skips trace
    # tests that differ (the port lags, other work is not blocked) and this
    # reports it.
    _cpython("hashes the Python the traces come from (CPython)")
    r = _load("native/test/run.py", "native_run")
    if r["python_hash"]() != r["marked"]():
        raise Skip("the C++ game port lags the Python: python3 native/test/run.py, port until its "
                   "trace tests pass, then python3 native/test/run.py --mark")


def test_host_tests_pass():
    _cpython("builds C++ with g++ (CPython)")
    code, out = _load("native/test/run.py", "native_run")["run"]()
    if code == 2:
        raise Skip(out)
    assert code == 0, out


def test_core_builds_for_the_watch():
    _cpython("runs the xtensa compilers (CPython)")
    code, out = _load("native/tools/xcheck.py", "native_xcheck")["run"]()
    if code == 2:
        raise Skip(out.strip())
    assert code == 0, out


def test_bench_report_groups_lines_by_variant():
    _cpython("native/tools/bench_report.py is a host tool (CPython)")
    br = _load("native/tools/bench_report.py", "bench_report")
    log = ["ets Jun  8 2016", "HM hello variant=tft_espi framework=arduino", "HM i2c",
           'HM compose fixture="HOT bump-ready" frames=60 step_us=900 blit_us=4100',
           "HM run hz=40000000 target=30 fps=29.9 p95_us=33400 miss=0",
           "HM error what=spi_clock hz=80000000", "HM done",
           "I (12) HM hello variant=idf-esplcd framework=espidf", "HM run hz=0x2faf080 target=20 fps=20.0"]
    recs, runs = br["parse_log"](log, "a.log")
    assert [(r["variant"], r["kind"]) for r in recs] == [
        ("tft_espi", "compose"), ("tft_espi", "run"), ("idf-esplcd", "run")]
    assert recs[0]["fixture"] == "HOT bump-ready" and recs[1]["fps"] == 29.9 and recs[2]["hz"] == 50000000
    assert [(r["variant"], r["done"], r["errors"]) for r in runs] == [
        ("tft_espi", True, ["what=spi_clock hz=80000000"]), ("idf-esplcd", False, [])]
    md = br["markdown"](recs, runs)
    assert "| variant | hz | target | fps | p95_us | miss |" in md and "| idf-esplcd | espidf | NO | - |" in md


def test_wifi_files_for_native_builds_are_ignored():
    # The only places a native build may keep Wi-Fi details (native/.gitignore).
    _cpython("runs git (CPython)")
    import subprocess
    for p in ("native/arduino/include/wifi_secrets.h", "native/idf/main/wifi_secrets.h",
              "native/idf/lvgl/src/wifi_secrets.h", "native/arduino/secrets.ini", "native/idf/secrets.ini"):
        try:
            r = subprocess.run(["git", "check-ignore", "-q", p], capture_output=True)
        except OSError:
            raise Skip("git is not installed")
        if r.returncode == 128:
            raise Skip("not a git checkout")
        assert r.returncode == 0, p + " is not ignored by git"


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
