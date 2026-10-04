"""tools/knocks.py: the bridge's judgement of each bump spike (docs/design/debug-mode.md
"Knocks"), from crafted records of two watches whose clocks differ from the
laptop's and from each other. A CPython host tool: skipped under MicroPython."""

import json
import os
import sys

from tests import Skip

L0 = 1_790_000_000_000     # laptop ms (rx) at L = 0
CLOCK = {"A": 100000, "B": 500000}   # each watch's ticks_ms at L = 0 (they booted at different times)
DELAY = 5                  # ms from a record's t to the bridge


def _kn():
    if sys.implementation.name != "cpython":
        raise Skip("tools/knocks.py is a CPython host tool")
    import tools.knocks as kn
    return kn


def w(dev, L):
    """Laptop moment ``L`` (ms from L0) on watch ``dev``'s clock."""
    return CLOCK[dev] + L


def _s(dev, L, **kw):
    d = {"dev": dev, "ev": "s", "t": w(dev, L), "arm": True, "tap": None, "tap_hot": False,
         "ptap": None, "ptap_hot": False, "spk": None}
    d.update(kw)
    return d


def _tap(dev, L, ok=True):
    return {"dev": dev, "ev": "tap", "t": w(dev, L), "ok": ok}


def play(ms, events=(), state=None, devs=("A", "B"), k=None, start=0):
    """Feeds ``devs``' 5 Hz state records from ``start`` to ``ms`` (``state(dev, L)``
    -> extra fields) and ``events`` (``(L_sent, rec)``: a record sent at laptop
    moment ``L_sent``), in laptop order -> (Knocks, judgements)."""
    kn = _kn()
    k = kn.Knocks() if k is None else k
    items = [(L, 1, _s(d, L, **(state(d, L) if state else {}))) for L in range(start, ms, 200)
             for d in devs]
    items += [(L, 0, rec) for L, rec in events]
    out = []
    for L, _, rec in sorted(items, key=lambda x: (x[0], x[1])):
        out += k.feed(rec, L0 + L + DELAY)
    return k, out


def heard(dev, spikes):
    """``state`` fields: the partner's spikes (``[(L_spike, hot, L_heard)]``) as ``dev`` hears them."""
    def f(d, L):
        got = [s for s in spikes if s[2] <= L]
        if d != dev or not got:
            return {}
        s = got[-1]
        return {"ptap": w(d, s[0]), "ptap_hot": s[1]}
    return f


def both(*fs):
    def f(d, L):
        out = {}
        for g in fs:
            out.update(g(d, L))
        return out
    return f


def own(dev, L_spike, hot, after):
    """``state`` fields: ``dev``'s own last spike, from ``after`` on."""
    return lambda d, L: {"tap": w(d, L_spike), "tap_hot": hot} if d == dev and L >= after else {}


def _by(out, dev):
    return [v for v in out if v["dev"] == dev]


def test_matched_on_both_watches_shares_a_row():
    st = both(heard("A", [(2060, True, 2200)]), heard("B", [(2000, True, 2200)]),
              own("A", 2000, True, 2200), own("B", 2060, True, 2200))
    k, out = play(4000, [(2190, _tap("A", 2000)), (2190, _tap("B", 2060))], st)
    a, b = _by(out, "A"), _by(out, "B")
    assert len(a) == len(b) == 1, out
    a, b = a[0], b[0]
    assert (a["v"], a["gap"], b["v"], b["gap"]) == ("matched", -60, "matched", 60), out
    assert a["why"] == "B felt it too, 60 ms later. Both were in HOT.", a
    assert b["why"] == "A felt it too, 60 ms earlier. Both were in HOT.", b
    assert a["row"] == b["row"] and a["t"] == w("A", 2000)
    assert a["at"] == L0 + 2000 + DELAY and b["at"] == L0 + 2060 + DELAY    # laptop time
    assert a["n"]["matched"] == a["n"]["felt"] == 1
    assert k.totals()["B"]["matched"] == 1


def test_judged_once_its_watch_is_a_second_past_it():
    st = heard("A", [(2060, True, 2200)])
    _, out = play(3000, [(2190, _tap("A", 2000))], st, devs=("A",))
    assert out == []                         # A's records reach 2800 < 2000 + SETTLE_MS
    _, out = play(3200, [(2190, _tap("A", 2000))], st, devs=("A",))
    assert [v["v"] for v in out] == ["matched"]


