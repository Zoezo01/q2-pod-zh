"""peq.c on its device path: filter response by the Audio EQ Cookbook, the engine, APO text and the save files."""
import cmath
import ctypes as C
import math
import random
import re
from types import SimpleNamespace

import pytest

BANDS, CHANNELS = 30, 8
EQ_OFF, EQ_3, EQ_PEQ = 0, 1, 2
PK, LS, HS = 0, 1, 2
r = C.byref


class Band(C.Structure):
    _fields_ = [('enabled', C.c_int), ('type', C.c_int), ('frequency', C.c_double), ('gain', C.c_double), ('q', C.c_double)]


class Preset(C.Structure):
    _fields_ = [('count', C.c_int), ('bypass', C.c_int), ('preamp', C.c_double), ('bands', Band * BANDS),
                ('balance', C.c_double), ('automatic', C.c_int), ('spare', C.c_int)]


class Coeff(C.Structure):
    _fields_ = [(k, C.c_double) for k in 'b0 b1 b2 a1 a2'.split()]


class Engine(C.Structure):
    _fields_ = [('c', Coeff * BANDS), ('z', ((C.c_double * 2) * BANDS) * CHANNELS), ('gain', C.c_double * CHANNELS),
                ('bypass', C.c_int), ('used', C.c_int), ('only', C.c_int * BANDS)]


class Dsp(C.Structure):
    _fields_ = [('current', Engine), ('next', Engine), ('pending', Engine)] + \
               [(k, C.c_int) for k in 'rate channels ramp ramp_length waiting'.split()]


class EqState(C.Structure):
    _fields_ = [('mode', C.c_int), ('hold', C.c_int), ('peq', Preset), ('eq3', Preset)]


class Error(C.Structure):
    _fields_ = [('line', C.c_uint), ('reason', C.c_char_p)]


V2 = Preset.automatic.offset
V1_BANDS = Preset.bands.offset + 10 * C.sizeof(Band)


def test_structs_are_the_saved_layout():
    assert (C.sizeof(Band), C.sizeof(Preset), V2) == (32, 992, 984)


def api(fw):
    """peq.c's calls."""
    ptr, i, d = C.c_void_p, C.c_int, C.c_double
    fns = {'peq_default': (None, ptr), 'peq_valid': (i, ptr), 'peq_parse': (i, ptr, C.c_uint, ptr, ptr),
           'peq_number': (i, ptr, ptr), 'peq_import_file': (i, C.c_char_p, ptr, ptr), 'peq_load': (i, C.c_char_p, ptr),
           'peq_load_active': (i, ptr), 'peq_save': (i, C.c_char_p, ptr, i), 'eq3_bands': (None, ptr, ptr),
           'eq_load': (i, ptr), 'eq_save': (i, ptr), 'eq_effective': (None, ptr, ptr), 'peq_compile': (i, ptr, i, ptr),
           'peq_response': (None, ptr, i, d, ptr, ptr), 'peq_auto': (d, ptr, i), 'peq_reset': (None, ptr, i, i, ptr),
           'peq_update': (i, ptr, ptr), 'peq_process': (None, ptr, ptr, C.c_uint), 'decibels': (d, d),
           'peq__level': (None, ptr, d, d), 'peq__peak': (d, ptr, ptr, i), 'peq__live': (i, ptr, ptr),
           'peq__sample': (d, ptr, ptr, i, d, i), 'peq__prepare': (i, ptr, i, ptr)}
    return SimpleNamespace(**{k.replace('peq__', ''): fw.fn(k, v[0], *v[1:]) for k, v in fns.items()})


libc = C.CDLL(None)
libc.malloc.restype, libc.malloc.argtypes = C.c_void_p, [C.c_size_t]
libc.free.argtypes = [C.c_void_p]


class Exact:
    """data in a malloc of exactly its size, so the sanitizer sees a read past it."""
    def __init__(self, data):
        self.p = libc.malloc(len(data) or 1)
        C.memmove(self.p, bytes(data), len(data))

    def __enter__(self):
        return self.p

    def __exit__(self, *_):
        libc.free(self.p)


def preset(g, *bands, preamp=0.0, balance=0.0, automatic=2, bypass=0):
    """bands: (type, frequency, gain, q[, enabled])."""
    p = Preset()
    g.peq_default(r(p))
    p.count, p.bypass, p.preamp, p.balance, p.automatic = len(bands), bypass, preamp, balance, automatic
    for i, b in enumerate(bands):
        p.bands[i] = Band(b[4] if len(b) > 4 else 1, b[0], b[1], b[2], b[3])
    return p


def compiled(g, p, rate=48000):
    e = Engine()
    assert g.peq_compile(r(p), rate, r(e))
    return e


def response(g, e, rate, f):
    left, right = C.c_double(), C.c_double()
    g.peq_response(r(e), rate, f, r(left), r(right))
    return left.value, right.value


def db(power):
    return 10 * math.log10(power)


def biquad(c, f, rate):
    """|H|^2 of a coefficient set by the complex definition."""
    z = cmath.exp(1j * math.tau * f / rate)
    return abs((c.b0 + c.b1 / z + c.b2 / z**2) / (1 + c.a1 / z + c.a2 / z**2)) ** 2


def test_sqrt_is_libm_s_here(fw):
    api(fw)
    assert fw.fn('sqrt', C.c_double, C.c_double)(9.0) == 3.0


# peq_default and eq3_bands

