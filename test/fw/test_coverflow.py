"""coverflow.c on its device path: Home's pan and cubic filter, the shelf's geometry, touch, the album order and the art cache."""
import ctypes as C
import math
import pathlib
import random
import re
import sqlite3
import struct
import unicodedata
from types import SimpleNamespace

import pytest

import host
from conftest import O, Deques, Externpic, Files, ask_cover, claim, high_halves, in_fork

ROOT = pathlib.Path(__file__).resolve().parents[2]
CX, TOP, ART, ONE, MISS = 187, 13, 160, 65536, 99
VIEW_W, VIEW_H = 375, 220
HOME_PW, HOME_H = 188, 290
PREPARING, COVERS, TRACKS = range(3)
ARTIST_ALBUMS, RATED_ALBUMS = 1, 2
CF_REACH, CF_WIDE_REACH = 3, 7  # the ring's slots either side of the centre: fx_ring's, and its callers' window
SORT_ALBUM, SORT_ARTIST, SORT_ADDED, SORT_PLAYED, SORT_RANDOM, SORT_RATING, SORT_YEAR = range(7)
FNV = 2166136261
r = C.byref


class Cf(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page body covers slide name artist title albums tracks stock'.split()),
                *((k, C.c_int) for k in 'sort sort_read year_old artists on_sort resort'.split()),
                ('jobs', C.c_void_p), ('thread', C.c_ulong), ('timer', C.c_uint), ('title_timer', C.c_uint), ('titled', C.c_int),
                ('saved_album', C.c_uint), ('albums_gen', C.c_uint),
                *((k, C.c_int) for k in 'screen album running text_albums done total cancel scoped all_sort'.split()),
                *((k, C.c_void_p) for k in 'all_albums all_stock hearts'.split())]


class Touch(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'down drag caught x y from_ n'.split()), ('sx', C.c_int * 8), ('st', C.c_uint * 8)]


class Home(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'win art list sets lib stream'.split()), ('carousel', C.c_void_p * 3),
                *((k, C.c_uint) for k in 'key repaint drift drift_ms drift_last'.split()),
                ('tex', C.c_void_p), ('frame', C.c_void_p), *((k, C.c_int) for k in 'tw rx ry u0 v0 du dv pane'.split()),
                *((k, C.c_void_p) for k in 'row row_image row_label svc'.split()), ('svc_name', C.c_char * 72),
                *((k, C.c_int) for k in 'kind songs albums services games free_tenths counted counting got_songs got_albums'.split()),
                ('count_thread', C.c_ulong), *((k, C.c_uint) for k in 'asked pane_read count_timer pane_timer'.split()),
                ('ring', (C.c_uint * HOME_PW) * 4), ('clip', C.c_int * 4), ('clipped', C.c_int)]


class Row(C.Structure):
    _fields_ = [('a', C.c_void_p), ('j', C.c_uint)]


class Pose(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'xc zc c s bright eye'.split()]


class Job(C.Structure):
    _fields_ = [('track', C.c_char_p), ('key', C.c_uint), ('shared', C.c_int)]


class Dirent(C.Structure):
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


def api(fw):
    ptr, i, u = C.c_void_p, C.c_int, C.c_uint
    fns = {'coverflow__bspline_w': (None, u, ptr), 'home_sharpen': (None, ptr, i, i), 'coverflow__home_fit': (None, u, u, ptr, ptr),
           'coverflow__home_render': (None, ptr, i, i, i), 'coverflow__drift_at': (i, i, u), 'coverflow__pan_u': (i,),
           'coverflow__pan_v': (i,), 'coverflow__velocity': (i, u), 'coverflow__sample': (None, i, u),
           'coverflow__round_stride': (i, i, i), 'coverflow__floor_div': (i, i, i), 'covers_live': (i,),
           'coverflow_hit': (i, i, i, i), 'coverflow_render': (None, ptr, i, i, ptr), 'pose': (None, i, ptr),
           'project': (i, ptr, i), 'unproject': (i, ptr, i, ptr, ptr), 'coverflow__slot': (i, i, i, i),
           'coverflow__lower': (u, ptr, u, ptr, ptr), 'coverflow__album_in': (i, ptr, u, ptr), 'coverflow__album_index': (i, ptr),
           'coverflow__name_cmp': (i, ptr, ptr), 'coverflow__tie': (i, i, ptr, ptr), 'coverflow__by_name': (i, ptr, ptr),
           'coverflow__by_album': (i, ptr, ptr), 'album_cmp': (i, ptr, ptr), 'album_id': (u, ptr), 'album_dir': (u, C.c_char_p, i),
           'folder_cmp': (i, ptr, ptr), 'coverflow__cover_artist': (C.c_char_p, ptr), 'coverflow__initial': (u, i),
           'coverflow_jump': (i, ptr, i, i, ptr), 'coverflow__bound': (i, i, i, i), 'coverflow__unbound': (i, i, i),
           'coverflow__release': (None, i), 'coverflow__touch': (i, ptr, ptr), 'stride_of': (i, ptr),
           'coverflow__stamp': (u, ptr), 'coverflow__lift': (None, i, u), 'coverflow__sort_saved': (i,),
           'coverflow_sort_name': (C.c_char_p,), 'coverflow_sort_step': (None,), 'coverflow__sort_next': (i, i),
           'blob_io': (i, C.c_char_p, C.c_char_p, ptr, u, i), 'file_size': (C.c_long, C.c_char_p),
           'coverflow__ff_run': (i, C.c_char_p, C.c_char_p, i, i, ptr), 'coverflow__build_art': (None, ptr),
           'coverflow__worker': (ptr, ptr), 'art_strike': (None, C.c_char_p), 'coverflow_clean_end': (None,),
           'thumb': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow__grid_cover': (i, C.c_char_p, C.c_char_p, i, i, i), 'coverflow_album_cover': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow_grid_cover': (i, C.c_char_p, C.c_char_p, i, i), 'coverflow_folder_cover': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow_player_cover': (i, C.c_char_p, C.c_char_p, i, i), 'coverflow__marked_empty': (i, C.c_char_p),
           'now_cover': (C.c_ubyte, ptr, ptr), 'hash_bytes': (u, u, ptr, u), 'fnv': (u, u, C.c_char_p)}
    fw.fn('cf_restyle', None)()  # the theme's Cover Flow values, as the page's open copies them
    return SimpleNamespace(**{k.replace('coverflow__', ''): fw.fn(k, v[0], *v[1:]) for k, v in fns.items()})


libc = C.CDLL(None)
libc.malloc.restype, libc.malloc.argtypes = C.c_void_p, [C.c_size_t]
libc.calloc.restype, libc.calloc.argtypes = C.c_void_p, [C.c_size_t, C.c_size_t]
libc.free.argtypes = [C.c_void_p]
libc.qsort.argtypes = [C.c_void_p, C.c_size_t, C.c_size_t, C.c_void_p]
libc.mmap.restype, libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]


def exact(ctype, n):
    """n zeroed ctypes in a calloc of exactly their size, so the sanitizer sees an overrun; free with libc.free."""
    return (ctype * n).from_address(libc.calloc(n, C.sizeof(ctype)))


class Exact:
    def __init__(self, data):
        self.p = libc.malloc(len(data) or 1)
        C.memmove(self.p, bytes(data), len(data))

    def __enter__(self):
        return self.p

    def __exit__(self, *_):
        libc.free(self.p)


class Split:
    """Strings for a record holding both album and artist: an 8-byte pointer at 0x14 runs into 0x18, so the album goes
    under 4 GB (its high half, the artist's low half, 0) and each artist on a 4 GB boundary (its low half 0, its high
    half one of this file's: conftest.high_halves). Both are in the sanitizer's application memory; mapped once a
    process, never freed."""
    low = at = 0
    artists = {}
    highs = high_halves(0x40)

    @classmethod
    def album(cls, s):
        if not cls.low:
            cls.low = libc.mmap(0, 1 << 20, 3, 0x62, -1, 0)  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT
            assert cls.low and cls.low < 1 << 32
            cls.at = cls.low
        p, cls.at = cls.at, cls.at + len(s) + 1
        assert cls.at < cls.low + (1 << 20)
        C.memmove(p, s + b'\0', len(s) + 1)
        return p

    @classmethod
    def artist(cls, s):
        if s not in cls.artists:
            p = cls.highs[len(cls.artists)] << 32
            assert libc.mmap(p, 0x1000, 3, 0x100022, -1, 0) == p  # MAP_FIXED_NOREPLACE
            C.memmove(p, s + b'\0', len(s) + 1)
            cls.artists[s] = p
        return cls.artists[s]


class Rec:
    """A stSongInfo as far as a 64-bit host can hold one: an 8-byte pointer at 0x14 overruns 0x18, so album and artist
    share a record only through Split's addresses."""
    def __init__(self, album=None, path=None, artist=None, album_artist=None, id=0):
        self.raw, self.keep = C.create_string_buffer(0x60), []
        struct.pack_into('<i', self.raw, 0, id)
        both = album is not None and artist is not None
        for off, s in ((0x0c, path), (0x14, album), (0x18, artist), (0x24, album_artist)):
            if s is None:
                continue
            s = s if isinstance(s, bytes) else s.encode()
            if both and off in (0x14, 0x18):
                p = Split.album(s) if off == 0x14 else Split.artist(s)
                struct.pack_into('<I' if off == 0x14 else '<Q', self.raw, off, p)  # the album's high half is the artist's
            else:
                self.keep.append(C.create_string_buffer(s))
                struct.pack_into('<Q', self.raw, off, C.addressof(self.keep[-1]))
        self.p = C.addressof(self.raw)


# Home's cubic filter

def test_bspline_weights_sum_to_256_and_are_never_negative(fw):
    g = api(fw)
    for t in range(256):
        w = (C.c_uint * 4)()
        g.bspline_w(t, w)
        assert sum(w) == 256, t
        assert all(x <= 256 for x in w), t  # unsigned: a negative would wrap past this


def test_bspline_weights_are_the_cubic_b_spline(fw):
    g = api(fw)
    for t in range(256):
        f = t / 256
        want = ((1 - f) ** 3 / 6, (3 * f**3 - 6 * f**2 + 4) / 6, (-3 * f**3 + 3 * f**2 + 3 * f + 1) / 6, f**3 / 6)
        w = (C.c_uint * 4)()
        g.bspline_w(t, w)
        for k in range(4):
            assert abs(w[k] - 256 * want[k]) <= 1.5, (t, k)


def test_bspline_at_phase_0_is_a_sixth_four_sixths_a_sixth(fw):
    w = (C.c_uint * 4)()
    api(fw).bspline_w(0, w)
    assert list(w) == [43, 171, 42, 0]


def test_bspline_mirrors_about_half_a_texel(fw):
    g = api(fw)
    for t in range(1, 256):
        a, b = (C.c_uint * 4)(), (C.c_uint * 4)()
        g.bspline_w(t, a)
        g.bspline_w(256 - t, b)
        assert all(abs(a[k] - b[3 - k]) <= 1 for k in range(4)), t


def sharpened(values):
    out = []
    for i, c in enumerate(values):
        prev, nxt = values[max(i - 1, 0)], values[min(i + 1, len(values) - 1)]
        out.append(0xff000000)
        for s in (0, 8, 16):
            v = (6 * (c >> s & 255) - (prev >> s & 255) - (nxt >> s & 255) + 2) >> 2
            out[-1] |= min(255, max(0, v)) << s
    return out


def test_sharpen_is_the_kernel_with_its_ends_their_own_neighbours(fw):
    g, rng = api(fw), random.Random(1)
    for n in (1, 2, 3, 8, 64):
        t = exact(C.c_uint, n)
        t[:] = [rng.getrandbits(32) for _ in range(n)]
        want = sharpened(list(t))
        g.home_sharpen(t, n, 1)
        assert list(t) == want
        libc.free(C.addressof(t))


def test_sharpen_leaves_a_flat_area_alone_and_clamps_an_edge(fw):
    g = api(fw)
    t = (C.c_uint * 4)(0x804020, 0x804020, 0x804020, 0x804020)
    g.home_sharpen(t, 4, 1)
    assert list(t) == [0xff804020] * 4
    t = (C.c_uint * 4)(0, 0, 0xffffff, 0xffffff)
    g.home_sharpen(t, 4, 1)
    assert [x & 255 for x in t] == [0, 0, 255, 255]  # would ring below 0 and past 255


def test_sharpen_walks_a_column_by_its_step(fw):
    g, rng = api(fw), random.Random(2)
    step, n = 5, 7
    data = [rng.getrandbits(32) for _ in range(step * n)]
    t = (C.c_uint * len(data))(*data)
    g.home_sharpen(t, n, step)
    assert list(t[::step]) == sharpened(data[::step])
    for i, v in enumerate(t):
        assert i % step == 0 or v == data[i]


def home_tex(fw, tw, th, value):
    """home.tex at tw x th inside its border, each texel value(memory column, memory row), in an exact allocation."""
    home = fw.var('coverflow__home', Home)
    ts = tw + 3
    tex = exact(C.c_uint, ts * (th + 3))
    for y in range(th + 3):
        for x in range(ts):
            tex[y * ts + x] = value(x, y)
    home.tex, home.tw = C.addressof(tex), tw
    return tex


def test_a_flat_cover_renders_flat_at_any_phase(fw):
    g = api(fw)
    tex = home_tex(fw, 206, 319, lambda x, y: 0xff123456)
    d = exact(C.c_uint, HOME_PW * HOME_H)
    for u, v in ((0, 0), (128, 128), (257, 3), (1111, 2222), (18 * 256, 29 * 256)):
        g.home_render(d, HOME_PW, u, v)
        assert all(x == 0xff123456 for x in d), (u, v)
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_a_ramp_comes_out_at_the_sub_pixel_position(fw):
    """A cubic B-spline reproduces straight lines: the output of a ramp reads the position sampled."""
    g = api(fw)
    tex = home_tex(fw, 206, 200 + 90, lambda x, y: 0xff400000 | x | min(y, 255) << 8)
    d = exact(C.c_uint, HOME_PW * HOME_H)
    for u, v in ((0, 0), (64, 0), (128, 128), (1000, 192), (16 * 256 + 37, 0)):
        g.home_render(d, HOME_PW, u, v)
        for py, px in ((0, 0), (100, 50), (150, 187), (163, 100)):
            c = d[py * HOME_PW + px]
            assert abs((c & 255) - (u / 256 + px + 1)) <= 1, (u, v, px)
            assert abs((c >> 8 & 255) - (v / 256 + py + 1)) <= 1, (u, v, py)
            assert c >> 16 == 0xff40
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_render_at_the_pan_s_far_corner_stays_inside_the_border(fw):
    g = api(fw)
    tw, th = 206, 319
    tex = home_tex(fw, tw, th, lambda x, y: 0xff000000)
    d = exact(C.c_uint, HOME_PW * HOME_H)
    g.home_render(d, HOME_PW, (tw - HOME_PW) * 256, (th - HOME_H) * 256)  # an overrun is the sanitizer's
    assert d[-1] == 0xff000000
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_render_honours_the_bitmap_s_pitch(fw):
    g = api(fw)
    tex = home_tex(fw, 206, 319, lambda x, y: 0xffffffff)
    pitch = HOME_PW + 12
    d = exact(C.c_uint, pitch * HOME_H)
    g.home_render(d, pitch, 300, 300)
    assert all(d[y * pitch + HOME_PW - 1] == 0xffffffff and d[y * pitch + HOME_PW] == 0 for y in range(0, HOME_H, 7))
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


# Home's pan

def fit(fw, w, h):
    a, b = C.c_int(), C.c_int()
    api(fw).home_fit(w, h, r(a), r(b))
    return a.value, b.value


@pytest.mark.parametrize('w, h', ((0, 0), (0, 100), (100, 0), (8193, 100), (100, 8193), (2**31, 2**31)))
def test_home_fit_unknown_sizes_fill_the_panel(fw, w, h):
    assert fit(fw, w, h) == (HOME_PW, HOME_H)


def test_home_fit_just_covers_the_panel_at_its_proportions(fw):
    rng = random.Random(3)
    for _ in range(500):
        w, h = rng.randint(1, 8192), rng.randint(1, 8192)
        fw_, fh = fit(fw, w, h)
        assert fw_ >= HOME_PW and fh >= HOME_H
        assert fw_ == HOME_PW or fh == HOME_H  # one side exact: just covering
        if fw_ == HOME_PW:
            assert fh == math.ceil(HOME_PW * h / w)
        else:
            assert fw_ == math.ceil(HOME_H * w / h)


@pytest.mark.parametrize('w, h, want', (
    (188, 290, (188, 290)), (376, 580, (188, 290)), (500, 500, (290, 290)), (1000, 100, (2900, 290)), (100, 1000, (188, 1880)),
    (8192, 8192, (290, 290)), (8192, 1, (2375680, 290)), (1, 8192, (188, 1540096))))
def test_home_fit_exact_cases(fw, w, h, want):
    assert fit(fw, w, h) == want


