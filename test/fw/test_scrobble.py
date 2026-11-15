"""scrobble.c (docs/internals.md#scrobbling): the accounts, the UI thread's listens, and an upload from start to
poll on the device path: the log read in complete lines, ListenBrainz's JSON and Last.fm's signed form through
demo's libcurl, then the log rewritten under the lock. test/peq.py runs the same upload on a real pthread."""
import ctypes as C
import hashlib
import json
import urllib.parse

import pytest

LOG, INI, CA = '/mnt/mmc/.scrobbler.log', '/mnt/mmc/.scrobble.ini', '/etc/scrobble-ca.pem'
HEADER = b'#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Q2 Pod\n'
LB_URL, FM_URL = 'https://api.listenbrainz.org/1/submit-listens', 'https://ws.audioscrobbler.com/2.0/'
BODY_MAX = 384 << 10
ACCOUNT = {('LISTENBRAINZ', 'TOKEN'): 'tok', ('LASTFM', 'USER'): 'u', ('LASTFM', 'PASSWORD'): 'p&w',
           ('LASTFM', 'API_KEY'): 'key', ('LASTFM', 'API_SECRET'): 'sec'}
LB_ONLY = {('LISTENBRAINZ', 'TOKEN'): 'tok'}

libc = C.CDLL(None)
libc.free.argtypes = [C.c_void_p]


def config(fw, values):
    """/mnt/mmc/.scrobble.ini by (section, key); stock leaves out alone for a key it hasn't."""
    read = []

    def lookup(path, section, key, out, default, *_):
        read.append(C.string_at(path).decode())
        v = values.get((C.string_at(section).decode(), C.string_at(key).decode()))
        if v is None:
            return -1
        C.memmove(out, v.encode() + b'\0', len(v.encode()) + 1)
        return 1
    fw.mock('toolsReadConfig', lookup)
    return read


