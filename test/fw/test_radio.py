"""radio.c (docs/internals.md#internet-radio): the station player, Favourites, the directory fetch and the pages. The
playlist and CSV parsers are test/peq.py's."""
import ctypes as C
import struct

import pytest

from conftest import O, Files

RADIO = '/mnt/mmc/Radio'
FAVS, LAST, STATE = f'{RADIO}/favourites.m3u', f'{RADIO}/.last', '/tmp/q2radio.state'
API = 'https://all.api.radio-browser.info'
QUERY = '?order=votes&reverse=true&limit=100&hidebroken=true'
KEY_PLAY, KEY_BACK_BTN, KEY_FWD_BTN, KEY_RETURN = O['KEY_PLAY'], O['KEY_BACK_BTN'], O['KEY_FWD_BTN'], O['KEY_RETURN']
EVT_DESTROY, EVT_VALUE_CHANGED, EVT_CLICK, EVT_BLUR, EVT_KEY_UP, EVT_IM_ACTION = (O[k] for k in (
    'EVT_DESTROY', 'EVT_VALUE_CHANGED', 'EVT_CLICK', 'EVT_BLUR', 'EVT_KEY_UP', 'EVT_IM_ACTION'))
EVENT_KEY, W_PARENT = O['EVENT_KEY'], O['W_PARENT']
MENU, PICK, STATIONS = range(3)
FEATURED = ['Radio Paradise', 'SomaFM Groove Salad', 'BBC World Service', 'KEXP', 'FIP', 'NTS 1']
MENU_ROWS = ['Now Playing', 'Favourites', 'Featured', 'Top Stations', 'By Country', 'By Genre']


class List(C.Structure):
    _fields_ = [('buf', C.c_void_p), ('len', C.c_uint), ('n', C.c_int), ('truncated', C.c_int),
                ('name', C.c_uint * 201), ('url', C.c_uint * 201)]

    def stations(self):
        return [(self.at(self.name[i]), self.at(self.url[i])) for i in range(self.n)]

    def at(self, off):
        return C.string_at((self.buf or 0) + off).decode()


class Rd(C.Structure):
    _fields_ = [('pid', C.c_int), ('sock', C.c_int), ('active', C.c_int), ('at', C.c_int), ('polled', C.c_uint),
                ('volume', C.c_uint), ('list', List), ('state', C.c_char * 12), ('title', C.c_char * 256),
                ('codec', C.c_char * 16), ('bitrate', C.c_char * 16), ('error', C.c_char * 64),
                ('since', C.c_ulonglong), ('serial', C.c_uint)]


class Fx(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('running', C.c_int), ('cancel', C.c_int), ('done', C.c_int), ('code', C.c_long),
                ('body', C.c_void_p), ('n', C.c_uint), ('url', C.c_char * 320)]


class Ui(C.Structure):
    _fields_ = [('page', C.c_void_p), ('view', C.c_void_p), ('edit', C.c_void_p), ('timer', C.c_uint), ('go', C.c_uint),
                ('leave', C.c_uint), *((k, C.c_int) for k in 'level next arg want az note genre favs from'.split()),
                ('rows', C.c_int * 6), ('sel', C.c_int * 3), ('pick', List), ('list', List), ('caption', C.c_char * 64)]


class Np(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page station artist song info elapsed heart'.split()),
                ('timer', C.c_uint), ('leave', C.c_uint), ('seen', C.c_uint), ('ipod', C.c_int)]


def rd(fw):
    return fw.var('radio__rd', Rd)


def ui(fw):
    return fw.var('radio__ui', Ui)


def np_(fw):
    return fw.var('radio__np', Np)


def fx(fw):
    return fw.var('radio__fx', Fx)


def m3u(*stations):
    return ('#EXTM3U\n' + ''.join(f'#EXTINF:-1,{n}\n{u}\n' for n, u in stations)).encode()


class Card(Files):
    """The card's files, RADIO_DIR's listing (opendir and readdir, not in name order) and access."""
    def __init__(self, fw, files=None):
        super().__init__(fw, dict(files or {}))
        self.ents, self.dirs = [], {}
        fw.mock('fw_access', lambda p, *_: 0 if self.exists(C.string_at(p).decode()) else -1)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d) and 0)
        fw.mock('fw_ferror', lambda *_: 0)

    def exists(self, path):
        return path in self.files or any(p.startswith(path + '/') for p in self.files)

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        names = sorted({p[len(path) + 1:] for p in self.files if p.startswith(path + '/') and '/' not in p[len(path) + 1:]},
                       reverse=True)
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


