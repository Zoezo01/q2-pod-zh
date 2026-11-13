"""tidal.c (docs/internals.md#tidal-cache, #tidal-track-lists) end to end: tidal_play's job downloaded by its
thread to the card through libcurl, tagged by ffmpeg from the queue row, kept or dropped, and the cache trimmed to
Streaming Configuration's cap; Clear All against a running download; the stream threads' gates; and Tidal's track
lists paged whole. tidal_ext, tidal_name, cached and progress alone are test_streaming.py's, whose td mirror this
shares."""
import ctypes as C
import re

import pytest

import host
from conftest import O, Deques, Files, claim, high_halves, libc
from test_streaming import Td

TIDAL = '/mnt/mmc/.tidal'
CACHE_SIZE = f'{TIDAL}/.cache-size'
URL = 'https://cdn.example/a/0.flac?token=1'
NEXT = 'https://cdn.example/b/0.flac?token=2'
MB = 1 << 20
CURLOPT_URL, CURLOPT_WRITEDATA, CURLOPT_WRITEFUNCTION = 10002, 10001, 20011
CURLOPT_NOSIGNAL, CURLOPT_FOLLOWLOCATION, CURLOPT_LOW_SPEED_LIMIT, CURLOPT_LOW_SPEED_TIME = 99, 52, 19, 20
CURLOPT_XFERINFOFUNCTION, CURLOPT_XFERINFODATA, CURLOPT_NOPROGRESS = 20219, 10057, 43
CURLE_OK, CURLE_WRITE_ERROR, CURLE_ABORTED_BY_CALLBACK = 0, 23, 42
STOPPED, PLAYING = 1, 2
EINTR, EAGAIN = 4, 11


class Card(Files):
    """The card's and /tmp's files (conftest's Files), a folder's listing, each file's size and age for trim's stat
    (o32: st_size word 12, st_mtime word 16; sizes stands in for bytes too many to hold), the free space and access."""
    def __init__(self, fw, files=None, free_mb=4096):
        super().__init__(fw, dict(files or {}))
        self.sizes, self.ages, self.free_mb, self.dirs, self.ents, self.handles = {}, {}, free_mb, {}, [], 0
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d) and 0)
        fw.mock('fw___xstat', self.stat)
        fw.mock('fw_statfs', self.statfs)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files else -1)
        fw.mock('fw_fputs', lambda s, f, *_: self.open[f].__setitem__(2, self.open[f][2] + C.string_at(s)))

    def listing(self):
        """The cache folder's names, its dot files left out."""
        return sorted(p[len(TIDAL) + 1:] for p in self.files if p.startswith(TIDAL + '/') and p[len(TIDAL) + 1] != '.')

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        self.handles += 1
        self.dirs[0x3100 + self.handles] = sorted(p[len(path) + 1:] for p in self.files
                                                  if p.startswith(path + '/') and '/' not in p[len(path) + 1:])
        return 0x3100 + self.handles

    def readdir(self, d, *_):
        if not self.dirs[d]:
            return 0
        ent = C.create_string_buffer(268)  # host.py's struct dirent: d_name at 11
        name = self.dirs[d].pop(0).encode()
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.ents.append(ent)
        return C.addressof(ent)

    def stat(self, version, path, st, *_):
        path = C.string_at(path).decode()
        if path not in self.files:
            return -1
        words = C.cast(st, C.POINTER(C.c_uint))
        words[12], words[16] = self.sizes.get(path, len(self.files[path])), self.ages.get(path, 0)
        return 0

    def statfs(self, path, buf, *_):
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, self.free_mb * MB // 4096


class Cdn:
    """libcurl over a dict of URLs: perform writes the body through the write callback in chunks and calls the
    transfer's progress callback after each, as libcurl does; a progress that answers nonzero aborts it. on_chunk
    runs after each chunk is written: another track starting, a Stop, a Clear All."""
    def __init__(self, fw, bodies, code=200, chunks=4):
        self.fw, self.bodies, self.code, self.chunks = fw, bodies, code, chunks
        self.opts, self.fetched, self.on_chunk = {}, [], lambda i: None
        fw.mock('fw_curl_easy_init', lambda *_: 0x7700)
        fw.mock('fw_curl_easy_setopt', lambda c, opt, value, *_: self.opts.__setitem__(opt, value))
        fw.mock('fw_curl_easy_perform', self.perform)
        fw.mock('fw_curl_easy_getinfo', self.getinfo)

    def perform(self, c, *_):
        url = C.string_at(self.opts[CURLOPT_URL]).decode()
        self.fetched.append(url)
        body = self.bodies[url]
        write = C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint, C.c_uint, C.c_void_p)(self.opts[CURLOPT_WRITEFUNCTION])
        progress = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)(self.opts[CURLOPT_XFERINFOFUNCTION])
        step = -(-len(body) // self.chunks)
        for i in range(self.chunks):
            piece = C.create_string_buffer(body[i * step:(i + 1) * step])
            n = len(piece) - 1
            if n and write(piece, 1, n, self.opts[CURLOPT_WRITEDATA]) != n:
                return CURLE_WRITE_ERROR
            self.on_chunk(i)
            if progress(self.opts[CURLOPT_XFERINFODATA], len(body), min(len(body), (i + 1) * step), 0, 0):
                return CURLE_ABORTED_BY_CALLBACK
        return CURLE_OK

    def getinfo(self, c, info, out, *_):
        assert info == 0x200002  # CURLINFO_RESPONSE_CODE
        C.c_long.from_address(out).value = self.code
        return 0


class Tidal:
    """tidal_play as stock's stream thread calls it, with stock's player status and clock; the download thread it
    starts runs on the test's own thread (run())."""
    def __init__(self, fw, card, level=0):
        self.fw, self.card, self.ms, self.status = fw, card, 1000000, PLAYING
        self.td, self.played, self.threads = fw.var('tidal__td', Td), [], []
        fw.mock('stock_tidal_trampoline', lambda url, *_: self.played.append(C.string_at(url).decode() if url else None))
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(fn) or 0)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('fw_clock_gettime', self.clock)
        fw.var('fw_ctx_tidalStreamingLevelNow', C.c_int).value = level
        self.play_fn = fw.fn('tidal_play', C.c_int, C.c_char_p, C.c_char_p)

    def clock(self, which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
        t[0], t[1] = self.ms // 1000, self.ms % 1000 * 1000000

    def play(self, url, track_id):
        self.play_fn(url.encode(), track_id.encode())

    def run(self):
        """The newest download thread, to its end."""
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.threads[-1])(None)


