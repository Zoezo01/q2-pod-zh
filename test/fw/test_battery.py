"""battery.c: Battery History's record on the flash (a fall appends, an unplug starts again, its bound, a reload,
a damaged file), the log it exports to the card and the rows of its page."""
import calendar
import ctypes as C
import struct
import time

import pytest

from conftest import O, Files

RECORD, LOG = '/mnt/data/q2-battery', '/mnt/mmc/battery-log.txt'
MAGIC, ROWS = 0x31484251, 100
PAGE, EVT_CLICK = 0x7000, O['EVT_CLICK']
SEL, COUNT = b'_ringnav_index', b'_ringnav_count'
NOON = calendar.timegm((2026, 11, 11, 12, 0, 0))  # the clock in UTC, as localtime here reads it
MOUNTS = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'


class Q2:
    """The flash and card's files, the wall clock (UTC as the local time), the cable and the level."""
    def __init__(self, fw, files=None):
        self.fw, self.files, self.now = fw, Files(fw, files), NOON
        fw.mock('fw_time', lambda *_: self.now)
        fw.mock('fw_localtime_r', self.local)
        self.tick = fw.fn('battery_tick', None, C.c_int, C.c_int)

    def local(self, at, out, *_):
        tm = time.gmtime(C.c_long.from_address(at).value)
        for k, v in enumerate([tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday, tm.tm_mon - 1, tm.tm_year - 1900]):
            C.c_int.from_address(out + 4 * k).value = v
        return out

    def poll(self, level, cable=0, minutes=0):
        """charge_poll's pass, minutes after the last."""
        self.now += 60 * minutes
        self.tick(level, cable)

    def rows(self):
        """The record on the flash: [(time, level)]."""
        data = self.files.files[RECORD]
        magic, n = struct.unpack_from('<II', data)
        assert magic == MAGIC and len(data) == 8 + 8 * ROWS
        return [struct.unpack_from('<II', data, 8 + 8 * k) for k in range(n)]

    def reboot(self):
        """A power-off and on: the payload's memory zeroed, the flash kept."""
        bh = C.c_char.in_dll(self.fw.lib, 'battery__bh')
        C.memset(C.addressof(bh), 0, self.fw.sizes['battery__bh'])

    def log(self):
        out = C.create_string_buffer(8192)
        n = self.fw.fn('battery_log', C.c_uint, C.c_char_p, C.c_uint)(out, len(out))
        return out.raw[:n].decode()


@pytest.fixture
def q2(fw):
    return Q2(fw)


def test_each_fall_of_a_percent_is_a_row_and_one_write(fw, q2):
    q2.poll(90)
    q2.poll(90, minutes=5)
    q2.poll(89, minutes=5)
    q2.poll(89, minutes=5)
    q2.poll(87, minutes=5)
    assert q2.rows() == [(NOON, 90), (NOON + 600, 89), (NOON + 1200, 87)]  # a fall of two is one row
    assert len([a for _, a in fw.calls('fw_rename') if C.string_at(a[1]) == RECORD.encode()]) == 3


def test_a_level_back_up_a_little_is_no_row_and_no_write(fw, q2):
    q2.poll(80)
    q2.poll(79, minutes=1)
    writes = len(fw.calls('fw_rename'))
    q2.poll(81, minutes=1)  # the gauge's rebound
    q2.poll(79, minutes=1)
    assert q2.rows() == [(NOON, 80), (NOON + 60, 79)] and len(fw.calls('fw_rename')) == writes
    q2.poll(78, minutes=1)
    assert q2.rows()[-1] == (NOON + 240, 78)


def test_charging_keeps_the_history_and_the_unplug_starts_a_new_one(q2):
    q2.poll(50)
    q2.poll(40, minutes=60)
    q2.poll(60, cable=1, minutes=30)
    q2.poll(95, cable=1, minutes=60)
    assert q2.rows() == [(NOON, 50), (NOON + 3600, 40)]  # kept while on the charger
    q2.poll(95, minutes=1)
    assert q2.rows() == [(NOON + 9060, 95)]


