"""patch/navigation.c's painters and settings rows, with the scheme and Light Mode (now theme_engine.c's)
and a row action's confirm (now playlists.c's): the marquee's lull, Classic's dialogs and toast, the
selection, separators, marks and scrollbar, ringnav_paint, the settings pages' rows, the tag and scan
hooks, the lyrics' follow and the status bar's play state.

The widgets are a small AWTK of their own (Ui): each a zeroed block whose box and parent the C reads
by offset, its type, name, props, text and children answered by the stubs; the canvas (Canvas) keeps
what was drawn, pixel by pixel, in the colours the C set."""
import contextlib
import ctypes as C
import random
import re
import struct
import typing

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, c_enum, Deques, Files, claim, high_halves, libc, ui_name

STRINGS = dict(re.findall(r'^#define (\w+) "([^"]*)"', (host.ROOT/'patch/offsets.inc').read_text(), re.M))
LIST_INTO_WHITE, BT_GLYPH = STRINGS['LIST_INTO_WHITE'], STRINGS['BT_GLYPH']

W_X, W_Y, W_W, W_H, W_PARENT, W_EMITTER = O['W_X'], O['W_Y'], O['W_W'], O['W_H'], O['W_PARENT'], O['W_EMITTER']
EVT_CLICK, EVT_DESTROY = O['EVT_CLICK'], O['EVT_DESTROY']
CANVAS_X, CANVAS_Y, CANVAS_LCD = O['CANVAS_X'], O['CANVAS_Y'], O['CANVAS_LCD']
LCD_FILL, LCD_STROKE = O['LCD_FILL_COLOR'], O['LCD_STROKE_COLOR']
THEMES = host.tools_theme.builtins()
MINIMAL, CLASSIC, MODERN = (next(i for i, t in enumerate(THEMES) if t['name'] == name) for name in ('Minimal', 'Classic', 'Modern'))
SETS = host.tools_theme.geometry_sets(THEMES)  # boot_values' rows: st.geom indexes them
SET0 = SETS[0]  # the set st.geom 0 reads: theme_boot's before it writes one
CLASSIC_SET = next(i for i, s in enumerate(SETS) if s.name == 'classic-light')


def i32(v):
    return C.c_int(v).value


def rgba(c, a=0xff):
    """0xRRGGBB as the canvas takes it (theme.h RGBA): bytes r, g, b, a."""
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16 & 255


def wide(s):
    return [ord(ch) for ch in s]


def bright(c):
    """A colour's channels summed, in either byte order: which of two is the darker."""
    return sum(c >> s & 255 for s in (0, 8, 16))


POD = c_enum('POD_CHARGE')  # pod_rows' rows
SETTING = c_enum('SETTING_ACCENT')  # Display's rows, st.setting_label's slots


class Node:
    """A widget: a zeroed block the C reads its box (+0) and parent (+0x48) from, and what the stubs answer."""
    def __init__(self, kind, parent, box, name):
        self.block = C.create_string_buffer(0x200)
        self.addr = C.addressof(self.block)
        self.kind, self.name, self.parent = kind, name, parent
        self.props: dict = {}
        self.children: list = []
        self.visible = 1
        self.text: list = []
        self.wide = None
        self.handlers: list = []
        self.items: list = []
        self.emitter = C.create_string_buffer(8)
        self.move(*box)
        struct.pack_into('<Q', self.block, W_PARENT, parent.addr if parent else 0)

    def move(self, x, y, w, h):
        struct.pack_into('<iiii', self.block, W_X, x, y, w, h)

    @property
    def box(self):
        return struct.unpack_from('<iiii', self.block, W_X)

    def int_at(self, offset):
        return C.c_int.from_address(self.addr + offset)

    def byte_at(self, offset):
        return C.c_ubyte.from_address(self.addr + offset)

    def clickable(self, ctx=0):
        """An EVT_CLICK on its emitter (V1.36: emitter @0x60, its first item, type @8, next @0x20)."""
        item = C.create_string_buffer(0x28)
        struct.pack_into('<Q', item, 0, ctx)
        struct.pack_into('<i', item, O['EMIT_TYPE'], EVT_CLICK)
        if self.items:
            struct.pack_into('<Q', self.items[-1], O['EMIT_NEXT'], C.addressof(item))
        else:
            struct.pack_into('<Q', self.emitter, 0, C.addressof(item))
            struct.pack_into('<Q', self.block, W_EMITTER, C.addressof(self.emitter))
        self.items.append(item)
        return self

    def child(self, name):
        return next(c for c in self.walk() if c.name == name)

    def walk(self):
        for c in self.children:
            yield c
            yield from c.walk()

    def __repr__(self):
        return f'<{self.kind} {self.name!r} {self.box}>'


