"""smart_wifi.c (docs/internals.md#smart-wi-fi): the radio off once nothing has needed the network for SMART_IDLE_MS
and back for what does, through stock's own switch with its WIFI= write dropped; the wait for its link, and the
presses held for it."""
import ctypes as C

import pytest

from conftest import O
from test_addon import Ap  # each file keeps its own state's mirror
from test_nav_library_pages import Up
from test_nav_painters import Ui, scratch, st, strings  # noqa: F401  the window stack; navigation.c's st by field
from test_netupdate import JOB_INSTALL, NU_RUNNING, Nu, Sh
from test_onlinematch import Al
from test_radio import Rd
from test_streaming import Sp, Td

IDLE, POLL, CONNECT = O['SMART_IDLE_MS'], O['SMART_POLL_MS'], O['SMART_CONNECT_MS']
TICK, RET_REPEAT = 500, 8  # the held press's timer; one that answers RET_REPEAT runs again, any other is removed
WAIT, NO_LINK = 'Connecting to Wi-Fi…', 'Connect to Wi-Fi first'
ACTION, TIMER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p), C.CFUNCTYPE(C.c_int, C.c_void_p)
SAVED_ON, SAVED_OFF = (b'SYSSET', b'WIFI', 1), (b'SYSSET', b'WIFI', 0)


