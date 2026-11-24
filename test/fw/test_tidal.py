"""tidal.c (docs/internals.md#tidal-cache, #tidal-track-lists) end to end: tidal_play's job downloaded by its
thread to the card through libcurl, tagged by ffmpeg from the queue row, kept or dropped, and the cache trimmed to
Streaming Configuration's cap; Clear All against a running download; the stream threads' gates; and Tidal's track
lists paged whole. tidal_ext, tidal_name, cached and progress alone are test_streaming.py's, whose td mirror this
shares."""
import ctypes as C
import re

import pytest

import host
from conftest import O, Deques, Files, Game, claim, dir_readdir, high_halves, in_fork, libc, stock_stub
from test_radio import Rd, Sp  # test_radio.py keeps the mirror
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
        return dir_readdir(self, d)

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
        self.play_fn = fw.fn('tidal_play', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p)

    def clock(self, which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))  # struct { long s, ns; }
        t[0], t[1] = self.ms // 1000, self.ms % 1000 * 1000000

    def play(self, url, track_id):
        self.play_fn(url.encode(), track_id.encode(), None)  # the thread's reply file: Download All's alone

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


def card_log(fw, card):
    """Each fsync, by its file's name, and each rename, in order; the cache folder opens, for its own."""
    log: list = []
    card.files[TIDAL], rename = b'', card.rename
    fw.mock('fw_fileno', lambda f, *_: f)
    fw.mock('fw_fsync', lambda fd, *_: log.append(('fsync', card.open[fd][0])) or 0)
    fw.mock('fw_rename', lambda old, new, *_: log.append(('rename', C.string_at(old).decode(),
                                                          C.string_at(new).decode())) or rename(old, new))
    return log


@pytest.mark.parametrize('titled', [True, False])
def test_a_kept_copy_is_on_the_card_under_its_name_when_its_job_ends(fw, queue, titled):
    """FAT (Linux's vfat) gives a renamed file's new name its size and first cluster only when the file itself is
    written back, so a power cut up to a minute after the rename left the song on the card at 0 bytes, its data in
    clusters no name pointed at. The download and its remux are each synced before their rename; after the last,
    the folder (its names), then the file (its size)."""
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    if titled:
        queue.row('123', 'Song', 'Artist', 'Album', '')
        Ffmpeg(fw, card)
    log = card_log(fw, card)
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    name = cached('123-0 Artist - Song.flac' if titled else '123-0.flac')
    tmp = name + '.tmp'
    remux = [('fsync', tmp + '.tag.tmp'), ('rename', tmp + '.tag.tmp', tmp)] if titled else []
    assert log == [('fsync', tmp), *remux, ('rename', tmp, name), ('fsync', TIDAL), ('fsync', name)]
    assert card.files[name] == (b'TAGGED' if titled else b'fLaC')


@pytest.mark.parametrize('refused, kept', [('.tag.tmp', b'fLaC'), (TIDAL, b'TAGGED'), ('.flac', b'TAGGED')],
                         ids=['the remux', 'the folder after the rename', 'the copy after the rename'])
def test_a_sync_the_card_refuses(fw, queue, refused, kept):
    """A remux the card won't sync is dropped, the synced download kept as any failed remux leaves it; a sync after
    the last rename that fails keeps the copy, its data synced before."""
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'Song', 'Artist', 'Album', '')
    Ffmpeg(fw, card)
    card_log(fw, card)
    fw.mock('fw_fsync', lambda fd, *_: -1 if card.open[fd][0].endswith(refused) else 0)
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    assert card.listing() == ['123-0 Artist - Song.flac'] and card.files[cached('123-0 Artist - Song.flac')] == kept


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
                ('hearts', C.c_int), ('now', C.c_int)]


def free_stubbed(play):
    """play(), with toolsFreeStSongInfo, called at its stock address (tidal.c's own define), a ret meanwhile: each
    record's strings stay for the asserts; the byte is put back after, as conftest's map outlives a load."""
    free_rec = int(re.findall(r'toolsFreeStSongInfo \(\(void \(\*\)\(void \*\)\)(0x[0-9a-f]+)u\)',
                              (host.ROOT/'patch/tidal.c').read_text())[0], 16)
    claim(free_rec)
    saved = C.string_at(free_rec, 1)
    C.memmove(free_rec, b'\xc3', 1)
    try:
        return play()
    finally:
        C.memmove(free_rec, saved, 1)


def urls_play(fw, files, lines, path='/tmp/q2-a.urls', header=b'#Q2URLS/1'):
    """app_urls_play over a Q2URLS/1 file of lines, put in files (conftest's Files, mocked): its result."""
    files.files[path] = header + b'\n' + b''.join(line + b'\n' for line in lines)
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    return free_stubbed(lambda: fw.fn('app_urls_play', C.c_int, C.c_char_p)(path.encode()))


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


# A song a card app serves itself, on 127.0.0.1 (q2-jellyfin's relay, which adds the token), plays only while the
# app runs: an app that ended (idle, Stop) is started as its page starts it before the URL
# goes to the player, which waits for its socket, AU_WAKE_MS at most. docs/internals.md, Card apps.
LOCAL = b'http://127.0.0.1:2317/song/1?q=hi'
WAKE = [LOCAL + b'\tA', b'https://x/b.flac\tB', b'http://127.0.0.10/c.flac\tC', b'http://127.0.0.1/d.flac\tD',
        b'http://127.0.0.1.evil/e.flac\tE', b'http://127.0.0.1@host/f.flac\tF']


