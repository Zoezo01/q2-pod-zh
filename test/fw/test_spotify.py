"""spotify.c (docs/internals.md#spotify) past Q2SPOT/1: spot_poll's hold on the player and the timers, the output it
writes for the card's aplay.sh, the idle stop, Now Playing's page (labels, times, cover, bar and glyph, the centre
scrub, Return and close) and the browser's search, Downloads hub, Storage Management, job controls, sign-out and its
tick. The protocol, the state file's parse and the browser's core are test_streaming.py's, whose struct mirrors and
enums these share."""
import ctypes as C

import pytest

from conftest import O, Files
from test_streaming import (A_CAPS, A_DOWNLOAD, A_PLAY, A_PREVIEW, A_REMOVE, A_SIGNOUT, L_CONFIRM, L_DOWNLOADS, L_JOB,
                            L_LIST, L_REMOVE, L_SELECT, L_STORAGE, S_OLD, S_PAIR, S_WAIT, W_NOSOCK, Br, Sp, Wk, track)

STATE, DOWNLOAD, LOG, OUT = '/tmp/q2-librespot.state', '/tmp/q2-librespot.download', '/mnt/mmc/.spotify/spot.log', '/tmp/q2-librespot.out'
SOCK, SINK = b'/tmp/q2-librespot.sock', b'/tmp/q2sink.sock'
KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY, KEY_RETURN = O['KEY_CENTER'], O['KEY_PREV'], O['KEY_NEXT'], O['KEY_PLAY'], O['KEY_RETURN']
EVT_DESTROY, EVT_CLICK, EVT_BLUR, EVT_KEY_UP = O['EVT_DESTROY'], O['EVT_CLICK'], O['EVT_BLUR'], O['EVT_KEY_UP']
EVT_VALUE_CHANGED, EVT_IM_ACTION = O['EVT_VALUE_CHANGED'], O['EVT_IM_ACTION']
SPOT_BAR_W, DOUBLE_CLICK_MS, SCRUB_MS = O['SPOT_BAR_W'], O['DOUBLE_CLICK_MS'], O['SCRUB_MS']
RET_REPEAT, RET_STOP = 8, 11
STOPPED, PLAYING, PAUSED = 1, 2, 3
ALL_CAPS = [b'download', b'collections', b'collection', b'playcollection', b'downloadliked', b'jobs', b'job', b'remove']


class Ui(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in 'page info msg art bar glyph title artist album elapsed remain note sub'.split()),
                *((n, C.c_uint) for n in 'timer leave scrub_at shown playing press press_at bar_drawn'.split()),
                *((n, C.c_int) for n in ('scrub', 'scrub_ms', 'ipod')),
                ('art_track', C.c_char * 64), ('art_tried', C.c_char * 64), ('art_tries', C.c_int)]


class Clock:
    """time_now_ms, sleep_ms passing it on, and clock_gettime's CLOCK_MONOTONIC (now_ms) in step."""
    def __init__(self, fw, now=100000):
        self.now = now
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('sleep_ms', lambda ms, *_: self.step(ms))
        fw.mock('fw_clock_gettime', self.clock)

    def clock(self, which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
        t[0], t[1] = self.now // 1000, self.now % 1000 * 1000000

    def step(self, ms):
        self.now += ms


class Widgets:
    """AWTK's constructors each a fresh zeroed widget with its parent; labels' text as set, read back; visibility;
    the images made; the rows of the newest list (list_item_create's, since its scroll_view) and the caption it was
    titled with."""
    def __init__(self, fw):
        self.fw, self.blocks, self.text, self.kids, self.shown, self.items = fw, [], {}, {}, {}, []
        self.title, self.last, self.writes, self.images = None, None, [], set()
        for name in ('window_create', 'hscroll_label_create', 'list_view_create', 'label_create'):
            fw.mock(name, lambda parent, *_: self.new(parent))
        fw.mock('image_create', lambda parent, *_: self.image(parent))
        fw.mock('list_item_create', lambda parent, *_: self.items.append(self.new(parent)) or self.items[-1])
        fw.mock('scroll_view_create', self.list)
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, *_: self.new(parent))
        fw.mock('widget_set_text_utf8', self.set)
        fw.mock('widget_get_text_utf8', self.get)
        fw.mock('widget_set_visible', lambda w, on, *_: self.shown.__setitem__(w, C.c_int(on).value))
        fw.mock('widget_get_child', self.child)

    def child(self, w, i, *_):
        kids, i = self.kids.get(w, []), C.c_int(i).value
        return kids[i] if 0 <= i < len(kids) else 0

    def new(self, parent):
        self.blocks.append(C.create_string_buffer(0x200))
        w = C.addressof(self.blocks[-1])
        C.c_void_p.from_address(w + O['W_PARENT']).value = parent
        self.kids.setdefault(parent, []).append(w)
        return w

    def image(self, parent):
        w = self.new(parent)
        self.images.add(w)
        return w

    def list(self, parent, *_):
        self.title, self.items = self.last, []
        return self.new(parent)

    def set(self, w, s, *_):
        self.text[w] = self.last = C.string_at(s).decode()
        self.writes.append(w)

    def get(self, w, out, n, *_):
        s = self.text.get(w, '').encode()
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0

    def rows(self):
        """Each row of the newest list: its labels' text, caption first."""
        return [[self.text[k] for k in self.kids.get(item, []) if k in self.text] for item in self.items]


def width(w, px):
    C.c_int.from_address(w + O['W_W']).value = px


def handler(fw, w, event):
    """The newest handler w has for event, as AWTK would call it."""
    fn, ctx = [(a[2], a[3]) for _, a in fw.calls('widget_on') if a[0] == w and C.c_int(a[1]).value == event][-1]
    return lambda e=None: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, e)


def key_event(key):
    event = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(event) + O['EVENT_KEY']).value = key
    return event


class Timers:
    """timer_add's timers by function; run() runs the newest of one, as the next loop pass would."""
    def __init__(self, fw):
        self.fw, self.added, self.removed = fw, [], []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.added.append((fn, C.c_int(ms).value)) or len(self.added))
        fw.mock('timer_remove', lambda timer, *_: self.removed.append(timer))

    def fn(self, name):
        return C.cast(getattr(self.fw.lib, 'spotify__' + name), C.c_void_p).value

    def of(self, name):
        return [ms for fn, ms in self.added if fn == self.fn(name)]

    def run(self, name):
        fn = [f for f, _ in self.added if f == self.fn(name)][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def classic(fw):
    """Classic, theme 1, from the config; every other key its default."""
    def read(path, section, key, out, default, *_):
        v = b'1' if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)


# spot_poll: the hold on the player and the timers.

class Librespot:
    """spot_poll's world: librespot launched from the row, its state and download files and log, its control
    socket taking a connection or not, the local player's status, and what spot_poll sends and runs."""
    def __init__(self, fw, alive=True, status=STOPPED):
        self.fw, self.clock, self.card = fw, Clock(fw), Files(fw, {})
        self.alive, self.status, self.sent, self.ran, self.connects = alive, status, [], [], 0
        self.sp = fw.var('spotify__sp', Sp)
        fw.mock('fw_socket', lambda *_: 7)
        fw.mock('fw_connect', self.connect)
        fw.mock('fw_sendto', lambda s, data, n, flags, to, *_: self.sent.append((C.string_at(to + 2), C.string_at(data, n))) or n)
        fw.mock('fw_system', lambda cmd, *_: self.ran.append(C.string_at(cmd).decode()))
        fw.mock('fw_access', lambda *_: 0)  # librespot on the card
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.fn('spotify__spot_launch', None)()
        self.poll_fn = fw.fn('spot_poll', None)

    def connect(self, fd, to, *_):
        self.connects += 1
        assert C.string_at(to + 2) == SOCK
        return 0 if self.alive else -1

    def poll(self, ms=500, state=None):
        if state is not None:
            self.card.files[STATE] = state
        self.clock.now += ms
        self.poll_fn()

    def holds(self):
        return [a[:3] for _, a in self.fw.calls('reset_poweroptions_timer')]

    def stopped(self):
        """spot_stop's commands run: the launcher's loop by its pid, librespot and aplay, and their files removed."""
        return [c for c in self.ran if 'kill -9 $(cat /tmp/q2-librespot)' in c and 'killall -9 librespot aplay' in c
                and all(f in c.split('rm -f', 1)[-1] for f in (STATE, OUT, DOWNLOAD))]


