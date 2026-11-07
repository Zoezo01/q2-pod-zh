"""photos.c (docs/internals.md#photos): file names, the EXIF orientation and its eight meanings, placement, the
cache worker, the albums, grid and viewer, and the decoded slots."""
import ctypes as C
import random
import struct

import pytest

from conftest import FNV_PRIME, Files, block, fnv1a

ROOT = '/mnt/mmc/Photos'
CACHE = '/mnt/mmc/.photos'
PH_BAD = 9
ALBUMS, GRID, VIEWER = range(3)
EVT_DESTROY, EVT_VALUE_CHANGED, EVT_CLICK, EVT_KEY_UP = 0x0c, 0x0e, 0x10c, 0x114
W_H, W_PARENT = 0xc, 0x48
SLIDE_INDEX, SLIDE_SPACER, SLIDE_OFFSET, SCROLL_Y = 0x78, 0x84, 0x94, 0x84
KEY_RETURN, EVENT_KEY = 170, 0x18
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


def exif(entries, le=True, ifd=8, order=None):
    """An APP1 Exif body: a TIFF header in either byte order and IFD0's SHORT entries (tag, value)."""
    e = '<' if le else '>'
    tiff = (order or (b'II' if le else b'MM')) + struct.pack(e + 'HI', 42, ifd) + b'\0' * (ifd - 8)
    tiff += struct.pack(e + 'H', len(entries))
    for tag, value in entries:
        tiff += struct.pack(e + 'HHIH', tag, 3, 1, value) + b'\0\0'  # a SHORT sits in the value's first half
    return b'Exif\0\0' + tiff + b'\0' * 4


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


def orientation(fw, data):
    """photo_orientation over data in a heap block of exactly its size, so ASan sees a read past it."""
    p = block(fw, data, size=len(data))
    return fw.fn('photo_orientation', C.c_int, C.c_void_p, C.c_uint)(p, len(data))


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


@pytest.mark.parametrize('data', [
    jpeg(JFIF),  # no EXIF
    jpeg(segment(0xe1, exif([(0x110, 6)]))),  # no orientation
    jpeg(segment(0xe1, exif([(0x112, 0)]))), jpeg(segment(0xe1, exif([(0x112, 9)]))),
    jpeg(segment(0xe1, exif([(0x112, 0xffff)]))),  # out of 1-8
    jpeg(segment(0xe1, exif([(0x112, 6)], order=b'IM'))), jpeg(segment(0xe1, exif([(0x112, 6)], order=b'XX'))),
    jpeg(segment(0xe1, far_ifd([(0x112, 6)]))),  # IFD0 past the segment
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
        p = block(fw, data[:n], size=max(n, 1))
        assert fw.fn('photo_orientation', C.c_int, C.c_void_p, C.c_uint)(p, n) == (3 if n >= end else 1), n


def test_photo_orientation_hostile_sweep(fw):
    """Damaged headers: any answer is 1-8 and nothing past the bytes given is read (ASan)."""
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
    src = block(fw, bytes(raw), size=len(raw))
    dst = block(fw, size=box * box * 4)
    size = (C.c_int * 2)()
    at = (C.c_ubyte * 4)(*RGBA_AT[fmt])
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
    src = [[(255, 128, 10, 0), (255, 128, 10, 128), (255, 128, 10, 255), (90, 60, 30, 51)]]
    rows, _ = orient(fw, src, 1, fmt=2)
    assert rows == [[(0, 0, 0, 255), (128, 64, 5, 255), (255, 128, 10, 255), (18, 12, 6, 255)]]


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
    """The card: files by path, their folders (and empty ones), access, opendir and readdir in an order that
    isn't the name's, statfs's free space; ffmpeg can't run here (fork refused)."""
    def __init__(self, fw, files=None, folders=(), free_mb=1024, typed=True):
        super().__init__(fw, dict(files or {}))
        self.folders, self.free_mb, self.typed, self.open_dirs, self.ents = set(folders), free_mb, typed, {}, []
        fw.mock('fw_access', lambda p, *_: 0 if self.exists(C.string_at(p).decode()) else -1)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.open_dirs.pop(d) and 0)
        fw.mock('fw_statfs', self.statfs)
        fw.mock('fw_fork', lambda *_: -1)

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
        return handle

    def readdir(self, d, *_):
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
    """Stock's toolsThumbSpecCover: (src, dst, w, h) each call; a decodable source gets a copy at dst."""
    def __init__(self, fw, card, bad=()):
        self.card, self.bad, self.made = card, set(bad), []
        fw.mock('toolsThumbSpecCover', self.thumb)

    def thumb(self, src, dst, w, h, *_):
        src, dst = C.string_at(src).decode(), C.string_at(dst).decode()
        self.made.append((src, dst, w, h))
        if src in self.bad or src not in self.card.files:
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


@pytest.mark.parametrize('o, shot, small', [(1, (375, 290), (85, 72)), (3, (375, 290), (85, 72)),
                                            (6, (290, 375), (72, 85)), (8, (290, 375), (72, 85))])
def test_a_new_photo_gets_its_screen_copy_then_a_thumbnail_from_it(fw, o, shot, small):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(o)})
    thumbs = Thumbs(fw, card)
    worker_state(fw, [path])
    assert build(fw) == o
    tmp = cached(path, '.tmp')
    assert thumbs.made == [(path, tmp, *shot), (cached(path), tmp, *small)]  # sides swapped when it turns
    assert card.files[cached(path)] == b'jpeg %dx%d' % shot and card.files[cached(path, 't.jpg')] == b'jpeg %dx%d' % small
    assert tmp not in card.files


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