class Clock:
    """time_now_ms, sleep_ms passing it on, and clock_gettime's CLOCK_MONOTONIC (now_ms) in step."""
    def __init__(self, fw, now=100000):
        self.now = now
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('sleep_ms', lambda ms, *_: self.step(ms))
        fw.mock('fw_clock_gettime', lambda which, t, *_: struct.pack_into('<qq', (C.c_char * 16).from_address(t), 0,
                                                                          self.now // 1000, self.now % 1000 * 1000000))

    def step(self, ms):
        self.now += ms


class Player:
    """q2video: fork's pid (0 when it can't start) and the keys on RADIO_SOCK; waitpid says whether it ended."""
    def __init__(self, fw, pid=4242, ends=True):
        self.fw, self.pid, self.ends, self.started, self.killed, self.sent = fw, pid, ends, [], [], []
        fw.mock('fw_fork', lambda *_: self.started.append(self.pid) or (self.pid or -1))
        fw.mock('fw_socket', lambda *_: 9)
        fw.mock('fw_waitpid', lambda pid, status, flags, *_: pid if self.ends or not flags else 0)
        fw.mock('fw_system', lambda cmd, *_: self.killed.append(C.string_at(cmd).decode()))
        # the datagram and its address are on the caller's stack: read as sent
        fw.mock('fw_sendto', lambda s, data, n, flags, to, *_: self.sent.append((C.string_at(to + 2), C.string_at(data, n))) or n)

    def keys(self):
        return [data for path, data in self.sent if path == b'/tmp/q2radio.sock']


class Widgets:
    """AWTK's constructors each a fresh zeroed widget with its parent; labels' text as set, read back."""
    def __init__(self, fw):
        self.fw, self.blocks, self.text = fw, [], {}
        for name in ('window_create', 'scroll_view_create', 'list_item_create', 'image_create', 'hscroll_label_create',
                     'list_view_create', 'button_create', 'label_create'):
            fw.mock(name, lambda parent, *_: self.new(parent))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, *_: self.new(parent))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get)

    def new(self, parent):
        self.blocks.append(C.create_string_buffer(0x200))
        C.c_void_p.from_address(C.addressof(self.blocks[-1]) + W_PARENT).value = parent
        return C.addressof(self.blocks[-1])

    def get(self, w, out, n, *_):
        s = self.text.get(w, '').encode()
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0


class Page:
    """radio_open's page: its timers by function, its rows' clicks and the toasts."""
    def __init__(self, fw, card, wifi=1):
        self.fw, self.card, self.w = fw, card, Widgets(fw)
        self.timers, self.toasts, self.threads = [], [], []
        self.wifi = wifi
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('get_wifisignal', lambda *_: self.wifi)
        fw.mock('navigator_to_with_context', lambda name, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: self.threads.append(fn) or 0)
        fw.fn('radio_open', C.c_int, C.c_void_p, C.c_void_p)(0, 0)

    def fn(self, name):
        return C.cast(getattr(self.fw.lib, 'radio__' + name), C.c_void_p).value

    def run(self, name):
        """The newest timer running name, as the next loop pass would."""
        fn = [f for f, _ in self.timers if f == self.fn(name)][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(0)

    def rows(self, name):
        """The newest list's rows clicking name: their clicks by index."""
        rows = [(ctx, fn) for _, (w, evt, fn, ctx, *_) in self.fw.calls('widget_on') if evt == EVT_CLICK and fn == self.fn(name)]
        return {ctx: (lambda f=fn, c=ctx: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, 0)) for ctx, fn in rows}

    def menu_rows(self):
        state, n = ui(self.fw), len(self.rows('menu_click'))
        return [MENU_ROWS[state.rows[k]] for k in range(n)]

    def menu(self, caption):
        """The menu's row with caption, clicked, and the rebuild it schedules."""
        self.rows('menu_click')[self.menu_rows().index(caption)]()
        if caption != 'Now Playing':
            self.run('go')

    def handler(self, w, event):
        (fn, ctx), = [(fn, ctx) for _, (ww, evt, fn, ctx, *_) in self.fw.calls('widget_on') if ww == w and evt == event][-1:]
        return lambda e=0: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, e)

    def key(self, page, key=KEY_RETURN):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, key)
        self.handler(page, EVT_KEY_UP)(C.addressof(event))


def page(fw, files=None, **kw):
    Clock(fw)
    return Page(fw, Card(fw, files), **kw)


# The menu and the built-in lists.

