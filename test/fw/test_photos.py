"""photos.c (docs/internals.md#photos): file names, the EXIF orientation and its eight meanings, placement, the
cache worker, the albums, grid and viewer, and the decoded slots."""
import collections
import contextlib
import ctypes as C
import random
import struct

import pytest

from conftest import FNV_PRIME, O, Files, block, fnv1a

ROOT = '/mnt/mmc/Photos'
CACHE = '/mnt/mmc/.photos'
PH_BAD = 9
ALBUMS, GRID, VIEWER = range(3)
EVT_DESTROY, EVT_VALUE_CHANGED, EVT_CLICK, EVT_KEY_UP = O['EVT_DESTROY'], O['EVT_VALUE_CHANGED'], O['EVT_CLICK'], O['EVT_KEY_UP']
W_H, W_PARENT = O['W_H'], O['W_PARENT']
SLIDE_INDEX, SLIDE_SPACER, SLIDE_OFFSET, SCROLL_Y = O['SLIDE_INDEX'], O['SLIDE_SPACER'], O['SLIDE_OFFSET'], O['SCROLL_Y']
KEY_RETURN, EVENT_KEY = O['KEY_RETURN'], O['EVENT_KEY']
TILE_W, TILE_H, THUMB_W, THUMB_H, GRID_X = 93, 80, 85, 72, 1
GREY = 0xff333333
RGBA_AT = [(0, 1, 2, 3), (3, 2, 1, 0), (2, 1, 0, 3), (1, 2, 3, 0)]  # r, g, b, a's bytes: RGBA, ABGR, BGRA, ARGB


class Slot(C.Structure):
    _fields_ = [('bm', C.c_void_p), ('photo', C.c_int), ('w', C.c_int), ('h', C.c_int)]


class Ph(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in 'page albums grid view viewer slide info'.split()),
                ('root', C.c_char * 32), ('album', C.POINTER(C.c_char_p)), ('nalbums', C.c_int),
                ('path', C.POINTER(C.c_char_p)), ('key', C.POINTER(C.c_uint)), ('state', C.POINTER(C.c_ubyte)),
                *((n, C.c_int) for n in 'n screen target current info_on space cursor seen'.split()),
                ('thread', C.c_ulong), ('timer', C.c_uint), ('poll', C.c_uint), ('running', C.c_int),
                *((n, C.c_int) for n in 'done cancel want finished'.split()), ('tiles', Slot * 32), ('shots', Slot * 3)]


def ph(fw):
    return fw.var('photos__ph', Ph)


def key(path):
    """A photo's cache key: FNV-1a of its path and a separator multiply (coverflow.c fnv)."""
    return fnv1a(path.encode()) * FNV_PRIME & 0xffffffff


LIBC = C.CDLL(None)  # this file's own handle: its argtypes are its own
LIBC.mmap.restype, LIBC.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
LIBC.mprotect.argtypes = [C.c_void_p, C.c_size_t, C.c_int]
LIBC.munmap.argtypes = [C.c_void_p, C.c_size_t]
PAGE = 0x1000


