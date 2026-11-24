"""lock_glance.c (docs/internals.md#lock-screen): the Lock Screen's At a Glance layout on stock's lock window, the
same in every theme: its widgets' boxes and what it paints, the blurred cover, slide to unlock and its shine, the
knob under a finger and the unlock through stock's handler, and the clock's hold that flips the view."""
import ctypes as C

import pytest

from conftest import O, Config, Deques, block
from test_lockscreen import WALLS, AURORA, BLACK, Bitmaps, Window, draws, handlers
from test_upnext import Low

THEMES = ['0', '1', '2', '3']  # THEME's stored values: Minimal, Classic, Modern, Classic Modern
STOCK = ('img_screensaver', 'view_timeinfo', 'clock_time', 'clock_date', 'label_battery', 'img_battery', 'pgbar_battery',
         'view_shade', 'view_playinfo', 'img_playcover', 'scrlabel_title', 'scrlabel_artist', 'img_slide')
HIDDEN = ('view_timeinfo', 'clock_time', 'clock_date', 'label_battery', 'img_battery', 'pgbar_battery', 'view_shade', 'img_slide')
ADDED = ('album', 'place', 'count', 'time', 'ampm', 'day', 'date')  # the labels it adds, in the order it makes them
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
                 played=(65, 200), lit=1, **config):
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
        self.made, self.views = 0, {}
        fw.mock('hscroll_label_create', lambda *_: self.new())
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.view(C.string_at(kind), x, y, w))
        self.texts = {}
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
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


# The window.

WANT = {'view_playinfo': (0, 76, 375, 150), 'img_playcover': (36, 8, 124, 124), 'scrlabel_title': (174, 12, 165, 26),
        'scrlabel_artist': (174, 38, 165, 22), 'album': (174, 60, 165, 22), 'place': (174, 104, 90, 22),
        'count': (249, 104, 90, 22), 'day': (169, 25, 170, 20), 'date': (169, 44, 170, 20)}


@pytest.mark.parametrize('theme', THEMES)
def test_every_theme_lays_the_window_out_the_same(fw, mcl, now, theme):
    g = Glance(fw, mcl, theme=theme, queue=3, pos=1)
    boxes = g.boxes()
    assert {k: boxes[k] for k in WANT} == WANT
    x, y, w, h = boxes['time']
    assert (x, y, h) == (36, 16, 52) and boxes['ampm'][:2] == (x + w - 8 + 6, 40)  # "7:05", then AM, top left
    props = g.win.props('widget_set_prop_int', 'view_playinfo')
    assert props['style:normal:bg_color'] == 0 and props['style:normal:round_radius'] == 0
    assert all(g.win.props('widget_set_prop_int', 'img_playcover')[f'style:{s}:round_radius'] == 0
               for s in ('normal', 'pressed', 'selected'))  # square


@pytest.mark.parametrize('theme', THEMES)
def test_stock_s_clocks_battery_shade_and_hint_hide(fw, mcl, now, theme):
    g = Glance(fw, mcl, theme=theme)
    hidden = {name for name, args in g.win.calls('widget_set_visible') if args[0] == 0}
    assert set(HIDDEN) <= hidden and 'img_screensaver' not in hidden