class Ffmpeg:
    """fork and waitpid for tag()'s ffmpeg: as it exits, the child has written out, its remux of the input (the
    .tmp being tagged), aside at <input>.tag.tmp, or nothing for None; status is waitpid's."""
    def __init__(self, fw, card, status=0, out=b'TAGGED', then=lambda: None):
        self.card, self.status, self.out, self.then, self.waits = card, status, out, then, 0
        fw.mock('fw_fork', lambda *_: 4242)
        fw.mock('fw_waitpid', self.wait)

    def wait(self, pid, status, options, *_):
        self.waits += 1
        assert pid == 4242
        tmp, = [p for p in self.card.files if p.endswith('.tmp') and not p.endswith('.tag.tmp')]
        if self.out is not None:
            self.card.files[tmp + '.tag.tmp'] = self.out
        C.c_int.from_address(status).value = self.status
        self.then()
        return pid


@pytest.fixture
def queue(fw):
    """Stock's play queue (mcl_pdeqplaylist) of Tidal rows, laid out as stock's 32-bit records: a pointer a word.
    P() reads 8 bytes here, so REC_TITLE, REC_ALBUM and REC_ARTIST, a word apart, overlap: one row's strings sit at
    fixed addresses whose halves make each read whole (the title's high half is the album's low, the album's high
    half the artist's low). A low half may sit near its page's end, so each string gets that page and the next."""
    assert (O['REC_ALBUM'], O['REC_ARTIST']) == (O['REC_TITLE'] + 4, O['REC_TITLE'] + 8)
    pages = []
    try:
        for at in Queue.TITLE, Queue.ALBUM, Queue.ARTIST:
            page = at & ~0xfff
            got = libc.mmap(page, 0x2000, 3, 0x100022, -1, 0)  # read/write, MAP_FIXED_NOREPLACE
            if got not in (None, C.c_void_p(-1).value):  # mapped, if elsewhere (a kernel without NOREPLACE)
                pages.append(got)
            assert got == page
        yield Queue(fw)
    finally:  # what was mapped goes, a map that failed partway too, so the next test can map them again
        for page in pages:
            assert libc.munmap(C.c_void_p(page), C.c_size_t(0x2000)) == 0


class Queue:
    HIGHS = high_halves(3)  # this file's (conftest)
    TITLE, ALBUM, ARTIST = HIGHS[0] << 32, HIGHS[1] << 32 | HIGHS[0], HIGHS[2] << 32 | HIGHS[1]

    def __init__(self, fw):
        self.deques, self.keep = Deques(fw), []
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.handle = self.deques.new()

    def string(self, s):
        self.keep.append(C.create_string_buffer(s.encode()))
        return C.addressof(self.keep[-1])

    def row(self, tidal_id, title=None, artist='', album='', cover=''):
        """A row; a titled one takes the fixed addresses, so one of those at a time."""
        r = C.create_string_buffer(0x60)
        self.keep.append(r)
        C.c_void_p.from_address(C.addressof(r) + O['REC_TIDAL_ID']).value = self.string(tidal_id)
        if title is not None:
            for at, s in (self.TITLE, title), (self.ALBUM, album), (self.ARTIST, artist):
                C.memmove(at, s.encode() + b'\0', len(s.encode()) + 1)
            C.c_ulonglong.from_address(C.addressof(r) + O['REC_TITLE']).value = self.TITLE
            C.c_ulonglong.from_address(C.addressof(r) + O['REC_ARTIST']).value = self.ARTIST
            C.c_void_p.from_address(C.addressof(r) + O['REC_COVER']).value = self.string(cover)
        self.deques[self.handle].append(C.addressof(r))


def cached(path):
    return f'{TIDAL}/{path}'


# A track's download, end to end.