@contextlib.contextmanager
def flush(data=b'', size=None):
    """data, or size zeroed bytes, ending where an unreadable page starts: a read or write one byte past them faults,
    with ASan or without, where a heap block's slack would let it pass."""
    size = len(data) if size is None else size
    span = -(-size // PAGE) * PAGE or PAGE
    base = LIBC.mmap(None, span + PAGE, 3, 0x22, -1, 0)  # read/write, MAP_PRIVATE | MAP_ANONYMOUS
    assert base and base != C.c_void_p(-1).value
    assert not LIBC.mprotect(base + span, PAGE, 0)
    C.memmove(base + span - size, data, len(data))
    try:
        yield base + span - size
    finally:
        LIBC.munmap(base, span + PAGE)


# File names.

@pytest.mark.parametrize('name, ok', [
    ('a.jpg', 1), ('a.JPG', 1), ('a.jpeg', 1), ('a.JpEg', 1), ('a.png', 1), ('a.PNG', 1), ('holiday.2024.jpg', 1),
    ('._a.jpg', 0), ('.hidden.png', 0), ('a.gif', 0), ('a.heic', 0), ('a.jpg.txt', 0), ('jpg', 0), ('a.jpgx', 0),
    ('a.', 0), ('a', 0), ('a.jp', 0), ('.jpg', 0),
])
def test_photo_file(fw, name, ok):
    assert fw.fn('photo_file', C.c_int, C.c_char_p)(name.encode()) == ok


# The EXIF orientation.

def segment(marker, body):
    return bytes([0xff, marker]) + struct.pack('>H', len(body) + 2) + body


def exif(entries, le=True, ifd=8, order=None, link=True):
    """An APP1 Exif body: a TIFF header in either byte order and IFD0's SHORT entries (tag, value), then (link) its
    offset to the next IFD."""
    e = '<' if le else '>'
    tiff = (order or (b'II' if le else b'MM')) + struct.pack(e + 'HI', 42, ifd) + b'\0' * (ifd - 8)
    tiff += struct.pack(e + 'H', len(entries))
    for tag, value in entries:
        tiff += struct.pack(e + 'HHIH', tag, 3, 1, value) + b'\0\0'  # a SHORT sits in the value's first half
    return b'Exif\0\0' + tiff + b'\0' * 4 * link


def far_ifd(entries):
    """Exif whose header points IFD0 at 4000, past its segment, with the entries where IFD0 usually is."""
    body = bytearray(exif(entries))
    struct.pack_into('<I', body, 6 + 4, 4000)
    return bytes(body)


JFIF = segment(0xe0, b'JFIF\0\x01\x02\0\0\x01\0\x01\0\0')
XMP = segment(0xe1, b'http://ns.adobe.com/xap/1.0/\0<x:xmpmeta/>')
SOS = b'\xff\xda\x00\x0c' + bytes(10)


def jpeg(*segments):
    return b'\xff\xd8' + b''.join(segments) + SOS + bytes(64) + b'\xff\xd9'


def orientation(fw, data, n=None):
    """photo_orientation over data's first n bytes, all of it by default, flush against an unreadable page."""
    n = len(data) if n is None else n
    with flush(data[:n]) as p:
        return fw.fn('photo_orientation', C.c_int, C.c_void_p, C.c_uint)(p, n)


@pytest.mark.parametrize('le', [True, False])
@pytest.mark.parametrize('o', range(1, 9))
def test_the_exif_orientation_in_either_byte_order(fw, o, le):
    assert orientation(fw, jpeg(JFIF, segment(0xe1, exif([(0x112, o)], le)))) == o


def test_the_orientation_is_any_entry_of_ifd0(fw):
    entries = [(0x10f, 7), (0x110, 7), (0x112, 6), (0x11a, 7)]
    assert orientation(fw, jpeg(segment(0xe1, exif(entries)))) == 6
    assert orientation(fw, jpeg(segment(0xe1, exif(entries, le=False, ifd=20)))) == 6  # IFD0 further on


def test_an_xmp_app1_before_the_exif_one_is_passed_over(fw):
    assert orientation(fw, jpeg(JFIF, XMP, segment(0xe1, exif([(0x112, 8)])))) == 8


@pytest.mark.parametrize('before', [
    segment(0xe0, b''),  # an empty segment: its length alone
    segment(0xe1, b'Exif\0X' + exif([(0x112, 3)])[6:]),  # not Exif's identifier, which ends in two NULs
    segment(0xe1, b'Exif\0\0II*\0\x08\0\0'),  # Exif's, too short for its TIFF header
    segment(0xfe, exif([(0x112, 3)])),  # Exif's bytes in a comment, not in APP1
])
def test_segments_that_aren_t_exif_are_passed_over(fw, before):
    assert orientation(fw, jpeg(JFIF, before, segment(0xe1, exif([(0x112, 6)])))) == 6


@pytest.mark.parametrize('first', [
    exif([(0x110, 6)]),  # IFD0 without the orientation
    b'Exif\0\0II*\0\x08\0\0\0',  # the TIFF header alone, its IFD0 past the segment
])
def test_the_first_exif_segment_is_the_photo_s(fw, first):
    """A later Exif APP1 (an editor's copy) isn't read: the first answers, upright when it has no orientation."""
    assert orientation(fw, jpeg(segment(0xe1, first), segment(0xe1, exif([(0x112, 6)])))) == 1


@pytest.mark.parametrize('le', [True, False])
@pytest.mark.parametrize('ifd', [8, 20, 300, 4000])
def test_ifd0_may_sit_anywhere_in_its_segment(fw, le, ifd):
    assert orientation(fw, jpeg(segment(0xe1, exif([(0x10f, 1), (0x112, 7)], le, ifd)))) == 7


@pytest.mark.parametrize('le', [True, False])
@pytest.mark.parametrize('decoy', [8, 9, 0x8008])
def test_an_ifd0_offset_of_64_kb_or_more_is_past_any_segment(fw, le, decoy):
    """The offset is a 32-bit LONG, 0x10008 here: an orientation IFD where its halves alone, run together or overlapped
    would point isn't IFD0."""
    body = bytearray(exif([(0x112, 6)], le, ifd=decoy))
    struct.pack_into('<I' if le else '>I', body, 6 + 4, 0x10008)
    assert orientation(fw, jpeg(segment(0xe1, bytes(body)))) == 1


def test_only_ifd0_s_count_of_entries_is_read(fw):
    body = bytearray(exif([(0x10f, 1), (0x112, 6)]))
    struct.pack_into('<H', body, 6 + 8, 1)  # one entry: the orientation after it is something else's bytes
    assert orientation(fw, jpeg(segment(0xe1, bytes(body)))) == 1


def test_an_entry_counts_only_whole_inside_its_segment_and_the_head(fw):
    tiff = exif([(0x112, 6)], link=False)  # the segment ends with IFD0's entry
    whole = b'\xff\xd8' + segment(0xe1, tiff)
    short = whole[:4] + struct.pack('>H', len(tiff) + 1) + whole[6:]  # its length a byte short of the entry
    after = SOS + bytes(64) + b'\xff\xd9'
    assert orientation(fw, whole + after) == orientation(fw, whole) == 6
    assert orientation(fw, whole, len(whole) - 1) == 1  # the head cut a byte into the entry
    assert orientation(fw, short + after) == orientation(fw, short) == 1  # the entry's last byte past the segment


@pytest.mark.parametrize('data', [
    jpeg(JFIF),  # no EXIF
    jpeg(segment(0xe1, exif([(0x110, 6)]))),  # no orientation
    jpeg(segment(0xe1, exif([(0x112, 0)]))), jpeg(segment(0xe1, exif([(0x112, 9)]))),
    jpeg(segment(0xe1, exif([(0x112, 0xffff)]))),  # out of 1-8
    jpeg(segment(0xe1, exif([(0x112, 6)], order=b'IM'))), jpeg(segment(0xe1, exif([(0x112, 6)], order=b'XX'))),
    jpeg(segment(0xe1, exif([(0x112, 6)], le=False, order=b'XX'))),  # neither order, whichever it would read as
    jpeg(segment(0xe1, far_ifd([(0x112, 6)]))),  # IFD0 past the segment
    b'\x00\xd8' + segment(0xe1, exif([(0x112, 6)])), b'\xff\x00' + segment(0xe1, exif([(0x112, 6)])),  # no SOI
    b'\xff\xd8\0\0\0\x02' + segment(0xe1, exif([(0x112, 6)])),  # after the SOI, bytes that aren't a marker
    b'\xff\xd8' + SOS + segment(0xe1, exif([(0x112, 6)])),  # after the image data
    b'\xff\xd8\xff\xe1\x00\x01' + exif([(0x112, 6)]),  # a length under 2
    b'\x89PNG\r\n\x1a\n' + bytes(40), b'', b'\xff', b'\xff\xd8', b'\xff\xd8\xff',
])
def test_no_orientation_reads_as_upright(fw, data):
    assert orientation(fw, data) == 1


def test_a_head_cut_short_has_the_orientation_only_with_its_whole_entry(fw):
    data = jpeg(JFIF, segment(0xe1, exif([(0x10f, 1), (0x112, 3)])))
    end = len(b'\xff\xd8') + len(JFIF) + 4 + 6 + 8 + 2 + 2 * 12  # through IFD0's second entry
    for n in range(len(data) + 1):
        assert orientation(fw, data, n) == (3 if n >= end else 1), n  # and no byte past n read


def test_photo_orientation_hostile_sweep(fw):
    """Damaged headers: any answer is 1-8 and nothing past the bytes given is read."""
    rng = random.Random(0x0e1f)
    good = jpeg(JFIF, XMP, segment(0xe1, exif([(0x10f, 1), (0x112, 6)])))
    for _ in range(3000):
        data = bytearray(good[:rng.randrange(len(good) + 1)])
        for _ in range(rng.randrange(1, 6)):
            if data:
                data[rng.randrange(len(data))] = rng.randrange(256)
        assert 1 <= orientation(fw, bytes(data)) <= 8


# Turning a decoded copy upright.

def shown(src, o):
    """The rows of src (a list of rows) as EXIF orientation o shows it (CIPA DC-008)."""
    t = [list(col) for col in zip(*src)]  # the transpose: 5
    return {1: src, 2: [r[::-1] for r in src], 3: [r[::-1] for r in src[::-1]], 4: src[::-1],
            5: t, 6: [r[::-1] for r in t], 7: [r[::-1] for r in t[::-1]], 8: t[::-1]}[o]


def orient(fw, pixels, o, fmt=0, box=64, pad=0):
    """pixels (rows of (r, g, b, a)) laid out in format fmt, stride padded, through photo_orient: the box's rows
    as (r, g, b) and the shown size."""
    h, w = len(pixels), len(pixels[0])
    stride = (w + pad) * 4
    raw = bytearray(stride * h)
    for y, row in enumerate(pixels):
        for x, px in enumerate(row):
            for k in range(4):
                raw[y * stride + x * 4 + RGBA_AT[fmt][k]] = px[k]
    size = (C.c_int * 2)()
    at = (C.c_ubyte * 4)(*RGBA_AT[fmt])
    with flush(bytes(raw)) as src, flush(size=box * box * 4) as dst:  # a read or a write past either faults
        fw.fn('photo_orient', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_int,
              C.c_void_p)(src, w, h, stride, at, o, dst, box, size)
        out = (C.c_uint * (box * box)).from_address(dst)
        rows = [[(v & 255, v >> 8 & 255, v >> 16 & 255, v >> 24) for v in out[y * box:y * box + size[0]]]
                for y in range(size[1])]
    return rows, tuple(size)


def picture(w, h, seed):
    rng = random.Random(seed)
    return [[(rng.randrange(256), rng.randrange(256), rng.randrange(256), 255) for _ in range(w)] for _ in range(h)]


@pytest.mark.parametrize('o', range(1, 9))
def test_each_orientation_shows_the_photo_as_exif_means(fw, o):
    src = picture(5, 3, o)
    rows, size = orient(fw, src, o, pad=2)
    want = shown(src, o)
    assert size == (len(want[0]), len(want)) == ((3, 5) if o >= 5 else (5, 3))
    assert rows == [[(r, g, b, 255) for r, g, b, _ in row] for row in want]


@pytest.mark.parametrize('fmt', range(4))
def test_each_channel_order_reads_red_green_blue(fw, fmt):
    src = [[(200, 100, 50, 255), (1, 2, 3, 255)]]
    rows, _ = orient(fw, src, 1, fmt)
    assert rows == [[(200, 100, 50, 255), (1, 2, 3, 255)]]


def test_alpha_is_over_black_and_the_slot_is_opaque(fw):
    src = [[(255, 128, 10, 0), (255, 128, 10, 128), (255, 128, 10, 255), (90, 60, 30, 51), (255, 255, 255, 254)]]
    rows, _ = orient(fw, src, 1, fmt=2)
    assert rows == [[(0, 0, 0, 255), (128, 64, 5, 255), (255, 128, 10, 255), (18, 12, 6, 255),
                     (254, 254, 254, 255)]]  # nearly opaque is still a 255th over black


@pytest.mark.parametrize('o', [1, 6])
def test_a_copy_bigger_than_the_box_is_cropped_to_it(fw, o):
    src = picture(9, 7, 3)
    rows, size = orient(fw, src, o, box=4)
    assert size == (4, 4) and rows == [[(r, g, b, 255) for r, g, b, _ in row[:4]] for row in shown(src, o)[:4]]


# Placement: fitted, centred, never enlarged.

@pytest.mark.parametrize('w, h, want', [
    (375, 290, (0, 0, 375, 290)), (750, 580, (0, 0, 375, 290)),  # the screen's shape
    (100, 50, (137, 120, 100, 50)),  # small: centred, as it is
    (1000, 100, (0, 126, 375, 37)), (300, 600, (115, 0, 145, 290)),  # wide, tall
    (1, 10000, (187, 0, 1, 290)), (10000, 1, (0, 145, 375, 1)),  # a sliver keeps a pixel
])
def test_photo_place(fw, w, h, want):
    r = (C.c_int * 4)()
    fw.fn('photo_place', None, *[C.c_int] * 6, C.c_void_p)(w, h, 0, 0, 375, 290, r)
    assert tuple(r) == want


def test_photo_place_any_size_fits_its_box_and_keeps_its_shape(fw):
    place, rng = fw.fn('photo_place', None, *[C.c_int] * 6, C.c_void_p), random.Random(7)
    r = (C.c_int * 4)()
    for _ in range(3000):
        w, h, x, y, bw, bh = (rng.randrange(1, 5000), rng.randrange(1, 5000), rng.randrange(-50, 50),
                              rng.randrange(-50, 50), rng.randrange(1, 400), rng.randrange(1, 400))
        place(w, h, x, y, bw, bh, r)
        rx, ry, rw, rh = r
        assert 1 <= rw <= min(w, bw) and 1 <= rh <= min(h, bh)  # inside the box, never enlarged
        # centred; a side that fits to under a pixel is centred at 0 and drawn 1 wide
        assert abs(rx - x - (bw - rw) // 2) <= (rw == 1) and abs(ry - y - (bh - rh) // 2) <= (rh == 1)
        if (rw, rh) != (w, h) and rw > 8 and rh > 8:
            assert abs(rw / rh - w / h) <= w / h * (2 / min(rw, rh))  # its shape, to a pixel's rounding


def test_by_string_is_strcmp_s_order(fw):
    words = [b'b', b'a', b'B', b'ab', b'', b'a b']
    arr = (C.c_char_p * len(words))(*words)
    cmp = fw.fn('by_string', C.c_int, C.c_void_p, C.c_void_p)
    for i in range(len(words)):
        for j in range(len(words)):
            got = cmp(C.addressof(arr) + 8 * i, C.addressof(arr) + 8 * j)
            assert (got > 0) - (got < 0) == (words[i] > words[j]) - (words[i] < words[j])


@pytest.mark.parametrize('current, failed, s, differs', [
    (b'Loading', 0, b'Loading', 0), (b'Loading', 0, b'Loading\xe2\x80\xa6', 1), (b'', 0, b'', 0),
    (b'x', 1, b'x', 1),  # stock wrote nothing: it didn't fit, so it differs
])
def test_text_differs(fw, current, failed, s, differs):
    def get(w, out, n, *_):
        C.memmove(out, current + b'\0', len(current) + 1)
        return failed
    fw.mock('widget_get_text_utf8', get)
    assert fw.fn('text_differs', C.c_int, C.c_void_p, C.c_char_p)(0x40, s) == differs


# The cache.

class Card(Files):
    """The card: files by path, their folders (and empty ones, the cache's unless cache is off), access, mkdir, opendir
    and readdir in an order that isn't the name's, statfs's free space; ffmpeg can't run here (fork refused)."""
    def __init__(self, fw, files=None, folders=(), free_mb=1024, typed=True, cache=True):
        super().__init__(fw, dict(files or {}))
        self.folders, self.free_mb, self.typed, self.open_dirs, self.ents = set(folders), free_mb, typed, {}, []
        if cache:
            self.folders.add(CACHE)
        self.asked = []  # access's (path, mode)s
        self.of_dir, self.reads = {}, collections.Counter()  # each folder's readdir calls
        fw.mock('fw_access', self.access)
        fw.mock('fw_mkdir', lambda p, *_: self.folders.add(C.string_at(p).decode()))
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.open_dirs.pop(d) and 0)
        fw.mock('fw_statfs', self.statfs)
        fw.mock('fw_fork', lambda *_: -1)

    def access(self, p, mode, *_):
        self.asked.append((C.string_at(p).decode(), mode))
        return 0 if self.exists(self.asked[-1][0]) else -1

    def exists(self, path):
        return path in self.files or self.isdir(path)

    def isdir(self, path):
        return path in self.folders or any(p.startswith(path + '/') for p in [*self.files, *self.folders])

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        if not self.isdir(path):
            return 0
        names = {}
        for p in [*self.files, *self.folders]:
            if p.startswith(path + '/'):
                first, _, rest = p[len(path) + 1:].partition('/')
                names[first] = 4 if rest or p in self.folders else 8
        handle = 0x3000 + len(self.open_dirs) + len(self.ents)
        self.open_dirs[handle] = sorted(names.items(), reverse=True)
        self.of_dir[handle] = path
        return handle

    def readdir(self, d, *_):
        self.reads[self.of_dir[d]] += 1
        if not self.open_dirs[d]:
            return 0
        name, kind = self.open_dirs[d].pop(0)
        ent = C.create_string_buffer(268)  # struct dirent: d_type at 10, d_name at 11
        struct.pack_into('B', ent, 10, kind if self.typed else 0)
        C.memmove(C.addressof(ent) + 11, name.encode() + b'\0', len(name.encode()) + 1)
        self.ents.append(ent)
        return C.addressof(ent)

    def statfs(self, path, fs, *_):
        C.c_uint.from_address(fs + 4).value = 4096
        C.c_uint.from_address(fs + 28).value = self.free_mb * 256
        return 0


class Thumbs:
    """Stock's toolsThumbSpecCover: (src, dst, w, h) each call; a decodable source gets a copy at dst, if its folder
    is there."""
    def __init__(self, fw, card, bad=()):
        self.card, self.bad, self.made = card, set(bad), []
        fw.mock('toolsThumbSpecCover', self.thumb)

    def thumb(self, src, dst, w, h, *_):
        src, dst = C.string_at(src).decode(), C.string_at(dst).decode()
        self.made.append((src, dst, w, h))
        if src in self.bad or src not in self.card.files or not self.card.isdir(dst.rpartition('/')[0]):
            return 0
        self.card.files[dst] = b'jpeg %dx%d' % (w, h)
        return 1


def head(o):
    return jpeg(JFIF, segment(0xe1, exif([(0x112, o)])))


def worker_state(fw, paths, space=1):
    """ph as to_grid leaves it for the worker, its strings and arrays the C's own (drop_photos frees them)."""
    p = ph(fw)
    arr = block(fw, size=8 * (len(paths) + 1))
    for i, path in enumerate(paths):
        C.c_void_p.from_address(arr + 8 * i).value = block(fw, path.encode())
    keys = block(fw, size=4 * (len(paths) + 1))
    for i, path in enumerate(paths):
        C.c_uint.from_address(keys + 4 * i).value = key(path)
    p.path = C.cast(arr, C.POINTER(C.c_char_p))
    p.key = C.cast(keys, C.POINTER(C.c_uint))
    p.state = C.cast(block(fw, size=len(paths) + 1), C.POINTER(C.c_ubyte))
    p.n, p.space, p.want = len(paths), space, -1
    return p


def cached(path, suffix='.jpg'):
    return f'{CACHE}/{key(path):08x}{suffix}'


def build(fw, i=0):
    return fw.fn('photos__build', C.c_int, C.c_int)(i)


@pytest.mark.parametrize('o', range(1, 9))
def test_a_new_photo_gets_its_screen_copy_then_a_thumbnail_from_it(fw, o):
    shot, small = ((290, 375), (72, 85)) if o >= 5 else ((375, 290), (85, 72))  # 5-8 turn: the sides swap
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(o)})
    thumbs = Thumbs(fw, card)
    worker_state(fw, [path])
    assert build(fw) == o
    tmp = cached(path, '.tmp')
    assert thumbs.made == [(path, tmp, *shot), (cached(path), tmp, *small)]
    assert card.files[cached(path)] == b'jpeg %dx%d' % shot and card.files[cached(path, 't.jpg')] == b'jpeg %dx%d' % small
    assert tmp not in card.files and card.open == {}  # the photo read and closed
    assert {mode for _, mode in card.asked} == {0}  # whether each exists, F_OK


