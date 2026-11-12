"""patch/onlinematch.c: URLs built from a song's tags, LRCLIB's lyrics and their credits, accent folding,
Download Album Lyrics' job, and the cover searches' replies."""
import ctypes as C
import json
import random
import struct
import unicodedata
import urllib.parse

import pytest

from conftest import O, Deques, Files, block, high_halves, libc

LRC_CANCELLED, LRC_CARD, LRC_OFFLINE, LRC_BUSY, LRC_NONE, LRC_SAVED = -4, -3, -2, -1, 0, 1
LJ_DONE, LJ_OFFLINE, LJ_CARD, LJ_CANCELLED = range(4)
LRCLIB_MAX, COVER_MAX = 512 << 10, 256 << 10
LRCLIB = 'https://lrclib.net/api/search?track_name='
MUSICBRAINZ = 'https://musicbrainz.org/ws/2/release/?fmt=json&limit=10&query=release%3A%22'
ITUNES = 'https://itunes.apple.com/search?entity=album&limit=5&term='


class Job(C.Structure):
    _fields_ = [(n, C.c_int) for n in ('cancel', 'done', 'total', 'saved', 'had', 'none', 'failed', 'state')]


class Track(C.Structure):
    _fields_ = [('path', C.c_char * 0x200), ('title', C.c_char * 0x200), ('artist', C.c_char * 0x100), ('shadow', C.c_char * (19 + 0x200))]


class Body(C.Structure):
    _fields_ = [('buf', C.c_void_p), ('len', C.c_uint), ('max', C.c_uint)]


class Al(C.Structure):
    _fields_ = [('job', Job), ('tracks', C.c_void_p), ('thread', C.c_ulong), ('running', C.c_int), ('label', C.c_void_p)]


def joined(fw, parts, n=256, lucene=0):
    fn = fw.fn('onlinematch__url_join', C.c_int, C.c_void_p, C.c_uint, C.c_void_p, C.c_int, C.c_int)
    out = block(fw, size=n)
    table = (C.c_char_p * len(parts))(*parts)
    return C.string_at(out) if fn(out, n, table, len(parts), lucene) else None


def test_even_parts_are_literal_and_odd_ones_encoded(fw):
    assert joined(fw, [b'a b/', b'c d/', b'e f']) == b'a b/c%20d%2Fe f'


def test_every_byte_encodes_as_urllib_does(fw):
    for c in range(1, 256):
        assert joined(fw, [b'', bytes([c])]) == urllib.parse.quote(bytes([c]), safe='').encode(), c


def test_a_character_encodes_byte_by_byte(fw):
    assert joined(fw, [b'', 'é—'.encode()]) == b'%C3%A9%E2%80%94'


def test_lucene_puts_a_backslash_before_quotes_and_backslashes_only(fw):
    assert joined(fw, [b'"', b'a"b\\c\'', b'"'], lucene=1) == b'"a%5C%22b%5C%5Cc%27"'
    assert joined(fw, [b'"', b'a"b'], lucene=0) == b'"a%22b'


@pytest.mark.parametrize('parts,lucene', [
    ([b'lit', b'x'], 0), ([b'', b'\xc3\xa9"'], 0), ([b'', b'a"\\'], 1), ([b'abc'], 0), ([b'', b'', b'x'], 0),
])
def test_it_fits_to_the_byte_or_gives_up(fw, parts, lucene):
    whole = joined(fw, parts, 64, lucene)
    assert joined(fw, parts, len(whole) + 1, lucene) == whole
    for n in range(1, len(whole) + 1):
        assert joined(fw, parts, n, lucene) is None


def test_all_empty_parts_are_an_empty_url(fw):
    assert joined(fw, [b'', b''], 1) == b''


def url(fw, name, n, first, second):
    fn = fw.fn(name, C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p)
    out = block(fw, size=n)
    return C.string_at(out).decode() if fn(out, n, first, second) else None


def test_lrclib_url(fw):
    assert url(fw, 'lrclib_url', 256, 'Café & Co'.encode(), b'A/B') == LRCLIB + 'Caf%C3%A9%20%26%20Co&artist_name=A%2FB'


def test_lrclib_url_without_an_artist(fw):
    assert url(fw, 'lrclib_url', 256, b'x', b'') == LRCLIB + 'x&artist_name='


def test_the_longest_tags_fit_their_callers_buffers(fw):
    title, artist = b'\xff' * 0x1ff, b'\xff' * 0xff  # keyinfo's sizes: three bytes each, encoded
    assert url(fw, 'lrclib_url', 3 * 0x300 + 64, title, artist)
    assert url(fw, 'itunes_url', 3 * 0x300 + 128, title, artist)
    assert url(fw, 'mb_url', 3 * 0x300 + 128, title, artist)


def test_a_url_too_long_says_so(fw):
    assert url(fw, 'lrclib_url', 60, b'a' * 20, b'b') is None
    assert url(fw, 'mb_url', 3 * 0x300 + 128, b'"' * 0x1ff, b'') is None  # lucene takes six each


def test_musicbrainz_release_query(fw):
    assert url(fw, 'mb_url', 256, b'a b', b'c') == MUSICBRAINZ + 'a%20b%22%20AND%20artist%3A%22c%22'
    assert url(fw, 'mb_url', 256, b'a"', b'') == MUSICBRAINZ + 'a%5C%22%22'


def test_itunes_term_is_artist_then_album(fw):
    assert url(fw, 'itunes_url', 256, b'In Rainbows', b'R') == ITUNES + 'R%20In%20Rainbows'


def art(fw, s):
    buf = block(fw, s)
    ok = fw.fn('itunes_art', C.c_int, C.c_void_p)(buf)
    return ok, C.string_at(buf)


@pytest.mark.parametrize('s,ok,out', [
    (b'https://x/100x100bb.jpg', 1, b'https://x/600x600bb.jpg'),
    (b'https://100x100/100x100.jpg', 1, b'https://100x100/600x600.jpg'),   # only the last
    (b'100x100100x100', 1, b'100x100600x600'),
    (b'100x100x100', 1, b'100x600x600'),                                   # the last, overlapping
    (b'100x100', 1, b'600x600'),
    (b'', 0, b''), (b'https://x/cover.jpg', 0, b'https://x/cover.jpg'), (b'100x10', 0, b'100x10'),
])
def test_itunes_art(fw, s, ok, out):
    assert art(fw, s) == (ok, out)


def body(fw, s):
    buf = block(fw, s)
    return C.string_at(fw.fn('lrc_body', C.c_void_p, C.c_void_p)(buf))


@pytest.mark.parametrize('s,kept', [
    (b'', b''),
    (b'[00:01.00] Sung\n', b'[00:01.00] Sung\n'),
    (b'plain', b'plain'),
    (b'[00:01.00] Mix : A\n[00:02.00] Sung\n', b'[00:02.00] Sung\n'),
    (b'[00:01.00] Mix : A\n[00:02.00] Lyrics : B\n[00:03.00] Sung', b'[00:03.00] Sung'),
    (b'[00:01.00][00:01.50] Mix : A\n[00:02.00] Sung', b'[00:02.00] Sung'),   # a line's several stamps
    (b'Mix : A\nSung', b'Sung'),
    (b'[00:01.00] Mix : A\r\n[00:02.00] Sung\r\n', b'[00:02.00] Sung\r\n'),
    (b'\n\n[00:01.00] Mix : A\n[00:02.00] Sung', b'[00:02.00] Sung'),         # blanks before a credit
    (b'\r\n \n[00:01.00]\n[00:02.00] Mix : A\nSung', b'Sung'),
    (b'[00:01.00] Mix : A\n\n[00:02.00] Sung', b'\n[00:02.00] Sung'),         # one after the last stays
    (b'[00:01.00]\n[00:02.00] Sung', b'[00:01.00]\n[00:02.00] Sung'),         # blanks before a sung line
    (b'[offset:+100]\n[00:01.00] Mix : A\n', b'[offset:+100]\n[00:01.00] Mix : A\n'),  # a tag ends the search
    (b'[00:01.00] Mix : A\n[ar:x]\n[00:02.00] B : C', b'[ar:x]\n[00:02.00] B : C'),
    (b'[00:01.00] Sung\n[00:02.00] Mix : A\n', b'[00:01.00] Sung\n[00:02.00] Mix : A\n'),  # credits lead only
    (b'[00:01.00] Mix : A\n[00:02.00] B : C\n', b''),                         # no sung line
    (b'[00:01.00] Mix : A', b''),
    (b'\n \r\n', b''),
    (b'[00:01.00]', b''),
    (b'a :\n b', b'a :\n b'),                                                 # " : " never crosses a line
    (b'x:y\n', b'x:y\n'), (b'a :b\n', b'a :b\n'), (b'a: b\n', b'a: b\n'),
    (b'a : \nSung', b'Sung'),
    (b'[00:01.00 Mix : A\nSung', b'Sung'),                                    # an unclosed stamp is text
    (b'[00:01.00 Sung\nx', b'[00:01.00 Sung\nx'),
    (b'[', b'['), (b'[1', b'[1'),
])
def test_opening_credits(fw, s, kept):
    assert body(fw, s) == kept