def test_a_streamed_track_is_kept_on_the_card_and_plays_from_it_next_time(fw):
    card, cdn = Card(fw), Cdn(fw, {URL: b'fLaC' + bytes(range(256)) * 4})
    t = Tidal(fw, card)
    t.play(URL, '123')
    assert t.played == [URL] and len(t.threads) == 1
    t.run()
    assert card.listing() == ['123-0.flac'] and card.files[cached('123-0.flac')] == cdn.bodies[URL]
    assert t.td.busy == 0 and len(fw.calls('fw_pthread_detach')) == 1  # it ended: the next track starts one
    # libcurl follows the CDN's redirects, raises no signal off the UI thread, gives up on a stalled transfer and
    # asks the progress callback, which can stop it
    opts = cdn.opts
    assert opts[CURLOPT_FOLLOWLOCATION] == 1 and opts[CURLOPT_NOSIGNAL] == 1 and opts[CURLOPT_NOPROGRESS] == 0
    assert opts[CURLOPT_LOW_SPEED_LIMIT] > 0 and opts[CURLOPT_LOW_SPEED_TIME] > 0
    t.play(URL, '123')
    assert t.played == [URL, cached('123-0.flac')] and len(t.threads) == 1 and cdn.fetched == [URL]


def test_a_higher_quality_never_plays_a_lower_ones_copy(fw):
    card = Card(fw, {cached('123-0.flac'): b'low'})
    Cdn(fw, {URL: b'high'})
    t = Tidal(fw, card, level=3)
    t.play(URL, '123')
    t.run()
    assert t.played == [URL] and card.files[cached('123-3.flac')] == b'high'


def test_a_skip_mid_download_drops_its_copy_and_the_next_track_downloads_after(fw):
    card, cdn = Card(fw), Cdn(fw, {URL: b'a' * 4000, NEXT: b'b' * 3000})
    t = Tidal(fw, card)
    t.play(URL, '1')
    cdn.on_chunk = lambda i: t.play(NEXT, '2') if cdn.fetched == [URL] and i == 1 else None
    t.run()
    assert t.played == [URL, NEXT] and len(t.threads) == 1  # one download at a time: the same thread took it
    assert cdn.fetched == [URL, NEXT] and card.listing() == ['2-0.flac'] and card.files[cached('2-0.flac')] == b'b' * 3000


def test_a_refetch_of_the_same_track_leaves_its_download_running(fw):
    card, cdn = Card(fw), Cdn(fw, {URL: b'a' * 4000})
    t = Tidal(fw, card)
    t.play(URL, '1')
    cdn.on_chunk = lambda i: t.play(URL, '1') if i == 1 else None
    t.run()
    assert cdn.fetched == [URL] and card.listing() == ['1-0.flac'] and len(t.threads) == 1


@pytest.mark.parametrize('stopped_ms, kept', [(2999, True), (3000, False)])
def test_a_stop_held_three_seconds_drops_the_copy_a_shorter_one_keeps_it(fw, stopped_ms, kept):
    card, cdn = Card(fw), Cdn(fw, {URL: b'a' * 4000})
    t = Tidal(fw, card)
    t.play(URL, '1')
    def chunk(i):
        if i == 0:  # stopped from an odd ms: progress marks a stop's start now | 1 (0 is not stopped), so this one's own
            t.ms, t.status = 1000001, STOPPED
        elif i == 1:  # the stop's last callback, stopped_ms after its first
            t.ms += stopped_ms
        else:  # between queued tracks the player plays again
            t.status = PLAYING
    cdn.on_chunk = chunk
    t.run()
    assert card.listing() == (['1-0.flac'] if kept else [])


@pytest.mark.parametrize('code', [403, 404, 500, 0])
def test_only_an_http_200_is_kept(fw, code):
    card = Card(fw)
    Cdn(fw, {URL: b'<html>denied</html>'}, code=code)
    t = Tidal(fw, card)
    t.play(URL, '1')
    t.run()
    assert card.listing() == [] and t.played == [URL]


def test_a_card_that_wont_take_the_file_downloads_nothing(fw):
    card, cdn = Card(fw), Cdn(fw, {URL: b'a'})
    fw.mock('fw_fopen', lambda path, mode, *_: 0 if b'w' in C.string_at(mode) else card.fopen(path, mode))
    t = Tidal(fw, card)
    t.play(URL, '1')
    t.run()
    assert cdn.fetched == [] and not fw.calls('fw_curl_easy_init') and card.listing() == []


def test_a_libcurl_that_wont_start_leaves_no_empty_file(fw):
    card = Card(fw)
    Cdn(fw, {URL: b'a'})
    fw.mock('fw_curl_easy_init', lambda *_: 0)
    t = Tidal(fw, card)
    t.play(URL, '1')
    t.run()
    assert card.listing() == [] and len(fw.calls('fw_fclose')) == 1


@pytest.mark.parametrize('call', ['fw_fflush', 'fw_fsync', 'fw_ferror', 'fw_fclose'])
def test_a_copy_that_may_not_have_reached_the_card_is_not_kept(fw, call):
    card = Card(fw)
    Cdn(fw, {URL: b'a' * 100})
    if call == 'fw_fclose':
        fw.mock(call, lambda f, *_: card.fclose(f) or -1)
    else:
        fw.mock(call, lambda *_: -1 if call != 'fw_ferror' else 1)
    t = Tidal(fw, card)
    t.play(URL, '1')
    t.run()
    assert card.listing() == []


