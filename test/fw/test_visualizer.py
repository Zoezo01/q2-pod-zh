"""visualizer.c (docs/ipod.md#visualizer, docs/internals.md#visualizer-ipod): the analysis's dynamics, the tap's
frames, and Now Playing's fourth page: its timer, its paint and the style names. vis_bands' sine levels are
test/peq.py's."""
import ctypes as C
import math
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
        v.pcm[i][0], v.pcm[i][1] = 0.3, 0.1  # DC: never crosses
    analyze(fw, SCOPE, 44100)
    assert v.wave[0][0] == pytest.approx(0.3) and v.wave[1][W - 1] == pytest.approx(0.1)


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


def test_reset_puts_every_style_at_rest(fw):
    v = vz(fw)
    sine(v, 1)
    for style in range(4):
        analyze(fw, style, 44100)
    analyze(fw, METERS, 44100, now=100)
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


@pytest.mark.parametrize('seq, age_ms', [(100000, 100), (100000, 0), (1000, 300), (5, 480)])
def test_the_frames_are_480_ms_behind_the_last_write_moved_on_since(fw, seq, age_ms):
    tap = Tap(fw, seq=seq, age_ms=age_ms)
    assert capture(fw) == 44100 and tap.want == 1
    lag = int(f32(44100 * f32(f32(0.48) - f32(f32(age_ms * 1e6) * f32(1e-9)))))
    first = (seq - lag - N) % RING
    v = vz(fw)
    assert v.pcm[0][0] == pytest.approx(first, abs=1) and v.pcm[0][1] == -v.pcm[0][0]
    assert [(v.pcm[i][0] - v.pcm[0][0]) % RING for i in range(N)] == list(range(N))  # in order, round the ring


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
    capture(fw)
    assert len(tap.opened) == 2  # mapped once


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
    """Now Playing's window: its slide_view (inside a view at 0, 60), the page dots, the cover; showing on
    the visualizer page with the screen on unless told otherwise; the clock; the timers."""
    def __init__(self, fw, width=375, lrc_y=0, style=None, now=100000):
        self.fw, self.now, self.blocks, self.timers = fw, now, [], []
        self.win = self.widget(0, 0, 375, 320)
        self.box = self.widget(0, 60, 375, 200, self.win)
        self.slide = self.widget(0, 0, width, 186, self.box)
        self.cover, self.dots = self.widget(0, 0, 200, 200), self.widget(0, 0, 60, 10)
        self.lrc = self.widget(0, lrc_y, width, 100) if lrc_y else 0
        self.value, self.animating, self.pressed, self.top, self.visible = 3, 0, 0, self.win, {}
        fw.mock('widget_set_visible', lambda w, on, *_: self.visible.__setitem__(w, on))
        fw.mock('widget_get_visible', lambda w, *_: self.visible.get(w, 0))
        found = {'slide_view': self.slide, 'slide_indicator1': self.dots, 'img_cover': self.cover, 'view_lrc': self.lrc}
        fw.mock('widget_lookup', lambda w, name, *_: found.get(C.string_at(name).decode(), 0))
        fw.mock('list_item_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h, parent))
        fw.mock('image_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h, parent))
        fw.mock('widget_count_children', lambda w, *_: 4 if w == self.slide else 0)
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
    """The window's canvas: its origin, its lcd and clip; the text drawn on it (read as drawn) and its colour."""
    def __init__(self, fw, x=0, y=0):
        self.raw, self.lcd = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
        struct.pack_into('<ii', self.raw, 0, x, y)
        struct.pack_into('<Q', self.raw, 0x38, C.addressof(self.lcd))
        self.p, self.texts, self.color, self.clips = C.addressof(self.raw), [], 0, []
        fw.mock('canvas_get_clip_rect', lambda c, r, *_: struct.pack_into('<4i', (C.c_char * 16).from_address(r), 0,
                                                                          0, 0, 375, 320))
        fw.mock('canvas_set_clip_rect', lambda c, r, *_: self.clips.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('canvas_get_vgcanvas', lambda *_: 0x7a00)
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'color', color))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.texts.append(
            (''.join(map(chr, (C.c_uint * n).from_address(s))), self.color >> 24 & 255)))

    @property
    def origin(self):
        return struct.unpack_from('<ii', self.raw, 0)


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


@pytest.mark.parametrize('style', range(4))
def test_the_style_s_name_shows_then_fades_over_its_last_half_second(fw, style):
    now = Now(fw, style=str((style - 1) % 4), now=100001)
    now.handler(now.v.page, EVT_CLICK)()
    seen = []
    for after in (0, 999, 1250, 1499, 1500):
        canvas = Canvas(fw)
        now.now = 100001 + after
        paint(fw, now.win, canvas)
        seen.append([a for t, a in canvas.texts if t == NAMES[style]])
    assert seen == [[255], [255], [127], [0], []]


# The timer.

def ticking(fw, style='0', playing=True):
    now = Now(fw, style=style)
    tap = Tap(fw, clock=lambda: now.now)
    fw.mock('time_now_ms', lambda *_: now.now)
    if not playing:
        fw.mock('mclGetPlayStatus', lambda *_: 3)
    paint(fw, now.win, Canvas(fw))
    return now, tap


def tick(now, step=40):
    now.now += step
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
    counts = []
    for _ in range(4):
        tick(now)
        counts.append(now.invalidated())
    assert now.v.quiet == 1 and counts[1:] == [counts[0]] * 3
    assert fw.fn('visualizer_live', C.c_int)() == 0


def quiet_paint(fw, now, title, artist, width_px=7):
    labels = {'scrlabel_title': title, 'scrlabel_artist': artist}
    wide = {k: (C.c_uint * (len(v) + 1))(*map(ord, v), 0) for k, v in labels.items()}
    widgets = {k: 0x6100 + i for i, k in enumerate(labels)}
    found = {'slide_view': now.slide}
    fw.mock('widget_lookup', lambda w, name, *_: widgets.get(C.string_at(name).decode(), found.get(C.string_at(name).decode(), 0)))
    fw.mock('widget_get_text', lambda w, *_: C.addressof(wide[{v: k for k, v in widgets.items()}[w]]) if w in widgets.values() else 0)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: float(n * width_px))
    canvas = Canvas(fw)
    now.v.quiet, now.v.named = 1, 0
    paint(fw, now.win, canvas)
    return [t for t, _ in canvas.texts]


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