def path(fw, p, ext=b'.lrc', n=0x208):
    fn = fw.fn('lrc_path', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p)
    out = block(fw, size=n)
    return C.string_at(out) if fn(out, n, p, ext) else None


@pytest.mark.parametrize('p,lrc', [
    (b'/mnt/mmc/a.flac', b'/mnt/mmc/a.lrc'),
    (b'/mnt/mmc/a.b.flac', b'/mnt/mmc/a.b.lrc'),
    (b'/mnt/mmc/no ext', b'/mnt/mmc/no ext.lrc'),
    (b'/mnt/m.m/no ext', b'/mnt/m.m/no ext.lrc'),   # a folder's dot is not the file's
    (b'/mnt/mmc/.hidden', b'/mnt/mmc/.hidden.lrc'),
    (b'/mnt/mmc/a.', b'/mnt/mmc/a.lrc'),
    (b'a.flac', b'a.lrc'), (b'a', b'a.lrc'), (b'', b'.lrc'),
])
def test_a_songs_lrc_path(fw, p, lrc):
    assert path(fw, p) == lrc


def test_lrc_path_to_the_byte(fw):
    assert path(fw, b'/a/b.flac', n=len(b'/a/b.lrc') + 1) == b'/a/b.lrc'
    assert path(fw, b'/a/b.flac', n=len(b'/a/b.lrc')) is None
    assert path(fw, b'/a/b.flac', b'.LRC', 9) == b'/a/b.LRC'


def name(fw, s, n=64):
    fn = fw.fn('lrc_name', C.c_int, C.c_void_p, C.c_uint, C.c_char_p)
    out = block(fw, size=n)
    return C.string_at(out) if fn(out, n, s) else None


@pytest.mark.parametrize('s,lrc', [
    (b'a.flac', b'a.lrc'), (b'a.b.c', b'a.b.lrc'), (b'.flac', b'.lrc'), (b'a.', b'a.lrc'),
    (b'noext', b'noext'),   # stock's name is the file's, without a dot
    (b'', b''),
])
def test_stocks_downpic_name(fw, s, lrc):
    assert name(fw, s) == lrc


def test_lrc_name_to_the_byte(fw):
    assert name(fw, b'ab.flac', 7) == b'ab.lrc' and name(fw, b'ab.flac', 6) is None
    assert name(fw, b'noext', 6) == b'noext' and name(fw, b'noext', 5) is None
    assert name(fw, b'', 1) == b''


def fold(fw, s, n=None):
    fn = fw.fn('lrc_fold', C.c_int, C.c_void_p, C.c_uint, C.c_void_p)
    size = len(s) + 1 if n is None else n
    out, src = block(fw, size=size), block(fw, s)
    return C.string_at(out) if fn(out, size, src) else None


# Letters NFD leaves whole: a stroke, a ligature, or a letter of their own.
STROKES = {'Æ': 'AE', 'æ': 'ae', 'Ð': 'D', 'ð': 'd', 'Ø': 'O', 'ø': 'o', 'Þ': 'TH', 'þ': 'th', 'ß': 'ss', 'Đ': 'D', 'đ': 'd',
           'Ħ': 'H', 'ħ': 'h', 'ı': 'i', 'Ĳ': 'IJ', 'ĳ': 'ij', 'ĸ': 'k', 'Ŀ': 'L', 'ŀ': 'l', 'Ł': 'L', 'ł': 'l', 'ŉ': 'n',
           'Ŋ': 'N', 'ŋ': 'n', 'Œ': 'OE', 'œ': 'oe', 'Ŧ': 'T', 'ŧ': 't', 'ſ': 's'}


def plain(c):
    if c in STROKES:
        return STROKES[c]
    base = unicodedata.normalize('NFD', c)[0]
    return base if base.isascii() and base.isalpha() else c


def test_every_latin_letter_folds_to_its_plain_one(fw):
    for point in [*range(0xc0, 0x180), *range(0x1e00, 0x1f00)]:
        c = chr(point)
        assert fold(fw, c.encode()) == plain(c).encode(), hex(point)


@pytest.mark.parametrize('s,out', [
    ('Ơơ Ưư', 'Oo Uu'),                                  # Vietnamese's horned letters
    ('e\u0301a\u036f\u0300', 'ea'), ('\u0301', ''),      # combining marks go
    ('Hataałii', 'Hataalii'), ('Ærøskøbing', 'AEroskobing'), ('Việt Nam', 'Viet Nam'),
    ('Straße', 'Strasse'), ('×÷', '×÷'),
    ('ascii ~!', 'ascii ~!'), ('', ''),
    ('Ωя你好😀', 'Ωя你好😀'),                             # other scripts as they are
    ('\u0180\u019f\u01a2\u01ae\u01b1\u0370\u02ff\u1dff\u1f00', '\u0180\u019f\u01a2\u01ae\u01b1\u0370\u02ff\u1dff\u1f00'),
])
def test_fold(fw, s, out):
    assert fold(fw, s.encode()) == out.encode()


@pytest.mark.parametrize('raw', [b'\xc3', b'a\xc3', b'\xe1\x80', b'\xe1', b'\x80', b'\xff\xfe', b'\xf0\x9f', b'\xc0\x80', b'\xe0\x80\x80'])
def test_bytes_that_are_no_character_stay(fw, raw):
    assert fold(fw, raw) == raw


def test_an_overlong_form_folds_as_its_character(fw):
    assert fold(fw, b'\xe0\x83\x80') == b'A'  # U+00C0 in three bytes


def test_fold_to_the_byte(fw):
    assert fold(fw, 'é'.encode(), 2) == b'e' and fold(fw, 'é'.encode(), 1) is None
    assert fold(fw, 'æ'.encode(), 3) == b'ae' and fold(fw, 'æ'.encode(), 2) is None
    assert fold(fw, 'ab'.encode(), 3) == b'ab' and fold(fw, 'ab'.encode(), 2) is None
    assert fold(fw, '好'.encode(), 4) == '好'.encode() and fold(fw, '好'.encode(), 3) is None
    assert fold(fw, b'', 1) == b''


SONGS = ['Hataałii — Tóshį́į́', 'Ærøskøbing', 'Việt Nam', 'Œuvre ß', '好 😀 é', 'a"b\\c', '[00:01.00] Mix : A\n[00:02.00] Sung\r\n']


def hostile(seed, samples, count=2000):
    """Random bytes, every truncation of the samples, and mutations of them."""
    r = random.Random(seed)
    samples = [s.encode() for s in samples]
    for _ in range(count):
        yield r.randbytes(r.randrange(80)).replace(b'\0', b'\xc3')
    for s in samples:
        for k in range(len(s)):
            yield s[:k]
    for _ in range(count):
        b = bytearray(r.choice(samples))
        for _ in range(r.randrange(1, 5)):
            b[r.randrange(len(b))] = r.choice(b'\xc3\xc4\xe1\xba\xcc\x80\xff "\\[]:\n\r ')
        yield bytes(b).replace(b'\0', b'.')


def test_hostile_folds_never_grow(fw):
    for s in hostile(11, SONGS):
        assert fold(fw, s) is not None  # exactly s's size: "never longer than s"


def test_hostile_lyrics_stay_inside_the_text(fw):
    for s in hostile(12, SONGS):
        kept = body(fw, s)
        assert s.endswith(kept)


def test_hostile_tags_in_urls(fw):
    for s in hostile(13, SONGS):
        for fn, make in (('lrclib_url', lambda u: u.startswith(LRCLIB)), ('mb_url', lambda u: u.startswith(MUSICBRAINZ))):
            whole = url(fw, fn, 512, s, s)
            if whole:
                assert make(whole) and all(c.isascii() and c > ' ' for c in whole)


def test_hostile_paths(fw):
    for s in hostile(14, ['/mnt/mmc/Album/01 Song.flac', 'a.b.c/.d', 'x']):
        for n in (len(s) + 1, len(s) + 5, 8):
            lrc = path(fw, s, n=n)
            assert lrc is None or lrc.endswith(b'.lrc')
            assert name(fw, s, n) is None or len(name(fw, s, n)) < n