class Wake:
    """The URL queue WAKE of app a, playing at pos, with the app on the card, its loop running or not: the song's
    start through ringnav_tidal_track, then its thread's job run here. tidal_playurl, at its stock address, is a
    stub: call this in_fork."""
    def __init__(self, fw, queue, mcl, running, answers_at=1):
        self.fw, self.tries, self.slept, self.played, self.ran, self.jobs = fw, 0, 0, [], [], []
        self.answers_at, self.ms = answers_at, 100000
        urls_play(fw, Files(fw), WAKE)
        fw.fn('addon__slot', C.c_void_p, C.c_char_p)(b'a')
        present = {b'/mnt/mmc/.a/q2-a', *([b'/tmp/q2-a'] if running else [])}
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p) in present else -1)
        fw.mock('fw_system', lambda cmd, *_: self.ran.append(C.string_at(cmd)))
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('networkGetConnectStatus', lambda *_: 1)
        self.log = (C.c_ulonglong * 3)()
        stock_stub(int(re.findall(r'#define tidal_playurl \(\(.*\)(0x[0-9a-f]+)u\)', (host.ROOT/'patch/tidal.c').read_text())[0], 16),
                   0, self.log)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.jobs.append((fn, arg)) and 0)
        fw.mock('stock_tidal_trampoline', lambda url, *_: self.played.append(C.string_at(url)))
        fw.mock('fw_socket', lambda *_: 5)
        fw.mock('fw_fcntl', lambda *_: 0)
        fw.mock('fw_connect', self.connect)
        fw.mock('fw_clock_gettime', lambda which, ts, *_: C.memset(ts, 0, 16) and 0)  # now_ms reads 0: no wait
        fw.mock('fw_usleep', self.usleep)
        self.later = None  # what happens while the job waits, once
        for k in range(len(WAKE)):
            queue.row(f'app:1:{k}')
        self.mcl = mcl
        mcl.type = 0xf1

    def connect(self, fd, to, n, *_):
        assert C.string_at(to + 2) == b'/tmp/q2-a.browse.sock'
        self.tries += 1
        return 0 if self.tries >= self.answers_at else -1

    def usleep(self, us, *_):
        self.slept += us
        if self.later:
            self.later(), setattr(self, 'later', None)

    def start(self, pos):
        self.mcl.pos = pos
        assert self.fw.fn('ringnav_tidal_track', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(0, 0, 0, 0) == 1
        return self.jobs[-1]

    def run(self, job):
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(job[0])(job[1])


@in_fork
def test_a_song_on_the_app_s_own_server_starts_the_app_and_waits_for_it(fw, queue, mcl):
    w = Wake(fw, queue, mcl, running=False, answers_at=4)
    w.run(w.start(0))
    assert w.ran == [b'/bin/sh /mnt/mmc/.a/run'] and w.tries == 4 and w.slept == 3 * 100000 and w.played == [LOCAL]


@in_fork
def test_a_running_app_s_song_plays_at_once(fw, queue, mcl):
    w = Wake(fw, queue, mcl, running=True)
    w.run(w.start(0))
    assert w.ran == [] and w.tries == 0 and w.slept == 0 and w.played == [LOCAL]


@in_fork
def test_a_song_from_another_server_starts_nothing(fw, queue, mcl):
    w = Wake(fw, queue, mcl, running=False)
    for pos, url in ((1, b'https://x/b.flac'), (2, b'http://127.0.0.10/c.flac'), (4, b'http://127.0.0.1.evil/e.flac'),
                     (5, b'http://127.0.0.1@host/f.flac')):
        w.run(w.start(pos))
        assert w.played[-1] == url
    assert w.ran == [] and w.tries == 0 and w.slept == 0


@in_fork
def test_an_app_that_never_answers_holds_its_song_five_seconds_at_most(fw, queue, mcl):
    w = Wake(fw, queue, mcl, running=False, answers_at=10 ** 6)
    w.run(w.start(3))
    assert w.slept == 5000 * 1000 and w.tries == 50 and w.played == [b'http://127.0.0.1/d.flac']


@in_fork
def test_a_start_meanwhile_ends_the_wait_and_its_song_alone_plays(fw, queue, mcl):
    w = Wake(fw, queue, mcl, running=False, answers_at=10 ** 6)
    first = w.start(0)
    w.later = lambda: w.start(1)  # Next, while the first waits
    w.run(first)
    assert w.slept == 100000 and w.played == []
    w.run(w.jobs[-1])
    assert w.played == [b'https://x/b.flac'] and w.ran == [b'/bin/sh /mnt/mmc/.a/run']


# The URL goes to stock's player past ringnav_start_player, which moves Spotify and a station off the PCM and refuses
# under a card game's sound: one started during the wait keeps it, and the song is dropped.
@pytest.mark.parametrize('what', ['radio', 'game', 'spotify'])
@in_fork
def test_something_else_started_during_the_wait_keeps_the_sound(fw, queue, mcl, what):
    w = Wake(fw, queue, mcl, running=False, answers_at=10 ** 6)
    rd, gm, sp = fw.var('radio__rd', Rd), fw.var('addon__gm', Game), fw.var('spotify__sp', Sp)
    on = {'radio': lambda: setattr(rd, 'active', 1) or setattr(rd, 'pid', 4321),
          'game': lambda: setattr(gm, 'pid', 4321) or setattr(gm, 'audio', 1),
          'spotify': lambda: setattr(sp, 'active', 1) or setattr(sp, 'playing', 1)}
    w.later = on[what]  # a station, a game with audio=1 or Spotify starts while the song waits
    w.run(w.start(0))
    assert w.slept == 100000 and w.played == [] and w.ran == [b'/bin/sh /mnt/mmc/.a/run']


# A song that didn't wait for its app goes to the player as before, whatever else reads as playing: ringnav_start_player
# has just moved it off the PCM.
@in_fork
def test_a_song_that_didn_t_wait_goes_whatever_else_plays(fw, queue, mcl):
    w = Wake(fw, queue, mcl, running=True)
    rd = fw.var('radio__rd', Rd)
    rd.active, rd.pid = 1, 4321
    for pos, url in (0, LOCAL), (1, b'https://x/b.flac'):
        w.run(w.start(pos))
        assert w.played[-1] == url
    assert w.slept == 0 and w.ran == []


@in_fork
def test_a_second_song_while_the_app_starts_waits_without_starting_it_again(fw, queue, mcl):
    w = Wake(fw, queue, mcl, running=False, answers_at=2)
    w.start(0)
    w.ms += 1000
    w.run(w.start(3))  # its loop's pid file not there yet: still starting
    assert w.ran == [b'/bin/sh /mnt/mmc/.a/run'] and w.slept == 100000 and w.played == [b'http://127.0.0.1/d.flac']


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


@pytest.mark.parametrize('header,now', [
    (b'#Q2URLS/1\tnow=1', 1), (b'#Q2URLS/1\tstart=0\tnow=1', 1), (b'#Q2URLS/1\tnow=1\tstart=0', 1), (b'#Q2URLS/1\tnow=1\r', 1),
    (b'#Q2URLS/1', 0), (b'#Q2URLS/1\tnow=0', 0), (b'#Q2URLS/1\tnow=10', 0), (b'#Q2URLS/1\tknow=1', 0), (b'#Q2URLS/1 now=1', 0),
])
def test_a_url_queue_asks_to_be_told_what_plays_in_its_header(fw, header, now):
    """now=1, a header field of its own, opts the queue in to addon.c's .now; a song line's now=1 is no header's."""
    Deques(fw)
    assert urls_play(fw, Files(fw), [b'https://x/a.flac\tA\tnow=1'], header=header) == 1 and fw.var('tidal__au', Au).now == now


def test_the_song_playing_is_told_only_for_the_latest_queue_that_asked(fw, mcl, queue):
    """app_urls_at: the place among the songs kept of the record at MCL_POS, for the latest URL queue (au.gen) whose
    header said now=1; -1 for an older queue's song, a Tidal track, a queue that didn't ask, or no 0xf1 queue."""
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    au = fw.var('tidal__au', Au)
    au.gen, au.n, au.now = 7, 3, 1
    for tidal_id in ('app:7:0', 'app:7:2', 'app:6:1', '12345', 'app:7:3', 'app:7'):
        queue.row(tidal_id)
    at = fw.fn('app_urls_at', C.c_int)
    mcl.type = 0xf1
    seen = []
    for pos in range(7):
        mcl.pos = pos
        seen.append(at())
    assert seen == [0, 2, -1, -1, -1, -1, -1]
    mcl.pos = 1
    au.now = 0
    assert at() == -1
    au.now, mcl.type = 1, 0xf001  # a local queue: its records are never an app's
    assert at() == -1


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


@pytest.mark.parametrize('ends_at, tagged', [(300000 - 20, True), (None, False)])  # TIDAL_TAG_MS
def test_a_hung_remux_is_killed_at_the_limit_and_the_copy_kept_as_downloaded(fw, queue, ends_at, tagged):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'Song', 'Artist', 'Album', '')
    ffmpeg, now, killed = Ffmpeg(fw, card, out=b'TAGGED' if tagged else b'partial'), [0], []
    fw.mock('time_now_ms', lambda *_: now[0])
    fw.mock('sleep_ms', lambda ms, *_: now.__setitem__(0, now[0] + ms))
    fw.mock('chapter_kill', lambda pid, sig, *_: killed.append((pid, sig)))

    def wait(pid, status, options, *_):
        if not killed and (ends_at is None or now[0] < ends_at):
            return 0  # still running (a blocking wait too: answered, so an endless one fails rather than hangs)
        ffmpeg.status = 9 if killed else 0
        return ffmpeg.wait(pid, status, options)
    fw.mock('fw_waitpid', wait)
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    assert killed == ([] if tagged else [(4242, 9)]) and now[0] <= 300000 + 20
    assert card.listing() == ['123-0 Artist - Song.flac']
    assert card.files[cached('123-0 Artist - Song.flac')] == (b'TAGGED' if tagged else b'fLaC')


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


