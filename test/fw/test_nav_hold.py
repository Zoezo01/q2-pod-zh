"""patch/navigation.c's Play/Pause hold and what it reaches: the queue menu (docs/internals.md#queue-menu) and
playlists.c's playlist editor, Playlists' More page and Classic's Playlists rows, then the UI loop's polls that keep
a place, a listen and the state record (resume, library_lists.c's Most Played, scrobbling, and system.c's charge
limit, watchdog and q2-state)."""
import ctypes as C
import re
import struct

import pytest

import host
from conftest import O, Deques, Files, Rows, claim, favdir_card, fnv1a, high_halves, in_fork, ui_name

W_X, W_PARENT, W_EMITTER = O['W_X'], O['W_PARENT'], O['W_EMITTER']
EMIT_TYPE, EMIT_PENDING_REMOVE, EMIT_NEXT = O['EMIT_TYPE'], O['EMIT_PENDING_REMOVE'], O['EMIT_NEXT']
EVENT_TYPE, EVENT_KEY, EVENT_X, EVENT_Y, EVENT_TARGET = (O[k] for k in ('EVENT_TYPE', 'EVENT_KEY', 'EVENT_X', 'EVENT_Y',
                                                                         'EVENT_TARGET'))
EVT_DESTROY, EVT_CLICK, EVT_KEY_UP, EVT_IM_ACTION = O['EVT_DESTROY'], O['EVT_CLICK'], O['EVT_KEY_UP'], O['EVT_IM_ACTION']
EVT_WINDOW_CLOSE, EVT_POINTER_ABORT, EVT_POINTER_UP_BEFORE = O['EVT_WINDOW_CLOSE'], O['EVT_POINTER_ABORT'], O['EVT_POINTER_UP_BEFORE']
KEY_PLAY, KEY_RETURN, KEY_BACK_BTN, KEY_FWD_BTN = O['KEY_PLAY'], O['KEY_RETURN'], O['KEY_BACK_BTN'], O['KEY_FWD_BTN']
REC_ID, REC_NAME, REC_PATH, REC_TITLE, REC_ALBUM, REC_ARTIST = (O[k] for k in ('REC_ID', 'REC_NAME', 'REC_PATH', 'REC_TITLE',
                                                                              'REC_ALBUM', 'REC_ARTIST'))
REC_TYPE, REC_TRACK, REC_DISC, REC_CUE_START, REC_CUE_END = (O[k] for k in ('REC_TYPE', 'REC_TRACK', 'REC_DISC',
                                                                            'REC_CUE_START', 'REC_CUE_END'))
TIMER_CTX, INPUT_KEYS, INPUT_KEY_SIZE, INPUT_KEY_TIME = O['TIMER_CTX'], O['INPUT_KEYS'], O['INPUT_KEY_SIZE'], O['INPUT_KEY_TIME']
PLAYLIST_ADD_MODE, G_ALLSELECT_FLAG = O['PLAYLIST_ADD_MODE'], O['G_ALLSELECT_FLAG']
STOP = 11
PLAYLIST_PICK, PLAYLIST_KEYUP = 0x4b4840, 0x4f2394  # playlists.c's stock handlers
PL_SELECT_ALL, PL_NEW, PL_EDIT, PL_IMPORT, PL_EXPORT, PL_DELETE, PL_DONE = range(7)
PL_STOCK = [0x4b46a4, 0x4b45a4, 0x4b44d4, 0x4b5e18, 0x4b5c70, 0x4b5b08, 0x4b45ec]  # pl_stock[]: stock's own handlers
PL_MARK_X = 333
SETS = host.tools_theme.geometry_sets(host.tools_theme.builtins())
THEMES = host.tools_theme.builtins()


def cstr(address):
    return C.string_at(address).decode('utf-8', 'replace') if address else None


# Records.
class Records:
    """stSongInfo rows as the Q2 lays them out, read by a 64-bit P(): each 4-byte field from REC_NAME (0x08) to the
    cover (0x28) is a pointer the C may read, and P() reads it with the next field as its high half. So each field's
    word is the low half of its own string's address and the high half of the field before's: every field reads as
    a string of its own (empty unless given), each in a page mapped there, past AddressSanitizer's shadow."""
    FIRST, LAST = REC_NAME, 0x2c  # the words: 0x2c is only the cover's high half
    HIGHS = high_halves(0x400)

    def __init__(self):
        self.libc = C.CDLL(None)
        self.libc.mmap.restype, self.libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
        self.libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
        self.pages, self.keep = set(), []
        # this file's high halves (conftest.high_halves), each a string's high half once; its low half may sit near
        # its page's end, so page() maps the next page too
        self.words = iter(self.HIGHS)

    def page(self, address):
        page = address & ~0xfff
        if page not in self.pages:
            assert self.libc.mmap(page, 0x2000, 3, 0x100022, -1, 0) == page  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
            self.pages.add(page)

    def __call__(self, kind=8, id_=0, disc=0, cue=0, end=0, track=0, **fields):
        """A record: name, path, title, album, artist (bytes or str) at their fields; the rest empty strings."""
        named = {REC_NAME: 'name', REC_PATH: 'path', REC_TITLE: 'title', REC_ALBUM: 'album', REC_ARTIST: 'artist',
                 0x1c: 'genre', 0x20: 'composer', 0x24: 'album_artist'}
        assert set(fields) <= set(named.values()), fields
        r = C.create_string_buffer(0x60)
        words = [next(self.words) for _ in range(self.FIRST, self.LAST + 4, 4)]
        for i, off in enumerate(range(self.FIRST, self.LAST + 4, 4)):
            struct.pack_into('<I', r, off, words[i])
        strings = {}
        for i, off in enumerate(range(self.FIRST, self.LAST, 4)):
            address = words[i] | words[i + 1] << 32
            self.page(address)
            value = fields.get(named.get(off, ''), b'')
            value = strings[off] = value.encode() if isinstance(value, str) else value
            C.memmove(address, value + b'\0', len(value) + 1)
        struct.pack_into('<ii', r, REC_ID, id_, 0)
        struct.pack_into('<i', r, REC_TYPE, kind)
        struct.pack_into('<i', r, REC_TRACK, track)
        struct.pack_into('<i', r, REC_DISC, disc)
        struct.pack_into('<ii', r, REC_CUE_START, cue, end)
        self.keep.append(r)
        # each field, read as P() reads it, is its own string: the numbers packed after left the words alone
        assert all(field(C.addressof(r), off) == s.decode('utf-8', 'replace') for off, s in strings.items())
        return C.addressof(r)

    def close(self):
        for page in self.pages:
            self.libc.munmap(page, 0x2000)


@pytest.fixture
def recs():
    made = Records()
    yield made
    made.close()


def field(r, off):
    return cstr(C.c_void_p.from_address(r + off).value)


# AWTK, as far as the C reads it.
class Node:
    """A widget: a zeroed block holding its rect, parent and click emitter at stock's offsets; the rest here."""
    def __init__(self, kind, parent):
        self.block, self.emitter = C.create_string_buffer(0x200), C.create_string_buffer(8)
        self.addr = C.addressof(self.block)
        C.c_void_p.from_address(self.addr + W_PARENT).value = parent or None
        C.c_void_p.from_address(self.addr + W_EMITTER).value = C.addressof(self.emitter)
        self.kind, self.parent, self.children = kind, parent, []
        self.name = self.text = self.image = ''
        self.visible, self.props, self.on, self.strings, self.tr = 1, {}, [], [], False

    def rect(self):
        return struct.unpack_from('<4i', self.block, W_X)

    def link(self):
        """emitter_on's items as click_bound walks them: {ctx @0, type @8, handler @12, pending_remove, next}."""
        address = 0
        for item in reversed(self.on):
            C.c_void_p.from_address(C.addressof(item[3])).value = item[2] or None
            C.c_int.from_address(C.addressof(item[3]) + EMIT_TYPE).value = item[0]
            C.c_void_p.from_address(C.addressof(item[3]) + 12).value = item[1] or None
            C.c_void_p.from_address(C.addressof(item[3]) + EMIT_NEXT).value = address or None
            address = C.addressof(item[3])
        C.c_void_p.from_address(C.addressof(self.emitter)).value = address or None


class Ui:
    """Widgets, the window stack, timers, the navigator and toasts, mocked over the stubs: what a page's code builds
    can be read back, its handlers fired and its timers run as the next loop pass would."""
    def __init__(self, fw):
        self.fw, self.nodes, self.errors = fw, {}, []
        self.wm = self.new('window_manager', 0)
        self.stack, self.timers, self.toasts, self.went, self.closed, self.dispatched = [], {}, [], [], [], []
        self.routes, self.answers, self.contexts = {}, {}, []
        self.keys = C.create_string_buffer(INPUT_KEYS + 16 * INPUT_KEY_SIZE)
        self.pressed, self.ids, self.held = 0, 0, []
        mocks = {
            'window_manager': lambda *_: self.wm,
            'window_manager_get_top_window': lambda *_: self.top(),
            'window_manager_get_pointer_pressed': lambda *_: self.pressed,
            'window_manager_get_input_device_status': lambda *_: C.addressof(self.keys),
            'window_manager_close_window_force': lambda wm, w, *_: self.close(w),
            'window_create': lambda *_: self.window(''),
            'window_close': lambda w, *_: self.close(w),
            'widget_factory_create_widget': lambda f, kind, parent, x, y, w, *_: self.child(parent, cstr(kind), x, y, w, 0),
            'widget_get_child': lambda w, i, *_: self.kid(w, C.c_int(i).value),
            'widget_count_children': lambda w, *_: len(self.kids(w)),
            'widget_lookup': lambda w, name, deep, *_: self.lookup(w, cstr(name), C.c_int(deep).value),
            'widget_get_prop_int': lambda w, name, d, *_: self.prop(w, cstr(name), C.c_int(d).value),
            'widget_get_prop_bool': lambda w, name, d, *_: self.prop(w, cstr(name), C.c_int(d).value),
            'widget_get_prop_str': lambda w, name, d, *_: self.prop_str(w, cstr(name), d),
            'widget_set_prop_int': lambda w, name, v, *_: self.set(w, cstr(name), C.c_int(v).value),
            'widget_set_prop_str': lambda w, name, v, *_: self.set(w, cstr(name), cstr(v)),
            'widget_set_name': lambda w, name, *_: self.attr(w, 'name', cstr(name)),
            'widget_get_type': lambda w, *_: self.c(w, self.nodes[w].kind) if w in self.nodes else 0,
            'widget_get_visible': lambda w, *_: self.nodes[w].visible if w in self.nodes else 0,
            'widget_set_visible': lambda w, v, *_: self.attr(w, 'visible', C.c_int(v).value),
            'widget_set_text_utf8': lambda w, s, *_: self.attr(w, 'text', cstr(s)),
            'widget_set_tr_text': lambda w, s, *_: self.attr(w, 'text', cstr(s)) or self.attr(w, 'tr', True),
            'widget_get_text_utf8': self.get_text,
            'widget_get_text': lambda w, *_: self.wide(w),
            'widget_move_resize': lambda w, x, y, ww, h, *_: self.place(w, x, y, ww, h),
            'widget_resize': lambda w, ww, h, *_: self.place(w, self.nodes[w].rect()[0], self.nodes[w].rect()[1], ww, h)
            if w in self.nodes else 0,
            'widget_on': lambda w, kind, fn, ctx, *_: self.bind(w, C.c_int(kind).value, fn, ctx),
            'widget_off_by_func': lambda w, kind, fn, ctx, *_: self.unbind(w, C.c_int(kind).value, fn, ctx),
            'widget_destroy_children': lambda w, *_: self.nodes[w].children.clear() if w in self.nodes else 0,
            'widget_restack': lambda w, at, *_: self.restack(w, C.c_int(at).value),
            'widget_use_style': lambda w, s, *_: self.set(w, 'style', cstr(s)),
            'image_base_set_image': lambda w, s, *_: self.attr(w, 'image', cstr(s)),
            'timer_add': self.add_timer,
            'timer_remove': lambda t, *_: self.timers.pop(C.c_uint(t).value, None) and 0,
            'navigator_to': lambda name, *_: self.go(cstr(name), 0),
            'navigator_to_with_context': lambda name, ctx, *_: self.go(cstr(name), ctx),
            'navigator_window_is_exist': lambda name, *_: any(self.nodes[w].name == cstr(name) for w in self.stack),
            'navigator_back': lambda *_: self.back(),
            'pointer_event_init': self.pointer_event,
            'stock_dispatch_trampoline': lambda w, e, *_: self.dispatched.append((w, C.c_int.from_address(e + EVENT_TYPE).value)),
            'tk_str_start_with': lambda s, prefix, *_: bool(s) and C.string_at(s).startswith(C.string_at(prefix)),
        }
        for kind in ('list_item', 'button', 'image', 'hscroll_label', 'label', 'list_view', 'scroll_view'):
            mocks[f'{kind}_create'] = lambda parent, x, y, w, h, *_, kind=kind: self.child(parent, kind, x, y, w, h)
        for name, fn in mocks.items():
            fw.mock(name, self.guard(fn))
        fw.var('g_backlight_status', C.c_ubyte).value = 1

    def guard(self, fn):
        """A mock's exception is kept: ctypes would print it and answer 0."""
        def run(*args):
            try:
                return fn(*args)
            except Exception as e:  # noqa: BLE001
                self.errors.append(repr(e))
                raise
        return run

    def new(self, kind, parent):
        node = Node(kind, parent)
        self.nodes[node.addr] = node
        return node.addr

    def child(self, parent, kind, x=0, y=0, w=0, h=0):
        address = self.new(kind, parent)
        if parent in self.nodes:
            self.nodes[parent].children.append(address)
        self.place(address, x, y, w, h)
        return address

    def window(self, name, kind='window'):
        address = self.new(kind, self.wm)
        self.nodes[address].name = name
        self.stack.append(address)
        return address

    def top(self):
        return self.stack[-1] if self.stack else 0

    def close(self, w):
        if w in self.stack:
            self.stack.remove(w)
            self.closed.append(w)
            self.fire(w, EVT_DESTROY)
        return 0

    def back(self):
        self.went.append('back')
        if self.stack:
            self.close(self.stack[-1])
        return 0

    def go(self, name, ctx):
        if name == 'dialog/msginfo_dialog':
            self.toasts.append(cstr(ctx + 8))
            return 0
        self.went.append(name)
        self.contexts.append((name, ctx))
        if name in self.routes:
            self.routes[name](ctx)
        return self.answers.get(name, 0)

    def kids(self, w):
        return self.nodes[w].children if w in self.nodes else []

    def kid(self, w, i):
        kids = self.kids(w)
        return kids[i] if 0 <= i < len(kids) else 0

    def lookup(self, w, name, deep):
        for k in self.kids(w):
            if self.nodes[k].name == name:
                return k
            if deep and (found := self.lookup(k, name, deep)):
                return found
        return 0

    def prop(self, w, name, default):
        value = self.nodes[w].props.get(name, default) if w in self.nodes else default
        return value if isinstance(value, int) else default

    def c(self, w, text):
        buf = C.create_string_buffer(text.encode())
        self.nodes[w].strings.append(buf)
        return C.addressof(buf)

    def prop_str(self, w, name, default):
        node = self.nodes.get(w)
        value = None if node is None else node.name if name == 'name' else node.image if name == 'image' else node.props.get(name)
        return self.c(w, value) if isinstance(value, str) and value else default

    def set(self, w, name, value):
        if w in self.nodes:
            self.nodes[w].props[name] = value
        return 0

    def attr(self, w, name, value):
        if w in self.nodes:
            setattr(self.nodes[w], name, value)
        return 0

    def get_text(self, w, out, n, *_):
        data = self.nodes[w].text.encode()[:n - 1] if w in self.nodes else b''
        C.memmove(out, data + b'\0', len(data) + 1)
        return 0

    def wide(self, w):
        if w not in self.nodes:
            return 0
        chars = [ord(ch) for ch in self.nodes[w].text] + [0]
        buf = (C.c_uint * len(chars))(*chars)
        self.nodes[w].strings.append(buf)
        return C.addressof(buf)

    def place(self, w, x, y, ww, h):
        if w in self.nodes:
            struct.pack_into('<4i', self.nodes[w].block, W_X, *(C.c_int(v).value for v in (x, y, ww, h)))
        return 0

    def bind(self, w, kind, fn, ctx):
        if w not in self.nodes or not fn:  # emitter_on refuses no handler (TK_INVALID_ID)
            return 0
        self.ids += 1
        self.nodes[w].on.append([kind, fn, ctx, C.create_string_buffer(0x28)])
        self.nodes[w].link()
        return self.ids

    def unbind(self, w, kind, fn, ctx):
        if w in self.nodes:
            node = self.nodes[w]
            node.on = [i for i in node.on if (i[0], i[1], i[2]) != (kind, fn, ctx)]
            node.link()
        return 0

    def restack(self, w, at):
        parent = self.nodes[self.nodes[w].parent]
        parent.children.remove(w)
        parent.children.insert(at, w)
        return 0

    def add_timer(self, fn, ctx, ms, *_):
        self.ids += 1
        self.timers[self.ids] = (fn, ctx, ms)
        return self.ids

    def pointer_event(self, out, kind, target, x, y, *_):
        C.memset(out, 0, 0x20)
        C.c_int.from_address(out + EVENT_TYPE).value = kind
        C.c_void_p.from_address(out + EVENT_TARGET).value = target or None
        C.c_int.from_address(out + EVENT_X).value, C.c_int.from_address(out + EVENT_Y).value = C.c_int(x).value, C.c_int(y).value
        return out

    # What a test does.
    def fn(self, name):
        """A function's address, in navigation.c or the unit it moved to (ui_name finds it), as the C passes it to
        widget_on and timer_add."""
        return C.cast(getattr(self.fw.lib, name if '__' in name or name.startswith('ringnav') else ui_name(self.fw.lib, name)), C.c_void_p).value

    def due(self, name=None):
        """The next loop pass: every timer pending now (or only name's) runs once, as stock's one-shots do."""
        ran = []
        for t, (fn, ctx, ms) in list(self.timers.items()):
            if name is not None and fn != self.fn(name):
                continue
            del self.timers[t]
            info = C.create_string_buffer(0x40)
            C.c_void_p.from_address(C.addressof(info) + TIMER_CTX).value = ctx or None
            ran.append(C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(C.addressof(info)))
        return ran

    def pending(self):
        return [fn for fn, _, _ in self.timers.values()]

    def fire(self, w, kind, event=None):
        """w's handlers for kind, in order, until one stops it; the last answer."""
        answer = 0
        if event is None:
            event = C.create_string_buffer(0x40)
            C.c_int.from_address(C.addressof(event) + EVENT_TYPE).value = kind
        for k, fn, ctx, _ in list(self.nodes[w].on if w in self.nodes else []):
            if k == kind:
                answer = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, C.addressof(event))
                if answer == STOP:
                    break
        return answer

    def click(self, w, x=0):
        event = C.create_string_buffer(0x40)
        self.pointer_event(C.addressof(event), EVT_CLICK, w, x, 0)
        return self.fire(w, EVT_CLICK, event)

    def key(self, w, key, kind=EVT_KEY_UP):
        event = C.create_string_buffer(0x40)
        C.c_int.from_address(C.addressof(event) + EVENT_TYPE).value = kind
        C.c_int.from_address(C.addressof(event) + EVENT_KEY).value = key
        return self.fire(w, kind, event)

    def handlers(self, w, kind):
        return [fn for k, fn, _, _ in self.nodes[w].on if k == kind]

    def label(self, item):
        """A row's caption: its first labelled descendant's text."""
        for k in self.kids(item):
            if self.nodes[k].text:
                return self.nodes[k].text
            if (found := self.label(k)) is not None:
                return found
        return None

    def rows(self, view):
        return [self.label(item) for item in self.kids(view)]

    def find(self, w, text):
        """The row of view w whose caption is text."""
        rows = self.rows(w)
        assert text in rows, rows
        return self.kids(w)[rows.index(text)]

    def press(self, key=KEY_PLAY, at=0x1234567890):
        """Stock's input device status: key held, pressed at at (the Play hold's latch reads it)."""
        struct.pack_into('<IQ', self.keys, INPUT_KEYS, key, 0)
        struct.pack_into('<Q', self.keys, INPUT_KEYS + INPUT_KEY_TIME, at)


