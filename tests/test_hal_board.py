from tests import fakes


def _setup(regs=None, pmu=True):
    m = fakes.install()
    dev = m.add_axp202(regs) if pmu else None
    import hal.board as hb
    return m, dev, hb


def _stub(name, log):
    def f(board):
        log.append(name)
        return name
    return f


def test_freq_and_lazy_parts():
    m, dev, hb = _setup(pmu=False)
    b = hb.Board()
    assert m.freq() == 240_000_000
    assert not b.has("pmu") and not b.has("display")
    t = b.touch                       # no PMU on the bus: must not matter
    assert b.has("touch") and not b.has("pmu")
    assert t.i2c is b.i2c1 and b.i2c1.bus_id == 1
    assert b.i2c1.sda.id == 23 and b.i2c1.scl.id == 32


def test_touch_rotation_matches_display():
    # Display MADCTL 0xC0 = LilyGO rotation 2, where getTouch() is identity.
    m, dev, hb = _setup(pmu=False)
    assert hb.Board().touch.rotation == 0
    assert hb.Board(touch_rotation=2).touch.rotation == 2


def test_no_freq_change_when_disabled():
    m, dev, hb = _setup(pmu=False)
    m.freq(80_000_000)
    hb.Board(cpu_hz=None)
    assert m.freq() == 80_000_000


def test_one_shared_i2c0():
    m, dev, hb = _setup()
    b = hb.Board()
    assert b.i2c0 is b.i2c0
    assert b.pmu.i2c is b.i2c0
    assert b.i2c0.bus_id == 0 and b.i2c0.sda.id == 21 and b.i2c0.scl.id == 22
    assert b.i2c0.freq == 400_000


def test_pmu_turns_ldo2_on_and_keeps_dcdc3():
    m, dev, hb = _setup()
    b = hb.Board()
    b.pmu
    assert dev.regs[0x12] & 0x06 == 0x06
    assert dev.regs[0x42] & 0x03 == 0x03       # PEK short/long IRQs
    for reg, d in dev.writes:
        if reg == 0x12:
            assert d[0] & 0x02


def test_init_order_and_backlight():
    m, dev, hb = _setup()
    order = []

    class B(hb.Board):
        def _get(self, name):
            if name not in self._parts:
                order.append(name)
            return hb.Board._get(self, name)

    b = B(factories={"imu": _stub("imu", []), "radio": _stub("radio", [])})
    b.init()
    assert tuple(order) == hb.ORDER, order
    assert abs(b.display.brightness() - 0.6) < 1e-6
    assert dev.regs[0x12] & 0x04
    assert b.imu == "imu" and b.radio == "radio"
    assert m.spi_log and m.spi_log[0][2] == b"\x01"   # SWRESET first
    cmds = [p for (_, _, p) in m.spi_log if len(p) == 1]
    assert cmds.index(b"\x2c") < cmds.index(b"\x29")  # GRAM cleared before DISPON


def test_init_non_strict_records_errors():
    m, dev, hb = _setup()

    def boom(board):
        raise OSError(19)

    b = hb.Board(factories={"imu": boom, "radio": _stub("radio", [])})
    b.init(parts=("pmu", "imu", "radio"), strict=False)
    assert "imu" in b.errors and b.has("pmu") and b.has("radio")
    assert not b.has("display")
    try:
        hb.Board(factories={"imu": boom}).init(parts=("imu",))
    except OSError:
        return
    assert False, "strict init swallowed the error"


def test_button_events():
    # Short + long latched before this boot (the power-on hold) are dropped.
    m, dev, hb = _setup({0x4A: 0x03, 0x4C: 0x60})
    from hal import axp202
    b = hb.Board()
    assert b.button() == 0
    dev.regs[0x4A] = 0x02
    assert b.button() & axp202.EV_SHORT
    assert b.button() == 0


def test_safe_boot_flag_file_skips_hardware():
    m, dev, hb = _setup(pmu=False)
    b = hb.Board(cpu_hz=None)
    assert hb.safe_boot(b, flag="tests/runner.py") == "flag"
    assert not b.has("pmu")


def test_safe_boot_no_press():
    m, dev, hb = _setup({0x4A: 0x02})         # stale short press is ignored
    calls = []
    why = hb.safe_boot(hb.Board(cpu_hz=None), window_ms=100, flag="/nope",
                       sleep=calls.append)
    assert why is None
    assert len(calls) == 5
    assert dev.regs[0x44] & 0x60 == 0          # edge IRQs switched off again


def _script(dev, events):
    """sleep() that sets AXP IRQ status bits on given call numbers."""
    n = [0]

    def sleep(ms):
        n[0] += 1
        for reg, bits in events.get(n[0], ()):
            dev.regs[reg] |= bits
    return sleep, n


PRESS = (0x4C, 0x20)          # PEK falling edge
RELEASE = (0x4C, 0x40)        # PEK rising edge
SHORT = (0x4A, 0x02)
LONG = (0x4A, 0x01)


def _sb(hb, sleep, window_ms=1000):
    return hb.safe_boot(hb.Board(cpu_hz=None), window_ms=window_ms,
                        flag="/nope", sleep=sleep)


def test_safe_boot_double_press_during_window():
    m, dev, hb = _setup()
    sleep, n = _script(dev, {3: [PRESS], 8: [RELEASE, SHORT], 15: [PRESS]})
    assert _sb(hb, sleep) == "pek" and n[0] == 15


def test_safe_boot_single_tap_runs_app():
    m, dev, hb = _setup()
    sleep, n = _script(dev, {3: [PRESS], 8: [RELEASE, SHORT]})
    assert _sb(hb, sleep) is None