@pytest.mark.parametrize('period', (20000, 26000))
def test_drift_is_a_ping_pong_of_its_period(fw, period):
    g = api(fw)
    home = fw.var('coverflow__home', Home)
    at = lambda ms: (setattr(home, 'drift_ms', ms), g.drift_at(30, period))[1]
    step = 30 * 256 // (period >> 4) + 1  # time counts in 16 ms steps
    assert at(0) == 0
    assert at(period) == 30 * 256  # the far end
    assert abs(at(period // 2) - 15 * 256) <= step
    assert abs(at(period + period // 2) - 15 * 256) <= step  # on the way back
    assert at(2 * period) == 0  # a whole cycle later
    assert at(2 * period + 123) == at(123)
    for ms in range(0, period, 997):
        assert at(ms) == at(2 * period - ms), ms
        assert at(ms) <= at(ms + 16)  # rising to the far end


def test_drift_range_zero_never_moves(fw):
    g = api(fw)
    fw.var('coverflow__home', Home).drift_ms = 7777
    assert g.drift_at(0, 20000) == 0


def test_drift_holds_its_largest_range_in_32_bits(fw):
    g = api(fw)
    fw.var('coverflow__home', Home).drift_ms = 26000
    assert g.drift_at(99, 26000) == 99 * 256


def test_the_pan_starts_at_the_old_crop_and_moves_each_axis_by_its_own_period(fw):
    g = api(fw)
    home = fw.var('coverflow__home', Home)
    home.u0, home.v0, home.rx, home.ry = 10, 20, 18, 29
    assert (g.pan_u(), g.pan_v()) == (10 * 256, 20 * 256)
    home.drift_ms = 20000
    assert g.pan_u() == (10 + 18) * 256  # X's far end
    assert g.pan_v() == 20 * 256 + 29 * 256 * (20000 >> 4) // (26000 >> 4)


# The shelf's geometry

def pose(g, t):
    p = Pose()
    g.pose(t, r(p))
    return p


def test_the_angle_tables_are_cosine_and_sine_to_60_degrees(fw):
    cos, sin = fw.var('coverflow__cf_cos', C.c_int * 17), fw.var('coverflow__cf_sin', C.c_int * 17)
    for k in range(17):
        a = math.radians(60 * k / 16)
        assert abs(cos[k] - 65536 * math.cos(a)) <= 1 and abs(sin[k] - 65536 * math.sin(a)) <= 1, k


def test_a_side_cover_s_pose_by_its_distance(fw):
    g = api(fw)
    p = pose(g, 0)
    assert (p.xc, p.zc, p.c, p.s, p.bright) == (0, 0, 65536, 0, 256)
    p = pose(g, ONE)
    assert (p.xc, p.zc, p.c, p.s, p.bright) == (16 * 128, 16 * 77, 0, 65536, 192)  # #329's shelf: edge-on
    p = pose(g, 2 * ONE)
    assert (p.xc, p.zc, p.bright) == (16 * (128 + 35), 16 * 77, 128)
    assert pose(g, 3 * ONE).bright == 128  # the third out stays (CF_THIRD)
    # and every one past it, drawn whole for as long as any of it shows (a thumb theme's wide ring)
    assert pose(g, 13 * ONE // 4).bright == pose(g, 7 * ONE // 2).bright == pose(g, 15 * ONE // 2).bright == 128


def test_a_cover_s_pose_is_continuous_and_keeps_turning_in_step(fw):
    g = api(fw)
    last = pose(g, 0)
    for t in range(256, 3 * ONE, 257):
        p = pose(g, t)
        assert 0 <= p.xc - last.xc <= 16 and 0 <= p.zc - last.zc <= 16 and last.bright - 2 <= p.bright <= last.bright, t
        assert abs(math.hypot(p.c, p.s) - 65536) < 90  # the 90 degree table's chords: 5.6 degrees a step
        last = p


def test_project_and_unproject_are_inverse(fw):
    g = api(fw)
    for t in (0, 1000, ONE // 2, ONE, 3 * ONE // 2):
        p = pose(g, t)
        for u in range(-1260, 1261, 40):
            dx = g.project(r(p), u)
            got, h = C.c_int(), C.c_int()
            assert g.unproject(r(p), dx, r(got), r(h)), (t, u)
            assert abs(got.value - u) <= 16, (t, u)
            assert h.value > 0


def test_unproject_misses_past_the_cover_s_edge(fw):
    g = api(fw)
    p = pose(g, 0)
    u, h = C.c_int(), C.c_int()
    assert g.unproject(r(p), 1279, r(u), r(h)) and u.value == 1279 and h.value == ART * 256
    assert not g.unproject(r(p), 1280, r(u), r(h))
    assert g.unproject(r(p), -1280, r(u), r(h))
    assert not g.unproject(r(p), -1281, r(u), r(h))


def test_the_ring_slot_nearest_first_and_the_side_being_left_farther(fw):
    g = api(fw)
    assert g.slot(0, 0, 0) == 3
    assert (g.slot(100, 1, 0), g.slot(100, 1, 1)) == (4, 2)  # moving right: the right cover is nearer
    assert (g.slot(-100, 1, 0), g.slot(-100, 1, 1)) == (2, 4)


class Ring:
    """The seven covers round the centre, ring[j] the cover j - 3, in a thumb theme's wide ring of
    fifteen (its shelf draws seven a side), the ends left out: .at is what coverflow_render takes."""
    def __init__(self, *covers):
        self.wide = (C.c_void_p * 15)(*([None] * 4 + list(covers)))
        self.at = C.c_void_p(C.addressof(self.wide) + 4 * C.sizeof(C.c_void_p))

    def __setitem__(self, j, cover):
        self.wide[j + 4] = cover


def corner_inset(fw):
    """coverflow_render fills cf_inset once."""
    g = api(fw)
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()  # kept alive: coverflow_render reads its array
    g.coverflow_render(d, VIEW_W, 0, ring.at)
    libc.free(C.addressof(d))
    return g


def test_hit_the_centre_cover_s_own_box(fw):
    g = api(fw)
    assert g.coverflow_hit(0, CX, 93) == 0
    assert g.coverflow_hit(0, CX - 80, 93) == 0 and g.coverflow_hit(0, CX + 79, 93) == 0
    assert g.coverflow_hit(0, CX, TOP) == 0 and g.coverflow_hit(0, CX, TOP + ART - 1) == 0
    assert g.coverflow_hit(0, CX, TOP - 1) == MISS
    assert g.coverflow_hit(0, CX, TOP + ART) == MISS  # the reflection is no target


def test_hit_a_rounded_corner_is_left_out(fw):
    g = corner_inset(fw)
    assert g.coverflow_hit(0, CX - 80, TOP) == MISS and g.coverflow_hit(0, CX + 79, TOP + ART - 1) == MISS
    assert g.coverflow_hit(0, CX - 70, TOP) == 0  # past the 11 px corner
    assert g.coverflow_hit(0, CX - 80, TOP + 20) == 0


def test_hit_across_the_shelf_at_rest(fw):
    g = api(fw)
    got = [g.coverflow_hit(0, x, 93) for x in range(VIEW_W)]
    hits = [h for h in got if h != MISS]
    assert sorted(set(hits)) == [-3, -2, -1, 0, 1, 2, 3]  # the third out stays, at CF_BRIGHT2
    assert hits == sorted(hits)
    assert got[0] == -3 and got[-1] == 3  # the third out runs off the frame
    assert got[CX - 81] == -1 and got[CX + 80] == 1


def test_hit_mirrors_about_the_centre_line(fw):
    g = api(fw)
    for x in range(0, 2 * CX, 3):
        for y in (40, 93, 150):
            a, b = g.coverflow_hit(0, x, y), g.coverflow_hit(0, 2 * CX - 1 - x, y)
            assert a == (b if b == MISS else -b), (x, y)


def texture(j):
    """A flat cover whose green over red says which: brightness scales both."""
    t = exact(C.c_uint, ART * ART)
    t[:] = [0xff0000f0 | (30 + 30 * j) << 8] * (ART * ART)
    return t


def bgra(p):
    """A texel (RGBA8888, red low) as the frame holds it: the screen's BGRA8888, red at bit 16."""
    return p & 0xff00ff00 | (p & 255) << 16 | p >> 16 & 255


def test_hit_names_the_cover_drawn_there(fw):
    g = api(fw)
    ring = [texture(j) for j in range(7)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    wide = Ring(*[C.addressof(t) for t in ring])
    pointers = wide.at
    checked = 0
    for frac in (0, 8000, 16000, 32767, -8000, -32768):
        g.coverflow_render(d, VIEW_W, frac, pointers)

        def cover(x, y):
            c = d[y * VIEW_W + x]
            red, green = c >> 16 & 255, c >> 8 & 255  # BGRA8888
            if red < 8:
                return None
            return round(((green / red * 240) - 30) / 30)
        for y in (26, 45, 70, 90):  # above the horizon: no reflection reaches here
            for x in range(2, VIEW_W - 2):
                if len({cover(x + k, y) for k in (-2, -1, 0, 1, 2)} | {cover(x, y - 1), cover(x, y + 1)}) > 1:
                    continue  # an edge's blend
                j = cover(x, y)
                got = g.coverflow_hit(frac, x, y)
                if j is None:  # nothing drawn: no cover, or one past the seven this ring holds
                    assert got == MISS or abs(got) > 3, (frac, x, y)
                elif abs((j - 3) * ONE - frac) < 13 * ONE // 4:  # at least a quarter bright
                    assert got == j - 3, (frac, x, y)
                    checked += 1
    assert checked > 2000
    for t in (*ring, d):
        libc.free(C.addressof(t))


def test_the_centre_cover_comes_out_texel_for_texel(fw):
    g = api(fw)
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xff000000 | x | y << 8 | 0x55 << 16 for y in range(ART) for x in range(ART)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()
    ring[3] = C.addressof(tex)
    g.coverflow_render(d, VIEW_W, 0, ring.at)
    for y in range(12, ART - 12):
        for x in range(ART):
            assert d[(TOP + y) * VIEW_W + CX - 80 + x] == bgra(tex[y * ART + x]), (x, y)
    for t in (tex, d):
        libc.free(C.addressof(t))


def test_the_reflection_mirrors_the_cover_and_fades_out_in_46_px(fw):
    g = api(fw)
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xff000000 | (y + 60) for y in range(ART) for x in range(ART)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()
    ring[3] = C.addressof(tex)
    g.coverflow_render(d, VIEW_W, 0, ring.at)
    bottom = TOP + ART
    for k in range(46):
        red = d[(bottom + k) * VIEW_W + CX] >> 16 & 255  # BGRA8888
        mirrored = ART - 1 - k + 60
        assert abs(red - mirrored * 90 / 256 * (1 - (k + .5) / 46)) <= 2, k
    assert all(d[y * VIEW_W + CX] == 0xff000000 for y in range(bottom + 46, VIEW_H))
    for t in (tex, d):
        libc.free(C.addressof(t))


def test_every_pixel_of_a_frame_is_opaque_and_stays_in_the_frame(fw):
    g = api(fw)
    ring = [texture(j) for j in range(7)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    wide = Ring(*[C.addressof(t) for t in ring])
    pointers = wide.at
    for frac in range(-32768, 32768, 7001):
        g.coverflow_render(d, VIEW_W, frac, pointers)
        assert all(x >> 24 == 255 for x in d)
    for t in (*ring, d):
        libc.free(C.addressof(t))


def test_covers_mirror_when_the_frame_does(fw):
    g = api(fw)
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xff000000 | (x + 40) | (y + 40) << 8 for y in range(ART) for x in range(ART)]
    flip = exact(C.c_uint, ART * ART)
    flip[:] = [0xff000000 | (ART - 1 - x + 40) | (y + 40) << 8 for y in range(ART) for x in range(ART)]
    a, b = exact(C.c_uint, VIEW_W * VIEW_H), exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()
    ring[2] = C.addressof(tex)
    g.coverflow_render(a, VIEW_W, 0, ring.at)
    ring[2], ring[4] = None, C.addressof(flip)
    g.coverflow_render(b, VIEW_W, 0, ring.at)
    for y in range(0, VIEW_H, 11):
        for x in range(0, 2 * CX):
            assert a[y * VIEW_W + x] == b[y * VIEW_W + 2 * CX - 1 - x], (x, y)
    for t in (tex, flip, a, b):
        libc.free(C.addressof(t))


# A decoded image into a texture

def bitmap(fw, w, h, pixels, format=1, stride=None, loads=True):
    """widget_load_image: a bitmap_t of w, h and format, pixels the bytes its lock gives."""
    def load(owner, url, b, *_):
        if not loads:
            return 1
        C.c_uint.from_address(b).value, C.c_uint.from_address(b + 4).value = w, h
        C.c_ushort.from_address(b + 14).value = format
        return 0
    fw.mock('widget_load_image', load)
    fw.mock('bitmap_lock_buffer_for_read', lambda *_: C.addressof(pixels))
    fw.mock('bitmap_get_line_length', lambda *_: stride or 4 * w)


def rgba(w, h, colour):
    data = bytes(b for y in range(h) for x in range(w) for b in colour(x, y))
    return (C.c_ubyte * len(data)).from_buffer_copy(data)


def decoded(fw, unload=1):
    out = exact(C.c_uint, ART * ART)
    ok = fw.fn('cf_decode', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'x', out, unload)
    return ok, out


def test_decode_a_square_cover_is_copied(fw):
    pixels = rgba(ART, ART, lambda x, y: (x, y, 7, 255))
    bitmap(fw, ART, ART, pixels)
    ok, out = decoded(fw)
    assert ok == 1 and all(out[y * ART + x] == 0xff070000 | y << 8 | x for y in range(ART) for x in range(ART))
    assert fw.calls('widget_unload_image')


def test_decode_a_wide_cover_sits_between_black_bars(fw):
    pixels = rgba(320, 160, lambda x, y: (200, 0, 0, 255))
    bitmap(fw, 320, 160, pixels)
    _, out = decoded(fw)
    column = [out[y * ART + 80] for y in range(ART)]
    assert column == [0xff000000] * 40 + [0xff0000c8] * 80 + [0xff000000] * 40


def test_decode_a_tall_cover_too(fw):
    pixels = rgba(80, 160, lambda x, y: (0, 200, 0, 255))
    bitmap(fw, 80, 160, pixels)
    _, out = decoded(fw)
    assert list(out[80 * ART:81 * ART]) == [0xff000000] * 40 + [0xff00c800] * 80 + [0xff000000] * 40


def test_decode_scales_by_the_nearest_texel_from_a_larger_one(fw):
    pixels = rgba(320, 320, lambda x, y: (x // 2, y // 2, 0, 255))
    bitmap(fw, 320, 320, pixels)
    _, out = decoded(fw)
    assert all(out[y * ART + x] == 0xff000000 | y << 8 | x for y in (0, 80, 159) for x in (0, 80, 159))


def test_decode_blends_a_transparent_pixel_over_the_black_page(fw):
    pixels = rgba(ART, ART, lambda x, y: (200, 100, 50, 128))
    bitmap(fw, ART, ART, pixels)
    _, out = decoded(fw)
    assert out[0] == 0xff000000 | 200 * 128 // 255 | (100 * 128 // 255) << 8 | (50 * 128 // 255) << 16


@pytest.mark.parametrize('format, order', ((1, (0, 1, 2, 3)), (2, (3, 2, 1, 0)), (3, (2, 1, 0, 3)), (4, (1, 2, 3, 0))))
def test_decode_reads_each_32_bit_format(fw, format, order):
    r_, g_, b_ = 10, 20, 30
    colour = [0] * 4
    colour[order[0]], colour[order[1]], colour[order[2]], colour[order[3]] = r_, g_, b_, 255
    pixels = rgba(ART, ART, lambda x, y: colour)
    bitmap(fw, ART, ART, pixels, format)
    assert decoded(fw)[1][0] == 0xff000000 | r_ | g_ << 8 | b_ << 16


def test_decode_honours_the_stride(fw):
    data = bytearray((ART * 4 + 8) * ART)
    for y in range(ART):
        data[y * (ART * 4 + 8):y * (ART * 4 + 8) + ART * 4] = bytes((1, y, 3, 255)) * ART
    pixels = (C.c_ubyte * len(data)).from_buffer(data)
    bitmap(fw, ART, ART, pixels, stride=ART * 4 + 8)
    assert decoded(fw)[1][159 * ART] == 0xff030001 | 159 << 8


@pytest.mark.parametrize('w, h, format', ((0, 160, 1), (160, 0, 1), (4097, 160, 1), (160, 4097, 1), (160, 160, 0), (160, 160, 5)))
def test_decode_refuses_what_it_can_t_read(fw, w, h, format):
    pixels = (C.c_ubyte * 4)()
    bitmap(fw, w, h, pixels, format)
    assert decoded(fw)[0] == 0 and fw.calls('widget_unload_image')


def test_decode_of_no_image_or_a_kept_one(fw):
    pixels = rgba(ART, ART, lambda x, y: (1, 2, 3, 255))
    bitmap(fw, ART, ART, pixels, loads=False)
    assert decoded(fw)[0] == 0
    bitmap(fw, ART, ART, pixels)
    assert decoded(fw, unload=0)[0] == 1 and not fw.calls('widget_unload_image')  # a theme image: the manager keeps it


def tex_rig(fw, w, h, pixels, **kw):
    bitmap(fw, w, h, pixels, **kw)
    frame = C.create_string_buffer(0x40)
    fw.mock('bitmap_create_ex', lambda *_: C.addressof(frame))
    return fw.fn('coverflow__home_tex', C.c_int, C.c_char_p), fw.var('coverflow__home', Home), frame


def test_home_tex_zooms_a_cover_by_a_tenth_with_its_pan_s_margin(fw):
    pixels = rgba(300, 300, lambda x, y: (10, 20, 30, 255))
    tex, home, frame = tex_rig(fw, 300, 300, pixels)
    assert tex(b'x') == 1
    assert (home.tw, home.rx, home.ry) == (319, 29, 29)  # the fit, 290 square, plus 10%
    assert (home.u0, home.v0, home.du) == ((290 - 188) // 2, 0, -1)  # the old crop, the frame not drawn yet
    assert struct.unpack_from('<H', frame, 0xc)[0] & 1  # opaque


def test_home_tex_a_flat_cover_stays_flat_out_to_its_border(fw):
    pixels = rgba(300, 300, lambda x, y: (10, 20, 30, 255))
    tex, home, _ = tex_rig(fw, 300, 300, pixels)
    tex(b'x')
    ts = home.tw + 3
    data = (C.c_uint * (ts * (319 + 3))).from_address(home.tex)
    assert all(v == 0xff1e140a for v in data)


def test_home_tex_blends_over_the_black_page(fw):
    pixels = rgba(300, 300, lambda x, y: (200, 200, 200, 128))
    tex, home, _ = tex_rig(fw, 300, 300, pixels)
    tex(b'x')
    want = 200 * 128 * 255 // (255 * 255)
    assert (C.c_uint * 8).from_address(home.tex)[5] & 255 == want


def test_home_tex_a_load_that_fails_says_so(fw):
    pixels = (C.c_ubyte * 4)()
    tex, _, _ = tex_rig(fw, 300, 300, pixels, loads=False)
    assert tex(b'x') == -1


@pytest.mark.parametrize('w, h, format', ((8192, 1, 1), (1, 8192, 1), (300, 300, 5), (300, 300, 0), (8193, 300, 1), (0, 300, 1)))
def test_home_tex_a_cover_it_won_t_pan_stays_still(fw, w, h, format):
    pixels = (C.c_ubyte * 4)()
    tex, home, _ = tex_rig(fw, w, h, pixels, format=format)
    assert tex(b'x') == 0 and home.tw == 0 and fw.calls('widget_unload_image')


# Touch: a fling's speed

def touches(fw, *samples):
    g = api(fw)
    for x, t in samples:
        g.sample(x, t)
    return g


def test_velocity_with_no_samples_or_one(fw):
    g = api(fw)
    assert g.velocity(0) == 0
    g.sample(10, 100)
    assert g.velocity(100) == 0  # no time passed


def test_velocity_is_px_a_second(fw):
    assert touches(fw, (0, 0), (10, 10), (20, 20)).velocity(20) == 1000


def test_velocity_is_signed(fw):
    assert touches(fw, (100, 0), (80, 10)).velocity(10) == -2000


def test_velocity_resting_60_ms_is_none(fw):
    g = touches(fw, (0, 0), (10, 10))
    assert g.velocity(70) == 1000
    assert g.velocity(71) == 0


def test_velocity_looks_back_100_ms_only(fw):
    g = touches(fw, (0, 0), (500, 10), (510, 110), (520, 120))
    assert g.velocity(120) == 1000
    g = touches(fw, (900, 0), (950, 20), (1050, 120))
    assert g.velocity(120) == 1000  # exactly 100 ms back is in: 100 px in 100 ms


@pytest.mark.parametrize('start, end, want', ((0, 100, 6000), (100, 0, -6000), (0, 60, 6000)))
def test_velocity_clamps_a_burst_of_queued_moves(fw, start, end, want):
    assert touches(fw, (start, 0), (end, 10)).velocity(10) == want


def test_velocity_the_ring_keeps_the_last_8(fw):
    g = api(fw)
    for k in range(30):
        g.sample(5 * k + (9000 if k < 22 else 0), 10 * k)  # the first 22, far off, have left the ring
    assert g.velocity(290) == 500
    assert fw.var('coverflow__tc', Touch).n == 30


def test_velocity_through_the_clock_s_wrap(fw):
    base = 2**32 - 15
    assert touches(fw, (0, base), (10, base + 10), (20, (base + 20) % 2**32)).velocity((base + 20) % 2**32) == 1000


def test_sample_fills_the_ring_in_order(fw):
    g = api(fw)
    tc = fw.var('coverflow__tc', Touch)
    for k in range(9):
        g.sample(k, 100 + k)
    assert list(tc.sx) == [8, 1, 2, 3, 4, 5, 6, 7] and tc.st[0] == 108 and tc.n == 9


@pytest.mark.parametrize('a, b, q', ((7, 2, 3), (-7, 2, -4), (6, 2, 3), (-6, 2, -3), (0, 5, 0), (-1, 5, -1), (1, 5, 0)))
def test_floor_div_rounds_down_past_zero(fw, a, b, q):
    assert api(fw).floor_div(a, b) == q


@pytest.mark.parametrize('off, want', ((0, 0), (54, 0), (55, 110), (56, 110), (-54, 0), (-55, 0), (-56, -110), (165, 220), (-165, -110), (110, 110)))
def test_round_stride_to_the_nearest_whole_cover(fw, off, want):
    assert api(fw).round_stride(off, 110) == want


def cf(fw):
    return fw.var('cf', Cf)


@pytest.mark.parametrize('slide, screen, timer, live', ((1, COVERS, 0, 1), (0, COVERS, 0, 0), (1, TRACKS, 0, 0), (1, PREPARING, 0, 0), (1, COVERS, 5, 0)))
def test_covers_live_needs_the_covers_with_no_change_pending(fw, slide, screen, timer, live):
    c = cf(fw)
    c.slide, c.screen, c.timer = slide, screen, timer
    assert api(fw).covers_live() == live


class Shelf:
    """An artist's slide_menu: its index, offset and animator in plain memory, n albums, stride 110."""
    def __init__(self, fw, n=4, index=1, offset=0, scoped=ARTIST_ALBUMS):
        self.fw, self.g, self.raw, self.anim = fw, api(fw), C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        self.p = C.addressof(self.raw)
        self.set(0x78, index)
        self.set(0x84, 10)  # the spacer
        self.set(0x94, offset)
        fw.mock('widget_count_children', lambda *_: n)
        fw.mock('slide_menu_item_width', lambda *_: 100)
        fw.mock('widget_animator_scroll_create', lambda *_: C.addressof(self.anim))
        fw.mock('widget_animator_on', lambda *_: 1)
        self.goals = []
        fw.mock('widget_animator_scroll_set_params', lambda a, x0, y0, x1, y1, _: self.goals.append(C.c_int(x1).value))
        c = cf(fw)
        c.slide, c.screen, c.scoped = self.p, COVERS, scoped

    def set(self, off, v):
        struct.pack_into('<i', self.raw, off, v)

    def get(self, off):
        return struct.unpack_from('<i', self.raw, off)[0]

    @property
    def ms(self):
        return struct.unpack_from('<i', self.anim, 0x20)[0]


def test_stride_is_an_item_and_its_spacer(fw):
    s = Shelf(fw)
    assert s.g.stride_of(s.p) == 110


def test_the_library_s_covers_are_unbounded(fw):
    s = Shelf(fw, scoped=0)
    assert s.g.bound(9999, 110, 1) == 9999 and s.g.unbound(-9999, 110) == -9999


def test_bound_stops_at_the_ends_without_rubber(fw):
    s = Shelf(fw, n=4, index=1)
    hi, lo = 110, -220  # one album before this one, two after
    assert [s.g.bound(x, 110, 0) for x in (hi, hi + 1, 500, lo, lo - 1, -500, 0)] == [hi, hi, hi, lo, lo, lo, 0]


def test_the_rubber_band_is_ios_s_and_never_reaches_half_a_cover(fw):
    s = Shelf(fw, n=4, index=1)
    d = 2 * 110 // 5
    assert s.g.bound(50, 110, 1) == 50  # inside: nothing
    last = 110
    for x in range(1, 2000, 7):
        got = s.g.bound(110 + x, 110, 1)
        want = x * .55 / (1 + x * .55 / d)
        assert abs(got - 110 - want) <= 1, x
        assert last <= got < 110 + 55, x  # it gives ever less, and the centre stays clear of the wrap
        last = got
    assert s.g.bound(-220 - 100, 110, 1) == -220 - (s.g.bound(110 + 100, 110, 1) - 110)  # the other end alike


def test_unbound_undoes_the_rubber_band(fw):
    s = Shelf(fw, n=4, index=1)
    for x in range(1, 300, 3):
        shown = s.g.bound(110 + x, 110, 1)  # both truncate, so the finger lies between two shown pixels' inverses
        if shown > 110 and shown + 1 - 110 < 44:
            assert s.g.unbound(shown, 110) <= 110 + x <= s.g.unbound(shown + 1, 110) + 1, x


def test_unbound_inside_or_past_the_band_s_limit_is_itself(fw):
    s = Shelf(fw, n=4, index=1)
    assert s.g.unbound(50, 110) == 50
    assert s.g.unbound(110 + 44, 110) == 110 + 44  # a is d: the formula would divide by nothing


@pytest.mark.parametrize('offset, want', ((40, 0), (60, 110), (-40, 0), (-60, -110), (0, 0)))
def test_a_release_with_no_speed_settles_on_the_nearest_cover(fw, offset, want):
    s = Shelf(fw, scoped=0, offset=offset)
    s.g.release(0)
    if offset:
        assert s.goals == [want] and s.ms == 280


def test_a_release_at_rest_does_nothing(fw):
    s = Shelf(fw, scoped=0)
    s.g.release(0)
    assert not s.goals and not fw.calls('widget_animator_scroll_create')


def test_a_flick_carries_400_ms_and_lands_on_a_whole_cover(fw):
    s = Shelf(fw, scoped=0, offset=0)
    s.g.release(900)  # 1100 px/s of offset: 440 px on
    assert s.goals == [440]
    assert s.ms == 3000 * 440 // 1100  # cubic out starts at three times the mean


def test_a_flick_never_goes_back_against_itself(fw):
    s = Shelf(fw, scoped=0, offset=50)
    s.g.release(250)
    assert s.goals[-1] >= 110


def test_a_backward_flick_never_goes_forward(fw):
    s = Shelf(fw, scoped=0, offset=-50)
    s.g.release(-250)
    assert s.goals[-1] <= -110


@pytest.mark.parametrize('offset, v, ms', ((0, 123, 1400), (100, 131, 280), (0, 900, 1200)))
def test_a_glide_is_three_times_its_mean_time_within_its_limits(fw, offset, v, ms):
    s = Shelf(fw, scoped=0, offset=offset)
    s.g.release(v)
    assert s.ms == ms


def test_a_flick_past_an_artist_s_last_album_stops_there(fw):
    s = Shelf(fw, n=4, index=1, offset=100)
    s.g.release(3000)
    assert s.goals == [110]


def event(kind, x=0, y=0, t=1):
    raw = C.create_string_buffer(0x40)
    struct.pack_into('<i', raw, 0, kind)
    struct.pack_into('<I', raw, 8, t)
    struct.pack_into('<ii', raw, 0x18, x, y)
    return raw


DOWN, MOVE, UP, ABORT = 0x100, 0x102, 0x104, 0x107


def test_a_touch_stamps_its_own_time_or_the_clock_s(fw):
    g = api(fw)
    fw.mock('time_now_ms', lambda *_: 777)
    assert g.stamp(event(UP, t=55)) == 55 and g.stamp(event(UP, t=0)) == 777


def test_a_drag_moves_the_covers_after_the_slop(fw):
    s = Shelf(fw, scoped=0, offset=0)
    touch = s.g.touch
    touch(None, event(DOWN, 100, 50))
    tc = fw.var('coverflow__tc', Touch)
    assert tc.down and not tc.drag
    touch(None, event(MOVE, 107, 50))
    assert not tc.drag and s.get(0x94) == 0  # inside the slop: still a tap
    touch(None, event(MOVE, 110, 50))
    assert tc.drag and tc.x == 110 and s.get(0x94) == 0  # no jump by the slop
    touch(None, event(MOVE, 200, 50))
    assert s.get(0x94) == 110  # 90 px of finger is one album


def test_a_mostly_vertical_touch_is_stock_s(fw):
    s = Shelf(fw, scoped=0)
    s.g.touch(None, event(DOWN, 100, 50))
    s.g.touch(None, event(MOVE, 102, 70))
    assert not fw.var('coverflow__tc', Touch).down


def test_a_touch_on_a_changing_screen_is_not_taken(fw):
    s = Shelf(fw, scoped=0)
    cf(fw).timer = 3
    s.g.touch(None, event(DOWN, 100, 50))
    assert not fw.var('coverflow__tc', Touch).down


def test_a_touch_catches_a_moving_shelf_and_settles_it(fw):
    s = Shelf(fw, scoped=0, offset=70)
    struct.pack_into('<Q', s.raw, 0xc8, C.addressof(s.anim))
    s.g.touch(None, event(DOWN, 100, 50))
    tc = fw.var('coverflow__tc', Touch)
    assert tc.caught and fw.calls('widget_animator_pause')
    s.g.touch(None, event(UP, 100, 50, t=5))
    assert s.goals == [110]


def test_an_aborted_drag_settles_without_a_throw(fw):
    s = Shelf(fw, scoped=0)
    s.g.touch(None, event(DOWN, 100, 50, t=1))
    for k in range(1, 5):
        s.g.touch(None, event(MOVE, 100 + 10 * k, 50, t=10 * k))
    s.g.touch(None, event(ABORT, 140, 50, t=41))
    assert s.goals == [s.g.round_stride(s.get(0x94), 110)] and s.ms == 280


def test_the_lost_release_ends_at_the_next_key(fw):
    s = Shelf(fw, scoped=0, offset=70)
    s.g.touch(None, event(DOWN, 100, 50))
    s.g.lift(1, 0)
    assert not fw.var('coverflow__tc', Touch).down


# Albums apart

def test_album_cmp_the_name_case_aside(fw):
    g = api(fw)
    assert g.album_cmp(Rec('Abbey Road', '/a/x').p, Rec('ABBEY ROAD', '/a/y').p) == 0
    assert g.album_cmp(Rec('Abbey', '/a/x').p, Rec('Help', '/a/x').p) < 0


def test_album_cmp_tagged_names_come_first(fw):
    g = api(fw)
    assert g.album_cmp(Rec('Abbey', '/a/x').p, Rec('', '/a/x').p) < 0
    assert g.album_cmp(Rec(None, '/a/x').p, Rec('Abbey', '/a/x').p) > 0


def test_album_cmp_a_compilation_stays_one_album_across_folders(fw):
    g = api(fw)
    a, b = Rec('Hits', '/m/1/a.mp3', album_artist='Various'), Rec('Hits', '/m/2/b.mp3', album_artist='various')
    other = Rec('Hits', '/m/1/a.mp3', album_artist='Other')
    assert g.album_cmp(a.p, b.p) == 0 and g.album_cmp(a.p, other.p) != 0


def test_album_cmp_an_album_artist_on_one_side_tells_them_apart(fw):
    g = api(fw)
    a, b = Rec('Hits', '/m/a.mp3', album_artist='X'), Rec('Hits', '/m/a.mp3')
    assert g.album_cmp(a.p, b.p) < 0 and g.album_cmp(b.p, a.p) > 0


def test_album_cmp_otherwise_the_folder_disc_folders_as_one(fw):
    g = api(fw)
    assert g.album_cmp(Rec('A', '/m/A/CD1/1.flac').p, Rec('A', '/m/A/CD2/1.flac').p) == 0
    assert g.album_cmp(Rec('A', '/m/A/Disc 1/1.flac').p, Rec('A', '/m/A/disk_02/1.flac').p) == 0
    assert g.album_cmp(Rec('A', '/m/A/1.flac').p, Rec('A', '/m/B/1.flac').p) != 0
    assert g.album_cmp(Rec('A', '/m/a/1.flac').p, Rec('A', '/m/A/1.flac').p) != 0  # folders keep their case


def test_folder_cmp_keeps_disc_folders_apart(fw):
    g = api(fw)
    assert g.folder_cmp(Rec('A', '/m/A/CD1/1.flac').p, Rec('A', '/m/A/CD2/1.flac').p) != 0
    assert g.folder_cmp(Rec('A', '/m/A/1.flac').p, Rec('B', '/m/A/2.flac').p) == 0


def test_a_path_with_no_folder_or_none_at_all_compares(fw):
    g = api(fw)
    assert g.album_cmp(Rec('A').p, Rec('A', 'x.mp3').p) == 0
    assert g.album_cmp(Rec('A').p, Rec('A', '/m/x.mp3').p) != 0


@pytest.mark.parametrize('path, disc, want', (
    ('/m/A/1.flac', 0, '/m/A'), ('/m/A/CD1/1.flac', 1, '/m/A'), ('/m/A/CD1/1.flac', 0, '/m/A/CD1'), ('/m/A/cd12/1.flac', 1, '/m/A'),
    ('/m/A/CD 2/1.flac', 1, '/m/A'), ('/m/A/CD-2/1.flac', 1, '/m/A'), ('/m/A/Disc 2/1.flac', 1, '/m/A'), ('/m/A/DISK_03/1.flac', 1, '/m/A'),
    ('/m/A/disc3/1.flac', 1, '/m/A'), ('/m/A/Track1/1.flac', 1, '/m/A/Track1'), ('/m/ABCD1/1.flac', 1, '/m/ABCD1'),
    ('/m/2001/1.flac', 1, '/m/2001'), ('/CD1/1.flac', 1, ''), ('x.flac', 1, ''), ('/x.flac', 1, ''), ('CD1/x.flac', 1, 'CD1'), ('', 1, ''),
    ('/m/Disc/1.flac', 1, '/m/Disc'), ('/m/dis1/1.flac', 1, '/m/dis1')))
def test_album_dir(fw, path, disc, want):
    assert api(fw).album_dir(path.encode(), disc) == len(want)


def test_album_dir_hostile_sweep(fw):
    rng = random.Random(4)
    album_dir = fw.fn('album_dir', C.c_uint, C.c_void_p, C.c_int)
    for _ in range(2000):
        s = bytes(rng.choices(b'/CDcdDISKik 12_-a.', k=rng.randrange(0, 24)))
        with Exact(s + b'\0') as p:
            assert album_dir(p, 1) <= album_dir(p, 0) <= len(s), s


def records(rng):
    names = (None, '', 'Abbey', 'ABBEY', 'Help', 'help!', 'Zoo')
    artists = (None, '', 'X', 'x', 'Y')
    dirs = ('/m/A/', '/m/a/', '/m/A/CD1/', '/m/A/CD2/', '/m/B/', 'top', '')
    return [Rec(rng.choice(names), rng.choice(dirs) + 't.mp3', album_artist=rng.choice(artists), id=k + 1) for k in range(40)]


def sign(x):
    return (x > 0) - (x < 0)


def test_album_cmp_is_an_order_qsort_can_trust(fw):
    g, rng = api(fw), random.Random(5)
    v = records(rng)
    table = [[sign(g.album_cmp(a.p, b.p)) for b in v] for a in v]
    n = len(v)
    for i in range(n):
        assert table[i][i] == 0
        for j in range(n):
            assert table[i][j] == -table[j][i]
            for k in range(n):
                if table[i][j] <= 0 and table[j][k] <= 0:
                    assert table[i][k] <= 0, (i, j, k)


def test_album_id_is_the_same_exactly_when_album_cmp_says_so(fw):
    g, rng = api(fw), random.Random(6)
    v = records(rng)
    for a in v:
        for b in v:
            assert (g.album_id(a.p) == g.album_id(b.p)) == (g.album_cmp(a.p, b.p) == 0)


def test_album_id_the_unknown_card_is_one_album_and_never_0(fw):
    g = api(fw)
    assert g.album_id(Rec('x', '/a/1', id=-1).p) == g.album_id(Rec('y', '/b/2', id=-1).p) != 0


def test_fnv_separates_its_fields(fw):
    g = api(fw)
    assert g.fnv(g.fnv(FNV, b'ab'), b'c') != g.fnv(g.fnv(FNV, b'a'), b'bc')
    assert g.fnv(FNV, None) == FNV * 16777619 % 2**32


def rows(*recs, order=None):
    v = (Row * max(1, len(recs)))()
    for i, rec in enumerate(recs):
        v[i].a, v[i].j = rec.p, i if order is None else order[i]
    return v


def test_name_cmp_case_aside_and_no_name_is_empty(fw):
    g = api(fw)
    assert g.name_cmp(Rec('abc').p, Rec('ABC').p) == 0
    assert g.name_cmp(Rec('abc').p, Rec('abd').p) < 0
    assert g.name_cmp(Rec(None).p, Rec('').p) == 0


def test_a_tie_goes_by_the_list_s_order(fw):
    g = api(fw)
    a, b = Row(None, 3), Row(None, 7)
    assert g.tie(0, r(a), r(b)) < 0 and g.tie(0, r(b), r(a)) > 0 and g.tie(0, r(a), r(a)) == 0
    assert g.tie(5, r(a), r(b)) == 5 and g.tie(-5, r(b), r(a)) == -5


def test_by_name_and_by_album_sort_with_ties_in_order(fw):
    g = api(fw)
    recs = [Rec('b', '/1/x'), Rec('A', '/2/x'), Rec('a', '/1/x'), Rec('B', '/1/x')]
    for fn, want in ((fw.lib.coverflow__by_name, [1, 2, 0, 3]), (fw.lib.coverflow__by_album, [2, 1, 0, 3])):
        v = rows(*recs)
        libc.qsort(v, 4, C.sizeof(Row), C.cast(fn, C.c_void_p))
        assert [row.j for row in v] == want


CMP = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)


@pytest.mark.parametrize('values', ([], [5], [1, 3, 5], [1, 1, 1], [1, 2, 2, 2, 3], list(range(100))))
def test_lower_is_the_first_not_before(fw, values):
    g = api(fw)
    v = (Row * max(1, len(values)))()
    for i, x in enumerate(values):
        v[i].a = x + 1000
    cmp = CMP(lambda a, b: a - b)
    for probe in range(-1, 102):
        want = next((i for i, x in enumerate(values) if x >= probe), len(values))
        assert g.lower(v, len(values), probe + 1000, cmp) == want, probe


def sorted_rows(fw, recs):
    v = rows(*recs)
    libc.qsort(v, len(recs), C.sizeof(Row), C.cast(fw.lib.coverflow__by_album, C.c_void_p))
    return v


def test_album_in_finds_a_row_s_album_or_says_none(fw):
    g = api(fw)
    recs = [Rec('Zoo', '/z/1'), Rec('Abbey', '/a/1'), Rec('Help', '/h/1')]
    v = sorted_rows(fw, recs)
    for j, rec in enumerate(recs):
        assert g.album_in(v, 3, rec.p) == j
    assert g.album_in(v, 3, Rec('abbey', '/a/2').p) == 1  # the same album by album_cmp
    assert g.album_in(v, 3, Rec('Abbey', '/b/1').p) == -1
    assert g.album_in(v, 3, Rec('Zzz', '/z/1').p) == -1
    assert g.album_in(v, 0, recs[0].p) == -1


def test_album_in_two_rows_of_one_album_gives_the_first(fw):
    g = api(fw)
    recs = [Rec('B', '/b/1'), Rec('A', '/a/1'), Rec('a', '/a/2')]
    assert g.album_in(sorted_rows(fw, recs), 3, recs[2].p) == 1


def test_album_index_reads_the_stock_rows(fw):
    g = api(fw)
    recs = [Rec('Zoo', '/z/1'), Rec('Abbey', '/a/1')]
    v = sorted_rows(fw, recs)
    fw.var('coverflow__stock_rows', C.c_void_p).value = C.addressof(v)
    fw.var('coverflow__stock_n', C.c_uint).value = 2
    assert g.album_index(recs[0].p) == 0 and g.album_index(recs[1].p) == 1 and g.album_index(Rec('No', '/z/1').p) == -1


@pytest.mark.parametrize('kind, aa, want', ((1, 'Various', b'Various'), (1, '', b'Song Artist'), (1, None, b'Song Artist'), (0, 'Various', b'Song Artist')))
def test_cover_artist_is_the_album_artist_only_when_artists_says_so(fw, kind, aa, want):
    fw.var('fw_ctx_artist_type', C.c_int).value = kind
    assert api(fw).cover_artist(Rec(artist='Song Artist', album_artist=aa).p) == want


# Letters and jumps

class Albums:
    def __init__(self, fw, names=None, artists=None, unknown=(), scoped=0, sort=SORT_ALBUM):
        self.recs = [Rec(artist=n) if artists else Rec(n, id=-1 if i in unknown else 0) for i, n in enumerate(names or artists)]
        fw.mock('fw_deque_at', lambda d, i, *_: self.recs[i].p)
        fw.mock('fw_deque_size', lambda *_: len(self.recs))
        c = cf(fw)
        c.albums, c.slide, c.screen, c.scoped, c.sort = 0x5000, 0x6000, COVERS, scoped, sort
        self.g = api(fw)

    def jump(self, start, step):
        letter = C.c_uint(0xdead)
        return self.g.coverflow_jump(0x6000, start, step, r(letter)), letter.value


@pytest.mark.parametrize('name, want', (
    ('Abbey', 'A'), ('abbey', 'A'), ('zoo', 'Z'), ('1984', '#'), ('', '#'), ('!!!', '#'), (' Spaced', '#'), ('The Wall', 'W'), ('A Night', 'N'),
    ('An Awesome', 'A'), ('the', 'T'), ('Thee', 'T'), ('Étude', 'É'), ('étude', 'é'), ('日本', '日'), ('Ñ', 'Ñ'), ('😀 Smile', '😀'),
    ('¿Qué?', '#'), ('«Live»', '#'), ('１２', '#'), ('‘Tis', '#'), ('　Wide', '#')))
def test_initial_of_an_album(fw, name, want):
    a = Albums(fw, [name])
    assert a.g.initial(0) == ord(want)


def test_initial_the_unknown_row_is_hash(fw):
    assert Albums(fw, ['Abbey'], unknown=(0,)).g.initial(0) == ord('#')


def test_initial_goes_by_the_artist_in_artist_order(fw):
    a = Albums(fw, artists=['The Beatles'], sort=SORT_ARTIST)
    assert a.g.initial(0) == ord('B')


@pytest.mark.parametrize('raw', (b'\xc3', b'\xc3A', b'\xe2\x82', b'\xe2\x82A', b'\xf0\x9f\x98', b'\xf0\x9f\x98A'))
def test_initial_a_cut_sequence_is_hash(fw, raw):
    assert Albums(fw, [raw]).g.initial(0) == ord('#')


def test_initial_any_bytes_stay_in_bounds(fw):
    rng = random.Random(7)
    a = Albums(fw, ['x'])
    for _ in range(2000):
        a.recs = [Rec(rng.randbytes(rng.randrange(0, 12)).replace(b'\0', b'A'))]
        assert 0 <= a.g.initial(0) <= 0x1fffff


def test_the_misc_table_is_sorted_and_is_numbers_punctuation_and_separators():
    source = (ROOT/'patch/coverflow.c').read_text()
    body = source[source.index('initial_misc[][2] = {'):source.index('/* Initial of the active')]
    table = [(int(a, 16), int(b, 16)) for a, b in re.findall(r'\{ (0x[0-9a-f]+), (0x[0-9a-f]+) \}', body)]
    assert all(a <= b for a, b in table) and all(x[1] < y[0] for x, y in zip(table, table[1:]))
    listed = {c for a, b in table for c in range(a, b + 1)}
    for c in listed:
        assert unicodedata.category(chr(c))[0] in 'NPZCS', hex(c)  # Cn: newer than this python's Unicode
    if tuple(map(int, unicodedata.unidata_version.split('.'))) <= (16, 0, 0):  # the table's version
        missing = [c for c in range(0xa0, 0x20000) if unicodedata.category(chr(c))[0] in 'NPZ' and c not in listed]
        assert not missing, [hex(c) for c in missing[:20]]


FIVE = ['Abba', 'Air', 'Beck', 'Blur', 'Cream']


@pytest.mark.parametrize('start, want', ((0, (2, ord('B'))), (1, (1, ord('B'))), (2, (2, ord('C'))), (3, (1, ord('C'))), (4, (1, 0))))
def test_jump_forward_lands_on_the_next_letter(fw, start, want):
    assert Albums(fw, FIVE).jump(start, 1) == want


@pytest.mark.parametrize('start, want', ((4, (2, ord('B'))), (3, (3, ord('A'))), (2, (2, ord('A')))))
def test_jump_back_lands_on_the_group_s_start(fw, start, want):
    assert Albums(fw, FIVE).jump(start, -1) == want


@pytest.mark.parametrize('start', (0, 1))
def test_jump_back_from_the_first_letter_wraps_to_the_last_album(fw, start):
    assert Albums(fw, FIVE).jump(start, -1) == (start + 1, 0)


def test_jump_one_album_only(fw):
    assert Albums(fw, ['Abba']).jump(0, 1) == (1, 0)
    assert Albums(fw, ['Abba']).jump(0, -1) == (1, 0)


def test_jump_the_utility_cards_and_other_orders_step_one_by_one(fw):
    a = Albums(fw, FIVE)
    assert a.jump(-1, 1) == (1, 0) and a.jump(5, 1) == (1, 0)
    c = cf(fw)
    c.sort = SORT_ARTIST + 1
    assert a.jump(0, 1) == (1, 0)
    c.sort, c.scoped = SORT_ALBUM, 1
    assert a.jump(0, 1) == (1, 0)
    c.scoped, c.screen = 0, TRACKS
    assert a.jump(0, 1) == (1, 0)
    c.screen = COVERS
    letter = C.c_uint(0xdead)
    assert a.g.coverflow_jump(0x6001, 0, 1, r(letter)) == 1 and letter.value == 0


# The files

class Card:
    """fopen and friends on a dict; a read past a file's end is short, as stdio's."""
    def __init__(self, fw, files=None):
        self.files, self.open, self.seen = dict(files or {}), {}, []
        self.fail_rename = self.fail_sync = False
        for name in 'fopen fread fwrite fseek ftell rename unlink'.split():
            fw.mock('fw_' + name, getattr(self, name))
        fw.mock('fw_fclose', lambda *_: 0)
        fw.mock('fw_fflush', lambda *_: 0)
        fw.mock('fw_fsync', lambda *_: self.fail_sync)
        fw.mock('fw_access', lambda path, *_: C.string_at(path).decode() not in self.files)

    def fopen(self, path, mode, *_):
        name, kind = C.string_at(path).decode(), C.string_at(mode)[:1]
        if kind == b'r' and name not in self.files:
            return 0
        handle = 0x1000 + len(self.open)
        self.open[handle] = [name, 0]
        if kind == b'w':
            self.files[name] = b''
        elif kind == b'a':
            self.files.setdefault(name, b'')
        return handle

    def fread(self, buf, size, n, f, *_):
        name, at = self.open[f]
        data = self.files[name][at:at + size * n]
        C.memmove(buf, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fwrite(self, buf, size, n, f, *_):
        self.files[self.open[f][0]] += C.string_at(buf, size * n)
        return n

    def fseek(self, f, off, whence, *_):
        self.open[f][1] = off if whence == 0 else len(self.files[self.open[f][0]]) + off
        return 0

    def ftell(self, f, *_):
        return self.open[f][1]

    def rename(self, a, b, *_):
        if self.fail_rename:
            return -1
        self.files[C.string_at(b).decode()] = self.files.pop(C.string_at(a).decode())
        return 0

    def unlink(self, path, *_):
        self.files.pop(C.string_at(path).decode(), None)
        return 0


def test_file_size(fw):
    g = api(fw)
    Card(fw, {'/a': b'12345', '/e': b''})
    assert g.file_size(b'/a') == 5 and g.file_size(b'/e') == 0 and g.file_size(b'/none') == -1


def test_blob_io_a_write_goes_through_its_temporary(fw):
    g = api(fw)
    card = Card(fw)
    buf = (C.c_ubyte * 4)(1, 2, 3, 4)
    assert g.blob_io(b'/a', b'/a.tmp', buf, 4, 1) == 1
    assert card.files == {'/a': bytes(buf)}
    out = (C.c_ubyte * 4)()
    assert g.blob_io(b'/a', b'/a.tmp', out, 4, 0) == 1 and bytes(out) == bytes(buf)


def test_blob_io_a_short_read_zeroes_the_buffer(fw):
    g = api(fw)
    Card(fw, {'/a': b'123'})
    out = (C.c_ubyte * 4)(9, 9, 9, 9)
    assert g.blob_io(b'/a', b'/a.tmp', out, 4, 0) == 0 and bytes(out) == bytes(4)


def test_blob_io_no_file_leaves_the_buffer(fw):
    g = api(fw)
    Card(fw)
    out = (C.c_ubyte * 4)(9, 9, 9, 9)
    assert g.blob_io(b'/a', b'/a.tmp', out, 4, 0) == 0 and bytes(out) == b'\x09' * 4


def test_blob_io_a_failed_sync_or_rename_is_a_failed_write(fw):
    g = api(fw)
    card = Card(fw, {'/a': b'old!'})
    buf = (C.c_ubyte * 4)(1, 2, 3, 4)
    card.fail_sync = True
    assert g.blob_io(b'/a', b'/a.tmp', buf, 4, 1) == 0 and card.files['/a'] == b'old!'
    card.fail_sync, card.fail_rename = False, True
    assert g.blob_io(b'/a', b'/a.tmp', buf, 4, 1) == 0 and card.files['/a'] == b'old!'


def statuses(fw, status):
    fw.mock('fw_fork', lambda *_: 1234)

    def wait(pid, out, *_):
        C.c_int.from_address(out).value = status
        return pid
    fw.mock('fw_waitpid', wait)


@pytest.mark.parametrize('status, exited', ((0, 1), (1 << 8, 1), (127 << 8, 0), (9, 0), (0x80 | 9, 0)))
def test_ff_run_a_run_that_reached_ffmpeg_s_own_exit(fw, status, exited):
    g = api(fw)
    Card(fw, {'/d.ff': b'jpg'})
    statuses(fw, status)
    got = C.c_int(-1)
    ok = g.ff_run(b'/s', b'/d', 160, 160, r(got))
    assert got.value == exited and ok == (status == 0)
    assert fw.var('ff_kills', C.c_uint).value == (0 if exited else 1)


def test_ff_run_success_renames_the_picture_into_place(fw):
    g = api(fw)
    card = Card(fw, {'/d.ff': b'jpg'})
    statuses(fw, 0)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 1 and card.files == {'/d': b'jpg'}


def test_ff_run_an_empty_or_failed_output_leaves_the_destination_alone(fw):
    g = api(fw)
    card = Card(fw, {'/d.ff': b'', '/d': b'old'})
    statuses(fw, 0)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 0 and card.files == {'/d': b'old'}
    card.files['/d.ff'] = b'partial'
    statuses(fw, 9)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 0 and card.files == {'/d': b'old'}


def test_ff_run_a_refused_fork_counts_as_killed(fw):
    g = api(fw)
    Card(fw)
    fw.mock('fw_fork', lambda *_: -1)
    got = C.c_int(-1)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(got)) == 0 and got.value == 0
    assert fw.var('ff_kills', C.c_uint).value == 1


def test_ff_run_a_signal_mid_wait_waits_again(fw):
    g = api(fw)
    Card(fw, {'/d.ff': b'jpg'})
    fw.mock('fw_fork', lambda *_: 1234)
    errno = C.c_int(4)
    fw.mock('fw___errno_location', lambda *_: C.addressof(errno))
    tries = []

    def wait(pid, out, *_):
        tries.append(pid)
        if len(tries) == 1:
            return -1
        C.c_int.from_address(out).value = 0
        return pid
    fw.mock('fw_waitpid', wait)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 1 and len(tries) == 2


def art(key, suffix=''):
    return f'/mnt/mmc/.coverflow/{key:08x}.jpg{suffix}'


def job(track, key=0xabc, shared=0):
    return Job(track, key, shared)


def builder(fw, card, cover=False, thumb=False, ff=None):
    """The art calls: stock's cover read, stock's thumbnailer, and ffmpeg's exit status (None: a refused fork)."""
    fw.mock('toolsGetAlbumCover', lambda t, dst, *_: (card.files.update({C.string_at(dst).decode(): b'art'}), 1)[1] if cover else 0)
    fw.mock('toolsThumbSpecCover', lambda src, dst, *_: (card.files.update({C.string_at(dst).decode(): b'art'}), 1)[1] if thumb else 0)
    if ff is None:
        fw.mock('fw_fork', lambda *_: -1)
    else:
        statuses(fw, ff)


def test_build_art_embedded_art_is_the_cache_file(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, cover=True)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b'art' and art(0xabc, '.tmp') not in card.files


def test_build_art_the_image_beside_the_track_is_next(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/folder.png': b'x'})
    builder(fw, card, thumb=True)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b'art'


def test_build_art_a_folder_too_long_for_its_cover_s_path_lends_no_file(fw):
    """Cut at the buffer, the cover's path is a prefix: whatever file that names is not the album's art."""
    g = api(fw)
    folder = '/m/' + 'x' * 1011  # with "/cover.jpg" one byte over the 1024-byte path
    card = Card(fw, {folder + '/a.mp3': b'', folder + '/cover.jp': b'x'})
    builder(fw, card, thumb=True, ff=0)
    g.build_art(r(job((folder + '/a.mp3').encode())))
    assert card.files[art(0xabc)] == b''  # nothing of its own: the marker


def test_build_art_a_shared_folder_s_cover_is_not_this_album_s(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/cover.jpg': b'x'})
    builder(fw, card, thumb=True, ff=0)
    j = job(b'/m/a.mp3', shared=1)
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b''  # none of its own: the "no art" marker


def test_build_art_no_art_leaves_an_empty_marker(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, ff=0)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b'' and art(0xabc, '.tmp') not in card.files


def test_build_art_a_killed_ffmpeg_leaves_no_marker_so_the_next_open_retries(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, ff=9)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert art(0xabc) not in card.files


def test_build_art_with_the_storage_gone_touches_nothing(fw):
    g = api(fw)
    card = Card(fw)
    builder(fw, card, cover=True)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert not card.files and not fw.calls('toolsGetAlbumCover')


def test_build_art_holds_both_stock_locks_round_stock_s_call(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, cover=True)
    order = []
    fw.mock('fw_pthread_mutex_lock', lambda m, *_: order.append(('lock', m)))
    fw.mock('fw_pthread_mutex_unlock', lambda m, *_: order.append(('unlock', m)))
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert [k for k, _ in order] == ['lock', 'lock', 'unlock', 'unlock']
    assert order[0][1] == order[3][1] and order[1][1] == order[2][1] and order[0][1] != order[1][1]


BUILDING = '/mnt/mmc/.coverflow/building'
CRASH_LOG = '/mnt/mmc/q2-crash.log'


def test_the_worker_notes_each_album_before_its_build_and_drops_the_note_after_the_last(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/b.mp3': b''})
    builder(fw, card, ff=0)
    noted = []
    fw.mock('toolsGetAlbumCover', lambda *_: noted.append(card.files.get(BUILDING)) or 0)
    jobs = (Job * 2)(Job(b'/m/a.mp3', 0xa, 0), Job(b'/m/b.mp3', 0xb, 0))
    c = cf(fw)
    c.jobs, c.total, c.done, c.cancel = C.addressof(jobs), 2, 0, 0
    assert g.worker(None) is None and c.done == 2
    assert noted == [struct.pack('<I', 0xa) + b'/m/a.mp3', struct.pack('<I', 0xb) + b'/m/b.mp3']
    assert set(card.files) == {'/m/a.mp3', '/m/b.mp3', art(0xa), art(0xb)}  # no note, no temporary


def test_strike_marks_the_album_the_last_build_never_finished_and_drops_the_note(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: struct.pack('<I', 0xabc) + b'/m/a.flac', CRASH_LOG: b'== date\n'})
    g.art_strike(BUILDING.encode())
    assert card.files == {art(0xabc): b'', CRASH_LOG: b'== date\n== cover flow skipped\n/m/a.flac\n'}


def test_strike_of_a_note_torn_before_its_key_marks_nothing(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: b'\xbc\x0a'})
    g.art_strike(BUILDING.encode())
    assert card.files == {}


def test_strike_leaves_a_cover_that_was_made_and_no_note_marks_nothing(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: struct.pack('<I', 0xabc), art(0xabc): b'art'})
    g.art_strike(BUILDING.encode())
    assert card.files == {art(0xabc): b'art'}
    g.art_strike(BUILDING.encode())
    assert card.files == {art(0xabc): b'art'}


def test_a_clean_end_drops_the_note(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: struct.pack('<I', 0xabc), BUILDING.replace('building', 'pl-building'): struct.pack('<I', 0xdef)})
    g.coverflow_clean_end()
    assert card.files == {}


@pytest.mark.parametrize('during', ['cover', 'note'])
def test_a_clean_end_mid_build_stops_the_worker_before_the_next_album_s_note(fw, during):
    """Power-off while album a builds (during its cover read, or as its note is synced): b is never begun, so no
    note of b's outlives demo to strike it at the next boot, and a's note goes with the worker."""
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/b.mp3': b''})
    builder(fw, card, ff=0)
    if during == 'cover':
        fw.mock('toolsGetAlbumCover', lambda *_: g.coverflow_clean_end() or 0)
    else:
        fw.mock('fw_fsync', lambda *_: g.coverflow_clean_end() or 0)
    jobs = (Job * 2)(Job(b'/m/a.mp3', 0xa, 0), Job(b'/m/b.mp3', 0xb, 0))
    c = cf(fw)
    c.jobs, c.total, c.done, c.cancel = C.addressof(jobs), 2, 0, 0
    assert g.worker(None) is None and c.done == (during == 'cover')
    assert set(card.files) == {'/m/a.mp3', '/m/b.mp3'} | ({art(0xa)} if during == 'cover' else set())


def png(w, h):
    """A PNG's signature and IHDR: all of the file thumb reads."""
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I4sIIBBBBB', 13, b'IHDR', w, h, 8, 6, 0, 0, 0) + bytes(4)


def segment(marker, body):
    return struct.pack('>BBH', 0xff, marker, len(body) + 2) + body


APP0, EXIF = segment(0xe0, b'JFIF\0' + bytes(9)), segment(0xe1, b'Exif\0\0' + bytes(2000))
DQT, DHT, DAC = segment(0xdb, bytes(65)), segment(0xc4, bytes(28)), segment(0xcc, bytes(2))


def jpeg(sof, w, h, *before):
    """A JPEG to its scan: SOI, the segments before, the frame header (8 bits, 3 components), SOS."""
    frame = segment(sof, struct.pack('>BHHB', 8, h, w, 3) + bytes(9))
    return b'\xff\xd8' + b''.join(before) + frame + segment(0xda, bytes(10)) + bytes(64)


@pytest.mark.parametrize('head, big', (
    (png(4000, 4000), 1), (png(1025, 16), 1), (png(16, 1025), 1), (png(70000, 1), 1), (png(1, 70000), 1),
    (png(1024, 1024), 0), (png(4000, 4000)[:20], 0),
    (jpeg(0xc2, 3000, 3000, APP0, EXIF, DQT, DHT, DAC), 1), (jpeg(0xc2, 1025, 16), 1), (jpeg(0xc2, 16, 1025), 1),
    (jpeg(0xc6, 2000, 2000), 0), (jpeg(0xca, 2000, 2000), 0), (jpeg(0xce, 2000, 2000), 0),
    (png(4000, 4000).replace(b'IHDR', b'CgBI'), 0),
    (jpeg(0xc2, 1024, 1024, APP0), 0), (jpeg(0xc0, 4000, 4000, APP0, EXIF), 0), (jpeg(0xc1, 4000, 4000), 0),
    (jpeg(0xc2, 3000, 3000, *[APP0] * 63), 1), (jpeg(0xc2, 3000, 3000, *[APP0] * 64), 1),
    (jpeg(0xc2, 3000, 3000, APP0, b'\xff'), 1), (jpeg(0xc0, 4000, 4000, b'\xff\xff', APP0, b'\xff'), 0),
    (b'\xff\xd8' + bytes(64), 1),
    (b'RIFF\0\0\0\0WEBPVP8 ' + bytes(16), 0)))
def test_thumb_sends_a_picture_stock_would_decode_whole_to_ffmpeg(fw, head, big):
    """Stock's PNG reader expands the whole image before its 1024 px test, and libjpeg holds a progressive JPEG's
    full-size coefficients at any scale, both in demo, the OOM killer's pick (#216): those go to ffmpeg alone, whose
    child the kernel kills first. A baseline JPEG decodes scaled, so it stays with stock, as does what thumb can't
    size (a cut-short PNG header, another type) or ffmpeg can't decode (an arithmetic or hierarchical progressive JPEG).
    Fill bytes before a marker are stepped over, and a JPEG whose frame header isn't found in its first 64 segments
    goes to ffmpeg, as it may be a big progressive one (GHSA-w54x-mvfw-c7v3)."""
    g = api(fw)
    card = Card(fw, {'/p/a': head, '/d.ff': b'jpg'})
    builder(fw, card, thumb=True, ff=0)
    assert g.thumb(b'/p/a', b'/d', 160, 160) == 1 and card.files['/d'] == (b'jpg' if big else b'art')
    assert len(fw.calls('toolsThumbSpecCover')) == 1 - big and len(fw.calls('fw_fork')) == big


def ogg_rig(fw, cover=0, status=1 << 8, ogg=b'/m/a.ogg'):
    card = Card(fw, {ogg.decode(): b''})
    fw.mock('toolsGetAlbumCover', lambda *_: cover)
    statuses(fw, status)
    return card


def test_grid_cover_stock_s_hit_needs_no_ffmpeg(fw):
    g = api(fw)
    ogg_rig(fw, cover=1)
    assert g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1) == 1 and not fw.calls('fw_fork')


@pytest.mark.parametrize('track', (b'/m/a.mp3', b'/m/a', b'/m/a.ogg.bak'))
def test_grid_cover_only_ogg_goes_to_ffmpeg(fw, track):
    g = api(fw)
    ogg_rig(fw)
    assert g.coverflow_grid_cover(track, b'/d', 1, 1) == 0 and not fw.calls('fw_fork')


@pytest.mark.parametrize('track', (b'/m/a.OGG', b'/m/a.opus', b'/m/a.Opus'))
def test_grid_cover_an_ogg_by_any_case_does(fw, track):
    g = api(fw)
    ogg_rig(fw, ogg=track)
    g.coverflow_grid_cover(track, b'/d', 1, 1)
    assert fw.calls('fw_fork')


def test_grid_cover_a_miss_is_remembered(fw):
    g = api(fw)
    ogg_rig(fw)
    assert g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1) == 0
    assert g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1) == 0
    assert len(fw.calls('fw_fork')) == 1


def test_grid_cover_a_killed_ffmpeg_or_a_gone_card_is_not_a_miss(fw):
    g = api(fw)
    card = ogg_rig(fw, status=9)
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    assert len(fw.calls('fw_fork')) == 2
    statuses(fw, 1 << 8)
    del card.files['/m/a.ogg']
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    assert len(fw.calls('fw_fork')) == 4


def test_grid_cover_the_lock_is_dropped_for_ffmpeg(fw):
    g = api(fw)
    ogg_rig(fw)
    order = []
    fw.mock('fw_pthread_mutex_unlock', lambda *_: order.append('unlock'))
    fw.mock('fw_pthread_mutex_lock', lambda *_: order.append('lock'))
    fw.mock('fw_fork', lambda *_: (order.append('fork'), -1)[1])
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    assert order == ['unlock', 'fork', 'lock']


def test_grid_cover_the_ring_of_misses_overwrites_the_oldest(fw):
    g = api(fw)
    tracks = [b'/m/%d.ogg' % k for k in range(1025)]
    ogg_rig(fw)
    Card(fw, {t.decode(): b'' for t in tracks})
    runs = []

    def wait(pid, out, *_):
        runs.append(pid)
        C.c_int.from_address(out).value = 1 << 8
        return pid
    fw.mock('fw_waitpid', wait)
    for t in tracks:
        g.coverflow_grid_cover(t, b'/d', 1, 1)
    assert len(runs) == 1025
    g.coverflow_grid_cover(tracks[1024], b'/d', 1, 1)
    assert len(runs) == 1025  # the last, remembered
    g.coverflow_grid_cover(tracks[0], b'/d', 1, 1)
    assert len(runs) == 1026  # the first, overwritten


def test_album_cover_tries_the_folder_image_before_a_process(fw):
    g = api(fw)
    ogg_rig(fw)
    fw.mock('toolsGetExternCover', lambda *_: 1)
    assert g.coverflow_album_cover(b'/m/a.ogg', b'/d', 1, 1) == 1 and not fw.calls('fw_fork')


class Listing:
    def __init__(self, fw, names, d_type=8):
        self.entries = [Dirent(0, 0, 0, d_type, n) for n in names]
        self.at, self.opens = 0, []

        def opendir(path, *_):
            self.opens.append(C.string_at(path))
            self.at = 0
            return 0x2000
        fw.mock('fw_opendir', opendir)
        fw.mock('fw_readdir', self.read)

    def read(self, *_):
        self.at += 1
        return C.addressof(self.entries[self.at - 1]) if self.at <= len(self.entries) else 0


def folder_rig(fw, names, art_for=()):
    Card(fw)
    tried = []

    def cover(t, *_):
        tried.append(C.string_at(t))
        return C.string_at(t) in art_for
    fw.mock('toolsGetAlbumCover', cover)
    return Listing(fw, names), tried


def test_folder_cover_tries_the_first_song_by_name_first(fw):
    g = api(fw)
    _, tried = folder_rig(fw, [b'03.mp3', b'01.mp3', b'02.mp3'], art_for=(b'/m/01.mp3',))
    assert g.coverflow_folder_cover(b'/m/03.mp3', b'/d', 1, 1) == 1
    assert tried == [b'/m/01.mp3']


def test_folder_cover_skips_dot_files_folders_and_other_kinds(fw):
    g = api(fw)
    _, tried = folder_rig(fw, [b'._01.mp3', b'00.flac', b'02.mp3'], art_for=(b'/m/02.mp3',))
    assert g.coverflow_folder_cover(b'/m/05.mp3', b'/d', 1, 1) == 1 and tried == [b'/m/02.mp3']


def test_folder_cover_the_first_song_with_no_art_falls_to_stock_s_walk_and_stays_bare(fw):
    g = api(fw)
    listing, tried = folder_rig(fw, [b'01.mp3', b'02.mp3'], art_for=(b'/m/03.mp3',))
    assert g.coverflow_folder_cover(b'/m/02.mp3', b'/d', 1, 1) == 0
    assert tried == [b'/m/01.mp3', b'/m/02.mp3']
    tried.clear()
    assert g.coverflow_folder_cover(b'/m/03.mp3', b'/d', 1, 1) == 1
    assert tried == [b'/m/03.mp3'] and len(listing.opens) == 1  # the same walk: no second listing


def test_folder_cover_the_first_song_itself_is_not_tried_twice(fw):
    g = api(fw)
    _, tried = folder_rig(fw, [b'01.mp3'])
    assert g.coverflow_folder_cover(b'/m/01.mp3', b'/d', 1, 1) == 0 and tried == [b'/m/01.mp3']


def test_folder_cover_a_hit_ends_the_walk_and_the_next_lists_again(fw):
    g = api(fw)
    listing, _ = folder_rig(fw, [b'01.mp3'], art_for=(b'/m/01.mp3',))
    g.coverflow_folder_cover(b'/m/02.mp3', b'/d', 1, 1)
    g.coverflow_folder_cover(b'/m/03.mp3', b'/d', 1, 1)
    assert len(listing.opens) == 2


def test_folder_cover_another_folder_lists_again(fw):
    g = api(fw)
    listing, _ = folder_rig(fw, [b'01.mp3'])
    g.coverflow_folder_cover(b'/m/a/02.mp3', b'/d', 1, 1)
    g.coverflow_folder_cover(b'/m/ab/02.mp3', b'/d', 1, 1)
    assert listing.opens == [b'/m/a', b'/m/ab']


@pytest.mark.parametrize('track', (b'noslash.mp3', b'/m/noext', b'/m.d/noext', b'/' + b'x' * 600 + b'/a.mp3'))
def test_folder_cover_what_it_can_t_list_is_stock_s_song_alone(fw, track):
    g = api(fw)
    listing, tried = folder_rig(fw, [b'01.mp3'])
    g.coverflow_folder_cover(track, b'/d', 1, 1)
    assert tried == [track] and not listing.opens


def player_rig(fw, track=b'/m/a.mp3', id3_path=b'/m/a.mp3', artist=b'Art', album=b'Alb'):
    buf = fw.var('fw_ctx_g_play_id3_info', C.c_char * 2716)
    C.memmove(C.addressof(buf), id3_path + b'\0', len(id3_path) + 1)
    C.memmove(C.addressof(buf) + 0x504, artist + b'\0', len(artist) + 1)
    C.memmove(C.addressof(buf) + 0x404, album + b'\0', len(album) + 1)


def tags_key(g, artist, album):
    return g.fnv(g.fnv(FNV, artist), album)


def test_marked_empty_is_the_cache_s_empty_file_of_the_parsed_track(fw):
    g = api(fw)
    player_rig(fw)
    card = Card(fw, {art(tags_key(g, b'Art', b'Alb')): b''})
    assert g.marked_empty(b'/m/a.mp3')
    card.files[art(tags_key(g, b'Art', b'Alb'))] = b'jpeg'
    assert not g.marked_empty(b'/m/a.mp3')
    del card.files[art(tags_key(g, b'Art', b'Alb'))]
    assert not g.marked_empty(b'/m/a.mp3')


def test_marked_empty_another_track_says_nothing(fw):
    g = api(fw)
    player_rig(fw, id3_path=b'/m/other.mp3')
    Card(fw, {art(tags_key(g, b'Art', b'Alb')): b''})
    assert not g.marked_empty(b'/m/a.mp3')


def test_player_cover_stock_s_external_first_then_ffmpeg_unless_marked_empty(fw):
    g = api(fw)
    player_rig(fw)
    card = Card(fw, {})
    fw.mock('fw_fork', lambda *_: -1)
    fw.mock('toolsGetExternCover', lambda *_: 1)
    assert g.coverflow_player_cover(b'/m/a.mp3', b'/d', 1, 1) == 1 and not fw.calls('fw_fork')
    fw.mock('toolsGetExternCover', lambda *_: 0)
    assert g.coverflow_player_cover(b'/m/a.mp3', b'/d', 1, 1) == 0 and len(fw.calls('fw_fork')) == 1
    card.files[art(tags_key(g, b'Art', b'Alb'))] = b''
    assert g.coverflow_player_cover(b'/m/a.mp3', b'/d', 1, 1) == 0 and len(fw.calls('fw_fork')) == 1  # no process


def test_now_cover_needs_the_player_to_have_parsed_this_track(fw):
    g = api(fw)
    url = fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024)
    C.memmove(C.addressof(url), b'/m/a.mp3\0', 9)
    fw.var('g_playcover_type', C.c_ubyte).value = 2
    out = C.c_char_p()
    rec = Rec('A', '/m/a.mp3')
    assert g.now_cover(rec.p, r(out)) == 2 and (out.value or b'').endswith(b'/tmp/externpic.jpg')
    assert g.now_cover(Rec('A', '/m/b.mp3').p, r(out)) == 0 and out.value is None
    assert g.now_cover(None, r(out)) == 0


@pytest.mark.parametrize('kind, name', ((1, b'coverpic.jpg'), (2, b'externpic.jpg'), (4, b'externpic.jpg'), (3, None), (0, None), (5, None), (9, None)))
def test_now_cover_each_type_s_file(fw, kind, name):
    g = api(fw)
    url = fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024)
    C.memmove(C.addressof(url), b'/m/a.mp3\0', 9)
    fw.var('g_playcover_type', C.c_ubyte).value = kind
    out = C.c_char_p()
    assert g.now_cover(Rec('A', '/m/a.mp3').p, r(out)) == kind
    assert (out.value.rsplit(b'/')[-1] if out.value else None) == name


SORT_FILE = '/mnt/data/ringnav-coversort'


def int_bytes(v):
    return v.to_bytes(4, 'little', signed=True)


@pytest.mark.parametrize('sort, name', ((0, b'Sort: Album'), (1, b'Sort: Artist'), (2, b'Sort: Recently Added'), (3, b'Sort: Most Played'),
                                         (4, b'Sort: Random'), (6, b'Sort: Year')))
def test_the_library_s_sort_reads_back(fw, sort, name):
    g = api(fw)
    Card(fw, {SORT_FILE: int_bytes(sort)})
    assert g.sort_saved() == sort and g.coverflow_sort_name() == name


@pytest.mark.parametrize('data', (None, b'', b'\x01', int_bytes(5), int_bytes(7), int_bytes(-1), int_bytes(2**31 - 1)))
def test_a_bad_sort_file_is_album(fw, data):
    g = api(fw)
    Card(fw, {} if data is None else {SORT_FILE: data})
    assert g.sort_saved() == SORT_ALBUM and g.coverflow_sort_name() == b'Sort: Album'


def test_sort_file_hostile_sweep(fw):
    g, rng = api(fw), random.Random(8)
    card = Card(fw)
    for _ in range(2000):
        data = rng.randbytes(rng.choice((0, 1, 3, 4, 4, 4, 5)))
        card.files = {SORT_FILE: data}
        card.open = {}
        want = int.from_bytes(data[:4], 'little') if len(data) >= 4 else 0
        assert g.sort_saved() == (want if want in (0, 1, 2, 3, 4, 6) else 0)


def test_sort_step_walks_the_library_s_six_orders_and_wraps(fw):
    g = api(fw)
    card = Card(fw, {SORT_FILE: int_bytes(0)})
    seen = []
    for _ in range(6):
        g.coverflow_sort_step()
        seen.append(int.from_bytes(card.files[SORT_FILE], 'little'))
    assert seen == [SORT_ARTIST, SORT_YEAR, SORT_ADDED, SORT_PLAYED, SORT_RANDOM, SORT_ALBUM]
    assert cf(fw).resort == 1


def test_sort_next_in_the_page_s_own_orders(fw):
    g = api(fw)
    c = cf(fw)
    c.sort = SORT_YEAR
    assert g.sort_next(1) == SORT_ADDED and g.sort_next(0) == SORT_YEAR
    c.sort = SORT_RATING  # not one of the library's
    assert g.sort_next(1) == SORT_ALBUM
    c.scoped = 2
    assert g.sort_next(0) == SORT_RATING and g.sort_next(1) == SORT_ALBUM
    c.sort = SORT_PLAYED
    assert g.sort_next(1) == SORT_RATING  # the rated orders wrap
    c.sort = SORT_RANDOM  # not a rated order
    assert g.sort_next(1) == SORT_RATING


def test_the_library_generation_moves_before_and_after_a_scan(fw):
    gen = fw.var('library_gen', C.c_uint)
    during = []
    fw.mock('stock_scan_all_trampoline', lambda *_: during.append(gen.value))
    fw.fn('coverflow_scan_all', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert during == [1] and gen.value == 2


# Stock's songtable, as demo's CREATE TABLE text has it: VARCHAR columns, so text affinity, which LIKE
# needs to use a NOCASE index.
SONGTABLE = ('create table songtable(id INTEGER PRIMARY KEY AUTOINCREMENT, filename VARCHAR(512), fileurl VARCHAR(1024), '
             'album VARCHAR(512), songer VARCHAR(256), style VARCHAR(128), name VARCHAR(512), composer VARCHAR(256), '
             'albumsonger VARCHAR(256), year INTEGER, track INTEGER, cd_num INTEGER, time INTEGER, time_end INTEGER, '
             'time_create INTEGER, Initial INTEGER, bitdepth INTEGER, samprate INTEGER)')


@pytest.mark.parametrize('scan', ['scan_all', 'scan_folder'])
def test_a_scan_ends_by_indexing_what_stock_looks_up_alone(fw, scan):
    """An album's songs, Recently Added and a file's row read the whole table without an index. Update
    Local Music drops the table, so the indexes come after stock's own writes."""
    made, during = [], []
    fw.mock('toolsUpdateDbTable', lambda db, sql, *_: made.append((C.string_at(db), C.string_at(sql).decode())) or 1)
    fw.mock(f'stock_{scan}_trampoline', lambda *_: during.append(len(made)))
    fw.fn(f'coverflow_{scan}', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert during == [0] and [db for db, _ in made] == [b'/mnt/data/database.db']
    sent = []
    fw.mock('stock_dbquery_trampoline', lambda db, sql, *_: sent.append(C.string_at(sql).decode()))
    query = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)
    music = fw.var('navigation__library_music', C.c_int)
    for staged in (0, 1):  # staged_music's subquery too
        music.value = staged
        for sql in (b"select * from songtable where album like 'Abbey Road'",  # getMusicByAlbum
                    b"select * from songtable where album like 'Abbey Road' and albumsonger like 'The Beatles'",
                    b'select * from songtable ORDER BY time_create desc limit 100;'):  # getAllRecentAddRecord
            query(b'/mnt/data/database.db', sql, None, 0)
    assert len(sent) == 6
    row = "filename='a.flac' and fileurl='/mnt/mmc/a.flac' and time=0 and time_end=0"
    sent += [f'select * from songtable where {row}', f'delete from songtable where {row}',  # toolsCheckItemExist, toolsDeleteDbTable
             f"update songtable set album='b' where {row}"]  # ringnav_scan_refresh
    bare, indexed = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    for db in bare, indexed:
        db.execute(SONGTABLE)
    indexed.executescript(made[0][1])
    plan = lambda db, sql: ' '.join(row[-1] for row in db.execute('explain query plan ' + sql))  # noqa: E731
    for sql in sent:
        assert 'INDEX' not in plan(bare, sql) and 'USING INDEX' in plan(indexed, sql), sql
    # and the same rows in the same order: songs of one second (a CUE image's tracks) stay in rowid order
    songs = [(i, f'{i}.flac', f'/mnt/mmc/{i}.flac', ('Abbey Road', 'abbey road')[i % 2], 'The Beatles', i // 3)
             for i in range(1, 10)]
    for db in bare, indexed:
        db.executemany('insert into songtable(id, filename, fileurl, album, songer, time_create) values (?, ?, ?, ?, ?, ?)', songs)
    for sql in sent[:6]:
        assert bare.execute(sql).fetchall() == indexed.execute(sql).fetchall(), sql
    # oldest first would walk the index backwards and reverse one second's songs: the hook adds id, stock's order
    asc, sent[:] = 'select * from songtable ORDER BY time_create asc;', []
    for staged in (0, 1):
        music.value = staged
        query(b'/mnt/data/database.db', asc.encode(), None, 0)
    assert len(sent) == 2
    for sql in sent:
        assert sql.endswith(' ORDER BY time_create asc, id') and indexed.execute(sql).fetchall() == bare.execute(asc).fetchall(), sql


@pytest.mark.parametrize('result, mask, btime, want', ((0, 0x800, 1700000000, 1700000000), (0, 0xfff, 1234, 1234),
                                                       (-1, 0x800, 1700000000, 99), (0, 0x7ff, 1700000000, 99), (0, 0x800, 0, 99)))
def test_a_song_is_dated_by_its_creation_stamp_where_the_card_keeps_one(fw, result, mask, btime, want):
    """statx's stx_btime (word 20) when its mask says it holds one, else stock's own date (#170, #188)."""
    asked = []

    def statx(dirfd, path, flags, m, buf, *_):
        asked.append((C.c_int(dirfd).value, C.string_at(path), flags, m))
        sx = (C.c_uint * 64).from_address(buf)
        sx[0], sx[20] = mask, btime
        return result
    fw.mock('ringnav_statx', statx)
    fw.mock('stock_file_created_trampoline', lambda path, *_: 99)
    assert fw.fn('ringnav_file_created', C.c_int, C.c_char_p)(b'/mnt/mmc/a.flac') == want
    assert asked == [(-100, b'/mnt/mmc/a.flac', 0, 0x800)]  # AT_FDCWD, STATX_BTIME


def xiph_h(name):
    """A size patch/xiph.h defines: a number, or a number shifted."""
    m = re.search(rf'^#define {name} \(?(\d+)(?: << (\d+))?\)?', (ROOT/'patch/xiph.h').read_text(), re.M)
    assert m, name
    return int(m[1]) << int(m[2] or 0)


OGG_TAGS_MAX = xiph_h('OGG_TAGS_MAX')


def ogg(size):
    """An Ogg Vorbis file's first two packets, the comment header (packet 1, what OGG_TAGS_MAX measures) size bytes
    long with its '\\x03vorbis' start (no CRC: xiph.c reads none)."""
    pages, seq = [], 0
    for packet in (b'\x01vorbis' + bytes(23), b'\x03vorbis' + bytes(size - 7)):
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        for at in range(0, len(lacing), 255):
            part = lacing[at:at + 255]
            pages.append(b'OggS\0' + bytes([at > 0]) + bytes(8) + struct.pack('<III', 1, seq, 0) + bytes([len(part)]) +
                         bytes(part) + packet[at * 255:at * 255 + sum(part)])
            seq += 1
    return b''.join(pages)


@pytest.mark.parametrize('track, size, stock', ((b'/m/a.ogg', OGG_TAGS_MAX + 1, False), (b'/m/a.OGG', OGG_TAGS_MAX + 1, False),
                                                (b'/m/a.ogg', OGG_TAGS_MAX, True), (b'/m/a.ogg', 1000, True),
                                                (b'/m/a.opus', OGG_TAGS_MAX + 1, True), (b'/m/a', OGG_TAGS_MAX + 1, True)))
def test_stock_reads_no_picture_out_of_an_ogg_comment_header_over_the_cap(fw, track, size, stock):
    """TagLib's whole parse of one over OGG_TAGS_MAX would get demo killed, a byte over it as surely as more; one at
    the cap is still stock's. Stock reads no .opus picture at all."""
    files = Files(fw, {track.decode(): ogg(size)})
    fw.mock('stock_album_cover_trampoline', lambda *_: 1)
    got = fw.fn('ringnav_album_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(track, b'/tmp/.tmp_picture', 160, 150)
    assert got == stock and not files.open
    calls = [(C.string_at(t), C.string_at(d), w, h) for t, d, w, h, *_ in (args for _, args in fw.calls('stock_album_cover_trampoline'))]
    assert calls == ([(track, b'/tmp/.tmp_picture', 160, 150)] if stock else [])


@pytest.mark.parametrize('writer, stock, walks', (('coverflow_scan_folder', 'stock_scan_folder_trampoline', True),
                                                  ('coverflow_delete_song', 'stock_delete_song_trampoline', False)))
def test_each_songtable_writer_moves_the_generation_round_stock_s_write(fw, writer, stock, walks):
    """A list queried while one runs (an odd generation) is never kept; a scan first walks the card for folder markers."""
    gen = fw.var('library_gen', C.c_uint)
    during = []
    fw.mock(stock, lambda a, b, *_: during.append((gen.value, a, b)) or 7)
    assert fw.fn(writer, C.c_int, C.c_void_p, C.c_void_p)(0x11, 0x22) == 7  # stock's answer, passed on
    assert during == [(1, 0x11, 0x22)] and gen.value == 2
    names = [name for name, _ in fw.calls()]
    assert ('fw_fopen' in names[:names.index(stock)]) == walks  # the marker walk writes its rules first


# What navigation.c asks of the open page

def py_key(artist, album):
    """album_key: FNV-1a of the artist, then of the album, each closed by a multiply (fnv's separator)."""
    h = FNV
    for s in (artist, album):
        for b in s:
            h = (h ^ b) * 16777619 % 2**32
        h = h * 16777619 % 2**32
    return h


FX_POOL = int(host.tools_build.need(re.search(r'^#define FX_POOL (\d+)', (ROOT/'patch/coverflow.h').read_text(), re.M), 'No FX_POOL')[1])
LD_SLOTS = int(host.tools_build.need(re.search(r'^#define LD_SLOTS (\d+)', (ROOT/'patch/coverflow.h').read_text(), re.M), 'No LD_SLOTS')[1])


class Ld(C.Structure):
    _fields_ = [('key', C.c_uint), *((k, C.c_int) for k in 'album rank state ok'.split())]


class Fx(C.Structure):
    _fields_ = [('frame', C.c_void_p), ('tex', C.c_void_p), ('album', C.c_int * FX_POOL), ('art', C.c_void_p * FX_POOL),
                ('half', C.c_ubyte * FX_POOL),
                *((k, C.c_int) for k in 'c frac drawn light lead arch todo mode letters_at next prev halves'.split()),
                ('half_at', C.c_uint), ('half_ms', C.c_int), ('full_ms', C.c_int),
                ('ld', Ld * LD_SLOTS), ('stage', C.c_void_p), ('gate', C.c_uint * 8), ('loader', C.c_ulong),
                ('ld_on', C.c_int), ('ld_stop', C.c_int), ('paint_at', C.c_uint), ('more', C.c_void_p), ('no_more', C.c_int)]


def deque_of(fw, recs):
    fw.mock('fw_deque_size', lambda *_: len(recs))
    fw.mock('fw_deque_at', lambda d, i, *_: recs[i].p if i < len(recs) else 0)


def test_the_live_album_is_the_centre_card_while_the_covers_show(fw):
    s = Shelf(fw, n=3, index=1, scoped=RATED_ALBUMS)  # the rated albums: two, then their Sort card
    recs = [Rec('A'), Rec('B')]
    deque_of(fw, recs)
    c = cf(fw)
    c.page, c.albums = 0x7000, 0x5000
    album = fw.fn('coverflow_album', C.c_void_p, C.c_void_p)
    assert album(0x7000) == recs[1].p
    assert album(0x7001) is None  # another page's
    s.set(0x78, 2)
    assert album(0x7000) is None  # the Sort card is no album
    s.set(0x78, 0)
    assert album(0x7000) == recs[0].p
    c.timer = 9
    assert album(0x7000) is None  # a screen change is pending
    c.timer, c.screen = 0, TRACKS
    assert album(0x7000) is None
    c.screen, c.albums = COVERS, 0
    assert album(0x7000) is None


def test_the_track_list_and_its_scope_are_the_open_album_s(fw):
    recs = [Rec('Abbey', artist='Beatles'), Rec('Help', artist='Beatles')]
    deque_of(fw, recs)
    c = cf(fw)
    c.page, c.albums, c.tracks, c.album, c.screen = 0x7000, 0x5000, 0x5100, 1, TRACKS
    tracks, scope = fw.fn('coverflow_tracks', C.c_void_p, C.c_void_p), fw.fn('coverflow_scope', C.c_uint, C.c_void_p)
    assert tracks(0x7000) == 0x5100 and scope(0x7000) == py_key(b'Beatles', b'Help')
    c.album = 0
    assert scope(0x7000) == py_key(b'Beatles', b'Abbey')  # each album remembers its own track
    assert tracks(0x7001) is None and scope(0x7001) == 0
    c.screen = COVERS
    assert tracks(0x7000) is None and scope(0x7000) == 0


def test_which_slide_menu_is_the_covers_and_which_stops_at_its_ends(fw):
    c = cf(fw)
    slide, bounded = fw.fn('coverflow_slide', C.c_int, C.c_void_p), fw.fn('coverflow_bounded', C.c_int, C.c_void_p)
    assert not slide(None) and not bounded(None)
    c.slide = 0x6000
    assert slide(0x6000) and not slide(0x6001) and not bounded(0x6000)  # the library's wrap round
    c.scoped = ARTIST_ALBUMS
    assert bounded(0x6000) and not bounded(0x6001)
    c.scoped = 2  # the rated albums wrap like the library's
    assert not bounded(0x6000)


def test_folder_cover_a_first_song_too_long_to_name_is_stock_s_song_alone(fw):
    g = api(fw)
    folder = b'/' + b'd' * 300
    first = b'a' * 246 + b'.mp3'  # fits the listing's 256, not the folder's 512 with it
    listing, tried = folder_rig(fw, [first])
    assert g.coverflow_folder_cover(folder + b'/z.mp3', b'/d', 1, 1) == 0
    assert listing.opens == [folder] and tried == [folder + b'/z.mp3']


class Query:
    """toolsQueryDbTable over rows (id, album, artist, track, album artist), each through the C's own callback as stock's
    sqlite3_exec runs it; album_row, stock's row maker, puts a record of the first four columns on the staging deque."""
    ROW = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)

    def __init__(self, fw, q, rows):
        self.made, self.sql = [], []
        staging = fw.var('fw_ctx_tools_pdeq_directory', C.c_void_p)

        def query(db, sql, cb, sort, *_):
            assert C.string_at(db) == b'/mnt/data/database.db' and not sort
            self.sql.append(C.string_at(sql).decode())
            for row in rows:
                v = (C.c_char_p * 5)(*[None if x is None else str(x).encode() for x in row])
                assert Query.ROW(cb)(0, 5, C.addressof(v), 0) == 0
            return len(rows)

        def album_row(a, n, v, c, *_):
            assert n == 4  # the album artist is the callback's own
            cols = (C.c_char_p * 4).from_address(v)
            self.made.append(Rec(cols[1], cols[3], id=int(cols[0] or 0)))
            q[staging.value].append(self.made[-1].p)
        fw.mock('toolsQueryDbTable', query)
        fw.mock('album_row', album_row)


def field(p, off):
    s = C.c_void_p.from_address(p + off).value
    return None if s is None else C.string_at(s).decode()


def test_split_makes_a_name_s_row_one_card_per_album_in_its_place(fw):
    """getAllAlbum groups by name alone; ALBUM_SQL's rows, the fifth column the album artist, tell its albums apart."""
    q = Deques(fw)
    hits, abbey, lost, unknown = Rec('Hits', '/m/H1/1.flac', id=1), Rec('Abbey', '/m/A/1.flac', id=3), \
        Rec('Lost', '/m/L/1.flac', id=9), Rec('Unknown Album', id=-1)
    listed = q.new([hits.p, abbey.p, lost.p, unknown.p])
    staging = fw.var('fw_ctx_tools_pdeq_directory', C.c_void_p)
    staging.value = 0x4321
    query = Query(fw, q, [(1, 'Hits', 'A', '/m/H1/1.flac', 'Various'), (2, 'hits', 'A', '/m/H2/1.flac', 'Other'),
                          (4, 'Hits', 'A', '/m/H3/1.flac', 'various'), (3, 'Abbey', 'B', '/m/A/1.flac', ''),
                          (5, 'Zed', 'Z', '/m/Z/1.flac', None)])
    assert fw.fn('coverflow_split', C.c_int, C.c_void_p)(listed) == 5
    got = [(field(p, 0x14), field(p, 0x24)) for p in q[listed]]
    assert got == [('Hits', 'Various'), ('hits', 'Other'), ('Abbey', ''), ('Lost', None), ('Unknown Album', None)]
    assert q[listed][2] == query.made[3].p and q[listed][3] == lost.p  # the query's row; a name it missed stays
    assert query.sql[0].startswith('select id,album,songer,') and ' order by ' not in query.sql[0]
    assert staging.value == 0x4321  # the staging pointer leaves no trace
    assert len(q.lists) == 1  # the copy, the query's rows and the split all gone


def test_album_only_keeps_the_album_s_tracks_in_order_and_where_the_track_went(fw):
    q = Deques(fw)
    recs = [Rec('A', '/m/A/1.flac'), Rec('B', '/m/B/1.flac'), Rec('a', '/m/A/2.flac'), Rec('A', '/m/X/1.flac'),
            Rec('A', '/m/A/3.flac')]
    listed = q.new([r.p for r in recs])
    only = fw.fn('album_only', C.c_int, C.c_void_p, C.c_void_p, C.c_int)
    assert only(listed, recs[2].p, 2) == 1
    assert q[listed] == [recs[0].p, recs[2].p, recs[4].p]  # a same-named album in another folder is another album
    other = q.new([recs[1].p, recs[3].p])
    assert only(other, recs[0].p, 1) == 1 and q[other] == [recs[1].p, recs[3].p]  # none of it: untouched
    assert len(q.lists) == 2


def test_album_only_keeps_the_unknown_album_card_s_songs_and_the_resumed_one(fw):
    """Stock's resume finds every untagged song (getMusicByAlbum(NULL), without MUSIC_WHERE): the card's are kept,
    spoken and scoped-out ones are not, unless one is what was playing."""
    Files(fw, {'/mnt/data/q2-library-scope': b'0/mnt/mmc/Hidden\n'})
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    q = Deques(fw)
    recs = [Rec(path='/mnt/mmc/Loose A/x.flac'), Rec(path='/mnt/mmc/Podcasts/Show/1.mp3'),
            Rec(path='/mnt/mmc/Hidden/h.flac'), Rec(path='/mnt/mmc/Loose B/y.flac'), Rec(path='/mnt/mmc/Loose/b.m4b'),
            Rec(path='/mnt/mmc/audiobooks/Book/1.mp3'), Rec('A', '/mnt/mmc/A/1.flac')]
    only = fw.fn('album_only', C.c_int, C.c_void_p, C.c_void_p, C.c_int)
    listed = q.new([r.p for r in recs])
    assert only(listed, recs[3].p, 3) == 1 and q[listed] == [recs[0].p, recs[3].p]
    for i, kept in ((1, [0, 1, 3]), (2, [0, 2, 3]), (4, [0, 3, 4])):  # what was playing stays, whatever the card shows
        listed = q.new([r.p for r in recs])
        assert only(listed, recs[i].p, i) == kept.index(i) and q[listed] == [recs[k].p for k in kept]


@pytest.fixture(scope='module')
def style(library):
    """cf_style_t's field offsets in this build."""
    return host.offsets(library.parent/'coverflow.o', 'cf_style_t')


def test_a_far_cover_s_pose_holds_for_a_wide_spacing(fw, style):
    """xs and zs as far as the theme's range takes them: a cover 22 out (entry_window's) stays exact, no wrap."""
    g = api(fw)
    cfs = C.addressof(fw.var('cfs', C.c_char))
    x1, z1 = (C.c_short.from_address(cfs + style[f]).value for f in ('x1', 'z1'))
    for f in ('xs', 'zs'):
        C.c_short.from_address(cfs + style[f]).value = 799
    p = pose(g, 22 * ONE)
    assert (p.xc, p.zc) == (16 * (x1 + 21 * 799), 16 * (z1 + 21 * 799))


def test_without_a_third_cover_the_second_fades_out_over_the_next_step(fw, style):
    g = api(fw)
    third = C.c_short.from_address(C.addressof(fw.var('cfs', C.c_char)) + style['third'])
    second = pose(g, 2 * ONE).bright
    assert third.value and pose(g, 3 * ONE).bright == second  # the theme's: the third stays
    third.value = 0
    assert pose(g, 2 * ONE).bright == second
    assert pose(g, 5 * ONE // 2).bright == second // 2
    assert pose(g, 3 * ONE).bright == 0 and pose(g, 4 * ONE).bright == 0
    last = second
    for t in range(2 * ONE, 3 * ONE, 999):
        b = pose(g, t).bright
        assert b <= last
        last = b


@pytest.mark.parametrize('scoped', (ARTIST_ALBUMS, 0))
def test_an_artist_s_shelf_shows_no_cover_past_its_ends(fw, scoped):
    t = themed(fw, MINIMAL)
    frame = C.create_string_buffer(0x40)
    fw.mock('bitmap_create_ex', lambda *_: C.addressof(frame))
    fw.mock('widget_load_image', lambda *_: 1)  # nothing decodes: the placeholder
    n = 3
    deque_of(fw, [Rec() for _ in range(n)])
    c = cf(fw)
    c.albums, c.slide, c.scoped = 0x5000, 0x6000, scoped
    fw.fn('cf_restyle', None)()
    assert fw.fn('coverflow__fx_open', C.c_int)() == 1
    wide = (C.c_void_p * (2 * CF_WIDE_REACH + 1))()  # the callers' window, ring[0] CF_REACH slots left of its centre
    ring = C.addressof(wide) + (CF_WIDE_REACH - CF_REACH) * C.sizeof(C.c_void_p)
    fw.fn('coverflow__fx_ring', None, C.c_int, C.c_int, C.c_void_p)(0, n, ring)
    reach = CF_WIDE_REACH if t['cf.thumb'] else CF_REACH  # a thumb theme's row reaches further
    js = range(-CF_WIDE_REACH, CF_WIDE_REACH + 1)  # each slot's place from the centre album, the first
    # an artist's shelf: its three albums alone; the library's small shelf repeats round its wrap
    assert [int(bool(p)) for p in wide] == [int(abs(j) <= reach and (not scoped or 0 <= j < n)) for j in js]
    fw.fn('coverflow__fx_close', None)()


# Next or a track's end moves the queue to another album: covers resting on its last one follow it, a few albums at a
# tap's glide, farther at once (TAIL_GLIDE 4); covers turned to another album, or moving, are left alone.
@pytest.mark.parametrize('centre, offset, to, want', [(0, 0, 2, [-220]), (0, 0, 5, []), (1, 0, 2, []), (0, 20, 2, [])])
def test_the_covers_follow_the_queue_s_album_from_its_last_alone(fw, mcl, centre, offset, to, want):
    q = Deques(fw)
    albums = [Rec(a, path=f'/m/{a}/1.flac') for a in 'ABCDEF']
    s = Shelf(fw, n=len(albums) + 1, index=centre, offset=offset, scoped=0)
    c = cf(fw)
    c.albums = q.new([r.p for r in albums])
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_void_p).value = q.new([albums[0].p, albums[to].p])
    tail = fw.fn('coverflow_tail', None)
    tail()  # first sight: album A, nothing to follow from
    assert not s.goals and not fw.calls('slide_menu_set_value')
    mcl.pos = 1
    tail()
    assert s.goals == want
    moved = [a[1] for _, a in fw.calls('slide_menu_set_value')]
    assert moved == ([to] if to > 4 and not centre and not offset else [])
    tail()  # the same song: nothing again
    assert s.goals == want and len(fw.calls('slide_menu_set_value')) == len(moved)


def test_a_tap_in_the_flat_fallback_opens_the_centre_cover_alone(fw):
    s = Shelf(fw, n=4, index=1, scoped=0)
    deque_of(fw, [Rec(), Rec(), Rec()])
    c = cf(fw)
    c.albums = 0x5000
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append((fn, ms)) or 5)

    def tap(x, y):
        c.timer = 0
        timers.clear()
        s.g.touch(None, event(DOWN, x, y, t=1))
        s.g.touch(None, event(UP, x, y, t=2))
        return timers
    opens = [(C.cast(fw.lib.coverflow__to_tracks, C.c_void_p).value, 0)]
    assert tap(CX, 50) == opens and c.album == 1 and c.timer == 5
    assert tap(CX - 79, 0) == opens and tap(CX + 79, ART - 1) == opens
    assert not tap(CX + 80, 50) and not tap(CX, ART) and not tap(CX - 120, 50)  # no side cover to aim at
    s.set(0x94, 20)  # moving: the centre cover settles rather than opens
    assert not tap(CX, 50) and s.goals == [0]


# Stock's widgets, for the pages and Home

THEMES = host.tools_theme.builtins()
MINIMAL, CLASSIC, MODERN = range(3)
EVT_CLICK, EVT_WINDOW_FOREGROUND = O['EVT_CLICK'], O['EVT_WINDOW_FOREGROUND']
PANE_NOW, PANE_LIBRARY, PANE_SHUFFLE, PANE_COVERFLOW, PANE_FOLDER, PANE_GAMES, PANE_ROCKBOX, PANE_STREAM, PANE_SETTINGS, \
    PANE_SERVICE = range(10)
SPLIT_W, BACK_W = 187, 294


def themed(fw, theme, **keys):
    """config.ini's [IPOD] keys as navigation.c reads them at its first theme(): Theme, and any others."""
    values = {b'THEME': str(theme).encode(), **{k.encode(): v.encode() for k, v in keys.items()}}

    def read(path, section, key, out, default, *_):
        value = values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', read)
    fw.mock('fw___xstat', lambda *_: -1)  # no Rockbox on the card
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    return THEMES[theme]


def address(fw, name):
    return C.cast(getattr(fw.lib, name), C.c_void_p).value


def rect(p):
    return tuple((C.c_int * 4).from_address(p))


class Widget:
    def __init__(self, kind, parent, name=''):
        self.type, self.parent, self.name, self.kids = kind, parent, name, []
        self.text, self.image, self.style, self.visible, self.sensitive = None, '', '', 1, 1
        self.props, self.on = {}, []


class Ui:
    """Stock's widgets: each a zeroed block of the C's own heap (its box at W_X to W_H, its parent at W_PARENT, a
    slide_menu's fields), its tree, name, text, image, properties and handlers here; the timers, the window manager's
    windows and top one, what was invalidated and the animators made."""
    HANDLER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
    TIMER = C.CFUNCTYPE(C.c_int, C.c_void_p)

    def __init__(self, fw):
        self.fw, self.w, self.cstrs, self.keep, self.timers, self.dirty, self.animators = fw, {}, {}, [], {}, [], []
        self.calloc = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t)
        self.top = self.animating = self.ids = 0
        self.animate = True
        self.wm = self.make(0, 'window_manager')
        for name, kind in (('window_create', 'window'), ('image_create', 'image'), ('list_view_create', 'list_view'),
                           ('scroll_view_create', 'scroll_view'), ('list_item_create', 'list_item'),
                           ('hscroll_label_create', 'hscroll_label')):
            fw.mock(name, lambda parent, x, y, w, h, *_, k=kind: self.make(parent, k, x, y, w, h))
        fw.mock('widget_factory', lambda *_: 1)
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.make(parent, self.str(kind), x, y, w))
        for name, prop in (('widget_set_name', 'name'), ('widget_use_style', 'style'), ('image_base_set_image', 'image')):
            fw.mock(name, lambda w, s, *_, p=prop: setattr(self[w], p, self.str(s)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: setattr(self[w], 'text', self.str(s) if s else None))
        fw.mock('widget_get_text', lambda w, *_: self.wide(self[w].text))
        fw.mock('widget_get_text_utf8', self.text_utf8)
        fw.mock('widget_set_visible', lambda w, v, *_: setattr(self[w], 'visible', v))
        fw.mock('widget_get_visible', lambda w, *_: self[w].visible)
        fw.mock('widget_set_sensitive', lambda w, v, *_: setattr(self[w], 'sensitive', v))
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self[w].on.append((evt, fn, ctx)) or len(self[w].on))
        fw.mock('widget_destroy_children', lambda w, *_: self.drop(self[w].kids[:]))
        fw.mock('widget_destroy', lambda w, *_: self.drop([w]))
        fw.mock('widget_count_children', lambda w, *_: len(self[w].kids))
        fw.mock('widget_get_child', lambda w, i, *_: self[w].kids[i] if i < len(self[w].kids) else 0)
        fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: self.box(w, x, y, ww, h))
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_set_prop_str', lambda w, k, v, *_: self[w].props.__setitem__(self.str(k), self.str(v)))
        fw.mock('widget_set_prop_int', lambda w, k, v, *_: self[w].props.__setitem__(self.str(k), C.c_int(v).value))
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: self[w].props.get(self.str(k), C.c_int(d).value))
        fw.mock('widget_lookup', lambda w, name, *_: self.lookup(w, self.str(name)))
        fw.mock('widget_get_type', lambda w, *_: self.cstr(self[w].type))
        fw.mock('widget_restack', lambda w, i, *_: self.restack(w, i))
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.dirty.append((w, rect(r) if r else None)))
        fw.mock('timer_add', self.add_timer)
        fw.mock('timer_remove', lambda i, *_: self.timers.pop(i, None) and 0)
        fw.mock('window_manager', lambda *_: self.wm)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('window_manager_is_animating', lambda *_: self.animating)
        fw.mock('widget_animator_prop_create', self.animator)

    def __getitem__(self, w):
        return self.w[w] if w in self.w else self.w.setdefault(0, Widget('none', 0))

    @staticmethod
    def str(s):
        return C.string_at(s).decode() if s else ''

    def cstr(self, s):
        if s not in self.cstrs:
            self.cstrs[s] = C.create_string_buffer(s.encode())
        return C.addressof(self.cstrs[s])

    def wide(self, text):
        if text is None:
            return 0
        self.keep.append((C.c_uint * (len(text) + 1))(*map(ord, text)))
        return C.addressof(self.keep[-1])

    def text_utf8(self, w, out, size, *_):
        data = (self[w].text or '').encode()[:size - 1]
        C.memmove(out, data + b'\0', len(data) + 1)
        return 0

    def make(self, parent, kind, x=0, y=0, w=0, h=0, name=''):
        p = self.calloc(1, 0x200)
        self.w[p] = Widget(kind, parent, name)
        self.box(p, x, y, w, h)
        C.c_void_p.from_address(p + 0x48).value = parent or None  # W_PARENT
        if parent:
            self[parent].kids.append(p)
        return p

    def box(self, w, *xywh):
        if w in self.w:
            (C.c_int * 4).from_address(w)[:] = [C.c_int(v).value for v in xywh]

    def at(self, w):
        return rect(w)

    def drop(self, ws):
        for w in ws:
            if self[w].parent in self.w and w in self[self[w].parent].kids:
                self[self[w].parent].kids.remove(w)
            self.w.pop(w, None)

    def restack(self, w, i):
        kids = self[self[w].parent].kids
        kids.remove(w)
        kids.insert(i, w)

    def lookup(self, w, name):
        for k in self[w].kids if w else ():
            if self[k].name == name:
                return k
            found = self.lookup(k, name)
            if found:
                return found
        return 0

    def prop_str(self, w, k, d, *_):
        k = self.str(k)
        value = self[w].image if k == 'image' else self[w].name if k == 'name' else self[w].props.get(k)
        return self.cstr(value) if value is not None else d

    def add_timer(self, fn, ctx, ms, *_):
        self.ids += 1
        self.timers[self.ids] = (fn, ctx, ms)
        return self.ids

    def timer(self, fn):
        """The timer running fn: (its id, ms), or None."""
        return next(((i, ms) for i, (f, _, ms) in self.timers.items() if f == fn), None)

    def fire(self, i):
        fn, ctx, ms = self.timers.pop(i)
        r = Ui.TIMER(fn)(ctx)
        if r == 8:  # RET_REPEAT
            self.timers[i] = (fn, ctx, ms)
        return r

    def animator(self, w, ms, delay, easing, prop, *_):
        if not self.animate:
            return 0
        self.animators.append((w, ms, easing, self.str(prop)))
        return self.calloc(1, 0x80)

    def handlers(self, w, evt=EVT_CLICK):
        return [(fn, ctx) for e, fn, ctx in self[w].on if e == evt]

    def click(self, w, evt=EVT_CLICK, event=0):
        return [Ui.HANDLER(fn)(ctx, event) for fn, ctx in self.handlers(w, evt)]

    def named(self, name):
        return next(w for w, x in self.w.items() if x.name == name)

    def of(self, kind, parent=None):
        return [w for w, x in self.w.items() if x.type == kind and (parent is None or x.parent == parent)]


