"""patch/spotify.c, tidal.c and radio.c: Q2SPOT/1, librespot's state file, the Spotify browser's replies,
Tidal's cached names, and Internet Radio's lists and fetch."""
import ctypes as C
import random

import pytest

from conftest import block, text

REPLY_MAX = 64 << 10
RADIO_MAX = 200
FETCH_MAX = 256 << 10
SPOT_DEPTH, SPOT_ROWS = 8, 50
L_BASE, L_LIST, L_CATS, L_CONFIRM, L_DOWNLOADS, L_JOB, L_STORAGE, L_SELECT, L_REMOVE = range(9)
S_LOAD, S_READY, S_ERROR, S_PAIR, S_OLD, S_WAIT = range(6)
W_OK, W_NOSOCK, W_FAIL = range(3)
A_FETCH, A_PLAY, A_ENQUEUE, A_SET, A_SIGNOUT, A_DOWNLOAD, A_CAPS, A_REMOVE, A_PREVIEW = range(9)
KEY_PLAY, KEY_BACK_BTN, KEY_FWD_BTN = 171, 222, 223


class Sp(C.Structure):
    _fields_ = [*((n, C.c_int) for n in ('launched', 'sock', 'playing', 'paused', 'local', 'active', 'played', 'dead')),
                *((n, C.c_uint) for n in ('polled', 'idle_at', 'duration', 'position', 'cover', 'launch_at', 'alive_at')),
                ('at', C.c_ulonglong), ('raw', C.c_char * 1536), ('state', C.c_char * 12), ('track', C.c_char * 64),
                ('title', C.c_char * 256), ('artist', C.c_char * 256), ('album', C.c_char * 256), ('out', C.c_char * 24),
                ('auth', C.c_int), ('loading', C.c_int), ('old', C.c_int), ('load_at', C.c_uint), ('log_from', C.c_long),
                ('wanted', C.c_char * 64), ('from_', C.c_char * 64)]


class Level(C.Structure):
    _fields_ = [*((n, C.c_int) for n in ('kind', 'state', 'n', 'sel', 'retry')),
                ('id', C.c_uint), ('buf', C.c_void_p), ('f', C.c_char_p * (4 + 6 * SPOT_ROWS)), ('title', C.c_char * 256),
                ('note', C.c_char * 128), ('op', C.c_char * 16), ('args', C.c_char * 320), ('cursor', C.c_char * 1100)]


class Br(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in ('page', 'view', 'edit')),
                *((n, C.c_uint) for n in ('timer', 'go', 'leave', 'poll_at', 'until')),
                *((n, C.c_int) for n in ('depth', 'act', 'next', 'arg', 'playing')),
                ('rows', C.c_int * 11), ('caps_checked', C.c_int), ('downloads', C.c_int), ('can_download', C.c_int),
                ('selected', C.c_char * 6500), ('remove_kind', C.c_char * 16), ('remove_note', C.c_char * 128), ('term', C.c_char * 64),
                ('lv', C.POINTER(Level))]


class Wk(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('running', C.c_int), ('fail', C.c_int), ('cancel', C.c_int), ('done', C.c_int),
                ('id', C.c_uint), ('n', C.c_uint), ('len', C.c_uint), ('buf', C.c_void_p)]


class Td(C.Structure):
    _fields_ = [('lock', C.c_uint * 16), ('busy', C.c_int), ('job', C.c_int), ('gen', C.c_uint), ('want', C.c_uint), ('job_gen', C.c_uint),
                ('cleared', C.c_uint), ('thread', C.c_ulong), ('id', C.c_char * 48), ('url', C.c_char * 2048), ('path', C.c_char * 296),
                ('tags', (C.c_char * 200) * 3), ('cover', C.c_char * 128)]


class Fx(C.Structure):
    _fields_ = [('thread', C.c_ulong), ('running', C.c_int), ('cancel', C.c_int), ('done', C.c_int), ('code', C.c_long),
                ('body', C.c_void_p), ('n', C.c_uint), ('url', C.c_char * 320)]


class Dirent(C.Structure):  # test/fw/host.py's
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


def hostile(seed, samples, count=2000, alphabet=b'%\t\n0aF'):
    """Random bytes, every truncation of the samples, and mutations of them."""
    r = random.Random(seed)
    for _ in range(count):
        yield r.randbytes(r.randrange(120))
    for s in samples:
        for k in range(len(s)):
            yield s[:k]
    for _ in range(count):
        b = bytearray(r.choice(samples))
        for _ in range(r.randrange(1, 6)):
            b[r.randrange(len(b))] = r.choice(alphabet)
        yield bytes(b)


def escape(fw, s, size):
    out = block(fw, size=size)
    n = fw.fn('spot_escape', C.c_int, C.c_void_p, C.c_void_p, C.c_uint)(block(fw, s), out, size)
    return n, C.string_at(out) if n >= 0 else None


@pytest.mark.parametrize('s,out', [
    (b'', b''), (b'plain', b'plain'), (b'a%b', b'a%25b'), (b'\t', b'%09'), (b'\n', b'%0A'), (b'\r', b'%0D'),
    (b'%\t\n\r', b'%25%09%0A%0D'), ('é'.encode(), 'é'.encode()), (b'\x01\x7f\xff', b'\x01\x7f\xff'),
])
def test_escape(fw, s, out):
    assert escape(fw, s, 64) == (len(out), out)


@pytest.mark.parametrize('s', [b'abc', b'a%', b'%%%', b'\n'])
def test_escape_fits_to_the_byte(fw, s):
    whole = escape(fw, s, 64)[1]
    assert escape(fw, s, len(whole) + 1) == (len(whole), whole)
    for size in range(1, len(whole) + 1):
        assert escape(fw, s, size) == (-1, None)


def test_nothing_escapes_into_a_buffer_of_nothing(fw):
    assert escape(fw, b'', 1) == (0, b'')
    assert fw.fn('spot_escape', C.c_int, C.c_char_p, C.c_void_p, C.c_uint)(b'', None, 0) == -1


def test_hex_digits(fw):
    hex_ = fw.fn('hex', C.c_int, C.c_char)
    for c in range(256):
        byte = bytes([c])
        expected = int(byte, 16) if byte in b'0123456789abcdefABCDEF' and byte else -1
        assert hex_(byte) == expected, c


def reply(fw, data, rows=SPOT_ROWS):
    """spot_reply's rows on an exact reply and fields table: -1, or (header, rows)."""
    fn = fw.fn('spot_reply', C.c_int, C.c_void_p, C.c_uint, C.c_void_p, C.c_int)
    buf, table = block(fw, data), block(fw, size=(4 + 6 * rows) * C.sizeof(C.c_void_p))
    n = fn(buf, len(data), table, rows)
    if n < 0:
        return -1
    f = C.cast(table, C.POINTER(C.c_char_p))
    return f[:4], [f[4 + 6 * i:10 + 6 * i] for i in range(n)]


HEAD = b'Q2SPOT/1\n1\tok\tm\tc\n'
ROW = b'track\tu\tt\ts\tx\tp\n'


def test_a_reply_and_its_rows(fw):
    assert reply(fw, HEAD + ROW * 2) == ([b'1', b'ok', b'm', b'c'], [[b'track', b'u', b't', b's', b'x', b'p']] * 2)


def test_escapes_come_out_in_place(fw):
    assert reply(fw, b'Q2SPOT/1\n1\t%25%09\ta%0Ab%0dc\t%e2%82%AC\n')[0] == [b'1', b'%\t', b'a\nb\rc', '€'.encode()]