@pytest.fixture
def ui(fw):
    world = Ui(fw)
    yield world
    assert not world.errors


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


@pytest.fixture(scope='module')
def power_fields(library):
    """system.c's own state, pw: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'system.o', 'pw')


@pytest.fixture
def power(fw, power_fields):
    base = C.addressof(fw.var('system__pw', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + power_fields[name])


@pytest.fixture(scope='module')
def lists_fields(library):
    """library_lists.c's own state, ll: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'library_lists.o', 'll')


@pytest.fixture
def lists(fw, lists_fields):
    base = C.addressof(fw.var('library_lists__ll', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + lists_fields[name])


@pytest.fixture(scope='module')
def rates_fields(library):
    """ratings.c's own state, ra: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'ratings.o', 'ra')


@pytest.fixture
def rates(fw, rates_fields):
    base = C.addressof(fw.var('ratings__ra', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + rates_fields[name])


@pytest.fixture
def stock_globals():
    """Stock's add mode and Select All flag, at their fixed addresses in conftest's map: zeroed, put back after."""
    saved = [(a, C.string_at(a, 4)) for a in (PLAYLIST_ADD_MODE, G_ALLSELECT_FLAG)]
    for a, _ in saved:
        claim(a)
        C.memset(a, 0, 4)
    yield
    for a, data in saved:
        C.memmove(a, data, 4)


def use_set(st, name):
    """The theme whose geometry set is name, as theme_boot leaves st.geom and st.theme."""
    at = [g.name for g in SETS].index(name)
    st('theme').value = next(i for i, t in enumerate(THEMES) if SETS.index(host.tools_theme.geometry(t)) == at)
    st('geom').value, st('settings_read').value = at, 1  # read: the config's THEME would set it again
    return SETS[at]


CLASSIC, MINIMAL = 'classic-light', 'ipod'


# The playlist editor (ple_*).
class Ple(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view songs picker edit match'.split()),
                *((k, C.c_int) for k in 'dest mode selected pending nmatch'.split()),
                ('key', C.c_uint), ('timer', C.c_uint), ('name', C.c_char * 128), ('query', C.c_char * 128)]


def directory(fw):
    """The deque stock's queries fill: staged() puts its own there for each query (Deques masks the high half)."""
    return fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value


class Table:
    """The library's playlist table: songs (id > 100, cd_num their playlist's), as getMusicByPlayList,
    checkPlayListExist, batch_add_file and sqlite3_exec see it, each query's records fresh as stock's; and the
    library's songs (getAllMusic). A failing statement (fail) rolls back."""
    def __init__(self, fw, recs, deques, titles):
        self.fw, self.recs, self.deques, self.titles = fw, recs, deques, titles
        self.rows, self.log, self.fail, self.saved = {}, [], (), {}
        self.library = [recs(path=p, title=t, id_=i + 1) for i, (p, t) in enumerate(titles.items())]
        fw.mock('getMusicByPlayList', self.playlist)
        fw.mock('sort_playlist_addtime', self.playlist)  # the editor's read
        fw.mock('getAllMusic', lambda *_: self.fill(self.library))
        fw.mock('checkPlayListExist', lambda r, dest, *_: (C.c_int(dest).value, field(r, REC_PATH)) in self.rows.values())
        fw.mock('batch_add_file', self.add)
        fw.mock('_create_vector', lambda *_: 0x7e)  # the editor's own selection
        fw.mock('sqlite3_open', lambda path, db, *_: setattr(C.c_void_p.from_address(db), 'value', 0x5d))
        fw.mock('sqlite3_exec', self.exec)

    def fill(self, records):
        self.deques[directory(self.fw)][:] = records
        return len(records)

    def songs(self, dest):
        return [self.titles[p] for i, (c, p) in sorted(self.rows.items()) if c == dest]

    def put(self, dest, *paths):
        for p in paths:
            self.rows[max(self.rows, default=100) + 1] = (dest, p)

    def playlist(self, dest, *_):
        dest = C.c_int(dest).value
        return self.fill([self.recs(path=p, title=self.titles[p], id_=i) for i, (c, p) in sorted(self.rows.items()) if c == dest])

    def add(self, cls, kind, songs, select, dest, *_):
        for r in self.deques[songs]:
            self.put(C.c_int(dest).value, field(r, REC_PATH))
        return 0

    def exec(self, db, sql, callback, ctx, *_):
        text = cstr(sql)
        self.log.append(text)
        if any(f in text for f in self.fail):
            return 1
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback) if callback else None
        changes = 0
        for s in text.split(';'):
            rows: list = []
            if s == 'begin immediate':
                self.saved = dict(self.rows)
            elif s == 'rollback':
                self.rows = self.saved
            elif s in ('commit', 'PRAGMA busy_timeout=500'):  # pl_db's wait for a reader
                pass
            elif m := re.fullmatch(r'select id,fileurl,time,time_end from playlist where cd_num=(-?\d+) and id in \((-?\d+),(-?\d+)\)', s):
                dest, a, b = map(int, m.groups())
                rows = [[b'%d' % i, p.encode(), b'0', b'0'] for i, (c, p) in sorted(self.rows.items()) if c == dest and i in (a, b)]
            elif s.startswith('select case when ifnull(max(id),100)<2147483647'):
                rows = [[b'%d' % (max(max(self.rows, default=100), 100) + 1)]]
            elif m := re.fullmatch(r'update playlist set id=(\d+) where cd_num=(\d+) and id=(\d+)', s):
                new, dest, old = map(int, m.groups())
                changes = int(self.rows.get(old, (None,))[0] == dest and new not in self.rows)
                if changes:
                    self.rows[new] = self.rows.pop(old)
            elif m := re.fullmatch(r'delete from playlist where cd_num=(\d+) and id=(\d+)', s):
                dest, old = map(int, m.groups())
                changes = int(self.rows.get(old, (None,))[0] == dest)
                if changes:
                    del self.rows[old]
            elif s == 'select changes()':
                rows = [[b'%d' % changes]]
            else:
                raise AssertionError(s)
            for row in rows:
                if call and call(ctx, len(row), (C.c_char_p * len(row))(*row), None):
                    return 4  # SQLITE_ABORT
        return 0


TITLES = {'/mnt/mmc/a.flac': 'One', '/mnt/mmc/b.flac': 'Two', '/mnt/mmc/c.flac': 'Three', '/mnt/mmc/d.flac': 'Twenty',
          '/mnt/mmc/e.flac': 'Five'}


class Editor:
    """Road Trip (playlist 3: One, Two, Three) as Playlists' row 1 opens it in the editor."""
    def __init__(self, fw, ui, recs, songs=('a', 'b', 'c')):
        self.fw, self.ui = fw, ui
        self.deques = Deques(fw)
        self.table = Table(fw, recs, self.deques, TITLES)
        self.table.put(3, *(f'/mnt/mmc/{s}.flac' for s in songs))
        headers = [recs(title='Gym', disc=1, id_=1), recs(title='Road Trip', disc=3, id_=3)]
        fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = self.deques.new(headers)
        self.ple = fw.var('playlists__ple', Ple)
        self.open = fw.fn('playlists__ple_open', C.c_int, C.c_void_p, C.c_void_p)

    def view(self):
        return self.ple.view

    def rows(self):
        return self.ui.rows(self.ple.view)

    def pick(self, caption):
        """A row's click and the pass after it, which runs it."""
        self.ui.click(self.ui.find(self.ple.view, caption))
        self.ui.due('ple_run')

    def title(self):
        """The page's title line (page_title's label)."""
        return self.ui.nodes[self.ui.kids(self.ple.page)[0]].text

    def ticked(self):
        return [self.ui.label(item) for item in self.ui.kids(self.ple.view)
                if any(self.ui.nodes[k].image == 'select' for k in self.ui.kids(item))]


@pytest.fixture
def editor(fw, ui, recs, st):
    e = Editor(fw, ui, recs)
    use_set(st, MINIMAL)
    assert e.open(1, None) == 0
    return e


def test_edit_playlist_lists_its_songs_under_add_and_done(editor, ui):
    assert ui.nodes[ui.top()].name == 'playlistedit_page' and editor.ple.dest == 3
    assert editor.rows() == ['Add Songs', 'Done', 'One', 'Two', 'Three']
    assert editor.title() == 'Editing: Road Trip'
    assert ui.nodes[editor.view()].props['_ringnav_index'] == 2  # the first song, below Add Songs and Done


@pytest.mark.parametrize('at', [2, 5, 0xffffffff])
def test_edit_playlist_of_no_such_row_opens_nothing(fw, ui, recs, at):
    e = Editor(fw, ui, recs)
    assert e.open(at, None) == 0 and not ui.stack and not e.ple.page


@pytest.mark.parametrize('disc', [-1, 100])
def test_a_playlist_whose_number_is_no_playlists_opens_nothing(fw, ui, recs, disc):
    e = Editor(fw, ui, recs)
    e.fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = e.deques.new([recs(title='Odd', disc=disc)])
    assert e.open(0, None) == 0 and not ui.stack


def test_a_second_open_is_the_page_already_open(editor, ui):
    assert editor.open(0, None) == 0 and len(ui.stack) == 1 and editor.ple.dest == 3


def test_move_down_saves_the_new_order_and_follows_the_song(editor, ui):
    editor.pick('Two')
    assert editor.rows() == ['Move Up', 'Move Down', 'Remove from Playlist'] and editor.title() == 'Song Actions: Road Trip'
    editor.pick('Move Down')
    assert editor.table.songs(3) == ['One', 'Three', 'Two'] and editor.rows()[2:] == ['One', 'Three', 'Two']
    assert ui.nodes[editor.view()].props['_ringnav_index'] == 4 and editor.table.log[-1] == 'commit'


def test_move_up_saves_the_new_order(editor):
    editor.pick('Three')
    editor.pick('Move Up')
    assert editor.table.songs(3) == ['One', 'Three', 'Two'] and editor.ple.selected == 1


@pytest.mark.parametrize('song, move', [('One', 'Move Up'), ('Three', 'Move Down')])
def test_a_move_past_either_end_changes_nothing(editor, song, move):
    editor.pick(song)
    editor.pick(move)
    assert editor.table.songs(3) == ['One', 'Two', 'Three'] and not editor.table.log
    assert editor.ple.mode == 0


def test_a_move_that_cant_be_saved_says_so_and_keeps_the_order(editor, ui):
    editor.table.fail = ('update',)
    editor.pick('Two')
    editor.pick('Move Down')
    assert ui.toasts == ['Could not save playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']
    assert editor.table.log[-1] == 'rollback'


def test_a_song_changed_under_the_editor_is_not_moved(editor, ui):
    editor.table.rows[102] = (3, '/mnt/mmc/e.flac')  # Two's row is another song now
    editor.pick('Two')
    editor.pick('Move Down')
    assert ui.toasts == ['Could not save playlist'] and editor.table.log[-1] == 'rollback'


def test_remove_asks_then_takes_the_song_out(editor):
    editor.pick('Two')
    editor.pick('Remove from Playlist')
    assert editor.rows() == ['Remove from Playlist', 'Cancel'] and editor.title() == 'Remove song?: Road Trip'
    editor.pick('Remove from Playlist')
    assert editor.table.songs(3) == ['One', 'Three'] and editor.rows() == ['Add Songs', 'Done', 'One', 'Three']


def test_cancel_keeps_the_song(editor):
    editor.pick('Two')
    editor.pick('Remove from Playlist')
    editor.pick('Cancel')
    assert editor.table.songs(3) == ['One', 'Two', 'Three'] and editor.rows()[0] == 'Add Songs' and not editor.table.log


def test_the_last_song_removed_leaves_the_selection_on_the_list(fw, ui, recs, st):
    e = Editor(fw, ui, recs, songs=('a',))
    use_set(st, MINIMAL)
    e.open(1, None)
    e.pick('One')
    e.pick('Remove from Playlist')
    e.pick('Remove from Playlist')
    assert e.rows() == ['Add Songs', 'Done'] and e.ple.selected == 0


def test_a_remove_that_cant_be_saved_says_so(editor, ui):
    editor.table.fail = ('delete',)
    editor.pick('One')
    editor.pick('Remove from Playlist')
    editor.pick('Remove from Playlist')
    assert ui.toasts == ['Could not save playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']


def test_done_leaves_the_editor_and_its_state(editor, ui):
    editor.pick('Done')
    assert ui.went == ['back'] and not ui.stack and editor.ple.page is None and editor.ple.songs is None


def test_add_songs_lists_the_library_with_the_playlists_ticked(editor, ui):
    editor.pick('Add Songs')
    assert editor.rows() == ['Search', 'One', 'Two', 'Three', 'Twenty', 'Five']
    assert editor.title() == 'Add Songs: Road Trip' and editor.ticked() == ['One', 'Two', 'Three']