def test_the_menu_without_a_station_played_has_no_now_playing(fw):
    p = page(fw)
    assert p.menu_rows() == MENU_ROWS[1:]
    assert [C.string_at(a[0]) for _, a in fw.calls('fw_mkdir')] == [RADIO.encode()]  # the folder, nothing in it
    assert p.card.files == {}


def test_the_menu_after_a_restart_has_now_playing_from_last(fw):
    p = page(fw, {LAST: m3u(('KEXP', 'https://kexp.example/128.mp3'))})
    assert p.menu_rows() == MENU_ROWS and fw.calls('fw_mkdir') == []


def test_featured_is_the_six_built_in_stations(fw):
    p = page(fw)
    p.menu('Featured')
    stations = ui(fw).list.stations()
    assert [n for n, _ in stations] == FEATURED and all('://' in u for _, u in stations)
    assert ui(fw).caption == b'Featured' and ui(fw).level == STATIONS


def test_favourites_are_favourites_m3u_then_every_other_playlist(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/')),
                  f'{RADIO}/b.PLS': b'[playlist]\nFile1=http://3.example/\nTitle1=Three\n',
                  f'{RADIO}/a.m3u': m3u(('Two', 'http://2.example/')),
                  f'{RADIO}/.hidden.m3u': m3u(('Hidden', 'http://h.example/')),
                  f'{RADIO}/notes.txt': b'http://x.example/\n', f'{RADIO}/._a.m3u': b'junk'})
    p.menu('Favourites')
    first, *rest = ui(fw).list.stations()
    assert first == ('One', 'http://1.example/')  # the rest in the folder's order
    assert sorted(rest) == [('Three', 'http://3.example/'), ('Two', 'http://2.example/')]
    assert ui(fw).caption == b'Favourites'


def test_no_favourites(fw):
    p = page(fw)
    p.menu('Favourites')
    assert ui(fw).list.n == 0 and ui(fw).caption == b'No favourites'


# Playing.

def play(fw, p, i=0, pid=4242, caption='Featured'):
    player = Player(fw, pid)
    p.menu(caption)
    p.rows('station_click')[i]()
    return player


def test_a_station_plays_in_q2video_and_is_kept_as_last(fw):
    p = page(fw)
    player = play(fw, p, 3)
    state = rd(fw)
    assert player.started == [4242] and (state.pid, state.active, state.at) == (4242, 1, 3)
    assert state.state == b'connecting' and state.serial == 1
    assert p.card.files[LAST] == m3u(('KEXP', 'https://kexp-mp3-128.streamguys1.com/kexp128.mp3'))
    assert [n for n, _ in state.list.stations()] == FEATURED  # the list it was in, for the side buttons
    assert fw.fn('radio_status', C.c_int)() == 2


def test_a_station_that_can_t_start_says_so(fw):
    p = page(fw)
    play(fw, p, 0, pid=0)
    assert rd(fw).pid == 0 and rd(fw).state == b'error' and fw.fn('radio_status', C.c_int)() == 3


def media(fw, key):
    return fw.fn('radio_media', C.c_int, C.c_uint)(key)


def test_play_pause_stops_the_station_and_plays_it_again(fw):
    p = page(fw)
    player = play(fw, p, 2)
    assert media(fw, KEY_PLAY) == 1
    state = rd(fw)
    assert player.keys() == [b'q'] and state.pid == 0 and state.state == b'stopped' and state.active == 1
    assert [C.string_at(a[0]).decode() for _, a in fw.calls('fw_unlink')] == [STATE]
    assert media(fw, KEY_PLAY) == 1 and state.pid == 4242 and state.at == 2


@pytest.mark.parametrize('at, key, then', [(2, KEY_FWD_BTN, 3), (5, KEY_FWD_BTN, 0), (2, KEY_BACK_BTN, 1),
                                           (0, KEY_BACK_BTN, 5)])
def test_the_side_buttons_step_through_the_list_round_the_ends(fw, at, key, then):
    p = page(fw)
    player = play(fw, p, at)
    assert media(fw, key) == 1 and rd(fw).at == then and player.keys() == [b'q']


@pytest.mark.parametrize('key', [218, 172, 173, KEY_RETURN])
def test_other_keys_are_not_the_radio_s(fw, key):
    p = page(fw)
    play(fw, p)
    assert media(fw, key) == 0


def test_once_another_player_took_over_the_keys_are_theirs(fw):
    p = page(fw)
    player = play(fw, p)
    fw.fn('radio_stop', None)()
    assert rd(fw).active == 0 and player.keys() == [b'q'] and media(fw, KEY_PLAY) == 0
    assert fw.fn('radio_status', C.c_int)() == 0