def test_the_title_takes_classic_s_face_and_the_rest_stock_s(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    face = lambda n: g.win.props('widget_set_prop_str', n)['style:normal:font_name']
    assert face('scrlabel_title') == 'classic-light' and {face(n) for n in ('scrlabel_artist', *ADDED)} == {'default'}
    size = lambda n: g.win.props('widget_set_prop_int', n)['style:normal:font_size']
    assert [size(n) for n in ('scrlabel_title', 'scrlabel_artist', 'album', 'place', 'count', 'time', 'day')] == [
        20, 16, 15, 14, 14, 44, 15]


def test_the_song_s_lines_and_the_clock(fw, mcl, now):
    g = Glance(fw, mcl, queue=3, pos=1, played=(65, 200))
    assert (g.text('album'), g.text('place'), g.text('count')) == ('Blue Train', '1:05 / 3:20', '2 of 3')
    assert (g.text('time'), g.text('ampm'), g.text('day'), g.text('date')) == ('7:05', 'AM', 'Thursday', 'January 1')


def test_the_album_is_written_again_only_when_it_changes(fw, mcl, now):
    """Each second while a song plays the album is looked at, and written only when it changed: its marquee
    keeps its rests at either end."""
    g = Glance(fw, mcl, queue=2)
    writes = lambda: [args for _, args in fw.calls('widget_set_text_utf8') if args[0] == g['album']]
    before = len(writes())
    for _ in range(3):
        assert tick(fw) == RET_REPEAT
    assert len(writes()) == before == 1
    g.tag(g.records[0], b'Giant Steps')
    tick(fw)
    assert len(writes()) == 2 and g.text('album') == 'Giant Steps'


def test_24_hour_time_has_no_am_or_pm(fw, mcl, now):
    fw.var('g_time24h_flag', C.c_ubyte).value = 1
    now['tm'] = (7, 21, 9, 10, 1)
    g = Glance(fw, mcl, queue=1)
    assert (g.text('time'), g.text('ampm'), g.text('day'), g.text('date')) == ('21:07', '', 'Monday', 'November 9')


@pytest.mark.parametrize('hour, shown, half', [(0, '12', 'AM'), (12, '12', 'PM'), (13, '1', 'PM'), (23, '11', 'PM')])
def test_12_hour_time(fw, mcl, now, hour, shown, half):
    now['tm'] = (0, hour, 1, 0, 4)
    g = Glance(fw, mcl, queue=1)
    assert (g.text('time'), g.text('ampm')) == (shown + ':00', half)


def test_a_clock_that_can_t_be_read_shows_dashes(fw, mcl, now):
    now['tm'] = None
    g = Glance(fw, mcl, queue=1)
    assert (g.text('time'), g.text('ampm'), g.text('day'), g.text('date')) == ('--:--', '', '', '')


def test_without_a_queue_or_a_picture_the_time_is_large_in_the_middle(fw, mcl, now):
    g = Glance(fw, mcl)
    boxes = g.boxes()
    x, y, w, h = boxes['time']
    ax, ay, aw, _ = boxes['ampm']
    assert (y, h) == (50, 112) and ay == 122 and ax == x + 4 + w - 8 + 6  # the time's box 4 px wider each side
    assert abs((x + 4) - (375 - (ax + aw - 4))) <= 1  # the time and AM centred
    assert boxes['day'] == (0, 168, 375, 26) and boxes['date'] == (0, 0, 1, 1)
    assert g.text('day') == 'Thursday, January 1' and g.text('date') == ''
    assert ('view_playinfo', 1) not in [(n, a[0]) for n, a in g.win.calls('widget_set_visible')]  # no song


def test_a_queue_coming_shows_the_song(fw, mcl, now):
    g = Glance(fw, mcl)
    g.queue(2, 1)
    tick(fw)
    assert [a[0] for n, a in g.win.calls('widget_set_visible') if n == 'view_playinfo'][-1] == 1
    assert g.boxes()['time'][:2] == (36, 16) and g.text('count') == '2 of 2'


# What it paints.

def test_with_a_song_the_picture_paints_black_under_it(fw, mcl, now):
    Bitmaps(fw)
    seen = draws(fw)
    g = Glance(fw, mcl, picture=WALLS[AURORA], queue=1)
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert seen == [] and lcd.boxes == [((0, 0, 375, 320), 0xff000000, 0)]


def test_without_a_song_a_built_in_paints_with_a_shade_under_the_time(fw, mcl, now):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    g = Glance(fw, mcl, picture=WALLS[AURORA])
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    bm, = bitmaps.made
    assert seen == [(bm, (0, 0, 375, 320), (0, 0, 375, 320))]
    assert [b[0] for b in lcd.boxes] == [(0, 2 * i, 375, 2) for i in range(44)]
    assert [b[1] >> 24 for b in lcd.boxes] == [88 - 2 * i for i in range(44)]  # fading out
    assert all(b[1] & 0xffffff == 0 for b in lcd.boxes)


def test_without_a_song_a_card_picture_is_stock_s_with_the_shade(fw, mcl, now):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    g = Glance(fw, mcl, picture=b'/mnt/mmc/lockscreenimage/a.jpg')
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert bitmaps.made == {} and seen == [] and len(lcd.boxes) == 44
    assert not g.win.calls('image_base_set_image')  # stock's picture stays


@pytest.mark.parametrize('picture', [b'screensaver_default', b'', WALLS[BLACK]])
def test_no_picture_or_black_is_the_clock_on_black(fw, mcl, now, picture):
    Bitmaps(fw)
    seen = draws(fw)
    g = Glance(fw, mcl, picture=picture)
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert seen == [] and lcd.boxes == [((0, 0, 375, 320), 0xff000000, 0)]
    assert g.boxes()['time'][1] == 50


SHADE = [((0, 2 * i, 375, 2), (88 - 2 * i) << 24, 0) for i in range(44)]
LABELS = ('scrlabel_title', 'scrlabel_artist', *ADDED)
INKS = {'album': 0xd1ffffff, 'place': 0xd1ffffff, 'count': 0xd1ffffff}  # the rest white


def light_glance(fw, mcl, theme, light, **kw):
    """Glance with Light Mode as asked, checked on: one settings read per test, so one half per test."""
    Bitmaps(fw)
    g = Glance(fw, mcl, theme=theme, LIGHT=light, **kw)
    assert fw.fn('ipod_light', C.c_int)() == int(light)
    return g


def screen_ink(fw, st, g, name):
    """name's text colour as the style hook gives it to the screen (ringnav_style_color, with stock's style
    answering what the label stores), while the label paints."""
    stored = g.win.props('widget_set_prop_int', name)['style:normal:text_color'] & 0xffffffff
    fw.mock('stock_color_trampoline', lambda color, *_: C.c_uint.from_address(color).__setattr__('value', stored))
    fw.mock('widget_get_prop_int', lambda w, key, d, *_: next(
        (v for kind, ww, k, v in reversed(g.win.set) if ww == w and k == C.string_at(key).decode()), d))
    fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))
    st('paint_w', C.c_void_p).value = g[name]
    color = C.c_uint(0)
    fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)(
        C.addressof(color), 0, b'text_color', 0)
    return color.value


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('light', ['0', '1'])
@pytest.mark.parametrize('queue, want', [(0, SHADE), (1, [((0, 0, 375, 320), 0xff000000, 0)])])
def test_light_mode_leaves_the_picture_s_look(fw, mcl, now, theme, light, queue, want):
    """Light Mode flips what the payload paints and the labels' colours: At a Glance's are final, so they reach the
    screen as in the dark half, in every theme. (A flip given in advance can't: Minimal's and Modern's light half
    turns no colour into white.)"""
    g = light_glance(fw, mcl, theme, light, picture=WALLS[AURORA], queue=queue)
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert lcd.boxes == want


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('light', ['0', '1'])
def test_light_mode_leaves_the_labels_white(fw, mcl, now, st, theme, light):
    g = light_glance(fw, mcl, theme, light, queue=1)
    assert {n: screen_ink(fw, st, g, n) for n in LABELS} == {n: INKS.get(n, 0xffffffff) for n in LABELS}


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


