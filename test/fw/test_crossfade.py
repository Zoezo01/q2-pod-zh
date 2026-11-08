"""patch/crossfade.c: what the next track change asks of hciplayer's fade (xfade_poll, from playback_poll),
Crossfade's page and the pages and rows that share it, and the words demo leaves the player: Swap L/R, MQA
and the wired start's stamps. What each should do is docs/internals.md's (Crossfade, MQA detection, Wired
start) and the code's comments."""
import ctypes as C
import struct

import pytest

from conftest import O, Deques, libc
from test_misc import Xf  # test_misc.py keeps the mirror

XFADE, PRESS, MUTE, SWAP = '/tmp/q2xfade', '/tmp/q2press', '/tmp/q2mute', '/tmp/q2swap'
XF_STOPS = 16  # peq.h: the fade's ms in the low bits, the count of stops above, as the filter reads them
LOCAL, LIBRARY, DLNA, ONLINE = 1, 0xf001, 2, 0xf1
PAGE, BELOW, WM = 0x7000, 0x6000, 0x9000
CLICK, CHANGING, CHANGED, DESTROY = O['EVT_CLICK'], O['EVT_VALUE_CHANGING'], O['EVT_VALUE_CHANGED'], O['EVT_DESTROY']
CENTER, NEXT, PREV = O['KEY_CENTER'], O['KEY_NEXT'], O['KEY_PREV']
HANDLER = C.CFUNCTYPE(C.c_int, C.c_long, C.c_long)  # an AWTK event handler: (ctx, event)
libc.munmap.argtypes = [C.c_void_p, C.c_size_t]


def xf(fw):
    return fw.var('crossfade__xf', Xf)


class Config:
    """config.ini: toolsReadConfig answers from values, else with the caller's default; what was asked, and
    write_int_config's (key, value)s."""
    def __init__(self, fw, values=None):
        self.values = {k: str(v) for k, v in (values or {}).items()}
        self.asked, self.written = [], []
        fw.mock('toolsReadConfig', self.read)
        fw.mock('write_int_config', lambda v, section, key, *_: self.written.append((C.string_at(key).decode(), C.c_int(v).value)))

    def read(self, path, section, key, out, default, *_):
        key = C.string_at(key).decode()
        self.asked.append(key)
        v = self.values.get(key, C.string_at(default).decode()).encode()
        C.memmove(out, v + b'\0', len(v) + 1)


class Shared:
    """The ints in tmpfs demo shares with hciplayer: made under `.tmp`, written whole, mapped and renamed
    into place. files: each renamed path's mapped cell, which reads as the file's zero until written."""
    def __init__(self, fw):
        self.made, self.files, self.full = {}, {}, False
        fw.mock('fw_open', self.open)
        fw.mock('fw_write', lambda fd, data, n, *_: -1 if self.full else n)
        fw.mock('fw_mmap', lambda at, n, prot, flags, fd, *_: C.addressof(self.made[fd][1]))
        fw.mock('fw_rename', self.rename)

    def open(self, path, *_):
        fd = 10 + len(self.made)
        self.made[fd] = (C.string_at(path).decode(), C.c_int(0))
        return fd

    def rename(self, old, new, *_):
        old, new = C.string_at(old).decode(), C.string_at(new).decode()
        self.files[new] = [cell for path, cell in self.made.values() if path == old][-1]

    def __getitem__(self, path):
        """The word as its reader sees it: no file reads as 0."""
        return self.files[path].value if path in self.files else 0


