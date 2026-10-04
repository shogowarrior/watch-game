import struct
from finder import proto
from finder.compat import ticks_add


def test_size_and_format():
    assert struct.calcsize(proto.FMT) == proto.SIZE == 16


def test_roundtrip_all_fields():
    b = proto.Beacon(7)
    b.seq = 65535
    b.rssi_last = -71
    b.rssi_filt = -63.6
    b.steps = 1234
    b.activity = 2
    b.battery = 88
    b.state = 3
    b.set_flags(sweeping=True, taps=5, walking=True)
    b.bump_ago_ms = 420
    buf = b.pack_into(bytearray(proto.SIZE))
    assert bytes(buf[:4]) == b"SK\x03\x07"      # magic, VERSION 3, game id
    assert proto.valid(buf, 16, 7) and proto.seq_of(buf) == 65535
    r = proto.Beacon().unpack_from(buf)
    assert (r.game_id, r.seq, r.rssi_last, r.rssi_filt, r.steps) == (7, 65535, -71, -64, 1234)
    assert (r.activity, r.battery, r.state, r.bump_ago_ms) == (2, 88, 3, 420)
    assert r.sweeping and r.walking and r.taps == 5 and not r.ready
    b.set_flags(taps=2, ready=True)                 # READY (PAIRING split): bit 5
    r = proto.Beacon().unpack_from(b.pack_into(bytearray(proto.SIZE)))
    assert r.ready and r.taps == 2 and not r.sweeping and not r.walking and r.flags == 0x24
    b.set_flags(sweeping=True, taps=5, walking=True)
    buf = b.pack_into(bytearray(proto.SIZE))
    # matches struct's own decoding
    t = struct.unpack_from(proto.FMT, buf, 0)
    assert t[0] == proto.MAGIC and t[4] == -71 and t[11] == 420


def test_pack_at_offset_and_unpack_offset():
    b = proto.Beacon(1)
    b.seq = 9
    buf = bytearray(20)
    b.pack_into(buf, 4)
    assert proto.Beacon().unpack_from(buf, 4).seq == 9


def test_clamping():
    b = proto.Beacon(300)           # game_id masked to u8
    b.rssi_last = -200
    b.rssi_filt = 500
    b.steps = 70000                 # steps wrap
    b.battery = 180
    b.activity = -3
    b.bump_ago_ms = 123456
    b.seq = 65536 + 5
    r = proto.Beacon().unpack_from(b.pack_into(bytearray(16)))
    assert r.game_id == 300 & 0xFF
    assert r.rssi_last == -128 and r.rssi_filt == 127
    assert r.steps == 70000 & 0xFFFF and r.battery == 180 and r.activity == 0
    assert r.bump_ago_ms == 0xFFFF and r.seq == 5
    assert proto.clamp_i8(None) == proto.RSSI_NONE
    assert proto.clamp_u8(999) == 255 and proto.clamp_u16(-1) == 0


def test_defaults_are_none_markers():
    r = proto.Beacon().unpack_from(proto.Beacon().pack_into(bytearray(16)))
    assert r.rssi_last == proto.RSSI_NONE and r.bump_ago_ms == proto.BUMP_NONE
    assert r.battery == proto.BATT_UNKNOWN and r.flags == 0 and r.taps == 0


def test_bump_ago_encoding():
    assert proto.bump_ago(1000, None) == proto.BUMP_NONE
    assert proto.bump_ago(1000, 800) == 200
    assert proto.bump_ago(1000, 1200) == 0          # future -> 0
    assert proto.bump_ago(100000, 100000 - 65534) == 65534
    assert proto.bump_ago(100000, 100000 - 65535) == proto.BUMP_NONE
    t = ticks_add(0, -30)                            # across the ticks wrap
    assert proto.bump_ago(20, t) == 50
    b = proto.Beacon()
    b.set_bump(500, 380)
    assert b.bump_ago_ms == 120


def test_valid_rejects():
    buf = proto.Beacon(4).pack_into(bytearray(16))
    assert proto.valid(buf, 16) and proto.valid(buf, 16, 4)
    assert not proto.valid(buf, 16, 5)
    assert not proto.valid(buf, 15, 4)
    bad = bytearray(buf)
    bad[0] = 0x58
    assert not proto.valid(bad, 16)
    bad = bytearray(buf)
    bad[2] = proto.VERSION + 1
    assert not proto.valid(bad, 16)


def test_seq_and_steps_wrap():
    b = proto.Beacon()
    b.seq = 0xFFFF
    assert b.next_seq() == 0
    assert proto.steps_delta(3, 0xFFFE) == 5
