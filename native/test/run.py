"""Build and run the native host tests (CPython, needs g++).

    python3 native/test/run.py [name-filter] [--tests test_game,...] [--classes ...] [--mark]

Compiles native/core/src/*.cpp with native/test/*.cpp, plus the portable
code and tests a port keeps beside its drivers (PORTS), for this machine, with
the address and undefined-behaviour sanitizers (C++ overflow is UB where the
Python it ports has none). Meanwhile native/tools/trace_game.py records the
Python game's calls into a temporary folder (HM_TRACES), which the game port's
trace tests replay (--tests and --classes as trace_game.py takes them: other
Python tests to record than its default, only these classes).
Runs them from the repo root and prints their result line
``[native] N passed, S skipped, K failed``.

native/test/traced.txt holds a hash of the Python the default traces come
from (PYTHON) as of the last run in which every port matched; --mark rewrites
it after such a run. While the Python differs from it, a trace test that
differs skips instead of failing (the port lags, nobody else is blocked).
"""

import glob
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLAGS = ["-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
         "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]
TRACED = os.path.join(ROOT, "native", "test", "traced.txt")
# What the default traces depend on, besides the tests trace_game.py runs.
PYTHON = ("finder/**/*.py", "sim/*.py", "app/*.py", "tests/__init__.py", "tests/est_helpers.py",
          "tests/fakes/*.py", "native/tools/trace_game.py")
# Plain C++ on the hm:: interfaces that lives in a port: (sources, headers, tests).
PORTS = [("native/idf/components/hm_idf/portable", "native/idf/components/hm_idf/include", "native/idf/test")]


def build(out):
    """Compile the host test binary to ``out``; returns (ok, compiler output)."""
    dirs = [os.path.join(ROOT, "native", "core", "src"), os.path.join(ROOT, "native", "test")]
    inc = ["-I" + os.path.join(ROOT, "native", "core", "include"), "-I" + os.path.join(ROOT, "native", "test")]
    for src, hdr, tests in PORTS:
        dirs += [os.path.join(ROOT, src), os.path.join(ROOT, tests)]
        inc.append("-I" + os.path.join(ROOT, hdr))
    srcs = sorted(f for d in dirs for f in glob.glob(os.path.join(d, "**", "*.cpp"), recursive=True))
    p = subprocess.run(["g++"] + FLAGS + inc + srcs + ["-o", out], capture_output=True, text=True)
    return p.returncode == 0, p.stdout + p.stderr


def python_hash():
    """sha256 over the Python the default traces come from."""
    sys.path.insert(0, os.path.join(ROOT, "native", "tools"))
    from trace_game import TESTS
    files = set(os.path.join("native", "tools", "scenarios", t[9:] + ".py") if t.startswith("scenario:")
                else os.path.join("tests", t + ".py") for t, _ in TESTS)
    for pat in PYTHON:
        files.update(os.path.relpath(f, ROOT) for f in glob.glob(os.path.join(ROOT, pat), recursive=True))
    h = hashlib.sha256()
    for f in sorted(files):
        with open(os.path.join(ROOT, f), "rb") as fh:
            h.update(f.encode() + b"\0" + fh.read() + b"\0")
    return h.hexdigest()


def marked():
    try:
        with open(TRACED) as f:
            return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")][-1]
    except (OSError, IndexError):
        return None


def run(args=()):
    """Build and run; returns (exit code, output)."""
    if shutil.which("g++") is None:
        return 2, "g++ not found"
    args = list(args)
    rec_args = []
    for opt in ("--tests", "--classes"):
        if opt in args:
            i = args.index(opt)
            rec_args += args[i:i + 2]
            args[i:i + 2] = []
    mark = "--mark" in args
    if mark:
        args.remove("--mark")
    now = python_hash()
    changed = not rec_args and now != marked()   # a --tests or --classes run is always strict
    with tempfile.TemporaryDirectory() as d:
        traces = os.path.join(d, "traces")
        rec = subprocess.Popen([sys.executable, os.path.join(ROOT, "native", "tools", "trace_game.py"), traces] + rec_args,
                               cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        exe = os.path.join(d, "hm_host_tests")
        ok, log = build(exe)
        rec_out = rec.communicate()[0]
        if not ok:
            return 1, log
        if rec.returncode:
            return 1, log + rec_out
        env = dict(os.environ, HM_TRACES=traces, HM_PYTHON_CHANGED="1" if changed else "0")
        p = subprocess.run([exe] + args, cwd=ROOT, capture_output=True, text=True, env=env)
        out = log + p.stdout + p.stderr
        if mark and not rec_args and not args and p.returncode == 0 and " 0 skipped, 0 failed" in p.stdout:
            with open(TRACED, "w") as f:
                f.write("# sha256 of the Python the default traces come from (native/test/run.py),\n"
                        "# as of the last run in which every port matched them. Rewrite: run.py --mark\n"
                        + now + "\n")
            out += "marked native/test/traced.txt\n"
        return p.returncode, out


if __name__ == "__main__":
    code, out = run(sys.argv[1:])
    print(out, end="")
    sys.exit(code)
