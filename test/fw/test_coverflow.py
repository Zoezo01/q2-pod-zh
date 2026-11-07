"""coverflow.c on its device path: Home's pan and cubic filter, the shelf's geometry, touch, the album order and the art cache."""
import ctypes as C
import math
import pathlib
import random
import re
import struct
import unicodedata
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CX, TOP, ART, ONE, MISS = 187, 13, 160, 65536, 99
VIEW_W, VIEW_H = 375, 220
HOME_PW, HOME_H = 188, 290
PREPARING, COVERS, TRACKS = range(3)
ARTIST_ALBUMS = 1
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
    _fields_ = [(k, C.c_int) for k in 'xc zc c s bright'.split()]


class Job(C.Structure):
    _fields_ = [('track', C.c_char_p), ('key', C.c_uint), ('shared', C.c_int)]


class Dirent(C.Structure):
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


def api(fw):
    ptr, i, u = C.c_void_p, C.c_int, C.c_uint
    fns = {'coverflow__bspline_w': (None, u, ptr), 'home_sharpen': (None, ptr, i, i), 'coverflow__home_fit': (None, u, u, ptr, ptr),
           'coverflow__home_render': (None, ptr, i, i, i), 'coverflow__drift_at': (i, i, u), 'coverflow__pan_u': (i,),
           'coverflow__pan_v': (i,), 'coverflow__velocity': (i, u), 'coverflow__sample': (None, i, u),
           'coverflow__round_stride': (i, i, i), 'coverflow__floor_div': (i, i, i), 'coverflow__covers_live': (i,),
           'coverflow_hit': (i, i, i, i), 'coverflow_render': (None, ptr, i, i, ptr), 'coverflow__pose': (None, i, ptr),
           'coverflow__project': (i, ptr, i), 'coverflow__unproject': (i, ptr, i, ptr, ptr), 'coverflow__slot': (i, i, i, i),
           'coverflow__lower': (u, ptr, u, ptr, ptr), 'coverflow__album_in': (i, ptr, u, ptr), 'coverflow__album_index': (i, ptr),
           'coverflow__name_cmp': (i, ptr, ptr), 'coverflow__tie': (i, i, ptr, ptr), 'coverflow__by_name': (i, ptr, ptr),
           'coverflow__by_album': (i, ptr, ptr), 'album_cmp': (i, ptr, ptr), 'album_id': (u, ptr), 'album_dir': (u, C.c_char_p, i),
           'folder_cmp': (i, ptr, ptr), 'coverflow__cover_artist': (C.c_char_p, ptr), 'coverflow__initial': (u, i),
           'coverflow_jump': (i, ptr, i, i, ptr), 'coverflow__bound': (i, i, i, i), 'coverflow__unbound': (i, i, i),
           'coverflow__release': (None, i), 'coverflow__touch': (i, ptr, ptr), 'coverflow__stride_of': (i, ptr),
           'coverflow__stamp': (u, ptr), 'coverflow__lift': (None, i, u), 'coverflow__sort_saved': (i,),
           'coverflow_sort_name': (C.c_char_p,), 'coverflow_sort_step': (None,), 'coverflow__sort_next': (i, i),
           'blob_io': (i, C.c_char_p, C.c_char_p, ptr, u, i), 'file_size': (C.c_long, C.c_char_p),
           'coverflow__ff_run': (i, C.c_char_p, C.c_char_p, i, i, ptr), 'coverflow__build_art': (None, ptr),
           'coverflow__grid_cover': (i, C.c_char_p, C.c_char_p, i, i, i), 'coverflow_album_cover': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow_grid_cover': (i, C.c_char_p, C.c_char_p, i, i), 'coverflow_folder_cover': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow_player_cover': (i, C.c_char_p, C.c_char_p, i, i), 'coverflow__marked_empty': (i, C.c_char_p),
           'now_cover': (C.c_ubyte, ptr, ptr), 'hash_bytes': (u, u, ptr, u), 'fnv': (u, u, C.c_char_p)}
    fw.fn('coverflow__cf_restyle', None)()  # the theme's Cover Flow values, as the page's open copies them
    return SimpleNamespace(**{k.replace('coverflow__', ''): fw.fn(k, v[0], *v[1:]) for k, v in fns.items()})


libc = C.CDLL(None)
libc.malloc.restype, libc.malloc.argtypes = C.c_void_p, [C.c_size_t]
libc.calloc.restype, libc.calloc.argtypes = C.c_void_p, [C.c_size_t, C.c_size_t]
libc.free.argtypes = [C.c_void_p]
libc.qsort.argtypes = [C.c_void_p, C.c_size_t, C.c_size_t, C.c_void_p]


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


