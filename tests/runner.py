"""Minimal test runner that works on CPython *and* MicroPython.

MicroPython has no unittest/pytest, so tests are plain modules named
``test_*.py`` with functions named ``test_*`` that use ``assert``. A test that
cannot run on this runtime raises ``tests.Skip("reason")``.

    python3 tests/runner.py                  # CPython
    node tools/mpy/run.mjs tests/runner.py   # MicroPython (WebAssembly)
    python3 tests/runner.py test_game        # some modules (tests/test_game.py works too)

Tests run from the repo root, so they open repo files by relative path. An
unknown module name, or a run in which no test passed or failed, exits 1.

Under tools/mpy/run.mjs the command line runs one test module per runPython
call (``__steps__``, below): the WebAssembly port collects garbage only between
calls, so a whole run in one call grows the heap to the port's 2 GB cap.
"""

import os
import sys


def _root():
    """Absolute repo root, from this file's path."""
    f = globals().get("__file__", "tests/runner.py")
    if not f.startswith("/"):
        f = os.getcwd() + "/" + f
    f = f[:f.rfind("/")]        # .../tests
    return f[:f.rfind("/")]


def _module(sel):
    """'tests/test_x.py', 'test_x.py' or 'test_x' -> 'test_x'."""
    sel = sel[sel.rfind("/") + 1:]
    return sel[:-3] if sel.endswith(".py") else sel


def _select(names, selected):
    """(kept names in run order, unknown selectors)."""
    sel = [_module(s) for s in selected]
    return [n for n in names if n in sel], [s for s in sel if s not in names]


def _steps(selected=None):
    """run() as a generator: it yields after each test module and returns what
    run() returns."""
    root = _root()
    os.chdir(root)
    for p in (root, root + "/tests"):
        if p not in sys.path:
            sys.path.insert(0, p)
    from tests import Skip
    names = sorted(n[:-3] for n in os.listdir("tests") if n.startswith("test_") and n.endswith(".py"))
    if selected:
        names, missing = _select(names, selected)
        if missing:
            print("unknown test module(s): " + ", ".join(missing))
            return 1
    passed = skipped = failed = 0
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
            except Skip as e:
                skipped += 1
                print("SKIP %s.%s: %s" % (modname, attr, e))
            except Exception as e:  # noqa: BLE001 - report and continue
                failed += 1
                print("FAIL %s.%s: %s: %s" % (modname, attr, type(e).__name__, e))
                pe = getattr(sys, "print_exception", None)   # MicroPython
                if pe is not None:
                    pe(e)
                else:
                    import traceback
                    traceback.print_exception(type(e), e, e.__traceback__)
        yield
    print("[%s] %d passed, %d skipped, %d failed" % (sys.implementation.name, passed, skipped, failed))
    if passed + failed == 0:
        print("no tests ran")
        return 1
    return failed


def run(selected=None):
    """Run the selected test modules (all by default): the number failed, or 1
    for an unknown module or a run in which nothing ran."""
    steps = _steps(selected)
    try:
        while True:
            next(steps)
    except StopIteration as e:
        return e.value


def _main(argv):
    rc = yield from _steps([a for a in argv if not a.startswith("-")])
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    if "__steps__" in globals():    # tools/mpy/run.mjs: it resumes them, one call per step
        __steps__ = _main(sys.argv[1:])
    else:
        for _ in _main(sys.argv[1:]):
            pass