# A card's file system (FAT32, exFAT) refuses a name over 255 characters: the copy's .tmp couldn't be made at all
# for the longer title, and for the shorter its remux's .tmp.tag.tmp would have been over.
@pytest.mark.parametrize('length', [300, 228])
def test_a_long_label_leaves_room_for_the_copy_s_tmp_and_its_remux_on_the_card(fw, queue, length):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'x' * length, 'Artist', 'Album', '')
    Ffmpeg(fw, card)
    fat = card.fopen
    fw.mock('fw_fopen', lambda path, mode, *_: 0 if len(C.string_at(path).rsplit(b'/', 1)[-1]) > 255 else fat(path, mode))
    t = Tidal(fw, card)
    t.play(URL, '123')
    t.run()
    names = card.listing()
    assert len(names) == 1 and len(names[0] + '.tmp.tag.tmp') <= 255 and card.files[cached(names[0])] == b'TAGGED'
    assert names[0].startswith('123-0 Artist - xxx') and names[0].endswith('.flac') and len(names[0]) == 243


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


# Download All.

REPLY = '/tmp/tidalplayurl_77'
ALL_ROW = 'Download Playing Songs'


class All:
    """Download All on the queue playing, a Tidal page's (0xf1): its row's presses (tidal_all), tidal_poll a second
    apart, and stock's stream thread, which answers the song asked for through tidal_play (tidal_start passes its
    first reply's file) and, after a 0, ends as for a song started: its flag cleared."""
    def __init__(self, fw, queue, mcl, card, ids, level=0):
        self.fw, self.card, self.t = fw, card, Tidal(fw, card, level)
        for tidal_id in ids:
            queue.row(tidal_id)
        mcl.type = 0xf1
        fw.var('fw_ctx_tidalStreamingLevelNext', C.c_int).value = level
        self.toasts, self.posts, self.net = [], [], 1
        self.ask_id, self.on_ask = '900', lambda: None  # the song to play stock asks for; a step inside stock's call
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('fw_sem_post', lambda gate, *_: self.posts.append(gate))
        fw.mock('networkGetConnectStatus', lambda *_: self.net)
        fw.mock('time_now_ms', lambda *_: self.t.ms)  # the UI loop's clock, as radio_poll's
        fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))  # AWTK's
        fw.mock('stock_tidaltrack_trampoline', lambda *_: self.stock_asks())
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.flag, self.heart = fw.var('tidalfurlstart', C.c_ubyte), fw.var('isFav_playing', C.c_ubyte)
        self.songid = fw.var('fw_ctx_tidalsongid', C.c_char * 1024)
        self.caption = C.create_string_buffer(64)
        self.all = fw.fn('tidal_all', None, C.c_int, C.c_char_p, C.c_uint)

    def row(self, press=0):
        self.all(press, self.caption, 64)
        return self.caption.value.decode()

    def poll(self, seconds=1):
        for _ in range(seconds):
            self.t.ms += 1000
            self.fw.fn('tidal_poll', None)()

    def asked(self):
        """The song stock's thread has been asked for, as it finds it: its flag up, the id where it copies it from."""
        return self.songid.value.decode() if self.flag.value and self.t.td.asked else None

    def answer(self, url, tidal_id):
        self.card.files[REPLY] = b'{}'
        result = self.t.play_fn(url if url is None else url.encode(), tidal_id.encode(), REPLY.encode())
        if not result:
            self.flag.value = 0  # the thread's own ending
        return result

    def stock_asks(self):
        """get_tidal_trackurl (0x483f5c on): a fetch in flight is dropped, the id set, the flag raised."""
        self.on_ask()
        self.flag.value = 0
        self.songid.value = self.ask_id.encode()
        self.flag.value = 1

    def play_asked(self):
        """A song to play asked for, as mclStartPlayer does."""
        self.fw.fn('ringnav_tidal_track', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(0, 0, 0, 0)

    def busy(self):
        return self.fw.fn('tidal_busy', C.c_int)()


def test_download_all_saves_the_queue_s_tidal_songs_one_after_another_without_playing_them(fw, queue, mcl):
    card, cdn = Card(fw), Cdn(fw, {URL: b'fLaC-1', NEXT: b'fLaC-2'})
    a = All(fw, queue, mcl, card, ['11', 'app:1:0', '', '22'])  # a card app's song and a row with no id aren't Tidal's
    assert a.row() == ALL_ROW and not a.busy()
    assert a.row(press=1) == 'Downloading 1 of 2. Press to stop' and a.busy()  # Smart Wi-Fi keeps the radio on
    a.heart.value = 1  # the playing song's
    a.poll()
    assert a.asked() == '11' and len(a.posts) == 1  # the thread woken once
    assert fw.calls('reset_poweroptions_timer')[-1][1][:3] == (1, 1, 0)  # standby and auto power-off held, not the screen's
    a.poll(5)
    assert len(a.posts) == 1  # the thread has it: nothing more is asked
    a.heart.value = 0  # the thread's favourites check was of song 11
    assert a.answer(URL, '11') == 1 and a.t.played == []  # 1: tidal_start passes the thread's ending over
    assert a.heart.value == 1 and a.flag.value == 0 and REPLY not in card.files  # what that ending would have left
    a.poll()
    assert a.asked() is None  # its download runs: the next waits
    a.t.run()
    assert card.listing() == ['11-0.flac'] and card.files[cached('11-0.flac')] == b'fLaC-1'
    a.poll()
    assert a.asked() == '22' and a.row() == 'Downloading 2 of 2. Press to stop'
    assert a.answer(NEXT, '22') == 1
    a.t.run()
    a.poll()
    assert not a.busy() and a.row() == ALL_ROW and a.toasts == []  # ended: said at the next pass
    a.poll()
    assert a.toasts == ['Downloaded 2 of 2 songs'] and a.t.played == [] and cdn.fetched == [URL, NEXT]
    a.poll(3)
    assert len(a.toasts) == 1 and len(a.posts) == 2


def test_download_all_names_and_tags_a_song_from_its_own_copy_of_the_queue(fw, queue, mcl):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    queue.row('123', 'Song', 'Artist', 'Album', '')
    Ffmpeg(fw, card)
    a = All(fw, queue, mcl, card, [])
    a.row(press=1)
    queue.deques[queue.handle].clear()  # another queue plays meanwhile
    a.poll()
    assert a.answer(URL, '123') == 1
    assert [tag.value for tag in a.t.td.tags] == [b'title=Song', b'artist=Artist', b'album=Album']
    a.t.run()
    assert card.listing() == ['123-0 Artist - Song.flac']


def test_download_all_passes_the_songs_already_on_the_card_at_the_streaming_quality(fw, queue, mcl):
    card = Card(fw, {cached('11-3 A - One.flac'): b'', cached('22-3.m4a'): b'', cached('33-3 C.flac.tmp'): b'', cached('33-1 C.flac'): b''})
    a = All(fw, queue, mcl, card, ['11', '22', '33'], level=5)  # HiRes and above is asked for as 3, as stock's thread does
    a.row(press=1)
    a.poll()
    assert a.asked() == '33' and a.row() == 'Downloading 3 of 3. Press to stop' and a.t.td.saved == 2
    assert not fw.calls('fw_curl_easy_init')


def test_download_all_of_songs_all_on_the_card_ends_at_once(fw, queue, mcl):
    card = Card(fw, {cached('11-0.flac'): b'', cached('22-0 B.flac'): b''})
    a = All(fw, queue, mcl, card, ['11', '22'])
    a.row(press=1)
    a.poll(2)
    assert a.toasts == ['Downloaded 2 of 2 songs'] and a.posts == [] and a.asked() is None


@pytest.mark.parametrize('url', [None, '', 'https://cdn.example/a/noext'])
def test_a_song_tidal_gives_no_url_for_is_passed_and_three_in_a_row_end_it(fw, queue, mcl, url):
    card = Card(fw, {cached('11-0.flac'): b''})
    Cdn(fw, {})
    a = All(fw, queue, mcl, card, ['11', '22', '33', '44', '55'])
    a.row(press=1)
    for tidal_id in '22', '33', '44':
        a.poll()
        assert a.asked() == tidal_id
        assert a.answer(url, tidal_id) == 1 and a.t.played == [] and a.t.threads == []
    a.poll()
    assert not a.busy() and a.asked() is None  # the Wi-Fi or the sign-in gone: 55 isn't asked for
    a.poll()
    assert a.toasts == ['Downloaded 1 of 5 songs']


def test_a_saved_song_between_two_that_werent_keeps_download_all_going(fw, queue, mcl):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ['1', '2', '3', '4', '5', '6'])
    a.row(press=1)
    for tidal_id, url in ('1', ''), ('2', ''), ('3', URL), ('4', ''), ('5', ''):
        a.poll()
        assert a.asked() == tidal_id
        a.answer(url, tidal_id)
        if url:
            a.t.run()
    a.poll()
    assert a.asked() == '6'


