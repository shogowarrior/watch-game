"""Tabulate the "HM " lines of benchmark logs (CPython, stdlib only).

    python3 native/tools/bench_report.py LOG [LOG ...] [--md OUT.md] [--json OUT.json]

Reads serial logs from native/tools/capture.py or mpremote (Arduino, ESP-IDF
and MicroPython benches all print ``HM <kind> key=value ...``). Lines after
``HM hello variant=V`` belong to V. Prints one Markdown table per kind, with
a column per key in the order first seen, then each variant's errors and
whether it reached ``HM done``. --json writes the parsed records for the
comparison page.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
from cli import parse_args  # noqa: E402

_PAIR = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\S+)')


def _value(s):
    if s.startswith('"'):
        return s[1:-1]
    for conv in (int, float):
        try:
            return conv(s, 0) if conv is int else conv(s)
        except ValueError:
            pass
    return s


def parse_line(line):
    """``HM kind k=v ...`` -> (kind, {k: v}, free text), or None for other lines."""
    i = line.find("HM ")
    if i < 0:
        return None
    rest = line[i + 3:].strip()
    if not rest:
        return None
    kind, _, tail = rest.partition(" ")
    fields = {k: _value(v) for k, v in _PAIR.findall(tail)}
    text = _PAIR.sub("", tail).strip()
    return kind, fields, text


def parse_log(lines, name="log"):
    """Records of one log, and a summary per variant (errors, done)."""
    recs, runs = [], []
    cur = {"log": name, "variant": "?", "framework": "", "errors": [], "done": False}
    for line in lines:
        p = parse_line(line)
        if p is None:
            continue
        kind, f, text = p
        if kind == "hello":
            cur = {"log": name, "variant": str(f.get("variant", "?")), "framework": str(f.get("framework", "")),
                   "errors": [], "done": False}
            runs.append(cur)
            continue
        if kind == "done":
            cur["done"] = True
            continue
        if kind == "error":
            cur["errors"].append(" ".join(["%s=%s" % kv for kv in f.items()] + ([text] if text else [])))
            continue
        if not f:
            continue                      # a heading such as "HM i2c"
        rec = {"log": name, "variant": cur["variant"], "framework": cur["framework"], "kind": kind}
        rec.update(f)
        recs.append(rec)
    return recs, runs


def tables(recs):
    """{kind: (columns, rows)}, columns in first-seen order, variant first."""
    out = {}
    for r in recs:
        cols, rows = out.setdefault(r["kind"], (["variant"], []))
        for k in r:
            if k not in cols and k not in ("log", "kind", "framework"):
                cols.append(k)
        rows.append(r)
    return out


def markdown(recs, runs):
    lines = []
    for kind, (cols, rows) in tables(recs).items():
        lines += ["## %s" % kind, "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join(str(r.get(c, "")) for c in cols) + " |" for r in rows]
        lines.append("")
    lines += ["## runs", "", "| log | variant | framework | done | errors |", "|---|---|---|---|---|"]
    for r in runs:
        lines.append("| %s | %s | %s | %s | %s |" % (r["log"], r["variant"], r["framework"],
                                                    "yes" if r["done"] else "NO", "; ".join(r["errors"]) or "-"))
    return "\n".join(lines) + "\n"


def main(argv):
    n = next((i for i, a in enumerate(argv) if a.startswith("--")), len(argv))
    logs, opts = argv[:n], parse_args(argv[n:], {"md": None, "json": None})
    if not logs:
        print(__doc__)
        return 2
    recs, runs = [], []
    for path in logs:
        with open(path, errors="replace") as f:
            r, s = parse_log(f, os.path.basename(path))
        recs += r
        runs += s
    md = markdown(recs, runs)
    if opts["md"]:
        with open(opts["md"], "w") as f:
            f.write(md)
    if opts["json"]:
        with open(opts["json"], "w") as f:
            json.dump({"records": recs, "runs": runs}, f, indent=1)
    print(md, end="")
    return 0 if runs and all(r["done"] for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