def test_a_boot_on_the_charger_starts_at_the_unplug(q2):
    q2.poll(70, cable=1)
    assert RECORD not in q2.files.files
    q2.poll(71, minutes=10)
    assert q2.rows() == [(NOON + 600, 71)]


def test_a_full_discharge_stays_inside_its_bound(q2):
    for level in range(100, 0, -1):
        q2.poll(level, minutes=6)
        q2.poll(level + 2, minutes=1)  # rebounds never add a row
    assert [p for _, p in q2.rows()] == list(range(100, 0, -1))
    q2.poll(0, minutes=6)  # not a level: demo powers off first
    assert len(q2.rows()) == ROWS


def test_the_history_survives_a_power_off(q2):
    q2.poll(60)
    q2.poll(59, minutes=10)
    q2.reboot()
    q2.poll(60, minutes=30)  # rested, the level reads a little higher
    q2.poll(58, minutes=5)
    assert q2.rows() == [(NOON, 60), (NOON + 600, 59), (NOON + 2700, 58)]


def test_a_charge_while_powered_off_starts_again(q2):
    q2.poll(30)
    q2.reboot()
    q2.poll(80, minutes=120)
    assert q2.rows() == [(NOON + 7200, 80)]


@pytest.mark.parametrize('damage', [
    lambda d: d[:-1],  # short
    lambda d: struct.pack('<I', 0) + d[4:],  # another layout
    lambda d: d[:4] + struct.pack('<I', ROWS + 1) + d[8:],  # more rows than it holds
    lambda d: d[:8 + 8 * 1 + 4] + struct.pack('<I', 61) + d[8 + 8 * 2:],  # a level that rose
    lambda d: d[:12] + struct.pack('<I', 101) + d[16:],  # over 100%
])
def test_a_damaged_record_is_ignored_and_the_next_poll_starts_again(fw, damage):
    first = Q2(fw)
    first.poll(60)
    first.poll(59, minutes=10)
    q2 = Q2(fw, {RECORD: damage(first.files.files[RECORD])})
    first.reboot()
    q2.poll(58, minutes=20)
    assert q2.rows() == [(NOON + 1200, 58)]


def test_the_log_dates_each_day_over_its_rows(fw, q2):
    q2.poll(99)
    q2.poll(98, minutes=75)
    q2.poll(97, minutes=12 * 60)  # 01:15 the next day
    fw.var('g_time24h_flag', C.c_ubyte).value = 1
    assert q2.log() == '11 Nov 2026\n99% | 12:00\n98% | 13:15\n\n12 Nov 2026\n97% | 01:15\n'
    fw.var('g_time24h_flag', C.c_ubyte).value = 0
    assert q2.log().split('\n')[1:3] == ['99% | 12:00 PM', '98% | 1:15 PM']


def test_a_clock_never_set_logs_the_time_since_the_start(q2):
    q2.now = 3600
    q2.poll(99)
    q2.poll(98, minutes=75)
    assert q2.log() == 'Clock not set: times since the start\n99% | +0:00\n98% | +1:15\n'


def xs(fw, q2):
    """bh_x for each row and for now, over a plot from 0 to 300."""
    x = fw.fn('battery__bh_x', C.c_int, C.c_uint, C.c_int, C.c_int, C.c_uint)
    n = len(q2.rows())
    return [x(i, 0, 300, q2.now) for i in range(n + 1)]


def test_the_graph_spreads_rows_kept_while_the_clock_was_unset_once_it_is_set(fw, q2):
    q2.now = 3600
    for k, level in enumerate((90, 80, 70)):
        q2.poll(level, minutes=60 * bool(k))
    q2.now = NOON  # the clock set since: now is no measure of these rows
    assert xs(fw, q2) == [0, 150, 300, 300]


def test_a_clock_set_mid_history_spaces_the_rows_evenly(fw, q2):
    q2.now = 3600
    q2.poll(90)
    q2.poll(80, minutes=60)
    q2.now = NOON
    q2.poll(70)
    q2.poll(60, minutes=60)
    assert xs(fw, q2) == [0, 75, 150, 225, 300]


