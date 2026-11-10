"""patch/navigation.c's list engine: the wheel's pane, its rows, position memory and reveal, Centre's
confirm, the edge bump and wrap, the carousel's step, pull to search, the time zone rows, and what is
painted over a list (the chevrons, the fast-scroll letter, the art's corners, Minimal's capitals)."""
import ctypes as C
import json
import math
import re
import sqlite3
import struct
from collections import Counter

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, Deques, claim, ui_name
from test_coverflow import COVERS, TRACKS, Home, Rec, cf, deque_of  # coverflow.c's home struct, its page and an album

W_PARENT, W_EMITTER = O['W_PARENT'], O['W_EMITTER']
KEY_NEXT, KEY_PREV, KEY_CENTER = O['KEY_NEXT'], O['KEY_PREV'], O['KEY_CENTER']
EVT_CLICK = O['EVT_CLICK']
SCROLL_X, SCROLL_Y = O['SCROLL_X'], O['SCROLL_Y']
# navigation.c's own tuning, read from its source so a retune moves the tests with it
NAV = {k: int(v) for k, v in re.findall(r'^#define (\w+) (\d+)\b', NAVIGATION_SOURCE, re.M)}
SEL, COUNT = b'_ringnav_index', b'_ringnav_count'


class Widget:
    """One AWTK widget: a zeroed block holding what the C reads at fixed offsets (its box, parent,
    emitter and view fields); its type, props, text and children are the stubs' to answer."""
    def __init__(self, kind, parent=None, box=(0, 0, 0, 0), name=None, text=None, click=False, visible=True):
        self.block = C.create_string_buffer(0x200)
        self.at = C.addressof(self.block)
        self.kind, self.children, self.visible, self.text = kind.encode(), [], visible, text
        self.props = {} if name is None else {b'name': name.encode()}
        struct.pack_into('<4i', self.block, 0, *box)
        self.parent = parent
        if parent is not None:
            struct.pack_into('<Q', self.block, W_PARENT, parent.at)
            parent.children.append(self)
        if click:  # an EVT_CLICK handler in its emitter: a tap target
            self.emitter, self.item = C.create_string_buffer(0x10), C.create_string_buffer(0x30)
            struct.pack_into('<i', self.item, O['EMIT_TYPE'], EVT_CLICK)
            struct.pack_into('<Q', self.emitter, 0, C.addressof(self.item))
            struct.pack_into('<Q', self.block, W_EMITTER, C.addressof(self.emitter))

    def int(self, offset, value=None):
        if value is not None:
            struct.pack_into('<i', self.block, offset, value)
        return struct.unpack_from('<i', self.block, offset)[0]

    def byte(self, offset, value=None):
        if value is not None:
            self.block[offset] = value
        return self.block[offset][0]

    def ptr(self, offset, value=None):
        if value is not None:
            struct.pack_into('<Q', self.block, offset, value)
        return struct.unpack_from('<Q', self.block, offset)[0]

    @property
    def box(self):
        return struct.unpack_from('<4i', self.block, 0)

    def origin(self):
        """Its top-left on screen: the boxes summed up to the window manager."""
        x = y = 0
        w = self
        while w is not None and w.kind != b'window_manager':
            x, y, w = x + w.box[0], y + w.box[1], w.parent
        return x, y


class Ui:
    """AWTK's window manager, widgets and timers over Widgets, and the clock (time_now_ms)."""
    def __init__(self, fw):
        self.fw, self.by, self.strings = fw, {}, {}
        self.wm = self.widget('window_manager')
        self.top = None
        self.animating = self.pressed = 0
        self.now = 10000
        self.glides, self.glide_arrives = [], True
        self.timers, self.next_timer, self.invalidated = {}, 1, []
        mock = fw.mock
        mock('window_manager', lambda *_: self.wm.at)
        mock('window_manager_get_top_window', lambda *_: self.top.at if self.top else 0)
        mock('window_manager_is_animating', lambda *_: self.animating)
        mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        mock('widget_get_type', lambda w, *_: self.cstr(self.by[w].kind) if w in self.by else 0)
        mock('widget_get_prop_str', self.prop_str)
        mock('widget_get_prop_int', self.prop_int)
        mock('widget_get_prop_bool', self.prop_int)
        mock('widget_set_prop_int', lambda w, name, v, *_: self.by[w].props.__setitem__(C.string_at(name), C.c_int(v).value))
        mock('widget_set_prop_str', lambda w, name, v, *_: self.by[w].props.__setitem__(C.string_at(name), C.string_at(v)))
        mock('widget_get_visible', lambda w, *_: w in self.by and self.by[w].visible)
        mock('widget_count_children', lambda w, *_: len(self.by[w].children) if w in self.by else 0)
        mock('widget_get_child', self.child)
        mock('widget_get_text', self.text)
        mock('widget_lookup', lambda w, name, *_: self.lookup(self.by.get(w), C.string_at(name)))
        mock('widget_invalidate_force', lambda w, *_: self.invalidated.append(w))
        mock('timer_add', self.timer_add)
        mock('timer_remove', lambda t, *_: self.timers.pop(t, None) and 0)
        mock('time_now_ms', lambda *_: self.now)
        mock('scroll_view_set_offset', self.set_offset)
        mock('scroll_view_scroll_delta_to', self.glide)
        mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))

    def widget(self, kind, parent=None, box=(0, 0, 0, 0), **kwargs):
        w = Widget(kind, parent, box, **kwargs)
        self.by[w.at] = w
        return w

    def cstr(self, value):
        if value not in self.strings:
            self.strings[value] = C.create_string_buffer(value)
        return C.addressof(self.strings[value])

    def prop_str(self, w, name, default, *_):
        value = self.by[w].props.get(C.string_at(name)) if w in self.by else None
        return self.cstr(value) if isinstance(value, bytes) else default

    def prop_int(self, w, name, default, *_):
        value = self.by[w].props.get(C.string_at(name)) if w in self.by else None
        return value if isinstance(value, int) else C.c_int(default).value

    def child(self, w, i, *_):
        children = self.by[w].children if w in self.by else []
        return children[i].at if 0 <= i < len(children) else 0

    def text(self, w, *_):
        widget = self.by.get(w)
        if widget is None or widget.text is None:
            return 0
        return self.cstr(widget.text.encode('utf-32-le') + b'\0' * 4)

    def lookup(self, w, name):
        for c in w.children if w else ():
            if c.props.get(b'name') == name:
                return c.at
            found = self.lookup(c, name)
            if found:
                return found
        return 0

    def set_offset(self, w, x, y, *_):
        self.by[w].int(SCROLL_X, x)
        self.by[w].int(SCROLL_Y, y)

    def glide(self, w, dx, dy, ms, *_):
        """scroll_view's animated scroll by dy over ms: arrived at once, or (glide_arrives off) cut short."""
        self.glides.append((dy, ms))
        if self.glide_arrives:
            self.by[w].int(SCROLL_Y, self.by[w].int(SCROLL_Y) + C.c_int(dy).value)

    def timer_add(self, fn, ctx, ms, *_):
        t, self.next_timer = self.next_timer, self.next_timer + 1
        self.timers[t] = (fn, ctx, ms)
        return t

    def due(self, fn_name):
        """The live timers whose callback is navigation.c's static fn_name."""
        address = C.cast(getattr(self.fw.lib, ui_name(self.fw.lib, fn_name)), C.c_void_p).value
        return [t for t, (fn, _, _) in self.timers.items() if fn == address]

    def fire(self, t):
        """Timer t's callback, as the loop runs it when it is due; gone unless it asks to repeat (8)."""
        fn, ctx, _ = self.timers[t]
        result = C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(ctx)
        if result != 8:
            self.timers.pop(t, None)
        return result


ROW_H, VIEW_H = 30, 240


def scroll_list(ui, parent, titles, box=(0, 0, 375, VIEW_H), subtitles=None):
    """A vertical scroll_view in parent with a row ROW_H high per title: a tappable list_item, its
    label's text the row's title, a second label its subtitle."""
    view = ui.widget('scroll_view', parent, box)
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], ROW_H * len(titles))
    for i, title in enumerate(titles):
        row = ui.widget('list_item', view, (0, ROW_H * i, box[2], ROW_H), click=True)
        ui.widget('label', row, (10, 0, 300, 20), text=title)
        if subtitles:
            ui.widget('label', row, (10, 20, 300, 10), text=subtitles[i])
    return view


class Page:
    """A list page on top: its window, a scroll_list of titles and the view's offset."""
    def __init__(self, ui, titles, name='allmusic_page', height=VIEW_H, subtitles=None, top=True):
        self.ui = ui
        self.win = ui.widget('window', ui.wm, (0, 30, 375, 290), name=name)
        self.view = scroll_list(ui, self.win, titles, (0, 0, 375, height), subtitles)
        self.rows = self.view.children[:]
        if top:
            ui.top = self.win

    @property
    def selected(self):
        return self.view.props.get(SEL, -1)

    @property
    def offset(self):
        return self.view.int(SCROLL_Y)

    def scroll(self, y):
        self.view.int(SCROLL_Y, y)

    def screen_y(self, i):
        return ROW_H * i - self.offset

    def shows(self, i):
        """Row i whole in the viewport."""
        return 0 <= self.screen_y(i) and self.screen_y(i) + ROW_H <= self.view.box[3]


STOCK_BYTES = [O['KEY_LOCKOUT'], O['BOOT_KEY_GUARD'], O['RETURN_RELEASE_LATCH']]


@pytest.fixture
def ui(fw):
    """A lit, unlocked Q2 whose stock key latches are clear; the latches are put back after."""
    for address in STOCK_BYTES + [O['SCAN_DONE']]:
        claim(address)
    saved = [C.string_at(a, 1) for a in STOCK_BYTES] + [C.string_at(O['SCAN_DONE'], 4)]
    for a in STOCK_BYTES:
        C.memset(a, 0, 1)
    C.memset(O['SCAN_DONE'], 0, 4)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    yield Ui(fw)
    for a, b in zip(STOCK_BYTES + [O['SCAN_DONE']], saved):
        C.memmove(a, b, len(b))


def key(fw, ui, code, at=None):
    """A key's release through the firmware's key filter (ringnav), at `at` ms."""
    if at is not None:
        ui.now = at
    event = C.create_string_buffer(0x40)
    struct.pack_into('<I', event, O['EVENT_KEY'], code)
    return fw.fn('ringnav', C.c_int, C.c_void_p, C.c_void_p)(None, event)


def paint(fw, page, canvas=None):
    """The list's own paint, which loads and settles its selection first (a canvas with no LCD draws
    nothing after that)."""
    canvas = canvas or C.create_string_buffer(0x100)
    fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)(page.view.at, canvas)


def titles(n, start=0):
    return [f'Song {i:03d}' for i in range(start, start + n)]


def spin(fw, ui, code, ticks, gap):
    """ticks wheel detents gap ms apart; the selection after each."""
    seen = []
    for _ in range(ticks):
        key(fw, ui, code, ui.now + gap)
        seen.append(ui.top.children[0].props.get(SEL, -1))
    return seen


SLOW = NAV['WHEEL_RUN_MS'] + 1  # past a run: each tick starts over


# The wheel on a list.
def test_a_fresh_list_takes_its_first_whole_row_then_the_tick_moves_on(fw, ui):
    page = Page(ui, titles(40))
    page.scroll(ROW_H * 3 + 5)  # row 3 is cut at the top: row 4 is the first whole one
    paint(fw, page)
    assert page.selected == 4
    key(fw, ui, KEY_NEXT)
    assert page.selected == 5 and page.offset == ROW_H * 3 + 5  # in view already: the list stays


def test_ticks_scroll_only_as_far_as_the_row_needs(fw, ui):
    page = Page(ui, titles(40))
    paint(fw, page)
    seen = []
    for _ in range(20):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
        seen.append((page.selected, page.offset, page.screen_y(page.selected)))
    assert [s for s, _, _ in seen] == list(range(1, 21))
    assert all(0 <= y and y + ROW_H <= VIEW_H for _, _, y in seen), seen
    moved = [i for i, (_, offset, _) in enumerate(seen) if offset]
    first = moved[0]
    assert all(seen[i][1] == seen[i - 1][1] + ROW_H for i in moved[1:])  # once it moves: a row a tick
    held = {y for _, _, y in seen[first:]}
    assert len(held) == 1  # the row stays put on screen as the list scrolls under it
    assert held.pop() + ROW_H < VIEW_H  # short of the bottom: a reading margin


def test_a_tick_up_from_the_top_row_of_a_scrolled_list_brings_it_in(fw, ui):
    page = Page(ui, titles(40))
    page.scroll(ROW_H * 10)
    paint(fw, page)
    assert page.selected == 10
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    assert page.selected == 9 and page.shows(9) and page.screen_y(9) > 0


