"""peq_ui.c's EQ page end to end on the device path (docs/internals.md#3eq): opened through peq_page_init and
driven only through what it registers (rows' clicks, the edit's events, keyup, the tab bar's and sliders'
touches, Return, closing), eq_key and peq_paint, with ringnav's real list_toggle, row_into and center_press
between, and the EQ state on a card of fopen and friends. EQ per Output (eq_output) too. test_misc.py has
the page's rules a function at a time; test/peq.py the same flows through its own shim."""
import ctypes as C
import struct

import pytest

from conftest import O

KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_RETURN = O['KEY_CENTER'], O['KEY_PREV'], O['KEY_NEXT'], O['KEY_RETURN']
EVT_DESTROY, EVT_VALUE_CHANGED, EVT_CLICK, EVT_FOCUS, EVT_KEY_UP, EVT_IM_ACTION = (O[k] for k in (
    'EVT_DESTROY', 'EVT_VALUE_CHANGED', 'EVT_CLICK', 'EVT_FOCUS', 'EVT_KEY_UP', 'EVT_IM_ACTION'))
EVT_POINTER_DOWN, EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE = (O[k] for k in (
    'EVT_POINTER_DOWN', 'EVT_POINTER_MOVE_BEFORE', 'EVT_POINTER_UP_BEFORE'))
EVENT_KEY, EVENT_X, EVENT_Y, W_PARENT, IM_WIDGET, IM_KEYBOARD = (
    O['EVENT_KEY'], O['EVENT_X'], O['EVENT_Y'], O['W_PARENT'], O['IM_WIDGET'], 0x0c)
RET_REMOVE, RET_REPEAT = 7, 8
ACTIVE, SAVED, IMPORT, OUTPUTS = '/mnt/data/peq-active', '/mnt/data/peq-presets', '/mnt/mmc/EQ', '/mnt/data/peq-outputs'
EQ_OFF, EQ_3, EQ_PEQ = range(3)
HOME, BAND, PRESETS, IMPORTS, SAVES, CONFIRM, DELETES, PICK, ADJUST, STOCKS, RENAMES, NAMING = range(12)
SIMPLE, ADVANCED = range(2)
E3_TABS, E3_SWITCH, E3_PRESETS, E3_RESET, E3_LOW, E3_MID, E3_HIGH = range(7)
GRAPH_X, GRAPH_W, PLOT_TOP, PLOT_BOTTOM, GRAPH_DB = 16, 343, 6, 78, 15
WHITE = 3 * 255  # bright() of white
BANDS = 30
r = C.byref


class Band(C.Structure):
    _fields_ = [('enabled', C.c_int), ('type', C.c_int), ('frequency', C.c_double), ('gain', C.c_double), ('q', C.c_double)]


class Preset(C.Structure):
    _fields_ = [('count', C.c_int), ('bypass', C.c_int), ('preamp', C.c_double), ('bands', Band * BANDS),
                ('balance', C.c_double), ('automatic', C.c_int), ('spare', C.c_int)]


class EqState(C.Structure):
    _fields_ = [('mode', C.c_int), ('hold', C.c_int), ('peq', Preset), ('eq3', Preset)]


class Ui(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view edit graph bar'.split()), ('timer', C.c_uint), ('keyboard_timer', C.c_uint),
                *((k, C.c_int) for k in 'screen band step count previous rendered dirty pick adjust tries capacity file_page tab tabs from_simple'.split()),
                ('typed', C.c_double), ('draft', Preset), ('candidate', Preset), ('named', Preset), ('names', C.c_void_p),
                ('destination', C.c_char * 600), ('status', C.c_char * 160), ('name', C.c_char * 256), ('applied', C.c_char * 256),
                ('curve', (C.c_float * GRAPH_W) * 2)]


class E3(C.Structure):
    _fields_ = [('view', C.c_void_p), ('save', C.c_uint), ('press', C.c_uint), ('press_at', C.c_uint),
                *((k, C.c_int) for k in 'focus adjusting dragging dirty on h'.split()), ('gain', C.c_double * 3), ('status', C.c_char_p),
                ('caption', C.c_int)]


class Dirent(C.Structure):
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


def i32(v):
    return C.c_int(v).value


