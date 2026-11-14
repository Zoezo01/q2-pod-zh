"""What the network answers, fuzzed (fuzz.py; test/score.py's R2): radio-browser's replies to the directory's fetch
(radio.c). They go in the way the fetch hands them over, and what parses them reads and fills heap blocks of the
exact size."""
import ctypes as C

import fuzz
from conftest import block
from test_radio import CURLE_WRITE_ERROR, CURLINFO_RESPONSE_CODE, CURLOPT_WRITEFUNCTION, FETCH_MAX, STATIONS, List, fx, m3u, pls

RADIO_MAX = 200  # the stations, countries or genres a list holds
CURL_MAX_WRITE_SIZE = 16384  # curl.h: the most its write callback is handed at once


def directory(n):
    """radio-browser's .m3u of n stations, A-Z."""
    return b'#EXTM3U\n' + b''.join(b'#RADIOBROWSERUUID:9617a958-0601-11e8-ae97-52543be%05d\n#EXTINF:1,Station %03d\n'
                                   b'http://s%d.example/live.mp3\n' % (i, i, i) for i in range(n))


def countries(n):
    """Its CSV of n countries, A-Z."""
    return b'name,iso_3166_1,stationcount\n' + b''.join(b'Country %03d,C%d,%d\n' % (i, i, 9000 - i) for i in range(n))


RADIO_SEEDS = [
    directory(3),
    m3u(('Radio Paradise', 'http://stream.radioparadise.com/mp3-128'), ('KEXP', 'https://kexp.example/kexp128.mp3')).replace(b'\n', b'\r\n'),
    pls(('Groove Salad', 'http://ice1.somafm.com/groovesalad-128-mp3'), ('FIP', 'http://icecast.radiofrance.fr/fip-midfi.mp3')),
    b'name,iso_3166_1,stationcount\n"Korea, Republic of",KR,10\nThe Netherlands,NL,90\nGermany,DE,99\n',
    b'name,stationcount\nrock,900\nhip hop,500\n"pop, rock",7\n',
    # a list's 200 rows to the row, and two more: its table has one spare, to tell a cut list by
    directory(RADIO_MAX), directory(RADIO_MAX + 2), countries(RADIO_MAX), countries(RADIO_MAX + 2),
    m3u(('a' * 63, 'http://a/'), ('b' * 64, 'http://b/'), ('c' * 200, 'http://c/')),  # A-Z's keys are 64 bytes
    directory(3) + b'#' * (FETCH_MAX - 1 - len(directory(3))),  # the most a fetch keeps, to the byte
]


def held(got, kept, stations):
    """A list's rows as (name, url) offsets: no more than a list holds, each string inside the kept bytes, ended there
    and not empty, and a station's URL one (radio.c: "A line without "://" is no station")."""
    assert 0 <= got.n <= RADIO_MAX and got.truncated in (0, 1)
    text = C.string_at(got.buf, kept + 1)
    names, urls = got.name[:got.n], got.url[:got.n]
    # a row a line, in order: a row past the table writes url[0] (name[201]) and padding ASan can't see (url[201])
    assert urls == sorted(set(urls))
    assert all(at < kept for at in names + urls)
    strings = [text[at:text.index(0, at)] for at in names + urls]
    assert all(strings) and (not stations or all(b'://' in s for s in strings[got.n:]))
    return sorted(zip(names, urls))


def test_fuzz_radio_response(fw):
    """The directory's answer, whatever was asked: the worker's write callback is handed it in curl's pieces, each a
    block of its bytes alone, and what the fetch kept is made a list each way the tick makes one (stations, countries
    by code, genres) and put A-Z, in a list of its own block: the page's are in a static, which ASan doesn't fence."""
    opts: dict = {}
    free = fw.fn('free', None, C.c_void_p)

    def perform(c, *_):
        write = C.CFUNCTYPE(C.c_uint, C.c_void_p, C.c_uint, C.c_uint, C.c_void_p)(opts[CURLOPT_WRITEFUNCTION])
        for at in range(0, len(data), CURL_MAX_WRITE_SIZE):
            piece = data[at:at + CURL_MAX_WRITE_SIZE]
            p = block(fw, piece, len(piece))
            took = write(p, 1, len(piece), None)
            free(p)
            if took != len(piece):
                return CURLE_WRITE_ERROR
        return 0

    def code(c, info, out, *_):
        C.c_long.from_address(out).value = 200
    fw.mock('get_wifisignal', lambda *_: 1)
    fw.mock('fw_curl_easy_init', lambda *_: 1)
    fw.mock('fw_curl_easy_setopt', lambda c, opt, value, *_: opts.__setitem__(opt, value))
    fw.mock('fw_curl_easy_perform', perform)
    fw.mock('fw_curl_easy_getinfo', code)
    fetch = fw.fn('radio__fetch', None, C.c_char_p, C.c_int, C.c_int)
    worker = fw.fn('radio__fetch_worker', C.c_void_p, C.c_void_p)
    list_set = fw.fn('radio__list_set', None, C.c_void_p, C.c_void_p, C.c_uint, C.c_int, C.c_int)
    list_sort = fw.fn('radio__list_sort', None, C.c_void_p)
    lists = [(block(fw, size=C.sizeof(List)), pick, coded) for pick, coded in ((0, 0), (1, 1), (1, 0))]
    f = fx(fw)
    for data in fuzz.cases('radio_response', RADIO_SEEDS):
        fetch(b'/m3u/stations/topvote/100?hidebroken=true', STATIONS, 0)  # frees the last fetch's body first
        worker(None)
        assert f.done == 1 and f.n <= len(data) and (f.code == 200) == (f.n == len(data))  # whole, or failed
        if f.code != 200 or not f.n:
            continue  # the tick's "Directory unavailable": it parses nothing
        for p, pick, coded in lists:
            list_set(p, f.body, f.n, pick, coded)
            got = List.from_address(p)
            rows = held(got, f.n, not pick)
            list_sort(p)
            assert sorted(zip(got.name[:got.n], got.url[:got.n])) == rows  # every row, whatever its order
    fw.fn('radio__fetch_cancel', None)()
    for p, _, _ in lists:
        fw.fn('radio__list_free', None, C.c_void_p)(p)
        free(p)