def test_a_thread_that_cant_start_leaves_nothing_waiting_and_the_next_track_starts_one(fw):
    card = Card(fw)
    Cdn(fw, {URL: b'a', NEXT: b'b'})
    t = Tidal(fw, card)
    fw.mock('fw_pthread_create', lambda t_, attr, fn, arg, *_: t.threads.append(fn) or EAGAIN)
    t.play(URL, '1')
    assert t.played == [URL] and (t.td.busy, t.td.job) == (0, 0)
    t.play(NEXT, '2')
    assert t.played == [URL, NEXT] and len(t.threads) == 2 and not fw.calls('fw_pthread_detach')


class Au(C.Structure):
    """tidal.c's au: a URL queue's URLs (0 for a card path) and generation."""
    _fields_ = [('lock', C.c_uint * 16), ('urls', C.POINTER(C.c_char_p)), ('n', C.c_uint), ('gen', C.c_uint),
                ('play', C.c_uint), ('lyr', C.c_char * 64), ('start', C.c_uint * 16),
                ('items', C.POINTER(C.c_char_p)), ('fav', C.POINTER(C.c_ubyte)), ('app', C.c_char * 16),
                ('hearts', C.c_int)]


def urls_play(fw, files, lines, path='/tmp/q2-a.urls'):
    """app_urls_play over a Q2URLS/1 file of lines, put in files (conftest's Files, mocked): its result.
    toolsFreeStSongInfo, called at its stock address, is a ret meanwhile, as below."""
    files.files[path] = b'#Q2URLS/1\n' + b''.join(line + b'\n' for line in lines)
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    free_rec = int(re.findall(r'toolsFreeStSongInfo \(\(void \(\*\)\(void \*\)\)(0x[0-9a-f]+)u\)',
                              (host.ROOT/'patch/tidal.c').read_text())[0], 16)
    claim(free_rec)
    saved = C.string_at(free_rec, 1)
    C.memmove(free_rec, b'\xc3', 1)
    try:
        return fw.fn('app_urls_play', C.c_int, C.c_char_p)(path.encode())
    finally:
        C.memmove(free_rec, saved, 1)


# A card app's songs' hearts (docs/librespot.md): a line's 7th field is the song's item, the app's own id for it,
# and its 8th its heart; Now Playing's heart on such a song is the app's.
HEARTS = [b'https://x/a.flac\tA\tBroadcast\t\t\t200\tid-a\t1', b'https://x/b.flac\tB\t\t\t\t\tid-b\t0',
          b'https://x/c.flac\tC\t\t\t\t200',  # an older app's line: no item, the heart stays stock's
          b'/mnt/mmc/Jellyfin/d.flac\tD\t\t\t\t\tid-d\tyes',  # a synced song in the queue; only 1 is a favourite
          b'https://x/e.flac\tE\t\t\t\t\t' + b'i' * 128 + b'\t1',  # an item too long to be one
          b'https://x/f.flac\tF\t\t\t\t\tid-f\t1\r',  # a CRLF file's line: still a favourite
          b'https://x/g.flac\tG\t\t\t\t\tid-g\t1\tlater',  # a field after it, as a later protocol may add
          b'https://x/h.flac\tH\t\t\t\t\tid-h\t10']  # not 1


def test_a_url_queue_keeps_each_song_s_item_and_heart(fw):
    Deques(fw)
    assert urls_play(fw, Files(fw), HEARTS) == 1
    au = fw.var('tidal__au', Au)
    assert [au.items[i] for i in range(8)] == [b'id-a', b'id-b', None, b'id-d', None, b'id-f', b'id-g', b'id-h']
    assert [au.fav[i] for i in range(8)] == [1, 0, 0, 0, 0, 1, 1, 0] and au.app == b'a'


def heart(fw):
    return fw.fn('app_fav', C.c_int)()


def favourite(fw):
    return fw.var('isFav_playing', C.c_ubyte).value


def test_the_heart_on_an_app_s_song_flips_and_goes_to_the_app(fw, queue, mcl):
    files = Files(fw)
    urls_play(fw, files, HEARTS)
    queue.row('app:1:0')
    mcl.type, mcl.pos = 0xf1, 0
    assert heart(fw) == 0 and favourite(fw) == 0  # a favourite: taken away
    assert heart(fw) == 1 and favourite(fw) == 1
    assert files.files['/mnt/mmc/.a/fav.log'] == b'\nitem\t0\tid-a\n\nitem\t1\tid-a\n'  # each after an LF too
    assert fw.var('tidal__au', Au).fav[0] == 1


@pytest.mark.parametrize('kind, tidal_id, want', [(0xf1, '12345', -1),  # stock Tidal's own: stock's heart
                                                  (0xf001, 'app:1:0', -1),  # not a stream's queue
                                                  (0xf1, 'app:1:2', -3),  # its line named no item: no heart,
                                                  (0xf1, 'app:1:4', -3),  # nor Tidal's for it
                                                  (0xf1, 'app:0:0', -3)])  # an older queue's