class Card:
    """/mnt as a dict: fopen and friends, access, unlink and rename, and a folder's files by opendir."""
    def __init__(self, fw, files=None):
        self.files, self.open, self.dirs, self.fail_writes = dict(files or {}), {}, {}, False
        self.folders = {p.rpartition('/')[0] for p in self.files}
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fread', self.fread)
        fw.mock('fw_fwrite', self.fwrite)
        fw.mock('fw_fclose', self.fclose)
        fw.mock('fw_rename', lambda old, new, *_: self.files.__setitem__(C.string_at(new).decode(), self.files.pop(C.string_at(old).decode())))
        fw.mock('fw_unlink', lambda path, *_: 0 if self.files.pop(C.string_at(path).decode(), None) is not None else -1)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files else -1)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', lambda d, *_: next(self.dirs[d], 0))
        fw.mock('fw_mkdir', lambda path, *_: self.folders.add(C.string_at(path).decode()))

    def fopen(self, path, mode, *_):
        path, mode = C.string_at(path).decode(), C.string_at(mode).decode()
        if 'w' in mode and self.fail_writes or 'r' in mode and path not in self.files:
            return 0
        self.folders.add(path.rpartition('/')[0])
        handle = 0x2000 + len(self.open) + len(self.files)
        while handle in self.open:
            handle += 1
        self.open[handle] = [path, 0, b'' if 'w' in mode else None]
        return handle

    def fread(self, out, size, count, f, *_):
        path, at, _ = self.open[f]
        data = self.files[path][at:at + size * count]
        C.memmove(out, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fwrite(self, data, size, count, f, *_):
        self.open[f][2] += C.string_at(data, size * count)
        return count

    def fclose(self, f, *_):
        path, _, written = self.open.pop(f)
        if written is not None:
            self.files[path] = written
        return 0

    def opendir(self, path, *_):
        folder = C.string_at(path).decode() + '/'
        names = sorted({p[len(folder):] for p in self.files if p.startswith(folder) and '/' not in p[len(folder):]}, reverse=True)
        if folder[:-1] not in self.folders:
            return 0
        entries = [Dirent(0, 0, 0, 8, n.encode()) for n in names]  # newest first, as FAT gives no order
        handle = 0x3000 + len(self.dirs)
        self.dirs[handle] = iter([C.addressof(e) for e in entries])
        self.dirs[handle, 'keep'] = entries
        return handle

    def state(self, path=ACTIVE):
        assert self.files[path][:8] == b'Q2PEQ03\0'
        return EqState.from_buffer_copy(self.files[path], 8)


def default(fw):
    p = Preset()
    fw.fn('peq_default', None, C.c_void_p)(r(p))
    return p


def state(fw, mode=EQ_PEQ, hold=0, gains=(0, 0, 0), peq=None):
    s = EqState(mode, hold)
    s.peq = peq if peq is not None else default(fw)
    s.peq.bypass = mode != EQ_PEQ
    fw.fn('eq3_bands', None, C.c_void_p, C.c_void_p)(r(s.eq3), (C.c_double * 3)(*gains))
    return s


def saved(s):
    return b'Q2PEQ03\0' + bytes(s)


def peak(fw, gain=6.0, frequency=1000.0, enabled=1, count=1):
    p = default(fw)
    p.count, p.bypass, p.automatic = count, 0, 1
    p.bands[0] = Band(enabled, 0, frequency, gain, 1.0)
    return p


class Page:
    """The EQ page in an AWTK of zeroed widgets (x, y, w, h and parent where stock keeps them), their texts, int
    props, names and images; its timers, run as the next loop pass would; the input method and its keyboard."""
    def __init__(self, fw, card, values=None, now=10000):
        self.fw, self.card, self.now = fw, card, now
        self.blocks, self.parent, self.kids, self.alive, self.kind, self.on = {}, {}, {}, {}, {}, {}
        self.text, self.props, self.names, self.images, self.timers, self.removed = {}, {}, {}, {}, [], set()
        self.requests, self.focused, self.selected, self.pages = [], [], [], []
        for name in ('list_item_create', 'label_create', 'list_view_create', 'scroll_view_create', 'image_create', 'hscroll_label_create'):
            fw.mock(name, lambda parent, x, y, w, h, *_: self.new(parent, x, y, w, h))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.new(parent, x, y, w, 0, C.string_at(kind).decode()))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_get_text', self.wide)
        fw.mock('widget_set_prop_int', lambda w, k, v, *_: self.props.__setitem__((w, C.string_at(k).decode()), i32(v)))
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: self.props.get((w, C.string_at(k).decode()), i32(d)))
        fw.mock('widget_set_name', lambda w, s, *_: self.names.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_lookup', self.lookup)
        fw.mock('widget_count_children', lambda w, *_: len(self.children(w)))
        fw.mock('widget_get_child', lambda w, i, *_: self.children(w)[i])
        fw.mock('widget_destroy_children', self.destroy_children)
        fw.mock('widget_resize', lambda w, width, h, *_: self.props.__setitem__((w, 'resized'), i32(h)))
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self.on.setdefault((w, i32(evt)), []).append((fn, ctx)) or len(self.on))
        fw.mock('scroll_view_set_offset', lambda w, x, y, *_: self.props.__setitem__((w, 'offset'), i32(y)))
        fw.mock('widget_set_focused', lambda w, on, *_: self.focused.append((w, i32(on))))
        fw.mock('edit_set_select', lambda w, a, b, *_: self.selected.append((w, i32(a), i32(b))))
        fw.mock('pages_set_active_by_name', lambda w, name, *_: self.pages.append((w, C.string_at(name).decode())))
        fw.mock('image_base_set_image', lambda w, s, *_: self.images.__setitem__(w, C.string_at(s).decode()))
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, i32(ms))) or len(self.timers))
        fw.mock('timer_remove', lambda timer, *_: self.removed.add(timer))
        fw.mock('time_now_ms', lambda *_: self.now)
        self.im, self.keyboard, self.panel = C.create_string_buffer(0x20), 0, 0
        fw.mock('input_method', lambda *_: C.addressof(self.im))
        fw.mock('input_method_request', self.request)
        self.values, self.config = dict(values or {}), []
        fw.mock('toolsReadConfig', self.read_config)
        fw.mock('write_int_config', lambda v, s, k, *_: self.config.append((C.string_at(s).decode(), C.string_at(k).decode(), i32(v))))
        self.page = self.new(0, 0, 0, 375, 320)
        self.props[(self.page, 'h')] = 290  # under the status bar
        data = card.files.get(ACTIVE, b'')
        on = data[:8] == b'Q2PEQ03\0' and EqState.from_buffer_copy(data, 8).mode != EQ_OFF
        fw.var('g_equalizer_flag', C.c_ubyte).value = on  # as peq_stock_eq left it at boot
        self.ui, self.e3 = fw.var('peq_ui__ui', Ui), fw.var('peq_ui__e3', E3)

    # AWTK
    def new(self, parent, x, y, w, h, kind=''):
        block = C.create_string_buffer(0x200)
        struct.pack_into('<4i', block, 0, i32(x), i32(y), i32(w), i32(h))
        struct.pack_into('<Q', block, W_PARENT, parent)
        p = C.addressof(block)
        self.blocks[p], self.parent[p], self.kind[p], self.alive[p] = block, parent, kind, True
        self.kids.setdefault(parent, []).append(p)
        return p

    def children(self, w):
        return self.kids.get(w, [])

    def destroy_children(self, w, *_):
        for c in self.kids.pop(w, []):
            self.destroy_children(c)
            del self.alive[c]

    def get_text(self, w, out, n, *_):
        s = self.text.get(w, '').encode()
        C.memmove(out, s[:n - 1] + b'\0', min(len(s), n - 1) + 1)
        return 0

    def wide(self, w, *_):
        buffer = (C.c_uint * (len(self.text.get(w, '')) + 1))(*map(ord, self.text.get(w, '')))
        self.blocks[C.addressof(buffer)] = buffer
        return C.addressof(buffer)

    def lookup(self, w, name, *_):
        name = C.string_at(name).decode()
        if w == self.keyboard and name == 'panel':
            return self.panel
        return next((c for c in self.children(w) if self.names.get(c) == name), 0)

    def request(self, im, edit, *_):
        self.requests.append(edit)
        struct.pack_into('<QQ', self.im, IM_WIDGET, edit, self.keyboard if edit else 0)

    def keyboard_opens(self):
        """T9's window, a while after the edit asked for it."""
        self.keyboard, self.panel = self.new(0, 0, 0, 375, 290), self.new(0, 0, 0, 375, 290)
        struct.pack_into('<Q', self.im, IM_KEYBOARD, self.keyboard)

    def read_config(self, path, section, key, out, default, *_):
        v = self.values.get((C.string_at(section).decode(), C.string_at(key).decode()))
        if v is not None:
            C.memmove(out, v.encode() + b'\0', len(v) + 1)
        return 0

    # the page's own
    def fn(self, name):
        return C.cast(getattr(self.fw.lib, 'peq_ui__' + name), C.c_void_p).value

    def pending(self, name):
        return [k for k, (f, _) in enumerate(self.timers, 1) if f == self.fn(name) and k not in self.removed]

    def run(self, name):
        """The newest timer running name, as the loop runs it: kept while it answers RET_REPEAT."""
        timer = self.pending(name)[-1]
        result = C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[timer - 1][0])(None)
        if result != RET_REPEAT:
            self.removed.add(timer)
        return result

    def settle(self):
        """The render a click or key asked for, if it did."""
        if self.pending('render'):
            self.run('render')

    def handlers(self, w, event):
        return self.on.get((w, event), [])

    def handler(self, w, event):
        fn, ctx = self.handlers(w, event)[-1]
        return lambda e=None: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, e)

    def open(self):
        assert self.fw.fn('peq_page_init', C.c_int, C.c_void_p, C.c_void_p)(self.page, 0) == 0
        return self

    def key(self, key):
        return self.fw.fn('eq_key', C.c_int, C.c_void_p, C.c_uint)(self.page, key)

    def centre(self):
        """A Centre press a while after the last: a single press, its action when its timer runs."""
        self.now += 1000
        return self.key(KEY_CENTER)

    def ret(self):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, KEY_RETURN)
        assert self.handler(self.page, EVT_KEY_UP)(C.addressof(event)) == 11  # RET_STOP: stock's keyup never sees it
        self.settle()

    def touch(self, w, kind, x, y):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, 0, kind)
        struct.pack_into('<ii', event, EVENT_X, x, y)
        target = w if kind == EVT_POINTER_DOWN else self.page
        return self.handler(target, kind)(C.addressof(event))

    def bar(self):
        """The title label, the newest 48-high label straight on the page."""
        return [w for w in self.children(self.page) if self.kind[w] == '' and struct.unpack_from('<i', self.blocks[w], 12)[0] == 48][-1]

    def title(self):
        return self.text.get(self.bar(), '')

    def tab(self, x):
        """A tap on the tab bar at x, and the render it asks for."""
        self.touch(self.bar(), EVT_POINTER_DOWN, x, 20)
        self.settle()

    def advanced(self):
        self.open().tab(300)
        assert self.ui.tab == ADVANCED and self.ui.screen == HOME
        return self

    # the rows
    def labels(self, item):
        out = []
        for c in self.children(item):
            if self.images.get(c) in ('switch_on', 'switch_off'):
                out.append('On' if self.images[c] == 'switch_on' else 'Off')
            elif self.text.get(c):
                out.append(self.text[c])
        return out

    def rows(self):
        """The list's rows in order: (caption and value joined by a space, the item)."""
        return [(' '.join(self.labels(item)), item) for item in self.alive if self.handlers(item, EVT_CLICK)]

    def captions(self):
        return [text for text, _ in self.rows()]

    def chevron(self, item):
        return any(self.images.get(c) == 'list_into' for c in self.children(item))

    def click(self, text):
        (item,) = [item for t, item in self.rows() if t.startswith(text)][:1]
        self.handler(item, EVT_CLICK)()
        self.settle()

    def edit(self):
        return [w for w in self.alive if self.kind[w] == 'edit'][-1]

    def row_y(self, text):
        item = [item for t, item in self.rows() if t.startswith(text)][0]
        return struct.unpack_from('<i', self.blocks[item], 4)[0]


class Canvas:
    """A canvas on the screen's LCD: the rectangles filled (colour, x, y, w, h), the texts drawn (text, px, colour, rect),
    and the vgcanvas's calls by fw.calls."""
    def __init__(self, fw, vg=0x7a00, fill=0xff102030):
        self.raw, self.lcd = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
        struct.pack_into('<Q', self.raw, 0x38, C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, 0xbc, fill)
        self.p, self.fill, self.rects, self.texts, self.color, self.px, self.lcd_fill = C.addressof(self.raw), 0, [], [], 0, 0, fill
        fw.mock('canvas_set_fill_color', lambda c, color, *_: setattr(self, 'fill', color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.rects.append((self.fill, i32(x), i32(y), i32(w), i32(h))))
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'color', color & 0xffffffff))
        fw.mock('canvas_set_font', lambda c, face, px, *_: setattr(self, 'px', i32(px)))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, rect, *_: self.texts.append(
            (''.join(map(chr, (C.c_uint * n).from_address(s))), self.px, self.color, tuple((C.c_int * 4).from_address(rect)))))
        fw.mock('canvas_get_vgcanvas', lambda *_: vg)

    def text(self, s):
        return [t for t in self.texts if t[0] == s]


