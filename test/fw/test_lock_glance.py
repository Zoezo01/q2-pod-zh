"""lock_glance.c (docs/internals.md#lock-screen): the Lock Screen's At a Glance layout on stock's lock window, in
the theme's own look: what it shows and when, Background's ground behind the song, slide to unlock, Classic Slider and Wheel Unlocks,
the knob under a finger and the unlock through stock's handler, and the clock's hold that flips the view."""
import ctypes as C

import pytest

from conftest import O, Config, Deques, block, rgba
from test_lockscreen import WALLS, AURORA, Bitmaps, Window, draws, handlers
from test_upnext import Low

THEMES = ['0', '1', '2', '3']  # THEME's stored values: Minimal, Classic, Modern, Classic Modern
STOCK = ('img_screensaver', 'view_timeinfo', 'clock_time', 'clock_date', 'label_battery', 'img_battery', 'pgbar_battery',
         'view_shade', 'view_playinfo', 'img_playcover', 'scrlabel_title', 'scrlabel_artist', 'img_slide')
HIDDEN = ('view_timeinfo', 'clock_time', 'clock_date', 'label_battery', 'img_battery', 'pgbar_battery', 'view_shade', 'img_slide')
ADDED = ('album', 'place', 'count', 'time', 'ampm', 'day', 'date', 'battery', 'words')  # the labels it adds, in the order it makes them
VIEWS = ('slider', 'veil')  # and its views
EVT_DESTROY = O['EVT_DESTROY']
RET_OK, RET_REPEAT = 0, 8


@pytest.fixture
def now(fw):
    """The local time: minute, hour, day of the month, month (0 January), weekday (0 Sunday); None: unreadable."""
    state = {'tm': (5, 7, 1, 0, 4)}  # Thursday, January 1, 7:05

    def local(_, out, *__):
        for off, v in zip((4, 8, 12, 16, 24), state['tm']):
            C.c_int.from_address(out + off).value = v
        return out
    fw.mock('fw_time', lambda *_: -1 if state['tm'] is None else 1700000000)
    fw.mock('fw_localtime_r', local)
    return state


class Glance:
    """At a Glance's lock window: stock's widgets by name (test_lockscreen's Window), the labels it adds named as
    ADDED, and the queue: `queue` songs, at `pos`, each on `album` (its record's own tag: the player has parsed no
    other file's)."""
    def __init__(self, fw, mcl, picture=b'screensaver_default', theme='0', queue=0, pos=0, album=b'Blue Train',
                 played=(65, 200), lit=1, info=1, **config):
        self.fw, self.mcl, self.low = fw, mcl, Low()
        Config(fw, LOCKLAYOUT='1', THEME=theme, **config)
        fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = picture
        fw.var('g_backlight_status', C.c_ubyte).value = lit
        self.timers = Timers(fw)
        self.deques, self.album = Deques(fw), album
        self.queue(queue, pos)
        self.played = played
        fw.mock('mclGetPlayTime', self.play_time)
        fw.mock('stock_saver_trampoline', lambda *_: 31)
        self.win = Window(fw, STOCK)
        self.shown = {}  # what widget_set_visible set; every widget is shown until then
        fw.mock('widget_set_visible', lambda w, v, *_: self.shown.__setitem__(w, v))
        fw.mock('widget_get_visible', lambda w, *_: self.shown.get(w, 1))
        if not info:  # Time show off: stock's init hid its clocks' view
            self.shown[self.win['view_timeinfo']] = 0
        self.made, self.views = 0, {}
        fw.mock('hscroll_label_create', lambda *_: self.new())
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.view(C.string_at(kind), x, y, w))
        self.texts, self.wide = {}, {}
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_get_text', self.get_wide)
        assert fw.fn('lockscreen_init', C.c_int, C.c_void_p, C.c_void_p)(0x7700, 0x7800) == 31

    def queue(self, n, pos=0):
        self.records = [block(self.fw, size=0x60) for _ in range(n)]
        for r in self.records:
            self.tag(r, self.album)
        self.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_void_p).value = self.deques.new(self.records) if n else 0
        self.mcl.pos = pos

    def tag(self, record, album):
        """record's REC_ALBUM: a 4-byte cell, as on the Q2, the empty REC_ARTIST after it (P() reads 8 here)."""
        C.c_uint.from_address(record + O['REC_ALBUM']).value = self.low.take(album + b'\0')

    def path(self, record, path):
        """record's REC_PATH, its REC_TITLE cell after it empty."""
        C.c_uint.from_address(record + O['REC_PATH']).value = self.low.take(path + b'\0')

    def play_time(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.played

    def get_text(self, w, out, n, *_):
        s = self.texts.get(w, '').encode()[:n - 1] + b'\0'
        C.memmove(out, s, len(s))
        return 0

    def get_wide(self, w, *_):
        text = self.texts.get(w, '')
        self.wide[w] = (C.c_uint * (len(text) + 1))(*map(ord, text), 0)
        return C.addressof(self.wide[w])

    def new(self):
        name = ADDED[self.made] if self.made < len(ADDED) else f'label{self.made}'
        self.made += 1
        return self.win.add(name, 10, 10)

    def view(self, kind, x, y, w):
        """widget_factory_create_widget's view, named by VIEWS in turn; its x, y and width (its height is past
        the six registers a mock sees)."""
        assert kind == b'view'
        name = VIEWS[len(self.views)]
        self.views[name] = (x, y, w)
        return self.win.add(name, w, 48)

    def __getitem__(self, name):
        return self.win[name]

    def boxes(self):
        """Each widget's last box, by name."""
        return {name: args[:4] for name, args in self.win.calls('widget_move_resize')}

    def text(self, name):
        return self.texts.get(self.win[name])


class Timers:
    """timer_add's timers, each its own id from 1: (id, fn, ms) as added; timer_remove's ids."""
    def __init__(self, fw):
        self.added, self.removed = [], []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.added.append((len(self.added) + 1, fn, ms)) or len(self.added))
        fw.mock('timer_remove', lambda i, *_: self.removed.append(i))

    def of(self, fw, name):
        """The ids added for lock_glance's static `name`, in order."""
        at = C.cast(getattr(fw.lib, f'lock_glance__{name}'), C.c_void_p).value
        return [i for i, fn, _ in self.added if fn == at]


