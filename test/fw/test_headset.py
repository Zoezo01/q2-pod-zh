"""headset.c (docs/internals.md#headset-controls-and-volume): AirPods' ear reports and the AVRCP keys, routed to whichever
player plays; the worker's I/O over simulated sockets and input devices; the Bluetooth link filters; EQ per Output's
key."""
import ctypes as C
import struct

import pytest

from conftest import claim

KEY_PLAY, KEY_BACK_BTN, KEY_FWD_BTN = 171, 222, 223
NEXT, PLAYPAUSE, PREV, STOP, PAUSE, PLAYCD, PAUSECD, PLAYKEY = 163, 164, 165, 166, 119, 200, 201, 207
PLAYING, PAUSED, STOPPED = 2, 3, 1
PAIRS, LINK_MAC, STOCK_STATUS = 0xa38110, 0xa38168, 0x4f1a50  # pdeq_btpairlist, BTLINKMAC, stock's status callback


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
    [4, 0, 4, 0, 6, 0, 3, 0], [4, 0, 4, 0, 6, 0, 0, 255],  # no such state
])
def test_anything_else_publishes_nothing(fw, data):
    hs(fw).report = 0x55
    assert parse(fw, data) == 0 and hs(fw).report == 0x55


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


def test_a_new_track_cancels_the_resume(fw, mcl):
    player = Player(fw)
    mcl.pos = 4
    ears(fw, player, 3, 1)
    mcl.pos = 5  # the queue moved on: another session
    assert ears(fw, player, 3) == [1]


def test_playing_again_since_cancels_the_resume(fw):
    player = Player(fw)
    ears(fw, player, 3, 1)
    player.status = PLAYING  # started some other way
    assert ears(fw, player, 1, step=1600) == [1]  # past 1.5 s: the pause is over
    player.status = PAUSED
    assert ears(fw, player, 3) == [1]