def test_a_photo_nothing_decodes_gets_the_bad_marker(fw):
    path = f'{ROOT}/a.jpg'
    card = Card(fw, {path: head(1)})
    Thumbs(fw, card, bad={path})
    worker_state(fw, [path])
    assert build(fw) == PH_BAD
    assert card.files[cached(path, '.bad')] == b'' and cached(path) not in card.files
    assert fw.calls('fw_fork')  # ffmpeg was the fallback


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
    """Each AWTK constructor a fresh zeroed widget with its box and parent; window_create the page."""
    def __init__(self, fw):
        self.fw, self.blocks, self.made = fw, [], []
        for name in ('window_create', 'scroll_view_create', 'list_item_create', 'image_create', 'hscroll_label_create',
                     'list_view_create', 'button_create'):
            fw.mock(name, lambda parent, x, y, w, h, *_, kind=name: self.new(kind, parent, x, y, w, h))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.new(
            C.string_at(kind).decode(), parent, x, y, w, 320))
        self.texts = {}
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))

    def new(self, kind, parent, x, y, w, h):
        self.blocks.append(C.create_string_buffer(0x200))
        struct.pack_into('<4i', self.blocks[-1], 0, *(C.c_int(v).value for v in (x, y, w, h)))
        struct.pack_into('<Q', self.blocks[-1], W_PARENT, parent)
        self.made.append((kind, C.addressof(self.blocks[-1]), parent))
        return C.addressof(self.blocks[-1])

    def of(self, kind, parent=None):
        return [w for k, w, p in self.made if k == kind and (parent is None or p == parent)]


class Page:
    """photos_open on root: the widgets, the timers (fn, ms) and the worker's thread, which a test runs."""
    def __init__(self, fw, card, root=ROOT, thread=True):
        self.fw, self.card = fw, card
        self.w = Widgets(fw)
        self.timers, self.workers = [], []
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

    def fire(self):
        """The last timer's function, as the next loop pass runs it."""
        fn, _ = self.timers[-1]
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


def test_an_album_is_its_own_photos(fw):
    card = Card(fw, files('own.jpg', 'Trip/1.jpg', 'Trip/0.jpg', 'Beach/2.png'))
    page = Page(fw, card)
    page.clicks()[2][1]()
    page.fire()
    assert page.paths() == [f'{ROOT}/Trip/0.jpg', f'{ROOT}/Trip/1.jpg'] and 'Trip' in page.w.texts.values()


def test_at_most_photos_max(fw):
    card = Card(fw, files(*[f'{i:03}.jpg' for i in range(505)]))
    page = Page(fw, card)
    assert page.ph.n == 500 and page.paths() == sorted(set(page.paths()))  # the first 500 the folder lists


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


def test_return_from_an_album_s_grid_goes_back_to_the_albums(fw):
    card = Card(fw, files('Trip/1.jpg', 'Beach/2.png'))
    page = Page(fw, card)
    page.clicks()[1][1]()
    page.fire()
    page.run_worker()
    keyup(page)
    page.fire()
    assert page.ph.screen == ALBUMS and page.ph.n == 0 and page.ph.view is None
    assert visible(fw, page.ph.albums) == 1 and fw.calls('navigator_back') == []


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
    assert ph(fw).page is None


# Decoded slots and the paint hook.

class Decoder:
    """widget_load_image of a cached copy: w x h pixels in format fmt (1 RGBA ... 4 ARGB), pixel (x, y) red x
    and green y; bitmap_create_ex's slots, and what's drawn."""
    def __init__(self, fw, w=10, h=8, fmt=1, fail=False):
        self.w, self.h, self.fmt, self.fail = w, h, fmt, fail
        self.loaded, self.unloaded, self.slots, self.destroyed, self.draws = [], [], [], [], []
        stride = w * 4 + 8
        raw = bytearray(stride * h)
        for y in range(h):
            for x in range(w):
                for k, v in enumerate((x & 255, y & 255, 99, 255)):
                    raw[y * stride + x * 4 + RGBA_AT[fmt - 1][k]] = v
        self.pixels = block(fw, bytes(raw), size=len(raw))
        self.stride = stride
        fw.mock('widget_load_image', self.load)
        fw.mock('widget_unload_image', lambda w, bm, *_: self.unloaded.append(bm))
        fw.mock('bitmap_lock_buffer_for_read', lambda bm, *_: self.pixels)
        fw.mock('bitmap_get_line_length', lambda bm, *_: self.stride)
        fw.mock('bitmap_create_ex', self.create)
        fw.mock('bitmap_lock_buffer_for_write', lambda bm, *_: C.c_void_p.from_address(bm + 0x20).value)
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
        self.slots.append((bm, w, h))
        return bm


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
    assert dec.unloaded and canvas.fills == []
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
    (_, _, (x0, *_)), (_, _, (x1, *_)) = dec.draws
    assert abs(x0 + 100) <= 1 and x1 - x0 == 375  # at the offset, to the fixed point's pixel; one stride on


def test_the_viewer_draws_nothing_for_a_photo_not_ready(fw):
    page = viewer(fw, n=3, ready=False, at=1)
    dec, canvas = Decoder(fw), Canvas(fw)
    fw.mock('slide_menu_item_width', lambda *_: 290)
    slide_at(page, 1, 0)
    paint(fw, page.ph.slide, canvas)
    assert dec.loaded == [] and dec.draws == []