def test_nothing_is_read_until_the_row_starts_librespot(fw):
    Files(fw, {STATE: b'state=playing\n'})
    fw.mock('fw_access', lambda *_: -1)  # no librespot on the card: nothing starts either
    fw.fn('spotify__spot_launch', None)()
    fw.fn('spot_poll', None)()
    assert not fw.calls('fw_fopen') and not fw.calls('fw_system') and fw.var('spotify__sp', Sp).launched == 0


def test_the_row_starts_librespot_once(fw):
    lib = Librespot(fw)
    fw.fn('spotify__spot_launch', None)()
    assert lib.ran == ['/bin/sh /mnt/mmc/.spotify/run'] and lib.sp.launch_at == lib.clock.now


def test_polls_are_paced(fw):
    lib = Librespot(fw)
    lib.poll(state=b'state=paused\n')
    reads = len(fw.calls('fw_fopen'))
    lib.poll(ms=100)
    assert len(fw.calls('fw_fopen')) == reads
    lib.poll(ms=400)
    assert len(fw.calls('fw_fopen')) == 2 * reads


@pytest.mark.parametrize('status, stops', [(PLAYING, True), (PAUSED, True), (STOPPED, False)])
def test_a_start_takes_the_dac_from_local_music_once(fw, status, stops):
    lib = Librespot(fw, status=status)
    lib.poll(state=b'state=playing\ntrack=1\n')
    assert lib.sp.playing == 1 and lib.sp.active == 1
    assert len(fw.calls('player_stop')) == stops  # hciplayer holds the PCM even paused
    assert len(fw.calls('mclSetPcmMode')) == 1 and [a[0] for _, a in fw.calls('mclSetMute')] == [0]
    lib.poll()
    assert len(fw.calls('player_stop')) == stops and len(fw.calls('mclSetPcmMode')) == 1


def test_while_playing_standby_and_auto_power_off_are_held_and_the_dac_kept_on(fw):
    lib = Librespot(fw)
    dacoff = fw.var('fw_ctx_g_dacoff_time', C.c_int)
    dacoff.value = 25  # counting towards check_dacoff_state's
    lib.poll(state=b'state=playing\ntrack=1\n')
    assert lib.holds() == [(1, 1, 0)] and dacoff.value == 0  # standby, auto power-off; not the backlight
    lib.poll(state=b'state=paused\ntrack=1\n')
    dacoff.value = 25
    lib.poll()
    assert lib.holds() == [(1, 1, 0)] and dacoff.value == 25


def test_a_download_alone_holds_the_timers_but_takes_no_dac(fw):
    lib = Librespot(fw)
    lib.card.files[DOWNLOAD] = b'downloading=1\n'
    lib.poll(state=b'state=none\n')
    lib.poll(ms=200000)
    assert lib.holds() == [(1, 1, 0)] * 2 and lib.sp.playing == 0 and not fw.calls('mclSetPcmMode')
    assert lib.sp.launched == 1  # nor idles out
    lib.card.files[DOWNLOAD] = b'downloading=0\n'
    lib.poll()
    assert len(lib.holds()) == 2


@pytest.mark.parametrize('later, asked', [(9999, False), (10000, True)])
def test_a_crashed_librespot_holds_nothing_and_is_asked_again_10_s_on(fw, later, asked):
    lib = Librespot(fw, alive=False)
    lib.poll(state=b'state=playing\ntrack=1\n')
    assert lib.sp.dead == 1 and lib.sp.playing == 0 and lib.holds() == [] and not fw.calls('mclSetPcmMode')
    checked = lib.clock.now
    lib.poll()
    assert lib.connects == 1
    lib.alive = True
    lib.poll(ms=checked + later - lib.clock.now)
    assert lib.connects == 1 + asked and lib.sp.dead == (not asked)
    assert lib.sp.playing == asked and len(lib.holds()) == asked


def test_a_crashed_librespot_s_download_holds_nothing_either_so_it_idles_out(fw):
    lib = Librespot(fw, alive=False)
    lib.card.files[DOWNLOAD] = b'downloading=1\n'
    lib.poll(state=b'state=none\n')
    assert lib.connects == 1 and lib.sp.dead == 1 and lib.holds() == []
    lib.poll(ms=200000)  # a live download holds it this long and more (test_a_download_alone_holds_the_timers...)
    assert lib.connects == 2 and lib.holds() == [] and len(lib.stopped()) == 1 and lib.sp.launched == 0


def test_no_socket_to_check_with_is_not_taken_for_a_crash(fw):
    lib = Librespot(fw, alive=False)
    fw.mock('fw_socket', lambda *_: -1)
    lib.poll(state=b'state=playing\ntrack=1\n')
    assert lib.sp.dead == 0 and lib.sp.playing == 1 and lib.connects == 0


def test_local_music_playing_takes_the_keys_back_until_librespot_plays_again(fw):
    lib = Librespot(fw, status=PAUSED)
    lib.poll(state=b'state=paused\ntrack=1\n')
    assert (lib.sp.paused, lib.sp.local, lib.sp.active) == (1, 0, 1)
    lib.status = PLAYING
    lib.poll()
    assert (lib.sp.local, lib.sp.active) == (1, 0)
    lib.poll(state=b'state=playing\ntrack=1\n')
    assert (lib.sp.local, lib.sp.active) == (0, 1)


@pytest.mark.parametrize('played, idle_ms', [(True, 30000), (False, 150000)])
def test_librespot_idle_with_the_browser_shut_is_stopped(fw, played, idle_ms):
    lib = Librespot(fw)
    lib.sp.sock = 9
    if played:
        lib.poll(state=b'state=playing\ntrack=1\n')
    lib.poll(state=b'state=paused\ntrack=1\n' if played else b'state=none\n')
    lib.poll(ms=idle_ms - 1000)
    assert lib.sp.launched == 1 and not lib.stopped()
    lib.poll()
    assert len(lib.stopped()) == 1 and lib.sp.launched == 0 and lib.sp.active == 0 and lib.sp.sock == 9  # the row starts it again


def test_the_open_browser_keeps_librespot_while_lit_and_is_shut_with_it_when_dark(fw):
    lib = Librespot(fw)
    br, light = fw.var('spotify__br', Br), fw.var('g_backlight_status', C.c_ubyte)
    br.page, light.value = 0x5000, 1
    lib.poll(state=b'state=none\n')
    lib.poll(ms=200000)
    assert lib.sp.launched == 1
    light.value = 0
    lib.poll(ms=149500)
    assert lib.sp.launched == 1
    lib.poll()
    order = [n for n, _ in fw.calls() if n in ('window_close', 'fw_system')][-2:]
    assert order == ['window_close', 'fw_system'] and fw.calls('window_close')[-1][1][0] == 0x5000 and lib.stopped() == lib.ran[-1:]


