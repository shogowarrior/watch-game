"""Minimal test runner that works on CPython *and* MicroPython.

MicroPython has no unittest/pytest, so tests are plain modules named
``test_*.py`` with functions named ``test_*`` that use ``assert``.

    python3 tests/runner.py                  # CPython
    node tools/mpy/run.mjs tests/runner.py   # MicroPython (WebAssembly)
"""

import sys

try:
    import os
    _listdir = os.listdir
except ImportError:  # pragma: no cover
    import uos as os
    _listdir = os.listdir


def _here():
    f = globals().get("__file__", "tests/runner.py")
    i = f.rfind("/")
    return f[:i] if i >= 0 else "."


def run(selected=None):
    here = _here()
    root = here[: here.rfind("/")] if "/" in here else "."
    for p in (root, here):
        if p not in sys.path:
            sys.path.insert(0, p)
    names = sorted(n[:-3] for n in _listdir(here) if n.startswith("test_") and n.endswith(".py"))
    if selected:
        names = [n for n in names if n in selected]
    passed = failed = 0
    for modname in names:
        mod = __import__(modname)
        for attr in sorted(dir(mod)):
            if not attr.startswith("test_"):
                continue
            fn = getattr(mod, attr)
            if not callable(fn):
                continue
            try:
                fn()
                passed += 1
            except Exception as e:  # noqa: BLE001 - report and continue
                failed += 1
                print("FAIL %s.%s: %s: %s" % (modname, attr, type(e).__name__, e))
                try:
                    sys.print_exception(e)  # MicroPython
                except AttributeError:
                    import traceback
                    traceback.print_exc()
    impl = sys.implementation.name
    print("[%s] %d passed, %d failed" % (impl, passed, failed))
    return failed


if __name__ == "__main__":
    _args = globals().get("__argv__") or getattr(sys, "argv", [])
    sel = [a for a in _args[1:] if not a.startswith("-")]
    sys.exit(1 if run(sel or None) else 0)