def test_any_other_song_s_heart_is_stock_s_and_an_app_s_never_tidal_s(fw, queue, mcl, kind, tidal_id, want):
    files = Files(fw)
    urls_play(fw, files, HEARTS)
    queue.row(tidal_id)
    mcl.type, mcl.pos = kind, 0
    assert heart(fw) == want and '/mnt/mmc/.a/fav.log' not in files.files


def test_a_press_the_card_wont_take_leaves_the_heart(fw, queue, mcl):
    urls_play(fw, Files(fw), HEARTS)
    queue.row('app:1:1')
    mcl.type, mcl.pos = 0xf1, 0
    fw.mock('fw_fopen', lambda *_: 0)
    assert heart(fw) == -2 and favourite(fw) == 0 and fw.var('tidal__au', Au).fav[1] == 0


def test_an_app_s_song_starting_shows_its_heart(fw, queue, mcl):
    urls_play(fw, Files(fw), HEARTS)
    for tidal_id in ('app:1:0', 'app:1:2', 'app:1:1'):
        queue.row(tidal_id)
    track = fw.fn('ringnav_tidal_track', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)
    for pos, want in ((0, 1), (1, 0), (2, 0)):  # a song without an item has no heart
        mcl.pos = pos
        track(0, 0, 0, 0)
        assert favourite(fw) == want, pos


def test_a_queue_loading_takes_an_app_song_s_heart_with_it(fw, queue, mcl):
    """Stock sets isFav_playing only when a Tidal page checks its favourites, not per track: a Tidal queue played
    after an app's would show the last app song's heart. A load clears it (app_fav_load, from ringnav_load), and
    leaves stock's own alone."""
    urls_play(fw, Files(fw), HEARTS)
    queue.row('app:1:0')
    fw.fn('ringnav_tidal_track', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(0, 0, 0, 0)
    assert favourite(fw) == 1
    load = fw.fn('app_fav_load', None)
    load()
    assert favourite(fw) == 0
    fw.var('isFav_playing', C.c_ubyte).value = 1  # stock's, from a Tidal page's check
    load()
    assert favourite(fw) == 1


def test_a_load_through_ringnav_load_takes_an_app_song_s_heart_with_it(fw, queue, mcl):
    """The clear is ringnav_load's own call: deleting it from there keeps the last app song's heart on."""
    urls_play(fw, Files(fw), HEARTS)
    queue.row('app:1:0')
    fw.fn('ringnav_tidal_track', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(0, 0, 0, 0)
    assert favourite(fw) == 1
    fw.mock('stock_load_trampoline', lambda *_: 0)
    fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)(None, 0, 1)
    assert favourite(fw) == 0


def test_a_url_queue_takes_web_urls_and_card_paths_alone(fw):
    """A server's line reaches stock's player as a URL or as a card path, nothing else (the rule a Stream mode
    playlist's lines pass in addon.c); a cover is fetched by stock's curl thread, so it is a web URL or none."""
    lines = [b'https://x/a.flac\tA', b'ftp://x/b.flac\tB', b'/etc/passwd\tC', b'/mnt/mmc/../etc/passwd\tD',
             b'mnt/mmc/x.flac\tX', b'/mnt/mmc/Music/e.flac\tE\t\t\thttps://x/e.jpg',
             b'https://x/f.flac\tF\t\t\tfile:///etc/shadow']
    Files(fw, {'/tmp/q2-a.urls': b'#Q2URLS/1\n' + b'\n'.join(lines) + b'\n'})
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    deques, covers = Deques(fw), []

    def push(q, rec, *_):
        cover = C.c_void_p.from_address(rec + O['REC_COVER']).value
        covers.append(C.string_at(cover) if cover else b'')
        deques[q].append(rec)
    fw.mock('fw__deque_push_back', push)
    # toolsFreeStSongInfo is called at its stock address (tidal.c's own define): a ret there for the test, so each
    # record's strings stay for the asserts; the byte is put back after, as conftest's map outlives a load
    free_rec = int(re.findall(r'toolsFreeStSongInfo \(\(void \(\*\)\(void \*\)\)(0x[0-9a-f]+)u\)',
                              (host.ROOT/'patch/tidal.c').read_text())[0], 16)
    claim(free_rec)
    saved = C.string_at(free_rec, 1)
    C.memmove(free_rec, b'\xc3', 1)
    try:
        assert fw.fn('app_urls_play', C.c_int, C.c_char_p)(b'/tmp/q2-a.urls') == 1
    finally:
        C.memmove(free_rec, saved, 1)
    au = fw.var('tidal__au', Au)
    assert au.n == 3 and [au.urls[i] for i in range(3)] == [b'https://x/a.flac', None, b'https://x/f.flac']
    assert covers == [b'', b'https://x/e.jpg', b'']


def test_a_full_card_streams_without_a_download(fw):
    card = Card(fw, free_mb=8)
    Cdn(fw, {URL: b'a'})
    t = Tidal(fw, card)
    t.play(URL, '1')
    assert t.played == [URL] and t.threads == [] and t.td.job == 0


# Tags from the queue row.

def test_the_queue_row_names_the_copy_and_ffmpeg_tags_it(fw, queue):
    card, cdn = Card(fw), Cdn(fw, {URL: b'fLaC-audio', NEXT: b'fLaC-next'})
    queue.row('99')
    queue.row('123', 'Song', 'Artist', 'Album', '/tmp/tidal/123.jpg')
    covers = []
    fw.mock('toolsThumbSpecCover', lambda src, dst, w, h, *_: covers.append((C.string_at(src), C.string_at(dst), w, h)) or 1)
    ffmpeg = Ffmpeg(fw, card)
    t = Tidal(fw, card)
    t.play(URL, '123')
    assert [tag.value for tag in t.td.tags] == [b'title=Song', b'artist=Artist', b'album=Album']
    t.run()
    name = '123-0 Artist - Song.flac'
    assert card.listing() == [name] and card.files[cached(name)] == b'TAGGED' and ffmpeg.waits == 1
    # the row's cover fitted, square, for the remux as the job starts, before stock removes it with its page
    (src, dst, w, h), = covers
    assert src == b'/tmp/tidal/123.jpg' and w == h > 0
    order = [call for call, _ in fw.calls() if call in ('toolsThumbSpecCover', 'fw_curl_easy_perform', 'fw_fork')]
    assert order == ['toolsThumbSpecCover', 'fw_curl_easy_perform', 'fw_fork']
    assert t.played == [URL]
    t.play(URL, '123')
    assert t.played == [URL, cached(name)]  # found again by its key, whatever the label
    queue.row('124', 'Next', 'Artist', 'Album', '/tmp/tidal/124.jpg')  # the next row, on the fixed strings now
    t.play(NEXT, '124')
    t.run()
    assert covers[1:] == [(b'/tmp/tidal/124.jpg', dst, w, h)] and ffmpeg.waits == 2  # fitted to the same file


def test_a_row_without_a_cover_file_gets_no_picture(fw, queue):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'Song', '', '', 'https://resources.example/cover.jpg')
    Ffmpeg(fw, card)
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    assert not fw.calls('toolsThumbSpecCover') and card.listing() == ['123-0 Song.flac']