def test_the_graph_runs_by_time_up_to_now(fw, q2):
    q2.poll(90)
    q2.poll(80, minutes=60)
    q2.now += 3600 * 2
    assert xs(fw, q2) == [0, 100, 300]


class Page:
    """Power's Battery History page on stubs: its rows' labels, a row's tap and the toasts."""
    def __init__(self, fw):
        self.fw, self.texts, self.children, self.items, self.toasts, self.boxes = fw, {}, {}, [], [], {}
        self.buffers, self.lists = [], []
        fw.mock('window_create', lambda *_: PAGE)
        fw.mock('list_view_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h))
        fw.mock('scroll_view_create', self.scroll)
        fw.mock('list_item_create', lambda *_: self.items.append(self.widget()) or self.items[-1])
        fw.mock('hscroll_label_create', self.label)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        self.props = {}
        fw.mock('widget_get_prop_int', self.prop)
        fw.mock('widget_set_prop_int', lambda w, key, v, *_: self.props.__setitem__((w, C.string_at(key)), C.c_int(v).value))
        fw.mock('widget_move_resize', lambda w, x, y, width, h, *_: self.boxes.__setitem__(w, (x, y, width, h)))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, *_: self.widget(x, y))  # h: a seventh argument
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('navigator_window_is_exist', lambda *_: 0)
        fw.mock('fw_statfs', self.statfs)
        self.free = 64 << 20

    def widget(self, x=0, y=0, w=0, h=0):
        self.buffers.append(C.create_string_buffer(0x400))
        p = C.addressof(self.buffers[-1])
        struct.pack_into('<iiii', self.buffers[-1], 0, x, y, w, h)
        self.boxes[p] = (x, y, w, h)
        return p

    def scroll(self, parent, x, y, w, h, *_):
        self.items.clear()
        p = self.widget(x, y, w, h)
        self.lists.append(p)
        C.c_void_p.from_address(p + O['W_PARENT']).value = parent
        return p

    def label(self, parent, *_):
        p = self.widget()
        self.children.setdefault(parent, []).append(p)
        return p

    def prop(self, w, key, default, *_):
        name = C.string_at(key)
        return 290 if name == b'h' else self.props.get((w, name), C.c_int(default).value)

    @property
    def view(self):
        """The list's scroll view, the latest built."""
        return self.lists[-1]

    def statfs(self, path, out, *_):
        C.c_uint.from_address(out + 4).value, C.c_uint.from_address(out + 28).value = 1, self.free
        return 0

    def open(self):
        assert self.fw.fn('battery_history', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0

    @property
    def captions(self):
        return [self.texts.get(self.children[item][0]) for item in self.items]

    def tap(self, row):
        (fn, ctx), = [(a[2], a[3]) for _, a in self.fw.calls('widget_on') if a[0] == self.items[row] and a[1] == EVT_CLICK]
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)


def test_the_page_lists_export_then_each_row_a_day_s_first_dated(fw, q2):
    fw.var('g_time24h_flag', C.c_ubyte).value = 1
    q2.poll(99)
    q2.poll(98, minutes=12 * 60 + 30)
    page = Page(fw)
    page.open()
    assert page.captions == ['Export Log', '99% | 12:00, 11 Nov', '98% | 00:30, 12 Nov']
    graph = [a for _, a in fw.calls('widget_factory_create_widget')]
    assert len(graph) == 1 and graph[0][2] == PAGE  # over the list, which starts below it
    gy = graph[0][4]
    (x, y, w, h), = [page.boxes[a[0]] for _, a in fw.calls('widget_move_resize') if a[0] in page.boxes]  # the list
    assert y > gy and y + h <= 290 and h >= 48  # under the graph, to the page's foot at most


def test_no_history_says_to_unplug(fw, q2):
    q2.poll(70, cable=1)
    page = Page(fw)
    page.open()
    assert page.items == [] and 'Unplug to start' in page.texts.values()
    assert not fw.calls('widget_factory_create_widget')  # no graph


