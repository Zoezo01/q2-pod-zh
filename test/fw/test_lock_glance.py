"""lock_glance.c (docs/internals.md#lock-screen): the Lock Screen's At a Glance layout on stock's lock window, the
same in every theme: its widgets' boxes and what it paints."""
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
                 played=(65, 200), **config):
        self.fw, self.mcl, self.low = fw, mcl, Low()
        Config(fw, LOCKLAYOUT='1', THEME=theme, **config)
        fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = picture
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.deques, self.album = Deques(fw), album
        self.queue(queue, pos)
        self.played = played
        fw.mock('mclGetPlayTime', self.play_time)
        fw.mock('stock_saver_trampoline', lambda *_: 31)
        self.win = Window(fw, STOCK)
        self.made = 0
        fw.mock('hscroll_label_create', lambda *_: self.new())
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

    def play_time(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.played

    def get_text(self, w, out, n, *_):
        s = self.texts.get(w, '').encode()[:n - 1] + b'\0'
        C.memmove(out, s, len(s))

    def new(self):
        name = ADDED[self.made] if self.made < len(ADDED) else f'label{self.made}'
        self.made += 1
        return self.win.add(name, 10, 10)

    def __getitem__(self, name):
        return self.win[name]

    def boxes(self):
        """Each widget's last box, by name."""
        return {name: args[:4] for name, args in self.win.calls('widget_move_resize')}

    def text(self, name):
        return self.texts.get(self.win[name])


def tick(fw):
    """lock_glance's one-second look, run once: what it answers."""
    return fw.fn('lock_glance__tick', C.c_int, C.c_void_p)(0)


class Lcd:
    """A canvas with its LCD, and the boxes fill_box fills: their rects, and their colours as they reach the screen
    (after ui_ink), and radius."""
    def __init__(self, fw):
        self.canvas = block(fw, size=0x100)
        self.lcd = block(fw, size=0x200)
        C.c_void_p.from_address(self.canvas + O['CANVAS_LCD']).value = self.lcd
        self.boxes = []
        fw.mock('canvas_fill_rounded_rect', lambda c, r, g, color, radius, *_: self.boxes.append(
            (tuple((C.c_int * 4).from_address(r)), C.c_uint.from_address(color).value, radius)) and 0)

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


@pytest.mark.parametrize('theme', THEMES)
@pytest.mark.parametrize('queue', [0, 1])
def test_light_mode_leaves_its_look(fw, mcl, now, theme, queue):
    """Light Mode flips what the payload paints: At a Glance gives its colours flipped there, so they reach the
    screen as in the dark half, in every theme."""
    seen = []
    for light in '01':
        Bitmaps(fw)
        g = Glance(fw, mcl, theme=theme, picture=WALLS[AURORA], queue=queue, LIGHT=light)
        lcd = Lcd(fw)
        lcd.paint(fw, g['img_screensaver'])
        seen.append(lcd.boxes)
    assert seen[0] == seen[1] and seen[0]


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
    """Layout Default (no LOCKLAYOUT, or 0) is stock's lock window as it was: no label of At a Glance's."""
    Config(fw, **({'LOCKLAYOUT': layout} if layout else {}))
    fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = b'screensaver_default'
    fw.mock('stock_saver_trampoline', lambda *_: 31)
    Window(fw, STOCK)
    fw.fn('lockscreen_init', C.c_int, C.c_void_p, C.c_void_p)(0x7700, 0x7800)
    assert fw.calls('hscroll_label_create') == []