def test_not_both_in_hot_cannot_end_the_round():
    st = both(heard("A", [(2060, False, 2200)]), own("A", 2000, True, 2200))
    _, out = play(4000, [(2190, _tap("A", 2000))], st)
    assert _by(out, "A")[0]["why"] == "B felt it too, 60 ms later. B was not in HOT, so it could not end the round."


def test_buzz_apart_and_alone():
    st = both(heard("A", [(5700, True, 5800)]))
    events = [(1190, _tap("A", 1000, ok=False)), (5190, _tap("A", 5000)), (9190, _tap("A", 9000))]
    k, out = play(12000, events, st)
    v = [(x["v"], x["gap"], x["why"]) for x in _by(out, "A")]
    assert v == [
        ("buzz", None, "A was buzzing, and its own motor shakes the accelerometer."),
        ("apart", -700, "B's knock came 0.70 s later; they must be within 0.4 s."),
        ("alone", None, "B felt nothing."),
    ], v
    n = k.totals()["A"]
    assert (n["felt"], n["matched"], n["buzz"], n["apart"], n["alone"]) == (3, 0, 1, 1, 1), n


def _alone(state=None, events=(), devs=("A", "B"), ms=5000):
    _, out = play(ms, [(2190, _tap("A", 2000))] + list(events), state, devs)
    a = _by(out, "A")
    assert len(a) == 1 and a[0]["v"] == "alone", out
    return a[0]["why"]


def test_why_the_partner_sent_no_knock():
    assert _alone(devs=("A",)) == "B has sent nothing yet."
    # B felt it but was buzzing; B felt it but A never heard it by radio
    assert _alone(events=[(2190, _tap("B", 2040, ok=False))]) == \
        "B felt it too, but was buzzing, so it set its spike aside."
    assert _alone(events=[(2190, _tap("B", 2040))]) == \
        "B felt it too, but its knock time never reached A by radio."
    # B was not feeling for knocks (not in HOT)
    assert _alone(lambda d, L: {"arm": d != "B"}) == (
        "B was not feeling for knocks (it does only in HOT, in FOUND, and in PAIRING once the "
        "runes show).")
    # B's accelerometer set a bump aside: [spikes, soft, odd, buzz], counted a batch late
    for k, why in ((1, "B felt only a soft bump, under the 1 g a knock needs."),
                   (2, "B felt a bump too long or too short for a knock."),
                   (3, "B's own buzz hid the bump from its accelerometer.")):
        def spk(d, L, k=k):
            c = [0, 0, 0, 0]
            if d == "B" and L >= 2200:
                c[k] = 1
            return {"spk": c}
        assert _alone(spk) == why
    # B stopped sending before the knock (A is judged once it has held the spike 3 s)
    kn = _kn()
    k = kn.Knocks()
    play(1000, k=k)
    _, out = play(6000, [(2190, _tap("A", 2000))], devs=("A",), k=k, start=1000)
    assert [v["why"] for v in out] == ["B was not sending then."]


def test_spk_far_from_the_knock_is_no_reason():
    def spk(d, L):
        return {"spk": [0, 1 if d == "B" and L >= 4000 else 0, 0, 0]}
    assert _alone(spk) == "B felt nothing."


def test_silent_watch_is_judged_after_three_seconds():
    """A stops after its spike; B carries on, so the bridge still gets records."""
    kn = _kn()
    k = kn.Knocks()
    _, out = play(2200, [(2190, _tap("A", 2000))], k=k)
    assert out == []
    _, out = play(5200, k=k, devs=("B",), start=2200)
    assert out == []                          # held 2.8 s
    _, out = play(5400, k=k, devs=("B",), start=5200)
    assert [v["dev"] for v in out] == ["A"] and out[0]["at"] == L0 + 2000 + DELAY


def test_spike_from_a_state_record_when_its_event_was_dropped():
    st = both(heard("A", [(2060, True, 2200)]), own("A", 2000, True, 2400))
    _, out = play(4000, (), st)
    assert [(v["dev"], v["v"], v["t"]) for v in out] == [("A", "matched", w("A", 2000))]


