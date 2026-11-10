"""xiph.c on its device path: an Ogg Opus or Vorbis comment header over OGG_TAGS_MAX, read a field at a time through
the card's file calls as stock's TagLib would read it, and on into toolsGetMusicInfo's info (ringnav_music_info).
test/unit/test_xiph.py checks the same reader built for the host; here it runs as the Q2 builds it."""
import ctypes as C
import random
import struct

import pytest

import host
from conftest import O, Files, block

CAP = 4 << 20  # OGG_TAGS_MAX
TEXT = 512  # XIPH_TEXT
TITLE, ARTIST, ALBUM, GENRE, COMPOSER, ALBUMARTIST, ALBUM_ARTIST = range(7)
OPUS_HEAD = b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0)
VORBIS_ID = b'\x01vorbis' + struct.pack('<IBIiiiB', 0, 2, 44100, 0, 128000, 0, 0xb8) + b'\x01'  # 44.1 kHz
BIG = b'COVERART=' + b'A' * (CAP + 1000)  # a field no one reads, over the cap alone


class Tags(C.Structure):
    _fields_ = [('text', (C.c_char * TEXT) * 7), ('used', C.c_ushort * 7), ('num', C.c_int * 7), ('has', C.c_ubyte * 7),
                *((k, C.c_int) for k in 'year track disc mqa kind rate'.split()), ('size', C.c_uint)]


def test_the_tags_mirror_is_xiph_h_s(library):
    laid = host.offsets(library.parent/'xiph.o', 'xiph_tags')
    assert laid == {name: getattr(Tags, name).offset for name, *_ in Tags._fields_}


