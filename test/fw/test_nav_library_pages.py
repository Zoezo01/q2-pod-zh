"""The payload's own Library pages, end to end on a fake AWTK: library_lists.c's Shuffle, Most Played, Long Songs, Era
and Tidal Downloads, ratings.c's Ratings and Now Playing's heart, navigation.c's Chapters and Streaming's
Configuration, scrobble_ui.c's Upload Scrobbles, the Library and Streaming pages (home.c) and the About page stock
builds and the payload rearranges, Folder view's hooks, the queue menu's tracks and the playlist editor's records
(playlists.c)."""
import ctypes as C
import re
import struct

import pytest

import host
from conftest import FNV_PRIME, O, Deques, Files, claim, fnv1a, high_halves, in_fork, libc, ui_name
from conftest import Rows as HeldRows  # Rows here is Folder view's list
from test_navigation import Sa  # test_navigation.py keeps the mirror

KEY_RETURN, KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY = (O[k] for k in ('KEY_RETURN', 'KEY_CENTER', 'KEY_PREV', 'KEY_NEXT', 'KEY_PLAY'))
EVT_CLICK, EVT_DESTROY, EVT_KEY_UP, EVT_POINTER_DOWN, EVT_POINTER_ABORT = (
    O[k] for k in ('EVT_CLICK', 'EVT_DESTROY', 'EVT_KEY_UP', 'EVT_POINTER_DOWN', 'EVT_POINTER_ABORT'))
W_PARENT, W_EMITTER, EVENT_KEY = O['W_PARENT'], O['W_EMITTER'], O['EVENT_KEY']
REC_ID, REC_TYPE, REC_CUE_START = O['REC_ID'], O['REC_TYPE'], O['REC_CUE_START']
PLAYS_SLOTS, RATE_SLOTS, RESUME_SLOTS = O['PLAYS_SLOTS'], O['RATE_SLOTS'], O['RESUME_SLOTS']
LONG_SONG_S, RATE_HOLD_MS = O['LONG_SONG_S'], O['RATE_HOLD_MS']
LIBRARY, FOLDER = 0xf001, 1
STOP, REPEAT = 11, 8  # a handler's RET_STOP; a timer's RET_REPEAT
PLAYS_FILE, RESUME_FILE = '/mnt/data/ringnav-plays', '/mnt/data/ringnav-resume'
RATINGS, ALBUM_RATINGS, LENGTHS = '/mnt/data/ringnav-ratings', '/mnt/data/ringnav-album-ratings', '/mnt/data/ringnav-lengths'
FAV_CLICK = 0x53c66c  # Now Playing's heart: stock's My Fav toggle
TIDAL_DIR, SPOT_DIR = '/mnt/mmc/.tidal', '/mnt/mmc/.spotify'
DOT = '·'
HEART, NO_HEART = '♥', '♡'
CLICK = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
TICK = C.CFUNCTYPE(C.c_int, C.c_void_p)
TYPES = {'window_create': 'window', 'list_item_create': 'list_item', 'hscroll_label_create': 'hscroll_label',
         'image_create': 'image', 'button_create': 'button', 'scroll_view_create': 'scroll_view',
         'list_view_create': 'list_view', 'label_create': 'label'}


def signed(v):
    return C.c_int(v).value


class Ui:
    """AWTK as a tree of zeroed widgets: what the C builds, names, writes, shows and hooks, kept per widget, with
    the timers, the windows (the newest on top) and where navigation went: toasts, plays and backs."""
    def __init__(self, fw):
        self.fw, self.blocks, self.cstrings = fw, {}, {}
        self.kind, self.kids = {}, {0: []}
        self.text, self.name, self.image, self.props = {}, {}, {}, {}
        self.hidden, self.dull = set(), set()
        self.handlers, self.windows, self.timers, self.tick = [], [], {}, 0
        self.toasts, self.played, self.went, self.backs, self.offs = [], [], [], 0, []
        self.deques = Deques(fw)
        for kind in TYPES:
            fw.mock(kind, lambda parent, x, y, w, h, *_, k=kind: self.new(k, parent, x, y, w, h))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.new(C.string_at(kind).decode(), parent, x, y, w, 0))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_set_name', lambda w, s, *_: self.name.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_get_type', lambda w, *_: self.cstr(TYPES.get(self.kind.get(w, ''), self.kind.get(w, ''))))
        fw.mock('widget_set_prop_int', lambda w, k, v, *_: self.props.__setitem__((w, C.string_at(k).decode()), signed(v)))
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: self.props.get((w, C.string_at(k).decode()), signed(d)))
        fw.mock('widget_set_prop_str', lambda w, k, v, *_: self.props.__setitem__((w, C.string_at(k).decode()), C.string_at(v).decode()))
        fw.mock('widget_set_visible', lambda w, v, *_: (self.hidden.discard if v & 0xff else self.hidden.add)(w))
        fw.mock('widget_get_visible', lambda w, *_: w not in self.hidden)
        fw.mock('widget_set_sensitive', lambda w, v, *_: (self.dull.discard if v & 0xff else self.dull.add)(w))
        fw.mock('widget_get_child', lambda w, i, *_: self.child(w, i & 0xffffffff))
        fw.mock('widget_count_children', lambda w, *_: len(self.kids.get(w, ())))
        fw.mock('widget_lookup', lambda w, name, deep, *_: self.lookup(w, C.string_at(name).decode(), deep & 0xff))
        fw.mock('widget_restack', lambda w, i, *_: self.restack(w, i & 0xffffffff))
        fw.mock('widget_destroy', lambda w, *_: self.destroy(w))
        fw.mock('widget_destroy_children', lambda w, *_: [self.destroy(k) for k in list(self.kids.get(w, ()))] and 0)
        fw.mock('image_base_set_image', lambda w, s, *_: self.image.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_on', self.on)
        fw.mock('widget_off_by_func', lambda w, evt, fn, ctx, *_: self.offs.append((w, evt, fn, ctx)))
        fw.mock('timer_add', self.add_timer)
        fw.mock('timer_remove', lambda t, *_: self.timers.pop(t & 0xffffffff, None) and 0)
        fw.mock('window_manager_get_top_window', lambda *_: self.windows[-1] if self.windows else 0)
        fw.mock('navigator_to_with_context', self.navigate)
        fw.mock('navigator_to', lambda name, *_: self.went.append(C.string_at(name).decode()))
        fw.mock('navigator_back', lambda *_: setattr(self, 'backs', self.backs + 1))

    def cstr(self, value):
        """A NUL-terminated copy of value the C may keep: one buffer per distinct string."""
        if value not in self.cstrings:
            self.cstrings[value] = C.create_string_buffer(value.encode())
        return C.addressof(self.cstrings[value])

    def new(self, kind, parent, x, y, w, h):
        block = C.create_string_buffer(0x200)
        if kind == 'window_create' and not w:
            w, h = 375, 320  # stock's pages fill the screen
        struct.pack_into('<4i', block, 0, *(signed(v) for v in (x, y, w, h)))
        struct.pack_into('<Q', block, W_PARENT, parent)
        at = C.addressof(block)
        self.blocks[at], self.kind[at], self.kids[at] = block, kind, []
        self.kids.setdefault(parent, []).append(at)
        if kind == 'window_create':
            self.windows.append(at)
        return at

    def parent(self, w):
        return C.c_void_p.from_address(w + W_PARENT).value or 0

    def child(self, w, i):
        kids = self.kids.get(w, [])
        return kids[i] if i < len(kids) else 0

    def get_text(self, w, out, n, *_):
        s = self.text.get(w, '').encode()[:n - 1]
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0

    def prop_str(self, w, key, default, *_):
        key = C.string_at(key).decode()
        value = {'name': self.name, 'image': self.image}.get(key, {}).get(w)
        if value is None:
            value = self.props.get((w, key))
        return self.cstr(value) if isinstance(value, str) else default

    def lookup(self, w, name, deep):
        for k in self.kids.get(w, ()):
            if self.name.get(k) == name:
                return k
            found = deep and self.lookup(k, name, deep)
            if found:
                return found
        return 0

    def restack(self, w, i):
        kids = self.kids[self.parent(w)]
        kids.remove(w)
        kids.insert(min(i, len(kids)), w)

    def destroy(self, w):
        """w and what it holds go, each one's EVT_DESTROY handlers run first, as AWTK's."""
        for k in list(self.kids.get(w, ())):
            self.destroy(k)
        for h in [h for h in self.handlers if h[0] == w and h[1] == EVT_DESTROY]:
            CLICK(h[2])(h[3], 0)
        self.handlers = [h for h in self.handlers if h[0] != w]
        if w in self.kids.get(self.parent(w), ()):
            self.kids[self.parent(w)].remove(w)
        if w in self.windows:
            self.windows.remove(w)
        self.kids.pop(w, None)
        return 0

    def on(self, w, evt, fn, ctx, *_):
        self.handlers.append((w, evt & 0xffffffff, fn, ctx))
        return len(self.handlers)

    def add_timer(self, fn, ctx, ms, *_):
        self.tick += 1
        self.timers[self.tick] = (fn, ctx, ms & 0xffffffff)
        return self.tick

    def navigate(self, name, info, *_):
        name = C.string_at(name).decode()
        if name == 'dialog/msginfo_dialog':
            self.toasts.append(C.string_at(info + 8).decode())
        elif name == 'playing_page':
            dq, idx, cls = struct.unpack('<Qii', C.string_at(info, 16))
            self.played.append((list(self.deques[dq]), idx, cls))  # playing_page copies the queue at once
        else:
            self.went.append(name)

    # What a test reads and does.
    def fn(self, name):
        return C.cast(getattr(self.fw.lib, ui_name(self.fw.lib, name)), C.c_void_p).value

    def pending(self, name):
        """The live timers running name, of navigation.c or the unit it moved to (ui_name finds it): [(id, ms)]."""
        return [(t, ms) for t, (fn, _, ms) in self.timers.items() if fn == self.fn(name)]

    def fire(self, name):
        """The newest timer running name, as the next loop pass runs it: gone unless it repeats."""
        t, _ = self.pending(name)[-1]
        fn, ctx, _ = self.timers[t]
        result = TICK(fn)(ctx)
        if result != REPEAT and self.timers.get(t, (None,))[0] == fn:
            del self.timers[t]
        return result

    def handler(self, w, evt):
        fns = [(fn, ctx) for ww, e, fn, ctx in self.handlers if ww == w and e == evt]
        fn, ctx = fns[-1]
        return lambda event=0: CLICK(fn)(ctx, event)

    def subtree(self, w):
        for k in self.kids.get(w, ()):
            yield k
            yield from self.subtree(k)

    def view(self, page):
        """The newest list in page: its scroll view."""
        return [w for w in self.subtree(page) if self.kind[w] == 'scroll_view_create'][-1]

    def rows(self, page):
        """The newest list's rows as (caption, detail or None)."""
        out = []
        for item in self.kids[self.view(page)]:
            labels = [self.text.get(k) for k in self.kids[item] if self.kind[k] == 'hscroll_label_create'] + [None, None]
            out.append((labels[0], labels[1]))
        return out

    def captions(self, page):
        return [c for c, _ in self.rows(page)]

    def click(self, page, row):
        """Row row of page's newest list, clicked."""
        return self.handler(self.kids[self.view(page)][row], EVT_CLICK)()

    def shown(self, page):
        """The texts of page's visible labels: its title or message among them."""
        def visible(w):
            while w and w != page:
                if w in self.hidden:
                    return False
                w = self.parent(w)
            return True
        return [self.text[w] for w in self.subtree(page) if w in self.text and visible(w)]

    def key(self, page, key):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, key)
        return self.handler(page, EVT_KEY_UP)(C.addressof(event))

    def close(self, page):
        self.destroy(page)

    def top(self):
        return self.windows[-1]


@pytest.fixture
def ui(fw):
    return Ui(fw)


class Config:
    """config.ini: a key's value, or the default; a missing key with an empty default leaves out alone. A write
    is read back, and kept in written."""
    def __init__(self, fw, **values):
        self.values, self.written = {k.encode(): v for k, v in values.items()}, []
        fw.mock('toolsReadConfig', self.read)
        fw.mock('toolsWriteConfig', self.write)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        if value or C.string_at(key) in self.values:
            C.memmove(out, value + b'\0', len(value) + 1)

    def write(self, path, section, key, value, *_):
        self.values[C.string_at(key)] = C.string_at(value)
        self.written.append(C.string_at(value))
        return 0


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    """navigation.c's state by field: st('rt_page', C.c_void_p).value."""
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


@pytest.fixture(scope='module')
def lists_fields(library):
    """library_lists.c's own state, ll: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'library_lists.o', 'll')


@pytest.fixture
def lists(fw, lists_fields):
    base = C.addressof(fw.var('library_lists__ll', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + lists_fields[name])


# Song records. The Q2's hold 4-byte pointers 4 bytes apart (REC_NAME, REC_PATH, REC_TITLE, REC_ALBUM, REC_ARTIST),
# and P() reads 8 here: a field's pointer is its own word with the next field's word as its high half. So each
# record's strings sit where that chain says: string k at (word k+1) << 32 | (word k), a page mapped for it. The
# words are this file's high halves (conftest.high_halves), the multiples of 0x40 among them.
WORDS = [w for w in high_halves(0x1000) if w % 0x40 == 0]  # a string's room: 63 bytes and its NUL
NAME_AT = O['REC_NAME']
FIELDS = ('name', 'path', 'title', 'album', 'artist')
MAPPED = set()


def place(address, data):
    page = address & ~0xfff
    if page not in MAPPED:
        assert libc.mmap(page, 0x1000, 3, 0x100022, -1, 0) == page, f'{page:#x} is taken'  # MAP_FIXED_NOREPLACE
        MAPPED.add(page)
    assert len(data) < 0x40
    C.memmove(address, data + b'\0', len(data) + 1)


class Songs:
    """stSongInfo records: a song's file name, path, title, album and artist, its id, kind and CUE range."""
    def __init__(self):
        self.keep, self.pairs, self.start = [], set(), 0

    def chain(self):
        n = len(WORDS)
        for stride in range(1, n):
            for _ in range(n):
                i, self.start = self.start, (self.start + 1) % n
                words = [WORDS[(i + k * stride) % n] for k in range(len(FIELDS) + 1)]
                pairs = set(zip(words[1:], words))
                if len(pairs) == len(FIELDS) and not pairs & self.pairs:
                    self.pairs |= pairs
                    return words
        raise AssertionError('no room for another record')

    def __call__(self, path=b'', title=b'', artist=b'', album=b'', name=None, id_=0, kind=8, cue=0, end=0, track=0, disc=0):
        name = path.rsplit(b'/', 1)[-1] if name is None else name
        words = self.chain()
        r = C.create_string_buffer(0x60)
        for k, value in enumerate((name, path, title, album, artist)):
            place(words[k + 1] << 32 | words[k], value)
        struct.pack_into('<6I', r, NAME_AT, *words)
        struct.pack_into('<i', r, REC_ID, id_)
        struct.pack_into('<i', r, REC_TYPE, kind)
        struct.pack_into('<ii', r, REC_CUE_START, cue, end)
        struct.pack_into('<i', r, O['REC_TRACK'], track)
        struct.pack_into('<i', r, O['REC_DISC'], disc)
        self.keep.append(r)
        return C.addressof(r)


@pytest.fixture
def song():
    return Songs()


def field(r, name):
    return C.string_at(C.c_void_p.from_address(r + NAME_AT + 4 * FIELDS.index(name)).value or 0)


def test_a_record_reads_back_whole(fw, song):
    """The layout the rest stands on: the C's own track_name reads the title, else the file name less its
    extension, through the same 8-byte reads."""
    tagged, untagged = song(b'/m/A/01 One.flac', b'One', b'Band', b'A'), song(b'/m/A/02 Two.flac')
    assert [field(tagged, f) for f in FIELDS] == [b'01 One.flac', b'/m/A/01 One.flac', b'One', b'A', b'Band']
    name = fw.fn('track_name', C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)
    out = C.create_string_buffer(64)
    assert name(out, 64, tagged) == b'One' and name(out, 64, untagged) == b'02 Two'


def listen_key(path, cue=0):
    """A play count's key (docs/internals.md#most-played): the path's FNV-1a with its separator, a CUE track's
    start mixed in; never 0."""
    key = fnv1a(path) * FNV_PRIME & 0xffffffff
    if cue:
        key = fnv1a(struct.pack('<i', cue), key)
    return key or 1


def file_key(path):
    return fnv1a(path) * FNV_PRIME & 0xffffffff


class Library:
    """Stock's library queries over a list of records: getAllMusic fills staged's deque with every song."""
    def __init__(self, fw, ui, songs=()):
        self.fw, self.deques, self.songs, self.asked = fw, ui.deques, list(songs), []
        fw.mock('getAllMusic', self.all)

    def all(self, sort, *_):
        self.asked.append(sort)
        self.deques[self.directory()].extend(self.songs)
        return len(self.songs)

    def directory(self):
        return self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value


def call(fw, name, ctx=0):
    """A row's click handler, in navigation.c or the unit it moved to (ui_name finds it), as the row's press
    calls it."""
    return fw.fn(ui_name(fw.lib, name), C.c_int, C.c_void_p, C.c_void_p)(ctx, None)


# The Library's Shuffle row.
def test_shuffle_opens_one_page_of_three_picks(fw, ui):
    call(fw, 'shuffle_menu')
    page = ui.top()
    assert ui.name[page] == 'shuffle_page' and ui.captions(page) == ['Shuffle Songs', 'Shuffle Albums', 'Shuffle Folders']
    assert 'Albums and folders take a moment to shuffle' in ui.shown(page)
    call(fw, 'shuffle_menu')  # pressed again while it is open: the same page
    assert ui.windows == [page]