@pytest.mark.parametrize('loading, log, kills', [(1, b'x\nerror audio key\n', True), (1, b'ok\n', False), (0, b'error audio key\n', False)])
def test_a_refused_key_while_a_play_loads_kills_librespot_for_its_restart(fw, loading, log, kills):
    lib = Librespot(fw)
    lib.card.files[LOG] = log
    lib.sp.loading = loading
    lib.poll(state=b'state=none\n')
    assert ('killall -9 librespot aplay' in lib.ran) == kills and lib.sp.loading == (3 if kills else loading)


def test_a_scrub_left_alone_seeks_there(fw):
    lib = Librespot(fw)
    ui = fw.var('spotify__ui', Ui)
    ui.scrub, ui.scrub_ms, ui.scrub_at = 1, 42000, lib.clock.now
    lib.poll(ms=SCRUB_MS - 500)
    assert lib.sent == [] and ui.scrub == 1
    lib.poll()
    assert lib.sent == [(SOCK, b'S42000')] and ui.scrub == 0


# spot_output: the output aplay.sh plays to.

VOLUME = 40


def outputs(fw, way, mono=False):
    fw.mock('mclGetOutputWay', lambda *_: way)
    fw.var('g_volume', C.c_ubyte).value = VOLUME
    def read(path, section, key, out, default, *_):
        v = b'1' if mono and C.string_at(key) == b'MONO' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)


@pytest.mark.parametrize('way, mono, line', [
    (1, False, 'plug:bluealsa {gain}\n'),  # Bluetooth, at the headset's gain
    (2, False, 'usb h{volume}\n'),         # a USB DAC that applies the volume itself
    (0, True, 'plughw:0,0 h{volume}\n'),   # Mono Audio on the DAC: q2video mixes it
])
def test_the_output_in_use_is_written_for_aplay_sh_and_the_sink_told(fw, way, mono, line):
    lib = Librespot(fw)
    outputs(fw, way, mono)
    gain = fw.fn('headset_gain', C.c_int)()
    lib.poll()
    assert lib.card.files.get(OUT) == line.format(gain=gain, volume=VOLUME).encode() and OUT + '.tmp' not in lib.card.files
    assert lib.sent == [(SINK, b'v' + bytes([gain]))]
    lib.poll()  # the same: nothing written or sent again
    assert len(lib.sent) == 1 and len(fw.calls('fw_rename')) == 1


def test_back_on_the_dac_the_output_file_goes(fw):
    lib = Librespot(fw)
    outputs(fw, 1)
    lib.poll()
    outputs(fw, 0)
    lib.poll()
    assert OUT not in lib.card.files and len(lib.sent) == 2 and lib.sent[1][0] == SINK


def test_on_the_dac_from_the_start_there_is_nothing_to_write(fw):
    lib = Librespot(fw)
    outputs(fw, 0)
    lib.poll()
    assert OUT not in lib.card.files and lib.sent == [] and not fw.calls('fw_unlink')


# Now Playing.

TRACK = dict(track=b'spotify:track:1', state=b'playing', title=b'Title', artist=b'Artist', album=b'Album',
             duration=200000, position=5000, playing=1)


def minutes(t, seconds, *_):
    """Stock's toolsTimeItoa: seconds as m:ss."""
    s = b'%d:%02d' % divmod(C.c_int(seconds).value, 60)
    C.memmove(t, s + b'\0', len(s) + 1)


class Np:
    """Now Playing as the browser's row opens it (spot_np): Widgets' blocks, the timers, the clock, stock's m:ss,
    the datagrams sent to librespot."""
    def __init__(self, fw, **sp):
        self.fw, self.w, self.clock, self.timers = fw, Widgets(fw), Clock(fw), Timers(fw)
        self.sp, self.ui, self.sent = fw.var('spotify__sp', Sp), fw.var('spotify__ui', Ui), []
        fw.mock('toolsTimeItoa', minutes)
        fw.mock('fw_socket', lambda *_: 9)
        fw.mock('fw_sendto', lambda s, data, n, flags, to, *_: self.sent.append((C.string_at(to + 2), C.string_at(data, n))) or n)
        fw.mock('fw_fork', lambda *_: -1)  # no ffmpeg for a cover stock's thumbnailer refused
        fw.mock('fw_access', lambda *_: -1)
        self.light = fw.var('g_backlight_status', C.c_ubyte)
        self.light.value = 1
        self.sp.launched, self.sp.auth, self.sp.at, self.sp.load_at = 1, 1, self.clock.now, self.clock.now
        for k, v in sp.items():
            setattr(self.sp, k, v)
        fw.fn('spotify__spot_np', None)()

    def tick(self, ms=0):
        self.clock.now += ms
        return self.timers.run('tick')

    def times(self):
        return self.w.text.get(self.ui.elapsed), self.w.text.get(self.ui.remain)

    def labels(self):
        return [self.w.text.get(w) for w in (self.ui.title, self.ui.artist, self.ui.album)]

    def note(self):
        return self.w.text.get(self.ui.note), self.w.text.get(self.ui.sub)

    def key(self, key):
        return self.fw.fn('spot_key', C.c_int, C.c_void_p, C.c_uint)(self.ui.page, key)

    def keyup(self, key):
        return handler(self.fw, self.ui.page, EVT_KEY_UP)(C.addressof(key_event(key)))

    def paint(self, w, canvas):
        self.fw.fn('spot_paint', None, C.c_void_p, C.c_void_p)(w, canvas.address)

    def invalidated(self, w):
        return len([a for _, a in self.fw.calls('widget_invalidate_force') if a[0] == w])

    def images(self, w):
        return [C.string_at(a[1]).decode() for _, a in self.fw.calls('image_base_set_image') if a[0] == w]


def test_a_playing_track_shows_its_labels_and_times(fw):
    np = Np(fw, **TRACK)
    assert np.w.shown[np.ui.info] == 1 and np.w.shown[np.ui.msg] == 0
    assert np.labels() == ['Title', 'Artist', 'Album'] and np.times() == ('0:05', '-3:15')
    assert len(np.timers.of('tick')) == 1  # what it does: test_the_page_s_timer_refreshes_it_only_with_the_screen_on


def test_the_time_runs_on_from_at_while_playing_to_the_end_and_stands_paused(fw):
    np = Np(fw, **TRACK)
    np.tick(2000)
    assert np.times() == ('0:07', '-3:13')
    np.tick(500000)
    assert np.times() == ('3:20', '-0:00')
    np.sp.playing = 0
    np.tick(2000)
    assert np.times() == ('0:05', '-3:15')  # the position as librespot last wrote it


def test_the_remaining_time_is_whole_seconds_less_whole_seconds(fw):
    np = Np(fw, **TRACK | dict(position=5999, duration=10001, playing=0))
    assert np.times() == ('0:05', '-0:05')


def test_labels_and_times_are_written_only_when_they_change(fw):
    np = Np(fw, **TRACK)
    writes = len(np.w.writes)
    np.tick(300)  # still the 5th second
    assert len(np.w.writes) == writes
    np.tick(1000)
    assert set(np.w.writes[writes:]) == {np.ui.elapsed, np.ui.remain}
    writes = len(np.w.writes)
    np.sp.title = b'Next'
    np.tick()
    assert np.w.writes[writes:] == [np.ui.title] and np.labels()[0] == 'Next'


# Why the page shows no track, in spot_refresh's order: the first that holds is the one it gives. Each is laid over
# a playing track's state, signed in, and names what it needs to hold.
WHY: dict[str, dict[str, object]] = {
       'missing': dict(launched=0, track=b''),        # librespot isn't on the card
       'pairing': dict(auth=-1),
       'refused': dict(loading=3),                    # Spotify refused the account's keys
       'timed out': dict(loading=2, state=b'none'),   # the play's wait ran out
       'loading': dict(loading=1),
       'old': dict(old=1, track=b''),                 # an add-on too old to browse
       'idle': dict(auth=1, track=b''),               # signed in, nothing playing
       'waiting': dict(auth=0, track=b'')}            # nothing heard from librespot yet