def paint(fw, w, canvas):
    fw.fn('peq_paint', None, C.c_void_p, C.c_void_p)(w, canvas.p)


def rgb(color):
    """color_t (r, g, b, a bytes) as 0xRRGGBB."""
    return (color & 255) << 16 | (color & 0xff00) | (color >> 16 & 255)


def bright(color):
    """r + g + b of a color_t or an 0xRRGGBB, either way round: the lighter of two, the larger."""
    return sum(color >> s & 255 for s in (0, 8, 16))


def grey(color):
    return len({color >> s & 255 for s in (0, 8, 16)}) == 1


def holds(outer, inner):
    """Whether the rect (x, y, w, h) outer covers inner."""
    x, y, w, h = outer
    return x <= inner[0] and inner[0] + inner[2] <= x + w and y <= inner[1] and inner[1] + inner[3] <= y + h


def tone(fw):
    return fw.fn('accent_tone', C.c_uint, C.c_int)(2) & 0xffffff  # TONE: the accent's light tone


def page(fw, s=None, files=None, values=None):
    card = Card(fw, {ACTIVE: saved(s if s is not None else state(fw)), **(files or {})})
    return Page(fw, card, values)


# Opening, and the tabs.

def test_opening_changes_nothing_and_shows_simple_s_sliders(fw):
    p = page(fw, state(fw, EQ_3, gains=(3, 0, -2)))
    before = dict(p.card.files)
    p.open()
    assert p.card.files == before and not p.config and not fw.calls('stock_eq_trampoline')
    assert p.ui.tab == SIMPLE and p.e3.view and p.kind[p.e3.view] == 'view' and p.e3.on == 1
    assert list(p.e3.gain) == [3, 0, -2] and p.e3.focus == E3_LOW
    assert {evt for w, evt in p.on if w == p.page} == {
        EVT_DESTROY, EVT_KEY_UP, EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE}


def test_no_page_is_refused(fw):
    assert fw.fn('peq_page_init', C.c_int, C.c_void_p, C.c_void_p)(0, 0) == 16


def test_the_bar_s_halves_are_the_tabs(fw):
    p = page(fw).open()
    p.tab(300)
    assert p.ui.tab == ADVANCED and p.captions()[:2] == ['Nothing to apply', 'Reset to defaults']
    p.tab(100)
    assert p.ui.tab == SIMPLE and p.e3.view and not p.rows()
    p.tab(100)  # the open tab: nothing to do
    assert p.ui.tab == SIMPLE and not p.pending('render')


def test_the_wheel_reaches_advanced_s_tab_bar_past_its_first_row(fw):
    p = page(fw).advanced()
    view = p.ui.view
    assert p.ui.tabs == 1 and p.props[(view, '_ringnav_index')] == -1  # a tab opened from the bar keeps the wheel there
    assert p.key(KEY_NEXT) == 1 and p.ui.tabs == 0 and p.props[(view, '_ringnav_index')] == 0  # down to the first row
    assert p.key(KEY_NEXT) == 0 and p.key(KEY_CENTER) == 0  # the list's own
    p.props[(view, '_ringnav_index')] = 1
    assert p.key(KEY_PREV) == 0  # not past its first row
    p.props[(view, '_ringnav_index')] = 0
    assert p.key(KEY_PREV) == 1 and p.ui.tabs == 1 and p.props[(view, '_ringnav_index')] == -1
    assert p.centre() == 1 and p.ui.tab == ADVANCED  # a single press waits out a double's
    assert p.run('e3_single') == RET_REMOVE and p.ui.tab == SIMPLE
    p.settle()
    assert p.e3.focus == E3_TABS and p.e3.view  # the wheel kept on the bar


def test_the_wheel_on_simple_s_tabs_opens_advanced_by_centre(fw):
    p = page(fw).open()
    for _ in range(4):
        p.key(KEY_PREV)
    assert p.e3.focus == E3_TABS
    p.centre()
    p.run('e3_single')
    p.settle()
    assert p.ui.tab == ADVANCED and p.ui.tabs == 1 and p.props[(p.ui.view, '_ringnav_index')] == -1


def test_a_double_centre_press_is_the_screen_s(fw):
    p = page(fw).open()
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.centre()
    assert p.key(KEY_CENTER) == 2 and not p.pending('e3_single')  # the second within DOUBLE_CLICK_MS
    (_, (top, event, *_)), = fw.calls('on_wm_keyup_fun')
    assert top == 0 and C.c_uint.from_address(event + EVENT_KEY).value == KEY_CENTER
    assert p.e3.focus == E3_LOW and not p.e3.adjusting  # nothing taken


def test_keys_on_another_page_or_none_are_not_the_eq_s(fw):
    p = page(fw)
    assert p.key(KEY_NEXT) == 0  # not open
    p.open()
    assert fw.fn('eq_key', C.c_int, C.c_void_p, C.c_uint)(p.page + 1, KEY_NEXT) == 0 and p.key(KEY_RETURN) == 0


# Advanced's main screen.

def test_advanced_s_rows_under_the_curve(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    s.peq.balance = -1.5
    p = page(fw, s).advanced()
    rh, x = fw.fn('page_h', C.c_int, C.c_int)(48), fw.fn('page_x', C.c_int)()
    # a +6 dB peak: Auto cuts 6 dB at either rate; the other nine of the ten default bands are off
    assert p.captions()[:9] == ['Nothing to apply', 'Reset to defaults', 'Enabled On', 'EQ per Output Off', 'Balance L 1.5 dB',
                                'Preamp Auto (44.1: -6.0 / 48: -6.0)', 'Presets', '1  Peaking 1000 Hz +6.0 dB', '2  Peaking 63 Hz Off']
    assert len(p.rows()) == 7 + 10 and p.captions()[-1] == '10  Peaking 16000 Hz Off'
    canvas = Canvas(fw)
    paint(fw, p.ui.graph, canvas)
    drawn = struct.unpack_from('<i', p.blocks[p.ui.graph], 4)[0] + max(t[3][1] + t[3][3] for t in canvas.texts)  # its scale, lowest
    head = p.row_y('Nothing')
    assert head % rh == 0 and drawn <= head < drawn + rh  # the rows under the curve, from the first whole row clear of it
    assert [p.row_y(t) for t in ('Nothing', 'Reset', '1  ')] == [head, head + rh, head + 7 * rh]
    assert p.props[(p.ui.view, 'virtual_h')] == head + 17 * rh
    rows = dict((t.split()[0], item) for t, item in p.rows())
    assert [p.chevron(rows[k]) for k in ('Reset', 'Enabled', 'Balance', 'Preamp', 'Presets', '1')] == [False, False, True, True, True, True]
    caption = lambda k: [c for c in p.children(rows[k]) if p.text.get(c, '').startswith(k + '  ')][0]
    dim = p.props.get((caption('2'), 'style:normal:text_color'))
    assert dim is not None and bright(dim) < WHITE  # an off band is dim; the on one keeps the row's white
    assert p.props.get((caption('1'), 'style:normal:text_color')) is None
    assert all(struct.unpack_from('<i', p.blocks[c], 0)[0] == x for c in p.children(rows['Reset']))
    assert p.title() == '' and p.ui.bar == p.bar()  # the tabs' bar


def test_nothing_to_apply_is_display_only_until_an_edit(fw):
    p = page(fw).advanced()
    before = dict(p.card.files)
    p.click('Nothing to apply')
    assert p.ui.screen == HOME and p.card.files == before
    p.click('1  ')
    p.click('Gain')
    p.click('+6.0 dB')
    p.ret()
    assert p.captions()[0] == 'Apply changes'
    p.click('Apply changes')
    assert p.title() == 'Applied' and p.card.state().peq.bands[0].gain == 6 and p.card.state().mode == EQ_PEQ
    assert p.captions()[0] == 'Nothing to apply'


def test_auto_s_two_cuts_are_drawn_smaller_to_fit(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 24))
    s.peq.balance = 12
    p = page(fw, s).advanced()
    assert 'Balance R 12.0 dB' in p.captions()
    preamp = [item for t, item in p.rows() if t.startswith('Preamp')][0]
    value = [c for c in p.children(preamp) if p.text.get(c, '').startswith('Auto')][0]
    assert p.props[(value, 'style:normal:font_size')] < 20  # two double-digit cuts beside the caption