class Ui:
    """AWTK's widget calls over Nodes; the window manager's top window is top."""
    CREATE = {'list_item_create': 'list_item', 'button_create': 'button', 'image_create': 'image',
              'hscroll_label_create': 'hscroll_label', 'label_create': 'label', 'scroll_view_create': 'scroll_view'}

    def __init__(self, fw):
        self.fw = fw
        self.nodes: dict = {}
        self.cstr: dict = {}
        self.wm = self.add('window_manager', None)
        self.top: typing.Any = None  # the top window, a Node
        self.pressed = 0  # the pointer's down: window_manager_get_pointer_pressed
        m = fw.mock
        m('window_manager', lambda *_: self.wm.addr)
        m('window_manager_get_top_window', lambda *_: self.top.addr if self.top else 0)
        m('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        m('widget_get_type', lambda w, *_: self.c(self.nodes[w].kind) if w in self.nodes else 0)
        m('widget_get_prop_str', self.prop_str)
        m('widget_get_prop_int', self.prop_int)
        m('widget_get_prop_bool', lambda w, name, default, *_: i32(default))
        m('widget_set_prop_int', lambda w, name, v, *_: self.nodes[w].props.__setitem__(C.string_at(name).decode(), i32(v)))
        m('widget_set_prop_str', lambda w, name, v, *_: self.nodes[w].props.__setitem__(C.string_at(name).decode(), C.string_at(v).decode()))
        m('widget_count_children', lambda w, *_: len(self.nodes[w].children) if w in self.nodes else 0)
        m('widget_get_child', lambda w, i, *_: self.nodes[w].children[i & 0xffffffff].addr
          if w in self.nodes and (i & 0xffffffff) < len(self.nodes[w].children) else 0)
        m('widget_lookup', self.lookup)
        m('widget_get_visible', lambda w, *_: self.nodes[w].visible if w in self.nodes else 0)
        m('widget_set_visible', lambda w, v, *_: setattr(self.nodes[w], 'visible', int(bool(i32(v)))))
        m('widget_move_resize', lambda w, x, y, ww, h, *_: self.nodes[w].move(i32(x), i32(y), i32(ww), i32(h)))
        m('widget_resize', lambda w, ww, h, *_: self.nodes[w].move(*self.nodes[w].box[:2], i32(ww), i32(h)))
        m('widget_get_text', self.get_text)
        m('widget_set_text', self.set_wide)
        m('widget_set_text_utf8', lambda w, s, *_: self.set_text(self.nodes[w], C.string_at(s).decode()))
        m('image_base_set_image', lambda w, s, *_: self.nodes[w].props.__setitem__('image', C.string_at(s).decode()))
        m('widget_set_name', lambda w, s, *_: setattr(self.nodes[w], 'name', C.string_at(s).decode()))
        m('widget_use_style', lambda w, s, *_: self.nodes[w].props.__setitem__('style', C.string_at(s).decode()))
        m('widget_destroy', self.destroy)
        m('widget_destroy_children', lambda w, *_: [self.destroy(c.addr) for c in list(self.nodes[w].children)] and 0)
        m('widget_restack', self.restack)
        m('widget_on', self.on)
        for name, kind in self.CREATE.items():
            m(name, lambda parent, x, y, w, h, *_, kind=kind: self.add(kind, self.nodes[parent], (i32(x), i32(y), i32(w), i32(h))).addr)

    def c(self, s):
        if s is None:
            return 0
        if s not in self.cstr:
            self.cstr[s] = C.create_string_buffer(s.encode())
        return C.addressof(self.cstr[s])

    def add(self, kind, parent, box=(0, 0, 0, 0), name='', **props):
        node = Node(kind, parent, box, name)
        node.props.update(props)
        self.nodes[node.addr] = node
        if parent:
            parent.children.append(node)
        return node

    def window(self, name, box=(0, 0, 375, 320), kind='window', top=True):
        win = self.add(kind, self.wm, box, name)
        if top:
            self.top = win
        return win

    def prop_str(self, w, name, default, *_):
        node, key = self.nodes.get(w), C.string_at(name).decode()
        if node is None:
            return default
        if key == 'name':
            return self.c(node.name)
        value = node.props.get(key)
        return self.c(value) if isinstance(value, str) else default

    def prop_int(self, w, name, default, *_):
        node = self.nodes.get(w)
        value = node.props.get(C.string_at(name).decode()) if node else None
        return value if isinstance(value, int) else i32(default)

    def lookup(self, w, name, recursive, *_):
        node, key = self.nodes.get(w), C.string_at(name).decode()
        if node is None:
            return 0
        found = node.walk() if i32(recursive) else iter(node.children)
        return next((c.addr for c in found if c.name == key), 0)

    def set_text(self, node, s):
        node.text = wide(s)
        node.wide = (C.c_uint * (len(node.text) + 1))(*node.text)

    def get_text(self, w, *_):
        node = self.nodes.get(w)
        return C.addressof(node.wide) if node is not None and node.wide is not None else 0

    def set_wide(self, w, s, *_):
        chars = []
        while s and (ch := C.c_uint.from_address(s + 4 * len(chars)).value):
            chars.append(ch)
        self.set_text(self.nodes[w], ''.join(map(chr, chars)))

    def destroy(self, w, *_):
        node = self.nodes[w]
        node.parent.children.remove(node)

    def restack(self, w, index, *_):
        node = self.nodes[w]
        siblings = node.parent.children
        siblings.remove(node)
        siblings.insert(min(i32(index), len(siblings)), node)

    def on(self, w, event, fn, ctx, *_):
        node = self.nodes[w]
        node.handlers.append((i32(event), fn, ctx))
        if i32(event) == EVT_CLICK:
            node.clickable(ctx)
        return len(node.handlers)

    def fire(self, node, event, data=0):
        """node's handlers for event, as AWTK calls them: (ctx, event)."""
        return [C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(own, data) for kind, fn, own in node.handlers if kind == event]

    def handled(self, node, fn):
        """The contexts node's handlers that are fn were given."""
        return [own for _, f, own in node.handlers if f == fn]

    def text(self, node):
        return ''.join(map(chr, node.text))


class Canvas:
    """A canvas and its lcd: the fill as the C sets it (lcd's fill too, as stock's setter does), every
    rect and row it fills into px, clipped as stock clips, rounded boxes and text as drawn."""
    def __init__(self, fw, x=0, y=0, clip=(0, 0, 375, 320), fill=0xff0a0b0c, stroke=0xff0d0e0f, char_w=10):
        self.block, self.lcd = C.create_string_buffer(0x100), C.create_string_buffer(0x200)
        self.addr = C.addressof(self.block)
        struct.pack_into('<ii', self.block, CANVAS_X, x, y)
        struct.pack_into('<Q', self.block, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<II', self.lcd, LCD_FILL, fill, stroke)
        self.start = (fill, stroke)
        self.clip = list(clip)
        self.char_w = char_w  # every character this wide: a text's width is its length's
        self.px: dict = {}
        self.rects: list = []
        self.boxes: list = []
        self.frames: list = []
        self.texts: list = []
        self.icons: list = []
        self.images: list = []
        self.art: dict = {}  # the theme images widget_load_image finds: name -> (width, height)
        self.text_color = 0
        m = fw.mock
        m('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_FILL, color & 0xffffffff))
        m('canvas_set_stroke_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_STROKE, color & 0xffffffff))
        m('canvas_set_text_color', lambda c, color, *_: setattr(self, 'text_color', color & 0xffffffff))
        m('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fill_rect(i32(x), i32(y), i32(w), i32(h)))
        m('canvas_stroke_rect', lambda c, x, y, w, h, *_: self.frames.append(((i32(x), i32(y), i32(w), i32(h)), self.stroke, 0)))
        m('canvas_fill_rounded_rect', self.rounded)
        m('canvas_stroke_rounded_rect', lambda c, r, _, color, radius, *rest: self.stroke_round(r, color, radius))
        m('canvas_get_clip_rect', lambda c, out, *_: C.memmove(out, struct.pack('<4i', *self.clip), 16) and 0)
        m('canvas_set_clip_rect', lambda c, r, *_: setattr(self, 'clip', list(struct.unpack('<4i', C.string_at(r, 16)))))
        m('canvas_measure_text', lambda c, s, n, *_: float(self.char_w * (n & 0xffffffff)))
        m('canvas_draw_text_in_rect', self.draw_text)
        m('canvas_draw_icon', lambda c, bitmap, x, y, *_: self.icons.append((struct.unpack('<II', C.string_at(bitmap, 8)), i32(x), i32(y))))
        m('canvas_draw_image', lambda c, bitmap, src, dst, *_: self.images.append(struct.unpack('<4i', C.string_at(dst, 16))))
        m('widget_load_image', self.load_image)

    @property
    def fill(self):
        return struct.unpack_from('<I', self.lcd, LCD_FILL)[0]

    @property
    def stroke(self):
        return struct.unpack_from('<I', self.lcd, LCD_STROKE)[0]

    def fill_rect(self, x, y, w, h):
        self.rects.append(((x, y, w, h), self.fill))
        cx, cy, cw, ch = self.clip
        for yy in range(max(y, cy), min(y + h, cy + ch)):
            for xx in range(max(x, cx), min(x + w, cx + cw)):
                self.px[xx, yy] = self.fill

    def rounded(self, c, r, _, color, radius, *rest):
        """A rounded box, painted as its rect; it leaves the fill alone, as stock's vgcanvas does."""
        box, ink = struct.unpack('<4i', C.string_at(r, 16)), C.c_uint.from_address(color).value
        self.boxes.append((box, ink, i32(radius)))
        saved = self.fill
        struct.pack_into('<I', self.lcd, LCD_FILL, ink)
        self.fill_rect(*box)
        self.rects.pop()
        struct.pack_into('<I', self.lcd, LCD_FILL, saved)
        return 0

    def stroke_round(self, r, color, radius):
        self.frames.append((struct.unpack('<4i', C.string_at(r, 16)), C.c_uint.from_address(color).value, i32(radius)))
        return 0

    def draw_text(self, c, s, n, r, *_):
        chars = [C.c_uint.from_address(s + 4 * i).value for i in range(n & 0xffffffff)]
        self.texts.append((''.join(map(chr, chars)), struct.unpack('<4i', C.string_at(r, 16)), self.text_color))
        return 0

    def load_image(self, w, name, bitmap, *_):
        size = self.art.get(C.string_at(name).decode())
        if size is None:
            return 1
        C.memmove(bitmap, struct.pack('<II', *size), 8)
        return 0

    def restored(self):
        """Whether the painter left the fill and stroke as it found them."""
        return (self.fill, self.stroke) == self.start

    def row(self, y, x0=0, x1=375):
        return [self.px.get((x, y)) for x in range(x0, x1)]


class Settings:
    """config.ini through toolsReadConfig: a key's value, or the default; written keys as toolsWriteConfig
    and write_int_config left them."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v for k, v in values.items()}
        self.written: list = []
        fw.mock('toolsReadConfig', self.read)
        fw.mock('write_int_config', lambda v, section, key, *_: self.written.append((C.string_at(key).decode(), i32(v))))
        fw.mock('toolsWriteConfig', lambda path, section, key, v, *_: self.written.append((C.string_at(key).decode(), C.string_at(v))))

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        if value or C.string_at(key) in self.values:
            C.memmove(out, value + b'\0', len(value) + 1)


@pytest.fixture(scope='module')
def scratch(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


def strings(fw):
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))


def theme(fw, index, **values):
    """The theme config.ini names, read as accent() reads it."""
    settings = Settings(fw, THEME=str(index).encode(), **values)
    fw.fn('ipod_light', C.c_int)()
    return settings


def g(fw, name, value=None):
    """One of stock's byte globals."""
    v = fw.var(name, C.c_ubyte)
    if value is not None:
        v.value = value
    return v.value


# The genre's strcpy: whole " / " entries within 0x7f bytes, or whole characters of one too long.
def kept_as_the_genre(joined, out):
    """Whether out is what the comment over ringnav_genre_copy keeps of joined: asserted, a property
    at a time."""
    assert joined.startswith(out) and len(out) <= 0x7f
    out.decode()  # whole characters
    rest, first = joined[len(out):], joined.split(b' / ')[0]
    if len(first) > 0x7f:  # the first entry alone is too long: as many of its whole characters as fit
        lead = rest[0]
        size = 1 if lead < 0x80 else 2 if lead >> 5 == 6 else 3 if lead >> 4 == 14 else 4
        assert len(out) + size > 0x7f
    else:  # whole entries: it ends on a boundary, and the next whole entry would pass 0x7f
        assert not rest or rest.startswith(b' / ')
        if rest:
            assert len(out) + 3 + len(rest[3:].split(b' / ')[0]) > 0x7f
    return True


@pytest.mark.parametrize('joined, kept', [
    (b'Rock', b'Rock'), (b'Rock / Pop', b'Rock / Pop'), (b'', b''),
    (b'a' * 100 + b' / ' + b'b' * 30, b'a' * 100),
    (b'a' * 0x7f, b'a' * 0x7f), (b'a' * 0x80, b'a' * 0x7f),
    (('é' * 100).encode(), ('é' * 63).encode()),  # one too long: 126 bytes of whole characters
    (b'x' * 60 + b' / ' + b'y' * 64, b'x' * 60 + b' / ' + b'y' * 64),
    (b'x' * 60 + b' / ' + b'y' * 65, b'x' * 60),
    (b' / ' + b'z' * 200, b''),  # the ponytail: an empty first entry before one too long
    (b'Rock/Pop; Jazz', b'Rock/Pop; Jazz'),
])
def test_genre_copy(fw, joined, kept):
    dst = (C.c_char * 0x80)()
    back = fw.fn('ringnav_genre_copy', C.c_void_p, C.c_void_p, C.c_char_p)(dst, joined)
    assert back == C.addressof(dst) and dst.value == kept and len(kept) <= 0x7f


def test_genre_copy_any_tag(fw):
    """Whole entries, never past 0x7f bytes, never inside a UTF-8 character, and no shorter than
    those rules make it."""
    copy = fw.fn('ringnav_genre_copy', C.c_void_p, C.c_void_p, C.c_void_p)
    calloc = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t)
    free = fw.fn('free', None, C.c_void_p)
    rng = random.Random(0x6e7e)
    pieces = [b'Rock', b'Hip-Hop', b' / ', b'/', b' ', 'é'.encode(), '音楽'.encode(), '\U0001f3b8'.encode(), b'x' * 40]
    for _ in range(1500):
        joined = b''.join(rng.choice(pieces) for _ in range(rng.randrange(0, 30)))
        src, dst = calloc(len(joined) + 1, 1), calloc(0x80, 1)
        C.memmove(src, joined, len(joined))
        copy(dst, src)
        assert kept_as_the_genre(joined, C.string_at(dst)), joined
        free(src), free(dst)


# The marquee's lull: each pass of a looping label ends here, on its end or (yoyo) its way back.
HOLD, PASSES = O['MARQUEE_HOLD_MS'], O['MARQUEE_PASSES']
LULL = C.CFUNCTYPE(C.c_int, C.c_void_p)


class Lull:
    """ringnav_hscroll_lull's timer, fired as stock's timer manager would, over stock's own lull callback."""
    def __init__(self, fw, label, loop=1, yoyo=0, answer=8):
        self.fw, self.label, self.seen = fw, label, []
        label.byte_at(O['HSCROLL_LOOP']).value, label.byte_at(O['HSCROLL_YOYO']).value = loop, yoyo
        self.timers: list = []
        fw.mock('widget_add_timer', lambda w, fn, ms, *_: self.timers.append((w, fn, i32(ms))) or 41)
        self.stock = LULL(lambda info: self.seen.append(len(fw.calls('hscroll_label_set_xoffset'))) or answer)
        self.info = C.create_string_buffer(0x30)
        struct.pack_into('<Q', self.info, O['TIMER_CTX'], label.addr)
        fw.fn('ringnav_hscroll_lull', C.c_uint, C.c_void_p, C.c_void_p, C.c_uint)(label.addr, C.cast(self.stock, C.c_void_p), 3000)

    def end(self):
        return LULL(self.timers[-1][1])(C.addressof(self.info))


@pytest.mark.parametrize('loop, yoyo, ms', [(1, 0, HOLD), (0, 0, HOLD), (0, 1, HOLD), (1, 1, 0)])
def test_a_lull_holds_the_end_unless_a_yoyo_turns_there(fw, loop, yoyo, ms):
    """Stock's 2000 or 3000 ms lull gives way to MARQUEE_HOLD_MS; a looping yoyo label holds its
    turn with its start's delay, so its lull is none."""
    ui = Ui(fw)
    lull = Lull(fw, ui.add('hscroll_label', None), loop, yoyo)
    assert [(w, ms) for w, _, ms in lull.timers] == [(lull.label.addr, ms)]


def test_a_pass_ends_back_on_the_start_and_stock_restarts_it(fw):
    ui = Ui(fw)
    lull = Lull(fw, ui.add('hscroll_label', None), answer=8)
    assert lull.end() == 8
    # the start is back before stock's callback restarts the label, so its delay holds there
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_xoffset')] == [(lull.label.addr, 0)]
    assert lull.seen == [1] and not fw.calls('hscroll_label_stop')


def test_a_yoyo_pass_turns_on_its_end(fw):
    ui = Ui(fw)
    lull = Lull(fw, ui.add('hscroll_label', None), yoyo=1)
    lull.end()
    assert lull.seen == [0] and not fw.calls('hscroll_label_set_xoffset')


def test_a_label_that_doesnt_loop_stops_on_its_start(fw):
    ui = Ui(fw)
    label = ui.add('hscroll_label', None)
    label.int_at(O['HSCROLL_TIMER']).value = 41
    lull = Lull(fw, label, loop=0)
    assert lull.end() == 7  # RET_REMOVE: this firing timer is the one stopped
    assert label.int_at(O['HSCROLL_TIMER']).value == 0 and lull.seen == []
    assert [c[1][0] for c in fw.calls('hscroll_label_stop')] == [label.addr]


def library(fw, ui, rows=4, pitch=48, name='localmusic_page', titles=None):
    """A lit, usable screen: a Library-style page over a vertical scroll_view of tappable rows, each a
    list_item holding a button and its title's hscroll_label. Returns the view and the buttons."""
    for flag, value in (('g_backlight_status', 1), ('g_usblink_status', 0), ('g_poweroff_state', 0)):
        g(fw, flag, value)
    win = ui.window(name)
    view = ui.add('scroll_view', win, (0, 26, 375, 294), 'scroll_view_list')
    view.byte_at(O['VIEW_VERTICAL']).value = 1
    view.int_at(O['VIEW_CONTENT_H']).value = rows * pitch
    buttons = []
    for i in range(rows):
        item = ui.add('list_item', view, (0, i * pitch, 375, pitch))
        button = ui.add('button', item, (20, 0, 335, pitch), f'row{i}').clickable()
        label = ui.add('hscroll_label', button, (10, 0, 260, pitch), f'title{i}')
        ui.set_text(label, titles[i] if titles else f'Row {i}')
        buttons.append(button)
    return view, buttons


def test_the_selected_rows_title_scrolls_its_passes_then_rests(fw, st):
    ui = Ui(fw)
    view, rows = library(fw, ui)
    label = rows[1].children[0]
    st('sel_w', C.c_void_p).value, st('sel_row').value = view.addr, 1
    st('marq_row', C.c_void_p).value, st('marq_id').value = rows[1].addr, 1
    st('marq_label', C.c_void_p).value = label.addr
    lull = Lull(fw, label)
    for passes in range(1, PASSES + 1):
        lull.end()
        assert st('marq_passes').value == passes
        assert bool(fw.calls('hscroll_label_stop')) == (passes == PASSES)
    # the last pass gives the label back its ellipsis: only_focus again, and no longer the marquee's
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_only_focus')] == [(label.addr, 1)]
    assert st('marq_label', C.c_void_p).value is None


@pytest.mark.parametrize('lit, moved', [(0, 0), (1, 1)])
def test_a_marquee_off_its_row_or_screen_ends_with_the_pass(fw, st, lit, moved):
    """A dark screen, or a selection moved to another row, stops it at once, and the next paint with a
    selection starts over (marq_row 0)."""
    ui = Ui(fw)
    view, rows = library(fw, ui)
    g(fw, 'g_backlight_status', lit)
    label = rows[1].children[0]
    st('sel_w', C.c_void_p).value, st('sel_row').value = view.addr, 2 if moved else 1
    st('marq_row', C.c_void_p).value, st('marq_id').value = rows[1].addr, 1
    st('marq_label', C.c_void_p).value = label.addr
    Lull(fw, label).end()
    assert st('marq_row', C.c_void_p).value is None and st('marq_label', C.c_void_p).value is None
    assert [c[1][0] for c in fw.calls('hscroll_label_stop')] == [label.addr]


# The theme's scrollbar: a thumb alone, drawn after a list's children (ringnav_paint).
class Lists:
    """A list_view holding its scroll_view (or a table's client) and stock's scroll_bar_m, under Classic's
    thumb: the list's top at screen row y."""
    def __init__(self, fw, y=26, h=294, content=1200, value=0, table=0):
        self.ui, self.fw = Ui(fw), fw
        theme(fw, CLASSIC)
        self.T = THEMES[CLASSIC]
        win = self.ui.window('allmusic_page')
        self.list = self.ui.add('list_view', win, (0, y, 375, h))
        client = self.ui.add('table_client' if table else 'scroll_view', self.list, (0, 0, 375, h))
        client.int_at(O['VIEW_CONTENT_H']).value = content
        fw.mock('table_client_get_virtual_h', lambda *_: content)
        self.bar = self.ui.add('scroll_bar_m', self.list, (369, 0, 6, h))
        self.bar.int_at(O['BAR_MAX']).value = 1000 if table else content  # a table's bar: an asset's 1000
        self.bar.int_at(O['BAR_VALUE']).value = value
        self.canvas = Canvas(fw, y=y)

    def paint(self):
        self.fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(self.list.addr, self.canvas.addr)
        return sorted(self.canvas.px)

    def track(self):
        """The screen rows the thumb travels, in the list's own rows."""
        y, h = struct.unpack_from('<i', self.canvas.block, CANVAS_Y)[0], self.list.box[3]
        return max(y, self.T['sb_top']) - y, min(y + h, self.T['sb_bottom']) - y


def thumb_box(pixels):
    xs, ys = [x for x, _ in pixels], [y for _, y in pixels]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def test_the_thumb_sits_at_the_lists_right_edge_and_starts_at_the_tracks_top(fw):
    lists = Lists(fw)
    x0, y0, x1, y1 = thumb_box(lists.paint())
    top, bottom = lists.track()
    T = lists.T
    assert (x0, x1) == (375 - T['sb_right'] - T['sb_w'], 375 - T['sb_right'])
    assert y0 == top and y1 - y0 == (bottom - top) * 294 // 1200  # the screenful's share of the content
    assert lists.canvas.restored()


def test_the_thumb_ends_at_the_tracks_bottom_at_the_lists_end(fw):
    lists = Lists(fw, value=1200)
    _, y0, _, y1 = thumb_box(lists.paint())
    assert y1 == lists.track()[1]


@pytest.mark.parametrize('value', [-50, 0, 300, 906, 1200, 5000])
def test_the_thumb_moves_with_the_list_and_stays_on_its_track(fw, value):
    lists = Lists(fw, value=value)
    _, y0, _, y1 = thumb_box(lists.paint())
    top, bottom = lists.track()
    length, at = y1 - y0, max(0, min(value, 1200))
    assert top <= y0 and y1 <= bottom
    assert y0 == top + (bottom - top - length) * at // 1200


def test_a_long_list_keeps_the_thumbs_least_length(fw):
    lists = Lists(fw, content=100000)
    _, y0, _, y1 = thumb_box(lists.paint())
    assert y1 - y0 == lists.T['sb_min']


def test_a_list_that_fits_has_no_thumb(fw):
    lists = Lists(fw, content=294)
    assert lists.paint() == []


def test_a_table_measures_by_its_virtual_height(fw):
    """Before its first scroll a table's bar holds an asset's 1000: the client's height counts."""
    lists = Lists(fw, content=2940, table=1)
    _, y0, _, y1 = thumb_box(lists.paint())
    top, bottom = lists.track()
    assert y1 - y0 == (bottom - top) * 294 // 2940


def test_a_list_under_the_bar_keeps_the_thumb_below_it(fw):
    """The track's ends are screen rows: a list from the top of the screen starts its thumb at sb_top."""
    lists = Lists(fw, y=0, h=320)
    _, y0, _, _ = thumb_box(lists.paint())
    assert y0 == lists.T['sb_top']


def test_the_thumb_is_the_themes_in_either_half(fw, st):
    lists = Lists(fw)
    dark = set(lists.canvas.px[p] for p in lists.paint())
    st('light').value = 1
    lists.canvas.px.clear()
    light = set(lists.canvas.px[p] for p in lists.paint())
    assert rgba(lists.T['sb_thumb']) in dark and dark != light


def test_minimal_has_no_thumb(fw, st):
    lists = Lists(fw)
    st('theme').value = MINIMAL
    assert lists.paint() == [] and fw.calls('stock_paint_trampoline')


# The status bar's play state (ringnav_showface, each second).
class Bar:
    NAMES = ('img_bt', 'img_wifi', 'label_battery', 'view_battery', 'img_battery', 'label_idle', 'img_state',
             'label_eq', 'label_title', 'label_clock')

    def __init__(self, fw, status=0):
        self.fw, self.ui = fw, Ui(fw)
        self.status = status
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        self.bar = self.ui.window('statusbar', (0, 0, 375, 26), top=False)
        fw.var('fw_ctx_system_bar', C.c_void_p).value = self.bar.addr
        for name in self.NAMES:
            self.ui.add('image' if name.startswith('img') else 'label', self.bar, (0, 0, 20, 20), name)
        self.w = {n.name: n for n in self.bar.children}

    def sync(self):
        self.fw.fn('ringnav_showface', C.c_int, C.c_void_p)(self.bar.addr)


@pytest.mark.parametrize('status, shown, image', [(2, 1, 'bar_play'), (3, 1, 'bar_pause'), (0, 0, None), (1, 0, None)])
def test_the_bar_shows_whats_playing(fw, status, shown, image):
    theme(fw, MODERN)
    bar = Bar(fw, status)
    bar.w['label_eq'].visible = 0
    bar.sync()
    assert bar.w['img_state'].visible == shown and bar.w['img_state'].props.get('image') == image
    assert bar.w['label_idle'].visible == (not shown)  # idle when neither the state nor EQ shows


def test_idle_stays_hidden_under_eq(fw):
    theme(fw, MODERN)
    bar = Bar(fw, 0)
    bar.sync()
    assert bar.w['label_eq'].visible and not bar.w['label_idle'].visible


def test_a_second_sync_with_nothing_new_sets_no_image(fw):
    """The image is set only when it changes (a set image is a relayout and a repaint); visibility
    writes are stock's no-ops when unchanged."""
    theme(fw, MODERN)
    bar = Bar(fw, 2)
    bar.sync()
    shown = {n: w.visible for n, w in bar.w.items()}
    bar.sync()
    assert len(fw.calls('image_base_set_image')) == 1 and {n: w.visible for n, w in bar.w.items()} == shown
    bar.status = 3
    bar.sync()
    assert bar.w['img_state'].props['image'] == 'bar_pause'


@pytest.mark.parametrize('light', [0, 1])
@pytest.mark.parametrize('status', [2, 3])
def test_classic_draws_its_own_state_by_half(fw, light, status):
    theme(fw, CLASSIC, LIGHT=str(light).encode())
    bar = Bar(fw, status)
    bar.sync()
    state = 'play' if status == 2 else 'pause'
    assert bar.w['img_state'].props['image'] == f'classic_light_{state}_{"dl"[light]}'
    assert bar.w['label_eq'].visible  # stock's, left alone: Classic's bar keeps it off-screen (tools/ipod.py)


@pytest.mark.parametrize('status', [2, 3])
def test_on_homes_list_the_state_stays_by_the_battery_and_the_title_shows(fw, status):
    """Home's own list is like any page: Classic's play state shows in img_state and leaves the title alone."""
    theme(fw, CLASSIC)
    bar = Bar(fw, status)
    home = bar.ui.window('home_page')
    bar.sync()
    on_home = bar.w['img_state'].props.get('image')
    assert bar.w['img_state'].visible and on_home and bar.w['label_title'].visible
    bar.ui.window('allmusic_page')
    bar.sync()
    assert bar.w['img_state'].visible and bar.w['img_state'].props['image'] == on_home  # as over a page
    bar.ui.top = home
    bar.status = 0
    bar.sync()
    assert not bar.w['img_state'].visible and bar.w['label_title'].visible


# The selection, painted behind the rows (ringnav_paint_bg).
@pytest.fixture(scope='module')
def layout(library):
    obj = library.parent/'navigation.o'
    return host.offsets(obj, 'scheme_t'), host.offsets(obj, 'stops_t')


class Scheme:
    """The live scheme's fields (theme.h scheme_t), as the theme table states them."""
    def __init__(self, fw, layout):
        at, stops = layout
        base = fw.fn('scheme', C.c_void_p)()
        tones = C.c_void_p.from_address(base + at['tones']).value or 0
        self.tones = [C.c_uint.from_address(tones + 4 * i).value for i in range(5)]
        self.flags = C.c_ubyte.from_address(base + at['flags']).value
        get = lambda name: C.c_uint.from_address(base + at[name]).value
        self.fill, self.muted, self.sel_text, self.ink = get('fill'), get('muted'), get('sel_text'), get('ink')
        self.sel_top, self.sel_edge, self.sel_tall_edge, self.sel_tall_line = (get('sel_top'), get('sel_edge'),
                                                                               get('sel_tall_edge'), get('sel_tall_line'))
        sel = base + at['sel']
        n = C.c_ubyte.from_address(sel + stops['n']).value
        self.sel = [C.c_uint.from_address(sel + stops['rgb'] + 4 * i).value for i in range(n)]
        self.sel_pos = [C.c_ubyte.from_address(sel + stops['pos'] + i).value for i in range(n)]
        self.paper = C.c_uint.from_address(base + at['paper']).value

    def sel_row(self, j, h):
        """Row j of h of the selection's stops: j / (h - 1) of the way down, mixed between the stops
        around it (theme.h stops_t)."""
        pos, rgb = self.sel_pos, self.sel
        if len(rgb) == h:
            return rgb[j]
        p, k = j * 25600 // (h - 1), 0
        while k + 2 < len(rgb) and p > pos[k + 1] * 256:
            k += 1
        return mix(rgb[k], rgb[k + 1], p - pos[k] * 256, (pos[k + 1] - pos[k]) * 256)


class Page:
    """A list page under a theme, its row sel the one selected and settled (its count and scope the
    view's), painted as stock paints a widget's background."""
    def __init__(self, fw, which, rows=4, pitch=48, sel=1, name='localmusic_page', **config):
        self.fw = fw
        theme(fw, which, **config)
        self.ui = Ui(fw)
        self.view, self.rows = library(fw, self.ui, rows, pitch, name)
        if sel is not None:
            self.view.props.update({'_ringnav_count': rows, '_ringnav_scope': 1, '_ringnav_index': sel})
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or len(self.timers))
        self.canvas = Canvas(fw)

    def paint(self, w=None):
        self.fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)((w or self.view).addr, self.canvas.addr)
        return self.canvas

    def band(self):
        """The rows painted, top to bottom, each (x0, x1, colours across)."""
        rows = {}
        for (x, y), c in self.canvas.px.items():
            rows.setdefault(y, {})[x] = c
        return {y: (min(r), max(r) + 1, [r[x] for x in sorted(r)]) for y, r in sorted(rows.items())}


def solid(row):
    return len(set(row[2])) == 1 and row[2][0]


def mix(a, b, j, n):
    """0xRRGGBB j/n of the way from a to b, per channel."""
    return sum((((a >> s & 255) * (n - j) + (b >> s & 255) * j) // n) << s for s in (0, 8, 16))


def neutral(c):
    """A grey: its channels within 16 of each other."""
    ch = [c >> s & 255 for s in (0, 8, 16)]
    return max(ch) - min(ch) <= 16


def style_ink(fw, color=0xffffffff):
    """The text colour stock's style lookup answers (ringnav_style_color) for the widget painting now:
    stock's white text, as the row paints it."""
    fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))
    c = C.c_uint(color)
    fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)(C.byref(c), None, b'text_color', 0)
    return c.value


def text_ink(fw, st, w, color=0xffffffff):
    """style_ink while w paints (stock's paint of w under way, as ringnav_paint_bg marks it)."""
    st('paint_w', C.c_void_p).value = w.addr
    return style_ink(fw, color)


def test_modern_bars_the_selected_row_full_width(fw, st, layout):
    page = Page(fw, MODERN)
    S = Scheme(fw, layout)
    band = page.band() if page.paint() else {}
    assert list(band) == list(range(48, 96)) and all(r[:2] == (0, 375) for r in band.values())
    # a one-pixel highlight over the accent's gradient, each row j / 47 of the way top tone to bottom
    assert solid(band[48]) == rgba(S.tones[4])
    assert [solid(band[48 + j]) for j in (1, 24, 47)] == [rgba(mix(S.tones[0], S.tones[1], j, 47)) for j in (1, 24, 47)]
    assert page.canvas.restored() and page.canvas.clip == [0, 0, 375, 320]


def test_rounded_corners_make_modern_a_pill_inset_from_the_glass(fw, layout):
    page = Page(fw, MODERN, ROUNDED=b'1')
    band = page.band() if page.paint() else {}
    inset = O['SEL_INSET']
    assert band[72][:2] == (inset, 375 - inset)
    assert band[48][0] > inset and band[95][0] > inset  # its ends round in


def test_minimal_ivory_lays_a_card_under_the_row(fw, st, layout):
    page = Page(fw, MINIMAL, MINIMAL_ACCENT=b'7', ROUNDED=b'0')
    S = Scheme(fw, layout)
    page.paint()
    assert S.flags & 1  # Ivory: SCHEME_CARD
    assert page.canvas.boxes == [((0, 48, 375, 48), rgba(S.fill), 0)]  # Corners: Square, full width
    # the card's row takes a dark ink on it; the other rows keep stock's
    lit, other = (text_ink(fw, st, page.rows[k].children[0]) for k in (1, 2))
    assert bright(lit) < bright(S.fill) <= bright(other)


def test_minimal_rounded_card_is_a_pill_inset_from_the_glass(fw, layout):
    page = Page(fw, MINIMAL, ROUNDED=b'1')
    page.paint()
    inset = O['SEL_INSET']
    assert page.canvas.boxes[0][0] == (inset, 48, 375 - 2 * inset, 48) and page.canvas.boxes[0][2] == 24


@pytest.mark.parametrize('coloured', [1, 0])
def test_minimal_accent_card_is_its_tone_and_a_coloured_ones_row_keeps_white_ink(fw, st, layout, coloured):
    page = Page(fw, MINIMAL)
    for accent in range(len(THEMES[MINIMAL]['schemes'])):
        st('minimal_accent').value = accent
        S = Scheme(fw, layout)
        if not S.flags & 1 and neutral(S.tones[0]) != coloured:
            break
    page.paint()
    assert page.canvas.boxes[0][1] == rgba(S.tones[0])
    # Light Mode keeps the row's white text white on a coloured card, and flips it on a grey one
    st('light').value = 1
    assert (text_ink(fw, st, page.rows[1].children[0]) == 0xffffffff) == bool(coloured)


@pytest.mark.parametrize('tall', [0, 1])
def test_classic_lays_its_stops_band_with_its_lines(fw, layout, tall):
    """A crisp top line, the stops, an edge; a row taller than the set's pitch ends on its two rows."""
    pitch = SET0.pitch + 16 if tall else SET0.pitch
    page = Page(fw, CLASSIC, pitch=pitch)
    S = Scheme(fw, layout)
    band = page.band() if page.paint() else {}
    y0, y1 = pitch, 2 * pitch
    assert list(band) == list(range(y0, y1)) and all(r[:2] == (0, 375) for r in band.values())
    assert solid(band[y0]) == rgba(S.sel_top)
    if tall:
        assert solid(band[y1 - 2]) == rgba(S.sel_tall_edge) and solid(band[y1 - 1]) == rgba(S.sel_tall_line)
    else:
        assert solid(band[y0 + 1]) == rgba(S.sel[0]) and solid(band[y1 - 2]) == rgba(S.sel[-1])
        assert solid(band[y1 - 1]) == rgba(S.sel_edge)


def test_a_fresh_page_settles_on_its_first_row(fw, layout):
    page = Page(fw, MODERN, sel=None)
    band = page.band() if page.paint() else {}
    assert min(band) == 0 and max(band) == 47
    assert page.view.props['_ringnav_index'] == 0 and page.view.props['_ringnav_count'] == 4


def test_touch_hides_the_selection_until_the_wheel(fw, st):
    page = Page(fw, MODERN)
    st('touch_mode').value = 1
    assert not page.paint().px and page.view.props['_ringnav_index'] == 1


def test_a_boundary_bump_nudges_the_band_by_the_themes_detent(fw, st):
    page = Page(fw, MODERN)
    st('fx_surface', C.c_void_p).value, st('fx_token').value, st('bump_dir').value = page.view.addr, 5, 1
    page.view.props['_ringnav_fx'] = 5
    band = page.band() if page.paint() else {}
    assert min(band) == 48 - THEMES[MODERN]['bump_px'] and len(band) == 48


def test_the_band_takes_a_rows_whole_pitch_under_its_button(fw):
    """The Library's row is a button over its pitch's top: the bar fills the row."""
    page = Page(fw, MODERN)
    for item in page.view.children:
        item.move(*item.box[:3], 56)
        item.children[0].move(20, 0, 335, 48)
    for i, item in enumerate(page.view.children):
        item.move(0, 56 * i, 375, 56)
    band = page.band() if page.paint() else {}
    assert (min(band), max(band) + 1) == (56, 112)


def test_a_new_row_starts_its_marquee_timer_and_the_same_row_doesnt(fw, st):
    page = Page(fw, MODERN)
    marq_moved = C.cast(fw.lib.navigation__marq_moved, C.c_void_p).value
    page.paint()
    assert page.timers.count(marq_moved) == 1 and st('marq_row', C.c_void_p).value == page.rows[1].addr
    page.paint()
    assert page.timers.count(marq_moved) == 1
    page.view.props['_ringnav_index'] = 2
    page.paint()
    assert page.timers.count(marq_moved) == 2 and st('marq_id').value == 2


def grid(page, columns=3):
    """The view's rows as an album grid's: each item a row of tiles, each a third of the width."""
    w = 375 // columns
    for item in page.view.children:
        button = item.children[0]
        button.move(0, 0, w - 1, item.box[3])
        for k in range(1, columns):
            page.ui.add('button', item, (k * w, 0, w - 1, item.box[3]), f'{button.name}.{k}').clickable()


@pytest.mark.parametrize('rounded, radius', [(b'0', 0), (b'1', O['SEL_RADIUS'])])
def test_a_grid_tile_keeps_its_own_card_inset_all_round(fw, rounded, radius):
    page = Page(fw, MINIMAL, ROUNDED=rounded)
    grid(page)
    page.view.props.update({'_ringnav_count': 12, '_ringnav_index': 4})  # the second row's middle tile
    page.paint()
    t = O['TILE_INSET']
    assert page.canvas.boxes == [((125 + t, 48 + t, 124 - 2 * t, 48 - 2 * t), page.canvas.boxes[0][1], radius)]


def test_a_page_under_a_sliding_one_draws_the_row_it_holds(fw, st):
    """The snapshot of the page under a sliding page: its own held row, nothing loaded or settled."""
    page = Page(fw, MODERN)
    top = page.ui.window('playset_page')
    top.props['anim_hint'] = 'htranslate'
    page.view.props['_ringnav_index'] = 3
    band = page.band() if page.paint() else {}
    assert (min(band), max(band) + 1) == (144, 192)


def test_classics_speed_row_is_one_band_its_stops_on_the_paper(fw, st, layout):
    """More's Speed row under the stops band: the band the row's whole height, each speed's 24 px
    button a plate of the paper on it so their marks read, the wheel's framed white, and the row's
    text the band's."""
    theme(fw, CLASSIC)
    st('geom').value = CLASSIC_SET
    ui = Ui(fw)
    view, _ = library(fw, ui, rows=0, name='playermore_page')
    item = ui.add('list_item', view, (0, 0, 375, 70))
    button = ui.add('button', item, (0, 20, 375, 40), style='s_btn_forwardspeed')
    for k in range(4):
        ui.add('image', button, (40 + 80 * k, 0, 60, 61)).clickable()
    view.props.update({'_ringnav_count': 4, '_ringnav_scope': 0, '_ringnav_index': 2})  # a dynamic page: no scope
    canvas = Canvas(fw)
    paint_bg(fw, view, canvas)
    S = Scheme(fw, layout)
    rows = sorted({y for _, y in canvas.px})
    assert (rows[0], rows[-1] + 1) == (0, 70) and canvas.row(10) == canvas.row(10)[:1] * 375  # one band
    assert [b[:2] for b in canvas.boxes] == [((40 + 80 * k, 20, 60, 24), rgba(0)) for k in range(4)]
    assert canvas.frames[0][:2] == ((200, 20, 60, 24), 0xffffffff)
    assert text_ink(fw, st, button) == rgba(S.sel_text)


def test_minimals_home_list_marks_its_row_with_a_dot(fw, st):
    theme(fw, MINIMAL)
    ui = Ui(fw)
    view, rows = library(fw, ui, name='home_page')
    view.props.update({'_ringnav_count': 4, '_ringnav_scope': 0, '_ringnav_index': 2})
    canvas = Canvas(fw)
    paint_bg(fw, view, canvas)
    dot = O['HOME_DOT']
    assert canvas.boxes == [((O['HOME_DOT_X'], 96 + (48 - dot) // 2, dot, dot), 0xffffffff, dot // 2)]
    lit, other = (text_ink(fw, st, rows[k].children[0]) for k in (2, 0))
    assert bright(lit) > bright(other)  # the dot's row bright, the rest of the list dimmed


def test_a_list_mid_layout_paints_nothing_and_ends_a_pending_centre(fw, st):
    page = Page(fw, MODERN)
    page.view.int_at(O['VIEW_CONTENT_H']).value = -1  # stock's relayout under way
    st('center_timer').value = 7  # a centre press pending on this list
    st('center_top', C.c_void_p).value, st('center_surface', C.c_void_p).value = page.ui.top.addr, page.view.addr
    assert not page.paint().px and st('center_timer').value == 0 and fw.calls('timer_remove')


def buttons_page(fw, which, name='playlistsmore_page', **config):
    """A BUTTONS window, its own tappable buttons the rows: three tiles side by side."""
    theme(fw, which, **config)
    ui = Ui(fw)
    for flag, value in (('g_backlight_status', 1), ('g_usblink_status', 0)):
        g(fw, flag, value)
    win = ui.window(name, kind='dialog')
    tiles = [ui.add('button', win, (20 + 112 * i, 200, 110, 40), f'b{i}').clickable() for i in range(3)]
    win.props.update({'_ringnav_count': 3, '_ringnav_scope': 0, '_ringnav_index': 1})  # a dynamic page: no scope
    return ui, win, tiles


@pytest.mark.parametrize('which', [MINIMAL, MODERN])
def test_a_pop_ups_button_is_framed_white_on_its_accent(fw, which):
    ui, win, tiles = buttons_page(fw, which)
    canvas = Canvas(fw)
    fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    assert canvas.frames[0][:2] == (tiles[1].box, 0xffffffff)  # the selected tile's own box
    assert canvas.restored()


# Classic's confirm pop-up: Form A's page or, for a row action's question, the tray.
def paint_bg(fw, w, canvas):
    return fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(w.addr, canvas.addr)


class Confirm:
    """Stock's confirminfo_dialog as dialog_confirminfo_dialog_init leaves it: full-screen and clear, its
    prompt's hscroll_label lines, maybe msg_delcheckbutton, and the asset's two buttons."""
    def __init__(self, fw, ui, lines=('Delete this song?',), check=None, cells=('Cancel', 'OK')):
        self.fw, self.ui = fw, ui
        self.win = ui.window('confirminfo_dialog', kind='dialog')
        self.lines = []
        for text in lines:
            self.lines.append(ui.add('hscroll_label', self.win, (0, 100, 375, 30)))
            ui.set_text(self.lines[-1], text)
        if check is not None:
            self.check = ui.add('check_button', self.win, (40, 124, 295, 40), 'msg_delcheckbutton', value=check)
            ui.set_text(self.check, 'Also delete source file')
        self.cancel = ui.add('button', self.win, (40, 250, 140, 50), 'img_cancel').clickable()
        self.enter = ui.add('button', self.win, (195, 250, 140, 50), 'img_enter').clickable()
        ui.set_text(self.cancel, cells[0])
        ui.set_text(self.enter, cells[1])
        self.canvas = Canvas(fw)

    def paint(self):
        paint_bg(self.fw, self.win, self.canvas)
        return self.canvas


def classic(fw, st, light=0, **config):
    theme(fw, CLASSIC, LIGHT=str(light).encode(), **config)
    st('geom').value = CLASSIC_SET
    for flag, value in (('g_backlight_status', 1), ('g_usblink_status', 0)):
        g(fw, flag, value)
    return Ui(fw)


def test_a_confirm_lays_its_buttons_side_by_side_centred_under_its_prompt(fw, st):
    confirm = Confirm(fw, classic(fw, st))
    confirm.paint()
    (x0, y0, w0, h0), (x1, y1, w1, h1) = confirm.cancel.box, confirm.enter.box
    assert (y0, h0) == (y1, h1) and x0 + w0 == x1  # one track, the cells abutting
    assert abs((x0 + x1 + w1) - 375) <= 1 and y0 + h0 <= 320  # centred, on the screen
    assert w0 > 6 * 10 and w1 > 2 * 10  # each wider than its text
    lx, ly, lw, lh = confirm.lines[0].box
    assert 2 * lx + lw == 375 and confirm.lines[0].props['style:normal:text_align_h'] == 'center'  # the prompt centred
    assert ly + lh <= y0  # over the cells
    for cell in (confirm.cancel, confirm.enter):  # confirm_labels draws their text
        assert all(cell.props[f'style:{s}:text_color'] == 0 for s in ('normal', 'pressed', 'over', 'focused'))
    assert confirm.win.props['style:normal:bg_color'] == 0  # clear: the bar and the page show through
    moves = len(fw.calls('widget_move_resize'))
    confirm.paint()
    assert len(fw.calls('widget_move_resize')) == moves  # dressed once: the cells' height says so


def test_a_cell_grows_with_its_text_past_a_least_width(fw, st):
    """Short words share one least width, wider than either; past it a cell grows as its text does."""
    widths = []
    for cells in (('No', 'Yes'), ('Cancel', 'Remove'), ('Cancel!!', 'Remove')):
        confirm = Confirm(fw, classic(fw, st), cells=cells)
        confirm.paint()
        widths.append((confirm.cancel.box[2], confirm.enter.box[2]))
    (no, yes), (cancel, remove), (longer, _) = widths
    assert no == yes > 3 * 10 and cancel == remove > no
    assert longer - cancel == 2 * 10  # two characters more, their width more


def test_a_long_prompt_line_breaks_at_its_last_space_that_fits(fw, st):
    text = 'Remove every song of this album from the playlist?'
    confirm = Confirm(fw, classic(fw, st), [text])
    confirm.paint()
    line, rest = confirm.lines[0], confirm.win.children[-1]
    head, tail = confirm.ui.text(line), confirm.ui.text(rest)
    x, y, w, h = line.box
    assert head + ' ' + tail == text and len(head) * 10 <= w < len(head + ' ' + tail.split()[0]) * 10
    assert rest.box == (x, y + h, w, h)  # the next line, under it
    assert y + 2 * h <= confirm.cancel.box[1]  # the cells under both


def test_a_long_prompt_with_nowhere_to_break_keeps_its_marquee(fw, st):
    confirm = Confirm(fw, classic(fw, st), ['x' * 60])
    confirm.paint()
    assert confirm.ui.text(confirm.lines[0]) == 'x' * 60 and len(confirm.win.children) == 3


@pytest.mark.parametrize('light', [0, 1])
def test_the_page_form_lays_paper_under_the_bar_and_the_track_under_the_cells(fw, st, layout, light):
    confirm = Confirm(fw, classic(fw, st, light))
    S = Scheme(fw, layout)
    canvas = confirm.paint()
    bar = SETS[CLASSIC_SET].bar
    assert ((0, bar, 375, 320 - bar), rgba(S.paper)) in canvas.rects
    x0, y0, w0, h0 = confirm.cancel.box
    track = [x for x in range(375) if canvas.px.get((x, y0 + h0 // 2)) != rgba(S.paper)]
    assert (min(track), max(track) + 1) == (x0, x0 + w0 + confirm.enter.box[2])  # the track spans both cells
    assert canvas.restored()


@pytest.mark.parametrize('light', [0, 1])
def test_the_pop_up_opens_on_cancel_and_the_wheel_lights_the_other(fw, st, layout, light):
    confirm = Confirm(fw, classic(fw, st, light))
    S = Scheme(fw, layout)
    canvas = confirm.paint()
    mid = lambda cell: canvas.px[cell.box[0] + cell.box[2] // 2, cell.box[1] + 15]
    assert confirm.win.props['_ringnav_index'] == 0
    assert mid(confirm.cancel) == rgba(S.sel_row(15, 30)) != mid(confirm.enter)
    texts = {t: c for t, r, c in canvas.texts if r[1] == confirm.cancel.box[1]}
    assert texts['Cancel'] != texts['OK'] or light  # dark: the unlit cell's text is dimmer
    confirm.win.props['_ringnav_index'] = 1
    confirm.paint()
    assert mid(confirm.enter) == rgba(S.sel_row(15, 30)) != mid(confirm.cancel)


def test_the_cells_texts_sit_over_their_shadows_a_row_up(fw, st):
    confirm = Confirm(fw, classic(fw, st))
    canvas = confirm.paint()
    for cell, text in ((confirm.cancel, 'Cancel'), (confirm.enter, 'OK')):
        drawn = [(r, c) for t, r, c in canvas.texts if t == text]
        (shadow, shade), (box, ink) = drawn
        assert box == cell.box and shadow == (box[0], box[1] - 1, box[2], box[3])
        assert shade >> 24 < 0xff and shade & 0xffffff == 0  # black, part transparent
    inks = {t: c for t, r, c in canvas.texts[1::2]}
    assert inks['Cancel'] == rgba(0xffffff) != inks['OK']  # the dark half's unlit cell is dimmer


def test_delete_files_check_is_drawn_where_the_icon_goes(fw, st, layout):
    """Stock centres the check's icon, its spacer and its text together, and the box is drawn in the
    icon's place: on the button's middle row, left of the text, a shorter text moving it right by
    half the width it lost. Checked, the box holds the tick."""
    confirm = Confirm(fw, classic(fw, st), check=0)
    S = Scheme(fw, layout)
    x0, y0, w0, h0 = confirm.check.box

    def inked(value, text):
        confirm.check.props['value'] = value
        confirm.ui.set_text(confirm.check, text)
        confirm.canvas = Canvas(fw)
        px = confirm.paint().px
        return {(x, y) for (x, y), c in px.items() if c == rgba(S.ink) and x0 <= x < x0 + w0 and y0 <= y < y0 + h0}

    ring = inked(0, 'Also delete source file')
    rx0, ry0, rx1, ry1 = thumb_box(ring)
    assert ry0 + ry1 == 2 * y0 + h0 and rx1 <= x0 + w0 // 2  # on the middle row, its text to its right
    tick = inked(1, 'Also delete source file') - ring
    tx0, ty0, tx1, ty1 = thumb_box(tick)
    assert rx0 < tx0 and tx1 < rx1 and ry0 < ty0 and ty1 < ry1  # inside the box
    assert abs((tx0 + tx1) - (rx0 + rx1)) <= 1 and abs((ty0 + ty1) - (ry0 + ry1)) <= 1  # in its middle
    assert thumb_box(inked(0, 'Delete source file')) == (rx0 + 25, ry0, rx1 + 25, ry1)  # 50 px of text fewer


def test_a_row_actions_question_is_the_tray_its_cell_the_verb(fw, st):
    ui = classic(fw, st)
    seen: dict = {}

    def open_dialog(name, prompt, *_):
        confirm = Confirm(fw, ui, ['Delete "Song"?'])
        confirm.canvas.art['classic_light_confirm_shadow'] = (315, 70)
        canvas = confirm.paint()
        seen['tray'] = (ui.text(confirm.enter), confirm.lines[0].visible, list(canvas.images),
                        [confirm.cancel.box, confirm.enter.box], {t: c for t, r, c in canvas.texts[1::2]})
        over = Confirm(fw, ui, ['Shut down?'])  # a prompt stock opens over it stays a question
        over.paint()
        seen['over'] = (ui.text(over.enter), over.lines[0].visible, over.lines[0].box, over.enter.box)
        return 1

    fw.mock('navigator_to_with_context', open_dialog)
    verb = C.create_string_buffer(b'Delete')
    assert fw.fn('playlists__confirm_over', C.c_int, C.c_void_p, C.c_char_p)(None, verb) == 1
    text, shown, images, cells, inks = seen['tray']
    assert text == 'Delete' and not shown  # the cell says what it's for; no question
    (ix, iy, iw, ih), = images  # the tray's shadow at its art's size, centred, the cells on it
    assert (iw, ih) == (315, 70) and 2 * ix + iw == 375
    assert all(ix <= x and x + w <= ix + iw and iy <= y and y + h <= iy + ih for x, y, w, h in cells)
    assert inks['Cancel'] == inks['Delete']  # lit or not, both cells' text alike
    text, shown, (lx, ly, lw, lh), (_, y, _, _) = seen['over']
    assert text == 'OK' and shown and ly + lh <= y  # its prompt asked, the cells under it
    after = Confirm(fw, ui, ['Shut down?'])  # the row action answered: the next confirm is a question
    after.paint()
    assert ui.text(after.enter) == 'OK' and after.lines[0].visible


def test_minimal_leaves_stocks_confirm(fw, st):
    theme(fw, MINIMAL)
    confirm = Confirm(fw, Ui(fw))
    box = confirm.cancel.box
    confirm.paint()
    assert confirm.cancel.box == box and 'style:normal:bg_color' not in confirm.win.props


# The toast and the update's dialog: a box sized to its lines.
def toast(fw, st, lines, name='msginfo_dialog'):
    ui = classic(fw, st)
    win = ui.window(name, kind='dialog')
    labels = []
    for text in lines:
        labels.append(ui.add('hscroll_label', win, (0, 140, 375, 30)))
        ui.set_text(labels[-1], text)
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    return win, labels, canvas


@pytest.mark.parametrize('lines', [['Done'], ['Saved', 'Cover Flow rebuilds artwork']])
def test_a_toasts_box_holds_its_lines_centred(fw, st, lines):
    win, labels, canvas = toast(fw, st, lines)
    (x, y, w, h), color, radius = canvas.boxes[0]
    assert abs(2 * x + w - 375) <= 1 and 0 < radius <= h // 2  # centred, its corners round
    assert color >> 24 < 0xff  # the page shows through it a little
    for label, text in zip(labels, lines):
        lx, ly, lw, lh = label.box
        assert 2 * lx + lw == 375 and label.props['style:normal:text_align_h'] == 'center'  # each line centred
        assert x < (375 - 10 * len(text)) // 2 and (375 + 10 * len(text)) // 2 < x + w  # its text inside the box
        assert y < ly and ly + lh < y + h
    ys = [label.box[1] for label in labels]
    assert [b - a for a, b in zip(ys, ys[1:])] == [labels[0].box[3]] * (len(ys) - 1)  # the lines abut
    assert all(label.props['_final_n'] for label in labels)  # white, in either half
    assert win.props['style:normal:bg_color'] == 0 and win.props['_toast'] == 1  # the window clear


def test_a_toast_grows_a_line_at_a_time_and_with_its_widest_line(fw, st):
    boxes = [toast(fw, st, lines)[2].boxes[0][0] for lines in (['Done'], ['Done'] * 2, ['Done'] * 3, ['Done', 'Done!!'])]
    one, two, three, wider = boxes
    pitch = two[3] - one[3]
    assert pitch > 0 and three[3] - two[3] == pitch and one[1] == two[1] == three[1]  # down from one top
    assert pitch == toast(fw, st, ['Done'])[1][0].box[3]  # a line's height a line
    assert (wider[2] - two[2], wider[3]) == (2 * 10, two[3])  # two characters wider


def test_a_line_too_long_scrolls_in_its_label_never_a_wider_box(fw, st):
    _, labels, canvas = toast(fw, st, ['x' * 40])
    (x, _, w, _), _, _ = canvas.boxes[0]
    assert w == labels[0].box[2] and abs(2 * x + w - 375) <= 1


def test_v134s_wrapping_label_gets_the_box_its_wrapped_lines_need(fw, st):
    """V1.36's plain toast is one word-wrapping label: AWTK sizes it, the box follows its height."""
    ui = classic(fw, st)
    win = ui.window('msginfo_dialog', kind='dialog')
    label = ui.add('label', win, (10, 100, 355, 120))
    ui.set_text(label, 'Rockbox Themes are ready in Rockbox\'s Theme Settings')
    fw.mock('label_resize_to_content', lambda w, lo, hi, lo_h, hi_h, *_: ui.nodes[w].move(*ui.nodes[w].box[:2], hi, 3 * lo_h))
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    (x, y, w, h), _, _ = canvas.boxes[0]
    lx, ly, lw, lh = label.box
    assert lw == w and lh == 3 * 24 and y < ly and ly + lh < y + h  # three lines inside the box
    assert label.props['style:normal:text_align_h'] == 'center' and label.props['_final_n']


def test_a_toast_moves_its_lines_once(fw, st):
    win, labels, canvas = toast(fw, st, ['Done'])
    moves = len(fw.calls('widget_move_resize'))
    paint_bg(fw, win, canvas)
    assert len(fw.calls('widget_move_resize')) == moves and len(canvas.boxes) == 2


def test_a_toast_is_the_same_dark_box_in_either_half(fw, st):
    """flip_if undoes the ink's Light Mode flip, to within the flip's rounding."""
    dark = toast(fw, st, ['Done'])[2].boxes[0][1]
    st('light').value = 1
    light = toast(fw, st, ['Done'])[2].boxes[0][1]
    assert dark >> 24 == light >> 24 and all(abs((dark >> s & 255) - (light >> s & 255)) <= 3 for s in (0, 8, 16))
    assert bright(light) < 3 * 0x80  # dark in the light half too


def test_the_serials_qr_takes_the_boxs_colour_on_its_own_rect(fw, st):
    (_, color, radius), = toast(fw, st, ['Done'])[2].boxes
    win, labels, canvas = toast(fw, st, ['SN'], name='showsn_dialog')
    assert win.props['style:normal:bg_color'] == i32(color) and win.props['style:normal:round_radius'] == radius
    assert not canvas.boxes and labels[0].box == (0, 140, 375, 30)  # no box: its lines where stock put them


def test_another_dialog_is_not_a_toast(fw, st):
    win, labels, canvas = toast(fw, st, ['?'], name='other_dialog')
    assert '_toast' not in win.props and not canvas.boxes


# Classic's separators: the last rows of each two-line row but the selected one's.
SEP = THEMES[CLASSIC]['sep']  # the dark half's stops, one a row


def detail_page(fw, st, rows=5, h=None, scroll=0, touch=0):
    """A page_list's rows, each page_detail high and named so, tappable, in a vertical view."""
    ui = classic(fw, st)
    h = h or THEMES[CLASSIC]['page_detail']
    win = ui.window('about_page')
    view = ui.add('scroll_view', win, (0, 26, 375, 294))
    view.byte_at(O['VIEW_VERTICAL']).value = 1
    view.int_at(O['VIEW_CONTENT_H']).value = rows * h
    view.int_at(O['SCROLL_Y']).value = scroll
    items = [ui.add('list_item', view, (0, i * h, 375, h), '_page_tall').clickable() for i in range(rows)]
    view.props.update({'_ringnav_count': rows, '_ringnav_scope': 1, '_ringnav_index': 1})
    st('touch_mode').value = touch
    canvas = Canvas(fw)
    paint_bg(fw, view, canvas)
    return view, items, canvas


def separated(canvas, y):
    """Whether the stops' rows end at y: one stop a row, across the list."""
    return all(canvas.row(y - len(SEP) + j) == [rgba(rgb)] * 375 for j, (_, rgb) in enumerate(SEP))


def test_every_detail_row_but_the_selected_ends_on_the_separator(fw, st):
    view, items, canvas = detail_page(fw, st)
    h = THEMES[CLASSIC]['page_detail']
    assert [separated(canvas, (i + 1) * h) for i in range(5)] == [1, 0, 1, 1, 1]
    assert canvas.restored() and canvas.clip == [0, 0, 375, 320]


def test_under_touch_every_row_is_separated(fw, st):
    _, _, canvas = detail_page(fw, st, touch=1)
    h = THEMES[CLASSIC]['page_detail']
    assert all(separated(canvas, (i + 1) * h) for i in range(5))


def test_separators_follow_the_scroll_and_stop_below_the_view(fw, st):
    h, scroll = THEMES[CLASSIC]['page_detail'], THEMES[CLASSIC]['page_detail'] + 10
    _, _, canvas = detail_page(fw, st, rows=10, scroll=scroll, touch=1)
    middles = sorted({y for (_, y), c in canvas.px.items() if c == rgba(SEP[1][1])})
    assert middles == [y for y in ((i + 1) * h - scroll - 2 for i in range(10)) if 0 <= y < 294]


def test_a_row_of_another_height_has_none(fw, st):
    _, _, canvas = detail_page(fw, st, h=40, touch=1)
    assert not canvas.px


def table(fw, st, art=(0, 0, 52, 0)):
    """A virtual table of two-line rows (taller than the set's pitch): each row a tappable table_row,
    its first child the leading image, with no width unless it has art."""
    ui = classic(fw, st)
    win = ui.window('allmusic_page')
    client = ui.add('table_client', win, (0, 26, 375, 294))
    row_h = SETS[CLASSIC_SET].pitch + 20
    client.int_at(O['ROW_HEIGHT']).value, client.int_at(O['TABLE_ROWS']).value = row_h, 4
    for i, width in enumerate(art):
        row = ui.add('table_row', client, (0, i * row_h, 375, row_h)).clickable()
        row.int_at(O['ROW_INDEX']).value = i
        ui.add('image', row, (0, 0, width, row_h))
        ui.add('hscroll_label', row, (10, 0, 300, 30))
    client.props.update({'_ringnav_count': 4, '_ringnav_scope': 1, '_ringnav_index': 0})
    st('touch_mode').value = 1
    canvas = Canvas(fw)
    strings(fw)
    paint_bg(fw, client, canvas)
    return row_h, canvas


def test_a_tables_two_line_rows_are_separated_but_not_rows_with_art(fw, st):
    row_h, canvas = table(fw, st)
    assert [separated(canvas, (i + 1) * row_h) for i in range(4)] == [1, 1, 0, 1]


def test_minimal_draws_no_separators(fw, st):
    theme(fw, MINIMAL)
    ui = Ui(fw)
    view, _ = library(fw, ui)
    st('touch_mode').value = 1
    canvas = Canvas(fw)
    paint_bg(fw, view, canvas)
    assert not canvas.px


# Marks on the selected row (ringnav_paint over an image): Classic's tick and More's dots, Minimal's card's
# radio rings, and the chevron.
class Mark:
    """An image in a row of a list, painted as AWTK paints a widget: its canvas at its own place."""
    def __init__(self, fw, st, which, image, name='', selected=True, size=(50, 48), **config):
        self.fw, self.st = fw, st
        theme(fw, which, **config)
        self.ui = Ui(fw)
        self.view, self.rows = library(fw, self.ui)
        row = self.rows[1] if selected else self.rows[2]
        self.w = self.ui.add('image', row, (280, 0, *size), name, image=image)
        st('sel_w', C.c_void_p).value = self.view.addr
        st('sel_widget', C.c_void_p).value = st('sel_accent', C.c_void_p).value = self.rows[1].addr
        self.canvas = Canvas(fw, x=300)

    def paint(self):
        self.st('paint_w', C.c_void_p).value = self.w.addr
        strings(self.fw)
        self.fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(self.w.addr, self.canvas.addr)
        return self.canvas


MIDDLE = (50, 48)  # Mark's image, 50 x 48: its middle, doubled


def middle(box):
    """A box's middle, doubled so it stays whole."""
    x, y, w, h = box
    return 2 * x + w, 2 * y + h


def dots_in_a_row(boxes):
    """Whether the boxes are round dots alike, side by side on one row and evenly apart."""
    (x, y, w, h), _, r = boxes[0]
    xs = [b[0][0] for b in boxes]
    return (all(b[0][1:] == (y, w, h) and b[2] == r for b in boxes) and w == h == 2 * r
            and len({b - a for a, b in zip(xs, xs[1:])}) == 1 and xs[1] - xs[0] > w)


@pytest.mark.parametrize('selected', [1, 0])
def test_classics_checked_row_has_its_tick_in_the_rows_ink(fw, st, layout, selected):
    mark = Mark(fw, st, CLASSIC, 'select', selected=selected)
    S = Scheme(fw, layout)
    canvas = mark.paint()
    tick = [p for p, c in canvas.px.items() if c == rgba(S.sel_text if selected else S.ink)]
    x0, y0, x1, y1 = thumb_box(tick)
    assert abs(x0 + x1 - MIDDLE[0]) <= 1 and abs(y0 + y1 - MIDDLE[1]) <= 1  # on the image's middle
    # a tick: its long stroke ends high on the right, its foot left of the middle, its short stroke
    # starting between the two
    assert all(2 * x > x0 + x1 for x, y in tick if y == y0) and all(2 * x < x0 + x1 for x, y in tick if y == y1 - 1)
    assert y0 < min(y for x, y in tick if x == x0) < y1 - 1
    assert canvas.restored()


def test_classics_unchecked_row_has_nothing(fw, st):
    assert not Mark(fw, st, CLASSIC, 'unselect').paint().px


@pytest.mark.parametrize('selected', [0, 1])
def test_a_playlists_quiet_more_is_three_grey_dots_off_the_band(fw, st, layout, selected):
    mark = Mark(fw, st, CLASSIC, 'unselect', 'img_more3', selected=selected)
    S = Scheme(fw, layout)
    boxes = mark.paint().boxes
    if selected:  # on the band its chevron takes their place
        assert boxes == []
        return
    assert len(boxes) == 3 and dots_in_a_row(boxes) and {b[1] for b in boxes} == {rgba(S.muted)}
    assert middle(boxes[1][0]) == MIDDLE  # the middle one on the image's middle


@pytest.mark.parametrize('image, dot', [('select', 1), ('unselect', 0)])
def test_minimals_card_redraws_its_radio_in_the_cards_ink(fw, st, layout, image, dot):
    """Stock's white ring and dot would vanish on the off-white card: a ring of ink with the card's
    fill in it, and the dot when checked, one inside the other on the image's middle."""
    mark = Mark(fw, st, MINIMAL, image, MINIMAL_ACCENT=b'7')
    S = Scheme(fw, layout)
    boxes = mark.paint().boxes
    assert len(boxes) == 2 + dot
    assert all(middle(box) == MIDDLE and box[2] == box[3] == 2 * r for box, _, r in boxes)  # discs, one middle
    sizes = [box[2] for box, _, _ in boxes]
    assert sizes == sorted(sizes, reverse=True) and len(set(sizes)) == len(sizes)  # ring, hole, dot
    (_, ink, _), (_, hole, _) = boxes[:2]
    assert hole == rgba(S.fill) and bright(ink) < bright(S.fill)  # dark ink on the card
    assert all(c == ink for _, c, _ in boxes[2:])


def test_minimals_radio_off_the_card_is_stocks(fw, st):
    assert not Mark(fw, st, MINIMAL, 'select', selected=False).paint().boxes


def test_minimals_card_redraws_more_dots_and_delete(fw, st, layout):
    mark = Mark(fw, st, MINIMAL, 'unselect', 'img_more0', MINIMAL_ACCENT=b'7')
    S = Scheme(fw, layout)
    (clear, fill, _), *dots = mark.paint().boxes
    assert fill == rgba(S.fill) and middle(clear) == MIDDLE  # stock's white dots wiped in the card's fill
    assert len(dots) == 3 and dots_in_a_row(dots) and all(bright(c) < bright(S.fill) for _, c, _ in dots)
    cx, cy, cw, ch = clear
    assert all(cx <= x and x + w <= cx + cw and cy <= y and y + h <= cy + ch for (x, y, w, h), _, _ in dots)
    mark = Mark(fw, st, MINIMAL, 'navbar_delete')
    mark.canvas.art['list_delete'] = art = (30, 30)
    canvas = mark.paint()
    (box, fill, _), = canvas.boxes  # list_delete's own size cleared, then it, on the image's middle
    assert fill == rgba(S.fill) and box[2:] == art and middle(box) == MIDDLE
    assert canvas.icons == [(art, MIDDLE[0] // 2, MIDDLE[1] // 2)]


def test_a_coloured_minimal_card_leaves_the_radio(fw, st):
    assert not Mark(fw, st, MINIMAL, 'select', MINIMAL_ACCENT=b'0').paint().boxes


@pytest.mark.parametrize('selected', [1, 0])
def test_classics_chevron_is_the_selected_rows_alone(fw, st, selected):
    mark = Mark(fw, st, CLASSIC, 'list_into', selected=selected)
    mark.canvas.art['classic_light_chevron'] = (12, 18)
    canvas = mark.paint()
    # the theme's box on screen, the canvas at the image's x
    assert canvas.icons == ([((12, 18), THEMES[CLASSIC]['chevron_x'] + 6 - 300, 24)] if selected else [])


def test_classics_folder_keeps_stocks_chevron(fw, st):
    mark = Mark(fw, st, CLASSIC, 'list_into', name='into')
    mark.ui.top.name = 'folder_page'
    mark.canvas.art['classic_light_chevron'] = (12, 18)
    assert not mark.paint().icons


@pytest.mark.parametrize('light', [1, 0])
def test_light_modes_chevron_on_a_coloured_bar_is_white(fw, st, layout, light):
    mark = Mark(fw, st, MODERN, 'list_into', LIGHT=str(light).encode(), ACCENT=b'1')
    mark.canvas.art[LIST_INTO_WHITE] = art = (50, 50)
    canvas = mark.paint()
    if not light:
        assert not canvas.icons and not canvas.boxes
        return
    # the glyph's middle half cleared in the bar's tone, then the white glyph, on the image's middle
    (box, color, _), = canvas.boxes
    mx, my = middle(box)
    assert box[2:] == (art[0], art[1] // 2) and mx == MIDDLE[0] and abs(my - MIDDLE[1]) <= 1
    tone = Scheme(fw, layout).tones[0]
    assert not neutral(tone) and color == rgba(tone)  # a coloured bar's own tone
    assert canvas.icons == [(art, MIDDLE[0] // 2, MIDDLE[1] // 2)]


@pytest.mark.parametrize('marked', [1, 0])
def test_a_bold_marked_label_paints_its_text_again_one_pixel_right(fw, marked):
    """font_weight.c, through the border hook: stock's self painter once more, 1 px right and back."""
    ui = Ui(fw)
    label = ui.add('label', ui.window('songs_page'), (0, 0, 200, 30), _q2_bold_fallback=marked)
    ui.set_text(label, '专辑')
    log: list = []
    fw.mock('canvas_translate', lambda c, x, y, *_: log.append(('translate', i32(x), i32(y))))
    fw.mock('widget_vtable_on_paint_self', lambda w, c, *_: log.append(('self', w)))
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(label.addr, Canvas(fw).addr)
    assert log == ([('translate', 1, 0), ('self', label.addr), ('translate', -1, 0)] if marked else [])


# ringnav_paint's own work around the painters.
def test_ringnav_paint_returns_stocks_and_hands_the_paint_to_the_parent(fw, st):
    """Stock paints a widget's border after its children: once the card's row's title is done, the
    row is painting again and takes the card's ink; once the row is done, its item, off the card."""
    page = Page(fw, MINIMAL, MINIMAL_ACCENT=b'7')
    page.paint()
    row = page.rows[1]
    card = text_ink(fw, st, row.children[0])
    fw.mock('stock_paint_trampoline', lambda *_: 5)
    paint = fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)
    assert paint(row.children[0].addr, Canvas(fw).addr) == 5  # stock's answer
    assert style_ink(fw) == card
    paint(row.addr, Canvas(fw).addr)
    assert style_ink(fw) != card


def cf_letter(fw, st, elapsed, drawn):
    theme(fw, MODERN)
    ui = Ui(fw)
    w = ui.add('slide_menu', ui.window('coverflow_page'), (0, 0, 375, 294))
    st('cf_letter_surface', C.c_void_p).value, st('cf_letter').value, st('cf_letter_at').value = w.addr, ord('M'), 1000
    fw.mock('time_now_ms', lambda *_: 1000 + elapsed)
    canvas = Canvas(fw)
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(w.addr, canvas.addr)
    box = O['LETTER_BOX']
    assert [b[0] for b in canvas.boxes] == [((375 - box) // 2, (294 - box) // 2, box, box)] * drawn
    assert [t[0] for t in canvas.texts] == ['M'] * drawn and canvas.restored()


@pytest.mark.parametrize('elapsed', [0, O['LETTER_MS'] - 1])
def test_cover_flows_letter_shows_for_its_time_centred(fw, st, elapsed):
    cf_letter(fw, st, elapsed, 1)


def test_cover_flows_letter_is_gone_after_its_time(fw, st):
    cf_letter(fw, st, O['LETTER_MS'], 0)


@pytest.mark.parametrize('same, kept', [(1, 1), (0, 0)])
def test_a_paint_ends_a_pending_centre_once_the_top_window_changed(fw, st, same, kept):
    ui = Ui(fw)
    g(fw, 'g_backlight_status', 1)
    first = ui.window('playset_page')
    if not same:
        ui.window('about_page')
    st('center_timer').value, st('center_top', C.c_void_p).value = 7, first.addr
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(first.addr, Canvas(fw).addr)
    assert st('center_timer').value == 7 * kept and len(fw.calls('timer_remove')) == 1 - kept


def test_a_paint_off_a_carousel_ends_homes_slide(fw, st):
    ui = Ui(fw)
    g(fw, 'g_backlight_status', 1)
    home = ui.window('home_page')
    st('home_surface', C.c_void_p).value = home.addr
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(home.addr, Canvas(fw).addr)
    assert st('home_surface', C.c_void_p).value == home.addr
    ui.window('about_page')
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(home.addr, Canvas(fw).addr)
    assert st('home_surface', C.c_void_p).value is None


@pytest.mark.parametrize('over', [0, 1])
def test_the_hold_menus_panel_has_its_header_and_rows(fw, st, over):
    """The navbar its header, the list its rows: before the dialog's children the panel, after them its
    frame; Cover Flow's flip panel is the same paint."""
    ui = classic(fw, st)
    dialog = ui.window('sortselect_dialog', kind='dialog')
    nav = ui.add('view', dialog, (40, 60, 295, 30), 'view_navbar')
    listed = ui.add('list_view', dialog, (40, 90, 295, 150), 'list_view')
    st('qm_dialog', C.c_void_p).value = dialog.addr
    canvas = Canvas(fw)
    fw.fn('ringnav_paint' if over else 'ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(dialog.addr, canvas.addr)
    rows = sorted({y for _, y in canvas.px})
    top, bottom = nav.box[1], nav.box[1] + nav.box[3] + listed.box[3]
    if over:  # the frame: a line above the header and below the rows
        assert {top - 1, bottom} <= set(rows)
    else:  # the panel from the header's top over header and rows
        assert (rows[0], rows[-1] + 1) == (top, bottom)
    assert canvas.restored()


# The settings pages' rows: stock's builders, then ours in the same widgets and styles.
def settings_page(fw, which, win_name, view_name, stock_rows=0, row_h=70, **config):
    """A settings window and its list, stock's own rows (each list_item named s<id>, row_h high, its
    button a settings row's) built by stock's init, as its trampoline does."""
    settings = theme(fw, which, **config)
    ui = Ui(fw)
    win = ui.window(win_name)
    view = ui.add('scroll_view', win, (0, 26, 375, 294), view_name)

    def build(*_):
        for i in range(stock_rows):
            item = ui.add('list_item', view, (0, 0, 375, row_h), f's{i}')
            ui.add('button', item, (20, 0, 335, row_h), str(i)).clickable()
        return 3
    fw.mock('fw___xstat', lambda *_: -1)  # no Rockbox on the card
    return ui, win, view, settings, build


def label_of(item):
    """A row's title: its first hscroll_label."""
    return next(c for c in item.walk() if c.kind == 'hscroll_label')


def row(ui, view, text):
    return next(item for item in view.children if any(ui.text(c) == text for c in item.walk()))


def switch(item):
    s = next((c for c in item.walk() if c.name == '_setting_switch'), None)
    return s and s.props['image']


def value(ui, item):
    v = next((c for c in item.walk() if c.name == '_setting_value'), None)
    return v and ui.text(v)


def click(ui, item):
    button = item.children[0]
    return ui.fire(button, EVT_CLICK)


def test_power_adds_standby_charge_and_in_vehicle_rows_after_stocks(fw):
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'powermanager_page', 'scroll_view_powermanager', 2)
    fw.mock('stock_power_trampoline', build)
    assert fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None) == 3
    stock, ours = view.children[:2], view.children[2:]
    assert [s.name for s in stock] == ['s0', 's1'] and len(ours) == 6
    icons = [next(c.props['image'] for c in r.walk() if c.kind == 'image') for r in ours]
    # Standby, Charge, USB DAC Charging, Smart Wi-Fi, In-Vehicle, Battery History
    assert icons == ['system_powermanager', 'usb_chargeswitch', 'usb_chargeswitch', 'wifiset_wifi', 'system_carmode',
                     'system_powermanager']
    standby, charge, *_, history = (ui.text(label_of(r)) for r in ours)
    assert standby.endswith('Off') and f'{O["CHARGE_STOP"]}%' in charge  # Standby off; where Charge stops
    assert history == 'Battery History' and 'list_into' in [c.props.get('image') for c in ours[-1].walk()]  # opens a page
    assert [switch(r) for r in ours] == [None, 'switch_off', 'switch_off', 'switch_off', 'switch_off', None]
    for r in ours:  # stock's settings row: its button, the title clear of the icon, both the row's height
        button, label = r.children[0], label_of(r)
        icon = next(c for c in r.walk() if c.kind == 'image')
        assert button.box == stock[0].children[0].box
        assert icon.box[0] + icon.box[2] <= label.box[0] and label.box[0] + label.box[2] <= button.box[2]
        assert icon.box[3] == label.box[3] == button.box[3]
    tr = [C.string_at(c[1][1]) for c in fw.calls('widget_set_tr_text')]
    assert tr == [b'system_carmode']  # stock's row, stock's translation


def test_standby_limit_cycles_its_four_choices_and_saves_each(fw):
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    standby = view.children[0]
    texts = []
    for _ in range(4):
        click(ui, standby)
        texts.append(ui.text(label_of(standby)))
    hours = [O[f'STANDBY_LIMIT_{k}'] for k in (1, 2, 3)]
    assert [re.findall(r'\d+', t) for t in texts] == [[str(h)] for h in hours] + [[]]  # each limit's hours, then none
    assert texts[-1].endswith('Off')
    assert settings.written == [('STANDBYLIMIT', v) for v in (1, 2, 3, 0)]


def test_charge_limit_switches_and_takes_effect_on_the_next_pass(fw, st):
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    charge = view.children[1]
    st('charge_at', C.c_uint).value = 12345
    click(ui, charge)
    assert switch(charge) == 'switch_on' and settings.written == [('CHARGELIMIT', 1)] and st('charge_at', C.c_uint).value == 0
    click(ui, charge)
    assert switch(charge) == 'switch_off' and settings.written[-1] == ('CHARGELIMIT', 0)


def dac_power(fw, **charging):
    """The Power page over usb_charge.c's guard, its settings loaded (test_usb_charge.Charging); its USB DAC
    Charging row."""
    from test_usb_charge import Charging
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'powermanager_page', 'scroll_view_powermanager')
    q = Charging(fw, **charging)
    q.config()
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    dac = view.children[2]
    assert ui.text(label_of(dac)) == 'USB DAC Charging'
    return ui, win, dac, q


def test_usb_dac_charging_is_the_dac_screens_choice_and_drives_the_pin_at_once(fw):
    ui, win, dac, q = dac_power(fw, pref=1, cable=1)  # Audio and DAC, a cable in
    assert switch(dac) == 'switch_on' and q.gpio == 0
    click(ui, dac)
    assert q.flag.value == 0 and q.saved == [('PLAYSET', 'USBDAC_CHARGE', 0)] and switch(dac) == 'switch_off'
    assert q.gpio == 1  # inhibited now, not at the next poll
    click(ui, dac)
    assert q.flag.value == 1 and q.saved[-1] == ('PLAYSET', 'USBDAC_CHARGE', 1) and switch(dac) == 'switch_on'
    assert q.gpio == 0


def test_usb_dac_charging_on_charges_after_the_dac_screens_off_and_off_leaves_usb_storage_charging(fw):
    ui, win, dac, q = dac_power(fw, pref=0, cable=1)
    q.charge(0)  # the DAC screen's switch turned Off: stock's switch_charge_enable(0)
    click(ui, dac)
    assert q.gpio == 0
    q.usbmode.value = 0  # USB Storage: Off is for Audio and DAC only
    click(ui, dac)
    assert q.flag.value == 0 and q.gpio == 0


def test_the_dac_screens_switch_shows_in_the_row_when_power_is_back(fw):
    ui, win, dac, q = dac_power(fw, pref=1)
    q.flag.value = 0  # the DAC screen's switch, over this page
    ui.fire(win, O['EVT_WINDOW_FOREGROUND'])
    assert switch(dac) == 'switch_off'


def test_charge_limits_hold_wins_over_usb_dac_charging_on(fw, st):
    ui, win, dac, q = dac_power(fw, pref=0, cable=1)
    st('charge_held').value = 1
    q.charge(0)  # charge_poll's hold
    click(ui, dac)
    assert q.flag.value == 1 and q.gpio == 1
    st('charge_held').value = 0
    q.charge(1)  # its hand-back at CHARGE_RESUME
    assert q.gpio == 0


def test_in_vehicle_mode_is_stocks_byte_and_key(fw):
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    g(fw, 'g_carmode_state', 4)
    standby, charge, dac, smart, car, history = view.children
    click(ui, car)
    assert (g(fw, 'g_carmode'), g(fw, 'g_carmode_state')) == (1, 0) and settings.written == [('CARMODE', 1)]
    assert switch(car) == 'switch_on'


def test_smart_wifi_switches_and_saves_its_own_key(fw, st):
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    car_mode = g(fw, 'g_carmode')
    standby, charge, dac, smart, car, history = view.children
    click(ui, smart)
    assert switch(smart) == 'switch_on' and settings.written == [('SMARTWIFI', 1)] and st('smart_wifi').value == 1
    assert switch(charge) == switch(car) == 'switch_off' and g(fw, 'g_carmode') == car_mode  # the other rows as they were
    click(ui, smart)
    assert switch(smart) == 'switch_off' and settings.written[-1] == ('SMARTWIFI', 0) and st('smart_wifi').value == 0


def test_classic_shows_a_switch_as_its_value(fw, st):
    ui, win, view, settings, build = settings_page(fw, CLASSIC, 'powermanager_page', 'scroll_view_powermanager', CHARGELIMIT=b'1')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    standby, charge, dac, smart, car, history = view.children
    assert value(ui, charge) == 'On' and value(ui, smart) == value(ui, car) == 'Off' and switch(charge) is None
    assert f'{O["CHARGE_STOP"]}%' in ui.text(label_of(charge))
    v = next(c for c in charge.walk() if c.name == '_setting_value')
    title, width = label_of(charge), charge.children[0].box[2]
    # the value in the trailing column to the row's end, the title ending short of it
    assert title.box[0] + title.box[2] < v.box[0] and width // 2 < v.box[0] and v.box[0] + v.box[2] >= width
    assert ui.text(label_of(standby)).endswith('Off') and value(ui, standby) is None  # not a switch: its text whole
    click(ui, charge)
    assert value(ui, charge) == 'Off' and len([c for c in charge.walk() if c.name == '_setting_value']) == 1


@pytest.mark.parametrize('bt', [1, 0])
def test_bluetooth_adds_resume_and_device_volume_after_its_fourth_row(fw, bt):
    """Iconless and as high as stock's rows (78 px on this page), each saving its own key."""
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'bluetooth_page', 'scroll_view_bluetooth', 7, row_h=78)
    fw.mock('stock_btrows_trampoline', build)
    g(fw, 'g_bluetoothflag', bt)
    fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    rows = view.children
    if not bt:
        assert [r.name for r in rows] == [f's{i}' for i in range(7)]
        return
    ours = rows[4:6]
    assert [r.name for r in rows[:4] + rows[6:]] == [f's{i}' for i in range(7)]
    for r in ours:
        click(ui, r)
    assert [key for key, _ in settings.written] == ['RESUME', 'BTVOLLINK']  # Resume on Connect, Control Device Volume
    assert [r.box[3] for r in ours] == [rows[0].box[3]] * 2
    beside = ui.add('scroll_view', win, (0, 0, 375, 100), 'beside')  # a settings row with an icon
    with_icon = ui.nodes[fw.fn('list_row', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p)(beside.addr, b'icon', None, None)]
    assert [c.name for r in ours for c in r.walk() if c.kind == 'image'] == ['_setting_switch'] * 2  # no icon
    assert [label_of(r).box[0] for r in ours] == [with_icon.parent.children[0].box[0]] * 2  # in the icon's place


@pytest.mark.parametrize('way, sent', [(1, 1), (0, 0)])
def test_device_volume_off_gives_stock_its_gain_back_at_once(fw, way, sent):
    ui, win, view, settings, build = settings_page(fw, MINIMAL, 'bluetooth_page', 'scroll_view_bluetooth', 0, BTVOLLINK=b'1')
    g(fw, 'g_bluetoothflag', 1)
    g(fw, 'g_volume', 37)
    fw.mock('mclGetOutputWay', lambda *_: way)
    fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    link = row(ui, view, 'Control Device Volume')
    bt_link = fw.fn('navigation__bt_link', C.c_int)
    assert switch(link) == 'switch_on' and bt_link() == 1
    click(ui, link)
    assert switch(link) == 'switch_off' and settings.written == [('BTVOLLINK', 0)] and bt_link() == 0
    assert [c[1][:2] for c in fw.calls('device_set_volume')] == [(37, 0)] * sent


def playset(fw, ui, view, stock_rows=13):
    """Stock's Playback rows, s0-s12: s8 Headphones required, s11 Volume and Album Display, s12 Folder skip."""
    def build(*_):
        for i in range(stock_rows):
            item = ui.add('list_item', view, (0, 0, 375, 70), f's{i}')
            ui.add('button', item, (20, 0, 335, 70)).clickable()
        return 0
    fw.mock('stock_audioset_trampoline', build)


def icon(item):
    return next((c.props.get('image') for c in item.walk() if c.kind == 'image' and c.box[0] < 72), None)


def test_playback_orders_its_rows_and_ends_on_library_and_folders(fw):
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'playset_page', 'scroll_view_playset')
    playset(fw, ui, view)
    fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    rows = view.children
    ids = [r.name for r in rows]
    # stock's Volume and Album Display is not offered; Folder skip is Library & Folders'
    assert len(rows) == 19 and 's11' not in ids and 's12' not in ids
    at = {r.name or icon(r): k for k, r in enumerate(rows)}
    assert rows[-1] is row(ui, view, 'Library & Folders') and 'list_into' in [c.props.get('image') for c in rows[-1].walk()]
    assert (at['s3'], at['s2']) == (0, 1)  # Play Mode, then EQ
    assert at['playset_gapless'] == at['s7'] + 1 and at['playset_memplay'] == at['s6'] + 1  # Crossfade, Resume Songs
    assert at['s8'] == at['playset_memplay'] + 1  # Headphones required, after Resume Songs
    balance = [k for k, r in enumerate(rows) if icon(r) == 'playset_balance']
    assert balance == [at['s10'] + 1, at['s10'] + 2]  # Mono Audio and Swap L/R
    assert at['s5'] == at['s4'] + 1 == 17  # DSD Output, then USB Audio last but one
    # that page's rows, where V1.32 had them: the two volumes before Balance, Enlarged Album Art after Swap L/R
    assert [at['playset_maxvol'], at['playset_bootvol'], at['s10']] == list(range(at['s9'] + 1, at['s9'] + 4))
    assert at['playset_covermode'] == balance[1] + 1


def test_playback_s_volume_rows_open_stock_s_pages_and_enlarged_art_is_stock_s_switch(fw):
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'playset_page', 'scroll_view_playset')
    playset(fw, ui, view)
    g(fw, 'g_bigcovermode', 0)
    fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    ours = {icon(r): r for r in view.children if not r.name}
    opened = []
    fw.mock('navigator_to', lambda name, *_: opened.append(C.string_at(name).decode()))
    for key in ('playset_maxvol', 'playset_bootvol'):
        assert 'list_into' in [c.props.get('image') for c in ours[key].walk()]
        click(ui, ours[key])
    assert opened == ['playset/maxvol_page', 'playset/bootvol_page']
    art = ours['playset_covermode']
    assert switch(art) == 'switch_off' and ui.text(label_of(art)) == 'Enlarged Album Art'
    # stock's rows, stock's translations
    assert [C.string_at(c[1][1]) for c in fw.calls('widget_set_tr_text')] == [b'playset_maxvol', b'playset_bootvol', b'playset_covermode']
    click(ui, art)
    assert switch(art) == 'switch_on' and g(fw, 'g_bigcovermode') == 1 and settings.written == [('COVERMODE', 1)]
    click(ui, art)
    assert switch(art) == 'switch_off' and g(fw, 'g_bigcovermode') == 0 and settings.written[-1] == ('COVERMODE', 0)


def test_library_and_folders_is_stocks_page_again_with_the_library_rows(fw, st):
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'playset_page', 'scroll_view_playset')
    fw.var('fw_ctx_artist_type', C.c_int).value = 1
    playset(fw, ui, view)
    audioset = fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)
    audioset(win.addr, None)
    sub = {}

    def open_page(name, *_):
        page = ui.window('playset_page')
        nav = ui.add('view', page, (0, 0, 375, 26), 'view_navbar')
        ui.add('label', nav, (0, 0, 375, 26), 'title')
        sub_view = ui.add('scroll_view', page, (0, 26, 375, 294), 'scroll_view_playset')
        playset(fw, ui, sub_view)
        audioset(page.addr, None)
        sub.update(name=C.string_at(name), title=ui.text(nav.children[0]), rows=list(sub_view.children))
        return 0

    fw.mock('navigator_to', open_page)
    opener = row(ui, view, 'Library & Folders')
    click(ui, opener)
    assert sub['name'] == b'playset/playset_page' and sub['title'] == ui.text(label_of(opener))  # titled as its row
    ours = sub['rows'][:4]
    skip, art = sub['rows'][4:]
    # stock's Folder skip, then Folder Album Cover (a row of stock's Volume and Album Display), last
    assert skip.name == 's12' and icon(art) == 'playset_foldercover' and ui.text(label_of(art)) == 'Folder Album Cover'
    assert C.string_at(fw.calls('widget_set_tr_text')[-1][1][1]) == b'playset_foldercover'  # stock's row, stock's translation
    was = g(fw, 'g_foldercover_flag')
    for r in ours + [art]:
        click(ui, r)
    # the library's rows, by the setting each saves: Artists, Artist album order, Year order, Folder Root, Folder Album Cover
    assert [key for key, _ in settings.written] == ['ARTISTTYPE', 'ARTISTAZ', 'YEAROLD', 'FOLDERROOT', 'FOLDERCOVER']
    assert g(fw, 'g_foldercover_flag') == (not was) and switch(art) == ('switch_off' if was else 'switch_on')
    again = ui.add('scroll_view', ui.window('playset_page'), (0, 26, 375, 294), 'scroll_view_playset')
    playset(fw, ui, again)
    audioset(ui.top.addr, None)
    assert len(again.children) == 19  # the next open is the main page again


def test_artists_toggles_stocks_artist_type(fw):
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'playset_page', 'scroll_view_playset')
    fw.var('fw_ctx_artist_type', C.c_int).value = 1
    fw.fn('pod_rows', None, C.c_void_p, C.c_char_p, C.c_int, C.c_int, C.c_void_p)(win.addr, b'scroll_view_playset', 4, 1, (C.c_char_p * 1)(b'x'))
    artists = view.children[0]
    assert ui.text(label_of(artists)) == 'Artists: Album Artist'
    click(ui, artists)
    assert fw.var('fw_ctx_artist_type', C.c_int).value == 0 and ui.text(label_of(artists)) == 'Artists: Artist'
    assert settings.written == [('ARTISTTYPE', 0)]


def pod_rows(fw, win, view_name, first, n):
    fw.fn('pod_rows', None, C.c_void_p, C.c_char_p, C.c_int, C.c_int, C.c_void_p)(
        win.addr, view_name.encode(), first, n, (C.c_char_p * n)(*[b'icon'] * n))


def test_cover_flow_sort_steps_and_refresh_says_when(fw):
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'display_page', 'scroll_view_display')
    Files(fw)
    toasts = []
    fw.mock('navigator_to_with_context', lambda name, info, *_: toasts.append(C.string_at(info + 8)) and 0)
    assert POD['POD_CF_REFRESH'] == POD['POD_CF_SORT'] + 1  # one call builds both
    pod_rows(fw, win, 'scroll_view_display', POD['POD_CF_SORT'], 2)
    sort, refresh = view.children
    first, action = ui.text(label_of(sort)), ui.text(label_of(refresh))
    click(ui, sort)
    assert ui.text(label_of(sort)) != first and not toasts  # the next sort, shown
    click(ui, refresh)
    assert ui.text(label_of(refresh)) == action and fw.calls('image_manager_unload_all')
    assert len(toasts) == 1 and toasts[0]  # nothing in view changes: a toast says when



def test_scan_song_durations_turned_off_cancels_a_pass(fw):
    from test_durations import Ds
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'display_page', 'scroll_view_display', SCANDURATIONS=b'1')
    pod_rows(fw, win, 'scroll_view_display', POD['POD_SCAN_DURATIONS'], 1)
    state = fw.var('durations__ds', Ds)
    state.due = 1  # an update's end, its paths not read yet
    click(ui, view.children[0])
    assert settings.written == [('SCANDURATIONS', 0)] and state.cancel and not state.due


@pytest.mark.parametrize('was', [0, 1])
def test_key_tone_shows_click_with_headphones_while_on(fw, st, scratch, was):
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'controls_page', 'scroll_view_controls')
    g(fw, 'g_keytone_flag', was)
    pod_rows(fw, win, 'scroll_view_controls', POD['POD_KEYTONE'], 1)
    item = ui.add('list_item', view, (0, 0, 375, 70), 'click_hp')
    button = ui.add('button', item, (20, 0, 335, 70))
    hp = ui.add('hscroll_label', button, (72, 0, 260, 70))
    slot = scratch['setting_label'] + SETTING['SETTING_KEYTONEHP'] * C.sizeof(C.c_void_p)  # Click With Headphones' label
    C.c_void_p.from_address(C.addressof(fw.var('st', C.c_char)) + slot).value = hp.addr
    tone = view.children[0]
    assert switch(tone) == ('switch_on' if was else 'switch_off')
    click(ui, tone)
    assert g(fw, 'g_keytone_flag') == (not was) and item.visible == (not was)
    assert settings.written == [('KEYTONE', int(not was))] and [c[1][0] for c in fw.calls('widget_layout')] == [view.addr]


@pytest.mark.parametrize('on', [1, 0])
def test_eq_per_output_turned_off_forgets_the_outputs_eq(fw, on):
    config = Settings(fw, EQOUTPUT=str(on).encode())
    assert fw.fn('ringnav_eq_output', C.c_int)() == on
    fw.fn('ringnav_eq_output_toggle', None)()
    assert fw.fn('ringnav_eq_output', C.c_int)() == (not on) and config.written == [('EQOUTPUT', int(not on))]
    assert bool(fw.calls('fw_unlink')) == bool(on)  # off: what plays stays, the owner's file goes


# Classic's settings rows: the title left, the value right-aligned.
def set_text_row(fw, text):
    """navigation.c's row_label under Classic on a settings row: the row's button, title and value."""
    theme(fw, CLASSIC)
    ui = Ui(fw)
    button = ui.add('button', ui.add('list_item', ui.window('display_page')), (20, 0, 335, 70))
    label = ui.add('hscroll_label', button, (72, 0, 260, 70))
    fw.fn('navigation__row_label', None, C.c_void_p, C.c_char_p)(label.addr, text.encode())
    return ui, button, label, button.child('_setting_value')


@pytest.mark.parametrize('text, title, val', [('Theme: Classic', 'Theme', 'Classic'), ('Classic Blue', 'Accent', 'Classic Blue'),
                                              ('Wheel: 50%: x', 'Wheel', '50%: x')])
def test_a_set_text_rows_title_and_value(fw, text, title, val):
    ui, button, label, v = set_text_row(fw, text)
    assert (ui.text(label), ui.text(v)) == (title, val)
    (lx, ly, lw, lh), (vx, vy, vw, vh), width = label.box, v.box, button.box[2]
    assert (lx, ly, lh) == (72, 0, 70) and (vy, vh) == (0, 70)  # the title where it was, both the row's height
    assert lx + lw < vx and width // 2 < vx and vx + vw >= width  # the value trailing to the row's end, clear of the title
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_only_focus')] == [(v.addr, 1)]  # scrolls only focused
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_ellipses')] == [(v.addr, 1)]


def test_a_long_title_is_cut_and_keeps_its_value(fw):
    ui, button, label, v = set_text_row(fw, 'x' * 50 + ': y')
    title = ui.text(label)
    assert title and 'x' * 50 != title == 'x' * len(title) and ui.text(v) == 'y'


def test_a_rows_label_elsewhere_is_its_text(fw):
    theme(fw, MINIMAL)
    ui = Ui(fw)
    label = ui.add('hscroll_label', ui.add('button', ui.window('display_page')), (72, 0, 260, 70))
    fw.fn('navigation__row_label', None, C.c_void_p, C.c_char_p)(label.addr, b'Theme: Minimal')
    assert ui.text(label) == 'Theme: Minimal' and not label.parent.children[1:]


def test_the_librarys_rows_take_its_constructors_live_words(fw):
    """Under the Library's own list the row's body and icon x are the ipod.json words theme_boot wrote."""
    body, icon_x = O['LIBRARY_ROW_BODY'], O['LIBRARY_ROW_ICON_X']
    saved = C.c_ushort.from_address(body).value, C.c_ushort.from_address(icon_x).value
    try:
        C.c_ushort.from_address(body).value, C.c_ushort.from_address(icon_x).value = 52, 14
        ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'localmusic_page', 'scroll_view_localmusic')
        list_row = fw.fn('list_row', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p)
        label = ui.nodes[list_row(view.addr, b'music_artist', None, None)]
        other = ui.nodes[list_row(ui.add('scroll_view', win, (0, 26, 375, 294), 'scroll_view_playset').addr, b'icon', None, None)]
        button, icon = label.parent, label.parent.children[0]
        assert button.box[:3] == other.parent.box[:3] and button.box[3] == 52  # a settings row, the body's height
        assert icon.box[0] == 14 != other.parent.children[0].box[0] and icon.props['image'] == 'music_artist'
        assert icon.box[3] == label.box[3] == 52 and icon.box[0] + icon.box[2] <= label.box[0]
        assert not button.handlers  # no click: a row that only shows
    finally:
        C.c_ushort.from_address(body).value, C.c_ushort.from_address(icon_x).value = saved


# Buttons lock and Quick Settings.
def test_buttons_lock_lists_its_four_choices_and_a_pick_moves_the_check(fw):
    ui, win, view, settings, _ = settings_page(fw, MINIMAL, 'keylock_page', 'scroll_view_keylock')
    old = ui.add('list_item', view)
    assert fw.fn('ringnav_keylock_rows', C.c_int, C.c_void_p)(win.addr) == 0
    names = [ui.text(label_of(r)) for r in view.children]
    assert old not in view.children and len(set(names)) == 4 and all(names)  # four choices, named apart
    checks = lambda: [r.children[0].children[0].props['image'] for r in view.children]
    assert checks() == ['select', 'unselect', 'unselect', 'unselect']
    click(ui, view.children[2])
    assert checks() == ['unselect', 'unselect', 'select', 'unselect']
    assert (g(fw, 'g_keylock_flag'), g(fw, 'g_keylock_mode')) == (1, 2)  # stock's: 2 the buttons
    assert settings.written == [('LOCKMODE', 2), ('LOCKFLAG', 1)]


class Quick:
    """Quick Settings (statusbar_dialog): its icons and labels by stock's names, opened through its init."""
    ICONS = ['img_wifiswitch', 'img_btswitch', 'img_lock', 'img_gain', 'img_usbmode', 'img_po', 'img_playset', 'img_sysset']

    def __init__(self, fw, st, which=MINIMAL):
        self.fw, self.st = fw, st
        theme(fw, which)
        if which == CLASSIC:
            st('geom').value = CLASSIC_SET
        self.ui = Ui(fw)
        g(fw, 'g_backlight_status', 1)
        self.win = self.ui.window('statusbar_dialog', kind='dialog')
        self.menu = self.ui.add('view', self.win, (0, 40, 375, 200), 'view_menu')
        self.menu_was = self.menu.box
        self.icons = {n: self.ui.add('image', self.menu, (10 * i, 10, 50, 50), n) for i, n in enumerate(self.ICONS)}
        self.ui.add('label', self.menu, (0, 0, 80, 20), 'label_lock')
        for n in ('image0', 'image1'):
            self.ui.add('image', self.win, (0, 0, 10, 10), n)
        self.slider = self.ui.add('slider', self.ui.add('view', self.win, (0, 250, 375, 60)), (20, 10, 335, 30), 'slider_backlight')
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, i32(ms))) or len(self.timers))
        fw.mock('pointer_event_init', lambda out, kind, *_: C.memmove(out, struct.pack('<i', i32(kind)), 4) or out)
        self.dispatched: list = []
        fw.mock('widget_dispatch_event_to_target_recursive',
                lambda w, e, *_: self.dispatched.append((w, C.c_int.from_address(e).value)) and 0)
        self.opened = fw.fn('ringnav_quick_settings', C.c_int, C.c_void_p, C.c_void_p)(self.win.addr, None)

    def fn(self, name):
        return C.cast(getattr(self.fw.lib, ui_name(self.fw.lib, name)), C.c_void_p).value

    def press(self, name, x=30, y=40):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<iii', event, O['EVENT_X'], x, y, 0)
        self.event = event
        self.ui.pressed = 1
        return self.ui.fire(self.icons[name], O['EVT_POINTER_DOWN'], C.addressof(event))

    def dispatch(self, name, kind):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, O['EVENT_TYPE'], kind)
        return self.fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)(self.icons[name].addr, event)

    def hold(self):
        """The hold timer, run as the timer manager would at RATE_HOLD_MS."""
        fn = [f for f, ms in self.timers if f == self.fn('quick_long')][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def test_quick_settings_names_the_lock_and_arms_three_holds(fw, st):
    quick = Quick(fw, st)
    assert quick.ui.text(quick.win.child('label_lock')) == 'Off' and quick.icons['img_lock'].props['image'] == 'drop_keylock'
    assert [quick.icons[n].props.get('_quick_hold') for n in Quick.ICONS] == [1, 2, None, None, None, None, 3, None]
    down = quick.fn('quick_down')
    assert [len(quick.ui.handled(quick.icons[n], down)) for n in Quick.ICONS] == [1, 1, 0, 0, 0, 0, 1, 0]
    cancel = quick.fn('quick_cancel')
    assert quick.ui.handled(quick.win, cancel) == [quick.win.addr] * 3  # destroy, close, background


@pytest.mark.parametrize('name, page', [('img_wifiswitch', b'systemset/wifi_page'), ('img_btswitch', b'systemset/bluetooth_page'),
                                        ('img_playset', b'playset/equalizer_page')])
def test_a_held_icon_opens_its_page_and_its_release_doesnt_click(fw, st, name, page):
    quick = Quick(fw, st)
    quick.press(name)
    assert quick.timers[-1] == (quick.fn('quick_long'), O['RATE_HOLD_MS'])
    quick.hold()
    assert [C.string_at(c[1][0]) for c in fw.calls('navigator_to')] == [page]
    assert [c[1][0] for c in fw.calls('dialog_quit')] == [quick.win.addr]
    assert quick.dispatched == [(quick.win.addr, O['EVT_POINTER_ABORT'])]  # the press ends before the window does
    assert quick.dispatch(name, EVT_CLICK) == 11 and not fw.calls('stock_dispatch_trampoline')  # STOP
    quick.press(name)  # the next press is a press again
    assert quick.icons[name].props['_quick_hold'] > 0


def test_a_release_before_the_hold_is_stocks_tap(fw, st):
    quick = Quick(fw, st)
    quick.press('img_btswitch')
    quick.dispatch('img_btswitch', O['EVT_POINTER_UP_BEFORE'])
    assert fw.calls('timer_remove')
    quick.ui.pressed = 0
    quick.hold()  # a timer that fired anyway: the finger is up, nothing opens
    assert not fw.calls('navigator_to') and not fw.calls('dialog_quit')


def test_an_open_eq_page_is_already_the_goal(fw, st):
    quick = Quick(fw, st)
    fw.mock('navigator_window_is_exist', lambda name, *_: C.string_at(name) == b'equalizer_page')
    quick.press('img_playset')
    quick.hold()
    assert fw.calls('dialog_quit') and not fw.calls('navigator_to')


def test_a_tap_on_the_lock_steps_through_its_choices(fw, st):
    quick = Quick(fw, st)
    first = quick.ui.text(quick.win.child('label_lock'))
    names, images, locks = [], [], []
    for _ in range(4):
        fw.fn('ringnav_quick_lock', C.c_int, C.c_void_p, C.c_void_p)(quick.win.addr, None)
        names.append(quick.ui.text(quick.win.child('label_lock')))
        images.append(quick.icons['img_lock'].props['image'])
        locks.append((g(fw, 'g_keylock_flag'), g(fw, 'g_keylock_mode')))
    # stock's modes: 3 both, 2 the buttons, 1 the wheel's volume; then off
    assert locks == [(1, 3), (1, 2), (1, 1), (0, 3)]
    assert len(set(names)) == 4 and names[-1] == first  # each choice named, round to where it began
    assert len(set(images[:3])) == 1 and images[3] != images[0]  # one image locked, another not


def test_classics_page_lays_its_icons_on_a_grid_that_paints_nothing(fw, st):
    quick = Quick(fw, st, CLASSIC)
    boxes = [quick.icons[n].box for n in Quick.ICONS]
    top, bottom = boxes[:4], boxes[4:]
    (x, y, w, h), xs = top[0], [b[0] for b in top]
    assert all(b[2:] == (w, h) for b in boxes) and w == h  # one square size
    assert {b[1] for b in top} == {y} and len({b[1] for b in bottom}) == 1 and y + h < bottom[0][1]  # two rows apart
    assert [b[0] for b in bottom] == xs  # in the same columns
    assert len({b - a for a, b in zip(xs, xs[1:])}) == 1 and xs[1] - xs[0] > w  # evenly apart
    assert abs(xs[0] + xs[-1] + w - 375) <= 1  # centred
    mx, my, mw, mh = quick.menu.box
    assert (mx, my, mw, mh) == (0, 0, quick.menu_was[2], quick.menu_was[1] + quick.menu_was[3])  # top to its old bottom
    assert all(b[0] >= 0 and b[0] + b[2] <= mw and b[1] >= 0 and b[1] + b[3] <= mh for b in boxes)  # all on it
    assert [c[1][1] for c in fw.calls('widget_set_opacity')] == [0] * 8
    assert not quick.win.child('image0').visible and not quick.win.child('image1').visible
    # each icon a tap: Classic's press shows under the finger, its hold as before
    down = quick.fn('quick_down')
    assert all(len(quick.ui.handled(quick.icons[n], down)) == 1 for n in Quick.ICONS)
    moved = quick.fn('quick_moved')
    assert quick.ui.handled(quick.slider, moved) == [quick.slider.parent.addr] * 2
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(moved)(quick.slider.parent.addr, None)  # the knob reaches past it
    assert fw.calls('widget_invalidate_force')[-1][1][0] == quick.slider.parent.addr


def test_classics_pressed_icon_repaints_under_the_finger_and_after(fw, st):
    quick = Quick(fw, st, CLASSIC)
    quick.press('img_gain')
    assert [c[1][0] for c in fw.calls('widget_invalidate_force')] == [quick.icons['img_gain'].addr]
    quick.press('img_po')
    assert [c[1][0] for c in fw.calls('widget_invalidate_force')][1:] == [quick.icons['img_gain'].addr, quick.icons['img_po'].addr]
    quick.dispatch('img_po', O['EVT_POINTER_UP_BEFORE'])
    assert [c[1][0] for c in fw.calls('widget_invalidate_force')][-1] == quick.icons['img_po'].addr


def test_closing_quick_settings_forgets_the_press(fw, st):
    quick = Quick(fw, st, CLASSIC)
    quick.press('img_gain')
    quick.ui.fire(quick.win, EVT_DESTROY)
    quick.dispatch('img_gain', O['EVT_POINTER_UP_BEFORE'])  # nothing pressed now: nothing repainted
    assert [c[1][0] for c in fw.calls('widget_invalidate_force')] == [quick.icons['img_gain'].addr]


# A page's switch as a row, under a set without stock's toolbars.
def sleep_page(fw, st, image='switch_off', geom=CLASSIC_SET, which=CLASSIC):
    theme(fw, which)
    st('geom').value = geom
    ui = Ui(fw)
    win = ui.window('sleeptimer_page')
    toolbar = ui.add('view', win, (0, 270, 375, 50), 'view_toolbar')
    sw = ui.add('image', toolbar, (300, 0, 50, 50), 'img_switch', image=image)
    ui.add('view', win, (0, 60, 375, 200), 'view_sleep')
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(fn) or 1)
    fw.fn('ringnav_sleeptimer', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    return ui, win, sw, timers


def test_the_sleep_timers_switch_is_its_first_row_reading_the_switch(fw, st):
    ui, win, sw, timers = sleep_page(fw, st)
    label = win.child('_switch_row')
    button = label.parent
    s = SETS[CLASSIC_SET]
    assert button.box == (0, 0, 375, s.set_row) and button.props['style'] == 's_btn_listitem'
    assert label.box[0] == s.text_x and ui.text(label) == 'Sleep Timer' and value(ui, button) == 'Off'


def test_the_rows_click_is_the_switchs_on_the_next_pass(fw, st):
    ui, win, sw, timers = sleep_page(fw, st)
    fw.mock('stock_dispatch_trampoline', lambda target, *_: sw.props.__setitem__('image', 'switch_on') or 0)
    button = win.child('_switch_row').parent
    ui.fire(button, EVT_CLICK)
    assert not fw.calls('stock_dispatch_trampoline')  # a click rebuilds Lock Screen's list: it waits
    C.CFUNCTYPE(C.c_int, C.c_void_p)(timers[-1])(None)
    assert [c[1][0] for c in fw.calls('stock_dispatch_trampoline')] == [sw.addr]
    assert value(ui, button) == 'On'


def test_a_set_with_toolbars_keeps_stocks_switch(fw, st):
    ui, win, sw, timers = sleep_page(fw, st, geom=0)
    assert not any(c.name == '_switch_row' for c in win.walk())


def test_lock_screens_switch_row_heads_its_list(fw, st):
    theme(fw, MINIMAL)
    st('geom').value = CLASSIC_SET
    ui = Ui(fw)
    win = ui.window('lockscreen_page')
    ui.add('image', win, (300, 270, 50, 50), 'img_switch', image='switch_on')
    view = ui.add('scroll_view', win, (0, 26, 375, 294), 'scroll_view_lock')
    ui.add('list_item', view, name='first')
    fw.fn('switch_row', None, C.c_void_p, C.c_void_p)(win.addr, view.addr)
    head = view.children[0]
    assert head.name != 'first' and ui.text(label_of(head)) == 'Lock Screen' and switch(head) == 'switch_on'


# The library scan's refresh of a row it already holds.
MAP = 0x100022  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
ROW_HIGHS = high_halves(2)  # song_row's
libc.munmap.argtypes = [C.c_void_p, C.c_size_t]


@contextlib.contextmanager
def mapped(*pages):
    """The pages (address, size) mapped where asked for the test; whatever got mapped is unmapped after,
    whether the setup failed or not."""
    held = []
    try:
        for at, size in pages:
            got = libc.mmap(at, size, 3, MAP, -1, 0)
            if got not in (None, C.c_void_p(-1).value):
                held.append((got, size))
            assert got == at, f'{at:#x} is taken'
        yield
    finally:
        for got, size in held:
            libc.munmap(got, size)


@pytest.fixture
def song_row():
    """A songtable row with its name (+8) and path (+0xc), as a 64-bit read of each finds them: on the
    Q2 they are 4-byte pointers, here P() reads 8, so the name's high half is the path's low half.
    Their pages sit at addresses that make that so, at this file's two high halves (conftest.high_halves):
    the name at the first << 32, the path at the second << 32 | the first. The trick holds two pointers
    only: the CUE branch reads the row's tags at a 4-byte stride (0x10, 0x14...), which no 64-bit layout
    holds, so it belongs to test/patch.py, which runs the MIPS build (docs/building.md, Limits)."""
    name_at, path_at = ROW_HIGHS[0] << 32, ROW_HIGHS[1] << 32 | ROW_HIGHS[0]
    record = C.create_string_buffer(0x60)

    def fill(name, path, cue=(0, 0)):
        C.memmove(name_at, name + b'\0', len(name) + 1)
        C.memmove(path_at, path + b'\0', len(path) + 1)
        struct.pack_into('<Q', record, O['REC_NAME'], name_at)
        struct.pack_into('<I', record, O['REC_PATH'] + 4, path_at >> 32)  # with the name's high half, the path
        struct.pack_into('<ii', record, O['REC_CUE_START'], *cue)
        return C.addressof(record)
    with mapped((name_at, 0x1000), (path_at & ~0xfff, 0x10000)):
        yield fill


class Scan:
    """toolsGetMusicInfo's tags for the file, TagLib's open, and the statements the database got."""
    FIELDS = {'album': 0, 'songer': 0x100, 'style': 0x200, 'name': 0x280, 'composer': 0x480, 'albumsonger': 0x580}

    def __init__(self, fw, readable=1, info=1, exists=1, **tags):
        self.tags = {'album': b'Record', 'songer': b'Band', 'style': b'Rock', 'name': b'Song', 'composer': b'Writer',
                     'albumsonger': b'The Band'} | tags
        self.numbers = (1999, 7, 2)
        self.sql: list = []
        Files(fw)
        fw.mock('toolsCheckItemExist', lambda *_: exists)
        fw.mock('taglib_file_new', lambda *_: 0x5000)
        fw.mock('taglib_file_tag', lambda *_: readable)
        fw.mock('toolsGetMusicInfo', lambda a, out, path, *_: self.info(out) if info else 0)
        fw.mock('toolsHanziToPinyin', lambda s, key, *_: C.memmove(key, C.string_at(s) + b'\0', len(C.string_at(s)) + 1) and 0)
        fw.mock('toolsTrimLeft', lambda s, *_: self.trim(s, bytes.lstrip))
        fw.mock('toolsTrimRight', lambda s, *_: self.trim(s, bytes.rstrip))
        fw.mock('toolsUpdateDbTable', lambda db, sql, *_: self.sql.append((C.string_at(db), C.string_at(sql).decode())) and 0)
        self.refresh = fw.fn('ringnav_scan_refresh', C.c_int, C.c_void_p, C.c_char_p, C.c_int)

    def info(self, out):
        for key, at in self.FIELDS.items():
            C.memmove(out + at, self.tags[key] + b'\0', len(self.tags[key]) + 1)
        C.memmove(out + 0x680, struct.pack('<3i', *self.numbers), 12)
        return 1

    @staticmethod
    def trim(s, how):
        value = how(C.string_at(s))
        C.memmove(s, value + b'\0', len(value) + 1)


def test_a_rescanned_song_keeps_its_row_and_takes_its_files_tags(fw, song_row):
    """Only that row (its name, path and CUE bounds) is updated: its ID, creation time and play count stay,
    so saved queues and favourites still name the same song."""
    scan = Scan(fw)
    assert scan.refresh(song_row(b'01 Song.flac', b'/mnt/mmc/Band/01 Song.flac'), b'songtable', 0) == 1
    assert scan.sql == [(b'/mnt/data/database.db',
                         "update songtable set album='Record',songer='Band',style='Rock',name='Song',composer='Writer',"
                         f"albumsonger='The Band',Initial={ord('S')},year=1999,track=7,cd_num=2 where filename='01 Song.flac' "
                         "and fileurl='/mnt/mmc/Band/01 Song.flac' and time=0 and time_end=0")]


def test_a_quote_in_a_tag_or_path_is_doubled(fw, song_row):
    scan = Scan(fw, songer=b"Guns N' Roses")
    scan.refresh(song_row(b"It's.flac", b"/mnt/mmc/It's.flac"), b'songtable', 0)
    sql = scan.sql[0][1]
    assert "songer='Guns N'' Roses'" in sql and "filename='It''s.flac'" in sql and "fileurl='/mnt/mmc/It''s.flac'" in sql


@pytest.mark.parametrize('title, initial', [(b'abc', 'A'), (b'  zed', 'Z'), (b'9 Lives', '*'), (b'!Hey', '#'), (b'', 'N'), (b'(null)', 'N')])
def test_the_initial_is_the_titles_else_the_file_names(fw, song_row, title, initial):
    scan = Scan(fw, name=title)
    scan.refresh(song_row(b'name.flac', b'/mnt/mmc/name.flac'), b'songtable', 0)
    assert f',Initial={ord(initial)},' in scan.sql[0][1]


@pytest.mark.parametrize('readable, info', [(0, 1), (1, 0)])
def test_a_file_without_readable_tags_keeps_the_old_ones(fw, song_row, readable, info):
    scan = Scan(fw, readable=readable, info=info)
    assert scan.refresh(song_row(b'a.flac', b'/mnt/mmc/a.flac'), b'songtable', 0) == 1 and not scan.sql


def test_a_statement_that_wont_fit_is_never_run(fw, song_row):
    scan = Scan(fw)
    assert scan.refresh(song_row(b'a.flac', b'/mnt/mmc/' + b"'" * 4200), b'songtable', 0) == 1 and not scan.sql


# The crash log's records (crash_note): the file in progress, and every nth a line with demo's memory.
class Proc:
    """open/read/write/close over files in memory: /proc/self/statm's pages, and what the C wrote."""
    def __init__(self, fw, statm=b'5000 1234 300 10 0 900 0\n'):
        self.files = {'/proc/self/statm': statm} if statm is not None else {}
        self.fds: dict = {}
        self.flags: list = []
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', lambda fd, out, n, *_: self.read(fd, out, n))
        fw.mock('fw_write', lambda fd, data, n, *_: self.write(fd, C.string_at(data, n)))
        fw.mock('fw_close', lambda fd, *_: self.fds.pop(fd) and 0)

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        if not flags & 3 and path not in self.files:
            return -1
        self.flags.append((path, flags & 0xffffffff))
        if flags & 0x200:
            self.files[path] = b''
        self.files.setdefault(path, b'')
        fd = 10 + len(self.fds)
        self.fds[fd] = [path, 0]
        return fd

    def read(self, fd, out, n):
        path, at = self.fds[fd]
        data = self.files[path][at:at + n]
        C.memmove(out, data, len(data))
        self.fds[fd][1] += len(data)
        return len(data)

    def write(self, fd, data):
        self.files[self.fds[fd][0]] += data
        return len(data)


def crash_note(fw, n, path, every=3):
    fw.fn('crash_note', None, C.c_char_p, C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)(path, b'/tmp/file', b'/tmp/log', every, n)


def test_the_crash_log_holds_the_file_in_progress_and_every_nth_with_demos_memory(fw):
    proc, n = Proc(fw), (C.c_uint * 2)()
    for i in range(1, 8):
        crash_note(fw, n, b'/mnt/mmc/%d.flac' % i)
        assert proc.files['/tmp/file'] == b'/mnt/mmc/%d.flac\n' % i
    assert proc.files['/tmp/log'] == b'3 4936 /mnt/mmc/3.flac\n6 4936 /mnt/mmc/6.flac\n'  # resident pages of 4 kB
    assert list(n) == [7, len(proc.files['/tmp/log'])]
    log_opens = [f for p, f in proc.flags if p == '/tmp/log']  # the Q2's (MIPS) flags: O_APPEND 8, O_TRUNC 0x200
    assert log_opens[0] & 0x200 and not log_opens[0] & 8 and log_opens[1] & 8 and not log_opens[1] & 0x200


def test_a_new_demo_starts_the_log_afresh_and_it_stops_at_64_kb(fw):
    proc, n = Proc(fw), (C.c_uint * 2)()
    proc.files['/tmp/log'] = b'last demo\n'
    crash_note(fw, n, b'/a', every=1)
    assert proc.files['/tmp/log'] == b'1 4936 /a\n'
    n[1] = 65536
    crash_note(fw, n, b'/b', every=1)
    assert proc.files['/tmp/log'] == b'1 4936 /a\n' and proc.files['/tmp/file'] == b'/b\n'


def test_a_long_path_is_cut_in_the_log_and_unreadable_memory_is_0(fw):
    proc, n = Proc(fw, statm=None), (C.c_uint * 2)()
    crash_note(fw, n, b'/' + b'p' * 700, every=1)
    assert proc.files['/tmp/log'] == b'1 0 /' + b'p' * 499 + b'\n'


def test_the_library_scan_notes_each_file_it_reads(fw):
    proc = Proc(fw)
    Files(fw)
    info = (C.c_char * 0x698)()
    for i in range(500):
        fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(0, info, b'/mnt/mmc/%d.mp3' % i)
    assert proc.files['/tmp/q2-scan-file'] == b'/mnt/mmc/499.mp3\n'
    assert proc.files['/tmp/q2-scan.log'] == b'500 4936 /mnt/mmc/499.mp3\n'


# An Ogg file whose comment header is over OGG_TAGS_MAX: xiph.c reads it, not TagLib.
def comments(*items, vendor=b'libopus'):
    return struct.pack('<I', len(vendor)) + vendor + struct.pack('<I', len(items)) + b''.join(struct.pack('<I', len(c)) + c for c in items)


def opus(tags):
    """An Ogg Opus file: OpusHead, then tags as packet 1 on pages of its own (no CRC: xiph.c reads none)."""
    pages, seq = [], 0
    for packet in (b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0), tags):
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        for at in range(0, len(lacing), 255):
            part = lacing[at:at + 255]
            pages.append(b'OggS\0' + bytes([at > 0]) + bytes(8) + struct.pack('<III', 1, seq, 0) + bytes([len(part)]) +
                         bytes(part) + packet[at * 255:at * 255 + sum(part)])
            seq += 1
    return b''.join(pages)