def test_old_spike_in_the_first_record_is_not_judged():
    """The bridge started long after A's last spike: only new spikes count."""
    st = own("A", -60000, True, 0)
    _, out = play(4000, (), st)
    assert out == []


def test_restart_judges_what_was_waiting_and_starts_afresh():
    kn = _kn()
    k = kn.Knocks()
    play(2200, [(2190, _tap("A", 2000))], devs=("A",), k=k)
    saved = dict(CLOCK)
    try:
        CLOCK["A"] = 50                                 # A restarted: its clock starts again
        _, out = play(3000, devs=("A",), k=k, start=2400)
        assert [v["v"] for v in out] == ["alone"]       # the spike before the restart
        assert k.watches["A"].off() == L0 - 50 + DELAY  # the new clock's offset only
        assert k.totals()["A"]["felt"] == 1
    finally:
        CLOCK.update(saved)


def test_accelerometer_counts_since_the_bridge_heard_it():
    kn = _kn()
    k = kn.Knocks()
    seq = [[5, 9, 2, 1], [6, 12, 2, 1], [6, 12, 3, 2], [0, 1, 0, 0], [1, 1, 0, 0]]   # 4th: restarted
    for i, c in enumerate(seq):
        k.feed(_s("A", 200 * i, spk=c), L0 + 200 * i)
    assert k.totals()["A"]["spk"] == [2, 4, 1, 1]


def test_rows_pair_one_knock_and_part_the_next():
    st = both(heard("A", [(2060, True, 2200), (2560, True, 2600)]),
              heard("B", [(2000, True, 2200), (2500, True, 2600)]))
    ev = [(2190, _tap("A", 2000)), (2190, _tap("B", 2060)),
          (2590, _tap("A", 2500)), (2590, _tap("B", 2560)), (5090, _tap("A", 5000))]
    _, out = play(7000, ev, st)
    rows = [(v["dev"], v["v"], v["row"]) for v in out]
    r = sorted(set(x[2] for x in rows))
    assert len(r) == 3, rows
    assert sorted(rows) == sorted([("A", "matched", r[0]), ("B", "matched", r[0]),
                                   ("A", "matched", r[1]), ("B", "matched", r[1]),
                                   ("A", "alone", r[2])]), rows


def test_bad_records_are_ignored():
    kn = _kn()
    k = kn.Knocks()
    for rec in ({"dev": "C", "ev": "tap", "t": 5, "ok": True}, {"dev": "A", "ev": "tap"},
                {"dev": "A", "ev": "tap", "t": True}, {"dev": "A", "ev": "s", "t": 1.5},
                _s("A", 0, spk=[1, 2]), _s("A", 200, ptap="x", tap=None)):
        assert k.feed(rec, L0) == []
    assert k.totals()["A"]["spk"] == [0, 0, 0, 0]


def test_log_is_judged_again_from_the_command_line():
    kn = _kn()
    import io
    import tempfile
    from contextlib import redirect_stdout
    st = both(heard("A", [(2060, True, 2200)]), heard("B", [(2000, True, 2200)]))
    items = [(L, 1, _s(d, L, **st(d, L))) for L in range(0, 4000, 200) for d in "AB"]
    items += [(2190, 0, _tap("A", 2000)), (2190, 0, _tap("B", 2060))]
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "debug-x.jsonl")
        with open(log, "w") as f:
            for L, _, rec in sorted(items, key=lambda x: (x[0], x[1])):
                f.write(json.dumps({"src": "ttyUSB0", "rx": L0 + L, "rec": rec}) + "\n")
            f.write(json.dumps({"src": "ttyUSB0", "rx": L0, "line": "fps 9.8"}) + "\n")
            f.write("not json\n")
        out = io.StringIO()
        with redirect_stdout(out):
            assert kn.main([log]) == 0
        lines = out.getvalue().splitlines()
        assert len(lines) == 5, lines
        assert "knock, watch A  Matched" in lines[0] and "B felt it too, 60 ms later." in lines[0]
        assert lines[2].startswith("watch A: 1 spike, 1 matched")
        assert lines[4] == "matched: both count 1"
        with redirect_stdout(io.StringIO()):
            assert kn.main([]) == 2
            assert kn.main([os.path.join(tmp, "missing.jsonl")]) == 1
