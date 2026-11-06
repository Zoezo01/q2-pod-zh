"""patch/xiph.c on the host: the Ogg page walk's measure of an Opus or Vorbis comment header, and the
fields its streaming reader takes from one over OGG_TAGS_MAX, as stock's TagLib XiphComment would.
Fixtures are written under ~/.cache (tmpfs /tmp is RAM) and removed after."""
import base64
import ctypes as C
import os
import pathlib
import shutil
import struct
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CAP = 4 << 20


class Tags(C.Structure):
    _fields_ = [('text', (C.c_char * 512) * 7), ('used', C.c_ushort * 7), ('num', C.c_int * 7),
                ('has', C.c_ubyte * 7), ('year', C.c_int), ('track', C.c_int), ('disc', C.c_int),
                ('mqa', C.c_int), ('kind', C.c_int), ('rate', C.c_int), ('size', C.c_uint)]


@pytest.fixture(scope='module')
def lib():
    cache = pathlib.Path(os.environ.get('XDG_CACHE_HOME') or pathlib.Path.home()/'.cache')
    cache.mkdir(parents=True, exist_ok=True)
    d = pathlib.Path(tempfile.mkdtemp(prefix='q2-xiph-', dir=cache))
    try:
        subprocess.run(['clang', '-shared', '-fPIC', '-DPEQ_HOST', '-Wall', '-Wextra', '-Werror',
                        str(ROOT/'patch/xiph.c'), '-o', str(d/'xiph.so')], check=True)
        so = C.CDLL(str(d/'xiph.so'))
        so.ogg_tags.argtypes = [C.c_char_p, C.POINTER(Tags)]
        so.utf8_latin1.argtypes = [C.c_char_p, C.c_char_p]
        so.dir = d
        yield so
    finally:
        shutil.rmtree(d)


def ogg(packets, segs=255, alone=(0, 1)):
    """Ogg pages of segs lacing values at most; each packet in alone starts its own page."""
    out, page, data, seq, cont = [], [], b'', 0, 0

    def flush(last=False):
        nonlocal page, data, seq, cont
        head = b'OggS\0' + bytes([cont | (2 if not seq else 0) | (4 if last else 0)]) + bytes(8)
        head += struct.pack('<III', 1, seq, 0) + bytes([len(page)]) + bytes(page)
        out.append(head + data)  # no CRC: neither TagLib nor xiph.c checks it
        cont = page[-1] == 255 if page else 0
        page, data, seq = [], b'', seq + 1

    for i, p in enumerate(packets):
        if i in alone and page: flush()
        n = len(p)
        lacing = [255] * (n // 255) + [n % 255]
        at = 0
        for v in lacing:
            if len(page) == segs: flush()
            page.append(v); data += p[at:at + v]; at += v
    flush(last=True)
    return b''.join(out)


def comments(magic, *items, vendor=b'q2 test', framing=b''):
    return magic + struct.pack('<I', len(vendor)) + vendor + struct.pack('<I', len(items)) + \
        b''.join(struct.pack('<I', len(c)) + c for c in items) + framing


def picture(n, key=b'METADATA_BLOCK_PICTURE'):
    block = struct.pack('>II', 3, 10) + b'image/jpeg' + struct.pack('>I', 0) + struct.pack('>IIIII', 1, 1, 24, 0, n) + bytes(n)
    return key + b'=' + base64.b64encode(block)


OPUS_HEAD = b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0)
VORBIS_ID = b'\x01vorbis' + struct.pack('<IBIiiiB', 0, 2, 44100, 0, 128000, 0, 0xb8) + b'\x01'
TEXT = (b'TITLE=Born Dead Buried Alive', b'artist=Hypocrisy', b'ALBUM=The Arrival', b'GENRE=Death Metal',
        b'GENRE=Melodic', b'COMPOSER=Peter T\xc3\xa4gtgren', b'ALBUMARTIST=Hypocrisy', b'DATE=2004-03-22',
        b'TRACKNUMBER=1/11', b'DISCNUMBER=1')


def read(lib, data, name='a.opus'):
    path = lib.dir/name
    path.write_bytes(data)
    t = Tags()
    try:
        return lib.ogg_tags(str(path).encode(), C.byref(t)), t
    finally:
        path.unlink()


def text(t, k): return bytes(t.text[k]).split(b'\0')[0].decode()


def test_cover_below_cap_stays_stocks(lib):
    tags = comments(b'OpusTags', *TEXT, picture(2_900_000))
    big, t = read(lib, ogg([OPUS_HEAD, tags, b'\xfc' * 40]))
    assert (big, t.size, t.kind) == (0, len(tags), 0)  # TagLib's read: nothing taken here
    for size in (CAP, CAP + 1):  # the cap itself is stock's
        tags = comments(b'OpusTags', b'TITLE=x', b'COVERART=' + b'A' * (size - len(comments(b'OpusTags', b'TITLE=x', b'COVERART='))))
        assert len(tags) == size
        big, t = read(lib, ogg([OPUS_HEAD, tags]))
        assert (big, t.size, t.kind) == (size > CAP, size, size > CAP)