class Homes:
    """A Home window as the asset lays it out: the art, the main list's rows, the Settings, Library and Streaming lists
    (hidden, two rows each: a button with its label and icon), and Carousel's slide_menu and arrows."""
    ROWS = ('img_playing', 'img_library', 'img_cover_flow', 'img_folder', 'img_rockbox', 'img_services', 'img_settings')

    def __init__(self, ui, carousel=False, rockbox=False):
        self.ui = ui
        self.win = ui.make(0, 'window', 0, 30, 375, 290)
        self.art = ui.make(self.win, 'image', 0, 0, 375, 290, name='img_homeart')
        self.main = ui.make(self.win, 'list_view', 0, 0, 375, 290, name='list_view_home')
        self.view = ui.make(self.main, 'scroll_view', 0, 0, 375, 290)
        self.rows = {name: ui.make(self.view, 'image', 0, 70 * i, 375, 70, name=name) for i, name in enumerate(self.ROWS)}
        if rockbox:
            ui[self.rows['img_rockbox']].name = 'btn_rockbox'
            ui.make(self.rows['img_rockbox'], 'label', name='label_rockbox')
        self.lists = {}
        for k in ('homeset', 'homelib', 'homestream'):
            self.lists[k] = ui.make(self.win, 'list_view', 0, 0, 375, 290, name='list_view_' + k)
            ui[self.lists[k]].visible = 0
            view = ui.make(self.lists[k], 'scroll_view', 0, 0, 375, 290)
            for i in range(2):
                row = ui.make(view, 'view', 0, 60 * i, 375, 60)
                b = ui.make(row, 'button', 10, 60 * i, 355, 60)
                ui.make(b, 'hscroll_label', 80, 10, 200, 40)
                ui[ui.make(b, 'image', 10, 10, 50, 50)].image = ('stream_radio', 'file:///mnt/mmc/apps/x/icon.png')[i]
        self.sets, self.lib, self.stream = self.lists['homeset'], self.lists['homelib'], self.lists['homestream']
        self.carousel = []
        if carousel:
            self.carousel = [ui.make(self.win, 'slide_menu', name='slide_menu'), ui.make(self.win, 'image', name='img_left'),
                             ui.make(self.win, 'image', name='img_right')]
            for name in ('img_cover_flow', 'img_rockbox', 'label_rockbox'):
                ui.make(self.carousel[0], 'image', name=name)
            for w in self.carousel:
                ui[w].visible = 0
        ui.top = self.win

    def open(self, fw, result=0):
        fw.mock('stock_home_trampoline', lambda *_: result)
        return fw.fn('coverflow_home', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0)