class Q2:
    """Stock's side of Smart Wi-Fi: the clock; wifi_switch, a toggle of g_wififlag that drops the link when it turns
    the radio off and saves through the payload's write_int_config hook, its tail call on the Q2 (saved: the writes
    stock got); the link as the status bar's bars and the monitor thread's status, each its own; the window stack,
    toasts and AWTK's timers. It starts with Power's row on, the user's Wi-Fi on, the module up and Home on top."""
    def __init__(self, fw, st):
        self.fw, self.now, self.bars, self.status, self.sliding, self.queue, self.airplay = fw, 1 << 20, 0, 0, 0, 0, 0
        self.switched: list = []
        self.saved: list = []
        self.toasts: list = []
        self.timers: list = []
        self.ui = Ui(fw)
        strings(fw)
        self.home = self.ui.window('home_page')
        self.row, self.wifi = st('smart_wifi'), fw.var('g_wififlag', C.c_ubyte)
        self.row.value = self.wifi.value = 1
        self.set('NET_TYPE', 2)
        self.poll, self.wake = fw.fn('smart_poll', None), fw.fn('smart_wake', None)
        self.wait, self.off = fw.fn('smart_wifi_wait', C.c_int), fw.fn('smart_wifi_off', C.c_int)
        self.hold = fw.fn('smart_hold', C.c_int, ACTION, C.c_void_p, C.c_char_p)
        self.need = fw.fn('need_link', C.c_int, ACTION, C.c_void_p)
        self.write = fw.fn('ringnav_write_config', C.c_int, C.c_int, C.c_char_p, C.c_char_p)
        m = fw.mock
        m('time_now_ms', lambda *_: self.now & 0xffffffff)
        m('wifi_switch', self.switch)
        m('stock_write_config_trampoline', lambda v, section, key, *_: self.saved.append((C.string_at(section), C.string_at(key), v)))
        m('get_wifisignal', lambda *_: 3 if self.bars else -1)
        m('networkGetConnectStatus', lambda *_: self.status)
        m('mclGetPlayType', lambda *_: self.queue)
        m('airplayGetFlag', lambda *_: self.airplay)
        m('window_manager_is_animating', lambda *_: self.sliding)
        m('navigator_to_with_context', lambda name, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        m('timer_add', self.timer)

    def set(self, name, value):
        """A stock global: a byte by its symbol, or NET_TYPE's word at its address."""
        (C.c_int.from_address(O[name]) if name in O else self.fw.var(name, C.c_ubyte)).value = value

    def switch(self, toggle=1, *_):
        """Stock's wifi_switch(1); the user's own tap when a test calls it."""
        assert toggle == 1
        self.wifi.value ^= 1
        if not self.wifi.value:
            self.bars = self.status = 0
        self.switched.append('on' if self.wifi.value else 'off')
        self.write(self.wifi.value, b'SYSSET', b'WIFI')

    def timer(self, fn, ctx, ms, *_):
        assert ms == TICK
        self.timers.append(fn)
        return len(self.timers)

    def up(self):
        self.bars = self.status = 1

    def pass_(self, ms):
        """ms of the UI loop: power_poll's smart_poll, each SMART_POLL_MS."""
        assert ms % POLL == 0
        for _ in range(ms // POLL):
            self.now += POLL
            self.poll()

    def idle(self):
        """Nothing has needed the network for SMART_IDLE_MS: Smart Wi-Fi has the radio off."""
        self.pass_(POLL + IDLE)
        assert self.switched == ['off'] and self.off()

    def tick(self, n=1):
        """The timers' next n turns, as AWTK runs them."""
        for _ in range(n):
            self.now += TICK
            due, self.timers = self.timers, []
            self.timers += [t for t in due if TIMER(t)(None) == RET_REPEAT]

    def action(self):
        """An action for smart_hold: (its C callback, the (ctx, event) of each run)."""
        runs: list = []
        fn = ACTION(lambda ctx, event: runs.append((ctx, event)) or 0)
        self.fw.keep.append(fn)
        return fn, runs


class Press:
    """A row's click handler as the payload's are (Upload Scrobbles, Network Update): it asks need_link first,
    with itself, and goes on at 1. runs: the (ctx, event) of each call; went: the ctx of those that went on."""
    def __init__(self, q2):
        self.q2, self.runs, self.went = q2, [], []
        self.fn = ACTION(self.click)

    def click(self, ctx, event):
        self.runs.append((ctx, event))
        if self.q2.need(self.fn, ctx):
            self.went.append(ctx)
        return 0


@pytest.fixture
def q2(fw, st):
    net = C.c_int.from_address(O['NET_TYPE'])  # stock's address, in conftest's map, which outlives the test
    saved = net.value
    yield Q2(fw, st)
    net.value = saved


def flag(name, value):
    return lambda q: q.set(name, value)


def has(**values):
    return lambda q: q.__dict__.update(values)


def pages(*names):
    return lambda q: [q.ui.window(n) for n in names]


def state(symbol, mirror, **fields):
    """Another file's work under way: its state's fields."""
    def use(q):
        s = q.fw.var(symbol, mirror)
        for field, value in fields.items():
            setattr(s, field, value)
    return use


def card_app(q):
    ap = q.fw.var('addon__ap', Ap)
    ap.n, ap.a[0].launched = 1, 1


# What needs the network, and what is like it but doesn't.
NEEDS = [('a USB cable in', flag('g_usbdet_value', 1), True),
         ('AirPlay on', flag('g_airplayflag', 1), True),
         ('DLNA on', flag('g_dlnaflag', 1), True),
         ('SyncLink over Wi-Fi', flag('g_synclinkflag', 2), True),
         ('SyncLink over Bluetooth', flag('g_synclinkflag', 1), False),
         ('a SyncLink session', flag('g_syncLink_status', 1), True),
         ('Wi-Fi transfer', flag('g_wifitransport_flag', 1), True),
         ('AirPlay playing', has(airplay=2), True),
         *((f'a stream queue of type {t:#x}', has(queue=t), True) for t in (2, 3, 0xf1, 0xf2)),
         *((f'a local queue of type {t:#x}', has(queue=t), False) for t in (1, 0xf001)),  # a folder's, the library's
         ('Spotify running', state('spotify__sp', Sp, launched=1), True),
         ('a station playing', state('radio__rd', Rd, active=1, pid=77), True),
         ('a station stopped', state('radio__rd', Rd, active=1), False),
         ('a card app running', card_app, True),
         ('a scrobble upload', state('scrobble__up', Up, running=1), True),
         ('Network Update downloading', state('netupdate__nu', Nu, state=NU_RUNNING), True),
         ('an Apps job', state('netupdate__sh', Sh, job=JOB_INSTALL), True),
         ('Download Album Lyrics', state('onlinematch__al', Al, running=1), True),
         ('a Tidal download', state('tidal__td', Td, busy=1), True),
         ('a time sync pending on a link', lambda q: (q.set('g_syncnettime_flag', 1), q.up()), True),
         ('a time sync pending with no link', flag('g_syncnettime_flag', 1), False),
         ('a link with nothing to do', Q2.up, False),
         *((f'{name} open', pages(name), True) for name in (
             'stream_page', 'spotify_page', 'spotbrowse_page', 'radio_page', 'radionp_page', 'addon_page', 'qrcode_page',
             'apps_page', 'airplay_page', 'netservice_page', 'synclink_page', 'wifi_page', 'wifitransport_page',
             'editwifi_dialog', 'kbwifiadd_dialog', 'kbwifipass_dialog', 'onlinematching_page', 'tidal_main_page')),
         ('Streaming under Now Playing', pages('stream_page', 'playing_page'), True),
         *((f'{name} open', pages(name), False) for name in ('tidal_downloads', 'streamconf_page', 'playermore_page', 'sysset_page'))]


@pytest.mark.parametrize('link', [0, 1])
def test_the_radio_goes_off_after_five_idle_minutes_associated_or_not_and_unsaved(q2, link):
    assert IDLE == 5 * 60 * 1000  # the changelog's "after 5 minutes"
    q2.bars = q2.status = link
    q2.pass_(POLL)  # the first poll with the row on: the count starts
    q2.pass_(IDLE - POLL)
    assert q2.switched == [] and not q2.off()
    q2.pass_(POLL)
    assert q2.switched == ['off'] and q2.off() and q2.wifi.value == 0
    assert q2.saved == []  # config.ini keeps the user's On: a reboot starts with Wi-Fi
    q2.pass_(2 * IDLE)
    assert q2.switched == ['off']  # once, not a toggle each poll


def test_with_the_row_off_the_radio_is_never_switched(q2):
    q2.row.value = 0
    q2.pass_(2 * IDLE)
    assert q2.switched == [] and q2.wifi.value == 1


@pytest.mark.parametrize('needed', [0, 1])
def test_the_users_wifi_off_stays_off_whatever_needs_the_network(q2, needed):
    q2.switch()
    assert q2.saved == [SAVED_OFF]
    if needed:
        q2.ui.window('stream_page')
        q2.set('g_usbdet_value', 1)
    q2.pass_(2 * IDLE)
    q2.wake()
    assert q2.wait() == 0 and q2.switched == ['off'] and q2.wifi.value == 0 and not q2.off()


@pytest.mark.parametrize('use,keeps', [n[1:] for n in NEEDS], ids=[n[0] for n in NEEDS])
def test_what_needs_the_network_keeps_the_radio_on_and_nothing_else_does(q2, use, keeps):
    use(q2)
    q2.pass_(POLL + 2 * IDLE)
    assert q2.switched == ([] if keeps else ['off'])


@pytest.mark.parametrize('use,wakes', [(pages('addon_page'), True), (flag('g_usbdet_value', 1), True),
                                       (pages('tidal_downloads'), False)], ids=['a card app\'s page', 'a cable', 'Tidal Downloads'])
def test_a_use_brings_the_radio_back_at_the_next_poll_unsaved(q2, use, wakes):
    q2.idle()
    use(q2)
    q2.pass_(POLL)
    assert q2.switched == (['off', 'on'] if wakes else ['off']) and q2.off() == (not wakes) and q2.saved == []


def test_the_five_minutes_count_from_the_last_use(q2):
    q2.set('g_usbdet_value', 1)
    q2.pass_(POLL + IDLE)
    q2.set('g_usbdet_value', 0)  # out after the last poll saw it in
    q2.pass_(IDLE - POLL)
    assert q2.switched == []
    q2.pass_(POLL)
    assert q2.switched == ['off']


def test_a_new_link_counts_as_a_use_so_time_sync_gets_its_five_minutes(q2):
    q2.pass_(IDLE)  # scanning: a poll short of going off
    q2.up()
    q2.pass_(IDLE)
    assert q2.switched == []
    q2.pass_(POLL)
    assert q2.switched == ['off']


def test_a_radio_woken_for_a_page_left_at_once_gets_its_five_minutes(q2):
    q2.idle()
    q2.wake()  # the Wi-Fi page or Streaming's, opened and left before the next poll
    q2.pass_(IDLE - POLL)
    assert q2.switched == ['off', 'on']
    q2.pass_(POLL)
    assert q2.switched == ['off', 'on', 'off']


def test_the_users_own_tap_is_saved_and_the_radio_is_the_users_again(q2):
    q2.idle()
    q2.pass_(IDLE)
    q2.switch()  # Quick Settings shows Off, as the radio is: a tap turns it on
    assert q2.saved == [SAVED_ON] and not q2.off()
    q2.wake()  # Streaming's page: nothing of Smart Wi-Fi's to bring back, and a toggle would turn it off
    assert q2.wait() == 0 and q2.switched == ['off', 'on']
    q2.pass_(IDLE - POLL)  # five minutes from the tap
    assert q2.switched == ['off', 'on']
    q2.pass_(POLL)
    assert q2.switched == ['off', 'on', 'off'] and q2.saved == [SAVED_ON]


def test_only_the_wifi_write_of_smart_wifis_own_toggle_is_dropped(q2):
    q2.idle()
    q2.write(3, b'SYSSET', b'BACKLIGHT')
    q2.write(1, b'Q2POD', b'WIFI')
    assert q2.saved == [(b'SYSSET', b'BACKLIGHT', 3), (b'Q2POD', b'WIFI', 1)]
    assert q2.off()  # and neither is the user's tap: the radio is still Smart Wi-Fi's to bring back


def test_the_row_turned_off_gives_the_radio_back_unsaved_and_leaves_it(q2):
    q2.idle()
    q2.row.value = 0
    q2.pass_(POLL)
    assert q2.switched == ['off', 'on'] and q2.saved == [] and not q2.off()
    q2.pass_(2 * IDLE)
    assert q2.switched == ['off', 'on']


@pytest.mark.parametrize('name,refused,ready', [('NET_TYPE', 1, 2), ('wifi_initstatus', 2, 0), ('wifi_connectstatus', 1, 2),
                                                ('g_testmode_flag', 1, 0), ('g_poweroff_state', 2, 0), ('g_guideflag', 1, 0)])
def test_the_switch_waits_both_ways_while_stock_would_refuse_its_own(q2, name, refused, ready):
    q2.set(name, refused)
    q2.pass_(POLL + 2 * IDLE)
    assert q2.switched == []
    q2.set(name, ready)
    q2.pass_(POLL)
    assert q2.switched == ['off']
    q2.set(name, refused)
    q2.ui.window('stream_page')
    q2.pass_(IDLE)
    q2.wake()
    assert q2.switched == ['off'] and q2.off()
    q2.set(name, ready)
    q2.pass_(POLL)
    assert q2.switched == ['off', 'on']


def test_between_polls_a_pass_of_the_ui_loop_reads_only_the_clock(fw, q2):
    q2.pass_(POLL)
    before = len(fw.calls())
    q2.now += POLL - 1
    q2.poll()
    assert [name for name, _ in fw.calls()[before:]] == ['time_now_ms']
    q2.now += 1
    q2.poll()
    assert 'window_manager' in [name for name, _ in fw.calls()[before:]]  # the window stack, looked through again


def test_the_idle_time_is_counted_across_the_clocks_wrap(q2):
    q2.now = (1 << 32) - IDLE // 2
    q2.pass_(IDLE)
    assert q2.switched == []
    q2.pass_(POLL)
    assert q2.switched == ['off']
    q2.ui.window('stream_page')
    q2.pass_(POLL)
    assert q2.switched == ['off', 'on']


# The Wi-Fi page, and the wait for a woken radio's link.
@pytest.mark.parametrize('radio', ['off for Smart Wi-Fi', 'on', 'off by the user'])
def test_the_wifi_page_shows_the_users_setting_the_radio_back_before_stock_reads_it(fw, q2, radio):
    if radio == 'off for Smart Wi-Fi':
        q2.idle()
    if radio == 'off by the user':
        q2.switch()
    switched, saved, seen = len(q2.switched), list(q2.saved), []
    fw.mock('stock_wifi_page_trampoline', lambda win, ctx, *_: seen.append((win, ctx, q2.wifi.value)) or 55)
    assert fw.fn('ringnav_wifi_page', C.c_int, C.c_void_p, C.c_void_p)(0x11, 0x22) == 55
    assert seen == [(0x11, 0x22, radio != 'off by the user')]
    assert q2.switched[switched:] == (['on'] if radio == 'off for Smart Wi-Fi' else []) and q2.saved == saved


@pytest.mark.parametrize('now', [1000, 1 << 20])  # 1000: no wake to count from is not a wake at the clock's 0
def test_with_the_radio_as_the_user_left_it_nothing_waits_for_a_link(q2, now):
    q2.now = now
    assert q2.wait() == 0 and q2.switched == []  # no link, and none coming: the caller says so, as stock's would


def test_the_users_tap_ends_the_wait_for_smart_wifis_wake(q2):
    q2.idle()
    assert q2.wait() == 1
    q2.switch()  # the user's Off while the link was coming
    assert q2.saved == [SAVED_OFF] and q2.wait() == 0 and q2.switched == ['off', 'on', 'off']


@pytest.mark.parametrize('bars,status,waits', [(0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 0)])
def test_the_ask_wakes_the_radio_and_waits_for_both_the_bars_and_the_connect_status(q2, bars, status, waits):
    q2.idle()
    assert q2.wait() == 1 and q2.switched == ['off', 'on'] and q2.saved == []
    q2.now += 3000
    q2.bars, q2.status = bars, status
    assert q2.wait() == waits and q2.switched == ['off', 'on']
    if not waits:
        q2.bars = q2.status = 0
        assert q2.wait() == 0  # that wake's wait is over: a link lost later is the caller's to report


@pytest.mark.parametrize('woken_by', ['the ask', 'a page'])
def test_a_wake_that_brings_no_link_is_waited_for_fifteen_seconds_and_no_longer(q2, woken_by):
    q2.idle()
    if woken_by == 'a page':
        q2.wake()  # Streaming's page opening (ringnav_stream): the row pressed there waits from this wake
    else:
        assert q2.wait() == 1
    q2.now += CONNECT - 1
    assert q2.wait() == 1
    q2.now += 1
    assert q2.wait() == 0 and q2.wait() == 0 and q2.switched == ['off', 'on']


def test_while_stock_refuses_its_switch_the_wait_ends_fifteen_seconds_from_the_ask(q2):
    q2.idle()
    q2.set('wifi_initstatus', 2)
    q2.up()  # the bars and the status as read a second ago, before the radio went off
    assert q2.wait() == 1
    q2.now += CONNECT - 1
    assert q2.wait() == 1
    q2.now += 1
    assert q2.wait() == 0 and q2.switched == ['off'] and q2.off()


# A press that needs the link.
def test_a_press_with_a_link_and_the_radio_as_the_user_left_it_goes_on(q2):
    q2.up()
    press = Press(q2)
    press.click(5, 0x77)
    assert press.went == [5] and q2.toasts == [] and q2.timers == [] and q2.switched == []


def test_a_press_with_no_link_and_none_coming_says_so(q2):
    press = Press(q2)
    press.click(5, 0x77)
    assert press.went == [] and q2.toasts == [NO_LINK] and q2.timers == [] and q2.switched == []


def test_a_press_while_the_radio_comes_back_is_held_and_runs_once_the_link_is_up(q2):
    q2.idle()
    press = Press(q2)
    press.click(5, 0x77)
    assert press.went == [] and q2.toasts == [WAIT] and q2.switched == ['off', 'on'] and q2.saved == []
    q2.tick(4)
    assert press.runs == [(5, 0x77)] and len(q2.timers) == 1
    q2.up()
    q2.tick()
    assert press.runs == [(5, 0x77), (5, None)] and press.went == [5] and q2.toasts == [WAIT] and q2.timers == []
    press.click(6, 0x77)  # nothing is held any more: the next press goes on, and the radio's five minutes run again
    assert press.went == [5, 6] and q2.toasts == [WAIT]
    q2.pass_(2 * IDLE)
    assert q2.switched == ['off', 'on', 'off']


@pytest.mark.parametrize('refused', [0, 1])  # 1: stock would refuse its switch, so the radio is still off at the end
def test_a_press_held_fifteen_seconds_with_no_link_runs_unheld_to_fail_as_it_would_have(q2, refused):
    q2.idle()
    q2.set('wifi_initstatus', 2 * refused)
    press = Press(q2)
    press.click(5, 0x77)
    q2.tick(CONNECT // TICK - 1)
    assert len(press.runs) == 1
    q2.tick()
    assert len(press.runs) == 2 and press.went == [] and q2.toasts == [WAIT, NO_LINK] and q2.timers == []


def test_a_second_press_joins_the_held_one_even_with_the_link_up(q2):
    q2.idle()
    press = Press(q2)
    press.click(5, 0x77)
    q2.up()
    press.click(5, 0x77)  # before the timer's next turn
    assert press.went == [] and q2.toasts == [WAIT, WAIT] and len(q2.timers) == 1
    q2.tick()
    assert press.went == [5] and q2.timers == []


def test_a_press_of_another_row_takes_the_held_ones_place(q2):
    q2.idle()
    first, second = Press(q2), Press(q2)
    first.click(1, 0x77)
    second.click(2, 0x77)
    q2.up()
    q2.tick()
    assert (first.went, second.went) == ([], [2]) and q2.timers == []


def test_a_held_press_is_dropped_once_its_page_is_left(q2):
    q2.idle()
    press = Press(q2)
    press.click(5, 0x77)
    playing = q2.ui.window('playing_page')
    q2.up()
    q2.tick()
    q2.ui.destroy(playing.addr)  # and back: it is not kept for later
    q2.tick()
    assert len(press.runs) == 1 and q2.timers == []


def test_a_toast_and_the_volume_over_the_page_do_not_drop_its_held_press(q2):
    q2.idle()
    press = Press(q2)
    press.click(5, 0x77)
    q2.ui.window('msginfo_dialog')
    q2.ui.window('volume_dialog')
    q2.up()
    q2.tick()
    assert press.went == [5]


@pytest.mark.parametrize('closes', [0, 1])
def test_a_sliding_page_is_looked_at_again_once_the_slide_ends(q2, closes):
    q2.idle()
    apps = q2.ui.window('apps_page')
    press = Press(q2)
    press.click(5, 0x77)
    q2.up()
    q2.sliding = 1  # Return: the page slides out, still the top window
    q2.tick()
    assert len(press.runs) == 1 and len(q2.timers) == 1
    q2.sliding = 0
    if closes:
        q2.ui.destroy(apps.addr)
    q2.tick()
    assert press.went == ([] if closes else [5]) and q2.timers == []


def test_another_page_where_the_closed_one_was_does_not_get_its_press(q2):
    q2.idle()
    apps = q2.ui.window('apps_page')
    press = Press(q2)
    press.click(5, 0x77)
    apps.name = 'radio_page'  # closed, and the next window opened at its address
    q2.up()
    q2.tick()
    assert len(press.runs) == 1 and q2.timers == []


def test_spotifys_row_with_the_radio_as_the_user_left_it_is_not_held(q2):
    fn, runs = q2.action()
    assert q2.hold(fn, 5, None) == 0 and q2.toasts == [] and q2.timers == [] and q2.switched == []


@pytest.mark.parametrize('case', ['another page than its own', 'a page name too long to keep', 'no page', 'no timer'])
def test_a_press_that_cannot_be_held_runs_now_as_without_smart_wifi(fw, q2, case):
    q2.idle()
    if case == 'a page name too long to keep':
        q2.ui.window('p' * 32)
    if case == 'no page':
        q2.ui.destroy(q2.home.addr)
    if case == 'no timer':
        fw.mock('timer_add', lambda *_: 0)
    fn, runs = q2.action()
    assert q2.hold(fn, 5, b'apps_page' if case == 'another page than its own' else None) == 0 and q2.toasts == []
    q2.up()
    q2.tick()
    assert runs == [] and q2.timers == []  # the caller's to run: never a second time from a timer


@pytest.mark.parametrize('name,page', [('apps_page', b'apps_page'), ('p' * 31, None)])
def test_a_press_on_its_own_page_is_held_with_its_argument(q2, name, page):
    q2.idle()
    q2.ui.window(name)
    fn, runs = q2.action()
    assert q2.hold(fn, 5, page) == 1 and q2.toasts == [WAIT] and runs == []
    q2.up()
    q2.tick()
    assert runs == [(5, None)]


# Stock's three actions that check the link once.
def rows(q2, parent, n):
    """n rows as stock builds them: each list_item and its button named by its index."""
    items = [q2.ui.add('list_item', parent, name=str(k)) for k in range(n)]
    return items, [q2.ui.add('button', item, name=str(k)) for k, item in enumerate(items)]


def test_tidals_row_waits_for_the_link_and_is_found_again_in_homes_streaming_list(fw, q2):
    q2.idle()
    rows(q2, q2.ui.add('scroll_view', q2.home, name='scroll_view_library'), 1)  # Home's Split: its Library list first
    items, buttons = rows(q2, q2.ui.add('scroll_view', q2.home, name='scroll_view_streamsset'), 1)
    stock: list = []
    fw.mock('stock_stream_row_trampoline', lambda row, event, *_: stock.append((row, event)) or 9)
    assert fw.fn('ringnav_stream_row', C.c_int, C.c_void_p, C.c_void_p)(buttons[0].addr, 0x77) == 0
    assert stock == [] and q2.toasts == [WAIT] and q2.switched == ['off', 'on']
    q2.tick()
    assert stock == []
    q2.up()
    q2.tick()
    assert stock == [(items[0].addr, 0)] and q2.timers == []


@pytest.mark.parametrize('index,held', [(0, 0), (1, 0), (2, 1)])
def test_network_service_holds_wifi_transfer_alone_dlna_and_airplay_switch_at_once(fw, q2, index, held):
    q2.idle()
    page = q2.ui.window('netservice_page')
    items, buttons = rows(q2, page, 3)
    stock: list = []
    fw.mock('stock_netservice_row_trampoline', lambda row, event, *_: stock.append((row, event)) or 9)
    result = fw.fn('ringnav_netservice_row', C.c_int, C.c_void_p, C.c_void_p)(buttons[index].addr, 0x77)
    assert (result, stock, q2.toasts) == ((0, [], [WAIT]) if held else (9, [(buttons[index].addr, 0x77)], []))
    q2.up()
    q2.tick()
    assert stock == [(items[2].addr, 0) if held else (buttons[index].addr, 0x77)]


@pytest.mark.parametrize('page,pressed,held', [('playermore_page', 'top', 1), ('playermore_page', 'home', 0), ('playing_page', 'top', 0)])
def test_mores_download_cover_and_lyrics_waits_for_the_link_on_more_alone(fw, q2, page, pressed, held):
    q2.idle()
    top = q2.ui.window(page)
    win = top.addr if pressed == 'top' else q2.home.addr
    stock: list = []
    fw.mock('stock_online_match_trampoline', lambda w, *_: stock.append(w) or 9)
    result = fw.fn('ringnav_online_match', C.c_int, C.c_void_p)(win)
    assert (result, stock, q2.toasts) == ((0, [], [WAIT]) if held else (9, [win], []))
    q2.up()
    q2.tick()
    assert stock == [win]


def test_stocks_actions_run_at_once_with_the_radio_as_the_user_left_it(fw, q2):
    stock: list = []
    fw.mock('stock_stream_row_trampoline', lambda row, event, *_: stock.append((row, event)) or 9)
    fw.mock('stock_netservice_row_trampoline', lambda row, event, *_: stock.append((row, event)) or 9)
    fw.mock('stock_online_match_trampoline', lambda win, *_: stock.append(win) or 9)
    tidal = rows(q2, q2.ui.add('scroll_view', q2.ui.window('stream_page'), name='scroll_view_streamsset'), 1)[1][0]
    assert fw.fn('ringnav_stream_row', C.c_int, C.c_void_p, C.c_void_p)(tidal.addr, 0x77) == 9
    transfer = rows(q2, q2.ui.window('netservice_page'), 3)[1][2]
    assert fw.fn('ringnav_netservice_row', C.c_int, C.c_void_p, C.c_void_p)(transfer.addr, 0x77) == 9
    more = q2.ui.window('playermore_page')
    assert fw.fn('ringnav_online_match', C.c_int, C.c_void_p)(more.addr) == 9
    assert stock == [(tidal.addr, 0x77), (transfer.addr, 0x77), more.addr]
    assert q2.toasts == [] and q2.timers == [] and q2.switched == []
