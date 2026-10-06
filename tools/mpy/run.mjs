// Run a Python file from this repo under real MicroPython (WebAssembly port).
//
//   cd tools/mpy && npm install            # once
//   node tools/mpy/run.mjs tests/runner.py [args...]
//   node tools/mpy/run.mjs tools/bakeoff.py --quick
//
// The repo's Python packages (app/, finder/, hal/, native/, sim/, ui/, tests/, tools/) and
// main.py, boot.py are copied into the WebAssembly filesystem at /repo and cwd is /repo, and sys.argv is set
// as CPython would set it.
import { loadMicroPython } from "./node_modules/@micropython/micropython-webassembly-pyscript/micropython.mjs";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const DIRS = ["app", "finder", "hal", "native", "sim", "ui", "tests", "tools"];
const FILES = ["main.py", "boot.py"];   // the watch's entry points (never secrets.py)
const [script, ...args] = process.argv.slice(2);
if (!script) {
  console.error("usage: node tools/mpy/run.mjs <file.py> [args...]");
  process.exit(2);
}

let exitCode = 0;
const mp = await loadMicroPython({
  heapsize: 64 * 1024 * 1024,
  stdout: (l) => process.stdout.write(l + "\n"),
  stderr: (l) => process.stderr.write(l + "\n"),
});

function copyTree(src, dst) {
  mp.FS.mkdirTree ? mp.FS.mkdirTree(dst) : mp.FS.mkdir(dst);
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (e.name === "node_modules" || e.name.startsWith(".") || e.name === "__pycache__") continue;
    const s = path.join(src, e.name), d = dst + "/" + e.name;
    if (e.isDirectory()) copyTree(s, d);
    else if (/\.(py|json|txt|bin)$/.test(e.name)) mp.FS.writeFile(d, fs.readFileSync(s));
  }
}
mp.FS.mkdir("/repo");
for (const d of DIRS) if (fs.existsSync(path.join(repo, d))) copyTree(path.join(repo, d), "/repo/" + d);
for (const f of FILES) if (fs.existsSync(path.join(repo, f))) mp.FS.writeFile("/repo/" + f, fs.readFileSync(path.join(repo, f)));

mp.globals.set("__argv", [script, ...args]);
try {
  mp.runPython(`
import sys, os
os.chdir('/repo')
sys.path.insert(0, '/repo')
_argv = list(__argv)
sys.argv[:] = _argv
_src = open(_argv[0]).read()
_g = {'__name__': '__main__', '__file__': '/repo/' + _argv[0]}
try:
    exec(compile(_src, _argv[0], 'exec'), _g)
    __rc = 0
except SystemExit as e:
    __rc = e.value if isinstance(e.value, int) else (0 if e.value is None else 1)
    if __rc == 1 and not isinstance(e.value, int):
        print(e.value, file=sys.stderr)
`);
  exitCode = mp.globals.get("__rc") ?? 0;
} catch (e) {
  console.error(String(e && e.message ? e.message : e));
  exitCode = 1;
}
process.exitCode = exitCode;   // not process.exit(): that drops stdout still queued for a slow pipe
