"""airplay.c: AirPlay's part in demo. When q2-airplay's shared file is looked for and what it is told each pass,
the volume's hand-over to a speaker and back (the earphones' level, their Volume Limit, the keys each is saved
under), the song's tags for the speaker, the lyrics' place and Now Playing's ⋯ row. The player's part, the
filter's tap, is test/peq.py's."""
import ctypes as C
import struct

import pytest

from conftest import Files
from test_addon import Page as AppPage  # a card app's page on its world
from test_nav_library_pages import Config
from test_onlinematch import More, Queue, records  # noqa: F401  Now Playing's ⋯ page, the play queue and its songs

TAP, TRACK = b'/tmp/q2cast', '/tmp/q2cast.track'
PID, BIN = b'/tmp/q2-airplay', b'/mnt/mmc/.airplay/q2-airplay'
MAGIC, STALE = 0x31544351, 1430
CAST_OFF, CAST_CONNECTING, CAST_ON = range(3)
STOPPED, PLAYING, PAUSED = 1, 2, 3  # mclGetPlayStatus
CLOCK_S = 10  # CLOCK_MONOTONIC here
NOW = CLOCK_S * 10 ** 9 >> 20  # its CAST_CLOCK tick


class Cast(C.Structure):
    """peq.h's cast_tap up to its ring, as q2-airplay keeps it."""
    _fields_ = [('magic', C.c_uint), ('size', C.c_uint), ('want', C.c_uint), ('mute', C.c_int), ('state', C.c_int),
                ('pos_ms', C.c_int), ('pos_id', C.c_uint), ('pos_at', C.c_uint), ('pos_run', C.c_int), ('speaker', C.c_char * 64),
                ('seq', C.c_uint), ('id', C.c_uint), ('pts_ms', C.c_int), ('jumps', C.c_uint), ('jump_seq', C.c_uint),
                ('dsd', C.c_int), ('spk', C.c_int), ('stamp', C.c_longlong), ('beat', C.c_uint), ('status', C.c_int),
                ('volume', C.c_int), ('maxvol', C.c_int), ('track', C.c_uint), ('spk_seq', C.c_uint)]


RING = 262144 * 4  # CAST_RING frames of two 16-bit samples


class Tap:
    """The app's shared file as demo finds and maps it, to read and write, shared: the app running when demo
    starts (its pid file) unless told, and reading (want) at the clock's tick. t is the file: the app's fields
    the test's to set, demo's to read back. The earphones' volume and Volume Limit are demo's globals."""
    def __init__(self, fw, running=True):
        self.fw, self.ms, self.opens, self.there, self.way, self.status = fw, 5000, 0, True, 0, PLAYING
        self.mem = C.create_string_buffer(C.sizeof(Cast) + RING)
        self.t = Cast.from_buffer(self.mem)
        self.t.magic, self.t.size, self.t.want = MAGIC, len(self.mem), NOW
        self.files = {PID} if running else set()
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path) in self.files else -1)
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', lambda fd, out, n, *_: C.memmove(out, self.mem, n) and n)
        fw.mock('fw_mmap', self.mmap)
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('fw_clock_gettime', lambda which, ts, *_: struct.pack_into('<qq', (C.c_char * 16).from_address(ts), 0, CLOCK_S, 0))
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('mclGetOutputWay', lambda *_: self.way)
        self.volume, self.limit = fw.var('g_volume', C.c_ubyte), fw.var('g_maxvolume', C.c_ubyte)
        self.volume.value, self.limit.value = 60, 80
        self.save = fw.fn('ringnav_write_config', C.c_int, C.c_int, C.c_void_p, C.c_void_p)  # stock's write_int_config, hooked
        fw.mock('write_int_config', lambda v, s, k, *_: self.save(C.c_int(v).value, s, k))  # as tools/build.py routes it

    def open(self, path, flags, *_):
        self.opens += C.string_at(path) == TAP
        return 9 if self.there and (C.string_at(path), flags & 3) == (TAP, 2) else -1  # O_RDWR

    def mmap(self, at, size, prot, flags, *_):
        return C.addressof(self.mem) if (size, prot, flags) == (len(self.mem), 3, 1) else -1  # read and write, MAP_SHARED

    def app(self, **fields):
        """What the app writes; a speaker's volume (spk) with its count, as the app bumps it."""
        for name, value in fields.items():
            setattr(self.t, name, value)
        self.t.spk_seq += 'spk' in fields

    def poll(self, ms=1000):
        """A UI pass, ms on."""
        self.ms += ms
        self.fw.fn('airplay_poll', None)()

    def saved(self):
        """What reached config.ini through the hook: (value, section, key)."""
        return [(a[0], C.string_at(a[1]), C.string_at(a[2])) for _, a in self.fw.calls('stock_write_config_trampoline')]

    def set(self):
        """The level device_set_volume gave the output."""
        return [a[:2] for _, a in self.fw.calls('device_set_volume')]