BIG = b'P=' + b'v' * (4 << 20)


def fix_id3(fw):
    """Stock's toolsFixId3ErrorCode as it treats UTF-8 text: out is the text."""
    fw.mock('toolsFixId3ErrorCode', lambda latin, latin1, utf8, out, k, *_: C.memmove(out, C.string_at(utf8) + b'\0', len(C.string_at(utf8)) + 1) and 0)


@pytest.mark.parametrize('a', [0, 1])
def test_an_oversized_ogg_header_is_read_field_by_field(fw, a):
    genre = b' / '.join(b'g%02d' % i + b'x' * 20 for i in range(8))
    Files(fw, {'/mnt/mmc/a.opus': opus(b'OpusTags' + comments(
        b'TITLE=Song', b'ARTIST=Band', b'ALBUM=Record', b'GENRE=' + genre, b'COMPOSER=Writer', b'ALBUMARTIST=The Band',
        b'DATE=1999', b'TRACKNUMBER=7', b'DISCNUMBER=2', b'MQAENCODER=x', BIG))})
    fix_id3(fw)
    info = (C.c_char * 0x698)()
    assert fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(a, info, b'/mnt/mmc/a.opus') == 1
    at = lambda o: C.string_at(C.addressof(info) + o)
    assert [at(o) for o in (0x280, 0x100, 0, 0x480, O['MUSIC_INFO_ALBUM_ARTIST'])] == [b'Song', b'Band', b'Record', b'Writer', b'The Band']
    assert at(0x200) == b' / '.join(genre.split(b' / ')[:5]) and len(at(0x200)) == 0x7f  # whole entries in 0x7f bytes
    year, disc, track, flags, rate = struct.unpack_from('<5i', info, 0x680)
    assert (year, disc, track) == (1999, 2, 7) and flags & 0x100  # MQA
    assert (rate, flags & 16) == ((48000, 16) if a else (0, 0))  # the audio properties, asked for
    assert not fw.calls('stock_music_info_trampoline')