def test_safe_boot_power_on_release_ignored():
    # Key held to power on, let go ~0.6 s after boot: release edge + short.
    m, dev, hb = _setup()
    sleep, n = _script(dev, {30: [RELEASE, SHORT]})
    assert _sb(hb, sleep) is None
    assert dev.regs[0x44] & 0x60 == 0          # edge IRQs switched off again


def test_safe_boot_power_on_hold_reaching_long_ignored():
    # Power-on hold passes the 1.5 s long-press time inside the window.
    m, dev, hb = _setup()
    sleep, n = _script(dev, {10: [LONG], 25: [RELEASE]})
    assert _sb(hb, sleep) is None


def test_safe_boot_long_press_latched_before_boot_ignored():
    # A power-on/off hold left PEK long + edges latched in the AXP202.
    m, dev, hb = _setup({0x4A: 0x03, 0x4C: 0x60})
    assert hb.safe_boot(hb.Board(cpu_hz=None), flag="/nope",
                        sleep=lambda ms: None) is None


def test_safe_boot_fresh_press_held_to_long():
    # Press at 0.9 s, long-press IRQ 1.5 s later: window extends while held.
    m, dev, hb = _setup()
    sleep, n = _script(dev, {45: [PRESS], 120: [LONG]})
    assert _sb(hb, sleep) == "pek" and n[0] == 120


def test_safe_boot_held_press_extension_is_capped():
    m, dev, hb = _setup()
    sleep, n = _script(dev, {45: [PRESS]})    # never released, no long IRQ
    assert _sb(hb, sleep) is None
    assert n[0] == (1000 + 3000) // 20


def test_safe_boot_second_press_after_window_within_gap():
    m, dev, hb = _setup()
    sleep, n = _script(dev, {45: [PRESS], 48: [RELEASE], 60: [PRESS]})
    assert _sb(hb, sleep) == "pek" and n[0] == 60


def test_safe_boot_tap_in_one_poll_then_press():
    # Press + release inside one 20 ms poll still counts as a fresh press.
    m, dev, hb = _setup()
    sleep, n = _script(dev, {5: [PRESS, RELEASE], 12: [PRESS]})
    assert _sb(hb, sleep) == "pek"


def test_safe_boot_release_then_two_presses():
    # Power-on release, then a deliberate double press.
    m, dev, hb = _setup()
    sleep, n = _script(dev, {2: [RELEASE], 10: [PRESS], 13: [RELEASE], 18: [PRESS]})
    assert _sb(hb, sleep) == "pek" and n[0] == 18


def test_safe_boot_without_pmu_runs_app():
    m, dev, hb = _setup(pmu=False)
    assert hb.safe_boot(hb.Board(cpu_hz=None), flag="/nope", sleep=lambda ms: None) is None


def test_radio_part_is_espnow_and_started():
    m, dev, hb = _setup(pmu=False)
    r = hb.Board(cpu_hz=None, channel=11).radio
    assert type(r).__name__ == "EspNowRadio" and r.channel == 11
    assert r._e is not None and r._e.active()
    assert hb.Board(cpu_hz=None).radio.channel == 6


def test_pmu_sets_ldo2_to_3v3_before_enabling():
    m, dev, hb = _setup({0x28: 0x0A})          # LDO2 at 1.8 V, LDO4 bits set
    hb.Board().pmu
    assert dev.regs[0x28] == 0xFA               # 3300 mV, LDO4 nibble kept
    regs = [r for r, _ in dev.writes]
    assert 0x28 in regs and 0x12 in regs
    assert regs.index(0x28) < regs.index(0x12)  # voltage set before LDO2 on


def _flaky_after(dev, n_ok):
    """Make ``dev`` raise OSError on every read after ``n_ok`` reads."""
    cnt = [0]
    real = dev.read

    def read(reg, n):
        cnt[0] += 1
        if cnt[0] > n_ok:
            raise OSError(116)                 # ETIMEDOUT-style NACK
        return real(reg, n)

    dev.read = read


def test_safe_boot_bus_error_in_loop_runs_app():
    m, dev, hb = _setup()
    b = hb.Board(cpu_hz=None)
    b.pmu                                       # created while the bus works
    calls = []

    def sleep(ms):
        calls.append(ms)
        _flaky_after(dev, 0)                    # every poll after this fails

    assert hb.safe_boot(b, window_ms=100, flag="/nope", sleep=sleep) is None
    assert len(calls) == 1                      # the failure hit inside the loop


def test_safe_boot_bus_error_keeps_press_seen():
    m, dev, hb = _setup()
    b = hb.Board(cpu_hz=None)
    b.pmu
    real = dev.write

    def write(reg, data):
        if reg == 0x44:                         # disabling edge IRQs NACKs
            raise OSError(116)
        real(reg, data)

    n = [0]

    def sleep(ms):
        n[0] += 1
        dev.regs[0x4C] |= 0x20 if n[0] in (1, 3) else 0x40 if n[0] == 2 else 0
        dev.write = write

    assert hb.safe_boot(b, window_ms=1000, flag="/nope", sleep=sleep) == "pek"


def test_debug_radio_joins_the_access_point_channel():
    # main.py sets board.debug to the joined hal/debuglink.DebugLink before init.
    m, dev, hb = _setup(pmu=False)
    import network
    from hal.debuglink import DebugLink
    network.set_ap("made-up-net", "made-up-pass", channel=3)
    link = DebugLink("A", "192.168.1.23")
    assert link.join("made-up-net", "made-up-pass", sleep=lambda ms: None)
    r = hb.Board(cpu_hz=None, channel=11, debug=link).radio
    assert r.associated and r.channel == 3 and r._sta is link.sta and link.sta.isconnected()
    r = hb.Board(cpu_hz=None, channel=11).radio      # no debug link: normal play
    assert not r.associated and r.channel == 11
