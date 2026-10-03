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
        assert any("--secrets" in n for n in notes)
        _, files, notes = d.collect(tmp, secrets=True)
        assert "secrets.py" in [r for _, r in files]
        assert any(n.startswith("WARNING") for n in notes)