def test_a_transfer_that_fails_is_a_song_passed(fw, queue, mcl):
    card = Card(fw)
    Cdn(fw, {URL: b'<html>', NEXT: b'fLaC'}, code=403)
    a = All(fw, queue, mcl, card, ['1', '2'])
    a.row(press=1)
    a.poll()
    a.answer(URL, '1')
    a.t.run()
    a.poll()
    assert a.asked() == '2' and a.t.td.saved == 0 and a.t.td.misses == 1 and card.listing() == []


def test_a_song_to_play_comes_before_download_all_s_and_its_dropped_answer_starts_nothing(fw, queue, mcl):
    """A song to play is asked for while the thread fetches Download All's: stock drops that fetch, whose answer,
    with a URL or without, starts nothing and leaves the flag stock raised for the song to play; that song then
    starts as ever, and Download All's is asked for again after."""
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC', NEXT: b'fLaC-played'})
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll()
    assert a.asked() == '11'
    a.play_asked()
    assert a.songid.value == b'900' and a.flag.value == 1 and len(a.posts) == 2
    a.poll()
    assert a.songid.value == b'900'  # nothing asked over the song to play
    assert a.answer(URL, '11') == 1 and a.t.played == [] and a.t.threads == [] and a.flag.value == 1
    assert a.answer(NEXT, '900') == 0 and a.t.played == [NEXT]
    a.t.run()  # the played song's own copy
    a.poll()
    assert a.asked() == '11' and a.t.td.misses == 0
    assert a.answer(URL, '11') == 1
    a.t.run()
    assert card.listing() == ['11-0.flac', '900-0.flac']