# The blur.

class Cover:
    """img_playcover's image as stock loaded it: its name (any widget's "image" reads it), and the picture
    widget_load_image gives, w x h RGBA8888 (format 1) from rgb(x, y). widget_load_image's calls and stock's
    image cache's unloads are counted; ok False: the load fails."""
    def __init__(self, fw, name=b'file:///tmp/cover.jpg', w=16, h=16, rgb=lambda x, y: (200, 100, 50)):
        self.name, self.ok, self.loads, self.unloads = C.create_string_buffer(name), True, 0, 0
        self.w, self.h = w, h
        self.pixels = (C.c_ubyte * (w * h * 4))(*[v for y in range(h) for x in range(w) for v in (*rgb(x, y), 255)])
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


def blur_window(fw, mcl, cover, **kw):
    """At a Glance with a song and cover as its picture: the Bitmaps, and the window."""
    bitmaps = Bitmaps(fw)
    made = bitmaps.made
    fw.mock('bitmap_get_line_length', lambda bm, *_: made[bm][2] if bm in made else cover.w * 4)
    return bitmaps, Glance(fw, mcl, **{'queue': 1, **kw})


def test_the_cover_is_blurred_dimmed_behind_the_song(fw, mcl, now):
    cover = Cover(fw)
    bitmaps, g = blur_window(fw, mcl, cover)
    blur, = bitmaps.made
    assert bitmaps.made[blur][:2] == (375, 320) and bitmaps.opaque(blur) and (cover.loads, cover.unloads) == (1, 1)
    r, gr, b = (v * 178 >> 8 for v in (200, 100, 50))  # one colour stays itself, at 178 of 256
    assert {px for row in bitmaps.pixels(blur) for px in row} == {0xff000000 | b << 16 | gr << 8 | r}
    seen = draws(fw)
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert seen == [(blur, (0, 0, 375, 320), (0, 0, 375, 320))] and lcd.boxes == []