def home_open(fw, theme=CLASSIC, carousel=False, rockbox=False, **keys):
    t = themed(fw, theme, **keys)
    ui = Ui(fw)
    h = Homes(ui, carousel, rockbox)
    h.open(fw)
    return t, ui, h, fw.var('coverflow__home', Home)


def test_home_binds_its_cards_and_lists_and_asks_the_pane_s_counts_after_boot(fw):
    themed(fw, CLASSIC)
    ui = Ui(fw)
    h = Homes(ui, carousel=True)
    assert h.open(fw, 5) == 5  # stock's answer
    home = fw.var('coverflow__home', Home)
    assert (home.win, home.art, home.list, home.sets, home.lib, home.stream) == (h.win, h.art, h.main, h.sets, h.lib, h.stream)
    assert list(home.carousel) == h.carousel
    assert not any(ui[w].sensitive for w in (h.art, h.sets, h.lib, h.stream))  # taps find the shown list's rows
    want = {'img_cover_flow': ('coverflow__coverflow_open', 0), 'img_rockbox': ('home_shortcut_open', 0),
            'img_settings': ('coverflow__home_settings', 0), 'img_library': ('coverflow__home_library', h.win),
            'img_services': ('coverflow__home_streaming', h.win)}
    for row, (fn, ctx) in want.items():
        assert ui.handlers(h.rows[row]) == [(address(fw, fn), ctx)], row
    on_card = [ui.handlers(ui.lookup(h.carousel[0], n)) for n in ('img_cover_flow', 'img_rockbox')]
    assert on_card == [[(address(fw, 'coverflow__coverflow_open'), 0)], [(address(fw, 'home_shortcut_open'), 0)]]
    assert ui.handlers(h.win, EVT_WINDOW_FOREGROUND) == [(address(fw, 'coverflow__home_foreground'), 0)]
    assert ui.timer(address(fw, 'coverflow__home_counts'))[1] > 0  # not in boot's first paints: later