def test_an_ask_the_thread_never_took_is_over_at_the_played_song_s_answer(fw, queue, mcl):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC', NEXT: b'fLaC-played'})
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll()
    a.play_asked()  # before the thread woke: it copies the song to play's id, never 11
    assert a.answer(NEXT, '900') == 0 and a.t.played == [NEXT] and a.t.td.asked == b''
    a.t.run()
    a.poll()
    assert a.asked() == '11'


def test_the_song_download_all_asked_for_played_meanwhile_plays(fw, queue, mcl):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll()
    a.ask_id = '11'
    a.play_asked()
    assert a.answer(URL, '11') == 1 and a.t.played == []  # the dropped fetch's
    assert a.answer(URL, '11') == 0 and a.t.played == [URL]  # the one stock asked for
    a.t.run()
    a.poll(2)
    assert a.toasts == ['Downloaded 1 of 1 song']


def test_nothing_is_asked_while_stock_sets_up_a_song_to_play(fw, queue, mcl):
    card = Card(fw)
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    polled = []
    def step():  # tidal_poll's pass falls inside stock's call
        a.poll()
        polled.append((a.t.td.asked, a.t.td.starting))
    a.on_ask = step
    a.play_asked()
    assert polled == [(b'', 1)] and a.t.td.starting == 0 and len(a.posts) == 1  # the one for the song to play
    a.poll()
    assert a.t.td.asked == b''  # that song's flag is still up
    a.flag.value = 0  # the thread took it
    a.poll()
    assert a.asked() == '11'