class Card:
    """access, rename and unlink on a set of paths; each call's order kept."""
    def __init__(self, fw, *files):
        self.files, self.log = set(files), []
        def access(p, mode, *_):
            return 0 if C.string_at(p) in self.files else -1
        def rename(a, b, *_):
            a, b = C.string_at(a), C.string_at(b)
            self.log.append(('rename', a, b))
            self.files.discard(a)
            self.files.add(b)
        def unlink(p, *_):
            self.log.append(('unlink', C.string_at(p)))
            self.files.discard(C.string_at(p))
        fw.mock('fw_access', access)
        fw.mock('fw_rename', rename)
        fw.mock('fw_unlink', unlink)


FETCH = C.CFUNCTYPE(C.c_int, C.POINTER(Track), C.c_char_p, C.POINTER(C.c_int))


def album(fw, card, paths, answers, shadow=b''):
    """album_lyrics over paths, each fetch taking the next of answers; (state, job, [(title, part)])."""
    tracks = (Track * len(paths))()
    for t, p in zip(tracks, paths):
        t.path, t.title, t.artist, t.shadow = p, p.rsplit(b'/')[-1], b'Artist', shadow
    job, asked, left = Job(), [], list(answers)
    def fetch(t, part, cancel):
        asked.append((t.contents.title, part))
        answer = left.pop(0)
        if answer == LRC_SAVED:
            card.files.add(part)
        return answer
    callback = FETCH(fetch)
    fn = fw.fn('album_lyrics', C.c_int, C.POINTER(Job), C.c_void_p, C.c_int, C.c_void_p)
    state = fn(C.byref(job), tracks, len(paths), C.cast(callback, C.c_void_p))
    assert not left
    return state, job, asked


def test_a_track_written_goes_through_its_part_file(fw):
    card = Card(fw, b'/a/1.flac')
    state, job, asked = album(fw, card, [b'/a/1.flac'], [LRC_SAVED])
    assert state == LJ_DONE and job.saved == 1 and asked == [(b'1.flac', b'/a/1.lrc.part')]
    assert card.log == [('rename', b'/a/1.lrc.part', b'/a/1.lrc')]


def test_a_saved_track_removes_its_shadow_copy_after_the_rename(fw):
    card = Card(fw, b'/a/1.flac')
    album(fw, card, [b'/a/1.flac'], [LRC_SAVED], shadow=b'/mnt/mmc/.downpic/1.lrc')
    assert [e[0] for e in card.log] == ['rename', 'unlink'] and card.log[1][1] == b'/mnt/mmc/.downpic/1.lrc'


def test_no_shadow_nothing_unlinked(fw):
    card = Card(fw, b'/a/1.flac')
    album(fw, card, [b'/a/1.flac'], [LRC_SAVED])
    assert not [e for e in card.log if e[0] == 'unlink']


def test_a_failed_rename_leaves_no_part_and_ends_as_a_card_error(fw):
    card = Card(fw, b'/a/1.flac', b'/a/2.flac')
    fw.mock('fw_rename', lambda *_: -1)
    state, job, asked = album(fw, card, [b'/a/1.flac', b'/a/2.flac'], [LRC_SAVED])
    assert state == LJ_CARD and job.saved == 0 and len(asked) == 1
    assert b'/a/1.lrc.part' not in card.files


def test_the_longest_path_a_track_holds_gets_its_lrc_and_part(fw):
    long = b'/' + b'a' * 0x1fe  # no extension to replace
    card = Card(fw, long)
    state, job, asked = album(fw, card, [long], [LRC_SAVED])
    assert state == LJ_DONE and job.saved == 1 and asked[0][1] == long + b'.lrc.part' and long + b'.lrc' in card.files


def test_busy_waits_in_steps_between_tries(fw):
    card = Card(fw, b'/a/1.flac')
    state, job, asked = album(fw, card, [b'/a/1.flac'], [LRC_BUSY, LRC_BUSY, LRC_NONE])
    sleeps = [c[1][0] for c in fw.calls('fw_usleep')]
    assert len(asked) == 3 and sleeps == [100000] * 40  # 2 s a retry, cancellable each 100 ms
    assert job.none == 1


def test_cancelled_during_the_pause_stops_at_once(fw):
    card = Card(fw, b'/a/1.flac', b'/a/2.flac')
    tracks, job, asked = (Track * 2)(), Job(), []
    tracks[0].path, tracks[1].path = b'/a/1.flac', b'/a/2.flac'
    def fetch(t, part, cancel):
        asked.append(1)
        return LRC_BUSY
    fw.mock('fw_usleep', lambda *_: setattr(job, 'cancel', 1))
    callback = FETCH(fetch)
    fn = fw.fn('album_lyrics', C.c_int, C.POINTER(Job), C.c_void_p, C.c_int, C.c_void_p)
    assert fn(C.byref(job), tracks, 2, C.cast(callback, C.c_void_p)) == LJ_CANCELLED
    assert len(asked) == 1 and len(fw.calls('fw_usleep')) == 1


def test_an_empty_album_is_done(fw):
    Card(fw)
    assert album(fw, Card(fw), [], [])[0] == LJ_DONE


def test_a_track_with_lyrics_beside_it_is_left_as_it_is(fw):
    card = Card(fw, b'/a/1.flac', b'/a/1.lrc', b'/a/2.flac', b'/a/2.LRC', b'/a/3.flac')
    state, job, asked = album(fw, card, [b'/a/1.flac', b'/a/2.flac', b'/a/3.flac'], [LRC_SAVED])
    assert (state, job.had, job.saved, job.done) == (LJ_DONE, 2, 1, 3) and [a[0] for a in asked] == [b'3.flac']


def test_a_file_gone_since_the_scan_is_passed_over_uncounted(fw):
    card = Card(fw, b'/a/2.flac')
    state, job, asked = album(fw, card, [b'/a/1.flac', b'/a/2.flac'], [LRC_NONE])
    assert (state, job.done, job.none, job.failed, job.had) == (LJ_DONE, 2, 1, 0, 0) and [a[0] for a in asked] == [b'2.flac']


def test_two_tracks_unanswered_in_a_row_end_the_job(fw):
    paths = [b'/a/1.flac', b'/a/2.flac', b'/a/3.flac']
    card = Card(fw, *paths)
    state, job, asked = album(fw, card, paths, [LRC_OFFLINE, LRC_OFFLINE])
    assert (state, job.failed, len(asked)) == (LJ_OFFLINE, 2, 2)
    assert card.log == [('unlink', b'/a/1.lrc.part'), ('unlink', b'/a/2.lrc.part')]
    assert not fw.calls('fw_usleep')  # nothing answered yet: likely offline, so no retry


def test_once_lrclib_has_answered_a_failed_request_is_retried_once(fw):
    paths = [b'/a/%d.flac' % i for i in range(1, 5)]
    card = Card(fw, *paths)
    state, job, asked = album(fw, card, paths, [LRC_OFFLINE, LRC_NONE, LRC_OFFLINE, LRC_OFFLINE, LRC_SAVED])
    assert (state, job.failed, job.none, job.saved) == (LJ_DONE, 2, 1, 1)  # an answer between ends a run
    assert [a[0] for a in asked] == [b'1.flac', b'2.flac', b'3.flac', b'3.flac', b'4.flac']
    assert [c[1][0] for c in fw.calls('fw_usleep')] == [100000] * 20  # one 2 s pause, cancellable each 100 ms


def test_tracks_still_busy_count_as_unanswered(fw):
    paths = [b'/a/1.flac', b'/a/2.flac', b'/a/3.flac']
    card = Card(fw, *paths)
    state, job, asked = album(fw, card, paths, [LRC_BUSY] * 6)
    assert (state, job.failed, len(asked)) == (LJ_OFFLINE, 2, 6)  # three requests each


def test_a_saved_track_between_unanswered_ones_starts_the_run_again(fw):
    paths = [b'/a/1.flac', b'/a/2.flac', b'/a/3.flac']
    card = Card(fw, *paths)
    state, job, asked = album(fw, card, paths, [LRC_OFFLINE, LRC_SAVED, LRC_OFFLINE, LRC_OFFLINE])
    assert (state, job.failed, job.saved) == (LJ_DONE, 2, 1)
    assert [a[0] for a in asked] == [b'1.flac', b'2.flac', b'3.flac', b'3.flac']  # answered once: the third retried


def test_a_card_that_cant_be_written_ends_the_job(fw):
    card = Card(fw, b'/a/1.flac', b'/a/2.flac')
    state, job, asked = album(fw, card, [b'/a/1.flac', b'/a/2.flac'], [LRC_CARD])
    assert (state, len(asked)) == (LJ_CARD, 1) and card.log == [('unlink', b'/a/1.lrc.part')]


