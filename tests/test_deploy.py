"""tools/deploy.py: what gets copied to the watch, and the mpremote commands.

A CPython host tool (argparse, subprocess): skipped under MicroPython. Every
Wi-Fi file here is a temporary one with made-up values (``WATCH_GAME_WIFI``
points at it), never a real one."""

from tests import Skip
from test_wifi_setup import SAVED, _Env, _hidden, _write


def _deploy():
    try:
        import tools.deploy as d                # CPython host tool only
    except ImportError:
        raise Skip("tools/deploy.py is a CPython host tool")
    return d


def _tree(tmp):
    import os
    for d in ("finder", "hal"):
        os.makedirs(os.path.join(tmp, d))
        open(os.path.join(tmp, d, "x.py"), "w").close()
    open(os.path.join(tmp, "main.py"), "w").close()


def test_deploy_copies_app_and_boot():
    d = _deploy()
    _, files, notes = d.collect()
    remote = [r for _, r in files]
    for f in ("app/__init__.py", "app/runtime.py", "app/imu_feed.py"):
        assert f in remote, f
    assert remote[-2:] == ["boot.py", "main.py"]   # last: a broken copy keeps the old ones
    assert "secrets.py" not in remote
    assert not any(n.startswith("missing") for n in notes), notes
    # ends with a hard reset: mpremote's soft-reset stays in the raw REPL, so
    # main.py would never start the game
    cmd = d.build_cmd(["mpremote"], "/dev/x", ["app"], [("a", "app/a.py")], False)
    assert cmd[-2:] == ["+", "reset"], cmd
    assert d.build_cmd(["mpremote"], "/dev/x", [], [], None, reset=False)[-1] != "reset"
    # --tele A writes /tele (main.py then logs to /log), --no-tele removes it
    cmd = d.build_cmd(["mpremote"], None, [], [], None, True, "A")
    assert cmd[-5:-2] == ["+", "exec", "f=open('/tele','w')\nf.write('A')\nf.close()"], cmd
    cmd = d.build_cmd(["mpremote"], None, [], [], None, False, False)
    assert cmd[-1].startswith("import os") and "os.remove('/tele')" in cmd[-1], cmd
    assert "/tele" not in " ".join(d.build_cmd(["mpremote"], None, [], [], None))
    # hard reset first, in its own mpremote call (a battery-started game runs
    # the hardware WDT, which would reboot the watch mid-copy)
    assert d.reset_cmd(["mpremote"], "/dev/x") == ["mpremote", "connect", "/dev/x", "reset"]


def test_collect_copies_the_wifi_file_only_when_given():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        wifi = tmp + "/wifi.py"
        _, files, notes = d.collect(tmp)
        assert "secrets.py" not in [r for _, r in files]
        _, files, notes = d.collect(tmp, wifi)                 # --secrets alone: a warning
        assert (wifi, "secrets.py") in files
        assert any(n.startswith("WARNING") for n in notes), notes
        _, files, notes = d.collect(tmp, wifi, debug=True)     # debug mode needs it: no warning
        remote = [r for _, r in files]
        assert (wifi, "secrets.py") in files and remote[-1] == "main.py"
        assert any("debug mode" in n for n in notes) and not any("WARNING" in n for n in notes)


def test_debug_writes_and_removes_its_files():
    import json
    d = _deploy()
    assert d.debug_config("A") == '{"dev": "A", "link": "usb"}'
    cfg = d.debug_config("B", True, "192.168.1.23")
    assert cfg == '{"dev": "B", "link": "wifi", "host": "192.168.1.23", "port": 47268}'
    assert json.loads(d.debug_config("A", True)) == {"dev": "A", "link": "wifi", "port": 47268}
    cmd = d.build_cmd(["mpremote"], None, [], [("s", "secrets.py")], None, True, None, cfg)
    usb = d.build_cmd(["mpremote"], None, [], [("s", "secrets.py")], None, True, None,
                      d.debug_config("A"))       # --debug A --secrets
    assert not [c for c in cmd + usb if "os.remove" in c], (cmd, usb)   # the copy is kept
    i = cmd.index("exec", 3)
    assert cmd[i - 1] == "+" and cmd[-2:] == ["+", "reset"], cmd
    code = cmd[i + 1]                            # the watch runs this: /debug gets the JSON
    fs = {}

    class F:
        def __init__(self, path, mode):
            self.path = path

        def write(self, text):
            fs[self.path] = text

        def close(self):
            pass
    exec(code, {"open": F})
    assert fs == {"/debug": cfg}
    cmd = d.build_cmd(["mpremote"], None, [], [], None, False, None, False)
    rm = [c for c in cmd if "os.remove" in c]
    assert len(rm) == 2 and "os.remove('/debug')" in rm[0] and "os.remove('/secrets.py')" in rm[1]
    assert "/debug" not in " ".join(d.build_cmd(["mpremote"], None, [], [], None))