def test_a_card_app_s_song_starting_drops_download_all_s_fetch_too(fw, queue, mcl):
    """A card app's song clears Download All's flag as a stock song's does, so its fetch's answer is dropped
    (curl_stop_httpget, at its stock address, tidal.c's own define, a ret meanwhile should it be called: the byte put
    back after, as conftest's map outlives a load)."""
    card = Card(fw)
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll()
    queue.deques[queue.handle][:] = []
    queue.row('app:1:0')
    mcl.pos, a.net = 0, 0  # offline: app_track gives up once the fetch is dropped
    stop = int(re.findall(r'curl_stop_httpget \(\(int \(\*\)\(void\)\)(0x[0-9a-f]+)u\)', (host.ROOT/'patch/tidal.c').read_text())[0], 16)
    claim(stop)
    saved = C.string_at(stop, 1)
    C.memmove(stop, b'\xc3', 1)
    try:
        a.play_asked()
    finally:
        C.memmove(stop, saved, 1)
    assert a.flag.value == 0 and a.t.td.dropped == 1 and a.t.td.starting == 0
    assert a.answer('', '11') == 1 and a.t.td.asked == b'' and a.t.td.tried == 0  # its answer: not a song passed
    a.net = 1
    a.poll()
    assert a.asked() == '11'


def test_a_played_song_s_download_and_download_all_s_take_turns(fw, queue, mcl):
    """One download at a time: while a played song's runs Download All asks for nothing, and an answer that finds one
    running is asked for again; a song played while Download All's transfer runs waits as the thread's next job."""
    card, cdn = Card(fw), Cdn(fw, {URL: b'fLaC-all', NEXT: b'fLaC-played'})
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll()
    a.t.play(NEXT, '900')  # a song starts, the thread's answer for 11 still to come
    assert a.answer(URL, '11') == 1 and len(a.t.threads) == 1 and a.t.td.tried == 0
    a.poll()
    assert a.asked() is None  # busy
    a.t.run()
    a.poll()
    assert a.asked() == '11'
    assert a.answer(URL, '11') == 1 and len(a.t.threads) == 2
    cdn.on_chunk = lambda i: a.t.play(NEXT.replace('/b/', '/c/'), '901') if cdn.fetched[-1] == URL and i == 0 else None
    cdn.bodies[NEXT.replace('/b/', '/c/')] = b'fLaC-next'
    a.t.status = STOPPED  # and the player stopped a while: neither ends Download All's transfer
    a.t.ms += 10000
    a.t.run()
    assert card.listing() == ['11-0.flac', '900-0.flac', '901-0.flac'] and len(a.t.threads) == 2


def test_a_second_press_ends_download_all_and_its_transfer(fw, queue, mcl):
    card, cdn = Card(fw), Cdn(fw, {URL: b'a' * 4000})
    a = All(fw, queue, mcl, card, ['11', '22'])
    a.row(press=1)
    a.poll()
    a.answer(URL, '11')
    cdn.on_chunk = lambda i: a.row(press=1) if i == 0 else None
    a.t.run()
    assert card.listing() == [] and not a.busy() and a.row() == ALL_ROW
    a.poll()
    assert a.toasts == ['Downloaded 0 of 2 songs'] and a.asked() is None


def test_an_answer_after_download_all_ended_starts_nothing_and_a_new_one_goes_on(fw, queue, mcl):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC', NEXT: b'fLaC-2'})
    a = All(fw, queue, mcl, card, ['11', '22'])
    a.row(press=1)
    a.poll()
    a.row(press=1)  # ended, the thread still fetching 11
    assert a.row(press=1) == 'Downloading 1 of 2. Press to stop'  # and started again
    a.poll()
    assert len(a.posts) == 1  # the answer still to come: asked once
    assert a.answer(URL, '11') == 1 and a.t.played == []  # the new one's first song as well: saved
    a.t.run()
    a.poll()
    assert a.asked() == '22' and card.listing() == ['11-0.flac']


def test_stop_streaming_services_ends_download_all(fw, queue, mcl):
    card = Card(fw)
    a = All(fw, queue, mcl, card, ['11'])
    stop = fw.fn('tidal_all_stop', None)
    stop()
    a.poll(2)
    assert a.toasts == []  # none running: nothing to say
    a.row(press=1)
    stop()
    assert not a.busy()
    a.poll()
    assert a.toasts == ['Downloaded 0 of 1 song']


def test_clear_all_downloads_ends_download_all_and_says_nothing(fw, queue, mcl):
    card = Card(fw, {cached('11-0.flac'): b''})
    a = All(fw, queue, mcl, card, ['11', '22'])
    a.row(press=1)
    a.poll()
    fw.fn('tidal_clear', None)()
    assert not a.busy() and a.row() == ALL_ROW and card.listing() == []
    a.poll(3)
    assert a.toasts == [] and len(a.posts) == 1


def test_download_all_ends_when_nothing_moves_for_two_minutes(fw, queue, mcl):
    card = Card(fw)
    a = All(fw, queue, mcl, card, ['11'])
    a.net = 0  # no Wi-Fi: nothing is asked
    a.row(press=1)
    a.poll(120)
    assert a.busy() and a.posts == []
    a.poll(2)
    assert not a.busy() and a.toasts == ['Downloaded 0 of 1 song']


def test_download_all_ends_on_a_full_card(fw, queue, mcl):
    card = Card(fw, free_mb=8)
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll(2)
    assert a.toasts == ['Downloaded 0 of 1 song'] and a.posts == []