def test_the_default_preset_is_ten_flat_octave_bands_bypassed(fw):
    g = api(fw)
    p = Preset()
    C.memset(r(p), 0xff, C.sizeof(p))
    g.peq_default(r(p))
    assert (p.count, p.bypass, p.preamp, p.balance, p.automatic) == (10, 1, 0, 0, 0)
    assert [p.bands[i].frequency for i in range(10)] == [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    assert all(p.bands[i].q == 1.41 and p.bands[i].gain == 0 and not p.bands[i].enabled for i in range(BANDS))
    assert all(p.bands[i].frequency == 1000 for i in range(10, BANDS))
    assert g.peq_valid(r(p))


@pytest.mark.parametrize('mid, frequency', ((3.0, 1750), (0.0, 1750), (-3.0, 900)))
def test_3eq_is_a_low_shelf_a_peak_and_a_high_shelf(fw, mid, frequency):
    g = api(fw)
    p = Preset()
    gains = (C.c_double * 3)(2.0, mid, -4.0)
    g.eq3_bands(r(p), gains)
    assert p.count == 3 and not p.bypass
    want = [(LS, 155, 2.0), (PK, frequency, mid), (HS, 5000, -4.0)]
    assert [(p.bands[i].type, p.bands[i].frequency, p.bands[i].gain) for i in range(3)] == want
    assert all(p.bands[i].q == 0.707 and p.bands[i].enabled == 1 for i in range(3)) and g.peq_valid(r(p))


def test_a_flat_3eq_is_all_identity(fw):
    g = api(fw)
    p = Preset()
    g.eq3_bands(r(p), (C.c_double * 3)())
    assert compiled(g, p).used == 0


# peq_valid

GOOD = (('count', 0), ('count', 30), ('bypass', 1), ('preamp', -60), ('preamp', 24), ('balance', -12), ('balance', 12), ('automatic', 2))
BAD = (('count', -1), ('count', 31), ('bypass', 2), ('bypass', -1), ('preamp', -60.001), ('preamp', 24.001), ('preamp', math.nan),
       ('preamp', math.inf), ('balance', -12.001), ('balance', 12.001), ('balance', math.nan), ('automatic', 3), ('automatic', -1))
BAND_GOOD = (('enabled', 0), ('enabled', 3), ('type', 2), ('frequency', 20), ('frequency', 20000), ('gain', -24), ('gain', 24), ('q', 0.1), ('q', 10))
BAND_BAD = (('enabled', -1), ('enabled', 4), ('type', -1), ('type', 3), ('frequency', 19.99), ('frequency', 20000.1), ('frequency', math.nan),
            ('gain', -24.01), ('gain', 24.01), ('gain', -math.inf), ('q', 0.099), ('q', 10.01), ('q', math.nan))


@pytest.mark.parametrize('field, value, ok', [(*x, 1) for x in GOOD] + [(*x, 0) for x in BAD])
def test_peq_valid_each_field_s_limits(fw, field, value, ok):
    g = api(fw)
    p = preset(g, (PK, 1000, 1, 1))
    setattr(p, field, value)
    assert g.peq_valid(r(p)) == ok


@pytest.mark.parametrize('field, value, ok', [(*x, 1) for x in BAND_GOOD] + [(*x, 0) for x in BAND_BAD])
def test_peq_valid_each_band_field_s_limits_on_the_last_band(fw, field, value, ok):
    g = api(fw)
    p = preset(g, *[(PK, 1000, 1, 1)] * BANDS)
    setattr(p.bands[29], field, value)
    assert g.peq_valid(r(p)) == ok


def test_peq_valid_a_band_past_count_does_not_count(fw):
    g = api(fw)
    p = preset(g, (PK, 1000, 1, 1))
    p.bands[1].q = math.nan
    assert g.peq_valid(r(p))
    p.count = 2
    assert not g.peq_valid(r(p))


def test_peq_valid_null_is_invalid(fw):
    assert not api(fw).peq_valid(None)


def oracle_valid(p):
    def within(x, lo, hi):
        return math.isfinite(x) and lo <= x <= hi
    if not 0 <= p.count <= BANDS or p.bypass not in (0, 1) or not within(p.preamp, -60, 24) or not within(p.balance, -12, 12):
        return False
    if not 0 <= p.automatic <= 2:
        return False
    return all(0 <= b.enabled <= 3 and 0 <= b.type <= 2 and within(b.frequency, 20, 20000) and within(b.gain, -24, 24) and
               within(b.q, .1, 10) for b in p.bands[:p.count])


def weird(rng):
    return rng.choice((math.nan, -math.inf, math.inf, -0.0, 1e308, 5e-324, 24.0, -24.0, 20.0, 20000.0, .1, 10.0, rng.uniform(-100, 100)))


def test_peq_valid_hostile_sweep(fw):
    g, rng = api(fw), random.Random(1)
    valid = 0
    base = preset(g, *[(PK, 1000, 1, 1)] * BANDS)
    for k in range(2000):
        if k % 2:
            p = Preset.from_buffer_copy(rng.randbytes(C.sizeof(Preset)))
            if k % 4 == 1:
                p.count = rng.randrange(0, 4)
        else:
            p = Preset.from_buffer_copy(base)
            for _ in range(rng.randrange(3)):
                b = p.bands[rng.randrange(BANDS)]
                setattr(b, rng.choice(('frequency', 'gain', 'q')), weird(rng))
            p.preamp = rng.choice((0.0, weird(rng)))
            p.count = rng.randrange(-1, 32)
        with Exact(bytes(p)) as q:
            got = g.peq_valid(q)
        assert bool(got) == oracle_valid(p), k
        valid += got
    assert 100 < valid < 1000


# The filters: the Cookbook's own values at their defining frequencies

@pytest.mark.parametrize('rate', (44100, 48000, 96000))
@pytest.mark.parametrize('gain', (-24, -6, 0.5, 12, 24))
def test_a_peak_has_its_gain_at_its_centre_and_none_at_dc_or_nyquist(fw, rate, gain):
    g = api(fw)
    for f, q in ((20, .7), (1000, 1.41), (15000, 10), (1000, .1)):
        e = compiled(g, preset(g, (PK, f, gain, q)), rate)
        assert db(response(g, e, rate, f)[0]) == pytest.approx(gain, abs=1e-9)
        assert db(response(g, e, rate, 0)[0]) == pytest.approx(0, abs=1e-6)
        assert db(response(g, e, rate, rate / 2)[0]) == pytest.approx(0, abs=1e-6)


@pytest.mark.parametrize('type_, dc, nyquist', ((LS, 1, 0), (HS, 0, 1)))
@pytest.mark.parametrize('gain', (-24, -3, 6, 24))
def test_a_shelf_has_its_gain_on_its_side_and_half_at_the_corner(fw, type_, dc, nyquist, gain):
    g = api(fw)
    rate = 48000
    for f, q in ((100, .707), (1000, .5), (5000, 1)):
        e = compiled(g, preset(g, (type_, f, gain, q)))
        assert db(response(g, e, rate, 0)[0]) == pytest.approx(gain * dc, abs=1e-6)
        assert db(response(g, e, rate, rate / 2)[0]) == pytest.approx(gain * nyquist, abs=1e-6)
        assert db(response(g, e, rate, f)[0]) == pytest.approx(gain / 2, abs=1e-6)


def test_a_q_of_0_707_shelf_never_overshoots(fw):
    g = api(fw)
    e = compiled(g, preset(g, (LS, 200, 6, .707)))
    levels = [db(response(g, e, 48000, 20 * 1.02**k)[0]) for k in range(300)]
    assert all(a >= b - 1e-9 for a, b in zip(levels, levels[1:]))


def test_every_design_is_stable(fw):
    g, rng = api(fw), random.Random(2)
    for _ in range(300):
        rate = rng.choice((8000, 44100, 48000, 192000, 384000))
        f = rng.uniform(20, min(20000, rate * .49))
        e = compiled(g, preset(g, (rng.randrange(3), f, rng.uniform(-24, 24), rng.uniform(.1, 10))), rate)
        c = e.c[0]
        assert abs(c.a2) < 1 and abs(c.a1) < 1 + c.a2  # the biquad's stability triangle


def test_peq_response_is_each_channel_s_product(fw):
    g, rng = api(fw), random.Random(3)
    p = preset(g, (PK, 300, 6, 1), (LS, 100, -4, .7, 2), (HS, 8000, 5, .7, 3), (PK, 1000, 3, 2, 0))
    e = compiled(g, p)
    assert (e.only[0], e.only[1], e.only[2], e.only[3]) == (0, 1, 2, 0)
    for _ in range(40):
        f = rng.uniform(10, 23900)
        both, left, right = (biquad(e.c[i], f, 48000) for i in range(3))
        assert response(g, e, 48000, f) == pytest.approx((both * left, both * right))


def test_no_bands_is_flat(fw):
    g = api(fw)
    assert response(g, compiled(g, preset(g)), 48000, 1000) == (1, 1)


def test_a_band_off_flat_or_past_nyquist_stays_identity(fw):
    g = api(fw)
    e = compiled(g, preset(g, (PK, 1000, 6, 1, 0), (PK, 1000, 0, 1), (PK, 5000, 6, 1)), 8000)
    assert all((e.c[i].b0, e.c[i].b1, e.c[i].b2, e.c[i].a1, e.c[i].a2) == (1, 0, 0, 0, 0) for i in range(BANDS))
    assert e.used == 3  # the one past Nyquist still counts
    p = preset(g, (PK, 4000, 6, 1))  # exactly Nyquist
    assert compiled(g, p, 8000).c[0].b0 == 1
    p.bands[0].frequency = 3999
    assert compiled(g, p, 8000).c[0].b0 != 1


@pytest.mark.parametrize('rate, ok', ((7999, 0), (8000, 1), (384000, 1), (384001, 0), (0, 0), (-48000, 0)))
def test_peq_compile_s_rates(fw, rate, ok):
    g = api(fw)
    e = Engine()
    e.used = 77
    assert g.peq_compile(r(preset(g)), rate, r(e)) == ok
    assert e.used == (0 if ok else 77)  # a refusal leaves the engine alone


def test_peq_compile_refuses_an_invalid_preset(fw):
    g = api(fw)
    p = preset(g)
    p.preamp = 99
    assert not g.peq_compile(r(p), 48000, r(Engine()))


def test_preamp_and_bypass_carry_into_the_engine(fw):
    g = api(fw)
    e = compiled(g, preset(g, preamp=-20, bypass=1))
    assert e.bypass == 1 and all(x == pytest.approx(.1) for x in e.gain)


@pytest.mark.parametrize('balance, left, right', ((0, 0, 0), (6, -6, 0), (-6, 0, -6), (12, -12, 0), (-12, 0, -12)))
def test_balance_turns_one_side_down(fw, balance, left, right):
    g = api(fw)
    e = Engine()
    g.level(r(e), .5, balance)
    assert 20 * math.log10(e.gain[0] / .5) == pytest.approx(left)
    assert 20 * math.log10(e.gain[1] / .5) == pytest.approx(right)
    assert all(x == .5 for x in e.gain[2:])


# Auto's peak

def test_peak_finds_a_boost_at_its_centre_exactly(fw):
    g = api(fw)
    for rate, f in ((48000, 19000), (44100, 19999), (48000, 37.3)):
        p = preset(g, (PK, f, 7, 10))
        assert 10 * math.log10(g.peak(r(compiled(g, p, rate)), r(p), rate)) == pytest.approx(7, abs=1e-9)


def test_peak_includes_dc_and_nyquist(fw):
    g = api(fw)
    for type_, f in ((LS, 2000), (HS, 2000)):
        p = preset(g, (type_, f, 9, .707))
        assert db(g.peak(r(compiled(g, p)), r(p), 48000)) == pytest.approx(9, abs=1e-6)


def test_peak_takes_the_louder_channel(fw):
    g = api(fw)
    p = preset(g, (PK, 500, 3, 1, 2), (PK, 500, 8, 1, 3))
    assert db(g.peak(r(compiled(g, p)), r(p), 48000)) == pytest.approx(8, abs=1e-9)


def test_peak_never_boosts_and_ignores_a_trace(fw):
    g = api(fw)
    p = preset(g, (PK, 1000, -6, 1))
    assert g.peak(r(compiled(g, p)), r(p), 48000) == 1
    p = preset(g, (PK, 1000, 0.00009, 1))  # under 0.0001 dB is none
    assert g.peak(r(compiled(g, p)), r(p), 48000) == 1
    p = preset(g, (PK, 1000, 0.00011, 1))
    assert g.peak(r(compiled(g, p)), r(p), 48000) > 1


def test_peak_caps_at_60_db(fw):
    g = api(fw)
    p = preset(g, *[(PK, 1000, 24, 1)] * 3)
    assert g.peak(r(compiled(g, p)), r(p), 48000) == 1e6


def test_peak_is_at_least_anything_a_dense_scan_finds(fw):
    g, rng = api(fw), random.Random(4)
    for _ in range(12):
        p = preset(g, *[(rng.randrange(3), rng.uniform(20, 20000), rng.uniform(-12, 12), rng.uniform(.3, 4)) for _ in range(5)])
        e = compiled(g, p)
        best = max(max(response(g, e, 48000, 1.01**k)) for k in range(1080))
        assert db(g.peak(r(e), r(p), 48000)) >= db(best) - .05  # sampled, by its comment: close, not exact


def test_peq_auto_is_the_negative_peak_and_no_boost_is_plus_zero(fw):
    g = api(fw)
    assert g.peq_auto(r(preset(g, (PK, 1000, 5, 1))), 48000) == pytest.approx(-5, abs=1e-9)
    zero = g.peq_auto(r(preset(g, (PK, 1000, -5, 1))), 48000)
    assert zero == 0 and math.copysign(1, zero) == 1


def test_peq_auto_of_an_invalid_preset_is_its_preamp(fw):
    g = api(fw)
    p = preset(g, preamp=-7)
    p.count = 99
    assert g.peq_auto(r(p), 48000) == -7


# live and sample

def test_live_skips_only_identity_with_no_memory(fw):
    g = api(fw)
    e = compiled(g, preset(g, (PK, 1000, 6, 1), (PK, 2000, 0, 1), (PK, 3000, 6, 1, 0), (LS, 200, 6, 1, 2)))
    assert e.used == 4
    out = (C.c_int * BANDS)()
    assert list(out[:g.live(r(e), out)]) == [0, 3]
    e.z[5][1][1] = 1e-20  # a band draining on a far channel still runs
    assert list(out[:g.live(r(e), out)]) == [0, 1, 3]


def test_live_an_only_band_stays_though_it_is_identity(fw):
    g = api(fw)
    e = Engine()
    for i in range(BANDS):
        e.c[i].b0 = 1
    e.used, e.only[2] = 3, 1
    out = (C.c_int * BANDS)()
    assert g.live(r(e), out) == 1 and out[0] == 2


def test_live_with_all_bands_fills_the_list(fw):
    g = api(fw)
    e = compiled(g, preset(g, *[(PK, 1000, 1, 1)] * BANDS))
    out = (C.c_int * BANDS)()
    assert g.live(r(e), out) == BANDS and list(out) == list(range(BANDS))


def run_sample(g, e, xs, ch=0):
    out = (C.c_int * BANDS)()
    n = g.live(r(e), out)
    return [g.sample(r(e), out, n, x, ch) for x in xs]


def test_sample_is_the_biquad_difference_equation(fw):
    g, rng = api(fw), random.Random(5)
    e = compiled(g, preset(g, (PK, 1000, 6, 1.41), (HS, 8000, -3, .707), preamp=-6))
    xs = [rng.uniform(-1, 1) for _ in range(200)]
    want = xs
    for k in range(2):
        c = e.c[k]
        x1 = x2 = y1 = y2 = 0.0
        filtered = []
        for x in want:
            y = c.b0 * x + c.b1 * x1 + c.b2 * x2 - c.a1 * y1 - c.a2 * y2
            x2, x1, y2, y1 = x1, x, y1, y
            filtered.append(y)
        want = filtered
    gain = 10 ** (-6 / 20)
    assert run_sample(g, e, xs) == pytest.approx([y * gain for y in want], abs=1e-12)


def test_sample_in_bypass_is_the_input_untouched(fw):
    g = api(fw)
    e = compiled(g, preset(g, (PK, 1000, 6, 1), preamp=-6))
    e.bypass = 1
    assert run_sample(g, e, [.25, -.5]) == [.25, -.5]
    assert not any(e.z[0][0])


def test_sample_a_band_of_the_other_channel_passes_and_drops_its_memory(fw):
    g = api(fw)
    e = compiled(g, preset(g, (PK, 1000, 6, 1, 3)))
    e.z[0][0][0], e.z[0][0][1] = .5, .25  # carried from when it played on both
    assert run_sample(g, e, [.5], ch=0) == [.5]
    assert list(e.z[0][0]) == [0, 0]
    assert run_sample(g, e, [.5], ch=1) != [.5]


def test_sample_a_tail_never_reaches_a_denormal(fw):
    g = api(fw)
    e = compiled(g, preset(g, (PK, 1000, 6, 1)))
    out = (C.c_int * BANDS)()
    n = g.live(r(e), out)
    g.sample(r(e), out, n, 1.0, 0)
    tail = []
    for _ in range(6000):
        tail += [g.sample(r(e), out, n, 0.0, 0), *e.z[0][0]]
    assert max(abs(y) for y in tail[-30:]) < 1e-30
    assert all(y == 0 or abs(y) > 1e-40 for y in tail)  # far above the 2e-308 a MIPS FPU traps on


def test_sample_an_identity_band_drains_in_two_samples(fw):
    g = api(fw)
    e = compiled(g, preset(g, (PK, 1000, 0, 1)))
    e.used = 1
    e.z[0][0][0], e.z[0][0][1] = .5, .25
    assert run_sample(g, e, [0.0, 0.0, 0.0]) == [.5, .25, 0]
    assert list(e.z[0][0]) == [0, 0]


def test_sample_gain_is_after_the_filter(fw):
    g = api(fw)
    e = compiled(g, preset(g, (PK, 1000, 6, 1)))
    e.gain[0] = 0
    assert run_sample(g, e, [1.0]) == [0.0]
    assert e.z[0][0][0] != 0  # the memory kept filtering, independent of the preamp


# peq_process

def dsp(g, p, rate=48000, channels=2):
    d = Dsp()
    g.peq_reset(r(d), rate, channels, r(p))
    return d


def play(g, d, samples):
    audio = (C.c_float * len(samples))(*samples)
    g.peq_process(r(d), audio, len(samples) // d.channels)
    return list(audio)


def test_peq_reset(fw):
    g = api(fw)
    d = dsp(g, preset(g, (PK, 1000, 6, 1)), 44100, 4)
    assert (d.rate, d.channels, d.ramp_length, d.ramp, d.waiting, d.current.bypass) == (44100, 4, 882, 0, 0, 0)
    assert d.current.used == 1


def test_peq_reset_of_a_bad_preset_or_rate_is_bypass(fw):
    g = api(fw)
    bad = preset(g)
    bad.count = 99
    assert dsp(g, bad).current.bypass == 1
    assert dsp(g, preset(g), rate=4000).current.bypass == 1


def test_the_impulse_response_is_the_bands_in_series_on_each_channel(fw):
    g = api(fw)
    p = preset(g, (PK, 1000, 6, 1, 2), (LS, 300, -3, .7, 3), (HS, 6000, 4, .7), preamp=-3, balance=0)
    d = dsp(g, p)
    n = 256
    impulse = [0.0] * (2 * n)
    impulse[0] = impulse[1] = 1.0
    out = play(g, d, impulse)
    e = compiled(g, p)
    for ch, active in ((0, (0, 2)), (1, (1, 2))):
        sig = [1.0] + [0.0] * (n - 1)
        for k in active:
            c = e.c[k]
            x1 = x2 = y1 = y2 = 0.0
            out_k = []
            for x in sig:
                y = c.b0 * x + c.b1 * x1 + c.b2 * x2 - c.a1 * y1 - c.a2 * y2
                x2, x1, y2, y1 = x1, x, y1, y
                out_k.append(y)
            sig = out_k
        want = [y * e.gain[ch] for y in sig]
        assert out[ch::2] == pytest.approx(want, abs=1e-6), ch


@pytest.mark.parametrize('f', (100, 1000, 3000, 12000))
def test_a_sine_comes_out_at_the_response(fw, f):
    g = api(fw)
    p = preset(g, (PK, 1000, 6, 1), (HS, 6000, -4, .7), preamp=-2)
    n = 4800
    tone = [.25 * math.sin(math.tau * f * k / 48000) for k in range(n)]
    out = play(g, dsp(g, p, channels=1), tone)
    settled = out[n // 2:]  # a whole number of cycles
    rms = math.sqrt(sum(y * y for y in settled) / len(settled))
    db_out = 20 * math.log10(rms / (.25 / math.sqrt(2)))
    want = db(response(g, compiled(g, p), 48000, f)[0]) - 2
    assert db_out == pytest.approx(want, abs=.02)


def test_a_steady_bypass_leaves_the_block_alone_nan_and_all(fw):
    g = api(fw)
    d = dsp(g, preset(g, (PK, 1000, 6, 1), bypass=1))
    samples = [math.nan, math.inf, .5, -.5]
    out = play(g, d, samples)
    assert math.isnan(out[0]) and out[1:] == samples[1:]


def test_no_finite_sample_is_lost_and_a_bad_one_is_silence(fw):
    g = api(fw)
    d = dsp(g, preset(g, preamp=0))
    assert play(g, d, [math.nan, math.inf, -math.inf, .5]) == [0, 0, 0, .5]


@pytest.mark.parametrize('channels', (0, 9, -1))
def test_a_channel_count_past_the_engine_s_is_left_alone(fw, channels):
    g = api(fw)
    d = dsp(g, preset(g, preamp=-6))
    d.channels = channels
    audio = (C.c_float * 4)(.5, .5, .5, .5)
    g.peq_process(r(d), audio, 2)
    assert list(audio) == [.5] * 4


def test_no_audio_or_no_frames_is_nothing(fw):
    g = api(fw)
    d = dsp(g, preset(g, preamp=-6))
    g.peq_process(r(d), None, 4)
    audio = (C.c_float * 2)(.5, .5)
    g.peq_process(r(d), audio, 0)
    assert list(audio) == [.5, .5]


def test_eight_channels_each_keep_their_own_gain_and_the_last_is_reached(fw):
    g = api(fw)
    d = dsp(g, preset(g, preamp=-6), channels=8)
    d.current.gain[7] = 2
    out = play(g, d, [1.0] * 8 * 2)
    assert out[7] == 2 and out[15] == 2 and out[0] == pytest.approx(10 ** (-6 / 20), abs=1e-6)


def test_the_output_is_not_clipped(fw):
    g = api(fw)
    assert play(g, dsp(g, preset(g, preamp=24)), [1.0, 1.0])[0] == pytest.approx(10 ** (24 / 20), abs=1e-4)


def test_an_update_crossfades_linearly_over_20_ms(fw):
    g = api(fw)
    d = dsp(g, preset(g), channels=1)
    assert g.peq_update(r(d), r(preset(g, preamp=-20)))
    assert d.waiting == 1
    n = d.ramp_length
    assert n == 960
    out = play(g, d, [1.0] * (n + 10))
    want = [1 + (.1 - 1) * (k + 1) / n for k in range(n)] + [.1] * 10
    assert out == pytest.approx(want, abs=1e-6)
    assert (d.ramp, d.waiting) == (0, 0) and d.current.gain[0] == pytest.approx(.1)


def test_an_update_across_block_boundaries_is_the_same_ramp(fw):
    g = api(fw)
    d = dsp(g, preset(g), channels=1)
    g.peq_update(r(d), r(preset(g, preamp=-20)))
    out = []
    for size in (1, 7, 300, 500, 200):
        out += play(g, d, [1.0] * size)
    n = d.ramp_length
    assert out[:n] == pytest.approx([1 + (.1 - 1) * (k + 1) / n for k in range(n)], abs=1e-6) and out[-1] == pytest.approx(.1)


def test_the_latest_update_wins_and_a_crossfade_is_never_cut_short(fw):
    g = api(fw)
    d = dsp(g, preset(g), channels=1)
    g.peq_update(r(d), r(preset(g, preamp=-20)))
    play(g, d, [1.0] * 100)
    g.peq_update(r(d), r(preset(g, preamp=-6)))
    g.peq_update(r(d), r(preset(g, preamp=-40)))
    out = play(g, d, [1.0] * (d.ramp_length - 100))
    assert out[-1] == pytest.approx(.1, abs=1e-6)  # the first ran to its end
    out = play(g, d, [1.0] * d.ramp_length)
    assert out[-1] == pytest.approx(.01, abs=1e-6)  # the last replaced the middle one


def test_a_refused_update_changes_nothing(fw):
    g = api(fw)
    d = dsp(g, preset(g, preamp=-6))
    bad = preset(g)
    bad.preamp = math.nan
    assert not g.peq_update(r(d), r(bad))
    assert not d.waiting


def test_bypass_is_crossfaded_in_and_out(fw):
    g = api(fw)
    d = dsp(g, preset(g, bypass=1), channels=1)
    g.peq_update(r(d), r(preset(g, preamp=-20)))
    n = d.ramp_length
    assert play(g, d, [1.0] * n)[-1] == pytest.approx(.1)
    g.peq_update(r(d), r(preset(g, bypass=1)))
    out = play(g, d, [1.0] * n)
    assert out[0] == pytest.approx(.1 + .9 / n, abs=1e-5) and out[-1] == 1
    assert d.current.bypass == 1
    samples = [math.nan, .5]
    assert math.isnan(play(g, d, samples)[0])  # steady bypass again


def test_a_preamp_edit_keeps_filter_memory_so_no_transient(fw):
    g = api(fw)
    bands = (PK, 1000, 6, 1), (LS, 200, 3, .7)
    tone = [.25 * math.sin(math.tau * 440 * k / 48000) for k in range(3000)]
    d = dsp(g, preset(g, *bands), channels=1)
    plain = play(g, d, tone)
    d = dsp(g, preset(g, *bands), channels=1)
    first = play(g, d, tone[:1000])
    g.peq_update(r(d), r(preset(g, *bands, preamp=-20)))
    rest = play(g, d, tone[1000:])
    n = d.ramp_length
    for k in range(n):
        mix = (k + 1) / n
        assert rest[k] == pytest.approx(plain[1000 + k] * (1 + (.1 - 1) * mix), abs=1e-5)
    assert rest[n:] == pytest.approx([y * .1 for y in plain[1000 + n:]], abs=1e-5)
    assert first == pytest.approx(plain[:1000], abs=0)


def test_from_bypass_the_new_filter_starts_with_no_memory(fw):
    g = api(fw)
    d = dsp(g, preset(g, bypass=1), channels=1)
    d.current.z[0][0][0] = 99
    g.peq_update(r(d), r(preset(g, (PK, 1000, 6, 1))))
    play(g, d, [0.0])
    assert d.ramp == d.ramp_length - 1 and d.next.z[0][0][0] == 0


def test_a_band_dropped_from_the_end_drains_instead_of_clicking(fw):
    g = api(fw)
    d = dsp(g, preset(g, (PK, 1000, 6, 1), (PK, 3000, 6, 1)), channels=1)
    play(g, d, [.5] * 100)
    assert d.current.z[0][1][0]
    g.peq_update(r(d), r(preset(g, (PK, 1000, 6, 1))))
    assert d.pending.used == 1
    play(g, d, [.5])
    assert d.next.used == 2  # kept till its memory is gone
    assert d.next.c[1].b0 == 1 and (d.next.z[0][1][0] or d.next.z[0][1][1])


def test_a_band_with_no_memory_past_the_new_end_is_dropped(fw):
    g = api(fw)
    d = dsp(g, preset(g, (PK, 1000, 6, 1), (PK, 3000, 6, 1)), channels=1)
    g.peq_update(r(d), r(preset(g, (PK, 1000, 6, 1))))
    play(g, d, [0.0])
    assert d.next.used == 1


# Auto in the player

def test_auto_takes_this_rate_s_cut_and_a_picked_preamp_stands(fw):
    g = api(fw)
    bands = (PK, 1000, 6, 1),
    e = Engine()
    assert g.prepare(r(preset(g, *bands, preamp=-1, automatic=1)), 48000, r(e))
    assert e.gain[0] == pytest.approx(10 ** (-6 / 20))
    assert g.prepare(r(preset(g, *bands, preamp=-1, automatic=2)), 48000, r(e))
    assert e.gain[0] == pytest.approx(10 ** (-1 / 20))
    assert g.prepare(r(preset(g, *bands, preamp=-1, automatic=0)), 48000, r(e))
    assert e.gain[0] == pytest.approx(10 ** (-1 / 20))


def test_auto_keeps_the_balance(fw):
    g = api(fw)
    e = Engine()
    g.prepare(r(preset(g, (PK, 1000, 6, 1), balance=6, automatic=1)), 48000, r(e))
    assert e.gain[0] == pytest.approx(10 ** (-12 / 20)) and e.gain[1] == pytest.approx(10 ** (-6 / 20))


def test_auto_differs_by_rate_where_a_band_is_past_nyquist(fw):
    g = api(fw)
    p = preset(g, (PK, 15000, 12, 1), automatic=1)
    e = Engine()
    g.prepare(r(p), 96000, r(e))
    assert e.gain[0] == pytest.approx(10 ** (-12 / 20))
    g.prepare(r(p), 16000, r(e))  # the band can't play there: nothing to cut
    assert e.gain[0] == 1


def test_prepare_refuses_what_compile_refuses(fw):
    g = api(fw)
    assert not g.prepare(r(preset(g)), 100, r(Engine()))


# eq_effective, every case of its comment

@pytest.mark.parametrize('mode', (EQ_OFF, EQ_3, EQ_PEQ))
@pytest.mark.parametrize('hold', (0, 1))
@pytest.mark.parametrize('automatic', (0, 1, 2))
def test_eq_effective(fw, mode, hold, automatic):
    g = api(fw)
    s = EqState(mode, hold)
    s.peq = preset(g, (PK, 1000, 6, 1), preamp=-3, balance=2, automatic=automatic, bypass=1)
    s.eq3 = preset(g, (LS, 155, 3, .7), (PK, 1750, 3, .7), (HS, 5000, 3, .7), preamp=-5, balance=1, automatic=automatic)
    out = Preset()
    g.eq_effective(r(s), r(out))
    source = s.eq3 if mode == EQ_3 else s.peq
    if mode == EQ_PEQ:
        assert out.automatic == (automatic or 1)  # never picked is Auto
    else:
        assert out.automatic == (0 if automatic == 1 else automatic)  # only PEQ plays Auto
    assert out.bypass == (mode == EQ_OFF and not hold)
    if mode == EQ_OFF and hold:
        assert (out.count, out.preamp, out.balance) == (0, -5, 0)  # 3EQ's preamp alone
    else:
        assert (out.count, out.preamp, out.balance) == (source.count, source.preamp, source.balance)


# APO text

GOOD_TEXT = b"""# a comment
Preamp: -6.5 dB
Filter 1: ON PK Fc 1000 Hz Gain -3.5 dB Q 1.41
Filter 2: OFF PK Fc 200 Hz Gain 2 dB Q 0.7
Filter 3: ON None
Filter: ON PEQ Fc 30,5 Hz Gain 1 dB Q 2 # trailing
"""


def parse(g, text, size=None):
    out, err = Preset(), Error()
    out.count = -7
    with Exact(text + b'\0') as p:
        ok = g.peq_parse(p, len(text) if size is None else size, r(out), r(err))
    return (out if ok else None), err


def test_parse_an_apo_file(fw):
    g = api(fw)
    p, _ = parse(g, GOOD_TEXT)
    assert p.count == 3 and p.preamp == -6.5 and p.automatic == 2 and not p.bypass and p.balance == 0
    a, b, c = p.bands[0], p.bands[1], p.bands[2]
    assert (a.enabled, a.type, a.frequency, a.gain, a.q) == (1, PK, 1000, -3.5, 1.41)
    assert (b.enabled, b.type, b.frequency, b.gain, b.q) == (0, PK, 200, 2, .7)
    assert (c.enabled, c.frequency) == (1, 30.5)  # a comma is the decimal mark, as APO's
    assert g.peq_valid(r(p))


def test_parse_no_preamp_is_auto(fw):
    p, _ = parse(api(fw), b'Filter 1: ON PK Fc 100 Hz Gain 1 dB Q 1\n')
    assert p.automatic == 1 and p.preamp == 0


@pytest.mark.parametrize('text', (b'', b'\n\n', b'# only a comment', b'\xef\xbb\xbf', b'\r\n', b'   \t  '))
def test_parse_an_empty_file_is_an_empty_preset(fw, text):
    p, _ = parse(api(fw), text)
    assert p.count == 0 and p.automatic == 1


def test_parse_takes_a_bom_crlf_and_no_last_newline(fw):
    p, _ = parse(api(fw), b'\xef\xbb\xbfPreamp: 1 dB\r\nFilter 1: ON PK Fc 100 Hz Gain 1 dB Q 1')
    assert p.preamp == 1 and p.count == 1


def test_parse_colons_stand_alone_or_stick(fw):
    p, _ = parse(api(fw), b'Filter 1 : ON PK Fc 100 Hz Gain 1 dB Q 1\nFilter:ON PK Fc 200 Hz Gain 1 dB Q 1\nPreamp : 2 dB\n')
    assert p.count == 2 and p.preamp == 2


def test_parse_preamps_add_up(fw):
    p, _ = parse(api(fw), b'Preamp: -3 dB\nPreamp: -4 dB\n')
    assert p.preamp == -7


def test_parse_channel_preamps_become_the_louder_and_a_balance(fw):
    p, _ = parse(api(fw), b'Channel: L\nPreamp: -3 dB\nChannel: R\nPreamp: -5 dB\nChannel: all\nPreamp: -1 dB\n')
    assert p.preamp == -4 and p.balance == -2  # the right is 2 dB lower


def test_parse_a_left_cut_alone_is_a_positive_balance(fw):
    p, _ = parse(api(fw), b'Channel: L\nPreamp: -3 dB\n')
    assert p.preamp == 0 and p.balance == 3


@pytest.mark.parametrize('line, enabled', ((b'Channel: L', 2), (b'Channel: R', 3), (b'Channel: all', 1), (b'Channel: L R', 1)))
def test_parse_a_filter_plays_on_its_channel(fw, line, enabled):
    p, _ = parse(api(fw), line + b'\nFilter 1: ON PK Fc 100 Hz Gain 1 dB Q 1\n')
    assert p.bands[0].enabled == enabled


@pytest.mark.parametrize('name, type_', ((b'PK', PK), (b'PEQ', PK), (b'LS', LS), (b'LSC', LS), (b'HS', HS), (b'HSC', HS)))
def test_parse_each_filter_type(fw, name, type_):
    p, _ = parse(api(fw), b'Filter 1: ON ' + name + b' Fc 100 Hz Gain 1 dB Q 1\n')
    assert p.bands[0].type == type_


@pytest.mark.parametrize('gain', (6.0, -6.0, 0.0))
def test_parse_a_shelf_without_q_has_slope_0_9_as_apo(fw, gain):
    p, _ = parse(api(fw), b'Filter 1: ON LS Fc 100 Hz Gain %g dB\n' % gain)
    a = 10 ** (gain / 40)
    assert p.bands[0].q == pytest.approx(1 / math.sqrt((a + 1 / a) * (1 / .9 - 1) + 2))


def test_parse_lsc_with_q_keeps_its_centre(fw):
    p, _ = parse(api(fw), b'Filter 1: ON LSC Fc 100 Hz Gain 6 dB Q 2\n')
    assert (p.bands[0].frequency, p.bands[0].q) == (100, 2)


@pytest.mark.parametrize('name, down', ((b'LS', False), (b'HS', True)))
def test_parse_ls_and_hs_with_q_move_the_corner_to_the_centre(fw, name, down):
    p, _ = parse(api(fw), b'Filter 1: ON ' + name + b' Fc 1000 Hz Gain 6 dB Q 2\n')
    a = 10 ** (6 / 40)
    f = 10 ** (abs(6) / 80 * ((1 / 4 - 2) / (a + 1 / a) + 1))
    assert p.bands[0].frequency == pytest.approx(1000 / f if down else 1000 * f)


def test_parse_a_corner_moved_past_the_range_clamps(fw):
    p, _ = parse(api(fw), b'Filter 1: ON LS Fc 20000 Hz Gain 24 dB Q 0.5\nFilter 2: ON HS Fc 20 Hz Gain 24 dB Q 0.5\n')
    assert p.bands[0].frequency == 20000 and p.bands[1].frequency == 20


def test_parse_an_off_filter_outside_the_ranges_is_a_blank_slot(fw):
    p, _ = parse(api(fw), b'Filter 1: OFF PK Fc 0 Hz Gain 0 dB Q 0\n')
    assert p.count == 0


def test_parse_exactly_30_bands_then_no_more(fw):
    g = api(fw)
    line = b'Filter: ON PK Fc 100 Hz Gain 1 dB Q 1\n'
    assert parse(g, line * 30)[0].count == 30
    p, err = parse(g, line * 31)
    assert p is None and err.line == 31 and b'30' in err.reason


def test_parse_limits_a_file_to_16384_bytes(fw):
    g = api(fw)
    assert parse(g, b'#' * 511 + b'\n' + (b'#' * 511 + b'\n') * 31)[0] is not None
    assert parse(g, b'\n' * 16384)[0] is not None
    p, err = parse(g, b'\n' * 16385)
    assert p is None and err.line == 1


def test_parse_limits_a_line_to_512_bytes(fw):
    g = api(fw)
    assert parse(g, b'#' * 512)[0] is not None
    p, err = parse(g, b'\n' + b'#' * 513)
    assert p is None and err.line == 2


def test_parse_16_tokens_then_no_more(fw):
    g = api(fw)
    assert parse(g, b'Channel: ' + b'L ' * 14)[0] is not None  # 16 tokens with the colon
    assert parse(g, b'Channel: ' + b'L ' * 15)[1].line == 1


@pytest.mark.parametrize('text, line, word', (
    (b'Filter 1: ON XX Fc 100 Hz Gain 1 dB Q 1\n', 1, b'type'),
    (b'\n\nnonsense\n', 3, b'command'),
    (b'Filter a: ON PK Fc 100 Hz Gain 1 dB Q 1\n', 1, b'label'),
    (b'Filter 1 ON PK Fc 100 Hz Gain 1 dB Q 1\n', 1, b'Filter'),
    (b'Filter 1: MAYBE PK Fc 100 Hz Gain 1 dB Q 1\n', 1, b'ON or OFF'),
    (b'Filter 1: ON PK Fc 100 Hz Gain 1 dB\n', 1, b'requires Q'),
    (b'Filter 1: ON PK Fc 100 Hz Gain 1 dB R 1\n', 1, b'Q'),
    (b'Filter 1: ON PK Fc 100 kHz Gain 1 dB Q 1\n', 1, b'Fc'),
    (b'Filter 1: ON PK Fc 0x10 Hz Gain 1 dB Q 1\n', 1, b'number'),
    (b'Filter 1: ON PK Fc 19 Hz Gain 1 dB Q 1\n', 1, b'range'),
    (b'Filter 1: ON PK Fc 100 Hz Gain 25 dB Q 1\n', 1, b'range'),
    (b'Filter 1: ON PK Fc 100 Hz Gain 1 dB Q 11\n', 1, b'range'),
    (b'Filter 1: ON PK\n', 1, b'form'),
    (b'Preamp: 1\n', 1, b'Preamp'),
    (b'Preamp: 25 dB\n', 1, b'preamp'),
    (b'Preamp: nan dB\n', 1, b'preamp'),
    (b'Channel\n', 1, b'Channel'),
    (b'Channel: C\n', 1, b'channels'),
    (b'Preamp: -40 dB\nPreamp: -40 dB\n', 2, b'summed'),
    (b'Channel: L\nPreamp: -13 dB\n', 2, b'differ'),
))
def test_parse_refuses_with_the_line_and_why(fw, text, line, word):
    p, err = parse(api(fw), text)
    assert p is None and err.line == line and word in err.reason


def test_parse_an_embedded_nul_is_refused(fw):
    p, err = parse(api(fw), b'Preamp: 1 dB\n\0\n')
    assert p is None and err.line == 2 and b'NUL' in err.reason


def test_parse_a_failure_writes_nothing(fw):
    g = api(fw)
    out = preset(g, preamp=-9)
    before = bytes(out)
    text = b'Filter 1: ON PK Fc 100 Hz Gain 1 dB Q 1\nbad\n'
    with Exact(text + b'\0') as p:
        assert not g.peq_parse(p, len(text), r(out), None)  # and no error record to fill
    assert bytes(out) == before


def test_parse_reads_only_size_bytes(fw):
    p, _ = parse(api(fw), b'Preamp: 1 dB\nbad\n', 13)
    assert p.preamp == 1


WORDS = (b'Filter', b'Preamp', b'Channel', b'ON', b'OFF', b'None', b'PK', b'PEQ', b'LS', b'LSC', b'HS', b'HSC', b'Fc', b'Hz', b'Gain',
         b'dB', b'Q', b':', b'L', b'R', b'all', b'1', b'0', b'-1e3', b'24', b'0x10', b'nan', b'1,5', b'#', b'\t', b'\r', b'\xff', b'+', b'.')


def garbage(rng):
    kind = rng.randrange(4)
    if kind == 0:
        return rng.randbytes(rng.randrange(0, 300))
    if kind == 1:
        lines = [b' '.join(rng.choices(WORDS, k=rng.randrange(0, 20))) for _ in range(rng.randrange(0, 12))]
        return b'\n'.join(lines)
    data = bytearray(GOOD_TEXT * rng.randrange(1, 12))
    for _ in range(rng.randrange(1, 8)):
        data[rng.randrange(len(data))] = rng.randrange(256)
    return bytes(data) if kind == 2 else b'#' * rng.randrange(505, 520) + b'\n' + bytes(data)


def test_parse_hostile_sweep(fw):
    g, rng = api(fw), random.Random(6)
    worked = 0
    for k in range(2000):
        p, err = parse(g, garbage(rng))
        if p is None:
            assert err.line and err.reason
        else:
            worked += 1
            assert g.peq_valid(r(p)), k
            assert p.automatic in (1, 2)
    assert 20 < worked < 1500


# peq_number

@pytest.mark.parametrize('s, v', ((b'1', 1), (b'-1.5', -1.5), (b'+2', 2), (b'1,5', 1.5), (b'1e3', 1000), (b'.5', .5), (b'5.', 5),
                                  (b'-0', 0), (b'1E-2', .01)))
def test_peq_number_takes_decimals(fw, s, v):
    out = C.c_double(-9)
    assert api(fw).peq_number(s, r(out)) and out.value == v


@pytest.mark.parametrize('s', (b'', b'+', b'-', b'.', b',', b'e', b'e5', b'--1', b'+-1', b'1.2.3', b'1 ', b' 1', b'abc', b'0x10',
                               b'-0X1p3', b'1e999', b'-1e999', b'nan', b'NAN', b'inf', b'infinity', b'1f', b'1d', b'1,2,3'))
def test_peq_number_refuses_anything_else(fw, s):
    out = C.c_double(-9)
    assert not api(fw).peq_number(s, r(out)) and out.value == -9


def test_peq_number_limits_its_length(fw):
    g = api(fw)
    out = C.c_double()
    assert g.peq_number(b'0' * 511 + b'7', r(out)) and out.value == 7
    assert not g.peq_number(b'0' * 513, r(out))


def test_peq_number_hostile_sweep(fw):
    g, rng = api(fw), random.Random(7)
    good = 0
    for _ in range(2000):
        s = bytes(rng.choices(b'0123456789+-.,eE', k=rng.randrange(0, 9)))
        if re.search(rb'\d[,.]?[eE][+-]?$', s):
            continue  # libc reads a number with a bare exponent mark as the number; report, not rule
        try:
            want = float(s.replace(b',', b'.').decode())
            want = want if math.isfinite(want) else None
        except ValueError:
            want = None
        out = C.c_double(1.25)
        with Exact(s + b'\0') as p:
            got = g.peq_number(p, r(out))
        assert bool(got) == (want is not None), s
        if got:
            good += 1
            assert out.value == want, s
    assert good > 200


# The files: fopen and fread as far as peq.c sees them

class Card:
    def __init__(self, fw, files=None):
        self.files, self.open, self.bad_fsync, self.bad_ferror, self.bad_fclose = dict(files or {}), {}, False, False, False
        self.unlinked = []
        for name in 'fopen fread fwrite rename unlink'.split():
            fw.mock('fw_' + name, getattr(self, name))
        fw.mock('fw_fclose', lambda *_: self.bad_fclose)
        fw.mock('fw_fflush', lambda *_: 0)
        fw.mock('fw_fsync', lambda *_: self.bad_fsync)
        fw.mock('fw_ferror', lambda *_: self.bad_ferror)
        fw.mock('fw_access', lambda path, *_: C.string_at(path).decode() not in self.files)

    def fopen(self, path, mode, *_):
        name, write = C.string_at(path).decode(), C.string_at(mode)[:1] == b'w'
        if not write and name not in self.files:
            return 0
        handle = 0x1000 + len(self.open)
        self.open[handle] = [name, 0]
        if write:
            self.files[name] = b''
        return handle

    def fread(self, buf, size, n, f, *_):
        name, at = self.open[f]
        data = self.files[name][at:at + size * n]
        C.memmove(buf, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fwrite(self, buf, size, n, f, *_):
        self.files[self.open[f][0]] += C.string_at(buf, size * n)
        return n

    def rename(self, a, b, *_):
        self.files[C.string_at(b).decode()] = self.files.pop(C.string_at(a).decode())
        return 0

    def unlink(self, path, *_):
        self.unlinked.append(C.string_at(path).decode())
        self.files.pop(self.unlinked[-1], None)
        return 0


ACTIVE = '/mnt/data/peq-active'


def magic(n):
    return b'Q2PEQ0%d\0' % n


def test_a_saved_preset_loads_back_whole(fw):
    g = api(fw)
    card = Card(fw)
    p = preset(g, (PK, 1000, 6, 1), balance=2, automatic=2, preamp=-3)
    assert g.peq_save(b'/x/a.peq', r(p), 0) == 1
    assert list(card.files) == ['/x/a.peq']  # the temporary renamed over it
    assert card.files['/x/a.peq'] == magic(2) + bytes(p)
    q = Preset()
    assert g.peq_load(b'/x/a.peq', r(q)) and bytes(q) == bytes(p)


def test_peq_save_asks_before_replacing(fw):
    g = api(fw)
    card = Card(fw, {'/x/a': b'old'})
    p = preset(g)
    assert g.peq_save(b'/x/a', r(p), 0) == 2 and card.files['/x/a'] == b'old'
    assert g.peq_save(b'/x/a', r(p), 1) == 1 and card.files['/x/a'] != b'old'


def test_peq_save_refuses_an_invalid_preset(fw):
    g = api(fw)
    card = Card(fw)
    p = preset(g)
    p.count = 99
    assert g.peq_save(b'/x/a', r(p), 1) == 0 and not card.files


def test_a_failed_sync_leaves_the_old_file_and_no_temporary(fw):
    g = api(fw)
    card = Card(fw, {'/x/a': b'old'})
    card.bad_fsync = True
    assert g.peq_save(b'/x/a', r(preset(g)), 1) == 0
    assert card.files == {'/x/a': b'old'} and card.unlinked == ['/x/a.tmp']


def test_peq_save_refuses_a_path_too_long_for_its_temporary(fw):
    g = api(fw)
    card = Card(fw)
    assert g.peq_save(b'/' + b'x' * 579, r(preset(g)), 1) == 1
    assert g.peq_save(b'/' + b'x' * 580, r(preset(g)), 1) == 0


def test_peq_load_of_no_file(fw):
    g = api(fw)
    Card(fw)
    assert not g.peq_load(b'/none', r(Preset()))


def test_peq_load_a_v2_file_from_before_automatic(fw):
    g = api(fw)
    p = preset(g, (PK, 1000, 6, 1), automatic=2)
    Card(fw, {'/a': magic(2) + bytes(p)[:V2]})
    q = Preset()
    assert g.peq_load(b'/a', r(q)) and q.automatic == 0 and q.count == 1 and q.bands[0].gain == 6


def test_peq_load_a_v1_file_has_ten_bands_and_no_balance(fw):
    g = api(fw)
    p = preset(g, *[(PK, 100 + k, 1, 1) for k in range(10)])
    Card(fw, {'/a': magic(1) + bytes(p)[:V1_BANDS]})
    q = Preset()
    assert g.peq_load(b'/a', r(q))
    assert q.bands[9].frequency == 109 and q.balance == 0 and q.automatic == 0
    assert q.bands[10].frequency == 1000 and q.bands[10].q == 1.41  # the defaults


@pytest.mark.parametrize('data', (
    b'', magic(2), b'Q2PEQ09\0' + bytes(C.sizeof(Preset)), magic(1) + bytes(V1_BANDS + 1), magic(2) + bytes(V2 - 1),
    magic(2) + bytes(V2 + 4), magic(2) + bytes(C.sizeof(Preset) + 1)))
def test_peq_load_refuses_any_other_size_or_magic(fw, data):
    g = api(fw)
    Card(fw, {'/a': data})
    q = preset(g, preamp=-9)
    before = bytes(q)
    assert not g.peq_load(b'/a', r(q)) and bytes(q) == before


def test_peq_load_refuses_a_valid_file_with_bad_values(fw):
    g = api(fw)
    p = preset(g)
    p.bypass = 2
    Card(fw, {'/a': magic(2) + bytes(p)})
    assert not g.peq_load(b'/a', r(Preset()))


def test_peq_load_a_read_error_or_failed_close_is_no_file(fw):
    g = api(fw)
    card = Card(fw, {'/a': magic(2) + bytes(preset(g))})
    card.bad_ferror = True
    assert not g.peq_load(b'/a', r(Preset()))
    card.bad_ferror, card.bad_fclose = False, True
    assert not g.peq_load(b'/a', r(Preset()))


def hostile_files(g, rng):
    good = preset(g, *[(PK, 100 + k, 1, 1) for k in range(rng.randrange(0, 31))])
    kind = rng.randrange(5)
    if kind == 0:
        return rng.randbytes(rng.choice((0, 1, 7, 8, 9, 100, V2 + 8, 992 + 8)))
    if kind == 1:
        return magic(rng.randrange(1, 4)) + rng.randbytes(rng.choice((V1_BANDS, V2, 992, 2 * V2 + 8, 2 * 992 + 8)))
    data = bytearray(magic(2) + bytes(good))
    for _ in range(rng.randrange(1, 6)):
        data[rng.randrange(len(data))] = rng.randrange(256)
    if kind == 3:
        data = data[:rng.randrange(len(data))]
    if kind == 4:
        data = data + rng.randbytes(rng.randrange(1, 9))
    return bytes(data)


def test_peq_load_hostile_sweep(fw):
    g, rng = api(fw), random.Random(8)
    card = Card(fw)
    ok = 0
    for _ in range(2000):
        card.files = {'/a': hostile_files(g, rng)}
        card.open = {}
        out = Preset()
        out.count = -5
        if g.peq_load(b'/a', r(out)):
            ok += 1
            assert g.peq_valid(r(out))
        else:
            assert out.count == -5
    assert ok > 10


def state(g, mode=EQ_PEQ, hold=0):
    s = EqState(mode, hold)
    s.peq = preset(g, (PK, 1000, 6, 1), preamp=-1, automatic=2)
    g.eq3_bands(r(s.eq3), (C.c_double * 3)(1, 2, 3))
    s.eq3.preamp = -3
    return s


def test_eq_state_saves_and_loads_back_whole(fw):
    g = api(fw)
    card = Card(fw)
    s = state(g)
    assert g.eq_save(r(s))
    assert card.files[ACTIVE] == magic(3) + bytes(s)
    t = EqState()
    assert g.eq_load(r(t)) == 1 and bytes(t) == bytes(s)


@pytest.mark.parametrize('field, value', (('mode', 3), ('mode', -1), ('hold', 2)))
def test_eq_save_refuses_a_bad_state(fw, field, value):
    g = api(fw)
    card = Card(fw)
    s = state(g)
    setattr(s, field, value)
    assert not g.eq_save(r(s)) and not card.files


def test_eq_save_refuses_a_3eq_of_another_size(fw):
    g = api(fw)
    Card(fw)
    s = state(g)
    s.eq3.count = 2
    assert not g.eq_save(r(s))


def test_eq_load_no_file_is_the_defaults(fw):
    g = api(fw)
    Card(fw)
    s = EqState()
    C.memset(r(s), 0xff, C.sizeof(s))
    assert g.eq_load(r(s)) == 0
    assert s.mode == EQ_OFF and s.hold == 0 and s.peq.count == 10 and s.peq.bypass == 1 and s.eq3.count == 3
    assert [s.eq3.bands[i].gain for i in range(3)] == [0, 0, 0]


def test_eq_load_the_1_3_5_record_moves_3eq_into_place(fw):
    g = api(fw)
    s = state(g, EQ_3)
    raw = bytes(s)
    old = raw[:8] + raw[8:8 + V2] + raw[8 + 992:8 + 992 + V2]
    Card(fw, {ACTIVE: magic(3) + old})
    t = EqState()
    assert g.eq_load(r(t)) == 1
    assert t.mode == EQ_3 and t.peq.automatic == 0 and t.peq.spare == 0 and t.eq3.automatic == 0
    assert t.eq3.preamp == -3 and t.eq3.count == 3 and t.eq3.bands[2].gain == 3 and t.peq.bands[0].gain == 6


@pytest.mark.parametrize('bypass, mode', ((0, EQ_PEQ), (1, EQ_OFF)))
def test_eq_load_a_preset_from_before_3eq_is_its_peq(fw, bypass, mode):
    g = api(fw)
    p = preset(g, (PK, 1000, 6, 1), bypass=bypass)
    Card(fw, {ACTIVE: magic(2) + bytes(p)})
    s = EqState()
    assert g.eq_load(r(s)) == 1
    assert s.mode == mode and s.peq.bands[0].gain == 6 and s.eq3.count == 3


def test_eq_load_an_invalid_state_is_the_defaults(fw):
    g = api(fw)
    s = state(g)
    s.mode = 9
    Card(fw, {ACTIVE: magic(3) + bytes(s)})
    t = EqState()
    assert g.eq_load(r(t)) == 0 and t.mode == EQ_OFF and t.peq.count == 10


def test_eq_load_hostile_sweep(fw):
    g, rng = api(fw), random.Random(9)
    card = Card(fw)
    good = magic(3) + bytes(state(g))
    worked = 0
    for k in range(2000):
        kind = k % 4
        if kind == 0:
            data = hostile_files(g, rng)
        else:
            data = bytearray(good)
            for _ in range(rng.randrange(1, 5)):
                data[rng.randrange(len(data))] = rng.randrange(256)
            if kind == 2:
                del data[rng.randrange(len(data)):]
            if kind == 3:
                data = magic(3) + bytes(data[8:8 + 2 * V2 + 8])
            data = bytes(data)
        card.files = {ACTIVE: data}
        card.open = {}
        s = EqState()
        if g.eq_load(r(s)):
            worked += 1
            assert 0 <= s.mode <= 2 and s.hold in (0, 1) and g.peq_valid(r(s.peq)) and g.peq_valid(r(s.eq3))
        else:
            assert s.mode == EQ_OFF and s.peq.count == 10 and s.eq3.count == 3
    assert worked > 10


def test_peq_load_active_is_the_effective_preset_even_without_a_file(fw):
    g = api(fw)
    card = Card(fw)
    p = Preset()
    assert g.peq_load_active(r(p)) == 0 and p.bypass == 1
    s = state(g, EQ_3)
    card.files[ACTIVE] = magic(3) + bytes(s)
    card.open = {}
    assert g.peq_load_active(r(p)) == 1 and p.count == 3 and p.preamp == -3 and not p.bypass


def test_import_a_file(fw):
    g = api(fw)
    Card(fw, {'/mnt/mmc/EQ/a.txt': b'Preamp: -3 dB\n'})
    p, err = Preset(), Error()
    assert g.peq_import_file(b'/mnt/mmc/EQ/a.txt', r(p), r(err)) and p.preamp == -3


def test_import_no_file_says_so(fw):
    g = api(fw)
    Card(fw)
    err = Error()
    assert not g.peq_import_file(b'/none', r(Preset()), r(err))
    assert err.line == 1 and b'cannot open' in err.reason


def test_import_a_read_error_says_so(fw):
    g = api(fw)
    card = Card(fw, {'/a': b'Preamp: 1 dB\n'})
    card.bad_ferror = True
    err = Error()
    assert not g.peq_import_file(b'/a', r(Preset()), r(err)) and b'read failed' in err.reason


def test_import_a_file_of_16385_bytes_is_too_long(fw):
    g = api(fw)
    Card(fw, {'/a': b'\n' * 16385})
    err = Error()
    assert not g.peq_import_file(b'/a', r(Preset()), r(err)) and b'16384' in err.reason


@pytest.mark.parametrize('power, want', ((1, 0), (10, 10), (100, 20), (.5, -3.0103)))
def test_decibels_is_10_log10(fw, power, want):
    assert api(fw).decibels(power) == pytest.approx(want, abs=1e-4)