def test_a_q2video_that_won_t_quit_is_killed_after_1_5_s(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    media(fw, KEY_PLAY)
    assert player.killed == ['kill -9 4242'] and rd(fw).pid == 0
    sleeps = [a[0] for _, a in fw.calls('sleep_ms')]
    assert set(sleeps) == {20} and 1500 <= 20 * len(sleeps) <= 1520


@pytest.mark.parametrize('active, pid, playing, then', [(1, 4242, 0, 'q'), (1, 0, 1, 'start'), (1, 4242, 1, None),
                                                        (1, 0, 0, None), (0, 0, 1, None)])
def test_set_playing(fw, active, pid, playing, then):
    p = page(fw)
    player = play(fw, p)
    if not pid:
        media(fw, KEY_PLAY)
    rd(fw).active = active
    started, keys = len(player.started), len(player.keys())
    fw.fn('radio_set_playing', None, C.c_int)(playing)
    assert (player.keys()[keys:], len(player.started) - started) == {'q': ([b'q'], 0), 'start': ([], 1), None: ([], 0)}[then]


# radio_poll.

def poll(fw):
    fw.fn('radio_poll', None)()


def test_the_poll_reads_q2video_s_state(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    p.card.files[STATE] = b'state=playing\ntitle=Artist - Song\ncodec=MP3\nbitrate=128\nerror=\nat=123456\n'
    state = rd(fw)
    state.polled -= 500
    poll(fw)
    assert (state.state, state.title, state.codec, state.bitrate, state.since) == (b'playing', b'Artist - Song', b'MP3',
                                                                                   b'128', 123456)


def test_while_it_plays_standby_and_the_dac_s_countdown_are_held(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    dac = fw.var('fw_ctx_g_dacoff_time', C.c_int)
    dac.value = 30
    rd(fw).polled -= 500
    poll(fw)
    assert [a[:3] for _, a in fw.calls('reset_poweroptions_timer')] == [(1, 1, 0)] and dac.value == 0


def test_a_dac_already_off_stays_off(fw):
    p = page(fw)
    play(fw, p).ends = False
    dac = fw.var('fw_ctx_g_dacoff_time', C.c_int)
    dac.value = -1
    rd(fw).polled -= 500
    poll(fw)
    assert dac.value == -1


def test_the_poll_is_paced_to_half_a_second(fw):
    p = page(fw)
    play(fw, p).ends = False
    state = rd(fw)
    state.polled = 100000 - 499
    poll(fw)
    assert fw.calls('reset_poweroptions_timer') == []


def test_a_volume_change_reaches_q2video(fw):
    p = page(fw)
    player = play(fw, p)
    player.ends = False
    fw.var('g_volume', C.c_ubyte).value = 40
    rd(fw).polled -= 500
    poll(fw)
    assert player.keys()[-1] == b'v\x28'
    rd(fw).polled -= 500
    poll(fw)
    assert player.keys().count(b'v\x28') == 1  # only on a change


@pytest.mark.parametrize('was, now', [(b'playing', b'stopped'), (b'error', b'error')])
def test_a_station_that_ended_is_stopped_or_says_its_error(fw, was, now):
    p = page(fw)
    play(fw, p)
    p.card.files[STATE] = b'state=' + was + b'\n'
    rd(fw).polled -= 500
    poll(fw)
    assert rd(fw).pid == 0 and rd(fw).state == now and fw.calls('reset_poweroptions_timer') == []


def test_nothing_playing_no_poll(fw):
    Clock(fw)
    poll(fw)
    assert fw.calls('fw_waitpid') == []


# Favourites (radio_hold, the heart).

def hold(fw, p, row):
    fw.mock('widget_get_prop_int', lambda w, key, default, *_: row if C.string_at(key) == b'_ringnav_index' else default)
    s = fw.fn('radio_hold', C.c_char_p, C.c_void_p)(ui(fw).page)
    return s and s.decode()


def test_held_on_a_station_it_goes_into_favourites_and_out_again(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/'))})
    p.menu('Featured')
    assert hold(fw, p, 3) == 'Added to Favourites'
    assert p.card.files[FAVS] == m3u(('One', 'http://1.example/'), ('KEXP', 'https://kexp-mp3-128.streamguys1.com/kexp128.mp3'))
    assert f'{FAVS}.tmp' not in p.card.files and fw.calls('fw_fsync')  # written aside, synced, renamed
    assert hold(fw, p, 3) == 'Removed from Favourites'
    assert p.card.files[FAVS] == m3u(('One', 'http://1.example/'))


def test_a_full_favourites_file_takes_no_more_but_gives_one_up(fw):
    kexp = ('KEXP', 'https://kexp-mp3-128.streamguys1.com/kexp128.mp3')
    full = m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(199)], kexp)
    p = page(fw, {FAVS: full})
    p.menu('Featured')
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == full
    assert hold(fw, p, 3) == 'Removed from Favourites'
    assert p.card.files[FAVS] == m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(199)])


