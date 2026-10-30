#!/usr/bin/env python3
"""Run the actual C parser/storage and PCM DSP checks."""
import cmath
import ctypes as C
import math
import pathlib
import subprocess
import tempfile
import sys; sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
from build import FUNCTIONS, ROOT
from peq import LIBC

PROTOTYPES = {**FUNCTIONS, **LIBC}  # the stock calls the host shims stand in for

class Band(C.Structure):
    _fields_ = [('enabled', C.c_int), ('type', C.c_int), ('frequency', C.c_double),
                ('gain', C.c_double), ('q', C.c_double)]

class Preset(C.Structure):
    _fields_ = [('count', C.c_int), ('bypass', C.c_int), ('preamp', C.c_double), ('bands', Band * 30),
                ('balance', C.c_double), ('automatic', C.c_int), ('spare', C.c_int)]
V2 = Preset.automatic.offset  # a preset as 1.3.5 saved it

class State(C.Structure):
    _fields_ = [('mode', C.c_int), ('hold', C.c_int), ('peq', Preset), ('eq3', Preset)]
EQ_OFF, EQ_3, EQ_PEQ = range(3)

class Error(C.Structure):
    _fields_ = [('line', C.c_uint), ('reason', C.c_char_p)]

class Coeff(C.Structure):
    _fields_ = [(k, C.c_double) for k in ('b0', 'b1', 'b2', 'a1', 'a2')]

class Engine(C.Structure):
    _fields_ = [('c', Coeff * 30), ('z', ((C.c_double * 2) * 30) * 8),
                ('gain', C.c_double * 8), ('bypass', C.c_int), ('used', C.c_int), ('only', C.c_int * 30)]

class DSP(C.Structure):
    _fields_ = [('current', Engine), ('next', Engine), ('pending', Engine),
                *[(k, C.c_int) for k in ('rate', 'channels', 'ramp', 'ramp_length', 'waiting')]]