def test_empty_fields(fw):
    assert reply(fw, b'Q2SPOT/1\n\t\t\t\n')[0] == [b'', b'', b'', b'']


def test_the_last_line_needs_no_newline(fw):
    assert reply(fw, b'Q2SPOT/1\n1\tok\t\tc')[0][3] == b'c'
    assert reply(fw, HEAD + ROW[:-1])[1][0][5] == b'p'


def test_high_bytes_pass_through(fw):
    assert reply(fw, b'Q2SPOT/1\n1\t\xc3\xa9\t\xff\t\x01\n')[0][1:] == [b'\xc3\xa9', b'\xff', b'\x01']


@pytest.mark.parametrize('data', [
    b'', b'Q2SPOT/1', b'Q2SPOT/1\n', b'Q2SPOT/2\n1\tok\t\t\n', b'q2spot/1\n1\tok\t\t\n',
    b'Q2SPOT/1\n1\tok\t\n', b'Q2SPOT/1\n1\tok\t\t\t\n', b'Q2SPOT/1\n\n',
    HEAD + b'\n', HEAD + b'a\tb\tc\td\te\n', HEAD + b'a\tb\tc\td\te\tf\tg\n',
    b'Q2SPOT/1\n1\t%\t\t\n', b'Q2SPOT/1\n1\t%2\t\t\n', b'Q2SPOT/1\n1\t%zz\t\t\n', b'Q2SPOT/1\n1\t%00\t\t\n',
    b'Q2SPOT/1\n1\tok\t\t%2', b'Q2SPOT/1\n1\tok\t\t%',
])
def test_malformed_replies(fw, data):
    assert reply(fw, data) == -1


def test_a_nul_in_the_reply_is_malformed(fw):
    fn = fw.fn('spot_reply', C.c_int, C.c_void_p, C.c_uint, C.c_void_p, C.c_int)
    data = HEAD + b'\0' + ROW
    assert fn(block(fw, data), len(data), block(fw, size=64 * 8), 1) == -1


def test_rows_are_held_to_their_max(fw):
    assert len(reply(fw, HEAD + ROW * 2, 2)[1]) == 2
    assert reply(fw, HEAD + ROW * 3, 2) == -1
    assert reply(fw, HEAD, 0)[1] == [] and reply(fw, HEAD + ROW, 0) == -1


def test_a_wide_last_row_never_writes_past_the_table(fw):
    assert reply(fw, HEAD + b'a\tb\tc\td\te\tf\tg\th\ti\n', 1) == -1


def test_what_is_escaped_comes_back(fw):
    r = random.Random(4)
    for _ in range(300):
        fields = [r.randbytes(r.randrange(12)).replace(b'\0', b'%') for _ in range(4)]
        escaped = [escape(fw, f, 80)[1] for f in fields]
        assert reply(fw, b'Q2SPOT/1\n' + b'\t'.join(escaped) + b'\n')[0] == fields


def test_hostile_replies(fw):
    samples = [HEAD + ROW * 3, b'Q2SPOT/1\n7\tlogin\tOpen%20Spotify\t\n', HEAD + b'a%25\t%0A\t%41\t\t\t\n']
    for data in hostile(5, samples):
        data = data if data[:9] == b'Q2SPOT/1\n' else b'Q2SPOT/1\n' + data
        data = data.split(b'\0')[0]
        for rows in (0, 2, SPOT_ROWS):
            out = reply(fw, data, rows)
            if out != -1:
                assert len(out[1]) <= rows and b'\0' not in b''.join(out[0])


def field(fw, raw, key, n=64):
    out = block(fw, size=n)
    fw.fn('field', None, C.c_void_p, C.c_char_p, C.c_void_p, C.c_uint)(block(fw, raw), key, out, n)
    return C.string_at(out)


RAW = '\nstate=playing\ntrack=abc\ntitle=Café\nempty=\nposition=61000\nend=x'.encode()


@pytest.mark.parametrize('key,value', [
    (b'state', b'playing'), (b'title', 'Café'.encode()), (b'end', b'x'), (b'empty', b''), (b'nothing', b''),
    (b'tate', b''), (b'x', b''),   # keys are whole, and start a line
])
def test_a_key_in_the_state_file(fw, key, value):
    assert field(fw, RAW, key) == value


def test_the_first_of_a_repeated_key_counts(fw):
    assert field(fw, b'\na=1\na=2\n', b'a') == b'1'


def test_a_key_needs_its_own_line(fw):
    assert field(fw, b'a=1 b=2\n', b'b') == b''


def test_a_value_is_cut_to_its_buffer_at_a_whole_character(fw):
    assert field(fw, '\nv=abcdef'.encode(), b'v', 4) == b'abc'
    assert field(fw, '\nv=abé'.encode(), b'v', 5) == 'abé'.encode()
    assert field(fw, '\nv=abé'.encode(), b'v', 4) == b'ab'
    assert field(fw, '\nv=a€'.encode(), b'v', 4) == b'a'
    assert field(fw, '\nv=é'.encode(), b'v', 2) == b''
    assert field(fw, b'\nv=a', b'v', 1) == b''


def number(fw, raw, key):
    return fw.fn('number', C.c_ulonglong, C.c_char_p, C.c_char_p)(raw, key)


@pytest.mark.parametrize('raw,value', [
    (b'\nn=61000\n', 61000), (b'\nn=0\n', 0), (b'\nn=\n', 0), (b'\nm=1\n', 0), (b'\nn=12x34\n', 12), (b'\nn=-5\n', 0),
    (b'\nn=18446744073709551615\n', 2**64 - 1),
])
def test_a_number(fw, raw, value):
    assert number(fw, raw, b'n') == value


def test_a_number_too_long_for_its_field_keeps_its_first_digits(fw):
    assert number(fw, b'\nn=' + b'1' * 40 + b'\n', b'n') == int('1' * 23) % 2**64


class Files:
    """fopen, fread, fseek, ftell and fclose over a dict of bytes."""
    def __init__(self, fw, files):
        self.files, self.at, self.names = files, {}, {}
        def fopen(path, mode, *_):
            name = C.string_at(path)
            if name not in self.files:
                return 0
            f = len(self.names) + 1
            self.names[f], self.at[f] = name, 0
            return f
        def fread(buf, size, n, f, *_):
            piece = self.files[self.names[f]][self.at[f]:self.at[f] + size * n]
            C.memmove(buf, piece, len(piece))
            self.at[f] += len(piece)
            return len(piece) // size
        def fseek(f, at, whence, *_):
            self.at[f] = len(self.files[self.names[f]]) if whence == 2 else at
        fw.mock('fw_fopen', fopen)
        fw.mock('fw_fread', fread)
        fw.mock('fw_fseek', fseek)
        fw.mock('fw_ftell', lambda f, *_: self.at[f])


def state_read(fw, data, size=32, path=b'/tmp/x'):
    Files(fw, {} if data is None else {path: data})
    raw = block(fw, size=size)
    n = fw.fn('state_read', C.c_uint, C.c_char_p, C.c_void_p, C.c_uint)(path, raw, size)
    return n, C.string_at(raw)


def test_a_state_file_reads_behind_a_newline(fw):
    assert state_read(fw, b'a=1\nb=2') == (7, b'\na=1\nb=2')


def test_no_file_reads_as_nothing(fw):
    assert state_read(fw, None) == (0, b'\n')
    assert state_read(fw, b'') == (0, b'\n')


def test_a_state_file_stops_at_its_buffer(fw):
    assert state_read(fw, b'x' * 100, 16) == (14, b'\n' + b'x' * 14)