def test_download_all_on_a_card_without_the_cache_folder_makes_it_and_goes_on(fw, queue, mcl):
    card, made = Card(fw), []  # a new or reformatted card: no .tidal, whose statfs fails until it is made
    fw.mock('fw_mkdir', lambda path, mode, *_: made.append(C.string_at(path).decode()) or 0)
    fw.mock('fw_statfs', lambda path, buf, *_: card.statfs(path, buf) if TIDAL in made else -1)
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll()
    assert made == [TIDAL] and a.asked() == '11'
    a.poll()
    assert a.busy() and a.toasts == []


def test_the_count_is_told_once_the_screen_is_on(fw, queue, mcl):
    card = Card(fw, {cached('11-0.flac'): b''})
    a = All(fw, queue, mcl, card, ['11'])
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    a.row(press=1)
    a.poll(30)
    assert a.toasts == [] and not a.busy()
    assert fw.calls('reset_poweroptions_timer') == fw.calls('reset_poweroptions_timer')[:1]  # nothing held once it ended
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    a.poll()
    assert a.toasts == ['Downloaded 1 of 1 song']


@pytest.mark.parametrize('kind, ids', [(0xf1, ['app:1:0', '']), (0xf1, []), (0xf001, ['11']), (1, ['11'])])
def test_download_all_needs_a_tidal_song_in_the_queue_playing(fw, queue, mcl, kind, ids):
    card = Card(fw)
    a = All(fw, queue, mcl, card, ids)
    mcl.type = kind
    assert a.row(press=1) == ALL_ROW and a.toasts == ['Play a Tidal album or playlist first'] and not a.busy()
    a.poll(2)
    assert a.posts == [] and len(a.toasts) == 1


def test_tidal_poll_does_nothing_until_download_all_runs(fw, queue, mcl):
    card = Card(fw)
    a = All(fw, queue, mcl, card, ['11'])
    a.poll(5)
    assert not fw.calls('reset_poweroptions_timer') and not fw.calls('fw_opendir') and a.posts == [] and a.toasts == []
    assert not fw.calls('time_now_ms')  # every UI loop pass: not even the clock is read


def test_tidal_poll_steps_once_a_second(fw, queue, mcl):
    card = Card(fw)
    a = All(fw, queue, mcl, card, ['11'])
    a.net = 0
    a.row(press=1)
    poll = fw.fn('tidal_poll', None)
    for _ in range(10):  # every UI loop pass
        a.t.ms += 100
        poll()
    assert a.t.td.idle == 1
    a.t.ms += 900
    poll()
    assert a.t.td.idle == 2



def usb_storage(fw, a, stock):
    """ringnav_usbmode, a computer's cable: stock's page (stock) runs once the card's writers are stopped."""
    fw.mock('stock_usbmode_trampoline', lambda *_: stock.append((a.t.td.all, a.t.td.busy, a.t.ms)) or 3)
    return fw.fn('ringnav_usbmode', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(None, None, None, None)


def test_usb_storage_drops_download_all_s_waiting_song_and_waits_for_its_worker(fw, queue, mcl):
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ['11', '22'])
    a.row(press=1)
    a.poll()
    a.answer(URL, '11')  # its worker not yet at the job
    sleeps, stock = [], []
    fw.mock('sleep_ms', lambda *_: sleeps.append(a.t.run()) and 0)  # the worker runs meanwhile
    assert usb_storage(fw, a, stock) == 3
    assert stock == [(None, 0, a.t.ms)] and len(sleeps) == 1 and card.listing() == [] and not fw.calls('fw_curl_easy_init')
    a.poll()
    assert a.toasts == ['Downloaded 0 of 2 songs'] and a.asked() is None


@pytest.mark.parametrize('all_run', [True, False])
def test_usb_storage_ends_a_running_transfer_and_waits_three_seconds_at_most(fw, queue, mcl, all_run):
    """Download All's transfer or a played song's: either ends, its .tmp gone, though the worker outlasts the wait."""
    card, cdn = Card(fw), Cdn(fw, {URL: b'a' * 4000})
    a = All(fw, queue, mcl, card, ['11'])
    if all_run:
        a.row(press=1)
        a.poll()
        a.answer(URL, '11')
    else:
        a.t.play(URL, '11')
    sleeps, stock, start = [], [], a.t.ms
    def sleep(ms, *_):
        sleeps.append(ms)
        a.t.ms += ms
    fw.mock('sleep_ms', sleep)
    cdn.on_chunk = lambda i: usb_storage(fw, a, stock) if i == 0 else None
    a.t.run()
    assert stock == [(None, 1, start + 3000)] and sum(sleeps) == 3000  # the transfer still inside its chunk: 3 s
    assert card.listing() == [] and a.t.td.busy == 0 and a.t.played == ([] if all_run else [URL])


def test_download_all_never_evicts_its_own_songs_it_ends_with_the_cache_full(fw, queue, mcl):
    """A cap of two songs and a little: an older file still goes first, then the run ends rather than delete its first
    song, over the cap by the one song it just saved, and its count is true."""
    card = Card(fw, {CACHE_SIZE: b'512', cached('old.flac'): b''})
    card.sizes[cached('old.flac')], card.ages[cached('old.flac')] = 200 * MB, 10
    for k, tidal_id in enumerate(['11', '22', '33']):
        card.sizes[cached(f'{tidal_id}-0.flac')], card.ages[cached(f'{tidal_id}-0.flac')] = 200 * MB, 100 + k
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ['11', '22', '33', '44'])
    a.row(press=1)
    for tidal_id in '11', '22', '33':
        a.poll()
        assert a.asked() == tidal_id
        a.answer(URL, tidal_id)
        a.t.run()
    assert card.listing() == ['11-0.flac', '22-0.flac', '33-0.flac'] and a.t.td.full == 1
    a.poll(2)
    assert a.toasts == ['Tidal Cache full: 3 of 4 songs saved'] and len(a.posts) == 3  # 44 never asked for
    a.row(press=1)
    assert a.t.td.full == 0 and a.t.td.since == 0  # a new run starts afresh