def note(fw, n, **fields):
    job, out = Job(**fields), block(fw, size=n)
    fw.fn('album_note', None, C.c_void_p, C.c_uint, C.POINTER(Job))(out, n, C.byref(job))
    return C.string_at(out).decode()


def test_every_note_is_cut_at_any_size_never_past_it(fw):
    cases = [dict(state=s, saved=a, none=b, had=c, failed=d) for s in range(4)
             for a, b, c, d in ((0, 0, 0, 0), (12, 3, 1, 2), (0, 1, 0, 0), (0, 0, 5, 0), (0, 0, 0, 7))]
    for case in cases:
        whole = note(fw, 200, **case)
        for n in range(1, len(whole) + 3):
            assert note(fw, n, **case) == whole[:n - 1]


def test_the_rest_of_the_note(fw):
    assert note(fw, 96, state=LJ_CANCELLED, saved=1, had=2) == 'Cancelled: 1 saved, 2 already had lyrics'
    assert note(fw, 96, state=LJ_CARD) == "Can't write to the card"
    assert note(fw, 96, state=LJ_OFFLINE, had=3) == "Can't reach LRCLIB"
    assert note(fw, 96, state=LJ_OFFLINE, none=1, failed=1) == 'LRCLIB stopped answering: 1 not found, 1 failed'