def test_a_favourites_file_past_200_is_never_rewritten(fw):
    over = m3u(*[(f'S{i}', f'http://{i}.example/') for i in range(201)])
    p = page(fw, {FAVS: over})
    p.menu('Featured')
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == over


def test_a_favourites_file_with_a_nul_is_never_rewritten(fw):
    """The rest after a NUL would be lost."""
    odd = m3u(('One', 'http://1.example/')) + b'\0' + m3u(('Two', 'http://2.example/'))
    p = page(fw, {FAVS: odd})
    p.menu('Featured')
    assert hold(fw, p, 0) == 'Could not save Favourites' and p.card.files[FAVS] == odd


def test_a_failed_rename_is_a_failed_save(fw):
    p = page(fw)
    p.menu('Featured')
    p.card.fail_rename = True
    assert hold(fw, p, 0) == 'Could not save Favourites' and FAVS not in p.card.files


def test_a_toggle_from_the_favourites_list_rebuilds_it(fw):
    p = page(fw, {FAVS: m3u(('One', 'http://1.example/'), ('Two', 'http://2.example/'))})
    p.menu('Favourites')
    assert hold(fw, p, 0) == 'Removed from Favourites'
    p.run('go')
    assert ui(fw).list.stations() == [('Two', 'http://2.example/')]


@pytest.mark.parametrize('row', [-1, 6])
def test_held_on_no_station_does_nothing(fw, row):
    p = page(fw)
    p.menu('Featured')
    assert hold(fw, p, row) is None


def test_held_on_the_menu_or_another_page_does_nothing(fw):
    p = page(fw)
    assert hold(fw, p, 0) is None
    p.menu('Featured')
    assert fw.fn('radio_hold', C.c_char_p, C.c_void_p)(0x1234) is None


# The directory.

class Curl:
    """demo's libcurl: perform hands the body to the write callback in chunks; its answer and the HTTP code."""
    def __init__(self, fw, body=b'', code=200, error=0, chunk=1000):
        self.fw, self.body, self.code, self.error, self.chunk, self.opts, self.wrote = fw, body, code, error, chunk, {}, []
        fw.mock('fw_curl_easy_init', lambda *_: 0x9000)
        fw.mock('fw_curl_easy_setopt', self.setopt)
        fw.mock('fw_curl_easy_perform', self.perform)
        fw.mock('fw_curl_easy_getinfo', lambda c, info, out, *_: C.c_long.from_address(out).__setattr__('value', self.code))

    def setopt(self, c, opt, value, *_):
        self.opts[opt] = C.string_at(value).decode() if 10000 <= opt < 20000 else value
        return 0

    def perform(self, c, *_):
        write = C.CFUNCTYPE(C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)(self.opts[20011])
        for at in range(0, len(self.body), self.chunk):
            part = self.body[at:at + self.chunk]
            self.wrote.append(write(part, 1, len(part), None))
            if self.wrote[-1] != len(part):
                return 23  # CURLE_WRITE_ERROR: the callback took less
        return self.error


def fetched(fw, p, curl):
    """The worker run as its pthread would, then the page's tick."""
    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(p.threads[-1])(0)
    return p.run('tick')


def note(p):
    return [t for t in p.w.text.values()]


def test_top_stations_fetches_by_votes_over_verified_tls(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('Zed', 'http://z.example/'), ('Abe', 'http://a.example/')))
    p.menu('Top Stations')
    assert fx(fw).running == 1 and 'Loading…' in note(p)
    fetched(fw, p, curl)
    assert curl.opts[10002] == f'{API}/m3u/stations/topvote/100?hidebroken=true'
    assert (curl.opts[10065], curl.opts[64], curl.opts[81], curl.opts[10018]) == ('/etc/scrobble-ca.pem', 1, 2, 'Q2Pod')
    assert (curl.opts[78], curl.opts[13], curl.opts[52], curl.opts[99]) == (10, 30, 1, 1)
    assert ui(fw).list.stations() == [('Zed', 'http://z.example/'), ('Abe', 'http://a.example/')]  # vote order
    assert ui(fw).level == STATIONS and ui(fw).caption == b'Top Stations' and fx(fw).running == 0