@pytest.mark.parametrize('rows', [[], [('5', 'Other')], [('123', None)]])
def test_a_track_no_row_titles_is_named_by_its_key_alone_and_not_remuxed(fw, queue, rows):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    for tidal_id, title in rows:
        queue.row(tidal_id, title)
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    assert [tag.value for tag in t.td.tags] == [b'title=', b'artist=', b'album=']
    assert not fw.calls('fw_fork') and card.listing() == ['123-0.flac'] and card.files[cached('123-0.flac')] == b'fLaC'


@pytest.mark.parametrize('status, out', [(1 << 8, b'partial'), (0, b''), (0, None), (9, b'TAGGED')])
def test_a_failed_remux_keeps_the_copy_as_downloaded_and_nothing_beside_it(fw, queue, status, out):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'Song', 'Artist', 'Album', '')
    Ffmpeg(fw, card, status, out)
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    assert card.listing() == ['123-0 Artist - Song.flac'] and card.files[cached('123-0 Artist - Song.flac')] == b'fLaC'


@pytest.mark.parametrize('errno, tagged', [(EINTR, True), (10, False)])  # ECHILD: no child to wait for
def test_a_signal_while_waiting_for_ffmpeg_waits_again(fw, queue, errno, tagged):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'Song', 'Artist', 'Album', '')
    ffmpeg = Ffmpeg(fw, card)
    error = C.c_int(errno)
    fw.mock('fw___errno_location', lambda *_: C.addressof(error))
    tries = []
    def wait(*a):
        tries.append(a[0])
        return -1 if len(tries) == 1 else ffmpeg.wait(*a)
    fw.mock('fw_waitpid', wait)
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    assert len(tries) == (2 if tagged else 1) and ffmpeg.waits == tagged
    assert card.files[cached('123-0 Artist - Song.flac')] == (b'TAGGED' if tagged else b'fLaC')


# Clear All and the cap.

def test_clear_all_while_a_finished_download_is_tagged_leaves_nothing_behind(fw, queue):
    card = Card(fw, {CACHE_SIZE: b'2048', cached('7-0 Old.flac'): b'old'})
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'Song', 'Artist', 'Album', '')
    Ffmpeg(fw, card, then=fw.fn('tidal_clear', None))
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    assert card.listing() == [] and CACHE_SIZE in card.files  # .cache-size stays


def test_clear_all_mid_transfer_stops_it_and_a_later_play_caches_again(fw):
    card, cdn = Card(fw), Cdn(fw, {URL: b'a' * 4000})
    t = Tidal(fw, card)
    t.play(URL, '1')
    cdn.on_chunk = lambda i: fw.fn('tidal_clear', None)() if i == 0 else None
    t.run()
    assert card.listing() == [] and t.td.busy == 0
    cdn.on_chunk = lambda i: None
    t.play(URL, '1')
    t.run()
    assert card.listing() == ['1-0.flac']