def test_a_picked_song_is_added_and_ticked(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Five')
    assert editor.table.songs(3) == ['One', 'Two', 'Three', 'Five'] and ui.toasts == ['Added to Playlist']
    assert 'Five' in editor.ticked() and editor.ple.mode == 1  # the picker stays on its song
    calls = [a for _, a in editor.fw.calls('batch_add_file')]
    assert [a[:2] for a in calls] == [(0xf001, 0xf00b)] and calls[0][4] == 3


def test_a_song_already_there_is_not_added_twice(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Two')
    assert ui.toasts == ['Already in playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']
    assert not editor.fw.calls('batch_add_file')


def test_an_add_that_stock_refused_says_so(editor, ui):
    editor.pick('Add Songs')
    editor.fw.mock('batch_add_file', lambda *_: 0)
    editor.pick('Five')
    assert ui.toasts == ['Could not save playlist'] and 'Five' not in editor.ticked()


def test_the_add_selection_is_made_once(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Five')
    editor.pick('Twenty')
    assert len(editor.fw.calls('_create_vector')) == 1 and len({a[3] for _, a in editor.fw.calls('batch_add_file')}) == 1


def test_an_add_with_no_memory_says_so(editor, ui, fw):
    editor.pick('Add Songs')
    fw.mock('fw__create_deque', lambda *_: 0)
    editor.pick('Five')
    assert ui.toasts == ['Could not save playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']


def test_search_filters_by_title_case_aside(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    edit = ui.lookup(editor.ple.page, 'search_edit', 1)
    assert edit and editor.ple.mode == 4 and ui.nodes[edit].props['tips'] == 'Search songs'
    ui.nodes[edit].text = 'tW'
    ui.fire(edit, EVT_IM_ACTION)
    ui.due('ple_run')
    assert editor.rows() == ['Search: tW', 'Two', 'Twenty'] and editor.ticked() == ['Two']


def test_a_search_that_matches_nothing_says_so(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    ui.nodes[ui.lookup(editor.ple.page, 'search_edit', 1)].text = 'zz'
    assert ui.key(editor.ple.page, KEY_RETURN) == STOP  # Return applies the box, from the next pass
    ui.due('ple_run')
    assert editor.rows() == ['Search: zz', 'No songs match']
    ui.click(ui.find(editor.ple.view, 'No songs match'))
    assert not ui.timers  # the note is no row to pick


def test_the_search_box_keeps_its_query(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    ui.nodes[ui.lookup(editor.ple.page, 'search_edit', 1)].text = 'one'
    ui.key(editor.ple.page, KEY_RETURN)
    ui.due('ple_run')
    editor.pick('Search: one')
    assert ui.nodes[ui.lookup(editor.ple.page, 'search_edit', 1)].text == 'one'


def test_an_unreadable_box_clears_the_query(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    ui.fw.mock('widget_get_text_utf8', lambda *_: 1)
    ui.key(editor.ple.page, KEY_RETURN)
    ui.due('ple_run')
    assert editor.rows()[0] == 'Search' and len(editor.rows()) == 6


@pytest.mark.parametrize('steps', [('Two',), ('Two', 'Remove from Playlist'), ('Add Songs',)])
def test_return_from_a_step_goes_back_to_the_list(editor, ui, steps):
    for caption in steps:
        editor.pick(caption)
    assert ui.key(editor.ple.page, KEY_RETURN) == STOP
    assert editor.ple.mode == 0 and editor.rows()[:2] == ['Add Songs', 'Done'] and len(ui.stack) == 1


def test_return_on_the_list_leaves(editor, ui):
    assert ui.key(editor.ple.page, KEY_RETURN) == STOP and ui.went == ['back'] and not ui.stack


def test_other_keys_are_not_the_editors(editor, ui):
    assert ui.key(editor.ple.page, KEY_PLAY) == 0 and len(ui.stack) == 1


def test_a_second_click_in_one_pass_is_dropped(editor, ui):
    ui.click(ui.find(editor.view(), 'Two'))
    ui.click(ui.find(editor.view(), 'Three'))
    ui.due('ple_run')
    assert editor.title() == 'Song Actions: Road Trip'
    editor.pick('Move Down')  # Two's actions, the first click's: Three, the last song, could not move down
    assert editor.table.songs(3) == ['One', 'Three', 'Two']


def test_a_click_run_after_the_page_closed_does_nothing(editor, ui):
    ui.click(ui.find(editor.view(), 'Done'))
    ui.close(editor.ple.page)
    ui.due()
    assert ui.went == []


def test_an_out_of_range_pick_does_nothing(editor, ui):
    editor.pick('Add Songs')
    editor.ple.pending = 9
    rows = editor.rows()
    assert editor.fw.fn('playlists__ple_run', C.c_int, C.c_void_p)(None) == 0 and editor.rows() == rows


def test_classic_editor_moves_only_the_selected_rows_title(fw, ui, recs, st):
    e = Editor(fw, ui, recs)
    use_set(st, CLASSIC)
    e.open(1, None)
    focus = [a[0] for _, a in fw.calls('hscroll_label_set_only_focus') if a[1] == 1]
    assert focus == [ui.kids(item)[0] for item in ui.kids(e.view())] and len(focus) == 5


@pytest.mark.parametrize('theme', [MINIMAL, CLASSIC])
def test_add_songs_title_names_the_playlist_unless_the_bar_holds_it(fw, ui, recs, st, theme):
    """A bar that holds the page's caption (Classic's) has room for the task's name alone."""
    e = Editor(fw, ui, recs)
    use_set(st, theme)
    e.open(1, None)
    e.pick('Add Songs')
    assert e.title().startswith('Add Songs') and ('Road Trip' in e.title()) == (theme == MINIMAL)


# The side buttons move the selected song: phase 1 the press, 2 its release, 3 stock's long press.
def ple_key(fw, key, phase):
    return fw.fn('ple_key', C.c_int, C.c_uint, C.c_int)(key, phase)


def test_the_next_button_moves_the_selected_song_down(editor, ui, fw):
    ui.nodes[editor.view()].props['_ringnav_index'] = 3  # Two
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1
    assert editor.table.songs(3) == ['One', 'Three', 'Two']
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1 and editor.table.songs(3) == ['One', 'Three', 'Two']  # a repeat
    assert ple_key(fw, KEY_FWD_BTN, 2) == 1 and editor.ple.key == 0


def test_the_previous_button_moves_it_up(editor, ui, fw):
    ui.nodes[editor.view()].props['_ringnav_index'] = 3
    ple_key(fw, KEY_BACK_BTN, 1)
    assert editor.table.songs(3) == ['Two', 'One', 'Three']


def test_a_button_on_add_and_done_moves_nothing(editor, ui, fw):
    ui.nodes[editor.view()].props['_ringnav_index'] = 1
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1 and not editor.table.log


def test_a_button_under_another_window_is_held_but_moves_nothing(editor, ui, fw):
    ui.window('msginfo_dialog')
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1 and not editor.table.log and editor.ple.key == 2


def test_a_release_after_the_page_closed_is_still_swallowed(editor, ui, fw):
    ple_key(fw, KEY_BACK_BTN, 1)
    ui.back()
    assert editor.ple.page is None and editor.ple.key == 1
    assert ple_key(fw, KEY_BACK_BTN, 2) == 1 and ple_key(fw, KEY_BACK_BTN, 2) == 0


def test_the_buttons_are_stocks_without_the_editor(fw, ui):
    assert ple_key(fw, KEY_FWD_BTN, 1) == 0 and ple_key(fw, KEY_PLAY, 1) == 0


def test_a_long_press_of_a_side_button_is_the_editors(editor, ui, fw):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, EVENT_KEY, KEY_FWD_BTN)
    assert fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)(None, event) == STOP
    assert not fw.calls('stock_keylong_trampoline') and not editor.table.log


# Stock code at a fixed address, for a handler the C calls there. Only ever in a fork.
def stock_handler(address, result=0):
    """x86-64 at address: its first two arguments and the event's x (rsi + EVENT_X) logged, result returned."""
    log = (C.c_long * 3)()
    claim(address)
    code = (b'\x48\xb8' + struct.pack('<Q', C.addressof(log)) + bytes.fromhex('488938 48897008') +
            b'\x8b\x4e' + bytes([EVENT_X]) + bytes.fromhex('48894810') + b'\xb8' + struct.pack('<I', result) + b'\xc3')
    C.memmove(address, code, len(code))
    return log


# Playlists' More page (ringnav_playlistsmore).
class More:
    """Stock's playlistsmore_page: Rename and Delete Song rows, each a list_item holding its button, icon and label."""
    def __init__(self, fw, ui, pitch):
        self.ui, self.pitch = ui, pitch
        self.win = ui.window('playlistsmore_page')
        self.rows = []
        for i, name in enumerate(('rename', 'del')):
            item = ui.child(self.win, 'list_item', 0, 10 + i * pitch, 375, pitch)
            button = ui.child(item, 'button', 20, 0, 335, pitch)
            icon = ui.child(button, 'image', 10, 0, 52, pitch)
            label = ui.child(button, 'label', 72, 0, 253, 20)
            ui.nodes[icon].name, ui.nodes[label].name = f'img_{name}', f'label_{name}'
            ui.nodes[label].text = 'Rename' if name == 'rename' else 'Delete Song'
            self.rows.append((item, icon, label))
        self.more = fw.fn('ringnav_playlistsmore', C.c_int, C.c_void_p, C.c_void_p)
        fw.mock('stock_playlistsmore_trampoline', lambda *_: 0x55)

    def edit(self):
        """The row this adds: its list_item, button, icon and label."""
        item = self.ui.kids(self.win)[-1]
        button = self.ui.kids(item)[0]
        icon, label = self.ui.kids(button)[:2]
        return item, button, icon, label


def pitch(st):
    return host.tools_theme.immediate_values(SETS[st('geom').value])['pitch']


def test_more_adds_edit_playlist_after_delete_at_the_same_spacing(fw, ui, st):
    use_set(st, MINIMAL)
    more = More(fw, ui, pitch(st))
    assert more.more(more.win, 1) == 0x55 and ui.nodes[more.win].props['_playlist'] == 1
    item, button, icon, label = more.edit()
    rename, delete = (ui.nodes[r[0]].rect()[1] for r in more.rows)
    assert ui.nodes[item].rect()[1] - delete == delete - rename and ui.nodes[item].rect()[3] == more.pitch
    assert ui.nodes[label].text == 'Edit Playlist' and ui.nodes[icon].image == 'rename'
    assert ui.handlers(button, EVT_CLICK) == [ui.fn('ple_open')]


def test_mores_delete_says_playlist_and_keeps_its_translation_key(fw, ui, st):
    use_set(st, MINIMAL)
    more = More(fw, ui, pitch(st))
    more.more(more.win, 1)
    label = ui.nodes[more.rows[1][2]]
    assert label.text == 'Delete Playlist' and label.tr and ui.nodes[more.rows[0][2]].text == 'Rename'


def test_edit_playlist_from_more_opens_the_editor_on_that_playlist(fw, ui, st, recs):
    use_set(st, MINIMAL)
    editor = Editor(fw, ui, recs)
    more = More(fw, ui, pitch(st))
    more.more(more.win, 1)
    ui.click(more.edit()[1])
    assert ui.nodes[ui.top()].name == 'playlistedit_page' and editor.rows()[2:] == ['One', 'Two', 'Three']


def test_classic_more_rows_lose_their_icons_and_line_up_at_the_text_column(fw, ui, st):
    geometry = use_set(st, CLASSIC)
    more = More(fw, ui, pitch(st))
    more.more(more.win, 1)
    item, button, icon, label = more.edit()
    rows = [(r[1], r[2]) for r in more.rows] + [(icon, label)]
    assert not any(ui.nodes[i].visible for i, _ in rows)
    assert {ui.nodes[lb].rect()[0] for _, lb in rows} == {geometry.text_x}
    assert all(ui.nodes[ui.nodes[lb].parent].rect()[2] == 375 for _, lb in rows)  # each button the row's width
    chevrons = [[k for k in ui.kids(r) if ui.nodes[k].kind == 'image'] for r in (more.rows[0][0], more.rows[1][0], item)]
    assert [len(c) for c in chevrons] == [1, 0, 1] and not any(ui.nodes[c[0]].visible for c in chevrons if c)
    assert ui.nodes[item].rect()[1] == 2 * more.pitch  # no toolbar above the rows


def test_more_of_no_page_is_stocks(fw, ui):
    fw.mock('stock_playlistsmore_trampoline', lambda *_: 7)
    assert fw.fn('ringnav_playlistsmore', C.c_int, C.c_void_p, C.c_void_p)(None, 1) == 7 and not ui.nodes[ui.wm].children


# Classic's Playlists (pl_*, ringnav_playlist).
class Playlists:
    """Stock's playlist_page as its init and every rebuild (stock_playlist_trampoline) leave it: the toolbar
    (view_navbar), its title and the buttons bound to stock's handlers; the list (scroll_view): Import and Export's
    tile row (none in the picker), then a row a playlist, its button bound to PLAYLIST_PICK holding the art, view_info
    (name and "N songs"), its dots img_more<i> and select mode's mark img_choice."""
    def __init__(self, fw, ui, recs, counts=(12, 1, 0), add=0):
        self.fw, self.ui, self.counts = fw, ui, counts
        self.deques = Deques(fw)
        self.names = ['Gym', 'Road Trip', 'Sleep'][:len(counts)]
        self.headers = [recs(name=n, title=n, disc=i + 1, id_=i + 1) for i, n in enumerate(self.names)]
        fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = self.deques.new(self.headers)
        self.marked: set = set()
        fw.mock('batch_get_selectitem', lambda i, *_: int(C.c_int(i).value in self.marked))
        C.c_int.from_address(PLAYLIST_ADD_MODE).value = add
        self.win = ui.window('playlist_page')
        nav = ui.child(self.win, 'view', 0, 0, 375, 30)
        ui.nodes[nav].name = 'view_navbar'
        ui.nodes[ui.child(nav, 'label')].text = 'Gym'  # stock writes the last list opened
        for action in (PL_SELECT_ALL, PL_NEW, PL_EDIT, PL_DELETE, PL_DONE):
            ui.bind(ui.child(nav, 'button'), EVT_CLICK, PL_STOCK[action], self.win)
        lv = ui.child(self.win, 'list_view', 0, 30, 375, 290)
        self.view = ui.child(lv, 'scroll_view', 0, 0, 375, 290)
        ui.nodes[self.view].name = 'scroll_view'
        C.c_ubyte.from_address(self.view + O['VIEW_VERTICAL']).value = 1
        self.ring = fw.fn('ringnav_playlist', C.c_int, C.c_void_p)
        fw.mock('stock_playlist_trampoline', lambda win, *_: self.build())

    def build(self):
        ui = self.ui
        ui.nodes[self.view].children.clear()
        if not C.c_int.from_address(PLAYLIST_ADD_MODE).value:
            tiles = ui.child(self.view, 'list_item', 0, 0, 375, 72)
            for action in (PL_IMPORT, PL_EXPORT):
                ui.bind(ui.child(tiles, 'button'), EVT_CLICK, PL_STOCK[action], self.win)
        marking = self.fw.var('g_navbar_status', C.c_ubyte).value == 1
        for i, (name, count) in enumerate(zip(self.names, self.counts)):
            item = ui.child(self.view, 'list_item', 0, 72 * (i + 1), 375, 72)
            button = ui.child(item, 'button', 0, 0, 375, 72)
            ui.bind(button, EVT_CLICK, PLAYLIST_PICK, button)
            ui.child(button, 'image', 10, 0, 52, 52)  # the art
            info = ui.child(button, 'view', 72, 0, 200, 72)
            ui.nodes[info].name = 'view_info'
            ui.nodes[ui.child(info, 'hscroll_label')].text = name
            ui.nodes[ui.child(info, 'hscroll_label')].text = f'{count} songs'
            dots = ui.child(button, 'image', 286, 0, 89, 72)
            ui.nodes[dots].name, ui.nodes[dots].image = f'img_more{i}', 'more'
            mark = ui.child(button, 'image', 330, 0, 40, 72)
            ui.nodes[mark].name, ui.nodes[mark].image, ui.nodes[mark].visible = 'img_choice', 'mulselect', int(marking)
        return 0x31

    def shown(self):
        """The list's rows as they show: (caption, second line) of each visible row."""
        rows = []
        for item in self.ui.kids(self.view):
            if not self.ui.nodes[item].visible:
                continue
            info = self.ui.lookup(item, 'view_info', 1)
            rows.append(tuple(self.ui.nodes[k].text for k in self.ui.kids(info)) if info else (self.ui.label(item),))
        return rows

    def playlist(self, i):
        """Playlist i's row button."""
        return [self.ui.kids(item)[0] for item in self.ui.kids(self.view) if self.ui.lookup(item, 'view_info', 1)][i]

    def action(self, caption):
        return self.ui.kids(self.ui.find(self.view, caption))[0]

    def title(self):
        return self.ui.nodes[self.ui.kids(self.ui.lookup(self.win, 'view_navbar', 1))[0]].text


@pytest.fixture
def classic(fw, ui, recs, st, stock_globals):
    use_set(st, CLASSIC)
    return lambda **kw: Playlists(fw, ui, recs, **kw)


def test_classic_playlists_rows_are_new_edit_then_the_playlists_with_their_counts(classic, ui):
    p = classic()
    assert p.ring(p.win) == 0x31
    assert p.shown() == [('New Playlist…',), ('Select Playlists…',), ('Gym', '12 Songs'), ('Road Trip', '1 Song'),
                         ('Sleep', '0 Songs')]
    assert p.title() == 'Playlists' and not ui.lookup(p.win, '_pl_none', 0)


def test_classic_playlist_rows_lose_their_art_and_keep_their_dots_column(classic, ui):
    p = classic()
    p.ring(p.win)
    button = p.playlist(0)
    art, info, dots, mark = ui.kids(button)
    assert not ui.nodes[art].visible and ui.nodes[dots].visible and ui.nodes[dots].image == 'unselect'
    assert ui.nodes[ui.nodes[button].parent].props['_pl_row'] == 1
    assert ui.nodes[info].rect()[0] + ui.nodes[info].rect()[2] <= PL_MARK_X <= sum(ui.nodes[dots].rect()[::2])


def test_classic_playlists_hide_stocks_tiles_and_bind_their_own_keys(classic, ui):
    p = classic()
    p.ring(p.win)
    assert not ui.nodes[ui.kids(p.view)[0]].visible  # Import and Export's tiles: the rows click them
    assert ui.handlers(p.win, EVT_KEY_UP) == [ui.fn('pl_keyup')] and ui.fn('pl_gone') in ui.handlers(p.win, EVT_DESTROY)
    assert all(ui.handlers(p.playlist(i), EVT_CLICK) == [ui.fn('pl_pick')] for i in range(3))  # one: AWTK runs the newest first


def test_an_empty_list_says_so_and_offers_import(classic, ui):
    p = classic(counts=())
    p.ring(p.win)
    assert p.shown() == [('New Playlist…',), ('Import Playlists',)]
    none = ui.lookup(p.win, '_pl_none', 0)
    assert ui.nodes[none].text == 'No Playlists' and ui.nodes[none].visible


def test_the_picker_offers_new_playlist_and_no_dots(classic, ui):
    p = classic(add=1)
    p.ring(p.win)
    assert p.shown() == [('New Playlist…',), ('Gym', '12 Songs'), ('Road Trip', '1 Song'), ('Sleep', '0 Songs')]
    assert ui.nodes[p.win].props['_pl_mode'] == 1
    assert not any(ui.nodes[ui.lookup(p.playlist(i), f'img_more{i}', 0)].visible for i in range(3))
    assert not ui.handlers(p.win, EVT_KEY_UP)


def test_each_page_keeps_the_add_mode_it_opened_with(classic, ui):
    p = classic(add=1)
    p.ring(p.win)
    C.c_int.from_address(PLAYLIST_ADD_MODE).value = 0  # a picker over it closed
    p.ring(p.win)
    assert C.c_int.from_address(PLAYLIST_ADD_MODE).value == 1 and p.shown()[0] == ('New Playlist…',)


@pytest.mark.parametrize('left', [1, 2])
def test_a_covers_centre_opens_its_playlist_whatever_mode_a_picker_left(classic, ui, fw, left):
    """Cover Flow's Centre (playlist_open) runs stock's row click, which reads its one add mode global: an Add to
    Playlist picker over the covers leaves it at 1 (a song's hold) or 2 (Now Playing's), which would add to the
    playlist; the page's own mode, the list's 0, goes back first."""
    p = classic()
    p.ring(p.win)
    for i in range(3):
        ui.nodes[p.playlist(i)].name = str(i)  # stock names a row by its place in the live list
    C.c_int.from_address(PLAYLIST_ADD_MODE).value = left
    clicks = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: clicks.append((w, C.c_int.from_address(PLAYLIST_ADD_MODE).value)) or 0)
    fw.fn('playlist_open', None, C.c_void_p, C.c_int)(p.win, 2)  # Road Trip's cd_num
    assert clicks == [(p.playlist(1), 0)]


def test_a_row_click_runs_stocks_handler_next_pass(classic, ui):
    p = classic()
    p.ring(p.win)
    ui.click(p.action('Select Playlists…'))
    assert not ui.dispatched
    ui.due('pl_due')
    target, kind = ui.dispatched[0]
    assert kind == EVT_CLICK and ui.handlers(target, EVT_CLICK) == [PL_STOCK[PL_EDIT]]


def test_the_last_row_clicked_in_a_pass_runs(classic, ui):
    p = classic()
    p.ring(p.win)
    ui.click(p.action('Select Playlists…'))
    ui.click(p.action('New Playlist…'))
    ui.due()
    assert [ui.handlers(t, EVT_CLICK) for t, _ in ui.dispatched] == [[PL_STOCK[PL_NEW]]]


def test_a_row_run_after_the_page_went_does_nothing(classic, ui):
    p = classic()
    p.ring(p.win)
    ui.click(p.action('New Playlist…'))
    ui.window('home_page')
    ui.due()
    assert not ui.dispatched


def select_mode(p, ui, fw):
    """Select Playlists… as stock's handler takes it: g_navbar_status 1, the rows rebuilt with their marks."""
    def edit(w, e, *_):
        fw.var('g_navbar_status', C.c_ubyte).value = 1
        p.ring(p.win)
    fw.mock('stock_dispatch_trampoline', edit)
    ui.click(p.action('Select Playlists…'))
    ui.due('pl_due')


def test_edit_playlists_shows_select_modes_rows(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    select_mode(p, ui, fw)
    assert p.shown() == [('Select All',), ('Gym', '12 Songs'), ('Road Trip', '1 Song'), ('Sleep', '0 Songs'),
                         ('Import Playlists',), ('Done',)]
    assert p.title() == 'Select Playlists' and ui.nodes[p.win].props['_pl_marks'] == 1
    assert ui.nodes[p.view].props['_ringnav_index'] == 1  # Select All's first playlist


def recount(p, ui):
    """A playlist row's click: stock's pick (a stub: the marks are p.marked), then the rows count them."""
    stock_handler(PLAYLIST_PICK, 0)
    ui.click(p.playlist(0), 0)


@in_fork
def test_marks_offer_export_and_delete_with_their_count(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    select_mode(p, ui, fw)
    p.marked |= {0, 2}
    recount(p, ui)
    assert p.shown()[-3:] == [('Export 2 Playlists',), ('Delete 2 Playlists…',), ('Done',)]
    assert p.title() == '2 Selected'
    p.marked = {1}
    recount(p, ui)
    assert p.shown()[-3:] == [('Export 1 Playlist',), ('Delete 1 Playlist…',), ('Done',)] and p.title() == '1 Selected'


@in_fork
def test_select_all_reads_deselect_all_once_all_are(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    select_mode(p, ui, fw)
    C.c_ubyte.from_address(G_ALLSELECT_FLAG).value = 1
    recount(p, ui)
    assert p.shown()[0] == ('Deselect All',)


def test_done_leaves_select_mode_for_the_list_on_edit(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    select_mode(p, ui, fw)
    fw.var('g_navbar_status', C.c_ubyte).value = 0  # stock's Done
    p.ring(p.win)
    assert p.shown()[:2] == [('New Playlist…',), ('Select Playlists…',)] and ui.nodes[p.win].props['_pl_marks'] == 0
    assert p.title() == 'Playlists'


@in_fork
def test_a_mark_outside_select_mode_changes_nothing(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    rows = p.shown()
    p.marked = {0}
    recount(p, ui)
    assert p.shown() == rows


@pytest.mark.parametrize('failing', ['select id from playlist', 'select count(*)'])
def test_a_new_playlist_whose_lookup_fails_is_not_made(fw, failing):
    seen = []
    fw.mock('sqlite3_exec', lambda db, sql, cb, ctx, *_: seen.append(cstr(sql)) or int(cstr(sql).startswith(failing)))
    assert fw.fn('ringnav_playlist_create', C.c_int, C.c_void_p, C.c_char_p)(0x10, b'Mine') == -1
    assert seen[-1] == 'rollback' and not any(q.startswith('insert') for q in seen)


def test_return_in_select_mode_is_done(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    select_mode(p, ui, fw)
    seen = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: seen.append(ui.handlers(w, EVT_CLICK)))
    assert ui.key(p.win, KEY_RETURN) == STOP
    ui.due('pl_due')
    assert seen == [[PL_STOCK[PL_DONE]]]


def test_import_in_select_mode_clicks_stocks_tile(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    select_mode(p, ui, fw)
    seen = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: seen.append(ui.handlers(w, EVT_CLICK)))
    ui.click(p.action('Import Playlists'))
    ui.due('pl_due')
    assert seen == [[PL_STOCK[PL_IMPORT]]]


def test_leaving_the_page_in_select_mode_leaves_select_mode(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    select_mode(p, ui, fw)
    ui.close(p.win)
    assert fw.var('g_navbar_status', C.c_ubyte).value == 0


def test_leaving_the_page_otherwise_leaves_stocks_mode_alone(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    fw.var('g_navbar_status', C.c_ubyte).value = 2
    ui.close(p.win)
    assert fw.var('g_navbar_status', C.c_ubyte).value == 2


@in_fork
def test_return_outside_select_mode_is_stocks(classic, ui):
    p = classic()
    p.ring(p.win)
    log = stock_handler(PLAYLIST_KEYUP, 9)
    assert ui.key(p.win, KEY_RETURN) == 9 and log[0] == p.win and not ui.timers


@in_fork
@pytest.mark.parametrize('add, status, x, passed', [
    (0, 0, PL_MARK_X, PL_MARK_X),       # the dots' column: More, as stock's
    (0, 0, PL_MARK_X - 1, 0),           # left of it: the playlist opens
    (0, 1, 100, 100),                   # select mode: stock's mark, where it was
    (1, 0, PL_MARK_X + 10, 0),          # the picker has no More: a pick
])
def test_a_playlist_rows_tap_is_more_only_on_its_dots(classic, ui, fw, add, status, x, passed):
    p = classic(add=add)
    p.ring(p.win)
    fw.var('g_navbar_status', C.c_ubyte).value = status
    log = stock_handler(PLAYLIST_PICK, 0)
    button = p.playlist(0)
    assert ui.handlers(button, EVT_CLICK)[0] == ui.fn('pl_pick')
    ui.click(button, x)
    assert log[0] == button and log[2] == passed


@in_fork
def test_the_lit_rows_tap_opens_even_on_the_dots(classic, ui, st):
    p = classic()
    p.ring(p.win)
    log = stock_handler(PLAYLIST_PICK, 0)
    button = p.playlist(0)
    st('sel_accent', C.c_void_p).value = ui.nodes[button].parent
    ui.click(button, PL_MARK_X + 5)
    assert log[2] == 0


class Menu(C.Structure):
    _fields_ = [('w', C.c_void_p), ('at', C.c_void_p * 512), ('id', C.c_int * 512), *((k, C.c_int) for k in
                'n kind rows row height ctx cols'.split()), ('scope', C.c_uint)]


def menu(fw, rows):
    """The C's own menu (g_menu, which fw.var checks the mirror against), holding rows."""
    m = fw.var('g_menu', Menu)
    m.n = len(rows)
    for i, b in enumerate(rows):
        m.at[i], m.id[i] = b, i
    return m


def test_a_fresh_classic_list_starts_on_its_first_playlist(classic, ui, fw):
    p = classic()
    p.ring(p.win)
    m = menu(fw, [ui.kids(item)[0] for item in ui.kids(p.view) if ui.nodes[item].visible])
    first = fw.fn('pl_first', C.c_int, C.POINTER(Menu), C.c_int)
    assert first(m, 0) == 2 and first(m, 3) == 3  # below New and Edit; a playlist stays


def test_a_list_with_no_actions_first_starts_where_it_is(fw, ui):
    m = menu(fw, [ui.child(ui.child(0, 'list_item'), 'button') for _ in range(2)])
    assert fw.fn('pl_first', C.c_int, C.POINTER(Menu), C.c_int)(m, 0) == 0


# Go to Album and Go to Artist (qm_goto), and the classinfo they borrow.
def classinfo(fw):
    return (C.c_char * 912).from_address(C.addressof(fw.var('fw_ctx_g_local_classinfo_save', C.c_char)))


def test_go_to_album_opens_its_songs_and_puts_the_browsing_back_after(fw, ui, st, recs):
    info = classinfo(fw)
    struct.pack_into('<I', info, 0, 0xf003)
    info[0xd:0xd + 4] = b'Old\0'
    before = bytes(info)
    ui.routes['playerjumpinfo_page'] = lambda ctx: ui.window('playerjumpinfo_page')
    r = recs(album='Abbey Road', path='/mnt/mmc/a.flac', id_=4)
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(r, 1)
    assert struct.unpack_from('<I', info)[0] == 0xff10 and info[9] == b'\0'
    assert C.string_at(C.addressof(info) + 0xd) == b'Abbey Road' and ui.went == ['playerjumpinfo_page']
    page = ui.top()
    assert ui.handlers(page, EVT_WINDOW_CLOSE) == [ui.fn('qm_restore')]
    ui.fire(page, EVT_WINDOW_CLOSE)
    assert bytes(info) == before


def test_go_to_album_writes_the_artist_too(fw, ui, st, recs):
    r = recs(artist='The Beatles', path='/mnt/mmc/a.flac')
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(r, 1)
    assert C.string_at(C.addressof(classinfo(fw)) + 0x10d) == b'The Beatles'


def test_go_to_artist_opens_artistinfo_with_the_class_and_record(fw, ui, st, recs):
    seen = []
    ui.routes['localmusic/artistinfo_page'] = lambda ctx: seen.append((C.c_int.from_address(ctx).value,
                                                                         C.c_void_p.from_address(ctx + 8).value)) or ui.window('artistinfo_page')
    st('qm_cls', C.c_uint).value = 0xf004
    r = recs(artist='Nina Simone', path='/mnt/mmc/n.flac')
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(r, 0)
    assert seen == [(0xf004, r)] and ui.handlers(ui.top(), EVT_WINDOW_CLOSE) == [ui.fn('qm_restore')]


def test_a_go_to_that_opened_nothing_keeps_no_restore(fw, ui, st, recs):
    under = ui.window('playing_page')
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(recs(album='A'), 1)
    assert ui.top() == under and not ui.handlers(under, EVT_WINDOW_CLOSE)


# The queue menu: a Play/Pause hold (ringnav_keylong, qm_hold), its dialog (qm_open), a row's run (qm_run).
class Card(Files):
    """Files, appends too ("ab" keeps what the file held), with access() and fputs."""
    def __init__(self, fw, files=None):
        super().__init__(fw, files)
        fw.mock('fw_access', lambda path, *_: 0 if cstr(path) in self.files else -1)
        fw.mock('fw_fputs', lambda s, f, *_: self.fwrite(s, 1, len(C.string_at(s)), f) and 0)

    def fopen(self, path, mode, *_):
        handle = super().fopen(path, mode)
        if handle and 'a' in cstr(mode):
            self.open[handle][2] = self.files.get(cstr(path), b'')
        return handle

    def fseek(self, f, offset, whence, *_):
        state = self.open[f]
        if whence == 2 and state[2] is not None:  # an append's end is what it holds, a new file's too
            state[1] = C.c_int(offset).value + len(state[2])
            return 0
        return super().fseek(f, offset, whence)


class Hold:
    """A Play/Pause hold from ringnav_keylong to the menu's rows: Play held in stock's input status, the hold's
    timer run as the next pass, the sortselect dialog stock opens for it (its navbar's title and Return, its list
    of stock's sort rows, which the menu replaces)."""
    def __init__(self, fw, ui, st, recs, mcl):
        self.fw, self.ui, self.st, self.recs, self.mcl = fw, ui, st, recs, mcl
        self.deques, self.dialog, self.folders = Deques(fw), 0, set()
        self.card = Card(fw)
        C.c_uint64.from_address(O['MCL_POOL']).value = self.deques.new()
        ui.routes['dialog/sortselect_dialog'] = lambda ctx: self.open_dialog()
        ui.press(KEY_PLAY, 0x51)
        fw.mock('fw___xstat', lambda v, path, info, *_: setattr(C.c_uint.from_address(info + 20), 'value',
                                                               0o040755 if cstr(path) in self.folders else 0o100644))
        self.keylong = fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)

    def open_dialog(self):
        ui = self.ui
        d = ui.window('sortselect_dialog', 'dialog')
        nav = ui.child(d, 'view', 0, 0, 375, 44)
        ui.nodes[nav].name = 'view_navbar'
        ui.nodes[ui.child(nav, 'image', 0, 0, 44, 44)].name = 'img_return'
        ui.nodes[ui.child(nav, 'hscroll_label', 44, 0, 287, 44)].name = 'scrlabel_title'
        lv = ui.child(d, 'list_view', 0, 44, 375, 210)
        ui.nodes[lv].name = 'list_view'
        view = ui.child(lv, 'scroll_view', 0, 0, 375, 210)
        ui.nodes[view].name = 'scroll_view'
        for caption in ('Title', 'Artist', 'Album'):
            ui.nodes[ui.child(ui.child(view, 'list_item'), 'hscroll_label')].text = caption
        self.dialog = d

    def now_playing(self, songs, at=1, held=False):
        """Now Playing over a queue of songs, playing its at'th. Held, the queue holds its own copies of them as
        libcstl's does (Rows), for Play Next, which turns rows into place by their bytes (playback_queue_insert)."""
        if held:
            rows = Rows(self.fw)
            self.deques = rows
            C.c_uint64.from_address(O['MCL_POOL']).value = rows.new()
            self.queue = rows.rows(songs)
        else:
            self.queue = self.deques.new(songs)
        self.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        self.mcl.type, self.mcl.pos = 1, at
        self.page = self.ui.window('playing_page')
        return self

    def songs_page(self, name, records, sel=0, cls=0xf001, showlist=True):
        """A page of rows, one a record of the showlist, the selection on sel as ringnav_select left it; g_class_type
        cls."""
        ui = self.ui
        self.page = ui.window(name)
        self.list = ui.child(self.page, 'scroll_view', 0, 30, 375, 290)
        C.c_ubyte.from_address(self.list + O['VIEW_VERTICAL']).value = 1
        for i, r in enumerate(records):
            item = ui.child(self.list, 'list_item', 0, 72 * i, 375, 72)
            button = ui.child(item, 'button', 0, 0, 375, 72)
            ui.bind(button, EVT_CLICK, 0x4b0000, i)  # stock's row click
            ui.nodes[ui.child(button, 'hscroll_label')].text = f'row {i}'
        if showlist:
            self.fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = self.deques.new(records)
        self.fw.var('fw_ctx_g_class_type', C.c_uint).value = cls
        self.fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(self.list, sel, len(records))
        return self

    def hold(self, key=KEY_PLAY):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, key)
        return self.keylong(None, event)

    def view(self):
        return self.ui.lookup(self.dialog, 'scroll_view', 1)

    def menu(self):
        """The menu, once the hold's pass ran: its rows, None when none opened."""
        self.ui.due('qm_open')
        return self.ui.rows(self.view()) if self.dialog in self.ui.stack else None

    def title(self):
        return self.ui.nodes[self.ui.lookup(self.dialog, 'scrlabel_title', 1)].text

    def pick(self, caption):
        self.ui.click(self.ui.find(self.view(), caption))
        self.ui.due('qm_run')


@pytest.fixture
def hold(fw, ui, st, recs, mcl):
    use_set(st, MINIMAL)
    return Hold(fw, ui, st, recs, mcl)


def song(recs, n, **kw):
    return recs(path=f'/mnt/mmc/Music/{n}.flac', title=f'Song {n}', album=kw.pop('album', 'Abbey Road'),
                artist=kw.pop('artist', 'The Beatles'), **kw)


PLAYING_ROWS = ['Play Next', 'Add to Queue', 'View Queue', 'Add to Favourites', 'Add to Playlist', 'Go to Album',
                'Go to Artist', 'Shuffle', 'Repeat', 'Group by: Album']


def test_holding_play_on_now_playing_opens_its_menu(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    assert hold.hold() == STOP and ui.pending() == [ui.fn('qm_open')] and not hold.fw.calls('stock_keylong_trampoline')
    assert hold.menu() == PLAYING_ROWS and hold.title() == 'Song 1'
    assert ui.nodes[ui.lookup(hold.dialog, 'scrlabel_title', 1)].props['loop'] == 1  # it loops, as Now Playing's
    assert st('qm_press', C.c_ulonglong).value == 0x51


def test_now_playings_menu_offers_its_folder_rating_and_more(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.folders.add('/mnt/mmc/Music')
    st('rate_win', C.c_void_p).value = hold.page
    st('np_win', C.c_void_p).value = hold.page
    ui.nodes[ui.child(hold.page, 'image')].name = 'img_more'
    hold.hold()
    assert hold.menu() == PLAYING_ROWS[:7] + ['Go to Folder'] + PLAYING_ROWS[7:] + ['Rate', 'More…']


def test_a_favourite_reads_remove_and_its_rating_full(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    st('rate_win', C.c_void_p).value = hold.page
    hold.fw.mock('checkFavExist', lambda *_: 1)
    hold.hold()
    rows = hold.menu()
    assert 'Remove from Favourites' in rows and 'Add to Favourites' not in rows
    assert rows[-1] == 'Rate  ' + '♥' * 5


def test_go_to_is_left_out_where_its_page_is_open(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.window('artistinfo_page')
    ui.window('playerjumpinfo_page')
    hold.page = ui.window('playing_page')
    hold.hold()
    rows = hold.menu()
    assert 'Go to Album' not in rows and 'Go to Artist' not in rows


def test_an_untagged_song_has_no_go_to(hold, recs):
    hold.now_playing([recs(path=f'/mnt/mmc/{i}.flac', name=f'{i}.flac') for i in range(3)])
    hold.hold()
    rows = hold.menu()
    assert 'Go to Album' not in rows and 'Go to Artist' not in rows and hold.title() == '1'  # the name less its type


def test_now_playing_offers_a_books_chapters(hold, recs, ui):
    hold.now_playing([recs(path=f'/mnt/mmc/Music/{i}.m4b', title=f'Book {i}') for i in range(3)])
    hold.hold()
    assert hold.menu()[:3] == ['Play Next', 'Add to Queue', 'Chapters']


def test_return_closes_the_menu_and_other_keys_dont(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    assert ui.key(hold.dialog, KEY_PLAY) == 0 and hold.dialog in ui.stack
    assert ui.key(hold.dialog, KEY_RETURN) == STOP and hold.dialog not in ui.stack and not st('qm_dialog', C.c_void_p).value


def test_the_title_bars_return_closes_it(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    ui.click(ui.lookup(hold.dialog, 'img_return', 1))
    assert hold.dialog not in ui.stack


def test_add_to_favourites_tags_the_playing_song_alone(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('Add to Favourites')
    assert hold.dialog not in ui.stack
    assert [a[0] for _, a in hold.fw.calls('batch_init_selectrecord')] == [3]
    assert [a[0] for _, a in hold.fw.calls('batch_set_selectitem')] == [1]
    (cls, kind, songs, *_), = [a for _, a in hold.fw.calls('batch_add_file')]
    assert (cls, kind, songs & 0xffffffff) == (0xf001, 0xf00a, hold.queue)


def test_remove_from_favourites_untags_the_song(hold, recs, ui, fw):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs)
    fw.mock('checkFavExist', lambda *_: 1)
    hold.hold()
    hold.menu()
    hold.pick('Remove from Favourites')
    assert [a[0] for _, a in fw.calls('deleteMusicFromFav')] == [songs[1]] and fw.var('g_delete_flag', C.c_ubyte).value == 0


@pytest.mark.parametrize('row, state', [('Add to Favourites', b'1'), ('Remove from Favourites', b'0')])
def test_a_favourite_from_the_menu_is_told_to_the_song_s_card_app(hold, recs, fw, row, state):
    """A song in a card app's favdir (q2-jellyfin's synced Jellyfin): the row's My Fav change goes to its fav.log."""
    hold.now_playing([recs(path=f'/mnt/mmc/Jellyfin/{i}.flac', title=f'Song {i}') for i in range(3)])
    fw.mock('checkFavExist', lambda *_: row.startswith('Remove'))
    favdir_card(fw, hold.card.files)
    hold.hold()
    hold.menu()
    hold.pick(row)
    assert hold.card.files['/mnt/mmc/.jf/fav.log'] == b'\npath\t' + state + b'\t/mnt/mmc/Jellyfin/1.flac\n'


def test_view_queue_opens_stocks_queue_in_order(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('View Queue')
    assert ui.went[-1] == 'playerqueue_page'


def test_add_to_queue_appends_the_song(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs)
    hold.hold()
    hold.menu()
    hold.pick('Add to Queue')
    assert hold.deques[hold.queue] == songs + [songs[1]] and not ui.toasts


def test_play_next_puts_the_song_after_the_playing_one(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs, at=0, held=True)
    hold.hold()
    hold.menu()
    hold.pick('Play Next')
    want = [songs[0], songs[0], songs[1], songs[2]]
    assert [field(r, REC_PATH) for r in hold.deques[hold.queue]] == [field(r, REC_PATH) for r in want]


def test_a_queue_changed_under_the_menu_is_left_alone(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs)
    hold.hold()
    hold.menu()
    hold.deques[hold.queue].append(song(recs, 9))
    hold.pick('Add to Queue')
    assert ui.toasts == ['Queue unchanged'] and len(hold.deques[hold.queue]) == 4


def test_go_to_folder_opens_the_songs_folder(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.folders.add('/mnt/mmc/Music')
    seen = []
    ui.routes['folder_page'] = lambda ctx: seen.append((st('go_layer').value, C.string_at(C.addressof(st('go_folder', C.c_char)))))
    hold.hold()
    hold.menu()
    hold.pick('Go to Folder')
    assert seen == [(3, b'/mnt/mmc/Music')] and st('go_layer').value == 0  # never left for a later visit


def test_a_folder_gone_since_says_so(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.folders.add('/mnt/mmc/Music')
    hold.hold()
    hold.menu()
    hold.folders.clear()
    hold.pick('Go to Folder')
    assert ui.toasts == ['Folder not found'] and 'folder_page' not in ui.went


def test_go_to_album_from_the_menu(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('Go to Album')
    assert ui.went[-1] == 'playerjumpinfo_page'
    assert C.string_at(C.addressof(classinfo(hold.fw)) + 0xd) == b'Abbey Road'


def test_more_clicks_now_playings_own_more(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    st('np_win', C.c_void_p).value = hold.page
    more = ui.child(hold.page, 'image')
    ui.nodes[more].name = 'img_more'
    hold.hold()
    hold.menu()
    hold.pick('More…')
    assert ui.dispatched == [(more, EVT_CLICK)]


def test_more_opens_the_apps_page_while_its_queue_plays(hold, recs, ui, st):
    # addon.c addon_more: an app's q2app said more=, and the queue playing is the one its .play started.
    from test_addon import Ap, Qa
    hold.now_playing([song(recs, i) for i in range(3)])
    st('np_win', C.c_void_p).value = hold.page
    ui.nodes[ui.child(hold.page, 'image')].name = 'img_more'
    ap = hold.fw.var('addon__ap', Ap)
    ap.n, a = 1, ap.a[0]
    a.id, a.name, a.more, a.bin, a.pid, a.launched = b'foo', b'Foo', b'playlists', b'/mnt/mmc/.foo/q2-foo', b'/tmp/q2-foo', 1
    hold.card.files.update({'/mnt/mmc/.foo/q2-foo': b'', '/tmp/q2-foo': b'1'})
    qa = hold.fw.var('addon__qa', Qa)
    qa.app, qa.loads = C.addressof(a), hold.fw.var('playback_loads', C.c_uint).value
    hold.hold()
    hold.menu()
    hold.pick('More…')
    assert ui.dispatched == [] and ui.nodes[ui.top()].name == 'addon_page'


def test_now_playings_rate_opens_the_picker_over_it(hold, recs, ui, st, rates):
    hold.now_playing([song(recs, i) for i in range(3)])
    st('rate_win', C.c_void_p).value = hold.page
    hold.hold()
    hold.menu()
    hold.pick('Rate')
    assert st('rate_on', C.c_void_p).value == hold.page and rates('rate_album', C.c_uint).value == 0


def advanced(fw, kind, value):
    """Playback's own Shuffle, Repeat or Group by, as its menu sets them."""
    return fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(kind, value)


def test_shuffle_albums_puts_skipping_the_album_first(hold, recs, ui, fw):
    hold.now_playing([song(recs, i, album=a) for i, a in enumerate(('A', 'A', 'B'))])
    assert advanced(fw, 0, 3)
    hold.hold()
    assert hold.menu()[:2] == ['Next Album', 'Previous Album']


def test_a_skip_with_no_other_album_says_so(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    advanced(fw, 0, 3)
    hold.hold()
    hold.menu()
    hold.pick('Next Album')
    assert ui.toasts == ['No other group']


def test_view_queue_under_advanced_shuffle_is_up_next(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    advanced(fw, 0, 1)
    hold.hold()
    hold.menu()
    hold.pick('View Queue')
    assert ui.nodes[ui.top()].name == 'upnext_page' and 'playerqueue_page' not in ui.went


def up_next_rows(ui):
    """Up Next's rows, its page the top window: each a track's title over its artist."""
    lv, = [k for k in ui.kids(ui.top()) if ui.nodes[k].kind == 'list_view']
    return ui.rows(ui.kids(lv)[0])


@pytest.mark.parametrize('seed', [1, 0x9e3779b9, 0x2545f491])  # whatever order the shuffle draws
def test_a_play_next_stocks_shuffle_holds_goes_first_in_up_next(hold, recs, ui, fw, mcl, seed):
    hold.now_playing([song(recs, i) for i in range(8)], at=0, held=True)
    mcl.mode = 2  # stock's Shuffle
    fw.var('playback__rng', C.c_uint).value = seed
    hold.hold()
    hold.menu()
    hold.pick('Play Next')  # Song 0, playing, once more after itself
    hold.hold()
    hold.menu()
    hold.pick('View Queue')
    assert ui.nodes[ui.top()].name == 'upnext_page' and up_next_rows(ui)[0] == 'Song 0'


SHUFFLE = ['Off', 'All Songs', 'Songs in Album', 'Albums', 'Albums and Songs']  # stock's play modes, in its order
REPEAT = ['Play One Song', 'Play One Album', 'Play All', 'Repeat Song', 'Repeat Album', 'Repeat All']


def ticked_rows(ui, view):
    return [ui.label(item) for item in ui.kids(view) if any(ui.nodes[k].image == 'select' for k in ui.kids(item))]


@pytest.mark.parametrize('caption, title, rows, ticked', [
    ('Shuffle', 'Shuffle', SHUFFLE, 'Off'),
    ('Repeat', 'Repeat', REPEAT, 'Play All'),
    ('Group by: Album', 'Group by', ['Album', 'Folder'], 'Album'),
])
def test_a_play_mode_row_opens_its_choices_on_the_current_one(hold, recs, ui, caption, title, rows, ticked):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick(caption)
    assert hold.title() == title and ui.rows(hold.view()) == rows
    assert ticked_rows(ui, hold.view()) == [ticked] and ui.nodes[hold.view()].props['_ringnav_index'] == rows.index(ticked)


def test_a_choice_picked_is_played_and_ticked_next_time(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('Repeat')
    hold.pick('Repeat All')  # from Play All
    assert hold.dialog not in ui.stack and not ui.toasts
    assert fw.fn('playback_option', C.c_int, C.c_int)(1) == REPEAT.index('Repeat All')
    hold.hold()
    assert hold.menu() == PLAYING_ROWS  # the next hold is the menu again, not the choice
    hold.pick('Repeat')
    assert ticked_rows(ui, hold.view()) == ['Repeat All']


def test_classic_draws_the_menu_as_the_flip_panel_centred_under_the_bar(fw, ui, st, recs, mcl):
    geometry = use_set(st, CLASSIC)
    hold = Hold(fw, ui, st, recs, mcl).now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    rows = hold.menu()
    nav, lv = ui.lookup(hold.dialog, 'view_navbar', 1), ui.lookup(hold.dialog, 'list_view', 1)
    nx, ny, nw, nh = ui.nodes[nav].rect()
    lx, ly, lw, lh = ui.nodes[lv].rect()
    row = THEMES[st('theme').value]['page_row']
    assert (lx, ly, lw) == (nx, ny + nh, nw) and lh == min(len(rows), 6) * row  # past six rows it scrolls
    assert abs((ny - geometry.bar) - (320 - ly - lh)) <= 1  # as much clear above as below
    assert not ui.nodes[ui.lookup(nav, 'img_return', 1)].visible
    assert all(ui.nodes[item].rect()[3] == row for item in ui.kids(hold.view()))


def test_classic_draws_a_choice_as_the_panel_too(fw, ui, st, recs, mcl):
    use_set(st, CLASSIC)
    hold = Hold(fw, ui, st, recs, mcl).now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('Group by: Album')
    lv = ui.lookup(hold.dialog, 'list_view', 1)
    assert ui.nodes[lv].rect()[3] == 2 * THEMES[st('theme').value]['page_row']


def test_add_to_playlist_opens_the_picker_guarded_by_the_menus_record(hold, recs, ui, st, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    picker = []
    ui.routes['localmusic/playlist_page'] = lambda ctx: picker.append(ctx) or ui.window('playlist_page')
    hold.hold()
    hold.menu()
    hold.pick('Add to Playlist')
    assert picker == [0x20000 | 0xf001] and st('qm_playlist', C.c_void_p).value == ui.top()
    assert ui.handlers(ui.top(), EVT_DESTROY) == [ui.fn('qm_playlist_gone')]
    assert hold.hold() == 0 and not ui.timers  # no menu over the picker: stock's hold
    ui.close(ui.top())
    assert not st('qm_playlist', C.c_void_p).value


def test_the_pickers_rows_are_rebound_to_the_guard_after_each_rebuild(fw, ui, st, recs, mcl, stock_globals):
    use_set(st, CLASSIC)
    p = Playlists(fw, ui, recs, add=1)
    st('qm_playlist', C.c_void_p).value = p.win
    p.ring(p.win)
    assert all(ui.handlers(p.playlist(i), EVT_CLICK) == [ui.fn('qm_playlist_pick')] for i in range(3))


@in_fork
def test_a_pick_in_the_guarded_picker_adds_the_menus_song(hold, recs, ui, st, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.routes['localmusic/playlist_page'] = lambda ctx: ui.window('playlist_page')
    hold.hold()
    hold.menu()
    hold.pick('Add to Playlist')
    row = ui.child(ui.top(), 'button')
    ui.bind(row, EVT_CLICK, ui.fn('qm_playlist_pick'), row)
    log = stock_handler(PLAYLIST_PICK, 0)
    ui.click(row, 200)
    assert log[0] == row and log[2] == 200 and [a[0] for _, a in fw.calls('batch_set_selectitem')][-1] == 1


@in_fork
def test_a_pick_after_the_queue_changed_adds_nothing(hold, recs, ui, st, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.routes['localmusic/playlist_page'] = lambda ctx: ui.window('playlist_page')
    hold.hold()
    hold.menu()
    hold.pick('Add to Playlist')
    row = ui.child(ui.top(), 'button')
    ui.bind(row, EVT_CLICK, ui.fn('qm_playlist_pick'), row)
    log = stock_handler(PLAYLIST_PICK, 0)
    hold.mcl.pos = 2
    assert ui.click(row) == 0 and ui.toasts == ['Queue unchanged'] and log[0] == 0


# The hold on a list's row.
def test_holding_play_on_a_song_row_opens_the_songs_menu(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.songs_page('allmusic_page', songs, sel=2)
    assert hold.hold() == STOP
    assert hold.menu() == ['Play Next', 'Add to Queue', 'Add to Favourites', 'Add to Playlist', 'Go to Album', 'Go to Artist']
    assert hold.title() == 'Song 2'


def test_a_songs_play_next_on_an_empty_queue_loads_it_unplayed(hold, recs, ui, fw, mcl):
    songs = [song(recs, i) for i in range(3)]
    hold.songs_page('allmusic_page', songs, sel=0)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = hold.deques.new()
    mcl.type = 1
    loaded = []
    fw.mock('mclLoadPlayList', lambda q, at, cls, *_: loaded.append((list(hold.deques[q]), at, cls)))
    hold.hold()
    hold.menu()
    hold.pick('Play Next')
    assert loaded == [([songs[0]], 0, 0xf001)] and not fw.calls('mclStartPlayer')


def test_my_favs_remove_reloads_my_fav(hold, recs, ui, fw):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(2)], sel=1, cls=0xf00a)
    fw.mock('checkFavExist', lambda *_: 1)
    hold.hold()
    hold.menu()
    hold.pick('Remove from Favourites')
    assert fw.var('g_delete_flag', C.c_ubyte).value == 1  # My Fav reloads, as Now Playing's heart


def test_a_list_changed_under_the_menu_runs_nothing(hold, recs, ui, fw):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=1)
    hold.hold()
    hold.menu()
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), b'/mnt/mmc/x\0', 11)  # browsing moved on
    hold.pick('Add to Favourites')
    assert not fw.calls('batch_add_file') and ui.toasts == ['Queue unchanged']


def test_an_album_rows_menu_shuffles_rates_and_goes_to_its_artist(hold, recs, ui):
    albums = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('localclass_page', albums, sel=1, cls=0xf003)
    hold.hold()
    assert hold.menu() == ['Play Next', 'Add to Queue', 'Shuffle', 'Add to Favourites', 'Rate Album', 'Add to Playlist',
                           'Go to Artist']
    assert hold.title() == 'Revolver'


def test_an_albums_favourite_is_kept_for_its_next_menu(hold, recs, ui):
    albums = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('localclass_page', albums, sel=1, cls=0xf003)
    hold.hold()
    hold.menu()
    hold.pick('Add to Favourites')
    assert not ui.toasts and '/mnt/data/ringnav-album-ratings' in hold.card.files
    hold.hold()
    rows = hold.menu()
    assert 'Remove from Favourites' in rows and 'Rate Album  ' + '♥' * 5 in rows


def test_rate_album_opens_the_picker_on_its_hearts_and_rates_that_album(hold, recs, ui, st, fw):
    albums = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('localclass_page', albums, sel=0, cls=0xf003)
    hold.hold()
    hold.menu()
    hold.pick('Add to Favourites')  # Help!: five hearts
    hold.hold()
    hold.menu()
    hold.pick('Rate Album  ' + '♥' * 5)
    assert st('rate_on', C.c_void_p).value == hold.page
    key = fw.fn('rate_key', C.c_int, C.c_void_p, C.c_uint)
    assert key(hold.page, O['KEY_PREV']) and key(hold.page, O['KEY_CENTER'])  # a heart less than it opened on, kept
    hold.hold()
    assert 'Rate Album  ' + '♥' * 4 + '♡' in hold.menu()
    ui.key(hold.dialog, KEY_RETURN)
    fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(hold.list, 1, 2)
    hold.hold()
    assert 'Rate Album' in hold.menu() and hold.title() == 'Revolver'  # its own: none


@pytest.mark.parametrize('sel, opened', ((1, [2]), (0, [])))  # 2: DISPLAY_COVERFLOW
def test_a_hold_on_homes_cover_flow_row_opens_displays_cover_flow_page(hold, recs, ui, fw, sel, opened):
    hold.songs_page('home_page', [recs(), recs()], sel=sel, showlist=False)
    ui.nodes[ui.kids(ui.kids(hold.list)[1])[0]].name = 'img_cover_flow'
    seen = []
    ui.routes['systemset/display_page'] = lambda ctx: seen.append(fw.var('navigation__display_sub', C.c_int).value)
    assert hold.hold() == (STOP if opened else 0)  # another row's hold stays stock's
    assert seen == opened and hold.menu() is None


@pytest.mark.parametrize('cls, field_', [(0xf004, 'artist'), (0xf005, 'composer'), (0xf006, 'genre')])
def test_an_artist_composer_or_genre_row_is_titled_by_its_own_tag(hold, recs, ui, cls, field_):
    rows = [recs(kind=4, **{field_: f'{field_} {i}'}) for i in range(2)]
    hold.songs_page('localclass_page', rows, sel=1, cls=cls)
    hold.hold()
    assert hold.menu() == ['Play Next', 'Add to Queue', 'Shuffle', 'Add to Playlist'] and hold.title() == f'{field_} 1'


def test_a_folder_rows_menu(hold, recs, ui, fw):
    rows = [recs(name=n, kind=4) for n in ('Jazz', 'Rock')]
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), b'/mnt/mmc/Music\0', 15)
    hold.songs_page('folder_page', rows, sel=0)
    hold.hold()
    assert hold.menu() == ['Play Next', 'Add to Queue', 'Shuffle', 'Add to Playlist'] and hold.title() == 'Jazz'


def test_an_artists_albums_tab_holds_as_albums_from_its_own_class(hold, recs, ui, fw):
    struct.pack_into('<I', classinfo(fw), 0, 0xff01)  # load_localartist_list's: g_class_type stays the Artists list's
    albums = [recs(album=a, kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('artistinfo_page', albums, sel=0, cls=0xf004)
    hold.hold()
    assert hold.menu() == ['Play Next', 'Add to Queue', 'Shuffle', 'Add to Playlist'] and hold.title() == 'Help!'
    hold.pick('Add to Playlist')
    assert ui.contexts[-1] == ('localmusic/playlist_page', 0x10000 | 0xff01)  # stock's add, from the tab's albums


def test_a_playlist_rows_more_opens_its_dots_page(hold, recs, ui, st, fw, stock_globals):
    p = Playlists(fw, ui, recs)
    p.ring(p.win)  # stock's rows as they are: a set with toolbars
    fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(p.view, 3, 5)  # Import, Export, Gym, then Road Trip
    hold.hold()
    assert hold.menu() == ['Play Next', 'Add to Queue', 'Shuffle', 'More…'] and hold.title() == 'Road Trip'
    hold.pick('More…')
    assert ui.contexts[-1] == ('localmusic/playlistsmore_page', 1)


@pytest.mark.parametrize('add, menu', [(0, ['Play Next', 'Add to Queue', 'Shuffle', 'More…']), (1, None)])
def test_the_add_to_playlist_pickers_hold_is_stocks(hold, recs, ui, fw, stock_globals, add, menu):
    """The picker is playlist_page too, its rows a playlist each with its dots over the same playlists: only its
    add mode tells it from the list."""
    p = Playlists(fw, ui, recs, add=add)
    p.ring(p.win)
    rows = len(ui.kids(p.view)) + (not add)  # the list's Import and Export tiles are a row each
    fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(p.view, rows - 2, rows)  # Road Trip
    assert ui.lookup(p.playlist(1), 'img_more1', 0)
    fw.mock('stock_keylong_trampoline', lambda *_: 0x77)
    assert hold.hold() == (0x77 if add else STOP) and hold.menu() == menu


def test_most_playeds_tracks_hold_as_its_songs(hold, recs, ui, st, fw):
    songs = [song(recs, i) for i in range(3)]
    hold.songs_page('mostplayed_page', songs, sel=1, showlist=False, cls=0xf003)  # the Library last listed albums
    st('mp_page', C.c_void_p).value = hold.page
    st('mp_list', C.c_void_p).value = mp = hold.deques.new(songs)
    hold.hold()
    rows = hold.menu()
    assert hold.title() == 'Song 1' and 'Add to Favourites' in rows and 'Add to Playlist' not in rows
    hold.pick('Add to Favourites')
    (cls, kind, tracks, *_), = [a for _, a in fw.calls('batch_add_file')]
    assert (cls, kind, tracks & 0xffffffff) == (0xf001, 0xf00a, mp)  # its own tracks, as songs whatever the Library


# What a hold leaves stock.
@pytest.mark.parametrize('how', ['dialog', 'timer', 'picker', 'dark', 'animating', 'touch', 'airplay', 'select mode',
                                 'not a list', 'stream', 'no key', 'video', 'rows changed'])
def test_a_hold_that_isnt_the_menus_is_stocks(hold, recs, ui, st, fw, mcl, how):
    songs = [song(recs, i) for i in range(3)]
    if how in ('not a list', 'video', 'rows changed'):
        rows = songs if how != 'video' else [recs(path=f'/mnt/mmc/{i}.mp4', kind=8) for i in range(3)]
        hold.songs_page('notes_page' if how == 'not a list' else 'allmusic_page', rows)
        if how == 'rows changed':
            hold.deques[fw.var('fw_ctx_p_deque_showlist', C.c_uint).value].append(songs[0])
    else:
        hold.now_playing(songs)
    {'dialog': lambda: setattr(st('qm_dialog', C.c_void_p), 'value', 1),
     'timer': lambda: setattr(st('qm_timer', C.c_uint), 'value', 5),
     'picker': lambda: setattr(st('qm_playlist', C.c_void_p), 'value', 1),
     'dark': lambda: setattr(fw.var('g_backlight_status', C.c_ubyte), 'value', 0),
     'animating': lambda: fw.mock('window_manager_is_animating', lambda *_: 1),
     'touch': lambda: setattr(ui, 'pressed', 1),
     'airplay': lambda: fw.mock('airplayGetFlag', lambda *_: 2),
     'select mode': lambda: setattr(fw.var('g_navbar_status', C.c_ubyte), 'value', 1),
     'stream': lambda: setattr(mcl, 'type', 0x2000),
     'no key': lambda: ui.press(0x99)}.get(how, lambda: None)()
    fw.mock('stock_keylong_trampoline', lambda *_: 0x77)
    assert hold.hold() == 0x77 and ui.went == [] and not ui.timers


def test_a_hold_on_a_page_with_no_list_is_stocks(hold, ui, fw):
    ui.window('allmusic_page')
    fw.mock('stock_keylong_trampoline', lambda *_: 0x77)
    assert hold.hold() == 0x77 and not ui.timers


def test_classic_swallows_a_hold_with_no_menu(fw, ui, st, recs, mcl):
    use_set(st, CLASSIC)
    hold = Hold(fw, ui, st, recs, mcl)
    ui.window('notes_page')
    assert hold.hold() == STOP and not fw.calls('stock_keylong_trampoline') and st('qm_press', C.c_ulonglong).value == 0x51


def test_another_key_held_is_stocks(hold, recs, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    fw.mock('stock_keylong_trampoline', lambda *_: 0x77)
    assert hold.hold(KEY_RETURN) == 0x77 and fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0x77


def test_the_rating_pickers_hold_does_nothing(hold, recs, ui, st, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    st('rate_on', C.c_void_p).value = hold.page
    assert hold.hold() == STOP and not ui.timers and st('qm_press', C.c_ulonglong).value == 0x51


def test_a_menu_whose_record_went_before_it_opened_opens_nothing(hold, recs, ui, mcl):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    mcl.pos = 0
    assert hold.menu() is None and ui.went == []


def test_a_menu_that_opened_something_else_closes_it(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.routes['dialog/sortselect_dialog'] = lambda ctx: ui.window('msginfo_dialog')
    hold.hold()
    ui.due('qm_open')
    assert [ui.nodes[w].name for w in ui.closed] == ['msginfo_dialog']


def test_a_run_after_the_menu_closed_does_nothing(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    ui.click(ui.find(hold.view(), 'Add to Favourites'))
    ui.key(hold.dialog, KEY_RETURN)
    ui.due('qm_run')
    assert not fw.calls('batch_add_file')


def test_a_second_click_before_the_run_is_dropped(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    ui.click(ui.find(hold.view(), 'Add to Favourites'))
    ui.click(ui.find(hold.view(), 'View Queue'))
    ui.due('qm_run')
    assert [a[1] for _, a in fw.calls('batch_add_file')] == [0xf00a] and 'playerqueue_page' not in ui.went


# Quick Settings' wheel: a held Play/Pause switches it between brightness and volume.
def test_a_hold_in_quick_settings_switches_the_wheel(fw, ui, st):
    ui.window('statusbar_dialog')
    keylong = fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, EVENT_KEY, KEY_PLAY)
    assert keylong(None, event) == STOP and keylong(None, event) == STOP
    assert [(a[0], cstr(a[1]), cstr(a[2])) for _, a in fw.calls('write_int_config')] == [(1, 'Q2POD', 'QSVOLUME'), (0, 'Q2POD', 'QSVOLUME')]
    assert not fw.calls('stock_keylong_trampoline')


def test_a_hold_in_quick_settings_names_the_wheel_in_a_toast(fw, ui):
    ui.window('statusbar_dialog')
    keylong = fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, EVENT_KEY, KEY_PLAY)
    keylong(None, event)
    keylong(None, event)
    assert ui.toasts == ['Wheel: Volume', 'Wheel: Brightness'] and not fw.calls('label_create')


def test_quick_settings_wheel_starts_from_the_saved_choice(fw, ui):
    fw.mock('toolsReadConfig', lambda path, section, key, out, d, *_: C.memmove(out, b'1\0', 2))
    ui.window('statusbar_dialog')
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, EVENT_KEY, KEY_PLAY)
    fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)(None, event)
    assert [a[0] for _, a in fw.calls('write_int_config')] == [0]


# A press whose release never reached the window manager (stale_heal) and a pull-down left open (quick_poll).
def test_a_press_stuck_past_the_limit_is_aborted_and_released_where_it_was(fw, ui, st):
    page = ui.window('allmusic_page')
    now = [100000]
    fw.mock('time_now_ms', lambda *_: now[0])
    seen: list = []
    fw.mock('widget_dispatch_event_to_target_recursive', lambda w, e, *_: seen.append((w, C.c_int.from_address(e).value)))
    fw.mock('stock_input_trampoline', lambda wm, e, *_: seen.append((wm, C.c_int.from_address(e).value, C.c_int.from_address(e + EVENT_X).value,
                                                                     C.c_int.from_address(e + EVENT_Y).value)))
    st('touch_at', C.c_uint).value, st('touch_x').value, st('touch_y').value = 100000 - O['STALE_TOUCH_MS'], 120, 200
    heal = fw.fn('navigation__stale_heal', None)
    heal()
    assert not seen  # nothing pressed
    ui.pressed = 1
    heal()
    assert seen == [(page, EVT_POINTER_ABORT), (ui.wm, EVT_POINTER_UP_BEFORE - 1, 120, 200)]
    heal()
    assert len(seen) == 2 and st('touch_at', C.c_uint).value == 100000  # one try each STALE_TOUCH_MS


def test_a_press_younger_than_the_limit_is_left(fw, ui, st):
    fw.mock('time_now_ms', lambda *_: 100000)
    st('touch_at', C.c_uint).value = 100001 - O['STALE_TOUCH_MS']
    ui.pressed = 1
    fw.fn('navigation__stale_heal', None)()
    assert not fw.calls('stock_input_trampoline')


def test_a_stale_press_is_noted_eight_times_a_boot(fw, ui, st):
    card = Card(fw)
    ui.window('allmusic_page')
    fw.mock('time_now_ms', lambda *_: 61000)
    note = fw.fn('navigation__stale_note', None, C.c_uint)
    for _ in range(9):
        note(5100)
    lines = card.files['/mnt/data/q2-wheel.log'].decode().splitlines()
    assert len(lines) == 8 and lines[0].startswith('== wheel: stale press at 61 s: top=allmusic_page ')
    assert lines[0].endswith(' 5100 ms ago')


def test_a_note_says_whether_the_touch_thread_holds_a_press(fw, ui):
    card = Card(fw)
    loop = C.create_string_buffer(0x80)
    loop[O['LOOP_PRESSED']] = b'\x01'
    fw.mock('main_loop', lambda *_: C.addressof(loop))
    fw.fn('navigation__stale_note', None, C.c_uint)(6000)
    assert ' thread=1 ' in card.files['/mnt/data/q2-wheel.log'].decode()


def test_a_note_with_no_log_writes_nothing(fw, ui):
    fw.mock('fw_fopen', lambda *_: 0)
    fw.fn('navigation__stale_note', None, C.c_uint)(5100)
    assert not fw.calls('fw_fputs')


def quick(fw, ui, state, idex=-1, pressed=0):
    top = ui.window('statusbar_dialog')
    ui.nodes[top].props['timer_idex'] = idex
    fw.var('fw_ctx_g_dropdown_menu_state', C.c_int).value = state
    ui.pressed = pressed
    fw.fn('navigation__quick_poll', None)()
    return top


@pytest.mark.parametrize('state', [1, 2])
def test_a_pull_down_left_with_its_release_is_handed_one(fw, ui, state):
    top = quick(fw, ui, state)
    assert ui.dispatched == [(top, EVT_POINTER_UP_BEFORE - 1)]


@pytest.mark.parametrize('state, idex, pressed', [(0, -1, 0), (3, -1, 0), (1, 4, 0), (1, -1, 1)])
def test_a_pull_down_moving_or_held_is_left(fw, ui, state, idex, pressed):
    quick(fw, ui, state, idex, pressed)
    assert not ui.dispatched


# Resume: a long track's place, kept each second by the UI loop (resume_poll), and a spoken book's.
RESUME_FILE, PLAYS_FILE = '/mnt/data/ringnav-resume', '/mnt/data/ringnav-plays'
EDGE, SAVE_S = O['RESUME_EDGE_S'], O['RESUME_SAVE_S']


def key_of(path, cue=0):
    """listen_key: the path's FNV-1a, a CUE start mixed in, never 0."""
    key = fnv1a(path.encode()) * 16777619 & 0xffffffff
    if cue:
        key = fnv1a(struct.pack('<i', cue), key)
    return key or 1


def ring(data):
    """The saved places, (key, seconds) most recent first, as RESUME_FILE holds them."""
    pairs = struct.iter_unpack('<Ii', data)
    return [p for p in pairs if p[0]]


class Player:
    """The queue and the player's clock as resume_poll reads them, each poll a second after the last."""
    def __init__(self, fw, mcl, deques=None, card=None):
        self.fw, self.mcl = fw, mcl
        self.deques, self.card = deques or Deques(fw), card or Card(fw)
        self.now, self.sec, self.total, self.status, self.seeks = 50000, 0, 0, 2, []
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('player_playtime_and_length', self.times)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('stock_seek_time_trampoline', lambda s, *_: self.seeks.append(C.c_int(s).value))
        fw.mock('fw_time', lambda *_: 1700000000)
        self.poll_fn = fw.fn('navigation__resume_poll', None)
        mcl.type = 1

    def times(self, sec, total, *_):
        if self.total <= 0:
            return -1
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total
        return 0

    def play(self, songs, at=0, total=3600):
        self.queue = self.deques.new(songs)
        self.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        self.mcl.pos, self.total = at, total

    def poll(self, sec=None, step=1000):
        self.now += step
        if sec is not None:
            self.sec = sec
        self.poll_fn()

    def places(self):
        return ring(self.card.files.get(RESUME_FILE, b''))


LONG = '/mnt/mmc/Music/long.flac'


@pytest.fixture
def player(fw, ui, mcl):
    return Player(fw, mcl)


def test_a_long_tracks_place_is_kept_each_minute_of_play(player, recs):
    player.play([recs(path=LONG, title='Long')])
    player.poll(0), player.poll(1)  # the change, then a poll to settle
    for s in range(2, SAVE_S + 1):
        player.poll(s)
    assert player.places() == []  # under a minute's movement
    player.poll(SAVE_S + 1)
    assert player.places() == [(key_of(LONG), SAVE_S + 1)]


def test_a_pause_keeps_the_place_after_two_still_polls(player, recs):
    player.play([recs(path=LONG)])
    player.poll(100), player.poll(101), player.poll(140)
    player.poll(140)
    assert player.places() == []
    player.poll(140)
    assert player.places() == [(key_of(LONG), 140)]


def test_a_track_change_saves_where_it_was_and_coming_back_jumps_there(player, recs):
    long, short = recs(path=LONG), recs(path='/mnt/mmc/Music/short.flac')
    player.play([long, short])
    player.poll(0), player.poll(1)
    player.poll(500)  # a seek: saved
    player.poll(512)
    player.mcl.pos, player.total = 1, 200
    player.poll(0)
    assert player.places() == [(key_of(LONG), 512)]
    player.mcl.pos, player.total = 0, 3600
    player.poll(0), player.poll(0)
    player.poll(0)
    assert player.seeks == []  # not playing yet: a paused resume waits for Play
    player.poll(1)
    assert player.seeks == [512]


def places_file(*pairs, slots=O['RESUME_SLOTS']):
    return b''.join(struct.pack('<Ii', k, s) for k, s in pairs).ljust(8 * slots, b'\0')


@pytest.mark.parametrize('slots', [O['RESUME_SLOTS'], 32])  # 32: earlier releases' ring
def test_a_place_saved_before_a_restart_is_where_the_track_starts(player, recs, slots):
    player.card.files[RESUME_FILE] = places_file((key_of('/x.flac'), 99), (key_of(LONG), 700), slots=slots)
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(0), player.poll(2)
    assert player.seeks == [700]


def test_a_place_near_either_end_is_forgotten(player, recs):
    player.play([recs(path=LONG)], total=3600)
    player.poll(0), player.poll(1), player.poll(600)
    assert player.places() == [(key_of(LONG), 600)]
    player.poll(3600 - EDGE)
    assert player.places() == []


def test_a_place_already_first_writes_nothing(fw, ui, st):
    card = Card(fw)
    keep = fw.fn('navigation__spot_keep', None, C.c_uint, C.c_int, C.c_int)
    keep(7, 100, 3600)
    first = card.files[RESUME_FILE]
    card.files.clear()
    keep(7, 100, 3600)
    keep(8, 10, 3600)  # near the start, and nothing kept for it to forget
    assert card.files == {} and ring(first) == [(7, 100)]


def test_a_full_ring_drops_its_oldest_place(fw, ui, st):
    card = Card(fw)
    keep = fw.fn('navigation__spot_keep', None, C.c_uint, C.c_int, C.c_int)
    for k in range(1, O['RESUME_SLOTS'] + 2):
        keep(k, 100 + k, 3600)
    kept = ring(card.files[RESUME_FILE])
    assert len(kept) == O['RESUME_SLOTS'] and kept[0] == (O['RESUME_SLOTS'] + 1, 101 + O['RESUME_SLOTS']) and (1, 101) not in kept


def test_a_short_track_keeps_no_place(player, recs):
    player.play([recs(path=LONG)], total=29 * 60)
    player.poll(0), player.poll(1), player.poll(600), player.poll(600), player.poll(600)
    assert player.places() == [] and RESUME_FILE not in player.card.files


def test_a_stopped_long_track_started_again_goes_back_to_where_it_stopped(player, recs):
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(400)
    total = player.total
    player.total, player.status = 0, 1  # stopped
    player.poll()
    player.total, player.status = total, 2
    player.poll(1)
    assert player.seeks == [400]


def test_a_confirmed_restart_starts_over(player, recs, fw):
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(400)
    total = player.total
    player.total, player.status = 0, 1
    player.poll()
    fw.fn('resume_restart', None)()
    player.total, player.status = total, 2
    player.poll(1)
    assert player.seeks == []


def test_two_polls_in_a_second_are_one(player, recs):
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(600)
    player.poll(900, step=999)
    assert player.places() == [(key_of(LONG), 600)]


def test_resume_held_says_whether_starting_over_loses_a_place(player, recs, fw):
    held = fw.fn('resume_held', C.c_int)
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(400)
    assert held() == 1
    player.sec = 10
    assert held() == 0
    player.total = 0  # stopped: where it was
    assert held() == 1


def test_resume_return_comes_back_to_this_second(player, recs, fw, st):
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(400)
    fw.fn('resume_return', None)()
    assert (st('rs_go').value, st('rs_go_key', C.c_uint).value) == (400, key_of(LONG))
    player.total = 0
    fw.fn('resume_return', None)()
    assert st('rs_go').value == 400  # stopped: the place it stopped at


BOOK = '/mnt/mmc/Audiobooks/book.m4b'
CHAPTERS = [(0, 600), (600, 1200), (1200, 0)]


def book(recs):
    return [recs(path=BOOK, title=f'Chapter {i + 1}', cue=c, end=e) for i, (c, e) in enumerate(CHAPTERS)]


def test_resume_on_a_books_chapter_plays_from_the_saved_place(hold, recs, ui, st, fw, mcl):
    chapters = book(recs)
    hold.card.files[RESUME_FILE] = places_file((key_of(BOOK), 900))
    hold.songs_page('allmusic_page', chapters, sel=0)
    hold.hold()
    assert hold.menu()[0] == 'Resume'
    played = []
    ui.routes['playing_page'] = lambda ctx: played.append((list(hold.deques[C.c_void_p.from_address(ctx).value]),
                                                         C.c_int.from_address(ctx + 8).value, C.c_int.from_address(ctx + 12).value))
    p = Player(fw, mcl, hold.deques, hold.card)
    hold.pick('Resume')
    assert played == [(chapters, 1, 0xf001)]  # the list's songs, from the chapter holding 900 s
    assert (st('rs_go').value, st('rs_go_key', C.c_uint).value) == (300, key_of(BOOK, 600))
    p.play(chapters, at=1, total=600)  # it plays: the chapter's first second seeks to the place
    p.poll(0), p.poll(0)
    assert p.seeks == []
    p.poll(1)
    assert p.seeks == [300]


def test_resume_with_its_place_gone_says_so(hold, recs, ui, st):
    chapters = book(recs)
    hold.card.files[RESUME_FILE] = places_file((key_of(BOOK), 900))
    hold.songs_page('allmusic_page', chapters, sel=0)
    hold.hold()
    hold.menu()
    C.memset(C.addressof(st('spots')), 0, 8 * O['RESUME_SLOTS'])  # the book finished since
    hold.pick('Resume')
    assert ui.toasts == ['Queue unchanged'] and 'playing_page' not in ui.went


def test_a_resume_rows_chapter_that_never_came_is_forgotten(player, recs, st):
    chapters = book(recs)
    st('rs_go_key', C.c_uint).value, st('rs_go').value, st('rs_go_at', C.c_uint).value = key_of(BOOK, 600), 300, player.now
    player.play(chapters, at=1, total=600)
    player.now += 8001
    player.poll(0), player.poll(0), player.poll(1)
    assert player.seeks == [] and st('rs_go_key', C.c_uint).value == 0


def test_a_books_chapter_keeps_the_books_place_past_its_first_seconds(player, recs):
    player.play(book(recs), at=1, total=600)
    player.poll(0), player.poll(1)
    player.poll(EDGE - 1)
    player.poll(EDGE - 1), player.poll(EDGE - 1)
    assert player.places() == []  # a chapter's start leaves the book's place alone
    player.poll(EDGE + 70)
    assert player.places() == [(key_of(BOOK), 600 + EDGE + 70)]


def test_a_music_cue_image_is_left_alone(player, recs):
    player.play([recs(path='/mnt/mmc/Music/live.flac', cue=600, end=4800)], total=4200)
    player.poll(0), player.poll(1), player.poll(900), player.poll(900), player.poll(900)
    assert player.places() == []


def test_the_chapters_page_suppresses_a_jump(player, recs, st):
    player.card.files[RESUME_FILE] = places_file((key_of(LONG), 700))
    player.play([recs(path=LONG)])
    st('ch_key', C.c_uint).value = key_of(LONG)
    player.poll(0), player.poll(0), player.poll(1)
    assert player.seeks == []


# Listens: a play count (Most Played) and a scrobble once half the track (or LISTEN_MAX_S) is heard.
LISTEN_MAX_S, LISTEN_MIN_S = O['LISTEN_MAX_S'], O['LISTEN_MIN_S']


def plays(data):
    return [p for p in struct.iter_unpack('<II', data) if p[0]]


def test_half_a_song_heard_counts_a_play_and_scrobbles_it(player, recs):
    r = recs(path='/mnt/mmc/Music/a.flac', title='Help!', artist='The Beatles', album='Help!', track=1)
    player.play([r], total=200)
    for s in range(0, 101):  # heard from the second poll on, a second a poll: 99 s
        player.poll(s)
    assert PLAYS_FILE not in player.card.files
    player.poll(101)
    assert plays(player.card.files[PLAYS_FILE]) == [(key_of('/mnt/mmc/Music/a.flac'), 1)]
    log = player.card.files['/mnt/mmc/.scrobbler.log'].decode()
    assert log.startswith('#AUDIOSCROBBLER/1.1\n#TZ/UTC\n') and log.count('#AUDIOSCROBBLER') == 1
    assert log.splitlines()[-1] == f'The Beatles\tHelp!\tHelp!\t1\t200\tL\t{1700000000 - 100}\t'  # started: now less heard


@pytest.mark.parametrize('takes', (True, False))
def test_only_a_listen_the_card_takes_counts_for_an_upload(fw, player, recs, takes):
    log = '/mnt/mmc/.scrobbler.log'

    def fclose(f, *_):  # a full card: the log's close fails
        refused = not takes and player.card.open[f][0] == log
        return player.card.fclose(f) or -refused
    fw.mock('fw_fclose', fclose)
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=200)
    for s in range(0, 102):
        player.poll(s)
    assert PLAYS_FILE in player.card.files and (log in player.card.files) == takes
    assert fw.var('scrobble_ui__sa', C.c_uint).value == takes  # pending, the struct's first field


def test_a_seek_is_not_heard(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=200)
    player.poll(0), player.poll(1), player.poll(150), player.poll(151)
    assert PLAYS_FILE not in player.card.files


def test_a_long_song_counts_after_listen_max(player, recs):
    player.play([recs(path=LONG, artist='A')], total=3600)
    for s in range(0, LISTEN_MAX_S + 1):
        player.poll(s)
    assert PLAYS_FILE not in player.card.files
    player.poll(LISTEN_MAX_S + 1)
    assert plays(player.card.files[PLAYS_FILE]) == [(key_of(LONG), 1)]


def test_a_song_too_short_never_counts(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=LISTEN_MIN_S)
    for s in range(LISTEN_MIN_S):
        player.poll(s)
    assert PLAYS_FILE not in player.card.files


def test_a_podcast_is_never_counted(player, recs, ui):
    player.play([recs(path='/mnt/mmc/Podcasts/ep.mp3', artist='A')], total=200)
    for s in range(150):
        player.poll(s)
    assert PLAYS_FILE not in player.card.files and '/mnt/mmc/.scrobbler.log' not in player.card.files


def test_a_repeat_counts_again(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=100)
    for s in list(range(60)) + list(range(60)):
        player.poll(s)
    assert plays(player.card.files[PLAYS_FILE]) == [(key_of('/mnt/mmc/Music/a.flac'), 2)]


def test_scrobble_percent_sends_at_its_share(player, recs, fw):
    fw.mock('toolsReadConfig', lambda path, section, key, out, d, *_: C.memmove(out, b'90\0', 3) if cstr(key) == 'PERCENT' else 0)
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=202)
    for s in range(0, 181):
        player.poll(s)
    assert '/mnt/mmc/.scrobbler.log' not in player.card.files and PLAYS_FILE in player.card.files
    player.poll(181)
    assert '/mnt/mmc/.scrobbler.log' in player.card.files  # 90% of all but its last 2 s


def test_an_untagged_song_counts_but_isnt_scrobbled(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac')], total=100)
    for s in range(60):
        player.poll(s)
    assert PLAYS_FILE in player.card.files and '/mnt/mmc/.scrobbler.log' not in player.card.files


def test_a_clock_never_set_isnt_scrobbled(player, recs, fw):
    fw.mock('fw_time', lambda *_: 1000)
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=100)
    for s in range(60):
        player.poll(s)
    assert '/mnt/mmc/.scrobbler.log' not in player.card.files


def test_album_artist_scrobbles_in_the_artists_place_when_asked(player, recs, fw):
    fw.mock('toolsReadConfig', lambda path, section, key, out, d, *_: C.memmove(out, b'1\0', 2) if cstr(key) == 'ALBUM_ARTIST' else 0)
    player.play([recs(path='/mnt/mmc/Music/a.flac', title='T', artist='A feat. B', album_artist='A')], total=100)
    for s in range(60):
        player.poll(s)
    assert player.card.files['/mnt/mmc/.scrobbler.log'].decode().splitlines()[-1].startswith('A\t')


def test_a_tab_in_a_tag_stays_one_field(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', title='a\tb', artist='x\ny')], total=100)
    for s in range(60):
        player.poll(s)
    assert player.card.files['/mnt/mmc/.scrobbler.log'].decode().splitlines()[-1].split('\t')[:3] == ['x y', '', 'a b']


def test_a_counted_song_joins_most_played_kept_tracks_unless_its_page_shows(player, recs, st, fw, lists):
    a, b = recs(path='/mnt/mmc/Music/a.flac'), recs(path='/mnt/mmc/Music/b.flac')
    kept = player.deques.new([a])
    st('mp_list', C.c_void_p).value = kept
    player.play([b], total=100)
    for s in range(60):
        player.poll(s)
    assert player.deques[kept] == [a, b]
    st('mp_page', C.c_void_p).value = 1
    player.play([recs(path='/mnt/mmc/Music/c.flac')], total=100)
    for s in range(60):
        player.poll(s)
    assert len(player.deques[kept]) == 2 and lists('mp_gen', C.c_uint).value == ~fw.var('library_gen', C.c_uint).value & 0xffffffff


def test_a_full_table_replaces_the_least_played_least_recent(player, recs, st):
    table = (C.c_uint * (2 * O['PLAYS_SLOTS'])).from_address(C.addressof(st('plays')))
    for i in range(O['PLAYS_SLOTS']):
        table[2 * i], table[2 * i + 1] = 1000 + i, 1 if i == 7 or i == 300 else 2
    st('plays_read', C.c_uint).value = 1
    player.play([recs(path='/mnt/mmc/Music/a.flac')], total=100)
    for s in range(60):
        player.poll(s)
    got = plays(player.card.files[PLAYS_FILE])
    assert got[0] == (key_of('/mnt/mmc/Music/a.flac'), 1) and (1000 + 300, 1) not in got and (1000 + 7, 1) in got


def test_a_played_song_moving_out_of_the_table_marks_most_played_stale(player, recs, st, fw, lists):
    a = recs(path='/mnt/mmc/Music/a.flac')
    table = (C.c_uint * (2 * O['PLAYS_SLOTS'])).from_address(C.addressof(st('plays')))
    for i in range(O['PLAYS_SLOTS']):
        table[2 * i], table[2 * i + 1] = 1000 + i, 2
    table[2 * 5], table[2 * 5 + 1] = key_of('/mnt/mmc/Music/a.flac'), 1  # the least played: shown by Most Played
    st('plays_read', C.c_uint).value = 1
    st('mp_list', C.c_void_p).value = player.deques.new([a])
    player.play([recs(path='/mnt/mmc/Music/b.flac')], total=100)
    for s in range(60):
        player.poll(s)
    assert lists('mp_gen', C.c_uint).value == ~fw.var('library_gen', C.c_uint).value & 0xffffffff


# Stock Shuffle's pick and a pending Play next (ringnav_shuffle, shuffle_forced).
def rec_hash(fw, r):
    return fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(r)


def shuffle_queue(fw, recs, mcl, n=4):
    deques = Deques(fw)
    songs = [recs(path=f'/mnt/mmc/{i}.flac') for i in range(n)]
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new(songs)
    return deques, songs


def test_a_play_next_held_for_stocks_shuffle_plays_next_once(fw, recs, mcl, st):
    deques, songs = shuffle_queue(fw, recs, mcl)
    st('qm_forced', C.c_uint).value, st('qm_forced_hash', C.c_uint).value = 3, rec_hash(fw, songs[2])
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 1) or 0x10)
    shuffle = fw.fn('ringnav_shuffle', C.c_int, C.c_int)
    assert shuffle(1) == 0x10 and mcl.pos == 2 and st('qm_forced', C.c_uint).value == 0
    assert shuffle(1) == 0x10 and mcl.pos == 1  # stock's own pick after it


def test_a_play_next_whose_track_moved_is_dropped(fw, recs, mcl, st):
    deques, songs = shuffle_queue(fw, recs, mcl)
    st('qm_forced', C.c_uint).value, st('qm_forced_hash', C.c_uint).value = 3, rec_hash(fw, songs[1])
    assert fw.fn('shuffle_forced', C.c_int)() == -1
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 3))
    fw.fn('ringnav_shuffle', C.c_int, C.c_int)(1)
    assert mcl.pos == 3


@pytest.mark.parametrize('forced', [0, 5])
def test_no_play_next_is_none(fw, recs, mcl, st, forced):
    shuffle_queue(fw, recs, mcl)
    st('qm_forced', C.c_uint).value = forced
    assert fw.fn('shuffle_forced', C.c_int)() == -1


def test_next_after_previous_in_stocks_shuffle_returns_to_the_track_it_left(fw, recs, mcl):
    shuffle_queue(fw, recs, mcl)
    mcl.mode, mcl.pos = 2, 2
    fw.mock('stock_prev_trampoline', lambda *_: setattr(mcl, 'pos', 1))
    fw.fn('ringnav_prev', C.c_int)()
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 3))
    fw.fn('ringnav_shuffle', C.c_int, C.c_int)(1)
    assert mcl.pos == 2


def test_next_in_stocks_shuffle_is_the_track_drawn_ahead_for_crossfade(fw, recs, mcl):
    deques, songs = shuffle_queue(fw, recs, mcl)
    mcl.mode, mcl.pos = 2, 0
    drawn = [C.c_int(3), C.c_int(3)]  # stock's unplayed pool: index 3 whatever the draw
    C.c_uint64.from_address(O['MCL_POOL']).value = deques.new([C.addressof(d) for d in drawn])
    fw.fn('playback_poll', None)()
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 1))
    fw.fn('ringnav_shuffle', C.c_int, C.c_int)(1)
    assert mcl.pos == 3


# Sorting names without their article (ringnav_sort_key).
@pytest.mark.parametrize('name, key', [
    (b'The Beatles', b'Beatles'), (b'A Tribe Called Quest', b'Tribe Called Quest'), (b'An Horse', b'Horse'),
    (b'THE WHO', b'WHO'), (b'the the', b'the'), (b'Theory', b'Theory'), (b'The', b'The'), (b'The ', b'The '),
    (b'The  Band', b'The  Band'), (b'Anna', b'Anna'), (b'A', b'A'), (b'', b''),
])
def test_a_names_article_is_left_out_of_its_sort(fw, name, key):
    s = C.create_string_buffer(name, 32)
    fw.fn('ringnav_sort_key', None, C.c_char_p)(s)
    assert s.value == key and len(fw.calls('toolsTrimLeft')) == 1


# The charge limit (charge_poll): charging stops at CHARGE_STOP and is handed back at CHARGE_RESUME.
STOP_AT, RESUME_AT, CHARGE_POLL = O['CHARGE_STOP'], O['CHARGE_RESUME'], O['CHARGE_POLL_MS']


class Battery:
    def __init__(self, fw, ui, st, limit=1):
        self.fw, self.now = fw, 1000
        st('charge_limit').value = limit
        self.level, self.state = fw.var('fw_ctx_g_power_capacity', C.c_int), fw.var('fw_ctx_g_power_chargestate', C.c_int)
        fw.mock('time_now_ms', lambda *_: self.now)
        self.poll_fn = fw.fn('system__charge_poll', None)

    def poll(self, level, state=1):
        self.level.value, self.state.value = level, state
        self.poll_fn()
        self.now += CHARGE_POLL

    def switches(self):
        return [a[0] for _, a in self.fw.calls('switch_charge_enable')]


def test_charging_stops_at_the_limit_and_comes_back_below_resume(fw, ui, st):
    battery = Battery(fw, ui, st)
    for level in range(STOP_AT - 3, STOP_AT):
        battery.poll(level)
    assert battery.switches() == []
    battery.poll(STOP_AT)
    assert battery.switches() == [0]
    for level in range(STOP_AT - 1, RESUME_AT, -1):
        battery.poll(level, state=0)
    assert battery.switches() == [0]
    battery.poll(RESUME_AT, state=0)
    assert battery.switches() == [0, 1]


def test_charging_turned_back_on_while_held_is_stopped_again(fw, ui, st):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT)
    battery.poll(STOP_AT - 2, state=0)
    battery.poll(STOP_AT - 2, state=1)  # USB mode's exit, or AirPlay, charges again
    assert battery.switches() == [0, 0]


def test_a_full_battery_is_held_too(fw, ui, st):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT + 5, state=2)
    assert battery.switches() == [0]


@pytest.mark.parametrize('page, flag, back', [
    ('usbmode_page', 0, 0), ('usbmode_page', 1, 1), ('airplay_page', 0, 0), ('home_page', 0, 1),
])
def test_the_limit_turned_off_hands_charging_back_as_usb_mode_or_airplay_chose(fw, ui, st, page, flag, back):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT)
    ui.window(page)
    fw.var('g_usbdac_chargeflag', C.c_ubyte).value = flag
    st('charge_limit').value = 0
    battery.poll(STOP_AT, state=0)  # not charging: a charge still held would be left as it is
    assert battery.switches() == [0, back]


def test_the_limit_off_leaves_charging_alone(fw, ui, st):
    battery = Battery(fw, ui, st, limit=0)
    battery.poll(100, state=1)
    assert battery.switches() == []


def test_the_battery_is_read_once_each_poll_period(fw, ui, st):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT - 1)
    battery.now -= 1
    battery.poll(STOP_AT)
    assert battery.switches() == []
    battery.poll(STOP_AT)
    assert battery.switches() == [0]


# The hardware watchdog (wdt_*), started by the first loop pass and fed by every one after.
WDT_MS, WDT_LONG_MS = O['WDT_MS'], O['WDT_LONG_MS']
WDT_START, WDT_STOP, WDT_FEED = 0x8004570d, 0x2000570e, 0x2000570f


class Watchdog:
    def __init__(self, fw, fd=5, refuse=False):
        self.fw, self.calls = fw, []
        fw.mock('fw_open', lambda path, flags, *_: fd if cstr(path) == '/dev/jz_watchdog' else -1)
        fw.mock('fw_ioctl', self.ioctl)
        self.refuse = refuse
        self.poll = fw.fn('system__wdt_poll', None)

    def ioctl(self, fd, request, arg, *_):
        """(fd, request, its int): only a write request (MIPS _IOW, 4 in the top 3 bits: START) reads an
        argument; STOP and FEED (_IO, 1 there) take none, so whatever the C passes them isn't theirs."""
        request &= 0xffffffff
        self.calls.append((fd, request, C.c_int.from_address(arg).value if request >> 29 == 4 else None))
        return -1 if self.refuse and request == WDT_START else 0


def test_the_first_pass_starts_the_watchdog_and_every_pass_feeds_it(fw):
    wdt = Watchdog(fw)
    wdt.poll(), wdt.poll(), wdt.poll()
    assert wdt.calls == [(5, WDT_START, WDT_MS), (5, WDT_FEED, None), (5, WDT_FEED, None)]


def test_a_refused_start_is_stocks_for_good(fw):
    wdt = Watchdog(fw, refuse=True)
    wdt.poll(), wdt.poll()
    assert wdt.calls == [(5, WDT_START, WDT_MS)] and [a[0] for _, a in fw.calls('fw_close')] == [5]


def test_no_watchdog_is_never_tried_again(fw):
    wdt = Watchdog(fw, fd=-1)
    wdt.poll(), wdt.poll()
    assert wdt.calls == [] and len(fw.calls('fw_open')) == 1


def test_a_long_pass_gets_its_long_span_then_the_usual_one(fw):
    wdt = Watchdog(fw)
    wdt.poll()
    long_ = fw.fn('ringnav_wdt_long', None)
    long_(), long_()
    wdt.poll(), wdt.poll()
    assert wdt.calls[1:] == [(5, WDT_STOP, None), (5, WDT_START, WDT_LONG_MS), (5, WDT_STOP, None), (5, WDT_START, WDT_MS),
                             (5, WDT_FEED, None)]


def test_a_long_pass_with_no_watchdog_does_nothing(fw):
    wdt = Watchdog(fw, fd=-1)
    fw.fn('ringnav_wdt_long', None)()
    wdt.poll()
    fw.fn('ringnav_wdt_long', None)()
    assert wdt.calls == []


def test_a_clean_end_stops_the_watchdog_and_removes_the_state_record(fw, ui):
    card = Card(fw, {'/mnt/data/q2-state': b'screen:on\n'})
    wdt = Watchdog(fw)
    wdt.poll()
    end = fw.fn('ringnav_clean_end', None)
    end(), end()
    wdt.poll()
    fw.fn('system__state_poll', None)()
    assert wdt.calls == [(5, WDT_START, WDT_MS), (5, WDT_STOP, None)] and card.files == {}


# The state record (state_poll): /mnt/data/q2-state, one line rewritten whenever it changes.
class State:
    def __init__(self, fw, ui):
        self.fw, self.card = fw, Card(fw)
        self.status, self.out = 2, 0
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('mclGetOutputWay', lambda *_: self.out)
        self.poll_fn = fw.fn('system__state_poll', None)

    def poll(self):
        self.poll_fn()
        return self.card.files.get('/mnt/data/q2-state', b'').decode()


def test_the_state_record_says_what_the_q2_is_doing(fw, ui):
    state = State(fw, ui)
    fw.var('fw_ctx_g_power_capacity', C.c_int).value = 57
    assert state.poll() == 'screen:on power:running cable:out battery:57 play:playing out:jack\n'
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    fw.var('g_usbdet_value', C.c_ubyte).value = 1
    state.status, state.out = 3, 1
    assert state.poll() == 'screen:off power:running cable:in battery:57 play:paused out:bluetooth\n'


@pytest.mark.parametrize('level, status, out, line', [
    (0, 1, 2, 'battery:0 play:stopped out:usb'), (100, 0, 0, 'battery:100 play:? out:jack'),
    (250, 4, 3, 'battery:100 play:? out:jack'), (-1, -1, -1, 'battery:100 play:? out:jack'),
])
def test_the_state_records_odd_readings(fw, ui, level, status, out, line):
    state = State(fw, ui)
    fw.var('fw_ctx_g_power_capacity', C.c_int).value = level
    state.status, state.out = status, out
    assert line in state.poll()


def test_the_state_record_is_written_only_when_it_changes(fw, ui):
    state = State(fw, ui)
    state.poll(), state.poll()
    assert len(fw.calls('fw_fopen')) == 1 and len(fw.calls('fw_fsync')) == 1
    state.status = 3
    state.poll()
    assert len(fw.calls('fw_fopen')) == 2


def test_a_record_that_cant_be_written_is_tried_again(fw, ui):
    state = State(fw, ui)
    fw.mock('fw_fopen', lambda *_: 0)
    state.poll()
    state.card = Card(fw)
    assert state.poll().startswith('screen:on')


def test_standby_records_the_suspend_it_may_never_wake_from(fw, ui, power):
    state = State(fw, ui)
    seen = []
    fw.mock('stock_standby_trampoline', lambda *_: seen.append(state.card.files['/mnt/data/q2-state'].decode()) or 0)
    fw.fn('ringnav_standby', C.c_int)()
    assert 'power:suspending' in seen[0] and power('suspended').value == 0


# The RTC's clock (rtc_now), as Standby's limit reads it.
@pytest.mark.parametrize('data, now', [(b'1700000000\n', 1700000000), (b'42', 42), (b'', 0), (b'x1', 0), (b'12x3', 12)])
def test_the_rtcs_seconds(fw, ui, data, now):
    Card(fw, {'/sys/class/rtc/rtc0/since_epoch': data})
    assert fw.fn('system__rtc_now', C.c_uint)() == now


def test_no_rtc_reads_zero(fw, ui):
    Card(fw)
    assert fw.fn('system__rtc_now', C.c_uint)() == 0