def test_a_preset_s_name_and_a_picked_preamp_show_as_set(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 3))
    s.peq.automatic, s.peq.preamp = 2, -4.5
    files = {f'{SAVED}/Rock.peq': b'Q2PEQ02\0' + bytes(s.peq)}
    p = page(fw, s, files).advanced()
    p.click('Presets')
    p.click('Rock.peq')
    assert 'Presets Rock' in p.captions() and 'Preamp -4.5 dB' in p.captions()
    assert p.title() == 'Preset loaded; choose Apply to activate'


def test_a_long_preset_name_ellipsises_right_aligned_clear_of_its_caption(fw):
    cut, focus, style = [], [], {}
    fw.mock('hscroll_label_set_ellipses', lambda w, on, *_: cut.append((w, i32(on))))
    fw.mock('hscroll_label_set_only_focus', lambda w, on, *_: focus.append((w, i32(on))))
    fw.mock('widget_use_style', lambda w, name, *_: style.__setitem__(w, C.string_at(name).decode()))
    s = state(fw, EQ_PEQ, peq=peak(fw, 3))
    name = 'LETSHUOER DEFIANT-to-iBASSO Exemplar'
    p = page(fw, s, {f'{SAVED}/{name}.peq': b'Q2PEQ02\0' + bytes(s.peq)}).advanced()
    p.click('Presets')
    p.click(f'{name}.peq')
    row = [item for t, item in p.rows() if t.startswith('Presets')][0]
    value = [c for c in p.children(row) if p.text.get(c) == name][0]
    x, _, w, _ = struct.unpack_from('<4i', p.blocks[value], 0)
    assert style[value] == 's_scrlabel_white20r' and p.props[(value, 'style:normal:margin_right')] == 0
    assert (value, 1) in cut and (value, 1) in focus  # without only_focus stock scrolls it from its first paint
    assert x > 118 and x + w == 316  # past Classic's 27 px caption; ends where the other values do


# The curve.

def grid(canvas):
    return [(rgb(c), x, y, w, h) for c, x, y, w, h in canvas.rects]


