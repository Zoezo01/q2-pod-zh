"""patch/onlinematch.c: URLs built from a song's tags, LRCLIB's lyrics and their credits, accent folding,
Download Album Lyrics' job, and the cover searches' replies."""
import ctypes as C
import json
import random
import unicodedata
import urllib.parse

import pytest

from conftest import block

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