def _main(d, argv, root, wifi=None):
    """``d.main(argv)`` on the tree ``root`` with the saved Wi-Fi file at
    ``wifi`` (a temporary path) -> (exit code, printed text)."""
    import io
    import sys
    old = (d.ROOT, sys.stdout, sys.stderr)
    out = io.StringIO()
    d.ROOT, sys.stdout, sys.stderr = root, out, out
    try:
        with _Env(WATCH_GAME_WIFI=wifi or root + "/no-wifi-saved.py"):
            rc = d.main(argv)
    except SystemExit as e:
        rc = e.code
    finally:
        d.ROOT, sys.stdout, sys.stderr = old
    return rc, out.getvalue()


def test_debug_over_usb_copies_nothing_secret():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        wifi = tmp + "/wifi.py"
        _write(wifi, SAVED)
        _write(tmp + "/secrets.py", SAVED)
        rc, out = _main(d, ["-n", "--debug", "A"], tmp, wifi)
        assert rc == 0, out
        assert "over the USB cable" in out and '{"dev": "A", "link": "usb"}' in out
        assert ":secrets.py" not in out and wifi not in out and _hidden(out), out
        assert "os.remove('/secrets.py')" in out    # an earlier --wifi deploy's copy


def test_debug_over_wifi_copies_the_saved_file():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        wifi = tmp + "/wifi.py"
        _write(wifi, SAVED)
        _write(tmp + "/secrets.py", SAVED)                    # older one: the saved file wins
        rc, out = _main(d, ["-n", "--debug", "A", "--wifi", "--debug-host", "10.0.0.7"], tmp, wifi)
        assert rc == 0, out
        assert "watch A will send over Wi-Fi to 10.0.0.7:47268" in out
        assert "fs cp %s :secrets.py" % wifi in out and "WARNING" not in out, out
        assert '{"dev": "A", "link": "wifi", "host": "10.0.0.7", "port": 47268}' in out
        assert "old place" not in out and _hidden(out), out
        lan_ip = d.lan_ip
        d.lan_ip = lambda: None                   # no address found: the watch broadcasts
        try:
            rc, out = _main(d, ["-n", "--debug", "B", "--wifi"], tmp, wifi)
        finally:
            d.lan_ip = lan_ip
        assert rc == 0 and "broadcast" in out and "--debug-host" in out, out
        assert '{"dev": "B", "link": "wifi", "port": 47268}' in out


def test_debug_over_wifi_falls_back_to_the_repo_secrets_with_a_hint():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        legacy = tmp + "/secrets.py"
        _write(legacy, SAVED)
        rc, out = _main(d, ["-n", "--debug", "A", "--wifi", "--debug-host", "10.0.0.7"], tmp)
        assert rc == 0, out
        assert "fs cp %s :secrets.py" % legacy in out and "old place" in out, out
        assert "python3 tools/wifi_setup.py" in out and _hidden(out)