def test_a_small_ogg_header_is_taglibs(fw):
    Files(fw, {'/mnt/mmc/a.opus': opus(b'OpusTags' + comments(b'TITLE=Song'))})
    fw.mock('stock_music_info_trampoline', lambda *_: 1)
    info = (C.c_char * 0x698)()
    fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(0, info, b'/mnt/mmc/a.opus')
    assert len(fw.calls('stock_music_info_trampoline')) == 1


# A card app's song in a library queue takes its file's tags as it starts.
@pytest.fixture
def low():
    """A page under 4 GB: strings whose addresses fit the Q2's 4-byte record fields."""
    at = 0x10000000
    used = [at]

    def string(s):
        here = used[0]
        C.memmove(here, s + b'\0', len(s) + 1)
        used[0] += (len(s) + 16) & ~7
        return here
    with mapped((at, 0x10000)):
        yield string


@pytest.fixture
def playing(fw, mcl, low):
    """The queue's playing record: its path a 4-byte field, as the Q2's, and no album."""
    deques = Deques(fw)
    record = C.create_string_buffer(0x60)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new([C.addressof(record)])
    mcl.pos, mcl.type = 0, 0xf001
    path = low(b'/mnt/mmc/Deezer/a.flac')
    struct.pack_into('<I', record, O['REC_PATH'], path)
    strings(fw)
    fw.mock('toolsGetMusicInfo', lambda a, out, p, *_: card_info(out))
    return record, path