class Card:
    """The card's files as a dict: read, write and append, each fopen's mode; fail names what fails ('open:path',
    'write:path', 'write+:path' for every write after the first, 'sync', 'close', 'rename', 'short' for a read that stops
    early, 'readerr' for a read error)."""
    def __init__(self, fw, files=None):
        self.files, self.open, self.fail, self.opened = dict(files or {}), {}, set(), []
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fread', self.fread)
        fw.mock('fw_fseek', self.fseek)
        fw.mock('fw_ftell', lambda f, *_: self.open[f][1])
        fw.mock('fw_fwrite', self.fwrite)
        fw.mock('fw_fclose', self.fclose)
        fw.mock('fw_fileno', lambda f, *_: f)
        fw.mock('fw_fsync', lambda fd, *_: -1 if 'sync' in self.fail else 0)
        fw.mock('fw_fflush', lambda f, *_: 0)
        fw.mock('fw_ferror', lambda f, *_: int('readerr' in self.fail))
        fw.mock('fw_ftruncate', self.ftruncate)
        fw.mock('fw_rename', self.rename)
        fw.mock('fw_unlink', lambda path, *_: self.files.pop(C.string_at(path).decode(), None) and 0)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files else -1)

    def fopen(self, path, mode, *_):
        path, mode = C.string_at(path).decode(), C.string_at(mode).decode()
        self.opened.append((path, mode))
        if f'open:{path}' in self.fail or ('r' in mode and path not in self.files):
            return 0
        handle = 0x2000 + len(self.opened)
        start = self.files.get(path, b'') if 'a' in mode else b'' if 'w' in mode else None
        self.open[handle] = [path, 0, start]
        return handle

    def fread(self, out, size, count, f, *_):
        path, at, _ = self.open[f]
        data = self.files[path][at:at + size * count]
        if 'short' in self.fail:
            data = data[:len(data) // 2]
        C.memmove(out, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fseek(self, f, offset, whence, *_):
        state = self.open[f]
        end = len(self.files[state[0]] if state[2] is None else state[2])  # an append's file may be new
        state[1] = C.c_long(offset).value + {0: 0, 1: state[1], 2: end}[whence]
        return 0

    def ftruncate(self, fd, size, *_):
        path = self.open[fd][0]
        self.files[path] = self.files[path][:size]
        return 0

    def fwrite(self, data, size, count, f, *_):
        state = self.open[f]
        if f'write:{state[0]}' in self.fail or (f'write+:{state[0]}' in self.fail and state[2]):
            return 0
        state[2] += C.string_at(data, size * count)
        return count

    def fclose(self, f, *_):
        path, _, written = self.open.pop(f)
        if written is not None:
            self.files[path] = written
        return -1 if 'close' in self.fail else 0

    def rename(self, old, new, *_):
        if 'rename' in self.fail:
            return -1
        self.files[C.string_at(new).decode()] = self.files.pop(C.string_at(old).decode())
        return 0


class Net:
    """demo's libcurl: each request's options, headers and body as sent; the reply handed to the write callback in
    two chunks, with its HTTP code. reply(url, body) -> (code, text), code None for no connection."""
    def __init__(self, fw, reply=None):
        self.requests, self.lists, self.opts = [], {}, {}
        self.reply = reply or self.ok
        fw.mock('fw_curl_easy_init', lambda *_: self.opts.clear() or 0x9000)
        fw.mock('fw_curl_easy_setopt', self.setopt)
        fw.mock('fw_curl_easy_perform', self.perform)
        fw.mock('fw_curl_easy_getinfo', lambda c, what, out, *_: C.c_long.from_address(out).__setattr__('value', self.code))
        fw.mock('fw_curl_slist_append', self.append)
        self.md5 = {}
        fw.mock('fw_MD5_Init', lambda ctx, *_: self.md5.__setitem__(ctx, hashlib.md5()))
        fw.mock('fw_MD5_Update', lambda ctx, data, n, *_: self.md5[ctx].update(C.string_at(data, n)))
        fw.mock('fw_MD5_Final', lambda out, ctx, *_: C.memmove(out, self.md5.pop(ctx).digest(), 16))

    @staticmethod
    def ok(url, body):
        if 'listenbrainz' in url:
            return 200, '{"status":"ok"}'
        if 'auth.getMobileSession' in body:
            return 200, '{"session":{"name":"u","key":"SK123","subscriber":0}}'
        return 200, '{"scrobbles":{"@attr":{"accepted":1,"ignored":0}}}'

    def append(self, head, s, *_):
        handle = head or 0x7000 + len(self.lists)
        self.lists.setdefault(handle, []).append(C.string_at(s).decode())
        return handle

    def setopt(self, c, opt, value, *_):
        self.opts[opt] = C.string_at(value).decode('latin1') if opt in (10002, 10015, 10065) else value
        return 0

    def perform(self, c, *_):
        url, body = self.opts[10002], self.opts[10015]
        self.requests.append((url, self.lists.get(self.opts.get(10023), []), body, dict(self.opts)))
        self.code, text = self.reply(url, body)
        if self.code is None:
            return 7  # CURLE_COULDNT_CONNECT
        write = C.CFUNCTYPE(C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)(self.opts[20011])
        data = text.encode()
        for part in (data[:7], data[7:]):
            assert write(part, 1, len(part), self.opts[10001]) == len(part)
        return 0

    def to(self, host):
        return [r for r in self.requests if host in r[0]]

    def listens(self):
        """Each ListenBrainz batch's listens: (time, artist, title, album or None)."""
        out = []
        for _, _, body, _ in self.to('listenbrainz'):
            sent = json.loads(body)
            assert sent['listen_type'] == 'import'
            out.append([(x['listened_at'], x['track_metadata']['artist_name'], x['track_metadata']['track_name'],
                         x['track_metadata'].get('release_name')) for x in sent['payload']])
        return out

    def forms(self):
        """Each Last.fm call's parameters in the order sent, less api_sig and format, checked against its signature."""
        out = []
        for _, headers, body, _ in self.to('audioscrobbler'):
            assert not headers
            params = urllib.parse.parse_qsl(body, keep_blank_values=True, strict_parsing=True)
            *signed, (sig, digest), (fmt, json_) = params
            assert (sig, fmt, json_) == ('api_sig', 'format', 'json')
            assert signed == sorted(signed)  # sent in the signature's order
            assert digest == hashlib.md5((''.join(k + v for k, v in signed) + 'sec').encode()).hexdigest()
            out.append(dict(signed))
        return out


def line(artist='Art', title='Song', album='Album', length='200', rating='L', time='1700000000'):
    return f'{artist}\t{album}\t{title}\t1\t{length}\t{rating}\t{time}\t\n'.encode()


class Upload:
    """scrobble_start with its pthread caught, then the thread's worker run whole, then scrobble_poll."""
    def __init__(self, fw):
        self.fw, self.threads = fw, []
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(fn) or 0)
        self.start = fw.fn('scrobble_start', C.c_int)
        self.poll = fw.fn('scrobble_poll', C.c_int, C.POINTER(C.c_int))

    def run(self):
        """(start's answer, poll's, the scrobbles sent)."""
        started = self.start()
        if started != 1:
            return started, None, None
        sent = C.c_int(-1)
        assert self.poll(C.byref(sent)) == 0  # running
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.threads[-1])(None)
        result = self.poll(C.byref(sent))
        assert self.poll(C.byref(C.c_int())) == 0  # told once
        return started, result, sent.value