@pytest.mark.parametrize('segs', [255, 17])  # full pages, and libogg's ~4 KB ones (thousands)
def test_twenty_mb_picture(lib, segs):
    tags = comments(b'OpusTags', picture(15_000_000), *TEXT)
    big, t = read(lib, ogg([OPUS_HEAD, tags, b'\xfc' * 40], segs=segs), 'b.OPUS')
    assert big == 1 and t.size == len(tags) > 20_000_000 and (t.kind, t.rate) == (1, 48000)
    assert [text(t, k) for k in range(7)] == ['Born Dead Buried Alive', 'Hypocrisy', 'The Arrival',
                                              'Death Metal / Melodic', 'Peter Tägtgren', 'Hypocrisy', '']
    assert (t.year, t.track, t.disc, t.mqa) == (2004, 1, 1, 0)


def test_two_pictures_and_vorbis(lib):
    tags = comments(b'\x03vorbis', picture(3_000_000), b'TITLE=After', picture(2_000_000, b'COVERART'),
                    b'ARTIST=Both', framing=b'\x01')
    setup = b'\x05vorbis' + bytes(3000)  # packet 2 shares packet 1's last page, as libvorbis lays it
    big, t = read(lib, ogg([VORBIS_ID, tags, setup], alone=(0,)), 'c.ogg')  # packet 1 after 0 on page 0
    assert big == 1 and t.size == len(tags) and (t.kind, t.rate) == (2, 44100)
    assert (text(t, 0), text(t, 1), t.year, t.track, t.disc) == ('After', 'Both', 0, 0, 1000)


def test_multi_values(lib):
    tags = comments(b'OpusTags', picture(5_000_000), b'TITLE=', b'Title=One', b'TITLE=\xff bad', b'TITLE=\0gone', b'TITLE=Two',
                    b'ARTIST', b'=nokey', b'ARTIST=A\0B', b'artist=C', b'ALBUM ARTIST=foobar', b'YEAR=1999', b'TRACKNUMBER=4',
                    b'TRACKNUM=9', b'DISCNUMBER=2', b'DISC= 3', b'MQAENCODER=x', b'GENRE=' + 'é'.encode() * 400)
    big, t = read(lib, ogg([OPUS_HEAD, tags]))
    assert big and text(t, 0) == 'One / Two' and text(t, 1) == 'A / C' and text(t, 6) == 'foobar'  # a value ends at a zero byte
    assert text(t, 3) == 'é' * 255 and t.used[3] == 512  # cut at a whole character, then closed
    assert (t.year, t.track, t.disc, t.mqa) == (1999, 9, 3, 1)


def test_cut_short(lib):
    tags = comments(b'OpusTags', picture(8_000_000), b'TITLE=Lost')
    whole = ogg([OPUS_HEAD, tags])
    big, t = read(lib, whole[:len(whole) // 2])  # truncated mid-packet
    assert big == 1 and t.kind == 0 and CAP < t.size < len(tags)
    pages = ogg([OPUS_HEAD, tags], segs=255)
    cut = pages.rfind(b'OggS')  # the last page gone: the packet never ends
    big, t = read(lib, pages[:cut])
    assert big == 1 and t.kind == 0 and t.size < len(tags)
    tags = comments(b'OpusTags', picture(6_000_000))
    big, t = read(lib, ogg([b'NotOpus!', tags]))  # no OpusHead: TagLib's Opus::File is invalid
    assert big == 1 and t.kind == 0


def test_not_ogg(lib):
    big, t = read(lib, b'fLaC' + bytes(9_000_000))
    assert (big, t.size, t.kind) == (0, 0, 0)
    big, t = read(lib, ogg([OPUS_HEAD, comments(b'OpusTags', picture(6_000_000))]), 'd.flac')
    assert big == 0  # another extension: never looked at


def test_latin1(lib):
    def latin1(s):
        out = C.create_string_buffer(64)
        return lib.utf8_latin1(s, out), out.value
    assert latin1('Café'.encode()) == (2, b'Caf\xe9')
    assert latin1('日本'.encode()) == (1, b'\xe5,')  # each UTF-16 unit's low byte
    assert latin1('aĀb'.encode()) == (1, b'a')  # U+0100's low byte ends it, as strdup
    assert latin1('𝄞'.encode()) == (1, b'\x34\x1e')  # a surrogate pair's
    assert latin1(b'\xc0\x80')[0] == latin1(b'\xed\xa0\x80')[0] == 0