def test_shuffle_page_opens_again_once_closed(fw, ui):
    call(fw, 'shuffle_menu')
    ui.close(ui.top())
    call(fw, 'shuffle_menu')
    assert len(ui.windows) == 1


def test_shuffle_songs_with_no_library_says_to_update_it(fw, ui, mcl):
    Library(fw, ui)
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), 0)
    assert ui.toasts == ['Update Local Music first'] and not ui.played


def test_shuffle_songs_plays_the_library_from_a_random_song(fw, ui, mcl, song):
    songs = [song(b'/m/%d.flac' % i) for i in range(4)]
    library = Library(fw, ui, songs)
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), 0)
    (queue, at, cls), = ui.played
    assert queue == songs and 0 <= at < 4 and cls == LIBRARY
    assert library.asked == [3]  # unsorted: Shuffle has no use for the title sort
    assert ui.windows  # the page stays, as Most Played's rows leave it


@pytest.mark.parametrize('row, folders', [(1, 0), (2, 1)])
def test_shuffle_albums_and_folders_with_no_library(fw, ui, mcl, row, folders):
    Library(fw, ui)
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), row)
    assert ui.toasts == ['Update Local Music first'] and not ui.played


@pytest.mark.parametrize('row, folders', [(1, 0), (2, 1)])
def test_shuffle_albums_and_folders_play_the_whole_library_grouped(fw, ui, mcl, song, row, folders):
    songs = [song(b'/m/%c/%d.flac' % (65 + i % 2, i), album=b'%c' % (65 + i % 2)) for i in range(4)]
    library = Library(fw, ui, songs)
    queue = library.deques.new()
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
    fw.mock('mclLoadPlayList', lambda dq, *_: library.deques[queue].__setitem__(slice(None), library.deques[dq]))
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), row)
    (played, at, cls), = ui.played
    assert played == songs and cls == LIBRARY and not ui.toasts
    assert fw.fn('playback_option', C.c_int, C.c_int)(2) == folders  # grouped by album or by folder


def test_shuffle_albums_over_the_queue_s_limit_says_so(fw, ui, mcl):
    library = Library(fw, ui, [0x100] * 65537)  # playback.c's QUEUE_LIMIT is 65536 songs
    queue = ui.deques.new()
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
    fw.mock('mclLoadPlayList', lambda dq, *_: ui.deques[queue].__setitem__(slice(None), ui.deques[dq]))
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), 1)
    assert ui.toasts == ['Too many songs to shuffle'] and not ui.played and library.asked == [3]


@pytest.mark.parametrize('key, back, result', [(KEY_RETURN, 1, STOP), (KEY_CENTER, 0, 0), (KEY_PLAY, 0, 0)])
def test_a_page_s_return_goes_back(fw, ui, key, back, result):
    call(fw, 'shuffle_menu')
    assert ui.key(ui.top(), key) == result and ui.backs == back


# Most Played.
def plays(*counts):
    """ringnav-plays: (path, count) slots, most recently counted first."""
    data = b''.join(struct.pack('<2I', listen_key(p), n) for p, n in counts)
    return data + b'\0' * (PLAYS_SLOTS * 8 - len(data))


def test_most_played_lists_the_counted_songs_most_played_first(fw, ui, song):
    a, b, c, d = (song(b'/m/%c.flac' % x, b'Song %c' % x, artist) for x, artist in zip(b'abcd', (b'X', b'', b'Y', b'Z')))
    Files(fw, {PLAYS_FILE: plays((b'/m/b.flac', 1), (b'/m/c.flac', 5), (b'/m/a.flac', 1))})
    Library(fw, ui, [a, b, c, d])
    call(fw, 'most_played')
    page = ui.top()
    assert ui.name[page] == 'mostplayed_page'
    # equal counts: the more recently counted first (the table's order)
    assert ui.rows(page) == [('Song c', f'Y {DOT} 5 plays'), ('Song b', '1 play'), ('Song a', f'X {DOT} 1 play')]
    ui.click(page, 1)
    assert ui.played == [([c, b, a], 1, LIBRARY)]


def test_most_played_with_no_plays_says_so(fw, ui, song):
    Files(fw)
    Library(fw, ui, [song(b'/m/a.flac')])
    call(fw, 'most_played')
    assert 'No plays yet' in ui.shown(ui.top()) and ui.rows(ui.top()) == []


def test_most_played_ranks_its_kept_songs_again_without_the_library(fw, ui, song, st):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Files(fw, {PLAYS_FILE: plays((b'/m/a.flac', 2), (b'/m/b.flac', 1))})
    library = Library(fw, ui, [a, b])
    call(fw, 'most_played')
    ui.close(ui.top())
    st('plays', C.c_uint * 4)[:] = [listen_key(b'/m/b.flac'), 3, listen_key(b'/m/a.flac'), 2]  # b heard twice since
    call(fw, 'most_played')
    assert ui.captions(ui.top()) == ['B', 'A'] and library.asked == [3]


def test_most_played_reads_the_library_again_after_it_changed(fw, ui, song):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Files(fw, {PLAYS_FILE: plays((b'/m/a.flac', 2), (b'/m/b.flac', 1))})
    library = Library(fw, ui, [a])
    call(fw, 'most_played')
    ui.close(ui.top())
    library.songs.append(b)
    fw.var('library_gen', C.c_uint).value += 1  # a scan or an update since
    call(fw, 'most_played')
    assert ui.captions(ui.top()) == ['A', 'B'] and library.asked == [3, 3]


def test_most_played_opens_once(fw, ui, song):
    Files(fw)
    Library(fw, ui)
    call(fw, 'most_played')
    call(fw, 'most_played')
    assert len(ui.windows) == 1


def test_most_played_finds_a_kept_song(fw, ui, song):
    a, b = song(b'/m/a.flac'), song(b'/m/b.flac')
    Files(fw, {PLAYS_FILE: plays((b'/m/b.flac', 4), (b'/m/a.flac', 1))})
    Library(fw, ui, [a, b])
    find = fw.fn('library_lists__mp_find', C.c_int, C.c_uint)
    assert find(listen_key(b'/m/a.flac')) == -1  # nothing kept before the page opens
    call(fw, 'most_played')
    assert [find(listen_key(p)) for p in (b'/m/b.flac', b'/m/a.flac', b'/m/c.flac')] == [0, 1, -1]


def test_album_plays_sums_each_album_s_listens(fw, ui, song):
    a1, a2, b1, other = (song(b'/m/%s.flac' % n) for n in (b'a1', b'a2', b'b1', b'x'))
    Files(fw, {PLAYS_FILE: plays((b'/m/a1.flac', 3), (b'/m/b1.flac', 2), (b'/m/a2.flac', 4))})
    Library(fw, ui, [a1, a2, b1, other])
    album = {a1: 0, a2: 0, b1: 1, other: -1}
    of = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda r: album[r])
    sums = (C.c_uint * 2)()
    fw.fn('album_plays', None, C.c_void_p, C.c_void_p)(C.cast(of, C.c_void_p), sums)
    assert list(sums) == [7, 2]


# Ratings' lists.
def ratings(*kept):
    """ringnav-ratings: (key, hearts) slots, the rest free."""
    data = b''.join(struct.pack('<I', key & ~7 | n) for key, n in kept)
    return data + b'\0' * (RATE_SLOTS * 4 - len(data))


def test_ratings_menu_lists_its_five_lists(fw, ui):
    call(fw, 'ratings_menu')
    page = ui.top()
    assert ui.name[page] == 'ratings_page' and ui.captions(page) == ['Top Rated', 'Loved', 'Albums', 'All Rated', 'Disliked']
    assert [ui.image[w] for w in ui.subtree(ui.view(page)) if w in ui.image] == ['list_into'] * 5  # each opens a list
    call(fw, 'ratings_menu')
    assert ui.windows == [page]


class Rated:
    """A library of five songs: a My Fav, one each of 4, 3 and 1 kept hearts, one unrated."""
    def __init__(self, fw, ui, song):
        self.fav, self.four, self.three, self.one, self.none = (
            song(b'/m/%s.flac' % n, n.title(), artist) for n, artist in
            ((b'fav', b'F'), (b'four', b''), (b'three', b'T'), (b'one', b'O'), (b'none', b'N')))
        self.library = Library(fw, ui, [self.one, self.fav, self.three, self.none, self.four])
        Files(fw, {RATINGS: ratings((listen_key(b'/m/four.flac'), 4), (listen_key(b'/m/three.flac'), 3),
                                    (listen_key(b'/m/one.flac'), 1))})
        stock_fav = song(b'/m/fav.flac')  # stock's own myfav row of the same file
        fw.mock('getAllFav', lambda *_: self.library.deques[self.library.directory()].append(stock_fav) or 1)


@pytest.mark.parametrize('row, title, want', [
    (0, 'Top Rated', [('Fav', f'{HEART * 5} {DOT} F'), ('Four', HEART * 4 + NO_HEART)]),
    (1, 'Loved', [('Fav', f'{HEART * 5} {DOT} F')]),
    (3, 'All Rated', [('Fav', f'{HEART * 5} {DOT} F'), ('Four', HEART * 4 + NO_HEART),
                      ('Three', f'{HEART * 3 + NO_HEART * 2} {DOT} T'), ('One', f'{HEART + NO_HEART * 4} {DOT} O')]),
    (4, 'Disliked', [('One', f'{HEART + NO_HEART * 4} {DOT} O')]),
])
def test_a_ratings_list_is_its_hearts_most_first(fw, ui, song, row, title, want):
    Rated(fw, ui, song)
    call(fw, 'ratings_menu')
    ui.click(ui.top(), row)
    page = ui.top()
    assert ui.name[page] == 'rated_page' and title in ui.shown(page) and ui.rows(page) == want


def test_a_ratings_list_plays_from_its_row(fw, ui, song):
    rated = Rated(fw, ui, song)
    call(fw, 'rated_open', 3)
    ui.click(ui.top(), 2)
    assert ui.played == [([rated.fav, rated.four, rated.three, rated.one], 2, LIBRARY)]


def test_a_ratings_list_in_library_order_among_equal_hearts(fw, ui, song):
    a, b, c = (song(b'/m/%c.flac' % x, b'%c' % x) for x in b'abc')
    Library(fw, ui, [c, a, b])
    Files(fw, {RATINGS: ratings(*((listen_key(b'/m/%c.flac' % x), 2) for x in b'abc'))})
    fw.mock('getAllFav', lambda *_: 0)
    call(fw, 'rated_open', 3)
    assert ui.captions(ui.top()) == ['c', 'a', 'b']


def test_an_empty_ratings_list_says_how_to_rate(fw, ui, song):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    Files(fw)
    call(fw, 'rated_open', 0)
    assert ui.rows(ui.top()) == [('No songs yet', 'Hold the heart on Now Playing to rate')]
    item, = ui.kids[ui.view(ui.top())]
    assert [fn for w, e, fn, _ in ui.handlers if w == item and e == EVT_CLICK] == [0]  # nothing to play


def test_a_ratings_list_reads_every_song(fw, ui, song):
    library = Library(fw, ui, [song(b'/m/a.flac')])
    Files(fw)
    call(fw, 'rated_open', 0)
    assert library.asked == [0]  # getAllMusic(0): stock's sort, spoken tracks too


def test_a_ratings_list_closes_with_its_page(fw, ui, song, st):
    Rated(fw, ui, song)
    call(fw, 'rated_open', 0)
    assert st('rt_list', C.c_void_p).value
    ui.close(ui.top())
    assert not st('rt_page', C.c_void_p).value and not st('rt_list', C.c_void_p).value
    call(fw, 'rated_open', 1)  # and opens again
    assert 'Loved' in ui.shown(ui.top())


def test_no_rated_albums_says_so(fw, ui, song):
    Files(fw)
    fw.mock('getAllAlbum', lambda *_: 0)
    call(fw, 'ratings_menu')
    ui.click(ui.top(), 2)
    assert ui.toasts == ['No rated albums yet']


# Long Songs.
class Probe:
    """Stock's TagLib, one file's seconds each (None: TagLib can't open it), and the worker's thread, run when
    the test says."""
    def __init__(self, fw, seconds):
        self.fw, self.seconds, self.opened, self.threads = fw, dict(seconds), [], []
        self.keep, self.files = [], {}
        self.length = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda p: self.seconds[self.files[p]])
        fw.mock('taglib_file_new', self.open)
        fw.mock('taglib_file_audioproperties', lambda f, *_: f)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(fn) or 0)

    def open(self, path, kind, *_):
        path = C.string_at(path)
        self.opened.append(path)
        if self.seconds.get(path) is None:
            return 0
        vtable = (C.c_void_p * 4)(0, 0, 0, C.cast(self.length, C.c_void_p).value)
        props = C.c_void_p(C.addressof(vtable))
        self.keep += [vtable, props]
        self.files[C.addressof(props)] = path
        return C.addressof(props)

    def run(self):
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.threads[-1])(None)


def lengths(*pairs):
    """ringnav-lengths: (path, seconds) as {key, seconds} sorted by key."""
    return b''.join(struct.pack('<2I', k, s) for k, s in sorted((file_key(p), s) for p, s in pairs))