def got(fw, b, data, size=1):
    fn = fw.fn('onlinematch__got', C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.POINTER(Body))
    return fn(data, size, len(data) // size, C.byref(b))


def test_a_reply_collects_under_its_max_with_room_for_the_nul(fw):
    b = Body(block(fw, size=10), 0, 10)
    assert got(fw, b, b'abcd') == 4 and got(fw, b, b'efgh') == 4 and b.len == 8
    assert got(fw, b, b'ij') == 0 and b.len == 8                       # would fill it: the transfer fails
    assert got(fw, b, b'i') == 1 and b.len == 9 and C.string_at(b.buf, 9) == b'abcdefghi'
    assert got(fw, b, b'') == 0


def test_curl_counts_items(fw):
    b = Body(block(fw, size=10), 0, 10)
    assert got(fw, b, b'abcdef', 3) == 6 and b.len == 6


def test_the_transfer_stops_when_cancelled(fw):
    stopped = fw.fn('onlinematch__stopped', C.c_int, C.POINTER(C.c_int), C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)
    cancel = C.c_int(0)
    assert stopped(C.byref(cancel), 0, 0, 0, 0) == 0
    cancel.value = 1
    assert stopped(C.byref(cancel), 0, 0, 0, 0) == 1


def test_al_put_copies_or_clears(fw):
    put = fw.fn('onlinematch__al_put', None, C.c_void_p, C.c_uint, C.c_char_p)
    out = block(fw, b'old', 4)
    put(out, 4, b'abcdef')
    assert C.string_at(out) == b'abc'
    put(out, 4, None)
    assert C.string_at(out) == b''


def test_the_label_is_forgotten_only_if_its_own(fw):
    gone = fw.fn('onlinematch__al_gone', C.c_int, C.c_void_p, C.c_void_p)
    al = fw.var('onlinematch__al', Al)
    al.label = 5
    assert gone(6, None) == 0 and al.label == 5
    assert gone(5, None) == 0 and not al.label


def test_the_row_says_where_the_job_is(fw):
    show = fw.fn('onlinematch__al_show', None)
    al = fw.var('onlinematch__al', Al)
    seen = []
    fw.mock('widget_set_text_utf8', lambda w, s, *_: seen.append(C.string_at(s).decode()))
    show()
    assert seen == []  # no row
    al.label = 9
    show()
    al.thread, al.job.done, al.job.total = 1, 1, 5
    show()
    al.job.done = 5
    show()
    al.job.cancel = 1
    show()
    assert seen == ['Download Album Lyrics', 'Downloading 2 of 5', 'Downloading 5 of 5', 'Cancelling…']


class Net:
    """libcurl and cJSON: a transfer answers from replies by URL, and cJSON_Parse is json.loads over
    nodes laid out as the Q2's cJSON (valuestring at +16)."""
    def __init__(self, fw, replies=()):
        self.replies, self.urls, self.nodes, self.keep = dict(replies), [], {}, []
        self.opts, self.info = {}, {}
        def setopt(c, opt, value, *_):
            self.opts[opt] = value
        def perform(c, *_):
            url = C.string_at(self.opts[10002]).decode()
            self.urls.append(url)
            reply = self.reply(url)
            if reply is None:
                return 7  # couldn't connect
            write = C.CFUNCTYPE(C.c_uint, C.c_char_p, C.c_uint, C.c_uint, C.c_void_p)(self.opts[20011]) if 20011 in self.opts else None
            for k in range(0, len(reply), 16384):
                piece = reply[k:k + 16384]
                if write and write(piece, 1, len(piece), self.opts[10001]) != len(piece):
                    return 23  # write error
            return 0
        fw.mock('fw_curl_easy_init', lambda *_: self.opts.clear() or 1)
        fw.mock('fw_curl_easy_setopt', setopt)
        fw.mock('fw_curl_easy_perform', perform)
        fw.mock('cJSON_Parse', self.parse)
        fw.mock('cJSON_GetArraySize', lambda n, *_: len(self.nodes[n]))
        fw.mock('cJSON_GetArrayItem', lambda n, i, *_: self.node(self.nodes[n][i]))
        fw.mock('cJSON_GetObjectItem', self.member)

    def reply(self, url):
        for prefix, reply in self.replies.items():
            if url.startswith(prefix):
                return reply[0](url) if isinstance(reply, tuple) else reply
        return None

    def node(self, value):
        """A cJSON item: valuestring at byte 16, 0 unless a string."""
        raw = C.create_string_buffer(32)
        if isinstance(value, str):
            s = C.create_string_buffer(value.encode())
            self.keep.append(s)
            C.c_void_p.from_buffer(raw, 16).value = C.addressof(s)
        self.keep.append(raw)
        self.nodes[C.addressof(raw)] = value
        return C.addressof(raw)

    def parse(self, text, *_):
        try:
            return self.node(json.loads(C.string_at(text)))
        except ValueError:
            return 0

    def member(self, n, key, *_):
        value = self.nodes[n]
        name = C.string_at(key).decode()
        return self.node(value[name]) if isinstance(value, dict) and name in value else 0


class Written:
    """fopen, fputs and fclose: what a lyrics file got."""
    def __init__(self, fw, opens=True, close=0):
        self.text, self.paths = b'', []
        fw.mock('fw_fopen', lambda p, *_: (self.paths.append(C.string_at(p)), 1)[1] if opens else 0)
        fw.mock('fw_fputs', lambda s, f, *_: setattr(self, 'text', self.text + C.string_at(s)))
        fw.mock('fw_fclose', lambda *_: close)


def search(fw, title=b'T', artist=b'A', cancel=None):
    fn = fw.fn('onlinematch__lrclib_search', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_void_p)
    return fn(title, artist, b'/a/1.lrc.part', cancel)


def results(*lyrics):
    return json.dumps([{'id': i, 'syncedLyrics': s} for i, s in enumerate(lyrics)]).encode()


def test_the_first_result_with_synced_lyrics_is_saved_with_a_bom_less_its_credits(fw):
    Net(fw, {LRCLIB: results(None, '', '[00:01.00] Mix : A\n[00:02.00] Sung\n', '[00:01.00] Later\n')})
    file = Written(fw)
    assert search(fw) == LRC_SAVED
    assert file.paths == [b'/a/1.lrc.part'] and file.text == b'\xef\xbb\xbf[00:02.00] Sung\n'


def test_a_result_that_is_all_credits_is_passed_over(fw):
    Net(fw, {LRCLIB: results('[00:01.00] Mix : A\n')})
    file = Written(fw)
    assert search(fw) == LRC_NONE and file.paths == []


def test_the_url_names_the_song(fw):
    net = Net(fw, {LRCLIB: b'[]'})
    Written(fw)
    assert search(fw, b'A B', b'C') == LRC_NONE
    assert net.urls == [LRCLIB + 'A%20B&artist_name=C']


def test_no_title_asks_nothing(fw):
    net = Net(fw)
    assert search(fw, b'') == LRC_NONE and not net.urls


def test_no_answer_is_offline(fw):
    Net(fw)
    assert search(fw) == LRC_OFFLINE


def test_an_answer_that_is_no_list_is_busy(fw):
    Net(fw, {LRCLIB: b'{"code":503,"name":"ServerOverloaded"}'})
    assert search(fw) == LRC_BUSY


def test_a_cancelled_transfer_says_so(fw):
    Net(fw)
    cancel = C.c_int(1)
    assert search(fw, cancel=C.addressof(cancel)) == LRC_CANCELLED


def test_garbage_that_looks_like_a_list_finds_nothing(fw):
    Net(fw, {LRCLIB: b'[1,'})
    Written(fw)
    assert search(fw) == LRC_NONE


def test_a_reply_over_the_limit_fails_the_transfer(fw):
    Net(fw, {LRCLIB: b'[' + b' ' * LRCLIB_MAX + b']'})
    assert search(fw) == LRC_OFFLINE


def test_a_reply_just_under_the_limit_is_read(fw):
    Net(fw, {LRCLIB: results('[00:01.00] x\n').ljust(LRCLIB_MAX - 1)})
    Written(fw)
    assert search(fw) == LRC_SAVED


def test_a_file_that_cant_open_or_close_is_a_card_error(fw):
    Net(fw, {LRCLIB: results('[00:01.00] x\n')})
    Written(fw, opens=False)
    assert search(fw) == LRC_CARD
    Written(fw, close=-1)
    assert search(fw) == LRC_CARD


def lrclib(fw, title, artist=b'A'):
    fn = fw.fn('onlinematch__lrclib', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_void_p)
    return fn(title, artist, b'/a/1.lrc.part', None)


def test_a_title_lrclib_lacks_is_asked_again_without_its_accents(fw):
    def answer(url):
        return results('[00:01.00] Sung\n') if 'Hataalii' in url else b'[]'
    net = Net(fw, {LRCLIB: (answer,)})
    Written(fw)
    assert lrclib(fw, 'Hataałii'.encode(), 'Ł'.encode()) == LRC_SAVED
    assert net.urls[0] == LRCLIB + 'Hataa%C5%82ii&artist_name=%C5%81' and net.urls[1] == LRCLIB + 'Hataalii&artist_name=L'


def test_plain_names_are_not_asked_twice(fw):
    net = Net(fw, {LRCLIB: b'[]'})
    Written(fw)
    assert lrclib(fw, b'Plain') == LRC_NONE and len(net.urls) == 1


def test_only_a_search_that_found_none_is_asked_again(fw):
    net = Net(fw)
    assert lrclib(fw, 'é'.encode()) == LRC_OFFLINE and len(net.urls) == 1


def keyinfo(title=b'', album=b'', artist=b''):
    def fill(key, *_):
        for at, value in ((0x200, title), (0x400, album), (0x500, artist)):
            C.memmove(key + at, value + b'\0', len(value) + 1)
    return fill


def test_the_lyric_search_asks_again_while_busy(fw):
    net = Net(fw, {LRCLIB: b'{"code":503}'})
    fw.mock('online_getkeyword_info', keyinfo(b'T', b'', b'A'))
    assert fw.fn('ringnav_lyric_search', C.c_int, C.c_char_p, C.c_char_p)(b'kw', b'/tmp/out') == 1
    assert len(net.urls) == 3 and [c[1][0] for c in fw.calls('fw_usleep')] == [2000000, 2000000]


def test_the_lyric_search_stops_at_an_answer(fw):
    net = Net(fw, {LRCLIB: b'[]'})
    fw.mock('online_getkeyword_info', keyinfo(b'T', b'', b'A'))
    fw.fn('ringnav_lyric_search', C.c_int, C.c_char_p, C.c_char_p)(b'kw', b'/tmp/out')
    assert len(net.urls) == 1 and not fw.calls('fw_usleep')


class Covers:
    def __init__(self, fw, title=b'', album=b'', artist=b''):
        self.added, self.limit = [], 0
        fw.mock('online_getkeyword_info', keyinfo(title, album, artist))
        fw.mock('online_addcoverlist', lambda s, *_: self.added.append(C.string_at(s).decode()))
        fw.mock('online_getsize_coverlist', lambda *_: len(self.added))
        self.fn = fw.fn('onlinematch__covers', None)


def groups(*ids):
    return json.dumps({'releases': [{'release-group': {'id': i}} for i in ids]}).encode()


def redirects(net, fw, code=307, to='https://archive.org/x.jpg'):
    target = C.create_string_buffer(to.encode()) if to else None
    def getinfo(c, what, out, *_):
        if what == 0x200002:
            C.cast(out, C.POINTER(C.c_long))[0] = code
        elif what == 0x10001f and target:
            C.cast(out, C.POINTER(C.c_void_p))[0] = C.addressof(target)
    net.keep.append(target)
    fw.mock('fw_curl_easy_getinfo', getinfo)


ID = 'abcdefgh-0000-0000-0000-00000000000%d'


def test_each_release_groups_front_once_at_most_four(fw):
    ids = [ID % 1, ID % 1, ID % 2, 'short', ID % 3, ID % 4, ID % 5]
    net = Net(fw, {'https://musicbrainz': groups(*ids), 'https://coverartarchive': b'', ITUNES: b'{}'})
    redirects(net, fw)
    covers = Covers(fw, album=b'Al', artist=b'Ar')
    covers.fn()
    asked = [u for u in net.urls if 'coverartarchive' in u]
    assert asked == [f'https://coverartarchive.org/release-group/{ID % k}/front-500' for k in (1, 2, 3, 4)]
    assert covers.added == ['https://archive.org/x.jpg'] * 4


def test_the_album_falls_back_to_the_title(fw):
    net = Net(fw, {'https://musicbrainz': b'{}', ITUNES: b'{}'})
    Covers(fw, title=b'Ti').fn()
    assert net.urls[0] == MUSICBRAINZ + 'Ti%22'


def test_nothing_to_search_for_asks_nothing(fw):
    net = Net(fw)
    Covers(fw).fn()
    assert not net.urls


def test_musicbrainz_unreachable_leaves_itunes_unasked(fw):
    net = Net(fw, {ITUNES: b'{}'})
    Covers(fw, album=b'A').fn()
    assert len(net.urls) == 1


def test_itunes_art_rewritten_and_unusable_passed_over(fw):
    reply = json.dumps({'results': [{'artworkUrl100': 'https://i/100x100bb.jpg'}, {'artworkUrl100': 'https://i/none.jpg'},
                                    {'artworkUrl100': 'https://i/100x100' + 'a' * 600}, {}]}).encode()
    net = Net(fw, {'https://musicbrainz': b'{}', ITUNES: reply})
    covers = Covers(fw, album=b'A')
    covers.fn()
    assert covers.added == ['https://i/600x600bb.jpg']


def test_a_full_cover_list_stops_both_searches(fw):
    net = Net(fw, {'https://musicbrainz': groups(ID % 1), ITUNES: json.dumps({'results': [{'artworkUrl100': 'https://i/100x100.jpg'}]}).encode()})
    covers = Covers(fw, album=b'A')
    covers.added.extend(['x'] * 10)
    covers.fn()
    assert len(covers.added) == 10 and not [u for u in net.urls if 'coverartarchive' in u]


def caa(fw, net, code=307, to='https://archive.org/x.jpg'):
    redirects(net, fw, code, to)
    out = block(fw, size=0x200)
    ok = fw.fn('onlinematch__caa', C.c_int, C.c_char_p, C.c_void_p)(b'id', out)
    return ok, C.string_at(out)


def test_no_front_cover_is_a_404(fw):
    net = Net(fw, {'https://coverartarchive': b''})
    assert caa(fw, net, 404, None)[0] == 0


def test_a_redirect_fills_its_0x200_buffer_exactly(fw):
    net = Net(fw, {'https://coverartarchive': b''})
    to = 'h' * 0x1ff
    assert caa(fw, net, to=to) == (1, to.encode())


def test_a_redirect_too_long_for_stocks_list_is_refused(fw):
    net = Net(fw, {'https://coverartarchive': b''})
    assert caa(fw, net, to='h' * 0x200)[0] == 0


class Copy:
    """lrc_copy's four stubs: tmp holds data, writes go to a bytes object."""
    def __init__(self, fw, data, opens=2, writes=True, close=0):
        self.data, self.written, self.opened, self.handles = list(data), b'', [], 0
        def fopen(path, mode, *_):
            self.opened.append(C.string_at(path))
            self.handles += 1
            return self.handles if self.handles <= opens else 0
        def fread(buf, size, n, f, *_):
            piece = bytes(self.data[:n])
            del self.data[:n]
            C.memmove(buf, piece, len(piece))
            return len(piece)
        def fwrite(buf, size, n, f, *_):
            self.written += C.string_at(buf, size * n)
            return n if writes else 0
        fw.mock('fw_fopen', fopen)
        fw.mock('fw_fread', fread)
        fw.mock('fw_fwrite', fwrite)
        fw.mock('fw_fclose', lambda f, *_: close if f == 2 else 0)
        self.card = Card(fw)
        self.fn = fw.fn('onlinematch__lrc_copy', C.c_int, C.c_char_p, C.c_char_p)


def test_a_copy_goes_through_a_part_file(fw):
    copy = Copy(fw, b'x' * 10000)
    assert copy.fn(b'/tmp/oslyric_1', b'/a/1.lrc') == 1
    assert copy.opened == [b'/tmp/oslyric_1', b'/a/1.lrc.part'] and copy.written == b'x' * 10000
    assert copy.card.log == [('rename', b'/a/1.lrc.part', b'/a/1.lrc')]


def test_a_copy_that_fails_leaves_no_part_file(fw):
    for kind in (dict(opens=1), dict(writes=False), dict(close=-1)):
        copy = Copy(fw, b'x', **kind)
        assert copy.fn(b'/tmp/t', b'/a/1.lrc') == 0
        assert copy.card.log == [('unlink', b'/a/1.lrc.part')], kind


def test_our_covers_go_before_stocks_kugou_search(fw):
    art = json.dumps({'results': [{'artworkUrl100': 'https://i/100x100.jpg'}]}).encode()
    net = Net(fw, {'https://musicbrainz': groups(ID % 1), 'https://coverartarchive': b'', ITUNES: art})
    redirects(net, fw)
    covers, seen = Covers(fw, album=b'Al', artist=b'Ar'), []
    fw.mock('stock_kugoucover_trampoline', lambda keyword, *_: seen.append((C.string_at(keyword), list(covers.added))))
    fw.fn('ringnav_cover_search', None, C.c_char_p)(b'Ti&Ar')
    assert seen == [(b'Ti&Ar', ['https://archive.org/x.jpg', 'https://i/600x600.jpg'])]


# Stock's records, its queue and library, and the card, for the lyrics save and Download Album Lyrics.
BOM = b'\xef\xbb\xbf'
THREAD = 0x7e57
FMT = b'cp -rf %s "/mnt/mmc/.downpic/%s"'  # stock's accept command (docs/internals.md#lyrics-search)


class Records:
    """Stock's song records as the Q2 lays them out, a word a field. P() reads a pointer field 8 bytes wide
    here, its word and the next one above it: so every word holds a number of its own, and a field's string
    goes where its word and the next make an address, in a mapping of its own: the words are this file's high
    halves (conftest.high_halves), each under 0x5000, so a string sits well inside the 64 KB at its next word."""
    HIGHS = high_halves(0x100)

    def __init__(self):
        self.words, self.maps, self.keep = iter(self.HIGHS), [], []

    def fresh(self):
        return next(self.words)

    def song(self, path, album=b'', title=b'', artist=b'', name=None, album_artist=None, kind=8, disc=0, track=0, cue=(0, 0)):
        record, words = C.create_string_buffer(0x60), {}
        fields = [('REC_NAME', path.rsplit(b'/', 1)[-1] if name is None else name), ('REC_PATH', path),
                  ('REC_TITLE', title), ('REC_ALBUM', album), ('REC_ARTIST', artist)]
        fields += [('REC_ALBUM_ARTIST', album_artist)] if album_artist is not None else []
        for field, value in fields:
            low, high = words.setdefault(O[field], self.fresh()), words.setdefault(O[field] + 4, self.fresh())
            assert libc.mmap(high << 32, 0x10000, 3, 0x100022, -1, 0) == high << 32  # MAP_FIXED_NOREPLACE
            self.maps.append(high << 32)
            C.memmove((high << 32) + low, value + b'\0', len(value) + 1)
        for offset, word in words.items():
            struct.pack_into('<I', record, offset, word)
        for field, number in (('REC_TYPE', kind), ('REC_DISC', disc), ('REC_TRACK', track), ('REC_CUE_START', cue[0]), ('REC_CUE_END', cue[1])):
            struct.pack_into('<i', record, O[field], number)
        assert all(C.c_char_p.from_address(C.addressof(record) + O[f]).value == v for f, v in fields)  # as P() reads them
        self.keep.append(record)
        return C.addressof(record)

    def close(self):
        for at in self.maps:
            libc.munmap(C.c_void_p(at), C.c_size_t(0x10000))


@pytest.fixture
def records():
    made = Records()
    yield made
    made.close()


class Queue:
    """Stock's play queue (mcl_pdeqplaylist at MCL_POS, of MCL_TYPE) and its library's getMusicByAlbum, which
    fills the deque staged() put in tools_pdeq_directory with the rows of albums of that name."""
    def __init__(self, fw, mcl, rows=(), pos=0, kind=0xf001):
        self.fw, self.deques, self.albums = fw, Deques(fw), {}
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.deques.new(rows)
        mcl.pos, mcl.type = pos, kind
        fw.mock('getMusicByAlbum', self.album)

    def album(self, name, *_):
        rows = self.albums.get(C.string_at(name), [])
        self.deques[self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value][:] = rows
        return len(rows)


class Disk(Files):
    """The card as conftest's Files, with fputs and access: what LRCLIB's lyrics and the album job write."""
    def __init__(self, fw, files=None):
        super().__init__(fw, files)
        fw.mock('fw_fputs', lambda s, f, *_: self.open[f].__setitem__(2, self.open[f][2] + C.string_at(s)))
        fw.mock('fw_access', lambda path, mode, *_: 0 if C.string_at(path).decode() in self.files else -1)


def save(fw, name, tmp=b'/tmp/oslyric_1'):
    """ringnav_lyric_save as stock's accept calls it: the command it leaves, and its length."""
    cmd = block(fw, size=0x400)
    n = fw.fn('ringnav_lyric_save', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p, C.c_char_p)(cmd, 0x400, FMT, tmp, name)
    return n, C.string_at(cmd).decode()


def test_an_accepted_match_goes_beside_the_playing_song_and_its_hiding_copy_goes(fw, mcl, records):
    Queue(fw, mcl, [records.song(b'/m/X/song.flac'), records.song(b'/m/Y/song.mp3')], pos=1)  # both make song.lrc
    disk = Disk(fw, {'/tmp/oslyric_1': b'lyrics', '/mnt/mmc/.downpic/song.lrc': b'stock'})
    assert save(fw, b'song.lrc') == (1, ':')  # system() runs nothing
    assert disk.files == {'/tmp/oslyric_1': b'lyrics', '/m/Y/song.lrc': b'lyrics'}


def test_an_accepted_match_for_another_queued_song_goes_beside_it(fw, mcl, records):
    Queue(fw, mcl, [records.song(b'/m/a.flac'), records.song(b'/m/b.flac'), records.song(b'/m/c.flac')], pos=0)
    disk = Disk(fw, {'/tmp/oslyric_1': b'lyrics'})
    assert save(fw, b'c.lrc') == (1, ':') and disk.files['/m/c.lrc'] == b'lyrics'


@pytest.mark.parametrize('song,kind', [
    (dict(path=b'/m/song.flac', cue=(30, 0)), 0xf001), (dict(path=b'/m/song.flac', cue=(0, 90)), 0xf001),  # a CUE track
    (dict(path=b'/m/song.flac'), 0xf1), (dict(path=b'/m/song.flac'), 0xf2),                                # an online queue
    (dict(path=b'http://h/song.flac'), 0xf001), (dict(path=b'/m/song.flac', kind=4), 0xf001),              # no file
    (dict(path=b'/m/other.flac'), 0xf001),                                                                  # not queued
])
def test_what_isnt_a_queued_song_of_its_own_goes_where_stock_puts_it(fw, mcl, records, song, kind):
    Queue(fw, mcl, [records.song(**song)], kind=kind)
    disk = Disk(fw, {'/tmp/oslyric_1': b'lyrics', '/mnt/mmc/.downpic/song.lrc': b'stock'})
    assert save(fw, b'song.lrc') == (1, ':')
    assert disk.files == {'/tmp/oslyric_1': b'lyrics', '/mnt/mmc/.downpic/song.lrc': b'lyrics'}


def test_a_name_never_reaches_the_shell(fw, mcl, records):
    """Stock's command ran the name through system() as root (GHSA-qp92-mq7c-6r64)."""
    Queue(fw, mcl, [records.song(b'/m/song.flac')])
    disk = Disk(fw, {'/tmp/oslyric_1': b'lyrics'})
    name = b'x$(touch poc)`id`".lrc'
    assert save(fw, name) == (1, ':') and disk.files['/mnt/mmc/.downpic/' + name.decode()] == b'lyrics'
    assert save(fw, b'a' * 0x200) == (1, ':')  # too long for .downpic: nothing written, nothing run
    assert save(fw, b'../../../etc/x.lrc') == (1, ':') and set(disk.files) == {'/tmp/oslyric_1', '/mnt/mmc/.downpic/' + name.decode()}


def test_a_copy_that_fails_leaves_the_card_as_it_was(fw, mcl, records):
    Queue(fw, mcl, [records.song(b'/m/song.flac')])
    disk = Disk(fw, {'/mnt/mmc/.downpic/song.lrc': b'stock'})  # no lyrics file to copy
    assert save(fw, b'song.lrc') == (1, ':') and disk.files == {'/mnt/mmc/.downpic/song.lrc': b'stock'}


def test_an_accepted_lrclib_match_is_what_the_next_load_shows(fw, mcl, records):
    Queue(fw, mcl, [records.song(b'/m/Y/song.flac', title=b'Song', artist=b'Band')])
    disk = Disk(fw, {'/mnt/mmc/.downpic/song.lrc': b'stock'})
    Net(fw, {LRCLIB: results('[00:01.00] Lyrics : Someone\n[00:02.50] la la\n[00:04.00] la\n')})
    fw.mock('online_getkeyword_info', keyinfo(b'Song', b'', b'Band'))
    assert fw.fn('ringnav_lyric_search', C.c_int, C.c_char_p, C.c_char_p)(b'Song&Band', b'/tmp/oslyric_1') == 1
    assert save(fw, b'song.lrc') == (1, ':') and '/mnt/mmc/.downpic/song.lrc' not in disk.files
    assert fw.fn('ringnav_lrc_utf8', C.c_int, C.c_char_p)(b'/m/Y/song.lrc') == 1  # stock's probe: no GB18030 conversion
    # stock's reload: its .lrc parser opens the file through LYRIC_OPEN, then ours replaces its lines
    lines = []
    fw.mock('fw_deque_clear', lambda *_: lines.clear())
    fw.mock('fw__deque_push_back', lambda q, line, *_: lines.append((C.c_int.from_address(line).value,
                                                                     C.c_char_p.from_address(line + 8).value)))
    fw.mock('mclGetLyricSize', lambda *_: 2)
    reopen = fw.fn('ringnav_lrc_open', C.c_void_p, C.c_char_p, C.c_char_p)
    fw.mock('stock_lyric_trampoline', lambda *_: reopen(b'/m/Y/song.lrc', b'r') and 1)
    assert fw.fn('ringnav_lyric', C.c_int)() == 1
    assert lines == [(2, b'la la'), (4, b'la')]


@pytest.fixture
def more_rows():
    """Stock's PLAYMORE_ROWS, each ⋯ row's kind by position (docs/internals.md#album-lyrics), in conftest's
    map: put back after."""
    at, size = O['PLAYMORE_ROWS'], 4 * O['PLAYMORE_COUNT']
    saved = C.string_at(at, size)
    C.memmove(at, struct.pack('<8i', 2, 3, 4, 5, 6, 7, 0, 1), size)
    yield
    C.memmove(at, saved, size)


def widget(parent=None, h=0):
    w = C.create_string_buffer(0x100)
    struct.pack_into('<Q', w, O['W_PARENT'], C.addressof(parent) if parent is not None else 0)
    struct.pack_into('<i', w, O['W_H'], h)
    return w


class More:
    """Now Playing's ⋯ page as stock's init leaves it, scroll_view_more and its rows, and the item, button
    and label list_row makes for ours: the label's text, the restack and each handler are kept."""
    def __init__(self, fw, rows=8, view=True):
        self.fw, self.texts, self.on, self.restacked = fw, [], {}, []
        self.win, self.view = widget(), widget()
        self.item = widget(self.view)
        self.button = widget(self.item)
        self.label = widget(self.button, 70)
        a = C.addressof
        fw.mock('stock_playermore_trampoline', lambda *_: 0x55)
        fw.mock('widget_lookup', lambda w, name, *_:
                a(self.view) if view and w == a(self.win) and C.string_at(name) == b'scroll_view_more' else 0)
        fw.mock('widget_count_children', lambda w, *_: rows if w == a(self.view) else 0)
        fw.mock('list_item_create', lambda *_: a(self.item))
        fw.mock('button_create', lambda *_: a(self.button))
        fw.mock('hscroll_label_create', lambda *_: a(self.label))
        fw.mock('widget_on', lambda w, event, fn, ctx, *_: self.on.__setitem__((w, event), (fn, ctx)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.append(C.string_at(s).decode()) if w == a(self.label) else 0)
        fw.mock('widget_restack', lambda w, at, *_: self.restacked.append((w, at)))

    def open(self, win=True):
        return self.fw.fn('ringnav_playermore', C.c_int, C.c_void_p, C.c_void_p)(C.addressof(self.win) if win else None, None)

    def handler(self, w, event):
        fn, ctx = self.on[(C.addressof(w), event)]
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)

    def click(self):
        return self.handler(self.button, O['EVT_CLICK'])


class Run:
    """The job's thread, timer and toasts: pthread_create keeps the worker, which runs when the test says or
    at pthread_join, which waits for it; timer_add keeps the row's timer; a toast is stock's msginfo dialog."""
    def __init__(self, fw, wifi=3, starts=True):
        self.fw, self.toasts, self.timers, self.start, self.ran = fw, [], [], None, False
        self.al = fw.var('onlinematch__al', Al)
        fw.mock('get_wifisignal', lambda *_: wifi)
        fw.mock('fw_pthread_create', self.create if starts else lambda *_: 11)  # EAGAIN
        fw.mock('fw_pthread_join', lambda thread, *_: self.run())
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or 1)
        fw.mock('navigator_to_with_context', self.navigate)

    def create(self, thread, attr, start, arg, *_):
        C.c_ulong.from_address(thread).value = THREAD
        self.start, self.ran = start, False

    def run(self):
        if self.start and not self.ran:
            self.ran = True
            C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.start)(None)

    def navigate(self, name, info, *_):
        if C.string_at(name) == b'dialog/msginfo_dialog':
            self.toasts.append(C.string_at(info + 8).decode())  # after its kind and time

    def tick(self):
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[-1][0])(None)