def clock(fw, ms):
    """CLOCK_MONOTONIC at ms (now_ms, the clock both sides of the press stamp read)."""
    fw.mock('fw_clock_gettime', lambda kind, ts, *_: C.memmove(ts, struct.pack('<qq', ms // 1000, ms % 1000 * 1000000), 16) and 0)


@pytest.fixture
def low():
    """Records below 2 GB, as every pointer is on the Q2: xfade_poll keeps the two it compares in its key's ints."""
    at, size = 0x30000000, 0x40000
    assert libc.mmap(at, size, 3, 0x100022, -1, 0) == at  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
    yield [at]
    libc.munmap(at, size)


class Fade:
    """A queue playing, Crossfade on at 7 s and stock's Gapless on: the word playback_poll leaves the filter."""
    def __init__(self, fw, mcl, low, values):
        self.fw, self.mcl, self.low, self.keep = fw, mcl, low, []
        self.config = Config(fw, values)
        self.shared = Shared(fw)
        self.deques = Deques(fw)
        self.queue = self.deques.new()
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        self.missing, self.empty = {'/tmp/notfile', '/tmp/notsupport'}, set()
        self.status, self.toast = 2, 0  # playing, no modal toast up
        fw.mock('fw_access', lambda path, *_: -(C.string_at(path).decode() in self.missing))
        fw.mock('fw___xstat', self.stat)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('navigator_window_is_exist', lambda *_: self.toast)
        self.gapless(1)
        self.poll = fw.fn('playback_poll', None)

    def stat(self, version, path, info, *_):
        """o32's stat: st_size at word 12."""
        C.c_uint.from_address(info + 48).value = 0 if C.string_at(path).decode() in self.empty else 4096
        return 0

    def gapless(self, on):
        C.c_ubyte.from_address(O['MCL_GAPLESS']).value = on

    def song(self, path, album):
        r = self.low[0]
        self.low[0] += 0x60
        for offset, value in ((O['REC_PATH'], path), (O['REC_ALBUM'], album)):
            chars = C.create_string_buffer(value)
            self.keep.append(chars)
            C.c_uint64.from_address(r + offset).value = C.addressof(chars)
        C.c_int.from_address(r + O['REC_TYPE']).value = 8
        return r

    def load(self, *albums, pos=0, paths=None):
        """A track of each album named, its file /m/<album>/<n>.flac unless paths names it."""
        paths = paths or {}
        rows = [self.song(paths.get(i, b'/m/%s/%d.flac' % (a.encode(), i)), a.encode()) for i, a in enumerate(albums)]
        self.deques[self.queue][:] = rows
        self.mcl.pos = pos
        return rows

    def fade(self, pos=None):
        if pos is not None:
            self.mcl.pos = pos
        self.poll()
        return self.shared[XFADE]


@pytest.fixture
def fade(fw, mcl, low):
    return Fade(fw, mcl, low, {'XFADE': 1, 'XFADESEC': 7})


# The policy: a fade only into another album's track, Crossfade and stock's Gapless on.
def test_nothing_playing_on_reads_no_setting_and_makes_no_file(fade):
    fade.load('A')
    fade.poll()
    assert not {'XFADE', 'XFADESEC'} & set(fade.config.asked) and not fade.shared.made


def test_another_albums_next_fades_by_the_saved_seconds_in_ms(fade):
    fade.load('A', 'A', 'B')
    assert fade.fade(0) == 0 and not fade.shared.made  # an album stays gapless, and no file is made for it
    assert fade.fade(1) == 7000 and XFADE in fade.shared.files
    assert fade.fade(0) == 0  # written back to none, the file kept


def test_gapless_off_asks_no_fade(fade):
    fade.load('A', 'B')
    assert fade.fade() == 7000
    fade.gapless(0)
    assert fade.fade() == 0
    fade.gapless(1)
    assert fade.fade() == 7000


def test_crossfade_off_makes_no_file(fw, mcl, low):
    fade = Fade(fw, mcl, low, {'XFADE': 0, 'XFADESEC': 7})
    fade.load('A', 'B')
    assert fade.fade() == 0 and not fade.shared.made and not fw.calls('stock_eq_trampoline')


def test_the_queues_end_has_no_next_but_repeat_all_wraps(fade):
    fade.load('A', 'B', 'A')
    assert fade.fade(0) == 7000
    assert fade.fade(2) == 0  # List Play's last track: nothing after it
    fade.mcl.mode = 3  # Repeat All: stock preloads position 0
    assert fade.fade(2) == 0  # back to the same album
    fade.load('B', 'A', 'A', pos=2)
    assert fade.fade() == 7000


@pytest.mark.parametrize('name', [b'x.dsf', b'x.DFF', b'x.iso'])
def test_a_dsd_next_never_fades_but_one_going_out_does(fade, name):
    fade.load('A', 'B', 'C', paths={1: b'/m/B/' + name})
    assert fade.fade(1) == 7000  # out of DSD: the filter's to pass through, the next mixes the tail
    assert fade.fade(0) == 0  # into DSD: no filter under it to mix the tail


def test_a_missing_or_empty_next_never_fades(fade):
    fade.load('A', 'B', 'C', 'D', paths={3: b'http://host/d.flac'})
    fade.missing |= {'/m/B/1.flac', 'http://host/d.flac'}
    fade.empty.add('/m/C/2.flac')
    assert fade.fade(2) == 7000  # a URL isn't looked for on the card
    assert fade.fade(0) == 0  # a missing file never plays: that song ends whole
    assert fade.fade(1) == 0  # nor does an empty one


def test_a_file_too_big_for_o32s_stat_still_fades(fade):
    fade.load('A', 'B')
    fade.fw.mock('fw___xstat', lambda version, path, info, *_: C.memset(info + 48, 0, 4) and -1)  # EOVERFLOW
    assert fade.fade() == 7000


def test_the_card_is_looked_at_only_when_the_next_track_changes(fw, fade):
    fade.load('A', 'B', 'C')
    looks = lambda: [C.string_at(a[0]) for _, a in fw.calls('fw_access')].count(b'/m/B/1.flac')
    assert fade.fade() == fade.fade() == fade.fade() == 7000 and looks() == 1
    fade.missing.add('/m/B/1.flac')  # gone mid-track: seen at the next change of inputs, not each pass
    assert fade.fade() == 7000 and looks() == 1
    assert fade.fade(1) == 7000 and fade.fade(0) == 0 and looks() == 2


def test_each_stop_moves_the_count_above_the_fade_once(fade):
    fade.load('A', 'B')
    assert fade.fade() == 7000
    fade.status = 1  # stopped
    assert fade.fade() == fade.fade() == 7000 | 1 << XF_STOPS
    fade.status = 2
    assert fade.fade() == 7000 | 1 << XF_STOPS
    fade.status = 3  # paused is no stop
    assert fade.fade() == 7000 | 1 << XF_STOPS
    fade.status = 1
    assert fade.fade() == 7000 | 2 << XF_STOPS


def test_a_file_errors_status_is_no_stop(fade):
    fade.load('A', 'B')
    assert fade.fade() == 7000
    fade.status = 1
    for missing, toast in (({'/tmp/notsupport'}, 0), ({'/tmp/notfile'}, 0), ({'/tmp/notfile', '/tmp/notsupport'}, 1)):
        fade.missing, fade.toast = missing, toast  # the error's file, then stock's modal toast in its place
        assert fade.fade() == 7000
    fade.toast, fade.status = 0, 2  # the next track plays
    assert fade.fade() == 7000
    fade.status = 1  # a real stop after it
    assert fade.fade() == 7000 | 1 << XF_STOPS


def test_a_fade_puts_the_filter_in_the_chain_once_something_plays(fw, fade):
    fade.load('A', 'B')
    chained = fw.var('peq_chained', C.c_int)
    assert fade.fade() == 7000
    fade.status = 1  # stopped: an attach might not stick
    fade.poll()
    assert not fw.calls('stock_eq_trampoline') and chained.value == 0
    fade.status = 2
    fade.poll()
    assert len(fw.calls('stock_eq_trampoline')) == 1 and chained.value == 1


def test_the_count_alone_puts_no_filter_in(fw, fade):
    fade.load('A', 'B')
    chained = fw.var('peq_chained', C.c_int)
    fade.fade()
    fade.status = 1
    fade.fade()
    fade.status = 2
    fade.gapless(0)
    assert fade.fade() == 1 << XF_STOPS
    chained.value = 0  # a new hciplayer, with no filter
    assert fade.fade() == fade.fade() == 1 << XF_STOPS and len(fw.calls('stock_eq_trampoline')) == 1 and chained.value == 0


def test_a_play_next_changes_the_answer_mid_track(fade):
    rows = fade.load('A', 'A', 'A')
    other = fade.song(b'/m/B/9.flac', b'B')
    assert fade.fade(0) == 0
    fade.deques[fade.queue][1:1] = [other]
    assert fade.fade() == 7000
    fade.deques[fade.queue][:] = rows
    assert fade.fade() == 0


def test_a_file_that_cannot_be_made_is_tried_next_pass(fade):
    fade.load('A', 'B')
    fade.shared.full = True  # a full tmpfs: nothing mapped, nothing stored
    assert fade.fade() == 0 and XFADE not in fade.shared.files
    fade.shared.full = False
    assert fade.fade() == 7000


# Stock Shuffle: its next drawn ahead, counted as another album's.
def shuffled(fade, pool, albums):
    rows = fade.load(*albums)
    fade.mcl.mode = 2
    fade.cells = (C.c_int * len(pool))(*pool)
    C.c_uint64.from_address(O['MCL_POOL']).value = fade.deques.new([C.addressof(fade.cells) + 4 * i for i in range(len(pool))])
    return rows


def test_stock_shuffles_next_fades_whatever_its_album(fade):
    shuffled(fade, range(4), 'AAAA')
    assert fade.fade() == 7000


def test_stock_shuffle_plays_the_draw_crossfade_asked_about(fw, fade):
    shuffled(fade, range(7), 'AAAAAAA')
    peek = fw.fn('playback_peek', C.c_int, C.c_uint)
    looks = lambda: sum(C.string_at(a[0]).startswith(b'/m/') for _, a in fw.calls('fw_access'))
    drawn = []
    for _ in range(80):
        fade.fade()
        before = looks()
        fade.fade()
        assert looks() == before  # a pass with the same queue keeps its draw: no other next to look for on the card
        drawn.append(peek(0))
        assert peek(0) == -1  # taken once
    assert set(drawn) == set(range(1, 7))  # any of the pool but the playing one


def test_a_draw_on_a_queue_that_changed_is_not_forced(fw, fade):
    rows = shuffled(fade, range(3), 'AAA')
    fade.fade()
    fade.deques[fade.queue][:] = [rows[0], rows[2], rows[1]]
    assert fw.fn('playback_peek', C.c_int, C.c_uint)(0) == -1


def test_a_dsd_draw_asks_no_fade(fw, fade):
    shuffled(fade, [0, 1], 'AB')
    assert fade.fade() == 7000 and fw.fn('playback_peek', C.c_int, C.c_uint)(0) == 1  # the one other track
    fade.deques[fade.queue][1] = fade.song(b'/m/B/x.dsf', b'B')
    assert fade.fade() == 0


def test_under_two_in_the_pool_the_draw_is_unknown_and_still_fades(fw, fade):
    shuffled(fade, [0], 'AA')
    assert fade.fade() == 7000 and fw.fn('playback_peek', C.c_int, C.c_uint)(0) == -1


# The settings rows: each names its label, so a change on the page shows on it.
def digits(text):
    return ''.join(ch for ch in text if ch.isdigit())


@pytest.mark.parametrize('values, shown', [({'XFADE': 1, 'XFADESEC': 9}, '9'), ({'XFADE': 1, 'XFADESEC': 99}, '5'),
                                           ({'XFADE': 0, 'XFADESEC': 9}, ''), ({}, '')])
def test_the_crossfade_row_shows_its_length_or_off(fw, values, shown):
    assert digits(Page(fw, values).row_text('xfade_row')) == shown


@pytest.mark.parametrize('values, shown', [({}, '30'), ({'RESUMEMIN': '45'}, '45'), ({'RESUMEMIN': '0'}, '')])
def test_the_resume_row_shows_its_minutes_or_off(fw, values, shown):
    assert digits(Page(fw, values).row_text('resume_row')) == shown  # unset: the default half hour


def test_the_night_shift_row_shows_off_its_temperature_or_auto(fw):
    page = Page(fw, {'NIGHTK': 2700})
    texts = []
    for on in (0, 1, 2):
        fw.fn('night_set', None, C.c_int, C.c_int)(on, 2700)
        texts.append(page.row_text('night_row'))
    assert len(set(texts)) == 3 and [digits(t) for t in texts] == ['', '2700', '']


def test_the_wheel_row_shows_its_sensitivity(fw):
    page = Page(fw, {})
    assert digits(page.row_text('wheel_row')) == '50'  # unset: 50%
    fw.fn('wheel_set_pct', None, C.c_int)(120)
    assert digits(page.row_text('wheel_row')) == '120'


# The page: stock's Boot volume page, re-dressed.
class Page:
    """Stock's Boot volume page as xfade_open finds it once navigator_to has it on top, and the settings rows
    on the window manager. shows: the value stock's slider reads."""
    NAMES = 'view_navbar img_bootswitch view_bootvol slider_bootvol img_dec img_add label_vol'.split()

    def __init__(self, fw, values=None, name=b'bootvol_page', missing=(), opens=True):
        self.fw, self.config = fw, Config(fw, values)
        self.top, self.opens = BELOW, opens
        self.widgets = {n: PAGE + 0x100 + 0x10 * i for i, n in enumerate(self.NAMES) if n not in missing}
        self.title = PAGE + 0x200
        self.rows, self.text, self.images, self.hidden, self.enabled, self.bound = {}, {}, {}, set(), set(), []
        self.shows, self.moved = 0, []
        self.name = C.create_string_buffer(name)
        fw.mock('window_manager', lambda *_: WM)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('navigator_to', lambda *_: self.opens and setattr(self, 'top', PAGE))
        fw.mock('widget_lookup', self.lookup)
        fw.mock('widget_get_child', lambda w, i, *_: self.title if w == self.widgets.get('view_navbar') and i == 0 else 0)
        fw.mock('widget_get_prop_str', lambda w, key, default, *_: C.addressof(self.name) if w == PAGE else default)
        fw.mock('widget_get_prop_int', lambda w, key, default, *_: self.shows if w == self.widgets.get('slider_bootvol') else default)
        fw.mock('widget_set_text_utf8', lambda w, t, *_: self.text.__setitem__(w, C.string_at(t).decode()))
        fw.mock('image_base_set_image', lambda w, image, *_: self.images.__setitem__(w, C.string_at(image).decode()))
        fw.mock('widget_set_visible', lambda w, visible, *_: (self.hidden.discard if visible else self.hidden.add)(w))
        fw.mock('widget_set_enable', lambda w, on, *_: (self.enabled.add if on else self.enabled.discard)(w))
        fw.mock('widget_on', lambda w, event, f, ctx, *_: self.bound.append((w, event, f, ctx)) or len(self.bound))
        fw.mock('widget_set_name', lambda w, row, *_: self.rows.__setitem__(C.string_at(row).decode(), w))
        fw.mock('slider_set_value', lambda w, *_: self.moved.append(w))  # its value is a double, out of the mock's reach

    def lookup(self, parent, name, *_):
        name = C.string_at(name).decode()
        return self.widgets.get(name, 0) if parent == PAGE else self.rows.get(name, 0) if parent == WM else 0

    def row_text(self, maker, label=0x7400):
        self.fw.fn(maker, None, C.c_void_p)(label)
        return self.text[label]

    def open(self, mode=0):
        return self.fw.fn('xfade_open', C.c_int, C.c_void_p, C.c_void_p)(mode, None)

    def fire(self, widget, event):
        """The handler xfade_open bound for widget's event, as AWTK calls it: with the context it was bound with."""
        w = PAGE if widget == 'page' else self.widgets[widget]
        f, ctx = [(f, ctx) for w_, e, f, ctx in self.bound if (w_, e) == (w, event)][-1]
        return HANDLER(f)(ctx, 0)

    def label(self):
        return self.text[self.widgets['label_vol']]

    def switch(self):
        return self.images.get(self.widgets['img_bootswitch'])

    def key(self, key, top=PAGE):
        return self.fw.fn('xfade_key', C.c_int, C.c_void_p, C.c_uint)(top, key)


def opened(fw, mode=0, values=None):
    page = Page(fw, values)
    assert page.open(mode) == 0 and xf(fw).page == PAGE
    return page


def test_the_page_swaps_stock_boot_volumes_handlers_for_its_own(fw):
    page = opened(fw, 0, {'XFADE': 1, 'XFADESEC': 5})
    w = page.widgets
    stock = {(w['img_bootswitch'], CLICK, O['BOOTVOL_SWITCH']), (w['img_dec'], CLICK, O['BOOTVOL_DEC']),
             (w['img_add'], CLICK, O['BOOTVOL_ADD']), (w['slider_bootvol'], CHANGING, O['BOOTVOL_CHANGING']),
             (w['slider_bootvol'], CHANGED, O['BOOTVOL_CHANGED'])}
    assert {(a[0], a[1], a[2]) for _, a in fw.calls('widget_off_by_func')} == stock
    assert all(a[3] == PAGE for _, a in fw.calls('widget_off_by_func'))  # stock bound each with the window
    assert {(b[0], b[1]) for b in page.bound} == {(x, e) for x, e, _ in stock} | {(PAGE, DESTROY)}
    assert not {b[2] for b in page.bound} & {f for _, _, f in stock}
    assert w['view_bootvol'] in page.enabled and w['view_bootvol'] not in page.hidden  # touch reaches the slider (#140)
    is_page = fw.fn('xfade_page', C.c_int, C.c_void_p)
    assert is_page(PAGE) == 1 and is_page(BELOW) == 0 and is_page(0) == 0


@pytest.mark.parametrize('mode, maker, switched', [(0, 'xfade_row', True), (1, 'wheel_row', False),
                                                    (2, 'resume_row', False), (3, 'night_row', True)])
def test_each_row_opens_the_page_under_its_own_name(fw, mode, maker, switched):
    page = Page(fw, {})
    row = page.row_text(maker)
    assert page.open(mode) == 0
    assert row.startswith(page.text[page.title])  # the row's name is the page's title
    assert (page.widgets['img_bootswitch'] in page.hidden) == (not switched)


@pytest.mark.parametrize('how', ['name', *Page.NAMES])
def test_a_page_that_is_not_boot_volume_is_closed(fw, how):
    page = Page(fw, {}, name=b'other_page' if how == 'name' else b'bootvol_page', missing=() if how == 'name' else (how,))
    assert page.open() == 0
    assert [a[0] for _, a in fw.calls('window_close')] == [PAGE] and not page.bound
    assert fw.fn('xfade_page', C.c_int, C.c_void_p)(PAGE) == 0


def test_a_page_that_never_opened_is_left_alone(fw):
    page = Page(fw, {}, opens=False)
    assert page.open() == 0 and not fw.calls('window_close') and not page.bound and xf(fw).page is None


def test_an_open_page_is_not_opened_again(fw):
    page = opened(fw)
    assert page.open(2) == 0 and len(fw.calls('navigator_to')) == 1 and xf(fw).ctx == 0


def test_plus_and_minus_save_each_step(fw):
    page = opened(fw, 0, {'XFADE': 1, 'XFADESEC': 5})
    page.fire('img_add', CLICK)
    assert xf(fw).seconds == 6 and page.config.written == [('XFADE', 1), ('XFADESEC', 6)] and '6' in page.label()
    page.fire('img_dec', CLICK), page.fire('img_dec', CLICK)
    assert xf(fw).seconds == 4 and page.config.written[-1] == ('XFADESEC', 4) and '4' in page.label()


def test_the_switch_turns_it_on_and_off_and_keeps_the_length(fw):
    page = opened(fw, 0, {'XFADE': 0, 'XFADESEC': 8})
    off, off_switch = page.label(), page.switch()
    page.fire('img_bootswitch', CLICK)
    assert xf(fw).on == 1 and page.config.written == [('XFADE', 1), ('XFADESEC', 8)]
    assert '8' in page.label() and page.switch() != off_switch
    page.fire('img_bootswitch', CLICK)
    assert (page.label(), page.switch()) == (off, off_switch) and page.config.written[-2:] == [('XFADE', 0), ('XFADESEC', 8)]


def test_a_drag_plays_each_value_and_saves_once_at_its_release(fw):
    page = opened(fw, 0, {'XFADE': 1, 'XFADESEC': 5})
    for value in (9, 11):
        page.shows = value
        page.fire('slider_bootvol', CHANGING)
        assert xf(fw).seconds == value and str(value) in page.label() and not page.config.written
    page.fire('slider_bootvol', CHANGED)
    assert page.config.written == [('XFADE', 1), ('XFADESEC', 11)]


def test_a_drag_to_off_keeps_the_length_for_the_switch(fw):
    page = opened(fw, 0, {'XFADE': 1, 'XFADESEC': 6})
    page.shows = 0
    page.fire('slider_bootvol', CHANGING), page.fire('slider_bootvol', CHANGED)
    assert page.config.written == [('XFADE', 0), ('XFADESEC', 6)]
    page.fire('img_bootswitch', CLICK)
    assert (xf(fw).on, xf(fw).seconds) == (1, 6)


def test_a_drag_whose_release_never_came_saves_with_the_switch(fw):
    page = opened(fw, 0, {'XFADE': 1, 'XFADESEC': 5})
    page.shows = 9
    page.fire('slider_bootvol', CHANGING)
    page.fire('img_bootswitch', CLICK)
    assert page.config.written == [('XFADE', 0), ('XFADESEC', 9)]


def test_closing_the_page_mid_drag_saves_and_lets_it_go(fw):
    page = opened(fw, 0, {'XFADE': 1, 'XFADESEC': 5})
    page.shows = 12
    page.fire('slider_bootvol', CHANGING)
    page.fire('page', DESTROY)
    assert page.config.written == [('XFADE', 1), ('XFADESEC', 12)]
    assert fw.fn('xfade_page', C.c_int, C.c_void_p)(PAGE) == 0 and page.key(NEXT) == 0
    page.top = BELOW  # the page gone, Playback is on top again
    assert page.open(1) == 0 and xf(fw).ctx == 1  # opens again, for another row


def test_the_slider_moves_only_to_a_new_value_and_its_own_move_is_no_release(fw):
    page = Page(fw, {'XFADE': 1, 'XFADESEC': 5})
    page.shows = 5
    assert page.open() == 0 and not page.moved
    page.shows = 2  # stale: it still shows a value the page left

    def set_value(w, *_):  # AWTK's slider sends value-changed for a set too
        page.moved.append(w)
        page.fire('slider_bootvol', CHANGED)
    fw.mock('slider_set_value', set_value)
    page.fire('img_add', CLICK)
    # moved once, to the new length; its own value-changed (the stale 2) taken for no release
    assert len(page.moved) == 1 and xf(fw).seconds == 6 and page.config.written == [('XFADE', 1), ('XFADESEC', 6)]


def test_the_rows_follow_the_page(fw):
    page = Page(fw, {'XFADE': 1, 'XFADESEC': 5})
    rows = {maker: 0x7300 + 0x10 * i for i, maker in enumerate(('xfade_row', 'wheel_row', 'resume_row', 'night_row'))}
    for maker, label in rows.items():
        page.row_text(maker, label)
    before = {label: page.text[label] for label in rows.values()}
    assert page.open() == 0
    page.fire('img_add', CLICK)
    assert '6' in page.text[rows['xfade_row']] and page.text[rows['xfade_row']] != before[rows['xfade_row']]
    assert all(page.text[rows[m]] == before[rows[m]] for m in ('wheel_row', 'resume_row', 'night_row'))


def test_the_wheel_sets_the_sensitivity_a_ten_a_tick(fw):
    page = opened(fw, 1, {'WHEELSENSITIVITY': '190'})
    pct = fw.fn('wheel_pct', C.c_int)
    assert page.key(NEXT) == 1 and pct() == 200 and page.config.written == [('WHEELSENSITIVITY', 200)] and '200' in page.label()
    assert page.key(NEXT) == 1 and pct() == 200 and len(page.config.written) == 1  # its top: nothing to save
    assert page.key(PREV) == 1 and page.key(PREV) == 1 and pct() == 180 and page.config.written[-1] == ('WHEELSENSITIVITY', 180)
    assert page.key(CENTER) == 0  # no switch: Centre is stock's, which goes back


def test_resume_songs_steps_in_fives_down_to_off(fw):
    page = opened(fw, 2, {'RESUMEMIN': '25'})
    page.fire('img_add', CLICK)
    assert xf(fw).rs_min == 30 and page.config.written == [('RESUMEMIN', 30)] and '30' in page.label()
    page.key(PREV), page.key(PREV)
    assert xf(fw).rs_min == 20
    page.key(PREV)
    assert xf(fw).rs_min == 0 and page.config.written[-1] == ('RESUMEMIN', 0) and not any(c.isdigit() for c in page.label())


def test_night_shifts_switch_cycles_off_on_and_auto(fw):
    page = opened(fw, 3, {'NIGHTSHIFT': 0, 'NIGHTK': 3000})
    seen = [(page.label(), page.switch())]
    for _ in range(3):
        page.fire('img_bootswitch', CLICK)
        seen.append((page.label(), page.switch()))
    assert [v for k, v in page.config.written if k == 'NIGHTSHIFT'] == [1, 2, 0]
    assert seen[3] == seen[0] and len({label for label, _ in seen[:3]}) == 3 and '3000' in seen[1][0]
    assert seen[1][1] == seen[2][1] != seen[0][1]  # On and Auto both light the switch


def test_night_shifts_wheel_warms_a_step_a_tick_and_turns_it_on(fw):
    page = opened(fw, 3, {'NIGHTSHIFT': 0, 'NIGHTK': 3000})
    kelvin = fw.fn('night_kelvin', C.c_int)
    assert page.key(NEXT) == 1
    one = kelvin()
    assert one < 3000 and fw.fn('night_on', C.c_int)() == 1 and str(one) in page.label()
    assert page.key(NEXT) == 1 and 3000 - kelvin() == 2 * (3000 - one) and str(kelvin()) in page.label()


class Centre:
    """A centre press's clock and AWTK's timers, which belong to no window: the one that tells a single press
    from a double, pending until it is removed or AWTK runs it."""
    def __init__(self, fw):
        self.now, self.timers, self.ids = 10000, {}, iter(range(77, 1 << 16))
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('timer_add', self.add)
        fw.mock('timer_remove', lambda timer, *_: self.timers.pop(timer, None) and 0)

    def add(self, f, ctx, ms, *_):
        timer = next(self.ids)
        self.timers[timer] = (f, ms)
        return timer

    def fire(self):
        """The pending timers run, as AWTK's loop runs each once it is due."""
        for timer in list(self.timers):
            f, _ = self.timers.pop(timer)
            C.CFUNCTYPE(C.c_int, C.c_void_p)(f)(None)


def test_centre_switches_once_a_double_press_can_no_longer_come(fw):
    page = opened(fw, 0, {'XFADE': 0, 'XFADESEC': 5})
    centre = Centre(fw)
    assert page.key(CENTER) == 1 and xf(fw).on == 0 and [ms for _, ms in centre.timers.values()] == [O['DOUBLE_CLICK_MS']]
    centre.fire()
    assert xf(fw).on == 1 and page.config.written == [('XFADE', 1), ('XFADESEC', 5)]


@pytest.mark.xfail(strict=True, reason='BUG: crossfade.c gone() clears xf.page and xf.ctx but not xf.press, the single-press '
                   'timer center_press armed; when the page closes within DOUBLE_CLICK_MS of one Centre press, single() runs '
                   'toggle() with xf.ctx 0 and flips and saves Crossfade (XFADE, XFADESEC) instead of Night Shift')
def test_a_centre_press_on_night_shift_closed_at_once_switches_no_crossfade(fw):
    page = opened(fw, 3, {'XFADE': 0, 'XFADESEC': 5, 'NIGHTSHIFT': 0, 'NIGHTK': 3000})
    centre = Centre(fw)
    assert page.key(CENTER) == 1
    page.fire('page', DESTROY)  # Back within DOUBLE_CLICK_MS: the page goes; AWTK's timer does not go with it
    centre.fire()
    assert xf(fw).on == 0 and not {'XFADE', 'XFADESEC'} & {k for k, _ in page.config.written}


@pytest.mark.parametrize('lit', [0, 1])
def test_a_double_centre_press_turns_a_lit_screen_off_and_switches_nothing(fw, lit):
    page = opened(fw, 0, {'XFADE': 0, 'XFADESEC': 5})
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    centre = Centre(fw)
    page.key(CENTER)
    centre.now += O['DOUBLE_CLICK_MS'] - 1
    assert page.key(CENTER) == 2 and xf(fw).on == 0 and [a[0] for _, a in fw.calls('timer_remove')] == [77]
    assert not centre.timers  # no single press left to come
    replays = fw.calls('on_wm_keyup_fun')
    assert len(replays) == lit and all(C.c_uint.from_address(a[1] + O['EVENT_KEY']).value == CENTER for _, a in replays)


def test_a_press_after_an_overdue_first_switches_at_once(fw):
    page = opened(fw, 0, {'XFADE': 0, 'XFADESEC': 5})
    centre = Centre(fw)
    page.key(CENTER)
    centre.now += O['DOUBLE_CLICK_MS']
    assert page.key(CENTER) == 0 and xf(fw).on == 1


# Swap L/R and MQA's rows.
class Row:
    """A settings row's label in its button, its switch (or, for a set_text theme, its value) found: what it shows."""
    def __init__(self, fw, maker):
        self.label, self.button, self.shown = C.create_string_buffer(0x60), C.create_string_buffer(0x60), {}
        struct.pack_into('<Q', self.label, O['W_PARENT'], C.addressof(self.button))
        fw.mock('widget_lookup', lambda *_: 0x7300)
        fw.mock('image_base_set_image', lambda w, image, *_: self.shown.__setitem__('image', C.string_at(image)))
        fw.mock('widget_set_text_utf8', lambda w, t, *_: w == 0x7300 and self.shown.__setitem__('value', C.string_at(t)))
        fw.fn(maker, None, C.c_void_p)(self.label)

    def state(self):
        return dict(self.shown)


def test_swap_click_saves_and_tells_the_player(fw):
    config, shared = Config(fw), Shared(fw)
    fw.var('g_equalizer_flag', C.c_ubyte).value = 1
    row = Row(fw, 'swap_row')
    off = row.state()
    click = fw.fn('swap_click', C.c_int, C.c_void_p, C.c_void_p)
    click(None, None)
    assert config.written == [('SWAPLR', 1)] and shared[SWAP] == 1 and row.state() != off
    click(None, None)
    assert config.written[-1] == ('SWAPLR', 0) and shared[SWAP] == 0 and row.state() == off and len(shared.made) == 1


MQA_ON, MQA_OFF = b'{mcl-mqadecodemode\\0}', b'{mcl-mqadecodemode\\2}'  # hciplayer's default, and the probe skipped


def sends(fw):
    return [(a[0], C.string_at(a[1], a[2])) for _, a in fw.calls('fw_send')]


def test_mqa_click_saves_and_sends_the_player_its_mode(fw, mcl):
    config = Config(fw)
    mcl.fd = 5
    row = Row(fw, 'mqa_row')
    off = row.state()
    click = fw.fn('mqa_click', C.c_int, C.c_void_p, C.c_void_p)
    click(None, None)
    assert config.written == [('MQA', 1)] and sends(fw) == [(5, MQA_ON)] and row.state() != off
    click(None, None)
    assert config.written[-1] == ('MQA', 0) and sends(fw)[-1] == (5, MQA_OFF) and row.state() == off


def test_mqa_click_with_no_player_sends_nothing(fw, mcl):
    Config(fw)
    mcl.fd = -1
    Row(fw, 'mqa_row')
    fw.fn('mqa_click', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert not fw.calls('fw_send')


@pytest.mark.parametrize('saved, fd, sent', [(None, 6, MQA_OFF), ('1', 6, MQA_ON), ('0', 0, None), ('1', -1, None)])
def test_each_new_player_hears_the_saved_mode_before_stock_has_it(fw, saved, fd, sent):
    Config(fw, {} if saved is None else {'MQA': saved})
    fw.mock('fw_accept', lambda *_: fd)
    assert fw.fn('mqa_accept', C.c_int, C.c_int, C.c_void_p, C.c_void_p)(3, None, None) == fd
    assert sends(fw) == ([] if sent is None else [(fd, sent)])


# The wired start: a press stamps PRESS_FILE odd; a natural start skips stock's mute and sleep and leaves an
# even mark for ao_init.
class Wired:
    def __init__(self, fw, mcl, kind=LOCAL, ms=5000):
        self.fw, self.mcl, self.shared = fw, mcl, Shared(fw)
        mcl.type = kind
        clock(fw, ms)
        self.starts = []
        self.natural = fw.var('press_natural', C.c_int)
        self.mute = fw.fn('start_mute', C.c_int, C.c_int)
        self.sleep = fw.fn('start_sleep', C.c_int, C.c_uint)

    def stock_start(self, *_):
        """mclStartPlayer's mute and sleep, the calls the build sends to start_mute and start_sleep."""
        self.starts.append((self.mute(1), self.sleep(150000), self.shared[PRESS]))
        return 0


@pytest.fixture
def wired(fw, mcl):
    return Wired(fw, mcl)


@pytest.mark.parametrize('kind, stamps', [(LOCAL, True), (LIBRARY, True), (0xff10, True), (DLNA, False), (3, False),
                                          (ONLINE, False), (0xf2, False)])
def test_a_press_stamps_only_a_local_queue(fw, mcl, kind, stamps):
    wired = Wired(fw, mcl, kind)
    fw.fn('press_stamp', None)()
    assert (PRESS in wired.shared.files) == stamps and wired.shared[PRESS] == (5001 if stamps else 0)


def test_a_press_stamp_is_the_clocks_low_32_bits_made_odd(fw, mcl):
    wired = Wired(fw, mcl, ms=(1 << 32) + 1002)
    fw.fn('press_stamp', None)()
    assert wired.shared[PRESS] == 1003


def test_nothing_stamps_inside_a_natural_change(wired):
    wired.natural.value = 1
    wired.fw.fn('press_stamp', None)()
    assert not wired.shared.made


def test_a_natural_next_skips_the_mute_and_sleep_and_leaves_an_even_mark(fw, wired):
    fw.mock('stock_next_trampoline', wired.stock_start)
    fw.fn('ringnav_next', C.c_int, C.c_int)(1)
    assert wired.starts == [(0, 0, 5000)] and not fw.calls('mclSetMute') and not fw.calls('fw_usleep')
    assert wired.natural.value == 0


def test_a_pressed_next_mutes_and_sleeps_and_keeps_its_stamp(fw, wired):
    fw.mock('stock_change_trampoline', lambda *_: wired.stock_start())
    fw.fn('ringnav_change', C.c_int, C.c_int)(1)
    assert wired.starts == [(0, 0, 5001)]
    assert [a[0] for _, a in fw.calls('mclSetMute')] == [1] and [a[0] for _, a in fw.calls('fw_usleep')] == [150000]


@pytest.mark.parametrize('ms, mark', [(0, 2), (1, 2), (7, 6), ((1 << 32) + 9, 8)])
def test_a_natural_mark_is_the_clock_made_even_and_never_0(fw, mcl, ms, mark):
    wired = Wired(fw, mcl, ms=ms)
    wired.natural.value = 2
    assert wired.mute(1) == 0 and wired.shared[PRESS] == mark


def test_a_natural_marks_quiet_start_is_spent_once(fw, wired):
    wired.natural.value = 2
    assert wired.mute(1) == 0 and wired.sleep(100) == 0
    assert wired.sleep(100) == 0 and [a[0] for _, a in fw.calls('fw_usleep')] == [100]


def test_a_start_nobody_pressed_for_drops_an_unspent_mark(fw, wired):
    fw.mock('stock_next_trampoline', wired.stock_start)
    fw.fn('ringnav_next', C.c_int, C.c_int)(1)
    assert wired.shared[PRESS] == 5000
    wired.stock_start()  # an output's change starts the track again: mclStartPlayer, no press
    assert wired.starts[-1] == (0, 0, 0) and len(fw.calls('mclSetMute')) == 1


def test_a_natural_start_with_no_word_keeps_stocks_mute(fw, wired):
    wired.shared.full = True  # tmpfs full: no word to leave a mark in
    fw.mock('stock_next_trampoline', wired.stock_start)
    fw.fn('ringnav_next', C.c_int, C.c_int)(1)
    assert len(fw.calls('mclSetMute')) == 1 and len(fw.calls('fw_usleep')) == 1


def test_a_natural_next_forgets_the_last_press(fw, wired):
    fw.fn('ringnav_change', C.c_int, C.c_int)(1)
    assert wired.shared[PRESS] == 5001
    fw.fn('ringnav_next', C.c_int, C.c_int)(1)
    assert wired.shared[PRESS] == 0


@pytest.mark.parametrize('status, word', [(1, PRESS), (2, MUTE), (3, None)])
def test_play_from_a_stop_stamps_the_press_and_pause_the_mute(fw, wired, status, word):
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.fn('ringnav_play_pause', C.c_int)()
    assert set(wired.shared.files) == ({word} if word else set()) and (not word or wired.shared[word] == 5001)
    assert len(fw.calls('stock_pause_trampoline')) == 1