def test_the_graph_s_grid_and_scale(fw):
    p = page(fw).advanced()
    canvas = Canvas(fw)
    paint(fw, p.ui.graph, canvas)
    rows = [g for g in grid(canvas) if g[3] == GRAPH_W and g[4] == 1]
    ys = [y for _, _, y, _, _ in rows]
    zero = (PLOT_TOP + PLOT_BOTTOM) // 2
    assert ys[0] == zero and len(set(ys)) == len(ys) == 5  # 0 dB first
    d = sorted(abs(y - zero) for y in ys[1:])
    assert d[1] - d[0] <= 1 and d[3] - d[2] <= 1 and abs(d[2] - 2 * d[0]) <= 1  # +-6 and +-12 dB, evenly
    assert all(PLOT_TOP < y < PLOT_BOTTOM for y in ys)
    assert sum(rows[0][0].to_bytes(3, 'big')) > sum(rows[1][0].to_bytes(3, 'big'))  # 0 dB the lighter line
    ticks = [x for _, x, y, w, h in grid(canvas) if w == 1 and (y, h) == (PLOT_TOP, PLOT_BOTTOM - PLOT_TOP)]
    assert len(ticks) == 3 and abs((ticks[1] - ticks[0]) - (ticks[2] - ticks[1])) <= 1  # a decade apart each
    assert [(t[0], t[3][0] + t[3][2] // 2) for t in canvas.texts] == [('100', ticks[0]), ('1k', ticks[1]), ('10k', ticks[2])]
    assert all(grey(t[2]) and bright(t[2]) < WHITE and t[3][1] > PLOT_BOTTOM for t in canvas.texts)  # grey, under the plot
    assert canvas.fill == canvas.lcd_fill  # the LCD's own fill back


def test_the_response_fills_toward_0_db_and_strokes_once(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    canvas = Canvas(fw)
    paint(fw, p.ui.graph, canvas)
    gradients = [a for _, a in fw.calls('vgcanvas_set_fill_linear_gradient')]
    assert len(gradients) == 2 and all(rgb(a[1]) == rgb(a[2]) == tone(fw) and a[1] >> 24 > 0 == a[2] >> 24 for a in gradients)
    assert [a[1] >> 24 for _, a in fw.calls('vgcanvas_set_stroke_color')][:1] == [0xff]
    assert len(fw.calls('vgcanvas_stroke')) == 2  # the line, and the one band's ring
    assert len(fw.calls('vgcanvas_save')) == len(fw.calls('vgcanvas_restore')) == 1


def test_channels_that_differ_draw_the_other_dimmer(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    s.peq.bands[0].enabled = 2  # left only
    p = page(fw, s).advanced()
    paint(fw, p.ui.graph, Canvas(fw))
    right, left = [a[1] for _, a in fw.calls('vgcanvas_set_stroke_color')][:2]
    assert 0 < right >> 24 < left >> 24 and rgb(right) == rgb(left)  # the right under the left, fainter
    assert len(fw.calls('vgcanvas_stroke')) == 3  # two lines, and the band's ring


def test_bypassed_is_the_line_alone_in_grey(fw):
    p = page(fw, state(fw, EQ_OFF, peq=peak(fw, 6))).advanced()
    paint(fw, p.ui.graph, Canvas(fw))
    assert not fw.calls('vgcanvas_set_fill_linear_gradient')
    line = rgb(fw.calls('vgcanvas_set_stroke_color')[0][1][1])
    assert grey(line) and line != tone(fw)


def test_rings_mark_the_bands_in_use_and_the_edited_one(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6, count=3))
    s.peq.bands[1] = Band(0, 0, 200, 0, 1)
    s.peq.bands[2] = Band(1, 1, 100, 3, .7)
    p = page(fw, s).advanced()
    paint(fw, p.ui.graph, Canvas(fw))
    assert len(fw.calls('vgcanvas_arc')) == 2  # the off band has none
    fills = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_fill_color')]
    assert fills == [0, 0]  # hollow
    p.click('2  ')  # the off band, being edited
    assert p.ui.screen == BAND and p.title() == 'Band 2'
    before = len(fw.calls('vgcanvas_arc'))
    paint(fw, p.ui.graph, Canvas(fw))
    assert len(fw.calls('vgcanvas_arc')) - before == 3
    fills = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_fill_color')][2:]
    strokes = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_stroke_color')][-3:]
    assert fills == [0, tone(fw), 0] and strokes == [tone(fw), 0xffffff, tone(fw)]  # filled, white-ringed


def test_another_widget_or_no_screen_paints_nothing(fw):
    p = page(fw).advanced()
    canvas = Canvas(fw)
    paint(fw, p.ui.view, canvas)
    paint(fw, 0, canvas)
    struct.pack_into('<Q', canvas.raw, 0x38, 0)
    paint(fw, p.ui.graph, canvas)
    assert not canvas.rects and not canvas.texts


def test_no_vgcanvas_leaves_the_grid(fw):
    p = page(fw).advanced()
    canvas = Canvas(fw, vg=0)
    paint(fw, p.ui.graph, canvas)
    assert len(canvas.rects) == 8 and not fw.calls('vgcanvas_save')


# A band, its value menu and the pickers.

def test_a_band_s_screen(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    assert p.captions() == ['Band On', 'Channels Both', 'Type Peaking', 'Frequency 1000 Hz', 'Gain +6.0 dB', 'Q 1.00']
    assert p.title() == 'Band 1' and p.ui.graph and p.ui.bar is None
    p.click('Channels')
    p.click('Type')
    assert 'Channels Left' in p.captions() and 'Type Low shelf' in p.captions()
    p.click('Band')
    assert p.captions()[:2] == ['Band Off', 'Type Low shelf']  # an off band has no channels
    p.ret()
    assert p.ui.screen == HOME and '1  Low shelf 1000 Hz Off' in p.captions()


def test_a_band_past_the_count_joins_the_preset(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('2  ')
    assert p.ui.draft.count == 2


def adjust(fw, what='Frequency'):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.click(what)
    return p


def test_the_frequency_menu_and_its_edit(fw):
    p = adjust(fw)
    assert p.ui.screen == ADJUST and p.captions() == ['Band 1 Frequency (Hz)', 'Raise 1 Hz', 'Lower 1 Hz', 'Step 1 Hz']
    edit = p.edit()
    assert p.parent[edit] == p.page and p.text[edit] == '1000' and p.ui.edit == edit
    assert p.handlers(edit, EVT_FOCUS) and not p.handlers(edit, EVT_IM_ACTION)
    p.click('Step')
    p.click('Raise')
    assert p.ui.draft.bands[0].frequency == 1010 and p.text[p.edit()] == '1010' and 'Step 10 Hz' in p.captions()


def test_a_typed_number_is_taken_and_anything_else_shows_the_value_again(fw):
    p = adjust(fw)
    p.text[p.edit()] = '440'
    p.handler(p.edit(), EVT_VALUE_CHANGED)()
    p.settle()
    assert p.ui.draft.bands[0].frequency == 440 and p.ui.dirty and p.text[p.edit()] == '440'
    p.text[p.edit()] = '4x0'
    p.handler(p.edit(), EVT_VALUE_CHANGED)()
    assert p.pending('render') and p.ui.draft.bands[0].frequency == 440
    p.settle()
    assert p.text[p.edit()] == '440'


def test_a_value_change_from_a_replaced_edit_is_ignored(fw):
    p = adjust(fw)
    stale = p.handler(p.edit(), EVT_VALUE_CHANGED)
    p.ui.edit = 0  # render let it go
    p.text[p.edit()] = '440'
    assert stale() == 0 and p.ui.draft.bands[0].frequency == 1000 and not p.pending('render')


def test_q_s_menu_has_no_step(fw):
    p = adjust(fw, 'Q')
    assert p.captions() == ['Band 1 Q', 'Raise 0.05', 'Lower 0.05'] and p.text[p.edit()] == '1.00'


def test_the_value_row_opens_t9_s_numbers_with_the_value_selected(fw):
    p = adjust(fw)
    p.click('Band 1 Frequency')
    assert p.focused[-1] == (p.edit(), 1) and not p.pending('render')  # a render would close it
    assert struct.unpack_from('<Q', p.im, IM_WIDGET)[0] == p.edit()
    assert p.run('numbers') == RET_REPEAT  # the keyboard isn't up yet
    p.keyboard_opens()
    assert p.run('numbers') == RET_REMOVE and p.ui.keyboard_timer == 0
    assert p.pages == [(p.panel, 'symnum')]
    assert p.selected[-1] == (p.edit(), 0, 4)  # "1000": the first key replaces it


def test_a_keyboard_that_never_opens_is_given_up_after_twenty_looks(fw):
    p = adjust(fw)
    p.handler(p.edit(), EVT_FOCUS)()
    results = [p.run('numbers') for _ in range(20)]
    assert results == [RET_REPEAT] * 19 + [RET_REMOVE] and not p.pages


def test_another_edit_s_keyboard_is_not_this_one_s(fw):
    p = adjust(fw)
    p.handler(p.edit(), EVT_FOCUS)()
    p.keyboard_opens()
    struct.pack_into('<Q', p.im, IM_WIDGET, p.edit() + 1)
    assert p.run('numbers') == RET_REPEAT and not p.pages


def test_a_render_lets_t9_go_before_replacing_its_edit(fw):
    p = adjust(fw)
    p.handler(p.edit(), EVT_FOCUS)()
    old = p.edit()
    p.click('Raise')
    requests = p.requests
    assert requests[-1] == 0 and p.edit() != old and p.ui.keyboard_timer == 0


@pytest.mark.parametrize('pick, value, title, rows, row', (('Balance', 0, 'Balance', 49, 24), ('Balance', -3, 'Balance', 49, 18),
                                                          ('Preamp', None, 'Preamp', 74, 0), ('Preamp', -3, 'Preamp', 74, 31)))
def test_a_picker_opens_centred_on_its_value(fw, pick, value, title, rows, row):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    if pick == 'Balance':
        s.peq.balance = value
    elif value is not None:
        s.peq.automatic, s.peq.preamp = 2, value
    p = page(fw, s).advanced()
    p.click(pick)
    rh, height = fw.fn('page_h', C.c_int, C.c_int)(48), (290 - 48) // fw.fn('page_h', C.c_int, C.c_int)(48) * fw.fn('page_h', C.c_int, C.c_int)(48)
    assert p.title() == title and len(p.rows()) == rows and p.props[(p.ui.view, '_ringnav_count')] == rows
    assert p.props[(p.ui.view, '_ringnav_index')] == row and not p.ui.graph
    offset = p.props[(p.ui.view, 'offset')]
    assert offset == max(0, min(row * rh - (height - rh) // 2, rows * rh - height))
    if pick == 'Balance':
        assert p.captions()[0] == 'L 12.0 dB' and p.captions()[24] == 'Centre' and p.captions()[-1] == 'R 12.0 dB'
    else:
        assert p.captions()[0].startswith('Auto (') and p.captions()[1] == '+12.0 dB' and p.captions()[-1] == '-24.0 dB'


def test_a_picked_preamp_at_its_top_opens_on_its_row_not_auto(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    s.peq.automatic, s.peq.preamp = 2, 15  # past the picker's +12.0
    p = page(fw, s).advanced()
    p.click('Preamp')
    assert p.props[(p.ui.view, '_ringnav_index')] == 1


def test_the_gain_picker_is_the_band_s(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.click('Gain')
    assert p.title() == 'Band 1 Gain' and len(p.rows()) == 97 and p.props[(p.ui.view, '_ringnav_index')] == 36
    p.ret()
    assert p.ui.screen == BAND


def test_a_kept_screen_keeps_its_selection_and_scroll(fw):
    p = page(fw).advanced()
    view = p.ui.view
    p.props[(view, '_ringnav_index')], p.props[(view, 'yoffset')] = 5, 96
    p.click('Reset to defaults')
    assert p.props[(p.ui.view, '_ringnav_index')] == 5 and p.props[(p.ui.view, 'offset')] == 96
    assert p.title() == 'Reset; choose Apply to activate'


# The presets' screens.

def test_the_presets_screen_lists_saved_presets_sorted(fw):
    p = page(fw, files={f'{SAVED}/b.peq': b'x', f'{SAVED}/a.peq': b'x', f'{SAVED}/notes.txt': b'x'}).advanced()
    p.click('Presets')
    assert p.captions() == ['Stock presets', 'Import from SD /EQ', 'Save editor preset', 'Rename a preset', 'Delete a preset',
                            'a.peq', 'b.peq']
    assert p.title() == 'Presets' and not p.ui.graph and p.ui.bar is None
    rows = dict(p.rows())
    assert all(p.chevron(rows[t]) for t in p.captions()[:5]) and not p.chevron(rows['a.peq'])


def test_more_than_a_page_of_presets_pages(fw):
    files = {f'{SAVED}/{k:03}.peq': b'x' for k in range(130)}
    p = page(fw, files=files).advanced()
    p.click('Presets')
    assert p.title() == 'Presets 1/2' and p.captions()[5:7] == ['Next page', '000.peq'] and p.captions()[-1] == '127.peq'
    assert len(p.rows()) == 5 + 1 + 128
    p.click('Next page')
    assert p.title() == 'Presets 2/2' and p.captions()[5:] == ['Previous page', '128.peq', '129.peq']
    p.click('Previous page')
    assert p.title() == 'Presets 1/2'


def test_a_page_the_card_no_longer_fills_is_its_last(fw):
    files = {f'{SAVED}/{k:03}.peq': b'x' for k in range(130)}
    p = page(fw, files=files).advanced()
    p.click('Presets')
    p.click('Next page')
    del p.card.files[f'{SAVED}/128.peq'], p.card.files[f'{SAVED}/129.peq']  # gone from the card meanwhile
    p.click('128.peq')
    assert p.ui.file_page == 0 and p.title() == 'Cannot read preset; settings unchanged' and p.captions()[5] == '000.peq'
    assert p.props[(p.ui.view, 'offset')] == 0 and p.props[(p.ui.view, '_ringnav_index')] == 0


def test_saving_offers_ten_slots_and_asks_before_replacing(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('Presets')
    p.click('Save editor preset')
    assert p.captions() == [f'Save as Manual {k:02}' for k in range(1, 11)] and p.title() == 'Presets'
    p.click('Save as Manual 03')
    assert f'{SAVED}/Manual 03.peq' in p.card.files and p.title() == 'Preset saved; active EQ unchanged'
    p.click('Save as Manual 03')
    assert p.captions() == ['Replace existing preset? Confirm', 'Cancel replacement'] and p.ui.screen == CONFIRM
    p.click('Cancel replacement')
    assert p.ui.screen == SAVES


def test_a_failed_save_says_so(fw):
    p = page(fw).advanced()
    p.click('Presets')
    p.click('Save editor preset')
    p.card.fail_writes = True
    p.click('Save as Manual 01')
    assert p.title() == 'Save failed; settings unchanged' and f'{SAVED}/Manual 01.peq' not in p.card.files


def test_deleting_asks_then_deletes(fw):
    p = page(fw, files={f'{SAVED}/a.peq': b'x'}).advanced()
    p.click('Presets')
    p.click('Delete a preset')
    p.click('a.peq')
    assert p.captions() == ['Delete a.peq? Confirm', 'Cancel']
    rh = fw.fn('page_h', C.c_int, C.c_int)(48)
    shrunk = [h for (w, k), h in p.props.items() if k == 'resized' and w in p.alive]
    assert shrunk == [2 * rh, 2 * rh]  # the list and its view end at the last row: no white below
    p.click('Delete a.peq? Confirm')
    assert f'{SAVED}/a.peq' not in p.card.files and p.title() == 'Deleted a.peq; active EQ unchanged'
    p.ret()
    assert p.ui.screen == PRESETS


def test_a_failed_delete_says_so(fw):
    p = page(fw, files={f'{SAVED}/a.peq': b'x'}).advanced()
    p.click('Presets')
    p.click('Delete a preset')
    p.click('a.peq')
    fw.mock('fw_unlink', lambda *_: -1)
    p.click('Delete a.peq? Confirm')
    assert p.title() == 'Delete failed; preset kept'


def test_the_stock_presets(fw):
    p = page(fw).advanced()
    p.click('Presets')
    p.click('Stock presets')
    assert p.captions() == ['Pop', 'Rock', 'Dance', 'Blues', 'Metal', 'Vocal', 'Classical', 'Jazz']
    p.click('Jazz')
    assert p.ui.screen == HOME and 'Presets Jazz' in p.captions() and p.captions()[0] == 'Apply changes'


def test_an_import_with_a_bad_line_says_which(fw):
    p = page(fw, files={f'{IMPORT}/x.txt': b'Preamp: -3 dB\nnonsense\n'}).advanced()
    p.click('Presets')
    p.click('Import from SD /EQ')
    assert p.captions() == ['x.txt'] and p.title() == 'Presets'
    p.click('x.txt')
    assert p.title() == 'Line 2: unsupported command' and f'{SAVED}/x.peq' not in p.card.files


def test_no_imports_says_why(fw):
    p = page(fw).advanced()
    p.click('Presets')
    p.click('Import from SD /EQ')
    assert p.captions() == [] and p.title() == 'Folder missing or unavailable'


def test_renaming_opens_the_name_with_the_keyboard_up(fw):
    p = page(fw, files={f'{SAVED}/Rock.peq': b'x'}).advanced()
    p.click('Presets')
    p.click('Rename a preset')
    p.click('Rock.peq')
    edit = p.edit()
    assert p.ui.screen == NAMING and p.captions() == ['Rename'] and p.text[edit] == 'Rock' and p.title() == ''
    assert p.props[(edit, 'select_none_when_focused')] == 1 and p.focused[-1] == (edit, 1)
    assert p.handlers(edit, EVT_IM_ACTION) and not p.handlers(edit, EVT_FOCUS)
    p.text[edit] = 'Jazz'
    p.handler(edit, EVT_IM_ACTION)()
    p.handler(edit, EVT_VALUE_CHANGED)()  # Done sends both: renamed once
    p.settle()
    assert f'{SAVED}/Jazz.peq' in p.card.files and p.ui.screen == PRESETS and p.title() == 'Renamed to Jazz'


def test_return_from_the_name_goes_back_to_the_list(fw):
    p = page(fw, files={f'{SAVED}/Rock.peq': b'x'}).advanced()
    p.click('Presets')
    p.click('Rename a preset')
    p.click('Rock.peq')
    p.ret()
    assert p.ui.screen == RENAMES and p.captions() == ['Rock.peq'] and p.card.files[f'{SAVED}/Rock.peq'] == b'x'


def test_a_tap_on_the_row_renames_through_the_edit_s_blur_once(fw):
    p = page(fw, files={f'{SAVED}/Rock.peq': b'x'}).advanced()
    p.click('Presets')
    p.click('Rename a preset')
    p.click('Rock.peq')
    p.text[p.edit()] = 'Jazz'
    row = p.rows()[0][1]
    p.handler(p.edit(), EVT_VALUE_CHANGED)()  # the blur, as the touch leaves the edit
    p.handler(row, EVT_CLICK)()  # then the row's own click: renamed already
    p.settle()
    assert sorted(p.card.files) == [ACTIVE, f'{SAVED}/Jazz.peq'] and p.ui.screen == PRESETS and p.title() == 'Renamed to Jazz'


def test_return_steps_back_and_leaves_from_the_main_screen(fw):
    p = page(fw).advanced()
    p.click('Presets')
    p.click('Stock presets')
    p.ret()
    assert p.ui.screen == PRESETS
    p.ret()
    assert p.ui.screen == HOME and not fw.calls('navigator_back')
    p.ret()
    assert fw.calls('navigator_back')


def test_the_switch_and_per_output_rows(fw):
    p = page(fw, state(fw, EQ_OFF)).advanced()
    p.click('Enabled')
    assert p.card.state().mode == EQ_PEQ and 'Enabled On' in p.captions()
    p.click('EQ per Output')
    assert ('Q2POD', 'EQOUTPUT', 1) in p.config and 'EQ per Output On' in p.captions()


# Closing.

def test_closing_saves_simple_s_gains_and_ends_the_held_comparison(fw):
    p = page(fw, state(fw, EQ_OFF, hold=1, gains=(3, 0, 0))).open()
    p.e3.gain[1], p.e3.dirty = 2, 1  # a turn the save timer hasn't taken yet
    p.handler(p.page, EVT_DESTROY)()
    s = p.card.state()
    assert s.hold == 0 and list(s.eq3.bands[k].gain for k in range(3)) == [3, 2, 0] and fw.calls('stock_eq_trampoline')
    assert p.ui.page is None and p.ui.view is None and p.ui.names is None and p.e3.view is None
    assert p.key(KEY_NEXT) == 0  # nothing open


def test_closing_from_simple_s_presets_reopens_on_simple(fw):
    p = page(fw).open()
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    assert p.ui.tab == ADVANCED and p.ui.screen == STOCKS and p.ui.from_simple
    p.handler(p.page, EVT_DESTROY)()
    assert p.ui.tab == SIMPLE and not p.ui.from_simple


def test_closing_with_an_edit_up_lets_t9_go(fw):
    p = adjust(fw)
    p.handler(p.page, EVT_DESTROY)()
    assert p.requests[-1] == 0 and p.ui.edit is None


def test_closing_frees_the_preset_list(fw):
    p = page(fw, files={f'{SAVED}/a.peq': b'x'}).advanced()
    p.click('Presets')
    assert p.ui.capacity and p.ui.names
    p.handler(p.page, EVT_DESTROY)()
    assert p.ui.capacity == 0 and p.ui.names is None and p.ui.file_page == 0


# Simple's tab.

def test_simple_s_presets_play_a_stock_preset_at_once_and_come_back(fw):
    p = page(fw, state(fw, EQ_3, gains=(2, 0, 0))).open()
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    stocks = p.captions()
    assert p.ui.screen == STOCKS and 'Rock' in stocks
    p.click('Rock')
    s = p.card.state()
    rock = list(fw.var('peq_ui__stock_gains', (C.c_byte * 10) * len(stocks))[stocks.index('Rock')])  # its row in stock's table
    assert s.mode == EQ_PEQ and not s.peq.bypass and [s.peq.bands[k].gain for k in range(10)] == rock
    assert [s.peq.bands[k].enabled for k in range(10)] == [int(g != 0) for g in rock]  # a band at 0 dB is off
    assert p.ui.tab == SIMPLE and p.e3.focus == E3_PRESETS and p.e3.status is None and p.e3.view
    assert p.ui.applied == b'Rock'


def test_back_from_simple_s_presets_changes_nothing(fw):
    p = page(fw, state(fw, EQ_3, gains=(2, 0, 0))).open()
    before = dict(p.card.files)
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    p.ret()
    assert p.ui.tab == SIMPLE and p.card.files == before and p.e3.focus == E3_PRESETS


def test_a_failed_apply_from_simple_s_presets_says_so_on_simple(fw):
    p = page(fw, state(fw, EQ_3)).open()
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    p.card.fail_writes = True
    p.click('Rock')
    assert p.ui.tab == SIMPLE and p.e3.status == b'Apply failed; active EQ unchanged'


def test_unsaved_gains_keep_simple_when_presets_can_t_save_them(fw):
    p = page(fw, state(fw, EQ_3)).open()
    p.e3.gain[0], p.e3.dirty, p.e3.focus = 4, 1, E3_PRESETS
    p.card.fail_writes = True
    p.centre()
    p.run('e3_single')
    assert p.ui.tab == SIMPLE and p.e3.dirty and not p.pending('render')
    p.tab(300)
    assert p.ui.tab == SIMPLE  # nor the tab bar


def test_the_save_timer_saves_a_turn(fw):
    p = page(fw, state(fw, EQ_3)).open()
    p.centre()
    p.run('e3_single')
    assert p.e3.adjusting
    p.key(KEY_NEXT), p.key(KEY_NEXT)
    assert p.card.state().eq3.bands[0].gain == 0 and len(p.pending('e3_tick')) == 1  # one save for the turns
    assert p.run('e3_tick') == RET_REMOVE
    assert p.card.state().eq3.bands[0].gain == 1 and not p.e3.dirty and fw.calls('stock_eq_trampoline')


def test_centre_or_return_ends_an_adjustment(fw):
    p = page(fw, state(fw, EQ_3)).open()
    for end in ('centre', 'return'):
        p.centre()
        p.run('e3_single')
        p.key(KEY_PREV)
        if end == 'centre':
            assert p.key(KEY_CENTER) == 1 and not p.pending('e3_single')
        else:
            p.ret()
        assert not p.e3.adjusting and p.e3.focus == E3_LOW and not fw.calls('navigator_back')
    assert p.card.state().eq3.bands[0].gain == -1
    p.ret()
    assert fw.calls('navigator_back')


def test_a_tap_on_r_resets_the_sliders_and_a_drag_follows_the_finger(fw):
    p = page(fw, state(fw, EQ_3, gains=(3, -2, 4))).open()
    view, y, shown = p.e3.view, fw.fn('peq_ui__e3_y', C.c_int, C.c_double), e3_canvas(fw, p)
    centre = lambda text: (lambda left, top, w, h: (left + w // 2, top + h // 2))(*shown.text(text)[0][3])  # where it's drawn
    assert p.touch(view, EVT_POINTER_DOWN, *centre('R')) == 0 and p.e3.focus == E3_RESET
    assert list(p.e3.gain) == [0, 0, 0] and [p.card.state().eq3.bands[k].gain for k in range(3)] == [0, 0, 0]  # saved at once
    assert fw.calls('stock_eq_trampoline') and not p.e3.dirty  # and heard
    top, bottom, x = y(6), y(-6), centre('Mid')[0]  # the travel's ends: +-6 dB
    p.touch(view, EVT_POINTER_DOWN, x, (top + bottom) // 2)
    assert p.e3.focus == E3_MID and p.e3.dragging and p.e3.adjusting and p.e3.gain[1] == 0
    p.touch(view, EVT_POINTER_MOVE_BEFORE, x, bottom)
    assert p.e3.gain[1] == -6
    p.touch(view, EVT_POINTER_MOVE_BEFORE, x, top - 20)  # past the end
    assert p.e3.gain[1] == 6
    p.touch(view, EVT_POINTER_UP_BEFORE, x, top)
    assert not p.e3.dragging and p.card.state().eq3.bands[1].gain == 6
    assert p.touch(view, EVT_POINTER_MOVE_BEFORE, x, bottom) == 0 and p.e3.gain[1] == 6  # no finger down


# Simple's paint.

def e3_canvas(fw, p):
    canvas = Canvas(fw)
    paint(fw, p.e3.view, canvas)
    return canvas


def test_simple_paints_the_switch_the_sliders_and_their_values(fw):
    p = page(fw, state(fw, EQ_3, gains=(1.5, 0, -6))).open()
    c = e3_canvas(fw, p)
    assert [t[0] for t in c.texts] == ['Enabled: On', 'Presets', 'R', 'Low', '+1.5 dB', 'Mid', '0 dB', 'High', '-6.0 dB']
    y = fw.fn('peq_ui__e3_y', C.c_int, C.c_double)
    zero, up, down = y(0), y(1.5), y(-6)
    assert up < zero < down
    rects = grid(c)
    bars = [g for g in rects if g[0] == tone(fw) and g[2] <= zero <= g[2] + g[4]]  # the accent's that meet 0 dB
    assert [(y, h) for _, _, y, _, h in bars] == [(up, zero - up), (zero, 1), (zero, down - zero + 1)]  # each from 0 dB to its gain
    xs = [x for _, x, _, _, _ in bars]
    assert xs[0] < xs[1] < xs[2] and xs[1] - xs[0] == xs[2] - xs[1]  # Low, Mid, High evenly
    assert [g for g in rects if g[2] == zero and g[4] == 1 and g[1] < xs[0] and g[1] + g[3] > xs[2] + bars[2][3]]  # 0 dB, across all
    assert c.fill == c.lcd_fill
    # the wheel's slider on a card: its name, its travel and its value on it, and one slider along as the wheel turns
    assert p.key(KEY_NEXT) == 1 and p.e3.focus == E3_MID
    turned = grid(e3_canvas(fw, p))
    (low,), (mid,) = [g for g in rects if g not in turned], [g for g in turned if g not in rects]
    for card, name, value, bar in ((low, 'Low', '+1.5 dB', bars[0]), (mid, 'Mid', '0 dB', bars[1])):
        travel = (bar[1], y(6), bar[3], y(-6) - y(6))
        assert holds(card[1:], c.text(name)[0][3]) and holds(card[1:], travel) and holds(card[1:], c.text(value)[0][3])
    assert mid[1] - low[1] == xs[1] - xs[0] and mid[2:] == low[2:] and rects.index(low) < rects.index(bars[0])  # under its bar


def slider(c, name, at):
    """Simple's slider called name, its gain at y == at, in a paint: its value's colour and its handle's, each centred under
    the name, the handle on y."""
    (left, _, w, _), = [t[3] for t in c.texts if t[0] == name]
    (value,) = [t[2] for t in c.texts if t[0].endswith('dB') and 2 * t[3][0] + t[3][2] == 2 * left + w]
    (handle,) = [g[0] for g in c.rects if 2 * g[1] + g[3] == 2 * left + w and 2 * g[2] + g[4] == 2 * at]
    return value, handle


def test_simple_off_is_grey_and_an_adjusted_slider_lit(fw):
    gains = (3, -2, 4)  # apart, so no handle shares its centre with the track's or a bar's
    p = page(fw, state(fw, EQ_OFF, gains=gains)).open()
    y = fw.fn('peq_ui__e3_y', C.c_int, C.c_double)
    c = e3_canvas(fw, p)
    assert c.text('Enabled: Off') and not [g for g in grid(c) if g[0] == tone(fw)]  # no accent anywhere
    sliders = lambda c: [slider(c, name, y(gain)) for name, gain in zip(('Low', 'Mid', 'High'), gains)]
    before = sliders(c)
    p.centre()
    p.run('e3_single')
    assert p.e3.adjusting and p.e3.focus == E3_LOW
    c = e3_canvas(fw, p)
    after = sliders(c)
    assert not [g for g in grid(c) if g[0] == tone(fw)]  # adjusting doesn't turn it on
    assert after[1:] == before[1:] and after[0][0] != before[0][0] and after[0][1] != before[0][1]  # Low's value and handle
    assert all(bright(after[0][k]) > bright(other[k]) for other in after[1:] for k in (0, 1))  # the brighter


@pytest.mark.parametrize('name, shown, small', (('', 'Advanced', False), ('Rock', 'Rock', False), ('Headphones', 'Headphones', True),
                                                ('Headphones X', 'Headphones X', True), ('Sennheiser HD 600', 'Sennheiser H', True),
                                                ('Café Del Mar!', 'Café Del Ma', True),  # 12 bytes, the e two of them
                                                ('Bass Boost €€', 'Bass Boost ', True),  # byte 12 inside the euro: all of it goes
                                                ('Party Mix \U0001f389', 'Party Mix ', True),
                                                ('€€€€€', '€€€€', True)))
def test_a_playing_peq_shows_on_under_its_preset_s_name(fw, name, shown, small):
    p = page(fw, state(fw, EQ_PEQ)).open()
    p.ui.applied = name.encode()
    c = e3_canvas(fw, p)
    switch = c.texts[0]
    assert switch[0] == shown and len(shown.encode()) <= 12
    assert (switch[1] < c.text('Presets')[0][1]) == small  # past nine characters, smaller
    names = [t[2] for t in c.texts if t[0] in ('Low', 'Mid', 'High')]
    assert len(names) == 3 and all(bright(n) < bright(switch[2]) for n in names)  # dimmed: one takes over as 3EQ


def test_a_stray_continuation_byte_is_drawn_as_itself(fw):
    p = page(fw, state(fw, EQ_PEQ)).open()
    p.ui.applied = b'a\x80b\xf0\x9f\x8e\xb5'
    assert e3_canvas(fw, p).texts[0][0] == 'a\x80b\U0001f3b5'


def test_the_tabs_paint_the_open_one_and_the_wheel_s(fw):
    p = page(fw).open()
    c = Canvas(fw)
    paint(fw, p.ui.bar, c)
    (simple,), (advanced,) = c.text('Simple'), c.text('Advanced')
    assert bright(simple[2]) > bright(advanced[2])  # the open tab the brighter
    (accent,) = [g for g in grid(c) if g[0] == tone(fw)]
    left, width = simple[3][0], simple[3][2]
    assert left <= accent[1] and accent[1] + accent[3] <= left + width  # under Simple
    for _ in range(E3_LOW - E3_TABS):
        p.key(KEY_PREV)
    assert p.e3.focus == E3_TABS
    wheel = Canvas(fw)
    paint(fw, p.ui.bar, wheel)
    assert [g for g in c.rects if g not in wheel.rects] == [] and wheel.texts == c.texts
    (card,) = [g for g in grid(wheel) if g not in grid(c)]  # Advanced on the card Centre would open
    x, w = advanced[3][0], advanced[3][2]
    assert card[1] <= x and x + w <= card[1] + card[3] and card[1] >= left + width  # under Advanced, clear of Simple
    assert card[0] != tone(fw)
    p.e3.status = b'Save failed; EQ unchanged'
    c = Canvas(fw)
    paint(fw, p.ui.bar, c)
    assert not c.texts  # the message has the bar


# EQ per Output.

def owner(card):
    data = card.files.get(f'{OUTPUTS}/owner')
    return data[8:].rstrip(b'\0').decode() if data and data[:8] == b'Q2OWNER\0' else None


def test_each_output_keeps_its_own_eq(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(3, 0, 0)))})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(b'3.5mm')
    assert owner(card) == '3.5mm' and card.state().mode == EQ_3  # the first: keeps what plays
    output(b'usb')
    assert owner(card) == 'usb' and card.state(f'{OUTPUTS}/3.5mm').eq3.bands[0].gain == 3
    usb = state(fw, EQ_PEQ, peq=peak(fw, 6))
    card.files[ACTIVE] = saved(usb)
    output(b'3.5mm')
    assert card.state().mode == EQ_3 and card.state().eq3.bands[0].gain == 3 and fw.calls('stock_eq_trampoline')
    assert card.state(f'{OUTPUTS}/usb').mode == EQ_PEQ and owner(card) == '3.5mm'
    calls = len(fw.calls('fw_rename'))
    output(b'3.5mm')  # the same output: nothing
    assert len(fw.calls('fw_rename')) == calls


def test_off_to_off_leaves_the_filter_out(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_OFF)), f'{OUTPUTS}/usb': saved(state(fw, EQ_OFF, gains=(1, 0, 0)))})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(b'3.5mm')
    output(b'usb')
    assert card.state().eq3.bands[0].gain == 1 and not fw.calls('stock_eq_trampoline')


def test_a_new_output_s_eq_that_can_t_be_saved_keeps_the_old_owner(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/usb': saved(state(fw, EQ_PEQ, peq=peak(fw, 6)))})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(b'3.5mm')
    renames = len(fw.calls('fw_rename'))
    real = card.fopen
    fw.mock('fw_fopen', lambda path, mode, *_: 0 if C.string_at(path).startswith(ACTIVE.encode()) and b'w' in C.string_at(mode) else real(path, mode))
    output(b'usb')
    assert owner(card) == '3.5mm' and card.state().mode == EQ_3 and len(fw.calls('fw_rename')) == renames + 1  # only 3.5mm's slot


@pytest.mark.parametrize('kept', (31, 32))  # EQ_DEVICES (peq.h) less one, and all of them
def test_a_new_bluetooth_device_is_remembered_until_the_slots_are_full(fw, kept):
    macs = [f'00:11:22:33:44:{k:02X}' for k in range(kept)]
    gain = lambda k: k % 6 + 1  # each device's own Low
    files = {f'{OUTPUTS}/{m}': saved(state(fw, EQ_3, gains=(gain(k), 0, 0))) for k, m in enumerate(macs)}
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_PEQ, peq=peak(fw, 6))), **files})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(macs[0].encode())
    output(b'AA:BB:CC:DD:EE:FF')  # a new one: it plays what played, the first's
    assert card.state().mode == EQ_3 and card.state().eq3.bands[0].gain == gain(0)
    output(b'usb')
    slot = f'{OUTPUTS}/AA:BB:CC:DD:EE:FF'
    assert (slot in card.files) == (kept < 32) and owner(card) == 'usb'
    if slot in card.files:  # what it played, filed under it
        assert card.state(slot).mode == EQ_3 and card.state(slot).eq3.bands[0].gain == gain(0)
    assert all(card.state(f'{OUTPUTS}/{m}').eq3.bands[0].gain == gain(k) for k, m in enumerate(macs))  # the others' kept


def test_turning_per_output_off_files_the_eq_and_forgets_the_owner(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(2, 0, 0)))})
    fw.fn('eq_output', None, C.c_char_p)(b'4.4mm')
    card.files[ACTIVE] = saved(state(fw, EQ_3, gains=(5, 0, 0)))
    fw.fn('eq_output_forget', None)()
    assert card.state(f'{OUTPUTS}/4.4mm').eq3.bands[0].gain == 5 and owner(card) is None
    assert card.state().eq3.bands[0].gain == 5  # what plays stays


def test_an_owner_read_back_after_a_restart_files_the_right_slot(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(4, 0, 0))), f'{OUTPUTS}/owner': b'Q2OWNER\0' + b'usb'.ljust(20, b'\0')})
    fw.fn('eq_output_forget', None)()
    assert card.state(f'{OUTPUTS}/usb').eq3.bands[0].gain == 4