def press(fw):
    """The ⋯ page's AirPlay row."""
    return fw.fn('airplay__air_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)


# The app's file.
def test_nothing_looks_for_the_file_until_airplay_is_used_and_then_once_a_second(fw):
    tap = Tap(fw, running=False)
    tap.there = False
    for _ in range(3):
        tap.poll()
    assert tap.opens == 0  # no one else pays for AirPlay
    tap.files.add(BIN)
    assert press(fw) == 0
    tap.poll(), tap.poll(500), tap.poll(500)
    assert tap.opens == 2  # the app not up yet
    tap.there = True
    tap.poll(), tap.poll(), tap.poll()
    assert tap.opens == 3 and tap.t.beat == 3  # mapped: never opened again


@pytest.mark.parametrize('other', ['magic', 'size'])
def test_a_file_of_another_layout_is_left_alone(fw, other):
    tap = Tap(fw)
    setattr(tap.t, other, getattr(tap.t, other) + 4)  # an app built for another firmware's cast_tap
    tap.app(state=CAST_ON, spk=20)
    tap.poll(), tap.poll()
    assert not fw.calls('fw_mmap') and tap.t.beat == 0 and tap.volume.value == 60
    assert len(fw.calls('fw_close')) == tap.opens  # each refused open closed: it is retried every second


def test_each_pass_tells_the_app_the_player_s_state_and_no_volume_without_a_speaker(fw):
    tap = Tap(fw)
    tap.poll()
    tap.status = PAUSED
    tap.poll()
    assert (tap.t.beat, tap.t.status, tap.t.volume, tap.t.maxvol) == (2, PAUSED, -1, 80)


def test_the_app_running_puts_the_filter_in_the_chain_with_the_eq_off_once_something_plays(fw):
    tap, chained = Tap(fw), fw.var('peq_chained', C.c_int)
    tap.poll()  # the app idle: no speaker asked for
    tap.status = STOPPED
    tap.app(state=CAST_CONNECTING, spk=45)
    tap.poll()  # nothing plays: an attach might not stick (crossfade.c peq_chain)
    assert chained.value == 0
    assert tap.volume.value == 60 and not tap.saved()  # no speaker plays yet: the earphones' level stays
    tap.status = PLAYING
    tap.poll()
    assert chained.value == 1


def test_the_playing_song_s_tags_go_to_the_app_once_a_song(fw, mcl, records):
    tap, files = Tap(fw), Files(fw)
    Queue(fw, mcl, [records.song(b'/m/a.flac', title=b'One\nTwo', artist=b'Ar', album=b'Al'), records.song(b'/m/b.flac'),
                    records.song(b'/m/b.flac', title=b'Three')])  # a track of the same file (a CUE sheet's)
    fw.mock('player_playtime_and_length', lambda sec, total, *_: C.memmove(total, struct.pack('<i', 200), 4) and 0)
    tap.poll()
    assert TRACK not in files.files  # the app idle
    tap.app(state=CAST_CONNECTING)
    tap.poll(), tap.poll()
    assert files.files[TRACK] == b'path=/m/a.flac\ntitle=One Two\nartist=Ar\nalbum=Al\nlength=200\n'  # a line a tag
    assert tap.t.track == 1
    mcl.pos = 1
    tap.poll()
    assert files.files[TRACK] == b'path=/m/b.flac\ntitle=b.flac\nartist=\nalbum=\nlength=200\n'  # untagged: its file's name
    assert tap.t.track == 2
    mcl.pos = 2
    tap.poll()
    assert files.files[TRACK].startswith(b'path=/m/b.flac\ntitle=Three\n') and tap.t.track == 3


# The volume: a speaker's is its own, the earphones' and their limit wait in RAM.
@pytest.mark.parametrize('end', ['stopped', 'the app gone'])
def test_a_speaker_takes_its_own_volume_and_the_earphones_level_and_limit_come_back(fw, end):
    tap = Tap(fw)
    tap.app(state=CAST_ON, spk=20)
    tap.poll()
    assert (tap.volume.value, tap.limit.value) == (20, 100)  # the limit is the earphones'
    assert tap.saved() == [(60, b'PLAYSET', b'VOLUME')]
    assert not tap.set()  # the output keeps the earphones' level: its last half second plays through it
    tap.poll()
    assert (tap.t.volume, tap.t.maxvol) == (20, 100)
    if end == 'stopped':
        tap.app(state=CAST_OFF)
    else:
        tap.t.want = NOW - STALE  # no read for a second and a half: a crash
    tap.poll()
    assert tap.t.volume == 20  # published before the swap: the filter stays muted this pass
    assert (tap.volume.value, tap.limit.value) == (60, 80) and tap.set() == [(60, 1)]
    assert tap.saved()[1:] == [(20, b'Q2POD', b'AIRVOL'), (60, b'PLAYSET', b'VOLUME')]  # the speaker's, for its next time
    tap.poll()
    assert tap.t.volume == -1


@pytest.mark.parametrize('way, key', [(0, b'VOLUME'), (1, b'BT_VOLUME')])  # the earphones, Bluetooth's
def test_a_volume_saved_while_a_speaker_plays_is_the_speaker_s_never_the_earphones(fw, way, key):
    tap = Tap(fw)
    tap.way = way
    tap.app(state=CAST_ON, spk=20)
    tap.poll()
    tap.save(25, b'PLAYSET', b'VOLUME')  # the wheel
    tap.app(state=CAST_OFF)
    tap.poll()
    tap.save(61, b'PLAYSET', b'VOLUME')
    assert tap.saved() == [(60, b'PLAYSET', key), (25, b'Q2POD', b'AIRVOL'),  # the speaker's even on Bluetooth
                           (20, b'Q2POD', b'AIRVOL'), (60, b'PLAYSET', key), (61, b'PLAYSET', key)]


@pytest.mark.parametrize('began, became, key', [(0, 1, 'BT_VOLUME'), (1, 0, 'VOLUME')])
def test_an_output_changed_meanwhile_gets_its_own_saved_level_within_the_limit(fw, began, became, key):
    tap = Tap(fw)
    tap.way = began
    Config(fw, **{key: b'90'})
    tap.app(state=CAST_ON, spk=20)
    tap.poll()
    fw.fn('ringnav_output', C.c_int, C.c_int)(became)  # Bluetooth earphones come or go: stock's mclSetOutputWay, hooked
    tap.way = became
    assert tap.volume.value == 20 and tap.saved() == [(60, b'PLAYSET', b'BT_VOLUME' if began else b'VOLUME')]  # the speaker's level is no earphones'
    tap.app(state=CAST_OFF)
    tap.poll()
    assert tap.volume.value == 80 and tap.set()[-1] == (80, 1)  # that output's 90, held to the limit


@pytest.mark.parametrize('seq, spk, last, want', [
    (1, 20, b'40', 20), (1, 0, b'40', 0), (1, 100, b'40', 100),  # as the app says
    (0, 20, b'40', 40),  # an app that never says: the level the speaker last had from the Q2
    (0, 20, None, 35),  # and none saved
    (1, 101, b'40', 40), (1, -1, None, 35),  # no volume
])
def test_the_speaker_s_volume_is_the_app_s_word_or_the_one_it_last_had(fw, seq, spk, last, want):
    tap = Tap(fw)
    Config(fw, **({'AIRVOL': last} if last else {}))
    tap.t.spk, tap.t.spk_seq = spk, seq
    tap.app(state=CAST_ON)
    tap.poll()
    assert tap.volume.value == want


def test_a_volume_the_app_says_anew_is_taken_while_the_speaker_plays(fw):
    tap = Tap(fw)
    tap.app(state=CAST_ON, spk=20)
    tap.poll()
    tap.volume.value = 30  # the wheel, since
    tap.poll()
    assert tap.volume.value == 30
    tap.app(spk=45)  # set on the speaker itself, or the app reconnected
    tap.poll()
    assert tap.volume.value == 45 and tap.limit.value == 100


# The lyrics.
def place(fw):
    """airplay_lyrics: whether a speaker's place stands in for the Q2's, and the place in ms."""
    ms = C.c_int(-1)
    return fw.fn('airplay_lyrics', C.c_int, C.POINTER(C.c_int))(C.byref(ms)), ms.value


def test_the_lyrics_follow_the_speaker_s_place_in_the_song(fw):
    tap = Tap(fw)
    tap.app(state=CAST_ON, spk=20, id=7, pos_id=7, pos_ms=30000, pos_at=NOW - 1000, pos_run=1)
    assert place(fw) == (0, -1)  # no speaker yet: the Q2's own place
    tap.poll()
    used, ms = place(fw)
    assert used == 1 and 31048 <= ms <= 31049  # 1000 ticks of 1.048576 ms on
    tap.app(pos_run=0)
    assert place(fw) == (1, 30000)  # paused there
    tap.app(id=8)  # the Q2 began the next song: the speaker still finishes the last
    assert place(fw) == (1, 0)


# Now Playing's ⋯ row.
def test_the_row_names_the_speaker_while_one_plays(fw):
    tap, more = Tap(fw), More(fw)
    more.open()
    tap.app(state=CAST_ON, spk=20)
    tap.poll()
    more.open()  # a speaker with no name yet
    tap.app(speaker=b'Kitchen')
    more.open()
    tap.app(state=CAST_OFF)
    tap.poll()
    more.open()  # stopped: the name stays in the file
    assert more.texts == ['AirPlay', 'AirPlay', 'AirPlay: Kitchen', 'AirPlay']


def test_the_row_pressed_without_the_app_on_the_card_says_where_to_install_it(fw):
    tap, more, said = Tap(fw, running=False), More(fw), []
    fw.mock('navigator_to_with_context', lambda page, info, *_: said.append(C.string_at(info + 8).decode()))
    more.open()
    assert more.click() == 0 and said == ['Install AirPlay in System settings → App Store']
    tap.poll(), tap.poll()
    assert tap.opens == 0  # nor is its file looked for


def test_the_row_opens_the_app_s_page_over_another_app_s(fw):
    page, shut = AppPage(fw), []  # app foo's page, open
    page.world.files.update({b'/mnt/mmc/.airplay/q2app': b'name=AirPlay\nhidden=1\n', BIN: b'ELF'})
    page.world.dirs[b'/mnt/mmc'].append(b'.airplay')
    fw.mock('window_manager_close_window_force', lambda manager, window, *_: shut.append(window))
    assert page.ap.a[page.pg.a].id == b'foo' and press(fw) == 0
    assert shut == [page.page] and page.pg.page and not page.toasts
    assert page.ap.a[page.pg.a].id == b'airplay' and page.top.op == b'menu'