def titles_of(url):
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['track_name'][0]


def titles(net):
    """The titles LRCLIB was asked for, in order."""
    return [titles_of(u) for u in net.urls]


def greatest_hits(fw, mcl, records, *extra):
    """The playing song's album as stock's library holds it: its songs out of order, a CUE track of its folder,
    another album of its name; the card holds them and stock's .downpic copies of two's lyrics."""
    album = dict(album=b'Greatest Hits', artist=b'A')
    two = records.song(b'/m/A/GH/02 Two.flac', title=b'Two', disc=1, track=2, **album)
    rows = [records.song(b'/m/B/GH/01 Else.flac', title=b'Else', album=b'Greatest Hits', artist=b'B'),
            records.song(b'/m/A/GH/CD2/01 Three.flac', title=b'Three', disc=2, track=1, **album),
            records.song(b'/m/A/GH/live.flac', title=b'Live', disc=1, track=9, cue=(10, 20), **album),
            two, records.song(b'/m/A/GH/01 One.flac', title=b'One', disc=1, track=1, **album), *extra]
    queue = Queue(fw, mcl, [two])
    queue.albums[b'Greatest Hits'] = rows
    return Disk(fw, {'/m/A/GH/01 One.flac': b'', '/m/A/GH/02 Two.flac': b'', '/m/A/GH/CD2/01 Three.flac': b'',
                     '/m/A/GH/live.flac': b'', '/m/B/GH/01 Else.flac': b'',
                     '/mnt/mmc/.downpic/01 One.lrc': b'stock', '/mnt/mmc/.downpic/02 Two.lrc': b'stock'})


