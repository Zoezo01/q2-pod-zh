"""headset.c (docs/internals.md#headset-controls-and-volume): AirPods' ear reports and the AVRCP keys, routed to whichever
player plays; the worker's I/O over simulated sockets and input devices; the Bluetooth link filters; EQ per Output's
key."""
import ctypes as C
import re
import struct

import pytest

from conftest import O, ROOT, Game, claim
from test_radio import Rd  # test_radio.py keeps the mirror

KEY_PLAY, KEY_BACK_BTN, KEY_FWD_BTN = O['KEY_PLAY'], O['KEY_BACK_BTN'], O['KEY_FWD_BTN']
NEXT, PLAYPAUSE, PREV, STOP, PAUSE, PLAYCD, PAUSECD, PLAYKEY = 163, 164, 165, 166, 119, 200, 201, 207
PLAYING, PAUSED, STOPPED = 2, 3, 1
PAIRS, LINK_MAC, STOCK_STATUS = 0xa4ba70, 0xa4bac8, 0x4fbfb0  # pdeq_btpairlist, BTLINKMAC, stock's status callback


class Hs(C.Structure):
    _fields_ = [*((n, C.c_uint) for n in 'generation running head tail report'.split()), ('keys', C.c_int * 16),
                *((n, C.c_uint) for n in ('worker_generation checked epoch paused_epoch paused_at recent_at recent_owner '
                                          'recent_session last_at session linked_at eq_checked').split()),
                *((n, C.c_int) for n in 'linked known ears want paused owner automatic last_key'.split()),
                ('address', C.c_ubyte * 14), ('airpods', C.c_int)]


def hs(fw):
    return fw.var('headset__hs', Hs)


class Player:
    """Stock's player: its status, which player_play_pause toggles, and the time (time_now_ms)."""
    def __init__(self, fw, status=PLAYING, now=100000):
        self.status, self.now, self.fw = status, now, fw
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_play_pause', lambda *_: self.toggle())
        fw.mock('time_now_ms', lambda *_: self.now)

    def toggle(self):
        self.status = PAUSED if self.status == PLAYING else PLAYING

    def toggles(self):
        return len(self.fw.calls('player_play_pause'))


def ears(fw, player, *reports, step=200):
    """Each ear report in turn (bit 0 one bud in, bit 1 the other), a step apart; the toggles after each."""
    out = []
    for report in reports:
        player.now += step
        fw.fn('headset_ears', None, C.c_uint)(4 | report)
        out.append(player.toggles())
    return out


# AACP ear packets.

def parse(fw, data):
    buf = (C.c_ubyte * max(len(data), 1))(*data)
    return fw.fn('ringnav_aap_parse', C.c_int, C.c_void_p, C.c_int)(buf if data else None, len(data))


@pytest.mark.parametrize('primary, secondary', [(a, b) for a in range(3) for b in range(3)])
def test_an_ear_packet_reports_which_buds_are_in(fw, primary, secondary):
    """Each bud's byte: 0 in an ear, 1 out, 2 in the case."""
    assert parse(fw, [4, 0, 4, 0, 6, 0, primary, secondary]) == 2
    assert hs(fw).report == 4 | (primary == 0) | (secondary == 0) << 1


@pytest.mark.parametrize('data', [
    [4, 0, 4, 0, 6, 0, 0], [4, 0, 4, 0, 6, 0, 0, 0, 0], [],  # not 8 bytes
    [5, 0, 4, 0, 6, 0, 0, 0], [4, 1, 4, 0, 6, 0, 0, 0], [4, 0, 3, 0, 6, 0, 0, 0], [4, 0, 4, 1, 6, 0, 0, 0],
    [4, 0, 4, 0, 4, 0, 0, 0], [4, 0, 4, 0, 6, 1, 0, 0],  # another report's header
    [4, 0, 4, 0, 6, 0, 3, 0], [4, 0, 4, 0, 6, 0, 0, 3], [4, 0, 4, 0, 6, 0, 0, 255],  # no such state
])
def test_anything_else_publishes_nothing(fw, data):
    hs(fw).report = 0x55
    assert parse(fw, data) == 0 and hs(fw).report == 0x55


def test_no_packet_publishes_nothing(fw):
    hs(fw).report = 0x55
    assert fw.fn('ringnav_aap_parse', C.c_int, C.c_void_p, C.c_int)(None, 8) == 0 and hs(fw).report == 0x55


# Ear detection, on stock's player.

def test_a_bud_out_pauses_and_back_in_resumes(fw):
    player = Player(fw)
    assert ears(fw, player, 3, 1, 3) == [0, 1, 2]
    assert player.status == PLAYING


def test_one_bud_worn_out_and_back(fw):
    player = Player(fw)
    assert ears(fw, player, 2, 0, 2) == [0, 1, 2]


def test_the_buds_swapping_slots_is_no_change(fw):
    """The report's bytes are the primary and the secondary: a swap with both in is still two."""
    player = Player(fw)
    assert ears(fw, player, 3, 1, 2, 1) == [0, 1, 1, 1]  # one out, then the other slot: still one in
    assert ears(fw, player, 3) == [2]


def test_only_as_many_buds_back_as_were_worn_resume(fw):
    player = Player(fw)
    assert ears(fw, player, 3, 0, 1) == [0, 1, 1]  # both out, one back: not yet
    assert ears(fw, player, 3) == [2]


def test_both_out_at_once_pauses_once(fw):
    player = Player(fw)
    assert ears(fw, player, 3, 0, 0) == [0, 1, 1]


def test_the_first_report_never_resumes(fw):
    """Out of the ears at the first report: playing pauses, and buds going in later don't start it."""
    player = Player(fw)
    assert ears(fw, player, 0, 3, 1, 3) == [1, 1, 1, 1]


def test_a_bud_out_while_nothing_plays_does_nothing(fw):
    player = Player(fw, status=PAUSED)
    assert ears(fw, player, 3, 1, 3) == [0, 0, 0]


def test_a_manual_command_cancels_the_resume(fw):
    player = Player(fw)
    ears(fw, player, 3, 1)
    fw.fn('headset_manual', None)()
    assert ears(fw, player, 3) == [1]


@pytest.mark.parametrize('field', ['pos', 'type'])
def test_a_new_track_or_queue_cancels_the_resume(fw, mcl, field):
    player = Player(fw)
    setattr(mcl, field, 4)
    ears(fw, player, 3, 1)
    setattr(mcl, field, 5)  # the queue moved on, or another queue took its place: another session
    assert ears(fw, player, 3) == [1]


def test_playing_again_since_cancels_the_resume(fw):
    player = Player(fw)
    ears(fw, player, 3, 1)
    player.status = PLAYING  # started some other way
    assert ears(fw, player, 1, step=1600) == [1]  # past 1.5 s: the pause is over
    player.status = PAUSED
    assert ears(fw, player, 3) == [1]


def test_a_bud_back_long_after_still_resumes(fw):
    player = Player(fw)
    assert ears(fw, player, 3, 1) == [0, 1]
    assert ears(fw, player, 3, step=60000) == [2]  # a minute later


def test_the_same_session_number_under_another_player_is_another_session(fw):
    player = Player(fw)
    ears(fw, player, 3, 1)  # stock's player paused by the ears
    session = fw.fn('headset__media_session', C.c_uint)()
    rd, keep = radio(fw, pid=0)
    rd.serial = session  # Internet Radio, stopped, took the output since, its session by chance the same number
    assert ears(fw, player, 3) == [1] and fw.calls('fw_fork') == []


