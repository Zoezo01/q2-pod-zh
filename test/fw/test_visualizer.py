"""visualizer.c (docs/ipod.md#visualizer, docs/internals.md#visualizer-ipod): the analysis's bands and dynamics, the
tap's frames, and Now Playing's fourth page: its timer, its paint, each style's drawing and the names. test/peq.py
checks vis_bands' sine levels in its own host build too."""
import ctypes as C
import math
import random
import struct

import pytest

from conftest import O

N, HALO, BARS, W = 1024, 64, 32, 343
SPECTRUM, SCOPE, METERS, HALO_STYLE = range(4)
DECAY, HOLD_MS, GRAVITY, FLOOR_DB, VU_REF_DB, LED_DB, FPS = 1.6, 400, 3.0, 60, -14, -1, 25
RET_REMOVE, RET_REPEAT = 7, 8
EVT_DESTROY, EVT_VALUE_CHANGED, EVT_CLICK = O['EVT_DESTROY'], O['EVT_VALUE_CHANGED'], O['EVT_CLICK']
W_X, W_Y, W_W, W_PARENT = O['W_X'], O['W_Y'], O['W_W'], O['W_PARENT']
RING, TAP_SIZE = 65536, 24 + 65536 * 8
NAMES = ['Spectrum', 'Oscilloscope', 'VU Meters', 'Halo']


class Vz(C.Structure):
    _fields_ = [('re', C.c_float * N), ('im', C.c_float * N), ('pcm', (C.c_float * 2) * N), ('hann', C.c_float * N),
                ('level', C.c_float * HALO), ('peak', C.c_float * BARS), ('fall', C.c_float * BARS),
                ('wave', (C.c_float * W) * 2), ('vu', C.c_float * 2), ('spin', C.c_float), ('bass', C.c_float),
                ('held', C.c_uint * BARS), ('lit', C.c_uint * 2), ('bin_rate', C.c_uint), ('w', C.c_int), ('bin', C.c_int * 65),
                *((k, C.c_void_p) for k in 'win slide page art cover tap'.split()),
                *((k, C.c_uint) for k in 'timer last named heard retry'.split()),
                *((k, C.c_int) for k in 'ox oy page_x style quiet'.split())]


def vz(fw):
    return fw.var('visualizer__vz', Vz)


def analyze(fw, style, rate, dt=0.04, now=100):
    fw.fn('vis_analyze', None, C.c_int, C.c_uint, C.c_float, C.c_uint)(style, rate, dt, now)


def sine(v, amp, k=12.0, phase=0.0, right=None):
    """pcm: a sine of amp, k cycles in the window, in both channels (right: its own amp)."""
    for i in range(N):
        s = math.sin(2 * math.pi * k * i / N + phase)
        v.pcm[i][0], v.pcm[i][1] = amp * s, (amp if right is None else right) * s


def silence(v):
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = 0


# The bands and the bars.

def test_bars_rise_six_tenths_of_the_way_a_tick(fw):
    v = vz(fw)
    sine(v, 1)
    analyze(fw, SPECTRUM, 44100)
    first = list(v.level)
    target = (C.c_float * HALO)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), 44100, target)
    assert max(target) > 0.99
    assert all(abs(a - 0.6 * t) < 1e-5 for a, t in zip(first, target))
    analyze(fw, SPECTRUM, 44100)
    assert all(abs(a - (f + 0.6 * (t - f))) < 1e-5 for a, f, t in zip(v.level, first, target))


def test_bars_fall_at_the_decay_rate_when_the_sound_stops(fw):
    v = vz(fw)
    for b in range(HALO):
        v.level[b] = 0.9
    levels = []
    for tick in range(14):
        analyze(fw, SPECTRUM, 0, dt=0.05, now=100 + 50 * tick)
        levels.append(v.level[5])
    want = [max(0, 0.9 - DECAY * 0.05 * (t + 1)) for t in range(14)]
    assert all(abs(a - b) < 1e-5 for a, b in zip(levels, want)) and levels[-1] == 0


def test_a_quieter_frame_falls_no_lower_than_it(fw):
    v = vz(fw)
    sine(v, 0.1)
    target = (C.c_float * HALO)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), 44100, target)
    for b in range(HALO):
        v.level[b] = 1
    analyze(fw, SPECTRUM, 44100, dt=1)
    assert all(abs(a - t) < 1e-6 for a, t in zip(v.level, target))  # 1.6 a second would go under it


def test_the_same_pitch_lands_in_the_same_band_at_any_rate(fw):
    v = vz(fw)
    out = []
    for rate in (44100, 48000, 44100):
        for i in range(N):
            v.pcm[i][0] = v.pcm[i][1] = math.sin(2 * math.pi * 1000 * i / rate)
        target = (C.c_float * HALO)()
        fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), rate, target)
        out.append(list(target).index(max(target)))
        assert v.bin_rate == rate
    band = 64 * math.log(1000 / 40) / math.log(400)  # 40 Hz to 16 kHz, log-spaced
    assert out[0] == out[1] == out[2] and abs(out[0] - band) <= 1


def test_a_sine_at_the_floor_reads_nothing(fw):
    v = vz(fw)
    sine(v, 10 ** (-(FLOOR_DB + 1) / 20))
    target = (C.c_float * HALO)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), 44100, target)
    assert max(target) == 0


def bands(fw, v, rate=44100):
    target = (C.c_float * HALO)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), rate, target)
    return list(target)


