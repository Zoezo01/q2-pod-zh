"""upnext.c on its device path: Up Next's rows over a shuffled queue as playback.c orders it, a row's tap, a
Play/Pause hold's move, and a queue changed under the page."""
import ctypes as C
import itertools
import mmap
import struct

import pytest

from conftest import O, Deques, libc

PAGE = 0x7000
EVT_CLICK, EVT_DESTROY, SEL, COUNT = O['EVT_CLICK'], O['EVT_DESTROY'], b'_ringnav_index', b'_ringnav_count'
MAP_32BIT = 0x40
NAMES = [b'%02d Song.flac' % k for k in range(8)]


class Low:
    """Memory under 4 GB for the queue's records, laid out as the Q2's: a pointer there is a 4-byte cell, and P()
    here reads 8, so a field reads whole only with an empty cell after it. A record keeps its file name (REC_NAME;
    REC_PATH's cell after it stays empty, so track_name shows the name, as for an untagged file) and its artist."""
    def __init__(self, size=1 << 16):
        self.base = libc.mmap(None, size, 3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS | MAP_32BIT, -1, 0)
        assert self.base and self.base + size < 1 << 32
        self.at, self.size = self.base, size

    def take(self, data):
        p = self.at
        C.memmove(p, data, len(data))
        self.at += len(data) + 15 & ~15
        assert self.at <= self.base + self.size
        return p

    def record(self, name, artist=None, cue=0):
        rec = bytearray(0x60)
        struct.pack_into('<I', rec, O['REC_NAME'], self.take(name + b'\0'))
        if artist is not None:
            struct.pack_into('<I', rec, O['REC_ARTIST'], self.take(artist + b'\0'))
        struct.pack_into('<ii', rec, O['REC_TYPE'], 8, 0)
        struct.pack_into('<ii', rec, O['REC_CUE_START'], cue, cue + 60)  # a queue's identity is in these too
        return self.take(bytes(rec))

    def close(self):
        libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
        libc.munmap(self.base, self.size)


@pytest.fixture
def low():
    memory = Low()
    yield memory
    memory.close()


class Queue:
    """The playing queue in stock's deque, its player's globals, and Advanced Play's Shuffle Songs over it
    (playback.c). The odd tracks' artists go in after the order is drawn: its album grouping reads REC_ALBUM's cell
    with the artist's as its high half here."""
    def __init__(self, fw, mcl, low, names=NAMES, pos=2, seed=0x2545f491):
        self.fw, self.mcl, self.low, self.deques = fw, mcl, low, Deques(fw)
        self.records = [low.record(n, cue=k) for k, n in enumerate(names)]
        self.handle = self.deques.new(self.records)
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.handle  # a 32-bit pointer's cell, as on the Q2
        mcl.pos = pos
        fw.var('playback__rng', C.c_uint).value = seed
        assert fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(0, 1) == 1
        for k in range(1, len(names), 2):
            C.c_uint.from_address(self.records[k] + O['REC_ARTIST']).value = low.take(b'Artist %d\0' % k)

    def name(self, at):
        return C.string_at(C.c_uint.from_address(self.deques[self.handle][at] + O['REC_NAME']).value).decode()

    def played(self, n):
        """The tracks the next n natural changes play, by name."""
        out = []
        for _ in range(n):
            assert self.fw.fn('ringnav_next', C.c_int, C.c_int)(1) >= 0
            out.append(self.name(self.mcl.pos))
        return out


class Page:
    """Up Next's page on stubs: its labels by parent, their texts, the list's rows, the timers and the toasts."""
    def __init__(self, fw):
        self.fw, self.texts, self.children, self.items, self.timers, self.toasts = fw, {}, {}, [], [], []
        self.props = {}
        self.view = C.create_string_buffer(0x400)  # ringnav_select reads the list's fields
        self.labels = []
        fw.mock('window_create', lambda *_: PAGE)
        fw.mock('scroll_view_create', lambda *_: self.items.clear() or C.addressof(self.view))
        ids = itertools.count(0x7100)
        fw.mock('list_item_create', lambda *_: self.items.append(next(ids)) or self.items[-1])
        fw.mock('hscroll_label_create', self.label)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_get_child', lambda w, i, *_: (self.children.get(w, []) + [0] * (i + 1))[i])
        fw.mock('widget_destroy_children', lambda w, *_: self.children.pop(w, None) and 0)
        fw.mock('widget_get_prop_int', self.prop)
        fw.mock('widget_set_prop_int', lambda w, key, v, *_: self.props.__setitem__((w, C.string_at(key)), C.c_int(v).value))
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or len(self.timers))
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()))

    def label(self, parent, *_):
        self.labels.append(C.create_string_buffer(0x400))
        p = C.addressof(self.labels[-1])
        self.children.setdefault(parent, []).append(p)
        return p

    def get_text(self, w, out, size, *_):
        data = self.texts.get(w, '').encode()[:size - 1] + b'\0'
        C.memmove(out, data, len(data))
        return 0

    def prop(self, w, key, default, *_):
        name = C.string_at(key)
        return 290 if name == b'h' else self.props.get((w, name), C.c_int(default).value)

    def open(self):
        self.fw.fn('up_next', None, C.c_void_p)(None)

    def close(self):
        """The page destroyed: the destroy handler page_open hooked on it, upnext.c's own close."""
        *_, (fn, ctx) = [(a[2], a[3]) for _, a in self.fw.calls('widget_on') if a[0] == PAGE and a[1] == EVT_DESTROY]
        assert C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None) == 0

    @property
    def rows(self):
        """Each row's caption and detail."""
        return [tuple(self.texts.get(label) for label in self.children.get(item, [])) for item in self.items]

    @property
    def captions(self):
        return [row[0] for row in self.rows]

    @property
    def said(self):
        """What the page's own labels say (its title, or an empty list's message)."""
        return [self.texts.get(label) for label in self.children.get(PAGE, [])]

    def tap(self, row):
        (fn, ctx), = [(a[2], a[3]) for _, a in self.fw.calls('widget_on') if a[0] == self.items[row] and a[1] == EVT_CLICK]
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)

    def un_hold(self, top=PAGE):
        """navigation.c's ringnav_keylong handing a Play/Pause hold to Up Next, top the page on top: 1 when taken."""
        return self.fw.fn('un_hold', C.c_int, C.c_void_p)(top)

    def run(self):
        """The timer last added, run from the next loop pass."""
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[-1])(None)

    def hold(self, row):
        """Play/Pause held on a row, and the move it defers, run from the next loop pass."""
        self.props[C.addressof(self.view), SEL] = row
        before = len(self.timers)
        assert self.un_hold() == 1 and len(self.timers) == before + 1
        return self.run()

    def selected(self):
        """The list's selected row and its row count, as ringnav_select left them."""
        return self.props.get((C.addressof(self.view), SEL)), self.props.get((C.addressof(self.view), COUNT))