@pytest.mark.parametrize('end, o', [(4096, 6), (4097, 1)])
def test_the_orientation_is_read_from_the_first_4_kb(fw, end, o):
    """An IFD0 entry ending at the 4096th byte counts; a byte further it's past what build reads."""
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: b'\xff\xd8' + segment(0xe1, exif([(0x112, 6)], ifd=end - 26)) + SOS + bytes(64)})
    Thumbs(fw, card)
    worker_state(fw, [path])
    assert build(fw) == o


def test_a_cached_photo_is_only_read_for_its_orientation(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(6), cached(path): b'x', cached(path, 't.jpg'): b'y'})
    thumbs = Thumbs(fw, card)
    worker_state(fw, [path])
    assert build(fw) == 6 and thumbs.made == []


def test_a_cached_copy_without_its_thumbnail_gets_one(fw):
    path = f'{ROOT}/a.png'
    card = Card(fw, {path: b'\x89PNG', cached(path): b'x'})
    thumbs = Thumbs(fw, card)
    worker_state(fw, [path])
    assert build(fw) == 1 and thumbs.made == [(cached(path), cached(path, '.tmp'), 85, 72)]


def test_a_photo_marked_bad_is_not_tried_again(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1), cached(path, '.bad'): b''})
    thumbs = Thumbs(fw, card)
    worker_state(fw, [path])
    assert build(fw) == PH_BAD and thumbs.made == []


def ffmpeg(fw, status):
    """ffmpeg starts and ends with status: 1 << 8 its own failure, 9 killed."""
    fw.mock('fw_fork', lambda *_: 1234)

    def wait(pid, out, *_):
        C.c_int.from_address(out).value = status
        return pid
    fw.mock('fw_waitpid', wait)


def test_a_photo_nothing_decodes_gets_the_bad_marker(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1)})
    Thumbs(fw, card, bad={path})
    ffmpeg(fw, 1 << 8)
    worker_state(fw, [path])
    assert build(fw) == PH_BAD
    assert card.files[cached(path, '.bad')] == b'' and cached(path) not in card.files and card.open == {}
    assert fw.calls('fw_fork')  # ffmpeg was the fallback
    assert (path, 0) in card.asked and {m for p, m in card.asked if p.startswith(CACHE)} == {0}  # still there? F_OK


@pytest.mark.parametrize('killed', (1, 0))
def test_a_photo_whose_ffmpeg_was_killed_or_never_started_gets_no_marker(fw, killed):
    """ffmpeg's child is the OOM killer's pick when memory is short (#216), and a big picture's only decoder, or its
    fork is refused: the photo may be fine, so the next open tries it again."""
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1)})
    Thumbs(fw, card, bad={path})
    if killed:
        ffmpeg(fw, 9)
    worker_state(fw, [path])
    assert build(fw) == PH_BAD and fw.calls('fw_fork') and cached(path, '.bad') not in card.files


def test_a_photo_gone_mid_build_gets_no_marker(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1)})
    def vanish(src, dst, *_):
        card.files.pop(path)
        return 0
    fw.mock('toolsThumbSpecCover', vanish)
    worker_state(fw, [path])
    assert build(fw) == PH_BAD and cached(path, '.bad') not in card.files


def test_a_photo_that_won_t_open_is_bad_without_a_marker(fw):
    card = Card(fw)
    thumbs = Thumbs(fw, card)
    worker_state(fw, [f'{ROOT}/gone.jpg'])
    assert build(fw) == PH_BAD and thumbs.made == [] and card.files == {}


def test_without_space_nothing_new_is_written(fw):
    path, other = f'{ROOT}/a.jpg', f'{ROOT}/b.jpg'
    card = Card(fw, {path: head(1), other: head(1), cached(other): b'x'})
    thumbs = Thumbs(fw, card)
    worker_state(fw, [path, other], space=0)
    assert build(fw, 0) == PH_BAD  # no copy to show
    assert build(fw, 1) == 1  # its copy shows; the tile stays grey
    assert thumbs.made == [] and len(card.files) == 3


def test_a_failed_thumbnail_leaves_the_photo_viewable(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1), cached(path): b'x'})
    Thumbs(fw, card, bad={cached(path)})
    worker_state(fw, [path])
    assert build(fw) == 1 and cached(path, 't.jpg') not in card.files and cached(path, '.tmp') not in card.files


def test_a_thumbnail_that_won_t_rename_leaves_no_tmp(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1), cached(path): b'x'})
    Thumbs(fw, card)
    card.fail_rename = True
    worker_state(fw, [path])
    assert build(fw) == 1 and cached(path, '.tmp') not in card.files and cached(path, 't.jpg') not in card.files


def test_a_failed_rename_is_a_failed_copy(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1)})
    Thumbs(fw, card)
    card.fail_rename = True
    worker_state(fw, [path])
    assert build(fw) == PH_BAD and cached(path, '.tmp') not in card.files and cached(path, '.bad') in card.files


def next_jobs(fw, n, want, ready=()):
    p = worker_state(fw, [f'{ROOT}/{i}.jpg' for i in range(n)])
    p.want = want
    for i in ready:
        p.state[i] = 1
    order = []
    while (i := fw.fn('photos__next_job', C.c_int)()) >= 0:
        order.append(i)
        p.state[i] = 1
    return order


@pytest.mark.parametrize('n, want, ready, order', [
    (5, -1, (), [0, 1, 2, 3, 4]),  # the grid alone: in order
    (6, 3, (), [3, 4, 2, 0, 1, 5]),  # the viewer's, the next, the previous, then the rest
    (4, 0, (), [0, 1, 2, 3]), (4, 3, (), [3, 2, 0, 1]),  # at either end
    (6, 3, (3, 4), [2, 0, 1, 5]), (3, -1, (0, 1, 2), []),
])
def test_the_worker_s_order(fw, n, want, ready, order):
    assert next_jobs(fw, n, want, ready) == order


# The page: albums, grid, viewer.