def test_a_theme_without_the_pane_asks_no_counts(fw):
    _, ui, h, _ = home_open(fw, MINIMAL)
    assert not ui.handlers(h.win, EVT_WINDOW_FOREGROUND) and not ui.timer(address(fw, 'coverflow__home_counts'))


def test_settings_slides_in_over_the_list_and_return_slides_the_list_back(fw):
    t, ui, h, _ = home_open(fw)
    title, back = fw.fn('coverflow_home_title', C.c_char_p), fw.fn('coverflow_home_back', C.c_int, C.c_void_p)
    assert title() == b'Q2 Pod' and not back(h.win)
    ui.box(h.sets, 50, 0, SPLIT_W, 290)  # a list still sliding goes to its rest first
    ui.click(h.rows['img_settings'])
    assert not ui[h.main].visible and not ui[h.main].sensitive and ui[h.sets].visible and ui[h.sets].sensitive
    assert ui.at(h.sets)[0] == SPLIT_W and ui.animators == [(h.sets, t['motion.swap.ms'], t['motion.swap.easing'], 'x')]
    assert ui[h.art].visible and title() == b'Settings'
    ui.click(h.rows['img_settings'])  # open already: nothing moves
    assert len(ui.animators) == 1
    assert not back(h.art) and back(h.win) == 1
    assert ui[h.main].visible and ui[h.main].sensitive and not ui[h.sets].visible and not ui[h.sets].sensitive
    assert ui.at(h.main)[0] == -SPLIT_W and ui.animators[-1][0] == h.main  # in from the left
    assert title() == b'Q2 Pod' and not back(h.win)