class Rec:
    """A stSongInfo as far as a 64-bit host can hold one: an 8-byte pointer at 0x14 overruns 0x18, so album and artist
    never share a record here."""
    def __init__(self, album=None, path=None, artist=None, album_artist=None, id=0):
        assert album is None or artist is None
        self.raw, self.keep = C.create_string_buffer(0x60), []
        struct.pack_into('<i', self.raw, 0, id)
        for off, s in ((0x0c, path), (0x14, album), (0x18, artist), (0x24, album_artist)):
            if s is not None:
                self.keep.append(C.create_string_buffer(s if isinstance(s, bytes) else s.encode()))
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
    assert (p.xc, p.zc, p.c, p.s, p.bright) == (16 * 112, 16 * 90, 32768, 56756, 192)
    p = pose(g, 2 * ONE)
    assert (p.xc, p.zc, p.bright) == (16 * (112 + 48), 16 * (90 + 20), 128)
    assert pose(g, 3 * ONE).bright == 128  # the third out stays (CF_THIRD)
    assert pose(g, 13 * ONE // 4).bright == 64 and pose(g, 7 * ONE // 2).bright == 0  # gone half a step on
    assert pose(g, 4 * ONE).bright == 0


def test_a_cover_s_pose_is_continuous_and_keeps_turning_in_step(fw):
    g = api(fw)
    last = pose(g, 0)
    for t in range(256, 3 * ONE, 257):
        p = pose(g, t)
        assert 0 <= p.xc - last.xc <= 16 and 0 <= p.zc - last.zc <= 16 and last.bright - 2 <= p.bright <= last.bright, t
        assert abs(math.hypot(p.c, p.s) - 65536) < 80
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


def corner_inset(fw):
    """coverflow_render fills cf_inset once."""
    g = api(fw)
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    g.coverflow_render(d, VIEW_W, 0, (C.c_void_p * 7)())
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


def test_hit_names_the_cover_drawn_there(fw):
    g = api(fw)
    ring = [texture(j) for j in range(7)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    pointers = (C.c_void_p * 7)(*[C.addressof(t) for t in ring])
    checked = 0
    for frac in (0, 8000, 16000, 32767, -8000, -32768):
        g.coverflow_render(d, VIEW_W, frac, pointers)

        def cover(x, y):
            c = d[y * VIEW_W + x]
            red, green = c & 255, c >> 8 & 255
            if red < 8:
                return None
            return round(((green / red * 240) - 30) / 30)
        for y in (26, 45, 70, 90):  # above the horizon: no reflection reaches here
            for x in range(2, VIEW_W - 2):
                if len({cover(x + k, y) for k in (-2, -1, 0, 1, 2)} | {cover(x, y - 1), cover(x, y + 1)}) > 1:
                    continue  # an edge's blend
                j = cover(x, y)
                got = g.coverflow_hit(frac, x, y)
                if j is None:
                    assert got == MISS, (frac, x, y)
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
    ring = (C.c_void_p * 7)()
    ring[3] = C.addressof(tex)
    g.coverflow_render(d, VIEW_W, 0, ring)
    for y in range(12, ART - 12):
        for x in range(ART):
            assert d[(TOP + y) * VIEW_W + CX - 80 + x] == tex[y * ART + x], (x, y)
    for t in (tex, d):
        libc.free(C.addressof(t))


def test_the_reflection_mirrors_the_cover_and_fades_out_in_46_px(fw):
    g = api(fw)
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xff000000 | (y + 60) for y in range(ART) for x in range(ART)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = (C.c_void_p * 7)()
    ring[3] = C.addressof(tex)
    g.coverflow_render(d, VIEW_W, 0, ring)
    bottom = TOP + ART
    for k in range(46):
        red = d[(bottom + k) * VIEW_W + CX] & 255
        mirrored = ART - 1 - k + 60
        assert abs(red - mirrored * 90 / 256 * (1 - (k + .5) / 46)) <= 2, k
    assert all(d[y * VIEW_W + CX] == 0xff000000 for y in range(bottom + 46, VIEW_H))
    for t in (tex, d):
        libc.free(C.addressof(t))


def test_every_pixel_of_a_frame_is_opaque_and_stays_in_the_frame(fw):
    g = api(fw)
    ring = [texture(j) for j in range(7)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    pointers = (C.c_void_p * 7)(*[C.addressof(t) for t in ring])
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
    ring = (C.c_void_p * 7)()
    ring[2] = C.addressof(tex)
    g.coverflow_render(a, VIEW_W, 0, ring)
    ring[2], ring[4] = None, C.addressof(flip)
    g.coverflow_render(b, VIEW_W, 0, ring)
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
    ok = fw.fn('coverflow__decode', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'x', out, unload)
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
    return fw.var('coverflow__cf', Cf)


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
        name, write = C.string_at(path).decode(), C.string_at(mode)[:1] == b'w'
        if not write and name not in self.files:
            return 0
        handle = 0x1000 + len(self.open)
        self.open[handle] = [name, 0]
        if write:
            self.files[name] = b''
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
    assert fw.var('coverflow__ff_kills', C.c_uint).value == (0 if exited else 1)


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
    assert fw.var('coverflow__ff_kills', C.c_uint).value == 1


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