def upload(fw, log, values=ACCOUNT, reply=None, fail=()):
    config(fw, values)
    card = Card(fw, {LOG: log} if log is not None else {})
    card.fail |= set(fail)
    net = Net(fw, reply)
    return Upload(fw).run(), card, net


# The accounts and flags.

@pytest.mark.parametrize('values, want', (({}, 0), (LB_ONLY, 1), ({k: v for k, v in ACCOUNT.items() if k[1] != 'API_SECRET'}, 1),
                                          ({k: v for k, v in ACCOUNT.items() if k[0] == 'LASTFM'}, 2), (ACCOUNT, 3)))
def test_ready_is_listenbrainz_s_token_and_last_fm_s_four(fw, values, want):
    read = config(fw, values)
    assert fw.fn('scrobble_ready', C.c_int)() == want
    assert set(read) == {INI}


def test_a_key_the_file_lacks_reads_empty_not_what_the_stack_held(fw):
    config(fw, ACCOUNT)
    assert fw.fn('scrobble_ready', C.c_int)() == 3
    config(fw, {})
    assert fw.fn('scrobble_ready', C.c_int)() == 0


@pytest.mark.parametrize('value, flag', (('1', 1), ('0', 0), ('2', 0), (None, 0), ('', 0)))
def test_a_flag_is_on_only_at_1(fw, value, flag):
    config(fw, {} if value is None else {('SCROBBLE', 'AUTO'): value})
    assert fw.fn('scrobble_flag', C.c_int, C.c_char_p)(b'AUTO') == flag
    assert fw.fn('scrobble_flag', C.c_int, C.c_char_p)(b'ALBUM_ARTIST') == 0  # another key's own


def test_percent_is_its_number(fw):
    config(fw, {('SCROBBLE', 'PERCENT'): '75'})
    assert fw.fn('scrobble_value', C.c_int, C.c_char_p)(b'PERCENT') == 75
    assert fw.fn('scrobble_value', C.c_int, C.c_char_p)(b'MISSING') == 0


# The UI thread's listens.

def test_a_listen_starts_the_log_with_its_header_once_under_the_lock(fw):
    card = Card(fw)
    append = fw.fn('scrobble_append', C.c_int, C.c_char_p, C.c_uint)
    first, second = line(title='One'), line(title='Two')
    append(first, len(first))
    append(second, len(second))
    assert card.files[LOG] == HEADER + first + second
    names = [n for n, _ in fw.calls() if n in ('fw_pthread_mutex_lock', 'fw_fopen', 'fw_fclose', 'fw_pthread_mutex_unlock')]
    assert names == ['fw_pthread_mutex_lock', 'fw_fopen', 'fw_fclose', 'fw_pthread_mutex_unlock'] * 2
    assert {m for p, m in card.opened} == {'ab'}


def test_a_listen_the_card_can_t_take_still_lets_go_of_the_lock(fw):
    card = Card(fw)
    card.fail.add(f'open:{LOG}')
    append = fw.fn('scrobble_append', C.c_int, C.c_char_p, C.c_uint)
    assert append(line(), len(line())) == 0
    assert LOG not in card.files
    assert len(fw.calls('fw_pthread_mutex_lock')) == len(fw.calls('fw_pthread_mutex_unlock')) == 1