def test_a_swap_with_no_animator_shows_the_list_in_place(fw):
    _, ui, h, _ = home_open(fw)
    ui.animate = False
    ui.click(h.rows['img_settings'])
    assert ui[h.sets].visible and ui.at(h.sets)[0] == 0


@pytest.mark.parametrize('bar', (False, True))
def test_a_hold_going_home_shows_the_main_list_at_once(fw, bar):
    _, ui, h, _ = home_open(fw)
    ui.click(h.rows['img_settings'])
    top = fw.fn('coverflow_home_top', None)
    over, first = ui.make(0, 'window'), []
    if bar:  # the bar, application_init's first window, under them all
        first = [ui.make(0, 'window')]
        fw.var('fw_ctx_system_bar', C.c_void_p).value = first[0]
    ui[ui.wm].kids[:] = first + [over, h.win]  # Home over another window: not the bottom one
    top()
    assert ui[h.sets].visible
    ui[ui.wm].kids[:] = first + [h.win, over]
    animated = len(ui.animators)
    top()
    assert ui[h.main].visible and ui[h.main].sensitive and not ui[h.sets].visible and len(ui.animators) == animated
    top()  # nothing open: nothing to do
    assert ui[h.main].visible


def test_home_owns_its_four_lists_rows(fw):
    themed(fw, CLASSIC)
    ui = Ui(fw)
    h = Homes(ui)
    owns = fw.fn('coverflow_home_owns', C.c_int, C.c_void_p)
    assert not owns(h.rows['img_library'])  # before Home's bind
    h.open(fw)
    for k, lst in h.lists.items():
        assert owns(ui[ui[lst].kids[0]].kids[0]), k
    assert owns(h.rows['img_library']) and owns(h.main) and not owns(h.art) and not owns(h.win) and not owns(None)


@pytest.fixture
def stock_bg():
    """Stock's background handlers library_rows and stream_rows call by address (LOCALMUSIC_BG, STREAM_BG): each a
    stand-in counting its calls. Unlike test_navigation's stock_stub and test_playback's Wheel, which run only in
    in_fork's child, these run in this process, so the gcov counts of the C they reach are kept (a forked child's
    are lost); the teardown after the yield puts the original bytes back whether or not the test passed."""
    counts, saved = (C.c_long * 2)(), []
    for k, name in enumerate(('LOCALMUSIC_BG', 'STREAM_BG')):
        at = O[name]
        claim(at)
        code = b'\x48\xb8' + struct.pack('<Q', C.addressof(counts) + 8 * k) + b'\x48\xff\x00\x31\xc0\xc3'  # ++*counter; return 0
        saved.append((at, C.string_at(at, len(code))))
        C.memmove(at, code, len(code))
    yield counts
    for at, data in saved:
        C.memmove(at, data, len(data))


def rows_of(ui, lst):
    return [ui[r].kids[0] for r in ui[ui[lst].kids[0]].kids]


def test_the_library_row_slides_its_rows_in_split_s_shape(fw, stock_bg):
    t, ui, h, _ = home_open(fw)
    ui.click(h.rows['img_library'])
    assert stock_bg[0] == 1  # library_rows filled it, as the page's constructor
    assert ui[h.lib].visible and not ui[h.main].visible and fw.fn('coverflow_home_title', C.c_char_p)() == b'Library'
    for b in rows_of(ui, h.lib):
        assert ui.at(b)[::2] == (0, SPLIT_W) and ui.at(b)[3] == 60  # Home's rows' width, their own height
        label = ui.of('hscroll_label', b)[0]
        assert ui.at(label)[0] == t['home_text_x'] and ui.at(label)[2] == SPLIT_W - t['home_text_x'] - t['home_label_end[0]']
    ui.click(h.rows['img_library'])
    assert stock_bg[0] == 1  # open already