def card_info(out):
    for at, value in ((0x280, b'Song'), (0x100, b'Band'), (O['MUSIC_INFO_ALBUM_ARTIST'], b'The Band')):
        C.memmove(out + at, value + b'\0', len(value) + 1)
    C.memmove(out + 0x684, struct.pack('<ii', 4, 2), 8)
    return 1


def id3_path(fw, path):
    id3 = (C.c_char * 0xa84)()
    fw.fn('ringnav_id3_path', C.c_void_p, C.c_void_p, C.c_char_p)(id3, path)
    return id3


def test_a_card_apps_song_takes_its_files_tags_as_it_starts(fw, playing):
    record, path = playing
    id3 = id3_path(fw, C.string_at(path))
    field = lambda o: C.string_at(struct.unpack_from('<Q', record, o)[0])
    assert [field(O[k]) for k in ('REC_TITLE', 'REC_ARTIST', 'REC_ALBUM_ARTIST')] == [b'Song', b'Band', b'The Band']
    assert struct.unpack_from('<ii', record, O['REC_TRACK']) == (4, 2)
    album = O['ID3_ALBUM']
    assert [C.string_at(C.addressof(id3) + album + o) for o in (0x280, 0x100, O['MUSIC_INFO_ALBUM_ARTIST'])] == [b'Song', b'Band', b'The Band']
    assert C.string_at(id3) == C.string_at(path)


