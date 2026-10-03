import math
from finder import arrow as A
from finder.compat import ticks_add
from finder.estimators.base import ACT_STILL, ACT_WALK
from finder.render_params import arrow_style

DT = 100


def _run(a, t, ms, **kw):
    """Step ``a`` every DT ms for ``ms``; return (t, list of haptics, toasts)."""
    hs = []
    ts = []
    end = ticks_add(t, ms)
    while True:
        t = ticks_add(t, DT)
        a.update(t, **kw)
        if a.haptic:
            hs.append(a.haptic)
        if a.toast:
            ts.append(a.toast)
        if t == end:
            return t, hs, ts


def _check_contract(a):
    trio = (a.arrow_deg, a.cone_deg, a.arrow_style)
    assert all(x is None for x in trio) or all(x is not None for x in trio), trio
    if a.arrow_deg is not None:
        assert 0.0 <= a.arrow_deg < 360.0
        assert 12.0 <= a.cone_deg <= 60.0
        assert a.arrow_style in ("solid_a", "solid_b", "outline")
        assert a.glyph == "arrow"
    if a.word is not None:
        assert len(a.word) <= 10 and a.word == a.word.upper(), a.word
    if a.top_text is not None:
        assert len(a.top_text) <= 18 and a.top_text == a.top_text.upper()


def _locked(theta=120.0, s0=25.0, t0=0, **kw):
    a = A.make(theta, s0, t0, **kw)
    a.update(ticks_add(t0, 1600))
    if a.phase == A.PH_TURN:
        assert a.tap()
    a.update(ticks_add(t0, 1700))
    t, _, _ = _run(a, ticks_add(t0, 1700), 400)
    assert a.phase == A.PH_WALK
    return a, t