def test_a_headset_pause_just_before_a_bud_comes_out_still_resumes(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    assert player.toggles() == 1 and player.status == PAUSED
    assert ears(fw, player, 1, step=1000) == [1]  # the pause was the bud's
    assert ears(fw, player, 3, step=1000) == [2]


@pytest.mark.parametrize('pos, toggles', [(4, 2), (5, 1)])
def test_a_headset_pause_is_the_bud_s_only_on_its_track(fw, mcl, pos, toggles):
    player = Player(fw)
    mcl.pos = 4
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    mcl.pos = pos  # 5: the queue moved on since, another session
    ears(fw, player, 1, step=500)
    assert ears(fw, player, 3) == [toggles]


def test_a_headset_pause_under_another_player_isn_t_the_bud_s(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    session = fw.fn('headset__media_session', C.c_uint)()
    rd, keep = radio(fw, pid=0)
    rd.serial = session  # the radio, stopped, took the output since, its session by chance the same number
    ears(fw, player, 1, step=500)
    assert ears(fw, player, 3) == [1] and fw.calls('fw_fork') == []


def test_a_pause_key_while_nothing_plays_is_no_pause_for_the_ears_to_resume(fw):
    player = Player(fw, status=PAUSED)
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    assert ears(fw, player, 1, 3) == [0, 0]


def test_a_headset_pause_long_before_a_bud_comes_out_is_the_user_s(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    assert ears(fw, player, 1, step=2000) == [1]
    assert ears(fw, player, 3) == [1]


def test_a_command_between_a_headset_pause_and_a_bud_coming_out_makes_the_pause_the_user_s(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    fw.fn('media_key', None, C.c_uint)(KEY_FWD_BTN)  # the next track, by hand, still paused
    assert ears(fw, player, 1, step=500) == [1]
    assert ears(fw, player, 3, step=500) == [1]


def test_a_report_without_ear_bits_changes_nothing(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_ears', None, C.c_uint)(0)
    assert player.toggles() == 0 and hs(fw).ears == 3


# A player slow to report a pause: Spotify's state is librespot's file as last read, which spot_status trusts again
# a second after a command.

def spotify(fw, track=b'spotify:track:1'):
    """Spotify playing (spotify.c sp)."""
    sp = fw.var('spotify__sp', Sp)
    sp.active, sp.playing, sp.track = 1, 1, track
    fw.mock('fw_socket', lambda *_: 7)
    return sp


def test_spotify_still_read_as_playing_after_an_ear_pause_keeps_the_resume(fw):
    player = Player(fw)
    sp = spotify(fw)
    ears(fw, player, 3, 1)
    ears(fw, player, 1, step=1100)  # its state file still says playing
    sp.playing = 0  # and now paused
    ears(fw, player, 3)
    assert sent(fw) == [b's', b'P']


def test_a_second_bud_out_while_spotify_still_reads_as_playing_keeps_the_count_worn(fw):
    player = Player(fw)
    sp = spotify(fw)
    ears(fw, player, 3, 1)  # two were worn
    ears(fw, player, 0, step=1100)  # the other out, its state file still saying playing: paused again
    sp.playing = 0
    ears(fw, player, 1)
    assert sent(fw) == [b's', b's']  # one back isn't as many as were worn
    ears(fw, player, 3)
    assert sent(fw) == [b's', b's', b'P']


def test_both_out_while_spotify_still_reads_as_playing_resume_with_both_back(fw):
    player = Player(fw)
    sp = spotify(fw)
    ears(fw, player, 3, 0)
    ears(fw, player, 0, step=1100)  # its state file still says playing, with nothing to hear: paused again
    sp.playing = 0
    ears(fw, player, 1)
    assert sent(fw) == [b's', b's']
    ears(fw, player, 3)
    assert sent(fw) == [b's', b's', b'P']


# Headset keys.

def command(fw, player, code, step=200):
    player.now += step
    fw.fn('headset_command', None, C.c_int)(code)


@pytest.mark.parametrize('code, stub', [(NEXT, 'player_next_music'), (PREV, 'player_prev_music'),
                                        (PLAYPAUSE, 'player_play_pause')])
def test_keys_reach_stock_s_player(fw, code, stub):
    player = Player(fw)
    command(fw, player, code)
    assert len(fw.calls(stub)) == 1


def test_a_repeated_key_within_150_ms_is_one(fw):
    player = Player(fw)
    command(fw, player, NEXT)
    command(fw, player, NEXT, step=149)
    command(fw, player, PREV, step=1)
    command(fw, player, PREV, step=150)
    assert len(fw.calls('player_next_music')) == 1 and len(fw.calls('player_prev_music')) == 2


@pytest.mark.parametrize('code', [PAUSE, PAUSECD, STOP])
@pytest.mark.parametrize('status, toggles', [(PLAYING, 1), (PAUSED, 0), (STOPPED, 0)])
def test_pause_keys_only_pause(fw, code, status, toggles):
    player = Player(fw, status=status)
    command(fw, player, code)
    assert player.toggles() == toggles


@pytest.mark.parametrize('code', [PLAYCD, PLAYKEY])
@pytest.mark.parametrize('status, toggles', [(PLAYING, 0), (PAUSED, 1)])
def test_play_keys_only_play(fw, code, status, toggles):
    player = Player(fw, status=status)
    command(fw, player, code)
    assert player.toggles() == toggles


def test_a_play_key_with_the_buds_out_doesn_t_play(fw):
    player = Player(fw, status=PAUSED)
    ears(fw, player, 0)
    command(fw, player, PLAYKEY)
    assert player.toggles() == 0
    ears(fw, player, 1)
    command(fw, player, PLAYKEY)
    assert player.toggles() == 1


@pytest.mark.parametrize('code', [113, 114, 115, 168, 208])  # mute, volume down and up, rewind, fast forward
def test_other_keys_do_nothing(fw, code):
    player = Player(fw, status=PAUSED)
    command(fw, player, code)
    assert player.toggles() == 0 and not [n for n, _ in fw.calls() if n.startswith('player_')]


@pytest.mark.parametrize('code', [PLAYPAUSE, PAUSE, PLAYKEY])
def test_keys_just_after_an_ear_pause_are_its_echo(fw, code):
    player = Player(fw)
    ears(fw, player, 3, 1)
    command(fw, player, code, step=1000)
    assert player.toggles() == 1
    assert ears(fw, player, 3) == [2]  # nor did it cancel the resume


@pytest.mark.parametrize('code', [PLAYPAUSE, PAUSE])
def test_a_key_after_the_ears_resume_is_a_press_again(fw, code):
    player = Player(fw)
    assert ears(fw, player, 3, 1, 3) == [0, 1, 2]
    command(fw, player, code)
    assert player.toggles() == 3


def test_a_play_press_well_after_an_ear_pause_plays(fw):
    player = Player(fw)
    ears(fw, player, 3, 1)
    command(fw, player, PLAYPAUSE, step=2000)
    assert player.toggles() == 2 and player.status == PLAYING


def test_a_pause_press_well_after_an_ear_pause_cancels_its_resume(fw):
    player = Player(fw)
    ears(fw, player, 3, 1)
    command(fw, player, PAUSE, step=2000)
    assert ears(fw, player, 3) == [1]


def link(fw, player, resume='0'):
    """The link comes up now (headset_poll), Resume on Connect as given."""
    config(fw, RESUME=resume)
    fw.var('g_bluetoothflag', C.c_ubyte).value = 1
    fw.var('bt_linkstatus', C.c_ubyte).value = 1
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.fn('headset_poll', None)()


def config(fw, **values):
    values = {k.encode(): v.encode() for k, v in values.items()}

    def read(path, section, key, out, default, *_):
        v = values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)


def test_play_as_the_link_comes_up_is_dropped(fw):
    player = Player(fw, status=PAUSED)
    link(fw, player)
    command(fw, player, PLAYPAUSE, step=1500)  # AirPods send PLAY with A2DP's setup
    command(fw, player, PLAYKEY, step=1500)
    assert player.toggles() == 0
    command(fw, player, PLAYPAUSE, step=2100)  # past 5 s: a press
    assert player.toggles() == 1


def test_the_hold_on_play_is_the_link_s_first_5_s(fw):
    """docs/internals.md#resume-on-connect: PLAY within CONNECT_HOLD_MS (5 s) of the link is dropped."""
    player = Player(fw, status=PAUSED)
    link(fw, player)
    command(fw, player, PLAYKEY, step=4999)
    assert player.toggles() == 0
    command(fw, player, PLAYPAUSE, step=1)
    assert player.toggles() == 1


def test_pause_as_the_link_comes_up_still_pauses(fw):
    player = Player(fw)
    link(fw, player)
    command(fw, player, PLAYPAUSE, step=1000)
    assert player.toggles() == 1


def test_with_resume_on_connect_play_on_the_link_plays(fw):
    player = Player(fw, status=PAUSED)
    link(fw, player, resume='1')
    command(fw, player, PLAYPAUSE, step=1000)
    assert player.toggles() == 1


# Routing: video, radio, Spotify, then stock's player.

class Vid(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'paused volume gain pid sock seek'.split()),
                *((k, C.c_uint) for k in 'polled control_at seek_at key at paused_at'.split())]


class List(C.Structure):
    _fields_ = [('buf', C.c_void_p), ('len', C.c_uint), ('n', C.c_int), ('truncated', C.c_int),
                ('name', C.c_uint * 201), ('url', C.c_uint * 201)]


class Sp(C.Structure):
    _fields_ = [*((n, C.c_int) for n in ('launched', 'sock', 'playing', 'paused', 'local', 'active', 'played', 'dead')),
                *((n, C.c_uint) for n in ('polled', 'idle_at', 'duration', 'position', 'cover', 'launch_at', 'alive_at')),
                ('at', C.c_ulonglong), ('raw', C.c_char * 1536), ('state', C.c_char * 12), ('track', C.c_char * 64),
                ('title', C.c_char * 256), ('artist', C.c_char * 256), ('album', C.c_char * 256), ('out', C.c_char * 24),
                ('auth', C.c_int), ('loading', C.c_int), ('old', C.c_int), ('load_at', C.c_uint), ('log_from', C.c_long),
                ('wanted', C.c_char * 64), ('from_', C.c_char * 64)]


def radio(fw, pid=9):
    """Internet Radio (radio.c rd) playing its one station as q2video's pid, which quits when told ('q'); a start
    forks 77. The station's URL goes with the returned buffer."""
    rd = fw.var('radio__rd', Rd)
    url = C.create_string_buffer(b'http://radio.example/1')
    rd.list.buf, rd.list.n, rd.pid, rd.active = C.addressof(url), 1, pid, 1
    fw.mock('fw_socket', lambda *_: 7)
    fw.mock('fw_waitpid', lambda pid, *_: pid)
    fw.mock('fw_fork', lambda *_: 77)
    return rd, url


def top_window(fw, name):
    page = C.create_string_buffer(name.encode())
    fw.mock('window_manager_get_top_window', lambda *_: 0x5100)
    fw.mock('widget_get_prop_str', lambda w, key, default, *_:
            C.addressof(page) if w == 0x5100 and C.string_at(key) == b'name' else default)
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    return page



@pytest.mark.parametrize('how', ['media_key', PLAYPAUSE, PLAYCD, PLAYKEY])
@pytest.mark.parametrize('audio, toggles', [(1, 0), (0, 1)])
def test_a_card_game_with_the_sound_keeps_the_headsets_keys_from_stock(fw, how, audio, toggles):
    """Through media_key, and the headset's AVRCP PLAY, which headset_command sends to media_set_playing."""
    player = Player(fw, status=PAUSED)
    gm = fw.var('addon__gm', Game)
    gm.pid, gm.audio = 4321, audio
    def press():
        player.status = PAUSED
        if how == 'media_key':
            fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
        else:
            command(fw, player, how)
    press()
    assert player.toggles() == toggles
    gm.pid = 0
    press()
    assert player.toggles() == toggles + 1


@pytest.mark.parametrize('how', ['media_key', PLAYPAUSE, PAUSECD, PAUSE, STOP])
def test_a_card_game_with_the_sound_lets_a_headset_pause_what_plays(fw, how):
    """Something else playing (stock's player stands in for the Spotify a phone resumed, the real case): a pause still
    passes, Play/Pause's too; only a start is kept."""
    player = Player(fw, status=PLAYING)
    gm = fw.var('addon__gm', Game)
    gm.pid, gm.audio = 4321, 1
    if how == 'media_key':
        fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    else:
        command(fw, player, how)
    assert player.toggles() == 1 and player.status == PAUSED


def test_a_playing_video_takes_the_keys(fw):
    player = Player(fw)
    fw.var('books__vid', Vid).pid = 4321
    fw.mock('fw_socket', lambda *_: 7)
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert player.toggles() == 0 and fw.fn('media_owner', C.c_int)() == 4


def test_a_video_pauses_and_resumes_by_the_ears(fw):
    player = Player(fw)
    vid = fw.var('books__vid', Vid)
    vid.pid = 4321
    fw.mock('fw_socket', lambda *_: 7)
    ears(fw, player, 3, 1)
    assert vid.paused == 1 and player.toggles() == 0
    ears(fw, player, 3)
    assert vid.paused == 0


def test_another_video_cancels_the_resume(fw):
    player = Player(fw)
    vid = fw.var('books__vid', Vid)
    vid.pid = 4321
    fw.mock('fw_socket', lambda *_: 7)
    ears(fw, player, 3, 1)
    vid.pid = 4322  # another video since, paused: another session
    ears(fw, player, 3)
    assert vid.paused == 1 and sent(fw) == [b'a']


def test_a_radio_stops_and_starts_again_by_the_ears(fw):
    player = Player(fw)
    rd, keep = radio(fw)
    ears(fw, player, 3, 1)
    assert sent(fw) == [b'q'] and rd.pid == 0
    ears(fw, player, 3)
    assert rd.pid == 77 and player.toggles() == 0  # its station again


def test_another_station_cancels_the_radio_s_resume(fw):
    player = Player(fw)
    rd, keep = radio(fw)
    ears(fw, player, 3, 1)
    rd.serial += 1  # another station since, stopped as well: another session
    ears(fw, player, 3)
    assert rd.pid == 0 and fw.calls('fw_fork') == []


@pytest.mark.parametrize('track, want', [(b'spotify:track:1', [b's', b'P']), (b'spotify:track:2', [b's'])])
def test_spotify_resumes_by_the_ears_only_on_its_track(fw, track, want):
    player = Player(fw)
    sp = spotify(fw)
    ears(fw, player, 3, 1)
    sp.track = track  # 2: another track since, another session
    ears(fw, player, 3)
    assert sent(fw) == want and player.toggles() == 0


@pytest.mark.parametrize('running', [dict(video=4321), dict(radio=9), dict(spotify=1)])
@pytest.mark.parametrize('page', ['tidal_album', 'tidal_'])
def test_tidal_on_top_keeps_the_keys_with_stock_s_player(fw, page, running):
    player = Player(fw)
    players(fw, **running)
    top_window(fw, page)
    fw.fn('media_key', None, C.c_uint)(KEY_FWD_BTN)
    assert len(fw.calls('player_next_music')) == 1 and sent(fw) == []


@pytest.mark.parametrize('page, focused', [('spotify_page', 1), ('spotbrowse_page', 1), ('radio_page', 1),
                                           ('radionp_page', 1), ('tidal_page', 1), ('playing_page', 0), ('', 0)])
def test_a_streaming_page_on_top_is_focused(fw, page, focused):
    top_window(fw, page)
    assert fw.fn('media_focused', C.c_int)() == focused


@pytest.mark.parametrize('page', ['spotify_page', 'radio_page'])
def test_a_focused_page_whose_player_isn_t_running_leaves_the_key_to_stock(fw, page):
    player = Player(fw)
    top_window(fw, page)
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert player.toggles() == 1


@pytest.mark.parametrize('page, running, command', [('spotify_page', dict(radio=9), b'q'),
                                                    ('radio_page', dict(spotify=1), b'p')])
def test_a_focused_page_whose_player_isn_t_running_leaves_the_key_to_the_one_playing(fw, page, running, command):
    player = Player(fw)
    players(fw, **running)
    keep = top_window(fw, page)
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert sent(fw) == [command] and player.toggles() == 0


def players(fw, video=0, radio=0, spotify=0):
    """Which players run: video (books.c vid.pid), radio (radio.c rd.active, rd.pid; q2video quits when told),
    Spotify (spotify.c sp.active)."""
    fw.var('books__vid', Vid).pid = video
    rd = fw.var('radio__rd', Rd)
    rd.pid, rd.active = radio, bool(radio)
    sp = fw.var('spotify__sp', Sp)
    sp.playing, sp.active = 1, spotify
    fw.mock('fw_socket', lambda *_: 7)
    fw.mock('fw_waitpid', lambda pid, *_: pid)


def sent(fw):
    """What went to the players' sockets (dgram_send)."""
    return [C.string_at(a[1], a[2]) for _, a in fw.calls('fw_sendto')]


@pytest.mark.parametrize('video, radio, spotify, owner', [(0, 0, 0, 1), (0, 0, 1, 2), (0, 9, 1, 3), (5, 9, 1, 4),
                                                          (5, 0, 0, 4), (0, 9, 0, 3)])
def test_the_owner_is_video_then_radio_then_spotify_then_stock(fw, video, radio, spotify, owner):
    players(fw, video, radio, spotify)
    assert fw.fn('media_owner', C.c_int)() == owner


@pytest.mark.parametrize('key, command', [(KEY_PLAY, b'p'), (KEY_FWD_BTN, b'n'), (KEY_BACK_BTN, b'b')])
def test_spotify_playing_takes_the_keys(fw, key, command):
    player = Player(fw)
    players(fw, spotify=1)
    fw.fn('media_key', None, C.c_uint)(key)
    assert sent(fw) == [command] and player.toggles() == 0 and not fw.calls('player_next_music')


@pytest.mark.parametrize('status, forbidden, runs', [(STOPPED, 1, 0), (PAUSED, 1, 0), (PLAYING, 1, 1), (STOPPED, 0, 1), (PAUSED, 0, 1)])
def test_headphones_required_with_none_lets_play_pause_only_pause(fw, status, forbidden, runs):
    """player_play_pause's hook: stock's check (it toasts) forbids a start, as stock's own Play key; a pause goes through."""
    asked = []
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.mock('check_forbid_play_no_headset', lambda quiet, *_: asked.append(quiet) or forbidden)
    fw.mock('stock_pause_trampoline', lambda *_: 7)
    assert fw.fn('ringnav_play_pause', C.c_int)() == (7 if runs else 0)
    assert len(fw.calls('stock_pause_trampoline')) == runs and asked == ([] if status == PLAYING else [0])


@pytest.mark.parametrize('key, skip', [(KEY_FWD_BTN, 'player_next_music'), (KEY_BACK_BTN, 'player_prev_music')])
@pytest.mark.parametrize('forbidden', [0, 1])
def test_headphones_required_with_none_skips_no_track(fw, key, skip, forbidden):
    Player(fw)
    fw.mock('check_forbid_play_no_headset', lambda *_: forbidden)
    fw.fn('media_key', None, C.c_uint)(key)
    assert len(fw.calls(skip)) == (not forbidden)


def test_a_focused_spotify_page_takes_the_keys_from_a_video(fw):
    player = Player(fw)
    players(fw, video=5, spotify=1)
    top_window(fw, 'spotify_page')
    fw.fn('media_key', None, C.c_uint)(KEY_FWD_BTN)
    assert sent(fw) == [b'n'] and player.toggles() == 0


def test_a_playing_radio_takes_the_keys(fw):
    player = Player(fw)
    rd, keep = radio(fw)
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert sent(fw) == [b'q'] and rd.pid == 0 and player.toggles() == 0


def commands(fw):
    """Each datagram to the players' sockets as (path, data), read in the call: video_key's is on its stack."""
    out = []
    fw.mock('fw_sendto', lambda s, data, n, flags, to, *_: out.append((C.string_at(to + 2), C.string_at(data, n))) or n)
    return out


@pytest.mark.parametrize('other', [dict(radio=9), dict(spotify=1)])
def test_a_video_takes_the_keys_before_a_radio_or_spotify(fw, other):
    Player(fw)
    players(fw, video=5, **other)
    out = commands(fw)
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert out == [(b'/tmp/q2video.sock', b'p')]


def test_a_focused_radio_page_takes_the_keys_from_a_video(fw):
    Player(fw)
    players(fw, video=5)
    rd, keep = radio(fw)
    page = top_window(fw, 'radio_page')
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert sent(fw) == [b'q'] and rd.pid == 0 and fw.var('books__vid', Vid).paused == 0


@pytest.mark.parametrize('playing, command', [(0, b's'), (1, b'P')])
def test_spotify_is_paused_and_resumed_by_its_own_commands(fw, playing, command):
    Player(fw)
    players(fw, spotify=1)
    fw.var('spotify__sp', C.c_int * 8)[2] = not playing
    fw.fn('media_set_playing', None, C.c_int)(playing)
    assert sent(fw) == [command]


def test_no_window_is_no_focus(fw):
    fw.mock('window_manager_get_top_window', lambda *_: 0)
    assert fw.fn('media_focused', C.c_int)() == 0


def test_other_keys_go_nowhere(fw):
    Player(fw)
    fw.fn('media_key', None, C.c_uint)(218)
    assert not [n for n, _ in fw.calls() if n.startswith('player_')]


@pytest.mark.parametrize('status, playing', [(PLAYING, 0), (PAUSED, 1), (STOPPED, 1)])
def test_set_playing_toggles_only_to_change(fw, status, playing):
    player = Player(fw, status=status)
    fw.fn('media_set_playing', None, C.c_int)(playing)
    fw.fn('media_set_playing', None, C.c_int)(playing)
    assert player.toggles() == 1 and hs(fw).automatic == 0


def test_an_automatic_change_is_no_manual_command(fw):
    """A play the ears started doesn't cancel the next resume, as a press does."""
    player = Player(fw)
    epoch = hs(fw).epoch
    fw.mock('player_play_pause', lambda *_: fw.fn('headset_manual', None)() or player.toggle())
    fw.fn('media_set_playing', None, C.c_int)(0)
    assert hs(fw).epoch == epoch
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert hs(fw).epoch == epoch + 2  # the key's, and stock's play inside it


@pytest.mark.parametrize('status', [STOPPED, PLAYING, PAUSED])
def test_play_pause_is_a_manual_command_then_stock_s(fw, status):
    Player(fw, status=status)
    fw.mock('stock_pause_trampoline', lambda *_: 61)
    epoch = hs(fw).epoch
    assert fw.fn('ringnav_play_pause', C.c_int)() == 61 and hs(fw).epoch == epoch + 1


def peq_path(name):
    """A file demo shares with hciplayer, as peq.h names it (PEQ_ROOT is empty in this build)."""
    found = re.search(rf'#define {name} PEQ_ROOT "([^"]+)"', (ROOT/'patch/peq.h').read_text())
    assert found, name
    return found[1]


class Shared:
    """/tmp as demo's shared words reach it: a file opened, written whole, mapped, then renamed into place. `words` is
    the word at each final path, as hciplayer would map it."""
    def __init__(self, fw):
        self.paths, self.maps, self.words = {}, {}, {}
        fw.mock('fw_open', self.open)
        fw.mock('fw_write', lambda fd, data, n, *_: n)
        fw.mock('fw_mmap', self.mmap)
        fw.mock('fw_rename', self.rename)
        fw.mock('fw_close', lambda *_: 0)

    def open(self, path, *_):
        fd = 20 + len(self.paths)
        self.paths[fd] = C.string_at(path).decode()
        return fd

    def mmap(self, addr, n, prot, flags, fd, *_):
        word = self.maps[self.paths[fd]] = C.c_int(0)
        return C.addressof(word)

    def rename(self, old, new, *_):
        self.words[C.string_at(new).decode()] = self.maps.pop(C.string_at(old).decode())
        return 0


@pytest.mark.parametrize('status, mark', [(STOPPED, 'PRESS_FILE'), (PLAYING, 'MUTE_FILE'), (PAUSED, None)])
def test_play_pause_marks_a_start_or_a_pause_for_the_audio_path(fw, mcl, status, mark):
    """peq.h: demo writes now_ms, never 0, at a press that starts a track (PRESS_FILE, where hciplayer takes an odd word
    for a press's) and just before a pause's mute (MUTE_FILE). From a stop stock starts a track; from playing it mutes,
    then pauses; from a pause it plays on, and nothing is marked."""
    Player(fw, status=status)
    mcl.type = 1  # a folder's queue, whose starts press_stamp marks
    shared = Shared(fw)
    fw.mock('fw_clock_gettime', lambda which, t, *_: struct.pack_into('<qq', (C.c_char * 16).from_address(t), 0,
                                                                        1234, 568000000))  # now_ms 1234568, even
    fw.fn('ringnav_play_pause', C.c_int)()
    words = {path: word.value for path, word in shared.words.items()}
    assert words == ({peq_path(mark): 1234568 | 1} if mark else {})


# The link (headset_poll).

def flags(fw, on=1, link=1, receiving=0):
    fw.var('g_bluetoothflag', C.c_ubyte).value = on
    fw.var('bt_linkstatus', C.c_ubyte).value = link
    fw.var('bt__recv_pageflag', C.c_ubyte).value = receiving


def poll(fw, player, step=100):
    player.now += step
    fw.fn('headset_poll', None)()


def threads(fw, ok=True):
    started = []
    def create(t, attr, fn, arg, *_):
        started.append((fn, arg))
        C.c_ulong.from_address(t).value = 0x1d
        return 0 if ok else 11
    fw.mock('fw_pthread_create', create)
    return started


def test_a_link_starts_one_worker_of_its_generation(fw):
    player, started = Player(fw), threads(fw)
    flags(fw)
    poll(fw, player)
    state = hs(fw)
    assert state.linked == 1 and state.linked_at == player.now and state.running == 1
    assert [arg for _, arg in started] == [state.generation] and state.worker_generation == state.generation
    assert [a[0] for _, a in fw.calls('fw_pthread_detach')] == [0x1d]
    poll(fw, player)
    assert len(started) == 1  # running


def test_a_worker_that_can_t_start_is_tried_again(fw):
    player, started = Player(fw), threads(fw, ok=False)
    flags(fw)
    poll(fw, player)
    assert hs(fw).running == 0
    poll(fw, player)
    assert len(started) == 2


@pytest.mark.parametrize('on, link, receiving', [(0, 1, 0), (1, 0, 0), (1, 1, 1)])
def test_no_link_no_worker(fw, on, link, receiving):
    player, started = Player(fw), threads(fw)
    flags(fw, on, link, receiving)  # receiving: a phone plays to the Q2
    poll(fw, player)
    assert started == [] and hs(fw).linked == 0


def test_a_poll_within_100_ms_does_nothing(fw):
    player, started = Player(fw), threads(fw)
    poll(fw, player)
    flags(fw)
    poll(fw, player, step=99)
    assert started == []
    poll(fw, player, step=1)
    assert len(started) == 1


def test_the_link_dropping_pauses_and_ends_the_worker_s_generation(fw):
    player = Player(fw)
    threads(fw)
    flags(fw)
    poll(fw, player)
    generation = hs(fw).generation
    ears(fw, player, 3)
    flags(fw, link=0)
    poll(fw, player)
    state = hs(fw)
    assert player.toggles() == 1 and state.generation == generation + 1 and state.known == 0 and state.linked == 0


def queue(fw, *codes):
    state = hs(fw)
    for code in codes:
        state.keys[state.head % 16] = code
        state.head += 1


def test_queued_keys_are_dispatched_in_order(fw):
    player = Player(fw)
    threads(fw)
    flags(fw)
    poll(fw, player)
    queue(fw, NEXT, PREV, NEXT)
    poll(fw, player)
    assert [n for n, _ in fw.calls() if n in ('player_next_music', 'player_prev_music')] == \
        ['player_next_music', 'player_prev_music', 'player_next_music'] and hs(fw).tail == hs(fw).head


def test_a_stale_worker_s_keys_wait(fw):
    player = Player(fw)
    threads(fw)
    flags(fw)
    poll(fw, player)
    hs(fw).worker_generation -= 1  # the old link's worker is still winding down
    queue(fw, NEXT)
    poll(fw, player)
    assert fw.calls('player_next_music') == [] and hs(fw).tail == 0


def test_the_poll_reads_the_ears(fw):
    player = Player(fw)
    threads(fw)
    flags(fw)
    poll(fw, player)
    hs(fw).report = 4 | 3
    poll(fw, player)
    hs(fw).report = 4 | 1
    poll(fw, player)
    assert player.toggles() == 1


def test_a_link_without_ear_reports_pauses_nothing(fw):
    """A headset that never sends an ear report (anything but AirPods) leaves what plays alone."""
    player = Player(fw)
    threads(fw)
    flags(fw)
    for _ in range(3):
        poll(fw, player)
    assert player.toggles() == 0 and hs(fw).linked == 1


class BtVol(C.Structure):  # navigation.c btvol: the headset's volume sync
    _fields_ = [('checked', C.c_uint), ('ready', C.c_int), ('last', C.c_int), ('failed', C.c_int)]


@pytest.mark.parametrize('before, after', [(0, 1), (1, 0)])
def test_each_link_change_syncs_the_headset_s_volume_afresh(fw, before, after):
    player = Player(fw, status=PAUSED)
    threads(fw)
    flags(fw, link=before)
    poll(fw, player)
    btvol = fw.var('navigation__btvol', BtVol)
    btvol.ready = 1
    poll(fw, player)
    assert btvol.ready == 1  # no change, no reset
    flags(fw, link=after)
    poll(fw, player)
    assert btvol.ready == 0


def test_a_new_link_s_first_report_isn_t_measured_against_the_last_link_s(fw):
    player = Player(fw)
    threads(fw)
    flags(fw)
    poll(fw, player)
    hs(fw).report = 4 | 3
    poll(fw, player)  # both buds in
    flags(fw, link=0)
    poll(fw, player)  # the link drops: playing pauses
    hs(fw).running = 0  # its worker ends
    player.status = PLAYING  # played on the Q2 since
    flags(fw)
    poll(fw, player)  # linked again: a new worker, no report yet
    hs(fw).report = 4 | 1
    poll(fw, player)  # its first report: one bud in
    assert player.toggles() == 1


# Which headset (headset_start): AirPods by the paired name of the linked MAC.

@pytest.fixture
def pairs():
    """stock's pdeq_btpairlist at its fixed address, put back after (conftest's map outlives a load)."""
    claim(PAIRS)
    saved = C.c_void_p.from_address(PAIRS).value
    C.c_void_p.from_address(PAIRS).value = 0
    yield
    C.c_void_p.from_address(PAIRS).value = saved


def start(fw, mac, paired, linked=1):
    """headset_start with `linked` links, the last mac's (stock's getBtLinkItem fills nothing for an index out of
    range), and the pair list's entries (MAC at 4, name at 0x24; None a missing one) in stock's deque."""
    entries, at = [], []
    for entry in paired:
        e = C.create_string_buffer(0x80)
        if entry:
            pmac, name = entry
            C.memmove(C.addressof(e) + 4, pmac.encode(), len(pmac))
            C.memmove(C.addressof(e) + 0x24, name.encode(), len(name))
        entries.append(e)
        at.append(C.addressof(e) if entry else 0)
    deque = 0x6100
    C.c_void_p.from_address(PAIRS).value = deque if paired else 0
    fw.mock('fw_deque_size', lambda q, *_: len(at) if q == deque else 0)
    fw.mock('fw_deque_at', lambda q, i, *_: at[i] if q == deque else 0)
    macs = ['11:22:33:44:55:66'] * (linked - 1) + [mac] * (linked > 0)

    def item(i, status, out, *_):
        if not 0 <= i < len(macs):
            return -1
        C.memmove(out, macs[i].encode() + b'\0', len(macs[i]) + 1)
        return 0
    fw.mock('getBtLinkSize', lambda *_: linked)
    fw.mock('getBtLinkItem', item)
    threads(fw)
    fw.fn('headset__headset_start', None)()
    return hs(fw)


def test_airpods_are_found_by_name_and_their_address_is_l2cap_s(fw, pairs):
    hs(fw).address[:] = [0x5a] * 14  # the last link's
    state = start(fw, 'AA:BB:CC:DD:EE:FF', [('11:22:33:44:55:66', 'Speaker'), ('AA:BB:CC:DD:EE:FF', 'AirPods Pro')])
    assert state.airpods == 1
    # sockaddr_l2: AF_BLUETOOTH (31), PSM 0x1001 little-endian, the address reversed, then CID and address type 0
    assert bytes(state.address) == bytes([31, 0, 1, 16, 0xff, 0xee, 0xdd, 0xcc, 0xbb, 0xaa, 0, 0, 0, 0])


@pytest.mark.parametrize('mac, paired', [
    ('AA:BB:CC:DD:EE:FF', [('AA:BB:CC:DD:EE:FF', 'Bose QC')]),  # not AirPods
    ('AA:BB:CC:DD:EE:FF', [('AA:BB:CC:DD:EE:00', 'AirPods')]),  # another device's
    ('AA:BB:CC:DD:EE', [('AA:BB:CC:DD:EE', 'AirPods')]),  # not a MAC
    ('AA:BB:CC:DD:EE:FG', [('AA:BB:CC:DD:EE:FG', 'AirPods')]), ('AA:BB:CC:DD:EE:GF', [('AA:BB:CC:DD:EE:GF', 'AirPods')]),
    ('AA-BB:CC:DD:EE:FF', [('AA-BB:CC:DD:EE:FF', 'AirPods')]), ('AA:BB:CC:DD:EE-FF', [('AA:BB:CC:DD:EE-FF', 'AirPods')]),
    ('AA:BB:CC:DD:EE:FF:', [('AA:BB:CC:DD:EE:FF:', 'AirPods')]),
    ('AA:BB:CC:DD:EE:FF', []),
])
def test_anything_else_is_no_airpods(fw, pairs, mac, paired):
    hs(fw).airpods = 1  # the last link's were
    assert start(fw, mac, paired).airpods == 0


@pytest.mark.parametrize('mac, address', [('aa:bb:cc:dd:ee:0f', [0x0f, 0xee, 0xdd, 0xcc, 0xbb, 0xaa]),
                                          ('10:20:30:40:50:60', [0x60, 0x50, 0x40, 0x30, 0x20, 0x10])])
def test_a_mac_reads_as_hex_in_either_case(fw, pairs, mac, address):
    state = start(fw, mac, [(mac, 'AirPods Max')])
    assert state.airpods == 1 and bytes(state.address[4:10]) == bytes(address)


@pytest.mark.parametrize('linked, paired, airpods', [
    (2, 'AA:BB:CC:DD:EE:FF', 1),
    (2, '11:22:33:44:55:66', 0),  # an earlier link's AirPods
    (0, 'AA:BB:CC:DD:EE:FF', 0),  # no link
])
def test_the_headset_is_the_last_link_s(fw, pairs, linked, paired, airpods):
    assert start(fw, 'AA:BB:CC:DD:EE:FF', [(paired, 'AirPods')], linked=linked).airpods == airpods


def test_a_missing_pair_entry_is_passed_over(fw, pairs):
    assert start(fw, 'AA:BB:CC:DD:EE:FF', [None, ('AA:BB:CC:DD:EE:FF', 'AirPods')]).airpods == 1


def test_no_pair_list_is_asked_nothing(fw, pairs):
    assert start(fw, 'AA:BB:CC:DD:EE:FF', []).airpods == 0 and fw.calls('fw_deque_size') == []


def test_a_start_clears_the_last_link_s_state(fw, pairs):
    state = hs(fw)
    state.report, state.head, state.tail, state.known, state.paused = 7, 3, 2, 1, 1
    state = start(fw, 'AA:BB:CC:DD:EE:FF', [])
    assert (state.report, state.head, state.tail, state.known, state.paused) == (0, 0, 0, 0, 0)


# The worker, over a simulated kernel numbered as the Q2's MIPS headers number it (asm/fcntl.h, asm/socket.h,
# asm/ioctl.h).

AAP, INPUT = 10, 11
AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP = 31, 5, 0
F_SETFL, O_NONBLOCK, O_CLOEXEC = 4, 0x80, 0x80000
SOL_SOCKET, SO_ERROR, MSG_DONTWAIT = 0xffff, 0x1007, 0x40
IOC_SIZE = 0x1fff << 16  # an ioctl request's size field: 13 bits on MIPS


def EVIOCGNAME(size):
    """_IOC(_IOC_READ, 'E', 0x06, size): _IOC_READ is 2 on MIPS."""
    return 2 << 29 | size << 16 | ord('E') << 8 | 0x06


FD_SETSIZE = 1024
EAR = bytes([4, 0, 4, 0, 6, 0, 0, 0])  # an ear report: both buds in
OTHER = bytes([4, 0, 4, 0, 0x17, 0, 1, 2, 3])  # another report


class Kernel:
    """The worker's kernel, as select(2), sockets and evdev behave. AACP packets and input event batches wait to be
    read (None: the socket closes, the device goes) once there's a connection. Without `l2cap` no socket can be made;
    a connect returns `connect` (-1: in progress) and completes `connected` ms after it starts (None: never), refused
    with errno `refused` if given. `bad` descriptors fail select until closed, and the first `errors` selects fail
    outright. Descriptors are `aap` and `input` (a list: one for each open, the last again). usleep and select's
    timeouts move a clock. When `passes` waits find nothing, the link's generation moves on, so the worker ends;
    `drop`, (call, n), moves it on at the nth recv, read or wait for the connect instead, as the UI loop would. A call
    on a descriptor the worker doesn't hold is `misused`."""
    def __init__(self, fw, packets=(), batches=(), device=3, name=b'AirPods Pro (AVRCP)', l2cap=True, connect=0,
                 connected=0, refused=0, passes=8, fcntl=0, errors=0, bad=(), aap=AAP, input=INPUT, drop=None):
        self.fw, self.packets, self.batches, self.device, self.name = fw, list(packets), list(batches), device, name
        self.l2cap, self.connect_result, self.connected, self.refused = l2cap, connect, connected, refused
        self.passes = passes
        self.fcntl_result, self.errors, self.bad, self.drop = fcntl, errors, set(bad), drop
        self.numbers = {'aacp': aap if isinstance(aap, list) else [aap],
                        'input': input if isinstance(input, list) else [input]}
        self.fds, self.clock, self.connect_at, self.nonblocking, self.error_read = {}, 0.0, None, False, False
        self.sent, self.delivered, self.closed, self.opened, self.open_flags = [], 0, [], [], []
        self.sockets, self.recvs, self.events, self.waits, self.slept, self.queued, self.misused = [], [], [], [], [], [], []
        for call in ('socket', 'fcntl', 'connect', 'getsockopt', 'send', 'recv', 'open', 'ioctl', 'read', 'close',
                     'select', 'usleep'):
            fw.mock(f'fw_{call}', getattr(self, call))

    def run(self, airpods=1):
        """The worker of link generation 5, to its end: it may touch only the descriptors it holds, and leaves none
        open."""
        state = hs(self.fw)
        state.airpods, state.running, state.generation = airpods, 1, 5
        state.address[:] = [31, 0, 1, 16, 1, 2, 3, 4, 5, 6, 0, 0, 0, 0]
        assert self.fw.fn('headset__headset_worker', C.c_void_p, C.c_void_p)(5) is None
        assert self.misused == [] and self.fds == {} and state.running == 0
        return state

    def give(self, kind):
        numbers = self.numbers[kind]
        fd = numbers.pop(0) if len(numbers) > 1 else numbers[0]
        while fd in self.fds:  # one the worker still holds: the next free one
            fd += 1
        self.fds[fd] = kind
        return fd

    def holds(self, call, fd, kind):
        if self.fds.get(fd) == kind:
            return True
        self.misused.append((call, fd))
        return False

    def moves_on(self, call):
        if self.drop and self.drop[0] == call:
            self.drop = (call, self.drop[1] - 1)
            if not self.drop[1]:
                hs(self.fw).generation += 1

    def idle(self):
        self.passes -= 1
        if self.passes <= 0:
            hs(self.fw).generation += 1

    def socket(self, domain, kind, protocol, *_):
        self.events.append('socket')
        self.sockets.append((domain, kind, protocol))
        if not self.l2cap or (domain, kind, protocol) != (AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP):
            return -1  # nothing else here
        self.connect_at, self.nonblocking, self.error_read = None, False, False
        return self.give('aacp')

    def fcntl(self, fd, cmd, arg, *_):
        if fd not in self.fds:
            self.misused.append(('fcntl', fd))
            return -1
        if self.fcntl_result:
            return self.fcntl_result
        if cmd == F_SETFL:
            self.nonblocking = bool(arg & O_NONBLOCK)
        return 0

    def connect(self, fd, address, n, *_):
        if not self.holds('connect', fd, 'aacp'):
            return -1
        self.sent.append(('connect', C.string_at(address, n)))
        if not self.nonblocking:
            self.misused.append(('a connect that blocks', fd))
        self.connect_at = self.clock
        return self.connect_result

    def done(self):
        """Whether the connect has completed, made or refused."""
        if self.connect_at is None:
            return False
        if self.connect_result == 0:
            return True
        return self.connected is not None and self.clock >= self.connect_at + self.connected

    def established(self):
        return self.done() and not self.refused

    def getsockopt(self, fd, level, name, out, size, *_):
        if not self.holds('getsockopt', fd, 'aacp') or (level, name) != (SOL_SOCKET, SO_ERROR):
            return -1
        C.c_int.from_address(out).value = self.refused if self.done() and not self.error_read else 0
        self.error_read |= self.done()  # SO_ERROR reads once
        return 0

    def send(self, fd, data, n, flags, *_):
        if not self.holds('send', fd, 'aacp'):
            return -1
        self.sent.append((C.string_at(data, n), flags))
        if not self.established() or flags & ~MSG_DONTWAIT:
            return -1
        self.delivered += 1
        return n

    def recv(self, fd, buf, n, flags, *_):
        if not self.holds('recv', fd, 'aacp'):
            return -1
        self.recvs.append(flags)
        self.moves_on('recv')
        if flags & ~MSG_DONTWAIT or not self.established() or not self.packets:
            return -1  # EAGAIN: drained; or not connected
        packet = self.packets.pop(0)
        if packet is None:
            return 0  # the socket closed
        packet = packet[:n]  # a longer one is cut to the buffer
        C.memmove(buf, packet, len(packet))
        return len(packet)

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        self.opened.append(path)
        self.open_flags.append(flags)
        if path == '/dev/input/event0':
            self.events.append('scan')
        if self.device is None or path != f'/dev/input/event{self.device}':
            return -1
        return self.give('input')

    def ioctl(self, fd, request, out, *_):
        """evdev's EVIOCGNAME(len), whatever the len: str_to_user copies the name and its NUL, cut to len (a longer
        name arrives without its NUL), and returns the bytes copied."""
        if not self.holds('ioctl', fd, 'input') or request & ~IOC_SIZE != EVIOCGNAME(0):
            return -1  # no other request here
        name = (self.name + b'\0')[:(request & IOC_SIZE) >> 16]
        C.memmove(out, name, len(name))
        return len(name)

    def read(self, fd, buf, n, *_):
        if not self.holds('read', fd, 'input'):
            return -1
        self.moves_on('read')
        if not self.batches or n < 16:
            return -1  # EAGAIN, or EINVAL for room for less than an event
        batch = self.batches.pop(0)
        if batch is None:
            return 0  # the device went
        if len(batch) > n // 16:  # as many whole events as fit; the rest wait
            self.batches.insert(0, batch[n // 16:])
            batch = batch[:n // 16]
        data = b''.join(struct.pack('<IIHHi', 0, 0, *event) for event in batch)
        C.memmove(buf, data, len(data))
        return len(data)

    def close(self, fd, *_):
        if self.fds.pop(fd, None) is None:
            self.misused.append(('close', fd))
            return -1
        self.closed.append(fd)
        self.bad.discard(fd)
        return 0

    def readable(self, fd):
        kind = self.fds.get(fd)
        return bool(self.packets) if kind == 'aacp' else bool(self.batches) if kind == 'input' else False

    def writable(self, fd, timeout):
        """A socket is writable once its connect completes, before the wait or within it."""
        if self.fds.get(fd) != 'aacp' or self.connect_at is None:
            return False
        if self.connect_result == 0:
            return True
        if self.connected is None or self.clock + timeout < self.connect_at + self.connected:
            return False
        self.clock = max(self.clock, self.connect_at + self.connected)
        return True

    def select(self, n, r, w, e, tv, *_):
        timeout = C.c_long.from_address(tv).value * 1000 + C.c_long.from_address(tv + 8).value / 1000
        sets = [(C.c_uint * (FD_SETSIZE // 32)).from_address(s) if s else None for s in (r, w)]
        asked = [[fd for fd in range(FD_SETSIZE) if s and s[fd // 32] >> fd % 32 & 1] for s in sets]
        self.waits.append((n, *asked, timeout))
        self.queued.append(len(self.packets))
        if r:
            self.events.append('select')
        else:
            self.moves_on('wait')
        if n > FD_SETSIZE:
            self.misused.append(('select', n))
        self.misused += [('select', fd) for fds in asked for fd in fds if fd < n and fd not in self.fds]
        if self.errors:
            self.errors -= 1
            return -1
        if self.bad & {fd for fds in asked for fd in fds if fd < n}:
            return -1  # EBADF
        ready = [[fd for fd in asked[0] if fd < n and self.readable(fd)],
                 [fd for fd in asked[1] if fd < n and self.writable(fd, timeout)]]
        for s, fds, keep in zip(sets, asked, ready):
            for fd in set(fds) - set(keep):
                if s is not None:
                    s[fd // 32] &= ~(1 << fd % 32)
        if ready[0] or ready[1]:
            return len(ready[0]) + len(ready[1])
        self.clock += timeout
        if r:
            self.idle()
        return 0

    def usleep(self, us, *_):
        self.events.append('sleep')
        self.slept.append(us)
        self.clock += us / 1000
        self.idle()


def test_the_worker_greets_airpods(fw):
    kernel = Kernel(fw)
    kernel.run()
    assert kernel.sockets == [(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP)]
    assert kernel.sent[0] == ('connect', bytes([31, 0, 1, 16, 1, 2, 3, 4, 5, 6, 0, 0, 0, 0]))
    hello, features, notify = kernel.sent[1:]
    assert hello[0] == bytes([0, 0, 4, 0, 1, 0, 2, 0]) + bytes(8)
    assert features[0][:7] == bytes([4, 0, 4, 0, 0x4d, 0, 0xd7]) and len(features[0]) == 14
    assert notify[0] == bytes([4, 0, 4, 0, 0xf, 0, 255, 255, 255, 255])
    assert {flags for _, flags in kernel.sent[1:]} == {MSG_DONTWAIT} and kernel.delivered == 3  # nonblocking sends
    assert sorted(kernel.closed) == [AAP, INPUT]


def test_a_connect_still_under_way_half_a_second_in_is_waited_for(fw):
    """A slow L2CAP connect, completing well inside the pass's second (docs/internals.md), is used: the first wait
    finding it still under way doesn't give it up."""
    kernel = Kernel(fw, connect=-1, connected=500)
    kernel.run()
    assert len(kernel.sockets) == 1 and kernel.delivered == 3


def test_a_connect_that_never_completes_is_given_up_within_a_second(fw):
    kernel = Kernel(fw, connect=-1, connected=None, passes=4)
    kernel.run()
    assert 0 < sum(timeout for n, r, w, timeout in kernel.waits if w) <= 1000
    assert [s for s in kernel.sent if s[0] != 'connect'] == [] and AAP in kernel.closed


def test_a_link_that_drops_during_the_connect_ends_its_wait(fw):
    kernel = Kernel(fw, connect=-1, connected=None, drop=('wait', 1))
    kernel.run()
    assert len([w for n, r, w, timeout in kernel.waits if w]) == 1 and kernel.sent[1:] == []


def test_a_refused_connect_sends_nothing(fw):
    kernel = Kernel(fw, connect=-1, refused=111)
    kernel.run()
    assert [s for s in kernel.sent if s[0] != 'connect'] == [] and AAP in kernel.closed


def test_a_socket_that_won_t_go_nonblocking_is_closed_unused(fw):
    kernel = Kernel(fw, fcntl=-1)
    kernel.run()
    assert kernel.sent == [] and AAP in kernel.closed


def test_the_worker_drains_every_report_in_one_pass_and_keeps_the_last_ear_one(fw):
    kernel = Kernel(fw, packets=[OTHER, bytes([4, 0, 4, 0, 6, 0, 1, 0]), OTHER, EAR, OTHER])
    assert kernel.run().report == 4 | 3
    assert kernel.queued[:2] == [5, 0] and set(kernel.recvs) == {MSG_DONTWAIT}  # all read before the next select


def test_a_pass_reads_64_reports_the_last_included(fw):
    assert Kernel(fw, packets=[OTHER] * 63 + [EAR]).run().report == 4 | 3


@pytest.mark.xfail(strict=True, reason='BUG: a pass that drains 64 reports reads a 65th into its buffer, then leaves '
                                       'it unparsed: the ear report is lost, not left for the next pass')
def test_a_65th_report_is_left_for_the_next_pass(fw):
    assert Kernel(fw, packets=[OTHER] * 64 + [EAR]).run().report == 4 | 3


def test_a_stale_worker_publishes_no_more_reports(fw):
    """The link drops as the worker drains the socket: what it reads after is no longer the link's."""
    assert Kernel(fw, packets=[OTHER, EAR], drop=('recv', 1)).run().report == 0


def test_a_closed_socket_is_closed_and_tried_again(fw):
    kernel = Kernel(fw, packets=[None], passes=12)
    kernel.run()
    assert kernel.closed.count(AAP) >= 2 and len(kernel.sockets) >= 2


def test_airpods_without_an_avrcp_device_still_report_their_ears(fw):
    assert Kernel(fw, device=None, packets=[EAR]).run().report == 4 | 3


def test_the_worker_queues_presses_only(fw):
    press, release, repeat = 1, 0, 2
    kernel = Kernel(fw, batches=[[(1, PLAYPAUSE, press), (1, PLAYPAUSE, release), (1, PLAYPAUSE, repeat),
                                  (4, 4, 0x9000c), (4, 4, 1), (1, NEXT, press)], [(0, 0, 0), (1, PREV, press)]])
    state = kernel.run()
    assert state.head == 3 and list(state.keys[:3]) == [PLAYPAUSE, NEXT, PREV]
    assert kernel.closed == [INPUT, AAP]  # at the end, the AACP socket undisturbed by the keys


def test_each_read_s_events_are_queued_once(fw):
    state = Kernel(fw, batches=[[(1, NEXT, 1), (1, PREV, 1)], [(1, PLAYPAUSE, 1)]]).run()
    assert state.head == 3 and list(state.keys[:3]) == [NEXT, PREV, PLAYPAUSE]


def test_the_worker_s_queue_holds_16(fw):
    assert Kernel(fw, batches=[[(1, NEXT, 1)] * 8, [(1, NEXT, 1)] * 8, [(1, PREV, 1)] * 8]).run().head == 16


def test_the_queue_takes_presses_after_the_ui_has_taken_some(fw):
    state = hs(fw)
    state.head = state.tail = 20  # 20 pressed and dispatched
    Kernel(fw, batches=[[(1, NEXT, 1)]]).run()
    assert state.head == 21 and state.keys[20 % 16] == NEXT


def test_presses_come_out_in_order_round_the_queue_s_end(fw):
    """The worker queues and the UI loop dispatches round the 16 slots."""
    player = Player(fw)
    state = hs(fw)
    state.head = state.tail = 14
    Kernel(fw, batches=[[(1, NEXT, 1), (1, PREV, 1), (1, NEXT, 1), (1, PREV, 1)]]).run(airpods=0)
    flags(fw)
    state.linked, state.running, state.worker_generation = 1, 1, state.generation  # the link and its worker
    poll(fw, player)
    assert [n for n, _ in fw.calls() if n in ('player_next_music', 'player_prev_music')] == \
        ['player_next_music', 'player_prev_music'] * 2


def test_a_stale_worker_queues_no_more_keys(fw):
    assert Kernel(fw, batches=[[(1, NEXT, 1)]], drop=('read', 1)).run().head == 0


def test_the_worker_finds_the_avrcp_device_by_name(fw):
    kernel = Kernel(fw, device=6, batches=[[(1, NEXT, 1)]])
    state = kernel.run()
    assert kernel.opened[:7] == [f'/dev/input/event{i}' for i in range(7)] and state.head == 1
    assert set(kernel.open_flags) == {O_NONBLOCK | O_CLOEXEC}  # read-only (0), nonblocking, not for a child


def test_a_scan_opens_each_node_in_turn_up_to_its_last(fw):
    """The scan's own bound, whatever it is (evdev numbers nodes past event31 too): event0 upwards, each node once,
    and a device at the last node it opens is found."""
    kernel = Kernel(fw, device=None, passes=1)
    kernel.run(airpods=0)
    last = len(kernel.opened) - 1
    assert last > 0 and kernel.opened == [f'/dev/input/event{i}' for i in range(last + 1)]
    assert Kernel(fw, device=last, batches=[[(1, NEXT, 1)]]).run(airpods=0).head == 1


def test_a_device_name_is_read_to_its_63rd_character(fw):
    name = b'x' * 56 + b'(AVRCP)'  # 63 characters, then EVIOCGNAME(64)'s NUL
    assert Kernel(fw, name=name, batches=[[(1, NEXT, 1)]]).run(airpods=0).head == 1


@pytest.mark.parametrize('length', [64, 79])
def test_a_device_name_past_63_characters_still_ends_in_avrcp(fw, length):
    """One character past what a 64-byte read holds with its NUL, and uinput's longest (UINPUT_MAX_NAME_SIZE 80, its
    NUL included)."""
    name = b'x' * (length - 8) + b' (AVRCP)'
    assert Kernel(fw, name=name, batches=[[(1, NEXT, 1)]]).run(airpods=0).head == 1


@pytest.mark.parametrize('device', [3, 31])
def test_a_device_by_another_name_isn_t_it(fw, device):
    kernel = Kernel(fw, device=device, name=b'gpio-keys', batches=[[(1, NEXT, 1)]])
    state = kernel.run(airpods=0)
    assert state.head == 0 and INPUT in kernel.closed


def test_without_airpods_no_socket(fw):
    kernel = Kernel(fw)
    kernel.run(airpods=0)
    assert kernel.sockets == []


def test_without_l2cap_the_keys_still_work_and_aacp_is_tried_again(fw):
    kernel = Kernel(fw, l2cap=False, batches=[[(1, NEXT, 1)]], passes=7)
    assert kernel.run().head == 1 and len(kernel.sockets) == 2  # passes 0 and 5


def test_a_device_that_goes_is_closed_and_found_again(fw):
    kernel = Kernel(fw, batches=[None, [(1, NEXT, 1)]])
    state = kernel.run(airpods=0)
    assert kernel.closed.count(INPUT) >= 2 and state.head == 1


def test_a_failing_select_closes_what_failed_and_finds_them_again(fw):
    kernel = Kernel(fw, errors=3, batches=[[(1, NEXT, 1)]])
    state = kernel.run()
    assert kernel.closed[:2] == [AAP, INPUT]  # each taken alone: both bad
    assert kernel.opened.count('/dev/input/event3') >= 2 and state.head == 1


@pytest.mark.parametrize('fds', [(AAP, INPUT), (0, 1), (1, 0)])
@pytest.mark.parametrize('bad, airpods, device', [('input', 1, 3), ('aap', 1, 3), ('input', 0, 3), ('aap', 1, None)])
def test_a_descriptor_gone_bad_alone_is_the_one_closed_and_found_again(fw, bad, airpods, device, fds):
    """select fails for both when one goes bad: each is taken alone, and only the bad one is closed and opened again,
    though the other has nothing to read."""
    aap, input = fds
    gone = {'aap': aap, 'input': input}[bad]
    kernel = Kernel(fw, bad=[gone], device=device, aap=aap, input=input, passes=10,
                    packets=[EAR] if bad == 'aap' else [], batches=[[(1, NEXT, 1)]] if bad == 'input' else [])
    state = kernel.run(airpods=airpods)
    assert kernel.closed[0] == gone and kernel.closed.count(gone) == 2  # and again at the end
    good = [fd for fd, held in ((aap, airpods), (input, device is not None)) if held and fd != gone]
    assert all(kernel.closed.count(fd) == 1 for fd in good)  # at the end only
    assert state.report == (4 | 3 if bad == 'aap' else 0) and state.head == (1 if bad == 'input' else 0)


@pytest.mark.parametrize('aap, input', [(0, 1), (1, 0), (1023, 1000), (1000, 1023), (0, None), (31, None),
                                        (1000, None), (None, 0), (None, 31), (None, 1000)])
def test_any_descriptor_select_can_watch_works(fw, aap, input):
    """Descriptors 0 (stdin closed) to 1023, select's whole range, both or one alone."""
    kernel = Kernel(fw, connect=-1, connected=100, packets=[EAR], batches=[[(1, NEXT, 1)]],
                    aap=AAP if aap is None else aap, input=INPUT if input is None else input,
                    device=None if input is None else 3)
    state = kernel.run(airpods=int(aap is not None))
    assert state.report == (0 if aap is None else 4 | 3) and state.head == (0 if input is None else 1)


@pytest.mark.parametrize('which', ['aap', 'input'])
def test_a_descriptor_past_select_s_reach_is_closed_and_opened_again(fw, which):
    """select watches descriptors under FD_SETSIZE: one at it is closed, never watched (its bit would land past the
    set), and the next open comes back lower."""
    kernel = Kernel(fw, packets=[EAR], batches=[[(1, NEXT, 1)]], passes=10,
                    **{which: [FD_SETSIZE, {'aap': AAP, 'input': INPUT}[which]]})
    state = kernel.run(airpods=int(which == 'aap'))
    assert FD_SETSIZE in kernel.closed and (state.report if which == 'aap' else state.head)


def test_a_quiet_link_wakes_once_a_second(fw):
    """docs/internals.md: one select over both descriptors, up to 1 s a pass."""
    kernel = Kernel(fw, passes=5)
    kernel.run()
    assert [(r, w, timeout) for n, r, w, timeout in kernel.waits] == [([AAP, INPUT], [], 1000)] * 5
    assert all(max(AAP, INPUT) < n <= FD_SETSIZE for n, *_ in kernel.waits)  # any nfds that covers both


def per_pass(events, end):
    """The worker's events a pass at a time, in no order within a pass: each pass ends at its `end` (its sleep, or its
    select)."""
    passes: list[list[str]] = [[]]
    for event in events:
        if event == end:
            passes.append([])
        else:
            passes[-1].append(event)
    if not passes[-1]:
        passes.pop()  # the last pass ended; anything after it stays, as a pass of its own
    return [sorted(p) for p in passes]


def test_with_nothing_held_a_pass_sleeps_a_second_and_retries_on_schedule(fw):
    """docs/internals.md: the AVRCP input is looked for each pass for the first 10, then every 2nd; AACP every 5th."""
    kernel = Kernel(fw, connect=-1, refused=111, device=None, passes=15)
    kernel.run()
    want = [sorted(['socket'] * (t % 5 == 0) + ['scan'] * (t < 10 or t % 2 == 0)) for t in range(15)]
    assert per_pass(kernel.events, 'sleep') == want and kernel.slept == [1000000] * 15


def test_with_airpods_up_the_input_is_looked_for_on_schedule(fw):
    kernel = Kernel(fw, device=None, passes=15)
    kernel.run()
    want = [sorted(['socket'] * (t == 0) + ['scan'] * (t < 10 or t % 2 == 0)) for t in range(15)]
    assert per_pass(kernel.events, 'select') == want


def test_a_pass_whose_select_fails_counts_towards_the_schedule(fw):
    kernel = Kernel(fw, connect=-1, refused=111, bad=[INPUT], passes=7)
    kernel.run()
    assert len(kernel.sockets) == 2  # passes 0 and 5


def test_a_stale_worker_ends_at_once(fw):
    kernel = Kernel(fw)
    state = hs(fw)
    state.generation, state.running = 6, 1
    assert fw.fn('headset__headset_worker', C.c_void_p, C.c_void_p)(5) is None
    assert state.running == 0 and kernel.opened == [] and kernel.sockets == [] and kernel.misused == []


# The Bluetooth link list.

def links(fw, *items):
    """getBtLinkItem's (status, MAC) by index."""
    fw.mock('getBtLinkSize', lambda *_: len(items))
    def item(i, status, mac, *_):
        C.c_int.from_address(status).value = items[i][0]
        C.memmove(mac, items[i][1].encode() + b'\0', len(items[i][1]) + 1)
        return 0
    fw.mock('getBtLinkItem', item)


@pytest.mark.parametrize('items, paired, result', [
    ([], 1, 1), ([(4, '11:22:33:44:55:66')], 1, 1), ([], 0, 0),
    ([(3, '11:22:33:44:55:66')], 1, 0),  # an incoming link is up: auto-link would append after it
    ([(3, '11:22:33:44:55:66'), (5, 'AA:BB:CC:DD:EE:FF')], 1, 0),
])
def test_autolink_only_when_nothing_is_connected(fw, items, paired, result):
    links(fw, *items)
    fw.mock('checkPairDev', lambda *_: paired)
    assert fw.fn('headset_bt_autolink', C.c_int, C.c_char_p)(b'AA:BB:CC:DD:EE:FF') == result


def test_bt_init_registers_the_lists_and_callbacks_first(fw):
    fw.mock('btctl_create_daemon_thread', lambda *_: 23)
    assert fw.fn('headset_bt_init', C.c_int, C.c_void_p)(0x700) == 23
    order = [n for n, _ in fw.calls() if n.startswith(('init', 'btctl'))]
    assert order == ['initBluetoothParam', 'btctl_set_prompt_callback', 'btctl_device_status_callback',
                     'btctl_set_a2dp_callback', 'btctl_create_daemon_thread']
    assert fw.calls('btctl_create_daemon_thread')[0][1][0] == 0x700


def status_callback(fw):
    fw.fn('headset_status_register', None)()
    (fn, _, *_), = [a for _, a in fw.calls('btctl_device_status_callback')]
    return C.CFUNCTYPE(None, C.c_int, C.c_char_p, C.c_void_p, C.c_void_p)(fn)


@pytest.fixture
def stock_status():
    """x86-64 at stock's status callback logging its first three arguments, and BTLINKMAC's bytes; both put
    back after, as conftest's map outlives a load."""
    claim(STOCK_STATUS)
    claim(LINK_MAC)
    saved = C.string_at(STOCK_STATUS, 64), C.string_at(LINK_MAC, 32)
    log = (C.c_long * 3)()
    code = (b'\x48\xb8' + struct.pack('<Q', C.addressof(log)) + bytes.fromhex('488938 48897008 48895010') + b'\xc3')
    C.memmove(STOCK_STATUS, code, len(code))
    yield log
    C.memmove(STOCK_STATUS, saved[0], 64)
    C.memmove(LINK_MAC, saved[1], 32)


def waiting(fw, target='AA:BB:CC:DD:EE:FF', status=5, linked=0, paired=1, thread=1):
    """A link thread running (bt_connectstatus 1) whose last item is target, connecting (5); the link list's
    changes, read as they're made."""
    changes = []
    fw.mock('addBtLinkItem', lambda mac, status, *_: changes.append(('add', C.string_at(mac), status)))
    fw.mock('updateBtLinkStatus', lambda status, mac, *_: changes.append(('update', status, C.string_at(mac))))
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = thread
    C.memmove(LINK_MAC, target.encode() + b'\0', len(target) + 1)
    links(fw, (status, target))
    fw.mock('getBtLinkStatus', lambda *_: linked)
    fw.mock('checkPairDev', lambda *_: paired)
    return changes


def test_a_paired_device_connecting_while_the_target_s_connect_runs_is_adopted(fw, stock_status):
    changes = waiting(fw)
    name = C.create_string_buffer(b'Speaker')
    status_callback(fw)(3, b'11:22:33:44:55:66', C.addressof(name), None)
    assert changes == [('add', b'11:22:33:44:55:66', 3), ('update', 4, b'AA:BB:CC:DD:EE:FF')]  # the wait's, now 4
    assert list(stock_status) == [0, 0, 0]  # stock never saw it


@pytest.mark.parametrize('event, status, linked, paired, thread, target', [
    (2, 5, 0, 1, 1, 'AA:BB:CC:DD:EE:FF'),  # not a connect
    (3, 4, 0, 1, 1, 'AA:BB:CC:DD:EE:FF'),  # the target isn't connecting
    (3, 5, 1, 1, 1, 'AA:BB:CC:DD:EE:FF'),  # already listed
    (3, 5, 0, 0, 1, 'AA:BB:CC:DD:EE:FF'),  # not paired
    (3, 5, 0, 1, 2, 'AA:BB:CC:DD:EE:FF'),  # no link thread waits
    (3, 5, 0, 1, 1, '00:00:00:00:00:01'),  # the wait is for another link than BTLINKMAC
])
def test_any_other_status_is_stock_s(fw, stock_status, event, status, linked, paired, thread, target):
    changes = waiting(fw, status=status, linked=linked, paired=paired, thread=thread)
    if target != 'AA:BB:CC:DD:EE:FF':
        links(fw, (status, target))
    name = C.create_string_buffer(b'Speaker')
    status_callback(fw)(event, b'11:22:33:44:55:66', C.addressof(name), None)
    assert changes == [] and stock_status[0] == event and stock_status[2] == C.addressof(name)


# EQ per Output's key.

def output_key(fw):
    mac = C.create_string_buffer(32)
    p = fw.fn('headset__eq_output_key', C.c_void_p, C.c_char_p)(mac)
    return C.string_at(p).decode() if p else None


@pytest.mark.parametrize('way, output, bal, po, want', [
    (2, 0, 0, 0, 'usb'),
    (0, 1, 1, 0, '4.4mm'), (0, 0, 0, 1, '3.5mm'), (0, 1, 1, 1, '4.4mm'), (0, 0, 1, 1, '3.5mm'),
    (0, 1, 0, 1, None), (0, 0, 1, 0, None),  # the output's jack is out: nothing to hear
])
def test_the_eq_output_is_what_plays(fw, way, output, bal, po, want):
    fw.mock('mclGetOutputWay', lambda *_: way)
    fw.var('g_headset_output', C.c_ubyte).value = output
    fw.var('g_bal_status', C.c_ubyte).value = bal
    fw.var('g_po_status', C.c_ubyte).value = po
    assert output_key(fw) == want


@pytest.mark.parametrize('items, want', [([(3, 'AA:BB:CC:DD:EE:FF')], 'AA:BB:CC:DD:EE:FF'), ([(5, 'AA:BB:CC:DD:EE:FF')], None),
                                         ([(3, 'AA:BB')], None), ([], None)])
def test_bluetooth_s_eq_output_is_its_device(fw, items, want):
    fw.mock('mclGetOutputWay', lambda *_: 1)
    links(fw, *items)
    assert output_key(fw) == want


@pytest.mark.parametrize('first', [True, False])
@pytest.mark.parametrize('other', [1, 2, 4, 5])
def test_the_connected_link_is_found_among_any_other(fw, other, first):
    fw.mock('mclGetOutputWay', lambda *_: 1)
    items = [(3, 'AA:BB:CC:DD:EE:FF'), (other, '11:22:33:44:55:66')]
    links(fw, *(items if first else items[::-1]))
    assert output_key(fw) == 'AA:BB:CC:DD:EE:FF'


def test_eq_poll_looks_each_half_second_with_eq_per_output_on(fw):
    player = Player(fw)
    config(fw, EQOUTPUT='1')
    looked, start = [], player.now
    fw.mock('mclGetOutputWay', lambda *_: looked.append(player.now - start))
    for step in (500, 100, 399, 1):
        player.now += step
        fw.fn('headset_eq_poll', None)()
    assert looked == [500, 1000]


@pytest.mark.parametrize('way, items, owner', [(2, [], b'usb'), (1, [(3, 'AA:BB:CC:DD:EE:FF')], b'AA:BB:CC:DD:EE:FF')])
def test_eq_poll_hands_the_eq_to_what_plays(fw, way, items, owner):
    player = Player(fw)
    config(fw, EQOUTPUT='1')
    fw.mock('mclGetOutputWay', lambda *_: way)
    links(fw, *items)
    player.now += 500
    fw.fn('headset_eq_poll', None)()
    assert fw.var('peq_ui__eq_owner', C.c_char * 20).value == owner  # peq_ui.c eq_output's: whose EQ plays


def test_eq_poll_with_eq_per_output_off_looks_at_nothing(fw):
    player = Player(fw)
    config(fw, EQOUTPUT='0')
    player.now += 1000
    fw.fn('headset_eq_poll', None)()
    assert fw.calls('mclGetOutputWay') == []