def test_download_all_keeps_the_songs_it_passes_as_saved_and_counts_them_true(fw, queue, mcl):
    """Songs already on the card are the run's own too: an older file goes, then the run ends rather than delete
    one it passed, and its count is what the card holds."""
    card = Card(fw, {CACHE_SIZE: b'512', cached('old.flac'): b'', cached('11-0.flac'): b'', cached('22-0.flac'): b''})
    card.sizes[cached('old.flac')], card.ages[cached('old.flac')] = 100 * MB, 10
    for tidal_id, age in ('11', 50), ('22', 51), ('33', 100):
        card.sizes[cached(f'{tidal_id}-0.flac')], card.ages[cached(f'{tidal_id}-0.flac')] = 200 * MB, age
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ['11', '22', '33', '44'])
    a.row(press=1)
    a.poll()
    assert a.asked() == '33'  # 11 and 22 passed
    a.answer(URL, '33')
    a.t.run()
    assert card.listing() == ['11-0.flac', '22-0.flac', '33-0.flac'] and a.t.td.full == 1
    a.poll(2)
    assert a.toasts == ['Tidal Cache full: 3 of 4 songs saved']


def test_a_run_whose_last_song_fills_the_cache_says_downloaded(fw, queue, mcl):
    """It reached the end of its list, so it wasn't stopped: no "Tidal Cache full"."""
    card = Card(fw, {CACHE_SIZE: b'512'})
    for tidal_id, age in ('11', 100), ('22', 101):
        card.sizes[cached(f'{tidal_id}-0.flac')], card.ages[cached(f'{tidal_id}-0.flac')] = 300 * MB, age
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ['11', '22'])
    a.row(press=1)
    for tidal_id in '11', '22':
        a.poll()
        assert a.asked() == tidal_id
        a.answer(URL, tidal_id)
        a.t.run()
    assert card.listing() == ['11-0.flac', '22-0.flac'] and a.t.td.full == 1
    a.poll(2)
    assert a.toasts == ['Downloaded 2 of 2 songs']


def test_a_run_the_cache_fills_passes_the_rest_already_saved_and_says_downloaded(fw, queue, mcl):
    """Full once 2 is saved, but 3-11 are on the card already: no song needs room, so the run passes them all."""
    ids = [str(k) for k in range(1, 12)]
    card = Card(fw, {CACHE_SIZE: b'512', **{cached(f'{i}-0.flac'): b'' for i in ids if i != '2'}})
    for k, i in enumerate(ids):
        card.sizes[cached(f'{i}-0.flac')], card.ages[cached(f'{i}-0.flac')] = 50 * MB, 100 if i == '2' else 50 + k
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ids)
    a.row(press=1)
    a.poll()
    assert a.asked() == '2'
    a.answer(URL, '2')
    a.t.run()
    assert a.t.td.full == 1 and len(card.listing()) == 11
    a.poll(3)  # 2-9, then 10-11 and the end, then the toast
    assert a.toasts == ['Downloaded 11 of 11 songs']


def test_tidal_poll_passes_at_most_8_saved_songs_a_second(fw, queue, mcl):
    ids = [str(k) for k in range(1, 22)]
    card = Card(fw, {cached(f'{tidal_id}-0.flac'): b'' for tidal_id in ids[:20]})
    a = All(fw, queue, mcl, card, ids)
    a.row(press=1)
    for at in 8, 16:
        a.poll()
        assert a.t.td.at == at and a.t.td.saved == at and a.asked() is None and a.posts == []
    a.poll()
    assert a.asked() == '21' and a.t.td.saved == 20 and len(a.posts) == 1


def test_an_offline_press_leaves_download_all_s_dropped_fetch_nothing_to_start(fw, queue, mcl):
    """Offline, stock's get_tidal_trackurl gives up before it sets the id or the flag (0x483f54): the flag Download
    All raised comes down at the press, so the dropped fetch's thread doesn't fetch that song again to play it."""
    card = Card(fw)
    Cdn(fw, {URL: b'fLaC'})
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.poll()
    assert a.asked() == '11'
    a.net = 0
    fw.mock('stock_tidaltrack_trampoline', lambda *_: 0)
    a.play_asked()
    assert a.flag.value == 0 and a.t.td.dropped == 1
    assert a.answer(URL, '11') == 1 and a.flag.value == 0 and a.t.played == [] and a.t.threads == []


def test_a_card_app_s_song_keeps_its_heart_after_download_all_s_dropped_answer(fw, queue, mcl):
    card = Card(fw)
    a = All(fw, queue, mcl, card, ['11'])
    a.row(press=1)
    a.heart.value = 0
    a.poll()
    urls_play(fw, card, HEARTS)
    queue.deques[queue.handle][:] = []
    queue.row('app:1:0')  # a favourite
    mcl.pos, a.net = 0, 0
    a.play_asked()
    assert a.heart.value == 1
    a.answer('', '11')
    assert a.heart.value == 1

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