def compile_host(tmp, name, *sources):
    path = tmp/name
    subprocess.run(['cc', '-DPEQ_HOST', f'-DPEQ_ROOT="{tmp}/root"', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-shared', '-fPIC', *map(str, sources), '-lm', '-o', str(path)], check=True)
    return C.CDLL(str(path))

def library(tmp):
    lib = compile_host(tmp, 'peq.so', ROOT/'patch/peq.c')
    signatures = {
        'peq_default': [C.POINTER(Preset)], 'peq_valid': [C.POINTER(Preset)],
        'peq_number': [C.c_char_p, C.POINTER(C.c_double)],
        'peq_parse': [C.c_char_p, C.c_uint, C.POINTER(Preset), C.POINTER(Error)],
        'peq_import_file': [C.c_char_p, C.POINTER(Preset), C.POINTER(Error)],
        'peq_save': [C.c_char_p, C.POINTER(Preset), C.c_int],
        'peq_load': [C.c_char_p, C.POINTER(Preset)],
        'peq_compile': [C.POINTER(Preset), C.c_int, C.POINTER(Engine)],
        'peq_reset': [C.POINTER(DSP), C.c_int, C.c_int, C.POINTER(Preset)],
        'peq_update': [C.POINTER(DSP), C.POINTER(Preset)],
        'peq_process': [C.POINTER(DSP), C.POINTER(C.c_float), C.c_uint],
        'eq3_bands': [C.POINTER(Preset), C.POINTER(C.c_double * 3)],
        'eq_load': [C.POINTER(State)], 'eq_save': [C.POINTER(State)],
        'eq_effective': [C.POINTER(State), C.POINTER(Preset)], 'peq_load_active': [C.POINTER(Preset)],
        'peq_auto': [C.POINTER(Preset), C.c_int],
    }
    for name, args in signatures.items(): getattr(lib, name).argtypes = args
    lib.peq_auto.restype = C.c_double
    return lib

def parse(lib, text):
    data = text.encode() if isinstance(text, str) else text
    p, error = Preset(), Error()
    assert lib.peq_parse(data, len(data), C.byref(p), C.byref(error)), (error.line, error.reason, data)
    return p

def parser_check(lib, tmp):
    value = C.c_double()
    for text in (b" 0x1p0", b"\t+0x1p0", b" 1", b"1 "):
        assert not lib.peq_number(text, C.byref(value)), text
    for text, want in ((b"1e3", 1000), (b"-1,5", -1.5), (b"+.7", .7)):
        assert lib.peq_number(text, C.byref(value)) and value.value == want, text
    source = '\ufeff # comment\r\nPreamp : -3 dB # inline\r\nPreamp:\t-2 dB\n'
    aliases = ['PK', 'PEQ', 'LS', 'LSC', 'HS', 'HSC']
    for i, alias in enumerate(aliases):
        source += f'Filter {99-i}: {"ON" if i%2 else "OFF"}\t{alias} Fc {100+i*100} Hz Gain -6 dB'
        source += ' Q 1.3\n' if i < 2 else '\n'
    p = parse(lib, source)
    assert p.count == 6 and p.preamp == -5 and p.bypass == 0
    assert [b.type for b in p.bands[:6]] == [0, 0, 1, 1, 2, 2]
    assert [b.enabled for b in p.bands[:6]] == [0, 1, 0, 1, 0, 1]
    assert [b.frequency for b in p.bands[:6]] == [100, 200, 300, 400, 500, 600]
    a = 10 ** (-6 / 40)  # APO: shelf without Q is slope 0.9 at Fc
    assert abs(p.bands[2].q - 1 / math.sqrt((a + 1/a) * (1/0.9 - 1) + 2)) < 1e-15
    # APO: LS/HS with Q give a corner frequency (S=1 at Q 0.7071 -> shift 10^(gain/80)); LSC/HSC the centre.
    shelves = parse(lib, 'Filter: ON LS Fc 100 Hz Gain 12 dB Q 0.7071067811865476\n'
                         'Filter: ON HS Fc 8000 Hz Gain -12 dB Q 0.7071067811865476\n'
                         'Filter: ON LSC Fc 100 Hz Gain 12 dB Q 0.7\n'
                         'Filter 11: OFF None\nFilter: ON PK Fc 1000,5 Hz Gain -3,5 dB Q 1,41\n')
    assert abs(shelves.bands[0].frequency - 100 * 10**(12/80)) < 1e-9
    assert abs(shelves.bands[1].frequency - 8000 / 10**(12/80)) < 1e-9
    assert shelves.bands[2].frequency == 100 and shelves.count == 4
    assert (shelves.bands[3].frequency, shelves.bands[3].gain, shelves.bands[3].q) == (1000.5, -3.5, 1.41)
    edges = parse(lib, 'Filter: ON LS Fc 19000 Hz Gain 12 dB Q 0.7\nFilter: ON HS Fc 21 Hz Gain -12 dB Q 0.7\n')
    assert (edges.bands[0].frequency, edges.bands[1].frequency) == (20000, 20)
    single = 'Filter: ON PK Fc 1e3 Hz Gain +6.0 dB Q .7\n'
    assert parse(lib, single).preamp == 0
    assert parse(lib, 'Preamp: 24 dB\nPreamp: 24 dB\nPreamp: -48 dB').preamp == 0
    assert parse(lib, single * 30).count == 30
    assert parse(lib, single + 'Filter 2: OFF PK Fc 0 Hz Gain 0.0 dB Q 0.000').count == 1  # blank slot
    assert parse(lib, '').count == 0
    # Import is pure. Saving succeeds only after validation and explicit collision confirmation.
    active, saved = tmp/'active', tmp/'preset'
    for path in (active, saved): assert lib.peq_save(bytes(path), C.byref(p), 0) == 1
    baseline = bytes(p)
    failures = [
        (single * 30 + single.replace('ON', 'OFF'), 31, '30 bands'),
        ('Preamp: 24 dB\nPreamp: 1 dB', 2, 'summed'),
        ('Preamp: -61 dB', 1, 'preamp'),
        ('Preamp: -3', 1, 'expected'),
        ('\nGraphicEQ: 20 0; 20000 0', 2, 'unsupported'),
        ('Include: other.txt', 1, 'unsupported'), ('Channel: C', 1, 'only channels'), ('Channel:', 1, 'expected Channel'),
        ('Channel: L\nPreamp: -13 dB', 2, 'differ'),
        ('Eval: x=1', 1, 'unsupported'), ('garbage', 1, 'unsupported'),
        (single.replace(' Q .7', ''), 1, 'requires Q'),
        (single.replace('PK', 'HP'), 1, 'type'),
        (single.replace('PK', 'LS 6dB'), 1, 'form'),
        (single.replace('Q .7', 'BW Oct 1'), 1, 'form'),
        (single.replace('1e3', '`1000`'), 1, 'number'),
        (single.replace('Filter:', 'Filter x:'), 1, 'label'),
        (single + 'Filter: ON PK Fc 19 Hz Gain 0 dB Q 1', 2, 'range'),
        (b'\0', 1, 'NUL'), (b'#' * 513, 1, 'line'), (b'#' * 16385, 1, 'file'),
    ]
    for old, values in [('1e3', ['nan', 'inf', '-inf', '1e999', '20junk', '0x100', '20001', '-20']),
                        ('+6.0', ['nan', '25', '-25']), ('.7', ['NaN', '0', '-1', '10.1'])]:
        failures += [(single.replace(old, value), 1, 'number' if value in ('nan', 'inf', '-inf', '1e999', '20junk', '0x100', 'NaN') else 'range') for value in values]
    for text, line, reason in failures:
        data = text.encode() if isinstance(text, str) else text
        error = Error()
        assert not lib.peq_parse(data, len(data), C.byref(p), C.byref(error)), data
        assert error.line == line and reason in error.reason.decode(), (data, error.line, error.reason)
        assert bytes(p) == baseline
        assert active.read_bytes() == saved.read_bytes()
    q = parse(lib, single)
    before = saved.read_bytes()
    assert lib.peq_save(bytes(saved), C.byref(q), 0) == 2
    assert saved.read_bytes() == before  # cancel
    assert lib.peq_save(bytes(saved), C.byref(q), 1) == 1  # confirm
    assert active.read_bytes() == before  # never auto-activate
    source_file = tmp/'input.txt'
    source_file.write_text(single)
    assert lib.peq_import_file(bytes(source_file), C.byref(p), C.byref(Error()))
    source_file.unlink()
    assert lib.peq_load(bytes(saved), C.byref(p)) and bytes(p) == bytes(q)
    unchanged = bytes(p)
    for path in (source_file, tmp):
        e = Error()
        assert not lib.peq_import_file(bytes(path), C.byref(p), C.byref(e))
        assert e.line and e.reason and bytes(p) == unchanged
    saved.write_bytes(saved.read_bytes()[:-1])
    assert not lib.peq_load(bytes(saved), C.byref(p)) and bytes(p) == unchanged
    # APO Channel scopes: bands follow it, per-channel preamps become preamp + balance.
    scoped = parse(lib, 'Preamp: -3 dB\nChannel: L\nPreamp: -1.5 dB\n' + single + 'Channel: R\nPreamp: -0.5 dB\n'
                        + single + 'Channel: L R\n' + single + 'Channel: all\n' + single.replace('ON', 'OFF'))
    assert [b.enabled for b in scoped.bands[:4]] == [2, 3, 1, 0]
    assert parse(lib, single * 30 + 'Filter: OFF PK Fc 0 Hz Gain 0 dB Q 0').count == 30  # blank slots do not count
    assert scoped.preamp == -3.5 and scoped.balance == 1  # left 1 dB below right
    # A file's Preamp line is kept as picked (2); without one the preamp stays Auto (1), so boosts never clip.
    assert scoped.automatic == 2 and parse(lib, single).automatic == 1
    # v1 files (no balance) still load, with the balance centred.
    v1 = tmp/'v1'
    q.automatic = 0  # older files lack it; parse sets 2
    v1.write_bytes(b'Q2PEQ01\0' + bytes(q)[:16 + 10*32])
    assert lib.peq_load(bytes(v1), C.byref(p)) and bytes(p) == bytes(q)  # q's bands 11-30 are the defaults
    v1.write_bytes(b'Q2PEQ02\0' + bytes(q)[:V2])  # 1.3.5's, before automatic: unknown (0)
    assert lib.peq_load(bytes(v1), C.byref(p)) and bytes(p) == bytes(q) and p.automatic == 0
    v1.write_bytes(b'Q2PEQ02\0' + bytes(q)[:V2 - 8])
    assert not lib.peq_load(bytes(v1), C.byref(p))
    v1.write_bytes(b'')
    assert not lib.peq_load(bytes(v1), C.byref(p))
    assert not lib.peq_save(bytes(tmp/'missing'/'preset'), C.byref(q), 1)
    print('PEQ parser/storage: syntax, limits, transaction failures, confirmation and persistence passed.')

def response(engine, rate, frequency, ch=0):
    if engine.bypass: return 1
    z = cmath.exp(-2j * math.pi * frequency / rate)
    result = complex(engine.gain[ch])
    for c, only in zip(engine.c, engine.only):
        if not only or only == ch + 1: result *= (c.b0 + c.b1*z + c.b2*z*z)/(1 + c.a1*z + c.a2*z*z)
    return abs(result)

def process(lib, d, values, channels=1):
    audio = (C.c_float * len(values))(*values)
    lib.peq_process(C.byref(d), audio, len(values)//channels)
    return list(audio)

def dsp_check(lib):
    p = parse(lib, 'Preamp: -6 dB\nFilter: ON PK Fc 1000 Hz Gain 6 dB Q 1')
    # The manual and imported representation use exactly the same compiler.
    manual = Preset(); lib.peq_default(C.byref(manual))
    manual.count, manual.bypass, manual.preamp = 1, 0, -6
    manual.bands[0] = Band(1, 0, 1000, 6, 1)
    for rate in (8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000):
        a, b = Engine(), Engine()
        assert lib.peq_compile(C.byref(p), rate, C.byref(a))
        assert lib.peq_compile(C.byref(manual), rate, C.byref(b))
        assert bytes(a) == bytes(b)
        assert abs(20 * math.log10(response(a, rate, 1000))) < 1e-7
        d = DSP(); lib.peq_reset(C.byref(d), rate, 1, C.byref(p))
        samples = [0.05*math.sin(2*math.pi*1000*i/rate) for i in range(rate//5)]
        out = process(lib, d, samples)
        gain = math.sqrt(sum(x*x for x in out[rate//10:])/sum(x*x for x in samples[rate//10:]))
        assert abs(20*math.log10(gain)) < .01, (rate, gain)
    # Every band, both shelf endpoints, gain/Q limits and low-rate fallback.
    for index in range(10):
        p = Preset(); lib.peq_default(C.byref(p)); p.bypass = 0
        p.bands[index].enabled = 1; p.bands[index].gain = -6
        e = Engine(); assert lib.peq_compile(C.byref(p), 48000, C.byref(e))
        assert abs(20*math.log10(response(e, 48000, p.bands[index].frequency)) + 6) < 1e-7
    for kind in (1, 2):
        for q in (0.1, 1/math.sqrt(2), 10):
            for gain in (-24, 24):
                p.bands[9] = Band(1, kind, 1000, gain, q)
                e = Engine(); assert lib.peq_compile(C.byref(p), 48000, C.byref(e))
                assert abs(20*math.log10(response(e, 48000, 0 if kind == 1 else 24000))-gain) < 1e-7
                for c in e.c:
                    assert abs(c.a2) < 1 and 1+c.a1+c.a2 > 0 and 1-c.a1+c.a2 > 0
    p.bands[9] = Band(1, 0, 16000, 6, 1)
    e = Engine(); assert lib.peq_compile(C.byref(p), 8000, C.byref(e)) and not e.bypass
    assert (e.c[9].b0, e.c[9].a1) == (1, 0)  # only the unrepresentable band is skipped
    p = parse(lib, '\n'.join(f'Filter: ON PK Fc {100+i*1000} Hz Gain 6 dB Q 1' for i in range(10)))
    p.automatic = 2  # a picked 0 dB preamp, not the file's Auto: the boosts pass full scale unclamped
    d = DSP(); lib.peq_reset(C.byref(d), 48000, 2, C.byref(p))
    boosted = process(lib, d, [1., -1.] * 20000, 2)
    assert all(math.isfinite(x) for x in boosted) and max(boosted) > 1 and min(boosted) < -1
    # Block splitting and stereo independence; no file I/O or allocation in process.
    a, b = DSP(), DSP()
    for d in (a, b): lib.peq_reset(C.byref(d), 48000, 2, C.byref(p))
    signal = [x for i in range(10000) for x in (0.02*math.sin(i*.13), 0.)]
    entire = process(lib, a, signal, 2)
    split = process(lib, b, signal[:2346], 2) + process(lib, b, signal[2346:], 2)
    assert entire == split and all(x == 0 for x in entire[1::2])
    p.bypass = 1
    assert lib.peq_update(C.byref(a), C.byref(p))
    process(lib, a, [0., 0.] * 960, 2)
    assert process(lib, a, signal, 2) == list((C.c_float * len(signal))(*signal))
    # A settled bypass preserves every bit, even NaNs, signed zero and out-of-range input.
    bits = (C.c_uint32 * 6)(0x7fc00001, 0x80000000, 0x7f800000, 0xff800000, 0x40000000, 0xc0000000)
    before = bytes(bits), bytes(a)
    lib.peq_process(C.byref(a), C.cast(bits, C.POINTER(C.c_float)), 3)
    assert (bytes(bits), bytes(a)) == before
    # Rapid updates queue the latest target without restarting the current fade.
    p.bypass = 0
    assert lib.peq_update(C.byref(a), C.byref(p))
    process(lib, a, [0., 0.] * 100, 2)
    old_ramp = a.ramp
    p.preamp = -24
    assert lib.peq_update(C.byref(a), C.byref(p)) and a.ramp == old_ramp
    process(lib, a, [0., 0.] * 2000, 2)
    assert not a.ramp and not a.waiting and abs(a.current.gain[0]-10**(-24/20)) < 1e-12
    lib.peq_reset(C.byref(a), 96000, 1, C.byref(p))
    assert process(lib, a, [0.] * 1000) == [0.] * 1000
    # Decaying tails settle at a ~-590 dB normal-float residue, never in the denormal range.
    tail = process(lib, a, [0.5] + [0.] * 96000)
    state = [abs(a.current.z[0][i][j]) for i in range(30) for j in range(2)]
    assert all(z < 1e-28 and (z == 0 or z > 1e-300) for z in state) and abs(tail[-1]) < 1e-28
    # A preamp-only change carries filter memory: the crossfade is a pure gain ramp.
    lib.peq_reset(C.byref(a), 48000, 1, C.byref(p))
    tone = [0.1*math.sin(2*math.pi*100*i/48000) for i in range(20000)]
    process(lib, a, tone[:10000])
    p.preamp = -30; assert lib.peq_update(C.byref(a), C.byref(p))
    after = process(lib, a, tone[10000:])
    b = DSP(); p.preamp = -24; lib.peq_reset(C.byref(b), 48000, 1, C.byref(p))
    reference = process(lib, b, tone)[10000:]
    fade = [1 - (1 - 10**(-6/20)) * min(i + 1, 960) / 960 for i in range(10000)]
    assert max(abs(x - r*f) for x, r, f in zip(after, reference, fade)) < 1e-6
    # A band switched off mid-play drains its carried memory before it is skipped, then passes x through.
    p = parse(lib, 'Filter: ON PK Fc 100 Hz Gain 6 dB Q 1'); p.automatic = 2  # 0 dB picked, as below
    lib.peq_reset(C.byref(a), 48000, 1, C.byref(p))
    process(lib, a, tone[:10000])
    p.bands[0].gain = 0; assert lib.peq_update(C.byref(a), C.byref(p))
    process(lib, a, tone[:2000])
    assert all(a.current.z[0][i][j] == 0 for i in range(30) for j in range(2)) and a.current.used == 1
    assert process(lib, a, tone) == list((C.c_float * len(tone))(*tone))
    # One-channel bands and balance: left gets the band and 2 dB less, right neither.
    p = parse(lib, 'Channel: L\nFilter: ON PK Fc 1000 Hz Gain 6 dB Q 1'); p.balance, p.automatic = 2, 2
    e = Engine(); assert lib.peq_compile(C.byref(p), 48000, C.byref(e))
    assert abs(20*math.log10(response(e, 48000, 1000, 0)) - 4) < 1e-7
    assert abs(20*math.log10(response(e, 48000, 1000, 1))) < 1e-12
    lib.peq_reset(C.byref(a), 48000, 2, C.byref(p))
    out = process(lib, a, [x for v in tone for x in (v, v)], 2)
    assert out[1::2] == list((C.c_float * len(tone))(*tone))
    gain = math.sqrt(sum(x*x for x in out[10000::2])/sum(x*x for x in tone[5000:]))
    assert abs(20*math.log10(gain) + 2) < .1, gain  # 100 Hz: the band barely reaches it
    # A both-channel band narrowed to the left drops the right's carried memory.
    p.bands[0].enabled = 1; lib.peq_reset(C.byref(a), 48000, 2, C.byref(p)); process(lib, a, [.5, .5] * 100, 2)
    p.bands[0].enabled = 2; assert lib.peq_update(C.byref(a), C.byref(p)); process(lib, a, [0., 0.] * 2000, 2)
    assert a.current.z[1][0][0] == a.current.z[1][0][1] == 0 and a.current.z[0][0][0]
    # Non-finite input is still sanitized while EQ is on.
    p = parse(lib, 'Preamp: 6 dB')
    lib.peq_reset(C.byref(a), 48000, 1, C.byref(p))
    assert process(lib, a, [math.nan, math.inf, -math.inf, 0.25]) == [0, 0, 0, C.c_float(.25*10**(.3)).value]
    # A preamp/bypass transition also retains float headroom, with no flattened peaks.
    bypass = parse(lib, 'Preamp: 0 dB'); bypass.bypass = 1
    assert lib.peq_update(C.byref(a), C.byref(bypass))
    ramp = process(lib, a, [1.5] * 1000)
    assert min(ramp) == 1.5 and max(ramp) > 2.9
    print('PEQ DSP: C PCM response, 30 bands, shelves, rates, bypass, float headroom, channels, balance and updates passed.')

REPORTED_PRESET = """Channel: L
Preamp: -2.2 dB
Filter 1: ON LSC Fc 3936 Hz Gain -0.40 dB Q 0.509
Filter 2: ON HSC Fc 15939 Hz Gain 2.88 dB Q 2.916
Filter 3: ON PK Fc 12705 Hz Gain 6.92 dB Q 2.292
Filter 4: ON PK Fc 13150 Hz Gain -4.65 dB Q 4.000
Filter 5: ON PK Fc 15172 Hz Gain 9.66 dB Q 2.078
Filter 6: ON PK Fc 15331 Hz Gain -4.33 dB Q 0.569
Filter 7: ON PK Fc 10160 Hz Gain 6.86 dB Q 1.401
Filter 8: ON PK Fc 15627 Hz Gain -5.71 dB Q 0.577
Channel: R
Preamp: -2.2 dB
Filter 1: ON LSC Fc 3936 Hz Gain 0.40 dB Q 0.509
Filter 2: ON HSC Fc 15939 Hz Gain -2.88 dB Q 2.916
Filter 3: ON PK Fc 12705 Hz Gain -6.92 dB Q 2.292
Filter 4: ON PK Fc 13150 Hz Gain 4.65 dB Q 4.000
Filter 5: ON PK Fc 15172 Hz Gain -9.66 dB Q 2.078
Filter 6: ON PK Fc 15331 Hz Gain 4.33 dB Q 0.569
Filter 7: ON PK Fc 10160 Hz Gain -6.86 dB Q 1.401
Filter 8: ON PK Fc 15627 Hz Gain 5.71 dB Q 0.577
Channel: all
Preamp: -6.3 dB
Filter 1: ON LSC Fc 32 Hz Gain 6.17 dB Q 0.552
Filter 2: ON PK Fc 51 Hz Gain 2.04 dB Q 0.867
Filter 3: ON PK Fc 204 Hz Gain -3.35 dB Q 0.804
Filter 4: ON PK Fc 385 Hz Gain -0.65 dB Q 1.986
Filter 5: ON PK Fc 787 Hz Gain 1.48 dB Q 1.224
Filter 6: ON PK Fc 1689 Hz Gain -2.24 dB Q 0.890
Filter 7: ON PK Fc 3076 Hz Gain 2.87 dB Q 1.327
Filter 8: ON HSC Fc 5562 Hz Gain 1.14 dB Q 2.018
Filter 9: ON PK Fc 9530 Hz Gain 4.19 dB Q 1.446
Filter 10: ON PK Fc 13560 Hz Gain -0.62 dB Q 2.940
"""

def headroom_check(lib, ui):
    """The reported preset stays linear; PEQ's Auto plays each rate's own cut, and 3EQ's saved headroom covers every
    standard rate, neither changing a set gain or the filters."""
    p = parse(lib, REPORTED_PRESET)
    before = bytes(p)
    assert p.count == 26 and p.preamp == -8.5 and p.balance == 0
    for rate in (44100, 48000, 96000):
        for f in (20, 50, 9840, 15000):
            a, b = DSP(), DSP()
            for d in (a, b): lib.peq_reset(C.byref(d), rate, 2, C.byref(p))
            signal = [x for i in range(rate//5) for x in (0.999*math.sin(2*math.pi*f*i/rate),)*2]
            loud = process(lib, a, signal, 2)
            quiet = process(lib, b, [x/16 for x in signal], 2)
            # Later volume can recover the waveform only if EQ hasn't clipped it first.
            assert max(abs(x/16-y) for x, y in zip(loud, quiet)) < 2e-7, (rate, f)
            if f == 9840 and rate in (44100, 48000):
                assert max(loud[::2]) > 1 and min(loud[::2]) < -1 and max(map(abs, loud[1::2])) < 1
    # PEQ's Auto as the player prepares it (peq_reset, peq_update): the rate's own response, against a
    # denser scan than the engine's 1024 points (8192, with DC, Nyquist and the band centres): within 0.005 dB, the
    # grid's sampling error here (0.003 dB at 44.1 kHz). A steady-state peak, not a transient or intersample bound.
    p.automatic = 1
    for rate, want in ((32000, -12.3492), (44100, -9.2466), (48000, -8.8120), (96000, -7.8743)):
        assert abs(lib.peq_auto(C.byref(p), rate) - want) < 0.005, (rate, lib.peq_auto(C.byref(p), rate))
    assert '%.1f %.1f' % (lib.peq_auto(C.byref(p), 44100), lib.peq_auto(C.byref(p), 48000)) == '-9.2 -8.8'
    for rate in (8000, 32000, 44100, 48000, 50000, 96000, 384000):
        cut = lib.peq_auto(C.byref(p), rate)
        manual = Preset.from_buffer_copy(p); manual.automatic, manual.preamp = 2, cut
        a, b = DSP(), DSP()
        lib.peq_reset(C.byref(a), rate, 2, C.byref(p)); lib.peq_reset(C.byref(b), rate, 2, C.byref(manual))
        e = a.current
        assert bytes(e.c) == bytes(b.current.c) and bytes(e.only) == bytes(b.current.only) and e.used == b.current.used
        assert all(abs(e.gain[ch] - 10**(cut/20)) < 1e-12 and abs(e.gain[ch] - b.current.gain[ch]) < 1e-12 for ch in range(8))
        nyquist = rate / 2
        grid = [0, nyquist] + [nyquist**(i/8191) for i in range(8192)] + [b.frequency for b in p.bands[:p.count]]
        peak = max(response(e, rate, f, ch) for f in grid if f <= nyquist for ch in range(2))**2
        assert 10*math.log10(peak) < 0.005, (rate, peak)
        signal = [0.5*math.sin(i*0.37) for i in range(2048)]
        assert max(abs(x-y) for x, y in zip(process(lib, a, signal, 2), process(lib, b, signal, 2))) < 1e-6, rate
        # A set preamp plays as set at every rate, though it is the saved preset's -8.5 dB.
        manual.preamp = -8.5; lib.peq_reset(C.byref(b), rate, 2, C.byref(manual))
        assert all(b.current.gain[ch] == 10**(-8.5/20) for ch in range(2)) and bytes(b.current.c) == bytes(e.c)
    p.automatic = 2  # as imported
    assert bytes(p) == before
    ui.check_headroom.argtypes = [C.POINTER(Preset)]
    ui.check_headroom.restype = C.c_double
    auto = ui.check_headroom(C.byref(p))  # 3EQ's: the loudest standard rate's
    assert -12.37 < auto < -12.34, auto  # strongest response is at 32 kHz, not the old 48 kHz estimate
    assert bytes(p) == before and ui.check_headroom(C.byref(p)) == auto
    p.preamp = auto
    for rate in (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100,
                 48000, 64000, 88200, 96000, 176400, 192000, 352800, 384000):
        e = Engine(); assert lib.peq_compile(C.byref(p), rate, C.byref(e))
        peak = max(response(e, rate, f, ch)
                   for f in [0] + [(rate/2)**(i/4095) for i in range(4096)] for ch in range(2))
        assert peak <= 1.00001, (rate, peak, auto)
    # Flat/cut-only Auto stays +0; shelves include DC and Nyquist outside the old 20..20k grid.
    for source, want in (('', 0), ('Filter: ON PK Fc 1000 Hz Gain -12 dB Q 1', 0),
                         ('Filter: ON LSC Fc 20 Hz Gain 6 dB Q 0.707', -6),
                         ('Filter: ON HSC Fc 20000 Hz Gain 6 dB Q 0.707', -6),
                         ('Filter: ON PK Fc 3999 Hz Gain 12 dB Q 10', -12)):
        p = parse(lib, source); actual = ui.check_headroom(C.byref(p))
        assert abs(actual-want) < .0001, (source, actual)
        if not want: assert math.copysign(1, actual) == 1
    print('PEQ headroom: reported 26-band preset, unclipped float output, per-rate Auto and 3EQ across standard PCM rates passed.')

# Host stand-ins for the stock services peq_platform.h maps on the device.
SHIM_H = r"""
#include <assert.h>
#include <dirent.h>
#include <sys/stat.h>
typedef int (*handler)(void *, void *);
extern volatile unsigned char g_equalizer_flag, g_backlight_status;
""" + ''.join(f'{r} {n}({a});\n' for n in """
list_item_create label_create list_view_create scroll_view_create widget_use_style
widget_set_text_utf8 widget_on widget_get_prop_int widget_set_prop_int widget_destroy_children
widget_resize scroll_view_set_offset widget_invalidate_force timer_add timer_remove
navigator_back write_int_config widget_factory widget_factory_create_widget widget_set_prop_str
widget_get_text_utf8 widget_set_focused widget_lookup window_manager pages_set_active_by_name edit_set_select
input_method input_method_request widget_set_name
canvas_set_fill_color canvas_fill_rect canvas_get_vgcanvas vgcanvas_save vgcanvas_restore vgcanvas_translate
vgcanvas_begin_path vgcanvas_close_path vgcanvas_move_to vgcanvas_line_to vgcanvas_arc vgcanvas_fill vgcanvas_stroke
vgcanvas_set_fill_color vgcanvas_set_stroke_color vgcanvas_set_fill_linear_gradient vgcanvas_set_line_width
widget_to_local on_wm_keyup_fun
""".split() for r, a in [PROTOTYPES[n]]) + r"""
/* peq_ui.c paints through the stock wrapper, which firmware maps to the real call. */
#define stock_canvas_set_fill_color canvas_set_fill_color
"""
SHIM = r"""
static struct { int parent, h, bg; char text[160]; handler click, destroy, keyup, change, focus, down, move, up; void *ctx; } w[4096];
static int count = 1, timers, removed, backs, chevrons[4096];
static int (*timer_fn)(const void *);
volatile unsigned char g_equalizer_flag, g_backlight_status = 1;
int stock_eq_trampoline(int mode) { return mode; }
static void *make(void *parent, int h) {
    ++count; w[count].parent = (int)(long)parent; w[count].h = h;
    w[count].text[0] = 0; chevrons[count] = 0; w[count].click = w[count].change = w[count].focus = w[count].down = 0; w[count].bg = 0; return (void *)(long)count;
}
/* The value menu's edit: its text, the keyboard it asks for, whether it took focus. */
static int edit_widget, edit_parent, edit_bottom, focused, keyboard_owner;
#include "offsets.inc"
/* The input method's editing target (IM_WIDGET) and open keyboard (IM_KEYBOARD, stock's window 4000). */
static unsigned char im[32];
static char edit_name[16];
void *input_method(void) { return im; }
int input_method_request(void *x, void *edit) {
    keyboard_owner = (int)(long)edit;
    P(im, IM_WIDGET) = edit, P(im, IM_KEYBOARD) = edit ? (void *)4000L : 0;
    return 0;
}
int widget_set_name(void *x, const char *name) { if ((long)x == edit_widget) snprintf(edit_name, sizeof edit_name, "%s", name); return 0; }
const char *shim_edit_name(void) { return edit_name; }
int shim_keyboard_owner(void) { return keyboard_owner; }
void shim_dismiss_keyboard(void) { input_method_request(im, 0); }
static char keyboard[32], input_type[16];
void *widget_factory(void) { return 0; }
void *widget_factory_create_widget(void *f, const char *type, void *p, int x, int y, int ww, int h) {
    void *e = make(p, h);
    if (!strcmp(type, "edit")) { edit_name[0] = 0; edit_widget = (int)(long)e; edit_parent = (int)(long)p; edit_bottom = y + h; keyboard[0] = 0; focused = 0; }
    return e;
}
/* The curve's vgcanvas drawing (peq_paint) is checked on the device; here it only has to link. Filled
   rectangles land in screen and captions in texts, for 3EQ's captures. */
static unsigned fill_color, screen[290 * 375];
static struct { int r[4]; unsigned px, color; char s[32]; } texts[64];
static int ntexts;
int canvas_set_fill_color(void *c, unsigned v) { fill_color = v; return 0; }
int canvas_fill_rect(void *c, int x, int y, int ww, int h) {
    for (int j = y < 0 ? 0 : y; j < y + h && j < 290; ++j)
        for (int i = x < 0 ? 0 : x; i < x + ww && i < 375; ++i) screen[j * 375 + i] = fill_color;
    return 0;
}
void *canvas_get_vgcanvas(void *c) { return 0; }
#define VG(name, ...) int name(void *vg, ##__VA_ARGS__) { return 0; }
VG(vgcanvas_save) VG(vgcanvas_restore) VG(vgcanvas_begin_path) VG(vgcanvas_close_path) VG(vgcanvas_fill) VG(vgcanvas_stroke)
int vgcanvas_translate(void *vg, float x, float y) { return 0; }
int vgcanvas_move_to(void *vg, float x, float y) { return 0; }
int vgcanvas_line_to(void *vg, float x, float y) { return 0; }
int vgcanvas_arc(void *vg, float x, float y, float r, float a, float b, int ccw) { return 0; }
int vgcanvas_set_fill_color(void *vg, unsigned c) { return 0; }
int vgcanvas_set_stroke_color(void *vg, unsigned c) { return 0; }
int vgcanvas_set_line_width(void *vg, float w) { return 0; }
int vgcanvas_set_fill_linear_gradient(void *vg, float a, float b, float c, float d, unsigned e, unsigned f) {
    return 0;
}
void draw_centred(void *canvas, const unsigned *s, unsigned n, const void *r, unsigned px, unsigned color) {
    if (ntexts == 64) return;
    memcpy(texts[ntexts].r, r, sizeof texts[ntexts].r);
    texts[ntexts].px = px, texts[ntexts].color = color;
    for (unsigned i = 0; i < n && i < 31; ++i) texts[ntexts].s[i] = (char)s[i], texts[ntexts].s[i + 1] = 0;
    ++ntexts;
}
/* iPod builds' theme: Classic's Graphite light tone, or Minimal's fill. */
static int shim_classic_on = 1;
int ipod_classic(void) { return shim_classic_on; }
unsigned accent_tone(int tone) { return shim_classic_on ? 0x6e6e6e : 0xeeeeec; }
void shim_classic(int on) { shim_classic_on = on; }
int widget_set_prop_str(void *x, const char *k, const char *v) {
    if ((long)x == edit_widget && !strcmp(k, "keyboard")) snprintf(keyboard, sizeof(keyboard), "%s", v);
    if ((long)x == edit_widget && !strcmp(k, "input_type")) snprintf(input_type, sizeof(input_type), "%s", v);
    return 0;
}
int widget_get_text_utf8(void *x, char *s, unsigned size) {
    s[0] = 0;
    if (strlen(w[(long)x].text) < size) snprintf(s, size, "%s", w[(long)x].text);
    return 0;
}
int widget_set_focused(void *x, int v) {
    if ((long)x == edit_widget && focused != v) {
        focused = v;
        input_method_request(input_method(), v ? x : 0);
        if (v && w[edit_widget].focus) w[edit_widget].focus(0, 0);
    }
    return 0;
}
/* The stock keyboard window (4000) and its page panel (4001); the page it was switched to. */
static char page[16];
void *window_manager(void) { return (void *)3999L; }
void *widget_lookup(void *x, const char *name, int recursive) {
    if ((long)x == 3999 && !strcmp(name, "kb_default_t9")) return (void *)4000L;
    return (long)x == 4000 && !strcmp(name, "panel") ? (void *)4001L : 0;
}
int pages_set_active_by_name(void *x, const char *name) { if ((long)x == 4001) snprintf(page, sizeof(page), "%s", name); return 0; }
const char *shim_page(void) { return page; }
void shim_page_reset(void) { page[0] = 0; }
/* The edit's selection, as start and end (stock's ufloat refuses "." where its text less this has one). */
static unsigned selected[2];
int edit_set_select(void *x, unsigned start, unsigned end) { selected[0] = start, selected[1] = end; return 0; }
unsigned shim_selected(int i) { return selected[i]; }
void shim_select_reset(void) { selected[0] = selected[1] = 0; }
const char *shim_edit(void) { return edit_widget ? w[edit_widget].text : ""; }
const char *shim_keyboard(void) { return keyboard; }
const char *shim_input_type(void) { return input_type; }
int shim_focused(void) { return focused; }
/* The edit on the page itself, its bottom in page y: T9's window starts at page y 30. */
int shim_edit_clear(void) { return edit_parent == 1 && edit_bottom <= 30; }
void shim_run(void);
/* Return with the keyboard up, as navigation.c's search_key does to an EDIT_NAME edit: emptied, then
 * blurred, which shuts the keyboard and reports EVT_VALUE_CHANGED. */
void shim_cancel(void) {
    w[edit_widget].text[0] = 0, focused = 0;
    input_method_request(im, 0);
    w[edit_widget].change(0, 0); shim_run();
}
/* Typing a value and closing the keyboard: the edit reports EVT_VALUE_CHANGED. */
void shim_type(const char *text) {
    assert(keyboard_owner == edit_widget);
    snprintf(w[edit_widget].text, 160, "%s", text);
    input_method_request(input_method(), 0); /* stock Done closes T9 before reporting the value */
    w[edit_widget].change(0, 0); shim_run();
}
void *list_item_create(void *p, int x, int y, int ww, int h) { return make(p, h); }
void *label_create(void *p, int x, int y, int ww, int h) { return make(p, h); }
/* coverflow.c's chevron: a textless child, marked. */
void row_into(void *item, int h) { chevrons[(long)make(item, h)] = 1; }
/* Whether the row reading text (as shim_click finds it) ends in list_into. */
int shim_chevron(const char *text) {
    for (int p = 2; p <= count; ++p) {
        char joined[400] = "";
        int into = 0;
        for (int i = p + 1; i <= count; ++i)
            if (w[i].parent == p) {
                into |= chevrons[i];
                if (w[i].text[0]) snprintf(joined + strlen(joined), sizeof(joined) - strlen(joined), "%s%s", *joined ? " " : "", w[i].text);
            }
        if (w[p].click && *joined && !strncmp(joined, text, strlen(text))) return into;
    }
    return -1;
}
/* navigation.c's switch beside label: here a child after it reading On or Off, so a row reads
 * "Enabled On" as the value rows read. */
void list_toggle(void *label, const char *title, int on) {
    int row = w[(long)label].parent, sw = (long)label + 1;
    if (sw > count || w[sw].parent != row) sw = (int)(long)make((void *)(long)row, 48);
    snprintf(w[(long)label].text, 160, "%s", title);
    snprintf(w[sw].text, 160, "%s", on ? "On" : "Off");
}
void *list_view_create(void *p, int x, int y, int ww, int h) { return make(p, h); }
void *scroll_view_create(void *p, int x, int y, int ww, int h) { return make(p, h); }
int widget_use_style(void *x, const char *s) { return 0; }
int widget_set_text_utf8(void *x, const char *s) { snprintf(w[(long)x].text, 160, "%s", s); return 0; }
unsigned widget_on(void *x, unsigned type, handler f, void *ctx) {
    long i = (long)x;
    if (type == 0x10c) { w[i].click = f; w[i].ctx = ctx; }
    else if (type == 0x0c) w[i].destroy = f;
    else if (type == 0x0e) w[i].change = f;
    else if (type == 0x10e) w[i].focus = f;
    else if (type == 0x100) w[i].down = f;
    else if (type == 0x102) w[i].move = f;
    else if (type == 0x104) w[i].up = f;
    else w[i].keyup = f;
    return 1;
}
static int selection, offset;
int widget_get_prop_int(void *x, const char *k, int d) {
    return (long)x == 1 && !strcmp(k, "h") ? 290 : !strcmp(k, "_ringnav_index") ? selection : d;
}
int widget_set_prop_int(void *x, const char *k, int v) {
    if (!strcmp(k, "style:normal:bg_color")) w[(long)x].bg = v;
    if (!strcmp(k, "_ringnav_index")) selection = v;
    return 0;
}
int widget_destroy_children(void *x) { assert(!keyboard_owner); count = 1; return 0; }
int widget_resize(void *x, int ww, int h) { w[(long)x].h = h; return 0; }
int scroll_view_set_offset(void *x, int a, int b) { offset = b; return 0; }
int shim_selection(void) { return selection; }
int shim_offset(void) { return offset; }
int widget_invalidate_force(void *x, void *y) { return 0; }
unsigned timer_add(int (*f)(const void *), void *ctx, unsigned ms) { timer_fn = f; return ++timers; }
int timer_remove(unsigned id) { timer_fn = 0; ++removed; return 0; }
int navigator_back(void) { return ++backs; }
static int eqflag = -1;
int write_int_config(int v, const char *s, const char *k) { if (!strcmp(s, "PLAYSET") && !strcmp(k, "EQFLAG")) eqflag = v; return 0; }
int shim_eqflag(void) { return eqflag; }

int peq_page_init(void *page, void *context);
int shim_open(void) { count = 1; w[1].destroy = w[1].keyup = 0; return peq_page_init((void *)1, 0); }
int shim_pending(void) { return timer_fn != 0; }
int shim_removed(void) { return removed; }
unsigned char shim_flag(void) { return g_equalizer_flag; }
void shim_run(void) { int (*f)(const void *) = timer_fn; timer_fn = 0; if (f) f(0); }
/* Click the row whose labels, caption and value joined by a space, start with text; optionally let
 * the deferred render run (1), or only find it (2). */
int shim_click(const char *text, int run) {
    for (int p = 2; p <= count; ++p) {
        char joined[400] = "";
        for (int i = p + 1; i <= count; ++i)
            if (w[i].parent == p && w[i].text[0]) snprintf(joined + strlen(joined), sizeof(joined) - strlen(joined), "%s%s", *joined ? " " : "", w[i].text);
        if (w[p].click && *joined && !strncmp(joined, text, strlen(text))) {
            if (run == 2) return 1; /* only look */
            w[p].click(w[p].ctx, 0);
            if (run) shim_run();
            return 1;
        }
    }
    return 0;
}
void shim_return(void) { int event[8] = {0}; event[6] = 170; w[1].keyup(0, event); shim_run(); }
void shim_close(void) { w[1].destroy(0, 0); }
const char *shim_title(void) {
    for (int i = 2; i <= count; ++i) if (w[i].parent == 1 && w[i].h == 48) return w[i].text;
    return "";
}
int shim_list_height(void) { return w[2].h; } /* the list view is the page's first child */
/* The Simple tab: its view the page's child with a press handler, the tab bar the 48px one. Centre's
 * single press runs at once; a double press is the screen's. */
static int screen_off;
static int child(int bar) {
    for (int i = count; i > 1; --i) if (w[i].parent == 1 && w[i].down && (w[i].h == 48) == bar) return i;
    return 0;
}
void stop_timer(unsigned *t) { if (*t) timer_remove(*t); *t = 0; }
int center_press(unsigned *timer, unsigned *at, int (*single)(const void *), int scrubbing) {
    single(0); return 1;
}
int on_wm_keyup_fun(void *a, void *b) { return ++screen_off; }
int widget_to_local(void *x, void *xy) { return 0; }
int eq_key(void *top, unsigned key);
int shim_e3_open(void) { return shim_open(); } /* the EQ page opens on Simple */
int shim_e3_key(int k) { return eq_key((void *)1, (unsigned[]){KEY_PREV, KEY_NEXT, KEY_CENTER}[k]); }
void shim_e3_return(void) { int event[8] = {0}; event[EVENT_KEY / 4] = KEY_RETURN; w[1].keyup(0, event); }
void shim_e3_close(void) { w[1].destroy(0, 0); }
int shim_e3_touch(int type, int x, int y) {
    int event[8] = {0}, view = child(0);
    event[EVENT_TYPE / 4] = type; event[EVENT_X / 4] = x; event[EVENT_Y / 4] = y;
    handler f = type == EVT_POINTER_DOWN ? w[view].down : type == EVT_POINTER_UP_BEFORE ? w[1].up : w[1].move;
    return f ? f(0, event) : -1;
}
/* A tap on the tab bar at x, and the render it asks for. */
int shim_tab_touch(int x) {
    int event[8] = {0}, bar = child(1);
    event[EVENT_X / 4] = x;
    if (!bar) return 0;
    w[bar].down(0, event); shim_run(); return 1;
}
const char *shim_e3_title(void) {
    for (int i = count; i > 1; --i) if (w[i].parent == 1 && w[i].h == 48) return w[i].text;
    return "";
}
int shim_timers(void) { return timers; }
/* 3EQ's page painted black first, as the window's background is; then its rectangles and captions. */
void peq_paint(void *w, void *canvas);
const unsigned *shim_e3_paint(void) {
    static char canvas[0x100], lcd[0x100];
    *(void **)(canvas + CANVAS_LCD) = lcd;
    for (int i = 0; i < 290 * 375; ++i) screen[i] = 0xff000000u;
    ntexts = 0;
    peq_paint((void *)(long)child(0), canvas);
    peq_paint((void *)(long)child(1), canvas); /* the tabs, at the page's origin as the view is */
    return screen;
}
int shim_texts(void) { return ntexts; }
const char *shim_text(int i, int *r, unsigned *px, unsigned *color) {
    memcpy(r, texts[i].r, sizeof texts[i].r); *px = texts[i].px; *color = texts[i].color; return texts[i].s;
}
int shim_backs(void) { return backs; }
int shim_screen_off(void) { return screen_off; }
unsigned shim_list_bg(void) { return (unsigned)w[2].bg; }
"""

def editor_check(lib, tmp):
    """The editor's promises, driven through its real click and render paths."""
    (tmp/'shim.h').write_text(SHIM_H)
    (tmp/'shim.c').write_text('#include "peq.h"\n' + SHIM)
    (tmp/'peq_ui_checks.c').write_text('#include "peq_ui.c"\ndouble check_headroom(const peq_preset *p) { return headroom(p); }\n')
    ui = compile_host(tmp, 'peq_ui.so', tmp/'peq_ui_checks.c', ROOT/'patch/peq.c', tmp/'shim.c',
                      '-I', ROOT/'patch', '-include', tmp/'shim.h')
    headroom_check(lib, ui)
    ui.shim_title.restype = C.c_char_p
    data, saved = tmp/'root/mnt/data', tmp/'root/mnt/data/peq-presets'
    saved.mkdir(parents=True); (tmp/'root/mnt/mmc/EQ').mkdir(parents=True)
    active = data/'peq-active'
    def preset(**band):
        p = Preset(); lib.peq_default(C.byref(p)); p.bypass = 0
        for k, v in band.items(): setattr(p.bands[0], k, v)
        return p
    def read():  # what plays: the PEQ, bypassed unless it is the mode
        p = Preset(); assert lib.peq_load_active(C.byref(p)); return p
    def click(text): assert ui.shim_click(text.encode(), 1), (text, ui.shim_title())
    title = lambda: ui.shim_title().decode()
    def auto(p):  # the Preamp row's and picker's Auto: the 44.1 and 48 kHz cuts
        return 'Auto (44.1: %.1f / 48: %.1f)' % (lib.peq_auto(C.byref(p), 44100), lib.peq_auto(C.byref(p), 48000))
    def advanced():  # the EQ page opens on Simple; its tab bar's right half is Advanced
        r = ui.shim_open(); assert ui.shim_tab_touch(300); return r
    named = lambda name: ui.shim_click(b'Presets ' + name.encode(), 2)  # the draft's preset, on its row

    assert lib.peq_save(bytes(active), C.byref(preset(enabled=1)), 1) == 1
    # With the stock flag clear no filter runs, so the saved ON reads OFF.
    assert advanced() == 0 and title() == ''
    # The visualizer's attach puts the filter in the chain with PEQ off: the saved ON becomes OFF, so it stays silent.
    ui.shim_close(); ui.peq_attach(); assert read().bypass == 1 and ui.shim_flag() == 0; advanced()
    assert lib.peq_save(bytes(active), C.byref(preset(enabled=1)), 1) == 1
    # The switch writes the stock config key, so boot restores it; the flag follows the preset.
    click('Enabled Off'); assert ui.shim_eqflag() == 1 and ui.shim_flag() == 1
    click('Enabled On'); assert ui.shim_eqflag() == 0 and ui.shim_flag() == 0
    ui.shim_close(); assert advanced() == 0; click('Enabled Off')
    full = ui.shim_list_height()
    # iPod rows are transparent, so the list itself must paint black, not the theme's light card.
    ui.shim_list_bg.restype = C.c_uint; assert ui.shim_list_bg() == 0xff000000
    # Bypass switches at once but keeps unapplied band edits out of the active preset.
    click('1  '); click('Gain +0.0 dB'); assert title() == 'Band 1 Gain'; click('+1.0 dB'); ui.shim_return()
    click('Enabled On')
    assert read().bypass == 1 and read().bands[0].gain == 0 and ui.shim_flag() == 0
    click('Apply changes')
    assert read().bands[0].gain == 1 and read().bypass == 1 and title() == 'Applied'
    # A band edit sets the preamp to minus the combined response's peak: here the one +1 dB band.
    assert abs(read().preamp + 1) < 0.01, read().preamp
    # A message takes the title bar; the list keeps every row.
    assert ui.shim_list_height() == full == 144  # 3 rows under the curve
    # Loading into the editor does not activate it.
    assert lib.peq_save(bytes(saved/'HD650.peq'), C.byref(preset(enabled=1, gain=-3.0)), 1) == 1
    before = active.read_bytes()
    click('Presets'); click('HD650.peq')
    assert active.read_bytes() == before and title() == 'Preset loaded; choose Apply to activate'
    click('Enabled Off'); click('Enabled On')  # any action clears the message
    assert named('HD650') and read().bands[0].gain == 1  # the draft's name; still the applied preset
    # A failed apply or switch leaves the active preset untouched.
    before = active.read_bytes()
    (data/'peq-active.tmp').mkdir()
    click('Apply changes')
    assert title() == 'Apply failed; active EQ unchanged' and active.read_bytes() == before
    click('Enabled Off')
    assert title() == 'Switch failed; EQ unchanged' and active.read_bytes() == before
    (data/'peq-active.tmp').rmdir()
    click('Apply changes'); assert read().bands[0].gain == -3
    # Closing cancels the pending render.
    assert ui.shim_click(b'Presets', 0) and ui.shim_pending()
    removed = ui.shim_removed()
    ui.shim_close()
    assert not ui.shim_pending() and ui.shim_removed() == removed + 1
    advanced()
    assert named('HD650')  # the applied preset's name survives the page
    # Renaming: Return with the keyboard up keeps the name and the file.
    ui.shim_edit.restype = C.c_char_p; name = lambda: ui.shim_edit().decode()
    click('Presets'); click('Rename a preset'); click('HD650.peq'); assert name() == 'HD650'
    click('Rename HD650'); assert ui.shim_keyboard_owner(); ui.shim_cancel()
    assert name() == 'HD650' and (saved/'HD650.peq').exists() and title() == ''
    ui.shim_return(); ui.shim_return()
    # Deleting asks first, removes only the saved copy and leaves the active EQ alone.
    before = active.read_bytes()
    click('Presets'); click('Delete a preset'); click('HD650.peq'); click('Cancel')
    assert (saved/'HD650.peq').exists()
    click('HD650.peq'); click('Delete HD650.peq? Confirm')
    assert not (saved/'HD650.peq').exists() and active.read_bytes() == before
    assert title() == 'Deleted HD650.peq; active EQ unchanged' and not ui.shim_click(b'HD650.peq', 0)
    # Stock presets are built in: picking one loads stock's curve, writes nothing until Apply.
    files, before = sorted(saved.iterdir()), active.read_bytes()
    ui.shim_return(); click('Stock presets'); click('Rock')
    assert title() == 'Preset loaded; choose Apply to activate' and active.read_bytes() == before
    assert sorted(saved.iterdir()) == files
    click('Apply changes'); p = read()
    assert [p.bands[k].gain for k in range(10)] == [-2, 0, 2, 4, -2, -2, 0, 0, 4, 4]
    assert [p.bands[k].enabled for k in range(10)] == [1, 0, 1, 1, 1, 1, 0, 0, 1, 1] and p.preamp < -4
    # Overlapping boosts add up (+6.5 and +6 dB at 1 kHz); cuts alone leave 0 dB.
    p = preset(enabled=1, gain=6.0); p.count = 2; p.bands[1] = p.bands[0]; p.preamp, p.automatic = -1, 2
    assert lib.peq_save(bytes(active), C.byref(p), 1) == 1
    ui.shim_close(); advanced(); click('1  '); click('Gain +6.0 dB'); click('+6.5 dB'); ui.shim_return(); click('Apply changes')
    assert read().preamp == -1, read().preamp  # a picked preamp sticks through band edits
    click('Preamp -1.0 dB'); assert title() == 'Preamp' and ui.shim_selection() == 27  # opens on -1.0
    assert auto(read()) == 'Auto (44.1: -12.5 / 48: -12.5)'
    click(auto(read())); click('Apply changes')
    assert abs(read().preamp + 12.5) < 0.05 and read().automatic == 1, read().preamp
    # Rows that open a screen or picker end in list_into; the switches and cyclers don't.
    got = [ui.shim_chevron(t) for t in (b'Balance', b'Preamp', b'Presets', b'1  ', b'Enabled', b'Reset')]; assert got == [1, 1, 1, 1, 0, 0], got
    click('1  ')
    assert [ui.shim_chevron(t) for t in (b'Band', b'Type', b'Frequency', b'Gain', b'Q ')] == [0, 0, 1, 1, 1]
    click('Band On'); ui.shim_return(); click('2  '); click('Band On'); ui.shim_return(); click('Apply changes')
    assert math.copysign(1, read().preamp) == 1 and read().preamp == 0, read().preamp  # +0: shown as 0.0
    # Channels cycle on enabled bands; headroom takes the louder side, balance only turns one down.
    click('1  Peaking 31 Hz Off'); click('Gain +6.5 dB'); click('+7.5 dB'); click('Channels Both')  # picking a gain turns the band on
    assert title() == 'Band 1'
    click('Channels Left'); ui.shim_return()
    # Balance one row opens a picker, L 12 to R 12 dB in 0.5 dB steps, on the current value.
    click('Balance Centre'); assert title() == 'Balance'
    assert (ui.shim_selection(), ui.shim_offset()) == (24, 24 * 48 - 96)  # 5 rows of 48 shown
    click('R 12.0 dB'); assert title() == ''; click('Balance R 12.0 dB')
    assert (ui.shim_selection(), ui.shim_offset()) == (48, 49 * 48 - 240)  # clamped to the end
    ui.shim_return(); assert title() == ''; click('Balance R 12.0 dB'); click('L 0.5 dB'); click('Apply changes')
    r = read(); assert (r.bands[0].enabled, r.balance) == (3, -0.5) and abs(r.preamp + 7.5) < 0.05, (r.bands[0].enabled, r.balance)
    assert ui.shim_click(b'1  Peaking 31 Hz R +7.5 dB', 0) and ui.shim_click(b'Balance L 0.5 dB', 0)
    # Frequency and Q open a value menu: an edit with the value (keyboard on tap or centre), then Raise/Lower.
    ui.shim_edit.restype = ui.shim_keyboard.restype = ui.shim_input_type.restype = C.c_char_p
    edit = lambda: ui.shim_edit().decode()
    def type_value(text):
        click('Band 1 '); assert ui.shim_keyboard_owner()  # row 0, the caption
        ui.shim_type(text)
    ui.shim_close(); advanced(); click('1  Peaking 31 Hz R'); click('Frequency 31 Hz')
    assert ui.shim_click(b'Band 1 Frequency (Hz)', 2) and edit() == '31' and ui.shim_keyboard() == b'kb_default_t9'
    assert ui.shim_edit_clear() and title() == ''  # over the title, above T9: what is typed shows, the page stays put
    assert ui.shim_input_type() == b'text'  # keep unfinished input out of stock's numeric auto-fix
    click('Lower 1 Hz'); click('Lower 1 Hz'); assert edit() == '29'
    click('Step 1 Hz'); click('Lower 10 Hz'); assert edit() == '20'  # clamped at 20 Hz
    click('Step 10 Hz'); click('Raise 100 Hz'); assert edit() == '120'
    ui.shim_page.restype = C.c_char_p
    assert not ui.shim_focused(); click('Band 1 '); assert ui.shim_focused()  # the centre button opens the keyboard
    assert ui.shim_keyboard_owner()
    ui.shim_dismiss_keyboard(); assert ui.shim_focused()  # stock keeps focus after T9 closes
    ui.shim_page_reset(); ui.shim_select_reset()
    click('Band 1 '); assert ui.shim_keyboard_owner()  # reopen the same focused field, no EVT_FOCUS
    assert ui.shim_page() == b'symnum' and (ui.shim_selected(0), ui.shim_selected(1)) == (0, 3)  # numbers, value selected
    type_value(b'2500'); assert edit() == '2500'
    ui.shim_edit_name.restype = C.c_char_p; assert ui.shim_edit_name() == b'q2_edit'  # search_key's cancel
    click('Band 1 '); ui.shim_cancel()  # Return with the keyboard up: the value and the screen stay
    assert edit() == '2500' and ui.shim_click(b'Band 1 Frequency (Hz)', 2) and not ui.shim_keyboard_owner()
    type_value(b'abc'); assert edit() == '2500'      # not a number: unchanged
    type_value('\u0131'.encode()); assert edit() == '2500'  # low wchar byte is ASCII '1', still not a number
    type_value(b'9'*16); assert edit() == '2500'    # stock conversion refuses a value that cannot fit
    type_value(b'99999'); assert edit() == '20000'   # clamped like Raise
    type_value(b'1000.5'); assert edit() == '1000'   # kept exactly, shown whole
    type_value(b'0'); assert edit() == '20'  # zero is entered, then clamped to the frequency minimum
    type_value(b'1000.5')
    ui.shim_return(); assert title() == 'Band 1'
    click('Q 1.41'); assert ui.shim_click(b'Band 1 Q', 2) and edit() == '1.41'
    assert ui.shim_input_type() == b'text'
    click('Raise 0.05'); assert edit() == '1.46'
    click('Band 1 '); assert (ui.shim_selected(0), ui.shim_selected(1)) == (0, 4)  # selected: a key replaces it, "." too
    type_value(b'0.7'); assert edit() == '0.70'
    for text, want in ((b'0.05', '0.10'), (b'0', '0.10'), (b'10', '10.00'),
                       (b'0,70', '0.70'), (b'NaN', '0.70'), (b'', '0.70')):
        type_value(text); assert edit() == want, (text, edit())
    ui.shim_return(); ui.shim_return(); click('Apply changes')
    r = read(); assert (r.bands[0].frequency, round(r.bands[0].q, 9)) == (1000.5, 0.7), (r.bands[0].frequency, r.bands[0].q)
    click('1  Peaking 1000 Hz R'); click('Frequency 1000 Hz'); click('Band 1 ')
    assert ui.shim_keyboard_owner()
    ui.shim_close(); assert not ui.shim_keyboard_owner() and not ui.shim_pending()
    advanced()  # reopen the EQ page after closing it with its keyboard attached
    # Reset replaces the draft with peq_default's ten flat bands and leaves what plays alone until
    # Apply changes; Advanced's On/Off is live state and stays as it was.
    before = active.read_bytes()
    click('Reset to defaults')
    assert title() == 'Reset; choose Apply to activate' and active.read_bytes() == before
    assert ui.shim_click(b'Preamp Auto (44.1: 0.0 / 48: 0.0)', 2) and ui.shim_click(b'Balance Centre', 2)
    assert not ui.shim_click(b'Presets ', 2)  # the draft is no longer a named preset
    click('Apply changes')
    r = read()
    assert (r.count, r.preamp, r.balance, r.bypass) == (10, 0, 0, 0)
    assert [round(b.frequency) for b in r.bands[:10]] == [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    assert all(not b.enabled and b.gain == 0 and b.q == 1.41 and b.type == 0 for b in r.bands[:10])
    # Off stays Off.
    click('Enabled On')  # turns PEQ off
    before = active.read_bytes()
    click('Reset to defaults')
    assert active.read_bytes() == before
    click('Apply changes')
    r = read()
    assert (r.count, r.preamp, r.balance, r.bypass) == (10, 0, 0, 1)
    s = State(); assert lib.eq_load(C.byref(s)) and s.mode == EQ_OFF
    # Auto is saved (automatic), so it survives a restart; the player works out each rate's cut, so the
    # number saved with it (the 48 kHz cut, or another version's) is never applied again. A set preamp stays set.
    p = preset(enabled=1, gain=6.0); want = lib.peq_auto(C.byref(p), 48000); assert want < -5
    shown = lambda a: ui.shim_click(f'Preamp {auto(p) if a else f"{want:.1f} dB"}'.encode(), 2)
    def reopen(preamp, automatic, v2=False):
        p.preamp, p.automatic, s.peq, s.mode = preamp, automatic, p, EQ_PEQ; assert lib.eq_save(C.byref(s))
        if v2: active.write_bytes(b'Q2PEQ03\0' + bytes(s)[:8] + bytes(s.peq)[:V2] + bytes(s.eq3)[:V2])  # 1.3.5's
        ui.shim_close(); advanced()
    reopen(-1, 1)  # Auto saved with another number: Auto, plays as Auto, and opening writes nothing
    before = active.read_bytes()
    assert shown(True) and ui.shim_click(b'Nothing to apply', 2) and active.read_bytes() == before
    assert read().automatic == 1 and read().preamp == -1
    assert lib.peq_save(bytes(saved/'Auto.peq'), C.byref(p), 1) == 1  # presets carry it too
    reopen(0, 2); click('Presets'); click('Auto.peq'); assert shown(True)
    click('Apply changes')  # Auto saves its 48 kHz cut
    t = State(); assert lib.eq_load(C.byref(t)) and t.peq.automatic == 1 and t.peq.preamp == want
    reopen(want, 2)  # set to exactly the 48 kHz cut: still set, through a band edit
    assert shown(False) and not shown(True)
    click('1  '); click('Gain +6.0 dB'); click('+7.0 dB'); ui.shim_return(); click('Apply changes')
    assert read().preamp == want and read().automatic == 2 and lib.eq_load(C.byref(t)) and t.peq.automatic == 2
    reopen(-1, 0, v2=True)  # 1.3.5's state loads; never picked, so Auto, in the editor and playing
    assert lib.eq_load(C.byref(t)) and t.peq.automatic == 0 and bytes(t.eq3) == bytes(s.eq3) and shown(True)
    assert ui.shim_click(b'Nothing to apply', 2) and read().automatic == 1
    # An import plays its own preamp; Auto, once picked, sticks to the saved preset, not the card's file.
    (tmp/'root/mnt/mmc/EQ').mkdir(parents=True, exist_ok=True)
    text = 'Preamp: -2 dB\nFilter 1: ON PK Fc 1000 Hz Gain 6 dB Q 1.41\n'
    (tmp/'root/mnt/mmc/EQ/Imp.txt').write_text(text)
    click('Presets'); click('Import from SD /EQ'); click('Imp.txt'); ui.shim_close(); advanced()
    click('Presets'); click('Imp.peq'); assert ui.shim_click(b'Preamp -2.0 dB', 2)
    click('Preamp -2.0 dB'); click(auto(p)); assert shown(True)
    r = Preset(); assert lib.peq_load(bytes(saved/'Imp.peq'), C.byref(r)) and r.automatic == 1
    assert (tmp/'root/mnt/mmc/EQ/Imp.txt').read_text() == text
    click('Presets'); click('Imp.peq'); assert shown(True)
    click(f'Preamp {auto(p)}'); click('-3.0 dB'); click('Apply changes')
    ui.shim_close(); advanced(); assert ui.shim_click(b'Preamp -3.0 dB', 2) and not shown(True)
    (tmp/'root/mnt/mmc/EQ/Imp.txt').unlink(); (saved/'Imp.peq').unlink()
    # The reported preset: the Preamp row and its picker label the 44.1 and 48 kHz cuts; an edit refreshes them.
    rep = parse(lib, REPORTED_PRESET); assert rep.automatic == 2  # an import's preamp is set
    rep.automatic = 1; assert lib.peq_save(bytes(saved/'Rep.peq'), C.byref(rep), 1) == 1
    click('Presets'); click('Rep.peq'); assert ui.shim_click(b'Preamp Auto (44.1: -9.2 / 48: -8.8)', 2)
    click('Preamp Auto'); assert ui.shim_selection() == 0 and ui.shim_click(b'Auto (44.1: -9.2 / 48: -8.8)', 2)
    ui.shim_return(); click('7  Peaking 10160 Hz L'); click('Band On'); ui.shim_return()
    rep.bands[6].enabled = 0; assert auto(rep) != 'Auto (44.1: -9.2 / 48: -8.8)'
    assert ui.shim_click(f'Preamp {auto(rep)}'.encode(), 2)
    (saved/'Rep.peq').unlink()
    print('EQ Advanced tab: bypass, apply, auto preamp, channels, balance, frequency and Q, load, failed saves, delete, reset and close passed.')
    # A full pack stays sorted and reachable, with bounded widget counts and page-local action IDs.
    imports = tmp/'root/mnt/mmc/EQ'
    for i in reversed(range(8850)):
        (imports/f'Headphone {i:04}.txt').write_text('Preamp: -3 dB\nFilter: ON PK Fc 1000 Hz Gain -2 dB Q 1\n')
    (imports/'ignored.txt').mkdir()
    (imports/'ignored.wav').write_bytes(b'')
    before = active.read_bytes()
    click('Presets'); click('Import from SD /EQ')
    for start in range(0, 8850, 128):
        assert ui.shim_click(f'Headphone {start:04}.txt'.encode(), 2)
        assert ui.shim_click(f'Headphone {min(start + 127, 8849):04}.txt'.encode(), 2)
        assert not ui.shim_click(f'Headphone {start + 128:04}.txt'.encode(), 2)
        assert title() == f'Presets {start // 128 + 1}/70'
        assert ui.shim_selection() == ui.shim_offset() == 0
        if start < 8832: click('Next page')
    assert not ui.shim_click(b'Next page', 2)
    click('Headphone 8849.txt')
    imported = Preset()
    assert lib.peq_load(bytes(saved/'Headphone 8849.peq'), C.byref(imported)) and imported.preamp == -3
    assert active.read_bytes() == before  # importing only saves a copy
    click('Previous page'); assert title() == 'Presets 69/70'
    ui.shim_return(); click('Import from SD /EQ')
    assert title() == 'Presets 1/70' and not ui.shim_click(b'Previous page', 2)
    click('Headphone 0000.txt'); assert (saved/'Headphone 0000.peq').exists()
    click('Headphone 0000.txt'); assert title() == 'Presets'  # existing preset asks before replacement
    assert ui.shim_click(b'Replace existing preset? Confirm', 2)
    click('Cancel replacement'); ui.shim_return()
    # Saved presets and deletion use the same paging.
    for i in range(257):
        assert lib.peq_save(bytes(saved/f'Batch {i:04}.peq'), C.byref(imported), 1) == 1
    ui.shim_return(); click('Presets')
    click('Next page'); click('Batch 0128.peq'); assert named('Batch 0128')
    click('Presets'); click('Delete a preset'); click('Next page'); click('Next page')
    click('Batch 0256.peq'); click('Delete Batch 0256.peq? Confirm')
    assert not (saved/'Batch 0256.peq').exists() and active.read_bytes() == before
    ui.shim_return(); ui.shim_return()
    print('EQ preset pages: all 8,850 imports, sorting, page boundaries, navigation, save/replace, load and deletion passed.')
    eq3_editor_check(lib, ui, tmp)

def eq3_check(lib, tmp):
    """3EQ's filters on the shared engine, and the one state record PEQ and 3EQ share."""
    def eq3(low, mid, high):
        p = Preset(); lib.eq3_bands(C.byref(p), C.byref((C.c_double * 3)(low, mid, high))); return p
    def db(p, f, rate=48000):
        e = Engine(); assert lib.peq_compile(C.byref(p), rate, C.byref(e)); return 20*math.log10(response(e, rate, f))
    p = eq3(6, -6, 6)
    assert [(b.enabled, b.type, b.frequency, b.q) for b in p.bands[:3]] == [(1, 1, 155, .707), (1, 0, 900, .707), (1, 2, 5000, .707)]
    assert p.count == 3 and p.bypass == 0 and p.preamp == 0 and lib.peq_valid(C.byref(p))
    assert eq3(0, 6, 0).bands[1].frequency == eq3(0, 0, 0).bands[1].frequency == 1750  # Mid boosts higher than it cuts
    # Shelves reach their gain at the ends and leave the other end alone; Mid peaks at its own frequency.
    for g in (-6, -0.5, 0.5, 6):
        assert abs(db(eq3(g, 0, 0), 20) - g) < .15 and abs(db(eq3(g, 0, 0), 20000)) < .01
        assert abs(db(eq3(0, 0, g), 20000) - g) < .15 and abs(db(eq3(0, 0, g), 20)) < .01
        f = 900 if g < 0 else 1750
        assert abs(db(eq3(0, g, 0), f) - g) < 1e-7 and abs(db(eq3(0, g, 0), 20)) < .01
        grid = [20 * 1000 ** (k / 400) for k in range(401)]
        peak = max(grid, key=lambda x: g * db(eq3(0, g, 0), x))
        assert abs(math.log2(peak / f)) < .05, (g, peak)
    # Flat is identity: no band runs, and the PCM passes unchanged while 3EQ is on.
    e = Engine(); assert lib.peq_compile(C.byref(eq3(0, 0, 0)), 48000, C.byref(e)) and e.used == 0 and not e.bypass
    d = DSP(); lib.peq_reset(C.byref(d), 48000, 2, C.byref(eq3(0, 0, 0)))
    signal = [0.7*math.sin(i*.031) for i in range(4000)]
    assert process(lib, d, signal, 2) == list((C.c_float * len(signal))(*signal))
    # Finite, stable coefficients at every supported rate; a shelf above Nyquist is skipped.
    for rate in (8000, 11025, 22050, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000):
        for gains in ((6, -6, 6), (-6, 6, -6)):
            e = Engine(); assert lib.peq_compile(C.byref(eq3(*gains)), rate, C.byref(e))
            for c in e.c:
                assert all(math.isfinite(getattr(c, k)) for k in ('b0', 'b1', 'b2', 'a1', 'a2'))
                assert abs(c.a2) < 1 and 1+c.a1+c.a2 > 0 and 1-c.a1+c.a2 > 0
        assert (e.c[2].b0, e.c[2].a1) == ((1, 0) if rate <= 10000 else (e.c[2].b0, e.c[2].a1))
    # Mid crossing zero changes frequency through the engine's 20 ms crossfade: no step in the output.
    d = DSP(); lib.peq_reset(C.byref(d), 48000, 1, C.byref(eq3(0, -6, 0)))
    tone = [0.25*math.sin(2*math.pi*1300*i/48000) for i in range(9600)]
    out = process(lib, d, tone[:4800])
    assert lib.peq_update(C.byref(d), C.byref(eq3(0, 6, 0)))
    out += process(lib, d, tone[4800:])
    step = max(abs(a - b) for a, b in zip(out[1:], out))
    assert step < 2.2 * 0.25 * 2 * math.pi * 1300 / 48000, step  # at most twice the tone's own slope
    # The state: one Q2PEQ03 record. None yet: Off, PEQ's defaults, 3EQ flat.
    active, s = tmp/'root/mnt/data/peq-active', State()
    active.parent.mkdir(parents=True, exist_ok=True)
    if active.exists(): active.unlink()
    assert not lib.eq_load(C.byref(s)) and (s.mode, s.hold) == (EQ_OFF, 0) and s.peq.bypass == 1
    assert bytes(s.eq3) == bytes(eq3(0, 0, 0))
    # Upgrade: a Q2PEQ02 (or 01) preset keeps its bands, on unless it was bypassed; 3EQ starts flat and off.
    q = parse(lib, 'Preamp: -3 dB\nFilter: ON PK Fc 1000 Hz Gain 3 dB Q 1')
    for bypass, mode in ((0, EQ_PEQ), (1, EQ_OFF)):
        q.bypass = bypass; assert lib.peq_save(bytes(active), C.byref(q), 1) == 1
        assert lib.eq_load(C.byref(s)) and s.mode == mode and bytes(s.peq) == bytes(q) and bytes(s.eq3) == bytes(eq3(0, 0, 0))
    active.write_bytes(b'Q2PEQ01\0' + bytes(q)[:8 + 10 * 32 + 8])
    assert lib.eq_load(C.byref(s)) and s.mode == EQ_OFF and s.peq.count == 1
    # Saved whole, read back as saved; the effective preset follows the mode.
    s.eq3 = eq3(3, -2, 1); s.eq3.preamp = -3; s.mode = EQ_3
    assert lib.eq_save(C.byref(s)) and active.read_bytes()[:8] == b'Q2PEQ03\0' and len(active.read_bytes()) == 8 + C.sizeof(State)
    t, e = State(), Preset()
    assert lib.eq_load(C.byref(t)) and bytes(t) == bytes(s)
    assert lib.peq_load_active(C.byref(e)) and bytes(e) == bytes(s.eq3)
    for mode, hold, want in ((EQ_PEQ, 0, s.peq), (EQ_OFF, 0, s.peq), (EQ_OFF, 1, None)):
        t.mode, t.hold = mode, hold; lib.eq_effective(C.byref(t), C.byref(e))
        if want is None:  # 3EQ's Off on its page: the preamp alone, no bands
            assert (e.bypass, e.count, e.preamp, e.balance) == (0, 0, -3, 0)
        else:
            assert e.bypass == (mode == EQ_OFF) and bytes(e.bands) == bytes(want.bands) and e.preamp == want.preamp
    # A corrupt, truncated or overlong record reads as no state; a failed write keeps the last one.
    good = active.read_bytes()
    for bad in (good[:-1], good + b'\0', b'Q2PEQ09' + good[7:], good[:8] + b'\7' + good[9:], b''):
        active.write_bytes(bad); assert not lib.eq_load(C.byref(t)) and t.mode == EQ_OFF and t.peq.bypass == 1
    active.write_bytes(good)
    (active.parent/'peq-active.tmp').mkdir()
    s.mode = EQ_PEQ; assert not lib.eq_save(C.byref(s)) and active.read_bytes() == good
    (active.parent/'peq-active.tmp').rmdir()
    s.mode = 3; assert not lib.eq_save(C.byref(s)) and active.read_bytes() == good  # never an invalid record
    active.unlink()
    print('3EQ: filters, asymmetric Mid, identity, rates, smooth updates, state upgrade, effective preset, corrupt and failed writes passed.')

def eq3_captures(tmp, out, font):
    """EQ's Simple tab in the iPod build's two themes, as e3_paint and tabs_paint draw it (rectangles exact, captions set in the
    stock font at their sizes, centred in their boxes): illustrations for review, not AWTK captures."""
    from ipod import imagemagick
    (tmp/'shim.h').write_text(SHIM_H)
    (tmp/'shim.c').write_text('#include "peq.h"\n' + SHIM)
    ui = compile_host(tmp, 'peq_ui_ipod.so', ROOT/'patch/peq_ui.c', ROOT/'patch/peq.c', tmp/'shim.c',
                      '-I', ROOT/'patch', '-include', tmp/'shim.h', '-DIPOD=1')
    ui.shim_e3_paint.restype = C.POINTER(C.c_uint32 * (290 * 375))
    ui.shim_text.restype = C.c_char_p
    (tmp/'root/mnt/data').mkdir(parents=True)
    def shot(name):
        rgba = bytes(ui.shim_e3_paint().contents)
        args = ['-size', '375x290', '-depth', '8', 'rgba:-']
        for i in range(ui.shim_texts()):
            r, px, color = (C.c_int * 4)(), C.c_uint(), C.c_uint()
            text, c = ui.shim_text(i, r, C.byref(px), C.byref(color)).decode(), color.value
            args += ['(', '-size', f'{r[2]}x{r[3]}', 'xc:none', '-font', str(font), '-pointsize', str(px.value),
                     '-fill', f'#{c & 255:02x}{c >> 8 & 255:02x}{c >> 16 & 255:02x}', '-gravity', 'center',
                     '-annotate', '+0+0', text, ')', '-gravity', 'NorthWest', '-geometry', f'+{r[0]}+{r[1]}', '-composite']
        (out/f'3eq-{name}.png').write_bytes(imagemagick(*args, '-alpha', 'off', 'png:-', data=rgba))
    PREV, NEXT, CENTRE = range(3)
    for classic, theme in ((1, 'classic'), (0, 'minimal')):
        ui.shim_classic(classic)
        (tmp/'root/mnt/data/peq-active').unlink(missing_ok=True)
        ui.peq_attach()  # EQ off, the flag clear, 3EQ flat
        ui.shim_e3_open()
        keys = lambda *k: [ui.shim_e3_key(x) for x in k]
        keys(CENTRE, *[NEXT] * 6, CENTRE, NEXT, CENTRE, *[PREV] * 4, CENTRE, NEXT, CENTRE, *[NEXT] * 8, CENTRE, PREV, PREV)
        shot(f'{theme}-off')  # Low +3, Mid -2, High +4; Low selected; 3EQ off
        keys(PREV, PREV, PREV, CENTRE, *[NEXT] * 4, CENTRE, PREV)
        shot(f'{theme}-on-adjusting')  # 3EQ on, Mid being turned to -2.5 dB
        keys(CENTRE, PREV, PREV)
        shot(f'{theme}-on-reset-selected')
        keys(PREV)
        shot(f'{theme}-on-presets-selected')
        keys(PREV, PREV)
        shot(f'{theme}-tabs-selected')
        keys(NEXT, NEXT, CENTRE); ui.shim_run(); assert ui.shim_click(b'Rock', 1)
        shot(f'{theme}-preset')  # Rock picked from Presets: PEQ plays, the sliders dimmed
        ui.shim_e3_close()

def eq3_editor_check(lib, ui, tmp):
    """3EQ's page through its real keys, touch and timers, against the state PEQ shares."""
    active = tmp/'root/mnt/data/peq-active'
    ui.shim_e3_title.restype = C.c_char_p
    title = lambda: ui.shim_e3_title().decode()
    PREV, NEXT, CENTRE = range(3)
    def state():
        s = State(); assert lib.eq_load(C.byref(s)); return s
    def gains(s=None): return [b.gain for b in (s or state()).eq3.bands[:3]]
    def keys(*k):
        for x in k: assert ui.shim_e3_key(x)
    # PEQ playing; opening 3EQ changes nothing.
    ui.shim_close(); ui.shim_open(); assert ui.shim_tab_touch(300)
    if ui.shim_flag() == 0: assert ui.shim_click(b'Enabled Off', 1)
    ui.shim_close(); assert ui.shim_open() == 0 and ui.shim_click(b'Enabled', 2)  # reopens on the tab it was last on
    assert ui.shim_tab_touch(50); ui.shim_close()  # back to Simple, where the page opens after a boot
    before = active.read_bytes(); peq = bytes(state().peq)
    assert state().mode == EQ_PEQ and ui.shim_flag() == 1
    assert ui.shim_e3_open() == 0 and title() == '' and active.read_bytes() == before
    # The wheel picks Mid (from Low) and Centre takes it: over a playing PEQ, 3EQ takes over at once
    # and PEQ's preset is kept. The wheel turns it in 0.5 dB steps: one save timer for the run, and
    # nothing written until it fires.
    keys(NEXT, CENTRE); s = state()
    assert s.mode == EQ_3 and bytes(s.peq) == peq and ui.shim_flag() == 1
    before, timers = active.read_bytes(), ui.shim_timers()
    keys(NEXT, NEXT, NEXT, NEXT)
    assert ui.shim_timers() == timers + 1 and active.read_bytes() == before and ui.shim_pending()
    ui.shim_run(); s = state()
    assert gains(s) == [0, 2, 0] and s.eq3.bands[1].frequency == 1750 and s.mode == EQ_3 and bytes(s.peq) == peq
    assert abs(s.eq3.preamp + 2) < .05 and ui.shim_flag() == 1  # headroom for the boost
    # Below zero Mid moves to 900 Hz; Centre ends the adjustment and saves at once.
    keys(*[PREV] * 7); assert gains() == [0, 2, 0]
    keys(CENTRE); s = state(); assert not ui.shim_pending() and gains(s) == [0, -1.5, 0] and s.eq3.bands[1].frequency == 900
    assert s.eq3.preamp == 0  # cuts need no headroom
    keys(NEXT); assert gains() == [0, -1.5, 0]  # the wheel moves the selection again: High
    # Return ends the adjustment without leaving; the limits hold.
    backs = ui.shim_backs(); keys(CENTRE, *[NEXT] * 20); ui.shim_e3_return()
    assert gains() == [0, -1.5, 6] and ui.shim_backs() == backs and title() == ''
    # The switch, from High past R and Presets: 3EQ is on, PEQ off and kept; the indicator stays on.
    keys(*[PREV] * 5)
    s = state(); assert s.mode == EQ_3 and bytes(s.peq) == peq and ui.shim_flag() == 1 and title() == ''
    e = Preset(); assert lib.peq_load_active(C.byref(e)) and bytes(e) == bytes(s.eq3) and e.bypass == 0
    # While on, adjustments are heard as they are saved (here at the Centre that ends one).
    keys(NEXT, NEXT, NEXT, CENTRE, NEXT, CENTRE); assert gains() == [0.5, -1.5, 6] and state().mode == EQ_3
    # Off from here keeps the 3EQ's preamp playing until the page closes; the indicator goes off.
    keys(PREV, PREV, PREV, CENTRE); s = state()
    assert (s.mode, s.hold) == (EQ_OFF, 1) and ui.shim_flag() == 0 and ui.shim_eqflag() == 0
    assert lib.peq_load_active(C.byref(e)) and (e.bypass, e.count) == (0, 0) and e.preamp == s.eq3.preamp < 0
    keys(CENTRE); s = state(); assert (s.mode, s.hold) == (EQ_3, 0) and ui.shim_flag() == 1 and ui.shim_eqflag() == 1
    # Reset (R, past Presets) zeroes the gains and keeps the mode.
    keys(NEXT, NEXT, CENTRE); s = state(); assert gains(s) == [0, 0, 0] and s.mode == EQ_3 and s.eq3.preamp == 0
    # Touch: a press on a slider takes it and sets the gain under the finger; the drag follows in
    # 0.5 dB steps; lifting saves. The page is 290 high: travel 138..230, 0 dB at 184.
    assert ui.shim_e3_touch(0x100, 62, 138) == 0 and ui.shim_e3_touch(0x102, 62, 100) == 0
    assert gains() == [0, 0, 0] and ui.shim_pending()
    ui.shim_e3_touch(0x102, 187, 176); ui.shim_e3_touch(0x104, 187, 176)  # the press chose Low; x no longer matters
    assert gains() == [1, 0, 0] and not ui.shim_pending()
    ui.shim_e3_touch(0x100, 312, 230); ui.shim_e3_touch(0x104, 312, 230); assert gains() == [1, 0, -6]
    ui.shim_e3_touch(0x102, 312, 138); assert gains() == [1, 0, -6]  # no drag after the lift
    ui.shim_e3_touch(0x100, 340, 70); assert gains() == [0, 0, 0]  # Reset
    ui.shim_e3_touch(0x100, 50, 70); assert state().mode == EQ_OFF  # the switch
    ui.shim_e3_touch(0x100, 50, 70); assert state().mode == EQ_3
    # A failed save says so and keeps the last good record; the next one writes the pending gains.
    good = active.read_bytes(); (active.parent/'peq-active.tmp').mkdir()
    keys(NEXT, NEXT, NEXT, CENTRE, NEXT, CENTRE)
    assert title() == 'Save failed; EQ unchanged' and active.read_bytes() == good
    ui.shim_tab_touch(300)
    assert title() == 'Save failed; EQ unchanged' and active.read_bytes() == good
    (active.parent/'peq-active.tmp').rmdir()
    keys(CENTRE, CENTRE); assert gains() == [0.5, 0, 0] and title() == ''
    # Return leaves; leaving with Off held lets the preamp go.
    keys(PREV, PREV, PREV, CENTRE); assert state().hold == 1
    backs = ui.shim_backs(); ui.shim_e3_return(); assert ui.shim_backs() == backs + 1
    ui.shim_e3_close(); s = state(); assert (s.mode, s.hold) == (EQ_OFF, 0)
    assert lib.peq_load_active(C.byref(e)) and e.bypass == 1 and ui.shim_flag() == 0
    # PEQ's switch selects PEQ again; the 3EQ settings stay.
    ui.shim_open(); assert ui.shim_tab_touch(300) and ui.shim_click(b'Enabled Off', 1); s = state()
    assert s.mode == EQ_PEQ and bytes(s.peq) == peq and gains(s) == [0.5, 0, 0] and ui.shim_flag() == 1
    assert ui.shim_tab_touch(50); ui.shim_close()
    ui.shim_e3_open(); assert ui.shim_e3_touch(0x100, 50, 70) == 0 and (state().mode, state().hold) == (EQ_OFF, 0)  # shown on: off
    assert ui.shim_e3_touch(0x100, 50, 70) == 0 and state().mode == EQ_3  # and back
    ui.shim_e3_close(); assert state().mode == EQ_3 and ui.shim_flag() == 1
    # The tabs: the wheel reaches the bar above Simple's switch, Centre opens Advanced with the wheel
    # still on the bar; past it the list's first row, and back. Neither tab changes what plays.
    before = active.read_bytes()
    ui.shim_e3_open(); keys(*[PREV] * 4, CENTRE); ui.shim_run()
    assert ui.shim_click(b'Enabled Off', 2) and ui.shim_selection() == -1 and title() == ''
    keys(NEXT); assert ui.shim_selection() == 0
    assert not ui.shim_e3_key(NEXT)  # the list's own
    keys(PREV); assert ui.shim_selection() == -1
    keys(CENTRE); ui.shim_run(); assert not ui.shim_click(b'Enabled', 2)  # Simple: no list
    keys(NEXT, CENTRE); assert state().mode == EQ_OFF; keys(CENTRE)  # the switch, from the bar
    assert active.read_bytes() == before and state().mode == EQ_3
    ui.shim_e3_close()
    # Restarting with the Simple page still open must not keep its temporary Off attenuation.
    before = active.read_bytes(); ui.peq_boot(); assert active.read_bytes() == before  # 3EQ stays on
    ui.shim_e3_open(); ui.shim_e3_touch(0x100, 50, 70)
    s = state(); assert (s.mode, s.hold) == (EQ_OFF, 1) and s.eq3.preamp < 0
    ui.peq_stock_eq(1); assert state().hold == 1  # live player updates keep the comparison
    before = active.read_bytes(); (active.parent/'peq-active.tmp').mkdir()
    ui.peq_boot(); assert active.read_bytes() == before  # a failed reset preserves the good state
    (active.parent/'peq-active.tmp').rmdir()
    ui.peq_boot(); after = state()
    assert (after.mode, after.hold) == (EQ_OFF, 0)
    assert bytes(after.peq) == bytes(s.peq) and bytes(after.eq3) == bytes(s.eq3)
    assert lib.peq_load_active(C.byref(e)) and e.bypass == 1
    ui.shim_e3_close()
    after.mode = EQ_PEQ; assert lib.eq_save(C.byref(after))
    before = active.read_bytes(); ui.peq_boot(); assert active.read_bytes() == before  # PEQ stays on
    # Simple's Presets, between the switch and R: Advanced's Stock presets screen, Return back to Simple.
    # A pick plays at once as PEQ, 3EQ off and its gains kept; the switch shows it on under its name and
    # the sliders dim. A slider then turns 3EQ back on, PEQ's preset kept; the switch over a playing
    # PEQ turns the EQ off, holding nothing. Leaving from Presets reopens on Simple.
    ui.shim_text.restype = C.c_char_p
    def label(i=1):
        ui.shim_e3_paint(); r, px, c = (C.c_int * 4)(), C.c_uint(), C.c_uint()
        return [ui.shim_text(i, r, C.byref(px), C.byref(c)).decode('latin-1') for i in range(ui.shim_texts())][i]  # code points < 256, a byte each
    simple = lambda: not ui.shim_click(b'Enabled', 2) and not ui.shim_click(b'Rock', 2)  # no list
    eq3 = gains(); after.mode = EQ_3; assert lib.eq_save(C.byref(after)); before = active.read_bytes()
    ui.shim_e3_open(); assert label() == 'Presets'
    keys(PREV, PREV, CENTRE); ui.shim_run(); assert ui.shim_click(b'Rock', 2) and not ui.shim_click(b'Stock presets', 2)
    ui.shim_return(); assert simple() and active.read_bytes() == before
    keys(CENTRE); ui.shim_run(); assert ui.shim_click(b'Rock', 1)
    s = state(); assert simple() and (s.mode, s.hold) == (EQ_PEQ, 0) and s.peq.bands[0].gain == -2
    assert gains(s) == eq3 and ui.shim_flag() == 1 and label(0) == 'Rock' and label() == 'Presets' and title() == ''
    keys(NEXT, NEXT, CENTRE); s = state()  # Low
    assert s.mode == EQ_3 and s.peq.bands[0].gain == -2 and label(0) == 'Enabled: On'
    keys(CENTRE, PREV, PREV, CENTRE); ui.shim_run(); assert ui.shim_click(b'Classical', 1) and label(0) == 'Classical'
    ui.shim_e3_touch(0x100, 50, 70); s = state()  # the switch
    assert (s.mode, s.hold) == (EQ_OFF, 0) and label(0) == 'Enabled: Off' and ui.shim_flag() == 0
    ui.shim_e3_touch(0x100, 230, 70); ui.shim_run(); assert ui.shim_click(b'Rock', 2)  # a tap on Presets
    ui.shim_close(); ui.shim_open(); assert simple(); ui.shim_close()
    # A saved preset applied in Advanced: the switch's name cut on a character boundary (byte 12 is ä's second).
    presets = active.parent/'peq-presets'
    for f in presets.glob('Batch *.peq'): f.unlink()
    assert lib.peq_save(bytes(presets/'Kopfhörerbässe.peq'), C.byref(state().peq), 1) == 1
    ui.shim_e3_open(); assert ui.shim_tab_touch(300)
    assert ui.shim_click(b'Presets', 1) and ui.shim_click('Kopfhörerbässe.peq'.encode(), 1) and ui.shim_click(b'Apply changes', 1)
    assert ui.shim_click(b'Enabled Off', 1) and state().mode == EQ_PEQ and ui.shim_tab_touch(50)
    assert label(0) == 'Kopfhörerb'; ui.shim_e3_close()
    print('EQ Simple tab: entry without change, tabs, wheel and touch adjustment, coalesced saves, switch, held Off, reset, failed save and indicator passed.')

# Drives patch/peq_player.c the way hciplayer's af chain does. Built 32-bit like the device,
# so the file's ABI asserts hold; checked against the shared DSP driven directly.
PLAYER = r"""
#include <assert.h>
#include "peq.h"
int mp3_toc(const unsigned char *h, unsigned n, double t, double *frac, double *length);
int peq_open(af_instance *af);

char *peq_mpctx; /* the player's MPContext pointer; null: no codec to check */
double peq_pts = -1; /* written_audio_pts: seconds decoded */
int peq_length;      /* {media-info}'s whole seconds */
static unsigned clock_reads;
static long clock_skew; /* seconds added: a pause without waiting for one */
int __real_clock_gettime(clockid_t clock, struct timespec *t);
int __wrap_clock_gettime(clockid_t clock, struct timespec *t) {
    ++clock_reads;
    int result = __real_clock_gettime(clock, t);
    t->tv_sec += clock_skew;
    return result;
}
static int left = -1; /* calloc calls before one fails; -1: never */
void *test_calloc(size_t n, size_t size) {
    if (!left--) return 0;
    void *p = malloc(n * size);
    return p ? memset(p, 0, n * size) : p;
}

static peq_preset active(double preamp, double gain) {
    peq_preset p;
    peq_default(&p);
    p.bypass = 0; p.preamp = preamp; p.count = 1;
    p.automatic = 2; /* a set preamp: these check the gain as saved */
    p.bands[0] = (peq_band){1, 0, 1000, gain, 1};
    assert(peq_save(PEQ_ACTIVE, &p, 1) == 1);
    return p;
}

static int negotiate(af_instance *af, af_data *in) { return af->control(af, 0x10000100, in); }

/* The visualizer's tap (demo creates the file before the player maps it). */
static vis_tap *tap_view(void) {
    static vis_tap *t;
    if (!t) {
        int fd = open(VIS_FILE, O_RDWR | O_CREAT, 0644);
        assert(fd >= 0 && !ftruncate(fd, sizeof(vis_tap)));
        t = mmap(0, sizeof(vis_tap), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        assert(t != MAP_FAILED && !close(fd));
    }
    return t;
}

/* One block through the filter equals the same block through the reference DSP, and is filtered. */
static void same(af_instance *af, peq_dsp *ref, int rate, int nch) {
    float a[1024], b[1024], in[1024];
    unsigned frames = 1024 / nch;
    for (int i = 0; i < 1024; ++i) a[i] = b[i] = in[i] = 0.5f * sinf(i * 0.05f);
    af_data d = {a, (int)(frames * nch * 4), rate, nch, 0x1d, 4};
    ++tap_view()->want; /* the visualizer is watching */
    assert(af->play(af, &d) == &d);
    peq_process(ref, b, frames);
    assert(!memcmp(a, b, sizeof(a)) && memcmp(a, in, sizeof(a)));
    /* The tap ends with what played: the first two channels, every rate / 44100th frame above 48 kHz. */
    vis_tap *t = tap_view();
    unsigned step = rate > 48000 ? (unsigned)rate / 44100 : 1, n = (frames + step - 1) / step;
    assert(t->rate == (unsigned)rate / step && t->stamp > 0);
    for (unsigned i = 0; i < n; ++i)
        for (int ch = 0; ch < 2; ++ch)
            assert(t->ring[(t->seq - n + i) % VIS_RING][ch] == a[i * step * nch + (ch && nch > 1)]);
}

/* Crossfade (peq_player.c crossfade): demo's flag, one filter instance per track. */
static volatile int *xfade_view(int ms) {
    static volatile int *f;
    if (!f) {
        int fd = open(XFADE_FILE, O_RDWR | O_CREAT, 0644);
        assert(fd >= 0 && !ftruncate(fd, sizeof *f));
        f = mmap(0, sizeof *f, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        assert(f != MAP_FAILED && !close(fd));
    }
    *f = ms;
    return f;
}
static af_instance *track(af_instance *af, int rate) {
    memset(af, 0, sizeof(*af));
    assert(peq_open(af) == 1);
    af_data in = {0, 0, rate, 2, 0x1d, 4};
    assert(negotiate(af, &in) == 1);
    return af;
}
/* One stereo block: channel 0 a ramp from *n (the track's frame count), channel 1 level. Returns
 * the frames put out, into out. */
static unsigned block(af_instance *af, float *out, unsigned frames, unsigned *n, float level, int rate) {
    for (unsigned i = 0; i < frames; ++i) out[2 * i] = (float)(*n + i), out[2 * i + 1] = level;
    *n += frames;
    peq_pts = (double)*n / rate;
    af_data d = {out, (int)(frames * 8), rate, 2, 0x1d, 4};
    assert(af->play(af, &d) == &d && d.audio == out && d.len % 8 == 0);
    return (unsigned)d.len / 8;
}

static void crossfade_check(void) {
    static float buf[2 * 1024];
    af_instance a, b;
    unlink(PEQ_ACTIVE); /* the defaults: bypass, so the filter leaves samples exact */
    xfade_view(1000);
    peq_length = 10;
    /* Outgoing: untouched until the hold window (2 x 1 s + 2 s before the end), then half of each
     * block until a second is held; what comes out is the track in order, nothing skipped. */
    track(&a, 48000);
    unsigned n = 0, out = 0, held;
    while (n < 10 * 48000) {
        unsigned k = block(&a, buf, 1024, &n, 1, 48000);
        if (n <= 6 * 48000) assert(k == 1024);
        for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)out++);
    }
    held = n - out;
    assert(held == 48000);
    a.uninit(&a);
    /* Incoming: its first second mixed with the held tail, equal power (ch 1: 1 out, 0 in, so the
     * tail's cos), the tail's frames in order; then the track alone. Every frame comes out. */
    track(&b, 48000);
    xfade_view(0); /* disabling the next fade must still finish the already held tail */
    unsigned m = 0, mixed = 0;
    while (m < 2 * 48000) {
        unsigned start = m, k = block(&b, buf, 1024, &m, 0, 48000);
        assert(k == 1024);
        for (unsigned i = 0; i < k; ++i, ++mixed) {
            if (mixed < held) {
                float p = (mixed + 0.5f) / held * 1.5707963f;
                assert(fabsf(buf[2 * i + 1] - cosf(p)) < 1e-5f);
                assert(fabsf(buf[2 * i] - ((start + i) * sinf(p) + (out + mixed) * cosf(p))) < 0.5f);
            } else
                assert(buf[2 * i] == (float)(start + i) && buf[2 * i + 1] == 0);
        }
    }
    /* Halfway the two are at equal power: cos^2 + sin^2 = 1, each at 0.707. */
    float p = (held / 2 + 0.5f) / held * 1.5707963f;
    assert(fabsf(cosf(p) - 0.70710678f) < 1e-3f);
    b.uninit(&b);

    /* No fade asked (another album next, Crossfade off, Gapless off): every block whole. */
    xfade_view(0);
    track(&a, 48000);
    /* Let the unwatched visualizer tap settle before counting clock queries. */
    for (n = 0; n < 2 * 48000;) assert(block(&a, buf, 1024, &n, 1, 48000) == 1024);
    unsigned reads = clock_reads;
    for (n = 0; n < 10 * 48000;) assert(block(&a, buf, 1024, &n, 1, 48000) == 1024);
    assert(clock_reads == reads);
    a.uninit(&a);

    /* A seek while holding drops the held audio (it was left behind) and plays on whole. */
    xfade_view(1000);
    track(&a, 48000);
    for (n = 0; n < 9 * 48000;) block(&a, buf, 1024, &n, 1, 48000);
    n = 2 * 48000; /* back to 2 s */
    assert(block(&a, buf, 1024, &n, 1, 48000) == 1024 && buf[0] == 2 * 48000);
    a.uninit(&a);
    /* A tail followed by another rate is dropped: the next track plays alone. */
    track(&a, 48000);
    for (n = 0; n < 10 * 48000;) block(&a, buf, 1024, &n, 1, 48000);
    a.uninit(&a);
    track(&b, 44100);
    m = 0;
    assert(block(&b, buf, 1024, &m, 0, 44100) == 1024 && buf[0] == 0 && buf[1] == 0);
    b.uninit(&b);
    /* The same instance starting over at the end (a chain kept across the change) is the next track. */
    track(&a, 48000);
    for (n = 0; n < 10 * 48000;) block(&a, buf, 1024, &n, 1, 48000);
    peq_length = 30; /* the next track's, once the player has switched */
    m = 0;
    for (mixed = 0; m < 2 * 48000;) {
        unsigned start = m, k = block(&a, buf, 1024, &m, 0, 48000);
        assert(k == 1024);
        for (unsigned i = 0; i < k; ++i, ++mixed)
            if (mixed < 48000) assert(fabsf(buf[2 * i + 1] - cosf((mixed + 0.5f) / 48000 * 1.5707963f)) < 1e-5f);
            else assert(buf[2 * i] == (float)(start + i) && buf[2 * i + 1] == 0);
    }
    a.uninit(&a);
    peq_length = 10;
    /* Unknown length or position: no hold. */
    peq_length = 0;
    track(&a, 48000);
    for (n = 0; n < 10 * 48000;) assert(block(&a, buf, 1024, &n, 1, 48000) == 1024);
    a.uninit(&a);
    /* A 15 s fade, the longest: the hold wraps the ring; odd block sizes; order and the equal-power gain hold. */
    xfade_view(15000);
    peq_length = 60;
    static const unsigned sizes[] = {1024, 333, 17, 1000};
    unsigned j = 0;
    track(&a, 48000);
    for (n = out = 0; n < 60 * 48000;) {
        unsigned k = block(&a, buf, sizes[j++ % 4], &n, 1, 48000);
        for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)out++);
    }
    held = n - out;
    a.uninit(&a);
    track(&b, 48000);
    for (m = mixed = 0; mixed < held;) {
        unsigned k = block(&b, buf, sizes[j++ % 4], &m, 0, 48000);
        for (unsigned i = 0; i < k && mixed < held; ++i, ++mixed)
            assert(fabsf(buf[2 * i + 1] - cosf((mixed + 0.5f) / held * 1.5707963f)) < 1e-5f);
    }
    b.uninit(&b);
    peq_length = 10;
    xfade_view(0);
    peq_pts = -1;
}

/* Mono Audio (peq_player.c play): demo's MONO_FILE, looked for once a second of audio; set, each
 * stereo frame is its channels' mean, live; cleared, the block plays exactly. */
static void mono_check(void) {
    static float buf[2 * 1024];
    af_instance a;
    int fd = open(MONO_FILE, O_RDWR | O_CREAT, 0644);
    assert(fd >= 0 && !ftruncate(fd, sizeof(int)));
    volatile int *f = mmap(0, sizeof *f, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(f != MAP_FAILED && !close(fd));
    int sf = open(SWAP_FILE, O_RDWR | O_CREAT, 0644);
    assert(sf >= 0 && !ftruncate(sf, sizeof(int)));
    volatile int *swap = mmap(0, sizeof *swap, PROT_READ | PROT_WRITE, MAP_SHARED, sf, 0);
    assert(swap != MAP_FAILED && !close(sf));
    *swap = 1;
    *f = 1;
    unlink(PEQ_ACTIVE); /* bypass */
    track(&a, 48000);
    unsigned n = 0, k = 0;
    for (int i = 0; i < 60; ++i) k = block(&a, buf, 1024, &n, -1, 48000); /* found within a second */
    for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == buf[2 * i + 1] && buf[2 * i] == (n - k + i - 1.0f) * 0.5f);
    *f = 0;
    k = block(&a, buf, 1024, &n, -1, 48000);
    for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == -1 && buf[2 * i + 1] == (float)(n - k + i));
    *swap = 0;
    k = block(&a, buf, 1024, &n, -1, 48000);
    for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)(n - k + i) && buf[2 * i + 1] == -1);
    a.uninit(&a);
    unlink(MONO_FILE);
    unlink(SWAP_FILE);
    peq_pts = -1;
}

/* A gapless change (#137): the next instance, or this one renegotiated, carries on the last one's filter
 * memory, so a continuous signal comes out as one uninterrupted DSP puts it; another preset, a skip
 * mid-track, a pause and a passed-through track start from zero. */
static void sine(float *x, unsigned *n) {
    for (unsigned i = 0; i < 1024; ++i, ++*n) x[i] = 0.5f * sinf(*n / 2 * 0.05f);
}
static void continues(af_instance *af, peq_dsp *ref, unsigned *n) {
    float x[1024], y[1024];
    sine(x, n);
    memcpy(y, x, sizeof x);
    af_data d = {x, sizeof x, 44100, 2, 0x1d, 4};
    assert(af->play(af, &d) == &d);
    peq_process(ref, y, 512);
    assert(!memcmp(x, y, sizeof x));
}
static void carry_check(void) {
    xfade_view(0);
    peq_length = 1, peq_pts = 0.5; /* each track filtered to its end */
    peq_preset p = active(-3, 6);
    peq_dsp ref;
    peq_reset(&ref, 44100, 2, &p);
    af_instance a, b;
    unsigned n = 0;
    track(&a, 44100);
    continues(&a, &ref, &n);
    a.uninit(&a);
    track(&b, 44100);
    continues(&b, &ref, &n);
    af_data in = {0, 0, 44100, 2, 0x1d, 4};
    assert(negotiate(&b, &in) == 1);
    continues(&b, &ref, &n);
    p = active(-3, 3);
    peq_reset(&ref, 44100, 2, &p);
    track(&a, 44100); /* negotiated while b is still there */
    b.uninit(&b);
    continues(&a, &ref, &n);
    a.uninit(&a);
    track(&b, 44100);
    continues(&b, &ref, &n);
    p = active(-3, 6);
    peq_reset(&ref, 44100, 2, &p);
    assert(negotiate(&b, &in) == 1);
    continues(&b, &ref, &n);
    b.uninit(&b);
    /* A pause or stop before the next track: the old memory would ring into it, so it starts from zero. */
    clock_skew += 4;
    track(&a, 44100);
    peq_reset(&ref, 44100, 2, &p);
    continues(&a, &ref, &n);
    a.uninit(&a);
    /* A passed-through track in between (here a rate the EQ leaves alone): the next starts from zero. */
    track(&b, 7999);
    float pass[2] = {0.5f, 0.5f};
    af_data d = {pass, sizeof pass, 7999, 2, 0x1d, 4};
    assert(b.play(&b, &d) == &d && pass[0] == 0.5f);
    b.uninit(&b);
    track(&a, 44100);
    peq_reset(&ref, 44100, 2, &p);
    continues(&a, &ref, &n);
    a.uninit(&a);
    clock_skew = 0;
    /* A skip mid-track (or a stop): the next track starts from silence, so from zero. */
    peq_length = 60, peq_pts = 10;
    track(&b, 44100); /* still after a track that ended */
    continues(&b, &ref, &n);
    b.uninit(&b);
    peq_length = 1, peq_pts = 0.5;
    track(&a, 44100);
    peq_reset(&ref, 44100, 2, &p);
    continues(&a, &ref, &n);
    a.uninit(&a);
    /* Mid-track with EQ on, once the unwatched tap settles: no clock reads. */
    peq_length = 60, peq_pts = 10;
    track(&a, 44100);
    for (int i = 0; i < 100; ++i) continues(&a, &ref, &n);
    unsigned reads = clock_reads;
    for (int i = 0; i < 20; ++i) continues(&a, &ref, &n);
    assert(clock_reads == reads);
    a.uninit(&a);
    peq_length = 0, peq_pts = -1;
    assert(!unlink(PEQ_ACTIVE));
}

int main(void) {
    af_instance af;
    tap_view();
    /* A failed open is cleaned up by af_create calling uninit on the partial state. */
    for (int n = 0; n < 2; ++n) {
        memset(&af, 0, sizeof(af));
        left = n;
        assert(peq_open(&af) == -2);
        af.uninit(&af);
        assert(!af.data && !af.setup);
    }
    left = -1;
    memset(&af, 0, sizeof(af));
    assert(peq_open(&af) == 1 && af.mul == 1 && !af.delay && af.data && af.setup);
    player_state *s = af.setup;

    /* Format negotiation: bad channel counts fail, unsupported rates and formats pass through as they
       come (never detach: af_add would hand back the freed filter), other formats are asked to convert
       to float, and float is accepted. */
    af_data in = {0, 0, 48000, 0, 0x1d, 4};
    assert(negotiate(&af, 0) == -2);
    assert(negotiate(&af, &in) == -2);
    in.nch = PEQ_CHANNELS + 1; assert(negotiate(&af, &in) == -2);
    in.nch = 2; in.rate = 7999; assert(negotiate(&af, &in) == 1 && af.data->rate == 7999);
    in.rate = 384001; assert(negotiate(&af, &in) == 1 && af.data->rate == 384001);
    in.rate = 48000; in.format = 64; assert(negotiate(&af, &in) == 1 && af.data->format == 64);
    /* DSD passes through untouched, by the vendor's dsdiff driver or an FFmpeg ffdsd codec; other codecs negotiate. */
    static char *mp[12], *sh[2], *codec[0x3e4 / 4];
    peq_mpctx = (char *)mp; mp[0x2c / 4] = (char *)sh; sh[1] = (char *)codec;
    in.format = 0x1d;
    float dop[4] = {0.5f, -0.5f, 0.25f, -0.25f};
    af_data pass = {dop, sizeof(dop), 48000, 2, 0x1d, 4};
    codec[0x3e0 / 4] = "dsdiff"; assert(negotiate(&af, &in) == 1 && !memcmp(af.data, &in, sizeof(in)));
    codec[0x3e0 / 4] = "ffmpeg"; codec[0x3d0 / 4] = "ffdsdmsbf"; assert(negotiate(&af, &in) == 1);
    codec[0x3d0 / 4] = "ffflac"; assert(negotiate(&af, &in) == 1 && s->dsp.channels == 2);
    codec[0x3e0 / 4] = "dsdiff"; assert(negotiate(&af, &in) == 1); /* after PCM, as a reinit would */
    assert(af.play(&af, &pass) == &pass && pass.len == sizeof(dop) && dop[0] == 0.5f && dop[3] == -0.25f);
    peq_mpctx = 0;
    peq_preset first = active(-6, 6);
    in.format = 0x11; in.bps = 2;
    assert(negotiate(&af, &in) == 0 && in.format == 0x1d && in.bps == 4);
    assert(in.rate == 48000 && in.nch == 2);
    assert(negotiate(&af, &in) == 1);
    assert(af.data->format == 0x1d && af.data->bps == 4);
    assert(af.data->rate == 48000 && af.data->nch == 2);
    assert(s->dsp.rate == 48000 && s->dsp.channels == 2);
    assert(!memcmp(&s->preset, &first, sizeof(first)));
    peq_dsp ref;
    peq_reset(&ref, 48000, 2, &first);
    same(&af, &ref, 48000, 2);

    /* Buffers not in the negotiated format pass through untouched and leave the state alone. */
    float a[64] = {0.25f}, keep[64];
    memcpy(keep, a, sizeof(a));
    af_data bad[] = {
        {a, 256, 44100, 2, 0x1d, 4}, {a, 256, 48000, 1, 0x1d, 4}, {a, 256, 48000, 2, 0x11, 4},
        {a, 256, 48000, 2, 0x1d, 2}, {a, 252, 48000, 2, 0x1d, 4}, {a, 0, 48000, 2, 0x1d, 4},
    };
    for (unsigned i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i)
        assert(af.play(&af, &bad[i]) == &bad[i] && !memcmp(a, keep, sizeof(a)));
    assert(af.play(&af, 0) == 0);
    same(&af, &ref, 48000, 2);

    /* Live updates arrive through the stock gain query on the playback loop, channel 0 only. */
    float gains[10]; /* stock's graphic EQ array */
    struct { float *gain; int channel; } ext = {gains, 1};
    peq_preset second = active(-3, -4);
    assert(af.control(&af, 0x40001d00, &ext) == 1 && !memcmp(&s->preset, &first, sizeof(first)));
    same(&af, &ref, 48000, 2);
    ext.channel = 0;
    assert(af.control(&af, 0x40001d00, &ext) == 1 && !memcmp(&s->preset, &second, sizeof(second)));
    assert(peq_update(&ref, &second));
    for (int i = 0; i < 4; ++i) same(&af, &ref, 48000, 2); /* through the crossfade */
    /* An unchanged or unreadable active file keeps the current filter. */
    assert(af.control(&af, 0x40001d00, &ext) == 1);
    FILE *f = fopen(PEQ_ACTIVE, "wb");
    assert(f && fputs("junk", f) >= 0 && !fclose(f));
    assert(af.control(&af, 0x40001d00, &ext) == 1 && !memcmp(&s->preset, &second, sizeof(second)));
    same(&af, &ref, 48000, 2);
    /* The stock graphic gains read back as flat; malformed queries fail. */
    for (int i = 0; i < 10; ++i) gains[i] = 1;
    assert(af.control(&af, 0x40001d01, &ext) == 1);
    for (int i = 0; i < 10; ++i) assert(gains[i] == 0);
    assert(af.control(&af, 0x40001d00, 0) == -2);
    ext.channel = PEQ_CHANNELS; assert(af.control(&af, 0x40001d00, &ext) == -2);
    ext.channel = -1; assert(af.control(&af, 0x40001d00, &ext) == -2);
    ext.channel = 0; ext.gain = 0; assert(af.control(&af, 0x40001d00, &ext) == -2);
    assert(af.control(&af, 0x20000300, 0) == 1 && af.control(&af, 0x12345, 0) == -1);

    /* A new track renegotiates: new rate and channels, the active preset reread, fresh state. */
    peq_preset third = active(0, 3);
    in = (af_data){0, 0, 96000, 1, 0x1d, 4};
    assert(negotiate(&af, &in) == 1 && s->dsp.rate == 96000 && s->dsp.channels == 1);
    assert(!memcmp(&s->preset, &third, sizeof(third)));
    peq_reset(&ref, 96000, 1, &third);
    same(&af, &ref, 96000, 1);
    memcpy(a, keep, sizeof(a));
    af_data old = {a, 256, 48000, 2, 0x1d, 4};
    assert(af.play(&af, &old) == &old && !memcmp(a, keep, sizeof(a)));
    /* Auto (1, or never picked: 0) plays each rate's own cut, worked out as a track negotiates, the
     * editor never opened, whatever number was saved with it; a picked preamp (2) plays as set at
     * every rate. 50 kHz: not a standard rate, its own response all the same. */
    peq_preset hi;
    peq_default(&hi);
    hi.bypass = 0, hi.count = 2, hi.preamp = -1;
    hi.bands[0] = (peq_band){1, 2, 9000, 6, 0.707};
    hi.bands[1] = (peq_band){1, 0, 15000, 6, 2};
    static const int modes[] = {2, 0, 1}, rates[] = {32000, 44100, 48000, 96000, 50000};
    for (int m = 0; m < 3; ++m)
        for (unsigned i = 0; i < 5; ++i) {
            hi.automatic = modes[m];
            assert(peq_save(PEQ_ACTIVE, &hi, 1) == 1);
            in = (af_data){0, 0, rates[i], 2, 0x1d, 4};
            assert(negotiate(&af, &in) == 1 && s->preset.automatic == (modes[m] == 2 ? 2 : 1));
            double want = modes[m] == 2 ? -1 : peq_auto(&hi, rates[i]);
            assert(fabs(s->dsp.current.gain[0] - pow(10, want / 20)) < 1e-12 && s->dsp.current.gain[1] == s->dsp.current.gain[0]);
            /* Only the gain moves: the filters are the saved preamp's, and nothing is written back. */
            peq_engine e;
            peq_preset saved;
            assert(peq_compile(&hi, rates[i], &e) && !memcmp(e.c, s->dsp.current.c, sizeof(e.c)));
            assert(!memcmp(e.only, s->dsp.current.only, sizeof(e.only)) && e.used == s->dsp.current.used);
            assert(peq_load(PEQ_ACTIVE, &saved) && !memcmp(&saved, &hi, sizeof(hi)));
        }
    assert(peq_auto(&hi, 32000) < peq_auto(&hi, 96000) - 0.5); /* -12.0 against -11.4 dB */
    /* An applied edit: the new cut at the playing rate, through the update ramp. */
    peq_reset(&ref, 50000, 2, &s->preset);
    same(&af, &ref, 50000, 2);
    hi.bands[1].gain = 9;
    ext.gain = gains, ext.channel = 0;
    assert(peq_save(PEQ_ACTIVE, &hi, 1) == 1 && af.control(&af, 0x40001d00, &ext) == 1 && s->dsp.waiting);
    assert(fabs(s->dsp.pending.gain[0] - pow(10, peq_auto(&hi, 50000) / 20)) < 1e-12);
    assert(peq_update(&ref, &s->preset));
    for (int i = 0; i < 4; ++i) same(&af, &ref, 50000, 2);
    in = (af_data){0, 0, 96000, 1, 0x1d, 4};
    /* A 3EQ state record: the live update plays the 3EQ, then Off's held preamp alone, then nothing. */
    eq_state st;
    double g[3] = {3, -2, 1};
    assert(eq_load(&st) && st.mode == EQ_PEQ);
    eq3_bands(&st.eq3, g);
    st.eq3.preamp = -3, st.mode = EQ_3;
    ext.gain = gains;
    assert(eq_save(&st) && af.control(&af, 0x40001d00, &ext) == 1 && !memcmp(&s->preset, &st.eq3, sizeof(st.eq3)));
    assert(fabs(s->dsp.pending.gain[0] - pow(10, -3 / 20.0)) < 1e-12); /* 3EQ keeps its saved preamp */
    /* The hold keeps 3EQ's preamp though the PEQ beside it is on Auto, whose cut for no bands is 0 dB. */
    st.mode = EQ_OFF, st.hold = 1;
    assert(st.peq.automatic == 1 && eq_save(&st) && af.control(&af, 0x40001d00, &ext) == 1);
    assert(!s->preset.bypass && !s->preset.count && s->preset.preamp == -3);
    assert(fabs(s->dsp.pending.gain[0] - pow(10, -3 / 20.0)) < 1e-12);
    st.hold = 0;
    assert(eq_save(&st) && af.control(&af, 0x40001d00, &ext) == 1 && s->preset.bypass);
    /* Without an active file a track starts on the defaults. */
    assert(!unlink(PEQ_ACTIVE));
    in.rate = 44100;
    assert(negotiate(&af, &in) == 1);
    peq_preset defaults;
    peq_default(&defaults);
    assert(!memcmp(&s->preset, &defaults, sizeof(defaults)));

    /* With nobody watching for a second of audio, the tap stops copying; a bump resumes it. */
    vis_tap *t = tap_view();
    unsigned seq = t->seq;
    float block[2 * 1000] = {0};
    af_data idle = {block, (int)sizeof(block), 44100, 1, 0x1d, 4};
    for (int i = 0; i < 100; ++i) af.play(&af, &idle); /* 200000 frames, past 44100 */
    unsigned after = t->seq;
    assert(after != seq && after - seq < 50000);
    af.play(&af, &idle); assert(t->seq == after);
    ++t->want; af.play(&af, &idle); assert(t->seq == after + 2000);

    af.uninit(&af);
    assert(!af.data && !af.setup);
    crossfade_check();
    mono_check();
    carry_check();

    /* Exact VBR seeking: a 44.1 kHz stereo MPEG-1 Layer III Xing frame, 3600 s long, whose table
     * puts the first half of the time in the first quarter of the bytes. */
    unsigned char h[192] = {0xff, 0xfb, 0x90, 0x00};
    unsigned frames = 3600 * 44100 / 1152; /* 137812 frames, 3599.98 s */
    memcpy(h + 36, "Xing\0\0\0\x07", 8);
    h[44] = frames >> 24; h[45] = frames >> 16; h[46] = frames >> 8; h[47] = frames;
    for (int i = 0; i < 100; i++) h[52 + i] = i < 50 ? i * 64 / 50 : 64 + (i - 50) * 192 / 50;
    double frac, length;
    assert(mp3_toc(h, sizeof h, 0, &frac, &length) && frac == 0 && fabs(length - frames * 1152.0 / 44100) < 1e-9);
    assert(mp3_toc(h, sizeof h, length / 2, &frac, &length) && fabs(frac - 0.25) < 1e-9);
    assert(mp3_toc(h, sizeof h, length / 4, &frac, &length) && fabs(frac - 0.125) < 1e-9);
    assert(mp3_toc(h, sizeof h, length * 0.995, &frac, &length) && frac > 0.98 && frac < 1);
    assert(mp3_toc(h, sizeof h, length, &frac, &length) && frac == 1);
    assert(!mp3_toc(h, 100, 10, &frac, &length));        /* cut short */
    h[43] = 3;                                            /* no table */
    assert(!mp3_toc(h, sizeof h, 10, &frac, &length));
    h[43] = 7; memcpy(h + 36, "Info", 4);                 /* CBR: stock is already exact */
    assert(!mp3_toc(h, sizeof h, 10, &frac, &length));
    memcpy(h + 36, "Xing", 4); h[1] = 0xfd;               /* Layer III only */
    assert(!mp3_toc(h, sizeof h, 10, &frac, &length));
    h[1] = 0xf3; h[2] = 0x90;                             /* MPEG-2, 22.05 kHz: side info 17, 576 a frame */
    memmove(h + 21, h + 36, 120);
    assert(mp3_toc(h, sizeof h, 0, &frac, &length) && fabs(length - frames * 576.0 / 22050) < 1e-9);
    return 0;
}
"""

def player_check(tmp):
    """hciplayer's filter: negotiation, track changes, live updates and cleanup."""
    root = tmp/'player'
    (root/'mnt/data').mkdir(parents=True)
    (root/'tmp').mkdir()
    (tmp/'player_test.c').write_text(PLAYER)
    binary = tmp/'player_test'
    sources = [ROOT/'patch/peq_player.c', ROOT/'patch/peq.c', tmp/'player_test.c']
    subprocess.run(['cc', '-m32', '-DPEQ_HOST', f'-DPEQ_ROOT="{root}"', '-Dcalloc=test_calloc',
                    '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-I', str(ROOT/'patch'),
                    *map(str, sources), '-Wl,--wrap=clock_gettime', '-lm', '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    print('PEQ player: negotiation, pass-through, the visualizer tap, live updates, track changes, carried filter memory, cleanup, crossfade, mono and the Xing seek table passed.')

def visualizer_check(tmp):
    """The visualizer's analysis (patch/visualizer.c vis_bands): a sine lands in its band at full scale."""
    lib = compile_host(tmp, 'visualizer.so', ROOT/'patch/visualizer.c', ROOT/'patch/peq.c', '-DIPOD=1', '-I', ROOT/'patch')
    rate, n, bands = 44100, 1024, 64
    def levels(pcm):
        out = (C.c_float * bands)()
        lib.vis_bands((C.c_float * (2 * n))(*pcm), rate, out)
        return list(out)
    assert levels([0.0] * (2 * n)) == [0.0] * bands  # silence rests
    edge = lambda b: int(40 * 400 ** (b / bands) * n / rate + 0.5)  # the C band_bin
    for k in (12, 100, 300):  # a sine exactly on bin k, in both channels
        pcm = [math.sin(2 * math.pi * k * i / n) for i in range(n) for _ in (0, 1)]
        got, want = levels(pcm), max(b for b in range(bands) if edge(b) <= k)
        assert got[want] > 0.99 and got.index(max(got)) <= want, (k, want, got)
        assert all(v == 0 for b, v in enumerate(got) if edge(b) > k + 1 or edge(b + 1) < k - 1), (k, got)
        assert abs(levels([v / 2 for v in pcm])[want] - (1 - 6.0206 / 60)) < 0.01  # half scale: 6 dB down
    harness = tmp/'visualizer_checks.c'
    harness.write_text(r"""
#include <assert.h>
#include "visualizer.c"
void check_styles(void) {
    for (int style = 0; style < STYLES; ++style) {
        memset(&vz, 0, sizeof vz);
        for (int i = 0; i < VIS_N; ++i) {
            vz.pcm[i][0] = sinf(TAU * 12 * i / VIS_N);
            vz.pcm[i][1] = 0;
        }
        vz.re[0] = 123; /* FFT scratch stays untouched by time-domain styles. */
        vis_analyze(style, 44100, 0.04f, 100);
        if (style == SCOPE || style == METERS) {
            assert(vz.re[0] == 123 && vz.bin_rate == 0);
            for (int i = 0; i < VIS_HALO; ++i) assert(vz.level[i] == 0);
        } else {
            assert(vz.bin_rate == 44100 && vz.re[0] != 123);
            float max = 0;
            for (int i = 0; i < VIS_HALO; ++i) if (vz.level[i] > max) max = vz.level[i];
            assert(max > 0.5f);
        }
        if (style == SCOPE) assert(vz.wave[0][10] != 0 && vz.wave[1][10] == 0);
        else for (int ch = 0; ch < 2; ++ch)
            for (int i = 0; i < VIS_W; ++i) assert(vz.wave[ch][i] == 0);
        if (style == METERS) assert(vz.vu[0] > 0 && vz.vu[1] == 0 && vz.lit[0] == 500);
        else assert(vz.vu[0] == 0 && vz.vu[1] == 0);
        if (style != SPECTRUM)
            for (int i = 0; i < VIS_BARS; ++i) assert(vz.peak[i] == 0);
        vis_reset();
        for (int i = 0; i < VIS_HALO; ++i) assert(vz.level[i] == 0);
        for (int i = 0; i < VIS_BARS; ++i) assert(vz.peak[i] == 0);
        assert(vz.vu[0] == 0 && vz.lit[0] == 0 && vz.bass == 0 && vz.wave[0][10] == 0);
        vis_analyze(style, 44100, 0.04f, 100);
        for (unsigned now = 140; now <= 1600; now += 40) vis_analyze(style, 0, 0.04f, now);
        for (int i = 0; i < VIS_HALO; ++i) assert(vz.level[i] == 0);
        for (int i = 0; i < VIS_BARS; ++i) assert(vz.peak[i] == 0);
        for (int ch = 0; ch < 2; ++ch) {
            assert(vz.vu[ch] < 0.0001f);
            for (int i = 0; i < VIS_W; ++i) assert(fabsf(vz.wave[ch][i]) < 0.0003f);
        }
    }
}
""")
    checked = compile_host(tmp, 'visualizer_checks.so', harness, ROOT/'patch/peq.c', '-DIPOD=1', '-I', ROOT/'patch')
    checked.check_styles()
    print('Visualizer: FFT bands, selected-style work, stereo scope/VU and pause decay passed.')

SCROBBLE = r"""
#include <assert.h>
#include <stdarg.h>
#include <sys/stat.h>
#include "peq.h"
int scrobble_ready(void), scrobble_start(void), scrobble_poll(int *), scrobble_flag(const char *);
void scrobble_append(const char *, unsigned);

static const char *cfg[7]; /* TOKEN, USER, PASSWORD, API_KEY, API_SECRET, ALBUM_ARTIST, AUTO */
int toolsReadConfig(const char *path, const char *section, const char *key, char *out, const char *def) {
    static const char *const keys[] = { "TOKEN", "USER", "PASSWORD", "API_KEY", "API_SECRET", "ALBUM_ARTIST", "AUTO" };
    static const char *const sections[] = { "LISTENBRAINZ", "LASTFM", "LASTFM", "LASTFM", "LASTFM", "SCROBBLE", "SCROBBLE" };
    assert(strstr(path, "/mnt/mmc/.scrobble.ini"));
    for (int i = 0; i < 7; i++)
        if (!strcmp(key, keys[i]) && !strcmp(section, sections[i]) && cfg[i]) { strcpy(out, cfg[i]); return 1; }
    return -1; /* stock leaves out alone for a missing file or key */
}

/* libcurl: every request is dumped as url, headers, verify, body; replies are scripted. */
typedef unsigned (*writer)(const char *, unsigned, unsigned, void *);
typedef struct node { const char *s; struct node *next; } node;
static struct { const char *url, *body; node *h; writer w; void *ctx; long verify, code; int untimed; } easy;
static int requests, fail_at = -1, no_key, append_at = -1;
static FILE *dump;
void *curl_easy_init(void) { memset(&easy, 0, sizeof easy); return &easy; }
void *curl_slist_append(void *list, const char *s) {
    node *n = calloc(1, sizeof *n), *l = list;
    n->s = s;
    if (!l) return n;
    while (l->next) l = l->next;
    l->next = n;
    return list;
}
void curl_slist_free_all(void *list) { for (node *n = list, *x; n; n = x) x = n->next, free(n); }
int curl_easy_setopt(void *c, int opt, ...) {
    va_list a;
    va_start(a, opt);
    assert(c == &easy);
    if (opt == 10002) easy.url = va_arg(a, const char *);
    else if (opt == 10015) easy.body = va_arg(a, const char *);
    else if (opt == 10023) easy.h = va_arg(a, node *);
    else if (opt == 20011) easy.w = va_arg(a, writer);
    else if (opt == 10001) easy.ctx = va_arg(a, void *);
    else if (opt == 64) { easy.verify = va_arg(a, long); assert(easy.verify == 1); }
    else if (opt == 81) assert(va_arg(a, long) == 2);
    else if (opt == 10065) assert(strstr(va_arg(a, const char *), "/etc/scrobble-ca.pem"));
    else if (opt == 20108) { /* SSL_CTX_FUNCTION: X509_V_FLAG_NO_CHECK_TIME into SSL_CTX->param->flags */
        unsigned flags[4] = {0, 0, 0, 1};
        void *ctx[0x100 / sizeof(void *) + 1];
        ctx[0x100 / sizeof(void *)] = flags;
        assert(!va_arg(a, int (*)(void *, void *, void *))(c, ctx, 0) && flags[3] == 0x200001);
        easy.untimed = 1;
    } else assert(opt == 99 || opt == 13 || opt == 78 || opt == 81);
    va_end(a);
    return 0;
}
int curl_easy_perform(void *c) {
    assert(easy.untimed);
    int i = requests++;
    if (i == append_at) scrobble_append("New\tB\tListen\t\t100\tL\t1700009999\t\n", 33); /* a listen meanwhile */
    fprintf(dump, "%s\t", easy.url);
    for (node *n = easy.h; n; n = n->next) fprintf(dump, "%s|", n->s);
    fprintf(dump, "\t%ld\t%s\n", easy.verify, easy.body);
    const char *reply = !strstr(easy.url, "audioscrobbler") ? "{\"status\":\"ok\"}"
                        : strstr(easy.body, "auth.getMobileSession") ? (no_key ? "{\"session\":{}}" : "{\"session\":{\"name\":\"u\",\"key\":\"SK123\"}}")
                        : "{\"scrobbles\":{\"@attr\":{\"accepted\":1,\"ignored\":0}}}";
    easy.code = i == fail_at ? 500 : 200;
    easy.w(reply, 1, (unsigned)strlen(reply), easy.ctx);
    return 0;
}
int curl_easy_getinfo(void *c, int opt, ...) {
    va_list a;
    va_start(a, opt);
    assert(c == &easy && opt == 0x200002);
    *va_arg(a, long *) = easy.code;
    va_end(a);
    return 0;
}
void curl_easy_cleanup(void *c) { assert(c == &easy); }

static char *slurp(const char *path) {
    static char s[1 << 20];
    FILE *f = fopen(path, "rb");
    size_t n = f ? fread(s, 1, sizeof s - 1, f) : 0;
    if (f) fclose(f);
    s[n] = 0;
    return f ? s : 0;
}
static int lines(const char *s) { int n = 0; for (; s && *s; s++) n += *s == '\n'; return n; }
static int run(int *sent) {
    int r, started = scrobble_start();
    if (started <= 0) return started - 10;
    while (!(r = scrobble_poll(sent))) usleep(1000);
    assert(!scrobble_poll(sent)); /* reported once */
    return r;
}
static void write_log(int n) {
    FILE *f = fopen(ROOT "/mnt/mmc/.scrobbler.log", "wb");
    fputs("#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Q2 Pod\n", f);
    for (int i = 0; i < n; i++)
        fprintf(f, "Art \"%d\" \\ &=+%%\xc3\xa9\t%s\tTitle %d\t%d\t%d\tL\t%d\t\n", i, i % 10 ? "Alb/um" : "", i, i, 200 + i, 1700000000 + i);
    fputs("Skipped\tA\tT\t\t200\tS\t1700000000\t\njunk\n# a comment\n", f);
    fclose(f);
}
#define LOG ROOT "/mnt/mmc/.scrobbler.log"
#define HEADER "#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Q2 Pod\n"

int main(void) {
    int sent = -1;
    dump = fopen(ROOT "/requests", "w");
    setvbuf(dump, 0, _IONBF, 0);
    assert(!scrobble_ready() && run(&sent) == -11); /* no accounts: no row, and never an upload */
    assert(!scrobble_flag("ALBUM_ARTIST")); /* [SCROBBLE] flags: off unless 1 */
    cfg[5] = "0";
    assert(!scrobble_flag("ALBUM_ARTIST"));
    cfg[5] = "1";
    assert(scrobble_flag("ALBUM_ARTIST") && !scrobble_flag("AUTO") && !scrobble_ready()); /* not an account */
    cfg[6] = "1";
    assert(scrobble_flag("AUTO"));
    cfg[0] = "tok";
    assert(scrobble_ready() == 1);
    cfg[1] = "u", cfg[2] = "p&w", cfg[3] = "key";
    assert(scrobble_ready() == 1); /* Last.fm needs all four */
    cfg[4] = "sec";
    assert(scrobble_ready() == 3);
    assert(run(&sent) == 1 && !sent && !requests); /* no log: nothing to upload */

    write_log(120);
    append_at = 0;
    assert(run(&sent) == 1 && sent == 120 && requests == 7); /* 50, 50, 20 to each, one session */
    assert(!strcmp(slurp(LOG), HEADER "New\tB\tListen\t\t100\tL\t1700009999\t\n"));
    char *s = slurp(LOG ".sent");
    assert(lines(s) == 122 && !strstr(s, "#") && strstr(s, "junk\n"));

    write_log(60); /* ListenBrainz fails the second batch: the first leaves, the rest stays */
    append_at = -1, fail_at = 10;
    assert(run(&sent) == -1 && sent == 50);
    s = slurp(LOG);
    assert(!strncmp(s, HEADER "Art \"50\"", sizeof HEADER + 7) && lines(s) == 3 + 10 + 3);
    assert(lines(slurp(LOG ".sent")) == 172);

    char before[1 << 16];
    strcpy(before, slurp(LOG)); /* no session key: nothing changes */
    fail_at = -1, no_key = 1;
    assert(run(&sent) == -1 && !sent && !strcmp(slurp(LOG), before) && lines(slurp(LOG ".sent")) == 172);
    cfg[1] = 0, no_key = 0; /* An old card CA file is ignored: firmware supplies trust. */
    fclose(fopen(ROOT "/mnt/mmc/.scrobble.pem", "w"));
    assert(run(&sent) == 1 && sent == 10 && !strcmp(slurp(LOG), HEADER));
    /* A log rewrite that fails (a full card) keeps the listens to resend, and .sent gains none. */
    assert(!mkdir(LOG ".tmp", 0755));
    write_log(1); strcpy(before, slurp(LOG));
    assert(run(&sent) == -1 && sent == 1 && !strcmp(slurp(LOG), before) && lines(slurp(LOG ".sent")) == 184);
    rmdir(LOG ".tmp");
    /* A full archive must never discard the source listens after network success. */
    unlink(LOG ".sent"); assert(!symlink("/dev/full", LOG ".sent"));
    assert(run(&sent) == -1 && sent == 1 && !strcmp(slurp(LOG), before));
    unlink(LOG ".sent");
    fclose(dump);
    return 0;
}
"""

def scrobble_check(tmp):
    """Upload Scrobbles: config, log parsing and batching, the ListenBrainz JSON, the signed Last.fm
    form, and the log rewrite, on a real pthread with libcurl scripted and the host's libcrypto MD5."""
    import hashlib, json, urllib.parse
    root = tmp/'scrobble'
    (root/'mnt/mmc').mkdir(parents=True)
    shim = ['#include <pthread.h>', '#define pthread_mutex_lock(m) pthread_mutex_lock((pthread_mutex_t *)(m))',
            '#define pthread_mutex_unlock(m) pthread_mutex_unlock((pthread_mutex_t *)(m))', '#define tk_snprintf snprintf']
    shim += [f'{PROTOTYPES[n][0]} {n}({PROTOTYPES[n][1]});' for n in PROTOTYPES if n.startswith(('curl_', 'MD5_', 'toolsReadConfig'))]
    (tmp/'scrobble_shim.h').write_text('\n'.join(shim) + '\n')
    (tmp/'scrobble_test.c').write_text(SCROBBLE)
    binary = tmp/'scrobble_test'
    subprocess.run(['cc', '-pthread', '-DPEQ_HOST', f'-DPEQ_ROOT="{root}"', f'-DROOT="{root}"', '-D_GNU_SOURCE',
                    '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', '-I', str(ROOT/'patch'), '-include', str(tmp/'scrobble_shim.h'),
                    str(ROOT/'patch/scrobble.c'), str(tmp/'scrobble_test.c'), '-lcrypto', '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    requests = [line.split('\t', 3) for line in (root/'requests').read_text().splitlines()]
    artist = lambda i: f'Art "{i}" \\ &=+%é'
    lb_sizes, fm_sizes = [], []
    for url, headers, verify, body in requests:
        if 'listenbrainz' in url:
            assert url == 'https://api.listenbrainz.org/1/submit-listens'
            assert headers == 'Authorization: Token tok|Content-Type: application/json|'
            listens = json.loads(body)['payload']
            assert json.loads(body)['listen_type'] == 'import'
            for x in listens:
                i = x['listened_at'] - 1700000000; m = x['track_metadata']
                assert m['artist_name'] == artist(i) and m['track_name'] == f'Title {i}'
                assert m.get('release_name') == ('Alb/um' if i % 10 else None)
            lb_sizes.append(len(listens))
            continue
        assert url == 'https://ws.audioscrobbler.com/2.0/' and not headers
        params = urllib.parse.parse_qsl(body, keep_blank_values=True, strict_parsing=True)
        assert params[-2:][1] == ('format', 'json') and params[-2][0] == 'api_sig'
        signed = sorted(params[:-2])
        assert [k for k, _ in params[:-2]] == [k for k, _ in signed]  # sent in signature order
        assert params[-2][1] == hashlib.md5((''.join(k + v for k, v in signed) + 'sec').encode()).hexdigest()
        p = dict(params)
        if p['method'] == 'auth.getMobileSession':
            assert (p['username'], p['password'], p['api_key']) == ('u', 'p&w', 'key'); continue
        assert p['method'] == 'track.scrobble' and p['sk'] == 'SK123'
        n = sum(k.startswith('artist[') for k in p)
        for j in range(n):
            i = int(p[f'timestamp[{j}]']) - 1700000000
            assert p[f'artist[{j}]'] == artist(i) and p[f'track[{j}]'] == f'Title {i}' and p[f'duration[{j}]'] == str(200 + i)
            assert p.get(f'album[{j}]') == ('Alb/um' if i % 10 else None)
        fm_sizes.append(n)
    assert lb_sizes == [50, 50, 20, 50, 10, 10, 1, 1] and fm_sizes == [50, 50, 20, 50]
    assert [v for _, _, v, _ in requests] == ['1'] * len(requests)
    print('Scrobble upload: config, batching, JSON and form escaping, Last.fm signatures, log rewrite and failures passed.')

def books_check(tmp):
    """Books (books.c): raw inflate against zlib and on garbage, XHTML to text, EPUB to text (stored
    and deflated entries, namespaced OPF, %XX hrefs, DRM, malformed zips), UTF-8 with the Latin-1
    fallback, and page layout forward and back."""
    import random, zipfile, zlib
    lib = compile_host(tmp, 'books.so', ROOT/'patch/books.c')
    lib.book_inflate.argtypes = [C.c_char_p, C.c_uint, C.c_char_p, C.c_uint]
    rng = random.Random(7)
    words = [bytes(rng.choice(b'abcdefghij ') for _ in range(rng.randrange(1, 9))) for _ in range(200)]
    samples = [b'', b'a', bytes(range(256)) * 40, b' '.join(rng.choice(words) for _ in range(30000)),
               bytes(rng.randrange(256) for _ in range(5000))]
    for data in samples:
        for level, strategy in ((0, 0), (1, 0), (6, 0), (9, 0), (6, zlib.Z_FIXED), (6, zlib.Z_HUFFMAN_ONLY), (6, zlib.Z_RLE)):
            c = zlib.compressobj(level, zlib.DEFLATED, -15, 9, strategy); packed = c.compress(data) + c.flush()
            out = C.create_string_buffer(len(data) + 1)
            assert lib.book_inflate(packed, len(packed), out, len(data)) == len(data) and out.raw[:len(data)] == data
            if data: assert lib.book_inflate(packed, len(packed), out, len(data) - 1) == -1  # longer than its room
            if data: assert lib.book_inflate(packed[:len(packed) // 2], len(packed) // 2, out, len(data)) == -1
    out = C.create_string_buffer(1 << 16)
    for _ in range(3000):  # garbage never reads or writes out of bounds, and ends
        junk = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64)))
        assert -1 <= lib.book_inflate(junk, len(junk), out, 1 << 16) <= 1 << 16
    xhtml = lib.book_xhtml; xhtml.argtypes = [C.c_char_p]
    def text(s):
        b = C.create_string_buffer(s.encode()); n = xhtml(b); return b.raw[:n].decode()
    assert text('<?xml version="1.0"?><!DOCTYPE html><html><head><title>T</title><style>p{}</style></head>'
                '<body><h1>One</h1>\n  <p>A  <i>b</i>\tc&amp;d &lt;&#233;&#x1F600;&gt; &nbsp;e&bogus; &#xZZ;</p>'
                '<!-- <p>hidden</p> --><script>x<y</script><p>f<br/>g</p><div><p>h</p></div>&quot;&apos;</body></html>') == \
        'One\n\nA b c&d <\u00e9\U0001F600>  e&bogus; &#xZZ;\n\nf\ng\n\nh\n\n"\''
    assert text('<p>no end') == 'no end' and text('<head>never closed') == '' and text('&#1114112;&#0;<') == '&#1114112;&#0;'
    def epub(path, files, method=zipfile.ZIP_DEFLATED, container=True):
        with zipfile.ZipFile(path, 'w', method) as z:
            z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip')
            if container:
                z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container><rootfiles>'
                           '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            for n, d in files.items(): z.writestr(n, d)
    opf = ('<opf:package><opf:manifest><opf:item id="c2" href="Text/two%20b.xhtml#top" media-type="application/xhtml+xml"/>'
           "<opf:item href='Text/one.xhtml' id='c1'/><opf:item id=\"img\" href=\"cover.jpg\" media-type=\"image/jpeg\"/><opf:item id=\"empty\" href=\"e.xhtml\"/>"
           '</opf:manifest><opf:spine><opf:itemref idref="img"/><opf:itemref idref="c1"/><opf:itemref idref="empty"/>'
           '<opf:itemref idref="missing"/><opf:itemref idref="c2"/></opf:spine></opf:package>')
    files = {'OEBPS/content.opf': opf, 'OEBPS/Text/one.xhtml': '<html><body><p>Hello caf\u00e9</p></body></html>' * 50,
             'OEBPS/Text/two b.xhtml': '<p>Second</p>', 'OEBPS/e.xhtml': '<html><head><title>x</title></head></html>',
             'OEBPS/cover.jpg': b'\xff\xd8\xff\xe0'}
    convert = lib.book_convert; convert.argtypes = [C.c_char_p, C.c_char_p, C.POINTER(C.c_int)]
    cancel = C.c_int(0); dst = tmp/'book.txt'
    def run(src): return convert(str(src).encode(), str(dst).encode(), C.byref(cancel))
    want = '\n\n'.join(['Hello caf\u00e9'] * 50) + '\f\nSecond'
    for method in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED):
        epub(tmp/'a.epub', files, method); assert run(tmp/'a.epub') == 1 and dst.read_text() == want
    # ../ and ./ in an href resolve against the OPF's folder
    epub(tmp/'r.epub', {**files, 'OEBPS/content.opf': opf.replace("'Text/one.xhtml'", "'../OEBPS/./Text/one.xhtml'")})
    assert run(tmp/'r.epub') == 1 and dst.read_text() == want
    epub(tmp/'f.epub', {**files, 'META-INF/encryption.xml': '<CipherReference URI="OEBPS/Fonts/a.otf"/>'})
    assert run(tmp/'f.epub') == 1  # obfuscated fonts alone
    epub(tmp/'d.epub', {**files, 'META-INF/encryption.xml': '<CipherReference URI="OEBPS/Text/one.xhtml"/>'})
    assert run(tmp/'d.epub') == 0
    epub(tmp/'n.epub', files, container=False); assert run(tmp/'n.epub') == 0
    whole = (tmp/'a.epub').read_bytes()
    for cut in (0, 10, len(whole) // 2, len(whole) - 30):
        (tmp/'t.epub').write_bytes(whole[:cut]); assert run(tmp/'t.epub') == 0
    for _ in range(200):  # flipped bytes: a refusal or some text, never a crash
        b = bytearray(whole); b[rng.randrange(len(b))] ^= 1 << rng.randrange(8); (tmp/'x.epub').write_bytes(b); run(tmp/'x.epub')
    assert run(tmp/'none.epub') == 0
    cancel.value = 1; assert run(tmp/'a.epub') == 0; cancel.value = 0
    lib.book_char.argtypes = [C.c_char_p, C.c_uint, C.POINTER(C.c_uint)]
    def chars(b):
        i = C.c_uint(0); out = []
        while i.value < len(b): out.append(lib.book_char(b, len(b), C.byref(i)))
        return out
    assert chars('a\u00e9\u20ac\U0001F600'.encode()) == [97, 0xe9, 0x20ac, 0x1f600]
    assert chars(b'\xe9t\xc3') == [0xe9, ord('t'), 0xc3] and chars(b'\xe2\x82') == [0xe2, 0x82]
    MEASURE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint); LINE = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.POINTER(C.c_uint), C.c_int)
    lines = []
    measure = MEASURE(lambda ctx, c: 10); line = LINE(lambda ctx, row, s, n: lines.append((row, ''.join(map(chr, s[:n])))))
    lib.book_page.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_int, C.c_int, MEASURE, LINE, C.c_void_p]
    lib.book_back.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_int, C.c_int, MEASURE, C.c_void_p]
    def page(t, pos, rows=3, width=100):
        lines.clear(); return lib.book_page(t, len(t), pos, rows, width, measure, line, None), [l for _, l in lines]
    t = b'\n\xef\xbb\xbfThe quick brown fox\r\njumps over the lazy dog\n\nAndsuperlongwordhere end\fNext'
    # Wraps at the last space (a space at the edge is used up), mid-word without one; the BOM and
    # the top's line breaks are dropped; a \f ends the page; a page from it starts after it.
    assert page(t, 0, rows=9) == (t.index(b'\f'), ['The quick', 'brown fox', 'jumps over', 'the lazy', 'dog', '', 'Andsuperlo', 'ngwordhere', 'end'])
    assert page(t, t.index(b'\f'), rows=9) == (len(t), ['Next'])
    assert page(t, 0) == (t.index(b'the'), ['The quick', 'brown fox', 'jumps over'])
    # Back: in a text shorter than the look-back, exactly the page before; beyond it, a page that
    # reaches the one asked about.
    starts = [0]
    while starts[-1] < len(t): starts.append(page(t, starts[-1])[0])
    for a, b in zip(starts, starts[1:-1]): assert lib.book_back(t, len(t), b, 3, 100, measure, None) == a
    assert lib.book_back(t, len(t), 0, 3, 100, measure, None) == 0
    big = b'\n'.join(b' '.join(rng.choice(words) for _ in range(rng.randrange(1, 40))) for _ in range(800))
    pos = 0
    for _ in range(60):
        pos = page(big, pos, rows=10, width=320)[0]
        b = lib.book_back(big, len(big), pos, 10, 320, measure, None)
        assert b < pos and page(big, b, rows=10, width=320)[0] >= pos and pos - b < 3072
    print('Books: inflate, XHTML text, EPUB conversion and refusals, UTF-8 and page layout passed.')

def games_check(tmp):
    """Games (games.c): every bundled Sudoku and 200 shuffled ones have one solution, clues stay,
    conflicts and completion; saves' magic and checksum; Vortex's paddle, bricks, lives, levels,
    speed cap, no tunnelling and determinism; Snake's turns, growth, collisions and food."""
    import random
    lib = compile_host(tmp, 'games.so', ROOT/'patch/games.c')
    class Sudoku(C.Structure):
        _fields_ = [('magic', C.c_uint), ('cell', C.c_ubyte * 81), ('cur', C.c_ubyte), ('pad', C.c_ubyte * 2), ('sum', C.c_uint)]
    class Vortex(C.Structure):
        _fields_ = [(k, C.c_int) for k in ('state', 'lives', 'level', 'score', 'pa', 'x', 'y', 'vx', 'vy')] + \
                   [('brick', C.c_ushort * 3), ('pad', C.c_ushort)]
    assert C.sizeof(Sudoku) == 92 and C.sizeof(Vortex) == 44
    def solutions(g, limit=2):
        g = list(g); rows, cols, boxes = [0] * 9, [0] * 9, [0] * 9
        for i, v in enumerate(g):
            if v:
                b, r, c = 1 << v, i // 9, i % 9
                if (rows[r] | cols[c] | boxes[r // 3 * 3 + c // 3]) & b: return 0
                rows[r] |= b; cols[c] |= b; boxes[r // 3 * 3 + c // 3] |= b
        found = []
        def go():
            best = None
            for i in range(81):
                if not g[i]:
                    r, c = divmod(i, 9); m = ~(rows[r] | cols[c] | boxes[r // 3 * 3 + c // 3]) & 0x3fe
                    if not m: return
                    if best is None or bin(m).count('1') < bin(best[1]).count('1'): best = (i, m)
            if best is None: found.append(list(g)); return
            i, m = best; r, c = divmod(i, 9); x = r // 3 * 3 + c // 3
            for v in range(1, 10):
                b = 1 << v
                if m & b and len(found) < limit:
                    g[i] = v; rows[r] |= b; cols[c] |= b; boxes[x] |= b
                    go()
                    rows[r] ^= b; cols[c] ^= b; boxes[x] ^= b; g[i] = 0
        go(); return found
    out = (C.c_ubyte * 81)()
    assert lib.sud_puzzles() == 24
    for k in range(24):
        lib.sud_puzzle(k, out); assert len(solutions(out)) == 1, k
    seed = C.c_uint.in_dll(lib, 'games_seed'); s = Sudoku()
    for n in range(200):
        seed.value = n * 2654435761 % 2**32 or 1; lib.sud_new(C.byref(s))
        assert all(c & 15 and c & 16 or not c for c in s.cell) and s.cur == 0
        found = solutions([c & 15 for c in s.cell]); assert len(found) == 1, n
    # a clue stays; an entry is set and cleared; conflicts by row, column and box; completion
    clue = next(i for i in range(81) if s.cell[i] & 16); blank = next(i for i in range(81) if not s.cell[i])
    assert not lib.sud_set(C.byref(s), clue, 0) and s.cell[clue] & 16
    assert not lib.sud_set(C.byref(s), blank, 10) and not lib.sud_set(C.byref(s), 81, 1)
    solution = found[0]
    for peer in (blank // 9 * 9 + (blank + 1) % 9, (blank + 9) % 81, blank // 27 * 27 + blank % 9 // 3 * 3 + (blank + 1) % 3 + 9 * ((blank // 9 + 1) % 3) - 9 * (blank // 9 % 3)):
        t = Sudoku.from_buffer_copy(s); r, c = divmod(blank, 9); pr, pc = divmod(peer, 9)
        assert peer != blank and (pr == r or pc == c or (pr // 3, pc // 3) == (r // 3, c // 3))
        v = solution[peer] if not t.cell[peer] else t.cell[peer] & 15
        if not t.cell[peer]: assert lib.sud_set(C.byref(t), peer, v)
        assert lib.sud_set(C.byref(t), blank, v) and lib.sud_conflict(C.byref(t), blank) and lib.sud_conflict(C.byref(t), peer)
    assert lib.sud_set(C.byref(s), blank, solution[blank]) and not lib.sud_conflict(C.byref(s), blank)
    assert lib.sud_set(C.byref(s), blank, 0) and s.cell[blank] == 0 and not lib.sud_done(C.byref(s))
    for i in range(81):
        if not s.cell[i] & 16: lib.sud_set(C.byref(s), i, solution[i])
    assert lib.sud_done(C.byref(s))
    # saves: sealed reads back; a zeroed (short) read, another magic or any flipped byte is none
    lib.games_seal(C.byref(s), 92, 0x31445553); assert lib.games_ok(C.byref(s), 92, 0x31445553)
    assert not lib.games_ok(C.byref(s), 92, 0x31585456) and not lib.games_ok(C.byref(Sudoku()), 92, 0x31445553)
    raw = bytearray(bytes(s))
    for i in range(92):
        b = bytearray(raw); b[i] ^= 4; t = Sudoku.from_buffer_copy(b); assert not lib.games_ok(C.byref(t), 92, 0x31445553), i
    # Vortex
    def speed(v): return round(math.hypot(v.vx, v.vy))
    v = Vortex(); lib.vortex_new(C.byref(v))
    assert (v.state, v.lives, v.level, v.score, v.pa, list(v.brick)) == (0, 3, 0, 0, 768, [0xffff] * 3)
    lib.vortex_step(C.byref(v), -800); assert v.pa == 992
    lib.vortex_step(C.byref(v), 100); assert v.pa == 68
    lib.vortex_step(C.byref(v), 700); assert v.pa == 768 and v.state == 0 and abs(v.x) < 1 << 16 and v.y < 0
    assert speed(v) == 0
    lib.vortex_launch(C.byref(v)); assert v.state == 1 and abs(speed(v) - (2 << 16)) < 64
    def until(test, limit=2000):
        for _ in range(limit):
            if test(): return
            lib.vortex_step(C.byref(v), 0)
        raise AssertionError('Vortex never got there')
    until(lambda: v.score)  # the outer ring's top brick, the ball sent back out
    assert v.score == 10 and v.brick[2] == 0xffff & ~(1 << 12) and v.brick[:2] == [0xffff] * 2 and v.vy < 0
    until(lambda: v.vy > 0)  # the paddle sends it back in
    assert v.state == 1 and v.lives == 3
    until(lambda: v.score == 30)  # the brick behind the one gone, ring 1
    assert v.brick[1] == 0xffff & ~(1 << 12)
    lib.vortex_step(C.byref(v), 512)  # the paddle away: a life lost, aiming again
    until(lambda: v.state != 1)
    assert v.state == 0 and v.lives == 2
    for lives in (1, 0):
        lib.vortex_launch(C.byref(v)); v.brick[0] = v.brick[1] = v.brick[2] = 0  # through the centre, past the paddle
        until(lambda: v.state != 1)
        assert v.lives == lives and v.state == (2 if not lives else 0)
    x, y = v.x, v.y; lib.vortex_step(C.byref(v), 0); assert (v.x, v.y, v.state) == (x, y, 2)  # over: frozen
    lib.vortex_launch(C.byref(v)); assert v.state == 2
    # the last brick: the next level, its layout, aiming
    lib.vortex_new(C.byref(v)); v.brick[0] = v.brick[1] = 0; v.brick[2] = 1 << 12
    lib.vortex_launch(C.byref(v)); until(lambda: v.level)
    assert (v.state, v.score, list(v.brick)) == (0, 110, [0x5555, 0xffff, 0xaaaa])
    lib.vortex_launch(C.byref(v)); assert abs(speed(v) - (2 << 16) - (1 << 14)) < 64
    # the speed caps; at it, from every angle, the outer ring is hit first: no ring is crossed
    for pa in range(0, 1024, 7):
        lib.vortex_new(C.byref(v)); v.level = 99; lib.vortex_step(C.byref(v), pa - 768); lib.vortex_launch(C.byref(v))
        assert abs(speed(v) - (9 << 15)) < 64
        until(lambda: v.score); assert v.score == 10 and v.brick[:2] == [0xffff] * 2 and bin(v.brick[2]).count('1') == 15, pa
    # the same inputs, the same game
    def play(seed):
        rng = random.Random(seed); w = Vortex(); lib.vortex_new(C.byref(w))
        for i in range(20000):
            if w.state == 0: lib.vortex_launch(C.byref(w))
            lib.vortex_step(C.byref(w), rng.randrange(-9, 10))
        return bytes(w)
    assert play(1) == play(1) and play(1) != play(2)
    # Snake: 20 x 20, a cell y * 20 + x; the body a ring from the tail, the head last; dir 0 up, 1 right, 2 down, 3 left
    class Snake(C.Structure):
        _fields_ = [('body', C.c_ushort * 400), ('tail', C.c_ushort), ('len', C.c_ushort), ('food', C.c_ushort),
                    ('dir', C.c_ubyte), ('turned', C.c_ubyte), ('state', C.c_ubyte), ('next', C.c_byte), ('score', C.c_int)]
    assert C.sizeof(Snake) == 816
    body = lambda s: [s.body[(s.tail + i) % 400] for i in range(s.len)]
    def fresh(food=0):
        s = Snake(); lib.snake_new(C.byref(s)); s.food = food; return s
    s = fresh(); assert (body(s), s.dir, s.state, s.score) == ([207, 208, 209], 1, 1, 0)
    assert s.food not in body(s) and lib.snake_ms(C.byref(s)) == 160
    # one turn a step and one waiting for the next, any more dropped: a quick spin U-turns into the next lane,
    # never back into itself
    for _ in range(3): lib.snake_turn(C.byref(s), 1)
    assert (s.dir, s.next) == (2, 1)
    assert lib.snake_step(C.byref(s)) == 0 and body(s) == [208, 209, 229] and (s.dir, s.next, s.state) == (3, 0, 1)
    lib.snake_turn(C.byref(s), 1); assert s.dir == 3 and s.next == 1
    assert lib.snake_step(C.byref(s)) == 0 and body(s) == [209, 229, 228] and s.dir == 0
    assert lib.snake_step(C.byref(s)) == 0 and body(s)[-1] == 208 and s.state == 1 and s.dir == 0  # the waiting turn was spent
    lib.snake_turn(C.byref(s), -1); assert s.dir == 3 and lib.snake_step(C.byref(s)) == 0 and body(s)[-1] == 207
    # an apple grows it by one, scores and speeds it up; the next apple is off the body
    s = fresh(210); assert lib.snake_step(C.byref(s)) == 1 and body(s) == [207, 208, 209, 210] and s.score == 1
    assert s.food not in body(s) and lib.snake_ms(C.byref(s)) == 156
    s.score = 99; assert lib.snake_ms(C.byref(s)) == 70
    # walls: off the right edge it dies, and a dead snake stays put
    s = fresh(); s.body[2] = 219
    assert lib.snake_step(C.byref(s)) == -1 and s.state == 2 and lib.snake_step(C.byref(s)) == -1 and body(s)[-1] == 219
    for d, head in ((0, 5), (3, 20), (2, 395)):
        s = fresh(); s.body[2] = head; s.dir = d; assert lib.snake_step(C.byref(s)) == -1 and s.state == 2, d
    # its own body kills, but the cell the tail is leaving doesn't
    s = fresh(); s.len = 5; s.body[:5] = [209, 189, 188, 208, 228]; s.dir = 0  # head 228 under 208
    assert lib.snake_step(C.byref(s)) == -1 and s.state == 2
    s = fresh(); s.len = 4; s.body[:4] = [208, 188, 189, 209]; s.dir = 3  # into 208, the tail's cell
    assert lib.snake_step(C.byref(s)) == 0 and body(s) == [188, 189, 209, 208]
    # food never lands on the body: eating with one cell left free puts the apple there, whichever it is
    for n, free in enumerate((2, 57, 399, 380)):
        seed.value = n + 1; s = fresh(0); cells = [c for c in range(400) if c not in (0, 1, free)] + [1]
        s.tail = 0; s.len = 398; s.body[:398] = cells; s.dir = 3  # the head at 1 eats the apple at 0
        assert lib.snake_step(C.byref(s)) == 1 and s.len == 399 and s.food == free and s.state == 1
    rng = random.Random(3)
    for trial in range(200):
        seed.value = trial + 1; s = fresh()
        for _ in range(400):
            lib.snake_turn(C.byref(s), rng.choice((-1, 0, 0, 1)))
            if lib.snake_step(C.byref(s)) < 0: break
            assert s.food not in body(s) and len(set(body(s))) == s.len
    # Solitaire: piles 0 stock, 1 waste, 2-5 foundations, 6-12 the columns; a card suit * 13 + rank, 64 face up
    class Sol(C.Structure):
        _fields_ = [('magic', C.c_uint), ('card', (C.c_ubyte * 24) * 13), ('len', C.c_ubyte * 13), ('cur', C.c_ubyte),
                    ('pad', C.c_ubyte * 2), ('sum', C.c_uint)]
    assert C.sizeof(Sol) == 336
    UP, S, H, D, CL = 64, 0, 13, 26, 39  # spades, hearts, diamonds, clubs
    pile = lambda s, p: list(s.card[p][:s.len[p]])
    def put(s, p, cards): s.len[p] = len(cards); s.card[p][:len(cards)] = cards
    def sound(s):  # every card once; columns face down then a run; foundations A up in one suit
        assert sorted(c & ~UP for p in range(13) for c in pile(s, p)) == list(range(52))
        for p in range(6, 13):
            c = pile(s, p); d = next((i for i, x in enumerate(c) if x & UP), len(c))
            assert all(x & UP for x in c[d:]) and (not c or c[-1] & UP)
            for a, b in zip(c[d:], c[d + 1:]):
                a, b = a & ~UP, b & ~UP; assert b % 13 == a % 13 - 1 and (a // 13 % 3 == 0) != (b // 13 % 3 == 0)
        for p in range(2, 6):
            f = [c & ~UP for c in pile(s, p)]; assert not f or f[0] % 13 == 0 and f == list(range(f[0], f[0] + len(f)))
    s = Sol()
    for n in range(50):
        seed.value = n * 2654435761 % 2**32 or 1; lib.sol_new(C.byref(s)); sound(s)
        assert [s.len[p] for p in range(13)] == [24, 0, 0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7] and s.cur == 0
        assert not any(c & UP for c in pile(s, 0)) and all(sum(c & UP for c in pile(s, 6 + k)) == UP for k in range(7))
    # draw one at a time; an empty stock takes the waste back in its order; nothing left draws nothing
    stock = pile(s, 0)
    for k in range(24): assert lib.sol_draw(C.byref(s))
    assert s.len[0] == 0 and pile(s, 1) == [c | UP for c in stock[::-1]]
    assert lib.sol_draw(C.byref(s)) and pile(s, 0) == stock and s.len[1] == 0
    put(s, 0, []); assert not lib.sol_draw(C.byref(s))
    # foundations: an Ace on an empty one, then up in its suit; columns: down in alternate colours, a King on an empty one
    t = Sol(); put(t, 1, [UP | H + 1]); put(t, 2, [UP | H]); put(t, 3, [UP | S]); put(t, 4, [])
    assert not lib.sol_move(C.byref(t), 1, 3) and not lib.sol_move(C.byref(t), 1, 4)
    assert lib.sol_move(C.byref(t), 1, 2) and pile(t, 2) == [UP | H, UP | H + 1] and t.len[1] == 0
    put(t, 6, [D + 4, UP | S + 9, UP | H + 8, UP | CL + 7]); put(t, 7, [UP | H + 10]); put(t, 8, [UP | D + 8])
    put(t, 9, [UP | D + 9]); put(t, 10, [])
    assert not lib.sol_move(C.byref(t), 6, 9) and not lib.sol_move(C.byref(t), 6, 10)  # red 9 on red 10; no King
    assert not lib.sol_move(C.byref(t), 0, 10) and not lib.sol_move(C.byref(t), 6, 1) and not lib.sol_move(C.byref(t), 6, 6)
    assert lib.sol_move(C.byref(t), 6, 8) and pile(t, 8) == [UP | D + 8, UP | CL + 7] and t.len[6] == 3  # just the 8
    assert lib.sol_move(C.byref(t), 6, 7) and pile(t, 7) == [UP | H + 10, UP | S + 9, UP | H + 8]  # the run's rest
    assert pile(t, 6) == [UP | D + 4]  # turned
    put(t, 11, [UP | S + 12, UP | H + 11]); assert lib.sol_move(C.byref(t), 11, 10) and pile(t, 10) == [UP | S + 12, UP | H + 11]
    put(t, 12, [UP | S + 1, UP | S]); put(t, 3, [])  # a foundation takes the top card only
    assert lib.sol_home(C.byref(t), 12) and pile(t, 3) == [UP | S] and lib.sol_home(C.byref(t), 12) and t.len[12] == 0
    # everything up and dealt: it all goes home; a face-down card anywhere and nothing moves
    def runs(s, down):
        for p in range(13): put(s, p, [])
        for k, (a, b) in enumerate(((S, H), (H, S), (D, CL), (CL, D))):
            put(s, 6 + k, [(a if r % 2 else b) + r | UP for r in range(12, -1, -1)])
        if down: s.card[6][0] &= ~UP
    runs(s, 1); lib.sol_finish(C.byref(s)); assert not lib.sol_won(C.byref(s)) and s.len[6] == 13
    runs(s, 0); lib.sol_finish(C.byref(s)); assert lib.sol_won(C.byref(s)) and all(s.len[p] == 13 for p in range(2, 6))
    # random play keeps every card once and every pile sound; a won game is a finished one
    won = 0
    for n in range(100):
        seed.value = n * 2654435761 % 2**32 or 1; lib.sol_new(C.byref(s)); rng = random.Random(n)
        for _ in range(400):
            moved = [(a, b) for a in range(1, 13) for b in range(2, 13) if lib.sol_move(C.byref(t := Sol.from_buffer_copy(s)), a, b)]
            home = [m for m in moved if m[1] < 6 and m[0] > 5 or m[0] == 1]  # home first, then the waste
            if moved and rng.random() < .8: lib.sol_move(C.byref(s), *rng.choice(home or moved))
            else: lib.sol_draw(C.byref(s))
            lib.sol_finish(C.byref(s)); sound(s)
            if lib.sol_won(C.byref(s)): won += 1; break
    assert won > 10, won
    print('Games: Sudoku uniqueness, clues, conflicts, completion and saves; Vortex paddle, bricks, lives, levels, speed, tunnelling and determinism; Solitaire deal, draw, moves, finish and random play; Snake turns, growth, walls, self collision and food placement passed.')

def video_check(tmp):
    """Videos' player (video.c): ffmpeg's argv for the Q2's framebuffer, frame pacing and BT volume."""
    lib = compile_host(tmp, 'q2video.so', ROOT/'patch/video.c')
    def argv(at, audio):
        a, ss = (C.c_char_p * 27)(), C.create_string_buffer(16)
        lib.ffmpeg_argv(a, ss, at, b'/mnt/mmc/Videos/a b.mp4', audio)
        return [x.decode() for x in a[:a[:].index(None)]]
    # /dev/fb0 is 320x375 BGRA (display_logo, soc_fb.ko): fitted into the landscape 375x320 view and
    # turned clockwise, as the boot logo.
    head = ['/usr/bin/ffmpeg', '-nostdin', '-loglevel', 'quiet', '-ss', '30', '-i', '/mnt/mmc/Videos/a b.mp4',
            '-map', '0:v:0', '-vf', 'scale=375:320:force_original_aspect_ratio=decrease:flags=fast_bilinear,format=bgra,'
            'pad=375:320:(ow-iw)/2:(oh-ih)/2,transpose=clock', '-r', '25', '-f', 'rawvideo', 'pipe:3']
    assert argv(30, 1) == head + ['-map', '0:a:0', '-ac', '2', '-ar', '48000', '-f', 's16le', 'pipe:4']
    assert argv(30, 0) == head
    # Frame n shows from n/25 s of the clock, and is dropped a whole frame late.
    lib.frame_due.argtypes = [C.c_int, C.c_longlong]
    assert [lib.frame_due(1, t) for t in (0, 39, 40, 79, 80)] == [0, 0, 1, 1, 2] and lib.frame_due(0, 0) == 1
    # Bluetooth: hciplayer's soft volume curve in 1/65536, clamped to 0-100, and the samples scaled by it.
    assert [lib.bt_gain(v) for v in (-5, 0, 1, 25, 49, 50, 75, 99, 100, 120)] == \
        [0, 0, 131, 3276, 6422, 6553, 36044, 64356, 65536, 65536]
    pcm = (C.c_short * 6)(32767, -32768, 1000, -1000, 1, -1)
    lib.scale(pcm, 6, 65536); assert list(pcm) == [32767, -32768, 1000, -1000, 1, -1]
    lib.scale(pcm, 5, lib.bt_gain(50)); assert list(pcm) == [3276, -3277, 99, -100, 0, -1]
    lib.scale(pcm, 6, 0); assert list(pcm) == [0] * 6
    # Mono Audio: each frame's mean in both channels, never clipping at the ends.
    pcm = (C.c_short * 8)(32767, 32767, -32768, -32768, 1000, -3000, 7, 0)
    lib.mono(pcm, 4); assert list(pcm) == [32767, 32767, -32768, -32768, -1000, -1000, 3, 3]
    pcm = (C.c_short * 4)(32767, -32768, 1000, -3000)
    lib.swap(pcm, 2); assert list(pcm) == [-32768, 32767, -3000, 1000]
    lib.mono(pcm, 2); lib.swap(pcm, 2); assert list(pcm) == [-1, -1, -1000, -1000]
    # The length from ffmpeg -i's report, for the position bar.
    assert lib.duration(b'Input #0, mov\n  Duration: 01:02:03.45, start: 0.0\n') == 3723
    assert lib.duration(b'  Duration: N/A, bitrate: N/A') == 0 and lib.duration(b'') == 0
    # A quit's place, kept for next time, but 0 within 30 s of either end; an unknown length keeps it.
    assert [lib.resume_at(p, 600) for p in (0, 29, 30, 300, 570, 571, 600)] == [0, 0, 30, 300, 570, 0, 0]
    assert lib.resume_at(5000, 0) == 5000
    for paused in (0, 1):
        assert lib.video_pause(paused, ord('a')) == 1
        assert lib.video_pause(paused, ord('r')) == 0
        assert lib.video_pause(paused, ord('p')) == 1-paused
        assert lib.video_pause(paused, ord('f')) == paused
    # The bar: along the bottom of the picture as seen, 40 px in from its ends and 23-30 px up, white
    # for the share given and black after; the picture is turned clockwise, so the bar runs down
    # columns 22-29 (picture x = panel y), from the top. Nothing else changes.
    def bar(n, total, line=1300):  # a padded line, as fix.line_length may be
        f = (C.c_ubyte * (line * 375))(*([0x55] * (line * 375)))
        lib.overlay(f, line, n, total)
        return [[f[y * line + x * 4 + b] for x in range(320) for b in range(4)] for y in range(375)], f
    p, f = bar(1, 4)
    for y in range(375):
        for x in range(320 * 4):
            want = 0x55 if not (22 * 4 <= x < 30 * 4 and 40 <= y < 335) else 0xff if y < 40 + 295 // 4 else 0
            assert p[y][x] == want, (x, y)
    assert all(f[y * 1300 + c] == 0x55 for y in range(375) for c in range(1280, 1300))
    assert [r[88] for r in bar(7, 5)[0][40:335]] == [0xff] * 295  # clamped to all
    assert [r[88] for r in bar(-3, 5)[0][40:335]] == [0] * 295 and bar(1, 0)[0][100][88] == 0x55
    # Internet Radio (-r): the ICY tags in stock ffmpeg 4.2's report, in its input dump or as updates.
    out = C.create_string_buffer(16)
    def meta(line, key, n=16):
        return lib.meta(line, key, out, n) and out.value.decode()
    assert meta(b'    icy-br          : 128\n', b'icy-br') == '128' and meta(b'    icy-name   : X', b'icy-br') == 0
    assert meta(b'[http @ 0x7f] Metadata update for StreamTitle: A - B\r', b'StreamTitle') == 'A - B'
    assert meta(b'    StreamTitle     : ', b'StreamTitle') == '' and meta(b'StreamTitle=x', b'StreamTitle') == 0
    assert meta(b'  Stream #0:0: Audio: mp3 (mp3float), 44100 Hz', b'Audio') == 'mp3 (mp3float),'
    assert meta(b'StreamTitle: a long title past the buffer', b'StreamTitle', 8) == 'a long '
    print('Videos: ffmpeg argv, frame pacing, Bluetooth volume, length and bar passed; radio tags passed.')


def audio_writer_check(tmp):
    """Exercise the actual shared writer with partial writes, underruns and lost outputs."""
    source = tmp/'writer_test.c'
    source.write_text(r'''#define main video_main
#define read mock_read
#define snd_pcm_writei mock_write
#define snd_pcm_recover mock_recover
#define snd_pcm_delay mock_delay
#define usleep mock_sleep
#include "video.c"
#undef main
extern void abort(void);
#define CHECK(x) do { if (!(x)) abort(); } while (0)
static int reads, calls, recoveries, sleeps, mode;
static unsigned tail_out;
static const short *base;
int mock_read(int fd, void *b, unsigned n) {
    if (mode==3) return reads++ ? (reads==2 ? 1024 : 0) : 2048; /* 768 frames, then EOF */
    CHECK(n==2048);
    if (reads++) return 0;
    for (unsigned i=0; i<n/2; ++i) ((short *)b)[i]=(short)i;
    return (int)n;
}
long mock_write(void *pcm, const void *b, unsigned long n) {
    if (mode==3) return tail_out += n, (long)n;
    if (!calls) base=b;
    ++calls;
    if (mode==1) return -32;
    if (mode==2) return -19;
    if (calls==1) { CHECK(n==512); return 128; }
    CHECK(b==base+256 && n==384 && *(const short *)b==256);
    if (calls==2) return -32;
    if (calls==3) return 0;
    return 384;
}
int mock_recover(void *pcm, int err, int silent) {
    ++recoveries;
    return err==-19 ? err : 0;
}
int mock_delay(void *pcm, long *delay) { *delay=mode==3 ? 100 : 0; return 0; }
const char *snd_strerror(int err) { return "mock error"; }
int mock_sleep(unsigned n) { CHECK(n==1000); ++sleeps; return 0; }
int main(void) {
    au.gain=65536;
    writer(0);
    CHECK(au.done && au.played==512 && calls==4 && recoveries==1 && sleeps==1);
    for (mode=1; mode<=2; ++mode) {
        reads=calls=recoveries=0; au.done=0;
        writer(0);
        CHECK(au.done && recoveries==1 && calls==(mode==1 ? 2 : 1));
        CHECK(au.error[0]);
    }
    reads=0; au.done=0; mode=3;
    writer(0);
    CHECK(au.done && tail_out==768 && au.played==668); /* still behind the buffer at the end */
    return 0;
}
''')
    binary=tmp/'writer_test'
    subprocess.run(['cc','-m32','-fno-builtin','-ffunction-sections','-fdata-sections',
                    '-I',str(ROOT/'patch'),str(source),'-Wl,--gc-sections','-o',str(binary)],check=True)
    subprocess.run([str(binary)],check=True)
    print('Audio writer: partial frames retained, underrun retry, zero progress, output failures and end delay passed.')

def radio_check(tmp):
    """Internet Radio (radio.c): .m3u and .pls favourites, radio-browser's m3u and CSV, URL escaping."""
    lib = compile_host(tmp, 'radio.so', ROOT/'patch/radio.c')
    def parse(fn, text, *extra):
        buf, a, b = C.create_string_buffer(text), (C.c_uint * 8)(), (C.c_uint * 8)()
        n = fn(buf, a, b, *extra)
        return [(C.string_at(C.addressof(buf) + a[i]).decode(), C.string_at(C.addressof(buf) + b[i]).decode()) for i in range(n)]
    playlist = lambda text, max=8: parse(lib.playlist, text, max)
    # #EXTINF names the next URL (CRLF, a BOM, radio-browser's extra tags); a bare URL names itself.
    assert playlist(b'\xef\xbb\xbf#EXTM3U\r\n#RADIOBROWSERUUID:1\r\n#EXTINF:-1,A, B\r\nhttp://a/1?x=1\r\n\r\n'
                    b'  https://b/2\n#EXTINF:1,\nhttp://c/3\n/local/file.mp3\n') == \
        [('A, B', 'http://a/1?x=1'), ('https://b/2', 'https://b/2'), ('http://c/3', 'http://c/3')]
    # .pls: FileN is a URL, TitleN names it; other keys are skipped. At most max.
    assert playlist(b'[playlist]\nFile1=http://p/1\nTitle1=One\nLength1=-1\nfile2=http://p/2\nNumberOfEntries=2\n') == \
        [('One', 'http://p/1'), ('http://p/2', 'http://p/2')]
    assert playlist(b'http://a\nhttp://b\nhttp://c\n', 2) == [('http://a',) * 2, ('http://b',) * 2] and playlist(b'') == []
    assert playlist(b'[playlist]\nTitle2=Two\nFile1=http://p/1\nFile2=http://p/2\nTitle1=One\n', 2) == \
        [('One', 'http://p/1'), ('Two', 'http://p/2')]
    assert playlist(b'File1=http://p/1\nTitle1=One\nFile2=http://p/2\nTitle2=Two\n', 1) == \
        [('One', 'http://p/1')]
    # The CSV after its header: a country's code, or the tag itself; a quoted name keeps its commas.
    countries = b'name,iso_3166_1,stationcount\n"Taiwan, Republic Of China",TW,214\r\nGermany,DE,6496\n,XX,1\nbad\n'
    assert parse(lib.choices, countries, 8, 1) == [('Taiwan, Republic Of China', 'TW'), ('Germany', 'DE')]
    assert parse(lib.choices, b'name,stationcount\npop,6387\nclassic rock,3312\n', 8, 0) == \
        [('pop', 'pop'), ('classic rock', 'classic rock')]
    out = C.create_string_buffer(64)
    lib.url_escape('drum & bass/é~'.encode(), out, 64); assert out.value == b'drum%20%26%20bass%2F%C3%A9~'
    lib.url_escape(b'abc def', out, 6); assert out.value == b'abc'  # never a cut escape
    print('Internet Radio: m3u and pls favourites, the directory\'s m3u and CSV, URL escaping passed.')

def spotify_check(tmp):
    """Spotify's browser (spotify.c): Q2SPOT/1 replies cut in place and fields escaped."""
    lib = compile_host(tmp, 'spotify.so', ROOT/'patch/spotify.c')
    def reply(text, max=50):
        buf, f = C.create_string_buffer(text), (C.c_char_p * (4 + 6 * max))()
        n = lib.spot_reply(buf, len(text), f, max)
        return n if n < 0 else (f[:4], [f[4 + 6 * i:10 + 6 * i] for i in range(n)])
    # The header, then rows; %25, %09, %0A and %0D (any case) are the bytes, UTF-8 literal.
    assert reply(b'Q2SPOT/1\n7\tok\t\tCg4%25x\ntrack\tspotify:track:1\tA%09B%0a%0D\t\xc3\xa9\tspotify:user:a:collection\t0\n') == \
        ([b'7', b'ok', b'', b'Cg4%x'], [[b'track', b'spotify:track:1', b'A\tB\n\r', b'\xc3\xa9', b'spotify:user:a:collection', b'0']])
    assert reply(b'Q2SPOT/1\n3\tlogin\tOpen Spotify\t') == ([b'3', b'login', b'Open Spotify', b''], [])
    # Malformed: no version, a bad or cut escape, %00, a NUL, a line of the wrong width, too many rows.
    for bad in (b'Q2SPOT/2\n1\tok\t\t\n', b'', b'Q2SPOT/1\n', b'Q2SPOT/1\n1\tok\t%zz\t\n', b'Q2SPOT/1\n1\tok\t\t%2',
                b'Q2SPOT/1\n1\tok\t%00\t\n', b'Q2SPOT/1\n1\tok\t\0\t\n', b'Q2SPOT/1\n1\tok\t\n',
                b'Q2SPOT/1\n1\tok\t\t\t\n', b'Q2SPOT/1\n1\tok\t\t\ntrack\tu\tt\ts\tc\n'):
        assert reply(bad) == -1, bad
    two = b'Q2SPOT/1\n1\tok\t\t\n' + b'album\tu\tt\ts\t\t\n' * 2
    assert len(reply(two, 2)[1]) == 2 and reply(two, 1) == -1
    out = C.create_string_buffer(20)
    assert lib.spot_escape(b'a%b\tc\nd\re\xc3\xa9', out, 20) == 19 and out.value == b'a%25b%09c%0Ad%0De\xc3\xa9'
    assert lib.spot_escape(b'abc', out, 3) == -1 and lib.spot_escape(b'%', out, 3) == -1 and lib.spot_escape(b'%', out, 4) == 3
    print('Spotify: Q2SPOT/1 replies, escapes and malformed input passed.')

TIDAL_DL = r'''#include <assert.h>
#include <dirent.h>
#include <errno.h>
#include <sys/wait.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#define tk_snprintf snprintf
#define pthread_mutex_lock(m) pthread_mutex_lock((pthread_mutex_t *)(m))
#define pthread_mutex_unlock(m) pthread_mutex_unlock((pthread_mutex_t *)(m))
#define sem_wait(s) sem_wait((sem_t *)(s))
#define sem_post(s) sem_post((sem_t *)(s))
#define ringnav_dark_usleep usleep
#define I(p, o) (*(int *)((char *)(p) + (o)))
#define P(p, o) (*(void **)((char *)(p) + (o)))
#define REC_TIDAL_ID 0
#define REC_TITLE 0
#define REC_ARTIST 0
#define REC_ALBUM 0
#define REC_COVER 0
#define fork() (-1) /* tag's ffmpeg: no remux here, the copy stays as downloaded */
#define child_exit _exit
static int thumb(const char *s, const char *d, int w, int h) { return 0; }
static long file_size(const char *p) { return -1; }
static int quality = 27; static void *queue;
#define tidalStreamingLevelNow (&quality)
#define mcl_pdeqplaylist (&queue)
static _Atomic int status = 1, gate, performing, performs, ended;
static _Atomic unsigned long long clk = 1000;
static char last[64];
static int mclGetPlayStatus(void) { return status; }
static unsigned long long now_ms(void) { return clk; }
/* stTidalTracks as on MIPS: total, limit, offset and the deque at byte 12, here rows' address. */
static unsigned char tracks[24], tidal_albuminfo_thread_run, tidal_collection_thread_run;
static int rows, runs, fail_at, reopen_at;
#define stTidalTracks tracks
static int deque_size(void *q) { return q == &rows ? rows : 0; }
static void *deque_at(void *q, unsigned i) { return 0; }
/* a stock list thread only saves the reply; its page's parser appends what the file holds, so a
 * reply left from the last page would append that page again */
static int page(void) { /* the page at the offset, unless scripted to fail */
    if (++runs == reopen_at) I(tracks, 8) = 0;
    char p[32]; snprintf(p, sizeof p, "/tmp/tidal_%u", (unsigned)pthread_self());
    FILE *f = runs != fail_at ? fopen(p, "w") : 0;
    if (f) fputs("{}", f), fclose(f);
    return 0;
}
static int parse(const char *path) {
    char want[32]; snprintf(want, sizeof want, "/tmp/tidal_%u", (unsigned)pthread_self()); assert(!strcmp(path, want));
    if (access(path, 0)) return -1;
    rows = I(tracks, 8) + I(tracks, 4) < I(tracks, 0) ? I(tracks, 8) + I(tracks, 4) : I(tracks, 0);
    return 1;
}
#define parseTidalPlaylistDetailJson parse
#define parseTidalAlbumDetailJson parse
#define parseTidalColTracksJson parse
static int stubbed(void) { /* the page's own parse is left an empty reply; removes it */
    char p[32], b[32] = ""; snprintf(p, sizeof p, "/tmp/tidal_%u", (unsigned)pthread_self());
    FILE *f = fopen(p, "r"); if (!f) return 0;
    fgets(b, sizeof b, f); fclose(f); remove(p); return !strcmp(b, "{\"items\":[]}");
}
int stock_tidalplaylist_trampoline(void *id) { assert(id == &runs); return page(); }
int stock_tidalalbum_trampoline(void *id) { return page(); }
int stock_tidalfavs_trampoline(void *offset) { assert((long)offset == I(tracks, 8)); return page(); }
static void list(int total, int offset, int fail, int reopen) {
    I(tracks, 0) = total, I(tracks, 4) = 50, I(tracks, 8) = offset, P(tracks, 12) = &rows;
    rows = offset, runs = 0, fail_at = fail, reopen_at = reopen;
    tidal_albuminfo_thread_run = tidal_collection_thread_run = 1;
}
static void headset_manual(void) {}
static int card_space(const char *d) { return 1; }
static int stream_cache(int s) { return 2; }
int stock_tidal_trampoline(const char *u) { return 0; }
int stock_tidaltrack_trampoline(void *a, void *b, void *c, void *d) { return 0; }
static int __xstat(int v, const char *p, unsigned *st) {
    struct stat s; if (stat(p, &s)) return -1; st[12] = s.st_size, st[16] = s.st_mtime; return 0;
}
typedef unsigned (*sink_t)(const void *, unsigned, unsigned, void *);
typedef int (*prog_t)(void *, long long, long long, long long, long long);
static struct { sink_t sink; void *data; prog_t prog; void *pdata; const char *url; } h;
static void *curl_easy_init(void) { return &h; }
static int curl_easy_setopt(void *c, int o, ...) {
    va_list ap; va_start(ap, o);
    if (o == 10002) h.url = va_arg(ap, const char *);
    if (o == 20011) h.sink = va_arg(ap, sink_t);
    if (o == 10001) h.data = va_arg(ap, void *);
    if (o == 20219) h.prog = va_arg(ap, prog_t);
    if (o == 10057) h.pdata = va_arg(ap, void *);
    va_end(ap); return 0;
}
static int curl_easy_perform(void *c) {
    snprintf(last, sizeof last, "%s", h.url); ++performs; performing = 1;
    while (gate) if (h.prog(h.pdata, 0, 0, 0, 0)) { performing = 0; return 42; } else usleep(1000);
    h.sink("data", 1, 4, h.data); performing = 0; return 0;
}
static int curl_easy_getinfo(void *c, int i, long *code) { *code = 200; return 0; }
static void curl_easy_cleanup(void *c) { }
static int renamed(const char *a, const char *b) { int r = rename(a, b); ++ended; return r; }
static int unlinked(const char *p) { int r = unlink(p); ++ended; return r; }
#define rename renamed
#define unlink unlinked
TIDAL_SOURCE
static void wait_for(_Atomic int *v, int want) { for (int i = 0; i < 3000 && *v != want; i++) usleep(1000); assert(*v == want); }
static int has(const char *id) { char p[300]; snprintf(p, sizeof p, TIDAL_DIR "/%s-27.flac", id); return !access(p, 0); }
static void play(const char *id) { char u[64]; snprintf(u, sizeof u, "https://x/%s.flac", id); tidal_play(u, id); }
static void finish(int n) { usleep(20000); gate = 0; wait_for(&ended, n); usleep(20000); }
int main(void) {
    gate = 1; play("A"); wait_for(&performing, 1); /* stopped 2 s between tracks, then playing: kept */
    clk += 2000; usleep(20000); status = 2; finish(1); assert(has("A"));
    gate = 1; play("B"); wait_for(&performing, 1); /* stopped 3 s: Stop, dropped */
    status = 1; usleep(20000); clk += 3001; wait_for(&ended, 2); assert(!has("B"));
    status = 2; gate = 1; play("C"); wait_for(&performing, 1); /* the same id again: one transfer */
    play("C"); finish(3); assert(has("C") && performs == 3);
    gate = 1; play("D"); wait_for(&performing, 1); /* a skip: D dropped, the same worker takes E */
    play("E"); wait_for(&ended, 4); wait_for(&performing, 1); assert(!strcmp(last, "https://x/E.flac"));
    finish(5); assert(!has("D") && has("E"));
    gate = 1; play("F"); wait_for(&performing, 1); /* G's job overtaken by a cached play: dropped */
    play("G"); play("A"); finish(6); usleep(50000); assert(!has("F") && !has("G") && performs == 6);
    /* Clear All under a running download: every file but .cache-size goes, and the copy that then
     * finishes is dropped, not renamed into place. */
    FILE *f = fopen(TIDAL_DIR "/.cache-size", "w"); fputs("2048M", f); fclose(f);
    gate = 1; play("H"); wait_for(&performing, 1);
    tidal_clear(); int e = ended; finish(e + 1);
    int left = 0; DIR *d = opendir(TIDAL_DIR);
    for (struct dirent *x; (x = readdir(d));) left += strcmp(x->d_name, ".") && strcmp(x->d_name, "..");
    closedir(d); assert(left == 1 && !access(TIDAL_DIR "/.cache-size", 0) && !has("H"));
    /* Track lists: every page, then the done flag; a failed page or a re-open ends the run. */
    list(120, 0, 0, 0); ringnav_tidal_playlist(&runs);
    assert(rows == 120 && runs == 3 && I(tracks, 8) == 100 && tidal_albuminfo_thread_run == 2 && stubbed());
    list(50, 0, 0, 0); ringnav_tidal_album(0); assert(rows == 50 && runs == 1 && I(tracks, 8) == 0 && stubbed());
    list(300, 0, 2, 0); ringnav_tidal_album(0); assert(rows == 50 && runs == 2 && tidal_albuminfo_thread_run == 2 && stubbed());
    list(300, 0, 1, 0); ringnav_tidal_album(0); assert(rows == 0 && runs == 1 && !stubbed()); /* stock's own error */
    list(300, 0, 0, 2); ringnav_tidal_playlist(&runs); assert(runs == 2 && stubbed());
    list(5000, 0, 0, 0); ringnav_tidal_playlist(&runs); assert(rows == 2000 && runs == 40 && stubbed());
    /* favourites from the scroll handler's second page: the offset is the thread's argument */
    list(175, 50, 0, 0); ringnav_tidal_favs((void *)50L);
    assert(rows == 175 && runs == 3 && I(tracks, 8) == 150 && tidal_collection_thread_run == 2 && stubbed());
    return 0;
}
'''

SHELF_SHIM = r"""
#include <stdio.h>
#include <stdlib.h>
void *read_all(const char *path, unsigned max, unsigned *size) { /* scrobble.c's */
    FILE *f = fopen(path, "rb");
    char *p = 0;
    long n = f && !fseek(f, 0, 2) ? ftell(f) : -1;
    if (n >= 0 && (unsigned long)n <= max && !fseek(f, 0, 0) && (p = calloc(1, n + 1)) && fread(p, 1, n, f) != (size_t)n)
        free(p), p = 0;
    if (f) fclose(f);
    *size = p ? n : 0;
    return p;
}
"""


def netupdate_check(tmp):
    """Network Update (netupdate.c): the latest tag from GitHub's releases/latest redirect."""
    (tmp/'shelf_shim.c').write_text(SHELF_SHIM)
    lib = compile_host(tmp, 'netupdate.so', ROOT/'patch/netupdate.c', tmp/'shelf_shim.c')
    lib.netupdate_tag.restype = C.c_char_p
    tag = lib.netupdate_tag
    assert tag(b'https://github.com/DiamondBond/q2-pod/releases/tag/1.3.5') == b'1.3.5'
    for bad in (None, b'', b'https://github.com/DiamondBond/q2-pod/releases', b'https://github.com/login',
                b'https://github.com/DiamondBond/q2-pod/releases/tag/'):
        assert tag(bad) is None, bad
    return lib


def shelf_check(tmp, lib):
    """Q2 Shelf (netupdate.c): GitHub's release JSON, the unzip listing's checks, and an install and
    remove of Rockbox, its themes, Spotify and the card apps on a scratch card, host unzip (Info-ZIP)
    for busybox's."""
    import shutil, zipfile
    def release(json):
        tag, url, size = C.create_string_buffer(32), C.create_string_buffer(256), C.c_uint()
        ok = lib.shelf_release(json, tag, 32, url, 256, C.byref(size))
        return (tag.value.decode(), url.value.decode(), size.value) if ok else None
    gh = 'https://github.com/DiamondBond/'
    assert release((ROOT/'test/shelf/q2-rockbox.json').read_bytes()) == ('v3.2', gh + 'q2-rockbox/releases/download/v3.2/rockbox.zip', 13018581)
    assert release((ROOT/'test/shelf/q2-rockbox-themes.json').read_bytes()) == \
        ('v1.3', gh + 'q2-rockbox-themes/releases/download/v1.3/q2-rockbox-themes-1.3.zip', 44591509)
    spot = (ROOT/'test/shelf/q2-librespot.json').read_bytes()
    assert release(spot) == ('q2-v1.9', gh + 'q2-librespot/releases/download/q2-v1.9/q2-librespot-v1.9-2ea4406.zip', 7108981)
    pretty = b'{"tag_name" : "1.0", "assets" : [ {"name": "SHA256SUMS", "size": 9, "browser_download_url": "x"},\n' \
             b'  {"name": "a.zip", "size": 5, "browser_download_url": "https://h/a.zip"} ] }'
    assert release(pretty) == ('1.0', 'https://h/a.zip', 5)
    for bad in (b'', b'{}', spot.replace(b'.zip"', b'.tar"'), spot.replace(b'"tag_name"', b'"tag"'),
                pretty.replace(b'"size": 5', b'"size": 0'), b'{"tag_name":"a\\"b","assets":[]}'):
        assert release(bad) is None, bad[:40]
    def unpacked(text, cap=1 << 30):
        total = C.c_uint(); n = lib.shelf_unpacked(text.encode(), cap, C.byref(total))
        return (n, total.value) if n >= 0 else None
    head = '  Length      Date    Time    Name\n---------  ---------- -----   ----\n'
    row = lambda size, name: f'{size:9}  10-06-2026 13:18   {name}\n'
    foot = '---------                     -------\n 19786468                     3 files\n'
    assert unpacked(head + row(19783496, '.spotify/librespot') + row(2149, '.spotify/my run') + row(0, '.spotify/') + foot) == (3, 19785645)
    assert unpacked(head + foot) == (0, 0) and unpacked('') == (0, 0)
    assert unpacked(row(1000000000, '.rockbox/big') + row(4000000000, '.rockbox/huge')) is None  # past the cap
    assert unpacked(row(1, '.rockbox/a') + row(2, '.rockbox/b'), 2) is None and unpacked(row(1, '.rockbox/..a'), 2) == (1, 1)
    for evil in ('/etc/passwd', '.rockbox/../../x', '..', '.rockbox/..', '../x'):
        assert unpacked(head + row(1, '.rockbox/ok') + row(1, evil) + foot) is None, evil
    listed = lambda text, path: lib.shelf_listed(text, len(text or b''), path)
    assert listed(b'.rockbox/a\0.rockbox/fonts/b\0', b'.rockbox/fonts/b') and not listed(b'.rockbox/a\0', b'.rockbox/') and not listed(None, b'x')

    # Install and remove on a scratch card, from zips laid out as the releases are.
    card = tmp/'root/mnt/mmc'; staging = card/'.q2/staging'
    def zip_of(path, entries):
        with zipfile.ZipFile(path, 'w') as z:
            for name, data in entries: z.writestr(name, data)
        return path
    font = '.rockbox/fonts/15-Adobe-Helvetica.fnt'
    synthetic = {0: zip_of(tmp/'rockbox.zip', [('.rockbox/', b''), ('.rockbox/rockbox', b'ELF3.2'), (font, b'rb'),
                                                ('.rockbox/codecs/flac.codec', b'c'), ('.rockbox/config.cfg', b'release')]),
                 1: zip_of(tmp/'themes.zip', [('INSTALL.txt', b'readme'), ('.rockbox/._fonts', b'mac'), (font, b'th'),
                                              ('.rockbox/wps/Big Theme.wps', b'w'), ('.rockbox/themes/._x.cfg', b'mac'),
                                              ('__MACOSX/.rockbox/._wps', b'mac')]),
                 2: zip_of(tmp/'spotify.zip', [('.spotify/run', b'#!/bin/sh'), ('.spotify/librespot', b'ELF'),
                                               ('.spotify/cache/credentials.json', b'theirs'), ('.spotify/.cache-size', b'1M')])}
    # sources.list's lines into the apps: the id less "q2-", Q2 Pod's own by their repo's name.
    class App(C.Structure):
        _fields_ = [('name', C.c_char * 32), ('id', C.c_char * 16), ('repo', C.c_char * 80), ('dir', C.c_char * 17),
                    ('bin', C.c_char * 40), ('latest', C.c_char * 32), ('keep', C.c_char_p)]
    apps = (App * 16).in_dll(lib, 'shelf_apps')
    def sources(text, into=apps):
        n = lib.shelf_sources(text.encode(), into, 16)
        return [(a.repo.decode(), a.name.decode(), a.id.decode(), a.dir.decode(), a.bin.decode(), a.keep.decode()) for a in into[:n]]
    scratch = (App * 16)()
    assert sources('# a comment\n\n  me/q2-foo  Foo Bar \r\nme/plain\nx/q2-librespot\nx/q2-rockbox-themes Themes\n', scratch) == [
        ('me/q2-foo', 'Foo Bar', 'foo', '.foo', '.foo/q2-foo', ''), ('me/plain', 'plain', 'plain', '.plain', '.plain/q2-plain', ''),
        ('x/q2-librespot', 'q2-librespot', 'spotify', '.spotify', '.spotify/librespot', "'.spotify/cache/*' .spotify/.cache-size"),
        ('x/q2-rockbox-themes', 'Themes', 'themes', '.rockbox', '', '.rockbox/config.cfg')]
    for bad in ('me/q2-Foo', 'me/q2-a;b', 'me/q2-' + 'a' * 16, 'me', '/q2-x', 'm e/q2-x', 'me/q2-x/y', 'me/q2-x a\x01b',
                'me/q2-spotify', 'me/q2-x N' + 'n' * 31, 'a.b/q2-x', 'me/..'):
        assert sources(bad, scratch) == [], bad
    assert [a[2] for a in sources('a/q2-x\nb/q2-x\nc/x\n', scratch)] == ['x']  # a repeated id: the first
    assert len(sources(''.join(f'me/q2-a{i}\n' for i in range(20)), scratch)) == 16
    C.c_int.in_dll(lib, 'shelf_n').value = len(sources((ROOT/'sources.list').read_text()))
    ids = [a.id.decode() for a in apps[:6]]
    assert ids == ['rockbox', 'themes', 'spotify', 'deezer', 'soulseek', 'filebrowser'], ids
    def install(i, zip_path):
        shutil.rmtree(staging, ignore_errors=True); staging.mkdir(parents=True); (card/'.q2/apps').mkdir(exist_ok=True)
        shutil.copy(zip_path, staging/f'{ids[i]}.part')
        total = C.c_uint(); r = lib.shelf_list(i, C.byref(total))
        if r: return r
        assert total.value > 0 and lib.shelf_unpack(i) == 0
        r = lib.shelf_place(i); shutil.rmtree(staging); return r
    def tree():
        return sorted(str(f.relative_to(card)) for f in card.rglob('*') if f.is_file() and '.q2' not in f.parts)
    def files(i): return (card/f'.q2/apps/{ids[i]}.files').read_text().split('\n')[:-1]
    shutil.rmtree(card, ignore_errors=True); (card/'.rockbox').mkdir(parents=True); (card/'.spotify/cache').mkdir(parents=True)
    (card/'.rockbox/config.cfg').write_bytes(b'mine'); (card/'.spotify/cache/credentials.json').write_bytes(b'login')
    (card/'.spotify/.cache-size').write_bytes(b'4096M'); (card/'Music').mkdir(); (card/'Music/a.flac').write_bytes(b'song')
    for i in range(3): assert install(i, synthetic[i]) == 0, i
    have = tree()
    assert '.rockbox/rockbox' in have and '.spotify/librespot' in have and font in have and 'INSTALL.txt' not in have
    assert not [f for f in have if '/._' in f or f.startswith('__MACOSX')] and not list(staging.parent.glob('staging'))
    assert (card/'.rockbox/config.cfg').read_bytes() == b'mine' and (card/'.spotify/cache/credentials.json').read_bytes() == b'login'
    assert (card/'.spotify/.cache-size').read_bytes() == b'4096M' and not [f for f in files(2) if 'cache' in f]
    assert set(files(0)) | set(files(1)) | set(files(2)) == set(have) - {'.rockbox/config.cfg', '.spotify/cache/credentials.json', '.spotify/.cache-size', 'Music/a.flac'}
    assert font in files(0) and font in files(1)
    assert install(0, synthetic[0]) == 0 and tree() == have  # an update: the same files again
    lib.shelf_remove(1)  # the themes: the font Rockbox lists stays, and so does Rockbox
    assert font in tree() and '.rockbox/rockbox' in tree() and not set(files(1) if (card/'.q2/apps/themes.files').exists() else []) and \
        not [f for f in tree() if f in set(have) - set(files(0)) and f.startswith('.rockbox/') and f != '.rockbox/config.cfg']
    lib.shelf_remove(0); lib.shelf_remove(2)
    assert tree() == ['.rockbox/config.cfg', '.spotify/.cache-size', '.spotify/cache/credentials.json', 'Music/a.flac']
    assert [d.name for d in (card/'.rockbox').iterdir()] == ['config.cfg'] and not list((card/'.q2/apps').iterdir())
    # A failed codec replacement must leave the old binary and installed file list intact.
    (card/'.rockbox/rockbox').write_bytes(b'old')
    (card/'.rockbox/codecs').mkdir(); (card/'.rockbox/codecs/flac.codec').mkdir()
    (card/'.q2/apps/rockbox.files').write_bytes(b'old list')
    assert install(0, synthetic[0]) == 3  # SH_CARD: a directory blocks the codec's rename
    assert (card/'.rockbox/rockbox').read_bytes() == b'old'
    assert (card/'.q2/apps/rockbox.files').read_bytes() == b'old list'
    evil = zip_of(tmp/'evil.zip', [('.rockbox/ok', b'x'), ('.rockbox/../../../escaped', b'x')])
    assert install(0, evil) == 5 and not (tmp/'escaped').exists() and not (card.parent/'escaped').exists()  # SH_BAD, nothing unpacked
    assert install(1, zip_of(tmp/'empty.zip', [('INSTALL.txt', b'readme')])) == 5  # nothing for the app's folder

    # The card apps (patch/addon.c), zipped as their releases are: the app, its launcher and manifest.
    # What each saved (Deezer's sign-in, settings and queue; Soulseek's settings, transfers and
    # share index; File Browser's settings), never in a release, survives an install over it, an
    # update and a remove.
    saved = {3: ['.deezer/arl', '.deezer/quality', '.deezer/organize', '.deezer/mode', '.deezer/layout',
                 '.deezer/queue.json'],
             4: ['.soulseek/config.json', '.soulseek/downloads.json', '.soulseek/shares.cache'],
             5: ['.filebrowser/config.json']}
    shutil.rmtree(card); card.mkdir(parents=True)
    for i, kept in saved.items():
        d = '.' + ids[i]
        for f in kept: (card/f).parent.mkdir(exist_ok=True); (card/f).write_bytes(b'mine')
        z = zip_of(tmp/f'{ids[i]}.zip', [(f'{d}/q2-{ids[i]}', b'ELF'), (f'{d}/run', b'#!/bin/sh'),
                                         (f'{d}/q2app', b'name=x\n'), (f'{d}/README.txt', b'r')])
        assert install(i, z) == 0 and install(i, z) == 0, i
        assert sorted(files(i)) == sorted(f'{d}/{n}' for n in (f'q2-{ids[i]}', 'run', 'q2app', 'README.txt'))
        assert all((card/f).read_bytes() == b'mine' for f in kept), i
        lib.shelf_remove(i)
        assert sorted(str(f.relative_to(card)) for f in (card/d).rglob('*')) == sorted(kept), i
    print("Q2 Shelf: release JSON, unzip listing checks, install, update, failed move, remove and card apps passed.")


def online_check(tmp):
    """Lyrics and cover search (onlinematch.c): LRCLIB's, MusicBrainz's and iTunes's search URLs,
    percent-encoded, iTunes artwork's 600 px size, and LRCLIB lyrics less their opening credits."""
    lib = compile_host(tmp, 'onlinematch.so', ROOT/'patch/onlinematch.c')
    def call(f, t, a, n=256):
        out = C.create_string_buffer(n)
        return out.value if f(out, n, t, a) else None
    url = lambda t, a, n=256: call(lib.lrclib_url, t, a, n)
    assert url('Café & Co/2'.encode(), b'AC~DC') == \
        b'https://lrclib.net/api/search?track_name=Caf%C3%A9%20%26%20Co%2F2&artist_name=AC~DC'
    assert url(b'Solo', b'') == b'https://lrclib.net/api/search?track_name=Solo&artist_name='
    assert url(b'x' * 300, b'a') is None and url(b'Solo', b'a', 59) is None and url(b'Solo', b'a', 60)  # too long for out
    mb = 'https://musicbrainz.org/ws/2/release/?fmt=json&limit=10&query=release%3A%22'
    assert call(lib.mb_url, b'OK Computer', b'Radiohead') == (mb + 'OK%20Computer%22%20AND%20artist%3A%22Radiohead%22').encode()
    assert call(lib.mb_url, b'Say "Hi" \\o', b'') == (mb + 'Say%20%5C%22Hi%5C%22%20%5C%5Co%22').encode()  # Lucene-escaped, no artist clause
    assert call(lib.mb_url, b'x' * 100, b'a', 200) is None
    assert call(lib.itunes_url, b'In Rainbows', b'Radiohead') == \
        b'https://itunes.apple.com/search?entity=album&limit=5&term=Radiohead%20In%20Rainbows'
    art = C.create_string_buffer(b'https://is1-ssl.mzstatic.com/image/thumb/a/100x100.png/100x100bb.jpg')
    assert lib.itunes_art(art) and art.value == b'https://is1-ssl.mzstatic.com/image/thumb/a/100x100.png/600x600bb.jpg'
    assert not lib.itunes_art(C.create_string_buffer(b'https://x/cover.jpg'))
    lib.lrc_body.restype = C.c_char_p
    body = lambda t: lib.lrc_body(t.encode())
    sung = '[00:06.10]안 봐도 보여 what is on your mind\n[00:09.00]Line : two\n'
    assert body('[00:00.00] 作词 : X\n[00:01.00]\r\n[00:03.00] Mix Engineer : N for 411 E. 9th Ave @ Z\n' + sung) == sung.encode()
    assert body(sung) == sung.encode() and body('[offset:+200]\n[00:00.00] A : B\n' + sung).startswith(b'[offset')  # tags stay
    assert body('[00:00.00]男：你好\n') == '[00:00.00]男：你好\n'.encode()  # a duet's label is sung
    assert body('[00:00.00] Lyrics : X\n[00:01.00]\n') == b''  # nothing sung: lrclib tries the next result
    def fold(s, n=256):
        out = C.create_string_buffer(n)
        return out.value.decode() if lib.lrc_fold(out, n, s.encode()) else None
    # Each folded code point against Python's own NFD base letters, strokes and ligatures spelled out.
    import unicodedata
    special = dict(zip('ÆæÞþßĲĳŒœ', ('AE', 'ae', 'TH', 'th', 'ss', 'IJ', 'ij', 'OE', 'oe')))
    special |= dict(zip('ÐðØøĐđĦħıĸĿŀŁłŉŊŋŦŧſ', 'DdOoDdHhikLlLlnNnTts'))
    for cp in [*range(0xc0, 0x180), *range(0x1e00, 0x1f00), 0x1a0, 0x1a1, 0x1af, 0x1b0]:
        ch = chr(cp); base = unicodedata.normalize('NFD', ch)[0]
        want = special.get(ch, base if base.isascii() and base.isalpha() else ch)
        assert fold(ch) == want, (hex(cp), fold(ch), want)
    assert fold('ẹ́x') == 'ex' and fold('×÷ẞ') == '×÷ẞ'  # combining marks dropped; no letter kept
    assert fold('Hataałii') == 'Hataalii' and fold('Rains Over Dził') == 'Rains Over Dzil' and fold('Sjøen') == 'Sjoen'
    assert fold('Straße') == 'Strasse' and fold('Æon') == 'AEon' and fold('Nơi Này Có Anh') == 'Noi Nay Co Anh'
    assert fold('Sơn Tùng M-TP') == 'Son Tung M-TP' and fold('東京 🎵 Ñu') == '東京 🎵 Nu' and fold('plain') == 'plain'
    assert fold('Hataałii', 9) == 'Hataalii' and fold('Hataałii', 8) is None  # folds into n, never longer


class LyrTrack(C.Structure):
    _fields_ = [('path', C.c_char * 0x200), ('title', C.c_char * 0x200), ('artist', C.c_char * 0x100),
                ('shadow', C.c_char * (len('/mnt/mmc/.downpic/') + 1 + 0x200))]

class LyrJob(C.Structure):
    _fields_ = [(n, C.c_int) for n in ('cancel', 'done', 'total', 'saved', 'had', 'none', 'failed', 'state')]

LRC_CANCELLED, LRC_CARD, LRC_OFFLINE, LRC_BUSY, LRC_NONE, LRC_SAVED = range(-4, 2)
LJ_DONE, LJ_OFFLINE, LJ_CARD, LJ_CANCELLED = range(4)


def album_check(tmp):
    """Download Album Lyrics (onlinematch.c): the .lrc beside a song, each track of an incomplete
    album on its own (gone, already with a .lrc, not found), LRCLIB's busy replies asked again, the
    retry once LRCLIB has answered, offline, busy and card-full ends, a cancel, no .part left behind,
    and the summary toast."""
    lib = compile_host(tmp, 'onlinematch.so', ROOT/'patch/onlinematch.c')
    def lrc(path, ext=b'.lrc', n=256):
        out = C.create_string_buffer(n)
        return out.value if lib.lrc_path(out, n, path, ext) else None
    assert lrc(b'/mnt/mmc/A/01 x.flac') == b'/mnt/mmc/A/01 x.lrc' and lrc(b'/mnt/mmc/A/b.mp3', b'.LRC') == b'/mnt/mmc/A/b.LRC'
    assert lrc(b'/mnt/mmc/A.b/song') == b'/mnt/mmc/A.b/song.lrc' and lrc(b'/mnt/mmc/.hidden') == b'/mnt/mmc/.hidden.lrc'
    assert lrc(b'a.tar.gz') == b'a.tar.lrc' and lrc(b'/x/song.flac', n=11) is None and lrc(b'/x/song.flac', n=12)
    def name(file, n=256):  # stock's .downpic name (modifystringtolrcsuffix): the last '.' on, else as it is
        out = C.create_string_buffer(n)
        return out.value if lib.lrc_name(out, n, file) else None
    assert name(b'01 x.flac') == b'01 x.lrc' and name(b'a.tar.gz') == b'a.tar.lrc' and name(b'.hidden') == b'.lrc'
    assert name(b'song') == b'song' and name(b'song.mp3', 8) is None and name(b'song.mp3', 9) == b'song.lrc'

    FETCH = C.CFUNCTYPE(C.c_int, C.POINTER(LyrTrack), C.c_char_p, C.POINTER(C.c_int))
    def run(name, songs, answers, cancel_at=None, shadows=()):
        """songs: (file, extra files beside it, on the card); answers: title -> fetch results, in turn;
        shadows: files with a .downpic copy of their lyrics."""
        album = tmp/name
        (album/'downpic').mkdir(parents=True)
        tracks = (LyrTrack * len(songs))()
        for t, (file, extra, present) in zip(tracks, songs):
            t.path, t.title, t.artist = str(album/file).encode(), file.split('.')[0].encode(), b'Artist'
            t.shadow = str(album/'downpic'/(file.rsplit('.', 1)[0] + '.lrc')).encode()
            if file in shadows: pathlib.Path(t.shadow.decode()).write_text('[00:00.00]Kugou')
            if present: (album/file).write_bytes(b'audio')
            for e in extra: (album/e).write_bytes(b'[00:01.00]mine\n')
        calls, left = [], {k: list(v) for k, v in answers.items()}
        def fetch(t, out, cancel):
            title = t.contents.title.decode(); calls.append((title, t.contents.artist.decode(), out.decode()))
            got = left[title].pop(0)
            if got == LRC_SAVED: pathlib.Path(out.decode()).write_text(f'[00:00.10]{title}\n')
            if got == LRC_CARD: pathlib.Path(out.decode()).write_text('[00:0')  # a write cut short
            if title == cancel_at: cancel[0] = 1; got = LRC_CANCELLED
            return got
        job, cb = LyrJob(), FETCH(fetch)
        state = lib.album_lyrics(C.byref(job), tracks, len(songs), cb)
        assert not [p for p in album.iterdir() if p.name.endswith('.part')], name
        assert all(not left[k] for k in left), (name, left)  # every scripted answer asked for
        return state, job, calls, album
    # An incomplete album: a file gone since the scan, two with a .lrc (either case).
    songs = [('01 Intro.flac', (), True), ('02 Song.flac', (), True), ('03 Gone.flac', (), False),
             ('04 Had.flac', ('04 Had.lrc',), True), ('05 Upper.mp3', ('05 Upper.LRC',), True),
             ('06 Tagged.m4a', (), True), ('07 NoExt', (), True)]
    state, job, calls, album = run('incomplete', songs, {'01 Intro': [LRC_NONE], '02 Song': [LRC_SAVED],
                                   '06 Tagged': [LRC_SAVED], '07 NoExt': [LRC_SAVED]})
    assert state == LJ_DONE and (job.total, job.done, job.saved, job.had, job.none, job.failed) == (7, 7, 3, 2, 1, 0)
    assert [c[0] for c in calls] == ['01 Intro', '02 Song', '06 Tagged', '07 NoExt'] and calls[1] == ('02 Song', 'Artist', str(album/'02 Song.lrc.part'))
    assert (album/'02 Song.lrc').read_text() == '[00:00.10]02 Song\n' and (album/'07 NoExt.lrc').exists()
    assert (album/'04 Had.lrc').read_text() == '[00:01.00]mine\n' and not (album/'01 Intro.lrc').exists()
    # A .downpic copy, which stock's loader would show first, goes once the song's own .lrc is saved;
    # a song LRCLIB lacks keeps its copy.
    state, job, calls, album = run('shadowed', [('0 T0.flac', (), True), ('1 T1.flac', (), True)],
                                   {'0 T0': [LRC_SAVED], '1 T1': [LRC_NONE]}, shadows={'0 T0.flac', '1 T1.flac'})
    assert state == LJ_DONE and (job.saved, job.none) == (1, 1) and (album/'0 T0.lrc').exists()
    assert not (album/'downpic'/'0 T0.lrc').exists() and (album/'downpic'/'1 T1.lrc').exists()
    # Offline from the start: no retry before LRCLIB has answered; two tracks unanswered end it.
    three = [(f'{i} T{i}.flac', (), True) for i in range(3)]
    state, job, calls, _ = run('offline', three, {'0 T0': [LRC_OFFLINE], '1 T1': [LRC_OFFLINE]})
    assert state == LJ_OFFLINE and (job.saved, job.failed, job.done) == (0, 2, 1) and len(calls) == 2
    # LRCLIB gone mid-album: each unanswered track retried once, then the job ends.
    state, job, calls, _ = run('drop', three, {'0 T0': [LRC_SAVED], '1 T1': [LRC_OFFLINE] * 2, '2 T2': [LRC_OFFLINE] * 2})
    assert state == LJ_OFFLINE and (job.saved, job.failed) == (1, 2) and len(calls) == 5
    # A blip: the retry answers.
    state, job, calls, album = run('blip', three, {'0 T0': [LRC_SAVED], '1 T1': [LRC_OFFLINE, LRC_SAVED], '2 T2': [LRC_NONE]})
    assert state == LJ_DONE and (job.saved, job.none, job.failed) == (2, 1, 0) and (album/'1 T1.lrc').exists()
    # LRCLIB busy (its 503): asked again, three requests at most; still busy counts as failed. A busy
    # reply is an answer, so the next unanswered request is retried too.
    state, job, calls, album = run('busy', three, {'0 T0': [LRC_BUSY, LRC_SAVED], '1 T1': [LRC_BUSY] * 3,
                                   '2 T2': [LRC_OFFLINE, LRC_SAVED]})
    assert state == LJ_DONE and (job.saved, job.failed) == (2, 1) and len(calls) == 7
    assert (album/'0 T0.lrc').exists() and not (album/'1 T1.lrc').exists() and (album/'2 T2.lrc').exists()
    # Busy for two tracks in a row ends the job.
    state, job, calls, _ = run('overloaded', three, {'0 T0': [LRC_BUSY] * 3, '1 T1': [LRC_BUSY] * 3})
    assert state == LJ_OFFLINE and (job.saved, job.failed, job.done) == (0, 2, 1) and len(calls) == 6
    # A card that can't be written ends the job, its cut-short .part removed.
    state, job, calls, album = run('card', three, {'0 T0': [LRC_CARD]})
    assert state == LJ_CARD and job.saved == 0 and len(calls) == 1 and not (album/'0 T0.lrc').exists()
    # A cancel mid-transfer keeps what was saved before it.
    state, job, calls, album = run('cancel', three, {'0 T0': [LRC_SAVED], '1 T1': [LRC_SAVED]}, cancel_at='1 T1')
    assert state == LJ_CANCELLED and job.saved == 1 and job.done == 1 and (album/'0 T0.lrc').exists() and not (album/'1 T1.lrc').exists()

    def note(state, saved=0, none=0, had=0, failed=0):
        job, out = LyrJob(state=state, saved=saved, none=none, had=had, failed=failed), C.create_string_buffer(96)
        lib.album_note(out, 96, C.byref(job))
        return out.value.decode()
    assert note(LJ_DONE, 9, 2, 1) == 'Album lyrics: 9 saved, 2 not found, 1 already had lyrics'
    assert note(LJ_DONE, had=12) == 'Album lyrics: 12 already had lyrics' and note(LJ_DONE) == 'Album lyrics: no songs on the card'
    assert note(LJ_OFFLINE, failed=2) == "Can't reach LRCLIB" and note(LJ_OFFLINE, 1, failed=2) == 'LRCLIB stopped answering: 1 saved, 2 failed'
    assert note(LJ_CARD, 3) == "Can't write to the card: 3 saved" and note(LJ_CANCELLED) == 'Cancelled'
    print('Album lyrics: .lrc paths, incomplete albums, busy retries, offline, card and cancel ends, and summaries passed.')


def tidal_check(tmp):
    """Tidal cache (tidal.c): the cached file's extension comes from the stream URL's path."""
    lib = compile_host(tmp, 'tidal.so', ROOT/'patch/tidal.c')
    lib.tidal_ext.restype = C.c_char_p
    ext = lambda url, n=8: lib.tidal_ext(url, C.create_string_buffer(n), n)
    assert ext(b'https://sp-pr-fa.audio.tidal.com/mediatracks/AbC/0.flac?token=1.2') == b'.flac'
    assert ext(b'https://x/a/b.mp4') == b'.mp4'
    assert ext(b'https://x.y/a/b?f=c.flac') is None and ext(b'https://x/a.') is None  # no extension in the path
    assert ext(b'https://x/a.longextension') is None and ext(b'https://x/a.flac', 5) is None  # too long for out
    lib.tidal_name.restype = C.c_char_p
    name = lambda n, artist, title, ext=b'.flac': lib.tidal_name(C.create_string_buffer(n), n, b'42-3', artist, title, ext)
    assert name(64, b'A/B', b'Song: "1"?') == b'42-3 A_B - Song_ _1__.flac'
    assert name(64, None, b'Solo') == b'42-3 Solo.flac' and name(64, b'A', b'') == b'42-3.flac'
    assert name(16, b'Artist', b'Title') == b'42-3 Artis.flac'  # cut to fit
    assert name(16, b'', '\u00e9\u00e9\u00e9'.encode()) == '42-3 \u00e9\u00e9.flac'.encode()  # never mid-character
    assert name(9, b'A', b'T') is None
    # Run the actual eviction loop with a synthetic directory, including a failed unlink.
    trim = (ROOT/'patch/tidal.c').read_text().split('static void trim(void) {', 1)[1].split('static unsigned save', 1)[0]
    source = tmp/'tidal_trim.c'
    source.write_text(r'''#include <assert.h>
#include <stdio.h>
#include <string.h>
#define TIDAL_DIR "/cache"
#define tk_snprintf snprintf
struct dirent { char d_name[16]; };
static int fail, left, cursor, attempts, choice = 2;
static int stream_cache(int spotify) { assert(!spotify); return choice; } /* 512 << choice MB */
static void *opendir(const char *p) { cursor=0; return (void *)1; }
static struct dirent *readdir(void *d) {
    static struct dirent e;
    if (cursor == left) return 0;
    snprintf(e.d_name, sizeof e.d_name, "%d.flac", cursor++); return &e;
}
static int closedir(void *d) { return 0; }
static int __xstat(int v, const char *p, unsigned *s) {
    s[12]=1200u<<20; s[16]=1; return 0;
}
static int unlink(const char *p) { ++attempts; if (fail) return -1; --left; return 0; }
static void trim(void) {''' + trim + r'''
int main(void) {
    left=3; trim(); assert(left==1 && attempts==2);
    fail=1; left=3; attempts=0; trim(); assert(left==3 && attempts==1);
    fail=0; choice=3; attempts=0; trim(); assert(left==3 && !attempts); /* 4 GB holds 3600 MB */
    choice=0; trim(); assert(left==0 && attempts==3); /* 512 MB holds none of 1200 */
    return 0;
}
''')
    binary = tmp/'tidal_trim'
    subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', str(source), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=5)
    import re
    # The download worker on a real pthread, curl scripted: perform runs the progress callback while
    # gate is open, then hands over a body (unless cancelled) and the worker renames or unlinks.
    body = re.sub(r'^(#include|#ifndef PEQ_HOST|#endif|__asm__).*$', '', (ROOT/'patch/tidal.c').read_text(), flags=re.M)
    (tmp/'tidal_dl.c').write_text(TIDAL_DL.replace('TIDAL_SOURCE', body))
    binary = tmp/'tidal_dl'
    subprocess.run(['cc', '-pthread', '-D_GNU_SOURCE', f'-DTIDAL_DIR="{tmp}/tidal"', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    str(tmp/'tidal_dl.c'), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True, timeout=30)
    print('Tidal cache: stream file extensions, eviction at the chosen cap, failed deletion, Stop, re-fetch and skip downloads, Clear All under a download, Tidal lists paged to the end passed.')

if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--captures', type=pathlib.Path, help="also draw 3EQ's page in both iPod themes as PNGs here")
    ap.add_argument('--font', type=pathlib.Path, help='the stock font for the captures (a build directory\'s default.ttf)')
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='q2-peq-check-') as directory:
        tmp = pathlib.Path(directory); lib = library(tmp)
        parser_check(lib, tmp)
        dsp_check(lib)
        eq3_check(lib, tmp)
        editor_check(lib, tmp)
        player_check(tmp)
        visualizer_check(tmp)
        scrobble_check(tmp)
        books_check(tmp)
        games_check(tmp)
        video_check(tmp)
        audio_writer_check(tmp)
        radio_check(tmp)
        spotify_check(tmp)
        tidal_check(tmp)
        shelf_check(tmp, netupdate_check(tmp))
        online_check(tmp)
        album_check(tmp)
        if args.captures:
            args.captures.mkdir(parents=True, exist_ok=True)
            (tmp/'captures').mkdir()
            eq3_captures(tmp/'captures', args.captures, args.font or 'DejaVu-Sans')