def test_a_headset_pause_just_before_a_bud_comes_out_still_resumes(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    assert player.toggles() == 1 and player.status == PAUSED
    assert ears(fw, player, 1, step=1000) == [1]  # the pause was the bud's
    assert ears(fw, player, 3, step=1000) == [2]


def test_a_headset_pause_long_before_a_bud_comes_out_is_the_user_s(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_command', None, C.c_int)(PAUSE)
    assert ears(fw, player, 1, step=2000) == [1]
    assert ears(fw, player, 3) == [1]


def test_a_report_without_ear_bits_changes_nothing(fw):
    player = Player(fw)
    ears(fw, player, 3)
    fw.fn('headset_ears', None, C.c_uint)(0)
    assert player.toggles() == 0 and hs(fw).ears == 3


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


@pytest.mark.parametrize('code', [PLAYPAUSE, PAUSE, PLAYKEY])
def test_keys_just_after_an_ear_pause_are_its_echo(fw, code):
    player = Player(fw)
    ears(fw, player, 3, 1)
    command(fw, player, code, step=1000)
    assert player.toggles() == 1


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


def top_window(fw, name):
    page = C.create_string_buffer(name.encode())
    fw.mock('window_manager_get_top_window', lambda *_: 0x5100)
    fw.mock('widget_get_prop_str', lambda w, key, default, *_: C.addressof(page) if w == 0x5100 else default)
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    return page


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


@pytest.mark.parametrize('page', ['tidal_album', 'tidal_'])
def test_tidal_on_top_keeps_the_keys_with_stock_s_player(fw, page):
    player = Player(fw)
    keep = top_window(fw, page)
    fw.var('books__vid', Vid).pid = 4321
    fw.fn('media_key', None, C.c_uint)(KEY_FWD_BTN)
    assert len(fw.calls('player_next_music')) == 1 and keep


@pytest.mark.parametrize('page, focused', [('spotify_page', 1), ('spotbrowse_page', 1), ('radio_page', 1),
                                           ('radionp_page', 1), ('tidal_page', 1), ('playing_page', 0), ('', 0)])
def test_a_streaming_page_on_top_is_focused(fw, page, focused):
    keep = top_window(fw, page)
    assert fw.fn('media_focused', C.c_int)() == focused and keep


@pytest.mark.parametrize('page', ['spotify_page', 'radio_page'])
def test_a_focused_page_whose_player_isn_t_running_leaves_the_key_to_stock(fw, page):
    player = Player(fw)
    keep = top_window(fw, page)
    fw.fn('media_key', None, C.c_uint)(KEY_PLAY)
    assert player.toggles() == 1 and keep


def players(fw, video=0, radio=0, spotify=0):
    """Which players run: video (books.c vid.pid), radio (radio.c rd.active, rd.pid), Spotify (spotify.c sp.active)."""
    fw.var('books__vid', Vid).pid = video
    rd = fw.var('radio__rd', C.c_int * 3)  # pid, sock, active
    rd[0], rd[2] = radio, bool(radio)
    sp = fw.var('spotify__sp', C.c_int * 8)  # launched, sock, playing, paused, local, active
    sp[2], sp[5] = 1, spotify
    fw.mock('fw_socket', lambda *_: 7)


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


def test_a_focused_spotify_page_takes_the_keys_from_a_video(fw):
    player = Player(fw)
    players(fw, video=5, spotify=1)
    keep = top_window(fw, 'spotify_page')
    fw.fn('media_key', None, C.c_uint)(KEY_FWD_BTN)
    assert sent(fw) == [b'n'] and player.toggles() == 0 and keep


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
    """headset_start with the link list's last MAC and the pair list's entries (MAC at 4, name at 0x24)."""
    entries = []
    for pmac, name in paired:
        e = C.create_string_buffer(0x80)
        C.memmove(C.addressof(e) + 4, pmac.encode(), len(pmac))
        C.memmove(C.addressof(e) + 0x24, name.encode(), len(name))
        entries.append(e)
    C.c_void_p.from_address(PAIRS).value = 0x6100 if paired else 0
    fw.mock('fw_deque_size', lambda q, *_: len(entries))
    fw.mock('fw_deque_at', lambda q, i, *_: C.addressof(entries[i]))
    fw.mock('getBtLinkSize', lambda *_: linked)
    fw.mock('getBtLinkItem', lambda i, status, out, *_: C.memmove(out, mac.encode() + b'\0', len(mac) + 1) and 0)
    threads(fw)
    fw.fn('headset__headset_start', None)()
    return hs(fw)


def test_airpods_are_found_by_name_and_their_address_is_l2cap_s(fw, pairs):
    state = start(fw, 'AA:BB:CC:DD:EE:FF', [('11:22:33:44:55:66', 'Speaker'), ('AA:BB:CC:DD:EE:FF', 'AirPods Pro')])
    assert state.airpods == 1
    # sockaddr_l2: AF_BLUETOOTH (31), PSM 0x1001 little-endian, the address reversed
    assert bytes(state.address) == bytes([31, 0, 1, 16, 0xff, 0xee, 0xdd, 0xcc, 0xbb, 0xaa, 0, 0, 0, 0])


@pytest.mark.parametrize('mac, paired', [
    ('AA:BB:CC:DD:EE:FF', [('AA:BB:CC:DD:EE:FF', 'Bose QC')]),  # not AirPods
    ('AA:BB:CC:DD:EE:FF', [('AA:BB:CC:DD:EE:00', 'AirPods')]),  # another device's
    ('AA:BB:CC:DD:EE', [('AA:BB:CC:DD:EE', 'AirPods')]),  # not a MAC
    ('AA:BB:CC:DD:EE:FG', [('AA:BB:CC:DD:EE:FG', 'AirPods')]), ('AA-BB:CC:DD:EE:FF', [('AA-BB:CC:DD:EE:FF', 'AirPods')]),
    ('AA:BB:CC:DD:EE:FF', []),
])
def test_anything_else_is_no_airpods(fw, pairs, mac, paired):
    assert start(fw, mac, paired).airpods == 0


def test_a_lowercase_mac_reads_the_same(fw, pairs):
    state = start(fw, 'aa:bb:cc:dd:ee:0f', [('aa:bb:cc:dd:ee:0f', 'AirPods Max')])
    assert state.airpods == 1 and bytes(state.address[4:10]) == bytes([0x0f, 0xee, 0xdd, 0xcc, 0xbb, 0xaa])


def test_a_start_clears_the_last_link_s_state(fw, pairs):
    state = hs(fw)
    state.report, state.head, state.tail, state.known, state.paused = 7, 3, 2, 1, 1
    state = start(fw, 'AA:BB:CC:DD:EE:FF', [])
    assert (state.report, state.head, state.tail, state.known, state.paused) == (0, 0, 0, 0, 0)


# The worker, over simulated kernel I/O.

AAP, INPUT = 10, 11


class Kernel:
    """The worker's sockets and input devices: AACP packets and input event batches to read, one select a pass;
    when both run dry the link's generation moves on, so the worker ends."""
    def __init__(self, fw, packets=(), batches=(), device=3, name=b'AirPods Pro (AVRCP)', connect=0, passes=8,
                 fcntl=0, errors=0):
        self.fw, self.packets, self.batches = fw, list(packets), list(batches)
        self.device, self.name, self.passes, self.errors = device, name, passes, errors
        self.sent, self.closed, self.opened = [], [], []
        fw.mock('fw_socket', lambda *_: AAP)
        fw.mock('fw_fcntl', lambda *_: fcntl)
        fw.mock('fw_connect', lambda fd, addr, n, *_: self.sent.append(('connect', C.string_at(addr, n))) or connect)
        fw.mock('fw_getsockopt', lambda fd, level, opt, out, n, *_: C.c_int.from_address(out).__setattr__('value', 0))
        fw.mock('fw_send', lambda fd, data, n, flags, *_: self.sent.append((C.string_at(data, n), flags)) or n)
        fw.mock('fw_open', self.open)
        fw.mock('fw_ioctl', lambda fd, req, out, *_: C.memmove(out, self.name, len(self.name)) and 1)
        fw.mock('fw_close', lambda fd, *_: self.closed.append(fd))
        fw.mock('fw_select', self.select)
        fw.mock('fw_recv', self.recv)
        fw.mock('fw_read', self.read)
        fw.mock('fw_usleep', lambda *_: self.idle())

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        self.opened.append(path)
        return INPUT if path == f'/dev/input/event{self.device}' else -1

    def idle(self):
        self.passes -= 1
        if self.passes <= 0:
            hs(self.fw).generation += 1

    def select(self, n, r, w, e, tv, *_):
        if self.errors:  # a descriptor gone bad: select fails
            self.errors -= 1
            return -1
        if w:
            return 1
        ready = 0
        rset = (C.c_uint * 32).from_address(r)
        if not self.packets:
            rset[AAP // 32] &= ~(1 << AAP % 32)
        if not self.batches:
            rset[INPUT // 32] &= ~(1 << INPUT % 32)
        ready = bin(rset[0]).count('1')
        if not ready:
            self.idle()
        return ready

    def recv(self, fd, buf, n, flags, *_):
        if not self.packets:
            return -1  # EAGAIN: drained
        p = self.packets.pop(0)
        if p is None:
            return 0  # the socket closed
        C.memmove(buf, p, len(p))
        return len(p)

    def read(self, fd, buf, n, *_):
        batch = self.batches.pop(0)
        if batch is None:
            return 0  # the device went
        data = b''.join(struct.pack('<IIHHi', 0, 0, *ev) for ev in batch)
        C.memmove(buf, data, len(data))
        return len(data)


def run_worker(fw, airpods=1):
    state = hs(fw)
    state.airpods, state.running, state.generation = airpods, 1, 5
    state.address[:] = [31, 0, 1, 16, 1, 2, 3, 4, 5, 6, 0, 0, 0, 0]
    assert fw.fn('headset__headset_worker', C.c_void_p, C.c_void_p)(5) is None
    return state


def test_the_worker_greets_airpods(fw):
    kernel = Kernel(fw)
    state = run_worker(fw)
    assert kernel.sent[0] == ('connect', bytes([31, 0, 1, 16, 1, 2, 3, 4, 5, 6, 0, 0, 0, 0]))
    hello, features, notify = [s for s in kernel.sent[1:4]]
    assert hello == (bytes([0, 0, 4, 0, 1, 0, 2, 0]) + bytes(8), 0x40)  # nonblocking sends
    assert features[0][:7] == bytes([4, 0, 4, 0, 0x4d, 0, 0xd7]) and len(features[0]) == 14
    assert notify[0] == bytes([4, 0, 4, 0, 0xf, 0, 255, 255, 255, 255])
    assert state.running == 0 and sorted(kernel.closed) == [AAP, INPUT]


def test_the_worker_drains_every_report_and_keeps_the_last_ear_one(fw):
    other = bytes([4, 0, 4, 0, 0x17, 0, 1, 2, 3])
    Kernel(fw, packets=[other, bytes([4, 0, 4, 0, 6, 0, 1, 0]), other, bytes([4, 0, 4, 0, 6, 0, 0, 0]), other])
    assert run_worker(fw).report == 4 | 3


def test_the_worker_queues_presses_only(fw):
    press, release, repeat = 1, 0, 2
    Kernel(fw, batches=[[(1, PLAYPAUSE, press), (1, PLAYPAUSE, release), (1, PLAYPAUSE, repeat), (4, 4, 0x9000c),
                         (1, NEXT, press)], [(0, 0, 0), (1, PREV, press)]])
    state = run_worker(fw)
    assert state.head == 3 and list(state.keys[:3]) == [PLAYPAUSE, NEXT, PREV]


def test_the_worker_s_queue_holds_16(fw):
    Kernel(fw, batches=[[(1, NEXT, 1)] * 8, [(1, NEXT, 1)] * 8, [(1, PREV, 1)] * 8])
    assert run_worker(fw).head == 16


def test_the_worker_finds_the_avrcp_device_by_name(fw):
    kernel = Kernel(fw, device=6, batches=[[(1, NEXT, 1)]])
    state = run_worker(fw)
    assert kernel.opened[:7] == [f'/dev/input/event{i}' for i in range(7)] and state.head == 1


def test_a_device_by_another_name_isn_t_it(fw):
    kernel = Kernel(fw, name=b'gpio-keys', batches=[[(1, NEXT, 1)]])
    state = run_worker(fw, airpods=0)
    assert state.head == 0 and INPUT in kernel.closed


def test_without_airpods_no_socket(fw):
    Kernel(fw)
    run_worker(fw, airpods=0)
    assert fw.calls('fw_socket') == []


def test_a_closed_socket_is_closed_and_tried_again(fw):
    kernel = Kernel(fw, packets=[None], passes=12)
    run_worker(fw)
    assert kernel.closed.count(AAP) >= 2 and len(fw.calls('fw_socket')) >= 2


def test_a_device_that_goes_is_closed_and_found_again(fw):
    kernel = Kernel(fw, batches=[None, [(1, NEXT, 1)]])
    state = run_worker(fw, airpods=0)
    assert kernel.closed.count(INPUT) >= 2 and state.head == 1


def test_a_refused_connect_sends_nothing(fw):
    kernel = Kernel(fw, connect=-1)
    fw.mock('fw_getsockopt', lambda fd, level, opt, out, n, *_: C.c_int.from_address(out).__setattr__('value', 111))
    run_worker(fw)
    assert [s for s in kernel.sent if s[0] != 'connect'] == [] and AAP in kernel.closed


def test_a_socket_that_won_t_go_nonblocking_is_closed_unused(fw):
    kernel = Kernel(fw, fcntl=-1)
    run_worker(fw)
    assert kernel.sent == [] and AAP in kernel.closed


def test_a_failing_select_closes_what_failed_and_finds_them_again(fw):
    kernel = Kernel(fw, errors=3, batches=[[(1, NEXT, 1)]])
    state = run_worker(fw)
    assert kernel.closed[:2] == [AAP, INPUT]  # each taken alone: both bad
    assert kernel.opened.count('/dev/input/event3') >= 2 and state.head == 1


def test_a_stale_worker_ends_at_once(fw):
    kernel = Kernel(fw)
    state = hs(fw)
    state.generation, state.running = 6, 1
    assert fw.fn('headset__headset_worker', C.c_void_p, C.c_void_p)(5) is None
    assert state.running == 0 and kernel.opened == [] and fw.calls('fw_socket') == []


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


def test_eq_poll_looks_each_half_second_with_eq_per_output_on(fw):
    player = Player(fw)
    config(fw, EQOUTPUT='1')
    fw.mock('mclGetOutputWay', lambda *_: 0)
    for step in (500, 100, 399, 1):
        player.now += step
        fw.fn('headset_eq_poll', None)()
    assert len(fw.calls('mclGetOutputWay')) == 2


def test_eq_poll_with_eq_per_output_off_looks_at_nothing(fw):
    player = Player(fw)
    config(fw, EQOUTPUT='0')
    player.now += 1000
    fw.fn('headset_eq_poll', None)()
    assert fw.calls('mclGetOutputWay') == []