@pytest.mark.parametrize('keys', ({'HOME': '1'}, {'HOMELIB': '0'}))
def test_the_library_row_opens_the_page_in_full_or_without_library_on_home(fw, stock_bg, keys):
    _, ui, h, _ = home_open(fw, **keys)
    fw.mock('on_home_music_onclick', lambda *_: 7)
    assert ui.click(h.rows['img_library']) == [7] and stock_bg[0] == 0 and not ui[h.lib].visible


def test_the_streaming_row_takes_home_s_row_height_and_decodes_the_build_s_icons(fw, stock_bg):
    _, ui, h, home = home_open(fw)
    home.kind, home.row = PANE_SERVICE, 0x1234
    loads = []
    fw.mock('widget_load_image', lambda owner, url, *_: loads.append((owner, C.string_at(url))) or 1)
    ui.click(h.rows['img_services'])
    assert stock_bg[1] == 1 and ui[h.stream].visible and fw.fn('coverflow_home_title', C.c_char_p)() == b'Streaming'
    assert home.kind == PANE_STREAM and not home.row  # the rows the pane read are rebuilt
    for b in rows_of(ui, h.stream):
        assert ui.at(b) == (0, 0, SPLIT_W, 70)  # the main list's row height
        assert ui.at(ui.of('hscroll_label', b)[0])[1::2] == (0, 70)
    assert loads == [(h.art, b'classic_light_stream_radio')]  # a card app's own file waits for the pane
    ui.click(h.rows['img_services'])
    assert stock_bg[1] == 1


def test_the_streaming_row_opens_the_page_in_full(fw, stock_bg):
    _, ui, h, _ = home_open(fw, HOME='1')
    fw.mock('on_home_stream_onclick', lambda *_: 9)
    assert ui.click(h.rows['img_services']) == [9] and stock_bg[1] == 0


@pytest.mark.parametrize('home', ('0', '1', '2'))
@pytest.mark.parametrize('theme', (MINIMAL, CLASSIC, MODERN))
@pytest.mark.parametrize('shortcut, shown, label', (('0', 0, 'Rockbox'), ('6', 1, 'Games'), ('11', 0, 'Favorites'),
                                                    ('12', 1, 'Favorites')))
def test_the_shortcut_row_sits_after_folder_unless_rockbox_is_missing_or_none(fw, theme, home, shortcut, shown, label):
    # None hides the row in every layout and theme; the carousel's card stays, naming the default as with no choice
    themed(fw, theme, SHORTCUT=shortcut, HOME=home)
    ui = Ui(fw)
    h = Homes(ui, carousel=True, rockbox=True)
    rockbox = h.rows['img_rockbox']
    ui.restack(rockbox, 0)
    h.open(fw)
    rows = ui[h.view].kids
    assert rows.index(rockbox) == (rows.index(h.rows['img_folder']) + 1 if shown else len(rows) - 1)  # else last
    assert [r for r in rows if r != rockbox] == [h.rows[n] for n in h.ROWS if n != 'img_rockbox']  # the others keep their order
    assert ui[rockbox].visible == shown and ui[rockbox].sensitive == shown
    assert ui[ui.named('label_rockbox')].text == label and ui[ui.lookup(h.carousel[0], 'label_rockbox')].text == label


@pytest.mark.parametrize('mode', (1, 2))
def test_full_and_carousel_close_an_open_list_and_carousel_shows_its_cards(fw, mode):
    themed(fw, CLASSIC, HOME=str(mode))
    ui = Ui(fw)
    h = Homes(ui, carousel=True)
    ui[h.lib].visible = 1
    h.open(fw)
    assert not ui[h.lib].visible and not ui[h.art].visible
    assert all(ui[w].visible == (mode == 2) for w in h.carousel)
    assert ui[h.main].visible == (mode != 2) and not ui[h.sets].visible


def test_split_puts_the_carousel_away(fw):
    themed(fw, CLASSIC)
    ui = Ui(fw)
    h = Homes(ui, carousel=True)
    for w in h.carousel:
        ui[w].visible = 1
    ui[h.main].visible = 0
    h.open(fw)
    assert not any(ui[w].visible or ui[w].sensitive for w in h.carousel) and ui[h.main].visible and ui[h.art].visible


# Split's pane: what the highlighted row shows

def home_row(fw, surface, row, id=0, painting=0):
    fw.fn('coverflow_home_row', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(surface, row, id, painting)
    return fw.var('coverflow__home', Home).kind


def pane_rect(fw, h):
    return h.win, (SPLIT_W, 0, HOME_PW, fw.fn('client_h', C.c_int)())


@pytest.mark.parametrize('name, kind', (('img_library', PANE_LIBRARY), ('img_cover_flow', PANE_COVERFLOW), ('img_folder', PANE_FOLDER),
                                        ('img_services', PANE_STREAM), ('img_stream', PANE_STREAM), ('img_settings', PANE_SETTINGS),
                                        ('img_unknown', PANE_LIBRARY)))
def test_the_pane_follows_the_highlighted_row(fw, name, kind):
    _, ui, h, _ = home_open(fw)
    row = ui.make(h.view, 'image', name=name)
    ui.dirty.clear()
    assert home_row(fw, h.view, row) == kind
    assert ui.dirty == [pane_rect(fw, h)]  # on a wheel move the pane joins the row's frame
    ui.dirty.clear()
    home_row(fw, h.view, row)
    assert not ui.dirty  # the same row: nothing compared or drawn again


@pytest.mark.parametrize('shortcut, kind', (('6', PANE_GAMES), ('0', PANE_ROCKBOX), ('5', PANE_SHUFFLE), ('8', PANE_FOLDER),
                                            ('9', PANE_FOLDER), ('1', PANE_LIBRARY), ('4', PANE_LIBRARY)))
def test_the_shortcut_row_shows_what_its_shortcut_opens(fw, shortcut, kind):
    _, ui, h, _ = home_open(fw, SHORTCUT=shortcut)
    assert home_row(fw, h.view, h.rows['img_rockbox']) == kind


def test_a_paint_s_row_change_shows_in_the_next_frame(fw):
    _, ui, h, home = home_open(fw)
    ui.dirty.clear()
    assert home_row(fw, h.view, h.rows['img_cover_flow'], painting=1) == PANE_COVERFLOW
    assert not ui.dirty  # a paint can't invalidate: AWTK drops it with the frame
    i, ms = ui.timer(address(fw, 'coverflow__pane_after'))
    assert ms == 0 and home.pane_timer == i
    ui.fire(i)
    assert ui.dirty == [pane_rect(fw, h)] and not home.pane_timer


def test_the_pane_in_home_s_other_lists(fw):
    _, ui, h, _ = home_open(fw)
    search = fw.fn('library_search', C.c_int)()
    sets_view, lib_view, stream_view = (ui[lst].kids[0] for lst in (h.sets, h.lib, h.stream))
    assert home_row(fw, sets_view, ui[sets_view].kids[0]) == PANE_SETTINGS
    assert home_row(fw, lib_view, ui[lib_view].kids[0], id=search) == PANE_SHUFFLE  # Search's row: Shuffle's count, the songs
    assert home_row(fw, lib_view, ui[lib_view].kids[1], id=search + 1) == PANE_LIBRARY
    assert home_row(fw, stream_view, ui[stream_view].kids[0]) == PANE_SERVICE
    ui.dirty.clear()
    home_row(fw, stream_view, ui[stream_view].kids[1])
    assert ui.dirty == [pane_rect(fw, h)]  # another service: drawn again though the kind stays


def test_the_pane_finds_a_service_row_s_icon_and_name(fw):
    _, ui, h, home = home_open(fw)
    stream_view = ui[h.stream].kids[0]
    row = ui.make(stream_view, 'view')
    label, icon = ui.make(row, 'hscroll_label'), ui.make(row, 'image')
    home_row(fw, stream_view, row)
    assert (home.row, home.row_image, home.row_label) == (row, icon, label)


@pytest.mark.parametrize('theme, keys, follows', ((CLASSIC, {'ROWPREVIEW': '0'}, True), (MINIMAL, {}, False),
                                                  (CLASSIC, {'HOME': '1'}, False), (CLASSIC, {'ART': ''}, False)))
def test_without_row_preview_the_pane_is_now_playing_s_and_without_the_pane_rows_change_nothing(fw, theme, keys, follows):
    _, ui, h, home = home_open(fw, theme, **{k: v for k, v in keys.items() if k != 'ART'})
    if 'ART' in keys:
        home.art = 0
    home.kind = PANE_GAMES
    kind = home_row(fw, h.view, h.rows['img_settings'])
    assert (kind, home.row) == ((PANE_NOW, h.rows['img_settings']) if follows else (PANE_GAMES, None))


@pytest.mark.parametrize('kind, counts, want', (
    (PANE_SHUFFLE, dict(songs=1), b'1 Song'), (PANE_LIBRARY, dict(songs=12), b'12 Songs'), (PANE_COVERFLOW, dict(albums=1), b'1 Album'),
    (PANE_COVERFLOW, dict(albums=704), b'704 Albums'), (PANE_GAMES, dict(games=4), b'4 Games'), (PANE_GAMES, dict(games=1), b'1 Game'),
    (PANE_STREAM, dict(services=5), b'5 Services'), (PANE_STREAM, dict(services=1), b'1 Service'),
    (PANE_SETTINGS, dict(free_tenths=412), b'41.2 GB Free'), (PANE_SETTINGS, dict(free_tenths=5), b'0.5 GB Free'),
    (PANE_LIBRARY, {}, b''), (PANE_COVERFLOW, dict(songs=3), b''), (PANE_SETTINGS, {}, b''), (PANE_FOLDER, dict(songs=9), b''),
    (PANE_ROCKBOX, dict(games=9), b'')))
def test_the_pane_s_caption_counts_what_its_row_opens(fw, kind, counts, want):
    home = fw.var('coverflow__home', Home)
    home.kind = kind
    for k, v in counts.items():
        setattr(home, k, v)
    t = C.create_string_buffer(20)
    assert fw.fn('coverflow__pane_caption', C.c_int, C.c_void_p, C.c_uint)(t, 20) == len(want) and t.value == want


@pytest.mark.parametrize('queued, want', ((0, b''), (1, b'1 Song'), (12, b'12 Songs')))
def test_now_playing_s_pane_counts_the_queue(fw, mcl, queued, want):
    q = Deques(fw)
    if queued:
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_void_p).value = q.new(range(1, queued + 1))
    t = C.create_string_buffer(20)
    assert fw.fn('coverflow__pane_caption', C.c_int, C.c_void_p, C.c_uint)(t, 20) == len(want) and t.value == want


def test_home_s_counts_wait_out_a_scan_and_forget_what_it_will_change(fw):
    _, ui, h, home = home_open(fw)
    counts = fw.fn('coverflow__home_counts', C.c_int, C.c_void_p)
    claim(O['SCAN_THREAD'])
    saved = C.string_at(O['SCAN_DONE'], 8)
    try:
        C.c_int.from_address(O['SCAN_THREAD']).value, C.c_int.from_address(O['SCAN_DONE']).value = 1, 0
        home.songs, home.albums, home.counted = 40, 3, 1
        ui.dirty.clear()
        assert counts(None) == 8 and (home.songs, home.albums, home.counted) == (0, 0, 0)  # looks again until it ends
        assert ui.dirty == [pane_rect(fw, h)]
        assert counts(None) == 8 and len(ui.dirty) == 1  # nothing shown to take back
        assert not fw.calls('fw_pthread_create')
    finally:
        C.memmove(O['SCAN_DONE'], saved, 8)


def test_a_refused_count_thread_is_asked_again_at_the_next_home(fw):
    _, ui, h, home = home_open(fw)
    fw.mock('fw_pthread_create', lambda *_: 11)
    counts = fw.fn('coverflow__home_counts', C.c_int, C.c_void_p)
    assert counts(None) == 0 and not home.counting and not home.count_timer and home.pane_read & 1  # the card's reads still run
    counts(None)
    assert len(fw.calls('fw_pthread_create')) == 2


def test_home_s_foreground_asks_the_counts_again_sooner_than_boot_and_reads_the_row_afresh(fw):
    _, ui, h, home = home_open(fw)
    _, boot = ui.timer(address(fw, 'coverflow__home_counts'))
    ui.timers.clear()
    home.count_timer, home.row = 0, h.rows['img_settings']
    ui.click(h.win, EVT_WINDOW_FOREGROUND)
    i, ms = ui.timer(address(fw, 'coverflow__home_counts'))
    assert 0 < ms < boot and home.count_timer == i  # once Home has slid back, not after a boot's paints
    assert not home.row  # Row Preview may have changed in Settings


# The pan's timer

def drifting(fw, light=1):
    home = fw.var('coverflow__home', Home)
    home.tw, home.rx, home.ry, home.u0, home.v0 = 300, 18, 29, 10, 0
    home.du, home.dv = 10 * 256, 0
    fw.var('g_backlight_status', C.c_ubyte).value = light
    return home


def test_the_pan_steps_while_it_shows_capped_after_a_stall(fw):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    now = [1000]
    fw.mock('time_now_ms', lambda *_: now[0])
    drift = fw.fn('coverflow__home_drift', C.c_int, C.c_void_p)
    tick, cap = O['DRIFT_TICK_MS'], O['DRIFT_MAX_STEP_MS']
    home.drift, home.drift_last, home.drift_ms = 3, 1000 - tick, 0
    ui.dirty.clear()
    assert drift(None) == 8 and home.drift_ms == tick and home.drift_last == 1000  # a tick on time keeps its time
    assert ui.dirty == [pane_rect(fw, h)]  # the pan moved: the panel repaints
    now[0] = 61000
    assert drift(None) == 8 and home.drift_ms == tick + cap  # a stalled loop never jumps
    home.drift_ms = 2 * O['DRIFT_X_MS'] * O['DRIFT_Y_MS'] - 1
    now[0] += tick
    drift(None)
    assert home.drift_ms == tick - 1  # whole cycles of both axes: no wrap mid-cycle


def test_a_pan_that_has_not_moved_repaints_nothing(fw):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    home.rx = home.ry = 0  # no margin: nothing to pan across
    fw.mock('time_now_ms', lambda *_: 1100)
    ui.dirty.clear()
    assert fw.fn('coverflow__home_drift', C.c_int, C.c_void_p)(None) == 8 and not ui.dirty


@pytest.mark.parametrize('change', ('dark', 'locked', 'covered', 'sliding', 'still', 'pane', 'row'))
def test_the_pan_stops_once_it_no_longer_shows(fw, change):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    if change == 'dark':
        fw.var('g_backlight_status', C.c_ubyte).value = 0
    elif change == 'locked':
        fw.var('g_lockscreen_pageflag', C.c_ubyte).value = 1
    elif change == 'covered':
        ui.top = ui.make(0, 'window')
    elif change == 'sliding':
        ui.animating = 1
    elif change == 'still':
        home.tw = 0
    elif change == 'pane':
        home.pane = 1
    else:
        home.kind = PANE_SETTINGS
    home.drift = 4
    ui.dirty.clear()
    assert fw.fn('coverflow__home_drift', C.c_int, C.c_void_p)(None) == 0 and not home.drift and not ui.dirty


def test_a_home_paint_arms_the_pan_once_while_it_shows(fw, mcl):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    fw.mock('time_now_ms', lambda *_: 4321)
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    i, ms = ui.timer(address(fw, 'coverflow__home_drift'))
    assert ms == O['DRIFT_TICK_MS'] and home.drift == i and home.drift_last == 4321
    home.tw = 300
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    assert len([t for t in ui.timers.values() if t[0] == address(fw, 'coverflow__home_drift')]) == 1


