"""Compile the portable core with the watch's compilers (CPython, stdlib only).

    python3 native/tools/xcheck.py

Builds each native/core/src/*.cpp, and each header on its own, with the two
xtensa g++ PlatformIO installs (8.4 for Arduino 2.0.17, 14.2 for ESP-IDF 5.5),
with the core's rules as errors: -Wdouble-promotion (double is software math
on the ESP32), no exceptions, no RTTI. Prints ``[xcheck] N passed, K failed``;
exits 2 when neither compiler is installed (``pio run`` in native/arduino or
native/idf installs them).
"""

import concurrent.futures
import glob
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKGS = os.path.expanduser("~/.platformio/packages")
COMPILERS = ("toolchain-xtensa-esp32/bin/xtensa-esp32-elf-g++",       # Arduino 2.0.17
             "toolchain-xtensa-esp-elf/bin/xtensa-esp32-elf-g++")     # ESP-IDF 5.5
FLAGS = ["-std=gnu++17", "-Os", "-Wall", "-Wextra", "-Werror", "-Wdouble-promotion",
         "-fno-exceptions", "-fno-rtti", "-mlongcalls"]


def compilers():
    return [p for p in (os.path.join(PKGS, c) for c in COMPILERS) if os.path.exists(p)]


def units(tmp):
    """The core's sources, and one source per header that includes only it."""
    inc = os.path.join(ROOT, "native", "core", "include")
    out = sorted(glob.glob(os.path.join(ROOT, "native", "core", "src", "**", "*.cpp"), recursive=True))
    for h in sorted(glob.glob(os.path.join(inc, "hm", "**", "*.h"), recursive=True)):
        rel = os.path.relpath(h, inc)
        src = os.path.join(tmp, rel.replace(os.sep, "_") + ".cpp")
        with open(src, "w") as f:
            f.write('#include "%s"\n' % rel)
        out.append(src)
    return out


def compile_one(cxx, src):
    cmd = [cxx] + FLAGS + ["-I" + os.path.join(ROOT, "native", "core", "include"), "-c", src, "-o", os.devnull]
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, p.stdout + p.stderr


def run():
    """Compile everything; returns (exit code, output)."""
    cxxs = compilers()
    if not cxxs:
        return 2, "[xcheck] no xtensa g++ under %s (run pio once in native/arduino or native/idf)\n" % PKGS
    with tempfile.TemporaryDirectory() as tmp:
        jobs = [(c, s) for c in cxxs for s in units(tmp)]
        with concurrent.futures.ThreadPoolExecutor(os.cpu_count() or 4) as ex:
            results = list(ex.map(lambda j: compile_one(*j), jobs))
    fails = ["FAIL %s with %s\n%s" % (os.path.basename(src), cxx.split(os.sep)[-3], log)
             for (cxx, src), (ok, log) in zip(jobs, results) if not ok]
    out = "".join(fails) + "[xcheck] %d passed, %d failed\n" % (len(jobs) - len(fails), len(fails))
    return (1 if fails else 0), out


if __name__ == "__main__":
    code, out = run()
    print(out, end="")
    sys.exit(code)