@pytest.fixture
def clock_text(fw):
    """toolsTimeItoa's m:ss, whatever stock's exact format."""
    fw.mock('toolsTimeItoa', lambda out, sec, *_: C.memmove(out, b'%d:%02d\0' % (sec // 60, sec % 60), 8) and 0)


def test_long_songs_lists_those_over_ten_minutes_longest_first(fw, ui, song, clock_text):
    a, b, c = song(b'/m/a.flac', b'A', b'X'), song(b'/m/b.flac', b'B'), song(b'/m/c.flac', b'C', b'Z')
    Library(fw, ui, [a, b, c])
    Files(fw, {LENGTHS: lengths((b'/m/a.flac', 700), (b'/m/b.flac', 1200), (b'/m/c.flac', LONG_SONG_S))})
    probe = Probe(fw, {})
    call(fw, 'long_songs')
    page = ui.top()
    assert ui.name[page] == 'longsongs_page' and ui.rows(page) == [('B', '20:00'), ('A', f'X {DOT} 11:40')]
    assert not probe.threads  # every length known
    ui.click(page, 1)
    assert ui.played == [([b, a], 1, LIBRARY)]


def test_long_songs_with_none_says_so(fw, ui, song):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    Files(fw, {LENGTHS: lengths((b'/m/a.flac', 60))})
    call(fw, 'long_songs')
    assert ui.rows(ui.top()) == [('No songs over 10 minutes', None)]
    call(fw, 'long_songs')  # pressed again while it is open
    assert len(ui.windows) == 1


def test_long_songs_measures_the_unknown_first_then_lists_them(fw, ui, song, clock_text):
    a, b, c = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B'), song(b'/m/c.flac', b'C')
    Library(fw, ui, [a, b, c])
    files = Files(fw, {LENGTHS: lengths((b'/m/a.flac', 900))})
    probe = Probe(fw, {b'/m/b.flac': 3600, b'/m/c.flac': None})
    call(fw, 'long_songs')
    page = ui.top()
    assert ui.rows(page) == [('Cancel', None)] and 'Measuring songs…' in ui.shown(page)
    assert [ms for _, ms in ui.pending('ls_poll')] == [250]
    assert ui.fire('ls_poll') == REPEAT and 'Measuring songs… 0/2' in ui.shown(page)
    probe.run()
    assert sorted(probe.opened) == [b'/m/b.flac', b'/m/c.flac']  # only the two never measured
    assert ui.fire('ls_poll') == 0 and not ui.pending('ls_poll')
    assert ui.rows(page) == [('B', '60:00'), ('A', '15:00')]
    # what was measured is kept, sorted, a file TagLib can't read as 0 so it isn't measured again
    assert files.files[LENGTHS] == lengths((b'/m/a.flac', 900), (b'/m/b.flac', 3600), (b'/m/c.flac', 0))


def test_long_songs_cancel_lists_what_is_known(fw, ui, song, clock_text):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Library(fw, ui, [a, b])
    files = Files(fw, {LENGTHS: lengths((b'/m/a.flac', 900))})
    Probe(fw, {b'/m/b.flac': 3600})
    call(fw, 'long_songs')
    ui.click(ui.top(), 0)  # Cancel, before the worker measured anything
    assert ui.fire('ls_poll') == 0
    assert len(fw.calls('fw_pthread_join')) == 1  # the worker stopped and joined
    assert ui.rows(ui.top()) == [('A', '15:00')] and files.files[LENGTHS] == lengths((b'/m/a.flac', 900))


def test_long_songs_closed_while_measuring_keeps_what_was_measured(fw, ui, song, clock_text):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Library(fw, ui, [a, b])
    files = Files(fw)
    probe = Probe(fw, {b'/m/a.flac': 700, b'/m/b.flac': 800})
    call(fw, 'long_songs')
    probe.run()
    ui.close(ui.top())
    assert not ui.pending('ls_poll') and files.files[LENGTHS] == lengths((b'/m/a.flac', 700), (b'/m/b.flac', 800))
    measured = len(probe.opened)
    call(fw, 'long_songs')  # opened again: nothing left to measure
    assert len(probe.opened) == measured and len(probe.threads) == 1 and ui.captions(ui.top()) == ['B', 'A']


def test_a_lengths_file_that_cannot_be_read_is_not_written_over(fw, ui, song, clock_text):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    old = lengths((b'/m/x.flac', 700), (b'/m/y.flac', 800))
    files = Files(fw, {LENGTHS: old})
    read = files.fread
    fw.mock('fw_fread', lambda out, size, count, f, *_: 0 if files.open[f][0] == LENGTHS else read(out, size, count, f))
    probe = Probe(fw, {b'/m/a.flac': 900})
    call(fw, 'long_songs')
    probe.run()
    ui.fire('ls_poll')
    assert ui.captions(ui.top()) == ['A'] and files.files[LENGTHS] == old  # this visit's, kept in memory alone


def test_a_cue_track_s_length_is_its_own(fw, ui, song, clock_text):
    whole = song(b'/m/book.flac', b'Whole', cue=100, end=800)  # both ends: no measuring
    last = song(b'/m/book.flac', b'Last', cue=800)  # to the file's end
    tail = song(b'/m/side.flac', b'Tail', cue=1000)
    Library(fw, ui, [whole, last, tail])
    files = Files(fw)
    probe = Probe(fw, {b'/m/book.flac': 1500, b'/m/side.flac': 900})
    call(fw, 'long_songs')
    probe.run()
    ui.fire('ls_poll')
    assert ui.rows(ui.top()) == [('Whole', '11:40'), ('Last', '11:40')] or \
        ui.rows(ui.top()) == [('Last', '11:40'), ('Whole', '11:40')]  # 700 s each; Tail starts past its end
    assert sorted(probe.opened) == [b'/m/book.flac', b'/m/side.flac']  # for Last and Tail: Whole has both ends
    assert files.files[LENGTHS] == lengths((b'/m/book.flac', 1500), (b'/m/side.flac', 900))


def test_a_file_two_cue_tracks_run_to_the_end_of_is_kept_once(fw, ui, song, clock_text):
    """Two CUE tracks to one unmeasured file's end each want it measured; the lengths file keeps it once, and
    both tracks' lengths come from it."""
    early, late = song(b'/m/book.flac', b'Early', cue=300), song(b'/m/book.flac', b'Late', cue=800)
    Library(fw, ui, [late, early])
    files = Files(fw)
    probe = Probe(fw, {b'/m/book.flac': 1500})
    call(fw, 'long_songs')
    assert ui.fire('ls_poll') == REPEAT and 'Measuring songs… 0/2' in ui.shown(ui.top())  # one job per track
    probe.run()
    ui.fire('ls_poll')
    assert ui.rows(ui.top()) == [('Early', '20:00'), ('Late', '11:40')]  # 1200 s and 700 s, longest first
    assert files.files[LENGTHS] == lengths((b'/m/book.flac', 1500))  # one {key, seconds} pair


def test_song_seconds_from_the_cache(fw, song):
    Files(fw, {LENGTHS: lengths((b'/m/a.flac', 300))})
    seconds = fw.fn('song_seconds', C.c_int, C.c_void_p)
    assert seconds(song(b'/m/a.flac')) == 300 and seconds(song(b'/m/a.flac', cue=100)) == 200
    assert seconds(song(b'/m/a.flac', cue=400)) == 0 and seconds(song(b'/m/b.flac')) == -1
    assert seconds(song(b'/m/b.flac', cue=10, end=70)) == 60


# Upload Scrobbles.
class Up(C.Structure):
    _fields_ = [('lock', C.c_uint * 16), ('thread', C.c_ulong), ('running', C.c_int), ('done', C.c_int), ('ok', C.c_int),
                ('sent', C.c_int), ('cfg', (C.c_char * 256) * 5)]


class Scrobbler:
    """.scrobble.ini (a ListenBrainz token, or none, and [SCROBBLE] AUTO), Wi-Fi, and the upload's thread, which
    only starts: the test ends it."""
    def __init__(self, fw, token=b'abc', wifi=1, auto=b''):
        self.up, self.sa, self.threads = fw.var('scrobble__up', Up), fw.var('scrobble_ui__sa', Sa), []
        self.values = values = {b'TOKEN': token, b'AUTO': auto}
        def read(path, section, key, out, default, *_):
            value = values.get(C.string_at(key), b'')
            C.memmove(out, value + b'\0', len(value) + 1)
        fw.mock('toolsReadConfig', read)
        fw.mock('get_wifisignal', lambda *_: wifi)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(fn) or 0)

    def ends(self, ok, sent):
        self.up.done, self.up.ok, self.up.sent = 1, ok, sent


def test_upload_needs_wifi(fw, ui):
    scrobbler = Scrobbler(fw, wifi=0)
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Connect to Wi-Fi first'] and not scrobbler.threads and not ui.timers


def test_upload_without_an_account_fails(fw, ui):
    Scrobbler(fw, token=b'')
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Upload failed'] and not ui.timers


@pytest.mark.parametrize('ok, sent, toast, pending', [(1, 3, 'Scrobbles sent: 3', 0), (1, 0, 'Nothing to upload', 0),
                                                      (0, 2, 'Upload failed, 2 sent', 5)])
def test_upload_reports_how_it_went(fw, ui, ok, sent, toast, pending):
    scrobbler = Scrobbler(fw)
    scrobbler.sa.pending = 5
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Uploading scrobbles'] and len(scrobbler.threads) == 1
    assert [ms for _, ms in ui.pending('upload_wait')] == [500]
    assert ui.fire('upload_wait') == REPEAT and ui.toasts == ['Uploading scrobbles']  # still going
    scrobbler.ends(ok, sent)
    assert ui.fire('upload_wait') == 0 and ui.toasts[1:] == [toast]
    assert scrobbler.sa.pending == pending  # listens sent no longer count toward the automatic upload


def test_upload_while_one_runs(fw, ui):
    scrobbler = Scrobbler(fw)
    call(fw, 'upload_scrobbles')
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Uploading scrobbles', 'Already uploading'] and len(ui.pending('upload_wait')) == 1
    assert len(scrobbler.threads) == 1


def test_upload_takes_over_the_automatic_one(fw, ui):
    scrobbler = Scrobbler(fw)
    scrobbler.up.running, scrobbler.sa.running = 1, 1
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Uploading scrobbles'] and ui.pending('upload_wait') and not scrobbler.sa.running
    scrobbler.ends(1, 4)
    ui.fire('upload_wait')
    assert ui.toasts[-1] == 'Scrobbles sent: 4'


@pytest.mark.parametrize('ok, pending', [(1, 0), (0, 3)])
def test_the_automatic_upload_ends(fw, ok, pending):
    scrobbler = Scrobbler(fw)
    now = 10 ** 9
    fw.mock('time_now_ms', lambda *_: now)
    scrobbler.up.running, scrobbler.sa.running, scrobbler.sa.pending = 1, 1, 3
    auto = fw.fn('scrobble_auto', None)
    auto()
    assert scrobbler.sa.running == 1  # still uploading
    scrobbler.ends(ok, 3)
    auto()
    assert scrobbler.sa.running == 0 and scrobbler.sa.pending == pending  # sent listens no longer wait


@pytest.mark.parametrize('why', ['failed', 'off'])
def test_the_automatic_upload_tries_again_30_minutes_on(fw, why):
    """A failure tries again SA_RETRY_MIN (30) minutes on (docs/internals.md#scrobbling), and a due upload that
    found AUTO off reads the card again as late (scrobble_auto's comment); not a millisecond sooner."""
    scrobbler = Scrobbler(fw, auto=b'' if why == 'off' else b'1')
    clock = [10 ** 9]
    fw.mock('time_now_ms', lambda *_: clock[0])
    auto = fw.fn('scrobble_auto', None)
    scrobbler.sa.pending, scrobbler.sa.first_at = 3, clock[0] - 180 * 60000  # three listens, long due
    auto()
    if why == 'failed':
        assert len(scrobbler.threads) == 1
        clock[0] += 5 * 60000  # five minutes on, it fails
        scrobbler.ends(0, 0)
        auto()
    else:
        assert not scrobbler.threads
        scrobbler.values[b'AUTO'] = b'1'  # turned on since
    assert scrobbler.sa.running == 0 and scrobbler.sa.pending == 3
    since, tried = clock[0], len(scrobbler.threads)
    clock[0] = since + 30 * 60000 - 1
    auto()
    assert len(scrobbler.threads) == tried
    clock[0] = since + 30 * 60000
    auto()
    assert len(scrobbler.threads) == tried + 1 and scrobbler.sa.running == 1


def test_the_automatic_upload_starts_on_its_own(fw, ui):
    scrobbler = Scrobbler(fw, auto=b'1')
    now = 10 ** 9
    fw.mock('time_now_ms', lambda *_: now)
    scrobbler.sa.pending, scrobbler.sa.first_at = 1, now - 180 * 60000
    fw.fn('scrobble_auto', None)()
    assert scrobbler.sa.running == 1 and len(scrobbler.threads) == 1 and not ui.toasts  # silently


# Now Playing's heart and the rating picker.
class Heart:
    """Now Playing's window with stock's heart (img_fav, its click stock's My Fav toggle) and a playing library
    track, rated or not; rate_bind as playing_page_init runs it."""
    CTX = 0x5eed

    def __init__(self, fw, ui, song, mcl, kept=0, fav=False, bind=True, handler=FAV_CLICK, removing=0, table=None):
        self.fw, self.ui = fw, ui
        self.track = song(b'/m/now.flac', b'Now')
        queue = ui.deques.new([self.track])
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
        mcl.type, mcl.pos = LIBRARY, 0
        if table is None and kept:
            table = ratings((listen_key(b'/m/now.flac'), kept))
        self.files = Files(fw, {} if table is None else {RATINGS: table})
        self.fav = fav
        fw.mock('checkFavExist', lambda *_: self.fav)
        self.pressed = 1
        fw.mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        self.win = ui.new('window_create', 0, 0, 0, 0, 0)
        self.heart = ui.new('image_create', self.win, 300, 200, 50, 40)
        ui.name[self.heart] = 'img_fav'
        ui.image[self.heart] = 'play_fav' if fav else 'play_unfav'
        self.emitter, self.item = C.create_string_buffer(0x40), C.create_string_buffer(0x40)
        struct.pack_into('<Q', self.emitter, 0, C.addressof(self.item))
        struct.pack_into('<Q', self.item, 0, self.CTX)  # emitter_on_with_tag's item: {ctx, id, type, handler...}
        struct.pack_into('<i', self.item, O['EMIT_TYPE'], EVT_CLICK)
        struct.pack_into('<Q', self.item, 12, handler)
        struct.pack_into('<B', self.item, O['EMIT_PENDING_REMOVE'], removing)
        C.c_void_p.from_address(self.heart + W_EMITTER).value = C.addressof(self.emitter)
        if bind:
            fw.fn('rate_bind', None, C.c_void_p)(self.win)

    def badge(self):
        """The badge's digit, or None while it's hidden."""
        badge = [w for w in self.ui.kids[self.win] if self.ui.kind[w] == 'hscroll_label_create'][0]  # before the picker's
        return None if badge in self.ui.hidden else self.ui.text[badge]

    def panel(self):
        return self.ui.lookup(self.win, 'rate_panel', 0)

    def picker(self):
        """The picker's caption and its five hearts' images, or None while it's down."""
        panel = self.panel()
        if not panel or panel in self.ui.hidden:
            return None
        caption, *hearts = self.ui.kids[panel]
        return self.ui.text[caption], [self.ui.image.get(h) for h in hearts]

    def hold(self):
        self.ui.handler(self.heart, EVT_POINTER_DOWN)()
        return self.ui.fire('rate_long')

    def tap(self, w):
        """A press and its release on a heart of the picker, or outside it."""
        self.ui.handler(w, EVT_POINTER_DOWN)()
        return self.ui.handler(w, EVT_CLICK)()

    def key(self, key, top=None):
        return self.fw.fn('rate_key', C.c_int, C.c_void_p, C.c_uint)(top or self.win, key)

    def kept(self):
        return self.fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)(0, listen_key(b'/m/now.flac'))

    def copy_due(self):
        return self.fw.var('ratings__rc_due', C.c_int).value


def hearts(n, on='play_fav', off='play_unfav'):
    return [on] * n + [off] * (5 - n)


def test_the_heart_s_click_is_swapped_for_one_that_tells_a_hold(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl)
    assert ui.offs == [(heart.heart, EVT_CLICK, FAV_CLICK, Heart.CTX)]  # stock's own click comes off
    on = {(e, fn, ctx) for w, e, fn, ctx in ui.handlers if w == heart.heart}
    assert on == {(EVT_CLICK, ui.fn('rate_fav_click'), Heart.CTX), (EVT_POINTER_DOWN, ui.fn('rate_down'), 0)}
    assert [ms for _, ms in ui.pending('rate_tick')] == [500]


@pytest.mark.parametrize('handler, removing', [(0x52b000, 0), (FAV_CLICK, 1)])
def test_a_heart_without_stock_s_click_is_left_alone(fw, ui, song, mcl, handler, removing):
    heart = Heart(fw, ui, song, mcl, handler=handler, removing=removing)
    assert not ui.offs and not [h for h in ui.handlers if h[0] == heart.heart] and not ui.timers
    assert ui.kids[heart.win] == [heart.heart]  # no badge


@pytest.mark.parametrize('kept, fav, badge', [(3, False, '3'), (0, False, None), (3, True, None), (1, False, '1')])
def test_the_badge_shows_the_kept_hearts_on_an_empty_heart(fw, ui, song, mcl, kept, fav, badge):
    heart = Heart(fw, ui, song, mcl, kept=kept, fav=fav)
    assert heart.badge() == badge


def test_the_badge_follows_a_rating_while_lit(fw, ui, song, mcl, st):
    heart = Heart(fw, ui, song, mcl, kept=2)
    fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)(0, listen_key(b'/m/now.flac'), 4)
    assert ui.fire('rate_tick') == REPEAT and heart.badge() == '2'  # the screen is dark: nothing paints
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert ui.fire('rate_tick') == REPEAT and heart.badge() == '4'
    writes = len([c for c in fw.calls('widget_set_text_utf8')])
    ui.fire('rate_tick')
    assert len(fw.calls('widget_set_text_utf8')) == writes  # unchanged: not written again


def test_a_streamed_track_has_no_badge(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3, bind=False)
    mcl.type = 0x2000  # a stream's queue
    fw.fn('rate_bind', None, C.c_void_p)(heart.win)
    assert heart.badge() is None


def test_holding_the_heart_opens_the_picker_on_its_rating(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3)
    assert heart.picker() is None
    ui.handler(heart.heart, EVT_POINTER_DOWN)()
    assert [ms for _, ms in ui.pending('rate_long')] == [RATE_HOLD_MS]
    ui.fire('rate_long')
    assert heart.picker() == ('3 of 5', hearts(3)) and heart.panel() not in ui.dull
    # the press is aborted, so the slide_view under the heart lets go of it
    (_, (_, kind, target, *_)), = fw.calls('pointer_event_init')
    assert kind == EVT_POINTER_ABORT and target == heart.win
    assert [c[1][0] for c in fw.calls('widget_dispatch_event_to_target_recursive')] == [heart.win]
    ui.handler(heart.heart, EVT_CLICK)()  # the hold's own release: stock's toggle never runs
    assert heart.picker() == ('3 of 5', hearts(3))


def test_a_my_fav_opens_the_picker_on_five(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=2, fav=True)
    heart.hold()
    assert heart.picker() == ('5 of 5', hearts(5))


def test_a_short_press_opens_nothing(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3)
    heart.pressed = 0
    heart.hold()
    assert heart.picker() is None and not fw.calls('pointer_event_init')


def test_the_wheel_moves_the_rating_and_return_leaves_it(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3)
    heart.hold()
    seen = []
    for key in (KEY_NEXT, KEY_NEXT, KEY_NEXT, *[KEY_PREV] * 6):
        assert heart.key(key) == 1
        seen.append(heart.picker()[0])
    assert seen == ['4 of 5', '5 of 5', '5 of 5', '4 of 5', '3 of 5', '2 of 5', '1 of 5', 'Unrated', 'Unrated']
    assert heart.picker()[1] == hearts(0)
    assert heart.key(KEY_RETURN) == 1 and heart.picker() is None and heart.panel() in ui.dull
    assert heart.kept() == 3 and not heart.copy_due() and heart.badge() == '3'


@pytest.mark.parametrize('moves, kept, badge', [(1, 4, '4'), (-2, 1, '1'), (-3, 0, None)])
def test_centre_keeps_the_picked_rating(fw, ui, song, mcl, moves, kept, badge):
    heart = Heart(fw, ui, song, mcl, kept=3)
    heart.hold()
    for _ in range(abs(moves)):
        heart.key(KEY_NEXT if moves > 0 else KEY_PREV)
    assert heart.key(KEY_CENTER) == 1 and heart.picker() is None
    assert heart.kept() == kept and heart.copy_due() and heart.badge() == badge


def test_the_picker_leaves_other_keys_and_windows_alone(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3)
    assert heart.key(KEY_NEXT) == 0  # no picker
    heart.hold()
    assert heart.key(KEY_PLAY) == 0 and heart.key(KEY_NEXT, top=0x1234) == 0
    assert heart.picker() == ('3 of 5', hearts(3))


def test_a_tap_on_a_heart_keeps_that_many(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=1)
    heart.hold()
    hearts_ = ui.kids[heart.panel()][1:]
    assert heart.tap(hearts_[1]) == STOP
    assert heart.kept() == 2 and heart.picker() is None and heart.badge() == '2'


