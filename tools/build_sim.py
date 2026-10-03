"""Build the web simulator into dist/sim/ (CPython only).

    python3 tools/build_sim.py            # then serve dist/sim/ or publish it

Outputs
  dist/sim/index.html      page body (artifact format: no <html>/<head> wrapper)
  dist/sim/local.html      same page wrapped in a full document for local preview
  dist/sim/py/bundle.json  {"mpy": version, "files": {path: source}} of finder/, ui/, sim/
  dist/sim/mpy/            micropython.mjs + micropython.wasm from tools/mpy/node_modules
"""

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.join(ROOT, "tools", "mpy", "node_modules", "@micropython", "micropython-webassembly-pyscript")
OUT = os.path.join(ROOT, "dist", "sim")
PY_DIRS = ("finder", "ui", "sim")


def collect():
    files = {}
    for d in PY_DIRS:
        for base, dirs, names in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if not x.startswith((".", "__"))]
            for n in sorted(names):
                if n.endswith(".py"):
                    p = os.path.join(base, n)
                    with open(p, encoding="utf-8") as f:
                        files[os.path.relpath(p, ROOT).replace(os.sep, "/")] = f.read()
    return files


def main():
    if not os.path.isdir(PKG):
        sys.exit("MicroPython wasm package missing: run `cd tools/mpy && npm install` first")
    with open(os.path.join(PKG, "package.json")) as f:
        version = json.load(f)["version"]
    os.makedirs(os.path.join(OUT, "py"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "mpy"), exist_ok=True)
    for n in ("micropython.mjs", "micropython.wasm"):
        shutil.copyfile(os.path.join(PKG, n), os.path.join(OUT, "mpy", n))
    files = collect()
    with open(os.path.join(OUT, "py", "bundle.json"), "w", encoding="utf-8") as f:
        json.dump({"mpy": version.split("-")[0], "files": files}, f)
    with open(os.path.join(ROOT, "web", "sim", "index.html"), encoding="utf-8") as f:
        page = f.read()
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(OUT, "local.html"), "w", encoding="utf-8") as f:
        f.write('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<style>body{margin:0}</style></head><body>\n' + page + "\n</body></html>\n")
    size = sum(len(v) for v in files.values())
    print("dist/sim: %d python files (%d KB), micropython %s" % (len(files), size // 1024, version))


if __name__ == "__main__":
    main()