def lrclib_answers(fw, **found):
    """LRCLIB with synced lyrics for the titles found names, and none for any other."""
    return Net(fw, {LRCLIB: (lambda url: results(found[titles_of(url)]) if titles_of(url) in found else b'[]',)})


def summed(fw, job, state=LJ_DONE, **counts):
    """The job ended in state with these counts (any not named 0) and said so once: album_note's toast for them, in
    al_finish's 96 bytes."""
    j = job.al.job
    assert (j.state, j.saved, j.none, j.had, j.failed) == (state, *(counts.get(k, 0) for k in ('saved', 'none', 'had', 'failed')))
    assert job.toasts == [note(fw, 96, state=state, **counts)]


@pytest.mark.parametrize('since', [0, 1])
def test_more_has_no_delete_for_a_playlists_song_played_from_its_cover(fw, more_rows, since):
    """A playlist's song played from its cover is a song list (0xf001), whose Delete takes it out of the library:
    Delete's row (kind 7, sixth) is hidden while that queue is the last loaded, and shows once another loads."""
    more, rows, hidden = More(fw), [widget() for _ in range(8)], []
    fw.mock('widget_get_child', lambda w, i, *_: C.addressof(rows[i]) if w == C.addressof(more.view) else 0)
    fw.mock('widget_set_visible', lambda w, on, *_: None if on else hidden.append(w))
    fw.var('playback_loads', C.c_uint).value = 7 + since
    fw.var('coverflow__cover_load', C.c_uint).value = 7  # coverflow.c's play, after its load
    more.open()
    assert hidden == ([] if since else [C.addressof(rows[5])])