def test_the_release_of_the_hold_that_opened_the_picker_picks_nothing(fw, ui, song, mcl):
    """#303: the picker opens under the held finger, whose release chose the heart there."""
    clock = [5001]  # odd: the stamp is the time | 1
    fw.mock('time_now_ms', lambda *_: clock[0])
    heart = Heart(fw, ui, song, mcl, kept=1)
    heart.hold()
    hearts_ = ui.kids[heart.panel()][1:]
    assert ui.handler(hearts_[4], EVT_CLICK)() == STOP  # no press came down on it
    assert heart.kept() == 1 and heart.picker() == ('1 of 5', hearts(1))
    clock[0] += 399  # the same finger settling
    assert heart.tap(hearts_[4]) == STOP and heart.kept() == 1 and heart.picker() == ('1 of 5', hearts(1))
    clock[0] += 1
    assert heart.tap(hearts_[3]) == STOP and heart.kept() == 4 and heart.picker() is None
    heart.hold()
    clock[0] += 400
    heart.tap(hearts_[2])
    assert ui.handler(hearts_[1], EVT_CLICK)() == STOP and heart.kept() == 3  # one press, one tap


def test_a_tap_on_the_rating_it_has_takes_it_away(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3)
    heart.hold()
    assert heart.tap(ui.kids[heart.panel()][3]) == STOP
    assert heart.kept() == 0 and heart.picker() is None and heart.badge() is None


def test_the_picker_is_made_once_per_window(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=1)
    heart.hold()
    heart.key(KEY_RETURN)
    made = len(ui.kids[heart.win])
    heart.hold()
    assert len(ui.kids[heart.win]) == made and heart.picker() == ('1 of 5', hearts(1))


def test_classic_s_picker_counts_hearts_and_a_tap_outside_leaves_it(fw, ui, song, mcl):
    Config(fw, THEME=b'1')
    heart = Heart(fw, ui, song, mcl, kept=1)
    heart.hold()
    caption, images = heart.picker()
    on, off = images[0], images[1]  # Classic's own lit and unlit heart
    assert caption == '1 Heart' and images == hearts(1, on, off) and on != off
    assert not {on, off} & {'play_fav', 'play_unfav'}  # not Now Playing's
    heart.key(KEY_NEXT)
    assert heart.picker() == ('2 Hearts', hearts(2, on, off))
    out = ui.lookup(heart.win, 'rate_out', 0)
    assert out not in ui.hidden and out not in ui.dull  # a tap outside the panel reaches it
    assert ui.handler(out, EVT_CLICK)() == STOP and heart.picker() is not None  # the opening hold's release
    assert heart.tap(out) == STOP
    assert heart.picker() is None and heart.kept() == 1 and out in ui.hidden and out in ui.dull


def test_classic_s_hearts_are_set_only_when_they_change(fw, ui, song, mcl):
    Config(fw, THEME=b'1')
    heart = Heart(fw, ui, song, mcl, kept=2)
    heart.hold()
    sets = len(fw.calls('image_base_set_image'))
    heart.key(KEY_NEXT)  # one heart changes
    assert len(fw.calls('image_base_set_image')) == sets + 1


def test_the_picker_goes_with_its_window(fw, ui, song, mcl, st):
    heart = Heart(fw, ui, song, mcl, kept=1)
    heart.hold()
    ui.close(heart.win)
    assert not st('rate_on', C.c_void_p).value and not st('rate_win', C.c_void_p).value
    assert not ui.pending('rate_tick') and not ui.pending('rate_long')


def test_a_window_closed_mid_hold_opens_nothing(fw, ui, song, mcl, st):
    heart = Heart(fw, ui, song, mcl, kept=1)
    ui.handler(heart.heart, EVT_POINTER_DOWN)()
    ui.close(heart.win)
    assert not ui.pending('rate_long') and not st('rate_on', C.c_void_p).value


def test_another_window_s_end_leaves_the_heart(fw, ui, song, mcl, st):
    heart = Heart(fw, ui, song, mcl, kept=1)
    assert fw.fn('ratings__rate_gone', C.c_int, C.c_void_p, C.c_void_p)(0x1234, None) == 0
    assert st('rate_win', C.c_void_p).value == heart.win and ui.pending('rate_tick')


def stock_stub(address, result, log):
    """x86-64 at a fixed stock address: its first three arguments logged, result returned. Only ever in a fork."""
    claim(address)
    code = (b'\x48\xb8' + struct.pack('<Q', C.addressof(log)) + bytes.fromhex('488938 48897008 48895010') +
            b'\xb8' + struct.pack('<I', result) + b'\xc3')
    C.memmove(address, code, len(code))


@in_fork
def test_five_hearts_are_a_my_fav_through_the_heart_s_own_click(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=4)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    heart.hold()
    heart.key(KEY_NEXT)
    heart.key(KEY_CENTER)
    assert list(log[:2]) == [Heart.CTX, 0]  # stock's toggle, with its own context
    assert heart.kept() == 0 and heart.copy_due()  # 5 is My Fav's, not kept here


@in_fork
def test_fewer_hearts_on_a_my_fav_take_it_out(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, fav=True)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    heart.hold()
    heart.key(KEY_PREV)
    heart.key(KEY_CENTER)
    assert log[0] == Heart.CTX and heart.kept() == 4


@in_fork
def test_a_tap_is_stock_s_toggle_and_forgets_the_kept_hearts(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    event = C.create_string_buffer(0x40)
    ui.handler(heart.heart, EVT_CLICK)(C.addressof(event))
    assert list(log[:2]) == [Heart.CTX, C.addressof(event)]
    assert heart.kept() == 0 and heart.copy_due() and heart.badge() is None


def test_a_full_ratings_table_says_so(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, table=b''.join(struct.pack('<I', (i + 1) << 3 | 1) for i in range(RATE_SLOTS)))
    heart.hold()
    heart.key(KEY_NEXT)
    heart.key(KEY_CENTER)
    assert ui.toasts == ['Ratings are full'] and heart.kept() == 0


def test_a_rating_that_cannot_be_written_says_so(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=2)
    heart.files.fail_rename = True
    heart.hold()
    heart.key(KEY_NEXT)
    heart.key(KEY_CENTER)
    assert ui.toasts == ['Rating not saved'] and heart.kept() == 2 and heart.badge() == '2'


# An album's rating, from the queue menu of an artist's albums.
QM_ALBUM, CLASS_ARTIST_ALBUMS = 2, O['CLASS_ARTIST_ALBUMS']


def album_menu(fw, ui, song, st, album_id=0xa1b0):
    """The queue menu on row 1 of an artist's albums tab, that row's album_id album_id."""
    rows = [song(b'/m/x.flac', album=b'X'), song(b'/m/y.flac', album=b'Y')]
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = ui.deques.new(rows)
    ids = (C.c_uint * 2)(0x77, album_id)
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, CLASS_ARTIST_ALBUMS
    st('qm_rows', C.c_uint).value, st('qm_idx', C.c_uint).value = 2, 1
    st('qm_hash', C.c_uint).value = fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(rows[1])
    st('qm_browse', C.c_uint).value = fw.fn('navigation__browse_hash', C.c_uint)()
    st('art_ids', C.c_void_p).value, st('art_n', C.c_uint).value = C.addressof(ids), 2
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    return win, ids  # the caller holds ids while st.art_ids points into it


def test_an_album_s_rating_is_kept_apart_from_its_songs(fw, ui, song, st):
    files = Files(fw)
    win, ids = album_menu(fw, ui, song, st)
    show = fw.fn('rate_show', C.c_int, C.c_void_p, C.c_int, C.c_uint)
    assert show(win, 4, 0xa1b0) == 1 and show(win, 2, 0xa1b0) == 0  # one picker at a time
    key = fw.fn('rate_key', C.c_int, C.c_void_p, C.c_uint)
    key(win, KEY_NEXT)
    key(win, KEY_CENTER)
    kept = fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)
    assert kept(1, 0xa1b0) == 5 and kept(0, 0xa1b0) == 0 and RATINGS not in files.files
    assert ALBUM_RATINGS in files.files


def test_an_album_no_longer_under_the_picker_is_not_rated(fw, ui, song, st):
    files = Files(fw)
    win, ids = album_menu(fw, ui, song, st)
    fw.fn('rate_show', C.c_int, C.c_void_p, C.c_int, C.c_uint)(win, 4, 0xa1b0)
    ui.new('window_create', 0, 0, 0, 0, 0)  # another page over it
    fw.fn('rate_key', C.c_int, C.c_void_p, C.c_uint)(win, KEY_CENTER)
    assert ui.toasts == ['Album changed'] and not files.files


def test_an_album_whose_row_changed_is_not_rated(fw, ui, song, st):
    files = Files(fw)
    win, ids = album_menu(fw, ui, song, st, album_id=0xb2c0)  # the row now holds another album
    fw.fn('rate_show', C.c_int, C.c_void_p, C.c_int, C.c_uint)(win, 4, 0xa1b0)
    fw.fn('rate_key', C.c_int, C.c_void_p, C.c_uint)(win, KEY_CENTER)
    assert ui.toasts == ['Album changed'] and not files.files


# Chapters.
def ffmetadata(*chapters):
    """ffmpeg's -f ffmetadata output: (start seconds, title) chapters, an empty title untitled."""
    rows = b''.join(b'[CHAPTER]\nTIMEBASE=1/1000\nSTART=%d\nEND=%d\n%s' % (s * 1000, s * 1000 + 1, b'title=%s\n' % t if t else b'')
                    for s, t in chapters)
    return b';FFMETADATA1\ntitle=The Book\n' + rows


class Book:
    """ffmpeg behind chapters.c's pipe2 and fork: its output in one read, then its exit; the playing track,
    the player's place in it, and its seeks."""
    def __init__(self, fw, ui, song, mcl, path=b'/mnt/mmc/Audiobooks/book.m4b', output=b'', status=0, cue=0, end=0,
                 files=None):
        self.fw, self.ui, self.output, self.status, self.chunks = fw, ui, output, status, [output]
        self.killed, self.waits, self.closed = [], [], []
        self.errno = C.c_int(0)
        def pipe2(p, flags, *_):
            C.cast(p, C.POINTER(C.c_int))[0], C.cast(p, C.POINTER(C.c_int))[1] = 5, 6
        def read(fd, buf, n, *_):
            if not self.chunks:
                return 0
            chunk = self.chunks.pop(0)
            C.memmove(buf, chunk, len(chunk))
            return len(chunk)
        def waitpid(pid, status, options, *_):
            self.waits.append(options)
            C.cast(status, C.POINTER(C.c_int))[0] = self.status
            return 123
        fw.mock('chapter_pipe2', pipe2)
        fw.mock('fw_fork', lambda *_: 123)
        fw.mock('fw_read', read)
        fw.mock('fw_waitpid', waitpid)
        fw.mock('fw_close', lambda fd, *_: self.closed.append(fd))
        fw.mock('chapter_kill', lambda pid, sig, *_: self.killed.append((pid, sig)))
        fw.mock('fw___errno_location', lambda *_: C.addressof(self.errno))
        files = files or {path: b'\0' * 64}  # no moov: no index to fit
        self.files = Files(fw, {k.decode() if isinstance(k, bytes) else k: v for k, v in files.items()})
        self.track = song(path, b'Book', cue=cue, end=end)
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([self.track])
        mcl.type, mcl.pos = LIBRARY, 0
        self.at, self.state = 0, 2
        fw.mock('player_playtime_and_length', lambda sec, total, *_: C.c_int.from_address(sec).__setattr__('value', self.at))
        fw.mock('mclGetPlayStatus', lambda *_: self.state)

    def open(self):
        self.fw.fn('navigation__chapters_open', None, C.c_void_p)(self.track)
        return self.ui.top() if self.ui.windows else None

    def selected(self, page):
        return self.ui.props.get((self.ui.view(page), '_ringnav_index'))


def test_chapters_load_then_list_with_their_times(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'Opening'), (754, b''), (3725, b'Caf\xc3\xa9')))
    book.at = 800
    page = book.open()
    assert ui.name[page] == 'chapters_page' and 'Loading chapters…' in ui.shown(page)
    assert [ms for _, ms in ui.pending('chapter_poll')] == [50]
    assert ui.fire('chapter_poll') == 0 and not ui.pending('chapter_poll')
    assert ui.rows(page) == [('Opening', '0:00:00'), ('Chapter 2', '0:12:34'), ('Café', '1:02:05')]
    assert book.selected(page) == 1  # the chapter playing


@pytest.mark.parametrize('at, playing', [(0, 0), (753, 0), (754, 1), (3724, 1), (3725, 2), (9999, 2)])
def test_chapters_open_on_the_one_playing(fw, ui, song, mcl, at, playing):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One'), (754, b'Two'), (3725, b'Three')))
    book.at = at
    page = book.open()
    ui.fire('chapter_poll')
    assert book.selected(page) == playing


def test_chapters_poll_until_ffmpeg_ends(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    book.chunks = []  # nothing written yet, and the pipe still open
    fw.mock('fw_read', lambda *_: (book.errno.__setattr__('value', 11), -1)[1])  # EAGAIN
    fw.mock('fw_waitpid', lambda *_: 0)  # still running
    page = book.open()
    assert ui.fire('chapter_poll') == REPEAT and 'Loading chapters…' in ui.shown(page)


@pytest.mark.parametrize('output, status, message', [(b';FFMETADATA1\ntitle=x\n', 0, 'No embedded chapters'),
                                                     (b'', 256, 'Could not read chapters')])
def test_chapters_none_or_unreadable_say_so(fw, ui, song, mcl, output, status, message):
    book = Book(fw, ui, song, mcl, output=output, status=status)
    page = book.open()
    ui.fire('chapter_poll')
    assert message in ui.shown(page) and ui.rows(page) == []


def test_a_cue_track_s_chapters_are_on_its_own_clock(fw, ui, song, mcl):
    chapters = [(0, b'Intro'), (300, b'A'), (900, b''), (1500, b'C'), (1800, b'D'), (2400, b'E')]
    book = Book(fw, ui, song, mcl, output=ffmetadata(*chapters), cue=600, end=1800)
    page = book.open()
    ui.fire('chapter_poll')
    # the one playing at its start shows at 0:00, earlier ones go, and so do those from its end on
    assert ui.rows(page) == [('A', '0:00:00'), ('Chapter 3', '0:05:00'), ('C', '0:15:00')]


def test_a_spoken_mp3_opens_chapters(fw, ui, song, mcl):
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    page = Book(fw, ui, song, mcl, path=b'/mnt/mmc/Podcasts/show.mp3', output=ffmetadata((0, b'One'))).open()
    ui.fire('chapter_poll')
    assert ui.rows(page) == [('One', '0:00:00')]


def test_a_song_has_no_chapters(fw, ui, song, mcl):
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    assert Book(fw, ui, song, mcl, path=b'/mnt/mmc/Music/song.mp3').open() is None and not fw.calls('fw_fork')


def box(kind, payload):
    return struct.pack('>I', 8 + len(payload)) + kind + payload


def test_a_book_too_long_for_ram_says_so(fw, ui, song, mcl):
    samples = 2 ** 20  # 28 MB of index against 20 MB free
    stsz = box(b'stsz', struct.pack('>3I', 0, 0, samples))
    mp4 = box(b'moov', box(b'trak', box(b'mdia', box(b'minf', box(b'stbl', stsz)))))
    path = b'/mnt/mmc/Audiobooks/long.m4b'
    book = Book(fw, ui, song, mcl, path=path, files={path: mp4, '/proc/meminfo': b'MemAvailable:   20000 kB\n'})
    assert book.open() is None and ui.toasts == ['Too long to read chapters on the Q2'] and not fw.calls('fw_fork')


def test_chapters_open_once(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    book.open()
    book.open()
    assert len(ui.windows) == 1 and len(fw.calls('fw_fork')) == 1


def test_chapters_without_a_page_reap_ffmpeg(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    fw.mock('window_create', lambda *_: 0)
    assert book.open() is None
    assert book.killed == [(123, 9)] and book.waits == [0]  # killed, then waited for: no zombie
    assert not st('ch', C.c_void_p).value and not ui.pending('chapter_poll')


def test_chapters_without_a_poll_close_their_page_and_reap_ffmpeg(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    fw.mock('timer_add', lambda *_: 0)  # no timer to be had
    book.open()
    assert ui.backs == 1 and book.killed == [(123, 9)] and book.waits == [0] and not st('ch', C.c_void_p).value


def test_chapters_closed_while_loading_end_ffmpeg_and_free_on_the_next_poll(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    ui.close(book.open())
    assert book.killed == [(123, 9)] and st('ch', C.c_void_p).value  # the poll still owns it
    assert ui.fire('chapter_poll') == 0 and not st('ch', C.c_void_p).value
    book.open()
    assert len(ui.windows) == 1  # and it opens again


def test_chapters_closed_once_loaded_free_at_once(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    page = book.open()
    ui.fire('chapter_poll')
    ui.close(page)
    assert not book.killed and not st('ch', C.c_void_p).value


@pytest.mark.parametrize('state', [2, 3])
def test_a_chapter_row_seeks_there_and_goes_back(fw, ui, song, mcl, st, state):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One'), (95, b'Two')))
    book.state = state
    page = book.open()
    ui.fire('chapter_poll')
    ui.click(page, 1)
    assert ui.backs == 1 and st('scrub_to').value == 95
    if state == 2:  # playing: the player's own seek
        assert [c[1][0] for c in fw.calls('player_seek_time')] == [95]
    else:  # paused: the file's second, waiting for Play
        assert [c[1][0] for c in fw.calls('mclSetSeek')] == [95]


def test_a_cue_track_s_chapter_seeks_in_the_file(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'A'), (700, b'B')), cue=600)
    book.state = 3
    page = book.open()
    ui.fire('chapter_poll')
    ui.click(page, 1)  # B, 100 s into the track
    assert [c[1][0] for c in fw.calls('mclSetSeek')] == [700]


@pytest.mark.parametrize('change', ['stopped', 'track'])
def test_a_chapter_row_only_goes_back_once_the_book_is_not_playing(fw, ui, song, mcl, change):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One'), (95, b'Two')))
    page = book.open()
    ui.fire('chapter_poll')
    if change == 'stopped':
        book.state = 1
    else:
        mcl.pos = 1  # another track on
        ui.deques[fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value].append(song(b'/m/next.flac'))
    ui.click(page, 1)
    assert ui.backs == 1 and not fw.calls('player_seek_time') and not fw.calls('mclSetSeek')


# Tidal Downloads, and a card app's local songs.
class Card(Files):
    """The card: files with their mtimes, its listings as stock's toolsLoadDirectory gives them (audio files, and
    a folder in Tidal's), and access. Its records come from the test's own song, so none shares another's room."""
    def __init__(self, fw, ui, song, songs):
        super().__init__(fw, {p.decode(): b'x' for p in songs})
        self.fw, self.ui, self.mtime = fw, ui, dict(songs)
        self.records = {p: song(p) for p in songs if p.endswith((b'.flac', b'.m4a'))}
        self.folder = song(b'/mnt/mmc/.tidal/old', kind=4)
        fw.mock('toolsLoadDirectory', self.listing)
        fw.mock('fw___xstat', self.stat)
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p).decode() in self.files else -1)
        self.ents, self.dirs = [], {}
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d) and 0)

    def listing(self, path, *_):
        path = C.string_at(path)
        rows = [r for p, r in self.records.items() if (p.rsplit(b'/', 1)[0] or b'/') == path and p.decode() in self.files]
        rows += [self.folder] if path == TIDAL_DIR.encode() else []
        self.ui.deques[self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value].extend(rows)
        return len(rows)

    def stat(self, version, path, info, *_):
        path = C.string_at(path)
        if path not in self.mtime or path.decode() not in self.files:
            return -1
        C.c_uint.from_address(info + 64).value = self.mtime[path]
        return 0

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        names = [p[len(path) + 1:] for p in self.files if p.startswith(path + '/')]
        self.dirs[0x3100 + len(self.ents)] = names
        return 0x3100 + len(self.ents)

    def readdir(self, d, *_):
        if not self.dirs[d]:
            return 0
        ent = C.create_string_buffer(268)
        name = self.dirs[d].pop(0).encode()
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.ents.append(ent)
        return C.addressof(ent)

    def paths(self, queue):
        return [field(r, 'path') for r in queue]


