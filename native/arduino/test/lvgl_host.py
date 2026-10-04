"""Build and run the LVGL env's host check (needs gcc, g++ and the env's LVGL).

    pio pkg install -d native/arduino -e bench-lvgl     # once: downloads LVGL
    python3 native/arduino/test/lvgl_host.py

Compiles LVGL with include/lv_conf.h, native/core and src/lvgl_drawer.cpp for
this machine, then checks that frames drawn through LVGL reach the panel's
memory exactly as the bench's strip loop draws them. Prints
``[lvgl-host] N passed, S skipped, K failed``. LVGL objects are cached in
.pio/lvgl_host/ between runs.
"""

import glob
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_DIR = os.path.dirname(HERE)
NATIVE = os.path.dirname(ENV_DIR)
LVGL = os.path.join(ENV_DIR, ".pio", "libdeps", "bench-lvgl", "lvgl")
CACHE = os.path.join(ENV_DIR, ".pio", "lvgl_host")
INC = ["-I" + LVGL, "-I" + os.path.join(ENV_DIR, "include"), "-I" + os.path.join(ENV_DIR, "src"),
       "-I" + os.path.join(NATIVE, "core", "include"), "-DLV_CONF_INCLUDE_SIMPLE"]


def compile_c(src):
    """Compile one LVGL source into the cache unless its object is newer; returns (obj, error)."""
    obj = os.path.join(CACHE, os.path.relpath(src, LVGL).replace(os.sep, "_") + ".o")
    deps = [src, os.path.join(ENV_DIR, "include", "lv_conf.h")]
    if os.path.exists(obj) and os.path.getmtime(obj) > max(map(os.path.getmtime, deps)):
        return obj, ""
    p = subprocess.run(["gcc", "-O2", "-c", src, "-o", obj] + INC, capture_output=True, text=True)
    return obj, (p.stderr if p.returncode else "")


def main():
    if not os.path.isdir(LVGL):
        print("LVGL not found: run  pio pkg install -d native/arduino -e bench-lvgl")
        return 2
    os.makedirs(CACHE, exist_ok=True)
    srcs = glob.glob(os.path.join(LVGL, "src", "**", "*.c"), recursive=True)
    with ThreadPoolExecutor(os.cpu_count() or 2) as pool:
        built = list(pool.map(compile_c, srcs))
    errors = [e for _, e in built if e]
    if errors:
        print(errors[0])
        return 1
    exe = os.path.join(CACHE, "lvgl_host")
    cpp = (glob.glob(os.path.join(NATIVE, "core", "src", "*.cpp")) +
           [os.path.join(ENV_DIR, "src", "lvgl_drawer.cpp"), os.path.join(HERE, "lvgl_host.cpp")])
    p = subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror"] + INC + cpp +
                       [o for o, _ in built] + ["-o", exe], capture_output=True, text=True)
    if p.returncode:
        print(p.stdout + p.stderr)
        return 1
    return subprocess.run([exe]).returncode


if __name__ == "__main__":
    sys.exit(main())