@pytest.mark.parametrize('case', ['album', 'folder', 'other', 'off card'])
def test_a_song_that_has_its_tags_or_isnt_a_card_apps_is_left(fw, playing, low, mcl, case):
    record, path = playing
    if case == 'album':
        struct.pack_into('<I', record, O['REC_ALBUM'], low(b'Record'))
    elif case == 'folder':
        mcl.type = 1  # stock parses a folder play's file itself
    elif case == 'off card':
        struct.pack_into('<I', record, O['REC_PATH'], low(b'/mnt/usb/a.flac'))
    id3_path(fw, b'/mnt/usb/a.flac' if case == 'off card' else C.string_at(path) + (b'x' if case == 'other' else b''))
    assert struct.unpack_from('<I', record, O['REC_TITLE'])[0] == 0 and struct.unpack_from('<i', record, O['REC_TRACK'])[0] == 0


# The paused seek, and Bluetooth's start on connect.
@pytest.mark.parametrize('status, held', [(3, 43), (2, 0)])
def test_a_paused_seek_waits_for_play(fw, mcl, status, held):
    deques = Deques(fw)
    record = C.create_string_buffer(0x60)
    struct.pack_into('<i', record, O['REC_CUE_START'], 120)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new([C.addressof(record)])
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.mock('player_playtime_and_length', lambda sec, total, *_: C.memmove(sec, struct.pack('<i', 42), 4) and 0)
    assert fw.fn('navigation__seek_position', C.c_int, C.c_int)(30) == held
    if status == 3:  # stock's seek resumes: the player's own, at the CUE track's offset, plays nothing
        assert [c[1][0] for c in fw.calls('mclSetSeek')] == [150] and not fw.calls('player_seek_time')
    else:
        assert [c[1][0] for c in fw.calls('player_seek_time')] == [30] and not fw.calls('mclSetSeek')