class Spot:
    def __init__(self, fw):
        self.fw, self.sp = fw, fw.var('spotify__sp', Sp)

    def parse(self, raw):
        self.sp.raw = raw
        self.fw.fn('spotify__spot_parse', None)()

    def started(self):
        return self.fw.fn('spotify__spot_started', C.c_int)()


STATE = b'\nstate=playing\ntrack=spotify:track:1\ntitle=T\nartist=A\nalbum=Al\nduration=200000\nposition=5000\nat=123456789012\ncover=7\n'


def test_the_state_file_parsed(fw):
    s = Spot(fw)
    s.parse(STATE)
    sp = s.sp
    assert (sp.state, sp.track, sp.title, sp.artist, sp.album) == (b'playing', b'spotify:track:1', b'T', b'A', b'Al')
    assert (sp.duration, sp.position, sp.at, sp.cover) == (200000, 5000, 123456789012, 7)


def test_a_playing_track_means_signed_in(fw):
    s = Spot(fw)
    s.sp.auth = -1
    s.parse(STATE)
    assert s.sp.auth == 1


def test_no_session_is_no_longer_signed_in(fw):
    s = Spot(fw)
    s.sp.auth = 1
    s.parse(b'\nstate=none\n')
    assert s.sp.auth == 0
    s.sp.auth = 1
    s.parse(b'\n')
    assert s.sp.auth == 0


def test_pairing_is_kept_until_a_track_plays(fw):
    s = Spot(fw)
    s.sp.auth = -1
    s.parse(b'\nstate=none\n')
    assert s.sp.auth == -1


def test_values_past_their_fields_are_cut(fw):
    s = Spot(fw)
    s.parse(b'\nstate=' + b's' * 40 + b'\ntrack=' + b't' * 100 + b'\ntitle=' + 'é'.encode() * 200 + b'\n')
    assert s.sp.state == b's' * 11 and s.sp.track == b't' * 63 and s.sp.title == 'é'.encode() * 127


@pytest.mark.parametrize('state,track,loading,wanted,before,started', [
    (b'playing', b'b', 1, b'b', b'a', 1),      # the one asked for
    (b'paused', b'b', 1, b'b', b'a', 1),
    (b'playing', b'c', 1, b'b', b'a', 1),      # a relinked id
    (b'playing', b'a', 1, b'b', b'a', 0),      # still the old one
    (b'playing', b'a', 2, b'b', b'a', 1),      # the wait timed out: any
    (b'playing', b'', 2, b'b', b'a', 0),       # none at all
    (b'none', b'b', 1, b'b', b'a', 0), (b'', b'b', 1, b'b', b'a', 0),
])
def test_the_play_asked_for_has_started(fw, state, track, loading, wanted, before, started):
    s = Spot(fw)
    s.sp.state, s.sp.track, s.sp.loading, s.sp.wanted, s.sp.from_ = state, track, loading, wanted, before
    assert s.started() == started


def test_a_load_ends_when_its_track_shows(fw):
    s = Spot(fw)
    s.sp.loading, s.sp.wanted, s.sp.from_ = 1, b'spotify:track:1', b'old'
    s.parse(STATE)
    assert s.sp.loading == 0


def test_a_change_in_the_state_file_is_seen(fw):
    s = Spot(fw)
    Files(fw, {b'/tmp/q2-librespot.state': b'state=none\n'})
    read = fw.fn('spotify__spot_read', C.c_int, C.c_void_p, C.c_uint)
    raw = block(fw, size=1536)
    assert read(raw, 1536) == 1
    s.sp.raw = C.string_at(raw)
    assert read(raw, 1536) == 0


LOG = b'/mnt/mmc/.spotify/spot.log'
REFUSED = b'error audio key'


def refused(fw, log, log_from=0):
    Files(fw, {LOG: log})
    sp = fw.var('spotify__sp', Sp)
    sp.log_from = log_from
    return fw.fn('spotify__spot_refused', C.c_int)(), sp.log_from


def test_a_refused_key_in_the_log(fw):
    assert refused(fw, b'x' * 100 + REFUSED + b'\n')[0] == 1


def test_only_what_is_new_counts(fw):
    assert refused(fw, REFUSED + b'\n' + b'x' * 100, 50)[0] == 0


def test_a_match_cut_between_reads_is_found_on_the_next(fw):
    found, at = refused(fw, b'x' * 100 + REFUSED[:7])
    assert not found
    assert refused(fw, b'x' * 100 + REFUSED, at)[0] == 1


def test_the_position_moves_on_past_all_but_a_matchs_length(fw):
    assert refused(fw, b'x' * 100)[1] == 100 - len(REFUSED) + 1
    assert refused(fw, b'x' * 5)[1] == 0


def test_a_shorter_log_is_a_new_one(fw):
    assert refused(fw, REFUSED, 1000)[0] == 1


def test_only_the_last_4k_are_read(fw):
    assert refused(fw, REFUSED + b'x' * 4095)[0] == 0
    assert refused(fw, b'x' + REFUSED + b'x' * 4080)[0] == 1
    assert refused(fw, b'y' * 5000 + b'x' * 4095)[0] == 0  # fits the buffer, its NUL too


def test_no_log_refuses_nothing(fw):
    Files(fw, {})
    assert fw.fn('spotify__spot_refused', C.c_int)() == 0