TIDAL_SONGS = {b'/mnt/mmc/.tidal/111-LOSSLESS Old One.flac': 100, b'/mnt/mmc/.tidal/222-HI_RES New One.flac': 300,
               b'/mnt/mmc/.tidal/333-HIGH Middle.m4a': 200}


def test_tidal_downloads_lists_newest_first_then_clear(fw, ui, song):
    card = Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    assert ui.name[page] == 'tidal_downloads'
    assert ui.captions(page) == ['New One', 'Middle', 'Old One', 'Clear All Downloads…']  # no folder
    ui.click(page, 1)
    (queue, at, cls), = ui.played
    assert card.paths(queue) == sorted(TIDAL_SONGS, key=lambda p: -TIDAL_SONGS[p]) and at == 1 and cls == LIBRARY


def test_a_download_gone_from_the_card_does_not_play(fw, ui, song):
    card = Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    del card.files['/mnt/mmc/.tidal/333-HIGH Middle.m4a']
    ui.click(ui.top(), 1)
    assert not ui.played


def test_no_downloads_says_so(fw, ui, song):
    Card(fw, ui, song, {})
    call(fw, 'tidal_downloads')
    assert 'No downloads yet' in ui.shown(ui.top()) and ui.rows(ui.top()) == []


def test_clear_all_asks_first_and_cancel_keeps_them(fw, ui, song):
    card = Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    ui.click(page, 3)
    ui.fire('td_show')  # from a timer: the click's own list isn't rebuilt under it
    assert ui.rows(page) == [('3 tracks', 'Keeps settings'), ('Cancel', ''), ('Remove', '')]
    assert ui.props[(ui.view(page), '_ringnav_index')] == 1  # on Cancel
    ui.click(page, 1)
    ui.fire('td_show')
    assert ui.captions(page)[-1] == 'Clear All Downloads…' and len(card.files) == 3
    assert ui.props[(ui.view(page), '_ringnav_index')] == 3  # back on Clear


def test_return_on_the_question_is_cancel(fw, ui, song):
    Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    ui.click(page, 3)
    ui.fire('td_show')
    assert ui.key(page, KEY_RETURN) == STOP and ui.backs == 0
    ui.fire('td_show')
    assert len(ui.rows(page)) == 4
    assert ui.key(page, KEY_RETURN) == STOP and ui.backs == 1  # on the list it leaves


def test_remove_clears_every_download(fw, ui, song):
    card = Card(fw, ui, song, {**TIDAL_SONGS, b'/mnt/mmc/.tidal/.cache-size': 1})
    call(fw, 'tidal_downloads')
    page = ui.top()
    ui.click(page, 3)
    ui.fire('td_show')
    ui.click(page, 2)
    ui.fire('td_show')
    assert list(card.files) == ['/mnt/mmc/.tidal/.cache-size']  # Keeps settings
    assert 'No downloads yet' in ui.shown(page)


def test_one_download_is_a_track(fw, ui, song):
    Card(fw, ui, song, {b'/mnt/mmc/.tidal/1-HIGH A.flac': 5})
    call(fw, 'tidal_downloads')
    ui.click(ui.top(), 1)
    ui.fire('td_show')
    assert ui.rows(ui.top())[0] == ('1 track', 'Keeps settings')


def test_leaving_the_page_drops_the_question(fw, ui, lists, song):
    Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    ui.click(page, 3)
    ui.close(page)
    assert not ui.pending('td_show') and not lists('td_asking').value and not lists('td_list', C.c_void_p).value
    call(fw, 'tidal_downloads')
    assert ui.captions(ui.top())[-1] == 'Clear All Downloads…'


APP = b'/mnt/mmc/Deezer/Artist/Album'
APP_SONGS = {APP + b'/02 - Second [22].flac': 300, APP + b'/01 - First [11].flac': 100, APP + b'/03 - Third.flac': 200}


def test_a_card_app_s_album_lists_in_track_order(fw, ui, song):
    card = Card(fw, ui, song, APP_SONGS)
    assert fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/', b'Album', 1) == 0
    page = ui.top()
    assert ui.name[page] == 'app_songs' and ui.captions(page) == ['01 - First', '02 - Second', '03 - Third']
    ui.click(page, 2)
    assert card.paths(ui.played[0][0]) == sorted(APP_SONGS) and ui.played[0][1:] == (2, LIBRARY)


def test_a_card_app_s_folder_lists_newest_first(fw, ui, song):
    Card(fw, ui, song, APP_SONGS)
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/', b'Downloads', 0)
    assert ui.captions(ui.top()) == ['02 - Second', '03 - Third', '01 - First']


def test_a_card_app_s_song_plays_its_folder_from_it(fw, ui, song):
    card = Card(fw, ui, song, APP_SONGS)
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/03 - Third.flac', b'', 1)
    (queue, at, cls), = ui.played
    assert card.paths(queue) == sorted(APP_SONGS) and at == 2 and cls == LIBRARY and not ui.windows


def test_a_card_app_s_song_no_longer_there_says_so(fw, ui, song):
    Card(fw, ui, song, APP_SONGS)
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/04 - Gone.flac', b'', 1)
    assert ui.toasts == ["It isn't on the card any more"] and not ui.played


@pytest.mark.parametrize('path', [b'', b'song.flac', b'/song.flac', b'/m/' + b'x' * 300])
def test_a_card_app_s_odd_path_opens_nothing(fw, ui, song, path):
    Card(fw, ui, song, {})
    assert fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(path, b'', 1) == 0
    assert not ui.windows and not ui.played and not ui.toasts


def test_the_card_root_lists_as_a_folder(fw, ui, song):
    Card(fw, ui, song, {b'/a.flac': 1})
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(b'/', b'Card', 0)
    assert ui.captions(ui.top()) == ['a']


def test_downloads_open_once(fw, ui, song):
    Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/', b'Album', 1)
    assert len(ui.windows) == 1


# The card as a tree of folders, for the pages that look at it.
class Disk(Files):
    """Files by path (a folder is what holds one, or one of folders); opendir lists a folder's names, a folder's
    with d_type 4, and access finds either."""
    def __init__(self, fw, files=None, folders=()):
        super().__init__(fw, dict(files or {}))
        self.folders, self.ents, self.dirs = set(folders), [], {}
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d) and 0)
        fw.mock('fw_access', lambda p, *_: 0 if self.exists(C.string_at(p).decode()) else -1)

    def folder(self, path):
        return path in self.folders or any(p.startswith(path + '/') for p in [*self.files, *self.folders])

    def exists(self, path):
        return path in self.files or self.folder(path)

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        if not self.folder(path):
            return 0
        names = sorted({p[len(path) + 1:].split('/')[0] for p in [*self.files, *self.folders] if p.startswith(path + '/')})
        handle = 0x3100 + len(self.ents) + len(self.dirs)
        self.dirs[handle] = [(n, 4 if self.folder(f'{path}/{n}') else 8) for n in names]
        return handle

    def readdir(self, d, *_):
        if not self.dirs[d]:
            return 0
        name, kind = self.dirs[d].pop(0)
        ent = C.create_string_buffer(268)
        struct.pack_into('<B', ent, 10, kind)
        C.memmove(C.addressof(ent) + 11, name.encode(), len(name))
        self.ents.append(ent)
        return C.addressof(ent)


# Streaming's Configuration.
def configuration(fw, ui, files=None, hide=None):
    disk = Disk(fw, files)
    config = Config(fw, **({} if hide is None else {'STREAMHIDE': hide}))
    call(fw, 'stream_config')
    return ui.top(), disk, config, config.written


def switches(ui, page):
    """The service rows' captions and switches."""
    out = []
    for item in ui.kids[ui.view(page)][5:]:
        label, switch = ui.kids[item][0], ui.lookup(item, '_setting_switch', 0)
        out.append((ui.text[label], ui.image[switch] == 'switch_on'))
    return out


SERVICES = ['TIDAL', 'Spotify', 'Tidal Downloads', 'Internet Radio', 'AirPlay']


def test_configuration_lists_the_caches_erase_stop_and_each_service(fw, ui):
    page, *_ = configuration(fw, ui, {f'{SPOT_DIR}/.cache-size': b'1024M'}, hide=b'RADIO')
    assert ui.name[page] == 'streamconf_page'
    assert ui.captions(page)[:5] == ['Tidal Cache: 2 GB', 'Spotify Cache: 1 GB', 'Erase Spotify Data', 'Stop Streaming Services', None]
    assert switches(ui, page) == [(s, s != 'Internet Radio') for s in SERVICES]
    call(fw, 'stream_config')
    assert ui.windows == [page]


@pytest.mark.parametrize('saved, shown', [(b'512M', '512 MB'), (b'8192M', '8 GB'), (b'3000M', '2 GB'), (b'', '2 GB')])
def test_a_cache_size_reads_back(fw, ui, saved, shown):
    page, *_ = configuration(fw, ui, {f'{TIDAL_DIR}/.cache-size': saved})
    assert ui.captions(page)[0] == f'Tidal Cache: {shown}'


@pytest.mark.parametrize('row, folder, saved, after, shown', [
    (0, TIDAL_DIR, None, b'4096M', '4 GB'), (1, SPOT_DIR, b'4096M', b'8192M', '8 GB'), (0, TIDAL_DIR, b'8192M', b'512M', '512 MB')])
def test_a_cache_row_steps_to_the_next_size_and_keeps_it(fw, ui, row, folder, saved, after, shown):
    page, disk, *_ = configuration(fw, ui, {} if saved is None else {f'{folder}/.cache-size': saved})
    ui.click(page, row)
    assert disk.files[f'{folder}/.cache-size'] == after  # librespot's --cache-size-limit form
    assert ui.captions(page)[row].endswith(shown) and [C.string_at(c[1][0]).decode() for c in fw.calls('fw_mkdir')] == [folder]


def test_a_cache_size_that_cannot_be_kept_says_so(fw, ui):
    page, disk, *_ = configuration(fw, ui)
    disk.fail_rename = True
    ui.click(page, 0)
    assert ui.toasts == ['Could not save setting'] and ui.captions(page)[0] == 'Tidal Cache: 2 GB'


def erasing(fw):
    """netupdate.c's erase: its thread only starts."""
    fw.mock('fw_pthread_create', lambda *_: 0)


def test_erase_wants_a_second_press(fw, ui):
    erasing(fw)
    page, *_ = configuration(fw, ui)
    ui.click(page, 2)
    assert ui.captions(page)[2] == 'Press again to erase the login and cache' and not fw.calls('fw_system')
    ui.click(page, 2)
    assert ui.captions(page)[2] == 'Erasing…' and fw.calls('fw_system')  # librespot stopped first


def test_another_row_drops_the_first_press(fw, ui):
    page, *_ = configuration(fw, ui)
    ui.click(page, 2)
    ui.click(page, 0)
    assert ui.captions(page)[2] == 'Erase Spotify Data'
    ui.click(page, 2)
    assert ui.captions(page)[2] == 'Press again to erase the login and cache'


def test_leaving_drops_the_first_press(fw, ui, st):
    page, *_ = configuration(fw, ui)
    ui.click(page, 2)
    ui.close(page)
    assert not st('sc_armed').value and not st('sc_page', C.c_void_p).value
    page, *_ = configuration(fw, ui)
    assert ui.captions(page)[2] == 'Erase Spotify Data'


def test_while_erasing_spotify_s_cache_waits(fw, ui):
    erasing(fw)
    page, disk, *_ = configuration(fw, ui)
    ui.click(page, 2), ui.click(page, 2)
    ui.click(page, 1)
    assert ui.toasts == ['Wait for the one in progress'] and f'{SPOT_DIR}/.cache-size' not in disk.files
    ui.click(page, 2)
    assert ui.toasts[-1] == 'Wait for the one in progress' and ui.captions(page)[2] == 'Erasing…'


def test_an_erase_s_end_shows_on_the_page(fw, ui):
    page, disk, *_ = configuration(fw, ui, {f'{SPOT_DIR}/.cache-size': b'1024M'})
    ui.click(page, 2)
    del disk.files[f'{SPOT_DIR}/.cache-size']  # netupdate.c's erase took it, and calls back
    fw.fn('stream_erased', None)()
    assert ui.captions(page)[1:3] == ['Spotify Cache: 2 GB', 'Press again to erase the login and cache']


def test_an_erase_s_end_with_the_page_gone_writes_nothing(fw, ui):
    page, *_ = configuration(fw, ui)
    ui.close(page)
    writes = len(fw.calls('widget_set_text_utf8'))
    fw.fn('stream_erased', None)()
    assert len(fw.calls('widget_set_text_utf8')) == writes


@pytest.mark.parametrize('kind, status, stops', [(0x2000, 2, 1), (0x2000, 1, 0), (LIBRARY, 2, 0), (FOLDER, 2, 0)])
def test_stop_streaming_ends_streams_and_leaves_local_music(fw, ui, mcl, kind, status, stops):
    page, *_ = configuration(fw, ui)
    mcl.type = kind
    fw.mock('mclGetPlayStatus', lambda *_: status)
    ui.click(page, 3)
    assert ui.toasts == ['Streaming stopped'] and len(fw.calls('player_stop')) == stops
    assert fw.calls('fw_system')  # librespot's loop ended whatever played


def test_a_service_row_hides_it(fw, ui):
    page, _, config, written = configuration(fw, ui, hide=b'TIDAL')
    ui.click(page, 5 + 3)
    assert written == [b'TIDAL,RADIO'] and switches(ui, page)[3] == ('Internet Radio', False)
    ui.click(page, 5)
    assert written[-1] == b'RADIO' and switches(ui, page)[0] == ('TIDAL', True)


# Stock's pages the payload rearranges: Streaming, the Library and About.
def stock_row(ui, view, caption, image=None, name=None):
    """A stock list row: a list_item holding a button with its icon and its label."""
    item = ui.new('list_item_create', view, 0, 0, 375, 70)
    button = ui.new('button_create', item, 20, 0, 335, 70)
    if image:
        ui.image[ui.new('image_create', button, 10, 0, 52, 70)] = image
    ui.text[ui.new('hscroll_label_create', button, 72, 0, 260, 70)] = caption
    if name:
        ui.name[button] = name
    return item


def row_text(ui, item):
    return next((ui.text[w] for w in ui.subtree(item) if w in ui.text), None)


def stock_page(ui, view_name, *rows):
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    view = ui.new('scroll_view_create', win, 0, 0, 375, 320)
    ui.name[view] = view_name
    for row in rows:
        stock_row(ui, view, *row)
    return win, view


def page_rows(ui, view):
    """The view's rows in order: (caption, shown)."""
    return [(row_text(ui, item), item not in ui.hidden) for item in ui.kids[view]]


STREAM_STOCK = [('TIDAL', 'list_tidal'), ('Baidu Netdisk', 'list_baidu'), ('Qobuz', 'list_qobuz')]


