"""finder/proto.py: packed bytes for a spread of field values, and a decode of each."""

from finder import proto

# game_id seq rssi_last rssi_filt steps activity battery state (sweeping taps walking ready) bump_ago_ms
CASES = (
    (0, 0, None, None, 0, 0, proto.BATT_UNKNOWN, 0, (False, 0, False, False), proto.BUMP_NONE),
    (7, 1, -61, -62.4, 1234, 2, 87, 0x23, (True, 3, False, True), 120),
    (255, 0xFFFF, -62.5, -61.5, 70000, 300, -5, 9, (False, 9, True, False), 70000),
    (12, 513, -200, 127.6, -1, 4, 100, 255, (True, 7, True, True), -3),
    (1, 2, 0.5, 1.5, 65535, 1, 0, 1, (False, 1, False, False), 0),
)
BUMPS = ((1000, None), (1000, 1000), (1000, 1200), (70000, 3000), (5000, 0))


def lines():
    yield "# pack <fields as in CASES, n for None> -> <32 hex chars>; unpack -> fields read back"
    buf = bytearray(proto.SIZE)
    for c in CASES:
        b = proto.Beacon(c[0])
        b.seq, b.rssi_last, b.rssi_filt, b.steps = c[1], c[2], c[3], c[4]
        b.activity, b.battery, b.state = c[5], c[6], c[7]
        b.set_flags(*c[8])
        b.bump_ago_ms = c[9]
        b.pack_into(buf)
        r = proto.Beacon().unpack_from(buf)
        vals = ["n" if v is None else repr(float(v)) if isinstance(v, float) else str(v) for v in c[:8]]
        vals += ["%d" % int(v) for v in c[8]] + [str(c[9])]
        yield "pack %s -> %s unpack %d %d %d %d %d %d %d %d %d %d %d %d %d %d %d" % (
            " ".join(vals), buf.hex(), r.game_id, r.seq, r.rssi_last, r.rssi_filt, r.steps, r.activity,
            r.battery, r.state, r.flags, r.bump_ago_ms, r.sweeping, r.taps, r.walking, r.ready,
            proto.valid(buf, len(buf), r.game_id))
    yield "# bump_ago <now> <bump_t or n> -> <u16>"
    for now, t in BUMPS:
        yield "bump_ago %d %s -> %d" % (now, "n" if t is None else t, proto.bump_ago(now, t))
    yield "# steps_delta <new> <old> -> <delta>"
    for new, old in ((10, 3), (2, 65530), (0, 0)):
        yield "steps_delta %d %d -> %d" % (new, old, proto.steps_delta(new, old))