@pytest.mark.parametrize('resume, saved, start', [(1, 0, (1,)), (0, 2, (0,)), (0, 3, (1,)), (0, 1, ()), (0, 0, ())])
def test_a_headset_resumes_only_what_was_going(fw, resume, saved, start):
    Settings(fw, RESUME=str(resume).encode())
    fw.mock('mclGetSavePlayStatus', lambda *_: saved)
    fw.mock('player_save_start', lambda pause, *_: 5)
    got = fw.fn('ringnav_bt_connect_start', C.c_int, C.c_int)(1)
    assert [c[1][0] for c in fw.calls('player_save_start')] == list(start) and got == (5 if start else 0)


# The lyrics' follow (LYRIC_FOLLOW) and the page's rebuild of its rows (LYRIC_ROWS).
def lyrics(fw, st, ui, rows=20, row_h=20, h=178, np=False, page=1):
    g(fw, 'g_backlight_status', 1)
    win = ui.window('playing_page')
    lrc = ui.add('scroll_view', win, (0, 60, 375, h), 'scroll_lrc')
    for i in range(rows):
        ui.add('label', lrc, (0, i * row_h, 375, row_h))
    lrc.int_at(O['VIEW_CONTENT_H']).value = rows * row_h
    if np:
        slide = ui.add('slide_view', win, (0, 0, 375, 320), 'slide', value=page)
        st('np_lrc', C.c_void_p).value, st('np_slide', C.c_void_p).value, st('np_win', C.c_void_p).value = lrc.addr, slide.addr, win.addr
    return lrc