def test_the_blur_is_the_cover_s_middle_soft(fw, mcl, now):
    """The cover is a square 495 px wide behind the 375 x 320 screen: its left and right edges are off the screen,
    and the blur runs smoothly from the left's colour to the right's."""
    cover = Cover(fw, w=99, h=99, rgb=lambda x, y: (255, 0, 0) if x < 12 else (0, 0, 255) if x >= 87 else (0, 255, 0))
    bitmaps, _ = blur_window(fw, mcl, cover)
    blur, = bitmaps.made
    row = bitmaps.pixels(blur)[160]
    assert all(px >> 16 & 255 < 40 and px & 255 < 40 for px in row[40:335])  # the red and blue columns are off the screen
    steps = [abs((a >> 8 & 255) - (b >> 8 & 255)) for a, b in zip(row, row[1:])]
    assert max(steps) <= 8  # nothing sharp


def test_the_blur_follows_the_playing_song_not_the_file_name(fw, mcl, now):
    """Every song's cover is the same file: the blur is made again when the song (or its cover file) changes, and
    the cached bitmap is dropped first, so it never shows an earlier song's."""
    cover = Cover(fw)
    bitmaps, g = blur_window(fw, mcl, cover, queue=2)
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
    assert cover.loads == loads + 1 and cover.unloads == loads + 1 and len(bitmaps.made) == 1  # one bitmap, made again
    cover.ok = False  # the next song's cover can't be read: black, never the last song's blur
    mcl.pos, url.value = 0, b'/mnt/mmc/a.flac'
    tick(fw)
    lcd = Lcd(fw)
    seen = draws(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert seen == [] and lcd.boxes == [((0, 0, 375, 320), 0xff000000, 0)]


def test_stock_s_new_cover_makes_the_blur_again(fw, mcl, now):
    cover = Cover(fw)
    _, g = blur_window(fw, mcl, cover)
    fw.mock('stock_savercover_trampoline', lambda *_: 12)
    assert fw.fn('lockscreen_cover', C.c_int, C.c_void_p)(0x7700) == 12
    assert cover.loads == 2


def test_a_cover_that_can_t_be_read_is_black(fw, mcl, now):
    cover = Cover(fw)
    cover.ok = False
    bitmaps, g = blur_window(fw, mcl, cover)
    lcd = Lcd(fw)
    seen = draws(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert seen == [] and lcd.boxes == [((0, 0, 375, 320), 0xff000000, 0)]


def test_no_blur_without_a_song(fw, mcl, now):
    cover = Cover(fw)
    bitmaps, _ = blur_window(fw, mcl, cover, queue=0)
    assert cover.loads == 0 and bitmaps.made == {}


# Slide to unlock.

TRACK = (36, 234, 303)  # its view's x, y and width, 48 high
KNOB = [((0, 0, 303, 48), 0x33ffffff, 11), ((1, 1, 301, 46), 0x30000000, 10),  # the rim, the track
        ((4, 4, 66, 40), 0xff8a8a8a, 8), ((5, 5, 64, 38), 0xfff4f4f4, 7),  # the knob's edge and face
        ((6, 24, 62, 18), 0xffd6d6d6, 6), ((6, 24, 62, 6), 0xffd6d6d6, 0)]  # its lower half
ARROW = [((23, 21, 16, 6), 0xff6b6b6b, 0)] + [((39 + i, 15 + i, 1, 19 - 2 * i), 0xff6b6b6b, 0) for i in range(10)]
WORDS_X = 72 + (303 - 72 - 4 - 150) // 2  # right of the knob's rest, the words 150 px wide (canvas_measure_text)


@pytest.mark.parametrize('theme', THEMES)
def test_the_slider_is_every_theme_s_alike(fw, mcl, now, theme):
    g = Glance(fw, mcl, theme=theme, queue=1)
    assert g.views['slider'] == TRACK
    lcd = Lcd(fw)
    lcd.paint(fw, g['slider'])
    assert lcd.boxes == KNOB + ARROW
    assert lcd.texts == [('slide to unlock', WORDS_X, 12, 0x8cffffff)]  # dim white


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('light', ['0', '1'])
def test_light_mode_leaves_the_slider_s_look(fw, mcl, now, theme, light):
    g = light_glance(fw, mcl, theme, light, queue=1)
    lcd = Lcd(fw)
    for _ in range(20):
        shine(fw)  # the shine over the words too
    lcd.paint(fw, g['slider'])
    assert lcd.boxes == KNOB + ARROW
    assert [t[3] for t in lcd.texts] == [0x8cffffff, 0x40ffffff, 0x50ffffff, 0x70ffffff]
    assert not fw.fn('ui_ink_raw', C.c_uint, C.c_uint)(0)  # put back after the paint


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('text, face, px', [('Blue Train', 'classic-light', 20), ('Синий поезд', 'bold', 17),
                                            ('青い列車', 'default', 17)])
def test_the_title_is_classic_s_face_in_every_theme(fw, mcl, now, theme, text, face, px):
    """The title is Classic's bold face (classic-light.ttf, in every theme's rootfs) whatever the theme; a script it
    lacks takes bold, then stock's, at the fallback's size, as Classic's own titles."""
    g = Glance(fw, mcl, theme=theme, queue=1)
    wide = (C.c_uint * (len(text) + 1))(*map(ord, text), 0)
    fw.mock('widget_get_text', lambda w, *_: C.addressof(wide) if w == g['scrlabel_title'] else 0)
    Lcd(fw).paint(fw, g['img_screensaver'])
    assert g.win.props('widget_set_prop_str', 'scrlabel_title')['style:normal:font_name'] == face
    assert g.win.props('widget_set_prop_int', 'scrlabel_title')['style:normal:font_size'] == px


def test_the_shine_crosses_the_words_in_three_bands(fw, mcl, now):
    """Each step moves the shine 7 px, one every 50 ms; its bands, 96, 60 and 28 px wide, show the words brighter,
    each inside the words only, the innermost brightest; past the words it rests a while, then starts again."""
    g = Glance(fw, mcl, queue=1)
    lcd = Lcd(fw)
    (_, _, ms), = [t for t in g.timers.added if t[0] in g.timers.of(fw, 'shine')]
    assert ms == 50
    for _ in range(20):
        assert shine(fw) == RET_REPEAT  # 140 px on: the middle at the words' 80th px
    assert ('widget_invalidate_force', g['slider']) in [(n, a[0]) for n, a in fw.calls() if n == 'widget_invalidate_force']
    lcd.paint(fw, g['slider'])
    middle = WORDS_X - 60 + 140
    assert lcd.clips[0::2] == [(middle - w // 2, 0, w, 48) for w in (96, 60, 28)]  # each band's clip, then the old back
    assert lcd.clips[1::2] == [(0, 0, 375, 320)] * 3
    assert [t[3] >> 24 for t in lcd.texts] == [0x8c, 64, 80, 112]
    for _ in range(40):
        shine(fw)  # 420 px a run: 60 steps
    lcd2 = Lcd(fw)
    lcd2.paint(fw, g['slider'])
    assert lcd2.clips == [] and len(lcd2.texts) == 1  # back before the words


def test_the_shine_stops_dark_and_starts_lit(fw, mcl, now):
    """A lock window opens with the screen still dark: the shine's timer starts all the same; its first dark step
    ends it while the one-second look lives on, and the first loop pass lit starts it again."""
    g = Glance(fw, mcl, queue=1, lit=0)
    assert len(g.timers.of(fw, 'shine')) == 1 and len(g.timers.of(fw, 'tick')) == 1
    assert shine(fw) == RET_OK
    poll(fw)
    assert len(g.timers.of(fw, 'shine')) == 1  # still dark
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    poll(fw)
    assert len(g.timers.of(fw, 'shine')) == 2 and len(g.timers.of(fw, 'tick')) == 1  # the tick was still running
    poll(fw)
    assert len(g.timers.of(fw, 'shine')) == 2


def test_the_window_s_end_stops_the_shine(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    (_, forget), = [h for h in handlers(fw, EVT_DESTROY) if h[0] == g['img_screensaver']][-1:]
    forget()
    assert set(g.timers.of(fw, 'shine') + g.timers.of(fw, 'tick')) <= set(g.timers.removed)
    assert shine(fw) == RET_OK


# A finger: the knob, the unlock, the clock's hold and the flip.

DOWN, MOVE, UP, ABORT = O['EVT_POINTER_FIRST'], 0x101, 0x103, O['EVT_POINTER_ABORT']
KNOB_X, KNOB_Y = 40, 238  # the knob's top left on the screen: the track's, 4 px in
TRAVEL = 303 - 66 - 8  # its run along the track


def touch(fw, kind, x, y, wm=0x4400):
    """A pointer event at the screen's (x, y), as the panel gives it (a quarter turn: x up from the screen's bottom,
    y across): lockscreen_touch's answer."""
    e = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(e) + O['EVENT_X']).value = 319 - y
    C.c_int.from_address(C.addressof(e) + O['EVENT_Y']).value = x
    return fw.fn('lockscreen_touch', C.c_int, C.c_void_p, C.c_void_p, C.c_uint)(wm, C.addressof(e), kind)


def knob(fw, g):
    """The knob's x on the screen, as the slider paints it."""
    lcd = Lcd(fw)
    lcd.paint(fw, g['slider'])
    return TRACK[0] + lcd.boxes[2][0][0]


def run(fw, g, name, last=True):
    """Run lock_glance's timer `name`, the last added: its answer."""
    i = g.timers.of(fw, name)[-1 if last else 0]
    fn = next(f for t, f, _ in g.timers.added if t == i)
    return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(0)


def fronted(fw, front=0x7700):
    fw.mock('window_manager_get_top_window', lambda *_: front)


def test_the_knob_follows_the_finger_and_springs_back_short_of_the_end(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    assert touch(fw, DOWN, KNOB_X + 33, 258) == 1
    assert touch(fw, MOVE, KNOB_X + 33 + 100, 250) == 1 and knob(fw, g) == KNOB_X + 100
    assert touch(fw, MOVE, KNOB_X + 33 + 400, 250) == 1 and knob(fw, g) == KNOB_X + TRAVEL  # no further than its end
    assert touch(fw, MOVE, 0, 250) == 1 and knob(fw, g) == KNOB_X
    touch(fw, MOVE, KNOB_X + 33 + 142, 250)
    assert touch(fw, UP, KNOB_X + 33 + 142, 250) == 1 and knob(fw, g) == KNOB_X  # 142 of 229: short
    assert g.timers.of(fw, 'unlock') == [] and fw.calls('saver_unlock') == []


@pytest.mark.parametrize('end', [UP, ABORT])
def test_let_go_past_five_eighths_unlocks_through_stock_s_own_handler(fw, mcl, now, end):
    """Past 62.5% of its run (143 px of 229) the knob goes to the end and stock's slide handler (saver_unlock,
    0x53ec68) closes the window a moment later, outside the touch's dispatch; a finger run off the glass is the same."""
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, KNOB_X + 10, 250)
    touch(fw, MOVE, KNOB_X + 10 + TRAVEL * 5 // 8, 250)
    touch(fw, end, KNOB_X + 10 + TRAVEL * 5 // 8, 250)
    assert knob(fw, g) == KNOB_X + TRAVEL and fw.calls('saver_unlock') == []
    (i, _, ms), = [t for t in g.timers.added if t[0] in g.timers.of(fw, 'unlock')]
    assert ms == 16 and run(fw, g, 'unlock') == RET_OK
    assert [args[:2] for _, args in fw.calls('saver_unlock')] == [(0x7700, 0)]
    assert touch(fw, DOWN, KNOB_X + TRAVEL + 10, 250) == 1 and touch(fw, UP, 40, 250) == 1  # unlocking: taken, nothing moves
    assert knob(fw, g) == KNOB_X + TRAVEL and len(g.timers.of(fw, 'unlock')) == 1


def test_wake_stays_while_the_knob_is_held(fw, mcl, now):
    Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, KNOB_X + 10, 250)
    assert [args[:3] for _, args in fw.calls('reset_poweroptions_timer')] == [(1, 1, 1)]


@pytest.mark.parametrize('x, y, grabbed', [(KNOB_X - 26, 250, True), (KNOB_X - 27, 250, False), (KNOB_X + 66 + 25, 250, True),
                                           (KNOB_X + 66 + 26, 250, False), (60, KNOB_Y - 26, True), (60, KNOB_Y - 27, False),
                                           (60, KNOB_Y + 40 + 25, True), (60, KNOB_Y + 40 + 26, False)])
def test_a_finger_may_land_26_px_round_the_knob(fw, mcl, now, x, y, grabbed):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    assert touch(fw, DOWN, x, y) == 1
    touch(fw, MOVE, x + 50, y)
    assert knob(fw, g) == KNOB_X + (50 if grabbed else 0)


@pytest.mark.parametrize('front, taken', [(0x7700, 1), ('volume_dialog', 1), ('confirminfo_dialog', 0), (0, 0)])
def test_its_window_or_the_volume_over_it_takes_the_touch(fw, mcl, now, front, taken):
    """The volume panel over the slider: ringnav_input put it away on the down, and the touch goes on. A prompt over
    the lock screen (Shut Down?) keeps its own touches."""
    Glance(fw, mcl, queue=1)
    if isinstance(front, str):
        name = C.create_string_buffer(front.encode())
        fw.mock('widget_get_prop_str', lambda w, key, d, *_: C.addressof(name) if w == 0x9900 and C.string_at(key) == b'name' else d)
        front = 0x9900
    fronted(fw, front)
    assert touch(fw, DOWN, KNOB_X + 10, 250) == taken


def test_default_takes_no_touch(fw):
    Config(fw)
    fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = b'screensaver_default'
    fw.mock('stock_saver_trampoline', lambda *_: 31)
    Window(fw, STOCK)
    fw.fn('lockscreen_init', C.c_int, C.c_void_p, C.c_void_p)(0x7700, 0x7800)
    fronted(fw)
    assert touch(fw, DOWN, KNOB_X + 10, 250) == 0


class Ms:
    """time_now_ms."""
    def __init__(self, fw, now=5000):
        self.now = now
        fw.mock('time_now_ms', lambda *_: self.now)


def test_the_clock_held_a_second_flips_to_the_picture_through_black_and_back(fw, mcl, now):
    ms = Ms(fw)
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    assert touch(fw, DOWN, 100, 40) == 1  # on the time, top left
    (_, _, hold), = [t for t in g.timers.added if t[0] in g.timers.of(fw, 'turn')]
    assert hold == 1000
    assert run(fw, g, 'turn') == RET_OK
    (_, _, step), = [t for t in g.timers.added if t[0] in g.timers.of(fw, 'fade_step')]
    assert step == 16
    veil = []
    fw.mock('canvas_set_fill_color', lambda c, color, *_: veil.append(color & 0xffffffff))
    lcd = Lcd(fw)

    def frame(at):
        ms.now = 5000 + at
        answer = run(fw, g, 'fade_step')
        veil.clear()
        lcd.paint(fw, g['veil'])
        return answer, (veil[0] >> 24 if veil and fw.calls('canvas_fill_rect') else 0)
    assert frame(80) == (RET_REPEAT, 127) and g.boxes()['time'][:2] == (36, 16)  # darkening the song's view
    assert frame(200) == (RET_REPEAT, 255 - 40 * 255 // 160) and g.boxes()['time'][1] == 50  # turned: the clock on black
    assert [a[0] for n, a in g.win.calls('widget_set_visible') if n == 'view_playinfo'][-1] == 0  # no song in it
    assert frame(330)[0] == RET_OK
    veil.clear()
    lcd.paint(fw, g['veil'])
    assert veil[1:] == []  # lifted: nothing but the fill put back
    touch(fw, UP, 100, 40)
    assert touch(fw, DOWN, 187, 100) == 1  # the large clock, held, flips back
    run(fw, g, 'turn')
    for at in (80, 200, 330):
        frame(at + 330)
    assert g.boxes()['time'][:2] == (36, 16)


def test_a_late_fade_step_still_turns(fw, mcl, now):
    """A step that comes after the whole fade's time (a busy loop) turns the view all the same, and ends it."""
    ms = Ms(fw)
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, 100, 40)
    run(fw, g, 'turn')
    ms.now += 900
    assert run(fw, g, 'fade_step') == RET_OK and g.boxes()['time'][1] == 50


def test_the_picture_s_view_is_the_picture_and_the_slider_alone(fw, mcl, now):
    """Flipped with a picture chosen, the screen is that picture (the shade under the time) and the slider: no song."""
    ms = Ms(fw)
    Bitmaps(fw)
    seen = draws(fw)
    g = Glance(fw, mcl, picture=WALLS[AURORA], queue=1)
    fronted(fw)
    touch(fw, DOWN, 100, 40)
    run(fw, g, 'turn')
    ms.now += 200
    run(fw, g, 'fade_step')
    lcd = Lcd(fw)
    lcd.paint(fw, g['img_screensaver'])
    assert len(seen) == 1 and len(lcd.boxes) == 44 and g.boxes()['time'][:2] == (36, 16)
    assert [a[0] for n, a in g.win.calls('widget_set_visible') if n == 'view_playinfo'][-1] == 0
    assert g.made == len(ADDED)  # no song line of its own over the slider


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


def test_a_finger_let_go_before_the_second_flips_nothing(fw, mcl, now):
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, 100, 40)
    hold = g.timers.of(fw, 'turn')
    touch(fw, UP, 100, 40)
    assert set(hold) <= set(g.timers.removed)


def test_the_queue_s_end_turns_back_to_the_picture_s_own_view(fw, mcl, now):
    """Flipped, then the queue goes: nothing to flip from; when one comes the song shows."""
    ms = Ms(fw)
    g = Glance(fw, mcl, queue=1)
    fronted(fw)
    touch(fw, DOWN, 100, 40)
    run(fw, g, 'turn')
    ms.now += 200
    run(fw, g, 'fade_step')
    g.queue(0)
    tick(fw)
    g.queue(1)
    tick(fw)
    assert [a[0] for n, a in g.win.calls('widget_set_visible') if n == 'view_playinfo'][-1] == 1


@pytest.mark.parametrize('layout', [None, '0'])
def test_default_adds_nothing(fw, layout):
    """Layout Default (no LOCKLAYOUT, or 0) is stock's lock window as it was: no label of At a Glance's."""
    Config(fw, **({'LOCKLAYOUT': layout} if layout else {}))
    fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = b'screensaver_default'
    fw.mock('stock_saver_trampoline', lambda *_: 31)
    Window(fw, STOCK)
    fw.fn('lockscreen_init', C.c_int, C.c_void_p, C.c_void_p)(0x7700, 0x7800)
    assert fw.calls('hscroll_label_create') == [] and fw.calls('widget_factory_create_widget') == []
