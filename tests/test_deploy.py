"""tools/deploy.py: what gets copied to the watch, and the mpremote commands.

A CPython host tool (argparse, subprocess): skipped under MicroPython."""

from tests import Skip


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
    for f in ("main.py", "secrets.py"):
        open(os.path.join(tmp, f), "w").close()


def test_deploy_copies_app_and_boot():
    d = _deploy()
    _, files, notes = d.collect()
    remote = [r for _, r in files]
    for f in ("app/__init__.py", "app/runtime.py", "app/imu_feed.py"):
        assert f in remote, f
    assert remote[-2:] == ["boot.py", "main.py"]   # last: a broken copy keeps the old ones
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


def test_deploy_secrets_opt_in():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        _, files, notes = d.collect(tmp)
        assert "secrets.py" not in [r for _, r in files]
        assert any("--debug" in n for n in notes)
        _, files, notes = d.collect(tmp, secrets=True)
        assert "secrets.py" in [r for _, r in files]
        assert any(n.startswith("WARNING") for n in notes)
        _, files, notes = d.collect(tmp, debug=True)       # debug mode needs it: no warning
        remote = [r for _, r in files]
        assert "secrets.py" in remote and remote[-1] == "main.py"
        assert any("debug mode" in n for n in notes) and not any("WARNING" in n for n in notes)


def test_debug_writes_and_removes_its_files():
    import json
    d = _deploy()
    cfg = d.debug_config("B", "192.168.1.23")
    assert json.loads(cfg) == {"dev": "B", "host": "192.168.1.23", "port": 47268}
    assert json.loads(d.debug_config("A", None)) == {"dev": "A", "port": 47268}   # broadcast
    cmd = d.build_cmd(["mpremote"], None, [], [("s", "secrets.py")], None, True, None, cfg)
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


def _main(d, argv, root):
    """``d.main(argv)`` on the tree ``root`` -> (exit code, printed text)."""
    import io
    import sys
    old = (d.ROOT, sys.stdout, sys.stderr)
    out = io.StringIO()
    d.ROOT, sys.stdout, sys.stderr = root, out, out
    try:
        rc = d.main(argv)
    except SystemExit as e:
        rc = e.code
    finally:
        d.ROOT, sys.stdout, sys.stderr = old
    return rc, out.getvalue()


def test_debug_command_line():
    d = _deploy()
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        rc, out = _main(d, ["-n", "--debug", "A", "--debug-host", "10.0.0.7"], tmp)
        assert rc == 0, out
        assert "debug mode: watch A will send to 10.0.0.7:47268" in out
        assert ":secrets.py" in out and "/debug" in out and "10.0.0.7" in out
        rc, out = _main(d, ["-n", "--no-debug"], tmp)
        assert rc == 0 and "os.remove('/debug')" in out and ":secrets.py" not in out
        lan_ip = d.lan_ip
        d.lan_ip = lambda: None                   # no address found: the watch broadcasts
        try:
            rc, out = _main(d, ["-n", "--debug", "B"], tmp)
        finally:
            d.lan_ip = lan_ip
        assert rc == 0 and "broadcast" in out and "--debug-host" in out
        assert '{"dev": "B", "port": 47268}' in out
        rc, out = _main(d, ["-n", "--debug", "A", "--debug-host", "my-laptop.local"], tmp)
        assert rc == 2 and "not a name" in out
        rc, out = _main(d, ["-n", "--debug-host", "10.0.0.7"], tmp)
        assert rc == 2 and "needs --debug" in out
        rc, out = _main(d, ["-n", "--no-debug", "--secrets"], tmp)
        assert rc == 2
        rc, out = _main(d, ["-n", "--debug", "C"], tmp)
        assert rc == 2                            # the page shows watches A and B
        os.remove(os.path.join(tmp, "secrets.py"))
        rc, out = _main(d, ["-n", "--debug", "A", "--debug-host", "10.0.0.7"], tmp)
        assert rc == 2 and "secrets.example.py" in out


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
    assert not d.is_ipv4("1.2.3.\u00b2") and not d.is_ipv4("1.2.3.\u0663")
    assert not d.is_ipv4("192.168.1.010") and not d.is_ipv4(" 192.168.1.23")


def test_debug_host_with_unicode_digits_is_an_error_not_a_crash():
    d = _deploy()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        _tree(tmp)
        rc, out = _main(d, ["-n", "--debug", "A", "--debug-host", "1.2.3.\u00b2"], tmp)
        assert rc == 2 and "must be an address" in out, out