def follow(fw, lrc, y, ms=300):
    return fw.fn('ringnav_lyric_follow', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int)(lrc.addr, 0, y, ms)


def test_the_lyrics_end_on_a_whole_row(fw, st):
    """178 px is no whole number of 20 px rows: the content grows to the first row top at or past the
    end plus the view, so the last offset shows a row whole at the top."""
    lrc = lyrics(fw, st, Ui(fw))
    follow(fw, lrc, 60)
    content = lrc.int_at(O['VIEW_CONTENT_H']).value
    assert (content - 178) % 20 == 0 and 400 - 178 <= content - 178 < 400 - 178 + 20
    assert [c[1][:4] for c in fw.calls('scroll_view_scroll_to')] == [(lrc.addr, 0, 60, 300)]


@pytest.mark.parametrize('held, spun, pressed', [(1, 0, 0), (0, 2, 0), (0, 0, 1)])
def test_lyrics_the_wheel_or_a_finger_moved_stay(fw, st, held, spun, pressed):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True)
    st('lyric_held').value, st('lyric_spun').value = held, spun
    ui.pressed = pressed
    assert follow(fw, lrc, 60) == 0 and not fw.calls('scroll_view_scroll_to')


def clock(fw, start=1000):
    """time_now_ms: the list's one item, which the test moves on."""
    now = [start]
    fw.mock('time_now_ms', lambda *_: now[0])
    return now


def glided(fw, lrc, now, passes=60, ms=16):
    """The offsets the glide's steps (lyric_step, each UI pass, ms apart on the clock now) move the
    lyrics through, the view taking each one as stock's setter does."""
    fw.mock('scroll_view_set_offset', lambda w, x, y, *_: struct.pack_into('<i', lrc.block, O['SCROLL_Y'], i32(y)))
    step = fw.fn('navigation__lyric_step', None, C.c_void_p)
    seen = []
    for _ in range(passes):
        now[0] += ms
        step(lrc.addr)
        seen.append(lrc.int_at(O['SCROLL_Y']).value)
    return seen


def eased(offsets, start, end):
    """Whether the offsets ease from start to end: the first step part of the way, each on from the
    last, none past the end, and there."""
    path = [start] + offsets
    return start < offsets[0] < end and all(a <= b <= end for a, b in zip(path, path[1:])) and offsets[-1] == end


def test_on_the_lyrics_page_the_follow_glides(fw, st):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True)
    animator = C.create_string_buffer(8)
    struct.pack_into('<Q', lrc.block, O['VIEW_ANIMATOR'], C.addressof(animator))
    now = clock(fw)
    follow(fw, lrc, 60)
    assert not fw.calls('scroll_view_scroll_to') and fw.calls('widget_animator_destroy')  # no jump
    assert struct.unpack_from('<Q', lrc.block, O['VIEW_ANIMATOR'])[0] == 0  # stock's animator stopped
    assert eased(glided(fw, lrc, now), 0, 60)  # the steps carry it there
    follow(fw, lrc, 10000)  # within the content: no further than its end
    assert eased(glided(fw, lrc, now), 60, lrc.int_at(O['VIEW_CONTENT_H']).value - 178)


def test_off_the_lyrics_page_the_follow_scrolls(fw, st):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True, page=0)
    st('lyric_glide').value = 1
    follow(fw, lrc, 60)
    assert st('lyric_glide').value == 0 and fw.calls('scroll_view_scroll_to')


LYRIC_ROWS = 0x53b67c  # stock's rebuild of scroll_lrc's rows, which ringnav_lyric_rows calls


def rows_rebuilt(fw, win, result=0):
    """ringnav_lyric_rows over stock's builder at its address: x86-64 standing in, returning result,
    and the page as conftest's map had it again after (bytes and protection), as nothing else runs there."""
    page = LYRIC_ROWS & ~0xfff
    saved = C.string_at(LYRIC_ROWS, 6)
    claim(LYRIC_ROWS)
    C.memmove(LYRIC_ROWS, b'\xb8' + struct.pack('<I', result) + b'\xc3', 6)  # mov eax, result; ret
    try:
        return fw.fn('ringnav_lyric_rows', C.c_int, C.c_void_p)(win.addr)
    finally:
        C.memmove(LYRIC_ROWS, saved, 6)
        assert not libc.mprotect(page, 0x2000, 3)


def test_a_rebuild_keeps_the_offset_for_the_same_song(fw, st):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui)
    lrc.int_at(O['SCROLL_Y']).value = 120
    assert rows_rebuilt(fw, ui.top, 9) == 9
    assert [c[1][:3] for c in fw.calls('scroll_view_set_offset')] == [(lrc.addr, 0, 120)]


def test_rows_for_another_song_start_at_the_top(fw, st):
    """Whatever held the old song's lyrics goes with them: the page's follow moves the new ones, and
    the rows are this song's, so the next rebuild keeps their offset."""
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True)
    offsets = lambda: [c[1][:3] for c in fw.calls('scroll_view_set_offset')]
    lrc.int_at(O['SCROLL_Y']).value = 120
    st('lyric_rows_song', C.c_uint).value = 0x1234  # the last rows' song; nothing plays now
    st('lyric_held').value = 1  # the wheel moved them
    rows_rebuilt(fw, ui.top)
    assert offsets() == [(lrc.addr, 0, 0)]
    # held no more: the page's follow (off the page, a jump) takes them to the playing line, none yet
    lrc.int_at(O['SCROLL_Y']).value = 50
    fw.fn('navigation__lyric_follow', None, C.c_void_p, C.c_int, C.c_int, C.c_int)(lrc.addr, 0, 0, 0)
    assert offsets() == [(lrc.addr, 0, 0)] * 2
    rows_rebuilt(fw, ui.top)
    assert offsets()[2:] == [(lrc.addr, 0, 50)]


# The status bar: the codec badge's fade, the percentage's width, Classic's dark bar.
RET_REPEAT = 8  # AWTK's ret_t: a timer that answers it runs again, any other answer ends it


def test_a_codec_badge_fades_out_into_the_glyph_and_the_glyph_fades_in(fw, st):
    ui = Ui(fw)
    bt = ui.add('image', None, (0, 0, 30, 20), 'img_bt', image='bar_ldac')
    st('bar_bt', C.c_void_p).value = bt.addr
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(i32(ms)) or 3)
    fade = fw.fn('navigation__codec_fade', C.c_int, C.c_void_p)
    steps = O['CODEC_STEPS']
    answers, opacity, images = [], [], []
    for _ in range(2 * steps):
        answers.append(fade(None))
        opacity.append(fw.calls('widget_set_opacity')[-1][1][1])
        images.append(bt.props['image'])
    out, back = opacity[:steps], opacity[steps - 1:]  # CODEC_STEPS steps each way
    assert all(a > b for a, b in zip(out, out[1:])) and out[-1] == 0  # out to nothing
    assert all(a < b for a, b in zip(back, back[1:])) and back[-1] == 255  # in to whole
    swap = images.index(BT_GLYPH)  # the badge becomes the glyph unseen, and stays
    assert opacity[swap] == 0 and set(images[:swap]) == {'bar_ldac'} and set(images[swap:]) == {BT_GLYPH}
    # the CODEC_MS one-shot that ran the first step ends, a CODEC_STEP_MS timer repeats the rest, then ends
    assert answers[0] != RET_REPEAT and timers == [O['CODEC_STEP_MS']]
    assert answers[1:-1] == [RET_REPEAT] * (2 * steps - 2) and answers[-1] != RET_REPEAT
    assert st('codec_timer', C.c_uint).value == 0


@pytest.mark.parametrize('level, digits', [(5, 1), (42, 2), (100, 3)])
def test_the_percentage_is_as_wide_as_its_digits(fw, st, level, digits):
    width = fw.fn('navigation__pct_width', C.c_int, C.c_uint)
    assert width(level) == O['BATT_PCT_W']  # until the first paint measures
    st('pct_digit').value, st('pct_sign').value = 11, 13
    assert width(level) == digits * 11 + 13


class DarkBar(Bar):
    def __init__(self, fw, st):
        theme(fw, CLASSIC)
        st('geom').value = CLASSIC_SET
        super().__init__(fw, 2)
        fw.mock('fw___xstat', lambda *_: -1)
        self.w['label_title'].move(60, 3, 200, 20)
        self.w['view_battery'].move(300, 2, 30, 20)
        self.w['img_state'].move(345, 2, 20, 20)  # right of the battery, where the dark bar's isn't
        self.ui.set_text(self.w['label_title'], 'Songs')
        styles = {b'text_align_h': 1, b'text_color': i32(0xff00ff00)}
        fw.mock('style_get_int', lambda style, key, default, *_: styles.get(C.string_at(key), i32(default)))
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or 5)
        self.sync()  # stock's showface: finds the bar's widgets

    def to(self, name):
        self.ui.window(name)
        self.fw.fn('navigation__bar_sync', None, C.c_void_p)(self.bar.addr)


def test_a_dark_page_takes_the_bars_title_across_in_white_and_hides_the_clock(fw, st):
    bar = DarkBar(fw, st)
    bar.to('usbmode_page')
    title = bar.w['label_title']
    assert title.box[:3] == (0, 3, 375) and title.props['style:normal:text_align_h'] == 'center'
    assert title.props['_final_n'] and not bar.w['label_clock'].visible
    assert bar.timers == [C.cast(fw.lib.bar_repaint, C.c_void_p).value]
    C.CFUNCTYPE(C.c_int, C.c_void_p)(bar.timers[0])(None)  # the repaint, after this paint
    assert fw.calls('widget_invalidate_force')[-1][1][0] == bar.bar.addr and st('bar_timer', C.c_uint).value == 0


def test_the_dark_bars_latin_title_drops_a_cjk_pages_bold_mark(fw, st):
    bar = DarkBar(fw, st)
    title = bar.w['label_title']
    bar.ui.set_text(title, '专辑')
    fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(title.addr, 2, -2)  # ROLE_BAR, FONT_ROW: the CJK page's
    assert title.props['_q2_bold_fallback'] == 1
    bar.to('usbmode_page')
    fw.fn('bar_title_sync', None, C.c_void_p)(bar.ui.top.addr)
    assert bar.ui.text(title) == 'Q2 Pod' and title.props['_q2_bold_fallback'] == 0


def test_the_dark_bars_marks_take_its_places_and_stay(fw, st):
    bar = DarkBar(fw, st)
    bar.to('usbmode_page')
    w = bar.w
    assert all(w[n].box[0] + w[n].box[2] <= 0 for n in ('img_bt', 'img_wifi'))  # off screen, where a relayout finds them
    (sx, sy, sw, sh), (bx, by, bw, bh) = state, slot = w['img_state'].box, w['view_battery'].box
    assert all(0 <= x and x + ww <= 375 and 0 <= y and y + h <= bar.bar.box[3] for x, y, ww, h in (state, slot))  # on the bar
    assert 2 * sx + sw < 2 * bx and by <= sy + sh // 2 < by + bh  # the play state left of the battery, on its line
    moves = len(fw.calls('widget_move_resize'))
    fw.fn('navigation__bar_sync', None, C.c_void_p)(bar.bar.addr)
    assert len(fw.calls('widget_move_resize')) == moves


def test_leaving_a_dark_page_puts_the_title_back_as_it_was(fw, st):
    bar = DarkBar(fw, st)
    bar.to('usbmode_page')
    bar.to('about_page')
    title = bar.w['label_title']
    assert title.box[:3] == (60, 3, 200) and bar.w['label_clock'].visible
    assert (title.props['style:normal:text_align_h'], title.props['style:normal:text_color']) == (1, i32(0xff00ff00))
    assert title.props['_final_n'] == 0  # its own ink again, not the dark bar's white
    assert fw.calls('widget_layout_children')[-1][1][0] == bar.bar.addr and st('clock_key', C.c_uint).value == 0


def test_the_bar_stays_light_off_classic(fw, st):
    bar = DarkBar(fw, st)
    st('theme').value = MINIMAL
    bar.to('usbmode_page')
    assert bar.w['label_clock'].visible and bar.w['img_bt'].box[0] != -400


@pytest.mark.parametrize('titled, shown', [(0, '6:05 PM'), (1, '6:05')])
def test_the_clocks_timer_shows_the_minute_and_rearms(fw, st, titled, shown):
    """Beside Classic's bar title the 12-hour clock drops AM and PM, which would reach the title."""
    ui = Ui(fw)
    bar = ui.window('statusbar', top=False)
    clock = ui.add('label', bar, (150, 0, 75, 26), 'label_clock')
    if titled:
        st('bar_title', C.c_void_p).value = ui.add('label', bar, (0, 0, 140, 26), 'label_title').addr
    fw.var('fw_ctx_system_bar', C.c_void_p).value = bar.addr
    Settings(fw)
    fw.mock('fw_time', lambda *_: 1700000000)
    fw.mock('fw_localtime_r', lambda when, out, *_: C.memmove(out, struct.pack('<3i', 40, 5, 18), 12) and out)
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append((fn, i32(ms))) or 6)
    st('clock_timer', C.c_uint).value = 9
    assert fw.fn('clock_due', C.c_int, C.c_void_p)(None) == 0
    assert ui.text(clock) == shown and timers == [(C.cast(fw.lib.clock_due, C.c_void_p).value, 20000)]
    assert not fw.calls('timer_remove')  # the firing timer is done: nothing else to stop


# A tap's row takes the selection (ringnav_dispatch's click).
def tap(fw, target):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, O['EVENT_TYPE'], EVT_CLICK)
    return fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)(target.addr, event)


def test_a_tapped_row_takes_the_selection(fw, st):
    page = Page(fw, MODERN)
    fw.mock('stock_dispatch_trampoline', lambda *_: 4)
    assert tap(fw, page.rows[2].children[0]) == 4  # its title: the row that owns it
    assert page.view.props['_ringnav_index'] == 2 and st('touch_mode').value == 1
    assert page.view.addr in [c[1][0] for c in fw.calls('widget_invalidate_force')]


def test_a_tap_in_one_pane_clears_the_others(fw, st):
    page = Page(fw, MODERN)
    other = page.ui.add('scroll_view', page.ui.top, (0, 200, 375, 120), 'other')
    other.byte_at(O['VIEW_VERTICAL']).value = 1
    item = page.ui.add('list_item', other, (0, 0, 375, 48))
    page.ui.add('button', item, (0, 0, 375, 48)).clickable()
    other.props['_ringnav_index'] = 0
    tap(fw, page.rows[3])
    assert other.props['_ringnav_index'] == -1 and page.view.props['_ringnav_index'] == 3
    assert other.addr in [c[1][0] for c in fw.calls('widget_invalidate_force')]


def test_a_tap_off_every_row_changes_nothing(fw, st):
    page = Page(fw, MODERN)
    tap(fw, page.view)
    assert page.view.props['_ringnav_index'] == 1


@pytest.mark.parametrize('which, revealed', [(CLASSIC, 1), (MODERN, 0)])
def test_classic_brings_a_cut_row_whole_when_tapped(fw, st, which, revealed):
    page = Page(fw, which, rows=8)
    st('geom').value = CLASSIC_SET if which == CLASSIC else 0
    tap(fw, page.rows[6])  # 288 to 336 in a 294 px view
    offsets = [i32(c[1][2]) for c in fw.calls('scroll_view_set_offset')]
    assert bool(offsets) == revealed
    if revealed:
        assert offsets[0] + 294 >= 336 and offsets[0] <= 288