def ogg(packets, segs=255, alone=(0, 1)):
    """Ogg pages of at most segs lacing values (no CRC: neither TagLib's read nor xiph.c checks one); a packet in
    alone starts a page of its own, any other follows on the page before."""
    pages, lacing, body, cont = [], [], [], 0

    def flush():
        nonlocal lacing, body, cont
        pages.append(b'OggS\0' + bytes([cont]) + bytes(8) + struct.pack('<III', 1, len(pages), 0) +
                     bytes([len(lacing)]) + bytes(lacing) + b''.join(body))
        cont, lacing, body = int(lacing[-1] == 255), [], []
    for k, packet in enumerate(packets):
        if k in alone and lacing:
            flush()
        at = 0
        for v in [255] * (len(packet) // 255) + [len(packet) % 255]:
            if len(lacing) == segs:
                flush()
            lacing.append(v)
            body.append(packet[at:at + v])
            at += v
    flush()
    return b''.join(pages)


def comments(magic, *items, vendor=b'q2 test', framing=b''):
    return magic + struct.pack('<I', len(vendor)) + vendor + struct.pack('<I', len(items)) + \
        b''.join(struct.pack('<I', len(c)) + c for c in items) + framing


def opus(*items):
    return ogg([OPUS_HEAD, comments(b'OpusTags', *items), b'\xfc' * 40])


def read(fw, data, name='/mnt/mmc/a.opus'):
    files = Files(fw, {name: data})
    t = Tags()
    big = fw.fn('ogg_tags', C.c_int, C.c_char_p, C.c_void_p)(name.encode(), C.byref(t))
    assert not files.open  # closed whatever it found
    return big, t


def text(t, k):
    return bytes(t.text[k]).split(b'\0')[0].decode()


def test_an_oversized_opus_header_is_read_here_field_by_field(fw):
    tags = comments(b'OpusTags', b'TITLE=Born Dead Buried Alive', BIG, b'artist=Hypocrisy', b'ALBUM=The Arrival',
                    b'GENRE=Death Metal', b'GENRE=Melodic', b'COMPOSER=Peter T\xc3\xa4gtgren', b'ALBUMARTIST=Hypocrisy',
                    b'ALBUM ARTIST=foobar', b'DATE=2004-03-22', b'TRACKNUMBER=1/11', b'DISCNUMBER=2')
    big, t = read(fw, ogg([OPUS_HEAD, tags, b'\xfc' * 40]))
    assert big == 1 and t.size == len(tags) and (t.kind, t.rate) == (1, 48000)  # Opus is always 48 kHz
    assert [text(t, k) for k in range(7)] == ['Born Dead Buried Alive', 'Hypocrisy', 'The Arrival', 'Death Metal / Melodic',
                                              'Peter Tägtgren', 'Hypocrisy', 'foobar']
    assert [t.used[k] for k in range(7)] == [len(text(t, k).encode()) for k in range(7)]
    assert (t.year, t.track, t.disc, t.mqa) == (2004, 1, 2, 0)  # String::toInt: the number a value starts with


@pytest.mark.parametrize('segs', (255, 17))  # full pages, and libogg's small ones: thousands of them
def test_a_header_over_the_cap_is_measured_and_read_across_its_pages(fw, segs):
    tags = comments(b'OpusTags', BIG, b'TITLE=After the picture')
    big, t = read(fw, ogg([OPUS_HEAD, tags, b'\xfc' * 40], segs=segs))
    assert big == 1 and t.size == len(tags) and text(t, TITLE) == 'After the picture'


def test_vorbis_takes_its_rate_from_its_identification_header(fw):
    tags = comments(b'\x03vorbis', BIG, b'TITLE=After', framing=b'\x01')
    setup = b'\x05vorbis' + bytes(3000)  # sharing the comments' last page, as libvorbis lays it out
    big, t = read(fw, ogg([VORBIS_ID, tags, setup], alone=(0,)), '/mnt/mmc/b.ogg')
    assert big == 1 and t.size == len(tags) and (t.kind, t.rate) == (2, 44100) and text(t, TITLE) == 'After'
    big, t = read(fw, ogg([b'\x01vorbiz' + VORBIS_ID[7:], tags, setup], alone=(0,)), '/mnt/mmc/b.oga')
    assert big == 1 and (t.kind, t.rate) == (2, 0) and text(t, TITLE) == 'After'  # no rate from another header


@pytest.mark.parametrize('items, want', [
    ((b'TRACKNUMBER=4', b'TRACKNUM=9', b'TRACKNUM=10', b'YEAR=1999', b'DISC= 3', b'DISCNUMBER=2', b'MQAENCODER=x'),
     (1999, 9, 3, 1)),  # TRACKNUM over TRACKNUMBER and DISC over DISCNUMBER whatever the order, each's first value
    ((b'DATE=2001', b'YEAR=1999', b'TRACKNUMBER=7', b'DISCNUMBER=4', b'MQAENCODER='), (2001, 7, 4, 0)),  # an empty one is none
    ((b'TITLE=x',), (0, 0, 1000, 0)),  # TagLib's own when none: no year, no track, disc 1000
    ((b'DATE=abc', b'TRACKNUMBER=-2', b'disc=0x10'), (0, -2, 0, 0)),  # base 10, as String::toInt
])
def test_the_numbers_are_taglib_s_first_values_by_its_precedence(fw, items, want):
    big, t = read(fw, opus(BIG, *items))
    assert big == 1 and (t.year, t.track, t.disc, t.mqa) == want


def test_a_text_field_joins_its_values_and_skips_what_taglib_would(fw):
    big, t = read(fw, opus(b'TITLE=', b'Title=One', b'TITLE=\xff bad', b'TITLE=\0gone', b'TITLE=Two', BIG, b'ARTIST',
                           b'=nokey', b'ARTIST=A\0B', b'artist=C', b'ALBUMARTIST=Two words', b'album artist=Three words'))
    assert big == 1 and text(t, TITLE) == 'One / Two' and text(t, ARTIST) == 'A / C'  # a value ends at a zero byte
    assert text(t, ALBUMARTIST) == 'Two words' and text(t, ALBUM_ARTIST) == 'Three words'  # two keys, kept apart
    assert text(t, ALBUM) == '' and t.used[ALBUM] == 0


@pytest.mark.parametrize('values, kept', [
    ([b'GENRE=' + 'é'.encode() * 400, b'GENRE=more'], 'é' * 255),  # cut at a whole character, then closed
    ([b'GENRE=' + '𝄞'.encode() * 300], '𝄞' * 127),  # past XIPH_VALUE too: the read's cut and the field's
    ([b'GENRE=' + b'a' * 507, b'GENRE=b'], 'a' * 507 + ' / b'),  # filled to the last byte
    ([b'GENRE=' + b'a' * 511, b'GENRE=b'], 'a' * 511),
])
def test_a_text_field_is_cut_at_a_whole_character_where_its_room_ends(fw, values, kept):
    big, t = read(fw, opus(BIG, *values))
    assert big == 1 and text(t, GENRE) == kept and len(kept.encode()) <= TEXT - 1


@pytest.mark.parametrize('key, field, char, kept', [('ARTIST', ARTIST, 'ü', 255), ('TITLE', TITLE, '€', 170)])
def test_a_value_longer_than_the_bytes_looked_at_stays_utf8_where_the_read_cuts_it(fw, key, field, char, kept):
    """The read takes XIPH_VALUE (1024) bytes of "KEY=" and the value, ending inside a character: cut before it, the
    value is still UTF-8, so it isn't dropped as invalid, and the field keeps what fits of it."""
    assert (1024 - len(key) - 1) % len(char.encode())  # the read's end is inside a character
    big, t = read(fw, opus(BIG, f'{key}='.encode() + (char * 600).encode(), b'ALBUM=next'))
    assert big == 1 and text(t, field) == char * kept and text(t, ALBUM) == 'next'


def test_a_count_past_the_end_reads_no_fields(fw):
    tags = comments(b'OpusTags', BIG, b'TITLE=Wraps')
    at = 8 + 4 + len(b'q2 test')
    big, t = read(fw, ogg([OPUS_HEAD, tags[:at] + struct.pack('<I', 0x40000001) + tags[at + 4:]]))  # 4 times it wraps to 4
    assert big == 1 and t.kind == 1 and text(t, TITLE) == ''


def test_a_comment_running_past_the_end_ends_the_list(fw):
    tags = comments(b'OpusTags', b'TITLE=Kept', BIG, b'ARTIST=Lost')
    at = tags.rindex(b'ARTIST=Lost') - 4
    big, t = read(fw, ogg([OPUS_HEAD, tags[:at] + struct.pack('<I', 0x7fffffff) + tags[at + 4:]]))
    assert big == 1 and text(t, TITLE) == 'Kept' and text(t, ARTIST) == ''


def test_a_vendor_string_past_the_end_reads_no_fields(fw):
    tags = comments(b'OpusTags', b'TITLE=x', BIG)
    big, t = read(fw, ogg([OPUS_HEAD, tags[:8] + struct.pack('<I', len(tags)) + tags[12:]]))
    assert big == 1 and t.kind == 1 and text(t, TITLE) == ''


@pytest.mark.parametrize('cut', ('mid-page', 'last page', 'broken page'))
def test_pages_that_end_before_the_header_does_give_no_tag(fw, cut):
    tags = comments(b'OpusTags', BIG, b'TITLE=Lost', BIG)
    whole = ogg([OPUS_HEAD, tags])
    pages = [whole.find(b'OggS')]
    while (at := whole.find(b'OggS', pages[-1] + 1)) > 0:
        pages.append(at)
    data = {'mid-page': whole[:len(whole) * 3 // 5], 'last page': whole[:pages[-1]],
            'broken page': whole[:pages[-3]] + b'Oggs' + whole[pages[-3] + 4:]}[cut]
    big, t = read(fw, data)
    assert big == 1 and t.kind == 0 and CAP < t.size < len(tags) and text(t, TITLE) == ''  # TagLib: no packet 1


def test_a_seek_the_card_refuses_ends_the_walk(fw):
    tags = comments(b'OpusTags', BIG, b'TITLE=Lost', BIG)
    data = ogg([OPUS_HEAD, tags])
    files = Files(fw, {'/mnt/mmc/a.opus': data})
    limit = [len(data)]

    def fseek(f, offset, whence, *_):
        target = C.c_int(offset).value + (files.open[f][1] if whence == 1 else 0)
        return -1 if target > limit[0] else files.fseek(f, offset, whence)
    fw.mock('fw_fseek', fseek)
    t = Tags()
    tags_of = fw.fn('ogg_tags', C.c_int, C.c_char_p, C.c_void_p)
    limit[0] = len(data) * 3 // 4  # past the cap: TagLib's read would find no packet 1
    assert tags_of(b'/mnt/mmc/a.opus', C.byref(t)) == 1 and t.kind == 0 and CAP < t.size < len(tags)
    limit[0] = CAP // 2  # short of it: what this walk saw is under the cap, so TagLib's read
    assert tags_of(b'/mnt/mmc/a.opus', C.byref(t)) == 0 and t.size < CAP // 2 and not files.open


def test_the_cap_itself_is_stock_s(fw):
    for size in (CAP, CAP + 1):
        head = comments(b'OpusTags', b'TITLE=x', b'COVERART=')
        tags = comments(b'OpusTags', b'TITLE=x', b'COVERART=' + b'A' * (size - len(head)))
        assert len(tags) == size
        big, t = read(fw, ogg([OPUS_HEAD, tags]))
        assert (big, t.size, t.kind) == (size > CAP, size, size > CAP)
        assert text(t, TITLE) == ('x' if size > CAP else '')  # under it, nothing is read here


def test_other_ogg_headers_and_other_files_stay_taglib_s(fw):
    flac_tags = b'\x84' + bytes(3) + comments(b'', BIG)  # Ogg FLAC's comment packet: a FLAC metadata block
    big, t = read(fw, ogg([b'\x7fFLAC' + bytes(46), flac_tags]), '/mnt/mmc/c.oga')
    assert big == 0 and t.kind == 0 and t.size == len(flac_tags)  # measured, but TagLib's to read
    big, t = read(fw, ogg([b'NotOpus!', comments(b'OpusTags', BIG, b'TITLE=x')]))
    assert big == 1 and t.kind == 0 and text(t, TITLE) == ''  # no OpusHead: TagLib's Opus::File is invalid
    big, t = read(fw, b'fLaC' + bytes(CAP + 9), '/mnt/mmc/d.ogg')
    assert (big, t.size, t.kind) == (0, 0, 0)


@pytest.mark.parametrize('name, opened', [('/a/b.OPUS', True), ('/a/b.Oga', True), ('/a/b.flac', False), ('/a/b', False),
                                          ('/a.ogg/b', False)])
def test_only_an_opus_ogg_or_oga_file_is_looked_at(fw, name, opened):
    files = Files(fw, {name: opus(BIG, b'TITLE=x')})
    assert fw.fn('ogg_tags', C.c_int, C.c_char_p, C.c_void_p)(name.encode(), None) == opened
    assert bool(fw.calls('fw_fopen')) == opened and not files.open


def latin1_copy(text):
    """libtag_c's Latin-1 copy (String::to8Bit(false)): each UTF-16 unit's low byte, up to the first zero one."""
    out = bytearray()
    for unit, in struct.iter_unpack('<H', text.encode('utf-16-le')):
        if not unit & 255:
            break
        out.append(unit & 255)
    return bytes(out)


def test_utf8_latin1_is_strict_utf8_its_latin1_range_and_copy_on_any_bytes(fw):
    """0 for what a strict decoder refuses (overlong forms, surrogates, past U+10FFFF, cut or stray bytes), 2 when
    every character is under U+0100, else 1; the copy as latin1_copy."""
    check = fw.fn('utf8_latin1', C.c_int, C.c_void_p, C.c_void_p)
    free = fw.fn('free', None, C.c_void_p)
    rng = random.Random(0x1a71)
    pieces = [b'a', b'Z', b'\x7f', b'\x80', b'\xbf', b'\xc0', b'\xc1', b'\xc2', b'\xc3\xa9', b'\xc4\x80', b'\xdf\xbf', b'\xe0',
              b'\xe0\x9f\xbf', b'\xe0\xa0\x80', b'\xe6\x97\xa5', b'\xed\x9f\xbf', b'\xed\xa0\x80', b'\xef\xbf\xbf', b'\xf0',
              b'\xf0\x8f\xbf\xbf', b'\xf0\x90\x80\x80', b'\xf0\x9d\x84\x9e', b'\xf4\x8f\xbf\xbf', b'\xf4\x90\x80\x80', b'\xf5',
              b'\xff', b'\xc3', b'\xe6\x97']
    seen = set()
    for _ in range(3000):
        data = b''.join(rng.choices(pieces, k=rng.randrange(0, 6)))
        try:
            decoded = data.decode('utf-8')
        except UnicodeDecodeError:
            decoded = None
        want = 0 if decoded is None else 2 if all(ord(c) < 0x100 for c in decoded) else 1
        s, out = block(fw, data), block(fw, size=2 * len(data) + 1)
        assert check(s, out) == want, data
        if decoded is not None:
            assert C.string_at(out) == latin1_copy(decoded), data
        seen.add(want)
        free(s)
        free(out)
    assert seen == {0, 1, 2}


# On into the library scan's tags (navigation.c ogg_music_info).

INFO, TITLE_AT, ARTIST_AT, ALBUM_AT, GENRE_AT, COMPOSER_AT = 0x698, 0x280, 0x100, 0, 0x200, 0x480
ALBUMARTIST_AT = O['MUSIC_INFO_ALBUM_ARTIST']


def fixer(fw):
    """Stock's toolsFixId3ErrorCode: what it was given (isLatin1, the Latin-1 copy, the UTF-8 text, the field), and
    the UTF-8 text as its answer."""
    given = []

    def fix(latin, latin1, utf8, out, field, *_):
        given.append((C.c_int(latin).value, C.string_at(latin1), C.string_at(utf8), C.c_int(field).value))
        C.memmove(out, C.string_at(utf8)[:0xff] + b'\0', min(len(C.string_at(utf8)), 0xff) + 1)
    fw.mock('toolsFixId3ErrorCode', fix)
    return given


def music_info(fw, data, a=1, name='/mnt/mmc/a.opus'):
    Files(fw, {name: data})
    info = block(fw, size=INFO)
    assert fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(a, info, name.encode()) == 1
    return info


def word(info, at):
    return C.c_int.from_address(info + at).value


def test_the_library_scan_takes_an_oversized_header_s_tags_from_here(fw):
    given = fixer(fw)
    info = music_info(fw, opus('TITLE=日本'.encode(), BIG, b'ARTIST=Peter T\xc3\xa4gtgren', b'ALBUM=Plain', b'GENRE=Death Metal',
                               b'GENRE=Melodic', b'COMPOSER=a\xc4\x80b', b'ALBUMARTIST=\xf0\x9d\x84\x9e', b'DATE=2004',
                               b'TRACKNUMBER=5', b'DISCNUMBER=2', b'MQAENCODER=x'))
    assert given == [(0, b'\xe5,', '日本'.encode(), 1),  # each UTF-16 unit's low byte (String::to8Bit)
                     (1, b'Peter T\xe4gtgren', b'Peter T\xc3\xa4gtgren', 2),  # all under U+0100: isLatin1
                     (1, b'Plain', b'Plain', 3), (1, b'Death Metal / Melodic', b'Death Metal / Melodic', 4),
                     (0, b'a', b'a\xc4\x80b', 5),  # U+0100's low byte ends the copy
                     (0, b'\x34\x1e', b'\xf0\x9d\x84\x9e', 6)]  # past U+FFFF, a surrogate pair's
    fields = [C.string_at(info + at) for at in (TITLE_AT, ARTIST_AT, ALBUM_AT, GENRE_AT, COMPOSER_AT, ALBUMARTIST_AT)]
    assert fields == [given[k][2] for k in range(6)]
    assert (word(info, 0x680), word(info, 0x684), word(info, 0x688)) == (2004, 2, 5)  # year, disc, track
    assert word(info, 0x68c) == 0x100 | 16 and word(info, 0x690) == 48000  # MQA; the audio properties, asked for
    assert not fw.calls('stock_music_info_trampoline')


def test_the_library_scan_without_audio_properties_and_a_genre_past_its_room(fw):
    fixer(fw)
    genre = b'Progressive Death Metal'
    info = music_info(fw, opus(BIG, *[b'GENRE=' + genre] * 8), a=0)
    fits = [b' / '.join([genre] * k) for k in range(1, 9) if len(b' / '.join([genre] * k)) <= 0x7f]
    assert len(fits[-1]) == 0x7f  # 5 entries fill the room to its last byte: a sixth won't fit
    assert C.string_at(info + GENRE_AT) == fits[-1]  # whole entries only, as many as fit
    assert word(info, 0x68c) == 0 and word(info, 0x690) == 0 and word(info, 0x684) == 1000  # no disc: TagLib's


def test_the_library_scan_leaves_a_header_taglib_would_find_no_tag_in(fw):
    given = fixer(fw)
    whole = opus(BIG, b'TITLE=x', BIG)
    info = music_info(fw, whole[:len(whole) * 3 // 5])
    assert given == [] and C.string_at(info, INFO) == bytes(INFO) and not fw.calls('stock_music_info_trampoline')


def test_the_library_scan_leaves_a_header_under_the_cap_to_stock(fw):
    given = fixer(fw)
    Files(fw, {'/mnt/mmc/a.opus': opus(b'TITLE=x')})
    info = block(fw, size=INFO)
    fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(1, info, b'/mnt/mmc/a.opus')
    assert given == [] and len(fw.calls('stock_music_info_trampoline')) == 1
