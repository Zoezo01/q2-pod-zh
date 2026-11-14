"""patch/navigation.c's status bar and Now Playing: the battery slot and its painters, the theme's faces, scrub, the
lyrics' wheel and touch, the full-screen art, the geometry sets' boot and Classic's slider pages; np_bar.c's bar
title and Classic's times and bar; classic_pages.c's keyboard, Search's field and the slider pages' paint."""
import ctypes as C
import re
import struct

import pytest

import host
import art as tools_art  # tools/, on the path host.py set: the art's own sizes
from conftest import NAVIGATION_SOURCE, O, Deques, Externpic, ask_cover, in_fork, ui_name

THEMES = host.tools_theme.builtins()
SETS = host.tools_theme.geometry_sets(THEMES)
MINIMAL, CLASSIC, MODERN = THEMES
GEOM = {t['name']: SETS.index(host.tools_theme.geometry(t)) for t in THEMES}
RET_REPEAT, STOP = 8, 11
FONT_ROW = -2
ROLE_TITLE = 0
W_PARENT = O['W_PARENT']
CANVAS_LCD, LCD_FILL, LCD_TEXT = O['CANVAS_LCD'], O['LCD_FILL_COLOR'], O['LCD_TEXT_COLOR']
SCROLL_X, SCROLL_Y, CONTENT_H = O['SCROLL_X'], O['SCROLL_Y'], O['VIEW_CONTENT_H']
KEY_NEXT, KEY_PREV, KEY_CENTER = O['KEY_NEXT'], O['KEY_PREV'], O['KEY_CENTER']
PCT_W, BODY_W, BODY_H, NUB_W, NUB_H, GAP = (O[k] for k in ('BATT_PCT_W', 'BATT_BODY_W', 'BATT_BODY_H', 'BATT_NUB_W',
                                                           'BATT_NUB_H', 'BATT_GAP'))
INSIDE_W, INSIDE_H, ROOM, STATUS_GAP, BT_REACH = (O[k] for k in ('BATT_INSIDE_W', 'BATT_INSIDE_H', 'BATT_ROOM',
                                                                 'STATUS_GAP', 'BT_REACH'))
CODECS = re.findall(r'"(\w+)"', host.ROOT.joinpath('patch/offsets.inc').read_text().split('#define BT_CODECS', 1)[1].split('\n')[0])
CODEC_REACH = [int(v) for v in re.findall(r'\d+', host.ROOT.joinpath('patch/offsets.inc').read_text()
                                          .split('#define BT_CODEC_REACH', 1)[1].split('\n')[0])]
BT_GLYPH = b'bar_btcon'
# the named periods of navigation.h and its units (NAVIGATION_SOURCE), as their comments state the behaviour by them
NAV = {k: int(v) for k, v in re.findall(r'^#define (\w+) (\d+)\b', NAVIGATION_SOURCE, re.M)}


def rgba(c, a=0xff):
    """theme.h's RGBA: 0xRRGGBB as the canvas's 0xAABBGGRR."""
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16


def wide(s):
    return (C.c_uint * (len(s) + 1))(*map(ord, s))


def read_wide(p):
    out = []
    while p and C.c_uint.from_address(p + 4 * len(out)).value:
        out.append(chr(C.c_uint.from_address(p + 4 * len(out)).value))
    return ''.join(out)


def lum(c):
    """A colour's channels summed: which of two is lighter, in either byte order."""
    return sum(c >> s & 255 for s in (0, 8, 16))


def within(inner, outer, slack=0):
    """Rect inner (x, y, w, h, ...) inside outer, slack px of it allowed past each side."""
    x, y, w, h = inner[:4]
    ox, oy, ow, oh = outer[:4]
    return ox - slack <= x and x + w <= ox + ow + slack and oy - slack <= y and y + h <= oy + oh + slack


def bbox(rects):
    xs, ys = [r[0] for r in rects], [r[1] for r in rects]
    x1, y1 = max(r[0] + r[2] for r in rects), max(r[1] + r[3] for r in rects)
    return min(xs), min(ys), x1 - min(xs), y1 - min(ys)


class Config:
    """config.ini's [IPOD] keys: a key's value, else the caller's default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): str(v).encode() for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


@pytest.fixture(scope='module')
def scratch(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture(scope='module')
def ranges(library):
    """The two faces' cmaps as the build cut them (tools/build.py subset_font): BOLD_RANGES, CLASSIC_LIGHT_RANGES."""
    header = (library.parent/'stock.h').read_text()
    def runs(name):
        values = [int(v, 16) for v in host.tools_build.need(re.search(rf'#define {name} \{{([^}}]*)\}}', header), name)[1].split(',')]
        return list(zip(values[::2], values[1::2]))
    return {'bold': runs('BOLD_RANGES'), 'classic-light': runs('CLASSIC_LIGHT_RANGES')}


def has(runs, text):
    return all(any(lo <= ord(c) <= hi for lo, hi in runs) for c in text)


PTRS = set('''bar_bt bar_wifi bar_pct bar_slot bar_icon bar_idle bar_state bar_eq bar_title np_win np_pos
    np_album np_slider np_remain np_elapsed np_cover np_title np_slide np_lrc slider_win slider_w slider_sw slider_hint
    t9_page t9_back kb_win search_win search_tabs pl_box stream_art stream_bar genre_label number_face'''.split())
BYTES = {'title_align', 'slider_kind', 'bar_dark'}
SHORTS = {'title_x', 'title_y', 'title_w'}
UNSIGNED = set('''codec_timer batt_key bar_timer np_hash np_art np_marquee np_remain_timer np_fit_timer np_press
    np_press_at scrub_timer scrub_track settle_timer lyric_push_at lyric_at lyric_follow_at lyric_song lyric_rows_song
    bc_timer bc_track bc_repaint bc_key bc_box bc_shape bc_url clock_key clock_timer keep_lit_at genre_timer
    last_input'''.split())


class State:
    """navigation.c's st by field name, as this build lays it out."""
    def __init__(self, fw, scratch):
        object.__setattr__(self, 'base', C.addressof(fw.var('st', C.c_char)))
        object.__setattr__(self, 'at', scratch)

    def ref(self, name, i=0):
        kind = (C.c_void_p if name in PTRS or name == 'bc' else C.c_ubyte if name in BYTES else C.c_short if name in SHORTS
                else C.c_uint if name in UNSIGNED else C.c_int)
        return kind.from_address(self.base + self.at[name] + i * C.sizeof(kind))

    def __getattr__(self, name):
        return self.ref(name).value or 0

    def __setattr__(self, name, value):
        self.ref(name).value = value


@pytest.fixture
def st(fw, scratch):
    return State(fw, scratch)


class Ui:
    """Stock's widgets as host blocks (x, y, w, h at 0, the parent at W_PARENT) with their names, props, text,
    visibility and children kept here, stock's widget calls answering from them as AWTK's do; the timers
    and handlers the C adds, kept to run as the loop would."""
    def __init__(self, fw):
        self.fw, self.keep, self.next_timer, self.now, self.top = fw, [], 0, 100000, 0
        self.kids, self.names, self.ints, self.strs, self.texts, self.shown, self.types = {}, {}, {}, {}, {}, {}, {}
        self.timers, self.handlers, self.int_log, self.dirty = {}, [], [], []
        self.wm = self.widget(None, 'window_manager', 0, 0, 375, 320)
        m = fw.mock
        m('widget_lookup', lambda w, name, deep, *_: self.lookup(w, C.string_at(name).decode(), deep))
        m('widget_count_children', lambda w, *_: len(self.kids.get(w, ())))
        m('widget_get_child', lambda w, i, *_: self.child(w, C.c_int(i).value))
        m('widget_index_of', lambda w, *_: self.kids[self.parent(w)].index(w) if self.parent(w) in self.kids else -1)
        m('widget_get_prop_int', lambda w, key, default, *_: self.ints.get((w, C.string_at(key)), C.c_int(default).value))
        m('widget_set_prop_int', self.set_int)
        m('widget_invalidate_force', lambda w, r, *_: self.dirty.append(
            (w, struct.unpack_from('<4i', (C.c_char * 16).from_address(r)) if r else None)))
        m('widget_get_prop_str', lambda w, key, default, *_: self.str_ptr(w, C.string_at(key), default))
        m('widget_set_prop_str', lambda w, key, v, *_: self.set_str(w, C.string_at(key), C.string_at(v)))
        m('image_base_set_image', lambda w, name, *_: self.set_str(w, b'image', C.string_at(name)))
        m('widget_get_text', lambda w, *_: C.addressof(self.texts[w]) if w in self.texts else 0)
        m('widget_set_text', lambda w, s, *_: self.set_text(w, read_wide(s)))
        m('widget_set_text_utf8', lambda w, s, *_: self.set_text(w, C.string_at(s).decode()))
        m('widget_get_text_utf8', lambda w, out, n, *_: C.memmove(out, s := (self.text(w) or '').encode()[:n - 1] + b'\0', len(s)) and 0)
        m('widget_get_visible', lambda w, *_: self.shown.get(w, 1))
        m('widget_set_visible', lambda w, v, *_: self.shown.__setitem__(w, int(bool(v))))
        m('widget_move_resize', lambda w, x, y, ww, h, *_: self.place(w, x, y, ww, h))
        m('widget_resize', lambda w, ww, h, *_: self.resize(w, ww, h))
        m('widget_get_type', lambda w, *_: self.cstr(self.types.get(w, b'view')))
        m('widget_set_name', lambda w, name, *_: self.rename(w, C.string_at(name).decode()))
        m('timer_add', self.timer_add)
        m('timer_remove', lambda t, *_: self.timers.pop(t, None) and 0)
        m('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)) or len(self.handlers))
        m('window_manager', lambda *_: self.wm)
        m('window_manager_get_top_window', lambda *_: self.top)
        m('time_now_ms', lambda *_: self.now)
        m('fw___xstat', lambda *_: -1)  # no Rockbox on the card
        m('hscroll_label_create', lambda parent, x, y, w, h, *_: self.widget(parent, '', x, y, w, h))
        m('image_create', lambda parent, x, y, w, h, *_: self.widget(parent, '', x, y, w, h))
        m('widget_factory_create_widget', lambda f, kind, parent, *_: self.widget(parent, ''))

    def cstr(self, value):
        self.keep.append(C.create_string_buffer(value))
        return C.addressof(self.keep[-1])

    def widget(self, parent, name, x=0, y=0, w=0, h=0, kind=None, text=None):
        block = C.create_string_buffer(0x400)
        self.keep.append(block)
        p = C.addressof(block)
        struct.pack_into('<4i', block, 0, x, y, w, h)
        struct.pack_into('<Q', block, W_PARENT, parent or 0)
        self.names[p] = name
        if name:
            self.set_str(p, b'name', name.encode())
        if parent:
            self.kids.setdefault(parent, []).append(p)
        if kind:
            self.types[p] = kind.encode()
        if text is not None:
            self.set_text(p, text)
        return p

    def set_int(self, w, key, v, *_):
        """The key as the call passed it: a style key is a buffer on the C's stack."""
        self.int_log.append((w, C.string_at(key), C.c_int(v).value))
        self.ints[w, C.string_at(key)] = C.c_int(v).value

    def rename(self, w, name):
        self.names[w] = name
        self.set_str(w, b'name', name.encode())

    def parent(self, w):
        return C.c_void_p.from_address(w + W_PARENT).value or 0

    def child(self, w, i):
        kids = self.kids.get(w, ())
        return kids[i] if 0 <= i < len(kids) else 0

    def lookup(self, w, name, deep):
        for k in self.kids.get(w, ()):
            if self.names.get(k) == name:
                return k
            found = deep and self.lookup(k, name, deep)
            if found:
                return found
        return 0

    def str_ptr(self, w, key, default):
        return C.addressof(self.strs[w, key]) if (w, key) in self.strs else default

    def set_str(self, w, key, value):
        self.strs[w, key] = C.create_string_buffer(value)

    def prop(self, w, key):
        return self.strs[w, key].value if (w, key) in self.strs else self.ints.get((w, key))

    def set_text(self, w, s):
        self.texts[w] = wide(s)

    def text(self, w):
        return read_wide(C.addressof(self.texts[w])) if w in self.texts else None

    def box(self, w):
        return struct.unpack_from('<4i', (C.c_char * 16).from_address(w))

    def screen(self, w):
        """w's box on the screen: its parents' offsets added, up to the window manager's."""
        x, y, ww, h = self.box(w)
        p = self.parent(w)
        while p and p != self.wm:
            px, py, _, _ = self.box(p)
            x, y, p = x + px, y + py, self.parent(p)
        return x, y, ww, h

    def place(self, w, x, y, ww, h):
        struct.pack_into('<4i', (C.c_char * 16).from_address(w), 0, *(C.c_int(v).value for v in (x, y, ww, h)))

    def resize(self, w, ww, h):
        x, y, _, _ = self.box(w)
        self.place(w, x, y, ww, h)

    def timer_add(self, fn, ctx, ms, *_):
        self.next_timer += 1
        self.timers[self.next_timer] = (fn, ctx, ms)
        return self.next_timer

    def fn(self, name):
        return C.cast(getattr(self.fw.lib, name), C.c_void_p).value

    def due(self, name):
        """The live timers that run the static name, of navigation.c or the unit it moved to (ui_name finds it),
        oldest first."""
        return [t for t, (fn, _, _) in sorted(self.timers.items()) if fn == self.fn(ui_name(self.fw.lib, name))]

    def run(self, timer):
        """A timer's pass: AWTK drops it unless it answers RET_REPEAT."""
        fn, ctx, _ = self.timers[timer]
        result = C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(ctx)
        if result != RET_REPEAT:
            self.timers.pop(timer, None)
        return result

    def run_all(self, name, limit=100):
        for _ in range(limit):
            due = self.due(name)
            if not due:
                return
            self.run(due[0])
        raise AssertionError(f'{name} never stops')

    def emit(self, w, evt, event=0):
        """The handlers widget_on gave w for evt, as AWTK dispatches them."""
        return [C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, event) for ww, e, fn, ctx in self.handlers
                if ww == w and e == evt]

    def invalidated(self, w=None):
        return [d for d, _ in self.dirty if w is None or d == w]

    def dirty_rects(self):
        return [(d, r) for d, r in self.dirty if r]