WORD = {'missing': "isn't", 'pairing': 'phone', 'refused': 'refused', 'timed out': "didn't", 'loading': 'Starting',
        'old': 'Update', 'idle': 'Nothing', 'waiting': 'Waiting'}  # one a reason, so no two are swapped


def why(np, **sp):
    """The page's note and sub, refreshed with sp over a signed-in playing track."""
    for k, v in (TRACK | dict(launched=1, auth=1, loading=0, old=0) | sp).items():
        setattr(np.sp, k, v)
    np.tick()
    return np.note()


@pytest.mark.parametrize('reason', WHY)
def test_without_a_track_the_page_says_why_instead(fw, reason):
    np = Np(fw, **TRACK | WHY[reason])
    assert np.w.shown[np.ui.info] == 0 and np.w.shown[np.ui.msg] == 1 and WORD[reason] in ' '.join(np.note())


def test_each_reason_reads_differently(fw):
    np = Np(fw, **TRACK)
    notes = {reason: why(np, **sp) for reason, sp in WHY.items()}
    assert len(set(notes.values())) == len(WHY)
    assert why(np, state=b'none') == notes['idle']  # a session without a track plays nothing either


def test_the_first_reason_that_holds_is_the_one_the_page_gives(fw):
    np = Np(fw, **TRACK)
    reasons, pairs = list(WHY.values()), 0
    for i, first in enumerate(reasons):
        alone = why(np, **first)
        for later in reasons[i + 1:]:
            if all(first.get(k, v) == v for k, v in later.items()):  # both can hold at once
                pairs += 1
                assert why(np, **(later | first)) == alone, (first, later)
    assert pairs > len(WHY)


def test_a_play_waits_15_s_then_says_so_until_any_track_plays(fw):
    np = Np(fw, **TRACK | dict(loading=1, state=b'stopped', wanted=b'2', from_=b'spotify:track:1'))
    loading = np.note()
    np.tick(14999)
    assert np.note() == loading and np.sp.loading == 1
    np.tick(1)
    assert np.sp.loading == 2 and np.note() != loading and WORD['timed out'] in np.note()[0]
    np.sp.state = b'playing'  # the track from before: after the wait, any track
    np.tick(250)
    assert np.sp.loading == 0 and np.w.shown[np.ui.info] == 1 and np.labels()[0] == 'Title'


class Thumbs:
    """Stock's thumbnailer (toolsThumbSpecCover): what it was asked, answering ok."""
    def __init__(self, fw, ok=1):
        self.ok, self.asked = ok, []
        fw.mock('toolsThumbSpecCover', lambda src, dst, w, h, *_: self.asked.append((C.string_at(src), C.string_at(dst), w, h)) or self.ok)


def test_the_cover_is_fitted_to_the_art_once_a_track(fw):
    thumbs = Thumbs(fw)
    np = Np(fw, **TRACK)
    placeholder = np.images(np.ui.art)
    width(np.ui.art, 166)
    np.sp.cover = 1
    np.tick()
    np.tick()
    (src, dst, w, h), = thumbs.asked
    assert src == STATE.encode() + b'.jpg' and w == h == 166  # librespot's cover, square at the art's width
    assert np.images(np.ui.art)[-1] == 'file://' + dst.decode()
    np.sp.track = b'spotify:track:2'
    np.tick()
    assert len(thumbs.asked) == 2
    np.sp.cover = 0  # the next track's cover not yet written
    np.tick()
    assert np.images(np.ui.art)[-1] == placeholder[-1] and np.ui.art_track == b''


def test_a_cover_that_fails_is_tried_on_the_next_3_ticks_then_left(fw):
    thumbs = Thumbs(fw, ok=0)
    np = Np(fw, **TRACK | dict(cover=1))
    for _ in range(6):
        np.tick()
    assert len(thumbs.asked) == 4 and np.images(np.ui.art)[-1] == np.images(np.ui.art)[0]
    np.sp.track = b'spotify:track:2'
    np.tick()
    assert len(thumbs.asked) == 5


def test_the_bar_repaints_only_when_its_width_or_the_scrub_s_colour_moves(fw):
    px = 200000 / SPOT_BAR_W  # a pixel's ms
    np = Np(fw, **TRACK | dict(position=int(8.3 * px), playing=0))
    width(np.ui.bar, SPOT_BAR_W)
    np.tick()
    painted = np.invalidated(np.ui.bar)
    np.tick()
    np.sp.position += int(0.5 * px)  # 8.8 px: the same 8 drawn
    np.tick()
    assert np.invalidated(np.ui.bar) == painted
    np.sp.position += int(0.5 * px)
    np.tick()
    assert np.invalidated(np.ui.bar) == painted + 1
    np.ui.scrub, np.ui.scrub_ms = 1, np.sp.position
    np.tick()
    assert np.invalidated(np.ui.bar) == painted + 2


def test_the_glyph_repaints_when_playing_changes(fw):
    np = Np(fw, **TRACK)
    glyph = np.invalidated(np.ui.glyph)
    np.tick()
    assert np.invalidated(np.ui.glyph) == glyph
    np.sp.playing = 0
    np.tick()
    assert np.invalidated(np.ui.glyph) == glyph + 1


def test_the_page_s_timer_refreshes_it_only_with_the_screen_on(fw):
    np = Np(fw, **TRACK)
    np.light.value = 0
    assert np.tick(3000) == RET_REPEAT and np.times() == ('0:05', '-3:15')
    np.light.value = 1
    assert np.tick() == RET_REPEAT and np.times() == ('0:08', '-3:12')


# The bar and glyph's paint.