@pytest.mark.parametrize('fail', (f'write:{LOG}', 'close'))
def test_a_listen_the_card_takes_only_part_of_leaves_the_log_as_it_was(fw, fail):
    first, second = line(title='One'), line(title='Two')
    card = Card(fw, {LOG: HEADER + first})
    card.fail.add(fail)
    append = fw.fn('scrobble_append', C.c_int, C.c_char_p, C.c_uint)
    assert append(second, len(second)) == 0
    assert card.files[LOG] == HEADER + first  # no torn line for the next listen to join
    card.fail.clear()
    assert append(second, len(second)) == 1 and card.files[LOG] == HEADER + first + second
    assert len(fw.calls('fw_pthread_mutex_lock')) == len(fw.calls('fw_pthread_mutex_unlock')) == 2


def test_a_first_listen_the_card_can_t_take_leaves_no_log(fw):
    card = Card(fw)
    card.fail.add('close')
    append = fw.fn('scrobble_append', C.c_int, C.c_char_p, C.c_uint)
    assert append(line(), len(line())) == 0 and LOG not in card.files  # a headerless log would stay so
    card.fail.clear()
    assert append(line(), len(line())) == 1 and card.files[LOG] == HEADER + line()


# read_all, which navigation.c shares.

def read_all(fw, path, limit):
    size = C.c_uint(99)
    p = fw.fn('read_all', C.c_void_p, C.c_char_p, C.c_uint, C.POINTER(C.c_uint))(path.encode(), limit, C.byref(size))
    data = C.string_at(p, size.value + 1) if p else None
    libc.free(p)
    return data, size.value


def test_read_all_is_the_bytes_and_a_nul(fw):
    Card(fw, {'/f': b'abc', '/empty': b''})
    assert read_all(fw, '/f', 3) == (b'abc\0', 3)
    assert read_all(fw, '/empty', 3) == (b'\0', 0)


@pytest.mark.parametrize('why', ('missing', 'over', 'short'))
def test_read_all_of_what_it_can_t_have_whole_is_nothing(fw, why):
    card = Card(fw, {'/f': b'abcd'})
    if why == 'short':
        card.fail.add('short')  # a card error part-way
    assert read_all(fw, '/g' if why == 'missing' else '/f', 3 if why == 'over' else 9) == (None, 0)
    assert not card.open  # closed whatever happened


# An upload.

def test_no_account_never_starts_and_the_log_stays(fw):
    (started, _, _), card, net = upload(fw, HEADER + line(), values={})
    assert started == -1 and not net.requests and card.files[LOG] == HEADER + line()
    assert not fw.calls('fw_pthread_create')


def test_no_log_is_nothing_to_send(fw):
    (started, result, sent), card, net = upload(fw, None)
    assert (started, result, sent) == (1, 1, 0) and not net.requests and not card.files


def test_both_services_get_each_listen_and_the_log_keeps_only_its_header(fw):
    log = HEADER + line('A', 'One', 'X', time='1700000001') + line('B', 'Two', '', '0', time='1700000002') + \
        line('C', 'Skipped', rating='S') + b'junk\n' + b'# a comment\n'
    (started, result, sent), card, net = upload(fw, log)
    assert (started, result, sent) == (1, 1, 2)
    assert net.listens() == [[(1700000001, 'A', 'One', 'X'), (1700000002, 'B', 'Two', None)]]  # no album, no release_name
    session, scrobble = net.forms()
    assert session == {'method': 'auth.getMobileSession', 'username': 'u', 'password': 'p&w', 'api_key': 'key'}
    assert scrobble == {'method': 'track.scrobble', 'sk': 'SK123', 'api_key': 'key', 'artist[0]': 'A', 'track[0]': 'One',
                        'album[0]': 'X', 'timestamp[0]': '1700000001', 'duration[0]': '200',
                        'artist[1]': 'B', 'track[1]': 'Two', 'timestamp[1]': '1700000002'}  # no length, no duration
    assert card.files[LOG] == HEADER
    assert card.files[LOG + '.sent'] == log[len(HEADER):].replace(b'# a comment\n', b'')  # every line taken but comments