def test_debug_over_wifi_without_a_file_says_what_to_run():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        rc, out = _main(d, ["-n", "--debug", "A", "--wifi", "--debug-host", "10.0.0.7"], tmp)
        assert rc == 2 and out.count("\n") == 1, out          # one line
        assert "run python3 tools/wifi_setup.py" in out, out
        wifi = tmp + "/wifi.py"
        _write(wifi, "WIFI_SSID = 'made-up-net'\nWIFI_PASSWORD = 12345678\n")
        rc, out = _main(d, ["-n", "--debug", "A", "--wifi", "--debug-host", "10.0.0.7"], tmp, wifi)
        assert rc == 2 and "cannot use the Wi-Fi file" in out and "quotes" in out, out
        assert "made-up-net" not in out and "12345678" not in out and "fs cp" not in out


def test_secrets_flag_uses_the_same_lookup():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        rc, out = _main(d, ["-n", "--secrets"], tmp)
        assert rc == 0 and "no Wi-Fi name and password to copy" in out, out
        assert ":secrets.py" not in out
        wifi = tmp + "/wifi.py"
        _write(wifi, SAVED)
        rc, out = _main(d, ["-n", "--secrets"], tmp, wifi)
        assert rc == 0 and "WARNING" in out and "fs cp %s :secrets.py" % wifi in out, out
        assert "/debug" not in out and _hidden(out)


def test_debug_flags_that_do_not_go_together():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        wifi = tmp + "/wifi.py"
        _write(wifi, SAVED)
        rc, out = _main(d, ["-n", "--no-debug"], tmp, wifi)
        assert rc == 0 and "os.remove('/debug')" in out and "os.remove('/secrets.py')" in out
        assert ":secrets.py" not in out, out
        for argv, why in ((["--debug", "A", "--debug-host", "10.0.0.7"], "needs --wifi"),
                          (["--debug-host", "10.0.0.7"], "needs --wifi"),
                          (["--wifi"], "needs --debug"),
                          (["--no-debug", "--wifi"], "needs --debug"),
                          (["--no-debug", "--secrets"], "--no-debug removes secrets.py"),
                          (["--debug", "C"], "invalid choice"),     # the page shows A and B
                          (["--debug", "A", "--wifi", "--debug-host", "my-laptop.local"],
                           "not a name")):
            rc, out = _main(d, ["-n"] + argv, tmp, wifi)
            assert rc == 2 and why in out and "fs cp" not in out, (argv, out)


class _FakeSocketModule:
    """Stands in for ``socket`` in tools/deploy.py: ``getsockname`` answers
    ``name``; ``connect`` raises when ``name`` is an exception."""
    AF_INET = 2
    SOCK_DGRAM = 2

    def __init__(self, name):
        self.name = name
        self.closed = []
        mod = self

        class Sock:
            def __init__(self, family, kind):
                pass

            def connect(self, addr):
                if isinstance(mod.name, Exception):
                    raise mod.name

            def getsockname(self):
                return mod.name

            def close(self):
                mod.closed.append(True)
        self.socket = Sock


def test_lan_ip_and_address_check():
    d = _deploy()
    real = d.socket
    try:
        for name, want in ((("192.168.1.5", 0), "192.168.1.5"),
                           (("127.0.1.1", 0), None),        # loopback: the watch cannot reach it
                           (("0.0.0.0", 0), None),          # no route
                           (OSError(101, "Network is unreachable"), None)):
            d.socket = _FakeSocketModule(name)
            assert d.lan_ip() == want, (name, d.lan_ip())
            assert d.socket.closed, name                  # the socket is closed every time
    finally:
        d.socket = real
    assert d.is_ipv4("192.168.1.23") and not d.is_ipv4("192.168.1") and not d.is_ipv4("1.2.3.256")
    assert not d.is_ipv4("laptop.local") and not d.is_ipv4("1.2.3.x")
    # Unicode digits are not address digits (one crashed int(), one was written to /debug)
    assert not d.is_ipv4("1.2.3.²") and not d.is_ipv4("1.2.3.٣")
    assert not d.is_ipv4("192.168.1.010") and not d.is_ipv4(" 192.168.1.23")


def test_debug_host_with_unicode_digits_is_an_error_not_a_crash():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        rc, out = _main(d, ["-n", "--debug", "A", "--wifi", "--debug-host", "1.2.3.²"], tmp)
        assert rc == 2 and "must be an address" in out, out