def test_a_spin_steps_by_the_documented_ramp(fw, ui):
    first, more, cap, gap = NAV['LIST_FIRST_MS'], NAV['LIST_RAMP_MS'], NAV['WHEEL_MAX_STEP'], 100
    page = Page(ui, titles(400))
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)  # a run's first tick: it has spun 0 ms
    seen = [0, page.selected] + spin(fw, ui, KEY_NEXT, 24, gap)
    steps = [b - a for a, b in zip(seen, seen[1:])]
    # one row until the run has spun `first` ms, then two and one more each `more` ms, up to cap
    want = [1 if gap * k < first else min(cap, 2 + (gap * k - first) // more) for k in range(25)]
    assert steps == want and want[-1] == cap


@pytest.mark.parametrize('second', [KEY_NEXT, KEY_PREV])
def test_a_pause_or_a_turn_back_starts_the_ramp_over(fw, ui, second):
    page = Page(ui, titles(400))
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    spin(fw, ui, KEY_NEXT, 12, 100)
    before = page.selected
    gap = 100 if second == KEY_PREV else SLOW
    key(fw, ui, second, ui.now + gap)
    assert abs(page.selected - before) == 1


def test_a_short_list_always_steps_one_row(fw, ui):
    page = Page(ui, titles(NAV['SHORT_LIST_MAX']))
    paint(fw, page)
    seen = spin(fw, ui, KEY_NEXT, 12, 50)
    assert seen == list(range(1, 13))


def test_a_spin_shows_the_letter_until_it_lapses(fw, ui):
    page = Page(ui, titles(400))
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    gap = NAV['LIST_FIRST_MS'] // 3
    spin(fw, ui, KEY_NEXT, 2, gap)
    assert not ui.due('letter_expire')  # still one row a tick
    spin(fw, ui, KEY_NEXT, 1, gap)
    (timer,) = ui.due('letter_expire')
    assert ui.timers[timer][2] == O['LETTER_MS']
    ui.invalidated.clear()
    ui.fire(timer)
    assert ui.invalidated == [page.view.at]  # the letter goes with a repaint


@pytest.mark.parametrize('lit, lock, power', [(0, 0, 0), (1, 1, 0), (1, 0, 2)])
def test_a_tick_on_a_dark_locked_or_powering_off_screen_moves_nothing(fw, ui, lit, lock, power):
    page = Page(ui, titles(40))
    paint(fw, page)
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    fw.var('g_lockscreen_pageflag', C.c_ubyte).value = lock
    fw.var('g_poweroff_state', C.c_ubyte).value = power
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert page.selected == 0


def test_a_page_outside_the_audited_windows_is_left_to_stock(fw, ui):
    page = Page(ui, titles(40), name='someone_elses_page')
    key(fw, ui, KEY_NEXT)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert page.selected == -1 and page.offset == 0


# The ends.
def to_end(fw, ui, page):
    paint(fw, page)
    while page.selected < len(page.rows) - 1:
        key(fw, ui, KEY_NEXT, ui.now + SLOW)


def test_a_ring_list_bumps_at_its_end_and_wraps_after_a_pause(fw, ui):
    page = Page(ui, titles(30))  # allmusic_page: a ring
    to_end(fw, ui, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert page.selected == 29  # the first detent past the end bumps
    (bump,) = ui.due('fx_expire')
    assert ui.timers[bump][2] == O['BUMP_MS']
    key(fw, ui, KEY_NEXT, ui.now + O['EDGE_PAUSE_MS'] - 1)
    assert page.selected == 29  # a spin hard-stops at the end
    key(fw, ui, KEY_NEXT, ui.now + O['EDGE_PAUSE_MS'])
    assert page.selected == 0 and page.offset == 0  # a detent after a pause carries over


def test_a_ring_list_wraps_from_its_top_to_its_last_row(fw, ui):
    page = Page(ui, titles(30))
    paint(fw, page)
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    key(fw, ui, KEY_PREV, ui.now + O['EDGE_PAUSE_MS'])
    assert page.selected == 29 and page.shows(29)


def test_turning_back_from_the_end_makes_the_next_end_bump_again(fw, ui):
    page = Page(ui, titles(30))
    to_end(fw, ui, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    key(fw, ui, KEY_NEXT, ui.now + O['EDGE_PAUSE_MS'] + 1)
    assert page.selected == 29  # bumped afresh, not wrapped


def test_a_list_that_is_not_a_ring_stops_at_its_end(fw, ui):
    page = Page(ui, titles(30), name='about_page')
    to_end(fw, ui, page)
    for _ in range(3):
        key(fw, ui, KEY_NEXT, ui.now + O['EDGE_PAUSE_MS'] + 1)
    assert page.selected == 29 and not ui.due('fx_expire')


@pytest.mark.parametrize('cls, ring', [(O['CLASS_ALBUMS'], True), (0xff10, False)])
def test_album_page_wraps_only_as_the_albums_list(fw, ui, cls, ring):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    page = Page(ui, titles(30), name='album_page')
    to_end(fw, ui, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    key(fw, ui, KEY_NEXT, ui.now + O['EDGE_PAUSE_MS'] + 1)
    assert page.selected == (0 if ring else 29)


def test_the_bump_repaints_its_list_when_it_lapses(fw, ui):
    page = Page(ui, titles(30))
    to_end(fw, ui, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    (bump,) = ui.due('fx_expire')
    ui.invalidated.clear()
    ui.fire(bump)
    assert ui.invalidated == [page.view.at]


def test_the_bump_of_a_page_gone_repaints_nothing(fw, ui):
    page = Page(ui, titles(30))
    to_end(fw, ui, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    (bump,) = ui.due('fx_expire')
    Page(ui, titles(30))  # a new page on top
    ui.invalidated.clear()
    ui.fire(bump)
    assert not ui.invalidated


# Centre.
class Clicks:
    """Stock's dispatch of the click Centre confirms: the widgets clicked."""
    def __init__(self, fw):
        self.seen = []
        fw.mock('pointer_event_init', self.init)
        fw.mock('stock_dispatch_trampoline', lambda target, e, *_: self.seen.append((target, C.c_int.from_address(e).value)))

    @staticmethod
    def init(e, kind, *_):
        C.c_int.from_address(e).value = kind
        return e


def centre(fw, ui, page, row):
    paint(fw, page)
    while page.selected < row:
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    clicks = Clicks(fw)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)
    return clicks


def test_centre_clicks_its_row_once_the_double_press_window_has_passed(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    (confirm,) = ui.due('confirm_center')
    assert ui.timers[confirm][2] == O['DOUBLE_CLICK_MS'] and not clicks.seen
    C.memset(O['KEY_LOCKOUT'], 8, 1)
    ui.now += O['DOUBLE_CLICK_MS']
    ui.fire(confirm)
    assert clicks.seen == [(page.rows[3].at, EVT_CLICK)]
    assert C.string_at(O['KEY_LOCKOUT'], 1) == b'\0'  # the page it opens takes the wheel at once


def test_a_second_centre_in_the_window_leaves_the_press_to_stock(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    key(fw, ui, KEY_CENTER, ui.now + O['DOUBLE_CLICK_MS'] - 1)
    assert not ui.due('confirm_center') and not clicks.seen


def test_a_row_rebound_before_the_confirm_is_not_clicked(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    page.rows[3].children[0].text = 'Another song'  # the list was rebuilt under the press
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


@pytest.mark.parametrize('flag', ['pressed', 'g_power_longkey', 'g_ingore_bootkey_flag', 'guard'])
def test_a_finger_down_or_a_power_key_cancels_the_click(fw, ui, flag):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    if flag == 'pressed':
        ui.pressed = 1
    elif flag == 'guard':
        C.memset(O['BOOT_KEY_GUARD'], 1, 1)
    else:
        fw.var(flag, C.c_ubyte).value = 1
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


def test_a_new_page_on_top_cancels_the_click(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    confirm = ui.due('confirm_center')[0]
    Page(ui, titles(30))
    ui.fire(confirm)
    assert not clicks.seen


# Position memory: a recreated page opens where the user left it.
def chosen(fw, ui, page, row):
    """The user's pick on page: the wheel to row, then the page's paint."""
    paint(fw, page)
    while page.selected < row:
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    paint(fw, page)
    return page


def theme(fw, index, **keys):
    """config.ini's IPOD THEME (0 Minimal, 1 Classic, 2 Modern) and any other keys given; the rest
    missing, so their defaults."""
    values = {b'THEME': str(index).encode(), **{k.encode(): str(v).encode() for k, v in keys.items()}}
    def read(path, section, name, out, default, *_):
        value = values.get(C.string_at(name), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', read)


def test_a_recreated_page_glides_to_the_row_last_chosen_in_it(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    again = Page(ui, titles(40))
    paint(fw, again)
    assert again.selected == 25 and again.shows(25)
    (dy, ms), = ui.glides
    assert dy == again.offset and ms == O['GLIDE_MS']  # Minimal's recall glides from the top


def test_a_reordered_list_restores_the_same_item(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(40)[::-1])
    paint(fw, again)
    assert again.selected == 39 - 25 and again.shows(39 - 25)


def test_the_second_line_tells_twin_titles_apart(fw, ui):
    names, artists = titles(40), ['A'] * 40
    names[5] = names[30] = 'Intro'
    artists[30] = 'B'
    chosen(fw, ui, Page(ui, names, subtitles=artists), 30)
    moved, moved_artists = titles(40), ['A'] * 40
    moved[2] = moved[29] = 'Intro'
    moved_artists[2] = 'B'  # the remembered artist's twin moved far; the other sits by the old place
    again = Page(ui, moved, subtitles=moved_artists)
    paint(fw, again)
    assert again.selected == 2


def test_twin_titles_without_a_second_line_take_the_nearer(fw, ui):
    names = titles(40)
    names[5] = names[30] = 'Intro'
    chosen(fw, ui, Page(ui, names), 30)
    moved = titles(40)
    moved[3] = moved[27] = 'Intro'
    again = Page(ui, moved)
    paint(fw, again)
    assert again.selected == 27


def test_a_list_with_no_row_of_that_name_keeps_the_place(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(40, start=100))
    paint(fw, again)
    assert again.selected == 25


def test_a_shorter_list_does_not_restore_past_its_end(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(10, start=100))
    paint(fw, again)
    assert again.selected == 0


def test_another_list_of_the_same_page_starts_at_its_top(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    fw.var('fw_ctx_g_local_classinfo_save', C.c_ubyte * 912)[0x10] = 7  # another artist's songs
    again = Page(ui, titles(40))
    paint(fw, again)
    assert again.selected == 0 and again.offset == 0


@pytest.mark.parametrize('name, cls, kept', [
    ('localclass_page', O['CLASS_ALBUMS'], True),  # a root collection ignores the saved detail query
    ('localclass_page', 0xff10, False), ('album_page', O['CLASS_ALBUMS'], False),
])
def test_opening_an_artist_keeps_a_root_collections_row(fw, ui, name, cls, kept):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    chosen(fw, ui, Page(ui, titles(40), name=name), 25)
    fw.var('fw_ctx_g_local_classinfo_save', C.c_ubyte * 912)[0x10] = 7
    again = Page(ui, titles(40), name=name)
    paint(fw, again)
    assert again.selected == (25 if kept else 0)


def folder(fw, path):
    data = path.encode() + b'\0'
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), data, len(data))


def test_each_folder_keeps_its_own_row(fw, ui):
    for path, row in (('/mnt/mmc/A', 5), ('/mnt/mmc/B', 9)):
        folder(fw, path)
        chosen(fw, ui, Page(ui, titles(20), name='folder_page'), row)
    for path, row in (('/mnt/mmc/A', 5), ('/mnt/mmc/B', 9), ('/mnt/mmc/C', 0)):
        folder(fw, path)
        again = Page(ui, titles(20), name='folder_page')
        paint(fw, again)
        assert again.selected == row


def test_a_folder_page_with_no_path_remembers_nothing(fw, ui):
    chosen(fw, ui, Page(ui, titles(20), name='folder_page'), 5)
    again = Page(ui, titles(20), name='folder_page')
    paint(fw, again)
    assert again.selected == 0


@pytest.mark.parametrize('name', ['search_dialog', 'wifi_page', 'coverflow_page'])  # Cover Flow: no album open
def test_a_page_without_a_content_key_never_imports_a_row(fw, ui, name):
    chosen(fw, ui, Page(ui, titles(20), name=name), 5)
    again = Page(ui, titles(20), name=name)
    paint(fw, again)
    assert again.selected == 0


def test_the_memory_holds_the_last_64_lists(fw, ui):
    for k in range(NAV['POS_MEM'] + 1):
        folder(fw, f'/mnt/mmc/{k}')
        chosen(fw, ui, Page(ui, titles(20), name='folder_page'), 1 + k % 7)
    recalled = []
    for k in (1, 0):  # the next oldest first: opening a forgotten one takes a slot
        folder(fw, f'/mnt/mmc/{k}')
        again = Page(ui, titles(20), name='folder_page')
        paint(fw, again)
        recalled.append(again.selected)
    assert recalled == [2, 0]  # the oldest went; the next oldest is still there


def checked(ui, page, row):
    ui.widget('image', page.rows[row], (340, 0, 20, 20)).props[b'image'] = b'select'


def test_a_picker_opens_on_its_current_option(fw, ui):
    chosen(fw, ui, Page(ui, titles(8), name='playmode_page'), 4)
    again = Page(ui, titles(8), name='playmode_page')
    checked(ui, again, 2)
    paint(fw, again)
    assert again.selected == 2


def test_a_sliding_page_arrives_on_its_row_without_a_glide(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    again = Page(ui, titles(40))
    again.win.props[b'anim_hint'] = b'htranslate'
    paint(fw, again)
    assert again.selected == 25 and again.shows(25) and not ui.glides


def test_classic_recalls_in_place(fw, ui):
    theme(fw, 1)  # Classic: recall ms 0
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    again = Page(ui, titles(40))
    paint(fw, again)
    assert again.selected == 25 and again.shows(25) and not ui.glides


def test_a_recall_glide_cut_short_is_asked_again_once(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    ui.glide_arrives = False
    again = Page(ui, titles(40))
    paint(fw, again)
    assert again.selected == 25 and len(ui.glides) == 2  # kept, and retried
    paint(fw, again)
    assert again.shows(again.selected)  # once only: then a row in view takes it


def test_a_recall_glide_that_arrives_on_its_retry_keeps_the_row(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(40))
    asked = []
    def glide(w, dx, dy, ms, *_):
        if asked:
            again.scroll(again.offset + C.c_int(dy).value)
        asked.append(dy)
    fw.mock('scroll_view_scroll_delta_to', glide)
    paint(fw, again)
    paint(fw, again)
    assert again.selected == 25 and again.shows(25) and len(asked) == 2


def test_a_swipe_keeps_the_row_while_it_moves_and_settles_on_the_middle_row(fw, ui):
    page = chosen(fw, ui, Page(ui, titles(40)), 2)
    page.scroll(10 * ROW_H)
    page.view.ptr(O['VIEW_ANIMATOR'], 0x1234)  # momentum
    paint(fw, page)
    assert page.selected == 2
    page.view.ptr(O['VIEW_ANIMATOR'], 0)
    paint(fw, page)
    middle = 10 + VIEW_H // ROW_H // 2  # rows 13 and 14 sit as near the middle: the earlier
    assert page.selected == middle - 1


def test_rows_taller_than_the_view_settle_on_one_partly_shown(fw, ui):
    page = chosen(fw, ui, Page(ui, titles(40), height=20), 0)
    page.scroll(5 * ROW_H + 10)
    paint(fw, page)
    assert page.selected == 5


def test_a_finger_down_does_not_recall(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    again = Page(ui, titles(40))
    again.scroll(ROW_H * 2 + 5)  # row 2 cut at the top
    ui.pressed = 1
    paint(fw, again)
    # as a list with nothing to recall: its first whole row, where the finger left it
    assert again.selected == 3 and again.offset == ROW_H * 2 + 5 and not ui.glides


# A dialog's buttons are its rows.
def dialog(ui, name='confirminfo_dialog', labels=('Cancel', 'OK')):
    win = ui.widget('dialog', ui.wm, (40, 100, 300, 120), name=name)
    buttons = [ui.widget('button', win, (20 + 140 * i, 70, 120, 40), click=True, text=t) for i, t in enumerate(labels)]
    ui.top = win
    return win, buttons


def test_a_confirm_dialogs_buttons_take_the_wheel_and_centre(fw, ui):
    win, buttons = dialog(ui)
    key(fw, ui, KEY_NEXT)
    assert win.props[SEL] == 1
    clicks = Clicks(fw)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)
    ui.fire(ui.due('confirm_center')[0])
    assert clicks.seen == [(buttons[1].at, EVT_CLICK)]


def test_a_held_power_key_leaves_only_its_shutdown_prompt_the_wheel(fw, ui):
    fw.var('g_poweroff_state', C.c_ubyte).value = 1
    page = Page(ui, titles(10))
    key(fw, ui, KEY_NEXT)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert page.selected == -1
    win, _ = dialog(ui)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert win.props[SEL] == 1


@pytest.mark.parametrize('cable, moves', [(1, False), (0, True)])
def test_usb_modes_scan_prompt_takes_the_wheel_once_the_cable_is_out(fw, ui, cable, moves):
    fw.var('g_usblink_status', C.c_ubyte).value = 2
    fw.var('g_usbdet_value', C.c_ubyte).value = cable
    win, _ = dialog(ui)
    key(fw, ui, KEY_NEXT)
    assert (win.props.get(SEL) == 1) == moves


# A virtual table: a pool of row widgets rebound to the rows in view.
class Table:
    """A table_client of n logical rows ROW_H high on page name, drawn by a pool of table_rows that its
    offset setter rebinds (ROW_INDEX each, -1 past the end)."""
    def __init__(self, fw, ui, n, pool=10, name='allmusic_page'):
        self.ui, self.n, self.names = ui, n, titles(n)
        self.win = ui.widget('window', ui.wm, (0, 30, 375, 290), name=name)
        self.client = self.view = ui.widget('table_client', self.win, (0, 0, 375, VIEW_H))
        self.client.int(O['ROW_HEIGHT'], ROW_H)
        self.client.int(O['TABLE_ROWS'], n)
        self.pool = []
        for _ in range(pool):
            row = ui.widget('table_row', self.client, (0, 0, 375, ROW_H), click=True)
            ui.widget('label', row, (10, 0, 300, 20))
            self.pool.append(row)
        self.bind(0)
        fw.mock('table_client_set_yoffset', lambda w, top, *_: self.bind(top))
        ui.top = self.win

    def bind(self, top):
        self.client.int(O['TABLE_TOP'], top)
        first = top // ROW_H
        for k, row in enumerate(self.pool):  # table_client's relayout: child k holds the k-th row in view
            i = first + k
            row.int(O['ROW_INDEX'], i if i < self.n else -1)
            struct.pack_into('<i', row.block, 4, ROW_H * i)
            row.children[0].text = self.names[i] if i < self.n else None

    @property
    def selected(self):
        return self.client.props.get(SEL, -1)

    def holder(self, i):
        (row,) = [r for r in self.pool if r.int(O['ROW_INDEX']) == i]
        return row


def test_the_wheel_walks_a_virtual_tables_logical_rows(fw, ui):
    table = Table(fw, ui, 100)
    seen = []
    for _ in range(30):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
        top = table.client.int(O['TABLE_TOP'])
        seen.append((table.selected, top))
    assert [s for s, _ in seen] == list(range(1, 31))
    assert all(0 <= ROW_H * s - top and ROW_H * s - top + ROW_H <= VIEW_H for s, top in seen)


def test_centre_on_a_virtual_table_clicks_whichever_widget_holds_the_row(fw, ui):
    table = Table(fw, ui, 100)
    for _ in range(12):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    clicks = Clicks(fw)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)
    before = table.holder(12)
    table.bind(table.client.int(O['TABLE_TOP']) + ROW_H)  # a rebind: another pool widget holds row 12
    assert table.holder(12) is not before
    ui.fire(ui.due('confirm_center')[0])
    assert clicks.seen == [(table.holder(12).at, EVT_CLICK)]


def test_a_virtual_table_recalls_its_logical_row(fw, ui):
    table = Table(fw, ui, 100)
    for _ in range(40):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert table.selected == 40
    again = Table(fw, ui, 100)
    scrolled = []
    fw.mock('table_client_scroll_to', lambda w, top, *_: scrolled.append(top) or again.bind(top))
    paint(fw, again)
    assert again.selected == 40 and scrolled and 0 <= ROW_H * 40 - scrolled[-1] <= VIEW_H - ROW_H


# Home's carousel: one animator, retargeted tick by tick.
ANIM_X_TO, ANIM_DURATION, SLIDE_OFFSET, SLIDE_INDEX = O['ANIM_X_TO'], O['ANIM_DURATION'], O['SLIDE_OFFSET'], O['SLIDE_INDEX']


class Carousel:
    """Home's slide_menu of n cards on home_page, a card's width plus the spacer apart; its animator
    a block whose goal (ANIM_X_TO) and run (ANIM_DURATION) the C sets. The live offset is the test's."""
    def __init__(self, fw, ui, n=5, index=2, width=90, spacer=10, animates=True, binds=True, name='home_page'):
        self.fw, self.ui, self.stride = fw, ui, width + spacer
        self.win = ui.widget('window', ui.wm, (0, 30, 375, 290), name=name)
        self.menu = ui.widget('slide_menu', self.win, (0, 0, 375, 200))
        self.cards = [ui.widget('button', self.menu, (0, 0, width, 150), click=True, text=f'Card {i}') for i in range(n)]
        self.menu.int(SLIDE_INDEX, index)
        self.menu.int(O['SLIDE_SPACER'], spacer)
        self.blocks, self.handlers, self.started, self.destroyed = {}, [], [], []
        fw.mock('slide_menu_item_width', lambda *_: width)
        fw.mock('widget_animator_scroll_create', lambda *_: self.create() if animates else 0)
        fw.mock('widget_animator_on', lambda a, evt, fn, ctx, *_: (self.handlers.append((evt, fn, ctx)) or 1) if binds else 0)
        fw.mock('widget_animator_scroll_set_params', self.params)
        fw.mock('widget_animator_start', lambda a, *_: self.started.append((self.goal, self.duration)))
        fw.mock('widget_animator_destroy', lambda a, *_: self.destroyed.append(a))
        ui.top = self.win

    def create(self):
        block = C.create_string_buffer(0x100)
        self.blocks[C.addressof(block)] = block
        return C.addressof(block)

    def params(self, a, x0, y0, x1, y1, *_):
        struct.pack_into('<i', self.blocks[a], ANIM_X_TO, C.c_int(x1).value)

    @property
    def animator(self):
        return self.blocks.get(self.menu.ptr(O['SLIDE_ANIMATOR']))

    @property
    def goal(self):
        return struct.unpack_from('<i', self.animator, ANIM_X_TO)[0]

    @property
    def duration(self):
        return struct.unpack_from('<i', self.animator, ANIM_DURATION)[0]

    def live(self, offset):
        self.menu.int(SLIDE_OFFSET, offset)


@pytest.mark.parametrize('code, way', [(KEY_NEXT, -1), (KEY_PREV, 1)])
def test_a_tick_glides_the_carousel_one_card(fw, ui, code, way):
    home = Carousel(fw, ui)
    key(fw, ui, code)
    assert home.started == [(way * home.stride, home.duration)] and home.duration > 0


def test_a_tick_before_the_glide_lands_extends_it_and_hurries(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    first = home.duration
    home.live(-home.stride * 2 // 5)
    key(fw, ui, KEY_NEXT, ui.now + NAV['HOME_FAST_WINDOW_MS'])
    assert home.goal == -2 * home.stride and home.duration < first
    assert len(home.blocks) == 1  # the same animator, retargeted


def test_a_tick_after_the_fast_window_keeps_the_normal_pace(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    first = home.duration
    home.live(-home.stride)
    key(fw, ui, KEY_NEXT, ui.now + NAV['HOME_FAST_WINDOW_MS'] + 1)
    assert home.goal == -2 * home.stride and home.duration == first


def test_a_tick_back_mid_glide_heads_for_the_card_it_has_just_passed(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    key(fw, ui, KEY_NEXT, ui.now + 50)
    assert home.goal == -2 * home.stride
    home.live(-home.stride * 7 // 5)  # past the next card, short of the one after
    key(fw, ui, KEY_PREV, ui.now + 50)
    assert home.goal == -home.stride


@pytest.mark.parametrize('index, code, lands', [(2, KEY_NEXT, 3), (4, KEY_NEXT, 0), (0, KEY_PREV, 4)])
def test_with_no_animator_a_tick_lands_at_once_round_the_ring(fw, ui, index, code, lands):
    home = Carousel(fw, ui, index=index, animates=False)
    home.live(-17)
    key(fw, ui, code)
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (home.menu.at, lands)
    assert home.menu.int(SLIDE_OFFSET) == 0 and not home.started


def test_the_glides_end_hands_back_the_cards_focus(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    (evt, fn, ctx), = home.handlers
    assert evt == O['EVT_ANIM_END'] and ctx == home.menu.at
    home.menu.int(SLIDE_INDEX, 3)  # stock committed the index
    assert C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0x40) == 7  # RET_REMOVE: once
    assert fw.calls('slide_menu_on_scroll_done')[-1][1][:2] == (home.menu.at, 0x40)
    assert fw.calls('widget_set_focused')[-1][1][:2] == (home.cards[3].at, 1)


def test_an_immediate_move_drops_the_glide_still_running(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    a = home.menu.ptr(O['SLIDE_ANIMATOR'])
    glide = fw.fn('carousel_glide', C.c_int, C.c_void_p, C.c_int, C.c_uint, C.c_int)
    assert glide(home.menu.at, -home.stride, 0, 0) == 0
    assert home.destroyed == [a] and home.menu.ptr(O['SLIDE_ANIMATOR']) == 0
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (home.menu.at, 3)


def test_a_follow_glide_runs_as_long_as_coverflows_aim_says(fw, ui):
    home = Carousel(fw, ui)
    home.live(-30)
    follow = O['EASE_FOLLOW']
    assert fw.fn('carousel_glide', C.c_int, C.c_void_p, C.c_int, C.c_uint, C.c_int)(home.menu.at, -2 * home.stride, 90, follow) == 1
    aim = fw.fn('coverflow_follow_aim', C.c_int, C.c_int, C.c_int, C.c_int)(-2 * home.stride + 30, 90, -1)
    assert home.duration == aim
    assert fw.calls('widget_animator_scroll_create')[0][1][3] == 0  # AWTK gets no easing it lacks
    easing = struct.unpack_from('<Q', home.animator, O['ANIM_EASING'])[0]
    assert easing == C.cast(fw.lib.coverflow_follow, C.c_void_p).value


def test_coverflows_letter_shows_for_its_time_then_repaints_away(fw, ui):
    page = Page(ui, titles(10), name='coverflow_page')
    letter = fw.fn('coverflow_letter', None, C.c_void_p, C.c_uint, C.c_uint)
    letter(page.view.at, ord('B'), 500)
    (timer,) = ui.due('cf_letter_expire')
    assert ui.timers[timer][2] == O['LETTER_MS']
    ui.invalidated.clear()
    ui.fire(timer)
    assert ui.invalidated == [page.view.at]


def test_coverflows_letter_cleared_stops_its_timer(fw, ui):
    page = Page(ui, titles(10), name='coverflow_page')
    letter = fw.fn('coverflow_letter', None, C.c_void_p, C.c_uint, C.c_uint)
    letter(page.view.at, ord('B'), 500)
    (timer,) = ui.due('cf_letter_expire')
    letter(page.view.at, 0, 600)
    assert not ui.due('cf_letter_expire') and timer not in ui.timers


# Pull to search: a drag down from the top of Local Songs' list.
STOP = NAV['STOP']
EVT_DOWN, EVT_MOVE, EVT_UP = O['EVT_POINTER_DOWN'], O['EVT_POINTER_MOVE_BEFORE'], O['EVT_POINTER_UP_BEFORE']
PULL_CLAIM, PULL_SEARCH = O['PULL_CLAIM_PX'], O['PULL_SEARCH_PX']


class Pull:
    """A finger on page: ringnav_touch's down, then the moves and lift stock dispatches to the handler
    the down bound (pull_event), as they reach it. The prompt is a label the C makes on the page."""
    def __init__(self, fw, ui, page, fail_at=None):
        self.fw, self.ui, self.page, self.fail_at = fw, ui, page, fail_at
        self.bound, self.unbound, self.aborts = [], [], []
        fw.mock('widget_on', self.on)
        fw.mock('widget_off_by_func', lambda w, t, *_: self.unbound.append((w, t)))
        fw.mock('widget_to_local', self.to_local)
        fw.mock('label_create', lambda parent, x, y, w, h, *_: ui.widget('label', ui.by[parent], (x, y, w, h)).at)
        fw.mock('widget_set_name', lambda w, name, *_: ui.by[w].props.__setitem__(b'name', C.string_at(name)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: setattr(ui.by[w], 'text', C.string_at(s).decode()))
        fw.mock('widget_set_visible', lambda w, on, *_: w in ui.by and setattr(ui.by[w], 'visible', bool(on)))
        fw.mock('pointer_event_init', Clicks.init)
        fw.mock('widget_dispatch_event_to_target_recursive', lambda w, e, *_: self.aborts.append((w, C.c_int.from_address(e).value)))
        self.x = self.y = 0

    def on(self, w, kind, fn, ctx, *_):
        if self.fail_at is not None and len(self.bound) == self.fail_at:
            self.fail_at = None
            return 0
        self.bound.append((w, kind, fn, ctx))
        return len(self.bound)

    def to_local(self, w, xy, *_):
        x, y = self.ui.by[w].origin()
        point = (C.c_int * 2).from_address(xy)
        point[0] -= x
        point[1] -= y

    def event(self, kind, x, y):
        e = C.create_string_buffer(0x40)
        struct.pack_into('<i', e, O['EVENT_TYPE'], kind)
        struct.pack_into('<2i', e, O['EVENT_X'], x, y)
        return e

    def down(self, x=100, y=100):
        self.x, self.y = x, y
        return self.fw.fn('ringnav_touch', C.c_int, C.c_void_p, C.c_void_p)(None, self.event(EVT_DOWN, x, y))

    def send(self, kind, dx=0, dy=0, to=None):
        """The handler for this event on widget `to` (the page unless given), as stock's dispatch runs
        it; 0 if none is bound."""
        to = to or self.page.win
        handlers = [(fn, ctx) for w, k, fn, ctx in self.bound if w == to.at and k == kind]
        if not handlers:
            return 0
        fn, ctx = handlers[0]
        self.last = self.event(kind, self.x + dx, self.y + dy)
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, self.last)

    def move(self, dx, dy):
        return self.send(EVT_MOVE, dx, dy)

    def up(self, dx, dy):
        return self.send(EVT_UP, dx, dy)

    @property
    def prompt(self):
        """The prompt label over the list, as (shown, its text); None before the pull makes it."""
        found = self.ui.lookup(self.page.win, b'_pull_prompt')
        return (self.ui.by[found].visible, self.ui.by[found].text) if found else None

    @property
    def says(self):
        """The prompt's text while it is shown, else None."""
        prompt = self.prompt
        return prompt[1] if prompt and prompt[0] else None

    @property
    def hidden(self):
        return self.prompt is not None and not self.prompt[0]


def test_a_pull_down_from_the_lists_top_searches_on_release(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    assert pull.move(0, PULL_CLAIM // 2) == 0 and pull.prompt is None  # the list's own, so far
    assert pull.move(2, PULL_CLAIM + 4) == STOP
    assert page.view.props[b'_pull_suppress'] == 1 and pull.aborts == [(page.win.at, O['EVT_POINTER_ABORT'])]
    pulling = pull.says
    assert pulling
    assert pull.move(0, PULL_SEARCH) == STOP
    ready = pull.says
    assert ready and ready != pulling  # far enough: it says so
    assert pull.move(0, PULL_SEARCH - 1) == STOP and pull.says == pulling  # back short of it
    assert pull.move(0, 0) == STOP and pull.hidden  # back where it began: still the pull's, nothing shown
    assert pull.move(0, PULL_SEARCH) == STOP and pull.says == ready
    assert pull.up(0, PULL_SEARCH) == STOP
    assert [a[:2] for _, a in fw.calls('stock_search')] == [(page.win.at, C.addressof(pull.last))]
    assert pull.hidden


def test_a_pull_let_go_short_does_not_search(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    pull.move(0, PULL_CLAIM + 4)
    assert pull.up(0, PULL_SEARCH - 1) == STOP  # claimed: the lift is the pull's, not a row's tap
    assert not fw.calls('stock_search') and pull.hidden


@pytest.mark.parametrize('dx, dy', [(PULL_CLAIM + 4, PULL_CLAIM), (0, -PULL_CLAIM)])
def test_a_sideways_or_upward_drag_is_left_to_the_list(fw, ui, dx, dy):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    assert pull.move(dx, dy) == 0
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0
    assert not fw.calls('stock_search') and page.view.props.get(b'_pull_suppress') == 0


@pytest.mark.parametrize('y, scrolled', [(O['QUICK_EDGE_PX'] - 1, 0), (100, ROW_H), (30 + VIEW_H + 5, 0)])
def test_a_touch_at_the_top_edge_below_the_list_or_on_a_scrolled_list_starts_no_pull(fw, ui, y, scrolled):
    page = Page(ui, titles(20), name='localmusic_page')
    page.scroll(scrolled)
    pull = Pull(fw, ui, page)
    pull.down(y=y)
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0
    assert pull.prompt is None and not fw.calls('stock_search')


@pytest.mark.parametrize('name, view, pulls', [
    ('allmusic_page', None, False), ('radio_page', None, True),
    ('home_page', 'scroll_view_localmusic', True), ('home_page', 'scroll_view_home', False),
])
def test_only_local_songs_internet_radio_and_homes_library_pull(fw, ui, name, view, pulls):
    page = Page(ui, titles(20), name=name)
    if view:
        page.view.props[b'name'] = view.encode()
    pull = Pull(fw, ui, page)
    pull.down()
    pull.move(0, PULL_SEARCH)
    assert (pull.up(0, PULL_SEARCH) == STOP) == pulls
    assert len(fw.calls('stock_search')) == (pulls and name != 'radio_page')  # the radio's search is its own


ENDS = {'background': ('EVT_WINDOW_BACKGROUND', 'win'), 'closed': ('EVT_WINDOW_CLOSE', 'win'),
        'aborted': ('EVT_POINTER_ABORT', 'win'), 'page destroyed': ('EVT_DESTROY', 'win'),
        'list destroyed': ('EVT_DESTROY', 'view')}


@pytest.mark.parametrize('end', [*ENDS, 'covered', 'animating', 'dark'])
def test_the_pull_ends_when_its_page_stops_being_the_one_in_front(fw, ui, end):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    pull.move(0, PULL_CLAIM + 4)
    if end in ENDS:
        kind, on = ENDS[end]
        assert pull.send(O[kind], to=getattr(page, on)) == 0  # the event goes on to stock
    else:
        if end == 'covered':
            ui.top = Page(ui, titles(3), name='localmusic_page', top=False).win
        elif end == 'animating':
            ui.animating = 1
        else:
            fw.var('g_backlight_status', C.c_ubyte).value = 0
        assert pull.move(0, PULL_SEARCH) == 0
    assert pull.hidden
    ui.top, ui.animating = page.win, 0
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0 and not fw.calls('stock_search')


def test_a_failed_bind_takes_back_what_it_bound_and_the_next_touch_binds_afresh(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page, fail_at=3)
    pull.down()
    assert pull.bound and Counter((w, k) for w, k, *_ in pull.bound) == Counter(pull.unbound)  # all taken back
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0 and not fw.calls('stock_search')
    pull.down()
    live = Counter((w, k) for w, k, *_ in pull.bound) - Counter(pull.unbound)
    assert set(live.values()) == {1}  # bound afresh, none twice
    pull.move(0, PULL_SEARCH)
    assert pull.up(0, PULL_SEARCH) == STOP and len(fw.calls('stock_search')) == 1


def test_a_page_binds_its_pull_once(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    first = len(pull.bound)
    pull.down()
    assert first and len(pull.bound) == first  # the second touch binds nothing more


# The Time Zone page's rows.
ZONES = host.tools_build.TIMEZONES
ORDER = host.tools_build.TIMEZONE_ORDER


class Zones:
    """Stock's time zone rows as stock builds them, row 0 first, then its caption, then the rest: each
    a list item whose last child is its button, holding an hscroll_label and a plain label. Each
    zone's offset now, in seconds, is offsets' (its label's standard one unless given)."""
    def __init__(self, fw, ui, offsets=None):
        self.fw, self.ui, self.zone = fw, ui, ''
        self.offsets = {zone: 60 * host.tools_build.timezone_offset(label) for label, zone in ZONES}
        self.offsets.update(offsets or {})
        self.stock, self.focus, self.texts = [], [], {}
        fw.mock('toolsSetTimeZone', lambda name, *_: setattr(self, 'zone', C.string_at(name).decode()))
        fw.mock('fw_localtime_r', self.local)
        fw.mock('stock_timezone_row_trampoline', lambda item, i, *_: self.stock.append(i))
        fw.mock('hscroll_label_set_only_focus', lambda label, on, *_: self.focus.append((label, on)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        self.list = ui.widget('list_view')
        self.caption_label = ui.widget('hscroll_label')  # stock adds it after row 0
        self.rows = []

    def local(self, when, out, *_):
        C.c_int.from_address(out + 36).value = self.offsets[self.zone]  # tm_gmtoff, the Q2's struct tm
        return out

    def build(self, i):
        if i == 1:  # stock's caption, between rows 0 and 1
            self.caption_label = self.ui.widget('hscroll_label', self.ui.widget('view', self.list))
        item = self.ui.widget('list_item', self.list)
        button = self.ui.widget('button', item)
        scroll, plain = self.ui.widget('hscroll_label', button), self.ui.widget('label', button)
        self.fw.fn('ringnav_timezone_row', None, C.c_void_p, C.c_int)(item.at, i)
        self.rows.append((scroll, plain))
        return self.texts.get(scroll.at), plain


def caption(offset, city):
    m = abs(offset) // 60
    return f'GMT{"-" if offset < 0 else "+"}{m // 60}{f":{m % 60:02d}" if m % 60 else ""} {city}'


def test_each_zone_row_reads_its_offset_now_west_to_east(fw, ui):
    zones = Zones(fw, ui, {'America/New_York': -4 * 3600, 'Europe/London': 3600})  # summer time
    shown = [zones.build(i)[0] for i in range(len(ZONES) + 1)]
    names = [zone for _, zone in ZONES]
    rows = sorted(range(len(ZONES)), key=lambda z: (zones.offsets[names[z]], ORDER.index(z)))
    cities = [label.split(' ', 1)[1] for label, _ in ZONES]
    assert shown[0] is None and zones.stock[0] == 0  # stock's own first row
    assert zones.stock[1:] == [z + 1 for z in rows]  # stock names and clicks the zone by its index
    assert shown[1:] == [caption(zones.offsets[names[z]], cities[z]) for z in rows]
    assert 'GMT-4 New York' in shown and 'GMT+1 London' in shown and 'GMT+5:45 Kathmandu' in shown
    assert 'GMT-3:30 St. John\'s' in shown


def test_zone_rows_keep_their_ellipsis_until_focused(fw, ui):
    zones = Zones(fw, ui)
    for i in range(3):
        zones.build(i)
    assert zones.focus == [(scroll.at, 1) for scroll, _ in zones.rows]
    assert not any(plain.at in zones.texts for _, plain in zones.rows)


def test_a_row_past_the_zones_is_stocks(fw, ui):
    zones = Zones(fw, ui)
    for i in range(len(ZONES) + 2):
        text = zones.build(i)[0]
    assert zones.stock[-1] == len(ZONES) + 1 and text is None


@pytest.mark.parametrize('index, moved', [(1, True), (0, False)])
def test_classic_lays_the_zone_caption_over_its_row(fw, ui, index, moved):
    theme(fw, index)
    zones = Zones(fw, ui)
    zones.build(0)
    zones.build(1)
    resized = [a[:5] for _, a in fw.calls('widget_move_resize') if a[0] == zones.caption_label.at]
    assert bool(resized) == moved
    if moved:
        assert resized[0][1:4] == (0, 0, 375)  # the screen's width, from the left


# Music loads leave out spoken tracks.
SONGS = [  # songtable's (id, album, fileurl): album X's music, the spoken tracks tagged X too, album Y
    (1, 'X', '/mnt/mmc/music/x/01.flac'), (2, 'X', '/mnt/mmc/audiobooks/x/01.mp3'),
    (3, 'X', '/mnt/mmc/podcasts/x/ep1.mp3'), (4, 'X', '/mnt/mmc/music/x/book.m4b'),
    (5, 'X', '/mnt/mmc/music/x/02.mp3'), (6, 'Y', '/mnt/mmc/music/y/01.flac')]


def songs(sql):
    """The ids of SONGS sql selects, as the Q2's sqlite runs it."""
    db = sqlite3.connect(':memory:')
    db.execute('create table songtable(id, album, songer, fileurl, albumsonger, track, cd_num)')
    db.executemany('insert into songtable(id, album, fileurl) values (?, ?, ?)', SONGS)
    return sorted(row[0] for row in db.execute(sql))


class Loads:
    """ringnav_library_query, inside a music load (staged_music) or not; the SQL stock is asked to run."""
    def __init__(self, fw):
        self.fw, self.ran = fw, []
        fw.mock('stock_dbquery_trampoline', lambda db, sql, *_: self.ran.append(C.string_at(sql).decode()) and 0)
        self.query = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)

    def music(self, sql):
        """sql as a music load runs it: its result, and the load's count staged_music hands back."""
        got = []

        @C.CFUNCTYPE(C.c_int, C.c_void_p)
        def load(arg):
            got.append(self.query(b'db', sql.encode(), None, 0))
            return 7

        count = C.c_int()
        self.fw.fn('staged_music', C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int))(load, None, C.byref(count))
        return got[0], count.value


def test_music_detail_loads_leave_out_spoken_tracks(fw):
    loads, sql = Loads(fw), "select * from songtable where album='X'"
    assert loads.music(sql) == (0, 7)
    loads.query(b'db', sql.encode(), None, 0)
    in_load, outside = loads.ran
    assert songs(in_load) == [1, 5]  # the album's music, its audiobook, podcast and .m4b left out
    assert songs(outside) == [1, 2, 3, 4, 5]  # outside a music load the query is stock's own


def test_a_music_load_too_long_to_filter_runs_nothing(fw):
    loads, head = Loads(fw), "select * from songtable where album='"
    (size,) = map(int, re.findall(r'\bfiltered\[(\d+)\]', NAVIGATION_SOURCE))
    sql = head + 'x' * (size - 2 - len(head)) + "'"  # as long as library_query's buffer can hold: no room for the filter
    assert loads.music(sql)[0] == -1 and not loads.ran  # not run unfiltered
    assert loads.query(b'db', sql.encode(), None, 0) == 0 and loads.ran == [sql]  # outside a load it runs as it is


# What is painted over a list.
@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


LCD_FILL = 0x12345678


class Canvas:
    """A canvas with an LCD (its fill colour LCD_FILL) and a clip of the whole screen; what it is asked
    to fill (colour, x, y, w, h), draw (icons, text) and clip to, in order."""
    def __init__(self, fw, clip=(0, 0, 375, 320)):
        self.block, self.lcd = C.create_string_buffer(0x100), C.create_string_buffer(0x200)
        struct.pack_into('<Q', self.block, O['CANVAS_LCD'], C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, O['LCD_FILL_COLOR'], LCD_FILL)
        self.at, self.clip = C.addressof(self.block), clip
        self.fill = self.text_color = self.stroke = 0
        self.fills, self.icons, self.clips, self.texts, self.boxes, self.images = [], [], [], [], [], {}
        fw.mock('canvas_set_fill_color', lambda c, color, *_: setattr(self, 'fill', color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, *box: self.fills.append((self.fill, *(C.c_int(v).value for v in box[:4]))))
        fw.mock('canvas_get_clip_rect', self.get_clip)
        fw.mock('canvas_set_clip_rect', lambda c, r, *_: self.clips.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('canvas_draw_icon', lambda c, bitmap, x, y, *_: self.icons.append((bitmap, C.c_int(x).value, C.c_int(y).value)))
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'text_color', color & 0xffffffff))
        fw.mock('canvas_measure_text', lambda *_: 10.0)  # every character 10 px
        fw.mock('canvas_draw_text', lambda c, s, n, x, y, *_: self.texts.append((self.chars(s, n), C.c_int(x).value, C.c_int(y).value, self.text_color)))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.boxes.append((self.chars(s, n), tuple((C.c_int * 4).from_address(r)), self.text_color)))
        fw.mock('widget_load_image', self.load)
        self.rounds, self.declines, self.strokes, self.drawn = [], 0, [], []  # declines: no vgcanvas
        fw.mock('canvas_fill_rounded_rect', lambda c, r, _, color, radius, *__: self.rounded('fill', r, color, radius))
        fw.mock('canvas_stroke_rounded_rect', lambda c, r, _, color, radius, *__: self.rounded('stroke', r, color, radius))
        fw.mock('canvas_set_stroke_color', lambda c, color, *_: setattr(self, 'stroke', color & 0xffffffff))
        fw.mock('canvas_stroke_rect', lambda c, *box: self.strokes.append((self.stroke, *(C.c_int(v).value for v in box[:4]))))
        fw.mock('canvas_draw_image', lambda c, bitmap, src, dst, *_: self.drawn.append((self.images[bitmap], tuple((C.c_int * 4).from_address(dst)))))

    def rounded(self, kind, r, color, radius):
        self.rounds.append((kind, tuple((C.c_int * 4).from_address(r)), C.c_uint.from_address(color).value, radius))
        return self.declines

    @staticmethod
    def chars(s, n):
        return ''.join(chr(c) for c in (C.c_uint * n).from_address(s))

    def get_clip(self, c, r, *_):
        (C.c_int * 4).from_address(r)[:] = list(self.clip)

    def load(self, w, name, bitmap, *_):
        """Each image 12 x 16, at the address of the bitmap it filled."""
        (C.c_uint * 2).from_address(bitmap)[:] = [12, 16]
        self.images[bitmap] = C.string_at(name)


PAGE_BLACK = 0xff000000  # RGBA(0): the page's black, which the corners are painted in


def arc(r, i):
    """How far in, in px, a radius-r round corner's row i (0 the outermost) starts: where the circle
    crosses the middle of the row."""
    y = r - i - 0.5  # the row's middle, up from the circle's centre
    return r - math.sqrt(r * r - y * y)


def painted(fills, size):
    """How much of each pixel of a size-px square art the fills cover in the page's black, 0 to 1, as
    alpha blending builds it up: a fill at alpha a leaves 1 - a of what was under it."""
    cover = {}
    for color, x, y, w, h in fills:
        assert color & 0xffffff == PAGE_BLACK & 0xffffff  # all in the page's black, at any alpha
        for px in range(x, x + w):
            for py in range(y, y + h):
                assert 0 <= px < size and 0 <= py < size  # on the art
                cover[px, py] = 1 - (1 - cover.get((px, py), 0)) * (1 - (color >> 24) / 255)
    return cover


def art(ui, size=166, box=None):
    return ui.widget('image', ui.widget('window', ui.wm, (0, 0, 375, 320)), box or (10, 20, size, size))


def test_now_playings_art_has_round_corners_in_the_pages_black(fw, ui, st):
    size = 166
    canvas, cover = Canvas(fw), art(ui, size)
    st('np_cover', C.c_void_p).value = cover.at
    fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(cover.at, canvas.at)
    r = O['NP_ART_RADIUS']  # its radius at 166 px
    black = painted(canvas.fills, size)
    assert {y for _, y in black} == {*range(r), *range(size - r, size)}  # r rows a corner; the straight edges are the art's own
    edges = []
    for i in range(r):
        # row i from each corner's side inward: top and bottom, the left and the mirrored right
        lines = [[black.get((x if left else size - 1 - x, y), 0) for x in range(size // 2)]
                 for y in (i, size - 1 - i) for left in (True, False)]
        assert all(line == lines[0] for line in lines)  # the four corners alike
        line = lines[0]
        whole = next(x for x, c in enumerate(line) if c < 1)
        assert not any(line[whole + 1:])  # whole black pixels, at most one part pixel, then the art
        # where the arc crosses the row: to 1/16 px, its edge pixel's alpha in 16 steps from 0 to 255,
        # so the paint may run 1/16 px more on top
        assert abs(whole + line[whole] - arc(r, i)) < 1 / 8
        edges.append((arc(r, i) - whole, line[whole]))  # how far the arc reaches past the whole pixels; that pixel's paint
    # the edge pixel is painted only where the arc reaches into it, the more the further it reaches
    assert all((part > 0) == (reach > 0) for reach, part in edges)
    assert [part for _, part in sorted(edges)] == sorted(part for _, part in edges)
    assert {part > 0 for _, part in edges} == {True, False}  # both kinds of row at this radius
    assert canvas.fill == LCD_FILL


def test_smaller_art_has_smaller_corners(fw, ui, st):
    rows = []
    for size in (166, 83):
        canvas, cover = Canvas(fw), art(ui, size)
        st('np_cover', C.c_void_p).value = cover.at
        fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(cover.at, canvas.at)
        rows.append(len({y for _, _, y, _, _ in canvas.fills if y < size // 2}))
    assert rows[1] * 2 == rows[0]


def test_the_full_screen_arts_corners_follow_its_image(fw, ui, st):
    canvas = Canvas(fw)
    window = ui.widget('window', ui.wm, (0, 0, 375, 320))
    image = ui.widget('image', window, (40, 30, 166, 166), name='img_bigcover')
    st('bc', C.c_void_p * 3)[2] = window.at
    fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(window.at, canvas.at)
    assert canvas.fills and all(40 <= x and x + w <= 40 + 166 and 30 <= y < 30 + 166 for _, x, y, w, _ in canvas.fills)
    assert {x for _, x, _, w, _ in canvas.fills if w > 1} >= {40}  # from the image's left edge, not the window's


@pytest.mark.parametrize('index, rounded, paints', [(0, 0, True), (1, 0, False), (1, 1, False), (2, 0, False), (2, 1, True)])
def test_square_art_themes_leave_the_corners(fw, ui, st, index, rounded, paints):
    theme(fw, index, ROUNDED=rounded)
    canvas, cover = Canvas(fw), art(ui)
    st('np_cover', C.c_void_p).value = cover.at
    fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(cover.at, canvas.at)
    assert bool(canvas.fills) == paints


def test_art_with_room_for_a_reflection_is_not_rounded(fw, ui, st):
    rounded = []
    for box in ((10, 20, 166, 166), (10, 20, 166, 250)):  # square, then taller: Classic's reflection under it
        canvas, cover = Canvas(fw), art(ui, box=box)
        st('np_cover', C.c_void_p).value = cover.at
        fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(cover.at, canvas.at)
        rounded.append(bool(canvas.fills))
    assert rounded == [True, False]


def test_only_the_art_gets_corners(fw, ui, st):
    canvas, cover = Canvas(fw), art(ui)
    st('np_cover', C.c_void_p).value = cover.at
    paint_cover = fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)
    paint_cover(art(ui).at, canvas.at)  # another image
    paint_cover(cover.at, C.create_string_buffer(0x100))  # a canvas with no LCD
    assert not canvas.fills


# The fast-scroll letter.
def spun(fw, ui, names, name='allmusic_page'):
    """A list spun past one row a tick (the letter's run), and the canvas to paint it on."""
    page = Page(ui, names, name=name)
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    spin(fw, ui, KEY_NEXT, 6, 100)
    return page, Canvas(fw)


def letter(fw, page, canvas):
    fw.fn('navigation__paint_letter', None, C.c_void_p, C.c_void_p)(page.view.at, canvas.at)
    return [text for text, _, _ in canvas.boxes]


@pytest.mark.parametrize('title, name, shown', [
    ('beatles', 'allmusic_page', 'B'), ('The Beatles', 'allmusic_page', 'B'), ('An Elephant', 'allmusic_page', 'E'),
    ('A Tribe', 'allmusic_page', 'T'), ('A', 'allmusic_page', 'A'), ('The ', 'allmusic_page', 'T'),  # no name after it
    ('Theory', 'allmusic_page', 'T'), ('  zed', 'allmusic_page', 'Z'),
    ('The Beatles', 'radio_page', 'T'),  # only the Library sorts past "The"
    ('Éclair', 'allmusic_page', 'É'),
    ('', 'allmusic_page', None), ('   ', 'allmusic_page', None),
])
def test_the_letter_is_the_one_the_selected_row_sorts_under(fw, ui, title, name, shown):
    page, canvas = spun(fw, ui, titles(200), name)
    page.rows[page.selected].children[0].text = title
    assert letter(fw, page, canvas) == ([shown] if shown else [])


def test_the_letter_sits_in_the_middle_of_the_list_and_puts_the_canvas_back(fw, ui):
    page, canvas = spun(fw, ui, titles(200))
    letter(fw, page, canvas)
    (_, (x, y, w, h), ink), = canvas.boxes
    assert (x, y) == ((375 - w) // 2, (VIEW_H - h) // 2) and w == h
    (kind, box, fill, _), = canvas.rounds
    assert kind == 'fill' and box == (x, y, w, h)  # the letter centred on its square
    assert 0 < fill >> 24 < 255  # a translucent square: the list shows through
    assert all((ink >> s & 255) > (fill >> s & 255) for s in (0, 8, 16))  # light ink on the dark square
    assert canvas.fill == LCD_FILL and canvas.clips[-1] == canvas.clip


def test_a_menu_or_a_slow_turn_shows_no_letter(fw, ui):
    page, canvas = spun(fw, ui, titles(200), 'sysset_page')  # a fixed menu is not alphabetical
    assert letter(fw, page, canvas) == []
    slow = Page(ui, titles(200))
    paint(fw, slow)
    spin(fw, ui, KEY_NEXT, 6, SLOW)
    assert letter(fw, slow, canvas) == []


def test_the_letter_goes_with_its_timer_or_a_touch(fw, ui):
    page, canvas = spun(fw, ui, titles(200))
    ui.fire(ui.due('letter_expire')[0])
    assert letter(fw, page, canvas) == []
    page, canvas = spun(fw, ui, titles(200))
    fw.fn('ringnav_touch', C.c_int, C.c_void_p, C.c_void_p)(None, C.create_string_buffer(0x40))
    assert letter(fw, page, canvas) == []


def test_homes_library_pane_shows_no_letter(fw, ui):
    page, canvas = spun(fw, ui, titles(200))
    page.view.props[b'name'] = b'scroll_view_localmusic'
    assert letter(fw, page, canvas) == []


def test_coverflows_album_tracks_show_no_letter(fw, ui):
    page = Page(ui, titles(200), name='coverflow_page')
    album = [Rec('Abbey Road', artist='The Beatles')]  # the open album: its key is the track list's scope
    deque_of(fw, album)
    flow = cf(fw)
    flow.page, flow.albums, flow.tracks, flow.album, flow.screen = page.win.at, 0x5000, 0x5100, 0, TRACKS
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    spin(fw, ui, KEY_NEXT, 6, 100)
    canvas = Canvas(fw)
    assert letter(fw, page, canvas) == []  # its tracks are in album order (disc and track), not A to Z
    flow.screen = COVERS
    assert letter(fw, page, canvas) == ['S']  # the same spin with no album open keeps its letter


@pytest.mark.parametrize('name, cls', [('albuminfo_page', 0xff10), ('albuminfo_page', 0xff11),
                                       ('playerjumpinfo_page', 0xff10)])
def test_an_albums_songs_in_the_library_show_no_letter(fw, ui, name, cls):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    page, canvas = spun(fw, ui, titles(200), name)
    assert letter(fw, page, canvas) == []  # in disc and track order, not A to Z
    Page(ui, titles(200), name='album_page')  # Return: the page behind is the top while the songs slide out
    assert letter(fw, page, canvas) == []


@pytest.mark.parametrize('name, cls', [
    ('album_page', O['CLASS_ALBUMS']),  # the Albums list a Return brings back
    ('artistinfo_page', 0xff11),  # Go to Artist from an album, one of its albums opened, Return: stock reloads nothing
])
def test_a_name_ordered_library_list_keeps_its_letter(fw, ui, name, cls):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    C.c_int.from_buffer(fw.var('fw_ctx_g_local_classinfo_save', C.c_ubyte * 912)).value = cls
    page, canvas = spun(fw, ui, titles(200), name)
    assert letter(fw, page, canvas) == ['S']


# The iPod `>` on rows that open another list.
def chevrons(fw, page, canvas):
    fw.fn('navigation__paint_chevrons', None, C.c_void_p, C.c_void_p)(page.view.at, canvas.at)
    return [(canvas.images[bitmap], x, y) for bitmap, x, y in canvas.icons]


def test_a_drill_lists_rows_each_get_a_chevron_lined_up_on_their_middles(fw, ui):
    page, canvas = Page(ui, titles(12), name='playlist_page'), Canvas(fw)
    page.scroll(ROW_H)
    drawn = chevrons(fw, page, canvas)
    assert len(drawn) == 12 and {name for name, _, _ in drawn} == {b'list_into'}
    assert len({x for _, x, _ in drawn}) == 1 and drawn[0][1] > 375 // 2  # one column, at the right
    assert [y for _, _, y in drawn] == [ROW_H * i - ROW_H + ROW_H // 2 for i in range(12)]
    assert canvas.clips[-1] == canvas.clip


def test_rows_with_more_dots_or_tiles_get_no_chevron(fw, ui):
    page, canvas = Page(ui, titles(6), name='playlist_page'), Canvas(fw)
    ui.widget('image', page.rows[1], (330, 0, 30, 30), name='img_more1')
    struct.pack_into('<i', page.rows[2].block, O['W_W'], 375 // 2 - 1)  # a grid tile
    assert [y for _, _, y in chevrons(fw, page, canvas)] == [ROW_H * i + ROW_H // 2 for i in (0, 3, 4, 5)]


@pytest.mark.parametrize('name, navbar', [('allmusic_page', 0), ('playlist_page', 1)])
def test_no_chevrons_off_a_drill_list_or_in_multi_select(fw, ui, name, navbar):
    fw.var('g_navbar_status', C.c_ubyte).value = navbar
    page, canvas = Page(ui, titles(6), name=name), Canvas(fw)
    assert chevrons(fw, page, canvas) == []


def test_classic_draws_only_the_selected_rows_chevron_in_white(fw, ui, st):
    theme(fw, 1)
    page, canvas = Page(ui, titles(6), name='playlist_page'), Canvas(fw)
    st('sel_w', C.c_void_p).value = page.view.at
    st('sel_row').value = 3
    (name, x, y), = chevrons(fw, page, canvas)
    assert name == b'classic_light_chevron' and y == ROW_H * 3 + ROW_H // 2


def test_classic_draws_no_chevron_when_the_selection_is_elsewhere(fw, ui, st):
    theme(fw, 1)
    page = Page(ui, titles(6), name='playlist_page')  # a drill list: its selected row would get one
    other = Page(ui, titles(6), name='playlist_page', top=False)
    st('sel_row').value = 3
    drawn = []
    for holder in (other, page):
        st('sel_w', C.c_void_p).value = holder.view.at
        drawn.append([y for _, _, y in chevrons(fw, page, Canvas(fw))])
    assert drawn == [[], [ROW_H * 3 + ROW_H // 2]]


@pytest.mark.parametrize('index, shown', [(0, False), (2, True)])
def test_homes_chevron_rides_its_selection_where_the_theme_has_one(fw, ui, st, index, shown):
    theme(fw, index)
    page, canvas = Page(ui, titles(6), name='home_page'), Canvas(fw)
    st('sel_w', C.c_void_p).value = page.view.at
    st('sel_row').value, st('sel_y').value = 2, 77
    drawn = chevrons(fw, page, canvas)
    assert [y for _, _, y in drawn] == ([77] if shown else [])


# Minimal's small capitals.
TRACK = O['SUDO_TRACK']


def caps(fw, canvas, w):
    fw.fn('navigation__paint_caps', None, C.c_void_p, C.c_void_p)(w.at, canvas.at)
    return canvas.texts


def test_the_idle_bars_mark_is_spaced_capitals(fw, ui, st):
    canvas, mark = Canvas(fw), ui.widget('label', None, (0, 0, 60, 30), text='q2')
    st('bar_idle', C.c_void_p).value = mark.at
    drawn = caps(fw, canvas, mark)
    assert [(c, x) for c, x, _, _ in drawn] == [('Q', 0), ('2', 10 + TRACK)]
    assert len({y for _, _, y, _ in drawn}) == 1


def test_a_theme_without_capitals_leaves_the_mark_to_its_label(fw, ui, st):
    theme(fw, 2)
    canvas, mark = Canvas(fw), ui.widget('label', None, (0, 0, 60, 30), text='q2')
    st('bar_idle', C.c_void_p).value = mark.at
    assert caps(fw, canvas, mark) == []


class Album:
    """Now Playing's album label in its parent view, on top of the window manager."""
    def __init__(self, fw, ui, st, text, width=100):
        self.win = ui.widget('window', ui.wm, (0, 0, 375, 320), name='playing_page')
        self.view = ui.widget('view', self.win, (0, 0, 375, 320))
        self.label = ui.widget('label', self.view, (30, 200, width, 20), text=text)
        st('np_album', C.c_void_p).value = self.label.at
        st('np_win', C.c_void_p).value = self.win.at
        ui.top = self.win
        self.room = width - O['NP_CAPS_INSET']


def test_a_short_album_is_drawn_whole_and_stays_still(fw, ui, st):
    canvas, album = Canvas(fw), Album(fw, ui, st, 'abc')
    drawn = caps(fw, canvas, album.view)
    x0 = 30 + O['NP_CAPS_INSET']
    assert [(c, x) for c, x, _, _ in drawn] == [('A', x0), ('B', x0 + 10 + TRACK), ('C', x0 + 2 * (10 + TRACK))]
    assert not ui.due('np_marquee') and canvas.clips[-1] == canvas.clip


def test_a_long_album_is_cut_at_its_label_and_starts_its_marquee(fw, ui, st):
    canvas, album = Canvas(fw), Album(fw, ui, st, 'x' * 20)
    drawn = caps(fw, canvas, album.view)
    fits = (album.room + TRACK) // (10 + TRACK)
    assert len(drawn) == fits and all(c == 'X' for c, _, _, _ in drawn)
    (timer,) = ui.due('np_marquee')
    assert ui.timers[timer][2] == O['MARQUEE_HOLD_MS']
    assert st('np_over').value == 20 * (10 + TRACK) - TRACK - album.room


@pytest.mark.parametrize('text, shown', [('é÷ÿ', 'É÷ÿ'), ('àz', 'ÀZ')])
def test_capitals_cover_latin_1(fw, ui, st, text, shown):
    canvas, album = Canvas(fw), Album(fw, ui, st, text)
    assert ''.join(c for c, _, _, _ in caps(fw, canvas, album.view)) == shown


def test_the_album_marquee_holds_scrolls_holds_and_comes_back(fw, ui, st):
    canvas, album = Canvas(fw), Album(fw, ui, st, 'x' * 9)
    caps(fw, canvas, album.view)
    over, px = st('np_over').value, NAV['MARQUEE_PX']
    assert 0 < over < 10 * px
    offsets, waits = [], []
    for _ in range(-(-over // px) + 1):
        ui.invalidated.clear()
        ui.fire(ui.due('np_marquee')[0])
        (timer,) = ui.due('np_marquee')
        offsets.append(st('np_off').value)
        waits.append(ui.timers[timer][2])
        assert ui.invalidated == [album.label.at]
    want = [min(over, px * k) for k in range(1, len(offsets))] + [0]
    assert offsets == want
    assert waits == [NAV['MARQUEE_MS']] * (len(offsets) - 2) + [O['MARQUEE_HOLD_MS']] * 2


def test_the_album_marquee_stops_under_another_page(fw, ui, st):
    canvas, album = Canvas(fw), Album(fw, ui, st, 'x' * 20)
    caps(fw, canvas, album.view)
    Page(ui, titles(3))
    ui.fire(ui.due('np_marquee')[0])
    assert not ui.due('np_marquee') and st('np_off').value == 0


# The selected row's title marquee.
class Marquee:
    """Row row of page selected and painted, its title an hscroll_label of text_w px in a 300 px label."""
    def __init__(self, fw, ui, st, text_w, row=3, only_focus=1):
        self.page = Page(ui, titles(10))
        self.labels = []
        for r in self.page.rows:
            label = ui.widget('hscroll_label', r.children[0], (0, 0, 300, 20))
            label.int(O['HSCROLL_TEXT_W'], text_w)
            label.byte(O['HSCROLL_ONLY_FOCUS'], only_focus)
            self.labels.append(label)
        st('sel_w', C.c_void_p).value = self.page.view.at
        st('sel_row').value = st('marq_id').value = row
        st('marq_row', C.c_void_p).value = self.page.rows[row].at
        self.label = self.labels[row]
        self.moved = fw.fn('navigation__marq_moved', C.c_int, C.c_void_p)


def test_the_selected_rows_long_title_scrolls(fw, ui, st):
    marquee = Marquee(fw, ui, st, 400)
    marquee.moved(None)
    assert [a[0] for _, a in fw.calls('hscroll_label_start')] == [marquee.label.at]
    assert marquee.label.byte(O['HSCROLL_LOOP']) == 1
    assert (marquee.label.at, 0) in [a[:2] for _, a in fw.calls('hscroll_label_set_only_focus')]


@pytest.mark.parametrize('text_w, only_focus', [(300 - 4, 1), (400, 0)])
def test_a_title_that_fits_or_scrolls_anyway_is_left_alone(fw, ui, st, text_w, only_focus):
    marquee = Marquee(fw, ui, st, text_w, only_focus=only_focus)
    marquee.moved(None)
    assert not fw.calls('hscroll_label_start')


def test_the_marquee_stops_once_the_selection_moves_on(fw, ui, st):
    marquee = Marquee(fw, ui, st, 400)
    marquee.moved(None)
    st('sel_row').value = 4
    marquee.moved(None)
    assert [a[0] for _, a in fw.calls('hscroll_label_stop')] == [marquee.label.at]
    assert fw.calls('hscroll_label_set_only_focus')[-1][1][:2] == (marquee.label.at, 1)  # its ellipsis back
    assert len(fw.calls('hscroll_label_start')) == 1 and st('marq_row', C.c_void_p).value is None


def test_a_destroyed_marquee_label_is_not_stopped(fw, ui, st):
    marquee = Marquee(fw, ui, st, 400)
    marquee.moved(None)
    (w, kind, fn, ctx), = [a[:4] for _, a in fw.calls('widget_on')]
    assert (w, kind) == (marquee.label.at, O['EVT_DESTROY'])
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
    marquee.moved(None)
    assert not fw.calls('hscroll_label_stop')


@pytest.mark.parametrize('speed, paced', [(0.0, True), (0.2, True), (0.02, False)])
def test_every_scrolling_label_holds_then_runs_at_most_the_pace(fw, speed, paced):
    label = C.create_string_buffer(0x100)
    struct.pack_into('<f', label, O['HSCROLL_SPEED'], speed)
    fw.fn('ringnav_hscroll_start', C.c_int, C.c_void_p)(label)
    assert struct.unpack_from('<i', label, O['HSCROLL_DELAY'])[0] == O['MARQUEE_HOLD_MS']
    pace = struct.unpack('<f', struct.pack('<I', O['HSCROLL_PACE']))[0]
    assert struct.unpack_from('<f', label, O['HSCROLL_SPEED'])[0] == pytest.approx(pace if paced else speed)
    assert fw.calls('stock_hscroll_start_trampoline')[0][1][0] == C.addressof(label)


# The Display settings the list engine reads.
@pytest.mark.parametrize('index, keys, on', [(1, {}, 1), (1, {'HOMELIB': 0}, 0), (0, {'HOMELIB': 1}, 0), (2, {'HOMELIB': 1}, 0)])
def test_library_on_home_is_classics_own_choice(fw, index, keys, on):
    theme(fw, index, **keys)
    assert fw.fn('home_lib_on', C.c_int)() == on


@pytest.mark.parametrize('keys, on', [({}, 1), ({'ROWPREVIEW': 0}, 0)])
def test_row_preview_is_classics_own_choice(fw, keys, on):
    theme(fw, 1, **keys)
    assert fw.fn('row_preview_on', C.c_int)() == on


@pytest.mark.parametrize('shortcut, card, row', [(None, 1, 1), (None, 0, 1), (0, 1, 1), (0, 0, 0), (2, 0, 1)])
def test_home_shows_its_shortcut_row_unless_it_is_a_missing_rockbox(fw, shortcut, card, row):
    theme(fw, 0, **({} if shortcut is None else {'SHORTCUT': shortcut}))
    def stat(version, path, info, *_):
        C.c_uint.from_address(info + 20).value = 0o100755
        return 0 if card else -1
    fw.mock('fw___xstat', stat)
    assert bool(fw.fn('ipod_home_rockbox', C.c_int)()) == bool(row)


@pytest.mark.parametrize('shortcut, page, context', [
    (1, b'localmusic/localclass_page', 0xf004), (3, b'localmusic/allmusic_page', 0xf00a),
    (7, b'localmusic/playlist_page', 0),
])
def test_the_home_shortcut_opens_its_list(fw, shortcut, page, context):
    theme(fw, 0, SHORTCUT=shortcut)
    fw.fn('home_shortcut_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    (name, ctx), = [a[:2] for _, a in fw.calls('navigator_to_with_context')]
    assert C.string_at(name) == page and ctx == context


def test_the_albums_shortcut_opens_all_albums(fw):
    theme(fw, 0, SHORTCUT=2)
    seen = []
    fw.mock('navigator_to_with_context', lambda name, ctx, *_: seen.append((C.string_at(name), C.c_int.from_address(ctx).value)))
    fw.fn('home_shortcut_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert seen == [(b'localmusic/album_page', O['CLASS_ALBUMS'])]


@pytest.mark.parametrize('light', [0, 1])
def test_unflip_finds_the_grey_light_mode_draws_as_v(fw, light):
    theme(fw, 0, LIGHT=light)
    unflip = fw.fn('unflip', C.c_uint, C.c_uint)
    flip_v = fw.fn('flip_v', C.c_uint, C.c_uint, C.c_uint, C.c_uint)
    flip = fw.fn('flip', C.c_uint, C.c_uint)
    paper, ink = flip(0xff000000) & 255, flip(0xffffffff) & 255  # where black and white go
    for v in range(256):
        u = unflip(v)
        if not light:
            assert u == v
        elif ink <= v <= paper:
            assert abs(flip_v(u, paper, ink) - v) <= 1 and 0 <= u <= 255
        else:
            assert u == (255 if v < ink else 0)  # past the ends: the nearest Light Mode can draw


# Pills and gradients.
class Rect(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'xywh']


def gradient(fw, canvas, r, top, bottom, hi, radius):
    fw.fn('navigation__gradient', None, C.c_void_p, Rect, C.c_uint, C.c_uint, C.c_uint, C.c_int)(canvas.at, Rect(*r), top, bottom, hi, radius)
    rows = {}
    for color, x, y, w, h in canvas.fills:
        rows.setdefault(y, []).append((color, x, w))
    return rows


def rgba(c, a=0xff):
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16


def test_a_square_gradient_runs_top_to_bottom_under_its_highlight(fw):
    canvas = Canvas(fw)
    rows = gradient(fw, canvas, (5, 10, 100, 9), 0x000000, 0x8080ff, 0xffffff, 0)
    assert sorted(rows) == list(range(10, 19)) and all(len(v) == 1 and v[0][1:] == (5, 100) for v in rows.values())
    colors = [rows[y][0][0] for y in range(10, 19)]
    assert colors[0] == rgba(0xffffff) and colors[-1] == rgba(0x8080ff)
    blues = [c >> 16 & 255 for c in colors[1:]]
    assert blues == sorted(blues) and blues[0] < 0xff  # from top's toward bottom's


def test_a_pill_gradient_rounds_both_ends_alike(fw):
    canvas = Canvas(fw)
    h = 20
    rows = gradient(fw, canvas, (0, 0, 120, h), 0x202020, 0x404040, 0x606060, h // 2)
    def inset(y):
        return min(x for _, x, w in rows[y] if w > 1)
    insets = [inset(y) for y in range(h)]
    assert insets == insets[::-1] and insets[0] > h // 4 and insets[h // 2 - 1] <= 1
    assert insets[:h // 2] == sorted(insets[:h // 2], reverse=True)
    for y in range(h):  # an end's part pixel sits just outside the band, both sides alike
        edge = [(color >> 24, x) for color, x, w in rows[y] if w == 1]
        if edge:
            assert [a for a, _ in edge] == [edge[0][0]] * 2 and 0 < edge[0][0] <= 255
            assert sorted(x for _, x in edge) == [inset(y) - 1, 120 - inset(y)]


def pill(fw, ui, h, children, inset=0):
    row = ui.widget('list_item', None, (0, 0, 300, h))
    for box, kind, props, visible in children:
        c = ui.widget(kind, row, box, visible=visible)
        c.props.update(props)
    return fw.fn('navigation__pill_radius', C.c_int, C.c_void_p, C.c_int, C.c_int)(row.at, h, inset)


def test_a_pill_without_art_is_half_its_height(fw, ui):
    assert pill(fw, ui, 40, []) == 20


@pytest.mark.parametrize('box', [(4, 4, 32, 32), (10, 2, 36, 36), (2, 8, 30, 24)])
def test_a_pill_clears_a_leading_covers_corner(fw, ui, box):
    h = 40
    r = pill(fw, ui, h, [(box, 'image', {}, True)])
    x, y = box[0], min(box[1], h - box[1] - box[3])
    def inside(r):
        return (r - x) ** 2 + (r - y) ** 2 <= r * r
    assert r < h // 2 and inside(r) and (r + 1 > h // 2 or not inside(r + 1))


@pytest.mark.parametrize('kind, props, visible, x', [
    ('image', {b'draw_type': O['IMAGE_DRAW_CENTER']}, True, 4), ('image', {}, False, 4),
    ('label', {}, True, 4), ('image', {}, True, 25),
])
def test_icons_hidden_checks_text_and_art_past_the_end_leave_the_pill(fw, ui, kind, props, visible, x):
    assert pill(fw, ui, 40, [((x, 4, 32, 32), kind, props, visible)]) == 20


# Finding the pane the wheel drives.
@pytest.mark.parametrize('depth, filler, found', [(3, 0, True), (20, 0, False), (0, 50, True), (0, 600, False)])
def test_the_pane_search_is_bounded(fw, ui, depth, filler, found):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    for _ in range(filler):  # widgets before the list that hold nothing to navigate
        ui.widget('label', win, (0, 0, 10, 10))
    parent = win
    for _ in range(depth):
        parent = ui.widget('view', parent, (0, 0, 375, VIEW_H))
    view = scroll_list(ui, parent, titles(20))
    ui.top = win
    key(fw, ui, KEY_NEXT)
    assert (view.props.get(SEL, -1) == 1) == found


@pytest.mark.parametrize('active, moved', [(1, 1), (0, 0), (-1, None)])
def test_the_wheel_drives_only_the_shown_tabs_list(fw, ui, active, moved):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    pages = ui.widget('pages', win, (0, 0, 375, VIEW_H))
    pages.props[b'active'] = active
    tabs = [scroll_list(ui, pages, titles(20, 100 * k)) for k in range(2)]
    ui.top = win
    key(fw, ui, KEY_NEXT)
    assert [t.props.get(SEL, -1) == 1 for t in tabs] == [k == moved for k in range(2)]


def test_a_dialogs_hidden_tab_has_no_buttons_to_reach(fw, ui):
    win = ui.widget('dialog', ui.wm, (40, 100, 300, 120), name='confirminfo_dialog')
    pages = ui.widget('pages', win, (0, 0, 300, 120))
    pages.props[b'active'] = 0
    for k, n in enumerate((2, 3)):
        tab = ui.widget('view', pages, (0, 0, 300, 120))
        for i in range(n):
            ui.widget('button', tab, (20 + 90 * i, 70, 80, 40), click=True, text=f'{k}{i}')
    ui.top = win
    for _ in range(4):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert win.props[SEL] == 1  # the shown tab's last button: the hidden tab's three are not rows


def test_a_row_whose_click_is_going_takes_its_inner_button_as_the_target(fw, ui):
    page = Page(ui, titles(10))
    row = page.rows[2]
    row.item[O['EMIT_PENDING_REMOVE']] = b'\1'
    button = ui.widget('button', row, (300, 0, 60, ROW_H), click=True)
    clicks = centre(fw, ui, page, 2)
    ui.fire(ui.due('confirm_center')[0])
    assert clicks.seen == [(button.at, EVT_CLICK)]


def two_panes(ui):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    panes = [scroll_list(ui, win, titles(20, 100 * k), (190 * k, 0, 180, VIEW_H)) for k in range(2)]
    ui.top = win
    return win, panes


def test_of_two_panes_the_wheel_takes_the_first_then_the_one_holding_a_selection(fw, ui):
    win, (left, right) = two_panes(ui)
    key(fw, ui, KEY_NEXT)
    assert left.props[SEL] == 1 and SEL not in right.props
    del left.props[SEL]
    scope = b'_ringnav_scope'
    right.props.update({SEL: 4, COUNT: 20, scope: left.props[scope]})  # loaded, its row 4 chosen
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert right.props[SEL] == 5 and SEL not in left.props


def test_two_panes_both_selected_take_no_tick(fw, ui):
    win, (left, right) = two_panes(ui)
    left.props[SEL], right.props[SEL] = 3, 4
    key(fw, ui, KEY_NEXT)
    assert (left.props[SEL], right.props[SEL]) == (3, 4)


def test_a_tap_finds_its_pane_by_ancestry(fw, ui):
    win, (left, right) = two_panes(ui)
    under = fw.fn('navigation__surface_under', C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p), C.c_int)
    other = C.c_void_p()
    label = right.children[3].children[0]
    assert under(win.at, label.at, C.byref(other), 0) == right.at and other.value == left.at
    assert under(win.at, win.at, C.byref(other), 0) is None  # the window itself: no pane
    stray = ui.widget('label', ui.widget('view', None, (0, 0, 1, 1)))
    assert under(win.at, stray.at, C.byref(other), 0) is None


def test_the_playlist_picker_keeps_its_own_row(fw, ui):
    chosen(fw, ui, Page(ui, titles(20), name='playlist_page'), 5)
    picker = Page(ui, titles(20), name='playlist_page')
    picker.win.props[b'_pl_mode'] = 1  # Add to Playlist
    paint(fw, picker)
    assert picker.selected == 0
    chosen(fw, ui, picker, 8)
    for mode, row in ((0, 5), (1, 8)):
        again = Page(ui, titles(20), name='playlist_page')
        again.win.props[b'_pl_mode'] = mode
        paint(fw, again)
        assert again.selected == row


@pytest.mark.parametrize('style, scrolls', [(b's_btn_forwardspeed', False), (b's_btn_other', True)])
def test_a_speed_buttons_row_is_its_visible_button(fw, ui, style, scrolls):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='playset_page')
    view = ui.widget('scroll_view', win, (0, 0, 375, 100))
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], 61 * 6)
    for i in range(6):  # 61 px tap targets whose buttons show 24 px
        holder = ui.widget('view', view, (0, 61 * i, 375, 61))
        holder.props[b'style'] = style
        ui.widget('image', holder, (0, 0, 375, 61), click=True)
    ui.top = win
    key(fw, ui, KEY_NEXT)
    assert view.props[SEL] == 1 and (view.int(SCROLL_Y) > 0) == scrolls


@pytest.mark.parametrize('holder, tall', [('list_item', True), ('view', False)])
def test_a_button_over_the_top_of_its_list_row_is_selected_as_the_whole_row(fw, ui, holder, tall):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    view = ui.widget('scroll_view', win, (0, 0, 375, VIEW_H))
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], 40 * 6)
    for i in range(6):  # the Library's rows: a 30 px button over the top of a 40 px row; or in a plain view
        ui.widget('button', ui.widget(holder, view, (0, 40 * i, 375, 40)), (0, 0, 375, 30), click=True, text=f'Row {i}')
    ui.top = win
    key(fw, ui, KEY_NEXT)
    canvas = Canvas(fw)
    fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)(view.at, canvas.at)
    (_, (_, y, _, h), _, _), = [r for r in canvas.rounds if r[0] == 'fill']  # Minimal's card
    assert y == 40 and h == (40 if tall else 30)  # a row's whole pitch; a button alone only its own


def test_a_tick_stops_the_lists_own_glide(fw, ui):
    page = Page(ui, titles(40))
    paint(fw, page)
    page.view.ptr(O['VIEW_ANIMATOR'], 0x7777)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert [a[0] for _, a in fw.calls('widget_animator_destroy')] == [0x7777] and page.view.ptr(O['VIEW_ANIMATOR']) == 0
    assert [a[0] for _, a in fw.calls('widget_animator_pause')] == [0x7777]


def test_a_virtual_table_keeps_its_row_while_it_glides_past_then_settles_in_view(fw, ui):
    table = Table(fw, ui, 100)
    for _ in range(40):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    table.bind(0)  # flung back to the top: row 40 is in no pool widget
    table.client.ptr(O['TABLE_ANIMATOR'], 0x5555)
    paint(fw, table)
    assert table.selected == 40
    table.client.ptr(O['TABLE_ANIMATOR'], 0)
    paint(fw, table)
    assert 0 <= table.selected < VIEW_H // ROW_H


def test_a_row_added_before_the_confirm_cancels_the_click(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    ui.widget('list_item', page.view, (0, ROW_H * 30, 375, ROW_H), click=True)
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


def test_a_row_rebuilt_in_place_before_the_confirm_is_not_clicked(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    old = page.view.children[3]
    new = ui.widget('list_item', None, old.box, click=True)
    ui.widget('label', new, (10, 0, 300, 20), text=old.children[0].text)  # the same text, another widget
    struct.pack_into('<Q', new.block, W_PARENT, page.view.at)
    page.view.children[3] = new
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


@pytest.mark.parametrize('rows, parent, woken', [(40, 'list_view', True), (5, 'list_view', False), (40, 'view', False)])
def test_a_tick_wakes_the_lists_own_scroll_bar_where_it_has_one(fw, ui, rows, parent, woken):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    holder = ui.widget(parent, win, (0, 0, 375, VIEW_H))
    scroll_list(ui, holder, titles(rows))
    bar = ui.widget('scroll_bar_m', holder, (370, 0, 5, VIEW_H))
    bar.int(O['BAR_VALUE'], 123)
    ui.top = win
    key(fw, ui, KEY_NEXT)
    seen = [a[:2] for _, a in fw.calls('scroll_bar_scroll_to')]
    assert seen == ([(bar.at, 123)] if woken else [])  # its own value: the thumb stays stock's


def test_setting_keys_name_each_option_in_config(fw):
    key_of = fw.fn('option_key', C.c_char_p, C.c_void_p, C.c_int)
    T = fw.fn('theme', C.c_void_p)()
    own = list(host.tools_theme.theme_keys(host.tools_theme.builtins()))
    assert [key_of(T, s) for s in (1, 3, 11)] == [b'HOME', b'THEME', b'TIMETITLE']
    assert [key_of(T, 12 + k).decode() for k in range(len(own))] == own
    assert key_of(T, 0) == b'MINIMAL_ACCENT'  # each theme keeps its own accent


def test_classics_accent_is_its_own_key(fw):
    theme(fw, 1)
    assert fw.fn('option_key', C.c_char_p, C.c_void_p, C.c_int)(fw.fn('theme', C.c_void_p)(), 0) == b'CLASSIC_LIGHT_ACCENT'


@pytest.mark.parametrize('index', [0, 1, 2])
def test_a_window_takes_its_themes_motion_as_it_loads(fw, ui, index):
    theme(fw, index)
    fields = dict(host.tools_theme.motion_fields(host.tools_theme.builtins()[index]))
    def hint(kind):
        """The theme's window motion for kind, as theme.xml gives it; None: the window keeps its own."""
        value = fields[f'motion[MOTION_{kind}].hint']
        return json.loads(value).encode() if value else None
    sliding = ui.widget('window', ui.wm, name='allmusic_page')
    sliding.props[b'anim_hint'] = b'htranslate'  # a page that opens with stock's slide
    still = ui.widget('window', ui.wm, name='allmusic_page')
    playing = ui.widget('window', ui.wm, name='playing_page')
    load = fw.fn('navigation__motion_load', None, C.c_void_p, C.c_int)
    for win, np in ((sliding, 0), (still, 0), (playing, 1)):
        load(win.at, np)
    assert sliding.props[b'anim_hint'] == (hint('PAGE') or b'htranslate')
    assert b'anim_hint' not in still.props  # a page that does not slide is not made to
    assert playing.props.get(b'close_anim_hint') == hint('PLAYING') and b'anim_hint' not in playing.props  # it opens at once


@pytest.mark.parametrize('keys, layout', [({}, 0), ({'NPCENTRED': 1}, 1)])
def test_centred_now_playing_forces_the_centred_layout(fw, keys, layout):
    theme(fw, 0, **keys)
    assert fw.fn('np_layout', C.c_int)() == layout  # NP_IPOD, NP_CENTRED


def test_a_set_with_a_list_end_keeps_the_selected_row_above_it(fw, ui, st):
    names = host.tools_theme.GEOM_VALS
    size = fw.sizes['theme_engine__geom_vals'] // 2
    table = fw.var('theme_engine__geom_vals', C.c_short * size)
    sets = [table[i:i + len(names)] for i in range(0, size, len(names))]
    found = [(i, s[names.index('list_end')]) for i, s in enumerate(sets) if s[names.index('list_end')] > 0]
    assert found, 'no geometry set has a list end'
    index, end = found[0]
    st('geom').value = index
    page = Page(ui, titles(60), height=end + 3 * ROW_H)
    paint(fw, page)
    bottoms = []
    for _ in range(30):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
        bottoms.append(page.screen_y(page.selected) + ROW_H)
    assert max(bottoms) <= end and max(bottoms) > end - 2 * ROW_H  # it runs to the end, not past it


# Painted helpers: the panel, the shadow, the boxes.
def test_classics_panel_sits_on_its_shadow_in_a_ring_and_puts_the_fill_back(fw, ui):
    theme(fw, 1)
    canvas, r = Canvas(fw), Rect(40, 50, 120, 30)
    fw.fn('paint_panel', None, C.c_void_p, C.c_void_p, C.POINTER(Rect), C.c_char_p)(0x10, canvas.at, C.byref(r), b'shadow')
    pad, drop = NAV['PANEL_PAD'], NAV['PANEL_DROP']
    assert canvas.drawn == [(b'shadow', (40 - pad, 50 - pad + drop, 12, 16))]
    assert all(39 <= x and x + w <= 40 + 120 + 1 and 49 <= y <= 50 + 30 for _, x, y, w, _ in canvas.fills)
    assert {y for _, _, y, _, _ in canvas.fills} == set(range(49, 50 + 30 + 1))  # the ring a pixel out
    assert canvas.fill == LCD_FILL


def wide(text):
    return (C.c_uint * (len(text) + 1))(*map(ord, text), 0)


def test_a_text_shadow_is_its_text_in_black_at_its_alpha(fw):
    canvas, r = Canvas(fw), Rect(10, 20, 200, 30)
    struct.pack_into('<I', canvas.lcd, O['LCD_TEXT_COLOR'], 0x0abcdef0)
    struct.pack_into('<2i', canvas.block, O['CANVAS_ALIGN_V'], 2, 0)
    shadow = fw.fn('text_shadow', None, C.c_void_p, C.c_char_p, C.c_void_p, C.POINTER(Rect), C.c_uint, C.c_uint)
    shadow(canvas.at, b'default', wide('Hello'), C.byref(r), 20, 0x4d)
    assert canvas.boxes == [('Hello', (10, 20, 200, 30), 0x4d000000)]
    assert canvas.text_color == 0x0abcdef0 and struct.unpack_from('<2i', canvas.block, O['CANVAS_ALIGN_V']) == (2, 0)
    shadow(canvas.at, b'default', wide(''), C.byref(r), 20, 0x4d)
    assert len(canvas.boxes) == 1


def test_without_vgcanvas_the_letters_box_is_square(fw, ui):
    page, canvas = spun(fw, ui, titles(200))
    canvas.declines = 1
    letter(fw, page, canvas)
    (_, box, _), = canvas.boxes
    (_, rounded, color, _), = canvas.rounds  # asked for first, and declined
    assert rounded == box and (color, *box) in canvas.fills  # the same square, its corners square


@pytest.mark.parametrize('declines, box, rounded', [(0, (10, 10, 80, 40), True), (1, (10, 10, 80, 40), False), (0, (10, 10, 20, 20), False)])
def test_a_frame_is_two_rings_rounded_where_it_can_be(fw, declines, box, rounded):
    canvas = Canvas(fw)
    canvas.declines = declines
    fw.fn('navigation__frame_box', None, C.c_void_p, C.POINTER(Rect), C.c_uint, C.c_uint)(canvas.at, C.byref(Rect(*box)), 0xffffffff, 12)
    x, y, w, h = box
    inner = (x + 1, y + 1, w - 2, h - 2)
    if rounded:
        assert [(k, r, radius) for k, r, _, radius in canvas.rounds] == [('stroke', box, 12), ('stroke', inner, 11)] and not canvas.strokes
    else:
        assert canvas.strokes == [(0xffffffff, *box), (0xffffffff, *inner)]


@pytest.mark.parametrize('value', [0, 7, 10, 42, 100, 999])
def test_put_num_writes_the_digits(fw, value):
    out = (C.c_uint * 4)()
    n = fw.fn('put_num', C.c_uint, C.c_void_p, C.c_uint)(out, value)
    assert ''.join(map(chr, out[:n])) == str(value)


def test_a_clip_that_misses_the_list_paints_no_chevrons(fw, ui):
    page, canvas = Page(ui, titles(6), name='playlist_page'), Canvas(fw, clip=(0, 300, 375, 20))
    assert chevrons(fw, page, canvas) == []


def test_a_clip_that_misses_the_album_paints_no_capitals(fw, ui, st):
    canvas, album = Canvas(fw, clip=(0, 0, 375, 100)), Album(fw, ui, st, 'abc')
    assert caps(fw, canvas, album.view) == []


# Classic's selected-only chevron off the drill lists.
def into_mark(ui, row):
    ui.widget('image', row, (302, 0, 50, ROW_H), visible=False)


@pytest.mark.parametrize('row, drawn', [(2, True), (1, False), (9, False)])
def test_classic_marks_a_settings_row_that_opens_a_page(fw, ui, st, row, drawn):
    theme(fw, 1)
    page, canvas = Page(ui, titles(6), name='sysset_page'), Canvas(fw)
    into_mark(ui, page.rows[2])
    st('sel_w', C.c_void_p).value = page.view.at
    st('sel_row').value = row
    assert [y for _, _, y in chevrons(fw, page, canvas)] == ([ROW_H * 2 + ROW_H // 2] if drawn else [])


def test_classic_marks_a_stock_button_whose_list_item_opens_a_page(fw, ui, st):
    theme(fw, 1)
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='sysset_page')
    view = ui.widget('scroll_view', win, (0, 0, 375, VIEW_H))
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], 3 * ROW_H)
    for i in range(3):
        item = ui.widget('list_item', view, (0, ROW_H * i, 375, ROW_H))
        ui.widget('button', item, (0, 0, 375, ROW_H), click=True)
        if i == 1:
            into_mark(ui, item)
    ui.top = win
    st('sel_w', C.c_void_p).value = view.at
    st('sel_row').value = 1
    canvas = Canvas(fw)
    fw.fn('navigation__paint_chevrons', None, C.c_void_p, C.c_void_p)(view.at, canvas.at)
    assert [y for _, _, y in canvas.icons] == [ROW_H + ROW_H // 2]


@pytest.mark.parametrize('row, drawn', [(1, True), (0, False)])
def test_classic_marks_a_folder_but_not_a_song(fw, ui, st, row, drawn):
    theme(fw, 1)
    deques, keep = Deques(fw), []
    for kind in (8, 4, 8):
        keep.append(C.create_string_buffer(0x60))
        struct.pack_into('<i', keep[-1], O['REC_TYPE'], kind)
    fw.var('fw_ctx_p_deque_showlist', C.c_ulong).value = deques.new([C.addressof(b) for b in keep])
    page, canvas = Page(ui, titles(3), name='folder_page'), Canvas(fw)
    st('sel_w', C.c_void_p).value = page.view.at
    st('sel_row').value = row
    assert [y for _, _, y in chevrons(fw, page, canvas)] == ([ROW_H * row + ROW_H // 2] if drawn else [])


# Minimal's Home labels.
def home_rows(fw, ui, n=3):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='home_page')
    listing = ui.widget('scroll_view', win, (0, 0, 188, VIEW_H))
    rows = [ui.widget('list_item', listing, (0, ROW_H * i, 188, ROW_H)) for i in range(n)]
    labels = [ui.widget('hscroll_label', r, (20, 0, 150, ROW_H), text=f'row {i}') for i, r in enumerate(rows)]
    fw.var('coverflow__home', Home).list = listing.at  # coverflow.c's Home list
    ui.top = win
    return rows, labels


def test_minimals_home_labels_are_muted_capitals_brighter_when_selected(fw, ui, st):
    rows, labels = home_rows(fw, ui)
    st('sel_widget', C.c_void_p).value = rows[1].at
    seen = []
    for label in labels:
        canvas = Canvas(fw)
        seen.append(caps(fw, canvas, label))
    assert [''.join(c for c, _, _, _ in s) for s in seen] == ['ROW 0', 'ROW 1', 'ROW 2']
    inks = [{color for *_, color in s} for s in seen]
    assert all(len(ink) == 1 for ink in inks)
    (other, selected, same) = (ink.pop() for ink in inks)
    assert other == same != selected  # the selected row stands out; the others share one ink
    assert sum(selected >> s & 255 for s in (0, 8, 16)) > sum(other >> s & 255 for s in (0, 8, 16))  # muted: dimmer
    assert seen[0][0][1:3] == (0, (ROW_H - O['HOME_CAPS_PX']) // 2)


def test_a_label_off_homes_list_keeps_its_own_ink(fw, ui, st):
    rows, labels = home_rows(fw, ui)
    stray = ui.widget('hscroll_label', ui.widget('view'), (0, 0, 100, 30), text='elsewhere')
    assert caps(fw, Canvas(fw), stray) == [] and caps(fw, Canvas(fw), labels[0])  # the list's own get capitals


@pytest.mark.parametrize('index, drawn', [(0, True), (2, False)])
def test_a_theme_without_home_capitals_leaves_homes_labels(fw, ui, st, index, drawn):
    theme(fw, index)
    rows, labels = home_rows(fw, ui)
    assert bool(caps(fw, Canvas(fw), labels[0])) == drawn


def test_a_selected_row_without_a_scrolling_title_starts_nothing(fw, ui, st):
    page = Page(ui, titles(10))
    st('sel_w', C.c_void_p).value = page.view.at
    st('sel_row').value = st('marq_id').value = 3
    st('marq_row', C.c_void_p).value = page.rows[3].at
    fw.fn('navigation__marq_moved', C.c_int, C.c_void_p)(None)
    assert not fw.calls('hscroll_label_start')


# Centre's single and double press on a page that tells them apart.
SINGLE = C.CFUNCTYPE(C.c_int, C.c_void_p)


class Press:
    """center_press on a page's own timer and press time; the single press's runs, by time."""
    def __init__(self, fw, ui):
        self.ui, self.timer, self.at, self.singles = ui, C.c_uint(0), C.c_uint(0), []
        self.single = SINGLE(lambda info: self.singles.append(ui.now) or 0)
        self.press = fw.fn('center_press', C.c_int, C.POINTER(C.c_uint), C.POINTER(C.c_uint), SINGLE, C.c_int)

    def __call__(self, at, scrubbing=0):
        self.ui.now = at
        return self.press(C.byref(self.timer), C.byref(self.at), self.single, scrubbing)


DOUBLE = O['DOUBLE_CLICK_MS']


def test_a_first_press_waits_for_a_second(fw, ui):
    press = Press(fw, ui)
    assert press(5000) == 1 and press.at.value == 5000
    fn, _, ms = ui.timers[press.timer.value]
    assert ms == DOUBLE and fn == C.cast(press.single, C.c_void_p).value and not press.singles


def test_a_second_press_in_time_is_the_double(fw, ui):
    press = Press(fw, ui)
    press(5000)
    timer = press.timer.value
    assert press(5000 + DOUBLE - 1) == 2 and press.timer.value == 0 and timer not in ui.timers and not press.singles


def test_a_press_after_an_overdue_first_runs_the_single_and_is_stocks(fw, ui):
    press = Press(fw, ui)
    press(5000)
    assert press(5000 + DOUBLE) == 0 and press.singles == [5000 + DOUBLE] and press.timer.value == 0


def test_a_scrubs_presses_are_doubles_and_the_next_waits_for_nothing(fw, ui):
    press = Press(fw, ui)
    assert press(5000, scrubbing=1) == 2 and press.at.value == 5000 and press.timer.value == 0
    assert press(5000 + DOUBLE - 1) == 1 and press.timer.value == 0  # no single: the screen stays on
    assert press(5000 + 2 * DOUBLE) == 1 and press.timer.value  # later: a first press again


# Photos' viewer steps like Home's carousel.
def test_photos_viewer_glides_a_photo_a_tick(fw, ui):
    viewer = Carousel(fw, ui, name='photos_page')
    key(fw, ui, KEY_NEXT)
    assert viewer.started == [(-viewer.stride, viewer.duration)]


def test_a_glide_that_cannot_hear_its_end_lands_at_once(fw, ui):
    home = Carousel(fw, ui, binds=False)
    key(fw, ui, KEY_NEXT)
    assert len(home.destroyed) == 1 and not home.started and home.menu.ptr(O['SLIDE_ANIMATOR']) == 0
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (home.menu.at, 3)