def spans(v):
    """Each band's bins as the last analysis laid them out at its rate (the test below holds them to the spec):
    from its first bin to the next band's first, at least its first, and none from half the transform up, which
    mirrors the lower half."""
    edge = list(v.bin)
    return [[k for k in range(edge[b], max(edge[b + 1], edge[b] + 1)) if k < N // 2] for b in range(HALO)]


def heard(span, k, db):
    """Each band's level, its bins span, for a sine exactly k bins up at db dBFS, from the Hann window's spectrum:
    all of it in bin k, half its amplitude (6.02 dB under) in each neighbour and nothing that reaches the floor
    further out. A band is its loudest bin, 0 at FLOOR_DB under full scale and 1 at it."""
    out = []
    for bins in span:
        db_k = db if k in bins else db + 20 * math.log10(0.5) if {k - 1, k + 1} & set(bins) else None
        out.append(None if db_k is None else min(1, max(0, 1 + db_k / FLOOR_DB)))
    return out


@pytest.mark.parametrize('rate', [44100, 48000, 22050])
def test_the_bands_are_log_spaced_from_40_hz_to_16_khz(fw, rate):
    """Band b starts at 40 Hz times 400 to the b/64th and the last ends at 16 kHz: each edge is its frequency's
    bin, within one, and no band starts before the last."""
    v = vz(fw)
    bands(fw, v, rate)
    edge = list(v.bin)
    assert edge == sorted(edge)
    assert all(abs(e - 40 * 400 ** (b / HALO) * N / rate) < 1 for b, e in enumerate(edge))


@pytest.mark.parametrize('k, db', [(1, -12), (39, 0), (42, -30), (360, -50), (100, 6)])
def test_a_band_reads_its_loudest_bin(fw, k, db):
    """Bin 1 is the first bin of the six lowest bands, narrower than a bin; 39 and 42 are the first and last of
    band 40 at 44.1 kHz; 360 is in the top band. Over full scale (an EQ's boost) reads full."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = 10 ** (db / 20) * math.sin(2 * math.pi * k * i / N)
    got = bands(fw, v)
    want = heard(spans(v), k, db)
    assert any(w == pytest.approx(min(1, 1 + db / FLOOR_DB), abs=1e-9) for w in want)
    for g, w in zip(got, want):
        assert g == pytest.approx(w, abs=2e-3) if w is not None else g < 0.02


def test_a_tone_between_two_bins_stays_in_its_bands(fw):
    """The Hann window's leakage: 1.4 dB down halfway between bins, and under the floor 10 bins out."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = math.sin(2 * math.pi * 100.5 * i / N)
    got = bands(fw, v)
    near = [b for b, bins in enumerate(spans(v)) if {100, 101} & set(bins)]
    far = [b for b, bins in enumerate(spans(v)) if bins and (max(bins) < 90 or min(bins) >= 111)]
    assert near and all(1 - 1.5 / FLOOR_DB < got[b] <= 1 for b in near)
    assert len(far) > HALO - 12 and all(got[b] == 0 for b in far)


def test_a_bar_is_its_two_bands_mean(fw):
    v = vz(fw)
    v.level[6], v.level[7] = 0.2, 0.6
    assert fw.fn('visualizer__bar', C.c_float, C.c_int)(3) == pytest.approx(0.4)


@pytest.mark.parametrize('side', [0, 1])
def test_one_channel_alone_reads_6_db_under_both(fw, side):
    """The bands read the mono mix, half of each channel."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][side], v.pcm[i][1 - side] = math.sin(2 * math.pi * 42 * i / N), 0
    assert bands(fw, v)[40] == pytest.approx(1 + 20 * math.log10(0.5) / FLOOR_DB, abs=2e-3)


def test_digital_silence_reads_nothing_after_a_loud_frame(fw):
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = 0.5  # DC: the last transform's bin 0 is large
    bands(fw, v)
    silence(v)
    assert max(bands(fw, v)) == 0


def test_bands_past_half_the_rate_read_nothing(fw):
    """At 22.05 kHz the top bands lie past 11 kHz: a sine's mirror image in the upper half of the transform is
    not a sound there."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = math.sin(2 * math.pi * 300 * i / N)
    got = bands(fw, v, 22050)
    edge = list(v.bin)
    above = [b for b in range(HALO) if edge[b] >= N // 2]
    assert above and edge[HALO] > N - 300 and all(got[b] == 0 for b in above)  # the image, bin N - 300, among them
    assert got[next(b for b, bins in enumerate(spans(v)) if 300 in bins)] > 0.99


def caps(fw, v, times, dt=0.04):
    out = []
    for now in times:
        analyze(fw, SPECTRUM, 0, dt=dt, now=now)
        out.append((v.peak[0], (v.level[0] + v.level[1]) / 2))
    return out


def test_a_cap_holds_its_peak_400_ms_then_falls_faster_and_faster(fw):
    v = vz(fw)
    v.level[0] = v.level[1] = 1
    seen = caps(fw, v, range(100, 1300, 40))
    top = seen[0][0]
    held = [p for (p, _), now in zip(seen, range(100, 1300, 40)) if now <= 100 + HOLD_MS]
    assert held == [top] * len(held)  # held over the falling bar
    falling = [p for (p, _), now in zip(seen, range(100, 1300, 40)) if now > 100 + HOLD_MS]
    steps = [a - b for a, b in zip([held[-1]] + falling, falling) if b > 0]
    assert steps[0] == pytest.approx(GRAVITY * 0.04 * 0.04, rel=1e-3)  # gravity's first step
    assert all(b > a for a, b in zip(steps, steps[1:]))  # accelerating
    assert all(p >= bar - 1e-6 for p, bar in seen)  # never under its bar


def test_a_louder_bar_lifts_its_cap_at_once(fw):
    v = vz(fw)
    v.peak[3], v.held[3] = 0.2, 0
    v.level[6] = v.level[7] = 0.8
    analyze(fw, SPECTRUM, 0, dt=0.0, now=1000)
    assert v.peak[3] == pytest.approx(0.8) and v.held[3] == 1000 + HOLD_MS


def hold(fw, v, bar, now, dt=0.04):
    """A frame with no new sound that leaves the first bar at bar: its bands a fall's worth over it first."""
    v.level[0] = v.level[1] = bar + DECAY * dt
    analyze(fw, SPECTRUM, 0, dt=dt, now=now)


def test_a_cap_holds_400_ms_from_the_last_frame_its_bar_reached_it(fw):
    v = vz(fw)
    for now in range(100, 1101, 40):  # a second steady at its cap
        hold(fw, v, 0.5, now)
    top = v.peak[0]
    for now in range(1140, 1501, 40):
        hold(fw, v, 0.2, now)
        assert v.peak[0] == top
    hold(fw, v, 0.2, 1540)
    assert v.peak[0] < top - 1e-3


def test_a_cap_falls_from_the_first_millisecond_past_its_hold(fw):
    v = vz(fw)
    hold(fw, v, 1, 1000)
    top = v.peak[0]
    hold(fw, v, 0, 1000 + HOLD_MS)
    assert v.peak[0] == top
    hold(fw, v, 0, 1001 + HOLD_MS)
    assert v.peak[0] == pytest.approx(top - GRAVITY * 0.04 * 0.04, abs=1e-6)


def test_each_fall_starts_from_rest(fw):
    v = vz(fw)
    first = []
    for start in (100, 3100):
        hold(fw, v, 1, start)
        top = v.peak[0]
        hold(fw, v, 0, start + HOLD_MS + 40)
        first.append(top - v.peak[0])
        for now in range(start + HOLD_MS + 80, start + 2500, 40):
            hold(fw, v, 0, now)
        assert v.peak[0] == pytest.approx(0, abs=1e-6)
    assert first == pytest.approx([GRAVITY * 0.04 * 0.04] * 2, abs=1e-6)


def test_a_falling_cap_lands_on_its_bar_and_stays(fw):
    v = vz(fw)
    hold(fw, v, 1, 100)
    for now in range(140, 2500, 40):
        hold(fw, v, 0.5, now)
        bar = (v.level[0] + v.level[1]) / 2
        assert v.peak[0] >= bar - 1e-6
    assert v.peak[0] == pytest.approx(0.5)


def test_a_silent_frame_after_music_only_falls(fw):
    v = vz(fw)
    sine(v, 1)
    analyze(fw, SPECTRUM, 44100)
    before = list(v.level)
    analyze(fw, SPECTRUM, 0, dt=0.04)
    assert list(v.level) == pytest.approx([max(0, b - DECAY * 0.04) for b in before], abs=1e-6)


def test_the_halo_s_bars_follow_the_music_as_the_spectrum_s(fw):
    v = vz(fw)
    sine(v, 1)
    target = bands(fw, v)
    analyze(fw, HALO_STYLE, 44100)
    assert max(target) > 0.99 and list(v.level) == pytest.approx([0.6 * t for t in target], abs=1e-5)


@pytest.mark.parametrize('bar, pulses', [(0, True), (1, True), (2, True), (3, True), (17, False), (31, False)])
def test_the_halo_s_ring_pulses_with_the_bass_alone(fw, bar, pulses):
    """Each of the four lowest bars, 40 to 80 Hz, moves the ring; a bar at 1 kHz or the top one leaves it."""
    v = vz(fw)
    v.level[2 * bar] = v.level[2 * bar + 1] = 0.8
    analyze(fw, HALO_STYLE, 0, dt=0.0)
    assert v.bass > 0 if pulses else v.bass == 0


# The oscilloscope.

def test_the_scope_starts_on_a_rising_zero_crossing_so_the_wave_stands_still(fw):
    v = vz(fw)
    waves = []
    for shift in (0, 13, 30):  # the first crossing where the sine's argument is exactly 0
        for i in range(N):
            s = math.sin(2 * math.pi * (i + shift - 37) / 100)
            v.pcm[i][0], v.pcm[i][1] = s, 0.5 * s
        analyze(fw, SCOPE, 44100)
        waves.append((list(v.wave[0]), list(v.wave[1])))
        at = 37 - shift
        assert waves[-1][0] == [v.pcm[at + i][0] for i in range(W)] and waves[-1][1] == [v.pcm[at + i][1] for i in range(W)]
    assert waves[0] == waves[1] == waves[2]


def test_without_sound_the_wave_fades(fw):
    v = vz(fw)
    sine(v, 0.8, phase=-math.pi / 2)
    analyze(fw, SCOPE, 44100)
    before = list(v.wave[0])
    analyze(fw, SCOPE, 0)
    assert list(v.wave[0]) == pytest.approx([x * 0.8 for x in before], rel=1e-6)


def test_no_crossing_in_reach_draws_from_the_start(fw):
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0], v.pcm[i][1] = 0.3 + i / N / 10, 0.1  # DC, rising a little: never crosses
    analyze(fw, SCOPE, 44100)
    assert v.wave[0][0] == pytest.approx(0.3) and v.wave[1][W - 1] == pytest.approx(0.1) and drawn_from(v, 0)


def first_rising(v):
    """Where the scope starts: the first frame within reach, a whole wave from the end, whose mono mix is not
    negative after one whose is; else the first frame."""
    mono = [v.pcm[i][0] + v.pcm[i][1] for i in range(N)]
    return next((i for i in range(1, N - W) if mono[i - 1] < 0 <= mono[i]), 0)


def drawn_from(v, at):
    return [list(v.wave[ch]) for ch in range(2)] == [[v.pcm[at + i][ch] for i in range(W)] for ch in range(2)]


@pytest.mark.parametrize('seed', range(4))
def test_the_crossing_is_the_mono_mix_s_not_either_channel_s(fw, seed):
    rng = random.Random(seed)
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0], v.pcm[i][1] = rng.uniform(-1, 1), rng.uniform(-1, 1)
    at = first_rising(v)
    analyze(fw, SCOPE, 44100)
    assert drawn_from(v, at)


@pytest.mark.parametrize('cross', [N - W - 1, 900])
def test_the_wave_starts_at_a_crossing_only_with_a_whole_wave_after_it(fw, cross):
    """The last crossing in reach, and one too late: the wave from the first frame."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0], v.pcm[i][1] = (0.5 if i >= cross else -0.5) + i / N / 10, 0.1
    analyze(fw, SCOPE, 44100)
    assert drawn_from(v, cross if cross < N - W else 0)


def test_a_crossing_made_by_one_channel_s_jump_is_where_the_mix_crosses(fw):
    """The mix is just under 0 for one frame, then the right channel jumps: the crossing is the jump's frame."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0], v.pcm[i][1] = 0.5 + i / N / 10, -0.6 if i == 9 else 0.5
    analyze(fw, SCOPE, 44100)
    assert first_rising(v) == 10 and drawn_from(v, 10)


# The VU meters.

def vu_position(vu):
    """The needle's place for a level in VU: proportional to voltage, +3 VU at the scale's end."""
    return 10 ** ((vu - 3) / 20)


@pytest.mark.parametrize('vu', [-20, -7, 0, 3])
def test_a_steady_tone_settles_the_needle_at_its_level(fw, vu):
    """0 VU is VIS_VU_REF_DB dBFS RMS; a sine's peak is its RMS times sqrt 2."""
    v = vz(fw)
    sine(v, 10 ** ((vu + VU_REF_DB) / 20) * math.sqrt(2), k=16)
    for tick in range(60):
        analyze(fw, METERS, 44100, now=100 + 40 * tick)
    assert v.vu[0] == pytest.approx(vu_position(vu), rel=0.01) and v.vu[1] == pytest.approx(v.vu[0])


def test_the_needle_stops_at_the_end(fw):
    v = vz(fw)
    sine(v, 1, k=16)  # about +14 VU
    for tick in range(60):
        analyze(fw, METERS, 44100, now=100 + 40 * tick)
    assert v.vu[0] == pytest.approx(1)


def test_the_needle_s_ballistics_reach_99_percent_in_300_ms(fw):
    v = vz(fw)
    sine(v, 10 ** (VU_REF_DB / 20) * math.sqrt(2), k=16)
    for ms in range(300):
        analyze(fw, METERS, 44100, dt=0.001, now=ms)
        if ms == 64:
            assert v.vu[0] == pytest.approx(vu_position(0) * (1 - math.exp(-1)), rel=0.02)  # tau 65 ms
    assert 0.985 * vu_position(0) <= v.vu[0] <= vu_position(0)


def test_only_one_channel_moves_its_needle(fw):
    v = vz(fw)
    sine(v, 0.2, k=16, right=0)
    analyze(fw, METERS, 44100)
    assert v.vu[0] > 0 and v.vu[1] == 0


@pytest.mark.parametrize('peak_db, lit', [(LED_DB + 0.05, 1), (LED_DB - 0.05, 0)])
def test_the_led_lights_at_its_peak_for_400_ms(fw, peak_db, lit):
    v = vz(fw)
    sine(v, 10 ** (peak_db / 20), k=16, phase=0.3)
    for i in range(N):  # the peak exactly, once
        v.pcm[i][0] = max(min(v.pcm[i][0], 10 ** (peak_db / 20)), -10 ** (peak_db / 20))
    v.pcm[500][0] = 10 ** (peak_db / 20)
    analyze(fw, METERS, 44100, now=2000)
    assert v.lit[0] == (2000 + HOLD_MS if lit else 0)


def test_with_nothing_playing_the_needles_return_to_rest(fw):
    v = vz(fw)
    v.vu[0], v.vu[1] = 0.9, 0.5
    for tick in range(40):
        analyze(fw, METERS, 0, now=100 + 40 * tick)
    assert v.vu[0] < 1e-3 and v.vu[1] < 1e-3


def test_with_nothing_playing_the_needles_fall_whatever_the_last_frames_held(fw):
    v = vz(fw)
    sine(v, 0.5, k=16)
    for tick in range(10):
        analyze(fw, METERS, 44100, now=100 + 40 * tick)
    assert v.vu[0] > 0.5
    for tick in range(40):
        analyze(fw, METERS, 0, now=500 + 40 * tick)
    assert max(v.vu) < 1e-3


@pytest.mark.parametrize('frame', [0, N - 1])
def test_any_frame_can_light_the_led(fw, frame):
    v = vz(fw)
    silence(v)
    v.pcm[frame][0] = v.pcm[frame][1] = -0.95
    analyze(fw, METERS, 44100, now=2000)
    assert list(v.lit) == [2000 + HOLD_MS] * 2


def under(x):
    """The float32 just under x."""
    return struct.unpack('<f', struct.pack('<I', struct.unpack('<I', struct.pack('<f', x))[0] - 1))[0]


@pytest.mark.parametrize('exact, lit', [(1, 1), (0, 0)])
def test_the_led_lights_from_exactly_its_level(fw, exact, lit):
    level = f32(10 ** (LED_DB / 20.0))
    v = vz(fw)
    silence(v)
    v.pcm[300][0] = level if exact else under(level)
    analyze(fw, METERS, 44100, now=2000)
    assert v.lit[0] == (2000 + HOLD_MS if lit else 0)


# The halo.

def test_the_halo_s_pulse_follows_the_bass_halfway_a_tick(fw):
    v = vz(fw)
    for b in (0, 2, 4, 6):
        v.level[b] = 0.8
    analyze(fw, HALO_STYLE, 0, dt=0.0)
    assert v.bass == pytest.approx(0.4)
    analyze(fw, HALO_STYLE, 0, dt=0.0)
    assert v.bass == pytest.approx(0.6)


def test_only_the_visible_style_does_its_work(fw):
    v = vz(fw)
    sine(v, 1)
    v.re[0] = 123
    for style in (SCOPE, METERS):
        analyze(fw, style, 44100)
    assert v.re[0] == 123 and max(v.level) == 0 and v.bin_rate == 0


@pytest.mark.parametrize('style', range(4))
def test_the_other_styles_stay_at_rest(fw, style):
    v = vz(fw)
    sine(v, 0.95, k=2)  # 86 Hz: the bass bands read it too
    v.re[0] = 123
    for now in (100, 140, 180):
        analyze(fw, style, 44100, now=now)
    moved = {'transform': v.re[0] != 123, 'levels': any(v.level), 'caps': any(v.peak), 'wave': any(v.wave[0]),
             'needles': any(v.vu), 'leds': any(v.lit), 'pulse': v.bass != 0}
    assert {k for k, m in moved.items() if m} == [{'transform', 'levels', 'caps'}, {'wave'}, {'needles', 'leds'},
                                                  {'transform', 'levels', 'pulse'}][style]


def test_reset_puts_every_style_at_rest(fw):
    v = vz(fw)
    sine(v, 1)
    for style in range(4):
        analyze(fw, style, 44100)
    analyze(fw, METERS, 44100, now=100)
    v.bass = 0.5  # the halo's pulse
    fw.fn('vis_reset', None)()
    assert max(v.level) == max(v.peak) == max(v.vu) == 0 and max(v.wave[0]) == 0 and list(v.lit) == [0, 0]
    assert v.bass == 0


# The tap: VIS_N frames VIS_LATENCY_MS behind the last write.

class Tap:
    """/tmp/q2vis, mapped: the ring holds each frame's index (left) and its negative (right)."""
    def __init__(self, fw, seq=100000, rate=44100, age_ms=100, exists=True, now_ms=50000, clock=None):
        self.mem = C.create_string_buffer(TAP_SIZE)
        ring = (C.c_float * (2 * RING)).from_address(C.addressof(self.mem) + 24)
        for j in range(RING):
            ring[2 * j], ring[2 * j + 1] = j, -j
        self.set(seq, rate, now_ms * 1000000 - age_ms * 1000000)
        self.fixed, self.clock, self.exists, self.opened = now_ms * 1000000, clock, exists, []
        fw.mock('fw_open', self.open)
        fw.mock('fw_ftruncate', lambda fd, n, *_: 0 if n == TAP_SIZE else -1)
        fw.mock('fw_rename', lambda *_: 0)
        fw.mock('fw_mmap', lambda addr, n, prot, flags, fd, *_: C.addressof(self.mem) if n == TAP_SIZE else -1)
        fw.mock('fw_clock_gettime', lambda which, t, *_: struct.pack_into(
            '<qq', (C.c_char * 16).from_address(t), 0, self.now_ns // 10 ** 9, self.now_ns % 10 ** 9))
        fw.mock('mclGetPlayStatus', lambda *_: 2)

    @property
    def now_ns(self):
        """CLOCK_MONOTONIC: the page's clock when given, else fixed."""
        return self.clock() * 1000000 if self.clock else self.fixed

    def set(self, seq, rate, stamp):
        struct.pack_into('<IIq', self.mem, 0, seq & 0xffffffff, rate, stamp)

    @property
    def want(self):
        return struct.unpack_from('<I', self.mem, 16)[0]

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        self.opened.append((path, flags))
        return 5 if self.exists or path.endswith('.tmp') else -1


def capture(fw):
    return fw.fn('visualizer__capture', C.c_uint)()


def f32(x):
    return struct.unpack('f', struct.pack('f', x))[0]


def lag(rate, age_ms):
    """Frames VIS_LATENCY_MS behind the last write, less its age: none ahead of it, at most half the ring."""
    behind = int(f32(rate * f32(f32(0.48) - f32(f32(age_ms * 1e6) * f32(1e-9)))))
    return min(max(behind, 0), RING // 2)


@pytest.mark.parametrize('seq, age_ms', [(100000, 100), (100000, 0), (1000, 300), (5, 480), (100000, 600)])
def test_the_frames_are_480_ms_behind_the_last_write_moved_on_since(fw, seq, age_ms):
    tap = Tap(fw, seq=seq, age_ms=age_ms)
    assert capture(fw) == 44100 and tap.want == 1
    first = (seq - lag(44100, age_ms) - N) % RING
    v = vz(fw)
    assert v.pcm[0][0] == pytest.approx(first, abs=1)
    assert [(v.pcm[i][0] - v.pcm[0][0]) % RING for i in range(N)] == list(range(N))  # in order, round the ring
    assert all(v.pcm[i][1] == -v.pcm[i][0] for i in range(N))  # the right channel from the same frames


def test_at_48_khz_the_frames_are_480_ms_behind_too(fw):
    Tap(fw, rate=48000, age_ms=0)
    assert capture(fw) == 48000
    assert vz(fw).pcm[0][0] == pytest.approx((100000 - 23040 - N) % RING, abs=1)  # 480 ms, well inside half the ring


def test_a_write_older_than_the_latency_gives_its_last_frames(fw):
    """600 ms old: the audio heard now was written before it, so the newest frames are the nearest."""
    Tap(fw, age_ms=600)
    assert capture(fw) == 44100 and vz(fw).pcm[N - 1][0] == (100000 - 1) % RING  # the ring holds each frame's index


@pytest.mark.parametrize('late, rate', [(0, 44100), (1, 0)])
def test_a_write_a_second_old_is_still_heard(fw, late, rate):
    tap = Tap(fw)
    tap.set(100000, 44100, tap.now_ns - 10 ** 9 - late)
    assert capture(fw) == rate


def test_a_capture_writes_its_frames_and_nothing_beside_them(fw):
    tap = Tap(fw)
    v = vz(fw)
    bands(fw, v)  # the window in place
    capture(fw)  # and the tap mapped
    before = C.string_at(C.addressof(v), C.sizeof(v))
    tap.set(100500, 44100, tap.now_ns - 100 * 10 ** 6)
    capture(fw)
    after, start, end = C.string_at(C.addressof(v), C.sizeof(v)), Vz.pcm.offset, Vz.pcm.offset + Vz.pcm.size
    assert after[:start] == before[:start] and after[end:] == before[end:] and after[start:end] != before[start:end]


def test_a_ring_ahead_of_half_its_length_is_held_there(fw):
    Tap(fw, rate=96000, age_ms=0)
    assert capture(fw) == 96000
    assert vz(fw).pcm[0][0] == (100000 - RING // 2 - N) % RING


@pytest.mark.parametrize('rate, age_ms', [(0, 100), (44100, 1001), (44100, -1)])
def test_nothing_written_lately_is_silence(fw, rate, age_ms):
    tap = Tap(fw, rate=rate, age_ms=age_ms)
    assert capture(fw) == 0 and tap.want == 1


def test_not_playing_is_silence_and_lets_the_writer_stop(fw):
    tap = Tap(fw)
    fw.mock('mclGetPlayStatus', lambda *_: 3)
    assert capture(fw) == 0 and tap.want == 0


def test_the_tap_is_made_whole_under_another_name_first(fw):
    tap = Tap(fw, exists=False)
    assert capture(fw) == 44100
    assert tap.opened == [('/tmp/q2vis', 2), ('/tmp/q2vis.tmp', 0x302)]
    assert [(C.string_at(a[0]), C.string_at(a[1])) for _, a in fw.calls('fw_rename')] == [(b'/tmp/q2vis.tmp', b'/tmp/q2vis')]
    assert capture(fw) == 44100
    assert len(tap.opened) == 2  # mapped once


def test_a_tap_already_there_is_mapped_as_it_is(fw):
    """hciplayer may have it mapped: a new file under its name would leave the writer on the old one."""
    tap = Tap(fw)
    assert capture(fw) == 44100
    assert tap.opened == [('/tmp/q2vis', 2)] and fw.calls('fw_ftruncate') == fw.calls('fw_rename') == []


@pytest.mark.parametrize('exists', [True, False])
def test_descriptor_0_is_a_file_like_any(fw, exists):
    """With demo's stdin closed, open can answer 0."""
    tap = Tap(fw, exists=exists)
    fw.mock('fw_open', lambda *a: 0 if tap.open(*a) == 5 else -1)
    assert capture(fw) == 44100 and len(tap.opened) == 1 + (not exists)
    assert [a[4] & 0xffffffff for _, a in fw.calls('fw_mmap')] == [0]


def test_the_tap_is_mapped_whole_shared_and_writable(fw):
    """demo bumps want in the writer's own pages: read and write, MAP_SHARED, from the start."""
    Tap(fw)
    capture(fw)
    (_, (addr, size, prot, flags, fd, offset)), = fw.calls('fw_mmap')
    assert (addr, size, prot & 0xffffffff, flags & 0xffffffff, fd & 0xffffffff, offset) == (0, TAP_SIZE, 3, 1, 5, 0)


@pytest.mark.parametrize('fail', ['open', 'ftruncate', 'rename', 'mmap'])
def test_no_tap_is_silence(fw, fail):
    tap = Tap(fw, exists=False)
    if fail == 'open':
        fw.mock('fw_open', lambda *_: -1)
    elif fail == 'ftruncate':
        fw.mock('fw_ftruncate', lambda *_: -1)
    elif fail == 'rename':
        fw.mock('fw_rename', lambda *_: -1)
    else:
        fw.mock('fw_mmap', lambda *_: -1)
    assert capture(fw) == 0 and tap.want == 0 and vz(fw).tap is None
    assert len(fw.calls('fw_close')) == (fail != 'open')


# The page.

class Now:
    """Now Playing's window: its slide_view (inside a view at box_x, 60), the page dots, the cover; showing on
    the visualizer page with the screen on unless told otherwise; the clock; the timers. Its widgets sit in its
    views, so only a recursive lookup finds them. page, art: whether stock makes the page's list_item and the
    Halo's image."""
    def __init__(self, fw, width=375, lrc_y=0, style=None, now=100000, box_x=0, slide_x=0, page=True, art=True,
                 dots=True, win_y=0):
        self.fw, self.now, self.blocks, self.timers = fw, now, [], []
        self.win = self.widget(0, win_y, 375, 320)
        self.box = self.widget(box_x, 60, 375, 200, self.win)
        self.slide = self.widget(slide_x, 0, width, 186, self.box)
        self.cover, self.dots = self.widget(0, 0, 200, 200), self.widget(0, 0, 60, 10) if dots else 0
        self.lrc = self.widget(0, lrc_y, width, 100) if lrc_y else 0
        self.pages = 4 if page else 3  # art, lyrics, info and this
        self.value, self.animating, self.pressed, self.top, self.visible = self.pages - 1, 0, 0, self.win, {}
        fw.mock('widget_set_visible', lambda w, on, *_: self.visible.__setitem__(w, on))
        fw.mock('widget_get_visible', lambda w, *_: self.visible.get(w, 0))
        found = {'slide_view': self.slide, 'slide_indicator1': self.dots, 'img_cover': self.cover, 'view_lrc': self.lrc}
        fw.mock('widget_lookup', lambda w, name, deep, *_: found.get(C.string_at(name).decode(), 0) if deep else 0)
        fw.mock('list_item_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h, parent) if page else 0)
        fw.mock('image_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h, parent) if art else 0)
        fw.mock('widget_count_children', lambda w, *_: self.pages if w == self.slide else 0)
        fw.mock('widget_get_prop_int', self.prop_int)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('window_manager_is_animating', lambda *_: self.animating)
        fw.mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        fw.var('g_equalizer_flag', C.c_ubyte).value = 1  # the filter is in already: peq_chain leaves it
        if style is not None:
            def config(path, section, key, out, default, *_):  # the style for VIS alone, the rest their defaults
                value = style.encode() if C.string_at(key) == b'VIS' else C.string_at(default)
                C.memmove(out, value + b'\0', len(value) + 1)
            fw.mock('toolsReadConfig', config)
        fw.fn('visualizer_attach', None, C.c_void_p)(self.win)
        self.v = vz(fw)

    def widget(self, x, y, w, h, parent=0):
        self.blocks.append(C.create_string_buffer(0x200))
        struct.pack_into('<4i', self.blocks[-1], 0, x, y, w, h)
        struct.pack_into('<Q', self.blocks[-1], W_PARENT, parent)
        return C.addressof(self.blocks[-1])

    def prop_int(self, w, key, default, *_):
        return self.value if w == self.slide and C.string_at(key) == b'value' else default

    def fire(self, ms):
        """The newest timer of that period, run as the next loop pass would."""
        fn = [f for f, p in self.timers if p == ms][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(0)

    def handler(self, w, event):
        (fn, ctx), = [(fn, ctx) for _, (ww, evt, fn, ctx, *_) in self.fw.calls('widget_on') if ww == w and evt == event]
        return lambda: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0)

    def invalidated(self):
        return len([1 for _, a in self.fw.calls('widget_invalidate_force') if a[0] == self.slide])


class Canvas:
    """The window's canvas: its origin, its lcd and clip; the text drawn on it (read as drawn) and its colour.
    drawn: each text's rect, colour, font (face, px) and the canvas origin it was drawn at."""
    def __init__(self, fw, x=0, y=0):
        self.raw, self.lcd = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
        struct.pack_into('<ii', self.raw, 0, x, y)
        struct.pack_into('<Q', self.raw, 0x38, C.addressof(self.lcd))
        self.p, self.texts, self.color, self.clips = C.addressof(self.raw), [], 0, []
        self.drawn, self.font, self.measured = [], None, []  # measured: the font each measure was made in
        fw.mock('canvas_get_clip_rect', lambda c, r, *_: struct.pack_into('<4i', (C.c_char * 16).from_address(r), 0,
                                                                          0, 0, 375, 320))
        fw.mock('canvas_set_clip_rect', lambda c, r, *_: self.clips.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('canvas_get_vgcanvas', lambda *_: 0x7a00)
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'color', color))
        fw.mock('canvas_set_font', lambda c, face, px, *_: setattr(self, 'font', (face and C.string_at(face), px)))
        fw.mock('canvas_draw_text_in_rect', self.draw)

    def draw(self, c, s, n, r, *_):
        text = ''.join(map(chr, (C.c_uint * n).from_address(s)))
        self.texts.append((text, self.color >> 24 & 255))
        self.drawn.append({'text': text, 'rect': tuple((C.c_int * 4).from_address(r)), 'color': self.color,
                           'font': self.font, 'origin': self.origin})

    @property
    def origin(self):
        return struct.unpack_from('<ii', self.raw, 0)


# Stock's vgcanvas calls as tools/build.py declares them. A float travels in a float register, which a stub's six
# integer ones don't hold; the stub passes those registers on untouched, so a mock with the call's own prototype
# reads them there.
VG = {'begin_path': (), 'close_path': (), 'fill': (), 'stroke': (), 'save': (), 'restore': (),
      'move_to': (C.c_float,) * 2, 'line_to': (C.c_float,) * 2, 'arc': (*(C.c_float,) * 5, C.c_int),
      'translate': (C.c_float,) * 2, 'set_line_width': (C.c_float,), 'set_line_cap': (C.c_char_p,),
      'set_fill_color': (C.c_uint,), 'set_stroke_color': (C.c_uint,),
      'set_fill_linear_gradient': (*(C.c_float,) * 4, C.c_uint, C.c_uint)}


class Pen:
    """Every vgcanvas call from now on, in order, with its arguments; marks() answers for those from since on."""
    def __init__(self, fw):
        self.log, self.since = [], 0
        for name, args in VG.items():
            callback = C.CFUNCTYPE(C.c_int, C.c_void_p, *args)(
                lambda vg, *a, name=name: (self.log.append((name, a)), 0)[1])
            fw.keep.append(callback)
            C.c_void_p.in_dll(fw.lib, f'fw_mock_vgcanvas_{name}').value = C.cast(callback, C.c_void_p).value

    def marks(self):
        """Each fill and stroke: (fill or stroke, its path, the state it drew in). A path is a list of sub-paths,
        each of (move, line, arc or close, its arguments), and lasts until a begin_path, from one paint into the
        next as vgcanvas's does; the state is each set_ call's last arguments, kept by save and restore."""
        out, path, state, saved = [], [], {}, []
        for at, (name, a) in enumerate(self.log):
            if name == 'begin_path':
                path = []
            elif name in ('move_to', 'line_to', 'arc', 'close_path'):
                if name == 'move_to' or not path:
                    path.append([])
                path[-1].append((name.split('_')[0], a))
            elif name in ('fill', 'stroke') and at >= self.since:
                out.append((name, [list(sub) for sub in path], dict(state)))
            elif name == 'save':
                saved.append(dict(state))
            elif name == 'restore':
                state = saved.pop()
            elif name.startswith('set_'):
                state[name[4:]] = a if len(a) > 1 else a[0]
        return out


def test_the_pen_reads_each_float_as_it_was_given(fw):
    """What every drawing test below stands on: host.py's stubs pass the float registers on untouched, so each
    float-taking call's mock reads its floats. Known values, straight to the stubs and through visualizer.c's own
    line(); exact in float32, so read back exactly."""
    pen, given = Pen(fw), {'move_to': (1.5, -2.25), 'line_to': (3.75, 4.5), 'arc': (5.5, 6.25, 7.0, -0.5, 2.75, 1),
                           'translate': (15.0, 90.0), 'set_line_width': (2.5,),
                           'set_fill_linear_gradient': (0.25, 142.0, -0.75, 16.0, 0xff112233, 0xff445566)}
    for name, a in given.items():
        fw.fn(f'vgcanvas_{name}', None, C.c_void_p, *VG[name])(0x7a00, *a)
    fw.fn('visualizer__line', None, C.c_void_p, *(C.c_float,) * 4)(0x7a00, 8.5, 9.25, -10.0, 11.75)
    assert pen.log == [*given.items(), ('begin_path', ()), ('move_to', (8.5, 9.25)), ('line_to', (-10.0, 11.75)),
                       ('stroke', ())]


class Box:
    """A capsule's rectangle and its corners' radius."""
    def __init__(self, x, y, w, h, r):
        self.x, self.y, self.w, self.h, self.r = x, y, w, h, r


def capsule(sub):
    """A sub-path that is a rounded rectangle, as a Box: a move to its left side, then four quarter turns clockwise
    from the top left corner, each round a circle of the same radius at a corner of one rectangle, then closed."""
    assert [op for op, _ in sub] == ['move', 'arc', 'arc', 'arc', 'arc', 'close']
    (_, start), arcs = sub[0], [a for _, a in sub[1:5]]
    r = arcs[0][2]
    for k, (cx, cy, radius, a0, a1, ccw) in enumerate(arcs):
        assert radius == pytest.approx(r, abs=1e-4) and not ccw
        assert a0 == pytest.approx((math.pi + k * math.pi / 2) % (2 * math.pi), abs=1e-5)
        assert a1 - a0 == pytest.approx(math.pi / 2, abs=1e-5)
    (x0, y0, *_), (x1, y1, *_), (x2, y2, *_), (x3, y3, *_) = arcs
    assert (y0, x1, y2, x3) == pytest.approx((y1, x2, y3, x0), abs=1e-4) and x1 >= x0 - 1e-4 and y2 >= y1 - 1e-4
    assert start == pytest.approx((x0 - r, y0), abs=1e-4)
    return Box(x0 - r, y0 - r, x1 - x0 + 2 * r, y2 - y1 + 2 * r, r)


def circle(sub):
    """A sub-path that is one whole circle, as its centre and radius."""
    (op, (x, y, r, a0, a1, ccw)), = sub
    assert op == 'arc' and r > 0 and a1 - a0 == pytest.approx(2 * math.pi, abs=1e-5) and not ccw
    return x, y, r


def paint(fw, w, canvas):
    fw.fn('visualizer_paint', None, C.c_void_p, C.c_void_p)(w, canvas.p)


def test_the_page_joins_the_slide_view_and_its_dots(fw):
    now = Now(fw)
    v = now.v
    page = struct.unpack_from('<4i', (C.c_char * 16).from_address(v.page))
    assert page == (0, 0, 375, 186) and C.c_void_p.from_address(v.page + W_PARENT).value == now.slide
    assert (v.ox, v.oy, v.w, v.style) == (0, 60, 375, SPECTRUM)
    art = struct.unpack_from('<4i', (C.c_char * 16).from_address(v.art))
    assert art == ((375 - 88) // 2, 60 + (186 - 88) // 2, 88, 88)  # the Halo's centre, over the slide area
    assert now.visible == {v.art: 0}
    assert [(a[0], C.string_at(a[1]), a[2]) for _, a in fw.calls('widget_set_prop_int') if a[0] == now.dots] == \
        [(now.dots, b'max', 4)]
    assert [(a[0], C.string_at(a[1])) for _, a in fw.calls('widget_use_style')] == [(v.page, b's_listitem_black')]
    assert [a[:2] for _, a in fw.calls('widget_set_sensitive')] == [(v.art, 0)]  # a tap on it reaches the page


def test_the_slide_area_s_place_counts_from_the_window_wherever_it_sits(fw):
    """The canvas has the window's own place already (under a status bar, say)."""
    now = Now(fw, win_y=30)
    assert (now.v.ox, now.v.oy) == (0, 60)


def test_without_page_dots_the_page_still_joins(fw):
    assert Now(fw, dots=False).v.page and fw.calls('widget_set_prop_int') == []


@pytest.mark.parametrize('width, page', [(375, 375), (225, 225), (0, 375), (376, 375), (400, 375)])
def test_the_page_is_its_slide_s_width_up_to_the_screen_s(fw, width, page):
    """A slide_view not laid out yet, or wider than the screen, has the screen's width: the buffers'."""
    assert Now(fw, width=width).v.w == page


def test_classic_s_centred_slide_draws_over_itself(fw):
    """The 225-wide slide sits 75 px into its view: the art and the drawing go there with it."""
    now = Now(fw, width=225, slide_x=75)
    v = now.v
    assert (v.ox, v.oy) == (75, 60)
    assert struct.unpack_from('<2i', (C.c_char * 8).from_address(v.art)) == (75 + (225 - 88) // 2, 60 + (186 - 88) // 2)
    paint(fw, v.page, Canvas(fw, x=75))  # the page first, at rest: at the slide area's own x
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    assert v.page_x == 0 and canvas.clips[0] == (75, 60, 225, 186)


def test_classic_s_centred_page_starts_where_its_lyrics_do(fw):
    now = Now(fw, width=225, lrc_y=18)
    page = struct.unpack_from('<4i', (C.c_char * 16).from_address(now.v.page))
    assert page == (0, 18, 225, 186) and (now.v.w, now.v.oy) == (225, 78)


@pytest.mark.parametrize('saved, style', [('3', HALO_STYLE), ('1', SCOPE), ('7', SPECTRUM), ('x', SPECTRUM)])
def test_the_style_is_ipod_vis(fw, saved, style):
    assert Now(fw, style=saved).v.style == style


def test_no_slide_view_no_page(fw):
    fw.mock('widget_lookup', lambda *_: 0)
    fw.fn('visualizer_attach', None, C.c_void_p)(0x4000)
    assert vz(fw).page is None and fw.calls('list_item_create') == []


def test_a_tap_moves_to_the_next_style_and_saves_it(fw):
    now = Now(fw)
    tap = now.handler(now.v.page, EVT_CLICK)
    styles, saved = [], []
    fw.mock('write_int_config', lambda value, section, key, *_: saved.append((value, C.string_at(section), C.string_at(key))))
    for _ in range(5):
        now.v.level[0] = 0.5
        tap()
        styles.append(now.v.style)
    assert styles == [1, 2, 3, 0, 1] and saved == [(s, b'IPOD', b'VIS') for s in styles]
    assert now.v.named == now.now | 1 and now.v.level[0] == 0 and now.invalidated() == 5


def test_painting_the_window_on_the_page_starts_its_timer_once(fw):
    now = Now(fw)
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    paint(fw, now.win, canvas)
    assert [ms for _, ms in now.timers] == [1000 // FPS] and now.v.timer == 1


@pytest.mark.parametrize('change', ['value', 'top', 'screen'])
def test_no_timer_unless_the_page_shows(fw, change):
    now = Now(fw)
    if change == 'value':
        now.value = 2
    elif change == 'top':
        now.top = 0x9999
    else:
        fw.var('g_backlight_status', C.c_ubyte).value = 0
    paint(fw, now.win, Canvas(fw))
    assert [ms for _, ms in now.timers if ms == 1000 // FPS] == []
    if change == 'value':  # on another page nothing waits to paint this one either
        assert now.timers == []


def test_a_paint_under_another_window_draws_nothing_and_looks_again(fw):
    now = Now(fw)
    now.top = 0x9999  # the lock screen, closing over Now Playing
    paint(fw, now.win, Canvas(fw))
    assert fw.calls('vgcanvas_save') == [] and [ms for _, ms in now.timers] == [250]


def test_a_look_under_another_window_looks_again(fw):
    now = Now(fw)
    now.animating = 1
    paint(fw, now.win, Canvas(fw))
    now.animating, now.top = 0, 0x9999  # the slide ended under an overlay
    count = now.invalidated()
    assert now.fire(250) == RET_REPEAT and now.invalidated() == count


def test_no_page_leaves_the_info_page_alone(fw):
    """list_item_create failed: the slide_view keeps its three pages, the last of them the info page."""
    now = Now(fw, page=False)
    paint(fw, now.win, Canvas(fw))
    assert now.v.page is None and now.timers == [] and fw.calls('vgcanvas_save') == []


def test_a_paint_while_the_window_slides_looks_again_250_ms_on(fw):
    now = Now(fw)
    now.animating = 1
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    paint(fw, now.win, canvas)
    assert [ms for _, ms in now.timers].count(250) == 1 and fw.calls('vgcanvas_save') == []
    assert now.fire(250) == RET_REPEAT  # still sliding
    now.animating = 0
    count = now.invalidated()
    assert now.fire(250) == 0 and now.invalidated() == count + 1 and now.v.retry == 0


def test_a_look_after_the_page_went_does_nothing(fw):
    now = Now(fw)
    now.animating = 1
    paint(fw, now.win, Canvas(fw))
    now.value = 1
    count = now.invalidated()
    assert now.fire(250) == 0 and now.invalidated() == count


def test_a_page_mid_slide_paints_nothing_and_hides_the_art(fw):
    now = Now(fw, style='3')
    now.visible[now.v.art] = 1
    canvas = Canvas(fw, x=-120)
    paint(fw, now.v.page, canvas)  # the page paints first: 120 px off its rest
    assert now.v.page_x == -120 and now.visible[now.v.art] == 0
    paint(fw, now.win, Canvas(fw))
    assert fw.calls('vgcanvas_save') == [] and [ms for _, ms in now.timers].count(250) == 1


def test_the_drawing_is_clipped_to_the_slide_area_and_the_canvas_given_back(fw):
    now = Now(fw)
    canvas = Canvas(fw, x=0, y=0)
    paint(fw, now.win, canvas)
    assert canvas.clips == [(0, 60, 375, 186), (0, 0, 375, 320)] and canvas.origin == (0, 0)
    assert len(fw.calls('vgcanvas_save')) == len(fw.calls('vgcanvas_restore')) == 1


def test_a_clip_clear_of_the_slide_area_draws_nothing(fw):
    now = Now(fw)
    canvas = Canvas(fw)
    fw.mock('canvas_get_clip_rect', lambda c, r, *_: struct.pack_into('<4i', (C.c_char * 16).from_address(r), 0,
                                                                      0, 280, 375, 40))  # the bar under it only
    paint(fw, now.win, canvas)
    assert canvas.clips == [] and fw.calls('vgcanvas_save') == [] and now.v.timer == 1


def test_no_lcd_no_paint(fw):
    now = Now(fw)
    canvas = Canvas(fw)
    struct.pack_into('<Q', canvas.raw, 0x38, 0)
    paint(fw, now.win, canvas)
    assert now.timers == [] and fw.calls('vgcanvas_save') == []


def test_paint_of_another_widget_does_nothing(fw):
    now = Now(fw)
    paint(fw, now.slide, Canvas(fw))
    paint(fw, 0, Canvas(fw))
    assert now.timers == []


def test_the_drawing_moves_to_the_slide_area_and_the_canvas_comes_back(fw):
    """The window under the status bar paints at its own place; the slide area is 12, 60 into it."""
    now = Now(fw, box_x=12, now=100001)
    now.handler(now.v.page, EVT_CLICK)()  # a style's name to draw
    pen, canvas = Pen(fw), Canvas(fw, x=3, y=30)
    paint(fw, now.win, canvas)
    assert canvas.clips == [(15, 90, 360, 186), (0, 0, 375, 320)] and canvas.origin == (3, 30)
    assert [a for name, a in pen.log if name == 'translate'] == [(15, 90)]
    assert [d['origin'] for d in canvas.drawn] == [(15, 90)]


def test_without_a_vgcanvas_the_style_s_name_still_shows(fw):
    now = Now(fw, now=100001)
    now.handler(now.v.page, EVT_CLICK)()
    canvas = Canvas(fw)
    fw.mock('canvas_get_vgcanvas', lambda *_: 0)
    paint(fw, now.win, canvas)
    assert [t for t, _ in canvas.texts] == ['Oscilloscope'] and not [n for n, _ in fw.calls() if n.startswith('vgcanvas_')]


def test_no_style_name_before_a_tap_even_just_after_boot(fw):
    """time_now_ms counts from boot, and named 0 is none: at 1 s, under 1.5 s from 0, no name shows."""
    now = Now(fw, now=1000)
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    assert canvas.texts == [] and fw.calls('vgcanvas_save')


def rgb(c):
    """color_t, red in the low byte, as 0xRRGGBB."""
    return (c & 255) << 16 | c & 0xff00 | c >> 16 & 255


def channels(c):
    return [c >> s & 255 for s in (16, 8, 0)]


def test_the_spectrum_s_32_bars_run_from_a_dark_shade_to_a_bright_tint(fw):
    now = Now(fw)
    paint(fw, now.win, Canvas(fw))
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)(2)
    (_, (vg, dark, bright, *_)), = fw.calls('vgcanvas_set_fill_linear_gradient')
    assert vg == 0x7a00 and dark >> 24 == bright >> 24 == 255
    assert all(d <= t <= b for d, t, b in zip(channels(rgb(dark)), channels(tone), channels(rgb(bright))))
    assert rgb(dark) != tone != rgb(bright)
    assert len(fw.calls('vgcanvas_close_path')) == 3 * BARS  # the bars, their reflections, their caps
    assert len(fw.calls('vgcanvas_fill')) == 3  # a fill each


def test_the_scope_draws_both_channels_a_point_every_2_px(fw):
    now = Now(fw, style='1')
    paint(fw, now.win, Canvas(fw))
    assert len(fw.calls('vgcanvas_line_to')) == 3 * ((W + 1) // 2 - 1) and len(fw.calls('vgcanvas_stroke')) == 3
    assert [a[1] >> 24 for _, a in fw.calls('vgcanvas_set_stroke_color')] == [0x50, 0x40, 0xff]  # the glow under a bright line


@pytest.mark.parametrize('lit', [(1, 0), (0, 1)])
def test_a_meter_s_led_is_bright_while_lit(fw, lit):
    now = Now(fw, style='2')
    paint(fw, now.win, Canvas(fw))  # the first paint starts the timer, from rest
    for ch in range(2):
        now.v.lit[ch] = now.now + 100 if lit[ch] else now.now - 1
    before = len(fw.calls('vgcanvas_set_fill_color'))
    paint(fw, now.win, Canvas(fw))
    fills = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_fill_color')][before:]
    leds = fills[2::3]  # each meter: its pivot's two discs, then the LED
    assert len(leds) == 2
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)(2)
    bright = [ch for ch in range(2) if sum(channels(leds[ch])) > sum(channels(tone))]
    assert bright == [ch for ch in range(2) if lit[ch]]


def test_the_meters_label_their_scale_and_channels(fw):
    now = Now(fw, style='2')
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    assert [t for t, _ in canvas.texts] == ['-20', '-10', '-5', '-3', '0', '+3', 'L'] + ['-20', '-10', '-5', '-3', '0', '+3', 'R']


def test_the_halo_draws_64_bars_in_6_shades_dark_to_bright(fw):
    now = Now(fw, style='3')
    paint(fw, now.win, Canvas(fw))  # the first paint starts the timer, from rest
    for b in range(HALO):
        now.v.level[b] = b / (HALO - 1)
    lines, strokes = len(fw.calls('vgcanvas_line_to')), len(fw.calls('vgcanvas_set_stroke_color'))
    paint(fw, now.win, Canvas(fw))
    assert len(fw.calls('vgcanvas_line_to')) - lines == HALO  # each bar in one shade's path
    shades = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_stroke_color')][strokes + 2:]
    assert len(shades) == 6 and all(sum(channels(a)) < sum(channels(b)) for a, b in zip(shades, shades[1:]))


# The drawing itself, as vgcanvas is given it, in the slide area's coordinates.

MARGIN, PAGE_H = 16, 186  # tools/ipod.py NP_MARGIN and NP_SLIDE_H
GREY = 0xffaaaaaa  # docs/ipod.md: labels in the stock grey #AAAAAA, as color_t; checked once, the rest by relation
VU_SCALE = [-20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3]  # a VU meter's marks, the labelled ones longer
VU_LABELS = {-20: '-20', -10: '-10', -5: '-5', -3: '-3', 0: '0', 3: '+3'}


def lighter(a, b):
    """color_t a at least as light as b in every channel, and not b."""
    return a != b and all(x >= y for x, y in zip(channels(rgb(a)), channels(rgb(b))))


def styled(fw, style, width=375, **fields):
    """A frame of the page in style with fields set, after the first paint (which starts the timer from rest) and
    a frame before it; the pen has them all, and marks() answers for the last."""
    now = Now(fw, width=width, style=str(style))
    pen = Pen(fw)
    paint(fw, now.win, Canvas(fw))
    for name, value in fields.items():
        target = getattr(now.v, name)
        if isinstance(value, list):
            for i, x in enumerate(value):
                target[i] = x
        else:
            setattr(now.v, name, value)
    paint(fw, now.win, Canvas(fw))
    pen.since, canvas = len(pen.log), Canvas(fw)
    paint(fw, now.win, canvas)
    return now, pen, canvas


def bars_at(levels):
    """Each bar's two bands at its level."""
    return [x for level in levels for x in (level, level)]


def test_the_spectrum_s_bars_stand_on_one_baseline_across_the_page(fw):
    levels = [i / (BARS - 1) for i in range(BARS)]
    now, pen, _ = styled(fw, SPECTRUM, level=bars_at(levels), peak=levels)
    marks = pen.marks()
    assert [k for k, _, _ in marks] == ['fill'] * 3
    bars, shades, caps = ([capsule(sub) for sub in path] for _, path, _ in marks)
    assert len(bars) == len(shades) == len(caps) == BARS
    assert all(b.r == pytest.approx(min(b.w, b.h) / 2, abs=1e-4) for b in bars + shades + caps)  # round ends
    pitch, w = W / BARS, bars[0].w  # the bars share the page's width, each centred in its share
    assert 0 < w < pitch and all(b.w == pytest.approx(w, abs=1e-3) for b in bars)
    assert [b.x + b.w / 2 for b in bars] == pytest.approx([MARGIN + (i + 0.5) * pitch for i in range(BARS)], abs=1e-3)
    base = bars[0].y + bars[0].h
    assert all(b.y + b.h == pytest.approx(base, abs=1e-3) for b in bars)
    low, high = bars[0].h, bars[-1].h
    assert 0 < low and [b.h for b in bars] == pytest.approx([low + x * (high - low) for x in levels], abs=1e-3)
    x0, y0, x1, y1, dark, bright = marks[0][2]['fill_linear_gradient']  # dark at the baseline, bright at full height
    assert x0 == x1 and (y0, y1) == pytest.approx((base, bars[-1].y), abs=1e-3)
    gap, ratio = shades[0].y - base, shades[-1].h / high  # the reflections: under the baseline, shorter
    assert gap > 0 and 0 < ratio < 1
    assert all((s.x, s.w, s.y, s.h) == pytest.approx((b.x, b.w, base + gap, ratio * b.h), abs=1e-3)
               for s, b in zip(shades, bars))
    clear = bars[0].y - caps[0].y - caps[0].h  # each cap on its peak, clear of a bar as high
    assert clear > 0 and caps[0].h > 0
    assert all((c.x, c.w, c.h, b.y - c.y - c.h) == pytest.approx((b.x, b.w, caps[0].h, clear), abs=1e-3)
               for c, b in zip(caps, bars))
    assert min(c.y for c in caps) >= 0 and max(s.y + s.h for s in shades) <= PAGE_H
    tone, shade, cap = fw.fn('accent_tone', C.c_uint, C.c_int)(2), marks[1][2]['fill_color'], marks[2][2]['fill_color']
    assert rgb(shade) == tone and 0 < shade >> 24 < 0x80 and cap == bright and bright >> 24 == 255


def test_the_scope_draws_the_left_over_the_right_on_one_scale(fw):
    left, right = [math.sin(x / 9) for x in range(W)], [0.5 * math.cos(x / 7) for x in range(W)]
    now, pen, _ = styled(fw, SCOPE, wave=[(C.c_float * W)(*left), (C.c_float * W)(*right)])
    marks, xs = pen.marks(), range(0, W, 2)
    assert [k for k, _, _ in marks] == ['stroke'] * 3
    ys = []
    for _, (sub,), _ in marks:
        assert [op for op, _ in sub] == ['move'] + ['line'] * (len(xs) - 1)
        assert [a[0] for _, a in sub] == [MARGIN + x for x in xs]  # a point every 2 px across the page
        ys.append([a[1] for _, a in sub])
    under, glow, fine = ys  # the right channel, then the left's glow and line
    l, r = [now.v.wave[0][x] for x in xs], [now.v.wave[1][x] for x in xs]
    mid, top = PAGE_H / 2, max(range(len(xs)), key=lambda i: abs(l[i]))
    scale = (mid - fine[top]) / l[top]  # silence at the page's middle, positive up
    assert PAGE_H / 4 < scale and 0 < mid - scale and mid + scale < PAGE_H
    assert glow == fine and fine == pytest.approx([mid - s * scale for s in l], abs=1e-3)
    shift = under[0] - (mid - r[0] * scale)  # the right on the same scale, at most moved
    assert under == pytest.approx([mid + shift - s * scale for s in r], abs=1e-3)
    (_, _, s_under), (_, _, s_glow), (_, _, s_fine) = marks
    assert 0 < s_under['line_width'] == s_fine['line_width'] < s_glow['line_width']
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)(2)
    assert rgb(s_under['stroke_color']) == rgb(s_glow['stroke_color']) == tone
    assert lighter(s_fine['stroke_color'], s_under['stroke_color'] | 0xff000000)


def on_ray(cx, cy, a, x, y, beyond):
    """Whether whole (x, y) is where a point some d past beyond along angle a from (cx, cy) lands, cut to ints."""
    if x != int(x) or y != int(y):
        return False
    spans = [sorted(((at - c) / step, (at + 1 - c) / step)) for c, step, at in
             ((cx, math.cos(a), x), (cy, math.sin(a), y))]
    lo, hi = max(s[0] for s in spans), min(s[1] for s in spans)
    return lo < hi and hi > beyond


def polar(cx, cy, x, y):
    return math.atan2(y - cy, x - cx), math.hypot(x - cx, y - cy)


class Meter:
    """One VU meter's marks, found by what each is: its pivot and the scale's radius; its scale, the arc up to 0 VU
    then the one past it, each (arguments, state); its ticks and needle, straight strokes from the arc out and from
    the pivot, each (start, stop, state); the discs on its pivot, largest first, and its LED, the disc off a pivot
    nearest it, each ((x, y, r), state)."""
    def __init__(self, pivot, r, scale, ticks, needle, discs, led):
        self.pivot, self.r, self.scale, self.ticks, self.needle, self.discs, self.led = \
            pivot, r, scale, ticks, needle, discs, led


def meters_drawn(marks):
    """The meters in marks, left to right, in whatever order they were drawn: a stroke of one arc is a scale's,
    a fill of one circle a disc, a stroke of straight segments ticks or a needle; every mark is a meter's."""
    arcs, lines, discs = [], [], []
    for kind, path, state in marks:
        ops = [[op for op, _ in sub] for sub in path]
        if ops == [['arc']]:
            arc = path[0][0][1]
            whole = arc[4] - arc[3] == pytest.approx(2 * math.pi, abs=1e-5)
            assert whole == (kind == 'fill')
            (discs if whole else arcs).append((arc, state))
        else:
            assert kind == 'stroke' and ops and all(o == ['move', 'line'] for o in ops)
            lines += [(sub[0][1], sub[1][1], state) for sub in path]
    pivots = sorted({arc[:2] for arc, _ in arcs})
    off = [(arc[:3], s) for arc, s in discs if arc[:2] not in pivots]
    out = []
    for p in pivots:
        scale = sorted([(arc, s) for arc, s in arcs if arc[:2] == p], key=lambda m: m[0][3])
        r = scale[0][0][2]
        out.append(Meter(p, r, scale, [line for line in lines if math.isclose(math.dist(p, line[0]), r, abs_tol=1e-3)],
                         [line for line in lines if line[0] == p],
                         sorted([(arc[:3], s) for arc, s in discs if arc[:2] == p], key=lambda d: -d[0][2]),
                         [d for d in off if min(pivots, key=lambda q: math.dist(q, d[0][:2])) == p]))
    assert sum(len(m.ticks) + len(m.needle) for m in out) == len(lines)
    assert sum(len(m.discs) + len(m.led) for m in out) == len(discs) and sum(len(m.scale) for m in out) == len(arcs)
    return out


def overlap(a, b):
    """Whether rects a and b (x, y, w, h) share any pixel."""
    (ax, ay, aw, ah), (bx, by, bw, bh) = a, b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def test_each_meter_s_scale_needle_pivot_and_led(fw):
    needles = [-7, 1]  # each needle on a mark
    now, pen, canvas = styled(fw, METERS, vu=[vu_position(x) for x in needles], lit=[100000 + 100, 0])
    tone, meters = fw.fn('accent_tone', C.c_uint, C.c_int)(2), meters_drawn(pen.marks())
    assert len(meters) == 2 and sorted(d['text'] for d in canvas.drawn) == sorted([*VU_LABELS.values()] * 2 + ['L', 'R'])
    colours = {d['color'] for d in canvas.drawn}  # the scales' labels and the channels' names in one colour
    assert len(colours) == 1 and colours.pop() >> 24 == 255
    rects = []
    for ch, m in enumerate(meters):
        (cx, py), r = m.pivot, m.r
        assert r > 0 and len(m.scale) == 2
        ((_, _, _, zero, zero_vu, ccw), lo), ((_, _, _, from_vu, end, back), hi) = m.scale
        spread = end - zero
        assert from_vu == pytest.approx(zero_vu) and not ccw and not back  # clockwise, the short way round
        assert (zero + end) / 2 == pytest.approx(-math.pi / 2)  # the face upright over its pivot
        def at(vu):
            return zero + spread * vu_position(vu)
        assert zero_vu == pytest.approx(at(0))
        assert 0 < lo['line_width'] < hi['line_width'] and rgb(lo['stroke_color']) == tone  # heavier and brighter past 0
        assert lo['stroke_color'] >> 24 == hi['stroke_color'] >> 24 == 255 and lighter(hi['stroke_color'], lo['stroke_color'])
        ticks = sorted(m.ticks, key=lambda t: polar(cx, py, *t[0])[0])
        assert len(ticks) == len(VU_SCALE)
        found = [(*polar(cx, py, *start), *polar(cx, py, *stop)) for start, stop, _ in ticks]
        for (a0, d0, a1, d1), vu in zip(found, VU_SCALE):  # radial, from the arc out, at each mark's place
            assert (a0, a1, d0) == pytest.approx((at(vu), at(vu), r), abs=1e-3) and d1 > d0
        length = {vu: d1 - d0 for (_, d0, _, d1), vu in zip(found, VU_SCALE)}
        reach = r + max(length.values())
        assert min(length[x] for x in VU_LABELS) > max(length[x] for x in VU_SCALE if x not in VU_LABELS) > 0
        tick_width = ticks[0][2]['line_width']
        assert 0 < tick_width < lo['line_width'] and all(s['line_width'] == tick_width for _, _, s in ticks)
        assert [s['stroke_color'] for _, _, s in ticks] == [(hi if vu > 0 else lo)['stroke_color'] for vu in VU_SCALE]
        assert py - reach >= 0
        (_, stop, needle), = m.needle
        angle, long = polar(cx, py, *stop)
        assert angle == pytest.approx(at(needles[ch]), abs=1e-4) and r < long < reach
        assert needle['line_width'] > tick_width and needle['stroke_color'] == hi['stroke_color']
        ((_, _, outer_r), outer), ((_, _, inner_r), inner) = m.discs
        ((lx, ly, led_r), led), = m.led
        assert 0 < inner_r < outer_r and py + outer_r <= PAGE_H
        dark = inner['fill_color']
        assert outer['fill_color'] == lo['stroke_color'] and dark >> 24 == 255 and lighter(lo['stroke_color'], dark)
        assert lx > cx and ly < py and polar(cx, py, lx, ly)[1] + led_r < r and led_r > 0  # on the face, up right
        lit = led['fill_color']
        assert lit == hi['stroke_color'] if ch == 0 else lit >> 24 == 255 and lighter(dark, lit)
        labels = [d for d in canvas.drawn if d['text'] in VU_LABELS.values() and on_ray(
            cx, py, at(int(d['text'])), d['rect'][0] + d['rect'][2] / 2, d['rect'][1] + d['rect'][3] / 2, reach)]
        assert sorted(d['text'] for d in labels) == sorted(VU_LABELS.values())  # each on its mark's line, past the ticks
        assert all(d['font'][1] > 0 and 0 <= d['rect'][0] and d['rect'][0] + d['rect'][2] <= now.v.w for d in labels)
        rects.append([d['rect'] for d in labels])
        name, = [d for d in canvas.drawn if d['text'] == 'LR'[ch]]
        x, y, w, h = name['rect']
        assert name['font'][1] > 0 and w > 0 and h > 0 and 0 <= x and x + w <= now.v.w and 0 <= y and y + h <= PAGE_H
        for px in range(x, x + w + 1):  # clear of the needle's sweep
            for qy in range(y, y + h + 1):
                a, d = polar(cx, py, px, qy)
                assert not (d <= long and zero <= a <= end)
    left, right = rects  # the arc's spread keeps the two meters' end labels apart
    assert not any(overlap(a, b) for a in left for b in right)
    (x0, y0), (x1, y1) = (m.pivot for m in meters)  # each meter centred in its half of the page
    assert (x0, x1, y0) == pytest.approx((MARGIN + W / 4, MARGIN + 3 * W / 4, y1))


def test_the_meters_scale_with_the_page(fw):
    radius = {}
    for width in (375, 225):
        _, pen, _ = styled(fw, METERS, width=width)
        radius[width - 2 * MARGIN] = meters_drawn(pen.marks())[0].r
    assert radius[193] / 193 == pytest.approx(radius[W] / W)


@pytest.mark.parametrize('ms, bright', [(1, True), (0, False)])
def test_a_meter_s_led_is_bright_until_its_400_ms_end(fw, ms, bright):
    """lit is the millisecond its 400 ms end: bright until then, as the scale past 0 VU."""
    _, pen, _ = styled(fw, METERS, lit=[100000 + ms] * 2)
    meters = meters_drawn(pen.marks())
    assert [m.led[0][1]['fill_color'] == m.scale[1][1]['stroke_color'] for m in meters] == [bright] * 2


def same_angle(a, b):
    return abs(math.remainder(a - b, 2 * math.pi)) < 1e-4


def test_the_halo_rounds_the_art_and_mirrors_64_bars_round_it(fw):
    levels, spin = [j / (BARS - 1) for j in range(BARS)], 0.3
    now, pen, _ = styled(fw, HALO_STYLE, level=bars_at(levels), spin=spin)
    black, ring, *shades = marks = pen.marks()
    assert [k for k, _, _ in marks] == ['stroke'] * 8
    ax, ay, aw, _ = struct.unpack_from('<4i', (C.c_char * 16).from_address(now.v.art))
    half = aw / 2
    (cx, cy, rb), = map(circle, black[1])  # round the art: black from its circle over its corners
    assert (cx, cy) == pytest.approx((now.v.w / 2, PAGE_H / 2)) and abs(ax + half - now.v.ox - cx) <= 0.5
    assert abs(ay + half - now.v.oy - cy) <= 0.5
    wb = black[2]['line_width']
    assert rb - wb / 2 == pytest.approx(half, abs=0.25) and rb + wb / 2 >= half * math.sqrt(2)
    assert black[2]['stroke_color'] == 0xff000000
    seen = {}
    for _, path, state in shades:
        assert state['line_cap'] == b'round' and state['line_width'] > 0
        for (m, start), (l, stop) in path:
            (a0, d0), (a1, d1) = polar(cx, cy, *start), polar(cx, cy, *stop)
            i = round((a0 - spin) / (2 * math.pi / HALO)) % HALO
            assert (m, l) == ('move', 'line') and same_angle(a0, spin + 2 * math.pi * i / HALO) and same_angle(a1, a0)
            seen[i] = d0, d1 - d0, sum(channels(rgb(state['stroke_color'])))  # its shade's lightness
    assert sorted(seen) == list(range(HALO))
    v = [levels[i if i < HALO // 2 else HALO - 1 - i] for i in range(HALO)]  # mirrored
    r0, low, high = seen[0][0], seen[0][1], seen[HALO // 2][1]
    assert all(d0 == pytest.approx(r0, abs=1e-3) for d0, _, _ in seen.values())
    assert 0 < low < high and (cy - r0) / 2 < high  # louder longer, a full bar most of the way to the edge
    assert [seen[i][1] for i in range(HALO)] == pytest.approx([low + x * (high - low) for x in v], abs=1e-3)
    light = [seen[i][2] for i in range(HALO)]  # a louder bar never in a darker shade, the ramp's ends at the extremes
    assert all(light[i] <= light[j] for i in range(HALO) for j in range(HALO) if v[i] <= v[j])
    assert len(set(light)) == len(shades) == 6 and light[v.index(0)] == min(light) and light[v.index(1)] == max(light)
    assert r0 + high + shades[0][2]['line_width'] / 2 <= cy  # a full bar's round end inside the page
    (rx, ry, rr), = map(circle, ring[1])  # the ring between the art and the bars
    wr = ring[2]['line_width']
    assert (rx, ry) == (cx, cy) and wr > 0 and half < rr - wr / 2 and rr + wr / 2 < r0
    caps = [(n, a) for n, a in pen.log if n in ('set_line_cap', 'stroke')]
    assert caps[2] == ('set_line_cap', (b'round',)) and caps[-1] == ('set_line_cap', (b'butt',))
    assert all(state['stroke_color'] >> 24 == 255 for _, _, state in shades)


def test_the_bass_widens_and_lights_the_halo_s_ring(fw):
    seen = []
    for bass in (0, 0.5, 1):
        _, pen, _ = styled(fw, HALO_STYLE, bass=bass)
        black, ring, *shades = pen.marks()
        (cx, cy, _), = map(circle, black[1])
        (_, start), _ = shades[0][1][0]  # every bar at rest: in the darkest shade
        seen.append((polar(cx, cy, *start)[1], ring[2]['stroke_color'] >> 24))
    (r0, a0), (r1, a1), (r2, a2) = seen
    assert r0 < r1 < r2 and r1 - r0 == pytest.approx(r2 - r1)
    assert 0 < a0 < 255 and a2 == 255 and abs(a1 - (a0 + 255) / 2) <= 1


@pytest.mark.parametrize('style', range(4))
def test_the_style_s_name_shows_then_fades_over_its_last_half_second(fw, style):
    now = Now(fw, style=str((style - 1) % 4), now=100001)
    now.handler(now.v.page, EVT_CLICK)()
    seen = []
    after = (0, 999, 1000, 1001, 1250, 1499, 1500)
    for ms in after:
        canvas = Canvas(fw)
        now.now = 100001 + ms
        paint(fw, now.win, canvas)
        seen.append([a for t, a in canvas.texts if t == NAMES[style]])
        assert len({d['color'] & 0xffffff for d in canvas.drawn}) <= 1  # the meters' labels' colour, only fading
    assert [len(a) for a in seen] == [1] * (len(after) - 1) + [0] and seen[0] == seen[1] == [255]
    assert [a for a, in seen[2:-1]] == pytest.approx([255 * (1500 - ms) / 500 for ms in after[2:-1]], abs=1)  # linear to 0


def test_the_style_s_name_and_the_track_s_take_one_place(fw):
    """The track's title and artist come up in the style name's place: centred across the page, the same row,
    the same size, in stock's grey."""
    now = Now(fw, now=100001)
    now.handler(now.v.page, EVT_CLICK)()
    named = Canvas(fw)
    paint(fw, now.win, named)
    now.now += 5000
    track = quiet_paint(fw, now, 'Song', 'Band', full=True)
    (name,), (title,) = named.drawn, track.drawn
    assert (name['text'], title['text']) == ('Oscilloscope', 'Song · Band')
    for d in (name, title):
        x, y, w, h = d['rect']
        assert x + w / 2 == now.v.w / 2 and PAGE_H / 2 < y and y + h <= PAGE_H
    assert (name['rect'][1::2], name['font']) == (title['rect'][1::2], title['font'])
    assert name['color'] == title['color'] == GREY  # the name at full strength, just tapped


# The timer.

def ticking(fw, style='0', playing=True, **page):
    now = Now(fw, style=style, **page)
    tap = Tap(fw, clock=lambda: now.now)
    fw.mock('time_now_ms', lambda *_: now.now)
    if not playing:
        fw.mock('mclGetPlayStatus', lambda *_: 3)
    paint(fw, now.win, Canvas(fw))
    return now, tap


def tick(now, step=40, sound=None):
    """The timer step ms on; with sound, a Tap, hciplayer wrote 10 ms before it."""
    now.now += step
    if sound:
        struct.pack_into('<q', sound.mem, 8, (now.now - 10) * 1000000)
    return now.fire(1000 // FPS)


def test_each_tick_while_playing_analyses_and_repaints(fw):
    now, tap = ticking(fw)
    struct.pack_into('<q', tap.mem, 8, now.now * 1000000)  # written 40 ms before the tick
    count = now.invalidated()
    assert tick(now) == RET_REPEAT
    assert now.v.heard == now.now and tap.want == 1 and now.invalidated() == count + 1
    assert fw.fn('visualizer_live', C.c_int)() == 1


def test_the_timer_ends_once_the_page_doesn_t_show(fw):
    now, _ = ticking(fw)
    now.value = 1
    assert tick(now) == RET_REMOVE and now.v.timer == 0 and fw.fn('visualizer_live', C.c_int)() == 0


def test_a_finger_down_holds_the_repaints(fw):
    now, _ = ticking(fw)
    now.pressed = 1
    count = now.invalidated()
    tick(now)
    assert now.invalidated() == count


def test_at_rest_one_repaint_puts_the_track_s_name_up_then_none(fw):
    now, _ = ticking(fw, playing=False)
    now.v.heard = now.v.named = 0
    counts, start = [], now.invalidated()
    for _ in range(4):
        assert tick(now) == RET_REPEAT  # the timer stays for the music's return
        counts.append(now.invalidated())
    assert now.v.quiet == 1 and counts == [start + 1] * 4
    assert fw.fn('visualizer_live', C.c_int)() == 0


def test_at_rest_a_finger_down_holds_the_track_s_name_back(fw):
    """A drag starting: nothing repaints under it, the track's name included, until it ends."""
    now, _ = ticking(fw, playing=False)
    now.v.heard = now.v.named = 0
    now.pressed, count = 1, now.invalidated()
    tick(now)
    assert now.v.quiet == 0 and now.invalidated() == count
    now.pressed = 0
    tick(now)
    assert now.v.quiet == 1 and now.invalidated() == count + 1


@pytest.mark.parametrize('which', ['heard', 'named'])
def test_the_page_is_live_or_at_rest_never_both(fw, which):
    """Not playing: 1.5 s after the last audio, or after a style's name came up, the repaints stop and the track's
    name comes up, at the same millisecond."""
    now, _ = ticking(fw, playing=False)
    v = now.v
    for since in (100, 1499, 1500, 1501, 1600, 1500, 1499):
        v.heard = v.named = 0
        setattr(v, which, now.now + 40 - since)
        tick(now)
        assert (v.quiet, fw.fn('visualizer_live', C.c_int)()) == ((1, 0) if since > 1500 else (0, 1))


def test_playing_with_nothing_heard_is_not_at_rest(fw):
    """DSD, or a filter that writes nothing: playing, so the track's name stays down."""
    now, _ = ticking(fw)
    now.v.heard = now.v.named = 0
    for _ in range(3):
        tick(now)
    assert now.v.quiet == 0


def test_the_page_leaving_stops_the_repaints_at_once(fw):
    now, tap = ticking(fw)
    tick(now, sound=tap)
    assert fw.fn('visualizer_live', C.c_int)() == 1
    now.value = 1
    assert tick(now, sound=tap) == RET_REMOVE and fw.fn('visualizer_live', C.c_int)() == 0


def test_a_style_s_name_repaints_as_it_fades_with_nothing_playing(fw):
    now, _ = ticking(fw, playing=False)
    now.now |= 1
    now.handler(now.v.page, EVT_CLICK)()
    counts = []
    for _ in range(45):
        before = now.invalidated()
        tick(now)
        counts.append(now.invalidated() - before)
    shown = 1500 // 40  # ticks within 1.5 s of the tap; the next puts the track's name up
    assert counts == [1] * (shown + 1) + [0] * (45 - shown - 1)


def test_the_halo_s_art_goes_with_the_page(fw):
    now, _ = ticking(fw, style='3')
    tick(now)
    assert now.visible[now.v.art] == 1
    now.value = 1  # slid to the art page
    assert tick(now) == RET_REMOVE and now.visible[now.v.art] == 0


def test_the_first_tick_puts_the_filter_in_for_the_tap(fw):
    now, _ = ticking(fw)
    fw.var('g_equalizer_flag', C.c_ubyte).value = 0  # the EQ is off: boot left the filter out
    tick(now)
    assert fw.calls('stock_eq_trampoline')


def test_a_tick_moves_by_the_time_since_the_last_at_most_a_tenth_of_a_second(fw):
    now, _ = ticking(fw)
    v = now.v
    v.heard = now.now  # the audio just stopped: the bars fall
    for b in range(HALO):
        v.level[b] = 0.9
    falls = []
    for step in (40, 40, 300, 10):
        before = v.level[0]
        tick(now, step)
        falls.append(before - v.level[0])
    assert falls == pytest.approx([DECAY * 0.04, DECAY * 0.04, DECAY * 0.1, DECAY * 0.01], abs=1e-6)


def test_ticks_show_the_audio_from_its_first_frame_and_its_fall_after(fw):
    now, tap = ticking(fw, style='2')
    tick(now, sound=tap)  # the first with audio, after a long silence
    up = now.v.vu[0]
    assert up > 0
    fw.mock('mclGetPlayStatus', lambda *_: 3)  # paused
    tick(now)
    assert 0 < now.v.vu[0] < up


@pytest.mark.parametrize('hold', ['finger', 'slide'])
def test_a_finger_down_or_a_slide_holds_the_repaints_while_playing(fw, hold):
    now, tap = ticking(fw, style='3')
    tick(now, sound=tap)
    assert now.visible[now.v.art] == 1
    if hold == 'finger':
        now.pressed = 1
    else:
        now.animating = 1
    count = now.invalidated()
    assert tick(now, sound=tap) == RET_REPEAT and now.invalidated() == count and now.visible[now.v.art] == 0


def test_the_halo_turns_with_the_time_slowly(fw):
    now, tap = ticking(fw, style='3')
    spins = [now.v.spin]
    for step in (40, 40, 80):
        tick(now, step, sound=tap)
        spins.append(now.v.spin)
    turns = [b - a for a, b in zip(spins, spins[1:])]
    assert turns[1] == pytest.approx(turns[0]) and turns[2] == pytest.approx(2 * turns[0])  # by the clock, not the frame
    assert 0 < abs(turns[0]) / 0.04 < 2 * math.pi / 10  # under a turn in 10 s


@pytest.mark.parametrize('since, kept', [(1000, True), (1500, True), (1501, False), (5000, False)])
def test_a_page_shown_again_keeps_its_history_while_the_audio_is_live(fw, since, kept):
    """A slide back onto the page within 1.5 s of the audio carries on; later it starts at rest."""
    now = Now(fw)
    v = now.v
    v.level[0], v.heard = 0.5, now.now - since
    now.handler(now.slide, EVT_VALUE_CHANGED)()
    assert v.level[0] == (0.5 if kept else 0)


def quiet_paint(fw, now, title, artist, width_px=7, quiet=1, full=False):
    """The page at rest with the track's title and artist labels (None: no such label), each letter width_px wide:
    the text drawn, or the canvas (full), whose measured list holds the font each measure was made in."""
    labels = {k: v for k, v in (('scrlabel_title', title), ('scrlabel_artist', artist)) if v is not None}
    wide = {k: (C.c_uint * (len(v) + 1))(*map(ord, v), 0) for k, v in labels.items()}
    widgets = {k: 0x6100 + i for i, k in enumerate(labels)}
    found = {'slide_view': now.slide}
    fw.mock('widget_lookup', lambda w, name, deep, *_: deep and widgets.get(C.string_at(name).decode(), found.get(C.string_at(name).decode(), 0)))
    fw.mock('widget_get_text', lambda w, *_: C.addressof(wide[{v: k for k, v in widgets.items()}[w]]) if w in widgets.values() else 0)
    canvas = Canvas(fw)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: canvas.measured.append(canvas.font) or float(n * width_px))
    now.v.quiet, now.v.named = quiet, 0
    paint(fw, now.win, canvas)
    return canvas if full else [t for t, _ in canvas.texts]


def test_at_rest_the_track_shows_as_title_and_artist(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, 'Song', 'Band') == ['Song · Band']


def test_a_track_too_long_for_both_shows_its_title(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, 'T' * 40, 'A' * 20) == ['T' * 40]


def test_a_title_too_long_alone_ends_in_an_ellipsis(fw):
    now = Now(fw)
    shown, = quiet_paint(fw, now, 'T' * 90, '')
    assert shown == 'T' * 48 + '…' and len(shown) * 7 <= W


def test_no_track_no_text(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, '', '') == []


def test_playing_no_track_name(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, 'Song', 'Band', quiet=0) == []


@pytest.mark.parametrize('title, artist, shown', [
    ('Song', '', 'Song'), ('', 'Band', 'Band'), ('Song', None, 'Song'), (None, 'Band', 'Band'),
    ('S' * 20, 'B' * 26, 'S' * 20 + ' · ' + 'B' * 26),  # exactly the page's width: whole
    ('T' * 49, 'A', 'T' * 49),  # the title alone exactly fits
    ('', 'A' * 60, 'A' * 48 + '…'), ('T' * 300, 'Band', 'T' * 48 + '…'), ('S', 'A' * 300, 'S'), ('', 'A' * 300, 'A' * 48 + '…')])
def test_the_track_s_name_fits_the_page(fw, title, artist, shown):
    """title · artist, the title alone if both don't fit, cut to fit with an ellipsis; no separator without
    both; no such label is no text."""
    assert quiet_paint(fw, Now(fw), title, artist) == [shown]


def test_a_text_too_wide_for_even_a_letter_is_an_ellipsis(fw):
    assert quiet_paint(fw, Now(fw), 'Song', 'Band', width_px=1000) == ['…']


def test_the_track_s_name_is_measured_in_the_font_it_is_drawn_in(fw):
    canvas = quiet_paint(fw, Now(fw), 'T' * 40, 'A' * 20, full=True)
    (drawn,) = canvas.drawn
    assert len(canvas.measured) > 1 and set(canvas.measured) == {drawn['font']}


def test_the_halo_s_art_is_the_cover_s_and_shows_only_with_the_halo(fw):
    now, _ = ticking(fw, style='3')
    cover = C.create_string_buffer(b'file:///tmp/cover.jpg')
    fw.mock('widget_get_prop_str', lambda w, key, default, *_: C.addressof(cover) if w == now.cover else default)
    tick(now)
    assert [C.string_at(a[1]) for _, a in fw.calls('image_base_set_image') if a[0] == now.v.art] == [cover.value]
    assert now.visible[now.v.art] == 1
    now.handler(now.v.page, EVT_CLICK)()  # the next style: Spectrum
    tick(now)
    assert now.visible[now.v.art] == 0


def test_the_window_s_end_forgets_it(fw):
    now, _ = ticking(fw)
    now.handler(now.win, EVT_DESTROY)()
    v = now.v
    assert (v.win, v.slide, v.page, v.art, v.cover, v.timer) == (None, None, None, None, None, 0)
    paint(fw, now.win, Canvas(fw))
    assert len([1 for _, ms in now.timers if ms == 1000 // FPS]) == 1


def test_a_slide_onto_the_page_starts_the_timer(fw):
    now = Now(fw)
    now.handler(now.slide, EVT_VALUE_CHANGED)()
    assert [ms for _, ms in now.timers] == [1000 // FPS]


class Images:
    """Each image widget's image and draw_type properties, as stock keeps them: the cover's set by the test, the
    art's by image_base_set_image."""
    def __init__(self, fw, now, cover, draw_type):
        self.image, self.kept = {now.cover: cover}, []
        fw.mock('image_base_set_image', lambda w, src, *_: self.image.__setitem__(w, C.string_at(src)))
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_get_prop_int', lambda w, key, default, *_: draw_type if (w, C.string_at(key)) == (
            now.cover, b'draw_type') else now.prop_int(w, key, default))

    def prop_str(self, w, key, default, *_):
        if C.string_at(key) != b'image' or w not in self.image:
            return default
        self.kept.append(C.create_string_buffer(self.image[w]))
        return C.addressof(self.kept[-1])


def test_the_art_loads_the_cover_once_and_only_while_the_halo_shows(fw):
    now, tap = ticking(fw, style='1')
    images = Images(fw, now, b'file:///tmp/cover.jpg', 3)  # IMAGE_DRAW_SCALE: the cover's own way
    tick(now, sound=tap)
    assert fw.calls('image_base_set_image') == []  # the Oscilloscope's
    for _ in range(2):  # to the Halo
        now.handler(now.v.page, EVT_CLICK)()
    for _ in range(3):
        tick(now, sound=tap)
    assert [(a[0], C.string_at(a[1])) for _, a in fw.calls('image_base_set_image')] == [(now.v.art, b'file:///tmp/cover.jpg')]
    assert [a[:2] for _, a in fw.calls('image_set_draw_type')] == [(now.v.art, 3)]
    images.image[now.cover] = b'file:///tmp/next.jpg'  # stock reloads the cover for the next track
    tick(now, sound=tap)
    assert images.image[now.v.art] == b'file:///tmp/next.jpg' and len(fw.calls('image_base_set_image')) == 2


def test_no_art_widget_is_left_alone(fw):
    """image_create failed: the Halo draws without one."""
    now, tap = ticking(fw, style='3', art=False)
    tick(now, sound=tap)
    assert now.v.art is None and now.visible.get(0, 0) == 0 and fw.calls('image_base_set_image') == []


def test_the_page_s_own_paint_at_rest_leaves_the_halo_s_art(fw):
    now, _ = ticking(fw, style='3')
    tick(now)
    paint(fw, now.v.page, Canvas(fw))  # at x 0: at rest
    assert now.v.page_x == 0 and now.visible[now.v.art] == 1


def test_the_handlers_keep_themselves_and_pass_the_event_on(fw):
    """RET_OK: not RET_REMOVE, which drops the handler, nor RET_STOP."""
    now = Now(fw)
    assert now.handler(now.v.page, EVT_CLICK)() == now.handler(now.slide, EVT_VALUE_CHANGED)() == 0
    assert now.handler(now.win, EVT_DESTROY)() == 0


def test_the_last_window_s_end_leaves_the_next_one(fw):
    """Now Playing again before the last one's destroy event: that event is the old window's."""
    old = Now(fw)
    gone = old.handler(old.win, EVT_DESTROY)
    new = Now(fw)
    gone()
    assert (new.v.win, new.v.slide) == (new.win, new.slide) and new.v.page and new.v.art


def test_the_window_s_end_stops_a_pending_look(fw):
    now = Now(fw)
    now.animating = 1
    paint(fw, now.win, Canvas(fw))
    look = now.v.retry
    now.handler(now.win, EVT_DESTROY)()
    assert look and now.v.retry == 0 and look in [a[0] for _, a in fw.calls('timer_remove')]