def streaming(fw, ui, hide=None, files=None):
    Disk(fw, files)
    Config(fw, **({} if hide is None else {'STREAMHIDE': hide}))
    fw.mock('stock_stream_trampoline', lambda *_: 7)
    win, view = stock_page(ui, 'scroll_view_streamsset', *STREAM_STOCK)
    assert fw.fn('ringnav_stream', C.c_int, C.c_void_p, C.c_void_p)(win, None) == 7  # stock's own result
    return win, view


def deezer(name=b'Deezer'):
    return {'/mnt/mmc/.deez/q2app': b'name=%s\n' % name, '/mnt/mmc/.deez/q2-deez': b'ELF'}


def test_streaming_drops_baidu_and_adds_the_payload_s_services(fw, ui):
    win, view = streaming(fw, ui)
    assert page_rows(ui, view) == [(c, True) for c in ('TIDAL', 'Qobuz', 'Spotify', 'Tidal Downloads', 'Internet Radio',
                                                       'AirPlay', 'Configuration')]


def test_streaming_lists_the_card_s_apps_after_spotify(fw, ui):
    win, view = streaming(fw, ui, files=deezer())
    assert [c for c, _ in page_rows(ui, view)][2:5] == ['Spotify', 'Deezer', 'Tidal Downloads']


def test_streaming_hides_what_configuration_hid(fw, ui):
    win, view = streaming(fw, ui, hide=b'TIDAL,deez,AIRPLAY', files=deezer())
    assert [c for c, shown in page_rows(ui, view) if not shown] == ['TIDAL', 'Deezer', 'AirPlay']


def test_streaming_s_airplay_opens_its_switch(fw, ui):
    win, view = streaming(fw, ui)
    item = ui.kids[view][-2]
    button, = ui.kids[item]
    ui.handler(button, EVT_CLICK)()
    assert ui.went == ['systemset/netservice_page']


def test_a_service_hidden_on_configuration_goes_from_streaming_at_once(fw, ui):
    win, view = streaming(fw, ui, hide=b'')
    button, = ui.kids[ui.kids[view][-1]]
    ui.handler(button, EVT_CLICK)()  # Configuration
    config = ui.top()
    assert ui.name[config] == 'streamconf_page'
    ui.click(config, 5 + 3)  # Internet Radio's switch
    assert dict(page_rows(ui, view))['Internet Radio'] is False
    ui.click(config, 5 + 3)
    assert dict(page_rows(ui, view))['Internet Radio'] is True
    ui.click(config, 5 + 0)  # stock's own TIDAL row too
    assert dict(page_rows(ui, view))['TIDAL'] is False


def test_streaming_without_its_list_is_stock_s(fw, ui):
    Disk(fw)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    fw.fn('ringnav_stream', C.c_int, C.c_void_p, C.c_void_p)(win, None)
    assert ui.kids[win] == [] and not fw.calls('fw_opendir')


BLOCKS = {'/mnt/mmc/.blocks/q2app': b'name=Blocks\ngame=1\n', '/mnt/mmc/.blocks/q2-blocks': b'ELF'}  # a card game


def home_pane(fw):
    """home_pane_read's services, games and the card's free tenths of a GB."""
    out = (C.c_int * 3)()
    fw.fn('home_pane_read', None, C.c_void_p, C.c_void_p, C.c_void_p)(C.byref(out, 0), C.byref(out, 4), C.byref(out, 8))
    return list(out)


@pytest.mark.parametrize('hide, services', [(b'', 6), (b'RADIO,deez', 4)])
def test_home_counts_the_shown_services(fw, hide, services):
    Disk(fw, {**deezer(), **BLOCKS})
    Config(fw, STREAMHIDE=hide)
    assert home_pane(fw)[0] == services  # less Configuration; a card game is in Games, not Streaming


def test_home_counts_the_games_page_s_rows(fw, ui):
    """Home's Games caption is what Games lists: its own games and the card's, one fewer once a card game goes."""
    disk = Disk(fw, {**deezer(), **BLOCKS})
    Config(fw)
    fw.fn('games_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    rows = ui.captions(ui.top())
    assert rows[-1] == 'Blocks' and 'Deezer' not in rows and home_pane(fw)[1] == len(rows)
    ui.close(ui.top())
    for path in BLOCKS:
        del disk.files[path]
    assert home_pane(fw)[1] == len(rows) - 1


def statfs(fw, device=(1, 2), bsize=4096, bavail=0):
    """stat's st_dev (word 0) of /mnt/mmc and /mnt, and statfs's f_bsize (word 1) and f_bavail (word 7), MIPS o32."""
    def stat(version, path, info, *_):
        C.c_uint.from_address(info).value = device[C.string_at(path) == b'/mnt']
        return 0
    def fs(path, out, *_):
        C.c_uint.from_address(out + 4).value, C.c_uint.from_address(out + 28).value = bsize, bavail
        return 0
    fw.mock('fw___xstat', stat)
    fw.mock('fw_statfs', fs)


@pytest.mark.parametrize('device, bsize, bavail, tenths', [
    ((1, 2), 4096, 5 * 2 ** 18, 50),  # 5 GiB free
    ((1, 2), 32768, 54931640, 16763),  # a 2 TB card's 1.8 TB: 1676.3 GiB
    ((1, 2), 512, 209714, 0), ((1, 2), 512, 209715, 1),  # a tenth of a GiB in 512-byte blocks
    ((2, 2), 4096, 5 * 2 ** 18, 0),  # /mnt/mmc on /mnt's own device: no card mounted
])
def test_home_shows_the_card_s_free_space_in_tenths_of_a_gb(fw, device, bsize, bavail, tenths):
    Disk(fw)
    Config(fw)
    statfs(fw, device, bsize, bavail)
    assert home_pane(fw)[2] == tenths


LIBRARY_STOCK = ['Update Local Music', 'All Songs', 'Album', 'Artist', 'Genre', 'Hi-Res', 'My Fav', 'Frequent', 'Recent',
                 'Recently Added', 'Playlist']


def library(fw, ui, folders=(), token=b'', extra=0):
    Disk(fw, folders=folders)
    Config(fw, TOKEN=token)
    fw.mock('stock_localmusic_trampoline', lambda *_: 9)
    win, view = stock_page(ui, 'scroll_view_localmusic', *[(c, None, str(i)) for i, c in enumerate(LIBRARY_STOCK)])
    for i in range(extra):
        stock_row(ui, view, 'more')
    assert fw.fn('ringnav_localmusic', C.c_int, C.c_void_p, C.c_void_p)(win, None) == 9
    return win, view


def boot(fw, st, theme=None):
    """config.ini's THEME (None: never set, Minimal) and the geometry set theme_boot writes for it (st.geom)."""
    Config(fw, **({} if theme is None else {'THEME': theme}))
    st('geom').value = fw.fn('theme_geom', C.c_int, C.c_void_p)(fw.fn('theme', C.c_void_p)())


# Minimal's set has Library icons; Classic's has none, so its Library leads with Search (library_search).
LEADS = [(None, []), (b'1', ['Search'])]


@pytest.mark.parametrize('theme, lead', LEADS)
def test_the_library_s_rows_in_their_order(fw, ui, st, theme, lead):
    boot(fw, st, theme)
    win, view = library(fw, ui)
    # My Fav and Frequent hidden, kept in the page for stock's index: Ratings and Most Played list theirs
    assert page_rows(ui, view) == [(c, c not in ('My Fav', 'Frequent')) for c in lead + [
        'Shuffle', 'Artist', 'Album', 'All Songs', 'Genre', 'Era', 'Playlist', 'Ratings', 'My Fav', 'Recently Added', 'Recent',
        'Most Played', 'Frequent', 'Hi-Res', 'Long Songs', 'Games', 'Update Local Music']]


def test_the_library_adds_the_card_s_media_folders_and_upload(fw, ui):
    win, view = library(fw, ui, folders=['/mnt/mmc/podcasts', '/mnt/mmc/VIDEOS', '/mnt/mmc/Music'], token=b'x')
    rows = [c for c, _ in page_rows(ui, view)]
    assert rows[rows.index('Long Songs'):] == ['Long Songs', 'Podcasts', 'Videos', 'Games', 'Upload Scrobbles',
                                               'Update Local Music']


def test_the_library_s_added_rows_have_no_name_for_stock_s_click(fw, ui):
    win, view = library(fw, ui)
    stock = {ui.kids[item][0] for item in ui.kids[view] if row_text(ui, item) in LIBRARY_STOCK}
    added = [ui.kids[item][0] for item in ui.kids[view] if row_text(ui, item) not in LIBRARY_STOCK]
    assert all(b in ui.name for b in stock) and not any(b in ui.name for b in added)
    clicks = {row_text(ui, item): fn for item in ui.kids[view] for w, e, fn, _ in ui.handlers
              if w == ui.kids[item][0] and e == EVT_CLICK}
    assert clicks['Shuffle'] == ui.fn('shuffle_menu') and clicks['Most Played'] == ui.fn('most_played')
    assert clicks['Ratings'] == ui.fn('ratings_menu') and clicks['Long Songs'] == ui.fn('long_songs')
    assert clicks['Era'] == ui.fn('era_menu')


def test_a_library_that_is_not_stock_s_is_left(fw, ui):
    win, view = library(fw, ui, extra=1)
    assert [c for c, _ in page_rows(ui, view)] == LIBRARY_STOCK + ['more']


def test_classic_s_library_leads_with_search_and_drops_its_icons(fw, ui, st):
    boot(fw, st, b'1')
    assert fw.fn('library_search', C.c_int)() == 1  # Classic's set has no Library icons
    win, view = library(fw, ui)
    assert page_rows(ui, view)[:2] == [('Search', True), ('Shuffle', True)]
    images = [w for item in ui.kids[view] for w in ui.subtree(item) if ui.kind[w] == 'image_create']
    icons = [w for w in images if ui.image[w] != 'list_into']
    assert icons and all(w in ui.hidden for w in icons)
    assert not [w for w in images if ui.image[w] == 'list_into' and w in ui.hidden]  # the chevrons stay
    search, = [ui.kids[item][0] for item in ui.kids[view] if row_text(ui, item) == 'Search']
    assert [fn for w, e, fn, _ in ui.handlers if w == search and e == EVT_CLICK] == [C.cast(fw.lib.stock_search, C.c_void_p).value]


@pytest.mark.parametrize('theme, lead', LEADS)
@in_fork
def test_home_s_library_list_is_the_library_s_rows(fw, ui, st, theme, lead):
    log = (C.c_long * 3)()
    stock_stub(O['LOCALMUSIC_BG'], 0, log)
    boot(fw, st, theme)
    _, page = library(fw, ui)  # the Library page's own rows
    win, view = stock_page(ui, 'scroll_view_localmusic', *[(c, None, str(i)) for i, c in enumerate(LIBRARY_STOCK)])
    fw.fn('library_rows', None, C.c_void_p)(win)
    rows = [c for c, _ in page_rows(ui, view)]
    assert page_rows(ui, view) == page_rows(ui, page) and rows[:len(lead) + 2] == lead + ['Shuffle', 'Artist']
    assert log[0] == win  # its play gif's timer stopped, as the page's background does
    assert [c[1][:2] for c in fw.calls('widget_off_by_ctx')] == [(win, win)]


@in_fork
def test_home_s_streaming_list_is_streaming_s_rows(fw, ui):
    log = (C.c_long * 3)()
    stock_stub(O['STREAM_BG'], 0, log)
    Disk(fw)
    Config(fw)
    win, view = stock_page(ui, 'scroll_view_streamsset', *STREAM_STOCK)
    fw.fn('stream_rows', None, C.c_void_p)(win)
    assert [c for c, _ in page_rows(ui, view)][-1] == 'Configuration' and log[0] == win
    assert [c[1][:2] for c in fw.calls('widget_off_by_ctx')] == [(win, win)]


ABOUT = [('Model', 'Q2'), ('FW. Version', 'V100I'), ('Serial', '1'), ('Storage', '-'), ('Remaining', '-'),
         ('Legal', ''), ('Reset', '')]


def about(ui, rows):
    """Stock's About page: its rows, then its thanks, a 100 px item of text."""
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    view = ui.new('scroll_view_create', win, 0, 0, 375, 320)
    ui.name[view] = 'scroll_view_about'
    for title, value in rows:
        button = ui.new('button_create', ui.new('list_item_create', view, 0, 0, 375, 70), 20, 0, 335, 70)
        for x, text in ((10, title), (176, value)):
            ui.text[ui.new('hscroll_label_create', button, x, 0, 149, 70)] = text
    ui.text[ui.new('hscroll_label_create', ui.new('list_item_create', view, 0, 0, 375, 100), 20, 10, 335, 100)] = 'about_more'
    return win, view


def about_rows(ui, view):
    """Each row's (title, value); the thanks, where it still stands, as its key alone."""
    firsts = [ui.kids[item][0] for item in ui.kids[view]]
    return [tuple(ui.text[label] for label in ui.kids.get(first, ())) or (ui.text[first],) for first in firsts]


def test_about_shows_stock_s_version_and_the_payload_s_after_it_without_stock_s_thanks(fw, ui):
    win, view = about(ui, ABOUT)
    fw.mock('stock_about_trampoline', lambda *_: 5)
    assert fw.fn('ringnav_about', C.c_int, C.c_void_p, C.c_void_p)(win, None) == 5
    version, beta = host.tools_build.VERSION, host.tools_build.BETA
    assert about_rows(ui, view) == [ABOUT[0], ('FW. Version', host.tools_build.STOCK_VERSION), ('CFW. Version', f'{version} Pod' + (f' beta {beta}' if beta else '')),
                                    *ABOUT[2:]]


def test_an_about_page_that_is_not_stock_s_is_left(fw, ui):
    win, view = about(ui, ABOUT[:6])
    fw.fn('ringnav_about', C.c_int, C.c_void_p, C.c_void_p)(win, None)
    assert about_rows(ui, view) == [*ABOUT[:6], ('about_more',)]


def test_usb_mode_ends_the_card_s_apps_before_stock_unmounts_it(fw):
    disk = Disk(fw, {**deezer(), '/tmp/q2-deez': b'1'})  # its pid file: running
    fw.fn('addon_scan', C.c_int)()
    order = []
    fw.mock('fw_system', lambda cmd, *_: order.append('stop'))
    fw.mock('sleep_ms', lambda *_: (order.append('wait'), disk.files.pop('/tmp/q2-deez', None)) and 0)
    fw.mock('stock_usbmode_trampoline', lambda *_: order.append('stock') or 3)
    assert fw.fn('ringnav_usbmode', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(None, None, None, None) == 3
    assert order == ['stop', 'wait', 'stock']


def test_a_track_too_long_for_ram_does_not_start(fw, ui, song, mcl):
    stsz = box(b'stsz', struct.pack('>3I', 0, 0, 2 ** 20))
    mp4 = box(b'moov', box(b'trak', box(b'mdia', box(b'minf', box(b'stbl', stsz)))))
    Files(fw, {'/m/long.m4b': mp4, '/proc/meminfo': b'MemAvailable:   20000 kB\n'})
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([song(b'/m/long.m4b')])
    assert fw.fn('ringnav_start_player', C.c_int)() == -1 and not fw.calls('stock_start_player_trampoline')


def test_a_local_track_starts_once_the_streams_let_go(fw, ui, song, mcl):
    """mclStartPlayer's hook: a playing librespot is told to pause (and says it has) and a radio station's
    q2video to quit, both before stock opens the DAC's PCM, whose result is the hook's."""
    files = Files(fw, {'/tmp/q2-librespot.state': b'state=paused\n', '/tmp/q2radio.state': b'state=playing\n'})
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([song(b'/m/a.flac')])
    sp = fw.var('spotify__sp', C.c_int * 8)  # launched, sock, playing, paused, local, active
    sp[2], sp[5] = 1, 1
    rd = fw.var('radio__rd', C.c_int * 3)  # pid, sock, active
    rd[0], rd[2] = 4242, 1
    clock = [0]
    fw.mock('time_now_ms', lambda *_: clock[0])
    fw.mock('sleep_ms', lambda ms, *_: clock.__setitem__(0, clock[0] + ms))
    fw.mock('fw_socket', lambda *_: 7)
    order: list[object] = []  # each datagram as (socket path, bytes), then stock's start
    fw.mock('fw_sendto', lambda s, data, n, flags, to, *_: order.append((C.string_at(to + 2), C.string_at(data, n))) or n)
    fw.mock('fw_waitpid', lambda pid, *_: pid)  # q2video quits when told
    fw.mock('stock_start_player_trampoline', lambda *_: order.append('start') or 4)
    assert fw.fn('ringnav_start_player', C.c_int)() == 4
    assert order == [(b'/tmp/q2-librespot.sock', b's'), (b'/tmp/q2radio.sock', b'q'), 'start']
    assert (sp[2], sp[5], rd[0], rd[2]) == (0, 0, 0, 0) and '/tmp/q2radio.state' not in files.files


# Era.
class EraDb:
    """The library database as sqlite3_open and _exec see it: Era's decade counts and a decade's albums, the
    statements asked, and an open that fails when told."""
    def __init__(self, fw, decades=(), albums=(), opens=True):
        self.decades, self.albums, self.opens, self.asked = decades, albums, opens, []
        self.keep = []
        def open_(path, db, *_):
            if not self.opens:
                return 14  # SQLITE_CANTOPEN
            C.c_void_p.from_address(db).value = 0xdb
            return 0
        fw.mock('sqlite3_open', open_)
        fw.mock('sqlite3_exec', self.run)

    def run(self, db, sql, callback, ctx, *_):
        sql = C.string_at(sql).decode()
        self.asked.append(sql)
        if not callback:
            return 0
        rows = self.decades if 'count(*)' in sql else self.albums
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback)
        for values in rows:
            cells = (C.c_char_p * len(values))(*values)
            self.keep.append(cells)
            if call(ctx, len(values), cells, None):
                return 4  # SQLITE_ABORT
        return 0


def test_era_lists_each_decade_with_its_albums(fw, ui):
    db = EraDb(fw, decades=[(b'1970', b'1'), (b'1990', b'3'), (b'2000', b'12')])
    call(fw, 'era_menu')
    page = ui.top()
    assert ui.name[page] == 'era_page' and 'Era' in ui.shown(page)
    assert ui.rows(page) == [('70s', '1 album'), ('90s', '3 albums'), ('2000s', '12 albums')]
    assert db.asked[-1].startswith('select year/10*10,count(*)')
    call(fw, 'era_menu')
    assert ui.windows == [page]


@pytest.mark.parametrize('opens, decades, toast', [(False, [], 'Era unavailable'), (True, [], 'No dated albums yet')])
def test_era_with_nothing_to_list_says_why(fw, ui, opens, decades, toast):
    EraDb(fw, decades=decades, opens=opens)
    call(fw, 'era_menu')
    assert ui.toasts == [toast] and not ui.windows


ERA_ALBUMS = [(b'Blue', b'Band', b'1991', b'/m/blue/1.flac'), (b'Grey', b'', b'1995', b'/m/grey/1.flac'),
              (b'Red', b'Band', b'1999', b'/m/red/1.flac')]


def era_decade(fw, ui, albums=ERA_ALBUMS):
    db = EraDb(fw, decades=[(b'1970', b'1'), (b'1990', b'%d' % len(albums))], albums=albums)
    call(fw, 'era_menu')
    ui.click(ui.top(), 1)
    return db, ui.top()


def test_a_decade_lists_its_albums_by_year(fw, ui):
    db, page = era_decade(fw, ui)
    assert ui.name[page] == 'era_albums_page' and '90s' in ui.shown(page)
    assert ui.rows(page) == [('Play All', '3 albums'), ('Shuffle', 'Every song, shuffled'), ('Blue', 'Band • 1991'),
                             ('Grey', '1995'), ('Red', 'Band • 1999')]
    assert 'having release_year between 1990 and 1999' in db.asked[-1]


def test_a_decade_s_albums_stop_at_its_count(fw, ui):
    EraDb(fw, decades=[(b'1990', b'1')], albums=ERA_ALBUMS)  # the albums query found more since
    call(fw, 'era_menu')
    ui.click(ui.top(), 0)
    assert ui.rows(ui.top())[0] == ('Play All', '1 album') and len(ui.rows(ui.top())) == 3


def test_a_decade_closes_and_opens_again(fw, ui):
    db, page = era_decade(fw, ui)
    era = ui.windows[0]
    ui.click(era, 0)  # another decade while one is open: nothing
    assert ui.windows == [era, page]
    ui.close(page)
    ui.click(ui.top(), 1)
    assert len(ui.rows(ui.top())) == 5


# a decade whose albums query fails says 'Era unavailable', as the decade list does, rather than opening an empty page
def test_a_failing_decade_query_says_era_unavailable(fw, ui):
    db = EraDb(fw, decades=[(b'1990', b'3')], albums=ERA_ALBUMS)
    call(fw, 'era_menu')
    era, run = ui.top(), db.run
    fw.mock('sqlite3_exec', lambda d, sql, *a: 1 if b'having release_year' in C.string_at(sql) else run(d, sql, *a))
    ui.click(era, 0)
    assert ui.toasts == ['Era unavailable'] and ui.windows == [era]
    fw.mock('sqlite3_exec', run)  # the next try opens it
    ui.click(era, 0)
    assert len(ui.rows(ui.top())) == 5


# a failing query behind Play All, Shuffle or an album says 'Era unavailable' too, not that the card or album is gone
@pytest.mark.parametrize('row', [0, 1, 2])
def test_a_failing_song_query_says_era_unavailable(fw, ui, row):
    db, page = era_decade(fw, ui)
    run = db.run
    fw.mock('sqlite3_exec', lambda d, sql, *a: 1 if b'select distinct s.id' in C.string_at(sql) else run(d, sql, *a))
    fw.mock('getMusicByAlbum', lambda *_: -1)  # stock's count when its query fails (toolsQueryDbTable: no database, or sqlite3_exec fails)
    ui.click(page, row)
    assert ui.toasts == ['Era unavailable'] and not ui.played


# Shuffle's key query worked but loading the library failed: still 'Era unavailable', not 'Storage unavailable'
def test_shuffle_with_the_library_failing_says_era_unavailable(fw, ui, mcl):
    fw.mock('getAllMusic', lambda *_: -1)
    db, page = era_decade(fw, ui)
    db.albums = [(b'9',)]
    ui.click(page, 1)
    assert ui.toasts == ['Era unavailable'] and not ui.played


# an album row whose query returned rows and then failed does not open
def test_an_album_row_with_a_partial_failing_query_says_era_unavailable(fw, ui, song):
    albums = Albums(fw, ui, song)
    db, page = era_decade(fw, ui)
    load = albums.load
    fw.mock('getMusicByAlbum', lambda name, *_: [load(name), -1][1])
    ui.click(page, 2)
    assert ui.toasts == ['Era unavailable'] and not ui.played


# Play All skips an album whose query failed and plays the rest; it says so only when every album failed
def test_play_all_skips_a_failed_album(fw, ui, song):
    albums = Albums(fw, ui, song)
    db, page = era_decade(fw, ui)
    load = albums.load
    fw.mock('getMusicByAlbum', lambda name, *_: -1 if C.string_at(name) == b'Grey' else load(name))
    ui.click(page, 0)
    (queue, at, cls), = ui.played
    assert [field(r, 'title') for r in queue] == [b'B1', b'B2', b'R1'] and not ui.toasts


def test_play_all_with_every_album_failing_says_era_unavailable(fw, ui, song):
    Albums(fw, ui, song)
    db, page = era_decade(fw, ui)
    fw.mock('getMusicByAlbum', lambda *_: -1)
    ui.click(page, 0)
    assert ui.toasts == ['Era unavailable'] and not ui.played


class Albums:
    """getMusicByAlbum: every song of that album name, in no order, and the card's access to them."""
    def __init__(self, fw, ui, song, gone=()):
        self.songs = {  # another album of the same name, in another folder
            b'Blue': [song(b'/m/blue/x.flac', b'B2', album=b'Blue', track=2), song(b'/m/blue/1.flac', b'B1', album=b'Blue', track=1)],
            b'Grey': [song(b'/m/grey/1.flac', b'G1', album=b'Grey')],
            b'Red': [song(b'/m/red/1.flac', b'R1', album=b'Red'), song(b'/m/other/red.flac', b'Other Red', album=b'Red')],
        }
        self.load = lambda name: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value] \
            .extend(self.songs.get(C.string_at(name), [])) or 0
        fw.mock('getMusicByAlbum', lambda name, *_: self.load(name))
        fw.mock('fw_access', lambda p, *_: -1 if C.string_at(p) in gone else 0)