def tick(fw):
    """lock_glance's one-second look, run once: what it answers."""
    return fw.fn('lock_glance__tick', C.c_int, C.c_void_p)(0)


def shine(fw):
    """The shine's step, run once."""
    return fw.fn('lock_glance__shine', C.c_int, C.c_void_p)(0)


def poll(fw):
    fw.fn('info_poll', None)()


class Lcd:
    """A canvas with its LCD, and the boxes fill_box fills: their rects, and their colours as they reach the screen
    (after ui_ink), and radius."""
    def __init__(self, fw):
        self.canvas = block(fw, size=0x100)
        self.lcd = block(fw, size=0x200)
        C.c_void_p.from_address(self.canvas + O['CANVAS_LCD']).value = self.lcd
        self.boxes, self.texts, self.clips, self.inks = [], [], [], []
        fw.mock('canvas_fill_rounded_rect', lambda c, r, g, color, radius, *_: self.boxes.append(
            (tuple((C.c_int * 4).from_address(r)), C.c_uint.from_address(color).value, radius)) and 0)
        fw.mock('canvas_get_clip_rect', lambda c, r, *_: C.memmove(r, (C.c_int * 4)(0, 0, 375, 320), 16) and 0)
        fw.mock('canvas_set_clip_rect', lambda c, r, *_: self.clips.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('canvas_measure_text', lambda *_: 150.0)
        fw.mock('canvas_set_text_color', lambda c, color, *_: self.inks.append(color & 0xffffffff))
        fw.mock('canvas_draw_text', lambda c, s, n, x, y, *_: self.texts.append(
            (''.join(map(chr, (C.c_uint * n).from_address(s))), x, y, self.inks[-1] if self.inks else None)))

    def paint(self, fw, w):
        fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(w, self.canvas)


# What it shows.

WANT = {'view_playinfo': (0, 76, 375, 150), 'img_playcover': (36, 8, 124, 124), 'scrlabel_title': (174, 12, 165, 26),
        'scrlabel_artist': (174, 38, 165, 22), 'album': (174, 60, 165, 22), 'place': (174, 104, 90, 22),
        'count': (249, 104, 90, 22), 'day': (169, 25, 170, 20), 'date': (169, 44, 170, 20), 'battery': (169, 6, 170, 20)}


@pytest.mark.parametrize('theme', THEMES)
def test_every_theme_lays_the_window_out_alike(fw, mcl, now, theme):
    g = Glance(fw, mcl, theme=theme, queue=3, pos=1)
    boxes = g.boxes()
    assert {k: boxes[k] for k in WANT} == WANT and boxes['time'][:2] == (36, 16)
    assert g.views['slider'] == (36, 234, 303)


@pytest.mark.parametrize('theme', THEMES)
def test_stock_s_clocks_battery_shade_and_hint_hide(fw, mcl, now, theme):
    g = Glance(fw, mcl, theme=theme)
    hidden = {name for name, args in g.win.calls('widget_set_visible') if args[0] == 0}
    assert set(HIDDEN) <= hidden and 'img_screensaver' not in hidden


def test_the_song_s_lines_and_the_clock(fw, mcl, now):
    g = Glance(fw, mcl, queue=3, pos=1, played=(65, 200))
    assert (g.text('album'), g.text('place'), g.text('count')) == ('Blue Train', '1:05 / 3:20', '2 of 3')
    assert (g.text('time'), g.text('ampm'), g.text('day'), g.text('date')) == ('7:05', 'AM', 'Thursday', 'Jan 1')
    assert g.text('words') == 'slide to unlock'


def test_the_album_is_written_again_only_when_it_changes(fw, mcl, now):
    g = Glance(fw, mcl, queue=2)
    writes = lambda: [args for _, args in fw.calls('widget_set_text_utf8') if args[0] == g['album']]
    for _ in range(3):
        assert tick(fw) == RET_REPEAT
    assert len(writes()) == 1
    g.tag(g.records[0], b'Giant Steps')
    tick(fw)
    assert len(writes()) == 2 and g.text('album') == 'Giant Steps'


def test_time_show_off_leaves_the_clock_out(fw, mcl, now):
    """Stock's init hid view_timeinfo for Time show off: no time, AM, weekday or date."""
    g = Glance(fw, mcl, queue=1, info=0)
    assert (g.text('time'), g.text('ampm'), g.text('day'), g.text('date')) == ('', '', '', '')


def test_no_queue_and_no_picture_is_the_large_clock(fw, mcl, now):
    g = Glance(fw, mcl)
    assert g.boxes()['time'][1] == 50 and g.text('day') == 'Thursday, Jan 1' and g.text('date') == ''
    assert ('view_playinfo', 1) not in [(n, a[0]) for n, a in g.win.calls('widget_set_visible')]


def test_a_clock_that_can_t_be_read_shows_dashes(fw, mcl, now):
    now['tm'] = None
    g = Glance(fw, mcl, queue=1)
    assert (g.text('time'), g.text('ampm'), g.text('day'), g.text('date')) == ('--:--', '', '', '')


@pytest.mark.parametrize('battery, shown', [('0', 0), ('1', 1)])
def test_the_battery_percent_follows_the_battery_setting(fw, mcl, now, battery, shown):
    """Stock's percent, which it writes in its hidden view_timeinfo, shows over the weekday; Battery: Icon hides it."""
    g = Glance(fw, mcl, queue=1, BATTERY=battery)
    g.texts[g['label_battery']] = '85%'
    tick(fw)
    assert g.text('battery') == '85%'
    assert [a[0] for n, a in g.win.calls('widget_set_visible') if n == 'battery'][-1] == shown


def test_a_paint_of_another_widget_is_not_its(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    lcd = Lcd(fw)
    lcd.paint(fw, g['view_playinfo'])
    lcd.paint(fw, 0)
    assert lcd.boxes == []


def test_the_picture_s_end_forgets_the_window(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    (_, forget), = [h for h in handlers(fw, EVT_DESTROY) if h[0] == g['img_screensaver']][-1:]
    assert forget() == 0
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert lcd.boxes == [] and tick(fw) == RET_OK


@pytest.mark.parametrize('layout', [None, '0'])
def test_default_adds_nothing(fw, layout):
    """Layout Default (no LOCKLAYOUT, or 0) is stock's lock window as it was: nothing of At a Glance's, no touch."""
    Config(fw, **({'LOCKLAYOUT': layout} if layout else {}))
    fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = b'screensaver_default'
    fw.mock('stock_saver_trampoline', lambda *_: 31)
    Window(fw, STOCK)
    fw.fn('lockscreen_init', C.c_int, C.c_void_p, C.c_void_p)(0x7700, 0x7800)
    assert fw.calls('hscroll_label_create') == [] and fw.calls('widget_factory_create_widget') == []
    fronted(fw)
    assert touch(fw, DOWN, KNOB_X + 10, 250) == 0 and wheel(fw) == 0


# The ground behind the song: Background.

class Cover:
    """img_playcover's image as stock loaded it: its name, and the picture widget_load_image gives, w x h RGBA8888
    of one colour. Loads and stock's cache's unloads are counted; ok False: the load fails."""
    def __init__(self, fw, w=16, h=16, rgb=(200, 100, 50)):
        self.name, self.ok, self.loads, self.unloads = C.create_string_buffer(b'file:///tmp/cover.jpg'), True, 0, 0
        self.w, self.h = w, h
        self.pixels = (C.c_ubyte * (w * h * 4))(*([*rgb, 255] * (w * h)))
        fw.mock('widget_get_prop_str', lambda w_, key, d, *_: C.addressof(self.name) if C.string_at(key) == b'image' else d)
        fw.mock('widget_load_image', self.load)
        fw.mock('image_manager_unload_bitmap_by_name', lambda m, n, *_: setattr(self, 'unloads', self.unloads + 1))
        fw.mock('bitmap_lock_buffer_for_read', lambda bm, *_: C.addressof(self.pixels))

    def load(self, w, name, bitmap, *_):
        self.loads += 1
        if not self.ok:
            return -1
        C.c_uint.from_address(bitmap).value, C.c_uint.from_address(bitmap + 4).value = self.w, self.h
        C.c_ushort.from_address(bitmap + 14).value = 1  # RGBA8888
        return 0


def cover_window(fw, mcl, cover, **kw):
    bitmaps = Bitmaps(fw)
    made = bitmaps.made
    fw.mock('bitmap_get_line_length', lambda bm, *_: made[bm][2] if bm in made else cover.w * 4)
    return bitmaps, Glance(fw, mcl, **{'queue': 1, **kw})


DARK_TONE = rgba(200 * 3 // 8 << 16 | 100 * 3 // 8 << 8 | 50 * 3 // 8)  # the cover's colour darkened to 3/8


def ground(fw, g):
    lcd = Lcd(fw)
    seen = draws(fw)
    lcd.paint(fw, g['img_screensaver'])
    return seen, lcd.boxes


@pytest.mark.parametrize('theme', THEMES)
def test_cover_colour_is_the_cover_s_average_darkened(fw, mcl, now, theme):
    cover = Cover(fw)
    bitmaps, g = cover_window(fw, mcl, cover, theme=theme)
    assert bitmaps.made == {} and (cover.loads, cover.unloads) == (1, 1)  # no blur bitmap made
    assert ground(fw, g) == ([], [((0, 0, 375, 320), DARK_TONE, 0)])


def test_plain_is_the_theme_s_own_ground(fw, mcl, now):
    cover = Cover(fw)
    _, g = cover_window(fw, mcl, cover, LOCKGROUND='1')
    assert cover.loads == 0 and ground(fw, g) == ([], [((0, 0, 375, 320), rgba(0), 0)])


def test_blurred_cover_makes_its_bitmap_only_then_and_frees_it_with_the_window(fw, mcl, now):
    cover = Cover(fw)
    bitmaps, g = cover_window(fw, mcl, cover, LOCKGROUND='2')
    blur, = bitmaps.made
    assert bitmaps.made[blur][:2] == (375, 320) and bitmaps.opaque(blur)
    assert {px for row in bitmaps.pixels(blur) for px in row} == {DARK_TONE}  # each pixel toned as the flat colour
    seen, boxes = ground(fw, g)
    assert seen == [(blur, (0, 0, 375, 320), (0, 0, 375, 320))] and boxes == []
    (_, forget), = [h for h in handlers(fw, EVT_DESTROY) if h[0] == g['img_screensaver']][-1:]
    forget()
    assert bitmaps.destroyed == [blur]


def test_light_mode_tints_the_cover_pale(fw, mcl, now):
    cover = Cover(fw)
    _, g = cover_window(fw, mcl, cover, LIGHT='1')
    paper = fw.fn('flat_page', C.c_uint)()
    want = sum(((v + 3 * (paper >> s & 255)) // 4) << s for v, s in ((200, 16), (100, 8), (50, 0)))
    assert ground(fw, g)[1] == [((0, 0, 375, 320), rgba(want), 0)]


def test_a_cover_that_can_t_be_read_is_the_plain_ground_and_not_read_again(fw, mcl, now):
    cover = Cover(fw)
    cover.ok = False
    _, g = cover_window(fw, mcl, cover)
    for _ in range(3):
        tick(fw)
    assert cover.loads == 1 and ground(fw, g)[1] == [((0, 0, 375, 320), rgba(0), 0)]


def test_the_ground_follows_the_playing_song_and_stock_s_new_cover(fw, mcl, now):
    cover = Cover(fw)
    _, g = cover_window(fw, mcl, cover, queue=2)
    g.path(g.records[0], b'/mnt/mmc/a.flac')
    g.path(g.records[1], b'/mnt/mmc/b.flac')
    url = fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024)
    url.value = b'/mnt/mmc/a.flac'
    fw.var('g_playcover_type', C.c_ubyte).value = 1  # an embedded cover
    tick(fw)
    loads = cover.loads
    tick(fw)
    assert cover.loads == loads  # the same song: kept
    mcl.pos, url.value = 1, b'/mnt/mmc/b.flac'
    tick(fw)
    assert cover.loads == loads + 1
    fw.mock('stock_savercover_trampoline', lambda *_: 12)
    assert fw.fn('lockscreen_cover', C.c_int, C.c_void_p)(0x7700) == 12 and cover.loads == loads + 2


def test_no_queue_reads_no_cover(fw, mcl, now):
    cover = Cover(fw)
    bitmaps, _ = cover_window(fw, mcl, cover, queue=0)
    assert cover.loads == 0 and bitmaps.made == {}


@pytest.mark.parametrize('light', ['0', '1'])
def test_a_picture_has_a_flat_band_behind_the_clock(fw, mcl, now, light):
    Bitmaps(fw)
    g = Glance(fw, mcl, picture=WALLS[AURORA], LIGHT=light)
    paper = fw.fn('flat_page', C.c_uint)()
    seen, boxes = ground(fw, g)
    assert len(seen) == 1 and boxes == [((0, 0, 375, 88), rgba(paper, 200 if light == '1' else 88), 0)]


# Slide to unlock: flat in every theme; Classic Slider's grey one and its shine on the words alone.

WORDS_X = 72 + (303 - 72 - 4 - 150) // 2  # right of the knob's rest, the words 150 px wide (canvas_measure_text)


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('light', ['0', '1'])
def test_the_slider_is_flat_in_the_theme_s_ink_and_on_colour(fw, mcl, now, theme, light):
    g = Glance(fw, mcl, theme=theme, queue=1, LIGHT=light)
    ink = fw.fn('flip_if', C.c_uint, C.c_int, C.c_uint)(int(light), 0xffffff)
    on = fw.fn('flat_on', C.c_uint)()
    lcd = Lcd(fw)
    lcd.paint(fw, g['slider'])
    assert lcd.boxes[:2] == [((0, 0, 303, 48), rgba(ink, 0x30), 24), ((4, 4, 66, 40), rgba(on), 20)]
    assert all(radius == 0 for _, _, radius in lcd.boxes[2:])  # the arrow: a shaft and a head
    assert lcd.texts[0] == ('slide to unlock', WORDS_X, 12, rgba(ink, 140))
    assert not fw.fn('ui_ink_raw', C.c_uint, C.c_uint)(0)  # put back after the paint


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('saved', [None, '0', '1'])
def test_classic_slider_is_off_until_set_and_alike_in_every_theme(fw, mcl, now, theme, saved):
    g = Glance(fw, mcl, theme=theme, queue=1, **({} if saved is None else {'LOCKSLIDER': saved}))
    assert len(g.timers.of(fw, 'shine')) == (saved == '1')


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('light', ['0', '1'])
def test_classic_slider_is_the_grey_slider_in_every_theme_and_half(fw, mcl, now, theme, light):
    g = Glance(fw, mcl, theme=theme, queue=1, LIGHT=light, LOCKSLIDER='1')
    lcd = Lcd(fw)
    lcd.paint(fw, g['slider'])
    assert lcd.boxes[:6] == [((0, 0, 303, 48), rgba(0xffffff, 51), 11), ((1, 1, 301, 46), rgba(0, 48), 10),
                             ((4, 4, 66, 40), rgba(0x8a8a8a), 8), ((5, 5, 64, 38), rgba(0xf4f4f4), 7),
                             ((6, 24, 62, 18), rgba(0xd6d6d6), 6), ((6, 24, 62, 6), rgba(0xd6d6d6), 0)]
    assert lcd.texts[0][3] == rgba(0xffffff, 140) and lcd.boxes[6][1] == rgba(0x6b6b6b)


def test_the_shine_crosses_the_words_alone_in_three_bands(fw, mcl, now):
    g = Glance(fw, mcl, queue=1, LOCKSLIDER='1')
    ink = 0xffffff
    lcd = Lcd(fw)
    lcd.paint(fw, g['slider'])  # the words measured
    boxes = len(lcd.boxes)
    for _ in range(20):
        assert shine(fw) == RET_REPEAT  # 140 px on
    lcd = Lcd(fw)
    lcd.paint(fw, g['slider'])
    middle = WORDS_X - 60 + 140
    assert lcd.clips[0::2] == [(middle - w // 2, 0, w, 48) for w in (96, 60, 28)]
    assert [t[3] for t in lcd.texts] == [rgba(ink, a) for a in (140, 64, 80, 112)]
    assert len(lcd.boxes) == boxes  # nothing on the knob or the track


def test_the_shine_repaints_only_near_the_words(fw, mcl, now):
    g = Glance(fw, mcl, queue=1, LOCKSLIDER='1')
    Lcd(fw).paint(fw, g['slider'])
    painted = lambda: len([a for n, a in fw.calls() if n == 'widget_invalidate_force' and a[0] == g['slider']])
    for _ in range(40):  # past the words, 280 px on
        shine(fw)
    before = painted()
    shine(fw), shine(fw)
    assert painted() == before


def test_the_shine_stops_dark_and_starts_lit(fw, mcl, now):
    g = Glance(fw, mcl, queue=1, lit=0, LOCKSLIDER='1')
    assert len(g.timers.of(fw, 'shine')) == 1 and len(g.timers.of(fw, 'tick')) == 1
    assert shine(fw) == RET_OK and tick(fw) == RET_OK  # the dark timers stop
    poll(fw)
    assert len(g.timers.of(fw, 'shine')) == 1  # still dark
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    poll(fw)
    assert len(g.timers.of(fw, 'shine')) == 2 and len(g.timers.of(fw, 'tick')) == 2


# A finger: the knob, the unlock, the clock's hold and the flip.

DOWN, MOVE, UP, ABORT = O['EVT_POINTER_FIRST'], 0x101, 0x103, O['EVT_POINTER_ABORT']
KNOB_X, KNOB_Y = 40, 238  # the knob's top left on the screen: the track's, 4 px in
TRAVEL = 303 - 66 - 8  # its run along the track


def touch(fw, kind, x, y, wm=0x4400):
    """A pointer event at the screen's (x, y), as the panel gives it (a quarter turn): lockscreen_touch's answer."""
    e = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(e) + O['EVENT_X']).value = 319 - y
    C.c_int.from_address(C.addressof(e) + O['EVENT_Y']).value = x
    return fw.fn('lockscreen_touch', C.c_int, C.c_void_p, C.c_void_p, C.c_uint)(wm, C.addressof(e), kind)


def knob(fw, g):
    lcd = Lcd(fw)
    lcd.paint(fw, g['slider'])
    return 36 + lcd.boxes[1][0][0]


def run(fw, g, name):
    i = g.timers.of(fw, name)[-1]
    fn = next(f for t, f, _ in g.timers.added if t == i)
    return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(0)


def fronted(fw, front=0x7700):
    fw.mock('window_manager_get_top_window', lambda *_: front)


def test_the_knob_follows_the_finger_and_springs_back_short_of_the_end(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    assert touch(fw, DOWN, KNOB_X + 33, 258) == 1
    assert touch(fw, MOVE, KNOB_X + 33 + 100, 250) == 1 and knob(fw, g) == KNOB_X + 100
    assert touch(fw, MOVE, KNOB_X + 33 + 400, 250) == 1 and knob(fw, g) == KNOB_X + TRAVEL
    touch(fw, MOVE, KNOB_X + 33 + 142, 250)
    assert touch(fw, UP, KNOB_X + 33 + 142, 250) == 1 and knob(fw, g) == KNOB_X  # 142 of 229: short
    assert g.timers.of(fw, 'unlock') == [] and fw.calls('saver_unlock') == []


@pytest.mark.parametrize('end', [UP, ABORT])
def test_let_go_past_five_eighths_unlocks_through_stock_s_own_handler(fw, mcl, now, end):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, KNOB_X + 10, 250)
    touch(fw, MOVE, KNOB_X + 10 + TRAVEL * 5 // 8, 250)
    touch(fw, end, KNOB_X + 10 + TRAVEL * 5 // 8, 250)
    assert knob(fw, g) == KNOB_X + TRAVEL and fw.calls('saver_unlock') == []
    (_, _, ms), = [t for t in g.timers.added if t[0] in g.timers.of(fw, 'unlock')]
    assert ms == 16 and run(fw, g, 'unlock') == RET_OK
    assert [args[:2] for _, args in fw.calls('saver_unlock')] == [(0x7700, 0)]
    assert touch(fw, DOWN, KNOB_X + TRAVEL + 10, 250) == 1  # unlocking: taken, nothing moves
    assert len(g.timers.of(fw, 'unlock')) == 1


def test_every_touch_taken_holds_the_screen_on(fw, mcl, now):
    """Stock's touch-down resets the screen-off timers; a touch taken here never reaches it."""
    Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, 300, 150), touch(fw, MOVE, 310, 150), touch(fw, UP, 310, 150)  # off the knob, on the cover
    assert [args[:3] for _, args in fw.calls('reset_poweroptions_timer')] == [(1, 1, 1)] * 3


def test_a_move_that_changes_nothing_repaints_nothing(fw, mcl, now):
    Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, 300, 150)
    n = len(fw.calls('widget_invalidate_force'))
    touch(fw, MOVE, 305, 150)
    assert len(fw.calls('widget_invalidate_force')) == n


@pytest.mark.parametrize('x, y, grabbed', [(KNOB_X - 26, 250, True), (KNOB_X - 27, 250, False), (60, KNOB_Y - 26, True),
                                           (60, KNOB_Y - 27, False)])
def test_a_finger_may_land_26_px_round_the_knob(fw, mcl, now, x, y, grabbed):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, x, y)
    touch(fw, MOVE, x + 50, y)
    assert knob(fw, g) == KNOB_X + (50 if grabbed else 0)


@pytest.mark.parametrize('front, taken', [(0x7700, 1), ('volume_dialog', 1), ('confirminfo_dialog', 0), (0, 0)])
def test_its_window_or_the_volume_over_it_takes_the_touch(fw, mcl, now, front, taken):
    Glance(fw, mcl, queue=1)
    if isinstance(front, str):
        name = C.create_string_buffer(front.encode())
        fw.mock('widget_get_prop_str', lambda w, key, d, *_: C.addressof(name) if w == 0x9900 and C.string_at(key) == b'name' else d)
        front = 0x9900
    fronted(fw, front)
    assert touch(fw, DOWN, KNOB_X + 10, 250) == taken


class Ms:
    def __init__(self, fw, now=5000):
        self.now = now
        fw.mock('time_now_ms', lambda *_: self.now)


def flip(fw, g, ms, at=200):
    """The clock held: its hold's timer fired and the veil past its turn."""
    touch(fw, DOWN, 100, 40)
    run(fw, g, 'turn')
    ms.now += at
    return run(fw, g, 'fade_step')


@pytest.mark.parametrize('light', ['0', '1'])
def test_the_clock_held_a_second_flips_through_the_plain_ground(fw, mcl, now, light):
    """No picture: the flipped view is the large clock on the plain ground, the veil that colour."""
    ms = Ms(fw)
    g = Glance(fw, mcl, queue=1, LIGHT=light)
    fronted(fw)
    touch(fw, DOWN, 100, 40)
    (_, _, hold), = [t for t in g.timers.added if t[0] in g.timers.of(fw, 'turn')]
    assert hold == 1000 and run(fw, g, 'turn') == RET_OK
    veil = []
    fw.mock('canvas_set_fill_color', lambda c, color, *_: veil.append(color & 0xffffffff))
    ms.now += 80
    assert run(fw, g, 'fade_step') == RET_REPEAT
    Lcd(fw).paint(fw, g['veil'])
    assert veil[0] == rgba(fw.fn('flat_page', C.c_uint)(), 127)
    ms.now += 120
    run(fw, g, 'fade_step')
    assert g.boxes()['time'][1] == 50  # turned: the large clock
    assert ground(fw, g)[1] == [((0, 0, 375, 320), rgba(fw.fn('flat_page', C.c_uint)()), 0)]


def test_a_late_fade_step_still_turns(fw, mcl, now):
    ms = Ms(fw)
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    assert flip(fw, g, ms, 900) == RET_OK and g.boxes()['time'][1] == 50


def test_with_a_picture_the_flip_shows_it_and_the_slider_alone(fw, mcl, now):
    ms = Ms(fw)
    Bitmaps(fw)
    g = Glance(fw, mcl, picture=WALLS[AURORA], queue=1)
    fronted(fw)
    flip(fw, g, ms)
    seen, boxes = ground(fw, g)
    assert len(seen) == 1 and len(boxes) == 1 and g.boxes()['time'][:2] == (36, 16)
    assert [a[0] for n, a in g.win.calls('widget_set_visible') if n == 'view_playinfo'][-1] == 0


@pytest.mark.parametrize('queue, x, y', [(0, 100, 40), (1, 300, 40), (1, 100, 120), (1, KNOB_X + 10, 250)])
def test_the_hold_is_the_clock_s_with_a_queue_alone(fw, mcl, now, queue, x, y):
    g = Glance(fw, mcl, queue=queue)
    fronted(fw)
    touch(fw, DOWN, x, y)
    assert g.timers.of(fw, 'turn') == []


@pytest.mark.parametrize('dx, dy, flips', [(14, 0, True), (15, 0, False), (0, -15, False), (-14, 14, True)])
def test_a_finger_moving_off_the_clock_lets_the_hold_go(fw, mcl, now, dx, dy, flips):
    Ms(fw)
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, 100, 40)
    touch(fw, MOVE, 100 + dx, 40 + dy)
    run(fw, g, 'turn')
    assert bool(g.timers.of(fw, 'fade_step')) == flips


def test_the_held_clock_s_view_is_the_next_lock_s(fw, mcl, now):
    """Flipped, unlocked and locked again: the next window opens on the large clock (RAM alone: a restart forgets)."""
    ms = Ms(fw)
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    flip(fw, g, ms)
    assert g.boxes()['time'][1] == 50
    g2 = Glance(fw, mcl, queue=1)
    tick(fw)
    assert g2.boxes()['time'][1] == 50


def test_the_queue_s_end_turns_back(fw, mcl, now):
    ms = Ms(fw)
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    flip(fw, g, ms)
    g.queue(0)
    tick(fw)
    g.queue(1)
    tick(fw)
    assert [a[0] for n, a in g.win.calls('widget_set_visible') if n == 'view_playinfo'][-1] == 1


KEY_PREV, KEY_NEXT, KEY_DOWN, KEY_UP = 172, 173, 0x110, 0x114
STEP = TRAVEL * 625 // 1000 // 3 + 1  # Wheel Unlocks: a tick's step, the third forward one past five eighths


def wheel(fw, key=KEY_NEXT, kind=KEY_DOWN, wm=0x4400):
    """A wheel tick's event with Wheel Unlocks on: glance_wheel's answer."""
    return fw.fn('glance_wheel', C.c_int, C.c_void_p, C.c_uint, C.c_uint)(wm, kind, key)


def test_three_ticks_forward_unlock_through_stock_s_own_handler(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    for i in (1, 2):
        assert wheel(fw) == 1 and wheel(fw, kind=KEY_UP) == 1 and knob(fw, g) == KNOB_X + i * STEP
    assert g.timers.of(fw, 'unlock') == []
    assert wheel(fw) == 1 and knob(fw, g) == KNOB_X + TRAVEL
    assert run(fw, g, 'unlock') == RET_OK and [args[:2] for _, args in fw.calls('saver_unlock')] == [(0x7700, 0)]
    assert wheel(fw) == 1 and len(g.timers.of(fw, 'unlock')) == 1  # unlocking: taken, nothing moves


def test_a_tick_back_moves_the_knob_back_and_never_past_its_rest(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    wheel(fw), wheel(fw, KEY_PREV)
    assert knob(fw, g) == KNOB_X
    wheel(fw, KEY_PREV)
    assert knob(fw, g) == KNOB_X


def test_ticks_short_of_the_end_go_back_a_second_after_the_last(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    ms = Ms(fw)
    wheel(fw), wheel(fw)
    ms.now += 999
    tick(fw)
    assert knob(fw, g) == KNOB_X + 2 * STEP
    ms.now += 1
    tick(fw)
    assert knob(fw, g) == KNOB_X and fw.calls('saver_unlock') == []


def test_every_tick_taken_holds_the_screen_on(fw, mcl, now):
    Glance(fw, mcl, queue=1)
    fronted(fw)
    wheel(fw), wheel(fw, kind=KEY_UP)
    assert [args[:3] for _, args in fw.calls('reset_poweroptions_timer')] == [(1, 1, 1)] * 2


@pytest.mark.parametrize('lit, front', [(0, 0x7700), (1, 0x9900)])
def test_dark_or_under_a_prompt_the_wheel_is_the_volume(fw, mcl, now, lit, front):
    g = Glance(fw, mcl, queue=1, lit=lit)
    fronted(fw, front)
    assert wheel(fw) == 0 and knob(fw, g) == KNOB_X


@pytest.mark.parametrize('saved, taken', [(None, 0), ('0', 0), ('1', 1)])
def test_wheel_unlocks_is_off_until_set(fw, mcl, now, saved, taken):
    """Off (the default): lockscreen_input leaves the tick to stock, the volume as ever."""
    Glance(fw, mcl, queue=1, **({} if saved is None else {'LOCKWHEEL': saved}))
    fronted(fw)
    fw.var('g_lockscreen_pageflag', C.c_ubyte).value = 1
    e = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(e) + O['EVENT_TYPE']).value = KEY_DOWN
    C.c_int.from_address(C.addressof(e) + O['EVENT_KEY']).value = KEY_NEXT
    assert fw.fn('lockscreen_input', C.c_int, C.c_void_p, C.c_void_p)(0x4400, C.addressof(e)) == taken