def test_download_album_lyrics_from_the_more_page_to_the_card(fw, mcl, records, more_rows):
    disk = greatest_hits(fw, mcl, records)
    net = lrclib_answers(fw, One='[00:01.00] one\n', Three='[00:01.00] Mix : X\n[00:02.00] three\n')
    job, more = Run(fw), More(fw)
    assert more.open() == 0x55  # stock's
    assert len(more.texts) == 1 and more.restacked == [(C.addressof(more.item), 3)]  # its title, under the third row
    assert more.click() == 0 and job.timers[0][1] == 500
    assert (job.al.job.done, job.al.job.total) == (0, 3) and more.texts[-1] != more.texts[0]  # its progress instead
    assert len(fw.calls('fw_deque_destroy')) == 2  # the library's rows and the album's list, copied: both go
    assert job.tick() == 8 and fw.calls('reset_poweroptions_timer')[-1][1][:3] == (1, 1, 0)  # standby held off
    job.run()
    assert job.tick() == 0 and more.texts[-1] == more.texts[0]  # its title again
    summed(fw, job, saved=2, none=1)
    assert titles(net) == ['One', 'Two', 'Three']  # its own songs in disc and track order, the CUE track left out
    assert {urllib.parse.parse_qs(urllib.parse.urlsplit(u).query)['artist_name'][0] for u in net.urls} == {'A'}
    assert disk.files['/m/A/GH/01 One.lrc'] == BOM + b'[00:01.00] one\n'
    assert disk.files['/m/A/GH/CD2/01 Three.lrc'] == BOM + b'[00:02.00] three\n'
    assert '/m/A/GH/02 Two.lrc' not in disk.files and not [p for p in disk.files if p.endswith('.part')]
    assert '/mnt/mmc/.downpic/01 One.lrc' not in disk.files and disk.files['/mnt/mmc/.downpic/02 Two.lrc'] == b'stock'
    assert not job.al.thread and not job.al.tracks


def test_a_press_while_it_runs_cancels_it(fw, mcl, records, more_rows):
    greatest_hits(fw, mcl, records)
    net = lrclib_answers(fw)
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    progress = more.texts[-1]
    more.click()
    assert job.al.job.cancel == 1 and more.texts[-1] not in (more.texts[0], progress)  # it says it is cancelling
    job.run()
    assert job.tick() == 0 and not net.urls
    summed(fw, job, LJ_CANCELLED)


def test_a_press_after_the_job_ended_leaves_it_to_the_timer(fw, mcl, records, more_rows):
    greatest_hits(fw, mcl, records)
    lrclib_answers(fw)
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    job.run()
    more.click()
    assert len(fw.calls('fw_pthread_create')) == 1 and not job.al.job.cancel
    assert job.tick() == 0
    summed(fw, job, none=3)


def test_a_second_job_counts_afresh(fw, mcl, records, more_rows):
    greatest_hits(fw, mcl, records)
    lrclib_answers(fw, One='[00:01.00] one\n')
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    job.run()
    job.tick()
    more.click()
    j = job.al.job
    assert (j.done, j.total, j.saved, j.none, j.had, j.failed) == (0, 3, 0, 0, 0, 0) and more.texts[-1] != more.texts[0]
    job.run()
    job.tick()
    assert (j.saved, j.none, j.had) == (0, 2, 1)  # One's lyrics, saved the first time, beside it now
    assert job.toasts == [note(fw, 96, state=LJ_DONE, saved=1, none=2), note(fw, 96, state=LJ_DONE, none=2, had=1)]


def test_usb_storage_stops_the_job_quietly(fw, mcl, records, more_rows):
    greatest_hits(fw, mcl, records)
    net = lrclib_answers(fw)
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    fw.fn('album_lyrics_stop', None)()
    assert [c[1][0] for c in fw.calls('fw_pthread_join')] == [THREAD] and not net.urls  # cancelled before it asked
    assert not job.al.thread and not job.al.tracks and more.texts[-1] == more.texts[0]  # its title again
    assert job.tick() == 0 and job.toasts == []


def test_the_page_gone_the_job_runs_on_and_still_says_how_it_went(fw, mcl, records, more_rows):
    greatest_hits(fw, mcl, records)
    lrclib_answers(fw, One='[00:01.00] one\n')
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    shown = len(more.texts)
    assert more.handler(more.label, O['EVT_DESTROY']) == 0 and not job.al.label
    job.run()
    assert job.tick() == 0 and len(more.texts) == shown
    summed(fw, job, saved=1, none=2)


def test_no_wifi_no_job(fw, mcl, records, more_rows):
    greatest_hits(fw, mcl, records)
    job, more = Run(fw, wifi=0), More(fw)
    more.open()
    more.click()
    assert len(job.toasts) == 1 and not fw.calls('getMusicByAlbum') and not fw.calls('fw_pthread_create')


def test_a_thread_that_wont_start_says_so(fw, mcl, records, more_rows):
    greatest_hits(fw, mcl, records)
    job, more = Run(fw, starts=False), More(fw)
    more.open()
    more.click()
    assert len(job.toasts) == 1 and fw.calls('fw_pthread_create') and not job.timers
    assert (job.al.thread, job.al.tracks, job.al.running) == (0, None, 0) and len(more.texts) == 1  # still its title


def test_only_the_albums_songs_files_on_the_card_are_fetched(fw, mcl, records, more_rows):
    band = dict(album=b'Hits', album_artist=b'Band')  # one album by its album artist, whatever the folder
    playing = records.song(b'/m/1.flac', title=b'One', **band)
    queue = Queue(fw, mcl, [playing])
    queue.albums[b'Hits'] = [playing, records.song(b'/m/dir', kind=4, **band), records.song(b'http://h/2.flac', title=b'Two', **band),
                             records.song(b'/' + b'a' * 0x1ff, title=b'Long', **band), records.song(b'/m/3.flac', title=b'Cue', cue=(0, 9), **band),
                             records.song(b'/m/3.flac', title=b'Last', cue=(9, 0), **band)]  # a sheet's last track ends with its file
    Disk(fw, {'/m/1.flac': b''})
    net = lrclib_answers(fw)
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    job.run()
    assert job.al.job.total == 1 and titles(net) == ['One']


def test_a_cue_album_has_no_songs_of_its_own(fw, mcl, records, more_rows):
    cue = dict(album=b'Live', path=b'/m/live.flac')
    playing = records.song(title=b'One', cue=(0, 300), **cue)
    Queue(fw, mcl, [playing]).albums[b'Live'] = [playing, records.song(title=b'Two', cue=(300, 600), **cue)]
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    assert len(job.toasts) == 1 and not job.start and fw.calls('getMusicByAlbum')


def test_a_song_without_a_title_is_not_found_unasked(fw, mcl, records, more_rows):
    disk = greatest_hits(fw, mcl, records, records.song(b'/m/A/GH/03.flac', album=b'Greatest Hits', artist=b'A', disc=1, track=3))
    disk.files['/m/A/GH/03.flac'] = b''
    net = lrclib_answers(fw)
    job, more = Run(fw), More(fw)
    more.open()
    more.click()
    job.run()
    job.tick()
    assert titles(net) == ['One', 'Two', 'Three']
    summed(fw, job, none=4)


@pytest.mark.parametrize('case', ['no window', 'no view', 'not stock rows', 'online', 'no album', 'not a song', 'no search row'])
def test_the_row_only_for_a_playing_song_on_the_card_under_stocks_rows(fw, mcl, records, more_rows, case):
    song = records.song(b'/m/a.flac', album=b'' if case == 'no album' else b'Al', kind=4 if case == 'not a song' else 8)
    Queue(fw, mcl, [song], kind=0xf2 if case == 'online' else 0xf001)
    if case == 'no search row':
        C.memmove(O['PLAYMORE_ROWS'], struct.pack('<8i', 0, 1, 2, 3, 5, 6, 7, 8), 32)
    more = More(fw, rows=7 if case == 'not stock rows' else 8, view=case != 'no view')
    assert more.open(win=case != 'no window') == 0x55
    assert not fw.calls('list_item_create') and not more.texts and not more.on