def test_play_all_plays_the_decade_s_albums_in_order(fw, ui, song):
    Albums(fw, ui, song)
    db, page = era_decade(fw, ui)
    ui.click(page, 0)
    (queue, at, cls), = ui.played
    assert [field(r, 'title') for r in queue] == [b'B1', b'B2', b'G1', b'R1'] and at == 0 and cls == LIBRARY


def test_play_all_with_the_card_away_says_so(fw, ui, song):
    Albums(fw, ui, song, gone={b'/m/blue/1.flac'})
    db, page = era_decade(fw, ui)
    ui.click(page, 0)
    assert ui.toasts == ['Storage unavailable'] and not ui.played


def test_shuffle_plays_the_decade_s_songs_shuffled(fw, ui, song, mcl):
    songs = [song(b'/m/%d.flac' % i, id_=i) for i in (5, 9, 1)]
    library = Library(fw, ui, songs)
    fw.mock('fw_access', lambda *_: 0)
    db, page = era_decade(fw, ui)
    db.albums = [(b'9',), (b'1',)]  # the decade's song ids
    ui.click(page, 1)
    (queue, at, cls), = ui.played
    assert queue == songs[1:] and cls == LIBRARY and library.asked == [3]


def test_an_album_row_that_is_gone_says_so(fw, ui, song):
    Albums(fw, ui, song)
    db, page = era_decade(fw, ui, albums=[(b'Blue', b'Band', b'1991', b'/m/blue/9.flac')])  # no such file now
    ui.click(page, 2)
    assert ui.toasts == ['Album not found']


def test_library_count_is_the_query_s_number(fw):
    db = EraDb(fw, decades=[(b'42',)])
    count = fw.fn('library_count', C.c_int, C.c_char_p)
    assert count(b'select count(*) from songtable') == 42
    db.opens = False
    assert count(b'select count(*) from songtable') == -1


# Podcasts, Audiobooks and Videos: Folder view at the card's folder.
def test_a_media_row_opens_folder_view_at_its_folder(fw, ui, st):
    Disk(fw, folders=['/mnt/mmc/PODCASTS', '/mnt/mmc/Music'])
    call(fw, 'media_click', 1)
    assert ui.went == ['folder_page'] and st('media_root', C.c_char * 32).value == b'/mnt/mmc/PODCASTS'


@pytest.mark.parametrize('kind, name', [(1, 'Podcasts'), (5, 'Videos')])
def test_a_media_row_whose_folder_went_says_so(fw, ui, kind, name):
    Disk(fw, {'/mnt/mmc/Podcasts': b'a file, not a folder'}, folders=['/mnt/mmc/Music'])
    call(fw, 'media_click', kind)
    assert ui.toasts == [f'No {name} folder on the card'] and not ui.went


@pytest.mark.parametrize('kind, page', [(3, 'photos_page'), (4, 'books_page')])
def test_photos_and_books_open_their_own_pages(fw, ui, kind, page):
    Disk(fw, folders=['/mnt/mmc/photos', '/mnt/mmc/Books'])
    fw.mock('fw_pthread_create', lambda *_: 0)
    call(fw, 'media_click', kind)
    assert ui.name[ui.top()] == page and not ui.went


class Folder:
    """Folder view: stock's page init, back and click as stubs that move g_folder_layer as stock's do, and the
    folder it shows."""
    def __init__(self, fw, ui):
        self.fw, self.ui = fw, ui
        self.layer, self.otg = fw.var('g_folder_layer', C.c_ubyte), fw.var('g_otg_status', C.c_ubyte)
        self.path = fw.var('fw_ctx_g_folder_path', C.c_char * 1024)
        fw.mock('stock_folder_trampoline', lambda *_: 6)
        fw.mock('stock_folder_back_trampoline', lambda *_: setattr(self.layer, 'value', self.layer.value - 1) or 0)
        self.win = ui.new('window_create', 0, 0, 0, 0, 0)

    def open(self):
        return self.fw.fn('ringnav_folder', C.c_int, C.c_void_p, C.c_void_p)(self.win, None)

    def back(self):
        return self.fw.fn('ringnav_folder_back', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def at(self):
        """Where stock was sent, or None: the folder and its layer."""
        if not self.fw.calls('folder_reset_variable'):
            return None
        names = [c[0] for c in self.fw.calls() if c[0].startswith('folder_')]
        assert names[-4:] == ['folder_reset_variable', 'folder_reload_data', 'folder_reinit_navbarname', 'folder_refresh']
        return self.path.value.decode(), self.layer.value


def test_a_media_folder_is_folder_view_s_root(fw, ui, st):
    Disk(fw, {'/tmp/mmc_add': b''}, folders=['/mnt/mmc/Audiobooks'])
    folder = Folder(fw, ui)
    call(fw, 'media_click', 2)
    assert folder.open() == 6 and folder.at() == ('/mnt/mmc/Audiobooks', 3)
    assert [c[1][0] for c in fw.calls('folder_refresh')] == [folder.win]
    folder.back()  # Back at it leaves the page, as stock's from its first layer
    assert folder.layer.value == 0 and not st('media_root', C.c_char * 32).value


@pytest.mark.parametrize('card, otg, at', [(True, 0, ('/mnt/mmc', 2)), (True, 1, None), (False, 0, None)])
def test_folder_view_opens_on_the_card_when_it_is_the_only_storage(fw, ui, card, otg, at):
    Disk(fw, {'/tmp/mmc_add': b''} if card else {})
    folder = Folder(fw, ui)
    folder.otg.value = otg
    folder.open()
    assert folder.at() == at


def test_folder_root_opens_folder_view_below_the_card(fw, ui):
    Disk(fw, {'/tmp/mmc_add': b''})
    Config(fw, FOLDERROOT=b'Music')
    fw.mock('fw___xstat', lambda v, path, info, *_: C.c_uint.from_address(info + 20).__setattr__('value', 0o040755) or 0)
    folder = Folder(fw, ui)
    folder.open()
    assert folder.at() == ('/mnt/mmc/Music', 3)


def test_go_to_folder_opens_folder_view_there_once(fw, ui, st):
    Disk(fw, {'/tmp/mmc_add': b''})
    folder = Folder(fw, ui)
    st('go_folder', C.c_char * 1024).value, st('go_layer').value = b'/mnt/mmc/A/B', 4
    folder.open()
    assert folder.at() == ('/mnt/mmc/A/B', 4) and not st('go_layer').value
    folder.open()
    assert folder.at() == ('/mnt/mmc', 2)


def test_back_below_the_root_is_stock_s(fw, ui, st):
    Disk(fw, {'/tmp/mmc_add': b''}, folders=['/mnt/mmc/Podcasts'])
    folder = Folder(fw, ui)
    call(fw, 'media_click', 1)
    folder.open()
    folder.path.value, folder.layer.value = b'/mnt/mmc/Podcasts/Show', 4
    folder.back()
    assert folder.layer.value == 3 and st('media_root', C.c_char * 32).value == b'/mnt/mmc/Podcasts'


def test_classic_s_folder_levels_slide_in(fw, ui, song):
    Config(fw, THEME=b'1')  # its levels move in place
    folder = Folder(fw, ui)
    table = ui.new('list_view_create', folder.win, 0, 0, 375, 300)
    ui.name[table] = 'tableview_folder'
    ui.new('scroll_view_create', table, 0, 0, 375, 300)
    fw.mock('widget_animator_prop_create', lambda w, *_: w)
    folder.layer.value = 4
    folder.back()
    rows = Rows(fw, ui, [song(b'/mnt/mmc/A/B', kind=4)])
    fw.mock('stock_folder_click_trampoline', lambda *_: setattr(folder.layer, 'value', folder.layer.value + 1) or 2)
    rows.click(0)
    folder.layer.value = 1
    folder.back()  # out of Folder view: no level to slide
    # Back's level comes in from the left, a deeper one from the right: each starts a width off, animated to 0
    assert [signed(c[1][1]) for c in fw.calls('widget_move_resize') if c[1][0] == table] == [-375, 375]
    assert len(fw.calls('widget_animator_start')) == 2


@pytest.mark.parametrize('otg, roots', [(0, 1), (1, 2)])
def test_the_storage_list_drops_otg_without_a_drive(fw, ui, otg, roots):
    folder = Folder(fw, ui)
    folder.otg.value = otg
    showlist = ui.deques.new([0x10, 0x20])
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = showlist
    fw.mock('fw_deque_pop_back', lambda q, *_: ui.deques[q].pop() and 0)
    fw.fn('ringnav_folder_top', None)()
    assert len(ui.deques[showlist]) == roots


class Rows:
    """Folder view's list (p_deque_showlist) and stock's row lookup: a click's target is row i."""
    def __init__(self, fw, ui, records):
        fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = ui.deques.new(records)
        self.row, self.event = C.create_string_buffer(0x100), C.create_string_buffer(0x40)
        fw.mock('table_row_of', lambda target, *_: C.addressof(self.row))
        fw.mock('stock_folder_click_trampoline', lambda *_: 2)
        self.fw = fw

    def click(self, i):
        struct.pack_into('<i', self.row, O['ROW_INDEX'], i)
        return self.fw.fn('ringnav_folder_click', C.c_int, C.c_void_p, C.c_void_p)(None, C.addressof(self.event))


def test_a_video_in_folder_view_plays_as_a_video(fw, ui, song):
    Files(fw)
    rows = Rows(fw, ui, [song(b'/mnt/mmc/Videos/a.mp4'), song(b'/mnt/mmc/Videos/b.flac')])
    fw.mock('fw_fork', lambda *_: 4242)
    assert rows.click(0) == 0 and fw.calls('fw_fork') and not fw.calls('stock_folder_click_trampoline')
    assert rows.click(1) == 2 and len(fw.calls('stock_folder_click_trampoline')) == 1


def test_select_mode_leaves_a_video_to_stock(fw, ui, song):
    rows = Rows(fw, ui, [song(b'/mnt/mmc/Videos/a.mp4')])
    fw.var('g_navbar_status', C.c_ubyte).value = 1
    assert rows.click(0) == 2 and not fw.calls('fw_fork')


# A spoken CUE track's saved place.
def spots(*places, slots=RESUME_SLOTS):
    data = b''.join(struct.pack('<Ii', key, sec) for key, sec in places)
    return data + b'\0' * (slots * 8 - len(data))


def book_place(fw, song, files, path=b'/mnt/mmc/Audiobooks/b.flac'):
    Files(fw, files)
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    deques = Deques(fw)
    tracks = [song(path, b'A', cue=0, end=600), song(path, b'B', cue=600, end=1200), song(path, b'C', cue=1200)]
    other = song(b'/mnt/mmc/Audiobooks/other.flac', cue=1000, end=2000)
    folder = song(b'/mnt/mmc/Audiobooks/x', kind=4)
    listing = deques.new([folder, tracks[0], other, tracks[1], tracks[2]])
    at, off = C.c_int(-1), C.c_int(-1)
    place = fw.fn('navigation__book_place', C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int))
    best = place(listing, tracks[1], C.byref(at), C.byref(off))
    return (field(best, 'title') if best else None), at.value, off.value


