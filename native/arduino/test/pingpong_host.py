"""Host check for the radio-pingpong env: the C++ pinger against tools/radio_pingpong.py.

    python3 native/arduino/test/pingpong_host.py

Builds src/pingpong.cpp for this machine and runs it beside the Python Pinger
through the same scripted link, each echoed by the real Python Ponger (lost
pings and pongs, a duplicate, a stray). Every ping must match the Python one
byte for byte, both must finish on the same millisecond, and their reports must
agree. Prints ``[pingpong-host] N passed, S skipped, K failed``; exit 2 without g++.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_DIR = os.path.dirname(HERE)
NATIVE = os.path.dirname(ENV_DIR)
ROOT = os.path.dirname(NATIVE)

N = 40                          # pings
LOST_PINGS = (3, 17)            # never reach the ponger
LOST_PONGS = (8, 39)            # echoed, never reach the pinger
MAC = b"\x02\x00\x00\x00\x00\x02"


class FakeRadio:
    """What radio_pingpong's classes need: send() records, poll() delivers due frames."""

    def __init__(self):
        self.sent = []
        self.inbox = []          # (deliver_t, rssi, bytes)

    def send(self, buf, now=None):
        self.sent.append(bytes(buf))
        return True

    def poll(self, now, callback=None):
        due = [f for f in self.inbox if f[0] <= now]
        self.inbox = [f for f in self.inbox if f[0] > now]
        for t, rssi, buf in due:
            callback(MAC, buf, len(buf), rssi, t)
        return len(due)


class Link:
    """One side's link: a ping sent at t reaches a Ponger 2 ms later; its pong comes back 3-6 ms after."""

    def __init__(self, pp, proto):
        self.pp, self.proto = pp, proto
        self.pong_radio = FakeRadio()
        self.ponger = pp.Ponger(self.pong_radio)
        self.out = []            # (deliver_t, rssi, bytes) for the pinger

    def carry(self, ping, t):
        seq = self.proto.seq_of(ping)
        if seq in LOST_PINGS:
            return
        self.ponger.on_rx(MAC, bytearray(ping), len(ping), -50 - seq % 7, t + 2)
        if not self.pong_radio.sent:             # not a ping it accepts: no echo
            return
        pong = self.pong_radio.sent.pop()
        if seq not in LOST_PONGS:
            self.out.append((t + 5 + seq % 4, -55 - seq % 5, pong))
            if seq == 5:                         # a duplicate pong
                self.out.append((t + 9, -60, pong))
        if seq == 11:                            # a stray: another watch's ping
            self.out.append((t + 1, -70, ping))

    def due(self, now):
        d = [f for f in self.out if f[0] <= now]
        self.out = [f for f in self.out if f[0] > now]
        return d


def parse(lines):
    """HM report lines -> {"step[.where]": {key: value}}."""
    out = {}
    for line in lines:
        words = line.split()
        if not words or words[0] != "HM":
            continue
        kv = dict(w.split("=", 1) for w in words[2:])
        out[words[1] + ("." + kv["where"] if "where" in kv else "")] = kv
    return out


def check(fails, what, got, want, tol=0):
    if abs(float(got) - float(want)) > tol:
        fails.append("%s: C++ %s, Python %s" % (what, got, want))


def scenario(exe, fails):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)                 # finder/, as on the watch
    spec = importlib.util.spec_from_file_location("radio_pingpong", os.path.join(ROOT, "tools", "radio_pingpong.py"))
    pp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pp)
    from finder import proto
    py_radio = FakeRadio()
    py = pp.Pinger(py_radio, n=N, period_ms=50)
    py_link, c_link = Link(pp, proto), Link(pp, proto)
    c = subprocess.Popen([exe, str(N)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def ask(cmd):
        c.stdin.write(cmd + "\n")
        c.stdin.flush()
        return c.stdout.readline().split()

    pings = 0
    for t in range(1000, 10000):
        py_radio.inbox += py_link.due(t)
        py.step(t)                               # sends, then drains its inbox
        for ping in py_radio.sent:
            py_link.carry(ping, t)
        reply = ask("t %d" % t)
        if reply[0] == "ping":
            want = py_radio.sent[0].hex() if py_radio.sent else "(no Python ping)"
            if reply[1] != want:
                fails.append("ping at %d ms: C++ %s, Python %s" % (t, reply[1], want))
            pings += 1
            c_link.carry(bytes.fromhex(reply[1]), t)
        elif py_radio.sent:
            fails.append("ping at %d ms: Python only" % t)
        py_radio.sent.clear()
        for at, rssi, buf in c_link.due(t):
            c.stdin.write("rx %d %d %s\n" % (at, rssi, buf.hex()))
        py_done, c_done = py.done(t), ask("done %d" % t)[1] == "1"
        if py_done != c_done:
            fails.append("done at %d ms: C++ %s, Python %s" % (t, c_done, py_done))
        if py_done or c_done:
            break
    out, _ = c.communicate("report\n")
    got, want = parse(out.splitlines()), py.report()
    pong = got.get("pingpong", {})
    for k, w in (("sent", "sent"), ("pongs", "pongs"), ("stray", "stray")):
        check(fails, k, pong.get(k, "nan"), want[w])
    check(fails, "delivery_pct", pong.get("delivery_pct", "nan"), want["delivery_pct"], 0.1)
    for step, name in (("pingpong_rtt", "rtt"), ("pingpong_gap", "gap")):
        for p in ("p50", "p95", "max"):
            check(fails, name + " " + p, got.get(step, {}).get(p + "_ms", "nan"), want[name + "_" + p])
    for where, key in (("rx", "rssi_rx"), ("peer", "rssi_at_peer")):
        s = got.get("pingpong_rssi." + where, {})
        n, lo, mean, hi, sd = want[key]
        for k, w, tol in (("n", n, 0), ("min", lo, 0), ("mean", mean, 0.051), ("max", hi, 0), ("sd", sd, 0.051)):
            check(fails, "rssi %s %s" % (where, k), s.get(k, "nan"), w, tol)
    return pings, want


def run():
    """(exit code, output): 0 passed, 1 failed, 2 no g++."""
    if not shutil.which("g++"):
        return 2, "no g++"
    with tempfile.TemporaryDirectory() as tmp:
        exe = os.path.join(tmp, "pingpong_host")
        src = [os.path.join(ENV_DIR, "src", "pingpong.cpp"), os.path.join(HERE, "pingpong_host.cpp")] + [
            os.path.join(NATIVE, "core", "src", f) for f in ("stats.cpp", "proto.cpp")]
        p = subprocess.run(["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-I" + os.path.join(NATIVE, "core", "include"), "-I" + os.path.join(ENV_DIR, "src")] +
                           src + ["-o", exe], capture_output=True, text=True)
        if p.returncode:
            return 1, p.stdout + p.stderr
        fails = []
        pings, want = scenario(exe, fails)
    if pings != N:
        fails.append("C++ sent %d pings, want %d" % (pings, N))
    out = "\n".join("FAIL " + f for f in fails)
    out += "\npongs=%d stray=%d rssi_rx=%s\n[pingpong-host] %d passed, 0 skipped, %d failed" % (
        want["pongs"], want["stray"], want["rssi_rx"], 0 if fails else 1, 1 if fails else 0)
    return (1 if fails else 0), out.strip()


if __name__ == "__main__":
    code, text = run()
    print(text)
    sys.exit(code)