class Widgets:
    """Each AWTK constructor a fresh zeroed widget with its box and parent; window_create the page. Their texts, names,
    moves and children, which destroy_children takes away; an int prop reads its default, as none is set here."""
    def __init__(self, fw):
        self.fw, self.blocks, self.made, self.at, self.gone = fw, [], [], {}, set()
        for name in ('window_create', 'scroll_view_create', 'list_item_create', 'image_create', 'hscroll_label_create',
                     'list_view_create', 'button_create'):
            fw.mock(name, lambda parent, x, y, w, h, *_, kind=name: self.new(kind, parent, x, y, w, h))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.new(
            C.string_at(kind).decode(), parent, x, y, w, 320))
        self.texts, self.names = {}, {}
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_set_name', lambda w, s, *_: self.names.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_move_resize', self.move)
        fw.mock('widget_get_child', lambda w, i, *_: (self.children(w)[i:i + 1] or [0])[0])
        fw.mock('widget_destroy_children', lambda w, *_: self.gone.update(self.below(w)))
        fw.mock('widget_get_prop_int', lambda w, name, default, *_: default)

    def new(self, kind, parent, x, y, w, h):
        self.blocks.append(C.create_string_buffer(0x200))
        struct.pack_into('<4i', self.blocks[-1], 0, *(C.c_int(v).value for v in (x, y, w, h)))
        struct.pack_into('<Q', self.blocks[-1], W_PARENT, parent)
        self.made.append((kind, C.addressof(self.blocks[-1]), parent))
        self.at[C.addressof(self.blocks[-1])] = self.blocks[-1]
        return C.addressof(self.blocks[-1])

    def get_text(self, w, out, n, *_):
        s = self.texts.get(w, '').encode() + b'\0'
        if len(s) > n:
            return 1  # stock writes nothing when it doesn't fit
        C.memmove(out, s, len(s))
        return 0

    def move(self, w, x, y, width, height, *_):
        if w in self.at:
            struct.pack_into('<4i', self.at[w], 0, *(C.c_int(v).value for v in (x, y, width, height)))

    def of(self, kind, parent=None):
        return [w for k, w, p in self.made if k == kind and (parent is None or p == parent)]

    def kind(self, w):
        return next(k for k, x, _ in self.made if x == w)

    def children(self, parent):
        """parent's children still there, in the order they were made."""
        return [w for _, w, p in self.made if p == parent and w not in self.gone]

    def below(self, parent):
        out, front = set(), {parent}
        while front:
            front = {w for _, w, p in self.made if p in front} - out
            out |= front
        return out

    def labels(self, parent):
        """The texts of parent's labels still there, in order."""
        return [self.texts.get(w) for w in self.children(parent) if self.kind(w) == 'hscroll_label_create']


def box(w):
    return struct.unpack_from('<4i', (C.c_char * 16).from_address(w))


class Page:
    """photos_open on root: the widgets, the timers (fn, ms) and the worker's thread, which a test runs. Timer ids
    count from 1, after any foreign timers someone else holds."""
    def __init__(self, fw, card, root=ROOT, thread=True, foreign=0):
        self.fw, self.card = fw, card
        self.w = Widgets(fw)
        self.timers, self.workers = [(0, 0)] * foreign, []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.start(t, fn) if thread else 11)
        fw.fn('photos_open', None, C.c_char_p)(root.encode())
        self.ph = ph(fw)

    def start(self, t, fn):
        C.c_ulong.from_address(t).value = 0x7e57
        self.workers.append(fn)
        return 0

    def run_worker(self):
        assert C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.workers[-1])(0) is None

    def fire(self, name=None):
        """The last timer's function, or the last of photos.c's static name, as the next loop pass runs it."""
        at = C.cast(getattr(self.fw.lib, f'photos__{name}'), C.c_void_p).value if name else None
        fn = [f for f, _ in self.timers if at is None or f == at][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(0)

    def clicks(self, parent=None):
        """EVT_CLICK handlers in order: [(widget, call)]."""
        out = []
        for _, (w, evt, fn, ctx, *_) in self.fw.calls('widget_on'):
            if evt == EVT_CLICK and (parent is None or C.c_void_p.from_address(w + W_PARENT).value == parent):
                out.append((w, lambda f=fn, c=ctx: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, 0)))
        return out

    def handler(self, event):
        (w, fn, ctx), = [(w, fn, ctx) for _, (w, evt, fn, ctx, *_) in self.fw.calls('widget_on') if evt == event]
        return lambda e=0: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, e)

    def paths(self):
        return [self.ph.path[i].decode() for i in range(self.ph.n)]

    def albums(self):
        return [self.ph.album[k].decode() for k in range(self.ph.nalbums)]

    def tiles(self):
        return self.w.of('list_item_create', self.ph.view)


def files(*names, data=None):
    return {f'{ROOT}/{n}': data if data is not None else head(1) for n in names}


def test_without_albums_the_page_opens_on_its_grid_by_name(fw):
    card = Card(fw, files('b.jpg', 'A.png', 'c.JPEG', '._b.jpg', 'notes.txt', 'z.jpg/x.gif'))
    page = Page(fw, card)
    assert page.ph.screen == GRID and page.ph.nalbums == 0
    assert page.paths() == [f'{ROOT}/A.png', f'{ROOT}/b.jpg', f'{ROOT}/c.JPEG']
    assert [page.ph.key[i] for i in range(3)] == [key(p) for p in page.paths()]
    assert 'Photos' in page.w.texts.values()
    assert fw.calls('page_push') == []