def test_requests_carry_credentials_only_over_verified_tls(fw):
    _, _, net = upload(fw, HEADER + line())
    (url, headers, _, opts), = net.to('listenbrainz')
    assert url == LB_URL and headers == ['Authorization: Token tok', 'Content-Type: application/json']
    assert all(r[0] == FM_URL for r in net.to('audioscrobbler'))
    for _, _, _, opts in net.requests:
        assert (opts[64], opts[81], opts[10065]) == (1, 2, CA) and opts[99] == 1  # verify peer and host; no signals
        assert opts[78] > 0 and opts[13] >= opts[78]  # a connect timeout within the whole one
    flags = (C.c_uint * 4)()
    ctx = (C.c_void_p * 33)()
    ctx[0x100 // 8] = C.addressof(flags)
    assert C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_void_p)(net.requests[0][3][20108])(0x9000, C.addressof(ctx), None) == 0
    assert flags[3] == 0x200000  # X509_V_FLAG_NO_CHECK_TIME: past 2038 the chain still verifies


def test_json_escapes_quotes_backslashes_and_control_bytes(fw):
    _, _, net = upload(fw, HEADER + line('Say "hi" \\ \x01bye', 'T\x1fe', 'A\x7f'), values=LB_ONLY)
    assert net.listens() == [[(1700000000, 'Say "hi" \\  bye', 'T e', 'A\x7f')]]


def test_the_form_escapes_all_but_the_unreserved(fw):
    _, _, net = upload(fw, HEADER + line('A&B=C+D%E é/~-_.', 'T'))
    assert net.forms()[1]['artist[0]'] == 'A&B=C+D%E é/~-_.'
    body = net.to('audioscrobbler')[1][2]
    assert 'artist%5B0%5D=A%26B%3DC%2BD%25E%20%C3%A9%2F~-_.&' in body


def test_listens_go_in_batches_of_50_with_one_session(fw):
    log = HEADER + b''.join(line(f'A{i}', time=str(1700000000 + i)) for i in range(51))
    (_, result, sent), card, net = upload(fw, log)
    assert result == 1 and sent == 51 and [len(b) for b in net.listens()] == [50, 1]
    assert [f['method'] for f in net.forms()] == ['auth.getMobileSession', 'track.scrobble', 'track.scrobble']
    assert [t for b in net.listens() for t, *_ in b] == list(range(1700000000, 1700000051))


# A session key in the file (tools/lastfm_key.py puts it where the password was).

KEYED = {('LASTFM', 'USER'): 'u', ('LASTFM', 'SESSION_KEY'): 'SK9', ('LASTFM', 'API_KEY'): 'key',
         ('LASTFM', 'API_SECRET'): 'sec'}


@pytest.mark.parametrize('values, want', (
        (KEYED, 2), ({k: v for k, v in KEYED.items() if k[1] != 'USER'}, 2),  # the key needs no user
        ({**ACCOUNT, **KEYED}, 3),
        ({k: v for k, v in KEYED.items() if k[1] != 'API_KEY'}, 0),  # but both of the API pair, which sign each call
        ({k: v for k, v in KEYED.items() if k[1] != 'API_SECRET'}, 0),
        ({k: v for k, v in ACCOUNT.items() if k[0] == 'LASTFM' and k[1] != 'PASSWORD'}, 0)))  # neither: as before
def test_last_fm_is_ready_with_a_session_key_in_the_password_s_place(fw, values, want):
    config(fw, values)
    assert fw.fn('scrobble_ready', C.c_int)() == want


def test_a_session_key_in_the_file_sends_the_batches_and_asks_for_no_session(fw):
    log = HEADER + b''.join(line(f'A{i}', time=str(1700000000 + i)) for i in range(51))
    (_, result, sent), card, net = upload(fw, log, KEYED)
    forms = net.forms()  # each call's signature checked too
    assert result == 1 and sent == 51 and card.files[LOG] == HEADER
    assert [f['method'] for f in forms] == ['track.scrobble', 'track.scrobble'] and all(f['sk'] == 'SK9' for f in forms)
    assert not any('username' in f or 'password' in f for f in forms)


def test_a_session_key_beside_a_password_is_the_one_used_and_the_password_stays_unsent(fw):
    (_, result, sent), _, net = upload(fw, HEADER + line(), {**ACCOUNT, **KEYED})
    assert result == 1 and sent == 1 and [f['method'] for f in net.forms()] == ['track.scrobble']
    assert not any('p%26w' in body for _, _, body, _ in net.requests)