@pytest.mark.parametrize('data', (b'Q2OWNER\0' + b'u' * 20, b'Q2OWNEX\0' + b'usb'.ljust(20, b'\0'), b'Q2OWNER\0usb'))
def test_a_bad_owner_file_is_no_owner(fw, data):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/owner': data})
    fw.fn('eq_output_forget', None)()
    assert not [p for p in card.files if p.startswith(OUTPUTS)]


def test_an_unreadable_eq_is_not_filed_over_a_good_slot(fw):
    card = Card(fw, {ACTIVE: b'junk', f'{OUTPUTS}/usb': saved(state(fw, EQ_3, gains=(1, 0, 0)))})
    fw.fn('eq_output', None, C.c_char_p)(b'usb')  # owner usb, its slot loaded over the junk
    card.files[ACTIVE] = b'junk again'
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    assert card.state(f'{OUTPUTS}/usb').eq3.bands[0].gain == 1


@pytest.mark.parametrize('key, caption', (('3.5mm', 'EQ for 3.5 mm'), ('4.4mm', 'EQ for 4.4 mm'), ('usb', 'EQ for USB'),
                                          ('00:11:22:33:44:55', 'EQ for this Bluetooth device')))
def test_the_page_opens_under_whose_eq_it_is(fw, key, caption):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/owner': b'Q2OWNER\0' + key.encode().ljust(20, b'\0')})
    p = Page(fw, card, {('Q2POD', 'EQOUTPUT'): '1'}).open()
    assert p.title() == caption and p.e3.caption
    assert p.key(KEY_NEXT) == 1 and p.title() == '' and not p.e3.caption  # the first turn clears it
    p.tab(300)
    assert p.title() == '' and 'EQ per Output On' in p.captions()