def _near(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_words():
    assert A.clock_word(120) == "4 O'CLOCK"
    assert A.clock_word(0) == "12 O'CLOCK"
    assert A.clock_word(350) == "12 O'CLOCK"
    assert A.clock_word(-30) == "11 O'CLOCK"
    assert A.clock_word(180) == "6 O'CLOCK"
    assert A.clock_word(44) == "1 O'CLOCK"
    assert A.clock_word(46) == "2 O'CLOCK"
    assert A.quad_word(30) == "AHEAD"
    assert A.quad_word(90) == "RIGHT"
    assert A.quad_word(-100) == "LEFT"
    assert A.quad_word(170) == "BEHIND"
    assert A.quad_word(-170) == "BEHIND"
    assert A.reveal_word(-15, 25) == "AHEAD"
    assert A.reveal_word(345, 40) == "AHEAD"
    assert A.reveal_word(120, 30) == "4 O'CLOCK"
    assert A.reveal_word(120, 31) == "RIGHT"
    for th in range(-180, 181, 5):
        assert len(A.reveal_word(th, 20)) <= 10


def test_wrap_helpers():
    assert A.wrap180(190) == -170
    assert A.wrap180(-190) == 170
    assert A.wrap180(180) == 180
    assert A.wrap180(-180) == 180
    assert A.wrap360(-10) == 350
    assert A.wrap360(720) == 0


def test_sigma_model_and_tiers():
    assert _near(A.sigma(30, 0, 0, 0, 0), 30)
    s = A.sigma(30, 120, 40, 0, 0)
    # spec example (spec quotes ~45; the formula gives 44.8): still solid_b
    assert abs(s - math.sqrt(2008.0)) < 1e-6 and arrow_style(s) == "solid_b", s
    assert _near(A.sigma(30, 0, 0, 0, 1), 50)
    assert _near(A.sigma(30, 0, 0, 40, 0), 50)
    assert arrow_style(25) == "solid_a" and arrow_style(25.1) == "solid_b"
    assert arrow_style(45) == "solid_b" and arrow_style(45.1) == "outline"
    assert arrow_style(60) == "outline" and arrow_style(60.1) is None


def test_birth_gate_and_probe_floor():
    assert A.make(90, 45.5, 0) is None
    assert A.make(90, 45.0, 0) is not None
    p = A.make(90, 10, 0, probe=True)
    assert p.s0 == 35.0 and p.arrow_style == "solid_b"
    assert A.make(90, 46, 0, probe=True) is None


def test_reveal_phase():
    a = A.make(120, 25, 0)
    assert a.phase == A.PH_REVEAL and a.sub == "reveal"
    assert a.glyph == "arrow"
    assert a.arrow_deg == 120 and a.cone_deg == 25 and a.arrow_style == "solid_a"
    assert a.word == "4 O'CLOCK" and a.top_text is None and a.sweep is None
    t, hs, _ = _run(a, 0, 1400)
    assert hs == [] and a.phase == A.PH_REVEAL
    assert a.tap() is False           # tap only locks during turn
    a.update(1500)
    assert a.phase == A.PH_TURN
    b = A.make(-100, 40, 0)
    assert b.word == "LEFT" and b.arrow_deg == 260 and b.arrow_style == "solid_b"
    _check_contract(a)
    _check_contract(b)


def test_turn_right_pacer_ticks_and_lock():
    a = A.make(120, 25, 0)
    a.update(1500)
    assert a.phase == A.PH_TURN and a.sub == "turn"
    assert a.word == "TURN RIGHT" and a.top_text is None
    a.update(2500)                     # 1 s -> pacer 30 deg
    assert _near(a.pacer, 30.0) and _near(a.arrow_deg, 90.0)
    assert a.sweep[0] == 30.0 and a.sweep[1] == (None,) * 12 and a.sweep[3] is False
    _check_contract(a)
    t, hs, _ = _run(a, 2500, 3000)     # pacer reaches 120 at 4 s into turn
    assert hs == ["TICK", "TICK", "DOUBLE"], hs
    assert a.phase in (A.PH_LOCK, A.PH_WALK)
    assert a.turn_deg == 120.0
    assert _near(a.sigma, math.sqrt(25 * 25 + 18 * 18))
    assert a.word == "WALK" and a.sweep is None


def test_turn_left_short_way():
    a = A.make(270, 25, 0)             # -90: turn left
    assert a.theta == -90.0
    a.update(1500)
    a.update(2500)
    assert a.word == "TURN LEFT"
    assert _near(a.pacer, -30.0)
    assert _near(a.arrow_deg, 300.0)   # -90 - (-30) = -60
    assert _near(a.sweep[0], 330.0)
    t, hs, _ = _run(a, 2500, 2100)
    assert hs == ["TICK", "DOUBLE"], hs   # the 90 deg tick is replaced by the lock


def test_tap_locks_and_dart_eases_to_zero():
    a = A.make(150, 25, 0)
    a.update(1500)
    a.update(2500)                     # pacer 30, dart at 120
    assert a.tap()
    a.update(2600)
    assert a.phase == A.PH_LOCK and a.haptic == "DOUBLE" and a.sub == "walk"
    assert a.turn_deg == 150.0
    prev = abs(A.wrap180(a.arrow_deg))
    assert prev > 100
    t = 2600
    while a.phase == A.PH_LOCK:
        t += 50
        a.update(t)
        cur = abs(A.wrap180(a.arrow_deg))
        assert cur <= prev + 1e-9
        prev = cur
    assert t <= 2950 and a.arrow_deg == 0.0 and a.phase == A.PH_WALK
    assert a.word == "WALK"
    a.update(5500)
    assert a.word == "WALK"
    a.update(5700)
    assert a.word is None              # readout takes the slot after 3 s
    assert a.tap() is False


def test_small_theta_skips_turn():
    a = A.make(-15, 25, 0)
    assert a.word == "AHEAD"
    _, hs, _ = _run(a, 0, 1500)
    assert hs == ["DOUBLE"] and a.phase == A.PH_LOCK
    assert a.turn_deg == 15.0 and a.sweep is None


def test_static_mode_face_and_autolock():
    a = A.make(100, 25, 0, mode=A.MODE_STATIC)
    a.update(1500)
    assert a.phase == A.PH_FACE and a.sub == "turn"
    assert a.word == "FACE IT" and a.sweep is None and a.arrow_deg == 100.0
    assert a.tap()
    a.update(1700)
    assert a.phase == A.PH_LOCK and a.haptic == "DOUBLE"
    b = A.make(100, 25, 0, mode=A.MODE_STATIC)
    t, hs, _ = _run(b, 0, 1500 + A.FACE_MS)
    assert hs == ["DOUBLE"] and b.phase == A.PH_LOCK


def test_probe_skips_turn_and_keeps_theta():
    a = A.make(90, 20, 0, probe=True)
    t, hs, _ = _run(a, 0, 2000)
    assert hs == [] and a.phase == A.PH_WALK
    assert a.arrow_deg == 90.0 and a.turn_deg == 0.0 and a.word is None
    assert a.cone_deg == 35.0


def test_steps_and_still_grow_sigma():
    a, t = _locked(120, 20)
    base = a.sigma
    a.update(ticks_add(t, 100), activity=ACT_WALK, steps=100)   # baseline
    a.update(ticks_add(t, 200), activity=ACT_WALK, steps=110)
    assert a.steps_walked == 10
    a.update(ticks_add(t, 300), activity=ACT_WALK, steps=120, trend=1)
    assert a.steps_walked == 10        # warmer pauses drift
    a.update(ticks_add(t, 400), activity=ACT_WALK, steps=130, trend=1, partner_walking=True)
    assert a.steps_walked == 20        # partner walking: warmer does not help
    a.update(ticks_add(t, 500), activity=ACT_WALK, steps=5)       # counter reset
    assert a.steps_walked == 20
    assert _near(a.sigma, A.sigma(20, 120, 20, 0, 0))
    assert a.sigma > base
    s1 = a.sigma
    a.update(ticks_add(t, 1500), activity=ACT_STILL)
    assert _near(a.still_s, 1.0) and a.sigma > s1
    a.update(ticks_add(t, 2500), activity=ACT_WALK)
    assert _near(a.still_s, 1.0)       # time only counts while still


def test_tiers_follow_sigma_then_expire():
    a, t = _locked(0, 20)
    assert a.arrow_style == "solid_a" and a.top_text is None
    steps = 0
    seen = []
    hs = []
    toasts = []
    for _ in range(200):
        t = ticks_add(t, DT)
        steps += 1
        a.update(t, activity=ACT_WALK, steps=steps)
        _check_contract(a)
        if a.haptic:
            hs.append(a.haptic)
        if a.toast:
            toasts.append(a.toast)
        if a.arrow_style and (not seen or seen[-1] != a.arrow_style):
            seen.append(a.arrow_style)
        if a.phase == A.PH_WALK and a.arrow_style == "outline":
            assert a.top_text == "TAP TO RESCAN"
        if a.done:
            break
    assert seen == ["solid_a", "solid_b", "outline"], seen
    assert a.done and hs == ["FARTHER"] and toasts == ["SCAN AGAIN"]
    assert a.sigma_true() > 60 and a.sigma > 60
    assert a.glyph is None and a.arrow_deg is None    # the renderer shrinks it out
    a.update(ticks_add(t, DT))
    assert a.haptic is None and a.toast is None


def test_age_expiry():
    a, t = _locked(0, 20)
    t, hs, ts = _run(a, t, 120000 - 2200)
    assert a.phase == A.PH_WALK and a.arrow_style == "solid_a"
    t, hs, ts = _run(a, t, 300)
    assert a.done and hs == ["FARTHER"] and ts == ["SCAN AGAIN"]


def test_lock_and_expire_on_one_frame_gives_farther():
    a = A.make(170, 42, 0)
    a.update(100, steps=0)
    a.update(1500, steps=52)            # turn, sigma just under 60
    assert a.phase == A.PH_TURN and a.sigma_true() < 60
    assert a.tap()
    a.update(1600, steps=52)            # the lock adds 0.15*|theta|: sigma > 60
    assert a.done and a.haptic == "FARTHER" and a.toast == "SCAN AGAIN"


def test_colder_hits_rate_limited():
    a, t = _locked(0, 20)
    s0 = a.sigma
    t, hs, _ = _run(a, t, 5900, trend=-1)
    assert hs == [] and a.colder_hits == 0
    t, hs, _ = _run(a, t, 300, trend=-1)
    assert hs == ["NOPE"] and a.colder_hits == 1
    assert _near(a.sigma, s0 + 20.0)
    assert a.top_text == "WRONG WAY? RESCAN"
    t, hs, _ = _run(a, t, 14000, trend=-1)
    assert hs == [] and a.colder_hits == 1          # at most one per 15 s
    assert a.top_text is None or a.top_text == "TAP TO RESCAN"
    t, hs, _ = _run(a, t, 1200, trend=-1)
    assert hs == ["NOPE"] and a.colder_hits == 2


def test_colder_needs_sustained_trend_and_still_partner():
    a, t = _locked(0, 20)
    t, hs, _ = _run(a, t, 5000, trend=-1)
    t, hs2, _ = _run(a, t, 100, trend=0)             # streak broken
    t, hs3, _ = _run(a, t, 5000, trend=-1)
    assert hs + hs2 + hs3 == [] and a.colder_hits == 0
    t, hs, _ = _run(a, t, 10000, trend=-1, partner_walking=True)
    assert hs == [] and a.colder_hits == 0
    b = A.make(120, 25, 0)                          # not locked during turn
    _, hs, _ = _run(b, 0, 5400, trend=-1)
    assert b.colder_hits == 0 and "NOPE" not in hs


def test_unreliable_is_display_only():
    a, t = _locked(0, 20)
    a.update(ticks_add(t, 100), unreliable=True)
    assert _near(a.sigma, 35.0) and a.cone_deg == 35.0 and a.arrow_style == "solid_b"
    a.still_s = 45.0                                # true ~49, display ~64
    a.update(ticks_add(t, 200), unreliable=True)
    assert a.sigma > 60 and a.sigma_true() < 60
    assert a.phase == A.PH_WALK and a.arrow_style == "outline" and a.cone_deg == 60.0
    a.update(ticks_add(t, 300))
    assert a.arrow_style == "outline" and a.sigma < 60


def test_link_lost_hides_grows_and_restores():
    a, t = _locked(0, 20)
    a.update(ticks_add(t, 100), activity=ACT_STILL, link_ok=False)
    assert a.glyph is None and a.arrow_deg is None and a.cone_deg is None
    assert a.arrow_style is None and a.word is None and a.sub is None
    s1 = a.sigma
    t, hs, ts = _run(a, ticks_add(t, 100), 10000, activity=ACT_STILL, link_ok=False)
    assert hs == [] and ts == [] and a.sigma > s1 and not a.done
    a.update(ticks_add(t, 100))
    assert a.glyph == "arrow" and a.arrow_deg == 0.0 and a.phase == A.PH_WALK
    assert abs(a.still_s - 10.1) < 0.01


def test_link_lost_too_long_or_too_wide_ends_silently():
    a, t = _locked(0, 20)
    t, hs, ts = _run(a, t, 10000, hidden=True)       # hiding does not start the 20 s
    t, hs, ts = _run(a, t, 20000, link_ok=False)
    assert not a.done                                # exactly 20 s: still restorable
    t, hs2, ts2 = _run(a, t, 200, link_ok=False)
    hs += hs2
    ts += ts2
    assert a.done and hs == [] and ts == []
    a.update(ticks_add(t, 100))
    assert a.done and a.glyph is None and a.haptic is None
    b, t = _locked(0, 40)
    b.update(ticks_add(t, 100), steps=0, link_ok=False)
    b.update(ticks_add(t, 200), steps=40, link_ok=False)     # walking while lost
    assert not b.done and b.glyph is None
    b.update(ticks_add(t, 300), steps=80, link_ok=False)
    assert b.done and b.haptic is None and b.toast is None  # sigma passed 60 while lost


def test_link_lost_pauses_turn():
    a = A.make(120, 25, 0)
    a.update(1500)
    a.update(2500)
    assert _near(a.pacer, 30.0)
    assert a.tap()
    _run(a, 2500, 3000, link_ok=False)
    assert a.glyph is None and a.phase == A.PH_TURN
    a.update(5600)                     # lost 3 s: pacer resumes from 30 deg
    assert a.phase == A.PH_TURN and _near(a.pacer, 33.0)
    assert a.haptic is None            # a tap before the loss was dropped


def test_hidden_pauses_turn_and_face_without_relink_limit():
    a = A.make(170, 20, 0)
    a.update(1500)
    a.update(2500)
    t, hs, ts = _run(a, 2500, 25000, hidden=True)   # hidden past the turn and the 20 s
    assert hs == [] and ts == [] and a.phase == A.PH_TURN and _near(a.pacer, 30.0)
    a.update(ticks_add(t, 100))
    assert a.phase == A.PH_TURN and _near(a.pacer, 33.0) and a.word == "TURN RIGHT"
    b = A.make(100, 25, 0, mode=A.MODE_STATIC)
    b.update(1500)
    t, hs, _ = _run(b, 1500, A.FACE_MS + 1000, steps=0, hidden=True)
    assert hs == [] and b.phase == A.PH_FACE         # no auto-lock under the MENU
    b.update(ticks_add(t, 100), steps=80, hidden=True)
    assert b.done and b.haptic == "FARTHER" and b.toast == "SCAN AGAIN"   # not silent


def test_ticks_wrap():
    t0 = ticks_add(0, -1000)
    a = A.make(90, 25, t0)
    t, hs, _ = _run(a, t0, 1500 + 3000 + 400)
    assert hs == ["TICK", "DOUBLE"] and a.phase == A.PH_WALK and a.arrow_deg == 0.0