def test_a_session_key_last_fm_refuses_fails_the_upload_and_the_password_is_not_tried_instead(fw):
    """A key the user revoked in Last.fm's settings (its error 9): the listens wait in the log, as after any refusal."""
    def revoked(url, body):
        if 'listenbrainz' in url:
            return 200, '{"status":"ok"}'
        return 403, '{"error":9,"message":"Invalid session key - Please re-authenticate"}'
    log = HEADER + line()
    (_, result, sent), card, net = upload(fw, log, {**ACCOUNT, **KEYED}, reply=revoked)
    assert result == -1 and sent == 0 and card.files[LOG] == log
    assert [f['method'] for f in net.forms()] == ['track.scrobble']


def test_a_line_still_being_written_stays_in_the_log(fw):
    partial = b'Art\tAl\tSo'
    (_, result, sent), card, _ = upload(fw, HEADER + line() + partial)
    assert result == 1 and sent == 1 and card.files[LOG] == HEADER + partial


def test_an_incomplete_last_fm_is_off(fw):
    """The API pair without a password or session key: no sign-in is tried with an empty password."""
    values = {**LB_ONLY, ('LASTFM', 'USER'): 'u', ('LASTFM', 'API_KEY'): 'k', ('LASTFM', 'API_SECRET'): 's'}
    (_, result, sent), _, net = upload(fw, HEADER + line(), values=values)
    assert result == 1 and sent == 1 and not net.to('audioscrobbler') and len(net.to('listenbrainz')) == 1


def test_last_fm_alone_sends_no_json(fw):
    values = {k: v for k, v in ACCOUNT.items() if k[0] == 'LASTFM'}
    (_, result, sent), _, net = upload(fw, HEADER + line(), values=values)
    assert result == 1 and sent == 1 and not net.to('listenbrainz') and len(net.forms()) == 2


def failing(when):
    def reply(url, body):
        code, text = Net.ok(url, body)
        return when(url, body, code, text)
    return reply


@pytest.mark.parametrize('why, reply', (
    ('a 500', failing(lambda url, body, code, text: (500, text) if 'listenbrainz' in url else (code, text))),
    ('an error in a 200', failing(lambda url, body, code, text: (200, '{"error":9,"message":"Invalid session key"}')
                                  if 'track.scrobble' in body else (code, text))),
    ('no session key', failing(lambda url, body, code, text: (200, '{"session":{}}') if 'getMobile' in body else (code, text))),
    ('no connection', failing(lambda url, body, code, text: (None, '')))))
def test_a_batch_a_service_refuses_stays_in_the_log(fw, why, reply):
    log = HEADER + line()
    (_, result, sent), card, _ = upload(fw, log, reply=reply)
    assert (result, sent) == (-1, 0) and card.files[LOG] == log and LOG + '.sent' not in card.files


def echoed(url, body):
    """Last.fm's replies, which echo names: auth.getMobileSession's the account's, track.scrobble's each listen's (a
    one-listen batch's is an object, not a list)."""
    form = dict(urllib.parse.parse_qsl(body))
    if 'auth.getMobileSession' in body:
        return 200, json.dumps({'session': {'name': form['username'], 'key': 'SK123', 'subscriber': 0}}, separators=(',', ':'))
    if 'track.scrobble' not in body:
        return Net.ok(url, body)
    names = [{k: {'corrected': '0', '#text': form.get(f'{k}[{i}]', '')} for k in ('artist', 'album', 'track')} |
             {'ignoredMessage': {'code': '0', '#text': ''}, 'timestamp': form[f'timestamp[{i}]']}
             for i in range(sum(k.startswith('timestamp[') for k in form))]
    reply = {'scrobble': names[0] if len(names) == 1 else names, '@attr': {'accepted': len(names), 'ignored': 0}}
    return 200, json.dumps({'scrobbles': reply}, ensure_ascii=False, separators=(',', ':'))


@pytest.mark.parametrize('names', (('error', 'Song', 'Album'), ('Art', 'error', 'Album'), ('Art', 'Song', 'error'),
                                   ('Say "error": 9', 'Song', 'Album')))  # escaped in the echo, no key
def test_a_listen_named_error_is_sent_and_retired(fw, names):
    """Only Last.fm's "error" key is a refusal: a name it echoes once held every later upload back behind it."""
    artist, title, album = names
    log = HEADER + line(artist, title, album) + line(time='1700000001')
    (_, result, sent), card, net = upload(fw, log, reply=echoed)
    assert (result, sent) == (1, 2) and card.files[LOG] == HEADER and card.files[LOG + '.sent'] == log[len(HEADER):]
    assert net.forms()[1]['artist[0]'] == artist