def test_without_per_output_the_page_opens_bare(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/owner': b'Q2OWNER\0' + b'usb'.ljust(20, b'\0')})
    p = Page(fw, card).open()
    assert p.title() == '' and not p.e3.caption


def test_another_output_taking_over_reloads_the_open_page(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(2, 0, 0))), f'{OUTPUTS}/usb': saved(state(fw, EQ_3, gains=(-4, 0, 0)))})
    p = Page(fw, card, {('Q2POD', 'EQOUTPUT'): '1'})
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    p.open()
    assert p.e3.gain[0] == 2
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    assert p.e3.gain[0] == -4 and p.pending('render')
    p.settle()
    assert p.title() == 'EQ for USB'


def test_unapplied_advanced_edits_follow_to_the_new_output(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_PEQ, peq=peak(fw, 6))), f'{OUTPUTS}/usb': saved(state(fw, EQ_3))})
    p = Page(fw, card)
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    p.advanced()
    p.click('Reset to defaults')
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    p.settle()
    assert p.ui.draft.bands[0].gain == 0 and p.ui.dirty and p.captions()[0] == 'Apply changes' and p.captions()[2] == 'Enabled Off'


def test_an_output_change_waits_while_a_keyboard_is_up(fw):
    p = adjust(fw)
    card = p.card
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    assert owner(card) is None and not p.pending('render')


def test_unsaved_gains_wait_for_the_next_pass(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3))})
    p = Page(fw, card)
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    p.open()
    p.e3.gain[0], p.e3.dirty = 3, 1
    card.fail_writes = True
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    assert owner(card) == '3.5mm' and p.e3.dirty