def test_countries_are_a_to_z_and_a_country_s_stations_too(fw):
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\n"Korea, Republic of",KR,10\nThe Netherlands,NL,90\nGermany,DE,99\n'
                    b'Spain,ES,70\nAustria,AT,50\n')
    p.menu('By Country')
    assert curl.opts == {} and fx(fw).url == f'{API}/csv/countries?order=stationcount&reverse=true&limit=60'.encode()
    fetched(fw, p, curl)
    pick = ui(fw).pick
    assert [(pick.at(pick.name[i]), pick.at(pick.url[i])) for i in range(pick.n)] == [  # "The" aside, as the library
        ('Austria', 'AT'), ('Germany', 'DE'), ('Korea, Republic of', 'KR'), ('The Netherlands', 'NL'), ('Spain', 'ES')]
    assert ui(fw).level == PICK
    curl.body = m3u(('NPO Radio 2', 'http://b.example/'), ('BNR', 'http://a.example/'))
    p.rows('pick_click')[3]()
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/bycountrycodeexact/NL{QUERY}'.encode()
    assert ui(fw).caption == b'The Netherlands'
    fetched(fw, p, curl)
    assert [n for n, _ in ui(fw).list.stations()] == ['BNR', 'NPO Radio 2']


def test_genres_by_tag_escaped(fw):
    p = page(fw)
    curl = Curl(fw, b'name,stationcount\nrock,900\nhip hop,500\n')
    p.menu('By Genre')
    assert fx(fw).url == f'{API}/csv/tags?order=stationcount&reverse=true&limit=60'.encode()
    fetched(fw, p, curl)
    pick = ui(fw).pick
    assert [pick.at(pick.name[i]) for i in range(pick.n)] == ['hip hop', 'rock']
    p.rows('pick_click')[0]()
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/bytagexact/hip%20hop{QUERY}'.encode()


def test_no_wifi_no_fetch(fw):
    p = page(fw, wifi=0)
    p.menu('Top Stations')
    assert p.threads == [] and fx(fw).running == 0 and 'No Wi-Fi' in note(p) and ui(fw).note == 1


@pytest.mark.parametrize('code, error, body', [(500, 0, m3u(('A', 'http://a/'))), (200, 28, m3u(('A', 'http://a/'))),
                                               (200, 0, b'')])
def test_a_failed_fetch_says_the_directory_is_unavailable(fw, code, error, body):
    p = page(fw)
    curl = Curl(fw, body, code=code, error=error)
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert 'Directory unavailable' in note(p) and ui(fw).note == 1


def test_a_fetch_with_no_stations_says_so(fw):
    p = page(fw)
    curl = Curl(fw, b'#EXTM3U\n')
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert 'No stations' in note(p)


def test_a_body_over_256_kb_is_cut_off(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a/')) + b'#' * (300 << 10), chunk=64 << 10)
    p.menu('Top Stations')
    fetched(fw, p, curl)
    assert curl.wrote == [64 << 10] * 3 + [0] and 'Directory unavailable' in note(p)


def test_the_tick_waits_for_the_worker(fw):
    p = page(fw)
    Curl(fw)
    p.menu('Top Stations')
    assert p.run('tick') == 8 and fx(fw).running == 1 and fw.calls('fw_pthread_join') == []


def test_return_while_loading_cancels_the_transfer_and_shows_the_menu(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('A', 'http://a/')))
    p.menu('Top Stations')
    p.key(ui(fw).page)
    p.run('leave')
    assert fx(fw).cancel == 1 and fx(fw).running == 0 and len(fw.calls('fw_pthread_join')) == 1
    assert ui(fw).level == MENU and ui(fw).note == 0 and curl.opts == {}
    # what the transfer, still running until the join, hears: stop
    progress = fw.fn('radio__fetch_progress', C.c_int, C.c_void_p, *[C.c_longlong] * 4)
    got = fw.fn('radio__fetch_got', C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)
    assert progress(None, 0, 0, 0, 0) == 1 and got(b'abc', 1, 3, None) == 0


def test_return_goes_up_a_level_each_time(fw):
    p = page(fw)
    curl = Curl(fw, b'name,iso_3166_1,stationcount\nGermany,DE,99\n')
    p.menu('By Country')
    fetched(fw, p, curl)
    curl.body = m3u(('A', 'http://a/'))
    p.rows('pick_click')[0]()
    p.run('go')
    fetched(fw, p, curl)
    levels = []
    for _ in range(3):
        p.key(ui(fw).page)
        p.run('leave')
        levels.append(ui(fw).level)
    assert levels == [PICK, MENU, MENU] and len(fw.calls('navigator_back')) == 1