def test_a_book_resumes_in_the_chapter_holding_its_place(fw, song):
    key = file_key(b'/mnt/mmc/Audiobooks/b.flac')
    assert book_place(fw, song, {RESUME_FILE: spots((0x77, 5), (key, 1500))}) == (b'C', 3, 300)  # 3: the folder row isn't a song


def test_a_book_s_place_from_an_earlier_release_s_file(fw, song):
    key = file_key(b'/mnt/mmc/Audiobooks/b.flac')
    assert book_place(fw, song, {RESUME_FILE: spots((key, 700), slots=32)}) == (b'B', 2, 100)


def test_a_book_with_no_place_has_none(fw, song):
    assert book_place(fw, song, {})[0] is None


def test_a_song_s_cue_track_is_not_a_book(fw, song):
    key = file_key(b'/mnt/mmc/Music/b.flac')
    assert book_place(fw, song, {RESUME_FILE: spots((key, 700))}, path=b'/mnt/mmc/Music/b.flac')[0] is None


# Shuffle Songs over the song holding its place.
def holding(fw, ui, mcl, st, playing):
    """playing on, a long track 600 s into its 3600: its place is held (resume_held)."""
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([playing])
    mcl.type, mcl.pos = LIBRARY, 0
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.mock('player_playtime_and_length', lambda sec, total, *_: C.c_int.from_address(sec).__setattr__('value', 600))
    st('rs_long').value, st('rs_total').value = 1, 3600


def test_shuffle_songs_of_the_one_song_holding_its_place_is_now_playing(fw, ui, song, mcl, st):
    playing = song(b'/m/book.flac')
    holding(fw, ui, mcl, st, playing)
    Library(fw, ui, [song(b'/m/book.flac')])
    call(fw, 'shuffle_songs')
    assert not ui.played and [C.string_at(c[1][0]) for c in fw.calls('navigator_switch_to_with_context')] == [b'playing_page']


def test_shuffle_songs_starts_anywhere_but_the_song_holding_its_place(fw, ui, song, mcl, st):
    holding(fw, ui, mcl, st, song(b'/m/book.flac'))
    for _ in range(8):  # rand_below's picks: never the held one
        Library(fw, ui, [song(b'/m/book.flac'), song(b'/m/other.flac')])
        ui.played.clear()
        call(fw, 'shuffle_songs')
        assert ui.played[0][1] == 1


# The queue menu's tracks for a row.
QM_SONG_, QM_FOLDER_, QM_COVERALBUM_, QM_PLAYLIST_ = 1, 4, 6, 8


def collect(fw, ui, st, kind, r, cls=0):
    st('qm_kind').value, st('qm_cls', C.c_uint).value = kind, cls
    return [field(t, 'path') for t in ui.deques[fw.fn('qm_collect', C.c_long, C.c_void_p)(r)]]


def test_a_song_row_s_tracks_are_the_song_but_a_video(fw, ui, song, st):
    assert collect(fw, ui, st, QM_SONG_, song(b'/m/a.flac')) == [b'/m/a.flac']
    assert collect(fw, ui, st, QM_SONG_, song(b'/m/clip.mp4')) == []


def test_a_folder_row_s_tracks_take_in_its_subfolders(fw, ui, song, st):
    tree = {b'/mnt/mmc/Music/Album': [song(b'/mnt/mmc/Music/Album/1.flac'), song(name=b'CD2', kind=4),
                                     song(name=b'.hidden', kind=4), song(b'/mnt/mmc/Music/Album/v.mp4')],
            b'/mnt/mmc/Music/Album/CD2': [song(b'/mnt/mmc/Music/Album/CD2/2.flac')],
            b'/mnt/mmc/Music/Album/.hidden': [song(b'/mnt/mmc/Music/Album/.hidden/3.flac')]}
    fw.mock('toolsLoadDirectory', lambda path, *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value]
            .extend(tree.get(C.string_at(path), [])) or 0)
    fw.var('fw_ctx_g_folder_path', C.c_char * 1024).value = b'/mnt/mmc/Music'
    assert collect(fw, ui, st, QM_FOLDER_, song(name=b'Album', kind=4)) == [b'/mnt/mmc/Music/Album/1.flac',
                                                                              b'/mnt/mmc/Music/Album/CD2/2.flac']


def test_a_playlist_row_s_tracks_leave_out_spoken_ones(fw, ui, song, st):
    rows = [song(b'/mnt/mmc/Music/a.flac'), song(b'/mnt/mmc/Podcasts/ep.mp3'), song(b'/mnt/mmc/Music/x', kind=4),
            song(b'/mnt/mmc/Music/b.flac')]
    asked = []
    fw.mock('getMusicByPlayList', lambda n, *_: asked.append(n) or ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value]
            .extend(rows) or 0)
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    playlist = song(kind=4, disc=7)  # a playlist row: its cd_num
    assert collect(fw, ui, st, QM_PLAYLIST_, playlist) == [b'/mnt/mmc/Music/a.flac', b'/mnt/mmc/Music/b.flac'] and asked == [7]


def test_a_cover_s_tracks_are_its_album_in_order(fw, ui, song, st):
    albums = Albums(fw, ui, song)
    assert collect(fw, ui, st, QM_COVERALBUM_, albums.songs[b'Blue'][0]) == [b'/m/blue/1.flac', b'/m/blue/x.flac']


@pytest.mark.parametrize('mode, forced', [(2, 2), (0, 0)])
def test_play_next_in_stock_shuffle_forces_the_track_next(fw, ui, song, st, mcl, mode, forced):
    a, b, c = song(b'/m/a.flac'), song(b'/m/b.flac'), song(b'/m/c.flac')
    held = HeldRows(fw)  # the queue holds its own copies, as libcstl's: Play Next turns them into place by their bytes
    queue = held.rows([a, b])
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
    C.c_uint64.from_address(O['MCL_POOL']).value = held.new()
    mcl.type, mcl.pos, mcl.mode, mcl.lastpos = LIBRARY, 0, mode, -1
    st('qm_kind').value = QM_SONG_
    assert fw.fn('navigation__qm_apply', C.c_int, C.c_void_p, C.c_int)(held.new([c]), 1) == 1
    assert [field(r, 'path') for r in held[queue]] == [field(r, 'path') for r in (a, c, b)]
    assert st('qm_forced', C.c_uint).value == forced
    assert st('qm_forced_hash', C.c_uint).value == fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(c)


# Rated albums.
def rated_albums(fw, ui, song, hearts=0):
    """The Library's albums A and B (stock's list, its album split and its music albums all the two), A rated."""
    files = Files(fw)
    rows = [song(b'/m/a/1.flac', album=b'A', id_=1), song(b'/m/b/1.flac', album=b'B', id_=2)]
    put = lambda *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value].extend(rows) or 2  # noqa: E731
    fw.mock('getAllAlbum', put)
    fw.mock('toolsQueryDbTable', put)
    if hearts:
        album_id = fw.fn('album_id', C.c_uint, C.c_void_p)(rows[0])
        assert fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)(1, album_id, hearts) == 1
    call(fw, 'rated_albums')
    return files


def test_albums_none_of_them_rated_says_so(fw, ui, song):
    rated_albums(fw, ui, song)
    assert ui.toasts == ['No rated albums yet'] and not ui.windows


def test_a_rated_album_opens_cover_flow_on_it_alone(fw, ui, song):
    rated_albums(fw, ui, song, hearts=4)
    assert not ui.toasts and ui.name[ui.top()] == 'coverflow_page'
    cf = fw.var('cf', C.c_void_p * 10)  # page body covers slide name artist title albums tracks stock
    assert [field(r, 'album') for r in ui.deques[cf[7]]] == [b'A']  # the covers shown: B has no hearts


def test_a_card_app_s_album_songs_are_in_track_order(fw, ui, song):
    card = Card(fw, ui, song, APP_SONGS)
    songs = fw.fn('app_album_songs', C.c_long, C.c_char_p)(APP)
    assert card.paths(ui.deques[songs]) == sorted(APP_SONGS)


# The playlist editor's records.
class Ple(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view songs picker edit match'.split()),
                *((k, C.c_int) for k in 'dest mode selected pending nmatch'.split()),
                ('key', C.c_uint), ('timer', C.c_uint), ('name', C.c_char * 128), ('query', C.c_char * 128)]


@pytest.mark.parametrize('query, titles', [(b'moon', [b'Blue Moon']), (b'ELVIS', [b'Blue Moon', b'02 Untitled']),
                                           (b'hits', [b'Blue Moon']), (b'untitled', [b'02 Untitled']), (b'zz', []),
                                           (b'', [b'Blue Moon', b'02 Untitled', b'Other'])])
def test_add_songs_search_matches_title_artist_or_album(fw, ui, song, query, titles):
    rows = [song(b'/m/1.flac', b'Blue Moon', b'Elvis', b'Hits'), song(b'/m/02 Untitled.flac', b'', b'elvis'),
            song(b'/m/3.flac', b'Other', b'Someone', b'Else')]
    ple = fw.var('playlists__ple', Ple)
    ple.picker, ple.query = ui.deques.new(rows), query
    fw.fn('playlists__ple_filter', None)()
    name = fw.fn('track_name', C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)
    out = C.create_string_buffer(64)
    matches = C.cast(ple.match, C.POINTER(C.c_void_p))
    assert [name(out, 64, matches[i]) for i in range(ple.nmatch)] == titles


def test_add_songs_search_with_no_picker_matches_nothing(fw, ui):
    ple = fw.var('playlists__ple', Ple)
    fw.fn('playlists__ple_filter', None)()
    assert ple.nmatch == 0 and not ple.match


@pytest.mark.parametrize('selected, size, kept', [(1, 3, 1), (5, 3, 2), (2, 0, 0)])
def test_a_playlist_read_again_keeps_the_selection_in_it(fw, ui, song, selected, size, kept):
    rows = [song(b'/m/%d.flac' % i) for i in range(size)]
    asked = []
    # the stored order, whatever stock's Sort shows the playlist in
    fw.mock('sort_playlist_addtime', lambda n, *_: asked.append(n) or ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value]
            .extend(rows) or 0)
    ple = fw.var('playlists__ple', Ple)
    old = ui.deques.new([1])
    ple.songs, ple.dest, ple.selected = old, 4, selected
    fw.fn('playlists__ple_reload', None)()
    assert ui.deques[ple.songs] == rows and ple.selected == kept and asked == [4]
    assert old not in ui.deques.lists  # the old list went


def test_a_picker_row_s_check_sits_at_its_end(fw, ui):
    item = ui.new('list_item_create', 0, 0, 0, 375, 48)
    label = ui.new('hscroll_label_create', item, 20, 0, 335, 48)  # the caption across the row
    tick = fw.fn('playlists__ple_tick', C.c_void_p, C.c_void_p)(item)
    assert ui.image[tick] == 'select' and ui.kids[item] == [label, tick]
    tx, ty, tw, th = struct.unpack_from('<4i', ui.blocks[tick])
    assert tx + tw <= 375 and (ty, th) == (0, 48)  # in the row, its height
    (_, (w, x, y, width, h, _)), = fw.calls('widget_move_resize')
    assert w == label and (x, y, h) == (20, 0, 48) and 0 < width and x + width <= tx  # the caption stops short of it
    assert fw.fn('playlists__ple_tick', C.c_void_p, C.c_void_p)(None) is None


def test_the_editor_s_close_frees_all_but_the_held_key(fw, ui):
    ple = fw.var('playlists__ple', Ple)
    songs, picker = ui.deques.new([1]), ui.deques.new([2])
    ple.songs, ple.picker, ple.timer, ple.key, ple.dest = songs, picker, 33, KEY_CENTER, 5
    fw.fn('playlists__ple_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert [c[1][0] for c in fw.calls('timer_remove')] == [33]
    assert not ui.deques.lists and (ple.songs, ple.picker, ple.dest, ple.key) == (None, None, 0, KEY_CENTER)


class Playlists:
    """playlist's rows by id: (cd_num, fileurl, time, time_end), and SQLite as ple_mutate speaks to it: its
    statements, a failure where asked, and the rows' state after a commit (a rollback puts them back)."""
    def __init__(self, fw, rows, fail=(), opens=True):
        self.rows, self.fail, self.opens, self.log, self.keep = dict(rows), fail, opens, [], []
        self.saved = dict(rows)
        fw.mock('sqlite3_open', lambda path, db, *_: 14 if not self.opens else C.c_void_p.from_address(db).__setattr__('value', 0xdb) or 0)
        fw.mock('sqlite3_exec', self.run)
        fw.mock('sqlite3_close', lambda *_: self.log.append('close') and 0)

    def rows_to(self, callback, ctx, rows):
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback)
        for values in rows:
            cells = (C.c_char_p * len(values))(*values)
            self.keep.append(cells)
            if call(ctx, len(values), cells, None):
                return 4  # SQLITE_ABORT
        return 0

    def run(self, db, sql, callback, ctx, *_):
        sql = C.string_at(sql).decode()
        self.log.append(sql)
        if any(f in sql for f in self.fail):
            return 1
        if sql == 'begin immediate':
            self.saved = dict(self.rows)
            return 0
        if sql == 'rollback':
            self.rows = dict(self.saved)
            return 0
        if sql == 'commit':
            return 0
        if sql.startswith('select id,fileurl'):
            dest, a, b = map(int, re.findall(r'-?\d+', sql)[-3:])
            found = [(b'%d' % i, r[1], b'%d' % r[2], b'%d' % r[3]) for i, r in sorted(self.rows.items())
                     if r[0] == dest and i in (a, b)]
            return self.rows_to(callback, ctx, found)
        if sql.startswith('select case'):
            top = max(self.rows, default=100)
            return self.rows_to(callback, ctx, [(None if top >= 2 ** 31 - 1 else b'%d' % (max(100, top) + 1),)])
        status = 0
        for statement in sql.split(';'):
            if m := re.fullmatch(r'update playlist set id=(-?\d+) where cd_num=(\d+) and id=(-?\d+)', statement):
                to, dest, at = map(int, m.groups())
                changed = at in self.rows and self.rows[at][0] == dest
                if changed:
                    self.rows[to] = self.rows.pop(at)
            elif m := re.fullmatch(r'delete from playlist where cd_num=(\d+) and id=(-?\d+)', statement):
                dest, at = map(int, m.groups())
                changed = at in self.rows and self.rows[at][0] == dest
                if changed:
                    del self.rows[at]
            elif statement == 'select changes()':
                status = status or self.rows_to(callback, ctx, [(b'%d' % changed,)])
        return status


def entry(song, id_, path, cue=0, end=0):
    return song(path, id_=id_, cue=cue, end=end)


def mutate(fw, dest, a, b=None):
    return fw.fn('playlists__ple_mutate', C.c_int, C.c_int, C.c_void_p, C.c_void_p)(dest, a, b)


def test_moving_a_playlist_song_swaps_two_rows(fw, song):
    db = Playlists(fw, {201: (3, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0), 300: (4, b'/m/c.flac', 0, 0)})
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 1
    assert db.rows == {201: (3, b'/m/b.flac', 0, 0), 202: (3, b'/m/a.flac', 0, 0), 300: (4, b'/m/c.flac', 0, 0)}
    assert db.log[:2] == ['PRAGMA busy_timeout=500', 'begin immediate'] and db.log[-2:] == ['commit', 'close']


def test_removing_a_playlist_song_deletes_its_row(fw, song):
    db = Playlists(fw, {201: (3, b'/m/a.flac', 10, 20), 202: (3, b'/m/b.flac', 0, 0)})
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac', 10, 20)) == 1
    assert db.rows == {202: (3, b'/m/b.flac', 0, 0)} and db.log[-2] == 'commit'


@pytest.mark.parametrize('rows', [
    {201: (3, b'/m/other.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0)},  # another song there since
    {201: (3, b'/m/a.flac', 5, 0), 202: (3, b'/m/b.flac', 0, 0)},  # another CUE track of the file
    {201: (4, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0)},  # in another playlist
    {202: (3, b'/m/b.flac', 0, 0)},  # gone
])
def test_a_playlist_changed_since_it_was_read_is_left_as_it_is(fw, song, rows):
    db = Playlists(fw, rows)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 0
    assert db.rows == rows and db.log[-2:] == ['rollback', 'close']


def test_no_id_left_above_the_top_one_leaves_the_playlist(fw, song):
    rows = {201: (3, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0), 2 ** 31 - 1: (9, b'/m/z.flac', 0, 0)}
    db = Playlists(fw, rows)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 0
    assert db.rows == rows and 'rollback' in db.log


@pytest.mark.parametrize('fail, rolled', [(('begin',), False), (('update',), True), (('commit',), True)])
def test_a_database_that_refuses_leaves_the_playlist(fw, song, fail, rolled):
    rows = {201: (3, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0)}
    db = Playlists(fw, rows, fail=fail)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 0
    assert db.rows == rows and ('rollback' in db.log) == rolled and db.log[-1] == 'close'


def test_a_database_that_will_not_open_is_left(fw, song):
    db = Playlists(fw, {}, opens=False)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac')) == 0 and db.log == []