def test_an_account_named_error_gets_its_session(fw):
    (_, result, sent), _, net = upload(fw, HEADER + line(), {**ACCOUNT, ('LASTFM', 'USER'): 'error'}, reply=echoed)
    assert (result, sent) == (1, 1) and net.forms()[1]['sk'] == 'SK123'


def test_the_second_batch_refused_keeps_it_and_retires_the_first(fw):
    log = HEADER + b''.join(line(f'A{i}', time=str(1700000000 + i)) for i in range(60))
    batches = []

    def reply(url, body):
        if 'listenbrainz' in url:
            batches.append(body)
        return (500, '') if len(batches) == 2 and 'listenbrainz' in url else Net.ok(url, body)
    (_, result, sent), card, _ = upload(fw, log, reply=reply)
    assert (result, sent) == (-1, 50)
    assert card.files[LOG] == HEADER + log[len(HEADER):].split(b'\n', 50)[50]
    assert card.files[LOG + '.sent'].count(b'\n') == 50


def at_most(size):
    """The artist's length that makes line()'s line size bytes, its newline aside."""
    return size - len(line('')) + 1


@pytest.mark.parametrize('big, sent', (('"' * at_most(2560), 50),  # 2 bytes each escaped, at the 2560-byte cap
                                       ('a' * (BODY_MAX // 50), 0)))  # over the cap: 50 would overrun the JSON body
def test_a_batch_fits_its_json_body_and_a_line_too_big_for_it_is_retired_unsent(fw, big, sent):
    log = HEADER + b''.join(line(big, time=str(1700000000 + i)) for i in range(50))
    (_, result, got), card, net = upload(fw, log, values=LB_ONLY)
    assert (result, got) == (1, sent) and len(net.requests) == (1 if sent else 0)
    assert card.files[LOG] == HEADER and card.files[LOG + '.sent'].count(b'\n') == 50  # gone, not blocking


@pytest.mark.parametrize('one, sent', ((line('&' * 2547, album='&', title='&', length='1', time='1'), 50),  # all sent fields escaped, at the cap
                                       (line('&' * (BODY_MAX // 50 // 3)), 0)))  # over the cap: the form would overrun
def test_a_batch_fits_its_form_and_a_line_too_big_for_it_is_retired_unsent(fw, one, sent):
    log = HEADER + one * 50
    values = {k: v for k, v in ACCOUNT.items() if k[0] == 'LASTFM'}
    (_, result, got), card, net = upload(fw, log, values=values)
    assert (result, got) == (1, sent)
    assert [f['method'] for f in net.forms()] == (['auth.getMobileSession', 'track.scrobble'] if sent else [])
    assert card.files[LOG] == HEADER and card.files[LOG + '.sent'].count(b'\n') == 50


@pytest.mark.parametrize('size, sent', ((2560, 2), (2561, 1)))
def test_a_line_over_2560_bytes_is_retired_unsent(fw, size, sent):
    long = line('a' * at_most(size))
    log = HEADER + long + line(time='1700000001')
    (_, result, got), card, net = upload(fw, log, values=LB_ONLY)
    assert len(long) == size + 1 and (result, got) == (1, sent) and [len(b) for b in net.listens()] == [sent]
    assert card.files[LOG] == HEADER and long in card.files[LOG + '.sent']


def long_lines(n):
    """n listens of about 1 KB, each its own time."""
    return [line('a' * 1000, time=str(1700000000 + i)) for i in range(n)]


def test_one_upload_takes_a_log_of_several_megabytes_a_window_at_a_time(fw):
    lines = long_lines(5 * (1 << 20) // 2 // len(line('a' * 1000)))  # two and a half windows
    (_, result, sent), card, net = upload(fw, HEADER + b''.join(lines), values=LB_ONLY)
    assert (result, sent) == (1, len(lines)) and sum(map(len, net.listens())) == len(lines)
    assert card.files[LOG] == HEADER and card.files[LOG + '.sent'] == b''.join(lines)
    assert [p for p, m in card.opened].count(LOG + '.tmp') == 3  # a rewrite a window


def test_a_window_with_nothing_to_send_does_not_end_the_upload(fw):
    skipped = line(rating='S') * ((1 << 20) // len(line(rating='S')) + 10)  # past the first window
    (_, result, sent), card, _ = upload(fw, HEADER + skipped + line() * 3, values=LB_ONLY)
    assert (result, sent) == (1, 3) and card.files[LOG] == HEADER


def test_a_batch_refused_in_a_later_window_keeps_the_rest_and_the_earlier_windows_stay_sent(fw):
    lines = long_lines(3 * (1 << 20) // 2 // len(line('a' * 1000)))  # a window and a half
    last = lines[-1].split(b'\t')[6].decode()

    def reply(url, body):
        return (500, '') if last in body else Net.ok(url, body)
    (_, result, sent), card, _ = upload(fw, HEADER + b''.join(lines), values=LB_ONLY, reply=reply)
    assert result == -1 and (1 << 20) // len(lines[0]) < sent < len(lines)  # past the first window, short of the end
    assert card.files[LOG] == HEADER + b''.join(lines[sent:]) and card.files[LOG + '.sent'] == b''.join(lines[:sent])


def test_a_megabyte_with_no_newline_is_dropped_as_junk(fw):
    log = HEADER + b'x' * (1 << 20) + b'\n' + line()
    (_, result, sent), card, _ = upload(fw, log, values=LB_ONLY)
    assert (result, sent) == (1, 1) and card.files[LOG] == HEADER  # the junk's rest skipped, line() sent


@pytest.mark.parametrize('tail, sent', ((2560, 0), (2561, 1)))
def test_a_full_window_s_cut_line_over_2560_bytes_is_junk(fw, tail, sent):
    fill = (1 << 20) - len(HEADER) - tail
    log = HEADER + b'#' * (fill - 1) + b'\n' + b'x' * tail + line()  # the window ends tail bytes into a line
    (_, result, got), card, net = upload(fw, log, values=LB_ONLY)
    assert (result, got) == (1, sent) and card.files[LOG] == HEADER  # waits, then too long; or its head dropped


@pytest.mark.parametrize('fail', (f'open:{LOG}.tmp', f'write:{LOG}.tmp', f'write+:{LOG}.tmp', 'readerr', 'sync', 'close',
                                  f'open:{LOG}.sent', f'write:{LOG}.sent', 'rename'))
def test_a_log_rewrite_that_fails_keeps_the_listens(fw, fail):
    log = HEADER + line() + b'Art\tAl'  # a line still being written, for the copy-through
    (_, result, sent), card, _ = upload(fw, log, fail=[fail])
    assert (result, sent) == (-1, 1) and card.files[LOG] == log and LOG + '.tmp' not in card.files  # sent, but kept to send again


def test_the_rewrite_holds_the_lock_and_writes_the_new_log_before_sent(fw):
    log = HEADER + line()
    upload(fw, log)
    names = [(n, C.string_at(a[0]).decode() if n == 'fw_fopen' else '') for n, a in fw.calls()
             if n in ('fw_pthread_mutex_lock', 'fw_pthread_mutex_unlock', 'fw_fopen', 'fw_rename')]
    assert names[-6:] == [('fw_pthread_mutex_lock', ''), ('fw_fopen', LOG), ('fw_fopen', LOG + '.tmp'), ('fw_fopen', LOG + '.sent'),
                          ('fw_rename', ''), ('fw_pthread_mutex_unlock', '')]
    syncs = [n for n, _ in fw.calls() if n in ('fw_fsync', 'fw_rename')]
    assert syncs == ['fw_fsync', 'fw_fsync', 'fw_rename']  # both files on the card before the old log goes


def test_a_second_start_while_one_runs_is_refused(fw):
    config(fw, ACCOUNT)
    Card(fw)
    Net(fw)
    up = Upload(fw)
    assert up.start() == 1 and up.start() == 0 and len(up.threads) == 1


def test_no_thread_is_no_upload(fw):
    config(fw, ACCOUNT)
    Card(fw)
    fw.mock('fw_pthread_create', lambda *_: 11)  # EAGAIN
    assert fw.fn('scrobble_start', C.c_int)() == -1
    assert fw.fn('scrobble_poll', C.c_int, C.POINTER(C.c_int))(C.byref(C.c_int())) == 0  # nothing to report