def test_each_level_keeps_its_selected_row(fw):
    p = page(fw)
    fw.mock('widget_get_prop_int', lambda w, key, default, *_: 4 if C.string_at(key) == b'_ringnav_index' else default)
    p.menu('Featured')
    assert ui(fw).sel[MENU] == 4


def test_the_page_s_end_frees_its_lists_and_stops_its_timers(fw):
    p = page(fw)
    p.menu('Featured')
    p.handler(ui(fw).page, EVT_DESTROY)()
    assert bytes(ui(fw)) == bytes(C.sizeof(Ui))


def test_a_second_open_does_nothing(fw):
    p = page(fw)
    count = len(fw.calls('window_create'))
    fw.fn('radio_open', C.c_int, C.c_void_p, C.c_void_p)(0, 0)
    assert len(fw.calls('window_create')) == count  # p's buffers stay alive to here


# Search.

def search(fw, p, text, failed=0):
    """The pull-down's box with text, and its OK; failed: the text didn't fit stock's copy."""
    fw.fn('radio_search', None)()
    edit = ui(fw).edit
    p.w.text[edit] = text
    wide = (C.c_uint * 2)(ord('x'), 0)
    if failed:
        fw.mock('widget_get_text_utf8', lambda *_: 1)
        fw.mock('widget_get_text', lambda *_: C.addressof(wide))
    p.handler(edit, EVT_VALUE_CHANGED)()
    return edit


def test_a_search_fetches_by_name_escaped_in_vote_order(fw):
    p = page(fw)
    curl = Curl(fw, m3u(('Zed FM', 'http://z/'), ('Abe FM', 'http://a/')))
    edit = search(fw, p, 'jazz & blues')
    assert edit and fw.calls('widget_set_focused')
    p.run('go')
    assert fx(fw).url == f'{API}/m3u/stations/byname/jazz%20%26%20blues{QUERY}'.encode()
    fetched(fw, p, curl)
    assert [n for n, _ in ui(fw).list.stations()] == ['Zed FM', 'Abe FM'] and ui(fw).caption == b'jazz & blues'
    p.key(ui(fw).page)
    p.run('leave')
    assert ui(fw).level == MENU  # back to where it was pulled over


def test_an_empty_search_shows_the_page_again(fw):
    p = page(fw)
    search(fw, p, '')
    p.run('go')
    assert ui(fw).edit is None and ui(fw).level == MENU and p.threads == []


def test_a_search_too_long_for_the_box_says_so(fw):
    p = page(fw)
    search(fw, p, 'x', failed=1)
    assert p.toasts == ['Search is too long']


def test_the_box_left_without_a_search_shows_the_page(fw):
    p = page(fw)
    fw.fn('radio_search', None)()
    p.handler(ui(fw).edit, EVT_BLUR)()
    p.run('go')
    assert ui(fw).edit is None and ui(fw).level == MENU


def test_return_with_the_box_up_empties_and_blurs_it(fw):
    p = page(fw)
    fw.fn('radio_search', None)()
    edit = ui(fw).edit
    p.w.text[edit] = 'abc'
    p.key(ui(fw).page)
    p.run('leave')
    assert p.w.text[edit] == '' and [a[:2] for _, a in fw.calls('widget_set_focused')][-1] == (edit, 0)


def test_no_search_over_a_note(fw):
    p = page(fw, wifi=0)
    p.menu('Top Stations')
    fw.fn('radio_search', None)()
    assert ui(fw).edit is None


# Now Playing.

def now_playing(fw, p):
    np = np_(fw)
    return {k: p.w.text.get(getattr(np, k), '') for k in ('station', 'artist', 'song', 'info', 'elapsed')}


def test_now_playing_shows_the_station_and_its_stream(fw):
    p = page(fw)
    player = play(fw, p, 1)
    shown = now_playing(fw, p)
    assert shown == {'station': 'SomaFM Groove Salad', 'artist': '', 'song': '', 'info': 'Connecting…', 'elapsed': ''}
    assert 'Internet Radio' in p.w.text.values()
    player.ends = False
    state = rd(fw)
    state.title, state.codec, state.bitrate, state.since = b'Air - La Femme d\'Argent', b'MP3', b'128', 100000 - 65000
    fw.mock('toolsTimeItoa', lambda out, sec, *_: C.memmove(out, b'%d\0' % sec, len(b'%d' % sec) + 1))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert p.run('np_tick') == 8
    assert now_playing(fw, p) == {'station': 'SomaFM Groove Salad', 'artist': 'Air', 'song': 'La Femme d\'Argent',
                                  'info': 'MP3  128 kbps', 'elapsed': '65'}


