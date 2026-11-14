"""Seeded malformed input (fuzz.py) into the parsers of the card's files and of flash's duration cache, for
AddressSanitizer and UBSan to watch (test/score.py's R2): each parser comes back, and what it returns holds the
limits its own comments give."""
import collections
import ctypes as C
import struct

import fuzz
from conftest import Files, block
from test_durations import Ds  # each mirror stays in its file's tests
from test_lyrics import FAR, TAG_MAX, Line, Mark
from test_nav_library_pages import LENGTHS, lengths, path_key, song  # noqa: F401
from test_nav_loop import RC_FILE, RC_HEADER, Rc

# lrc_space's spaces: none is left at a line's end
SPACES = (b' ', b'\t', b'\v', b'\f', b'\xc2\xa0', b'\xe3\x80\x80', b'\xe2\x80\x8b', b'\xef\xbb\xbf')
LRC = [  # tags, stamps to the hundredth with either separator, a line with two, each line ending; A2's word stamps; plain
    b'[ar:Artist]\n[ti:Song]\n[00:01.00]one\n[00:02.50] two \n[01:02:50]three\n[00:04]four\n',
    b'[00:03.00][00:01.00]chorus\r\n[00:02.123]verse\r\r\n[9999:59.999]\xe3\x80\x80end\xc2\xa0',
    b'[00:01.00]<00:01.00>one <00:01.50>two <00:02.00>three\n[00:03.00]  <00:03.00>four<00:04.00>  \n',
    b'plain\rtext \xe2\x80\x8b\n\xef\xbb\xbf\n\t<00:01.00>no stamp < 1\n',
    b'x' * 65500 + b'\n' + b'[00:01.00]' * 8 + b'word\n',  # a stamped line from inside the tag limit to past it
]
TSV = [  # the header alone; songs, one with a CUE start; albums by name and by folder; a copy over rc_read's 1024 bytes
    RC_HEADER,
    RC_HEADER + b'song\t3\t/mnt/mmc/a.flac\t\nsong\t5\t/mnt/mmc/b.cue\t1200\n',
    RC_HEADER + b'album\t5\tThe Arrival\tHypocrisy\t\nalbum\t2\t\t\t/mnt/mmc/Mixes\n',
    RC_HEADER + b''.join(b'song\t%d\t/mnt/mmc/Music/%02d.flac\t\n' % (k % 4 + 1, k) for k in range(40)),
]
SONGS = (b'/mnt/mmc/a.flac', b'/mnt/mmc/b/c.mp3', b'/mnt/mmc/d.opus')
CACHE = [  # no song yet; one; three, one tried with no length; the longest length
    lengths(), lengths((SONGS[0], 225)), lengths(*zip(SONGS, (225, 3601, 0))), lengths((SONGS[1], 1), (SONGS[2], 0x7fffffff)),
]


# The quickest first: the file's first test is timed with the library's build, and each has ten seconds.
def test_fuzz_ratings_tsv(fw):
    """rc_read over the card's .ratings.tsv, against a copy of exactly the pass's bytes: its lines counted, and
    the same only when it is that copy, byte for byte."""
    files, rc = Files(fw), fw.var('ratings__rc', Rc)
    read = fw.fn('ratings__rc_read', C.c_uint, C.c_char_p, C.POINTER(C.c_int))
    copies = [block(fw, seed, len(seed)) for seed in TSV]
    for i, data in enumerate(fuzz.cases('ratings_tsv', TSV)):
        copy = TSV[i % len(TSV)]  # the valid one this case was made from: alike up to where it was changed
        files.files[RC_FILE] = data
        rc.out, rc.len, same = copies[i % len(TSV)], len(copy), C.c_int(-1)
        assert read(RC_FILE.encode(), C.byref(same)) == data.count(b'\n')
        assert same.value == (data == copy) and not files.open


def test_fuzz_durations_cache(fw, song):
    """duration_read over the cache file, then a lookup of each song: a table only when the file is the magic
    and whole records ascending by key, none with a length below 0; anything else is no cache, every song unknown."""
    files, ds = Files(fw), fw.var('durations__ds', Ds)
    seconds, free = fw.fn('duration_seconds', C.c_int, C.c_void_p), fw.fn('free', None, C.c_void_p)
    records = [(path_key(path), song(path)) for path in SONGS]
    for data in fuzz.cases('durations_cache', CACHE):
        files.files[LENGTHS] = data
        table = list(struct.iter_unpack('<Q3Ii', data[8:])) if len(data) >= 8 and len(data) % 24 == 8 else None
        keys = [row[0] for row in table or ()]
        if data[:8] != b'Q2DUR2\0\0' or keys != sorted(set(keys)) or any(row[4] < 0 for row in table or ()):
            table = None
        known = {row[0]: row[4] for row in table or ()}
        assert [seconds(record) for _, record in records] == [known.get(key, -1) for key, _ in records]
        assert (bool(ds.blob), ds.n) == (table is not None, len(known)) and not files.open
        free(ds.blob)
        ds.blob, ds.n, ds.writable = None, 0, 0


def test_fuzz_lrc(fw):
    """lrc_parse over a .lrc's or a lyrics tag's bytes: the lines and word stamps it gives are inside the text it
    was given, cut as its comment says, and no more of them than the lines, stamps and '<'s it read."""
    parse = fw.fn('lyrics__lrc_parse', C.c_int, C.c_void_p, C.POINTER(C.c_void_p), C.POINTER(C.c_void_p))
    free = fw.fn('free', None, C.c_void_p)
    for data in fuzz.cases('lrc', LRC):
        buf, lp, mp = block(fw, data), C.c_void_p(), C.c_void_p()
        n = parse(buf, C.byref(lp), C.byref(mp))
        read = data.split(b'\0')[0][:TAG_MAX]  # the text ends at a NUL, and lines start in its first TAG_MAX bytes
        room = read.count(b'<')
        assert 0 <= n <= 1 + sum(read.count(c) for c in (b'\n', b'\r', b'['))
        cut = C.string_at(buf, len(data) + 1)
        lines = list(struct.iter_unpack('iiPII', C.string_at(lp, n * C.sizeof(Line))))
        marks = list(struct.iter_unpack('iI', C.string_at(mp, room * C.sizeof(Mark)))) if room else []
        shown = collections.Counter(line[2] for line in lines)
        synced = bool(lines) and lines[0][0] != FAR
        for i, (time, at, text, mark, count) in enumerate(lines):
            assert buf <= text < buf + len(data)
            words = cut[text - buf:cut.index(b'\0', text - buf)]
            assert words and b'\n' not in words and b'\r' not in words
            assert not words.startswith(SPACES) and not words.endswith(SPACES)
            assert 0 <= time < FAR if synced else (time, at, count) == (FAR, i, 0)
            assert mark + count <= room and not (count and shown[text] > 1)  # a line shown twice has none
            offsets = [marks[k][1] for k in range(mark, mark + count)]
            assert offsets == sorted(offsets) and all(k <= len(words) for k in offsets)
            assert all(0 <= marks[k][0] < FAR for k in range(mark, mark + count))
        assert not synced or [line[:2] for line in lines] == sorted({line[:2] for line in lines})  # by time, then the file's order
        for p in (buf, lp, mp):
            free(p)