def test_the_position_runs_on_while_playing_up_to_the_end(fw):
    sp = fw.var('spotify__sp', Sp)
    def clock(which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
        t[0], t[1] = 100, 500000000  # 100.5 s
    fw.mock('fw_clock_gettime', clock)
    sp.position, sp.duration, sp.at, sp.playing = 5000, 200000, 100000, 1
    position = fw.fn('spotify__spot_position', C.c_uint)
    assert position() == 5500
    sp.playing = 0
    assert position() == 5000
    sp.playing, sp.position = 1, 199900
    assert position() == 200000


def test_now_ms_keeps_the_seconds_past_32_bits(fw):
    def clock(which, ts, *_):
        assert which == 1
        t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
        t[0], t[1] = 5000000, 999999999
    fw.mock('fw_clock_gettime', clock)
    assert fw.fn('now_ms', C.c_ulonglong)() == 5000000999


def test_a_datagram_opens_its_socket_once(fw):
    fw.mock('fw_socket', lambda *_: 9)
    sent = []
    def sendto(fd, data, n, flags, to, size):
        sent.append((fd, C.string_at(data, n), flags, C.cast(to, C.POINTER(C.c_ushort))[0], C.string_at(to + 2)))
    fw.mock('fw_sendto', sendto)
    send = fw.fn('dgram_send', None, C.POINTER(C.c_int), C.c_char_p, C.c_char_p, C.c_uint)
    sock = C.c_int(0)
    send(C.byref(sock), b'/tmp/s', b'pq', 2)
    send(C.byref(sock), b'/tmp/s', b'r', 1)
    assert sock.value == 9 and len(fw.calls('fw_socket')) == 1
    assert sent == [(9, b'pq', 0x40, 1, b'/tmp/s'), (9, b'r', 0x40, 1, b'/tmp/s')]


def test_media_keys_are_spotifys_only_while_its_active(fw):
    sp = fw.var('spotify__sp', Sp)
    sent = []
    fw.mock('fw_sendto', lambda fd, data, n, *_: sent.append(C.string_at(data, n)))
    media = fw.fn('spot_media', C.c_int, C.c_uint)
    assert media(KEY_PLAY) == 0 and not sent
    sp.active = 1
    assert [media(k) for k in (KEY_PLAY, KEY_FWD_BTN, KEY_BACK_BTN, 5)] == [1, 1, 1, 0]
    assert sent == [b'p', b'n', b'b']


def yielded(fw, states, playing=1):
    """spot_yield with the state file saying each of states in turn."""
    sp = fw.var('spotify__sp', Sp)
    sp.playing, sp.active = playing, 1
    files = Files(fw, {b'/tmp/q2-librespot.state': b''})
    clock, sent = [0], []
    def sleep(ms, *_):
        clock[0] += ms
        files.files[b'/tmp/q2-librespot.state'] = states.pop(0) if states else b'state=playing\n'
    fw.mock('sleep_ms', sleep)
    fw.mock('time_now_ms', lambda *_: clock[0])
    fw.mock('fw_sendto', lambda fd, data, n, *_: sent.append(C.string_at(data, n)))
    fw.fn('spot_yield', None)()
    return sp, sent, clock[0]


def test_local_music_waits_for_librespot_to_pause(fw):
    sp, sent, waited = yielded(fw, [b'state=playing\n', b'state=paused\n'])
    assert sent == [b's'] and sp.playing == 0 and sp.local == 1 and sp.active == 0 and waited == 40


def test_it_waits_no_longer_than_it_said(fw):
    sp, _, waited = yielded(fw, [])
    assert sp.playing == 1 and waited == 1500


def test_nothing_to_yield(fw):
    assert yielded(fw, [], playing=0)[1] == []


def worker(fw, script, connect=0, clock=None):
    """Spotify's worker over a socket whose recv hands out script; the reply buffer is request()'s size."""
    wk = fw.var('spotify__wk', Wk)
    wk.buf, wk.n, wk.len, wk.cancel = block(fw, b'Q2SPOT/1\n1\tcaps\n', REPLY_MAX + 2), 0, 16, 0
    script = list(script)
    def recv(fd, buf, n, flags, *_):
        piece = script.pop(0) if script else b''
        assert len(piece) <= n and n <= REPLY_MAX + 1 - wk.n
        C.memmove(buf, piece, len(piece))
        return len(piece) if piece != b'FAIL' else -1
    ms = [0]
    def tick(which, ts, *_):
        ms[0] += 1
        t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
        t[0], t[1] = ms[0], 0
    fw.mock('fw_clock_gettime', tick)
    fw.mock('fw_socket', lambda *_: 5)
    fw.mock('fw_connect', lambda *_: connect)
    fw.mock('fw_select', lambda *_: 1)
    fw.mock('fw_send', lambda fd, buf, n, *_: n)
    fw.mock('fw_recv', recv)
    fw.fn('spotify__worker', C.c_void_p, C.c_void_p)(None)
    return wk


def test_a_reply_read_to_eof(fw):
    wk = worker(fw, [b'Q2SPOT/1\n', b'1\tok\t\t\n'])
    assert wk.done == 1 and wk.fail == W_OK and wk.n == 16 and C.string_at(wk.buf) == b'Q2SPOT/1\n1\tok\t\t\n'
    assert len(fw.calls('spot_shut')) == 1 and len(fw.calls('fw_close')) == 1


def test_no_socket_is_its_own_failure(fw):
    wk = worker(fw, [], connect=-1)
    assert wk.fail == W_NOSOCK and wk.done == 1


def test_a_reply_of_exactly_the_limit_is_whole(fw):
    wk = worker(fw, [b'x' * 32768, b'x' * 32768])
    assert wk.fail == W_OK and wk.n == REPLY_MAX and C.string_at(wk.buf + REPLY_MAX) == b''


def test_a_reply_past_the_limit_fails_and_stays_in_its_buffer(fw):
    wk = worker(fw, [b'x' * 32768, b'x' * 32768, b'y'])
    assert wk.fail == W_FAIL and wk.n == REPLY_MAX + 1


def test_a_read_error_fails(fw):
    wk = worker(fw, [b'abc', b'FAIL'])
    assert wk.fail == W_FAIL


class Browser:
    """Spotify's browser driven as the UI drives it, on stubs. The worker's pthread never runs: answer()
    puts its reply where the pthread would and the page's tick takes it; a click runs through go()."""
    def __init__(self, fw):
        self.fw, self.now, self.sel, self.timers, self.drawn, self.toasts = fw, 1000, 0, 0, 0, []
        self.page, self.view = block(fw, size=4096), block(fw, size=4096)  # a widget's bytes, all zero
        self.sp, self.br, self.wk = fw.var('spotify__sp', Sp), fw.var('spotify__br', Br), fw.var('spotify__wk', Wk)
        def timer(*_):
            self.timers += 1
            return self.timers
        def lists(*_):
            self.drawn = 0
            return self.view
        def row(*_):
            self.drawn += 1
            return self.view
        def toast(path, info, *_):
            self.toasts.append(C.string_at(info + 8))
        def prop(w, name, default, *_):
            return self.sel if C.string_at(name) == b'_ringnav_index' else default
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_pthread_create', lambda *_: 0)
        fw.mock('timer_add', timer)
        fw.mock('window_create', lambda *_: self.page)
        fw.mock('scroll_view_create', lists)
        fw.mock('list_item_create', row)
        fw.mock('widget_get_child', lambda *_: self.view)
        fw.mock('widget_lookup', lambda *_: self.view)
        fw.mock('navigator_to_with_context', toast)
        fw.mock('widget_get_prop_int', prop)
        fw.fn('spot_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    @property
    def top(self):
        return self.br.lv[self.br.depth - 1]

    def request(self):
        return C.string_at(self.wk.buf)[:self.wk.len]

    def tick(self):
        self.fw.fn('spotify__br_tick', C.c_int, C.c_void_p)(None)

    def answer(self, status=b'ok', msg=b'', cursor=b'', rows=(), id=None, fail=W_OK, raw=None):
        """The reply to the request in flight (a Q2SPOT/1 line for each of rows), and the tick that takes it."""
        data = raw if raw is not None else b'Q2SPOT/1\n%d\t%s\t%s\t%s\n' % (self.wk.id if id is None else id, status, msg, cursor) + \
            b''.join(b'\t'.join(r) + b'\n' for r in rows)
        C.memmove(self.wk.buf, data + b'\0', len(data) + 1)
        self.wk.n, self.wk.fail, self.wk.done = len(data), fail, 1
        self.tick()

    def click(self, i):
        self.fw.fn('spotify__clicked', C.c_int, C.c_void_p, C.c_void_p)(i, None)
        self.fw.fn('spotify__go', C.c_int, C.c_void_p)(None)

    def back(self):
        self.fw.fn('spotify__br_leave', C.c_int, C.c_void_p)(None)

    def menu(self, caps=()):
        assert self.request().endswith(b'account\n')
        self.answer()
        assert self.request().endswith(b'caps\n')
        self.answer(rows=[(b'op', c, b'', b'', b'', b'') for c in caps])


def track(n, ctx=b'spotify:playlist:1'):
    return (b'track', b'spotify:track:%d' % n, b'Song %d' % n, b'Artist', ctx, b'%d' % n)


def album(n):
    return (b'album', b'spotify:album:%d' % n, b'Album %d' % n, b'Artist', b'', b'')


def test_the_browser_opens_on_the_account_then_its_caps(fw):
    b = Browser(fw)
    assert b.request() == b'Q2SPOT/1\n1\taccount\n'
    b.answer()
    assert b.request() == b'Q2SPOT/1\n2\tcaps\n' and b.top.state == S_READY
    b.answer()
    assert b.br.caps_checked == 1 and b.drawn == 8  # no track, so no Now Playing, Queue or Playback Settings


def test_the_menu_with_a_track_and_every_cap(fw):
    b = Browser(fw)
    b.sp.track, b.sp.state = b'x', b'playing'
    b.menu([b'download', b'collections', b'collection', b'playcollection', b'downloadliked', b'jobs', b'job', b'remove'])
    assert b.br.downloads == 1 and b.br.can_download == 1 and b.drawn == 10  # less Downloaded Tracks


def test_one_missing_cap_keeps_the_hub_away(fw):
    b = Browser(fw)
    b.menu([b'download', b'collections'])
    assert b.br.can_download == 1 and b.br.downloads == 0


def test_a_failed_caps_is_asked_again_later(fw):
    b = Browser(fw)
    b.answer()
    b.answer(status=b'offline')
    assert b.br.caps_checked == 0 and b.br.poll_at == b.now + 2000


def test_a_row_opens_its_list(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)  # Liked Songs
    assert b.br.depth == 2 and b.top.op == b'liked' and b.request().endswith(b'\tliked\n')
    b.answer(cursor=b'c1', rows=[track(1), track(2)])
    assert (b.top.state, b.top.n, b.top.f[3], b.drawn) == (S_READY, 2, b'c1', 3)  # the rows and More…


def test_a_track_plays_in_its_context(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[track(1), track(2)])
    b.click(1)
    assert b.request() == b'Q2SPOT/1\n%d\tplay\tspotify:playlist:1\t2\tspotify:track:2\n' % b.wk.id
    assert b.sp.wanted == b'2' and b.br.act == A_PLAY


def test_a_track_alone_plays_by_its_uri(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[track(1, b'')])
    b.click(0)
    assert b.request().endswith(b'\tplay\tspotify:track:1\n')


def test_a_play_accepted_waits_for_the_track(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[track(1)])
    b.click(0)
    b.answer()
    assert b.sp.loading == 1 and b.sp.auth == 1


def test_an_album_opens_with_its_uri(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[album(1)])
    b.click(0)
    assert b.top.op == b'album' and b.top.args == b'\tspotify:album:1' and b.top.title == b'Album 1'


def test_more_asks_the_same_list_at_its_cursor(fw):
    b = Browser(fw)
    b.menu()
    b.click(1)
    b.answer(cursor=b'c%251', rows=[album(1)])
    assert b.top.f[3] == b'c%1'
    b.click(1)
    assert b.top.op == b'playlists' and b.request().endswith(b'\tplaylists\tc%251\n')


def test_a_reply_to_another_request_is_dropped(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(id=b.wk.id + 1, rows=[track(1)])
    assert b.top.n == 0 and b.top.state == S_ERROR  # as a reply that didn't parse: Retry


def test_an_error_offers_retry(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(status=b'error', msg=b'Spotify says no')
    assert b.top.state == S_ERROR and b.top.note == b'Spotify says no' and b.top.retry == 1 and b.drawn == 1
    b.click(0)
    assert b.top.state == S_LOAD and b.request().endswith(b'\tliked\n')


def test_a_malformed_reply_says_it_didnt_answer(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(raw=b'Q2SPOT/1\nnot a reply\n')
    assert b.top.state == S_ERROR and b.top.note == b"Spotify didn't answer"


def test_login_goes_back_to_the_pairing_prompt(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(status=b'login')
    assert b.br.depth == 1 and b.top.state == S_PAIR and b.sp.auth == -1


def test_no_socket_while_starting_keeps_waiting(fw):
    b = Browser(fw)
    b.br.until = b.now + 5000
    b.answer(fail=W_NOSOCK, raw=b'')
    assert b.top.state == S_WAIT and b.br.poll_at == b.now + 500


def test_an_old_add_on_has_a_state_file_but_no_socket(fw):
    b = Browser(fw)
    b.br.until = 0
    b.now = 100000
    fw.mock('fw_access', lambda *_: 0)
    b.answer(fail=W_NOSOCK, raw=b'')
    assert b.top.state == S_OLD and b.sp.old == 1


def test_an_arg_too_long_for_its_level_sends_nothing(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[(b'album', b'u' * 319, b'T', b'S', b'', b''), (b'album', b'u' * 318, b'T', b'S', b'', b'')])
    b.click(0)
    assert b.br.depth == 2
    b.click(1)
    assert b.br.depth == 3 and len(b.top.args) == 319


def test_a_cursor_too_long_for_its_level_does_nothing(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(cursor=b'c' * 1099, rows=[track(1)])
    depth = b.br.depth
    b.click(1)
    assert b.br.depth == depth


def test_an_oversized_request_is_never_sent(fw):
    b = Browser(fw)
    b.menu()
    calls = len(fw.calls('fw_pthread_create'))
    request = fw.fn('spotify__request', None, C.c_char_p, C.c_char_p, C.c_int)
    request(b'op', b'\t' + b'x' * (8 << 10), A_ENQUEUE)
    assert b.wk.len == 0 and b.wk.running == 0 and b.wk.done == 1 and len(fw.calls('fw_pthread_create')) == calls


def test_levels_past_the_depth_forget_the_oldest(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[album(0)])
    for n in range(1, 12):
        b.click(0)
        assert b.br.depth <= SPOT_DEPTH
        b.answer(rows=[album(n)])
        assert b.top.f[4] == b'album' and b.top.f[6] == b'Album %d' % n
    assert b.br.depth == SPOT_DEPTH and b.br.lv[0].kind == L_BASE


def test_levels_well_below_the_top_let_their_rows_go_and_fetch_them_back(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[album(0)])
    b.click(0)
    b.answer(rows=[album(1)])
    b.click(0)
    b.answer(rows=[album(2)])
    b.click(0)
    b.answer(rows=[album(3)])
    assert not b.br.lv[1].buf and b.br.lv[1].state == S_LOAD and b.br.lv[2].buf
    b.back()
    b.back()
    assert b.top.state == S_READY and b.top.f[6] == b'Album 1'
    b.back()
    assert b.top.state == S_LOAD and b.request().endswith(b'\tliked\n')


def test_a_refreshing_level_keeps_its_rows_when_the_refresh_fails(fw):
    b = Browser(fw)
    b.menu([b'download', b'collections', b'collection', b'playcollection', b'downloadliked', b'jobs', b'job', b'remove'])
    b.click(6)   # Downloads
    b.click(2)   # Download Progress
    b.answer(rows=[(b'job', b'j1', b'Job', b'50%25', b'ctx', b'')])
    assert b.top.op == b'jobs'
    b.now += 3000
    fw.mock('window_manager_get_top_window', lambda *_: b.page)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    b.tick()
    assert b.request().endswith(b'\tjobs\n')
    b.answer(raw=b'garbage')
    assert b.top.state == S_READY and b.top.f[6] == b'Job'
    b.now += 3000
    b.tick()
    b.answer(rows=[(b'job', b'j1', b'Job', b'60%25', b'ctx', b''), (b'job', b'j2', b'Two', b'1%25', b'ctx', b'')])
    assert b.top.n == 2 and b.top.f[7] == b'60%'


def test_a_refresh_drops_a_click_on_its_old_rows(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[track(1)])
    fw.fn('spotify__clicked', C.c_int, C.c_void_p, C.c_void_p)(0, None)
    assert b.br.go
    b.fw.fn('spotify__fetch', None, C.POINTER(Level))(b.br.lv[1])
    b.answer(rows=[])
    assert b.br.go == 0


def test_settings_toggle(fw):
    b = Browser(fw)
    b.sp.track, b.sp.state = b'x', b'playing'
    b.menu()
    b.click(7)  # Playback Settings
    assert b.top.op == b'settings'
    b.answer(rows=[(b'setting', b'shuffle', b'off', b'', b'', b''), (b'setting', b'repeat', b'context', b'', b'', b'')])
    b.click(0)
    assert b.request().endswith(b'\tshuffle\ton\n') and b.br.act == A_SET
    b.answer()
    assert b.top.state == S_LOAD  # asked again, as it may have changed
    b.answer(rows=[(b'setting', b'repeat', b'context', b'', b'', b'')])
    b.click(0)
    assert b.request().endswith(b'\trepeat\ttrack\n')


def test_a_held_play_queues_a_track_and_downloads_a_playlist(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[track(1), (b'playlist', b'spotify:playlist:9', b'P', b'', b'', b''), album(3)])
    hold = fw.fn('spot_hold', C.c_int, C.c_void_p)
    b.sel = 0
    assert hold(b.br.page) == 1 and b.request().endswith(b'\tenqueue\tspotify:track:1\n')
    b.answer()
    b.sel = 1
    assert hold(b.br.page) == 1 and b.request().endswith(b'\tdownload\tspotify:playlist:9\n')
    b.answer()
    b.sel = 2
    assert hold(b.br.page) == 0
    b.sel = 3
    assert hold(b.br.page) == 0
    assert hold(None) == 0


def test_put_appends_a_tab_and_the_escaped_field(fw):
    put = fw.fn('spotify__put', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)
    out = block(fw, size=16)
    assert put(out, 16, b'a') == 1 and put(out, 16, b'b%') == 1 and C.string_at(out) == b'\ta\tb%25'
    assert put(out, 16, b'') == 1 and C.string_at(out) == b'\ta\tb%25\t'


def test_put_fits_to_the_byte(fw):
    put = fw.fn('spotify__put', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)
    assert put(block(fw, size=5), 5, b'abc') == 1
    assert put(block(fw, size=4), 4, b'abc') == 0
    assert put(block(fw, size=1), 1, b'') == 0
    assert put(block(fw, b'x', 2), 2, b'') == 0


def test_top_and_cell(fw):
    b = Browser(fw)
    b.menu()
    b.click(0)
    b.answer(rows=[track(1), track(2)])
    top = fw.fn('spotify__top', C.POINTER(Level))
    cell = fw.fn('spotify__cell', C.c_char_p, C.POINTER(Level), C.c_int, C.c_int)
    assert top().contents.op == b'liked'
    assert cell(top(), 1, 1) == b'spotify:track:2' and cell(top(), 0, 5) == b'1'


def test_more_is_a_cursor_or_an_account_page(fw):
    more = fw.fn('spotify__more', C.c_int, C.POINTER(Level))
    empty, full = text(b''), text(b'c')
    l = Level()
    l.f[3] = C.cast(empty, C.c_char_p)
    assert more(C.byref(l)) == 0
    l.f[3] = C.cast(full, C.c_char_p)
    assert more(C.byref(l)) == 1
    l.f[3] = C.cast(empty, C.c_char_p)
    l.op = b'account'
    assert more(C.byref(l)) == 1


@pytest.mark.parametrize('kind,yes', [
    (b'album', 1), (b'playlist', 1), (b'artist', 1), (b'collection', 1), (b'job', 1),
    (b'track', 0), (b'setting', 0), (b'', 0), (b'albums', 0), (b'Album', 0),
])
def test_a_row_that_opens(fw, kind, yes):
    assert fw.fn('spotify__opens', C.c_int, C.c_char_p)(kind) == yes


@pytest.mark.parametrize('track_id,state,yes', [(b'x', b'playing', 1), (b'x', b'none', 0), (b'', b'playing', 0), (b'x', b'', 1)])
def test_has_track(fw, track_id, state, yes):
    sp = fw.var('spotify__sp', Sp)
    sp.track, sp.state = track_id, state
    assert fw.fn('spotify__has_track', C.c_int)() == yes


def test_drop_frees_a_levels_reply_and_clears_it(fw):
    l = Level(n=3, sel=1)
    l.buf = block(fw, b'reply')
    fw.fn('spotify__drop', None, C.POINTER(Level))(C.byref(l))
    assert not l.buf and l.n == 0 and l.sel == 0


def test_browsing_is_the_page(fw):
    br = fw.var('spotify__br', Br)
    assert fw.fn('spotify__browsing', C.c_void_p)() is None
    br.page = 44
    assert fw.fn('spotify__browsing', C.c_void_p)() == 44


KINDS = [b'track', b'album', b'playlist', b'artist', b'collection', b'job', b'setting', b'missing', b'junk', b'']
OPS = [b'ok', b'ok', b'ok', b'ok', b'error', b'offline', b'throttled', b'login', b'']


def noise(r):
    return bytes(r.choice(b'abc%,:spotify 0123456789') for _ in range(r.choice((0, 1, 8, 30, 400))))


def hostile_row(r):
    return (r.choice(KINDS), noise(r).replace(b'%', b'%25'), r.choice((b'off', b'on', b'context', b'track', noise(r).replace(b'%', b'%25'))),
            noise(r).replace(b'%', b'%25'), noise(r).replace(b'%', b'%25'), noise(r).replace(b'%', b'%25'))


def test_hostile_replies_through_the_browser(fw):
    """The whole browser under what a bad or broken librespot might send, clicks and Backs between."""
    r = random.Random(6)
    caps = [b'download', b'collections', b'collection', b'playcollection', b'downloadliked', b'jobs', b'job', b'remove']
    b = Browser(fw)
    b.menu(caps)
    fw.mock('fw_access', lambda *_: r.randrange(2))
    hold = fw.fn('spot_hold', C.c_int, C.c_void_p)
    for _ in range(2500):
        pending = b.wk.running or b.wk.done
        if pending:
            kind = r.random()
            if kind < 0.1:
                b.answer(raw=bytes(r.choice(b'Q2SPOT/1\n\t%0a') for _ in range(40)))
            elif kind < 0.15:
                b.answer(fail=r.choice((W_FAIL, W_NOSOCK)), raw=b'')
            elif kind < 0.2:
                b.answer(id=b.wk.id + 1, rows=[hostile_row(r)])
            elif b.br.act and b.br.act != A_CAPS:
                b.answer(status=r.choice(OPS), msg=noise(r).replace(b'%', b'%25'))
            else:
                b.answer(status=r.choice(OPS), msg=noise(r).replace(b'%', b'%25'), cursor=r.choice((b'', noise(r).replace(b'%', b'%25'))),
                         rows=[(b'op', c, b'', b'', b'', b'') for c in caps] if b.br.act == A_CAPS else
                         [hostile_row(r) for _ in range(r.choice((0, 1, 3, SPOT_ROWS)))])
        elif b.br.depth > 1 and r.random() < 0.15:
            b.back()
        elif b.drawn:
            b.sel = r.randrange(b.drawn)
            if r.random() < 0.1:
                hold(b.br.page)
            else:
                b.click(b.sel)
        else:
            b.now += 1000
            b.tick()
            if not (b.wk.running or b.wk.done) and b.br.depth > 1:
                b.back()
    fw.fn('spotify__br_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)


def tidal_ext(fw, url, n=8):
    out = block(fw, size=n)
    return fw.fn('tidal_ext', C.c_char_p, C.c_void_p, C.c_void_p, C.c_uint)(block(fw, url), out, n)


@pytest.mark.parametrize('url,ext', [
    (b'https://x/a.flac', b'.flac'), (b'https://x/a.flac?t=1.2', b'.flac'), (b'https://x/0.b.mp4?x', b'.mp4'),
    (b'a.b', b'.b'), (b'', None), (b'.', None), (b'a', None), (b'/', None), (b'?', None), (b'https://x/a', None),
    (b'https://x/a.?q', None), (b'https://x.y/a', None), (b'https://x/a?b.c', None),
])
def test_tidal_ext(fw, url, ext):
    assert tidal_ext(fw, url) == ext


def test_tidal_ext_fits_to_the_byte(fw):
    assert tidal_ext(fw, b'x/a.flac', 6) == b'.flac' and tidal_ext(fw, b'x/a.flac', 5) is None


def tidal_name(fw, n, artist, title, key=b'42-3', ext=b'.flac'):
    out = block(fw, size=n)
    return fw.fn('tidal_name', C.c_char_p, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p, C.c_char_p, C.c_char_p)(out, n, key, artist, title, ext)


def test_a_cached_name(fw):
    assert tidal_name(fw, 64, b'Artist', b'Song') == b'42-3 Artist - Song.flac'


def test_every_reserved_and_control_character_becomes_an_underscore(fw):
    for c in [*b'/\\:*?"<>|', *range(1, 32)]:
        assert tidal_name(fw, 64, None, b'a' + bytes([c]) + b'b') == b'42-3 a_b.flac', c


def test_other_characters_stay(fw):
    assert tidal_name(fw, 64, b"O'Neil & co.", 'Café (Live) [x]'.encode()) == "42-3 O'Neil & co. - Café (Live) [x].flac".encode()


def test_the_label_is_left_out_without_a_title(fw):
    assert tidal_name(fw, 64, b'Artist', b'') == b'42-3.flac'
    assert tidal_name(fw, 64, b'Artist', None) == b'42-3.flac'


def test_no_artist_names_the_title_alone(fw):
    assert tidal_name(fw, 64, b'', b'Song') == b'42-3 Song.flac'


def test_room_for_the_key_and_extension_or_nothing(fw):
    assert tidal_name(fw, 9, b'A', b'T') is None
    assert tidal_name(fw, 10, b'A', b'T') == b'42-3.flac'
    assert tidal_name(fw, 11, b'A', b'T') == b'42-3 .flac'


def test_it_is_cut_at_a_whole_character_at_every_size(fw):
    title = 'é€😀ab'.encode()
    whole = b'42-3 ' + title + b'.flac'
    for n in range(10, len(whole) + 3):
        name = tidal_name(fw, n, None, title)
        assert len(name) < n and name.endswith(b'.flac')
        name.decode()  # valid UTF-8 at every cut


def test_hostile_names(fw):
    for s in hostile(7, ['Ærøskøbing / 好'.encode(), b'a:b*c'], 1000, b'\xc3\xa9\xe2/:'):
        s = s.split(b'\0')[0]
        for n in (10, 30, 256):
            name = tidal_name(fw, n, s, s)
            assert name.startswith(b'42-3') and name.endswith(b'.flac') and len(name) < n
            assert not set(name) & set(b'/\\:*?"<>|')


def directory(fw, names):
    entries = [Dirent(d_name=n) for n in names]
    left = list(entries)
    fw.mock('fw_opendir', lambda *_: 1)
    fw.mock('fw_readdir', lambda *_: C.addressof(left.pop(0)) if left else 0)
    return entries


def cached(fw, names, key=b'12-5', ext=b'.flac', n=296):
    directory(fw, names)
    path = block(fw, size=n)
    found = fw.fn('tidal__cached', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_uint)(key, ext, path, n)
    return C.string_at(path) if found else None


@pytest.mark.parametrize('names,found', [
    ([b'12-5.flac'], b'/mnt/mmc/.tidal/12-5.flac'),
    ([b'12-5 A - T.flac'], b'/mnt/mmc/.tidal/12-5 A - T.flac'),
    ([b'x.flac', b'12-5 B.flac', b'12-5.flac'], b'/mnt/mmc/.tidal/12-5 B.flac'),
    ([b'12-50.flac', b'12-5.m4a', b'12-5.flac.tmp', b'112-5.flac', b'12-5 x.m4a'], None),
    ([], None),
])
def test_finding_a_cached_track(fw, names, found):
    assert cached(fw, names) == found


def test_a_name_too_long_for_the_path_is_not_found(fw):
    assert cached(fw, [b'12-5.flac'], n=len(b'/mnt/mmc/.tidal/12-5.flac')) is None
    assert cached(fw, [b'12-5.flac'], n=len(b'/mnt/mmc/.tidal/12-5.flac') + 1) == b'/mnt/mmc/.tidal/12-5.flac'


def test_save_counts_bytes_for_curl(fw):
    fw.mock('fw_fwrite', lambda data, size, n, f, *_: n)
    assert fw.fn('tidal__save', C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)(b'abcdef', 2, 3, None) == 6


class Stream:
    """tidal_progress's clock and the player's status."""
    def __init__(self, fw):
        self.ms, self.status, self.at = 1000, 2, C.c_uint(0)
        def clock(which, ts, *_):
            t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
            t[0], t[1] = self.ms // 1000, self.ms % 1000 * 1000000
        fw.mock('fw_clock_gettime', clock)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        self.fn = fw.fn('tidal__progress', C.c_int, C.POINTER(C.c_uint), C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)
        self.td = fw.var('tidal__td', Td)

    def poll(self, ms=0):
        self.ms += ms
        return self.fn(C.byref(self.at), 0, 0, 0, 0)


def test_a_playing_transfer_goes_on(fw):
    s = Stream(fw)
    assert s.poll() == 0 and s.at.value == 0


def test_another_track_stops_the_transfer(fw):
    s = Stream(fw)
    s.td.gen = 1
    assert s.poll() == 1


def test_a_stop_ends_it_after_three_seconds_only(fw):
    s = Stream(fw)
    s.status = 1
    assert s.poll() == 0
    assert s.at.value & 1  # 0 means not stopped
    assert s.poll(2998) == 0
    assert s.poll(10) == 1


def test_playing_again_forgets_the_stop(fw):
    s = Stream(fw)
    s.status = 1
    s.poll()
    s.status = 2
    assert s.poll(1000) == 0 and s.at.value == 0
    s.status = 1
    assert s.poll(4000) == 0


def test_the_clock_wrapping_32_bits_still_measures_the_stop(fw):
    s = Stream(fw)
    s.ms, s.status = 2**32 - 1000, 1
    assert s.poll() == 0
    assert s.poll(2000) == 0
    assert s.poll(1500) == 1


def test_clear_all_removes_all_but_dot_files_and_stops_the_download(fw):
    td = fw.var('tidal__td', Td)
    td.job = 1
    directory(fw, [b'.cache-size', b'a.flac', b'b.flac.tmp'])
    gone = []
    fw.mock('fw_unlink', lambda p, *_: gone.append(C.string_at(p)))
    fw.fn('tidal_clear', None)()
    assert gone == [b'/mnt/mmc/.tidal/a.flac', b'/mnt/mmc/.tidal/b.flac.tmp']
    assert td.cleared == 1 and td.job == 0 and td.gen == 1


class Play:
    """tidal_play's world: nothing cached, a card with room, no queue."""
    def __init__(self, fw):
        self.td, self.played = fw.var('tidal__td', Td), []
        fw.mock('stock_tidal_trampoline', lambda url, *_: self.played.append(C.string_at(url) if url else None))
        fw.mock('fw_statfs', self.statfs)
        directory(fw, [])
        self.fn = fw.fn('tidal_play', C.c_int, C.c_char_p, C.c_char_p)

    @staticmethod
    def statfs(path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, 1 << 24


URL = b'https://cdn/a/0.flac?token=1'


def test_a_new_track_starts_its_download_and_plays_the_stream(fw):
    p = Play(fw)
    p.fn(URL, b'123')
    assert p.played == [URL] and len(fw.calls('fw_pthread_create')) == 1
    assert p.td.job == 1 and p.td.busy == 1 and p.td.url == URL and p.td.id == b'123'
    assert p.td.path == b'/mnt/mmc/.tidal/123-0.flac'


def test_a_cached_track_plays_from_the_card(fw):
    p = Play(fw)
    directory(fw, [b'123-0 A - T.flac'])
    p.fn(URL, b'123')
    assert p.played == [b'/mnt/mmc/.tidal/123-0 A - T.flac'] and not fw.calls('fw_pthread_create')


def test_an_mp4_is_kept_as_m4a(fw):
    p = Play(fw)
    p.fn(b'https://cdn/a/0.mp4', b'123')
    assert p.td.path == b'/mnt/mmc/.tidal/123-0.m4a'


@pytest.mark.parametrize('url,track_id', [
    (None, b'1'), (URL, None), (URL, b''), (URL, b'a/b'), (URL, b'x' * 41), (b'https://cdn/a', b'1'),
])
def test_what_cant_be_cached_just_plays(fw, url, track_id):
    p = Play(fw)
    p.fn(url, track_id)
    assert p.played == [url] and not fw.calls('fw_pthread_create')


def test_the_longest_id_fits(fw):
    p = Play(fw)
    p.fn(URL, b'x' * 40)
    assert p.td.id == b'x' * 40


def test_a_url_too_long_for_the_job_is_not_downloaded(fw):
    p = Play(fw)
    p.fn(b'https://cdn/' + b'a' * 2048 + b'.flac', b'1')
    assert len(p.played) == 1 and not fw.calls('fw_pthread_create')


def fx(fw):
    return fw.var('radio__fx', Fx)


def fetch_got(fw, data, size=1):
    return fw.fn('radio__fetch_got', C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)(data, size, len(data) // size, None)


def test_a_fetch_collects_its_reply(fw):
    f = fx(fw)
    f.body = block(fw, size=FETCH_MAX)
    assert fetch_got(fw, b'abc') == 3 and fetch_got(fw, b'defg', 2) == 4 and f.n == 7
    assert C.string_at(f.body) == b'abcdefg'


def test_a_fetch_keeps_room_for_its_nul(fw):
    f = fx(fw)
    f.body = block(fw, size=FETCH_MAX)
    f.n = FETCH_MAX - 2
    assert fetch_got(fw, b'ab') == 0 and f.n == FETCH_MAX - 2
    assert fetch_got(fw, b'a') == 1 and f.n == FETCH_MAX - 1


def test_a_cancel_stops_the_transfer(fw):
    f = fx(fw)
    f.body, f.cancel = block(fw, size=FETCH_MAX), 1
    assert fetch_got(fw, b'abc') == 0 and f.n == 0
    progress = fw.fn('radio__fetch_progress', C.c_int, C.c_void_p, C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)
    assert progress(None, 0, 0, 0, 0) == 1
    f.cancel = 0
    assert progress(None, 0, 0, 0, 0) == 0


def stations(fw, raw, max=RADIO_MAX + 1):
    fn = fw.fn('playlist', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int)
    buf, name, url = block(fw, raw), block(fw, size=max * 4), block(fw, size=max * 4)
    n = fn(buf, name, url, max)
    names, urls = C.cast(name, C.POINTER(C.c_uint)), C.cast(url, C.POINTER(C.c_uint))
    return [(C.string_at(buf + names[i]), C.string_at(buf + urls[i])) for i in range(n)]


def test_a_name_goes_with_the_next_url_only(fw):
    assert stations(fw, b'#EXTINF:-1,One\n#x\nhttp://a\nhttp://b\n') == [(b'One', b'http://a'), (b'http://b', b'http://b')]


def test_a_title_with_no_number_or_value_names_nothing(fw):
    assert stations(fw, b'File1=http://a\nTitle=x\nTitle1=\n') == [(b'http://a', b'http://a')]


def test_a_title_names_every_file_of_its_number(fw):
    assert stations(fw, b'File1=http://a\nFILE1=http://b\ntitle1=One\n') == [(b'One', b'http://a'), (b'One', b'http://b')]


def test_a_title_key_is_no_prefix_of_another(fw):
    assert stations(fw, b'File12=http://a\nTitle1=x\n') == [(b'http://a', b'http://a')]


def test_lines_with_only_spaces(fw):
    assert stations(fw, b'   \n\t\n  http://a  \n   ') == [(b'http://a  ', b'http://a  ')]


def test_a_list_past_its_max_is_held_to_it(fw):
    assert len(stations(fw, b'http://a\n' * 300)) == RADIO_MAX + 1
    assert len(stations(fw, b'http://a\n' * 3, 2)) == 2


def picks(fw, raw, coded, max=RADIO_MAX):
    fn = fw.fn('choices', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_int)
    buf, name, key = block(fw, raw), block(fw, size=max * 4), block(fw, size=max * 4)
    n = fn(buf, name, key, max, coded)
    names, keys = C.cast(name, C.POINTER(C.c_uint)), C.cast(key, C.POINTER(C.c_uint))
    return [(C.string_at(buf + names[i]), C.string_at(buf + keys[i])) for i in range(n)]


@pytest.mark.parametrize('raw,coded,rows', [
    (b'', 1, []), (b'header', 1, []), (b'h\n', 1, []),
    (b'h\nno commas\n', 1, []), (b'h\nName,3\n', 1, [(b'Name', b'Name')]),   # no code to take: the name
    (b'h\n"a,b",3\n', 0, [(b'a,b', b'a,b')]), (b'h\n,3\n', 0, []),
    (b'h\n"",UK,3\n', 1, []), (b'h\n"unclosed,UK,3\n', 1, [(b'"unclosed', b'UK')]),
])
def test_csv_edges(fw, raw, coded, rows):
    assert picks(fw, raw, coded) == rows


def test_the_csv_is_held_to_its_max(fw):
    assert len(picks(fw, b'h\n' + b'pop,1\n' * 300, 0)) == RADIO_MAX


def escaped(fw, s, n=128):
    out = block(fw, size=n)
    fw.fn('url_escape', None, C.c_char_p, C.c_void_p, C.c_uint)(s, out, n)
    return C.string_at(out)


def test_url_escape_stays_in_its_buffer_and_never_cuts_an_escape(fw):
    assert escaped(fw, b'abcd', 4) == b'a'
    assert escaped(fw, b'abcdef', 5) == b'ab' and escaped(fw, b'a b', 5) == b'a%20'
    assert escaped(fw, b'', 1) == b'' and escaped(fw, b'abc', 3) == b'' and escaped(fw, b'  ', 7) == b'%20%20'


RADIO = [b'#EXTM3U\n#EXTINF:-1,A, B\r\nhttp://a/1?x=1\r\n\r\n  https://b/2\n', b'[playlist]\nFile1=http://p/1\nTitle1=One\nfile2=x://y\n',
         b'name,iso_3166_1,stationcount\n"Taiwan, X",TW,214\r\nGermany,DE,6496\n,XX,1\nbad\n']


def test_hostile_radio_lists(fw):
    for raw in hostile(8, RADIO, 1500, b'#=,:/"\n\r \t'):
        raw = raw.split(b'\0')[0]
        for name, value in stations(fw, raw, 8) + picks(fw, raw, 1, 8) + picks(fw, raw, 0, 8):
            assert name is not None and value is not None
        escaped(fw, raw, 64)
