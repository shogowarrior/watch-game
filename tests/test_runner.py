"""tests/runner.py: module selection; tools/mpy/run.mjs: output reaches a slow pipe."""
import os

import runner
from tests import Skip


def test_selectors_accept_paths_and_names():
    assert runner._module("tests/test_game.py") == runner._module("test_game.py") == "test_game"
    assert runner._module("test_game") == "test_game"


def test_select_keeps_known_and_reports_unknown():
    assert runner._select(["test_a", "test_b"], ["tests/test_b.py", "test_x", "test_a"]) == (
        ["test_a", "test_b"], ["test_x"])


def test_mpy_run_flushes_stdout_to_a_slow_pipe():
    """A reader that falls behind by more than the pipe buffer still gets all of
    run.mjs's output (process.exit() dropped what Node had queued) and the exit code."""
    try:
        import shutil
        import subprocess
        import time
    except ImportError:
        raise Skip("needs subprocess (CPython)")
    node = shutil.which("node")
    if not node or not os.path.isdir("tools/mpy/node_modules"):
        raise Skip("needs node and the tools/mpy npm install")
    names = ["m%059d" % i for i in range(3000)]    # one ~190 KB "unknown test module(s)" line
    p = subprocess.Popen([node, "tools/mpy/run.mjs", "tests/runner.py"] + names, stdout=subprocess.PIPE)
    t = time.time()
    while p.poll() is None and time.time() - t < 1:  # do not read yet
        time.sleep(0.02)
    out = p.communicate(timeout=60)[0]
    assert out.rstrip().endswith(names[-1].encode()) and p.returncode == 1, (len(out), p.returncode)
