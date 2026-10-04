"""Build and run the native host tests (CPython, needs g++).

    python3 native/test/run.py [name-filter]

Compiles native/core/src/*.cpp with native/test/*.cpp for this machine, with
the address and undefined-behaviour sanitizers (C++ overflow is UB where the
Python it ports has none), runs them from the repo root and prints their
result line ``[native] N passed, S skipped, K failed``.
"""

import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FLAGS = ["-std=c++17", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
         "-fsanitize=address,undefined", "-fno-sanitize-recover=all"]


def build(out):
    """Compile the host test binary to ``out``; returns (ok, compiler output)."""
    srcs = sorted(glob.glob(os.path.join(ROOT, "native", "core", "src", "*.cpp")) +
                  glob.glob(os.path.join(ROOT, "native", "test", "*.cpp")))
    inc = ["-I" + os.path.join(ROOT, "native", "core", "include"), "-I" + os.path.join(ROOT, "native", "test")]
    p = subprocess.run(["g++"] + FLAGS + inc + srcs + ["-o", out], capture_output=True, text=True)
    return p.returncode == 0, p.stdout + p.stderr


def run(args=()):
    """Build and run; returns (exit code, output)."""
    if shutil.which("g++") is None:
        return 2, "g++ not found"
    with tempfile.TemporaryDirectory() as d:
        exe = os.path.join(d, "sf_host_tests")
        ok, log = build(exe)
        if not ok:
            return 1, log
        p = subprocess.run([exe] + list(args), cwd=ROOT, capture_output=True, text=True)
        return p.returncode, log + p.stdout + p.stderr


if __name__ == "__main__":
    code, out = run(sys.argv[1:])
    print(out, end="")
    sys.exit(code)