@pytest.mark.parametrize('usb, mounted, free, said', [
    (0, 1, 64 << 20, 'Saved to battery-log.txt'),
    (1, 1, 64 << 20, 'The card is in use over USB'),
    (0, 0, 64 << 20, 'Storage unavailable'),
    (0, 1, 1000, 'The card is full'),
])
def test_export_writes_the_log_whole_or_says_why_not(fw, q2, usb, mounted, free, said):
    q2.poll(99)
    q2.poll(98, minutes=60)
    page = Page(fw)
    page.free = free
    fw.mock('navigator_window_is_exist', lambda name, *_: usb and C.string_at(name) == b'usbmode_page')
    if mounted:
        q2.files.files['/proc/mounts'] = MOUNTS
    page.open()
    page.tap(0)
    assert page.toasts == [said]
    assert q2.files.files.get(LOG) == (q2.log().encode() if usb == 0 and mounted and free > 1000 else None)
    assert LOG + '.tmp' not in q2.files.files


def test_an_export_the_card_refuses_leaves_no_temporary_file(fw, q2):
    q2.poll(99)
    page = Page(fw)
    q2.files.files['/proc/mounts'] = MOUNTS
    q2.files.fail_rename = True
    page.open()
    page.tap(0)
    assert page.toasts == ["Couldn't save"] and LOG not in q2.files.files
    assert [C.string_at(a[0]).decode() for _, a in fw.calls('fw_unlink')] == [LOG + '.tmp']


def test_every_row_takes_the_wheel(fw, q2):
    """ringnav's wheel and Centre go only to rows with a click handler: each history row needs one too."""
    q2.poll(99)
    q2.poll(98, minutes=10)
    page = Page(fw)
    page.open()
    clicks = {a[0]: a[2] for _, a in fw.calls('widget_on') if a[1] == EVT_CLICK}
    assert len(page.items) == 3 and all(clicks.get(item) for item in page.items)
    assert page.tap(2) == 0 and page.toasts == []  # a history row's Centre does nothing


def lit(fw, on):
    fw.var('g_backlight_status', C.c_ubyte).value = on


def test_a_new_row_shows_on_the_open_page_where_the_wheel_left_it(fw, q2):
    lit(fw, 1)
    q2.poll(99)
    q2.poll(98, minutes=10)
    page = Page(fw)
    page.open()
    view = page.view
    page.props[view, SEL], page.props[view, b'yoffset'] = 2, 48
    builds = len(page.lists)
    q2.poll(98, minutes=5)  # nothing new: the page stays
    assert len(page.lists) == builds
    q2.poll(97, minutes=5)
    assert page.captions[1:] == ['99% | 12:00 PM, 11 Nov', '98% | 12:10 PM', '97% | 12:20 PM'] and page.view != view
    assert page.props[page.view, SEL] == 2 and page.props[page.view, COUNT] == 4
    assert [a[:3] for _, a in fw.calls('scroll_view_set_offset')][-1] == (page.view, 0, 48)
    assert len([a for _, a in fw.calls('widget_factory_create_widget')]) == 2  # the graph drawn again


def test_a_row_while_dark_shows_at_the_first_lit_poll(fw, q2):
    lit(fw, 1)
    q2.poll(99)
    page = Page(fw)
    page.open()
    lit(fw, 0)
    builds = len(page.lists)
    q2.poll(98, minutes=10)
    q2.poll(98, minutes=1)
    assert len(page.lists) == builds  # nothing drawn while the screen is off
    lit(fw, 1)
    q2.poll(98, minutes=1)
    assert len(page.lists) == builds + 1 and page.captions[-1] == '98% | 12:10 PM'
    q2.poll(98, minutes=1)
    assert len(page.lists) == builds + 1  # once


def test_a_closed_page_is_not_drawn(fw, q2):
    lit(fw, 1)
    q2.poll(99)
    page = Page(fw)
    page.open()
    (fn, ctx), = [(a[2], a[3]) for _, a in fw.calls('widget_on') if a[0] == PAGE and a[1] == O['EVT_DESTROY']]
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
    builds = len(page.lists)
    q2.poll(98, minutes=10)
    assert len(page.lists) == builds