def test_a_finished_download_trims_the_oldest_copies_to_the_cap(fw):
    card = Card(fw, {CACHE_SIZE: b'512', cached('old.flac'): b'', cached('mid.flac'): b'', cached('new.flac'): b''})
    card.sizes.update({cached('old.flac'): 200 * MB, cached('mid.flac'): 200 * MB, cached('new.flac'): 100 * MB})
    card.ages.update({cached('old.flac'): 10, cached('mid.flac'): 20, cached('new.flac'): 30})
    Cdn(fw, {URL: b'a' * 4000})
    t = Tidal(fw, card)
    card.ages[cached('1-0.flac')] = 40
    card.sizes[cached('1-0.flac')] = 100 * MB  # 600 MB with it: over by the oldest's 200
    t.play(URL, '1')
    t.run()
    assert card.listing() == ['1-0.flac', 'mid.flac', 'new.flac']


def trim(fw, card):
    fw.fn('tidal__trim', None)()
    return card.listing()


@pytest.mark.parametrize('setting, cap_mb', [(b'512', 512), (b'1024', 1024), (b'2048', 2048), (b'4096', 4096), (b'8192', 8192),
                                             (b'3000', 2048), (b'', 2048), (None, 2048)])
def test_the_cap_is_streaming_configurations_and_2_gb_otherwise(fw, setting, cap_mb):
    card = Card(fw, {} if setting is None else {CACHE_SIZE: setting})
    parts = max(2, cap_mb // 2048)  # a file's size is a 32-bit word
    names = [f'f{k}' for k in range(parts)]
    for age, name in enumerate(names):
        card.files[cached(name)], card.sizes[cached(name)], card.ages[cached(name)] = b'', cap_mb // parts * MB, age + 1
    assert trim(fw, card) == names  # exactly the cap: kept
    card.files[cached('g')], card.sizes[cached('g')], card.ages[cached('g')] = b'', 1, parts + 1
    assert trim(fw, card) == names[1:] + ['g']  # a byte over: the oldest goes


def test_trim_goes_oldest_first_until_the_rest_fit(fw):
    card = Card(fw, {CACHE_SIZE: b'512'})
    for name, mb, age in ('a', 300, 50), ('b', 150, 10), ('c', 100, 30), ('d', 120, 20):
        card.files[cached(name)], card.sizes[cached(name)], card.ages[cached(name)] = b'', mb * MB, age
    assert trim(fw, card) == ['a', 'c']  # 670 MB: b, then at 520 d


def test_partial_and_dot_files_never_count_nor_go(fw):
    card = Card(fw, {CACHE_SIZE: b'512', cached('a.flac.tmp'): b'', cached('.hidden'): b'', cached('b.flac'): b''})
    card.sizes.update({cached('a.flac.tmp'): 4000 * MB, cached('.hidden'): 4000 * MB, cached('b.flac'): 500 * MB})
    assert trim(fw, card) == ['a.flac.tmp', 'b.flac'] and cached('.hidden') in card.files


def test_a_file_stat_cant_read_is_left_out(fw):
    card = Card(fw, {CACHE_SIZE: b'512', cached('a'): b'', cached('b'): b''})
    card.sizes.update({cached('a'): 400 * MB, cached('b'): 400 * MB})
    card.ages.update({cached('a'): 1, cached('b'): 2})
    stat = card.stat
    fw.mock('fw___xstat', lambda v, path, st, *_: -1 if C.string_at(path).endswith(b'/a') else stat(v, path, st))
    assert trim(fw, card) == ['a', 'b']


def test_a_card_that_refuses_the_delete_ends_the_trim(fw):
    card = Card(fw, {CACHE_SIZE: b'512', cached('a'): b'', cached('b'): b''})
    card.sizes.update({cached('a'): 400 * MB, cached('b'): 400 * MB})
    fw.mock('fw_unlink', lambda *_: -1)
    assert trim(fw, card) == ['a', 'b'] and len(fw.calls('fw_unlink')) == 1  # once: the worker isn't trapped


# The stream threads' gates.

def test_a_track_asked_for_wakes_the_stream_thread_once_after_stock_set_its_flag(fw):
    order = []
    fw.mock('stock_tidaltrack_trampoline', lambda *a: order.append(('track', a[:4])) or 7)
    fw.mock('fw_sem_post', lambda gate, *_: order.append(('post', gate)))
    fw.mock('fw_sem_wait', lambda gate, *_: order.append(('wait', gate)))
    track = fw.fn('ringnav_tidal_track', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)
    assert track(1, 2, 3, 4) == 7
    fw.fn('ringnav_tidal_poll', C.c_int, C.c_uint)(10000)
    fw.fn('ringnav_baidu_poll', C.c_int, C.c_uint)(10000)
    (_, stream), (_, wait), (_, baidu) = order[1:]
    assert order[0] == ('track', (1, 2, 3, 4)) and [k for k, _ in order] == ['track', 'post', 'wait', 'wait']
    assert stream == wait and baidu != stream  # Baidu's gate is never posted


# Track lists, paged whole.

PAGE = '/tmp/tidal_77'
EMPTY = b'{"items":[]}'
KINDS = {'playlist': ('ringnav_tidal_playlist', 'stock_tidalplaylist_trampoline', 'parseTidalPlaylistDetailJson', 'tidal_albuminfo_thread_run'),
         'album': ('ringnav_tidal_album', 'stock_tidalalbum_trampoline', 'parseTidalAlbumDetailJson', 'tidal_albuminfo_thread_run'),
         'favs': ('ringnav_tidal_favs', 'stock_tidalfavs_trampoline', 'parseTidalColTracksJson', 'tidal_collection_thread_run')}


class Lists:
    """Stock's Tidal list thread and its page's parser over a server holding total tracks: the thread writes the
    page at its offset (stTidalTracks', or for favourites its argument), 50 rows, to /tmp/tidal_<its thread id>,
    failing at fail's offsets; the parse appends its rows to the list's deque and writes back total and the reply's
    limit. moved: the page opened again during the request at that offset (init_infodata sets the offset to 0)."""
    def __init__(self, fw, kind, total, limit=50, fail=(), moved=None, have=0):
        self.fw, self.kind, self.total, self.limit, self.fail, self.moved = fw, kind, total, limit, fail, moved
        self.card, self.deques, self.requests = Card(fw), Deques(fw), []
        self.t = fw.var('fw_ctx_stTidalTracks', C.c_int * 4)  # total, limit, offset, the deque
        self.t[0], self.t[1], self.t[2] = (total, 50, have) if have else (0, 50, 0)
        self.t[3] = self.handle = self.deques.new(range(have))
        hook, thread, parser, self.flag = KINDS[kind]
        fw.mock('fw_pthread_self', lambda *_: 77)
        fw.mock(thread, self.thread)
        fw.mock(parser, self.parse)
        self.hook = fw.fn(hook, C.c_int, C.c_void_p)

    def thread(self, arg, *_):
        assert PAGE not in self.card.files  # removed first: a failed request leaves no old page to parse again
        offset = arg if self.kind == 'favs' else self.t[2]
        self.requests.append((arg or 0, self.t[2]))
        if offset == self.moved:
            self.t[2] = 0
        if offset not in self.fail:
            self.card.files[PAGE] = b','.join(b'%d' % i for i in range(offset, min(offset + 50, self.total)))
        return 0

    def parse(self, path, *_):
        data = self.card.files.get(C.string_at(path).decode())
        if data is None:
            return 0
        self.deques[self.handle].extend(int(i) for i in data.split(b',') if data != EMPTY and i)
        self.t[0], self.t[1] = self.total, self.limit
        return 1

    def rows(self):
        return self.deques[self.handle]

    def run(self, arg):
        assert self.hook(arg) == 0
        assert self.fw.var(self.flag, C.c_ubyte).value == 2  # the page may parse


@pytest.mark.parametrize('kind', ['playlist', 'album'])
def test_a_list_is_fetched_page_by_page_until_whole(fw, kind):
    lists = Lists(fw, kind, 120)
    lists.run(0xab)
    assert lists.requests == [(0xab, 0), (0xab, 50), (0xab, 100)] and lists.rows() == list(range(120))
    assert lists.card.files[PAGE] == EMPTY  # the page's own parse adds nothing more
    assert lists.t[2] == 100


def test_my_collection_s_tracks_page_on_from_the_offset_it_was_given(fw):
    lists = Lists(fw, 'favs', 175, have=50)  # the scroll handler asked for the page after the first
    lists.run(50)
    assert lists.requests == [(50, 50), (100, 100), (150, 150)] and lists.rows() == list(range(175))
    assert lists.t[2] == 150  # the last page's: the scroll handler finds none left


def test_a_list_of_one_page_asks_once(fw):
    lists = Lists(fw, 'playlist', 30)
    lists.run(0xab)
    assert lists.requests == [(0xab, 0)] and lists.rows() == list(range(30)) and lists.card.files[PAGE] == EMPTY


def test_a_failed_first_page_is_left_to_the_page_as_stock(fw):
    lists = Lists(fw, 'album', 120, fail=(0,))
    lists.run(0xab)
    assert lists.requests == [(0xab, 0)] and lists.rows() == [] and PAGE not in lists.card.files


def test_a_failed_later_page_ends_the_list_with_what_loaded(fw):
    lists = Lists(fw, 'playlist', 200, fail=(100,))
    lists.run(0xab)
    assert [o for _, o in lists.requests] == [0, 50, 100] and lists.rows() == list(range(100))
    assert lists.card.files[PAGE] == EMPTY


def test_a_page_opened_again_mid_run_ends_it(fw):
    lists = Lists(fw, 'playlist', 200, moved=50)
    lists.run(0xab)
    assert [o for _, o in lists.requests] == [0, 50] and lists.card.files[PAGE] == EMPTY


def test_a_list_stops_at_2000_tracks(fw):
    lists = Lists(fw, 'playlist', 5000)
    lists.run(0xab)
    assert len(lists.requests) == 40 and len(lists.rows()) == 2000 and lists.card.files[PAGE] == EMPTY


def test_a_reply_with_no_limit_ends_the_run_rather_than_ask_its_page_again(fw):
    lists = Lists(fw, 'playlist', 120, limit=0)
    lists.run(0xab)
    assert lists.requests == [(0xab, 0)] and lists.rows() == list(range(50)) and lists.card.files[PAGE] == EMPTY