@pytest.mark.parametrize('state, error, bitrate, info', [(b'stopped', b'', b'', 'Stopped'),
                                                         (b'error', b'open: Device busy', b'', "Can't play: open: Device busy"),
                                                         (b'error', b'', b'', "Can't play this station")])
def test_now_playing_says_why_nothing_plays(fw, state, error, bitrate, info):
    p = page(fw)
    play(fw, p)
    r = rd(fw)
    r.pid, r.state, r.error = 0, state, error
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == info and now_playing(fw, p)['elapsed'] == ''


def test_a_stream_without_a_bitrate_shows_its_codec(fw):
    p = page(fw)
    play(fw, p)
    r = rd(fw)
    r.codec, r.since = b'AAC', 99000
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    assert now_playing(fw, p)['info'] == 'AAC'


def test_the_screen_off_leaves_now_playing_alone(fw):
    p = page(fw)
    play(fw, p)
    rd(fw).title = b'A - B'
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert p.run('np_tick') == 8 and now_playing(fw, p)['song'] == ''


def hearts(fw):
    return [C.string_at(a[1]).decode() for _, a in fw.calls('image_base_set_image') if a[0] == np_(fw).heart]


def test_the_heart_is_the_station_in_favourites_and_a_tap_toggles_it(fw):
    p = page(fw)
    play(fw, p, 4)
    assert hearts(fw) == ['play_unfav']
    p.handler(np_(fw).heart, EVT_CLICK)()
    assert p.toasts == ['Added to Favourites'] and p.card.files[FAVS] == m3u(('FIP', 'http://icecast.radiofrance.fr/fip-midfi.mp3'))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.run('np_tick')
    p.run('np_tick')  # favourites.m3u read again only when the serial moves
    assert hearts(fw) == ['play_unfav', 'play_fav']


def test_now_playing_from_the_menu_after_a_restart_plays_last(fw):
    p = page(fw, {LAST: m3u(('KEXP', 'https://kexp.example/128.mp3'))})
    player = Player(fw)
    p.menu('Now Playing')
    assert player.started == [4242] and rd(fw).list.stations() == [('KEXP', 'https://kexp.example/128.mp3')]
    assert np_(fw).page and now_playing(fw, p)['station'] == 'KEXP'


def test_now_playing_from_the_menu_while_playing_just_opens(fw):
    p = page(fw)
    player = play(fw, p, 2)
    p.key(np_(fw).page)
    p.run('np_leave')
    assert len(fw.calls('navigator_back')) == 1
    p.handler(np_(fw).page, EVT_DESTROY)()
    assert bytes(np_(fw)) == bytes(C.sizeof(Np))
    p.key(ui(fw).page)  # Featured back to the menu, which now has Now Playing
    p.run('leave')
    assert p.menu_rows() == MENU_ROWS
    p.menu('Now Playing')
    assert player.started == [4242] and np_(fw).page


def test_the_session_moves_with_each_start_and_favourites_change(fw):
    """headset.c's resume is for the same session: another station, or the heart, is another."""
    p = page(fw)
    session = fw.fn('radio_session', C.c_uint)
    play(fw, p, 0)
    first = session()
    media(fw, KEY_FWD_BTN)
    second = session()
    fw.fn('radio_hold', C.c_char_p, C.c_void_p)(np_(fw).page)
    assert len({first, second, session()}) == 3


def test_classic_s_now_playing_is_the_local_page_s(fw):
    def read(path, section, key, out, default, *_):
        v = b'1' if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)
    p = page(fw)
    play(fw, p, 0)
    arts = [C.string_at(a[1]).decode() for _, a in fw.calls('image_base_set_image') if a[0] != np_(fw).heart]
    assert np_(fw).ipod == 1 and arts[-2:] == ['stream_radio', 'classic_light_station']  # after the rows' arrows
    name = [w for w, t in p.w.text.items() if t == 'Internet Radio'][-1]  # the page's, after the menu's title
    assert (name, 0) in [a[:2] for _, a in fw.calls('widget_set_visible')]  # Classic's bar names the page


def test_held_on_now_playing_toggles_the_station_playing(fw):
    p = page(fw)
    play(fw, p, 5)
    assert fw.fn('radio_hold', C.c_char_p, C.c_void_p)(np_(fw).page) == b'Added to Favourites'
    assert p.card.files[FAVS] == m3u(('NTS 1', 'https://stream-relay-geo.ntslive.net/stream'))