class Canvas:
    """A canvas and its lcd: the fill and text colours stock's setters write there, every rect filled in the
    fill colour, the text drawn with its font and colour, the images by name."""
    def __init__(self, fw, x=0, y=0, fill=0xff123456, sizes=None):
        self.raw, self.lcd = C.create_string_buffer(0x100), C.create_string_buffer(0x200)
        struct.pack_into('<ii', self.raw, 0, x, y)
        struct.pack_into('<Q', self.raw, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<II', self.lcd, LCD_TEXT, 0xff654321, fill)
        self.p, self.rects, self.texts, self.images, self.font, self.clip, self.loaded = C.addressof(self.raw), [], [], [], (0, 0), None, []
        self.sizes = sizes or {}
        m = fw.mock
        m('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_FILL, color & 0xffffffff))
        m('canvas_set_text_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_TEXT, color & 0xffffffff))
        m('canvas_fill_rect', lambda c, x, y, w, h, *_: self.rects.append(
            (C.c_int(x).value, C.c_int(y).value, C.c_int(w).value, C.c_int(h).value, self.fill)))
        m('canvas_set_font', lambda c, face, px, *_: setattr(self, 'font', (C.string_at(face) if face else None, px)))
        m('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.texts.append(
            (''.join(chr(v) for v in (C.c_uint * n).from_address(s)), struct.unpack_from('<4i', (C.c_char * 16).from_address(r)),
             self.font, self.text_color, self.clip)))
        m('canvas_measure_text', lambda c, s, n, *_: sum({'%': 12.6}.get(chr(v), 9.4) for v in (C.c_uint * n).from_address(s)))
        m('canvas_get_clip_rect', lambda c, r, *_: struct.pack_into('<4i', (C.c_char * 16).from_address(r), 0,
                                                                    *(self.clip or (0, 0, 375, 320))))
        m('canvas_set_clip_rect', lambda c, r, *_: setattr(self, 'clip', struct.unpack_from('<4i', (C.c_char * 16).from_address(r))))
        m('widget_load_image', self.load)
        m('canvas_draw_image', lambda c, bitmap, src, dst, *_: self.images.append(
            (self.loaded[C.c_uint.from_address(bitmap + 8).value], struct.unpack_from('<4i', (C.c_char * 16).from_address(dst)))))

    def load(self, w, name, bitmap, *_):
        name = C.string_at(name)
        if self.sizes.get(name, (12, 12)) is None:
            return 1
        self.loaded.append(name)
        struct.pack_into('<3I', (C.c_char * 12).from_address(bitmap), 0, *self.sizes.get(name, (12, 12)), len(self.loaded) - 1)
        return 0

    @property
    def fill(self):
        return struct.unpack_from('<I', self.lcd, LCD_FILL)[0]

    @property
    def text_color(self):
        return struct.unpack_from('<I', self.lcd, LCD_TEXT)[0]

    def pixels(self):
        """Each pixel's last fill, as the rects land."""
        out = {}
        for x, y, w, h, color in self.rects:
            for j in range(y, y + h):
                for i in range(x, x + w):
                    out[i, j] = color
        return out


def theme(fw, st, t=MINIMAL, light=0, **keys):
    """The theme t on its own geometry set (theme_boot's), light or dark."""
    Config(fw, THEME=t['index'], LIGHT=light, **keys)
    st.settings_read = 0
    fw.fn('accent', C.c_int)()
    st.geom = GEOM[t['name']]


# The status bar's battery slot (bar_sync) and what view_battery paints in it (paint_battery).
class Bar:
    """systembar's widgets: Bluetooth, Wi-Fi, the battery label stock writes "88%" in, its slot and icon, the
    play state, EQ, the idle label and the clock, right-aligned in its row."""
    def __init__(self, fw, ui, title=False, eq_x=0):
        self.ui = ui
        self.bar = ui.widget(ui.wm, 'system_bar', 0, 0, 375, 30)
        self.row = ui.widget(self.bar, 'view_right', 240, 0, 135, 30)
        self.bt = ui.widget(self.row, 'img_bt', 10, 6, 14, 18)
        self.wifi = ui.widget(self.row, 'img_wifi', 30, 6, 20, 18)
        self.pct = ui.widget(self.row, 'label_battery', 0, 0, 40, 30, text='88%')
        self.slot = ui.widget(self.row, 'view_battery', 60, 0, 40, 30)
        self.icon = ui.widget(self.row, 'img_battery', 0, 0, 20, 30)
        self.state = ui.widget(self.row, 'img_state', 100, 4, 16, 20)
        self.eq = ui.widget(self.row, 'label_eq', eq_x, 4, 30, 20)
        self.idle = ui.widget(self.row, 'label_idle', 0, 4, 30, 20)
        self.clock = ui.widget(self.bar, 'label_clock', 150, 0, 75, 30, text='12:00')
        self.title = ui.widget(self.bar, 'label_title', 20, 2, 91, 25, text='') if title else 0
        ui.shown.update({self.wifi: 0, self.state: 0, self.eq: 0})
        ui.set_str(self.icon, b'image', b'bar_battery')
        fw.var('fw_ctx_system_bar', C.c_void_p).value = self.bar
        self.sync = lambda: fw.fn('navigation__bar_sync', None, C.c_void_p)(self.bar)

    def level(self, text, icon=b'bar_battery'):
        self.ui.set_text(self.pct, text)
        self.ui.set_str(self.icon, b'image', icon)


@pytest.fixture
def ui(fw):
    return Ui(fw)


def slot_width(ui, bar):
    return ui.box(bar.slot)[2]


@pytest.mark.parametrize('mode, width', [(0, BODY_W + NUB_W), (1, PCT_W), (2, BODY_W + NUB_W + GAP + PCT_W),
                                         (3, INSIDE_W + NUB_W)])
def test_the_slot_is_its_modes_ink_wide_and_the_bars_height(fw, st, ui, mode, width):
    theme(fw, st, BATTERY=mode)
    bar = Bar(fw, ui)
    bar.sync()
    assert ui.box(bar.slot)[2:] == (width, SETS[0].bar)
    assert (ui.shown[bar.icon], ui.shown[bar.pct], ui.shown[bar.slot]) == (0, 0, 1)  # the slot paints it all


def test_only_a_new_level_or_charge_repaints_the_bar_after_the_frame(fw, st, ui):
    theme(fw, st, BATTERY=2)
    bar = Bar(fw, ui)
    repaints = []
    def sync(*level):
        if level:
            bar.level(*level)
        bar.sync()  # mid-paint: an invalidate here is lost, so charging's colour reached one digit
        repaints.append(len(ui.due('bar_repaint')))
        ui.run_all('bar_repaint')
    sync()
    sync()
    sync('87%')
    sync('87%', b'bar_charge')
    sync('87%', b'bar_lowcharge')
    assert repaints == [1, 0, 1, 1, 1] and ui.invalidated(bar.bar) == [bar.bar] * 4


# Stock's battery writes land on the hidden label_battery and img_battery, which repaint nothing: showface's tick
# repaints the bar for a new level or charge, as the minute does for the clock, and only then.
@pytest.mark.parametrize('t, mode', [(MODERN, 1), (CLASSIC, 2), (MINIMAL, 3)])
def test_a_new_level_repaints_the_bar_with_nothing_else_painting(fw, st, ui, t, mode):
    theme(fw, st, t, BATTERY=mode)
    bar = Bar(fw, ui)
    lit = fw.var('g_backlight_status', C.c_ubyte)
    lit.value = 1
    st.pct_digit, st.pct_sign = 10, 10  # as the first paint measured them
    bar.sync()  # the first paint
    ui.run_all('bar_repaint')
    def tick(*level):
        if level:
            bar.level(*level)
        before = len(ui.invalidated(bar.bar))
        fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
        return len(ui.invalidated(bar.bar)) - before
    def frame():  # the repaint's frame shows the new level and asks for no other, unless the slot resized
        bar.sync()
        asked = len(ui.due('bar_repaint'))
        ui.run_all('bar_repaint')
        return asked
    assert [tick(), tick('87%'), frame(), tick(), tick('87%', b'bar_charge'), frame(), tick()] == [0, 1, 0, 0, 1, 0, 0]
    grows = st.batt_mode in (1, 2)  # the digits shown beside or alone; Inside's body keeps its width
    assert [tick('9%'), frame()] == [1, grows]  # a digit fewer
    width = slot_width(ui, bar)
    # a digit more: the slot widens and the row relays, after the frame
    assert [tick('10%'), frame()] == [1, grows] and slot_width(ui, bar) == width + 10 * grows
    lit.value = 0
    assert tick('86%') == 0  # dark: the wake paints it


def test_a_dark_bar_gets_one_repaint_for_a_new_level_and_its_sync_asks_for_no_more(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    st.bar_dark = 1
    bar.sync()
    ui.run_all('bar_repaint')
    bar.level('87%')
    before = len(ui.invalidated(bar.bar))
    fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
    assert len(ui.invalidated(bar.bar)) - before == 1
    bar.sync()
    assert not ui.due('bar_repaint')


def test_classic_repaints_the_bar_when_only_the_cable_flips(fw, st, ui):
    theme(fw, st, CLASSIC, BATTERY=2)
    bar = Bar(fw, ui)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.var('fw_ctx_g_power_chargestate', C.c_int).value = 2  # full
    usb = fw.var('g_usbdet_value', C.c_ubyte)
    usb.value = 0
    st.pct_digit, st.pct_sign = 10, 10
    bar.sync()
    ui.run_all('bar_repaint')
    def tick():
        before = len(ui.invalidated(bar.bar))
        fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
        return len(ui.invalidated(bar.bar)) - before
    usb.value = 1  # full on power: Classic's plug, the label and the icon unchanged
    assert [tick(), tick()] == [1, 0]


def test_wifi_and_bluetooth_crowd_the_percentage_out_to_the_icon(fw, st, ui):
    theme(fw, st, BATTERY=2)
    bar = Bar(fw, ui)
    both = BODY_W + NUB_W + GAP + PCT_W
    assert both + STATUS_GAP + BT_REACH <= ROOM < both + STATUS_GAP + BT_REACH + 20 + STATUS_GAP
    bar.sync()
    assert slot_width(ui, bar) == both
    ui.shown[bar.wifi] = 1
    bar.sync()
    assert slot_width(ui, bar) == BODY_W + NUB_W
    ui.shown[bar.wifi] = ui.shown[bar.bt] = 0
    bar.sync()
    assert slot_width(ui, bar) == both


def test_a_codec_badge_takes_the_room_until_it_fades_to_the_glyph(fw, st, ui):
    theme(fw, st, BATTERY=2)
    bar = Bar(fw, ui)
    both = BODY_W + NUB_W + GAP + PCT_W
    ldac = CODECS.index('bar_ldac')
    assert both + STATUS_GAP + CODEC_REACH[ldac] > ROOM >= both + STATUS_GAP + BT_REACH
    ui.set_str(bar.bt, b'image', b'bar_ldac')
    bar.sync()
    assert slot_width(ui, bar) == BODY_W + NUB_W
    assert [ui.timers[t][2] for t in ui.due('codec_fade')] == [O['CODEC_MS']]
    ui.run_all('codec_fade')
    assert ui.prop(bar.bt, b'image') == BT_GLYPH
    ui.set_str(bar.bt, b'image', b'bar_ldac')  # stock writes the badge again each second
    bar.sync()
    assert ui.prop(bar.bt, b'image') == BT_GLYPH and slot_width(ui, bar) == both
    assert ui.due('codec_fade') == []


def test_a_new_codec_starts_over_and_the_glyph_alone_ends_the_badge(fw, st, ui):
    theme(fw, st, BATTERY=2)
    bar = Bar(fw, ui)
    opacity = []
    fw.mock('widget_set_opacity', lambda w, v, *_: opacity.append(v))
    ui.set_str(bar.bt, b'image', b'bar_aac')
    bar.sync()
    ui.run_all('codec_fade')
    ui.set_str(bar.bt, b'image', b'bar_sbc')
    bar.sync()
    assert opacity[-1] == 255 and len(ui.due('codec_fade')) == 1  # shown whole again, for CODEC_MS
    ui.set_str(bar.bt, b'image', BT_GLYPH)  # a badge's own faded glyph: still that badge
    bar.sync()
    assert len(ui.due('codec_fade')) == 1
    ui.set_str(bar.bt, b'image', b'bar_bt')  # stock's plain glyph: no badge
    bar.sync()
    assert ui.due('codec_fade') == [] and opacity[-1] == 255


def row_h(t):
    """The height of t's icon row (tools/ipod.py status_bar): Classic's is shorter than its bar."""
    g = SETS[GEOM[t['name']]]
    return 2 * ((g.bar - 2 - g.batt_h) // 2 + g.batt_h // 2) if g.bar_px else g.bar


# Status Bar's look on every theme's row: Bluetooth and Wi-Fi on the look's rows off the layout's, and its battery
# centred on the row's height, where the icons are, so each look's group reads as it does under its own theme.
@pytest.mark.parametrize('t, look', [(t, look) for t in THEMES for look in range(4)],
                         ids=lambda v: v['name'] if isinstance(v, dict) else ('Theme', 'Minimal', 'Modern', 'Classic')[v])
def test_a_look_keeps_its_icons_rows_and_its_battery_centred_on_any_row(fw, st, ui, t, look):
    b = (MINIMAL, MODERN, CLASSIC)[look - 1] if look else t
    theme(fw, st, t, BATTERY=0, STATUSBAR=look)
    bar = Bar(fw, ui)
    row = row_h(t)
    ui.place(bar.row, 240, 0, 135, row)
    bt, wifi = ui.box(bar.bt), ui.box(bar.wifi)
    top = min(r[1] for r in Paint(fw, ui, bar)().rects)  # the dark half: no ink above the body
    assert ui.box(bar.bt) == (bt[0], b['bar_bt_dy'], *bt[2:]) and ui.box(bar.wifi) == (wifi[0], b['bar_wifi_dy'], *wifi[2:])
    assert top == (row + 1 - SETS[GEOM[b['name']]].batt_h) // 2


@pytest.mark.parametrize('t, digits, status', [(MINIMAL, [], 0), (CLASSIC, ['88', '88'], 0), (CLASSIC, ['88', '88'], 2)])
def test_a_crowded_row_turns_percent_inside_to_the_icon_but_classics_is_the_icons_own_width(fw, st, ui, t, digits, status):
    theme(fw, st, t, BATTERY=3)
    bar = Bar(fw, ui)
    ui.place(bar.wifi, 30, 6, 60, 18)
    ui.shown[bar.wifi] = ui.shown[bar.eq] = 1
    fw.mock('mclGetPlayStatus', lambda *_: status)  # Classic's group: the play state while playing
    canvas = Paint(fw, ui, bar)()
    assert ui.shown[bar.state] == (status == 2) and ui.shown[bar.eq] == 1  # EQ is stock's to show
    assert [x[0] for x in canvas.texts] == digits  # Classic's twice: dark on the level, white past it
    g = SETS[GEOM[t['name']]]
    assert slot_width(ui, bar) == (g.batt_w + 1 if t is CLASSIC else BODY_W) + NUB_W


def test_classics_battery_reads_icon_and_percent_as_inside(fw, st, ui):
    theme(fw, st, CLASSIC, BATTERY=2)  # Icon + Percent is no Classic value: its default, inside
    bar = Bar(fw, ui)
    assert [x[0] for x in Paint(fw, ui, bar)().texts] == ['88', '88']
    assert slot_width(ui, bar) == SETS[GEOM['Classic']].batt_w + 1 + NUB_W


def test_with_a_bar_widget_missing_nothing_moves(fw, st, ui):
    theme(fw, st, BATTERY=2)
    bar = Bar(fw, ui)
    ui.names[bar.slot] = 'gone'
    before = ui.box(bar.slot)
    bar.sync()
    assert ui.box(bar.slot) == before and ui.invalidated() == []


class Paint:
    """The bar synced, then view_battery painted on a fresh canvas."""
    def __init__(self, fw, ui, bar, **kwargs):
        self.fw, self.ui, self.bar, self.kwargs = fw, ui, bar, kwargs

    def __call__(self, text='88%', icon=b'bar_battery'):
        self.bar.level(text, icon)
        self.bar.sync()
        canvas = Canvas(self.fw, **self.kwargs)
        self.fw.fn('navigation__paint_battery', None, C.c_void_p, C.c_void_p)(self.bar.slot, canvas.p)
        assert canvas.fill == 0xff123456  # put back
        return canvas


def test_percent_alone_is_the_level_and_a_percent_sign_centred_in_the_slot(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    canvas = Paint(fw, ui, bar)()
    (text, rect, (face, px), color, _), = canvas.texts
    assert (text, rect, px, color) == ('88%', (0, 0, *ui.box(bar.slot)[2:]), O['BATT_PX'], 0xffffffff)
    assert canvas.rects == []


@pytest.mark.parametrize('icon, color', [(b'bar_battery', 0xffffff), (b'bar_charge', O['BATT_CHARGE_RGB']),
                                         (b'bar_lowcharge', O['BATT_LOW_RGB'])])
def test_the_percentage_says_charging_or_low_in_its_colour(fw, st, ui, icon, color):
    theme(fw, st, BATTERY=1)
    canvas = Paint(fw, ui, Bar(fw, ui))('15%', icon)
    assert canvas.texts[0][3] == rgba(color)


def test_a_cable_in_with_charging_held_off_is_not_shown_charging(fw, st, ui):
    """Stock's icon is bar_charge for any cable; USB DAC with charging Off, or Charge Limit, charges nothing."""
    from test_usb_charge import Charging
    theme(fw, st, BATTERY=1)
    paint, q = Paint(fw, ui, Bar(fw, ui)), Charging(fw, pref=0, mode=1, cable=1)
    q.config()  # Audio and DAC, charging Off: held
    assert [paint(text, b'bar_charge').texts[0][3] for text in ('50%', '14%')] == [rgba(0xffffff), rgba(O['BATT_LOW_RGB'])]
    q.pref = 1
    q.config()  # charging On
    assert paint('14%', b'bar_charge').texts[0][3] == rgba(O['BATT_CHARGE_RGB'])


def test_the_first_paint_measures_the_digits_and_the_slot_fits_them(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    paint = Paint(fw, ui, bar)
    paint('88%')
    digit, sign = round(9.4), round(12.6)  # Canvas measures '0' 9.4 px and '%' 12.6
    widths = {}
    for text in ('5%', '88%', '100%'):
        paint(text)
        widths[text] = slot_width(ui, bar)
    assert widths == {'5%': digit + sign, '88%': 2 * digit + sign, '100%': 3 * digit + sign}


def icon_parts(canvas, level_h):
    """The stock icon's rects: its level (the one level_h high), its nub (rightmost) and its outline's box."""
    level = [r for r in canvas.rects if r[3] == level_h]
    nub = max(canvas.rects, key=lambda r: r[0])
    return level, nub, bbox([r for r in canvas.rects if r not in level and r != nub])


@pytest.mark.parametrize('mode', [0, 2])
def test_the_stock_icons_level_fills_its_share_and_charging_fills_it(fw, st, ui, mode):
    theme(fw, st, BATTERY=mode)
    bar = Bar(fw, ui)
    paint = Paint(fw, ui, bar)
    inner = BODY_W - 4
    def level(text, icon=b'bar_battery'):
        (x, y, w, h, _), = icon_parts(paint(text, icon), BODY_H - 4)[0]
        return w
    assert level('100%') == inner and level('50%') == inner // 2 and level('0%') == 0
    assert level('10%', b'bar_charge') == inner
    c = paint('100%')
    (lvl,), nub, (ox, oy, ow, oh) = icon_parts(c, BODY_H - 4)
    assert within(lvl, (ox + 1, oy + 1, ow - 2, oh - 2))  # the outline round the level, a pixel clear
    assert (ow, oh) == (BODY_W, BODY_H) and (nub[0], nub[2]) == (ox + ow, NUB_W)  # the nub on its right
    assert abs(2 * nub[1] + nub[3] - (2 * oy + oh)) <= 1  # centred on it
    h = ui.box(bar.slot)[3]
    # icon alone, centred on the slot's rows; beside the percentage it rises BATT_PCT_DY to the digits' ink
    assert abs(2 * (oy + O['BATT_PCT_DY'] * (mode == 2)) + oh - h) <= 1
    assert [t[0] for t in c.texts] == (['100%'] if mode == 2 else [])
    if mode == 2:  # past the nub by the gap, to the slot's end, on its rows
        x = nub[0] + nub[2] + GAP
        assert c.texts[0][1] == (x, 0, ui.box(bar.slot)[2] - x, h)


def test_percent_inside_is_dark_digits_on_a_solid_body(fw, st, ui):
    theme(fw, st, BATTERY=3)
    bar = Bar(fw, ui)
    rounded = []
    fw.mock('canvas_fill_rounded_rect', lambda c, r, g, color, radius, *_: rounded.append(
        (struct.unpack_from('<4i', (C.c_char * 16).from_address(r)), C.c_uint.from_address(color).value, radius)))
    canvas = Paint(fw, ui, bar)('88%', b'bar_charge')
    (body, color, radius), = rounded
    x, y, w, h = body
    assert (x, w, h) == (0, INSIDE_W, INSIDE_H) and abs(2 * y + h - ui.box(bar.slot)[3]) <= 1  # centred on the bar
    assert color == rgba(O['BATT_CHARGE_RGB']) and radius > 0  # solid, rounded, in the charge's colour
    (digits, rect, (_, px), ink, _), = canvas.texts
    assert digits == '88' and x < rect[0] and rect[0] + rect[2] < x + w  # across the body, clear of its ends
    assert rect[1] == y + O['BATT_INSIDE_TEXT_DY']  # lowered: digits have no descenders
    assert px == O['BATT_INSIDE_PX'] and ink == rgba(0)  # dark digits, no percent sign
    (nx, ny, nw, nh, nub_color), = canvas.rects
    assert nx == x + w and abs(2 * ny + nh - (2 * y + h)) <= 1 and nub_color == color  # the nub on its right


def test_no_lcd_paints_nothing(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    bar.sync()
    canvas = Canvas(fw)
    struct.pack_into('<Q', canvas.raw, CANVAS_LCD, 0)
    fw.fn('navigation__paint_battery', None, C.c_void_p, C.c_void_p)(bar.slot, canvas.p)
    assert canvas.rects == canvas.texts == []


# Classic's battery: the set's body with its outline and gloss, Icon or Percent Inside.
def classic_battery(fw, st, ui, light=0, mode=0):
    theme(fw, st, CLASSIC, light, BATTERY=mode)
    bar = Bar(fw, ui)
    ui.place(bar.row, 240, 0, 135, row_h(CLASSIC))  # its body's top is then the middle of the bar's rows above its two lines
    return bar, Paint(fw, ui, bar)


@pytest.mark.parametrize('t, look', [(CLASSIC, 1), (CLASSIC, 2)])
def test_a_flat_status_bar_under_classic_is_minimals_battery(fw, st, ui, t, look):
    def drawn(**keys):
        bar = Bar(fw, ui)
        return Paint(fw, ui, bar)('88%').rects
    theme(fw, st, MINIMAL, BATTERY=0)
    minimal = drawn()
    theme(fw, st, t, BATTERY=0, STATUSBAR=look)
    assert drawn() == minimal


def test_status_bar_classic_under_minimal_is_classics_battery_at_its_own_size(fw, st, ui):
    theme(fw, st, MINIMAL, BATTERY=0, STATUSBAR=3)
    bar = Bar(fw, ui)
    g, y = SETS[GEOM['Classic']], (row_h(MINIMAL) + 1 - SETS[GEOM['Classic']].batt_h) // 2
    rows = level_rows(Paint(fw, ui, bar)('50%'), 2, y + 1, g.batt_h - 2)
    assert sorted(rows) == list(range(y + 1, y + g.batt_h - 1))
    assert all(runs[0][0] == g.batt_w - 2 for runs in rows.values())  # Classic's width, not Minimal's body
    assert ui.box(bar.slot)[2] == g.batt_w + 1 + O['BATT_NUB_W']


def test_status_bar_classic_keeps_the_digits_the_row_has_room_for(fw, st, ui):
    theme(fw, st, MINIMAL, BATTERY=2, STATUSBAR=3)
    bar = Bar(fw, ui)
    canvas = Paint(fw, ui, bar)('100%')
    (text, _, (_, px), _, _), = canvas.texts
    assert (text, px) == ('100%', O['BATT_PX'])  # the theme's size, which BATT_PCT_W reserves, not Classic's 21
    # beside them the body rises to the digits' ink, as the flat icon does
    assert min(r[1] for r in canvas.rects) == (row_h(MINIMAL) + 1 - SETS[GEOM['Classic']].batt_h) // 2 - O['BATT_PCT_DY']


@pytest.mark.parametrize('t, look, image', [(CLASSIC, 1, b'bar_play'), (CLASSIC, 2, b'bar_play'), (MINIMAL, 3, b'classic_light_play_d')])
def test_status_bar_paints_its_look_s_play_state(fw, st, ui, t, look, image):
    theme(fw, st, t, STATUSBAR=look)
    bar = Bar(fw, ui)
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
    assert ui.prop(bar.state, b'image') == image


@pytest.mark.parametrize('t, mode', [(CLASSIC, 0), (MODERN, 3)])
def test_the_battery_s_room_counts_the_row_the_theme_laid_out(fw, st, ui, t, mode):
    theme(fw, st, t, BATTERY=3, STATUSBAR=1)  # a flat look: only Classic's row has the play state on the right
    bar = Bar(fw, ui)
    C.c_int.from_address(bar.state + 8).value = 30
    ui.shown[bar.bt], ui.shown[bar.wifi] = 1, 1
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    bar.sync()
    assert st.batt_mode == mode


# EQ is stock's to show or hide: a bar without it keeps the label off-screen (tools/ipod.py), whatever the look.
@pytest.mark.parametrize('t, look', [(CLASSIC, 1), (MINIMAL, 3)])
def test_the_payload_leaves_eq_as_stock_set_it_whatever_the_status_bar(fw, st, ui, t, look):
    theme(fw, st, t, STATUSBAR=look)
    bar = Bar(fw, ui)
    ui.shown[bar.eq] = 1
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
    assert ui.shown[bar.eq] == 1


def level_rows(canvas, x, y, ih):
    """The rows from x, y down ih: the widest run of each from x."""
    rows = {}
    for rx, ry, w, h, color in canvas.rects:
        if rx == x and h == 1 and y <= ry < y + ih:
            rows.setdefault(ry, []).append((w, color))
    return rows


def test_classics_level_is_its_share_of_the_body_over_the_empty_rows(fw, st, ui):
    _, paint = classic_battery(fw, st, ui)
    g = SETS[GEOM['Classic']]
    iw, ih, y = g.batt_w - 2, g.batt_h - 2, (g.bar - 2 - g.batt_h) // 2
    greens = {c for runs in level_rows(paint('100%'), 2, y + 1, ih).values() for _, c in runs[1:]}
    for text, share in (('88%', 88), ('50%', 50), ('0%', 0)):
        rows = level_rows(paint(text), 2, y + 1, ih)
        assert sorted(rows) == list(range(y + 1, y + 1 + ih))
        for (empty_w, empty), *level in rows.values():
            assert empty_w == iw and empty not in greens  # the empty rows across the inside first
            assert [w for w, _ in level] == [iw * share // 100] and {c for _, c in level} <= greens
    assert len(greens) > 1  # a gloss, row by row


def test_classic_low_is_a_red_sliver_and_charging_a_full_body(fw, st, ui):
    _, paint = classic_battery(fw, st, ui)
    g = SETS[GEOM['Classic']]
    iw, ih, y = g.batt_w - 2, g.batt_h - 2, (g.bar - 2 - g.batt_h) // 2
    low = paint('60%', b'bar_lowcharge')
    assert (2, y + 1, CLASSIC['batt_low_w'], ih, rgba(CLASSIC['batt_low'])) in low.rects
    full = level_rows(paint('10%', b'bar_charge'), 2, y + 1, ih)
    assert all(iw in [w for w, _ in runs[1:]] for runs in full.values())


@pytest.mark.parametrize('icon, power, art', [(b'bar_charge', 0, b'classic_light_bolt'), (b'bar_battery', 1, b'classic_light_plug'),
                                              (b'bar_battery', 0, None)])
def test_classic_shows_the_bolt_charging_and_the_plug_full_on_power(fw, st, ui, icon, power, art):
    _, paint = classic_battery(fw, st, ui)
    fw.var('fw_ctx_g_power_chargestate', C.c_int).value = 2 if power else 1
    fw.var('g_usbdet_value', C.c_ubyte).value = power
    canvas = paint('100%', icon)
    y = (SETS[GEOM['Classic']].bar - 2 - SETS[GEOM['Classic']].batt_h) // 2
    assert canvas.images == ([(art, (2, y + 1, 12, 12))] if art else [])


def test_classic_inside_has_no_bolt_and_its_digits_change_ink_at_the_level(fw, st, ui):
    _, paint = classic_battery(fw, st, ui, mode=3)
    g = SETS[GEOM['Classic']]
    canvas = paint('50%', b'bar_charge')
    assert canvas.images == []
    canvas = paint('50%')
    f = (g.batt_w - 2) * 50 // 100
    (on, _, _, dark, clip_on), (past, _, _, white, clip_past) = canvas.texts
    assert on == past == '50' and (dark, white) == (rgba(0), rgba(0xffffff))
    assert (clip_on[0], clip_on[2]) == (2, f) and (clip_past[0], clip_past[2]) == (2 + f, g.batt_w - 2 - f)


def test_classic_inside_digits_read_the_same_in_the_light_half(fw, st, ui):
    _, paint = classic_battery(fw, st, ui, light=1, mode=3)
    canvas = paint('50%')
    assert [t[3] for t in canvas.texts] == [rgba(0), rgba(0xffffff)]  # final inks: dark on the level, white past it


def test_classics_light_half_adds_a_soft_edge_and_an_emboss_outside_the_body(fw, st, ui):
    g = SETS[GEOM['Classic']]
    _, paint = classic_battery(fw, st, ui, 0)
    dark = bbox(paint('50%').rects)
    assert dark[2:] == (g.batt_w + NUB_W, g.batt_h)  # the dark half: the body and its nub, nothing past them
    theme(fw, st, CLASSIC, 1, BATTERY=0)
    light = paint('50%').rects
    past = [r for r in light if not within(r, dark)]
    assert past and all(within(r, dark, slack=2) for r in past)  # ink just outside the body that the dark half lacks
    assert {r[1] for r in past if r[1] >= dark[1] + dark[3]} and {r[0] for r in past if r[0] < dark[0]}  # under it and left


# Classic's dark bar (USB mode's page, the slider pages, Set Time): its own battery, the icons off.
def dark_parts(rects):
    """The dark battery's rects, the bar's full-width rows left out: its outline (the largest box) and the
    level's rows (1 high, within it)."""
    battery = [r for r in rects if r[2] != 375]
    outline = max(battery, key=lambda r: r[2] * r[3])
    return outline, [r for r in battery if r[3] == 1]


def dark_level(rects):
    return max((r[2] for r in dark_parts(rects)[1]), default=0)


def usb_page(ui):
    page = ui.widget(ui.wm, 'usbmode_page', 0, 0, 375, 320, kind='window')
    ui.set_str(page, b'name', b'usbmode_page')
    return page


def test_the_dark_bar_moves_the_icons_off_and_paints_its_own_battery(fw, st, ui):
    theme(fw, st, CLASSIC, BATTERY=3)
    bar = Bar(fw, ui, title=True)
    bar.sync()  # the bar's first paint finds its widgets
    title = ui.box(bar.title)
    ui.top = usb_page(ui)
    bar.sync()
    for w in (bar.bt, bar.wifi):
        x, _, ww, _ = ui.screen(w)
        assert x + ww <= 0  # off the screen's left
    g = SETS[GEOM['Classic']]
    sx, sy, sw, sh = ui.screen(bar.slot)
    assert 375 // 2 <= sx and sx + sw <= 375 and 0 <= sy and sy + sh <= g.bar  # the bar's right end, on its rows
    assert ui.shown[bar.clock] == 0 and ui.box(bar.title)[::2] == (0, 375)  # the title across the screen
    widths = {}
    for text, icon in (('50%', b'bar_battery'), ('100%', b'bar_battery'), ('10%', b'bar_charge')):
        canvas = Paint(fw, ui, bar)(text, icon)
        outline, level = dark_parts(canvas.rects)
        assert canvas.texts == [] and level and all(within(r, outline) for r in level)  # the level inside the body
        widths[text] = dark_level(canvas.rects)
    assert abs(2 * widths['50%'] - widths['100%']) <= 1 and widths['10%'] == widths['100%']  # charging: full
    state = ui.screen(bar.state)
    ox, oy, _, oh = outline[:4]
    assert state[0] + state[2] // 2 < sx + ox and abs(2 * state[1] + state[3] - (2 * (sy + oy) + oh)) <= 1  # left of it, on its rows
    # a full-screen page's dark bar puts the same battery in the same place
    full = Canvas(fw)
    label = wide('10%')  # the label stock wrote, bar_charge still the icon
    fw.fn('paint_dark_bar', None, C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint)(
        ui.wm, full.p, None, fw.fn('dark_bar_key', C.c_uint, C.c_void_p)(C.addressof(label)))
    assert sorted(r for r in full.rects if r[2] != 375) == sorted((x + sx, y + sy, w, h, c) for x, y, w, h, c in canvas.rects)
    ui.top = 0  # leaving: the title's box and the clock come back
    bar.sync()
    assert ui.shown[bar.clock] == 1 and ui.box(bar.title) == title


def test_dark_bar_key_reads_the_label_and_the_charge_icon(fw, st, ui):
    theme(fw, st, CLASSIC)
    bar = Bar(fw, ui)
    st.bar_icon = bar.icon
    key = fw.fn('dark_bar_key', C.c_uint, C.c_void_p)
    paint = fw.fn('paint_dark_bar', None, C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint)
    def level(text, icon=b'bar_battery'):
        ui.set_str(bar.icon, b'image', icon)
        label = wide(text) if text is not None else None
        canvas = Canvas(fw)
        paint(ui.wm, canvas.p, None, key(None if label is None else C.addressof(label)))
        return dark_level(canvas.rects)
    assert level('250%') == level('100%') > level('88%') > level('7%') > level(None) == 0  # past 100: full
    assert level('7%', b'bar_charge') == level('100%') and level('7%', b'bar_lowcharge') == level('7%')


@pytest.mark.parametrize('status, art', [(2, b'classic_light_play_w'), (3, b'classic_light_pause_w'), (0, None)])
def test_a_full_screen_pages_dark_bar(fw, st, ui, status, art):
    theme(fw, st, CLASSIC)
    fw.mock('mclGetPlayStatus', lambda *_: status)
    paint = fw.fn('paint_dark_bar', None, C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint)
    full = Canvas(fw)
    paint(ui.wm, full.p, None, 100 << 3)
    canvas = Canvas(fw)
    title = wide('Backlight')
    paint(ui.wm, canvas.p, C.addressof(title), 40 << 3)
    assert canvas.fill == 0xff123456
    (text, rect, _, ink, _), = canvas.texts
    assert (text, rect[0], rect[2], ink) == ('Backlight', 0, 375, rgba(0xffffff))  # white, across the screen
    outline, level = dark_parts(canvas.rects)
    rows = {r[1] for r in canvas.rects if r[0] == 0 and r[2] == 375}
    assert rows == set(range(max(rows) + 1)) and {y for r in canvas.rects for y in range(r[1], r[1] + r[3])} <= rows
    assert dark_level(canvas.rects) == dark_level(full.rects) * 40 // 100 and all(within(r, outline) for r in level)
    if art:
        (name, (x, y, w, h)), = canvas.images
        assert name == art and x + w <= outline[0] and outline[1] <= y + h // 2 < outline[1] + outline[3]  # left of it, on its rows
    else:
        assert canvas.images == []
    canvas = Canvas(fw)
    paint(ui.wm, canvas.p, None, 0)
    assert canvas.texts == []


# Stock's systembar_showface each second: the play state follows whichever player plays.
@pytest.mark.parametrize('status, shown, image', [(2, 1, b'bar_play'), (3, 1, b'bar_pause'), (1, 0, None)])
def test_showface_shows_the_play_state_and_answers_as_stock(fw, st, ui, status, shown, image):
    theme(fw, st)
    bar = Bar(fw, ui)
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.mock('stock_showface_trampoline', lambda b, *_: 77 if b == bar.bar else 0)
    assert fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar) == 77
    assert ui.shown[bar.state] == shown and ui.prop(bar.state, b'image') == image
    assert ui.shown[bar.idle] == (not shown)  # neither state nor EQ: the idle label


def test_classics_play_state_is_its_own_art(fw, st, ui):
    theme(fw, st, CLASSIC, light=1)
    bar = Bar(fw, ui)
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
    assert ui.prop(bar.state, b'image') == b'classic_light_play_l'


# Time in Title off: the bar's centre names the page in front.
def window(ui, name, navbar_title=None, navbar_shown=0):
    win = ui.widget(ui.wm, name, 0, 0, 375, 320, kind='window')
    ui.set_str(win, b'name', name.encode())
    if navbar_title is not None:
        nav = ui.widget(win, 'view_navbar', 0, 0, 375, 60)
        ui.shown[nav] = navbar_shown
        ui.widget(nav, 'scrlabel_title', 0, 0, 200, 40, kind='hscroll_label', text=navbar_title)
    return win


def bar_title(fw, bar, top):
    fw.fn('bar_title', None, C.c_void_p, C.c_void_p)(bar.bar, top)


def test_with_time_in_title_on_the_clock_stays(fw, st, ui):
    theme(fw, st)
    bar = Bar(fw, ui)
    bar_title(fw, bar, window(ui, 'display_page', 'Display'))
    assert ui.text(bar.clock) == '12:00'


@pytest.mark.parametrize('name, navbar, want', [('playing_page', None, 'Now Playing'), ('coverflow_page', None, 'Cover Flow'),
                                                ('display_page', 'Display', 'Display')])
def test_the_bar_names_the_page_in_front(fw, st, ui, name, navbar, want):
    theme(fw, st, TIMETITLE=0)
    bar = Bar(fw, ui)
    top = window(ui, name, navbar)
    if name == 'playing_page':
        st.np_win = top
    bar_title(fw, bar, top)
    assert ui.text(bar.clock) == want and len(ui.due('bar_repaint')) == 1
    sets = len(fw.calls('widget_set_text'))
    bar_title(fw, bar, top)
    assert len(fw.calls('widget_set_text')) == sets  # the same title is written once


@pytest.mark.parametrize('title, shown', [('Display', 1), ('', 0), ('(null)', 0)])
def test_a_page_without_a_hidden_title_gives_the_clock_back(fw, st, ui, title, shown):
    theme(fw, st, TIMETITLE=0)
    bar = Bar(fw, ui)
    bar_title(fw, bar, window(ui, 'display_page', 'Display'))
    assert ui.text(bar.clock) == 'Display'
    bar_title(fw, bar, window(ui, 'other_page', title, navbar_shown=shown))
    assert st.bar_titled == 0 and st.clock_key == 0 and len(ui.due('clock_due')) == 1


def test_modern_names_the_page_in_its_bold_and_the_clock_goes_back_to_stocks_face(fw, st, ui):
    theme(fw, st, MODERN, TIMETITLE=0)
    bar = Bar(fw, ui)
    st.np_win = top = window(ui, 'playing_page')
    bar_title(fw, bar, top)
    assert ui.prop(bar.clock, b'style:normal:font_name') == b'bold'
    bar_title(fw, bar, window(ui, 'home_page', 'Q2', navbar_shown=1))
    assert ui.prop(bar.clock, b'style:normal:font_name') == b'default' and len(ui.due('clock_due')) == 1


def test_modern_clock_loses_a_cjk_titles_bold_mark_when_the_title_goes(fw, st, ui):
    """A CJK page's name falls back to stock's face, kept bold by the mark; going Home the clock is plain."""
    theme(fw, st, MODERN, TIMETITLE=0)
    bar = Bar(fw, ui)
    bar_title(fw, bar, window(ui, 'artist_page', '专辑'))
    assert ui.prop(bar.clock, b'_q2_bold_fallback') == 1
    bar_title(fw, bar, window(ui, 'home_page', 'Q2', navbar_shown=1))
    assert ui.prop(bar.clock, b'_q2_bold_fallback') == 0


def test_a_dialog_or_no_window_leaves_the_bar(fw, st, ui):
    theme(fw, st, TIMETITLE=0)
    bar = Bar(fw, ui)
    dialog = window(ui, 'volume_dialog', 'Volume')
    ui.types[dialog] = b'dialog'
    bar_title(fw, bar, dialog)
    bar_title(fw, bar, 0)
    assert ui.text(bar.clock) == '12:00'


def test_classics_own_title_takes_the_clocks_room_up_to_eq(fw, st, ui):
    theme(fw, st, CLASSIC, TIMETITLE=0)
    bar = Bar(fw, ui, title=True, eq_x=10)
    st.bar_title, st.bar_eq = bar.title, bar.eq
    ui.set_text(bar.title, 'Albums')
    bar_title(fw, bar, 0)
    assert ui.text(bar.clock) == '' and ui.box(bar.title)[2] == 240 + 10 - 20  # to EQ's left edge
    ui.set_text(bar.title, '')
    bar_title(fw, bar, 0)
    assert ui.box(bar.title)[2] == 91 and len(ui.due('clock_due')) == 1


# The theme's faces (theme_font, theme_face): its own face where it has the text's characters, else bold.ttf,
# else stock's, which has CJK.
LATIN, CYRILLIC, CJK = 'Albums', 'Альбом', '专辑'
TITLE_PX = CLASSIC['font_px[ROLE_TITLE]']
FACES = [b'default', b'bold', b'classic-light']  # navigation.c's font_faces, by FONT_*


def first_face(ranges, text):
    """The face theme_font gives text, by the cmaps the build cut: Classic's own, else bold, else stock's."""
    return b'classic-light' if has(ranges['classic-light'], text) else b'bold' if has(ranges['bold'], text) else b'default'


def label_font(ui, w):
    return ui.prop(w, b'style:normal:font_name'), ui.prop(w, b'style:normal:font_size')


@pytest.mark.parametrize('text, face', [(LATIN, b'classic-light'), (CYRILLIC, b'bold'), (CJK, b'default')])
def test_classics_title_face_falls_back_by_the_text(fw, st, ui, ranges, text, face):
    assert first_face(ranges, text) == face  # the text is where the cmaps say, or the test proves nothing
    theme(fw, st, CLASSIC)
    label = ui.widget(ui.wm, 'label', text=text)
    answer = fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(label, ROLE_TITLE, -1)
    got, px = label_font(ui, label)
    assert got == face and answer == FACES.index(face) | px << 2
    # bold's and stock's capitals stand taller: standing in for Classic's face either is smaller
    assert px == TITLE_PX if face == b'classic-light' else px == round(TITLE_PX * .83)


def test_a_face_already_written_is_not_written_again(fw, st, ui):
    theme(fw, st, CLASSIC)
    label = ui.widget(ui.wm, 'label', text=LATIN)
    font = fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)
    was = font(label, ROLE_TITLE, -1)
    writes = len(fw.calls('widget_set_prop_str'))
    assert font(label, ROLE_TITLE, was) == was and len(fw.calls('widget_set_prop_str')) == writes


@pytest.mark.parametrize('text', [LATIN, CYRILLIC, CJK])
def test_canvas_text_takes_the_face_and_size_a_label_would(fw, st, ui, ranges, text):
    theme(fw, st, CLASSIC)
    label = ui.widget(ui.wm, 'label', text=text)
    fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(label, ROLE_TITLE, -1)
    face, px = label_font(ui, label)
    size = C.c_int(TITLE_PX)
    s = wide(text)
    got = fw.fn('theme_face', C.c_char_p, C.c_void_p, C.c_int, C.POINTER(C.c_int))(C.addressof(s), -1, C.byref(size))
    assert face == first_face(ranges, text) and (got or b'default', size.value) == (face, px)  # none: the system font


def test_a_theme_without_a_face_draws_the_system_font(fw, st):
    theme(fw, st, MODERN)
    s = wide(LATIN)
    assert fw.fn('theme_face', C.c_char_p, C.c_void_p, C.c_int, C.c_void_p)(C.addressof(s), -1, None) is None


def test_a_pooled_rows_labels_find_their_face_again_for_the_text_stock_bound(fw, st, ui):
    """#217: a table reuses a row, stock's on_load_data writes the new text with no relayout."""
    theme(fw, st, CLASSIC)
    table = ui.widget(ui.wm, 'table')
    row = ui.widget(table, 'row')
    title = ui.widget(row, 'title', text=LATIN)
    plain = ui.widget(row, 'plain', text=LATIN)
    fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(title, ROLE_TITLE, FONT_ROW)
    assert label_font(ui, title)[0] == b'classic-light'
    bound = []
    def load(ctx, index, w):
        bound.append((ctx, index, w))
        ui.set_text(title, CJK)
        ui.set_text(plain, CJK)
        return 7
    stock = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(load)
    C.c_void_p.from_address(table + O['TABLE_LOAD_DATA']).value = C.cast(stock, C.c_void_p).value
    assert fw.fn('ringnav_table_load', C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(0x55, 3, row) == 7
    assert bound == [(0x55, 3, row)]
    assert label_font(ui, title)[0] == b'default' and ui.prop(plain, b'style:normal:font_name') is None


@pytest.mark.parametrize('text', [LATIN, CYRILLIC, CJK])
def test_the_playing_rows_mark_keeps_its_titles_face_under_classic(fw, st, ui, ranges, text):
    """View Queue's playing row, bound as stock's on_load_data binds it: title, style, then the faces again."""
    theme(fw, st, CLASSIC)
    table = ui.widget(ui.wm, 'table')
    row = ui.widget(table, 'row')
    title = ui.widget(row, 'title', text='')
    def load(ctx, index, w):
        fw.fn('stock_row_text', C.c_int, C.c_void_p, C.c_char_p)(title, text.encode())
        fw.fn('stock_row_style', C.c_int, C.c_void_p, C.c_char_p)(title, b's_scrlabel_red24l')
        return 7
    stock = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(load)
    C.c_void_p.from_address(table + O['TABLE_LOAD_DATA']).value = C.cast(stock, C.c_void_p).value
    assert fw.fn('ringnav_table_load', C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(0x55, 3, row) == 7
    plain = ui.widget(ui.wm, 'label', text=text)  # the same title without the mark
    fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(plain, ROLE_TITLE, FONT_ROW)
    face, px = label_font(ui, title)
    assert ui.text(title).startswith('▶ ') and (face, px) == label_font(ui, plain)
    assert face == first_face(ranges, text) and (px == TITLE_PX if face == b'classic-light' else px < TITLE_PX)


# Now Playing (ringnav_playing): the iPod labels bound, the bar and its fill, the times, scrub.
def clock(sec):
    """Seconds as stock's toolsTimeItoa writes them: 1:05."""
    return f'{sec // 60}:{sec % 60:02}'


class Now:
    """Stock's playing_page as its init leaves it, over a queue of twelve tracks playing the third: the
    track's place, album and remaining time, the slider and elapsed time, the art and title, the
    slide_view of art, lyrics and info pages in their column, and the lyrics' scroll_view."""
    def __init__(self, fw, ui, mcl, st, sec=50, total=200, status=2, layout='beside', lyrics=(), opened=True):
        self.fw, self.ui, self.st, self.mcl = fw, ui, st, mcl
        self.sec, self.total, self.status = sec, total, status
        self.deques = Deques(fw)
        self.paths = [C.create_string_buffer(f'/mnt/mmc/Music/{i:02}.flac'.encode()) for i in range(12)]
        self.recs = [C.create_string_buffer(0x60) for _ in self.paths]
        for rec, path in zip(self.recs, self.paths):
            struct.pack_into('<Q', rec, O['REC_PATH'], C.addressof(path))
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.deques.new([C.addressof(r) for r in self.recs])
        mcl.pos = 2
        info = fw.var('fw_ctx_g_play_id3_info', C.c_char * 2716)
        C.memmove(info, self.paths[2].raw, len(self.paths[2].raw))
        C.memmove(C.addressof(info) + O['ID3_ALBUM'], b'Blue Train\0', 11)
        fw.mock('player_playtime_and_length', self.playtime)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('toolsTimeItoa', lambda s, t, *_: C.memmove(s, clock(t).encode() + b'\0', len(clock(t)) + 1))
        fw.mock('stock_playing_trampoline', lambda w, ctx, *_: 41)
        fw.mock('scroll_view_set_offset', lambda w, x, y, *_: struct.pack_into('<ii', (C.c_char * 8).from_address(w + SCROLL_X), 0,
                                                                             C.c_int(x).value, C.c_int(y).value))
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.win = win = ui.widget(ui.wm, 'playing_page', 0, 0, 375, 320, kind='window')
        ui.set_str(win, b'name', b'playing_page')
        self.title = ui.widget(win, 'scrlabel_title', 206, 38, 160, 28, text='Moment\'s Notice')
        self.pos = ui.widget(win, 'label_ipod_pos', 206, 128, 160, 20)
        self.album = ui.widget(win, 'label_ipod_album', 206, 100, 160, 22)
        self.cover = ui.widget(win, 'img_cover', 16, 22, 178, 178)
        self.state = ui.widget(win, 'img_playstate', 0, 0, 40, 40)
        times_y = 245 if layout == 'beside' else 275
        self.elapsed = ui.widget(win, 'label_playtime', 23, times_y, 35, 21)
        self.remain = ui.widget(win, 'label_ipod_remain', 317, times_y, 35, 21)
        self.col = ui.widget(win, 'slide_view_view', 80, 22, 225, 220)
        self.slide = ui.widget(self.col, 'slide_view', 0, 0, 225, 198)
        self.dots = ui.widget(self.col, 'slide_indicator1', 0, 205, 225, 10)
        self.art_page = ui.widget(self.slide, 'view_art', 0, 0, 225, 198)
        self.lyric = ui.widget(self.slide, 'view_lyric', 0, 0, 225, 198)
        self.lrc = ui.widget(self.lyric, 'scroll_lrc', 0, 10, 225, 178)
        self.rows = [ui.widget(self.lrc, f'row{i}', 0, 20 * i, 225, 20, text=line) for i, (line, _) in enumerate(lyrics)]
        for row, (_, sec_) in zip(self.rows, lyrics):
            ui.ints[row, b'lrc_time'] = sec_
        struct.pack_into('<i', (C.c_char * 4).from_address(self.lrc + CONTENT_H), 0, 20 * len(lyrics))
        self.slider = ui.widget(win, 'slider_play', 66, 245, 243, 20)  # last: over the column, as stock's
        ui.ints[self.slider, b'max'], ui.ints[self.slider, b'value'] = 100, 0  # the asset's
        ui.top = win
        if opened:
            assert self.open() == 41

    def open(self):
        return self.fw.fn('ringnav_playing', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0x77)

    def playtime(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total

    def key(self, key):
        return self.fw.fn('navigation__np_key', C.c_int, C.c_void_p, C.c_uint)(self.win, key)

    def value(self):
        return self.ui.ints[self.slider, b'value']

    def seeks(self):
        return [a[0] for _, a in self.fw.calls('player_seek_time')] + [('paused', a[0]) for _, a in self.fw.calls('mclSetSeek')]

    def fill(self):
        return self.ui.prop(self.slider, b'style:normal:fg_color') & 0xffffffff


@pytest.fixture
def now(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    return Now(fw, ui, mcl, st)


def test_the_page_shows_its_tracks_place_album_and_time_left(fw, ui, now):
    assert (ui.text(now.pos), ui.text(now.album), ui.text(now.remain)) == ('3 of 12', 'Blue Train', '-2:30')
    sets = [(w, key) for w, key, _ in ui.int_log if w == now.slider]
    assert sets.index((now.slider, b'max')) < sets.index((now.slider, b'value'))  # the max first, or it clamps
    assert now.value() == 50 and ui.ints[now.slider, b'max'] == 200


def test_the_labels_are_written_only_when_their_source_changes(fw, ui, mcl, now, st):
    sync = fw.fn('np_sync', C.c_int, C.c_void_p)
    writes = len(fw.calls('widget_set_text_utf8'))
    assert sync(now.win) == 0 and len(fw.calls('widget_set_text_utf8')) == writes
    ui.ints[now.slider, b'value'] = 60
    assert sync(now.win) == 1 and ui.text(now.remain) == '-2:20'
    mcl.pos = 3
    sync(now.win)
    assert ui.text(now.pos) == '4 of 12' and ui.text(now.album) == ''  # the parsed tags are another track's
    assert sync(ui.wm) == 0


def test_a_stopped_queue_shows_no_place_and_a_slider_past_its_end_no_negative_time(fw, ui, mcl, now):
    mcl.pos = 12
    ui.ints[now.slider, b'value'] = 260
    fw.fn('np_sync', C.c_int, C.c_void_p)(now.win)
    assert (ui.text(now.pos), ui.text(now.remain)) == ('', '-0:00')


@pytest.mark.parametrize('t', THEMES, ids=[t['name'] for t in THEMES])
def test_the_bar_takes_its_themes_height_and_ends_and_the_glyph_and_album_their_opacity(fw, ui, mcl, st, t):
    theme(fw, st, t)
    opacity = {}
    fw.mock('widget_set_opacity', lambda w, v, *_: opacity.__setitem__(w, v))
    now = Now(fw, ui, mcl, st)
    assert ui.ints[now.slider, b'bar_size'] == t['np_bar']
    assert {ui.ints[now.slider, k] for k in (b'style:normal:round_radius', b'style:focused:round_radius')} == {t['np_bar'] // 2}
    assert (opacity[now.state], opacity[now.album]) == (t['np_state_op'], t['np_album_op'])


def test_no_window_binds_nothing(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    Now(fw, ui, mcl, st, opened=False)
    assert fw.fn('ringnav_playing', C.c_int, C.c_void_p, C.c_void_p)(None, 0) == 41
    assert st.np_win == 0 and ui.handlers == []


def test_a_page_stock_refused_binds_nothing(fw, ui, mcl, st):
    """Headphones required with none to play to: stock toasts, closes the window it was given and returns STOP (11)."""
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, opened=False)
    fw.mock('stock_playing_trampoline', lambda w, ctx, *_: 11)
    assert now.open() == 11
    assert st.np_win == 0 and ui.handlers == []


def test_classics_times_and_bar_fit_a_long_track_off_the_paint(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, sec=10, total=4000)
    assert ui.due('np_fit_due') == []  # the open fitted them at once
    x, _, w, _ = ui.box(now.slider)
    assert x > 66 and x + w == 375 - x  # narrower for the hours' wider times, still centred
    ex, _, ew, _ = ui.box(now.elapsed)
    rx, _, rw, _ = ui.box(now.remain)
    gap = O['NP_IPOD_TIME_GAP']
    assert ex + ew + gap == x and rx == x + w + gap and ew == rw
    ui.ints[now.slider, b'max'] = 300  # the next track, short: found stale in a paint
    fw.fn('np_sync', C.c_int, C.c_void_p)(now.win)
    fit, = ui.due('np_fit_due')
    ui.run(fit)
    assert ui.box(now.slider)[0] == 66 and st.np_fit_timer == 0


def test_times_under_the_bar_are_never_refitted(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, sec=10, total=4000, layout='under')
    assert ui.box(now.slider) == (66, 245, 243, 20)
    ui.ints[now.slider, b'max'] = 300
    fw.fn('np_sync', C.c_int, C.c_void_p)(now.win)
    assert ui.due('np_fit_due') == []


def test_the_remaining_time_repaints_once_after_the_frame(fw, ui, now, st):
    st.np_remain_timer = 9
    assert fw.fn('remain_due', C.c_int, C.c_void_p)(None) == 0
    assert ui.invalidated(now.remain) == [now.remain] and st.np_remain_timer == 0


# Scrub: a double Centre press, the wheel previews, one press commits.
def press(now, at):
    now.ui.now = at
    return now.key(KEY_CENTER)


def scrub(now, at=200000):
    assert press(now, at) == STOP and press(now, at + 100) == STOP
    assert now.st.scrub == 1


def test_a_double_press_starts_a_scrub_white_on_the_held_bar(fw, ui, now, st):
    accent = now.fill()
    scrub(now)
    assert now.fill() == 0xffffffff != accent
    assert len(fw.calls('playing_timer_clear')) == 1 and [ui.timers[t][2] for t in ui.due('scrub_expire')] == [O['SCRUB_MS']]
    assert ui.due('np_single') == []  # the first press's single was called off


def test_the_wheel_previews_the_target_in_steps_and_one_press_seeks_there(fw, ui, now, st):
    scrub(now)
    step = O['SCRUB_STEP']
    for _ in range(3):
        ui.now += 300  # slow ticks: a step each
        assert now.key(KEY_NEXT) == STOP
    to = now.sec + 3 * step
    assert now.value() == to and ui.text(now.elapsed) == clock(to) and ui.text(now.remain) == '-' + clock(now.total - to)
    assert now.seeks() == []  # only a preview
    ui.now += 300
    now.key(KEY_PREV)
    assert now.value() == to - step
    press(now, ui.now + 1000)
    assert now.seeks() == [to - step] and st.scrub == 0 and now.fill() != 0xffffffff
    assert len(ui.due('settle_poll')) == 1  # the bar holds the target until the player gets there


def test_a_fast_spin_steps_further_than_slow_ticks(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, sec=0, total=3000)
    moved = []
    for gap in (200, 40):
        ui.now += 1000
        scrub(now, ui.now)
        start = now.value()
        for _ in range(12):
            ui.now += gap
            now.key(KEY_NEXT)
        moved.append(now.value() - start)
        press(now, ui.now + 1000)
    slow, fast = moved
    assert slow == 12 * O['SCRUB_STEP'] < fast


@pytest.mark.parametrize('key, start, end', [(KEY_NEXT, 199, 200), (KEY_PREV, 1, 0)])
def test_the_target_stays_within_the_track(fw, ui, mcl, st, key, start, end):
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, sec=start, total=200)  # a second from either end: a step would pass it
    scrub(now)
    ui.now += 300
    now.key(key)
    assert now.value() == end


def test_a_scrub_that_never_moved_seeks_nowhere_and_the_timer_comes_back(fw, ui, now):
    scrub(now)
    press(now, ui.now + 1000)
    assert now.seeks() == [] and len(fw.calls('playing_timer_start')) == 1 and ui.due('settle_poll') == []


def test_a_scrub_ends_by_itself_after_its_time_and_seeks(fw, ui, now, st):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    expire, = ui.due('scrub_expire')
    ui.run(expire)
    assert st.scrub == 0 and now.seeks() == [now.sec + O['SCRUB_STEP']]


def test_another_track_drops_the_scrub_without_a_seek(fw, ui, mcl, now, st):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    mcl.pos = 5
    assert now.key(KEY_NEXT) == STOP
    assert st.scrub == 0 and now.seeks() == []


def test_a_paused_scrub_seeks_on_play_and_the_bar_holds_its_target(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, status=3)
    cue = 600
    struct.pack_into('<i', now.recs[2], O['REC_CUE_START'], cue)  # a CUE track: file seconds
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    to = now.sec + O['SCRUB_STEP']
    assert now.seeks() == [('paused', cue + to)] and st.held_sec == now.sec + 1  # the player's second then, +1
    settle, = ui.due('settle_poll')
    for _ in range(20):
        assert ui.run(settle) == RET_REPEAT  # paused: the player's second stands still
    assert fw.calls('playing_timer_start') == []
    now.status, now.sec = 2, to  # Play: the player gets there
    assert ui.run(settle) == 0 and len(fw.calls('playing_timer_start')) == 1


def test_a_seek_the_player_never_reaches_gives_up_after_a_while_playing(fw, ui, now):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    settle, = ui.due('settle_poll')
    period, runs = ui.timers[settle][2], 1
    while ui.run(settle) == RET_REPEAT:
        runs += 1
    # SETTLE_PLAY_MS of playing short of the target, then stock's timer has the bar back
    assert runs > 1 and runs * period == NAV['SETTLE_PLAY_MS'] and len(fw.calls('playing_timer_start')) == 1


def test_reopening_the_page_mid_wait_shows_the_target_again(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, status=3)
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    ui.emit(now.win, O['EVT_DESTROY'], 0)
    again = Now(fw, ui, mcl, st, status=3)
    to = now.sec + O['SCRUB_STEP']
    assert again.value() == to and ui.text(again.elapsed) == clock(to) and len(ui.due('settle_poll')) == 1
    third = Now(fw, ui, mcl, st, status=2)  # playing again: the target is gone
    assert third.value() == now.sec and st.held_sec == 0


def test_a_single_press_turns_the_screen_off_as_stocks_release(fw, ui, now):
    releases = []
    fw.mock('on_wm_keyup_fun', lambda w, event, *_: releases.append(C.c_uint.from_address(event + O['EVENT_KEY']).value))
    assert press(now, 200000) == STOP
    single, = ui.due('np_single')
    assert ui.timers[single][2] == O['DOUBLE_CLICK_MS']
    ui.run(single)
    assert releases == [KEY_CENTER]
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    press(now, 300000)
    ui.run(ui.due('np_single')[0])
    assert releases == [KEY_CENTER]  # dark: nothing to turn off


def test_single_press_scrub_swaps_the_presses(fw, ui, mcl, st):
    theme(fw, st, CLASSIC, SCRUBSINGLE=1)
    releases = []
    fw.mock('on_wm_keyup_fun', lambda w, event, *_: releases.append(1))
    now = Now(fw, ui, mcl, st)
    press(now, 200000)
    ui.run(ui.due('np_scrub')[0])
    assert st.scrub == 1 and releases == []
    press(now, 200500)  # one press ends it
    assert st.scrub == 0
    press(now, 201000)
    press(now, 201100)  # a double press: the screen off
    assert releases == [1] and st.scrub == 0


def test_single_press_scrubs_overdue_press_starts_and_ends_one(fw, ui, mcl, st):
    theme(fw, st, CLASSIC, SCRUBSINGLE=1)
    now = Now(fw, ui, mcl, st)
    press(now, 200000)
    assert press(now, 200400) == STOP  # its timer not yet run: the single now, and this press ends it
    assert st.scrub == 0 and now.seeks() == [] and len(fw.calls('playing_timer_clear')) == 1


def test_a_scrub_closes_the_volume_band_over_the_page_first(fw, ui, now, st):
    vol = ui.widget(ui.wm, 'volume_dialog', 0, 200, 375, 120, kind='dialog')
    ui.set_str(vol, b'name', b'volume_dialog')
    ui.top = vol
    fw.fn('navigation__scrub_start', None)()
    assert [a[0] for _, a in fw.calls('window_close')] == [vol] and st.scrub == 1


def test_a_volume_dialog_over_another_page_stays(fw, ui, now, st):
    ui.widget(ui.wm, 'home_page', kind='window')
    vol = ui.widget(ui.wm, 'volume_dialog', kind='dialog')
    ui.set_str(vol, b'name', b'volume_dialog')
    ui.top = vol
    fw.fn('navigation__scrub_start', None)()
    assert fw.calls('window_close') == [] and st.scrub == 1


def test_the_page_going_mid_scrub_seeks_nowhere_and_forgets_it(fw, ui, now, st):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    st.np_marquee = ui.timer_add(0, 0, 50)
    ui.emit(ui.wm, O['EVT_DESTROY'], 0)  # another window's destroy: nothing
    assert st.np_win == now.win
    ui.emit(now.win, O['EVT_DESTROY'], 0)
    assert (st.np_win, st.np_slider, st.np_lrc, st.scrub, st.np_marquee) == (0, 0, 0, 0, 0)
    assert now.seeks() == [] and ui.due('scrub_expire') == []


def test_closing_mid_wait_restarts_stocks_timer(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    now = Now(fw, ui, mcl, st, status=3)
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    starts = len(fw.calls('playing_timer_start'))
    fw.fn('navigation__np_cancel', None)()
    assert ui.due('settle_poll') == [] and len(fw.calls('playing_timer_start')) == starts + 1


# The art follows the player's cover (np_art_poll).
def test_the_art_reloads_when_the_players_cover_changes(fw, ui, now, st):
    poll = fw.fn('navigation__np_art_poll', None)
    def unloads():
        return [C.string_at(a[1]) for _, a in fw.calls('image_manager_unload_bitmap_by_name')]
    poll()
    assert unloads() == []  # stock's init loaded this one
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = now.paths[2].value
    fw.var('g_playcover_type', C.c_ubyte).value = 1
    poll()
    assert unloads() == [b'file:///tmp/coverpic.jpg'] and ui.prop(now.cover, b'image') == b'file:///tmp/coverpic.jpg'
    poll()
    assert len(unloads()) == 1
    fw.var('g_playcover_type', C.c_ubyte).value = 3
    poll()
    assert ui.prop(now.cover, b'image') == b'play_defaultcover'
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    fw.var('g_playcover_type', C.c_ubyte).value = 2
    poll()
    assert ui.prop(now.cover, b'image') == b'play_defaultcover'  # dark: the next lit pass


# A card app's URL queue: every song's REC_PATH is "" and a cover by URL leaves g_lastcover_url "", so the type and the
# track's path are the same for every song; only the file stock's cover thread wrote (its size and time) tells their
# art apart. The thread clears its request before it downloads, and a cover asked for mid-download lands after it.
@in_fork
def test_each_cover_a_url_queue_downloads_reloads_the_art_dark_or_lit(fw, ui, now, st):
    poll = fw.fn('navigation__np_art_poll', None)
    def unloads():
        return len(fw.calls('image_manager_unload_bitmap_by_name'))
    for path in now.paths:
        path.value = b''
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = b''
    kind, lit, pic = fw.var('g_playcover_type', C.c_ubyte), fw.var('g_backlight_status', C.c_ubyte), Externpic(fw)
    def downloaded(size=20000):
        pic.write(size)
        kind.value = 2
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 1 and ui.prop(now.cover, b'image') == b'file:///tmp/externpic.jpg'
    pic.write(8000)  # read mid-write: part of the file there
    poll()
    assert unloads() == 2
    pic.grow(20000)  # the rest, in the same tick: only the size tells
    poll()
    poll()
    assert unloads() == 3 and st.np_art == fw.fn('navigation__np_art', C.c_uint, C.c_void_p)(C.byref(C.c_char_p()))
    lit.value = 0
    now.mcl.pos = 3  # the next song while the screen is dark: no pass sees its cover asked for
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 3
    lit.value = 1
    poll()
    assert unloads() == 4  # its own art at the first lit pass
    poll()
    now.mcl.pos = 4  # lit, and no pass while its cover downloads
    poll()
    assert unloads() == 4  # the last song's file still: nothing new to show yet
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 5
    now.mcl.pos = 5  # a quick skip: asked while song 4's cover still downloads, which lands first (as large, the
    # same second: only the nanoseconds tell)
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 6
    kind.value = 0  # the thread takes song 5's request; no pass sees it
    downloaded(31000)
    poll()
    assert unloads() == 7
    now.mcl.pos = 12  # stopped past the queue's end: no track, nothing read from one
    poll()
    assert unloads() == 7


# Classic's progress bar (ipod_bar, paint_scrubber) and the rows painter (paint_rows_bar).
def scrubber(fw, w, canvas):
    fw.fn('paint_scrubber', None, C.c_void_p, C.c_void_p)(w, canvas.p)


def test_the_played_part_is_the_accents_band_and_the_rest_the_tracks(fw, ui, now, st):
    canvas = Canvas(fw)
    scrubber(fw, now.slider, canvas)
    bar, w = CLASSIC['np_bar'], ui.box(now.slider)[2]
    on = w * 50 // 200
    y = (20 - bar) // 2
    rows = [r for r in canvas.rects if r[3] == 1]
    played = [r for r in rows if r[0] == 0 and r[2] == on]
    track = [r for r in rows if r[0] == on and r[2] == w - on]
    reflect = O['SCRUB_REFLECT_ROWS']
    assert [r[1] for r in played] == [r[1] for r in track] == list(range(y, y + bar + reflect))
    assert {r[4] for r in played[:bar]}.isdisjoint({r[4] for r in track[:bar]})
    assert canvas.fill == 0xff123456 and not [r for r in rows if r[2] == 2 * O['NP_SCRUB_MARK'] + 1]  # no cue


@pytest.mark.parametrize('lo, hi, value, share', [(10, 210, 60, (1, 4)), (0, 200, 250, (1, 1)), (0, 200, -5, (0, 1)),
                                                  (50, 50, 50, (0, 1))])
def test_the_fill_is_the_values_share_of_the_sliders_range(fw, ui, now, st, lo, hi, value, share):
    ui.ints[now.slider, b'min'], ui.ints[now.slider, b'max'], ui.ints[now.slider, b'value'] = lo, hi, value
    canvas = Canvas(fw)
    scrubber(fw, now.slider, canvas)
    w = ui.box(now.slider)[2]
    first = [r for r in canvas.rects if r[3] == 1 and r[0] == 0][0]  # the played part's first row
    assert first[2] == w * share[0] // share[1]


def test_the_reflection_fades_from_its_row_to_the_paper(fw, ui, now, st):
    canvas = Canvas(fw)
    scrubber(fw, now.slider, canvas)
    bar, reflect = CLASSIC['np_bar'], O['SCRUB_REFLECT_ROWS']
    band = [r for r in canvas.rects if r[3] == 1 and r[0] == 0]
    def dist(c):  # from the dark half's paper, black
        return sum(c >> s & 255 for s in (0, 8, 16))
    fades = [dist(r[4]) for r in band[bar:bar + reflect]]
    assert fades == sorted(fades, reverse=True) and fades[0] < dist(band[bar - 1][4])


@pytest.mark.parametrize('value', [0, 100, 200])
def test_a_scrubs_cue_is_a_diamond_at_the_fills_end_kept_in_the_bar(fw, ui, now, st, value):
    st.scrub = 1
    ui.ints[now.slider, b'value'] = value
    canvas = Canvas(fw)
    scrubber(fw, now.slider, canvas)
    mark, bar, w = O['NP_SCRUB_MARK'], CLASSIC['np_bar'], ui.box(now.slider)[2]
    ink = [r for r in canvas.rects if r[4] == rgba(0xffffff)]  # the dark half's ink
    cy = (20 - bar) // 2 + (bar - 1) // 2
    assert [(r[1], r[2]) for r in ink] == [(cy + j, 2 * (mark - abs(j)) + 1) for j in range(-mark, mark + 1)]
    fill_end = w * value // ui.ints[now.slider, b'max']
    assert {r[0] + r[2] // 2 for r in ink} == {min(max(fill_end, mark), w - 1 - mark)}  # its widest row whole in the bar


def test_the_radios_bar_paints_always_full(fw, ui, now, st):
    bar = ui.widget(now.win, 'bar', 66, 245, 243, 26)
    st.stream_bar = bar
    canvas = Canvas(fw)
    scrubber(fw, bar, canvas)
    assert {r[2] for r in canvas.rects if r[3] == 1} == {243, 0, 1}  # the band across, none of the track, its end columns


def test_a_theme_without_classics_track_paints_no_bar(fw, ui, mcl, st):
    theme(fw, st, MINIMAL)
    now = Now(fw, ui, mcl, st)
    canvas = Canvas(fw)
    scrubber(fw, now.slider, canvas)
    assert fw.fn('ipod_bar', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.p, 0, 0, 100, 50, 0) == 0
    assert canvas.rects == []


@pytest.mark.parametrize('light', [0, 1])
def test_the_rows_painter_draws_its_themes_rows_and_the_fill_over_them(fw, st, light):
    theme(fw, st, CLASSIC, light)
    canvas = Canvas(fw)
    fw.fn('paint_rows_bar', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.p, 10, 5, 100, 40, 30)
    track, fill = CLASSIC[f'np_rows[{2 * light}]'], CLASSIC[f'np_rows[{2 * light + 1}]']
    def final(c):
        return rgba(c & 0xffffff, c >> 24)
    assert len(track) < 40  # the 40 rows asked for: the theme's rows, no more
    assert canvas.rects == [r for j in range(len(track)) for r in ((10, 5 + j, 100, 1, final(track[j])),
                                                                   (10, 5 + j, 30, 1, final(fill[j])))]
    assert canvas.fill == 0xff123456


# The lyrics page (lyric_poll, every UI pass): the follow glides on the page, the wheel's raw travel nudges or
# takes them, a finger's drag holds them; a tap takes them full screen on every layout, Centred's within its
# column, the iPod page's over the whole window.
LINES = [(f'Line {i}', 10 * i) for i in range(12)]
SLOT = O['LYRIC_SLOT']


class Lyrics(Now):
    """Now Playing on its lyrics page, twelve lines ten seconds apart; AWTK's list_view layout puts the rows
    under a blank LYRIC_SLOT, each as high as lyrics.c sizes it."""
    def __init__(self, fw, ui, mcl, st, page=1, **kwargs):
        self.layouts, self.pressed, self.animating = {}, 0, 0
        fw.mock('widget_set_children_layout', lambda w, s, *_: self.layouts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_layout_children', lambda w, *_: self.layout(ui, w))
        fw.mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        fw.mock('window_manager_is_animating', lambda *_: self.animating)
        fw.mock('mclGetLyricSize', lambda *_: len(LINES))
        super().__init__(fw, ui, mcl, st, lyrics=kwargs.pop('lyrics', LINES), **kwargs)
        ui.ints[self.slide, b'value'] = page
        self.poll_fn = fw.fn('navigation__lyric_poll', None)
        fw.mock('widget_to_local', self.to_local)

    def to_local(self, w, xy, *_):
        """AWTK's: the point less w's place on the screen, plus scroll_lrc's offset (the rows' place in it)."""
        x, y, _, _ = self.ui.screen(w)
        if w == self.lrc:
            x, y = x - C.c_int.from_address(w + SCROLL_X).value, y - self.y()
        point = (C.c_int * 2).from_address(xy)
        point[0] -= x
        point[1] -= y

    def layout(self, ui, w):
        m = re.fullmatch(r'list_view\(x=0,y=(\d+),s=0\)', self.layouts.get(w, ''))
        y = int(m[1]) if m else 0
        for k in ui.kids.get(w, ()):
            x, _, kw, kh = ui.box(k)
            ui.place(k, x, y, kw, kh)
            y += kh

    def poll(self, passes=1, ms=25):
        offsets = []
        for _ in range(passes):
            self.ui.now += ms
            self.poll_fn()
            offsets.append(self.y())
        return offsets

    def y(self):
        return C.c_int.from_address(self.lrc + SCROLL_Y).value

    def scroll(self, y):
        C.c_int.from_address(self.lrc + SCROLL_Y).value = y

    def settle(self):
        offsets = self.poll(60)
        assert offsets[-1] == offsets[-2] and self.st.lyric_glide == 0
        return offsets[-1]


def centre(line):
    """lyric_centre: row i's middle on the view's (178 px) middle, the rows SLOT apart after a blank SLOT."""
    return SLOT + SLOT * line - (178 - SLOT) // 2


@pytest.fixture
def wheel(fw):
    travel, held = fw.var('wheel_travel', C.c_int), fw.var('wheel_held', C.c_int)
    yield travel, held
    travel.value = held.value = 0
    fw.var('wheel_raw', C.c_int).value = 0


def test_the_lyrics_page_glides_its_playing_line_to_the_middle_easing_in(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    offsets = lyr.poll(40)
    assert offsets[-1] == centre(3) and st.lyric_glide == 0
    steps = [b - a for a, b in zip(offsets, offsets[1:]) if b != a]
    assert all(s > 0 for s in steps) and steps[1:] == sorted(steps[1:], reverse=True) and steps[1] > 1
    assert fw.var('wheel_raw', C.c_int).value == 1  # the page holds the wheel's raw travel
    lyr.sec = 75
    assert lyr.poll(40)[-1] == centre(7)


def test_off_the_page_the_follow_jumps_at_stocks_pace(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35, page=0)
    assert lyr.poll(1) == [centre(3)]  # at once, no glide
    lyr.sec = 55
    assert lyr.poll(1, ms=100) == [centre(3)] and lyr.poll(1, ms=150) == [centre(5)]
    assert fw.var('wheel_raw', C.c_int).value == 0


def test_a_turn_takes_the_lyrics_a_line_a_ticks_worth_and_holds_them(fw, ui, mcl, st, wheel):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    travel, held = wheel
    detents, tick = O['LYRIC_PUSH_DETENTS'], O['WHEEL_TICK_UNITS']  # at 50% sensitivity, a tick's raw units
    travel.value = -detents * tick  # clockwise: on down the lyrics, as far as a push takes
    held.value = 1
    lyr.poll(1)
    held.value = 0
    taken = centre(3) + detents * SLOT
    assert lyr.settle() == taken and st.lyric_held == 1
    lyr.sec = 95
    assert lyr.settle() == taken  # held: the follow leaves them
    travel.value = (detents + 1) * tick  # anticlockwise: back up, a line a tick
    lyr.poll(1)
    assert lyr.settle() == taken - (detents + 1) * SLOT


# A scrub (Centre) on the lyrics page takes the wheel as ticks: the lyrics give it up, unmoved, and take it back when
# the scrub ends. They held it raw before, and their first turn ended the scrub.
def test_a_scrub_on_the_lyrics_page_has_the_wheel_until_it_ends(fw, ui, mcl, st, wheel):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    home = lyr.settle()
    raw, (travel, held) = fw.var('wheel_raw', C.c_int), wheel
    assert raw.value == 1
    st.scrub = 1
    lyr.poll(1)
    assert raw.value == 0
    travel.value = -O['LYRIC_PUSH_DETENTS'] * O['WHEEL_TICK_UNITS']
    lyr.poll(1)
    assert st.scrub == 1 and st.lyric_held == 0 and lyr.settle() == home
    st.scrub = 0
    lyr.poll(1)
    assert raw.value == 1


def test_a_small_turn_only_nudges_and_springs_back_still_following(fw, ui, mcl, st, wheel):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    home = lyr.settle()
    travel, held = wheel
    held.value = 1
    for _ in range(2):  # two small turns, together short of a push: the nudge keeps its home
        travel.value = -(O['LYRIC_PUSH_DETENTS'] * O['WHEEL_TICK_UNITS'] // 3)
        lyr.poll(1)
        assert lyr.settle() == home + O['BUMP_PX']
    assert fw.calls('reset_poweroptions_timer') and st.lyric_held == 0
    held.value = 0
    assert lyr.settle() == home
    lyr.sec = 55
    assert lyr.settle() == centre(5)


def test_the_stock_rows_scroll_a_stock_row_a_tick(fw, ui, mcl, st, wheel):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    ui.widget(lyr.lrc, 'row12', 0, 0, 225, 20)  # stock rebuilt its rows: they are no longer the page's
    lyr.scroll(100)
    lyr.poll(1)  # the page takes the wheel raw
    travel, held = wheel
    travel.value, held.value = -2 * O['WHEEL_TICK_UNITS'], 1
    lyr.poll(1)
    assert lyr.settle() == 100 + 2 * 20 and st.lyric_held == 1


def test_a_drag_holds_the_lyrics_and_a_tap_holds_nothing(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    at = lyr.settle()
    lyr.pressed = 1
    lyr.poll(1)
    lyr.pressed = 0
    lyr.poll(1)
    assert st.lyric_held == 0
    lyr.pressed = 1
    lyr.poll(1)
    lyr.scroll(at + 70)  # the finger moved them
    lyr.sec = 55
    assert lyr.poll(3) == [at + 70] * 3  # a finger on the screen: no follow
    lyr.pressed = 0
    lyr.poll(1)
    assert st.lyric_held == 1 and lyr.settle() == at + 70


def test_a_swipe_away_and_back_keeps_the_hold_and_a_new_song_starts_at_the_top(fw, ui, mcl, st, wheel):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    travel, _ = wheel
    travel.value = -100
    lyr.poll(1)
    held = lyr.settle()
    assert st.lyric_held == 1
    ui.ints[lyr.slide, b'value'] = 2  # the info page
    lyr.sec = 55  # the song goes on meanwhile
    lyr.poll(10, ms=250)
    assert st.lyric_held == 1 and fw.var('wheel_raw', C.c_int).value == 0
    ui.ints[lyr.slide, b'value'] = 1
    assert lyr.settle() == held != centre(5)  # where the wheel left them, not the playing line
    mcl.pos = 4
    lyr.poll(1)
    assert st.lyric_held == 0 and lyr.y() < centre(3)  # the next song's lyrics from the top


@pytest.mark.parametrize('end', ['gone', 'replaced'])
def test_now_playing_going_or_replaced_drops_the_lyrics_hold(fw, ui, mcl, st, wheel, end):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    travel, _ = wheel
    travel.value = -100
    lyr.poll(1)
    assert st.lyric_held == 1
    if end == 'gone':
        ui.emit(lyr.win, O['EVT_DESTROY'], 0)
    else:  # another Now Playing over this one, which stays: np_gone leaves a page it replaces alone
        Lyrics(fw, ui, mcl, st, sec=35)
    assert st.lyric_held == 0


def test_a_tick_before_the_wheel_goes_raw_glides_to_the_next_rows_top(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    at = lyr.settle()
    lyrics = fw.fn('navigation__np_lyrics', C.c_int, C.c_uint)
    tops = sorted(ui.box(r)[1] for r in lyr.rows)
    assert lyrics(KEY_NEXT) == 1 and st.lyric_held == 1
    assert lyr.settle() == min(t for t in tops if t > at)
    assert lyrics(KEY_PREV) == 1
    assert lyr.settle() == max(t for t in tops if t < min(t for t in tops if t > at))
    for _ in range(len(tops)):
        lyrics(KEY_NEXT)
    assert lyr.settle() == C.c_int.from_address(lyr.lrc + CONTENT_H).value - 178  # no row past it: the end
    for _ in range(len(tops) + 1):
        lyrics(KEY_PREV)
    assert lyr.settle() == 0
    ui.ints[lyr.slide, b'value'] = 0
    assert lyrics(KEY_NEXT) == 0


def test_a_late_pass_glides_half_the_way_no_leap(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    first = lyr.poll(1)[0]  # the glide starts: a pixel
    late = lyr.poll(1, ms=400)[0]
    assert late - first == (centre(3) - first) * 50 // 100  # dt capped at LYRIC_EASE_MS: 50 / (50 + 50)
    on_time = lyr.poll(1, ms=25)[0]
    assert on_time - late == (centre(3) - late) * 25 // 75


def tap(ui, w, kind, x, y):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, 0, kind)
    struct.pack_into('<ii', event, O['EVENT_X'], x, y)
    return ui.emit(w, kind, C.addressof(event))


def tap_line(ui, lyr, y):
    """A tap on the lyrics at y in scroll_lrc's rows (its content's own place), where the screen shows it."""
    at = ui.screen(lyr.lrc)[1] - lyr.y() + y
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, at)
    return tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, at)


def test_centreds_tap_on_the_lyrics_takes_them_full_screen_and_back(fw, ui, mcl, st):
    theme(fw, st, MINIMAL, NPCENTRED=1)  # Classic has no Centred layout
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    col, slide, at = ui.box(lyr.col), ui.box(lyr.slide), ui.kids[lyr.win].index(lyr.col)
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
    assert tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 108, 95) == [0]
    assert ui.box(lyr.col) == (0, col[1], 375, col[3]) and ui.box(lyr.slide)[2:] == (375, ui.box(lyr.dots)[1])
    assert ui.prop(lyr.col, b'style:normal:bg_color') == b'#000000' and st.np_full == 1
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
    tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)  # any tap: the column back, nothing plays
    assert lyr.seeks() == [] and ui.box(lyr.col) == col and ui.box(lyr.slide) == slide and st.np_full == 0
    assert ui.prop(lyr.col, b'style:normal:bg_color') == b'#00000000'
    restacks = [a[1] for _, a in fw.calls('widget_restack')]
    assert restacks == [len(ui.kids[lyr.win]), at]  # over all, then its own place again, under the bar


@pytest.mark.parametrize('end', ['far', 'drag', 'abort', 'page', 'reopen'])
def test_full_screen_lyrics_end_with_a_swipe_or_off_their_page(fw, ui, mcl, st, end):
    theme(fw, st, MINIMAL, NPCENTRED=1)  # Classic has no Centred layout
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
    tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)
    assert st.np_full == 1
    tap_px = NAV['TAP_PX']
    if end == 'far':  # a lift far off, with no move between, is no tap
        tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 300, 100)
        tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)
        assert st.np_full == 1 and lyr.seeks() == []
        return
    if end == 'drag':  # scroll_lrc drags the lines: its STOP comes back as an abort, and any drag ends full screen
        tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
        tap(ui, lyr.lyric, O['EVT_POINTER_ABORT'], 100, 100 - tap_px)
        tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)  # back where it came down: no tap
        assert lyr.seeks() == []
    elif end == 'abort':  # the slide_view's, which takes the touch past TAP_PX sideways
        tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
        tap(ui, lyr.lyric, O['EVT_POINTER_ABORT'], 100 + tap_px + 1, 100)
    elif end == 'page':
        ui.ints[lyr.slide, b'value'] = 0
        lyr.poll(1)
    else:
        Lyrics(fw, ui, mcl, st, sec=35)
    assert st.np_full == 0 and ui.box(lyr.col)[::2] == (80, 225)


def test_without_lyrics_a_tap_stays_in_the_column(fw, ui, mcl, st):
    theme(fw, st, MINIMAL, NPCENTRED=1)  # Classic has no Centred layout
    lyr = Lyrics(fw, ui, mcl, st, lyrics=())
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
    tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)
    assert st.np_full == 0 and fw.calls('widget_restack') == []


@pytest.mark.parametrize('t, centred, switch', [(CLASSIC, 1, 0), (MINIMAL, 0, 0), (MINIMAL, 0, 1)])
def test_the_ipod_pages_tap_takes_the_lyrics_over_the_whole_window_and_back(fw, ui, mcl, st, t, centred, switch):
    theme(fw, st, t, NPCENTRED=centred)  # Classic leaves the option undeclared: it reads 0
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    places = []  # widget_set_self_layout_params' x, y, w and h, as the C passes them
    fw.mock('widget_set_self_layout_params',
            lambda w, *a: places.append((w, *(C.string_at(s).decode() for s in a[:4]))))
    view, (_, _, win_w, win_h) = ui.parent(lyr.lrc), ui.box(lyr.win)
    col, slide, dots, lines = ui.box(lyr.col), ui.box(lyr.slide), ui.box(lyr.dots), ui.box(view)
    at = ui.kids[lyr.win].index(lyr.col)
    layout = fw.fn('np_layout', C.c_int)
    ipod = layout()
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
    assert tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 108, 95) == [0]
    assert ui.box(lyr.col) == (0, 0, win_w, win_h) and st.np_full == 2  # 2: the iPod page's, which the way back keeps
    assert ui.prop(lyr.col, b'style:normal:bg_color') == b'#000000'
    foot = ui.box(lyr.slide)[3]  # the pages down to the dots
    gap = win_h - foot
    assert ui.box(lyr.slide)[2] == win_w and gap == NAV['NP_FULL_FOOT']  # the dots that far above the window's foot
    # The dots at the pages' foot, their own height; the lines' view from the top, as high as its parent (the
    # pages) less the gap: the lines stop as far above the dots as the dots sit above the window's foot.
    assert places == [(lyr.dots, '0', str(foot), '100%', str(dots[3])), (view, '0', '0', '100%', str(-gap))]
    if switch:  # Centred switched on while full screen: it changes only the next Now Playing's page
        theme(fw, st, t, NPCENTRED=1)
        assert layout() != ipod
    lyr.scroll(0)
    tap_line(ui, lyr, ui.box(lyr.rows[0])[1] // 2)  # the blank slot over the first line: the column back
    assert ui.box(lyr.col) == col and ui.box(lyr.slide) == slide and st.np_full == 0
    assert ui.prop(lyr.col, b'style:normal:bg_color') == b'#00000000'
    assert places[2:] == [(lyr.dots, '0', str(dots[1]), '100%', str(dots[3])),
                          (view, '0', str(lines[1]), '100%', str(lines[3]))]  # both self_layouts their old place
    assert [a[1] for _, a in fw.calls('widget_restack')] == [len(ui.kids[lyr.win]), at]


# Smart Awake (keep_lit_poll): Now Playing off its art page, a song playing, holds the screen on.
@pytest.mark.parametrize('keep, page, status, top, lit', [(1, 1, 2, 'np', 1), (1, 0, 2, 'np', 0), (1, 0, 2, 'art', 1),
                                                          (1, 1, 3, 'np', 0), (0, 1, 2, 'np', 0), (1, 1, 2, 'other', 0)])
def test_smart_awake_holds_the_screen_on_off_the_art_page_while_playing(fw, ui, mcl, st, keep, page, status, top, lit):
    theme(fw, st, CLASSIC, KEEPLIT=keep)
    lyr = Lyrics(fw, ui, mcl, st, page=page, status=status)
    if top == 'art':
        ui.top = ui.widget(ui.wm, 'bigcover_page', kind='window')
        st.ref('bc', 2).value = ui.top
    elif top == 'other':
        ui.top = ui.widget(ui.wm, 'home_page', kind='window')
    poll = fw.fn('navigation__keep_lit_poll', None)
    ui.now += 5000
    poll()
    ui.now += 500
    poll()  # once a second
    assert len(fw.calls('reset_poweroptions_timer')) == lit
    assert fw.fn('ringnav_smart_awake', C.c_int)() == keep and st.np_slide == lyr.slide


# Classic's Spotify and Internet Radio Now Playing (stream_ipod): the local page's places, and the art's frame
# goes with the page that set it.
def stream(fw, ui, with_bar=True):
    page = ui.widget(ui.wm, 'radionp_page', 0, 0, 375, 320, kind='window')
    art = ui.widget(page, 'art', 0, 0, 166, 166)
    texts = (C.c_void_p * 4)(*[ui.widget(page, f't{i}') for i in range(4)])
    elapsed, remain = ui.widget(page, 'elapsed'), ui.widget(page, 'remain')
    bar = ui.widget(page, 'bar') if with_bar else None
    ipod = fw.fn('stream_ipod', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)
    return ipod(art, C.addressof(texts), elapsed, remain, bar, None), art, texts, elapsed, remain, bar


def test_classics_stream_page_takes_the_local_pages_places(fw, ui, st):
    theme(fw, st, CLASSIC)
    ok, art, texts, elapsed, remain, bar = stream(fw, ui, with_bar=False)
    assert ok == 1 and st.stream_art == art and st.stream_bar
    bar = st.stream_bar
    bx, by, bw, bh = ui.box(bar)
    assert bx + bw == 375 - bx and bh == O['NP_BAR_IPOD'] + O['SCRUB_REFLECT_ROWS']
    ex, ey, ew, eh = ui.box(elapsed)
    rx, ry, rw, rh = ui.box(remain)
    gap = O['NP_IPOD_TIME_GAP']
    assert ex + ew + gap == bx and rx == bx + bw + gap and ey == ry and ew == rw
    assert [ui.box(t)[0] for t in texts] == [O['NP_IPOD_TEXT_X']] * 4
    ys = [ui.box(t)[1] for t in texts]
    assert ys == sorted(ys) and len(set(ys)) == 4
    ax, ay, aw, ah = ui.box(art)
    assert aw == O['NP_IPOD_ART'] < ah  # taller than wide: the reflection under it
    assert ui.prop(elapsed, b'style:normal:font_name') == b'classic-light'


def test_another_theme_keeps_the_stream_pages_own_layout(fw, ui, st):
    theme(fw, st, MINIMAL)
    ok, art, *_ = stream(fw, ui)
    assert ok == 0 and ui.box(art) == (0, 0, 166, 166) and ui.handlers == []


def test_the_stream_arts_frame_goes_with_its_own_page_only(fw, ui, st):
    theme(fw, st, CLASSIC)
    _, old, *_ = stream(fw, ui)
    _, new, *_ = stream(fw, ui)
    ui.emit(old, O['EVT_DESTROY'])
    assert st.stream_art == new
    ui.emit(new, O['EVT_DESTROY'])
    assert st.stream_art == 0 and st.stream_bar == 0


# The full-screen art (ringnav_bigcover): the song name and its shade fade out, the art takes its cover's shape.
class Big:
    def __init__(self, fw, ui, mcl, st, image=b'default_bigcover', sizes=None):
        self.fw, self.ui, self.st = fw, ui, st
        self.now = Now(fw, ui, mcl, st)
        self.opacity = {}
        fw.mock('widget_set_opacity', lambda w, v, *_: self.opacity.setdefault(w, []).append(v))
        fw.mock('stock_bigcover_trampoline', lambda *_: 3)
        fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))
        self.canvas = Canvas(fw, sizes=sizes)  # widget_load_image's answers
        self.win = ui.widget(ui.wm, 'bigcover_page', 0, 0, 375, 320, kind='window')
        self.img = ui.widget(self.win, 'img_bigcover', 27, 0, 320, 320)
        self.shade = ui.widget(self.win, 'view_shade', 27, 260, 320, 60)
        self.title = ui.widget(self.win, 'scrlabel_title', 39, 280, 296, 30)
        ui.set_str(self.img, b'image', image)
        assert fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0) == 3

    def fit(self):
        self.fw.fn('navigation__bc_fit', None, C.c_void_p)(self.win)
        return self.ui.box(self.img)


def test_the_song_name_and_its_shade_show_then_fade_out_together(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    big = Big(fw, ui, mcl, st)
    first, = ui.due('bc_fade')
    assert ui.timers[first][2] == O['BC_MS']
    ui.run(first)
    assert [ui.timers[t][2] for t in ui.due('bc_fade')] == [O['BC_STEP_MS']]  # then a step each BC_STEP_MS
    ui.run_all('bc_fade')
    steps = O['BC_STEPS']
    want = [255] + [255 * (steps - n) // steps for n in range(1, steps + 1)]
    assert big.opacity[big.title] == big.opacity[big.shade] == want and st.bc_timer == 0


def test_a_new_track_shows_the_name_again_and_the_page_going_stops_it(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    big = Big(fw, ui, mcl, st)
    ui.run_all('bc_fade')
    fw.fn('navigation__bc_show', None)()
    assert big.opacity[big.title][-1] == 255 and len(ui.due('bc_fade')) == 1
    st.bc_repaint = ui.timer_add(0, 0, 0)
    ui.emit(big.win, O['EVT_DESTROY'], 0)
    assert ui.due('bc_fade') == [] and st.bc_repaint == 0 and st.ref('bc', 2).value is None


def clears_glass(x, y, w, h):
    """No corner of the box past the glass's rounded corner, GLASS_R round, GLASS_SLACK in from the side."""
    r, slack = O['GLASS_R'], O['GLASS_SLACK']
    dx, dy = r + slack - x, r - y
    return h <= 320 and not (dx > 0 and dy > 0 and dx * dx + dy * dy > r * r)


@pytest.mark.parametrize('size', [(400, 200), (200, 400), (500, 500), (300, 299)])
def test_the_art_is_the_largest_box_of_its_covers_shape_that_clears_the_glass(fw, ui, mcl, st, size):
    theme(fw, st, CLASSIC)
    big = Big(fw, ui, mcl, st, image=b'cover.jpg', sizes={b'cover.jpg': size})
    x, y, w, h = big.fit()
    assert x == (375 - w) // 2 and y == (320 - h) // 2 and clears_glass(x, y, w, h)
    long_, short = max(w, h), min(w, h)
    assert abs(short / long_ - min(size) / max(size)) < 0.02 and (w >= h) == (size[0] >= size[1])
    s = long_ + 2  # the next size up would not clear it
    w2, h2 = (s, s * size[1] // size[0]) if size[0] >= size[1] else (s * size[0] // size[1], s)
    assert long_ == 374 or not clears_glass((375 - w2) // 2, (320 - h2) // 2, w2, h2)
    sx, sy, sw, sh = ui.box(big.shade)
    assert (sx, sy + sh, sw) == (x, y + h, w)  # on the art's foot
    tx, _, tw, _ = ui.box(big.title)
    assert tx == x + O['BC_TITLE_PAD'] and tw == w - 2 * O['BC_TITLE_PAD']
    assert [C.string_at(a[1]) for _, a in fw.calls('image_manager_unload_bitmap_by_name')] == [b'cover.jpg']
    repaint, = ui.due('bc_repaint')  # after the paint, which drops its own invalidates
    ui.run(repaint)
    assert ui.invalidated(big.win) == [big.win] and st.bc_repaint == 0


@pytest.mark.parametrize('image, sizes', [(b'default_bigcover', {}), (b'cover.jpg', {b'cover.jpg': (500, 100)}),
                                          (b'cover.jpg', {b'cover.jpg': None})])
def test_a_placeholder_a_cover_past_4_to_1_or_one_that_wont_load_is_square(fw, ui, mcl, st, image, sizes):
    theme(fw, st, CLASSIC)
    big = Big(fw, ui, mcl, st, image=image, sizes=sizes)
    x, y, w, h = big.fit()
    assert w == h and clears_glass(x, y, w, h)


def test_the_box_is_fitted_once_until_the_track_or_image_changes(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    big = Big(fw, ui, mcl, st, image=b'cover.jpg', sizes={b'cover.jpg': (400, 200)})
    big.fit()
    loads, moves = len(big.canvas.loaded), len(fw.calls('widget_move_resize'))
    big.fit()
    assert len(big.canvas.loaded) == loads and len(fw.calls('widget_move_resize')) == moves
    big.ui.set_str(big.img, b'image', b'other.jpg')
    big.fit()
    assert big.canvas.loaded[-1] == b'other.jpg' and ui.box(big.img)[2] == ui.box(big.img)[3]


def test_the_players_file_waits_until_it_is_this_tracks_cover(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    big = Big(fw, ui, mcl, st, image=b'file:///tmp/coverpic.jpg', sizes={b'file:///tmp/coverpic.jpg': (400, 200)})
    x, y, w, h = big.fit()
    assert big.canvas.loaded == [] and w == h  # the last track's file: not decoded, the square
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = big.now.paths[2].value
    fw.var('g_playcover_type', C.c_ubyte).value = 1
    x, y, w, h = big.fit()
    assert big.canvas.loaded == [b'file:///tmp/coverpic.jpg'] and w > h


# A card app's URL queue (np_art's test): a paint between the song change and its cover's request decodes the last
# song's file, still in place; the next song's file, written after, is decoded once it is in.
@in_fork
def test_a_url_queues_next_cover_is_fitted_once_it_is_in(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    ext = b'file:///tmp/externpic.jpg'
    sizes = {ext: (400, 200)}
    big = Big(fw, ui, mcl, st, image=ext, sizes=sizes)
    for path in big.now.paths:
        path.value = b''
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = b''
    kind, pic = fw.var('g_playcover_type', C.c_ubyte), Externpic(fw)
    ask_cover(fw)
    pic.write()
    kind.value = 2
    x, y, w, h = big.fit()
    assert w > h
    mcl.pos = 3
    x, y, w, h = big.fit()
    assert w > h and len(big.canvas.loaded) == 2  # the last song's file, the next one's not asked for yet
    ask_cover(fw)
    sizes[ext] = (200, 400)
    pic.write()
    kind.value = 2
    x, y, w, h = big.fit()
    assert h > w and len(big.canvas.loaded) == 3


# The geometry sets' boot (theme_boot): tools/ipod.py bakes SET0 into stock's row immediates; another set
# rewrites the low halfwords that differ, every page RWX first, then written, flushed and back to R-X.
SITES = [(int(a, 16), g['value']) for g in host.tools_theme.AUDIT['immediates'] if isinstance(g['value'], str)
         for a, _ in g['sites']]
PAGE = 4096
RWX, RX = 7, 5


def set_values(g):
    return host.tools_theme.immediate_values(SETS[g])


@pytest.fixture
def code():
    """stock's code words at the boot sites, which conftest's map keeps past a test: put back after."""
    saved = [(a, C.c_uint.from_address(a).value) for a, _ in SITES]
    yield {a: C.c_uint.from_address(a) for a, _ in SITES}
    for a, v in saved:
        C.c_uint.from_address(a).value = v


def baked(code, g, high=0x5a5a0000):
    """Every site as the build left it for set g: its value in the low half, an instruction's high half."""
    values = set_values(g)
    for a, name in SITES:
        code[a].value = high | values[name]


def boot(fw, fail_at=None):
    calls = []
    def mprotect(addr, size, prot, *_):
        calls.append(('mprotect', addr, size, prot))
        return -1 if (addr, prot) == (fail_at, RWX) else 0
    fw.mock('theme_mprotect', mprotect)
    fw.mock('theme_cacheflush', lambda addr, size, cache, *_: calls.append(('flush', addr, size, cache)))
    fw.fn('navigation__theme_boot', None)()
    return calls


def test_classics_set_rewrites_the_low_halves_page_by_page(fw, st, code):
    baked(code, 0)
    theme(fw, st, CLASSIC)
    st.geom = 0
    g = GEOM['Classic']
    values = set_values(g)
    differ = sorted({a & ~(PAGE - 1) for a, name in SITES if values[name] != set_values(0)[name]})
    assert len(differ) > 1  # sites to rewrite on more than one page, or page by page proves nothing
    calls = boot(fw)
    assert calls == [('mprotect', p, PAGE, RWX) for p in differ] + \
        [c for p in differ for c in (('flush', p, PAGE, 3), ('mprotect', p, PAGE, RX))]
    assert all(code[a].value == 0x5a5a0000 | values[name] for a, name in SITES)
    assert st.geom == g and st.theme == 1


def test_a_refused_page_puts_back_those_opened_and_writes_nothing(fw, st, code):
    baked(code, 0)
    theme(fw, st, CLASSIC)
    st.geom = 0
    values = set_values(GEOM['Classic'])
    differ = sorted({a & ~(PAGE - 1) for a, name in SITES if values[name] != set_values(0)[name]})
    calls = boot(fw, fail_at=differ[-1])
    assert calls == [('mprotect', p, PAGE, RWX) for p in differ] + [('mprotect', p, PAGE, RX) for p in differ[:-1]]
    assert all(code[a].value == 0x5a5a0000 | set_values(0)[name] for a, name in SITES)
    assert st.geom == 0 and st.theme == 0  # Minimal's set is what the code holds: Minimal until the next boot


@pytest.mark.parametrize('t', [MINIMAL, MODERN, CLASSIC], ids=['Minimal', 'Modern', 'Classic'])
def test_the_set_the_code_holds_already_makes_no_syscall(fw, st, code, t):
    baked(code, GEOM[t['name']])
    theme(fw, st, t)
    st.geom = 0
    assert boot(fw) == [] and st.geom == GEOM[t['name']]


def test_only_the_pages_with_a_stale_site_open(fw, st, code):
    g = GEOM['Classic']
    baked(code, g)
    a, name = next((a, n) for a, n in SITES if set_values(g)[n] != set_values(0)[n])
    code[a].value = 0x5a5a0000 | set_values(0)[name]
    theme(fw, st, CLASSIC)
    calls = boot(fw)
    page = a & ~(PAGE - 1)
    assert calls == [('mprotect', page, PAGE, RWX), ('flush', page, PAGE, 3), ('mprotect', page, PAGE, RX)]
    assert code[a].value == 0x5a5a0000 | set_values(g)[name]


@pytest.mark.parametrize('rowart, label', [(1, 'On'), (0, 'Off')])
def test_row_art_picks_classics_set(fw, st, rowart, label):
    theme(fw, st, CLASSIC, ROWART=rowart)
    T = fw.fn('theme', C.c_void_p)()
    want = SETS.index(host.tools_theme.geometry(CLASSIC, {'ROWART': label}))
    assert fw.fn('theme_geom', C.c_int, C.c_void_p)(T) == want
    geom_with = fw.fn('navigation__geom_with', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int)
    assert geom_with(T, -1, 0, 1) == SETS.index(host.tools_theme.geometry(CLASSIC, {'ROWART': 'On'}))  # a switch presets On


@pytest.mark.parametrize('t', [MINIMAL, MODERN], ids=['Minimal', 'Modern'])
def test_a_theme_without_a_set_option_boots_its_own(fw, st, t):
    theme(fw, st, t, ROWART=0)
    T = fw.fn('theme', C.c_void_p)()
    assert fw.fn('theme_geom', C.c_int, C.c_void_p)(T) == GEOM[t['name']] == SETS.index(host.tools_theme.geometry(t))


# A-B markers on Classic's bar fitted to the track (ringnav_ab_x): the same share of it.
def test_an_ab_marker_keeps_its_share_of_a_fitted_bar(fw, ui, mcl, st):
    theme(fw, st, CLASSIC)
    values = set_values(GEOM['Classic'])
    x0, w0 = values['np_bar_x'], values['np_bar_w']
    stock = {}
    fw.mock('ab_x', lambda t, *_: stock[t])
    ab_x = fw.fn('ringnav_ab_x', C.c_int, C.c_int)
    stock.update({1: x0 + w0 // 2, 2: 0, 3: x0})
    assert ab_x(1) == x0 + w0 // 2  # no Now Playing: stock's
    now = Now(fw, ui, mcl, st, sec=10, total=4000)  # a long track: the bar narrows
    x, _, w, _ = ui.box(now.slider)
    assert ab_x(1) == x + (w0 // 2) * w // w0 and ab_x(3) == x
    assert ab_x(2) == 0  # no point to mark


# Classic's own pages as stock's windows load (ringnav_ui_load): the slider pages, T9, New Playlist and Rename.
def load(fw, ui, root):
    fw.mock('stock_ui_load_trampoline', lambda name, parent, *_: root)
    name = ui.prop(root, b'name')
    assert fw.fn('ringnav_ui_load', C.c_void_p, C.c_char_p, C.c_void_p)(name, ui.wm) == root
    return root


SLIDERS = {'backlight_page': 'slider_backlight', 'maxvol_page': 'slider_maxvol', 'balance_page': 'slider_balance',
           'bootvol_page': 'slider_bootvol'}
STOCK_HIDDEN = ['img_dec', 'img_add', 'img_left', 'img_right', 'img_volleft', 'img_volright', 'label_backlight', 'label_vol']


class SliderPage:
    """One of stock's four slider windows: its slider in a view with the +/- and labels around it."""
    def __init__(self, fw, ui, name):
        self.win = window(ui, name)
        self.view = ui.widget(self.win, 'view_slider', 20, 80, 335, 120)
        self.slider = ui.widget(self.view, SLIDERS[name], 60, 30, 215, 40)
        self.hidden = [ui.widget(self.view, n, 0, 0, 30, 30) for n in STOCK_HIDDEN]
        if name == 'balance_page':
            self.number = ui.widget(self.win, 'label_balance', 150, 60, 75, 30, text='0')
        if name == 'bootvol_page':
            self.navbar = ui.widget(self.win, 'view_navbar', 0, 0, 375, 60)
            self.switch = ui.widget(self.navbar, 'img_bootswitch', 300, 10, 60, 40)
        self.stock = ui.box(self.slider)
        load(fw, ui, self.win)

    def caption(self, ui):
        return ui.lookup(self.win, 'caption', 1)

    def hit(self, ui):
        return ui.lookup(self.win, 'switch_hit', 1)


def switch(fw, page, canvas):
    fw.fn('slider_switch', None, C.c_void_p, C.c_void_p, C.c_void_p)(page.win, canvas.p, page.switch)


@pytest.mark.parametrize('name', list(SLIDERS))
def test_a_slider_page_takes_classics_shape_and_every_stock_name_still_finds_its_widget(fw, st, ui, name):
    theme(fw, st, CLASSIC)
    page = SliderPage(fw, ui, name)
    assert all(ui.shown[w] == 0 for w in page.hidden) and ui.lookup(page.win, 'img_dec', 1) == page.hidden[0]
    view = ui.box(page.view)
    assert view == (0, 0, 375, 320 - SETS[GEOM['Classic']].bar)  # the whole page under the bar
    slider, caption = ui.box(page.slider), page.caption(ui)
    assert within(slider, view) and within(ui.box(caption), view)
    assert slider[1] + slider[3] <= ui.box(caption)[1] and ui.box(caption)[::2] == (0, 375)  # under it, centred across
    assert {ui.ints[page.slider, f'style:{s}:icon'.encode()] for s in ('normal', 'pressed', 'over', 'disable', 'focused')} == {0}
    assert ui.text(caption) == read_wide(C.addressof(fw.var('navigation__slider_caption', C.c_uint)))
    assert ui.prop(caption, b'style:normal:font_name') == b'classic-light'
    assert (st.slider_win, st.slider_w, st.slider_kind) == (page.win, page.slider, list(SLIDERS).index(name))


def test_off_classic_a_slider_page_stays_stocks(fw, st, ui):
    theme(fw, st, MINIMAL)
    page = SliderPage(fw, ui, 'backlight_page')
    assert ui.box(page.slider) == page.stock and st.slider_win == 0 and page.caption(ui) == 0
    assert all(ui.shown.get(w, 1) for w in page.hidden)


def test_max_volumes_slider_sits_higher_and_its_triangle_repaints_past_it(fw, st, ui):
    theme(fw, st, CLASSIC)
    other = SliderPage(fw, ui, 'backlight_page')
    assert ui.emit(other.slider, O['EVT_VALUE_CHANGING']) == []  # no triangle: the slider repaints itself
    page = SliderPage(fw, ui, 'maxvol_page')
    x, y, w, h = ui.box(page.slider)
    ox, oy, ow, oh = ui.box(other.slider)
    assert y < oy and (x, w, h) == (ox, ow, oh)  # the same slider, higher: its triangle under it
    ui.dirty.clear()
    for evt in (O['EVT_VALUE_CHANGING'], O['EVT_VALUE_CHANGED']):
        assert ui.emit(page.slider, evt) == [0]
    (w1, band), (w2, again) = ui.dirty_rects()
    assert w1 == w2 == page.win and band == again
    bx, by, bw, bh = band
    assert bx < x and x + w < bx + bw and y <= by and by + bh <= y + h  # past the slider's ends, on its rows
    assert within(band, ui.box(page.view))  # and still the page's


def test_balance_shows_its_number_and_l_and_r(fw, st, ui):
    theme(fw, st, CLASSIC)
    page = SliderPage(fw, ui, 'balance_page')
    nx, ny, nw, nh = ui.box(page.number)
    sx, sy, sw, sh = ui.box(page.slider)
    (lx, ly, lw, lh), (rx, ry, rw, rh) = [ui.box(w) for t in 'LR' for w in ui.kids[page.win] if ui.text(w) == t]
    assert lx + lw <= sx and sx + sw <= rx  # either side of the slider
    assert (ly, lw, lh) == (ry, rw, rh) and abs(2 * lx + lw + 2 * rx + rw - 2 * 375) <= 2  # mirrored about the middle
    assert sy <= ly and ly + lh <= sy + sh  # on the slider's rows
    assert (nx, nw) == (0, 375) and ny + nh <= ly  # centred across the screen, over the bar and its ends
    assert ui.prop(page.number, b'style:normal:font_name') == b'classic-light'
    ui.set_text(page.number, CJK)
    fw.fn('slider_number', None, C.c_void_p)(page.number)
    assert ui.prop(page.number, b'style:normal:font_name') == b'default'  # a face that has the text


def test_default_volumes_tray_tap_reaches_stocks_switch(fw, st, ui):
    theme(fw, st, CLASSIC)
    dispatched = []
    fw.mock('pointer_event_init', lambda e, kind, target, x, y, *_: struct.pack_into('<i', (C.c_char * 4).from_address(e), 0, kind) or e)
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: dispatched.append((w, C.c_int.from_address(e).value)))
    page = SliderPage(fw, ui, 'bootvol_page')
    assert ui.shown[page.navbar] == 0
    hit = page.hit(ui)
    assert ui.prop(hit, b'clickable') == b'true'
    canvas = Canvas(fw)
    switch(fw, page, canvas)
    painted = [r[:4] for r in canvas.rects] + [t[1] for t in canvas.texts]
    assert painted and all(within(r, ui.box(hit)) for r in painted)  # the tap covers the tray as it paints
    ui.emit(hit, O['EVT_CLICK'])
    assert dispatched == [(page.switch, O['EVT_CLICK'])] and page.win in ui.invalidated()
    ui.shown[page.switch] = 0  # Crossfade's page hides it
    ui.emit(hit, O['EVT_CLICK'])
    assert len(dispatched) == 1


def test_the_tray_and_hint_are_the_slider_pages_own(fw, st, ui):
    theme(fw, st, CLASSIC)
    page = SliderPage(fw, ui, 'bootvol_page')
    other = window(ui, 'crossfade_page')
    dirty = fw.fn('slider_tray_dirty', None, C.c_void_p)
    ui.dirty.clear()
    dirty(other)
    dirty(page.win)
    assert ui.dirty_rects() == [(page.win, ui.box(page.hit(ui)))]  # where the tray is
    opening = ui.text(page.caption(ui))
    hint = fw.fn('slider_centre_hint', None, C.c_void_p, C.c_int)
    hint(page.win, 1)
    night = ui.text(page.caption(ui))
    hint(page.win, 0)
    day = ui.text(page.caption(ui))
    assert night and day and len({opening, night, day}) == 3  # what Centre does there, Night Shift's and Crossfade's
    ui.emit(page.win, O['EVT_DESTROY'], 0)
    hint(page.win, 1)
    assert st.slider_win == 0 and ui.text(page.caption(ui)) == day  # gone: the hint no longer its


@pytest.mark.parametrize('value, part', [(0, 0), (50, 102), (100, 204), (25, 51)])
def test_the_sliders_part_is_its_share_of_the_width(fw, ui, value, part):
    s = ui.widget(ui.wm, 'slider')
    ui.ints[s, b'value'] = value
    assert fw.fn('slider_part', C.c_int, C.c_void_p, C.c_int)(s, 204) == part
    ui.ints[s, b'min'] = ui.ints[s, b'max'] = 5
    assert fw.fn('slider_part', C.c_int, C.c_void_p, C.c_int)(s, 204) == 0  # no range


@pytest.mark.parametrize('image, on', [(b'switch_on', True), (b'switch_off', False), (b'', False)])
def test_the_switch_lights_its_value_s_cell(fw, st, ui, image, on):
    theme(fw, st, CLASSIC)
    page = SliderPage(fw, ui, 'bootvol_page')
    ui.set_str(page.switch, b'image', image)
    canvas = Canvas(fw)
    switch(fw, page, canvas)
    off_cell, on_cell = [t[1] for t in canvas.texts]  # Off's label, then On's, each centred in its cell
    assert off_cell[0] + off_cell[2] <= on_cell[0] and off_cell[1] == on_cell[1]  # side by side, On on the right
    assert within(off_cell, ui.box(page.hit(ui))) and within(on_cell, ui.box(page.hit(ui)))  # in the tray
    def lit(cell):  # the selection's rows, the cell's own width across
        x, y, w, h = cell
        return any(r[0] == x and r[2] == w and r[3] == 1 and y <= r[1] < y + h for r in canvas.rects)
    assert (lit(off_cell), lit(on_cell)) == (not on, on)
    (_, (ex, _, _, _)), = [i for i in canvas.images if i[0] == b'classic_light_lit_edge']
    assert abs(ex - (on_cell if on else off_cell)[0]) <= 1  # its lit edge with it
    assert len({t[3] for t in canvas.texts}) == 1  # both labels in one ink


@pytest.mark.parametrize('light', [0, 1])
def test_a_page_is_flat_in_its_halfs_home_pane_colour(fw, st, light):
    theme(fw, st, CLASSIC, light)
    canvas = Canvas(fw)
    bar = SETS[GEOM['Classic']].bar
    fw.fn('paint_page', None, C.c_void_p, C.c_void_p, C.c_int)(0, canvas.p, bar)
    (x, y, w, h, color), = canvas.rects
    assert (x, y, w, y + h) == (0, bar, 375, 320)  # under the bar to the screen's foot
    assert (color == rgba(CLASSIC['home_pane'][0][1])) == (not light)


def test_final_text_is_never_flipped_and_leaves_the_ink_as_it_found_it(fw, st):
    theme(fw, st, CLASSIC, 1)
    canvas = Canvas(fw)
    text = wide('Hi')
    rect = (C.c_int * 4)(0, 0, 100, 30)
    fw.fn('final_text', None, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_uint, C.c_uint)(
        canvas.p, b'classic-light', C.addressof(text), rect, 20, 0x80000000)
    assert [(t[0], t[3]) for t in canvas.texts] == [('Hi', 0x80000000)] and st.raw_ink == 0
    empty = wide('')
    fw.fn('final_text', None, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_uint, C.c_uint)(
        canvas.p, None, C.addressof(empty), rect, 20, 0xff000000)
    assert len(canvas.texts) == 1


def test_a_labels_final_ink_is_rewritten_when_the_half_changes(fw, st, ui):
    theme(fw, st, CLASSIC, 1)
    win = window(ui, 'page')
    label = ui.widget(win, 'label')
    button = ui.widget(win, 'button')
    fw.fn('text_ink', None, C.c_void_p, C.c_uint, C.c_int)(label, 0xffffffff, 1)
    fw.fn('text_ink', None, C.c_void_p, C.c_uint, C.c_int)(button, 0xccffffff, 5)
    assert ui.ints[label, b'style:normal:text_color'] & 0xffffffff == rgba(0)  # the light half's style hook flips it back
    assert (label, b'style:pressed:text_color') not in ui.ints and ui.ints[button, b'style:focused:text_color'] & 0xffffffff == 0xcc000000
    theme(fw, st, CLASSIC, 0)
    fw.fn('text_inks', None, C.c_void_p)(ui.wm)
    assert ui.ints[label, b'style:normal:text_color'] & 0xffffffff == 0xffffffff
    assert ui.ints[button, b'style:over:text_color'] & 0xffffffff == 0xccffffff


def test_the_info_screens_tray_stays_in_its_ring_and_puts_the_fill_back(fw, st):
    theme(fw, st, CLASSIC)
    canvas = Canvas(fw)
    tray = fw.fn('paint_tray', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)
    tray(canvas.p, 16, 44, 343, 116)
    xs = [r[0] for r in canvas.rects] + [r[0] + r[2] for r in canvas.rects]
    ys = [r[1] for r in canvas.rects]
    assert (min(xs), max(xs), min(ys), max(ys)) == (15, 16 + 343 + 1, 43, 44 + 116 + 1) and canvas.fill == 0xff123456
    canvas = Canvas(fw)
    struct.pack_into('<Q', canvas.raw, CANVAS_LCD, 0)
    tray(canvas.p, 16, 44, 343, 116)
    assert canvas.rects == []


@pytest.mark.parametrize('when, minutes', [((14, 35), 14 * 60 + 35), ((0, 0), 0), (None, -1)])
def test_clock_minutes_is_minutes_since_midnight(fw, when, minutes):
    def local(t, out, *_):
        C.c_int.from_address(out + 4).value, C.c_int.from_address(out + 8).value = when[1], when[0]
        return out
    fw.mock('fw_time', lambda *_: -1 if when is None else 1700000000)
    fw.mock('fw_localtime_r', local)
    assert fw.fn('clock_minutes', C.c_int)() == minutes


# Classic's T9 keyboard and Search's field.
def keyboard(fw, ui):
    kb = window(ui, 'kb_default_t9')
    cand = ui.widget(kb, 'candidates', 0, 0, 375, 30)
    pages = ui.widget(kb, 'pages', 0, 60, 375, 260)
    page = ui.widget(pages, 'page', 0, 0, 375, 260)
    row = ui.widget(page, 'row', 0, 0, 375, 60)
    load(fw, ui, kb)
    return kb, cand, row


def search(fw, ui):
    win = window(ui, 'search_dialog')
    nav = ui.widget(win, 'view_navbar', 0, 0, 375, 60)
    control = ui.widget(win, 'tab_control', 0, 100, 375, 220)
    group = ui.widget(control, 'tab_button_group', 0, 0, 375, 30)
    ui.widget(control, 'pages', 0, 30, 375, 190)
    item = ui.widget(win, 'list_item_search', 0, 60, 375, 40)
    edit = ui.widget(item, 'search_edit', 40, 0, 300, 40)
    glass = ui.widget(item, 'img_search', 0, 0, 40, 40)
    load(fw, ui, win)
    return win, nav, item, edit, glass, group


# The keyboard's art and the field's image at the sizes tools/art.py draws them.
ART = {b'classic_light_' + name.encode(): size for name, (size, _) in tools_art.KB_GLYPHS.items()}
ART[b'classic_light_field'] = (tools_art.FIELD_W, tools_art.FIELD_H)
EMPTY_PX, TYPED_PX = b'style:empty:font_size', b'style:normal:font_size'


def test_the_keyboard_coming_up_moves_searchs_field_over_it_and_back(fw, st, ui):
    theme(fw, st, CLASSIC)
    win, nav, item, edit, glass, group = search(fw, ui)
    down, down_hint = ui.box(item), ui.ints[edit, EMPTY_PX]
    x, _, w, h = down
    assert x + w == 375 - x and ui.shown[nav] == 0  # centred in the strip, stock's toolbar hidden
    assert ui.box(edit) == (0, 0, w, h) and ui.box(glass)[::3] == (0, h)  # the glass at the field's left end
    assert ui.ints[edit, b'style:normal:margin_left'] >= ui.box(glass)[2]  # the text starts past it
    kb, cand, row = keyboard(fw, ui)
    assert {ui.ints[cand, f'style:{s}:bg_color'.encode()] for s in ('normal', 'disable', 'focused')} == {0}
    up = ui.box(item)
    assert up[2] < w and up[1] < down[1] and ui.shown[nav] == 1  # narrower and higher, over the keyboard
    assert ui.box(edit) == (0, 0, up[2], up[3]) and ui.ints[edit, EMPTY_PX] < down_hint  # the hint smaller to fit
    ui.emit(kb, O['EVT_DESTROY'], 0)
    assert st.kb_win == 0 and ui.box(item) == down and ui.shown[nav] == 0 and ui.ints[edit, EMPTY_PX] == down_hint
    ui.emit(win, O['EVT_DESTROY'], 0)
    assert st.search_win == 0
    keyboard(fw, ui)  # no Search under it: nothing to move
    assert ui.box(item) == down


def test_searchs_hint_size_follows_the_field_and_is_written_once(fw, st, ui):
    theme(fw, st, CLASSIC)
    win, nav, item, edit, glass, group = search(fw, ui)
    hint = fw.fn('classic_pages__search_hint', None, C.c_void_p)
    def writes():
        return len([1 for w, key, _ in ui.int_log if w == edit and key == b'style:disable:font_size'])
    opened, sizes = writes(), []
    for text in ('', '', 'Coltrane'):
        ui.set_text(edit, text)
        hint(win)
        sizes.append(ui.ints[edit, b'style:disable:font_size'])
    # stock's edit is disabled, so it paints its hint and query in that state: the hint's size, then the query's
    assert sizes == [ui.ints[edit, EMPTY_PX]] * 2 + [ui.ints[edit, TYPED_PX]] and sizes[0] < sizes[-1]
    assert writes() == opened + 2  # a change each, the same size again none
    keyboard(fw, ui)
    ui.set_text(edit, '')
    hint(win)
    assert ui.ints[edit, b'style:disable:font_size'] == ui.ints[edit, EMPTY_PX] < sizes[0]  # the keyboard's smaller hint


def test_a_keyboard_button_is_four_levels_under_its_window(fw, st, ui):
    theme(fw, st, CLASSIC)
    kb, cand, row = keyboard(fw, ui)
    key = ui.widget(row, 'a')
    is_key = fw.fn('kb_key', C.c_int, C.c_void_p)
    assert (is_key(key), is_key(row), is_key(cand), is_key(None)) == (1, 0, 0, 0)
    ui.emit(kb, O['EVT_DESTROY'], 0)
    assert is_key(key) == 0


def paint_key(fw, ui, row, name, text=None, w=60, h=50):
    key = ui.widget(row, name, 0, 0, w, h, text=text)
    canvas = Canvas(fw, sizes=ART)
    fw.fn('kb_paint_key', None, C.c_void_p, C.c_void_p)(key, canvas.p)
    assert canvas.fill == 0xff123456
    return canvas


def mid_row(canvas, w):
    return [r[4] for r in canvas.rects if r[2] == w and r[3] == 1 and r[0] == 0 and r[1] == 25][-1]


def channels(c):
    return c & 255, c >> 8 & 255, c >> 16 & 255


def label(canvas):
    """A key's label as drawn twice, its shadow then its ink: (text, shadow's offset down, shadow lighter, px)."""
    (text, shadow, _, under, _), (again, rect, (_, px), ink, _) = canvas.texts
    assert text == again and shadow[::2] == rect[::2]
    return text, shadow[1] - rect[1], lum(under) > lum(ink), px


def test_the_keys_faces_and_labels(fw, st, ui):
    theme(fw, st, CLASSIC)
    kb, cand, row = keyboard(fw, ui)
    plain, done, space, fn = (paint_key(fw, ui, row, n, t) for n, t in (('2', 'abc'), ('action', None), ('space', None),
                                                                          ('page:123', '123')))
    text, down, lighter, char_px = label(plain)
    assert (text, down > 0, lighter) == ('abc', True, True)  # dark ink on a light shadow a row under it
    for key in (done, fn):  # white ink, the shadow dark and a row over it, in a smaller size than a character's
        text, down, lighter, px = label(key)
        assert down < 0 and not lighter and px < char_px
    assert label(fn)[0] == '123'
    assert label(done)[0] and label(space)[0] and label(done)[0] != label(space)[0]  # their own words, no text of theirs
    assert label(space)[1] > 0 and label(space)[2]  # space's ink is dark, as a character's
    r, g, b = channels(mid_row(done, 60))
    assert b > r + 60  # the enter key's blue
    pr, pg, pb = channels(mid_row(plain, 60))
    assert min(pr, pg, pb) > 200 and max(pr, pg, pb) - min(pr, pg, pb) < 8  # near white
    fr, fg, fb = channels(mid_row(fn, 60))
    assert max(fr, fg, fb) < min(pr, pg, pb) and fb >= fr  # a darker slate


@pytest.mark.parametrize('w, h', [(60, 50), (61, 47)])
@pytest.mark.parametrize('name, art', [('key:del', b'classic_light_kb_del'), ('opt:shift', b'classic_light_kb_shift')])
def test_a_key_without_a_label_shows_its_glyph_centred(fw, st, ui, name, art, w, h):
    theme(fw, st, CLASSIC)
    kb, cand, row = keyboard(fw, ui)
    canvas = paint_key(fw, ui, row, name, w=w, h=h)
    (got, (x, y, gw, gh)), = canvas.images
    assert canvas.texts == [] and got == art and (gw, gh) == ART[art]
    assert abs(2 * x + gw - w) <= 1 and abs(2 * y + gh - h) <= 1


def test_the_keyboards_ramp_darkens_down_the_screen(fw, st):
    theme(fw, st, CLASSIC)
    canvas = Canvas(fw)
    fw.fn('paint_ramp', None, C.c_void_p, C.c_int, C.c_int, C.c_int)(canvas.p, 60, 320, 60)
    assert [r[:4] for r in canvas.rects] == [(0, y - 60, 375, 1) for y in range(60, 320)]
    light = [sum(channels(r[4])) for r in canvas.rects]
    assert light == sorted(light, reverse=True) and light[0] > light[-1]


def field_pieces(canvas):
    """The field image's pieces as drawn, left to right."""
    return sorted(dst for name, dst in canvas.images if name == b'classic_light_field')


@pytest.mark.parametrize('loads', [True, False])
def test_the_field_is_its_caps_round_a_stretched_middle_and_the_glass(fw, st, ui, loads):
    theme(fw, st, CLASSIC)
    canvas = Canvas(fw, sizes=ART | ({} if loads else {b'classic_light_field': None}))
    x, y, w = 24, 33, 327
    fw.fn('paint_field', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int)(ui.wm, canvas.p, x, y, w)
    pieces = field_pieces(canvas)
    if loads:
        (lx, ly, lw, lh), (mx, my, mw, mh), (rx, ry, rw, rh) = pieces
        assert lx == x and lx + lw == mx and mx + mw == rx and rx + rw == x + w  # across the field, no gap
        assert ly == my == ry == y and lh == mh == rh == ART[b'classic_light_field'][1]  # its rows, unscaled
        assert lw == rw < ART[b'classic_light_field'][0] // 2 < mw  # the image's two caps round a stretched middle
    else:
        assert pieces == []
    (gx, gy, gw, gh), = [dst for name, dst in canvas.images if name == b'classic_light_field_glass']
    assert x < gx and gx + gw < x + w // 2 and y < gy and gy + gh < y + ART[b'classic_light_field'][1]  # in its left end


@pytest.mark.parametrize('up', [False, True])
def test_searchs_field_paints_where_the_field_sits_the_glass_left_of_its_text(fw, st, ui, up):
    theme(fw, st, CLASSIC)
    win, nav, item, edit, glass, group = search(fw, ui)
    if up:
        keyboard(fw, ui)
    canvas = Canvas(fw, sizes=ART)
    fw.fn('search_paint', None, C.c_void_p, C.c_void_p)(win, canvas.p)
    x, y, w, h = ui.box(item)  # the dialog is at 0, 0: the screen's
    pieces = field_pieces(canvas)
    assert pieces[0][:2] == (x, y) and pieces[-1][0] + pieces[-1][2] == x + w  # under the field as it moves
    (gx, gy, gw, gh), = [dst for name, dst in canvas.images if name == b'classic_light_field_glass']
    assert x < gx and gx + gw <= x + ui.ints[edit, b'style:normal:margin_left'] and y < gy and gy + gh < y + h
    assert canvas.fill == 0xff123456


def test_t9s_box_is_searchs_field_under_the_bar_with_its_hint(fw, st, ui):
    theme(fw, st, CLASSIC)
    win, nav, item, search_edit, glass, group = search(fw, ui)
    keyboard(fw, ui)
    page = window(ui, 'playlist_page')
    edit = ui.widget(page, 'edit', 0, 100, 300, 40)
    box = fw.fn('t9_box', None, C.c_void_p, C.c_void_p, C.c_char_p)
    box(page, edit, b'Playlist Name')
    x, y, w, h = ui.box(edit)
    assert ui.prop(edit, b'tips') == b'Playlist Name' and (x, w) == ui.box(item)[::2]  # Search's field with T9 up
    assert y + h <= 30  # its foot clears T9's top, page y 30: the keyboard doesn't move the page
    title = ui.lookup(page, '_page_title', 1)
    assert ui.text(title) == 'Search' and ui.shown[title] == 0 and st.t9_page == page and st.t9_back
    for key in (b'style:normal:margin_left', TYPED_PX):
        assert ui.ints[edit, key] == ui.ints[search_edit, key]  # Search's field's text
    assert ui.ints[edit, EMPTY_PX] == ui.ints[edit, TYPED_PX]  # a short hint fits at the typed text's size
    ui.emit(edit, O['EVT_DESTROY'], 0)
    assert st.t9_page == st.t9_back == 0


def test_off_classic_t9s_box_only_takes_its_hint(fw, st, ui):
    theme(fw, st, MINIMAL)
    page = window(ui, 'playlist_page')
    edit = ui.widget(page, 'edit', 0, 100, 300, 40)
    fw.fn('t9_box', None, C.c_void_p, C.c_void_p, C.c_char_p)(page, edit, b'Playlist Name')
    assert ui.prop(edit, b'tips') == b'Playlist Name' and ui.box(edit) == (0, 100, 300, 40) and st.t9_page == 0


def test_a_long_hint_takes_a_smaller_size_never_under_12(fw, st, ui):
    theme(fw, st, CLASSIC)
    box = fw.fn('t9_box', None, C.c_void_p, C.c_void_p, C.c_char_p)
    sizes = []
    for n in (1, 5, 10, 13, 20, 40, 80, 200):
        page = window(ui, 'playlist_page')
        edit = ui.widget(page, 'edit', 0, 100, 300, 40)
        box(page, edit, b'x' * n)
        empty = {ui.ints[edit, f'style:{s}:font_size'.encode()] for s in ('empty', 'empty_focus', 'empty_over')}
        assert len(empty) == 1 and ui.prop(edit, b'tips') == b'x' * n  # one size in each empty state
        sizes.append((empty.pop(), ui.ints[edit, TYPED_PX]))
    hints = [hint for hint, _ in sizes]
    assert hints == sorted(hints, reverse=True) and hints[0] == sizes[0][1]  # never larger for more: a short one fits as typed
    assert all(hint <= typed for hint, typed in sizes) and any(12 < hint < typed for hint, typed in sizes)
    assert min(hints) == hints[-1] == hints[-2] == 12  # the smallest worth reading, however long


@pytest.fixture
def rename_row():
    """stock's PL_RENAME_ROW, which conftest's map keeps past a test."""
    cell = C.c_int.from_address(O['PL_RENAME_ROW'])
    saved = cell.value
    yield cell
    cell.value = saved


def playlist_dialog(fw, ui, name, edit_name):
    win = window(ui, name)
    item = ui.widget(win, f'list_item_{edit_name}', 0, 100, 375, 40)
    edit = ui.widget(item, edit_name, 0, 0, 300, 40)
    back = ui.widget(win, 'img_return', 0, 0, 60, 60)
    load(fw, ui, win)
    return win, item, edit, back


def test_new_playlist_is_the_keyboards_page_with_a_field_and_its_name_on_the_bar(fw, st, ui):
    theme(fw, st, CLASSIC)
    bar = Bar(fw, ui, title=True)
    bar.sync()
    _, _, _, search_edit, _, _ = search(fw, ui)
    win, item, edit, back = playlist_dialog(fw, ui, 'addplaylist_dialog', 'ssedit')
    x, y, w, h = ui.box(item)
    bx, by, bw, bh = ui.box(back)
    assert ui.box(edit) == (0, 0, w, h) and bx + bw <= x and by < y + h and y < by + bh  # the chevron beside the field
    assert 0 < ui.ints[edit, b'style:normal:margin_left'] < ui.ints[search_edit, b'style:normal:margin_left']  # no glass
    assert st.pl_box == win
    canvas = Canvas(fw, sizes=ART)
    fw.fn('pl_box_paint', None, C.c_void_p, C.c_void_p)(win, canvas.p)
    pieces = field_pieces(canvas)
    assert pieces[0][:2] == (x, y) and pieces[-1][0] + pieces[-1][2] == x + w  # its image under the field
    due, = ui.due('pl_box_due')
    ui.run(due)  # the next pass: stock's init has written its own hint
    assert ui.prop(edit, b'tips') == b'Playlist Name' and ui.text(bar.title) == 'New Playlist'
    hint, ink = (ui.ints[edit, key] & 0xffffffff for key in (b'style:empty:tips_text_color', b'style:normal:text_color'))
    assert lum(hint) > lum(ink)  # a muted hint, lighter than the typed text on the white field
    ui.emit(win, O['EVT_DESTROY'], 0)
    assert st.pl_box == 0 and ui.run(ui.timer_add(ui.fn('classic_pages__pl_box_due'), 0, 0)) == 0


@pytest.mark.parametrize('rows, at, tips', [(['Road Trip', 'Gym'], 1, b'Gym'), (['Road Trip'], 4, b'Rename'), ([None], 0, b'Rename')])
def test_rename_hints_the_playlists_own_name(fw, st, ui, rename_row, rows, at, tips):
    theme(fw, st, CLASSIC, 1)
    deques = Deques(fw)
    names = [C.create_string_buffer(n.encode()) if n else None for n in rows]
    recs = [C.create_string_buffer(0x60) for _ in rows]
    for rec, name in zip(recs, names):
        if name:
            struct.pack_into('<Q', rec, O['REC_TITLE'], C.addressof(name))
    fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = deques.new([C.addressof(r) for r in recs])
    rename_row.value = at
    win, item, edit, back = playlist_dialog(fw, ui, 'renameplaylist_dialog', 'edit')
    ui.run(ui.due('pl_box_due')[0])
    assert ui.prop(edit, b'tips') == tips
    hint, ink = (ui.ints[edit, key] & 0xffffffff for key in (b'style:empty_focus:tips_text_color', b'style:normal:text_color'))
    assert lum(hint) < lum(ink)  # stored flipped, as the typed text's ink: Light Mode's hook turns both back


def test_a_playlist_dialog_missing_a_part_stays_stocks(fw, st, ui):
    theme(fw, st, CLASSIC)
    win = window(ui, 'addplaylist_dialog')
    ui.widget(win, 'ssedit', 0, 0, 300, 40)
    load(fw, ui, win)
    assert st.pl_box == 0 and ui.due('pl_box_due') == []