@pytest.fixture
def up(fw, mcl, low):
    queue = Queue(fw, mcl, low)
    return queue, Page(fw)


def test_up_next_lists_what_then_plays_each_title_over_its_artist(fw, up):
    queue, page = up
    page.open()
    assert 'View Queue' in page.said and len(page.rows) == len(NAMES) - 1  # every track but the playing one
    assert all(row[1] == ('Artist %d' % int(row[0][:2]) if int(row[0][:2]) % 2 else '') for row in page.rows)  # '' untagged
    assert page.captions == queue.played(len(NAMES) - 1)


def test_up_next_stops_at_500_rows_of_a_longer_queue(fw, mcl):
    low = Low(1 << 18)
    queue, page = Queue(fw, mcl, low, names=[b'%03d Song.flac' % k for k in range(600)]), Page(fw)
    page.open()
    assert len(page.rows) == 500 and page.captions == queue.played(500)  # the next 500, in play order
    low.close()


def test_a_tapped_row_plays_its_own_track_and_the_page_closes(fw, up):
    queue, page = up
    page.open()
    caption = page.captions[3]
    assert page.tap(3) == 0
    (at, *_), = [a for _, a in fw.calls('player_start_queue')]
    assert queue.name(at) == caption and fw.calls('navigator_back')


def test_a_hold_makes_its_row_s_track_play_next_and_takes_the_rows_again_with_it_first(fw, up):
    queue, page = up
    page.open()
    before = page.captions
    assert page.hold(4) == 0
    assert page.captions == [before[4]] + before[:4] + before[5:] and page.selected() == (0, len(before))
    assert page.toasts == [] and queue.played(2) == [before[4], before[0]]  # the order then plays it


def test_a_hold_on_the_row_already_next_keeps_the_order(fw, up):
    queue, page = up
    page.open()
    before = page.captions
    page.hold(0)
    assert page.captions == before and page.toasts == [] and page.selected() == (0, len(before))
    assert queue.played(len(before)) == before


def test_a_hold_after_the_queue_changed_under_the_page_says_so_and_moves_nothing(fw, up, low):
    queue, page = up
    page.open()
    before = page.captions
    last = NAMES.index(before[-1].encode())
    queue.deques[queue.handle][last] = low.record(b'Other.flac', cue=99)  # the queue replaced under the page
    page.hold(3)
    assert page.toasts == ['Queue changed'] and page.selected()[0] == 0
    assert queue.played(1) == [before[0]]  # not the held row's


def test_a_hold_on_a_row_played_since_says_the_queue_changed(fw, up):
    queue, page = up
    page.open()
    before = page.captions
    assert queue.played(1) == before[:1]
    page.hold(0)
    assert page.toasts == ['Queue changed'] and page.captions == before[1:]  # taken again, without it


def test_a_hold_up_next_doesn_t_take_stays_stock_s_and_moves_nothing(fw, up):
    """Another page on top, no row selected, or no timer for the move: un_hold leaves the hold to stock."""
    queue, page = up
    page.open()
    before = page.captions
    page.props[C.addressof(page.view), SEL] = 4
    assert page.un_hold(0x7f00) == 0 and page.un_hold(None) == 0  # another page on top, or none
    del page.props[C.addressof(page.view), SEL]
    assert page.un_hold() == 0  # Up Next on top, no row selected
    page.props[C.addressof(page.view), SEL] = 4
    fw.mock('timer_add', lambda *_: 0)
    assert page.un_hold() == 0  # nowhere to defer the move to
    assert page.timers == [] and page.toasts == [] and queue.played(len(before)) == before


def test_a_hold_whose_page_closed_before_the_next_pass_moves_nothing(fw, up):
    queue, page = up
    page.open()
    before = page.captions
    page.props[C.addressof(page.view), SEL] = 4
    assert page.un_hold() == 1
    page.close()
    page.run()
    assert page.toasts == [] and queue.played(len(before)) == before


def test_nothing_to_play_next_says_so(fw, mcl, low):
    Queue(fw, mcl, low, names=NAMES[:1], pos=0)
    page = Page(fw)
    page.open()
    assert page.rows == [] and 'Nothing to play next' in page.said


def test_a_closed_page_lets_view_queue_open_a_new_one(fw, up):
    _, page = up
    page.open()
    page.open()  # one at a time
    assert len(fw.calls('window_create')) == 1
    page.close()
    page.open()
    assert len(fw.calls('window_create')) == 2 and len(page.rows) == len(NAMES) - 1