def test_four_tiles_a_row(fw):
    card = Card(fw, files(*[f'{i:02}.jpg' for i in range(10)]))
    page = Page(fw, card)
    boxes = [struct.unpack_from('<4i', (C.c_char * 16).from_address(t)) for t in page.tiles()]
    assert boxes == [(GRID_X + i % 4 * TILE_W, i // 4 * TILE_H, TILE_W, TILE_H) for i in range(10)]
    heights = [a[2] for _, a in fw.calls('widget_set_prop_int')
               if a[0] == page.ph.view and C.string_at(a[1]) == b'virtual_h']
    assert heights == [3 * TILE_H]  # three rows, the last part full


def test_folders_holding_photos_are_albums_by_name(fw):
    card = Card(fw, {**files('own.jpg', 'Trip/1.jpg', 'Beach/2.png', 'Beach/x/3.jpg', 'Docs/a.pdf', '.trash/4.jpg',
                             'Deep/x/5.jpg')}, folders=[f'{ROOT}/Empty'])
    page = Page(fw, card)
    assert page.ph.screen == ALBUMS and page.albums() == ['Beach', 'Trip']  # a deeper folder's photo isn't its
    assert card.open_dirs == {}  # each folder read and closed


def test_an_album_is_known_by_its_first_photo(fw):
    card = Card(fw, files(*[f'Trip/{i:02}.jpg' for i in range(50)]))
    Page(fw, card)
    assert card.reads[f'{ROOT}/Trip'] == 1  # the rest unread until it opens


@pytest.mark.parametrize('typed', [True, False])
def test_all_photos_is_the_folder_s_own_then_each_album_s(fw, typed):
    card = Card(fw, files('own.jpg', 'Trip/1.jpg', 'Trip/0.jpg', 'Beach/2.png'), typed=typed)
    page = Page(fw, card)
    (_, all_photos), (_, beach), (_, trip) = page.clicks()
    all_photos()
    page.fire()
    assert page.ph.screen == GRID
    assert page.paths() == [f'{ROOT}/own.jpg', f'{ROOT}/Beach/2.png', f'{ROOT}/Trip/0.jpg', f'{ROOT}/Trip/1.jpg']
    assert 'All Photos' in page.w.texts.values()


@pytest.mark.parametrize('row, album', [(1, ['Beach/2.png']), (2, ['Trip/0.jpg', 'Trip/1.jpg'])])
def test_an_album_is_its_own_photos(fw, row, album):
    card = Card(fw, files('own.jpg', 'Trip/1.jpg', 'Trip/0.jpg', 'Beach/2.png'))
    page = Page(fw, card)
    page.clicks()[row][1]()
    page.fire()
    assert page.paths() == [f'{ROOT}/{path}' for path in album]
    assert visible(fw, page.ph.albums) == 0 and visible(fw, page.ph.grid) == 1


def test_at_most_photos_max(fw):
    card = Card(fw, files(*[f'{i:03}.jpg' for i in range(505)]))
    page = Page(fw, card)
    # the first 500 the folder lists (Card's readdir runs in reverse name order: 504 down to 5), sorted
    assert page.ph.n == 500 and page.paths() == [f'{ROOT}/{i:03}.jpg' for i in range(5, 505)]


def test_no_photos(fw):
    page = Page(fw, Card(fw, folders=[ROOT]))
    assert page.ph.n == 0 and page.tiles() == [] and 'No photos' in page.w.texts.values() and page.workers == []


def test_the_worker_makes_every_photo_and_the_poll_repaints_and_joins(fw):
    card = Card(fw, files('a.jpg', 'b.jpg', data=head(6)))
    Thumbs(fw, card)
    page = Page(fw, card)
    assert page.ph.running == 1 and page.timers[-1][1] == 250  # a look a quarter second
    page.run_worker()
    assert [page.ph.state[i] for i in range(2)] == [6, 6] and page.ph.finished == 1
    page.fire()
    assert page.ph.running == 0 and [a[0] for _, a in fw.calls('fw_pthread_join')] == [0x7e57]
    assert page.ph.page in [a[0] for _, a in fw.calls('widget_invalidate_force')]


def test_a_cancelled_worker_stops_before_its_next_photo(fw):
    card = Card(fw, files('a.jpg', 'b.jpg'))
    thumbs = Thumbs(fw, card)
    page = Page(fw, card)
    page.ph.cancel = 1  # leaving set it while the worker ran
    page.run_worker()
    assert thumbs.made == [] and page.ph.finished == 1 and [page.ph.state[i] for i in range(2)] == [0, 0]


def test_while_the_worker_runs_the_poll_looks_again(fw):
    card = Card(fw, files('a.jpg'))
    page = Page(fw, card)
    count = len(page.timers)
    page.fire()
    assert len(page.timers) == count + 1 and page.ph.running == 1


def test_no_thread_no_photos_to_show(fw):
    card = Card(fw, files('a.jpg', 'b.jpg'))
    page = Page(fw, card, thread=False)
    assert page.ph.running == 0 and [page.ph.state[i] for i in range(2)] == [PH_BAD] * 2


def test_little_space_writes_nothing(fw):
    card = Card(fw, files('a.jpg'), free_mb=15)
    thumbs = Thumbs(fw, card)
    page = Page(fw, card)
    page.run_worker()
    assert thumbs.made == [] and page.ph.state[0] == PH_BAD


def viewer(fw, n=5, ready=True, at=2):
    card = Card(fw, files(*[f'{i}.jpg' for i in range(n)], data=head(1)))
    Thumbs(fw, card)
    page = Page(fw, card)
    if ready:
        page.run_worker()
    page.clicks(page.ph.view)[at][1]()
    page.fire()
    return page


def caption(page):
    return page.w.texts.get(page.ph.info)


def visible(fw, w):
    return [a[1] for _, a in fw.calls('widget_set_visible') if a[0] == w][-1]


def test_a_tile_opens_its_photo_full_screen(fw):
    page = viewer(fw)
    assert page.ph.screen == VIEWER and page.ph.current == page.ph.want == 2
    slide = page.ph.slide
    assert len(page.w.of('image_create', slide)) == 5  # an item per photo
    assert [a[1] for _, a in fw.calls('slide_menu_set_value')] == [2]
    assert visible(fw, page.ph.grid) == 0 and visible(fw, page.ph.viewer) == 1
    assert caption(page) == f'3 of 5  2.jpg' and visible(fw, page.ph.info) == 0  # until Centre


def test_centre_shows_and_hides_the_caption(fw):
    page = viewer(fw)
    toggle = page.clicks(page.ph.slide)[0][1]
    toggle()
    assert visible(fw, page.ph.info) == 1
    toggle()
    assert visible(fw, page.ph.info) == 0


def wheel(page, i):
    """The slide_menu at photo i, and its value-changed event."""
    struct.pack_into('<i', (C.c_char * 0x200).from_address(page.ph.slide), SLIDE_INDEX, i)
    page.handler(EVT_VALUE_CHANGED)()


def test_the_caption_follows_the_wheel(fw):
    page = viewer(fw)
    wheel(page, 4)
    assert page.ph.current == 4 and caption(page) == '5 of 5  4.jpg'


def test_a_photo_not_made_yet_says_loading_and_a_bad_one_says_so(fw):
    page = viewer(fw, ready=False)
    assert caption(page) == 'Loading…' and visible(fw, page.ph.info) == 1
    page.ph.state[2] = PH_BAD
    wheel(page, 2)
    assert caption(page) == "Can't open this photo" and visible(fw, page.ph.info) == 1


def test_the_same_caption_isn_t_written_again(fw):
    page = viewer(fw)
    fw.mock('widget_get_text_utf8', lambda w, out, n, *_: C.memmove(out, b'3 of 5  2.jpg\0', 14) and 0)
    writes = len(fw.calls('widget_set_text_utf8'))
    wheel(page, 2)  # a decode's repaint: the same photo
    assert len(fw.calls('widget_set_text_utf8')) == writes


def keyup(page, key=KEY_RETURN):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, EVENT_KEY, key)
    page.handler(EVT_KEY_UP)(C.addressof(event))


@pytest.mark.parametrize('current, top, h, want', [
    (2, 0, 240, 0), (9, 0, 240, 0),  # in view: it stays
    (11, 0, 200, 40),  # its row's foot below the view: scrolled up to it
    (1, 160, 240, 0), (6, 100, 240, 80),  # above it: scrolled down to its top
])
def test_return_from_the_viewer_shows_the_grid_at_the_photo(fw, current, top, h, want):
    page = viewer(fw, n=12)
    view = (C.c_char * 0x200).from_address(page.ph.view)
    struct.pack_into('<i', view, SCROLL_Y, top)
    struct.pack_into('<i', view, W_H, h)
    page.ph.current = current
    keyup(page)
    page.fire()
    assert page.ph.screen == GRID and page.ph.want == -1
    assert [a[1:3] for _, a in fw.calls('scroll_view_set_offset')] == [(0, want)]
    assert visible(fw, page.ph.viewer) == 0 and visible(fw, page.ph.grid) == 1
    assert prop(fw, page.ph.view, '_ringnav_index') == current  # the wheel's selection on it (ringnav_select)


def test_return_from_an_album_s_grid_goes_back_to_the_albums(fw):
    card = Card(fw, files('Trip/1.jpg', 'Beach/2.png'))
    page = Page(fw, card)
    page.clicks()[1][1]()
    page.fire()
    page.run_worker()
    keyup(page)
    page.fire()
    assert page.ph.screen == ALBUMS and page.ph.n == 0 and page.ph.view is None
    assert visible(fw, page.ph.albums) == 1 and visible(fw, page.ph.grid) == 0 and fw.calls('navigator_back') == []


def test_return_from_a_grid_without_albums_leaves(fw):
    page = Page(fw, Card(fw, files('a.jpg')))
    keyup(page)
    page.fire()
    assert len(fw.calls('navigator_back')) == 1


def test_return_from_the_albums_leaves(fw):
    page = Page(fw, Card(fw, files('Trip/1.jpg')))
    keyup(page)
    page.fire()
    assert len(fw.calls('navigator_back')) == 1


def test_other_keys_do_nothing(fw):
    page = Page(fw, Card(fw, files('a.jpg')))
    count = len(page.timers)
    keyup(page, key=218)
    assert len(page.timers) == count


def test_leaving_a_grid_cancels_and_joins_its_worker(fw):
    card = Card(fw, files('Trip/1.jpg', 'Beach/2.png'))
    page = Page(fw, card)
    page.clicks()[1][1]()
    page.fire()
    keyup(page)
    page.fire()
    assert page.ph.cancel == 1 and page.ph.running == 0 and [a[0] for _, a in fw.calls('fw_pthread_join')] == [0x7e57]


def test_the_page_s_end_frees_everything(fw):
    page = viewer(fw)
    page.handler(EVT_DESTROY)()
    assert bytes(page.ph) == bytes(C.sizeof(Ph))


def test_a_second_open_while_open_does_nothing(fw):
    page = Page(fw, Card(fw, files('a.jpg')))
    count = len(fw.calls('window_create'))
    fw.fn('photos_open', None, C.c_char_p)(ROOT.encode())
    assert len(fw.calls('window_create')) == count and page.ph.page


def test_no_window_no_page(fw):
    Widgets(fw)
    fw.mock('window_create', lambda *_: 0)
    fw.fn('photos_open', None, C.c_char_p)(ROOT.encode())
    assert ph(fw).page is None and fw.calls('widget_factory_create_widget') == fw.calls('fw_opendir') == []


# The page's widgets and its flow from screen to screen.

SCREEN_W, SCREEN_H = 375, 320


def classic(fw):
    """THEME 1, Classic: the bar holds a page's title (page_title_h 0) and lists slide in place. Before anything reads
    the theme."""
    def read(path, section, key, out, default, *_):
        v = b'1' if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', read)


def classic_bar(fw, page):
    """Classic's status bar over page, the top window: its title label, found where navigation.c looks for it
    (bar_state_sync: label_title), and AWTK's answers to what the title's paint (bar_title_sync, page_caption) asks of
    the page's widgets: their types, children, visibility and names. Returns the bar's title label."""
    w, keep = page.w, {}
    bar, title = w.new('view', 0, 0, 0, SCREEN_W, 30), w.new('label', 0, 0, 0, 200, 30)

    def c(s):  # a C string as long as the test
        return C.addressof(keep.setdefault(s, C.create_string_buffer(s.encode())))

    def kind(x):
        return next((k for k, y, _ in w.made if y == x), 'label').removesuffix('_create')

    def shown(x):
        return ([a[1] for _, a in fw.calls('widget_set_visible') if a[0] == x] or [1])[-1]

    fw.mock('window_manager_get_top_window', lambda *_: page.ph.page)
    fw.mock('widget_lookup', lambda x, name, *_: title if x == bar and C.string_at(name) == b'label_title' else 0)
    fw.mock('widget_get_type', lambda x, *_: c(kind(x)))
    fw.mock('widget_count_children', lambda x, *_: len(w.children(x)))
    fw.mock('widget_get_visible', lambda x, *_: shown(x))
    fw.mock('widget_get_prop_str', lambda x, key, default, *_:
            c(w.names[x]) if C.string_at(key) == b'name' and x in w.names else default)
    fw.fn('navigation__bar_state_sync', C.c_int, C.c_void_p)(bar)
    return title


def prop(fw, w, name):
    """The last int w's name was set to."""
    return [a[2] for _, a in fw.calls('widget_set_prop_int') if a[0] == w and C.string_at(a[1]) == name.encode()][-1]


def repaints(fw, page):
    return sum(a[0] == page.ph.page for _, a in fw.calls('widget_invalidate_force'))


def media_name(fw):
    """Local Music's row for the card's Photos folder: the navigation.c MEDIA name the folder's matches, ignoring case,
    as media_kind finds it."""
    folder = ROOT.rsplit('/', 1)[1].lower()
    media = fw.var('MEDIA', C.c_char_p * (fw.sizes['MEDIA'] // C.sizeof(C.c_char_p)))
    (name,) = [m.decode() for m in media if m.decode().lower() == folder]
    return name


def bar_label(page):
    """The grid's label Classic's bar reads its title from (peq.h PAGE_TITLE)."""
    (label,) = [w for w in page.w.children(page.ph.grid) if page.w.names.get(w) == '_page_title']
    return label


def test_the_page_opens_on_its_albums_over_two_hidden_views(fw):
    page = Page(fw, Card(fw, files('Trip/1.jpg')))
    views = [page.ph.albums, page.ph.grid, page.ph.viewer]
    assert [page.w.kind(v) for v in views] == ['view'] * 3 and {box(v)[:3] for v in views} == {(0, 0, SCREEN_W)}
    assert [visible(fw, v) for v in views] == [1, 0, 0]


def test_the_page_is_one_ringnav_steps_as_a_carousel(fw):
    """navigation.c carousel_page knows it by its name, so the wheel steps the viewer's slide_menu a photo a tick."""
    page = Page(fw, Card(fw, files('a.jpg')))
    name = C.create_string_buffer(page.w.names[page.ph.page].encode())
    fw.mock('widget_get_prop_str',
            lambda w, key, default, *_: C.addressof(name) if C.string_at(key) == b'name' else default)
    assert fw.fn('navigation__carousel_page', C.c_int, C.c_void_p)(page.ph.page) == 1


def test_the_albums_are_local_music_s_row_all_photos_then_each_album(fw):
    page = Page(fw, Card(fw, files('Trip/1.jpg', 'Beach/2.png')))
    assert page.w.labels(page.ph.albums) == [media_name(fw)]  # titled as the row that opened the page
    (lv,) = page.w.of('list_view_create', page.ph.albums)
    (view,) = page.w.of('scroll_view_create', lv)
    rows = [w for w, _ in page.clicks()]
    first, *albums = [page.w.labels(r) for r in rows]  # All Photos first, by a name no album has
    assert albums == [['Beach'], ['Trip']] and len(first) == 1 and first[0] not in ('', 'Beach', 'Trip')
    item_h = prop(fw, lv, 'item_height')
    assert {box(r)[3] for r in rows} == {item_h} and prop(fw, view, 'virtual_h') == len(rows) * item_h


@pytest.mark.parametrize('row', range(3))
def test_a_grid_is_titled_as_the_row_that_opened_it(fw, row):
    page = Page(fw, Card(fw, files('own.jpg', 'Trip/1.jpg', 'Beach/2.png')))
    item, click = page.clicks()[row]
    click()
    page.fire()
    assert page.w.labels(page.ph.grid)[:1] == page.w.labels(item)


def test_at_most_200_albums(fw):
    page = Page(fw, Card(fw, files(*[f'{i:03}/a.jpg' for i in range(205)])))
    assert page.ph.nalbums == 200 and page.albums() == sorted(set(page.albums()))
    page.clicks()[0][1]()  # All Photos: each album's
    page.fire()
    assert page.ph.n == 200
    page.handler(EVT_DESTROY)()
    assert bytes(page.ph) == bytes(C.sizeof(Ph))


@pytest.mark.parametrize('n, rows', [(1, 1), (4, 1), (5, 2), (12, 3)])
def test_the_grid_scrolls_whole_rows_of_four(fw, n, rows):
    page = Page(fw, Card(fw, files(*[f'{i:02}.jpg' for i in range(n)])))
    assert prop(fw, page.ph.view, 'virtual_h') == rows * TILE_H


@pytest.mark.parametrize('bar_titles', [False, True])
def test_the_grid_scrolls_up_and_down_below_its_title(fw, bar_titles):
    """Under a title row (Minimal), three whole rows; where the bar holds the title (Classic), on to the page's foot."""
    if bar_titles:
        classic(fw)
    page = Page(fw, Card(fw, files(*[f'{i:02}.jpg' for i in range(10)])))
    title = page.w.children(page.ph.grid)[0]
    x, y, w, h = box(page.ph.view)
    assert (x, y, w) == (0, box(title)[3], box(page.ph.grid)[2])
    assert y + h == fw.fn('client_h', C.c_int)() if bar_titles else h == 3 * TILE_H
    assert (prop(fw, page.ph.view, 'yslidable'), prop(fw, page.ph.view, 'xslidable')) == (1, 0)


def test_a_tile_is_styled_as_a_list_row(fw):
    """The black list row's style, so the selection frames a tile as it does a row."""
    page = Page(fw, Card(fw, files('Trip/0.jpg', 'Trip/1.jpg', 'Beach/0.jpg')))
    row, click = page.clicks()[2]
    click()
    page.fire()
    styles = collections.defaultdict(list)
    for _, (w, style, *_) in fw.calls('widget_use_style'):
        styles[w].append(C.string_at(style))
    assert styles[row] and [styles[t] for t in page.tiles()] == [styles[row]] * 2


def test_classic_s_bar_reads_the_folder_s_name_over_no_photos(fw):
    classic(fw)
    page = Page(fw, Card(fw, folders=[ROOT]))
    label = bar_label(page)
    assert page.w.texts[label] == media_name(fw) and visible(fw, label) == 0
    (line,) = [w for w in page.w.children(page.ph.grid) if page.w.kind(w) == 'hscroll_label_create' and w != label]
    assert page.w.texts[line] not in ('', page.w.texts[label])  # the message, not the name
    assert not [a for _, a in fw.calls('widget_set_visible') if a[0] == line]  # the page's one line


def test_classic_s_bar_names_an_album_s_grid_by_its_album(fw):
    classic(fw)
    page = Page(fw, Card(fw, files('Trip/1.jpg', 'Beach/2.png')))
    page.clicks()[2][1]()
    page.fire()
    assert page.w.texts[bar_label(page)] == 'Trip'


def test_classic_s_bar_names_an_album_s_grid_as_it_opens(fw):
    """The bar finds a window's title label once, and the albums' stays alive under the grid, hidden: opening the grid
    has the bar drop it and read the grid's, at once."""
    classic(fw)
    page = Page(fw, Card(fw, files('Trip/1.jpg', 'Beach/2.png')))
    bar = classic_bar(fw, page)
    fw.fn('bar_title_sync', None, C.c_void_p)(page.ph.page)  # the bar's paint over the albums
    albums = page.w.labels(page.ph.albums)[0]
    assert page.w.texts[bar] == albums
    page.clicks()[2][1]()
    page.fire('to_grid')
    assert page.w.texts[bar] == page.w.texts[bar_label(page)] != albums


def test_classic_slides_an_album_s_grid_in_and_the_albums_back(fw):
    classic(fw)
    page = Page(fw, Card(fw, files('Trip/1.jpg', 'Beach/2.png')))
    def start(w):  # where its slide starts: its first move
        return [C.c_int(a[1]).value for _, a in fw.calls('widget_move_resize') if a[0] == w][:1]
    page.clicks()[2][1]()
    page.fire()
    assert start(page.ph.grid) == [box(page.ph.grid)[2]]  # in from the right, deeper
    keyup(page)
    page.fire()
    assert start(page.ph.albums) == [-box(page.ph.albums)[2]]  # back in from the left


def test_classic_opens_a_folder_without_albums_on_its_grid_still(fw):
    classic(fw)
    page = Page(fw, Card(fw, files('a.jpg')))
    assert page.ph.screen == GRID and not [a for _, a in fw.calls('widget_move_resize') if a[0] == page.ph.grid]


def test_a_card_without_the_cache_folder_gets_one(fw):
    card = Card(fw, files('a.jpg'), cache=False)
    Thumbs(fw, card)
    page = Page(fw, card)
    page.run_worker()
    assert page.ph.state[0] == 1 and card.isdir(CACHE) and cached(f'{ROOT}/a.jpg', 't.jpg') in card.files


def test_the_poll_repaints_only_when_a_photo_is_made(fw):
    page = Page(fw, Card(fw, files('a.jpg', 'b.jpg', 'c.jpg')))
    before = repaints(fw, page)
    page.fire('poll')
    assert repaints(fw, page) == before  # none made yet
    page.ph.done = 1  # the worker made one
    page.fire('poll')
    assert repaints(fw, page) == before + 1
    page.fire('poll')
    assert repaints(fw, page) == before + 1 and page.ph.running == 1


def test_a_photo_made_while_shown_gets_its_caption(fw):
    """The poll's repaint swaps the wait for the caption the wheel shows on a made photo, hidden again until Centre."""
    page = viewer(fw, ready=False)
    waiting = caption(page)
    assert visible(fw, page.ph.info) == 1  # the wait shows without Centre
    page.run_worker()
    page.fire('poll')
    made = caption(page), visible(fw, page.ph.info)
    wheel(page, 2)  # back onto the same photo, made
    assert made == (caption(page), 0) and made[0] != waiting


def test_a_smaller_album_after_the_viewer_polls_only_its_own_photos(fw):
    """The viewer left at Beach's fifth photo; Trip's grid has one. Its poll repaints with no caption to write, as the
    viewer went with Beach's grid, and reads nothing of a fifth photo it hasn't got."""
    card = Card(fw, files(*[f'Beach/{i}.jpg' for i in range(5)], 'Trip/0.jpg'))
    Thumbs(fw, card)
    page = Page(fw, card)
    page.clicks()[1][1]()
    page.fire()
    page.run_worker()
    page.clicks(page.ph.view)[4][1]()
    page.fire()
    for _ in range(2):
        keyup(page)
        page.fire()
    page.clicks()[2][1]()
    page.fire()
    assert page.paths() == [f'{ROOT}/Trip/0.jpg'] and page.ph.current == 4
    before = repaints(fw, page)
    page.run_worker()
    page.fire('poll')
    assert page.ph.state[0] == 1 and repaints(fw, page) == before + 1 and page.ph.running == 0


def test_photos_removes_only_timers_it_added(fw):
    """Timer 1 is someone else's here. A spent id of its own is harmless (AWTK never reuses one); a stale 1 isn't."""
    card = Card(fw, files('Trip/0.jpg', 'Trip/1.jpg', 'Beach/0.jpg'))
    Thumbs(fw, card)
    page = Page(fw, card, foreign=1)
    page.clicks()[2][1]()
    page.fire()
    page.run_worker()
    page.fire('poll')
    page.clicks(page.ph.view)[1][1]()
    page.fire()
    for _ in range(2):
        keyup(page)
        page.fire()
    page.clicks()[1][1]()
    page.fire()
    page.handler(EVT_DESTROY)()
    assert 1 not in [a[0] for _, a in fw.calls('timer_remove')]


def test_closing_the_page_drops_an_open_it_scheduled(fw):
    page = Page(fw, Card(fw, files('Trip/1.jpg', 'Beach/2.png')))
    page.clicks()[1][1]()  # Beach's grid, on the next pass
    pending = len(page.timers)
    page.handler(EVT_DESTROY)()
    assert [a[0] for _, a in fw.calls('timer_remove')] == [pending] and bytes(page.ph) == bytes(C.sizeof(Ph))


def test_closing_mid_build_cancels_and_joins_the_worker(fw):
    page = Page(fw, Card(fw, files('a.jpg', 'b.jpg')))
    page.handler(EVT_DESTROY)()
    assert [a[0] for _, a in fw.calls('fw_pthread_join')] == [0x7e57]


def tour(fw, page):
    """Trip's grid made, its photo 1 full screen, Centre twice, then Return twice: each step's repaints, before and
    after."""
    steps = [lambda: page.clicks()[2][1](), lambda: page.clicks(page.ph.view)[1][1](), lambda: keyup(page),
             lambda: keyup(page)]
    seen = []
    for k, step in enumerate(steps):
        before = repaints(fw, page)
        step()
        page.fire()
        if k == 0:
            page.run_worker()
        if k == 1:
            for _ in range(2):
                page.clicks(page.ph.slide)[0][1]()
        seen.append((before, repaints(fw, page)))
    return seen


def test_each_screen_change_repaints_the_page(fw):
    card = Card(fw, files('Trip/0.jpg', 'Trip/1.jpg', 'Beach/0.jpg'))
    Thumbs(fw, card)
    page = Page(fw, card)
    assert all(after > before for before, after in tour(fw, page)) and page.ph.screen == ALBUMS


def test_views_show_and_hide_alone(fw):
    """widget_set_visible's last argument sets the children too: a recursive hide outlasts the plain show after it."""
    card = Card(fw, files('Trip/0.jpg', 'Trip/1.jpg', 'Beach/0.jpg'))
    Thumbs(fw, card)
    tour(fw, Page(fw, card))
    calls = fw.calls('widget_set_visible')
    assert len(calls) > 10 and [a[2] for _, a in calls] == [0] * len(calls)


def test_the_viewer_is_a_slide_menu_a_photo_a_stride_of_the_screen_s_width(fw):
    """Its items the page's height square, the spacer making the stride the screen's width (docs/internals.md#photos),
    and each photo clickable, for Centre."""
    page = viewer(fw)
    slide, width = page.ph.slide, box(page.ph.viewer)[2]
    assert page.w.kind(slide) == 'slide_menu' and box(slide)[:3] == (0, 0, width)
    (spacer,) = [C.c_int(a[1]).value for _, a in fw.calls('slide_menu_set_spacer')]
    assert spacer + fw.fn('client_h', C.c_int)() == width
    clickable = {a[0] for _, a in fw.calls('widget_set_prop_int') if C.string_at(a[1]) == b'clickable' and a[2] == 1}
    assert len(page.w.of('image_create', slide)) == 5 and set(page.w.of('image_create', slide)) <= clickable


def test_the_caption_is_a_dark_rounded_band_centred_on_the_glass(fw):
    page = viewer(fw)
    info = page.ph.info
    assert info in page.w.children(page.ph.viewer)
    x, y, w, h = box(info)
    page_h, r = fw.fn('client_h', C.c_int)(), O['GLASS_R']
    assert w > 0 and x + w + x == box(page.ph.viewer)[2]  # centred
    assert h > 0 and 0 < y and y + h < page_h  # off the page's foot
    foot = SCREEN_H - page_h + y + h  # the page runs to the screen's foot
    for cx in (x, x + w):  # its lower corners clear of the glass's rounded ones
        dx = max(r - cx, cx - (SCREEN_W - r), 0)
        assert dx * dx + max(foot - (SCREEN_H - r), 0) ** 2 <= r * r
    colour = prop(fw, info, 'style:normal:bg_color') & 0xffffffff
    assert colour >> 24 >= 0x80 and max(colour >> 16 & 255, colour >> 8 & 255, colour & 255) <= 0x40  # under white
    assert prop(fw, info, 'style:normal:round_radius') > 0


def test_a_new_caption_starts_unscrolled(fw):
    """A long caption scrolls; the next one starts at its start, not where the last had got to."""
    page = viewer(fw)
    for i in (4, 0):
        wheel(page, i)
    writes = [a for _, a in fw.calls('widget_set_text_utf8') if a[0] == page.ph.info]
    resets = [a for _, a in fw.calls('widget_set_prop_int')
              if a[0] == page.ph.info and C.string_at(a[1]) == b'xoffset' and a[2] == 0]
    assert len(writes) == len(resets) == 3


def test_the_viewer_is_made_once_a_grid_and_afresh_for_the_next(fw):
    card = Card(fw, files('Trip/0.jpg', 'Trip/1.jpg', 'Beach/0.jpg', 'Beach/1.jpg', 'Beach/2.jpg'))
    page = Page(fw, card)
    page.clicks()[2][1]()
    page.fire()
    for tile in (0, 1):
        page.clicks(page.ph.view)[tile][1]()
        page.fire()
        keyup(page)
        page.fire()
    assert len(page.w.of('slide_menu')) == 1 and page.ph.current == 1
    keyup(page)
    page.fire()
    assert page.ph.viewer in [a[0] for _, a in fw.calls('widget_destroy_children')]  # its slide and caption go
    page.clicks()[1][1]()
    page.fire()
    page.clicks(page.ph.view)[2][1]()
    page.fire()
    slides = page.w.of('slide_menu')
    assert len(slides) == 2 and page.ph.slide == slides[-1] and len(page.w.of('image_create', slides[-1])) == 3


# Decoded slots and the paint hook.

class Decoder:
    """widget_load_image of a cached copy: w x h pixels in format fmt (1 RGBA ... 4 ARGB; AWTK's others aren't four
    bytes a pixel), pixel (x, y) red x and green y; bitmap_create_ex's slots, made with flags, the locks and unlocks,
    and what's drawn."""
    def __init__(self, fw, w=10, h=8, fmt=1, fail=False, flags=0):
        self.w, self.h, self.fmt, self.fail, self.flags = w, h, fmt, fail, flags
        self.loaded, self.unloaded, self.slots, self.destroyed, self.draws = [], [], [], [], []
        self.locked, self.unlocked = [], []
        stride = w * 4 + 8
        raw = bytearray(stride * h)
        layout = RGBA_AT[fmt - 1] if 1 <= fmt <= 4 else RGBA_AT[0]
        for y in range(h):
            for x in range(w):
                for k, v in enumerate((x & 255, y & 255, 99, 255)):
                    raw[y * stride + x * 4 + layout[k]] = v
        self.pixels = block(fw, bytes(raw), size=len(raw))
        self.stride = stride
        fw.mock('widget_load_image', self.load)
        fw.mock('widget_unload_image', lambda w, bm, *_: self.unloaded.append(bm))
        fw.mock('bitmap_lock_buffer_for_read', lambda bm, *_: self.locked.append(bm) or self.pixels)
        fw.mock('bitmap_get_line_length', lambda bm, *_: self.stride)
        fw.mock('bitmap_create_ex', self.create)
        fw.mock('bitmap_lock_buffer_for_write',
                lambda bm, *_: self.locked.append(bm) or C.c_void_p.from_address(bm + 0x20).value)
        fw.mock('bitmap_unlock_buffer', lambda bm, *_: self.unlocked.append(bm))
        fw.mock('bitmap_destroy', lambda bm, *_: self.destroyed.append(bm))
        fw.mock('canvas_draw_image', lambda c, bm, src, dst, *_: self.draws.append(
            (bm, tuple((C.c_int * 4).from_address(src)), tuple((C.c_int * 4).from_address(dst)))))
        self.fw = fw

    def load(self, w, url, bm, *_):
        self.loaded.append(C.string_at(url).decode())
        if self.fail:
            return 1
        struct.pack_into('<iiiHH', (C.c_char * 16).from_address(bm), 0, self.w, self.h, self.stride, 0, self.fmt)
        return 0

    def create(self, w, h, line, fmt, *_):
        assert (w * 4, fmt) == (line, 1)
        bm = block(self.fw, size=0x40)
        C.c_void_p.from_address(bm + 0x20).value = block(self.fw, size=w * h * 4)
        C.c_ushort.from_address(bm + 0xc).value = self.flags
        self.slots.append((bm, w, h))
        return bm

    def balanced(self):
        """Each lock of a bitmap unlocked once."""
        return collections.Counter(self.locked) == collections.Counter(self.unlocked)


class Canvas:
    def __init__(self, fw, fill=0xff102030):
        self.lcd, self.raw = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.raw, 0x38, C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, 0xbc, fill)
        self.p, self.fills = C.addressof(self.raw), []
        fw.mock('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, 0xbc, color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fills.append(
            (x, y, w, h, struct.unpack_from('<I', self.lcd, 0xbc)[0])))

    @property
    def fill(self):
        return struct.unpack_from('<I', self.lcd, 0xbc)[0]


def paint(fw, w, canvas):
    fw.fn('photos_paint', None, C.c_void_p, C.c_void_p)(w, canvas.p)


def grid(fw, n, ready=True, **decoder):
    card = Card(fw, files(*[f'{i:03}.jpg' for i in range(n)], data=head(1)))
    Thumbs(fw, card)
    page = Page(fw, card)
    if ready:
        page.run_worker()
    return page, Decoder(fw, **decoder), Canvas(fw)


def test_a_ready_tile_draws_its_thumbnail_centred_in_the_tile(fw):
    page, dec, canvas = grid(fw, 6, w=85, h=60)
    paint(fw, page.tiles()[5], canvas)
    assert dec.loaded == [f'file://{cached(page.paths()[5], "t.jpg")}']
    (bm, w, h), = dec.slots
    assert (w, h) == (THUMB_W, THUMB_W)  # a slot is a square of the thumbnail's long side
    assert dec.draws == [(bm, (0, 0, 85, 60), (4, 4 + (72 - 60) // 2, 85, 60))]
    assert dec.unloaded and canvas.fills == [] and len(dec.locked) == 2 and dec.balanced()
    assert C.c_ushort.from_address(bm + 0xc).value & 1  # opaque


@pytest.mark.parametrize('fmt', [1, 2, 3, 4])
def test_the_slot_holds_the_copy_upright_in_red_green_blue(fw, fmt):
    page, dec, canvas = grid(fw, 1, w=3, h=2, fmt=fmt)
    paint(fw, page.tiles()[0], canvas)
    (bm, w, _), = dec.slots
    px = (C.c_uint * (w * w)).from_address(C.c_void_p.from_address(bm + 0x20).value)
    assert [[px[y * w + x] for x in range(3)] for y in range(2)] == [[0xff630000 | y << 8 | x for x in range(3)]
                                                                      for y in range(2)]


@pytest.mark.parametrize('ready, fail', [(False, False), (True, True)])
def test_a_tile_without_a_thumbnail_is_grey(fw, ready, fail):
    page, dec, canvas = grid(fw, 3, ready=ready, fail=fail)
    paint(fw, page.tiles()[1], canvas)
    assert canvas.fills == [(4, 4, THUMB_W, THUMB_H, GREY)] and canvas.fill == 0xff102030 and dec.draws == []
    assert dec.unloaded == [] and dec.locked == dec.unlocked == []  # nothing loaded, nothing to give back


@pytest.mark.parametrize('state', range(10))
def test_a_tile_draws_only_a_photo_made(fw, state):
    """1-8 its orientation, made; 0 pending and PH_BAD aren't, and nothing is loaded for them."""
    page, dec, canvas = grid(fw, 1)
    page.ph.state[0] = state
    paint(fw, page.tiles()[0], canvas)
    assert len(dec.loaded) == len(dec.draws) == (1 <= state <= 8)


@pytest.mark.parametrize('decoded', [
    dict(fmt=0), dict(fmt=5), dict(fmt=7),  # none, RGB565, RGB888: not four bytes a pixel
    dict(w=4097, h=2), dict(w=2, h=4097),  # past any copy made
])
def test_a_decode_photo_orient_can_t_read_leaves_the_tile_grey(fw, decoded):
    page, dec, canvas = grid(fw, 1, **decoded)
    paint(fw, page.tiles()[0], canvas)
    assert dec.draws == [] and canvas.fills == [(4, 4, THUMB_W, THUMB_H, GREY)]
    assert dec.unloaded and dec.balanced()  # given back, nothing left locked


@pytest.mark.parametrize('o', range(1, 9))
@pytest.mark.parametrize('w, h', [(0, 10), (10, 0)])
def test_a_decode_with_no_pixels_leaves_the_tile_grey_however_it_turns(fw, w, h, o):
    """A decode with a side of 0 has nothing to show, whichever side its orientation puts first (5-8 swap them): drawn,
    it would be a sliver."""
    page, dec, canvas = grid(fw, 1, w=w, h=h)
    page.ph.state[0] = o
    paint(fw, page.tiles()[0], canvas)
    assert dec.draws == [] and canvas.fills == [(4, 4, THUMB_W, THUMB_H, GREY)]
    assert dec.unloaded and dec.balanced()


@pytest.mark.parametrize('w, h', [(4096, 2), (2, 4096)])
def test_a_decode_up_to_4096_a_side_is_drawn(fw, w, h):
    page, dec, canvas = grid(fw, 1, w=w, h=h)
    paint(fw, page.tiles()[0], canvas)
    assert len(dec.draws) == 1 and dec.balanced()


def test_the_slot_keeps_the_flags_it_was_made_with(fw):
    page, dec, canvas = grid(fw, 1, flags=0x20)
    paint(fw, page.tiles()[0], canvas)
    (bm, *_), = dec.slots
    assert C.c_ushort.from_address(bm + 0xc).value == 0x21  # opaque added to them


def test_a_held_thumbnail_is_drawn_again_from_memory(fw):
    page, dec, canvas = grid(fw, 2)
    for _ in range(2):
        paint(fw, page.tiles()[0], canvas)
    (bm, *_), = dec.slots
    assert len(dec.loaded) == 1 and [d[0] for d in dec.draws] == [bm, bm]


def test_without_a_slot_bitmap_the_tile_stays_grey(fw):
    page, dec, canvas = grid(fw, 1)
    fw.mock('bitmap_create_ex', lambda *_: 0)
    paint(fw, page.tiles()[0], canvas)
    assert dec.loaded == dec.draws == [] and canvas.fills == [(4, 4, THUMB_W, THUMB_H, GREY)]


def test_thumbnail_slots_give_way_farthest_first(fw):
    page, dec, canvas = grid(fw, 41)
    tiles = page.tiles()
    for i in range(32):
        paint(fw, tiles[i], canvas)
    assert len(dec.slots) == 32
    paint(fw, tiles[40], canvas)  # photo 0's slot, the farthest from 40, gives way
    assert len(dec.slots) == 32 and sorted(s.photo - 1 for s in page.ph.tiles) == [*range(1, 32), 40]
    loads = len(dec.loaded)
    paint(fw, tiles[31], canvas)  # held: drawn from memory
    assert len(dec.loaded) == loads
    paint(fw, tiles[0], canvas)  # 40 is now the farthest
    assert sorted(s.photo - 1 for s in page.ph.tiles) == list(range(32))


def test_the_photo_farthest_either_way_gives_way(fw):
    page, dec, canvas = grid(fw, 41)
    tiles = page.tiles()
    for i in [40, *range(7, 24), *range(25, 39)]:  # every slot held
        paint(fw, tiles[i], canvas)
    paint(fw, tiles[24], canvas)  # 40 is 16 on, 7 is 17 back
    assert sorted(s.photo - 1 for s in page.ph.tiles) == [*range(8, 39), 40]


def test_while_a_slot_is_free_nothing_gives_way(fw):
    page, dec, canvas = grid(fw, 41)
    for i in (40, 5):
        paint(fw, page.tiles()[i], canvas)
    assert sorted(s.photo - 1 for s in page.ph.tiles if s.photo) == [5, 40]


def test_paint_ignores_other_widgets_and_no_page(fw):
    page, dec, canvas = grid(fw, 3)
    stray = page.w.new('label', 0, 0, 0, 10, 10)
    paint(fw, stray, canvas)
    paint(fw, 0, canvas)
    assert dec.draws == [] and canvas.fills == []


def slide_at(page, index, offset, stride_item=290):
    s = (C.c_char * 0x200).from_address(page.ph.slide)
    struct.pack_into('<i', s, SLIDE_INDEX, index)
    struct.pack_into('<i', s, SLIDE_SPACER, 375 - stride_item)
    struct.pack_into('<i', s, SLIDE_OFFSET, offset)
    struct.pack_into('<i', s, W_H, 290)


def test_the_viewer_at_rest_draws_its_photo_fitted_and_decodes_both_neighbours(fw):
    page = viewer(fw, n=5, at=2)
    dec, canvas = Decoder(fw, w=375, h=200), Canvas(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    slide_at(page, 2, 0)
    paint(fw, page.ph.slide, canvas)
    assert page.ph.want == 2
    assert [u.rsplit('/', 1)[1] for u in dec.loaded] == [f'{key(page.paths()[i]):08x}.jpg' for i in (2, 3, 1)]
    assert len(dec.draws) == 1 and dec.draws[0][1:] == ((0, 0, 375, 200), (0, 45, 375, 200))


def test_mid_slide_the_viewer_draws_the_one_coming_in(fw):
    page = viewer(fw, n=5, at=4)
    dec, canvas = Decoder(fw, w=375, h=290), Canvas(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    slide_at(page, 4, -100)  # a third of the way to the next, which wraps round to the first
    paint(fw, page.ph.slide, canvas)
    assert [u.rsplit('/', 1)[1] for u in dec.loaded] == [f'{key(page.paths()[i]):08x}.jpg' for i in (4, 0)]
    (_, _, (x0, y0, *_)), (_, _, (x1, y1, *_)) = dec.draws
    assert abs(x0 + 100) <= 1 and x1 - x0 == 375  # at the offset, to the fixed point's pixel; one stride on
    assert y0 == y1


def test_mid_slide_back_the_viewer_draws_the_one_before_coming_in(fw):
    page = viewer(fw, n=5, at=0)
    dec, canvas = Decoder(fw, w=375, h=290), Canvas(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    slide_at(page, 0, 100)  # a little way back from the first, to the last
    paint(fw, page.ph.slide, canvas)
    assert [u.rsplit('/', 1)[1] for u in dec.loaded] == [f'{key(page.paths()[i]):08x}.jpg' for i in (0, 4)]
    (_, _, (x0, y0, *_)), (_, _, (x1, y1, *_)) = dec.draws
    assert abs(x0 - 100) <= 1 and x0 - x1 == 375 and y0 == y1


@pytest.mark.parametrize('to', [8, 12])
def test_settled_the_viewer_holds_its_photo_and_both_neighbours(fw, to):
    """Two photos on, either way, the next slide draws from memory."""
    page = viewer(fw, n=20, at=10)
    dec, canvas = Decoder(fw, w=375, h=290), Canvas(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    for c in (10, to):
        slide_at(page, c, 0)
        paint(fw, page.ph.slide, canvas)
    loads = len(dec.loaded)
    paint(fw, page.ph.slide, canvas)
    assert len(dec.loaded) == loads and sorted(s.photo - 1 for s in page.ph.shots) == [to - 1, to, to + 1]


def test_the_viewer_draws_only_on_its_slide_menu(fw):
    page = viewer(fw)
    dec, canvas = Decoder(fw), Canvas(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    for w in (page.ph.info, page.ph.viewer, *page.w.of('image_create', page.ph.slide)):
        paint(fw, w, canvas)
    assert dec.loaded == dec.draws == []


def test_before_the_slide_menu_lays_its_items_out_nothing_is_drawn(fw):
    page = viewer(fw, at=2)
    dec, canvas = Decoder(fw), Canvas(fw)  # slide_menu_item_width 0: no stride yet
    paint(fw, page.ph.slide, canvas)
    assert dec.loaded == dec.draws == [] and page.ph.want == 2


def test_the_worker_makes_the_photo_the_wheel_moves_to_first(fw):
    page = viewer(fw, n=6, ready=False, at=1)
    Decoder(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    slide_at(page, 4, 0)
    paint(fw, page.ph.slide, Canvas(fw))
    assert page.ph.want == 4 and fw.fn('photos__next_job', C.c_int)() == 4


def test_the_viewer_draws_nothing_for_a_photo_not_ready(fw):
    page = viewer(fw, n=3, ready=False, at=1)
    dec, canvas = Decoder(fw), Canvas(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    slide_at(page, 1, 0)
    paint(fw, page.ph.slide, canvas)
    assert dec.loaded == [] and dec.draws == []


def test_leaving_a_grid_destroys_each_decoded_slot_once(fw):
    card = Card(fw, files(*[f'Trip/{i}.jpg' for i in range(6)], 'Beach/0.jpg'))
    Thumbs(fw, card)
    page = Page(fw, card)
    page.clicks()[2][1]()
    page.fire()
    page.run_worker()
    dec, canvas = Decoder(fw), Canvas(fw)
    for t in page.tiles()[:4]:
        paint(fw, t, canvas)
    page.clicks(page.ph.view)[2][1]()
    page.fire()
    fw.mock('slide_menu_item_width', lambda *_: 290)
    slide_at(page, 2, 0)
    paint(fw, page.ph.slide, canvas)
    for _ in range(2):
        keyup(page)
        page.fire()
    assert page.ph.screen == ALBUMS and len(dec.slots) == 4 + 3
    assert sorted(dec.destroyed) == sorted(bm for bm, *_ in dec.slots)


def test_another_album_s_grid_decodes_its_own_thumbnails(fw):
    card = Card(fw, files('Trip/0.jpg', 'Beach/0.jpg'))
    Thumbs(fw, card)
    page = Page(fw, card)
    dec, canvas = Decoder(fw), Canvas(fw)
    for row in (2, 1):  # Trip's grid, then Beach's
        page.clicks()[row][1]()
        page.fire()
        page.run_worker()
        paint(fw, page.tiles()[0], canvas)
        keyup(page)
        page.fire()
    want = [f'{key(f"{ROOT}/{album}/0.jpg"):08x}t.jpg' for album in ('Trip', 'Beach')]
    assert [u.rsplit('/', 1)[1] for u in dec.loaded] == want