# A card app's URL queue: its songs have no path and a cover by URL leaves g_lastcover_url "", so a paint between the
# song change and its cover's request shows the last song's file, still in place, under the new album. A quick skip
# asks for the next cover while the last one downloads: that lands first, then the next one with no paint between
# its type 0 and 2. Home keys the file written, so each download is shown.
@in_fork
def test_home_shows_each_cover_a_url_queue_downloads(fw, mcl):
    _, ui, h, _ = home_open(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    recs = [Rec(path='') for _ in range(2)]
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = Deques(fw).new([rec.p for rec in recs])
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = b''
    kind, pic = fw.var('g_playcover_type', C.c_ubyte), Externpic(fw)
    def downloaded(size=20000):
        pic.write(size)
        kind.value = 2
    def paint():
        fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
        return len(fw.calls('image_manager_unload_bitmap_by_name'))
    mcl.pos = 0
    player_rig(fw, id3_path=b'', album=b'One')
    ask_cover(fw)
    downloaded()
    assert paint() == 1
    mcl.pos = 1
    player_rig(fw, id3_path=b'', album=b'Two')
    assert paint() == 2  # the last song's file, under the new album
    ask_cover(fw)
    downloaded()  # the first song's cover again, still downloading at the skip
    assert paint() == 3
    kind.value = 0  # the thread takes the second song's request: no paint sees it
    downloaded(31000)
    assert paint() == 4
    assert paint() == 4


def test_a_dark_screen_arms_no_pan(fw, mcl):
    _, ui, h, _ = home_open(fw)
    drifting(fw, light=0)
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    assert not ui.timer(address(fw, 'coverflow__home_drift'))


# A Streaming row's icon in the pane

class Bitmaps:
    """widget_load_image by url: a bitmap_t (w @0, h @4, format @0xe) whose lock reads its pixels; bitmap_create_ex a
    blank one the C writes into."""
    def __init__(self, fw, images=None, create=True):
        self.images, self.made, self.loads, self.unloads, self.data, self.reads = images or {}, [], [], [], {}, 0
        self.keep = []
        fw.mock('widget_load_image', self.load)
        fw.mock('widget_unload_image', lambda owner, b, *_: self.unloads.append(b))
        fw.mock('bitmap_lock_buffer_for_read', self.read)
        fw.mock('bitmap_lock_buffer_for_write', lambda b, *_: C.addressof(self.data[b][0]))
        fw.mock('bitmap_get_line_length', lambda b, *_: self.data[b][1])
        fw.mock('bitmap_create_ex', self.create if create else lambda *_: 0)

    def read(self, b, *_):
        self.reads += 1
        return C.addressof(self.data[b][0])

    def load(self, owner, url, b, *_):
        url = C.string_at(url).decode()
        self.loads.append(url)
        if url not in self.images:
            return 1
        self.put(b, *self.images[url])
        return 0

    def put(self, b, w, h, pixels, fmt=1):
        (C.c_uint * 2).from_address(b)[:] = [w, h]
        C.c_ushort.from_address(b + 14).value = fmt
        self.data[b] = (pixels, 4 * w)

    def create(self, w, h, line, fmt, *_):
        block, pixels = (C.c_uint * 16)(w, h, line), (C.c_ubyte * (h * line))()
        C.c_ushort.from_address(C.addressof(block) + 14).value = fmt
        self.made.append((block, pixels))
        self.data[C.addressof(block)] = (pixels, line)
        return C.addressof(block)


ICON = 'file:///mnt/mmc/apps/x/icon.png'


def scaled(fw, bitmaps, w, h, colour, px, name=ICON, fmt=1):
    src = (C.c_uint * 64)()
    bitmaps.put(C.addressof(src), w, h, rgba(w, h, colour), fmt)
    bitmaps.keep.append(src)
    return fw.fn('coverflow__pane_scaled', C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)(C.addressof(src), name.encode(), px), \
        C.addressof(src)


def pixels_of(bitmaps, b):
    pixels, line = bitmaps.data[b]
    return [list((C.c_uint * (line // 4)).from_buffer(pixels, y * line)) for y in range(len(pixels) // line)]


def test_pane_scaled_keeps_a_flat_icon_flat_at_the_theme_s_size(fw):
    b = Bitmaps(fw)
    out, src = scaled(fw, b, 2, 2, lambda x, y: (200, 100, 50, 255), 7)
    assert out != src and rect(out)[:2] == (7, 7)
    assert all(p == 0xff3264c8 for row in pixels_of(b, out) for p in row)  # RGBA8888, its bytes r, g, b, a
    assert fw.var('coverflow__home', Home).svc_name == ICON.encode()


def test_pane_scaled_weighs_by_alpha_so_a_transparent_pixel_lends_no_colour(fw):
    b = Bitmaps(fw)
    out, _ = scaled(fw, b, 2, 1, lambda x, y: (0, 0, 255, 255) if x == 0 else (255, 0, 0, 0), 8)
    for row in pixels_of(b, out):
        assert all(p & 255 == 0 for p in row)  # no red from the transparent pixel
        alphas = [p >> 24 for p in row]
        assert alphas[0] == 255 and alphas[-1] == 0 and alphas == sorted(alphas, reverse=True)
        assert all(p >> 16 & 255 == 255 for p in row if p >> 24)  # what shows is the opaque pixel's blue, whole
        assert row[-1] == 0


def test_pane_scaled_stretches_an_edge_over_one_pixel_not_the_stretch(fw):
    """#303: a card app's 52 px icon at 96 px was soft, plain bilinear blending every edge over two pixels."""
    b = Bitmaps(fw)
    out, _ = scaled(fw, b, 2, 1, lambda x, y: (0, 0, 0, 255) if x == 0 else (255, 255, 255, 255), 8)
    for row in pixels_of(b, out):
        reds = [p & 255 for p in row]
        assert reds == sorted(reds) and len([r for r in reds if 0 < r < 255]) <= 1, reds


def test_pane_scaled_scales_an_app_once(fw):
    b = Bitmaps(fw)
    out, _ = scaled(fw, b, 4, 4, lambda x, y: (9, 9, 9, 255), 6)
    reads = b.reads
    again, _ = scaled(fw, b, 4, 4, lambda x, y: (1, 2, 3, 255), 6)
    assert again == out and b.reads == reads and len(b.made) == 1


@pytest.mark.parametrize('w, h, fmt, create', ((513, 4, 1, True), (4, 513, 1, True), (0, 4, 1, True), (4, 4, 5, True), (4, 4, 0, True),
                                               (4, 4, 1, False)))
def test_pane_scaled_leaves_what_it_can_t_scale_as_it_is(fw, w, h, fmt, create):
    b = Bitmaps(fw, create=create)
    out, src = scaled(fw, b, w, h, lambda x, y: (1, 2, 3, 255), 6, fmt=fmt)
    assert out == src and not fw.var('coverflow__home', Home).svc_name


class Canvas:
    """A canvas whose LCD holds a fill colour: the images, texts (in the last font's px), fonts and filled rects drawn."""
    def __init__(self, fw):
        self.lcd, self.raw = (C.c_char * 0x200)(), (C.c_char * 0x100)()
        C.c_uint.from_buffer(self.lcd, O['LCD_FILL_COLOR']).value = 0x12345678
        C.c_void_p.from_buffer(self.raw, O['CANVAS_LCD']).value = C.addressof(self.lcd)
        self.p = C.addressof(self.raw)
        self.images, self.texts, self.fonts, self.fills, self.rects = [], [], [('', 0)], [], []
        fw.mock('canvas_draw_image', lambda c, b, s, d, *_: self.images.append((b, rect(s), rect(d))))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.texts.append(
            (''.join(map(chr, (C.c_uint * n).from_address(s))), rect(r), self.fonts[-1][1])))
        fw.mock('canvas_set_font', lambda c, face, px, *_: self.fonts.append((C.string_at(face).decode() if face else '', px)))
        fw.mock('canvas_measure_text', lambda c, s, n, *_: n * self.fonts[-1][1] * .75)
        fw.mock('canvas_set_fill_color', lambda c, colour, *_: self.fills.append(colour & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.rects.append((self.fills[-1], *(C.c_int(v).value for v in (x, y, w, h)))))


def service_rig(fw, icon, name='Spotify', images=None):
    t, ui, h, home = home_open(fw)
    row = ui.make(0, 'view')
    image, label = ui.make(row, 'image'), ui.make(row, 'hscroll_label')
    ui[image].image, ui[label].text = icon, name
    home.kind, home.row, home.row_image, home.row_label = PANE_SERVICE, row, image, label
    bitmaps, canvas = Bitmaps(fw, images), Canvas(fw)

    def paint():
        canvas.images.clear(), canvas.texts.clear()
        fw.fn('home_pane_paint', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.p, 5, -3, HOME_PW, 294)
        assert canvas.fills[-1] == 0x12345678  # the LCD's fill back
    return t, ui, home, bitmaps, canvas, paint


def icon_at(t, side):
    return (0, 0, side, side), (5 + t['pane_svc_x'], -3 + t['pane_svc_y'], side, side)


def name_at(t):
    return 5, -3 + t['pane_count_y'], t['pane_count_w'], 24


def test_a_built_in_service_s_pane_is_its_icon_and_name(fw):
    t, ui, home, b, canvas, paint = service_rig(fw, 'stream_radio')
    side = t['pane_svc_px']
    b.images['classic_light_stream_radio'] = (side, side, rgba(1, 1, lambda x, y: (0, 0, 0, 0)))  # the build's, made at it
    paint()
    assert b.loads == ['classic_light_stream_radio'] and not b.unloads  # the build's copy, at the pane's size
    assert [i[1:] for i in canvas.images] == [icon_at(t, side)] and not b.made  # drawn as it is: nothing scaled
    assert canvas.texts == [('Spotify', name_at(t), t['pane_count_px'])]


def test_a_card_app_s_icon_is_scaled_to_the_pane_once_and_its_file_let_go(fw):
    t, ui, home, b, canvas, paint = service_rig(fw, ICON, images={ICON: (64, 64, rgba(64, 64, lambda x, y: (5, 6, 7, 255)))})
    side = t['pane_svc_px']
    paint()
    assert canvas.images == [(home.svc, *icon_at(t, side))] and rect(home.svc)[:2] == (side, side)
    assert len(b.unloads) == 1  # the app's file leaves the image cache
    paint()
    assert canvas.images == [(home.svc, *icon_at(t, side))] and b.loads == [ICON] and len(b.made) == 1


def test_a_service_icon_that_won_t_load_shows_the_card_apps_own(fw):
    t, ui, home, b, canvas, paint = service_rig(fw, 'stream_gone', images={'classic_light_stream_app': (90, 80, rgba(1, 1, lambda x, y: (0, 0, 0, 0)))})
    paint()
    assert b.loads == ['classic_light_stream_gone', 'classic_light_stream_app'] and not b.unloads
    assert [i[1:] for i in canvas.images] == [((0, 0, 90, 80), icon_at(t, 0)[1][:2] + (t['pane_svc_px'],) * 2)]
    b.images.clear()
    paint()
    assert not canvas.images and canvas.texts  # no icon at all: the name still shows


def test_a_long_service_name_shrinks_to_three_quarters_then_loses_its_end(fw):
    t, ui, home, b, canvas, paint = service_rig(fw, 'stream_radio', name='Internet Radio Stations')
    paint()
    (text, box, px), = canvas.texts
    px0 = t['pane_count_px']
    assert box == name_at(t) and px <= px0 * 3 // 4
    assert text.endswith('…') and 'Internet Radio Stations'.startswith(text[:-1])
    assert len(text) * (px0 * 3 // 4) * .75 <= box[2] < (len(text) + 1) * (px0 * 3 // 4) * .75  # as much as fits


def test_no_service_row_or_name_draws_no_service(fw):
    t, ui, home, b, canvas, paint = service_rig(fw, 'stream_radio', name='')
    ui[home.row_label].text = None
    paint()
    assert not canvas.texts
    home.row = 0
    paint()
    assert not canvas.images and not canvas.texts


# The pages' pieces, shared with photos.c, books.c and navigation.c

@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets in this build: navigation.c's st."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


def bar_title(fw, ui, scratch, width):
    """Classic's bar title, width px: a caption of n characters measures 10 n px."""
    bar = ui.make(0, 'hscroll_label', 0, 0, width, 30)
    C.c_void_p.from_address(C.addressof(fw.var('st', C.c_char)) + scratch['bar_title']).value = bar
    fw.mock('widget_get_canvas', lambda *_: 0x1234)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: float(n * 10))


def page_title(fw, body, caption, n):
    return fw.fn('page_title', C.c_void_p, C.c_void_p, C.c_char_p, C.c_int)(body, caption, n)


def test_minimal_s_page_title_is_its_own_row(fw):
    t = themed(fw, MINIMAL)
    ui = Ui(fw)
    title = page_title(fw, ui.make(0, 'view'), b'Albums', 3)
    assert ui.at(title) == (t['page_x'], 0, 375 - 2 * t['page_x'], t['page_title_h']) and ui[title].text == 'Albums'
    assert ui[title].visible and not ui[title].name  # nothing for a bar to read


@pytest.mark.parametrize('rows, width, hidden', ((3, 200, True), (3, 40, False), (0, 200, False)))
def test_classic_s_bar_holds_the_caption_unless_it_is_all_the_page_says_or_won_t_fit(fw, scratch, rows, width, hidden):
    t = themed(fw, CLASSIC)
    ui = Ui(fw)
    bar_title(fw, ui, scratch, width)
    title = page_title(fw, ui.make(0, 'view'), b'Albums', rows)
    assert ui[title].name == '_page_title' and ui[title].text == 'Albums'  # what the bar reads
    assert ui[title].visible == (not hidden)
    if not hidden:  # the page's first line, a row high, in the page's text column
        x, y, w, h = ui.at(title)
        assert (x, y, h) == (fw.fn('page_x', C.c_int)(), 0, t['page_row']) and x + w == 375 - x


def page_list_or(fw, page, body, name, none, n, item_h=48):
    return fw.fn('page_list_or', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(
        page, body, name, none, n, item_h)


@pytest.mark.parametrize('theme', (MINIMAL, CLASSIC))
def test_an_empty_page_says_so_and_a_bar_still_reads_its_name(fw, theme):
    themed(fw, theme)
    ui = Ui(fw)
    page = ui.make(0, 'window')
    body = ui.make(page, 'view')
    page_list_or(fw, page, body, b'Photos', b'No photos', 0)
    labels = ui.of('hscroll_label', body)
    assert [ui[w].text for w in labels if ui[w].visible] == ['No photos']
    read = [ui[w].text for w in labels if ui[w].name == '_page_title' and not ui[w].visible]
    assert read == (['Photos'] if theme == CLASSIC else [])
    assert ui.at(ui.of('list_view', body)[0])[3] == 0  # no rows


@pytest.mark.parametrize('n, rows', ((3, 3), (30, None)))
def test_a_page_list_s_rows_run_from_its_title_to_the_page_s_bottom_or_its_last_row(fw, n, rows):
    t = themed(fw, MINIMAL)
    ui = Ui(fw)
    page = ui.make(0, 'window')
    body = ui.make(page, 'view')
    view = page_list_or(fw, page, body, b'Albums', b'None', n)
    lv, row = ui[view].parent, t['page_row']
    room = fw.fn('client_h', C.c_int)() - t['page_title_h']
    assert ui.at(lv) == (0, t['page_title_h'], 375, rows * row if rows else room)
    assert ui[lv].props['item_height'] == row and ui[view].props['virtual_h'] == n * row and ui[view].props['yslidable'] == 1
    assert [ui[w].text for w in ui.of('hscroll_label', body)] == ['Albums'] and ui[body].visible


def page_row_detail(fw, view, index, caption, detail, click=0x1234, into=False):
    name = 'page_row_detail_into' if into else 'page_row_detail'
    return fw.fn(name, C.c_void_p, C.c_void_p, C.c_int, C.c_char_p, C.c_char_p, C.c_void_p)(view, index, caption, detail, click)


@pytest.mark.parametrize('theme', (MINIMAL, CLASSIC))
def test_a_row_with_a_detail_puts_its_caption_over_it_in_the_page_s_text_column(fw, theme):
    t = themed(fw, theme)
    ui = Ui(fw)
    item = page_row_detail(fw, ui.make(0, 'scroll_view'), 2, b'Song', b'3 plays')
    h = t['page_detail']
    assert ui.at(item) == (0, 2 * h, 375, h) and ui.handlers(item) == [(0x1234, 2)]  # the third of the taller rows
    caption, detail = ui.of('hscroll_label', item)
    assert (ui[caption].text, ui[detail].text) == ('Song', '3 plays')
    (cx, cy, cw, ch), (dx, dy, dw, dh) = ui.at(caption), ui.at(detail)
    x = fw.fn('page_x', C.c_int)()
    assert cx == dx == x and cw == dw == 375 - 2 * x  # page_x's text column, its margins even
    assert 2 * cy + ch < 2 * dy + dh and cy + ch <= h and dy + dh <= h  # the caption over the detail, both ending in the row
    grey = ui[detail].props['style:normal:text_color'] & 0xffffffff
    r, g, b, a = (grey >> s & 255 for s in (0, 8, 16, 24))
    assert a == 255 and r == g == b < 255  # stock's second line: an opaque grey, dimmer than the caption's white
    assert ui[item].name == ('_page_tall' if t['sep'] else '')  # its separators' row


@pytest.mark.parametrize('theme', (MINIMAL, CLASSIC))
def test_an_empty_detail_centres_the_caption_above_the_theme_s_separators(fw, theme):
    t = themed(fw, theme)
    ui = Ui(fw)
    item = page_row_detail(fw, ui.make(0, 'scroll_view'), 0, b'Song', b'')
    over = ui.of('hscroll_label', page_row_detail(fw, ui.make(0, 'scroll_view'), 0, b'Song', b'3 plays'))[0]
    caption, detail = ui.of('hscroll_label', item)
    h, sep = t['page_detail'], len(t['sep'])
    _, y, _, ch = ui.at(caption)
    assert ui.at(caption)[::2] == ui.at(over)[::2]  # the same text column
    if sep:
        assert 2 * y + ch == h - sep  # centred in the row above its separators
    else:
        assert ui.at(caption) == ui.at(over)  # where it stands over a detail
    assert ui[detail].text == ''  # kept for whoever writes it


CHEVRON_GLYPH = 322  # row_into: stock's list_into glyph spans x 322 to 332 on a full-width row, as on the Library's


@pytest.mark.parametrize('theme', (MINIMAL, CLASSIC))
def test_a_row_into_another_page_ends_its_labels_before_the_chevron(fw, theme):
    t = themed(fw, theme)
    ui = Ui(fw)
    view = ui.make(0, 'scroll_view')
    item = page_row_detail(fw, view, 1, b'Albums', b'', into=True)
    h, sep, x = t['page_detail'], len(t['sep']), fw.fn('page_x', C.c_int)()
    caption, detail = ui.of('hscroll_label', item)
    (cx, cy, cw, ch), (dx, _, dw, _) = ui.at(caption), ui.at(detail)
    assert cx == dx == x and 2 * cy + ch == h - sep  # centred in the row (above any separators), beside the chevron
    assert cw == dw > 0 and x + cw <= CHEVRON_GLYPH  # both labels end before its glyph
    chevron, = ui.of('image', item)
    vx, vy, vw, vh = ui.at(chevron)
    assert vx <= CHEVRON_GLYPH and CHEVRON_GLYPH + 10 <= vx + vw <= 375 and (vy, vh) == (0, h)  # the glyph in it, a row high
    assert ui[chevron].image == 'list_into' and ui[chevron].visible == (not t['chevron_sel'])  # else the selection's
    fw.fn('page_row_into', None, C.c_void_p, C.c_int, C.c_char_p, C.c_void_p)(view, 2, b'Songs', 0)
    row = ui[view].kids[-1]
    lx, ly, lw, lh = ui.at(ui.of('hscroll_label', row)[0])
    assert ui.at(row)[3] == t['page_row'] and (lx, ly, lh) == (x, 0, t['page_row']) and 0 < lw <= CHEVRON_GLYPH - x


@pytest.mark.parametrize('theme', (MINIMAL, CLASSIC))
def test_a_level_slides_in_from_the_side_it_comes_from_where_the_theme_moves_levels(fw, theme):
    t = themed(fw, theme)
    ui = Ui(fw)
    lv = ui.make(0, 'list_view', 30, 48, 375, 200)
    view = ui.make(lv, 'scroll_view')
    push = fw.fn('page_push', C.c_int, C.c_void_p, C.c_int)
    ms = t['motion.inplace.ms']
    if not ms:
        assert push(view, 1) == 0 and ui.at(lv)[0] == 30 and not ui.animators
        return
    assert push(view, 1) == 1 and ui.at(lv)[0] == 375  # from its rest at 0, a width to the right
    assert ui.animators == [(lv, ms, t['motion.inplace.easing'], 'x')]
    assert push(view, -1) == 1 and ui.at(lv)[0] == -375
    assert push(view, 0) == 0 and push(None, 1) == 0 and len(ui.animators) == 2
    ui.animate = False
    assert push(view, 1) == 0 and ui.at(lv)[0] == 0  # no animator: the list at x 0


# The covers' captions

class Th(C.Structure):
    _fields_ = [*((k, C.c_float) for k in 'pos vel goal lead lead_v rate tau ticks seen seen_lead stretch'.split()),
                *((k, C.c_uint) for k in 'at tick_at edge_at letter_at'.split()),
                *((k, C.c_int) for k in 'raw on finger held dir dead early clicked bounced capped jump beat jump_to lean fold'.split()),
                ('fold_at', C.c_uint), *((k, C.c_float) for k in 'enter fold_off fold_vel hold_lead hold_run hold_xrun hold_xs'.split()),
                ('hold_k', C.c_int), ('arch', C.c_float), ('seen_arch', C.c_float)]


def captions(fw, theme, scoped=0, albums=(('Abbey', 'Beatles'), ('Help', 'Beatles'))):
    t = themed(fw, theme)
    ui = Ui(fw)
    recs = [Rec(n, artist=a) for n, a in albums]
    deque_of(fw, recs)
    c = cf(fw)
    c.albums, c.scoped, c.screen = 0x5000, scoped, COVERS
    c.slide = ui.make(0, 'slide_menu')
    for _ in range(len(recs) + (scoped != 0)):
        ui.make(c.slide, 'image')
    c.name, c.artist = ui.make(0, 'hscroll_label'), ui.make(0, 'hscroll_label')
    return t, ui, c, fw.fn('titles', None, C.c_uint)


def test_the_captions_name_the_centre_album_and_its_artist(fw):
    _, ui, c, titles = captions(fw, MINIMAL)
    titles(1)
    assert (ui[c.name].text, ui[c.artist].text) == ('Help', 'Beatles') and c.titled == 2
    assert ui[c.name].props['xoffset'] == 0 and ui[c.artist].props['xoffset'] == 0
    ui[c.name].text = 'stale'
    titles(1)
    assert ui[c.name].text == 'stale'  # once each time it changes
    assert 'style:normal:font_name' not in ui[c.name].props  # Minimal's captions keep the style's face


@pytest.mark.parametrize('scoped, card', ((ARTIST_ALBUMS, 'All albums'), (2, 'Sort: Rating')))
def test_the_card_after_the_albums_is_named_for_what_it_does(fw, scoped, card):
    _, ui, c, titles = captions(fw, MINIMAL, scoped)
    c.sort = SORT_RATING
    titles(2)
    assert (ui[c.name].text, ui[c.artist].text) == (card, '')


def test_classic_s_captions_are_in_now_playing_s_face(fw):
    t, ui, c, titles = captions(fw, CLASSIC)
    titles(0)
    for w in (c.name, c.artist):
        assert ui[w].props['style:normal:font_name'] == 'classic-light' and ui[w].props['style:normal:font_size'] == t['font_px[ROLE_NP]']


@pytest.mark.parametrize('state', ('covers', 'thumb', 'tracks', 'gone'))
def test_the_captions_catch_up_with_the_frame_after_the_paint(fw, state):
    _, ui, c, titles = captions(fw, MINIMAL)
    fw.var('fx', Fx).c = 1
    c.title_timer = 7
    if state == 'thumb':
        fw.var('th', Th).on = 1
    elif state == 'tracks':
        c.screen = TRACKS
    elif state == 'gone':
        c.slide = 0
    assert fw.fn('coverflow__retitle', C.c_int, C.c_void_p)(None) == 0 and not c.title_timer
    assert ui[c.name].text == ('Help' if state == 'covers' else None)


# The flip's panel paint, shared with navigation.c's hold menu

@pytest.mark.parametrize('light', (0, 1))
def test_the_panel_s_paper_header_shadow_and_border(fw, light):
    themed(fw, CLASSIC, LIGHT=str(light))
    canvas = Canvas(fw)
    paint = fw.fn('coverflow_panel_paint', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)
    paint(canvas.p, 40, 9, 57, 285, 0)
    paper, *head = canvas.rects
    assert paper[1:] == (40, 9, BACK_W, 285) and paper[0] >> 24 == 255 and bool(paper[0] & 0xffffff) == bool(light)
    assert [r[1:] for r in head] == [(40, 9 + j, BACK_W, 1) for j in range(57)]
    assert len({r[0] >> 24 for r in head}) == 1  # one alpha all the way down
    assert all(r[0] >> 16 & 255 > max(r[0] >> 8 & 255, r[0] & 255) for r in head)  # a blue: RGBA8888, b the third byte
    for s in (0, 8, 16):  # lighter row by row
        assert [r[0] >> s & 255 for r in head] == sorted(r[0] >> s & 255 for r in head)
    assert head[-1][0] != head[0][0]
    canvas.rects.clear()
    paint(canvas.p, 40, 9, 57, 285, 1)
    shadow, border = canvas.rects[:8], canvas.rects[8:]
    assert [r[1:] for r in shadow] == [(40, 9 + 55 + j, BACK_W, 1) for j in range(8)]  # over the header's foot and the rows
    alpha = [r[0] >> 24 for r in shadow]
    assert all(not r[0] & 0xffffff for r in shadow) and alpha[0] == alpha[-1] == 0 and max(alpha) > 0
    assert [r[1:] for r in border] == [(39, 8, BACK_W + 2, 1), (39, 9 + 285, BACK_W + 2, 1), (39, 9, 1, 285), (40 + BACK_W, 9, 1, 285)]
    assert len({r[0] for r in border}) == 1 and border[0][0] >> 24 == 255  # one opaque colour all round
    assert (border[0][0] & 255 > 0x80) == bool(light)


# The album panel's rows: the title runs to the length's text, not to the box reserved for it

def panel_row(fw, text, canvas=1):
    """panel_fit on two fake widgets, the length's box 66 wide (88 from an hour) and the title's 194; a character measures 10 px."""
    wide = (C.c_uint * (len(text) + 1))(*map(ord, text))
    title, time = bytearray(0x80), bytearray(0x80)
    for w, (width, height) in ((title, (194, 45)), (time, (88 if len(text) > 5 else 66, 45))):
        C.c_int.from_buffer(w, O['W_W']).value, C.c_int.from_buffer(w, O['W_H']).value = width, height
    bufs = {C.addressof((C.c_char * 0x80).from_buffer(w)): w for w in (title, time)}
    fw.mock('widget_get_canvas', lambda *a: 0x1000 * canvas)
    fw.mock('widget_get_text', lambda *a: C.addressof(wide))
    fw.mock('canvas_measure_text', lambda c, s, n, *a: 10.0 * n)
    fit = fw.fn('coverflow__panel_fit', None, C.c_void_p, C.c_void_p, C.c_uint)
    fit(C.addressof((C.c_char * 0x80).from_buffer(title)), C.addressof((C.c_char * 0x80).from_buffer(time)), len(text))
    return list(bufs.values()), fw.calls('widget_resize'), fw.calls('widget_move_resize'), title, time


@pytest.mark.parametrize('text,w', [('4:01', 40), ('14:57', 50), ('1:02:03', 70)])
def test_a_panel_title_runs_to_its_length_s_own_text(fw, text, w):
    _, resize, move, title, time = panel_row(fw, text)
    gap, box = 8, w + 4  # hscroll_label paints in its width less two 2 px margins, and scrolls a text wider than that
    assert [a[1:3] for _, a in resize] == [(BACK_W - 12 - box - gap - 12, 45)]  # from x 12, ending gap before the box
    assert [a[1:5] for _, a in move] == [(BACK_W - 12 - box, 0, box, 45)]  # the length stays at the right margin


def test_a_panel_length_too_wide_for_its_reserve_keeps_it(fw):
    _, resize, move, *_ = panel_row(fw, '10:00:00')  # 80 px and its margins against the 88 px reserve: 84 fits
    assert [a[3] for _, a in move] == [84]
    _, resize, move, *_ = panel_row(fw, '100:00:00')  # 90 px: left alone
    assert len(resize) == 1 and len(move) == 1


def test_a_panel_row_keeps_its_reserve_with_no_canvas(fw):
    _, resize, move, *_ = panel_row(fw, '4:01', canvas=0)
    assert not resize and not move