class Canvas:
    """A canvas on an LCD with its own fill colour: what is drawn, in order, each in the colour it's in."""
    def __init__(self, fw, fill=0xff102030, rounded=True, lcd=True):
        self.lcd, self.block = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        C.c_uint.from_address(C.addressof(self.lcd) + O['LCD_FILL_COLOR']).value = fill
        if lcd:
            C.c_void_p.from_address(C.addressof(self.block) + O['CANVAS_LCD']).value = C.addressof(self.lcd)
        self.address, self.fill, self.rounded, self.colour, self.drawn = C.addressof(self.block), fill, rounded, None, []
        fw.mock('canvas_fill_rounded_rect', self.round_rect)
        fw.mock('canvas_set_fill_color', lambda c, colour, *_: setattr(self, 'colour', colour & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, *box: self.drawn.append(('rect', *(C.c_int(v).value for v in box[:4]), self.colour)))

    def round_rect(self, c, r, g, colour, radius, *_):
        if not self.rounded:
            return 1
        box = C.cast(r, C.POINTER(C.c_int))
        self.drawn.append(('round', box[0], box[1], box[2], box[3], C.c_uint.from_address(colour).value, C.c_int(radius).value))
        return 0

    def boxes(self):
        return [d[1:6] for d in self.drawn]


def test_the_bar_is_the_track_with_its_played_part_over_it(fw):
    np = Np(fw, **TRACK | dict(position=100000, playing=0))
    canvas = Canvas(fw)
    np.paint(np.ui.bar, canvas)
    track, played = canvas.drawn
    h = track[4]
    assert track[:5] == ('round', 0, 0, SPOT_BAR_W, h) and played[:5] == ('round', 0, 0, 100000 * SPOT_BAR_W // 200000, h)
    assert played[5] != track[5] and track[6] == played[6] == h // 2  # capsules: round ends
    assert canvas.colour == canvas.fill  # the LCD's fill as stock left it


def test_a_scrub_paints_the_played_part_in_another_colour(fw):
    np = Np(fw, **TRACK | dict(position=100000, playing=0))
    canvas = Canvas(fw)
    np.paint(np.ui.bar, canvas)
    track, playing = canvas.drawn
    np.ui.scrub, np.ui.scrub_ms = 1, 100000
    canvas.drawn = []
    np.paint(np.ui.bar, canvas)
    scrub = canvas.drawn[1]
    assert scrub[:5] == playing[:5] and scrub[5] not in (playing[5], track[5])


@pytest.mark.parametrize('position, dot', [(0, False), (700, True)])
def test_the_played_part_starts_as_a_round_dot_and_nothing_before_it(fw, position, dot):
    np = Np(fw, **TRACK | dict(position=position, playing=0))
    canvas = Canvas(fw)
    np.paint(np.ui.bar, canvas)
    h = canvas.drawn[0][4]
    assert canvas.boxes()[1:] == ([(0, 0, h, h, canvas.drawn[-1][5])] if dot else [])


def test_without_rounded_rectangles_the_bar_is_plain_ones_of_the_same_boxes(fw):
    np = Np(fw, **TRACK | dict(position=100000, playing=0))
    rounded = Canvas(fw)
    np.paint(np.ui.bar, rounded)
    plain = Canvas(fw, rounded=False)
    np.paint(np.ui.bar, plain)
    assert {d[0] for d in plain.drawn} == {'rect'} and plain.boxes() == rounded.boxes() and plain.colour == plain.fill


def test_the_glyph_is_a_triangle_playing_and_two_bars_paused(fw):
    np = Np(fw, **TRACK)
    canvas = Canvas(fw)
    np.paint(np.ui.glyph, canvas)
    columns = [d[1:5] for d in canvas.drawn]
    tallest = columns[0][3]
    assert len(columns) > 2 and all(a[0] + a[2] == b[0] for a, b in zip(columns, columns[1:]))  # side by side
    assert [h for *_, h in columns] == sorted({h for *_, h in columns}, reverse=True)  # narrowing to a point
    assert {2 * y + h for _, y, _, h in columns} == {tallest}  # about one middle row
    np.sp.playing = 0
    canvas.drawn = []
    np.paint(np.ui.glyph, canvas)
    bars = [d[1:5] for d in canvas.drawn]
    assert len(bars) == 2 and {(y, h) for _, y, _, h in bars} == {(0, tallest)} and bars[0][0] + bars[0][2] < bars[1][0]
    assert canvas.colour == canvas.fill


def test_paint_draws_on_the_open_page_s_bar_and_glyph_only(fw):
    np = Np(fw, **TRACK)
    canvas = Canvas(fw)
    np.paint(np.ui.title, canvas)
    np.paint(0, canvas)
    np.paint(np.ui.bar, Canvas(fw, lcd=False))
    assert canvas.drawn == []
    bar = np.ui.bar
    handler(fw, np.ui.page, EVT_DESTROY)()
    canvas = Canvas(fw)
    np.paint(bar, canvas)
    assert canvas.drawn == [] and canvas.colour is None


def test_classic_s_page_takes_the_local_now_playing_s_places(fw):
    classic(fw)
    np = Np(fw, **TRACK)
    name = np.w.kids[np.ui.info][0]  # the label over the art naming the page
    assert np.w.text.get(name) and np.ui.ipod == 1 and np.w.shown[name] == 0 and np.w.shown[np.ui.glyph] == 0  # the title bar names it


def test_classic_s_bar_carries_the_scrub_s_cue_at_the_played_end(fw):
    classic(fw)
    np = Np(fw, **TRACK | dict(position=100500, playing=0))
    width(np.ui.bar, 300)
    canvas = Canvas(fw)
    np.paint(np.ui.bar, canvas)
    plain = list(canvas.drawn)
    np.ui.scrub, np.ui.scrub_ms = 1, 100500
    canvas.drawn = []
    np.paint(np.ui.bar, canvas)
    mark = O['NP_SCRUB_MARK']
    cue = [d for d in canvas.drawn if d not in plain]
    widest = [d for d in cue if d[3] == 2 * mark + 1]
    assert len(cue) == 2 * mark + 1 and len(widest) == 1 and widest[0][1] + mark == 100500 * 300 // 200000
    assert not [d for d in plain if d[0] == 'round'] and canvas.colour == canvas.fill


# Keys on the page.

def test_two_centre_presses_scrub_the_wheel_moves_it_and_a_third_seeks(fw):
    np = Np(fw, **TRACK | dict(playing=0))
    assert np.key(KEY_CENTER) == 1 and np.ui.scrub == 0  # a single press, maybe: wait for a second
    np.clock.now += DOUBLE_CLICK_MS - 1
    assert np.key(KEY_CENTER) == 1 and np.ui.scrub == 1 and np.ui.scrub_ms == 5000
    assert np.key(KEY_NEXT) == 1 and np.ui.scrub_ms == 10000 and np.times()[0] == '0:10'
    for _ in range(3):
        np.key(KEY_PREV)
    assert np.ui.scrub_ms == 0
    for _ in range(50):
        np.key(KEY_NEXT)
    assert np.ui.scrub_ms == 200000 and np.sent == []
    assert np.key(KEY_CENTER) == 1 and np.sent == [(SOCK, b'S200000')] and np.ui.scrub == 0
    timers = len(np.timers.added)
    np.clock.now += 100
    assert np.key(KEY_CENTER) == 1 and len(np.timers.added) == timers  # a quick second press: swallowed, no single


@pytest.mark.parametrize('lit', [1, 0])
def test_a_single_centre_press_replays_its_release_to_stock_lit(fw, lit):
    np = Np(fw, **TRACK)
    released = []
    fw.mock('on_wm_keyup_fun', lambda w, event, *_: released.append(C.c_int.from_address(event + O['EVENT_KEY']).value))
    np.key(KEY_CENTER)
    assert np.timers.of('spot_single') == [DOUBLE_CLICK_MS]
    np.light.value = lit
    np.timers.run('spot_single')
    assert released == ([KEY_CENTER] if lit else [])  # stock then turns the screen off


@pytest.mark.parametrize('sp', [dict(loading=1), dict(auth=-1), dict(track=b''), dict(duration=0)])
def test_without_a_track_to_scrub_the_keys_are_stock_s(fw, sp):
    np = Np(fw, **TRACK | sp)
    assert np.key(KEY_CENTER) == 0 and np.key(KEY_NEXT) == 0 and np.timers.of('spot_single') == []


def test_the_wheel_without_a_scrub_and_another_page_s_keys_are_stock_s(fw):
    np = Np(fw, **TRACK)
    assert np.key(KEY_NEXT) == 0 and np.key(KEY_PLAY) == 0
    assert fw.fn('spot_key', C.c_int, C.c_void_p, C.c_uint)(np.ui.page + 8, KEY_CENTER) == 0


def test_return_seeks_a_scrub_first_then_goes_back(fw):
    np = Np(fw, **TRACK | dict(playing=0))
    np.key(KEY_CENTER)
    np.key(KEY_CENTER)
    np.key(KEY_NEXT)
    assert np.keyup(KEY_RETURN) == RET_STOP and np.sent == [(SOCK, b'S10000')] and np.ui.scrub == 0
    assert not np.timers.of('leave') and not fw.calls('navigator_back')
    assert np.keyup(KEY_RETURN) == RET_STOP
    np.timers.run('leave')
    assert len(fw.calls('navigator_back')) == 1 and np.ui.leave == 0
    assert np.keyup(KEY_CENTER) == 0


def test_closing_seeks_a_pending_scrub_and_forgets_the_page(fw):
    np = Np(fw, **TRACK | dict(playing=0))
    art = fw.fn('spot_art', C.c_void_p)
    assert art() == np.ui.art
    np.key(KEY_CENTER)
    np.key(KEY_CENTER)
    timer = np.ui.timer
    handler(fw, np.ui.page, EVT_DESTROY)()
    assert np.sent == [(SOCK, b'S5000')] and timer in np.timers.removed
    assert bytes(np.ui) == bytes(C.sizeof(Ui)) and art() is None


def test_the_session_stays_with_a_track_and_moves_with_it(fw):
    sp, session = fw.var('spotify__sp', Sp), fw.fn('spot_session', C.c_uint)
    sp.track = b'spotify:track:1'
    first = session()
    assert session() == first
    sp.track = b'spotify:track:2'
    assert session() != first
    sp.track = b'spotify:track:1'
    assert session() == first


def test_a_play_or_pause_asked_shows_at_once_then_librespot_s_own_word(fw):
    lib = Librespot(fw)
    status, set_playing = fw.fn('spot_status', C.c_int), fw.fn('spot_set_playing', None, C.c_int)
    set_playing(0)
    assert status() == 0 and lib.sent == []  # not the active source: nothing to ask
    lib.sp.active, lib.sp.playing = 1, 1
    set_playing(1)
    assert status() == 2 and lib.sent == []  # as it is
    set_playing(0)
    assert lib.sent == [(SOCK, b's')] and status() == 3  # paused before the state file says so
    set_playing(0)
    lib.clock.now += 999
    assert len(lib.sent) == 1 and status() == 3
    lib.clock.now += 1
    assert status() == 2  # a second on, the state file's word: it still plays
    lib.sp.playing = 0
    set_playing(1)
    assert lib.sent[1:] == [(SOCK, b'P')] and status() == 2


# The browser.

class Browser:
    """Spotify's browser as the UI drives it, on stubs, over Widgets' tree (test_streaming's drives the same page on
    one block, with no rows to read back). The worker's pthread never runs: answer() puts its reply where the pthread
    would and runs the page's tick; click(i) runs row i's own click handler, i in show()'s order, and the go() timer
    it arms. captions(), details() and opens() are the list as last drawn, w.title its title."""
    def __init__(self, fw):
        self.fw, self.w, self.clock, self.timers = fw, Widgets(fw), Clock(fw), Timers(fw)
        self.sel, self.toasts = 0, []
        self.sp, self.br, self.wk = fw.var('spotify__sp', Sp), fw.var('spotify__br', Br), fw.var('spotify__wk', Wk)
        self.light, self.on_top = fw.var('g_backlight_status', C.c_ubyte), True
        self.light.value = 1
        fw.mock('fw_pthread_create', lambda *_: 0)
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('widget_get_prop_int', lambda w, name, default, *_: self.sel if C.string_at(name) == b'_ringnav_index' else default)
        fw.mock('window_manager_get_top_window', lambda *_: self.br.page if self.on_top else 0x1234)
        fw.fn('spot_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    @property
    def top(self):
        return self.br.lv[self.br.depth - 1]

    def request(self):
        return C.string_at(self.wk.buf)[:self.wk.len] if self.wk.buf else b''

    def tick(self, ms=0):
        self.clock.now += ms
        self.fw.fn('spotify__br_tick', C.c_int, C.c_void_p)(None)

    def answer(self, status=b'ok', msg=b'', cursor=b'', rows=(), fail=0, raw=None):
        """The reply to the request in flight (a Q2SPOT/1 line for each of rows), or raw with the worker's fail,
        and the tick that takes it."""
        data = raw if raw is not None else \
            b'Q2SPOT/1\n%d\t%s\t%s\t%s\n' % (self.wk.id, status, msg, cursor) + b''.join(b'\t'.join(r) + b'\n' for r in rows)
        C.memmove(self.wk.buf, data + b'\0', len(data) + 1)
        self.wk.n, self.wk.fail, self.wk.done = len(data), fail, 1
        self.tick()

    def menu(self, caps=()):
        assert self.request().endswith(b'\taccount\n')
        self.answer()
        assert self.request().endswith(b'\tcaps\n')
        self.answer(rows=[(b'op', c, b'', b'', b'', b'') for c in caps])

    def captions(self):
        return [r[0] if r else '' for r in self.w.rows()]

    def details(self):
        return [r[1] if len(r) > 1 else None for r in self.w.rows()]

    def opens(self):
        """Each row: whether it carries the chevron of a row that opens a level (page_row_detail_into)."""
        return [any(k in self.w.images for k in self.w.kids.get(item, [])) for item in self.w.items]

    def listing(self):
        return list(zip(self.captions(), self.details(), self.opens()))

    def click(self, i):
        self.sel = i
        handler(self.fw, self.w.items[i], EVT_CLICK)()
        self.timers.run('go')

    def on(self, w, event):
        handler(self.fw, w, event)()

    def key(self, key):
        return handler(self.fw, self.br.page, EVT_KEY_UP)(C.addressof(key_event(key)))

    def back(self):
        assert self.key(KEY_RETURN) == RET_STOP
        self.timers.run('br_leave')


# The menu's rows without a track, in show()'s order: MENU less Now Playing, Queue and Playback Settings. With the
# Downloads hub, Downloaded Tracks' row goes and Downloads takes its place (HUB).
LIKED, PLAYLISTS, ALBUMS, ARTISTS, SEARCH, ACCOUNT, DOWNLOADED, DOWNLOADS = range(8)
HUB = DOWNLOADED


def test_search_lays_its_box_over_the_menu_and_opens_the_term_s_categories(fw):
    b = Browser(fw)
    b.menu()
    b.click(SEARCH)
    edit = b.br.edit
    assert edit and not b.br.view and (edit, 1) in [a[:2] for _, a in fw.calls('widget_set_focused')]
    b.w.text[edit] = 'AC%DC'
    b.on(edit, EVT_IM_ACTION)
    b.on(edit, EVT_BLUR)  # the keyboard shuts after its OK: the search stands
    b.timers.run('go')
    assert b.w.title == 'AC%DC' and b.opens() == [True] * 4 and not b.br.edit  # the term over its four categories
    albums = b.captions()[1]
    b.click(1)  # tracks, albums, artists, playlists
    assert b.request().endswith(b'\tsearch\talbums\tAC%25DC\n') and b.top.title.decode() == albums and b.top.kind == L_LIST


def test_the_keyboard_shut_with_text_searches_too(fw):
    b = Browser(fw)
    b.menu()
    b.click(SEARCH)
    b.w.text[b.br.edit] = 'x'
    b.on(b.br.edit, EVT_VALUE_CHANGED)
    b.timers.run('go')
    assert b.w.title == 'x' and b.opens() == [True] * 4


@pytest.mark.parametrize('event', [EVT_IM_ACTION, EVT_BLUR], ids=['ok', 'blur'])
def test_an_empty_search_or_the_box_left_shows_the_menu_as_it_was(fw, event):
    b = Browser(fw)
    b.menu()
    menu, asked = b.listing(), b.wk.id
    b.click(SEARCH)
    b.w.text[b.br.edit] = ''
    b.on(b.br.edit, event)
    b.timers.run('go')
    assert b.listing() == menu and b.br.depth == 1 and b.wk.id == asked and not b.br.edit


def test_return_in_the_search_box_empties_and_shuts_it_without_leaving(fw):
    b = Browser(fw)
    b.menu()
    b.click(SEARCH)
    edit = b.br.edit
    b.w.text[edit] = 'abc'
    b.back()
    assert b.w.text[edit] == '' and (edit, 0) in [a[:2] for _, a in fw.calls('widget_set_focused')]
    assert b.br.depth == 1 and not fw.calls('navigator_back')


def test_return_goes_back_a_level_then_from_the_menu_to_streaming(fw):
    b = Browser(fw)
    b.menu()
    menu = b.listing()
    b.click(ALBUMS)
    assert b.br.depth == 2 and b.request().endswith(b'\talbums\n')
    b.back()
    assert b.br.depth == 1 and b.listing() == menu and not fw.calls('navigator_back')
    b.back()
    assert len(fw.calls('navigator_back')) == 1
    assert b.key(KEY_PLAY) == 0


@pytest.mark.parametrize('caps, row, opens', [
    ([], DOWNLOADS, [True, False]),                 # Downloaded Tracks, and a note to update the add-on
    ([b'download'], DOWNLOADS, [True, False, False]),  # and the latest download's status between
    (ALL_CAPS, HUB, [True] * 4),                    # the hub: saved playlists, tracks, progress and storage
])
def test_downloads_offers_what_the_add_on_can_do(fw, caps, row, opens):
    b = Browser(fw)
    b.menu(caps)
    assert len(b.captions()) == row + 1 and b.opens()[row]  # Downloads, the last row
    downloads = b.captions()[row]
    b.click(row)
    assert b.top.kind == L_DOWNLOADS and b.w.title == downloads and b.opens() == opens


def test_an_older_add_on_s_download_status_is_toasted(fw):
    b = Browser(fw)
    b.menu([b'download'])
    b.click(DOWNLOADS)
    b.click(1)  # the latest download's status
    assert b.request().endswith(b'\tdownloadstatus\n') and b.br.act == A_DOWNLOAD
    b.answer(msg=b'3 of 12 tracks')
    assert b.toasts == ['3 of 12 tracks'] and b.br.depth == 2
    asked = b.wk.id
    b.click(2)  # the note to update the add-on: nothing to send
    assert b.wk.id == asked
    b.click(0)  # Downloaded Tracks
    assert b.top.op == b'downloaded' and b.request().endswith(b'\tdownloaded\n')


def test_without_the_status_op_the_update_note_does_nothing(fw):
    b = Browser(fw)
    b.menu()
    b.click(DOWNLOADS)
    asked = b.wk.id
    b.click(1)  # the note, second of two
    assert b.wk.id == asked and b.br.depth == 2


REMOVE_PLAYLISTS, REMOVE_TRACKS, CLEAR_ALL = range(3)  # Storage Management's rows


def storage(fw, which):
    b = Browser(fw)
    b.menu(ALL_CAPS)
    b.click(HUB)
    b.click(3)  # Storage Management
    assert b.top.kind == L_STORAGE and b.opens() == [True, True, False]  # two selections, then Clear All
    b.click(which)
    return b


def test_tracks_are_selected_previewed_and_removed_once_confirmed(fw):
    b = storage(fw, REMOVE_TRACKS)
    assert b.top.kind == L_SELECT and b.request().endswith(b'\tdownloaded\n')
    b.answer(rows=[track(1, b''), track(2, b''), track(3, b'')])
    assert b.captions()[:3] == ['Song 1', 'Song 2', 'Song 3'] and len(b.captions()) == 4  # and Remove Selected…
    unselected = b.details()[0]
    assert b.details()[:3] == [unselected] * 3
    for i in (0, 2, 0):
        b.click(i)
    selected = b.details()[2]
    assert b.details()[:3] == [unselected, unselected, selected] and selected != unselected
    assert b.br.selected == b'spotify:track:3'
    b.click(3)  # Remove Selected…
    assert b.request().endswith(b'\tremove\ttracks\tspotify:track:3\n') and b.br.act == A_PREVIEW  # a count, no deletion
    b.answer(msg=b'1 track; 2 playlists become incomplete')
    assert b.top.kind == L_REMOVE and b.captions()[0] == '1 track; 2 playlists become incomplete' and len(b.captions()) == 3
    b.click(2)  # Remove, after Cancel
    assert b.request().endswith(b'\tremove\ttracks\tspotify:track:3\tconfirm\n') and b.br.act == A_REMOVE
    b.answer(msg=b'Removed 1 track')
    assert b.toasts == ['Removed 1 track'] and b.top.kind == L_STORAGE and b.br.selected == b''


def test_playlists_are_removed_by_their_ids_comma_separated(fw):
    b = storage(fw, REMOVE_PLAYLISTS)
    assert b.top.kind == L_SELECT and b.request().endswith(b'\tcollections\n')
    b.answer(rows=[(b'collection', b'liked', b'Liked Songs', b'50 of 50', b'', b''),
                   (b'collection', b'spotify:playlist:9', b'Road Trip', b'3 of 40', b'', b'')])
    b.click(0)
    b.click(1)
    b.click(2)  # Remove Selected…
    assert b.request().endswith(b'\tremove\tcollections\tliked,spotify:playlist:9\n')


def test_cancel_sends_nothing_and_keeps_the_selection(fw):
    b = storage(fw, REMOVE_TRACKS)
    b.answer(rows=[track(1, b''), track(2, b'')])
    b.click(1)
    picked = b.details()[:2]
    b.click(2)  # Remove Selected…
    b.answer(msg=b'1 track')
    asked = b.wk.id
    b.click(1)  # Cancel
    assert b.wk.id == asked and b.top.kind == L_SELECT and b.details()[:2] == picked and picked[0] != picked[1]


def test_nothing_selected_asks_for_a_selection(fw):
    b = storage(fw, REMOVE_TRACKS)
    b.answer(rows=[track(1, b'')])
    asked = b.wk.id
    b.click(1)  # Remove Selected…
    assert len(b.toasts) == 1 and b.wk.id == asked and b.top.kind == L_SELECT


def test_a_full_selection_asks_for_these_to_go_first(fw):
    b = storage(fw, REMOVE_TRACKS)
    b.answer(rows=[track(1, b'')])
    full = b'x' * (Br.selected.size - 10)
    b.br.selected = full
    b.click(0)
    assert len(b.toasts) == 1 and b.br.selected == full


def test_clear_all_previews_every_download_then_removes_them(fw):
    b = storage(fw, CLEAR_ALL)
    assert b.request().endswith(b'\tremove\tall\t\n') and b.br.act == A_PREVIEW
    b.answer(msg=b'All 120 tracks')
    assert b.top.kind == L_REMOVE and b.captions()[0] == 'All 120 tracks'
    b.click(2)  # Remove
    assert b.request().endswith(b'\tremove\tall\t\tconfirm\n')


def test_a_saved_playlist_plays_from_its_manifest_and_has_its_download_controls(fw):
    b = Browser(fw)
    b.menu(ALL_CAPS)
    b.click(HUB)
    b.click(0)  # Downloaded Playlists
    assert b.request().endswith(b'\tcollections\n')
    b.answer(rows=[(b'collection', b'liked', b'Liked Songs', b'50 of 50', b'', b'')])
    assert b.opens() == [True]
    b.click(0)
    assert b.top.op == b'collection' and b.top.title == b'Liked Songs' and b.request().endswith(b'\tcollection\tliked\n')
    b.answer(rows=[(b'track', b'spotify:track:7', b'Seven', b'A', b'liked', b'51'),
                   (b'missing', b'spotify:track:8', b'Eight', b'Unavailable', b'liked', b'52')])
    assert b.captions()[:2] == ['Seven', 'Eight'] and b.opens() == [False, False, True]  # and its download controls
    b.click(0)
    assert b.request().endswith(b'\tplaycollection\tliked\t51\n') and b.br.act == A_PLAY and b.sp.wanted == b'7'
    b.answer(status=b'error', msg=b'Not downloaded')
    controls = b.captions()[2]
    b.click(2)
    assert b.top.kind == L_JOB and b.w.title == controls and len(b.captions()) == 5
    b.click(3)  # pause, resume, cancel, retry, update
    assert b.request().endswith(b'\tjob\tliked\tretry\n') and b.br.act == A_DOWNLOAD
    b.answer(msg=b'Retrying 1')
    assert b.toasts == ['Not downloaded', 'Retrying 1']


def test_a_job_opens_its_controls_under_its_collection_s_name(fw):
    b = Browser(fw)
    b.menu(ALL_CAPS)
    b.click(HUB)
    b.click(2)  # Download Progress
    assert b.request().endswith(b'\tjobs\n')
    b.answer(rows=[(b'job', b'spotify:playlist:4', b'Paused', b'12 of 40', b'Road Trip', b'12')])
    assert b.captions() == ['Paused'] and b.details() == ['12 of 40'] and b.opens() == [True]
    b.click(0)
    assert b.w.title == 'Road Trip' and b.top.kind == L_JOB
    b.click(1)  # pause, resume, …
    assert b.request().endswith(b'\tjob\tspotify:playlist:4\tresume\n')


@pytest.mark.parametrize('caps, offered', [(ALL_CAPS, True), ([b'download'], False)])
def test_liked_songs_downloads_whole_with_saved_collections(fw, caps, offered):
    b = Browser(fw)
    b.menu(caps)
    b.click(LIKED)
    assert b.request().endswith(b'\tliked\n')
    b.answer(rows=[track(1)])
    assert len(b.captions()) == 1 + offered  # its track, and Download Liked Songs
    if offered:
        b.click(1)
        assert b.request().endswith(b'\tdownloadliked\n') and b.br.act == A_DOWNLOAD
        b.answer(msg=b'Downloading 50 tracks')
        assert b.toasts == ['Downloading 50 tracks']


def account(b):
    """The menu's Account, signed in as Tyler: its info row, then Sign Out."""
    b.menu()
    b.click(ACCOUNT)
    assert b.request().endswith(b'\taccount\n')
    b.answer(rows=[(b'info', b'', b'Tyler', b'Premium', b'', b'')])


def test_sign_out_asks_first_then_returns_to_pairing(fw):
    b = Browser(fw)
    account(b)
    shown = b.listing()
    assert len(shown) == 2 and shown[0][:2] == ('Tyler', 'Premium')
    b.click(1)  # Sign Out
    assert b.top.kind == L_CONFIRM and len(b.captions()) == 2
    asked = b.wk.id
    b.click(0)  # Cancel
    assert b.listing() == shown and b.wk.id == asked
    b.click(1)
    b.click(1)  # Cancel, Sign Out
    assert b.request().endswith(b'\tsignout\n') and b.br.act == A_SIGNOUT
    b.answer()
    assert b.sp.auth == -1 and b.br.depth == 1 and b.top.state == S_PAIR and len(b.captions()) == 1


@pytest.mark.parametrize('later, waits', [(14999, True), (15000, False)])
def test_after_signing_out_no_socket_is_librespot_restarting_for_15_s(fw, later, waits):
    b = Browser(fw)
    fw.mock('fw_access', lambda *_: 0)  # its state file: no socket after the restart is an add-on too old to browse
    account(b)
    b.click(1)
    b.click(1)
    b.answer()
    out = b.clock.now
    b.tick(2000)  # the pairing prompt asks again
    asked = b.wk.id
    assert b.request().endswith(b'\taccount\n')
    b.clock.now = out + later
    b.answer(fail=W_NOSOCK, raw=b'')
    assert b.top.state == (S_PAIR if waits else S_OLD) and b.sp.old == (not waits)
    b.tick(500)
    assert b.wk.id == asked + waits  # asked again soon while it restarts


@pytest.mark.parametrize('later, waits', [(14999, True), (15000, False)])
def test_after_a_refused_key_s_kill_the_open_browser_waits_15_s_for_librespot(fw, later, waits):
    b = Browser(fw)
    fw.mock('fw_access', lambda *_: 0)
    b.menu()
    b.click(LIKED)
    Files(fw, {LOG: b'x\nerror audio key\n'})
    b.sp.loading = 1
    fw.fn('spot_poll', None)()
    killed = b.clock.now
    assert b.sp.loading == 3 and [C.string_at(a[0]) for _, a in fw.calls('fw_system')][-1] == b'killall -9 librespot aplay'
    asked = b.wk.id
    b.clock.now = killed + later
    b.answer(fail=W_NOSOCK, raw=b'')  # the list asked for meanwhile: no socket
    assert b.top.state == (S_WAIT if waits else S_OLD) and b.br.depth == 1 + waits  # else an old add-on, at the base
    b.tick(500)
    assert b.wk.id == asked + waits  # the list asked again soon while it restarts
    assert not waits or b.request().endswith(b'\tliked\n')


def test_the_pairing_prompt_asks_again_lit_and_on_top_only(fw):
    b = Browser(fw)
    b.answer(status=b'login')
    assert b.top.state == S_PAIR
    asked = b.wk.id
    b.tick(1999)
    assert b.wk.id == asked
    b.light.value = 0
    b.tick(1)
    assert b.wk.id == asked
    b.light.value, b.on_top = 1, False
    b.tick()
    assert b.wk.id == asked
    b.on_top = True
    b.tick()
    assert b.wk.id == asked + 1 and b.request().endswith(b'\taccount\n')
    b.answer()
    assert b.request().endswith(b'\tcaps\n')  # paired: the menu next


def test_caps_that_failed_are_asked_again_once_the_menu_is_idle_lit_and_on_top(fw):
    b = Browser(fw)
    b.answer()
    b.answer(status=b'offline')
    menu = b.listing()
    assert b.br.caps_checked == 0 and len(menu) == DOWNLOADS + 1
    b.click(LIKED)
    b.answer(rows=[track(1)])  # nothing in flight now, lit and on top: only the level open holds caps back
    asked = b.wk.id
    b.tick(2000)
    assert b.wk.id == asked
    b.back()
    b.light.value = 0
    b.tick()
    assert b.wk.id == asked
    b.light.value, b.on_top = 1, False
    b.tick()
    assert b.wk.id == asked
    b.on_top = True
    b.tick()
    assert b.wk.id == asked + 1 and b.request().endswith(b'\tcaps\n') and b.br.act == A_CAPS
    b.answer(rows=[(b'op', c, b'', b'', b'', b'') for c in ALL_CAPS])
    assert b.br.caps_checked == 1 and b.listing() == menu[:DOWNLOADED] + menu[DOWNLOADS:]  # the hub's


def test_the_menu_follows_a_track_starting_and_ending(fw):
    b = Browser(fw)
    b.menu()
    menu = b.listing()
    b.sp.track, b.sp.state = b'spotify:track:1', b'playing'
    b.tick()
    playing = b.listing()
    assert len(playing) == len(menu) + 3 and not playing[0][2]  # Now Playing first, a page; Queue, Playback Settings
    assert [r for r in playing if r in menu] == menu  # the rest as they were
    b.sp.state = b'none'
    b.tick()
    assert b.listing() == menu


def test_now_playing_from_the_menu_opens_its_page_over_the_browser(fw):
    b = Browser(fw)
    b.sp.track, b.sp.state, b.sp.title, b.sp.auth = b'spotify:track:1', b'playing', b'Title', 1
    b.menu()
    fw.mock('toolsTimeItoa', minutes)
    b.click(0)  # Now Playing, first with a track
    ui = fw.var('spotify__ui', Ui)
    assert ui.page and ui.page != b.br.page and b.w.text[ui.title] == 'Title'
