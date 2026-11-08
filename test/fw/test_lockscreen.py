"""lockscreen.c (docs/internals.md#lock-screen): the six built-in wallpapers wall_render draws, their place in
stock's picture list, the lock window's wallpaper and the picker's thumbnails, the Large clock and Classic's
info screen."""
import ctypes as C
import functools
import math
import statistics
import struct

import pytest

import host
import art as tools_art  # tools/, which host puts on the path
import ipod as tools_ipod
from conftest import O, block

WALLS = [b'q2pod:' + n for n in (b'aurora', b'sunrise', b'graphite', b'tide', b'ember', b'black')]
AURORA, SUNRISE, GRAPHITE, TIDE, EMBER, BLACK = range(6)
EVT_DESTROY, EVT_CLICK = O['EVT_DESTROY'], O['EVT_CLICK']
W_Y, W_W, W_PARENT = O['W_Y'], O['W_W'], O['W_PARENT']
RET_OK, RET_REPEAT = 0, 8
# The 4x4 ordered-dither (Bayer) index matrix: each value's place in the order a fraction fills a 4x4 tile
BAYER = [[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]
# Each mesh wallpaper's points' colours (r, g, b): a mesh gradient blends them, nothing outside
AURORA_RGB = [(0.06, 0.05, 0.22), (0.42, 0.20, 0.82), (0.08, 0.62, 0.70), (0.80, 0.22, 0.58), (0.04, 0.05, 0.16),
              (0.10, 0.06, 0.26), (0.05, 0.18, 0.35)]
SUNRISE_RGB = [(1.00, 0.74, 0.38), (0.96, 0.56, 0.46), (0.92, 0.38, 0.36), (0.74, 0.26, 0.42), (0.36, 0.12, 0.32),
               (0.50, 0.16, 0.36)]


class Lk(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in 'img full date pct card title artist hint'.split()),
                *((n, C.c_int) for n in 'built_in info page black large'.split()),
                ('timer', C.c_uint), ('shown', C.c_uint), ('full_key', C.c_uint),
                ('thumb', C.c_void_p * 6), ('small', C.c_void_p * 6)]


def lk(fw):
    return fw.var('lockscreen__lk', Lk)


def render(fw, wall, w, h, stride=None, fill=0x5a5a5a5a):
    """wall_render into a heap block of exactly stride x h pixels, first filled with fill: its answer and the
    rows, each stride long."""
    stride = stride or w
    p = block(fw, size=stride * h * 4)
    px = (C.c_uint * (stride * h)).from_address(p)
    px[:] = [fill] * (stride * h)
    ok = fw.fn('wall_render', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(p, stride, w, h, wall)
    return ok, [list(px[y * stride:(y + 1) * stride]) for y in range(h)]


def rgb(px):
    """RGBA8888 as the bitmap holds it, red in the low byte."""
    return px & 255, px >> 8 & 255, px >> 16 & 255


# The wallpapers.

@pytest.mark.parametrize('wall', range(6))
def test_each_wallpaper_is_opaque_and_stays_in_its_rows(fw, wall):
    ok, rows = render(fw, wall, 23, 17, stride=26)
    assert ok == 1
    for row in rows:
        assert all(px >> 24 == 0xff for px in row[:23])
        assert row[23:] == [0x5a5a5a5a] * 3  # the stride's gap is someone else's


@pytest.mark.parametrize('w, h', [(1, 1), (2, 3), (5, 5), (6, 11), (110, 94)])
def test_any_size_renders_whole(fw, w, h):
    ok, rows = render(fw, AURORA, w, h)
    assert ok == 1 and all(px >> 24 == 0xff for row in rows for px in row)


@pytest.mark.parametrize('wall', [BLACK, 6, 99, -1])
def test_black_and_any_unknown_id_are_black(fw, wall):
    ok, rows = render(fw, wall, 31, 9)
    assert ok == 1 and {px for row in rows for px in row} == {0xff000000}


@pytest.mark.parametrize('wall, points', [(AURORA, AURORA_RGB), (SUNRISE, SUNRISE_RGB)])
def test_a_mesh_blends_only_its_points_colours_and_the_vignette_only_darkens(fw, wall, points):
    _, rows = render(fw, wall, 75, 64)
    for k in range(3):
        lo, hi = min(p[k] for p in points), max(p[k] for p in points)
        seen = [rgb(px)[k] for row in rows for px in row]
        assert int(lo * 0.72 * 255) <= min(seen) and max(seen) <= hi * 255 + 1  # the vignette's at most 28%


def test_sunrise_is_warm_everywhere(fw):
    _, rows = render(fw, SUNRISE, 75, 64)
    assert all(rgb(px)[0] > rgb(px)[2] for row in rows for px in row)


def test_graphite_is_grey_a_touch_blue(fw):
    _, rows = render(fw, GRAPHITE, 90, 77)
    for row in rows:
        for px in row:
            r, g, b = rgb(px)
            assert r == g and 2 <= b - r <= 6  # its blue is 0.012 above, more in the sheen


def test_graphite_s_grain_is_the_same_every_time(fw):
    """The grain is hashed, not random: a picture rendered again for a new lock window is the same picture."""
    assert render(fw, GRAPHITE, 60, 40) == render(fw, GRAPHITE, 60, 40)


def test_graphite_s_grain_gives_each_row_its_own_shade(fw):
    """Rows 5 apart are evaluated at the same grid row fraction, so without grain two rows of a flat field
    would match; with it, neighbouring rows differ somewhere."""
    _, rows = render(fw, GRAPHITE, 60, 40)
    assert sum(rows[y] != rows[y + 1] for y in range(39)) >= 35


def test_tide_runs_from_navy_at_the_top_to_azure_below(fw):
    _, rows = render(fw, TIDE, 375, 320)
    for row in rows:
        for px in row:
            r, g, b = rgb(px)
            assert b >= g >= r
    top = sum(rgb(px)[2] for px in rows[0]) / 375
    bottom = sum(rgb(px)[2] for px in rows[-1]) / 375
    assert top < 40 and bottom > 3 * top


def test_tide_s_bands_are_waves(fw):
    """Each band's edge rises and falls across the picture, a crest 0.055 of the height either way. The vignette
    alone is the same either side of the middle; waves are not."""
    _, rows = render(fw, TIDE, 375, 320)
    edge = [next(y for y in range(320) if rgb(rows[y][x])[2] > 140) for x in range(375)]
    assert sum(abs(edge[x] - edge[374 - x]) > 3 for x in range(187)) > 60


def test_ember_rises_red_out_of_black(fw):
    _, rows = render(fw, EMBER, 375, 320)
    for row in rows:
        for px in row:
            r, g, b = rgb(px)
            assert r >= g and r >= b
    for corner in (rows[0][0], rows[0][-1]):
        assert max(rgb(corner)) <= 10
    r, g, b = rgb(rows[-1][187])
    assert r >= 160 and r >= 2 * g and r >= 2 * b


def test_noise_is_centred_hashed_noise_in_k(fw):
    """noise(x, k): hashed noise in -k/2..k/2, centred, a byte's worth of levels across the range, neighbours
    unrelated; k only scales it."""
    noise = fw.fn('lockscreen__noise', C.c_float, C.c_uint, C.c_float)
    values = [noise(x, 1) for x in range(4096)]
    assert all(-0.5 <= v <= 0.5 for v in values) and min(values) < -0.49 and max(values) > 0.49
    assert abs(statistics.fmean(values)) < 0.02 and len(set(values)) >= 200
    assert sum(a == b for a, b in zip(values, values[1:])) < 64
    assert [noise(x, 0.25) for x in range(64)] == [v / 4 for v in values[:64]]


class Point(C.Structure):
    _fields_ = [(name, C.c_float) for name in 'u v r g b'.split()]


def mesh(fw, points, u, v, aspect=1.0):
    """mesh's colour at (u, v) for points [(u, v, r, g, b)], the points in a heap block of exactly their size."""
    size = C.sizeof(Point) * len(points)
    p = block(fw, size=size)
    C.memmove(p, (Point * len(points))(*(Point(*q) for q in points)), size)
    out = block(fw, size=3 * 4)
    fw.fn('lockscreen__mesh', None, C.c_void_p, C.c_int, C.c_float, C.c_float, C.c_float, C.c_void_p)(
        p, len(points), u, v, aspect, out)
    return list((C.c_float * 3).from_address(out))


RED, BLUE = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)


@pytest.mark.parametrize('u, v, aspect', [(0.3, 0.7, 1.0), (0.0, 0.0, 1.17), (1.0, 0.4, 0.5), (0.9, 1.0, 2.0)])
def test_a_mesh_of_one_point_is_its_colour_everywhere(fw, u, v, aspect):
    """The blend's weights add up to one, wherever it's taken."""
    assert mesh(fw, [(0.3, 0.7, 0.2, 0.4, 0.6)], u, v, aspect) == pytest.approx([0.2, 0.4, 0.6])


@pytest.mark.parametrize('aspect', [1.0, 1.17])
def test_a_mesh_halfway_between_two_points_is_their_average(fw, aspect):
    across = [(0.2, 0.5, *RED), (0.8, 0.5, *BLUE)]
    down = [(0.5, 0.2, *RED), (0.5, 0.8, *BLUE)]
    for points in (across, down):
        assert mesh(fw, points, 0.5, 0.5, aspect) == pytest.approx([0.5, 0, 0.5])


def test_a_mesh_takes_most_from_the_nearest_point(fw):
    points = [(0.1, 0.2, *RED), (0.9, 0.7, *BLUE)]
    r, _, b = mesh(fw, points, 0.1, 0.2)
    assert r > 3 * b
    r, _, b = mesh(fw, points, 0.75, 0.6)
    assert b > 3 * r


def test_a_mesh_weighs_u_and_v_alike_at_aspect_one(fw):
    """Distance is round: the same points turned across the diagonal blend the same at the turned place."""
    points = [(0.1, 0.5, *RED), (0.9, 0.5, *BLUE), (0.3, 0.9, 0.0, 1.0, 0.0)]
    turned = [(v, u, *c) for u, v, *c in points]
    for u, v in ((0.3, 0.5), (0.6, 0.2), (0.45, 0.8)):
        assert mesh(fw, points, u, v) == pytest.approx(mesh(fw, turned, v, u), abs=1e-6)


def test_a_mesh_stretches_u_by_the_aspect(fw):
    """u and v each run 0..1 across the picture: in a wide one a point beside is farther than one above at the same
    u, v distance, in a tall one nearer."""
    points = [(0.5, 0.0, *RED), (0.0, 0.5, *BLUE)]
    r, _, b = mesh(fw, points, 0.5, 0.5, 2.0)
    assert r > b
    r, _, b = mesh(fw, points, 0.5, 0.5, 0.5)
    assert b > r


def test_glow_is_a_soft_round_falloff_from_one_to_none_at_its_radius(fw):
    """Soft: it fades out flat at its radius, with no edge, each step toward it taking off less than the last."""
    glow = fw.fn('lockscreen__glow', C.c_float, C.c_float, C.c_float, C.c_float)
    for r in (0.55, 1.25):
        assert glow(0, 0, r) == 1
        d = 0.6 * r
        assert glow(d, 0, r) == pytest.approx(glow(0, -d, r)) == pytest.approx(glow(0.6 * d, 0.8 * d, r))
        out = [glow(r * i / 10, 0, r) for i in range(11)]
        assert all(a > b for a, b in zip(out, out[1:]))
        assert out[-1] == 0 and glow(1.5 * r, 0, r) == 0 and glow(0, -3 * r, r) == 0
        drops = [a - b for a, b in zip(out[6:], out[7:])]
        assert all(a > b for a, b in zip(drops, drops[1:])), drops
    assert glow(0.3, 0.2, 1.0) == pytest.approx(glow(0.6, 0.4, 2.0))  # it scales with its radius


@pytest.mark.parametrize('t', [0, 0.25, 1])
def test_blend_moves_each_channel_toward_the_colour_by_t(fw, t):
    rgb = block(fw, size=3 * 4)
    C.memmove(rgb, (C.c_float * 3)(0.2, 0.4, 0.8), 3 * 4)
    fw.fn('lockscreen__blend', None, C.c_void_p, C.c_float, C.c_float, C.c_float, C.c_float)(rgb, 1.0, 0.0, 0.5, t)
    assert list((C.c_float * 3).from_address(rgb)) == pytest.approx([0.2 + 0.8 * t, 0.4 - 0.4 * t, 0.8 - 0.3 * t])


@pytest.mark.parametrize('x, clamped', [(-0.5, 0), (0, 0), (0.3, 0.3), (0.999, 0.999), (1, 1), (1.5, 1), (40, 1)])
def test_clamp01_keeps_0_to_1(fw, x, clamped):
    assert fw.fn('lockscreen__clamp01', C.c_float, C.c_float)(x) == pytest.approx(clamped)


def field(fw, wall, u, v, aspect=375 / 320):
    out = block(fw, size=3 * 4)
    fw.fn('lockscreen__wall_field', None, C.c_int, C.c_float, C.c_float, C.c_float, C.c_void_p)(wall, u, v, aspect, out)
    return list((C.c_float * 3).from_address(out))


def test_the_vignette_darkens_tide_s_flat_sky_toward_its_edges(fw):
    """Tide's navy above its waves is one colour across: there only the vignette shows, brightest in the middle and
    darker toward either edge alike."""
    blue = [field(fw, TIDE, i / 20, 0.1)[2] for i in range(21)]
    assert all(a < b for a, b in zip(blue[:10], blue[1:11])) and all(a > b for a, b in zip(blue[10:], blue[11:]))
    assert blue == pytest.approx(blue[::-1])
    assert blue[0] < 0.9 * blue[10]


@pytest.mark.parametrize('wall', [AURORA, SUNRISE, TIDE, EMBER])
def test_the_5_px_grid_never_shows(fw, wall):
    """The field is taken every 5 pixels and blended between: across a cell's edge the colour steps no more than
    within a cell, along a row or down a column."""
    _, rows = render(fw, wall, 180, 150)
    for pairs in (((row[x], row[x - 1], x) for row in rows for x in range(1, 180)),
                  ((rows[y][x], rows[y - 1][x], y) for y in range(1, 150) for x in range(180))):
        across, within = [], []
        for a, b, at in pairs:
            (across if at % 5 == 0 else within).append(sum(abs(p - q) for p, q in zip(rgb(a), rgb(b))))
        assert statistics.fmean(across) <= 1.25 * statistics.fmean(within) + 0.5


def test_the_dither_is_a_4x4_ordered_dither(fw):
    """A level's fraction is spread over each 4x4 tile in the Bayer order: the pixel at matrix value m adds
    (m + 0.5) / 16 and drops the rest, so it rounds up once the fraction passes 1 - (m + 0.5) / 16, the higher m
    the sooner. Graphite's grain makes the fractions even, so each place averages m / 16 of a level more than the
    place of value 0 would."""
    sums = [[0] * 4 for _ in range(4)]
    for y, row in enumerate(graphite(fw, 376, 640)):
        for x, level in enumerate(row):
            sums[y & 3][x & 3] += level
    means = [[s / (376 * 640 // 16) for s in row] for row in sums]
    # a row's grain shade is the same along it, so each of the four rows of the tile is taken from its own mean
    off = [[means[j][i] - statistics.fmean(means[j]) for i in range(4)] for j in range(4)]
    want = [[(m - statistics.fmean(row)) / 16 for m in row] for row in BAYER]
    # the grain's runs share a fraction across pixels, so the fractions are only nearly even: within 1/24 of a level
    assert all(off[j][i] == pytest.approx(want[j][i], abs=1 / 24) for j in range(4) for i in range(4)), off


def graphite(fw, w=375, h=320):
    """Graphite's rows, red only: green is the same and blue a touch above."""
    return [[px & 255 for px in row] for row in render(fw, GRAPHITE, w, h)[1]]


def test_graphite_is_dark_metal_never_black(fw):
    red = graphite(fw)
    assert 0.02 * 255 < min(map(min, red)) and max(map(max, red)) < 0.5 * 255


def test_graphite_s_sheen_runs_diagonally_down_to_the_left(fw):
    """One soft diagonal sheen: down the picture its brightest place moves steadily left, in a straight line."""
    red = graphite(fw)

    def brightest(rows):
        columns = [sum(row[x] for row in rows) for x in range(375)]
        smooth = [sum(columns[max(0, x - 12):x + 13]) / len(columns[max(0, x - 12):x + 13]) for x in range(375)]
        return max(range(375), key=smooth.__getitem__)
    top, middle, bottom = brightest(red[:20]), brightest(red[150:170]), brightest(red[-20:])
    assert top - middle > 50 and middle - bottom > 50, (top, middle, bottom)
    assert abs(middle - (top + bottom) / 2) < 30, (top, middle, bottom)


def test_ember_is_its_own_mirror_image(fw):
    """Ember's glows rise from the middle of the bottom edge and the vignette is centred across: each column
    matches its mirror but for the dither's level, the first column the field's left edge and the last its right.
    (At 111 wide every fifth column is a sample of the field, the mirrored ones among them.)"""
    _, rows = render(fw, EMBER, 111, 94)
    for y, row in enumerate(rows):
        for x in range(55):
            assert all(abs(a - b) <= 1 for a, b in zip(rgb(row[x]), rgb(row[110 - x]))), (x, y)


@pytest.mark.parametrize('wall', [AURORA, SUNRISE])
def test_the_right_edge_carries_on_the_picture_at_any_width(fw, wall):
    """At a width that isn't a multiple of 5 the last columns lie past the last whole cell, blending on toward the
    field just beyond the edge: over the rows, their step is the step of the columns before them."""
    w = 174
    _, rows = render(fw, wall, w, 94)
    for k in range(3):
        def step(x):
            return statistics.fmean((row[x] >> 8 * k & 255) - (row[x - 1] >> 8 * k & 255) for row in rows)
        assert abs(step(w - 1) - step(w - 6)) < 1, (k, step(w - 1), step(w - 6))


class Mallinfo2(C.Structure):
    _fields_ = [(name, C.c_size_t) for name in
                'arena ordblks smblks hblks hblkhd usmblks fsmblks uordblks fordblks keepcost'.split()]


def test_a_render_frees_its_grid(fw):
    """wall_render's grid (the field every 5 pixels, a float a channel) is its own: none of it stays allocated,
    however many pictures and thumbnails are drawn."""
    libc = C.CDLL(None)
    libc.mallinfo2.restype = Mallinfo2
    held = lambda: libc.mallinfo2().uordblks + libc.mallinfo2().hblkhd  # noqa: E731
    run = fw.fn('wall_render', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)
    dst = block(fw, size=375 * 320 * 4)
    assert run(dst, 375, 375, 320, AURORA) == 1
    before = held()
    for wall in range(6):
        assert run(dst, 375, 375, 320, wall) == 1
    assert held() - before < (375 // 5 + 2) * (320 // 5 + 2) * 3 * 4


def test_graphite_s_grain_shades_each_row_its_own(fw):
    """Each row's grain is a shade of its own: a row's mean stands off its neighbours' average by a few levels,
    where the smooth field alone would leave a fraction of one."""
    means = [statistics.fmean(row) for row in graphite(fw)]
    off = [means[y] - (means[y - 1] + means[y + 1]) / 2 for y in range(1, len(means) - 1)]
    assert math.sqrt(statistics.fmean(d * d for d in off)) > 1.5


def test_graphite_s_grain_drifts_in_runs_each_row_its_own_way(fw):
    """Along a row the grain holds for a run of pixels, then steps: the steps make columns where neighbours differ
    more than elsewhere, evenly spaced, and a step is different in each row."""
    red = graphite(fw)
    steps = [[row[x] - row[x - 1] for row in red] for x in range(1, 375)]  # steps[x - 1]: column x's
    size = [statistics.fmean(s * s for s in column) for column in steps]
    typical = statistics.median(size)
    edges = [x + 1 for x, s in enumerate(size) if s > 2 * typical]
    gaps = {b - a for a, b in zip(edges, edges[1:])}
    assert len(edges) >= 8 and len(gaps) == 1 and min(gaps) >= 8, edges
    assert min(statistics.pstdev(steps[x - 1]) for x in edges) > 1, [statistics.pstdev(steps[x - 1]) for x in edges]


# Their names.

@pytest.mark.parametrize('name', WALLS)
def test_a_built_in_s_name_passes_stock_s_file_check_without_one(fw, name):
    fw.mock('fw_access', lambda *_: -1)
    assert fw.fn('lockscreen_access', C.c_int, C.c_char_p, C.c_int)(name, 4) == 0
    assert fw.calls('fw_access') == []


@pytest.mark.parametrize('name, there', [(b'/mnt/mmc/lockscreenimage/a.jpg', 0), (b'/mnt/mmc/gone.jpg', -1),
                                         (b'q2pod:invalid', -1), (b'q2pod:aurora ', -1), (b'Q2POD:AURORA', -1)])
def test_any_other_name_is_access_s_answer(fw, name, there):
    fw.mock('fw_access', lambda *_: there)
    assert fw.fn('lockscreen_access', C.c_int, C.c_char_p, C.c_int)(name, 4) == there
    (path, mode, *_), = [args for _, args in fw.calls('fw_access')]
    assert C.string_at(path) == name and mode == 4


def test_no_name_is_no_built_in(fw):
    """A missing name is no built-in: it is access's to answer."""
    fw.mock('fw_access', lambda *_: -1)
    assert fw.fn('lockscreen_access', C.c_int, C.c_char_p, C.c_int)(None, 4) == -1
    assert [args[:2] for _, args in fw.calls('fw_access')] == [(0, 4)]


# The picture list.

class Pictures:
    """p_deque_screensaverlist, a libcstl char* deque: stock's get_screensaver_image fills it (fill), and the
    payload's pushes copy the string as libcstl's do. A popped string is freed, so it's overwritten."""
    HANDLE = 0x5150

    def __init__(self, fw, names, picture=b'screensaver_default', stock=None):
        self.names, self.held = list(names), []
        self.picture = fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256)
        self.picture.value = picture
        fw.var('fw_ctx_p_deque_screensaverlist', C.c_void_p).value = self.HANDLE
        fw.mock('stock_saverlist_trampoline', lambda *_: (stock or (lambda: None))() or 77)
        fw.mock('fw_deque_size', lambda q, *_: self.check(q) and len(self.names))
        fw.mock('fw_deque_at', lambda q, i, *_: self.check(q) and self.at(i))
        fw.mock('fw_deque_pop_back', lambda q, *_: self.check(q) and self.pop())
        fw.mock('fw__deque_push_back', lambda q, s, *_: self.check(q) and self.names.append(C.string_at(s)))
        self.run = fw.fn('lockscreen_images', C.c_int)

    def check(self, q):
        assert q == self.HANDLE
        return 1

    def at(self, i):
        self.held.append(C.create_string_buffer(self.names[i]))
        return C.addressof(self.held[-1])

    def pop(self):
        gone = self.names.pop()
        for s in self.held:
            if s.value == gone:
                C.memset(s, ord('X'), len(gone))


def test_the_built_ins_go_after_the_default_and_before_the_card_images(fw):
    cards = [b'/mnt/mmc/lockscreenimage/%d.jpg' % i for i in range(3)]
    pics = Pictures(fw, [b'screensaver_default', *cards])
    assert pics.run() == 77
    assert pics.names == [b'screensaver_default', *WALLS, *cards]


def test_five_card_images_all_stay_in_order(fw):
    cards = [b'/mnt/mmc/lockscreenimage/' + bytes([97 + i]) * (i * 40 + 1) + b'.png' for i in range(5)]
    pics = Pictures(fw, [b'screensaver_default', *cards])
    pics.run()
    assert pics.names == [b'screensaver_default', *WALLS, *cards]


def test_with_no_card_images_the_built_ins_follow_the_default(fw):
    pics = Pictures(fw, [b'screensaver_default'])
    pics.run()
    assert pics.names == [b'screensaver_default', *WALLS]


def test_a_rebuilt_list_never_doubles_them(fw):
    cards = [b'/mnt/mmc/lockscreenimage/a.jpg', b'/mnt/mmc/lockscreenimage/b.jpg']
    pics = Pictures(fw, [b'screensaver_default', *cards])
    pics.run()
    pics.run()  # stock left the list as it was
    assert pics.names == [b'screensaver_default', *WALLS, *cards]


def test_an_empty_list_stays_empty(fw):
    pics = Pictures(fw, [])
    assert pics.run() == 77 and pics.names == []


def test_no_list_is_left_alone(fw):
    fw.var('fw_ctx_p_deque_screensaverlist', C.c_void_p).value = 0
    fw.mock('stock_saverlist_trampoline', lambda *_: 5)
    assert fw.fn('lockscreen_images', C.c_int)() == 5
    assert not [name for name, _ in fw.calls() if 'deque' in name]


@pytest.mark.parametrize('picture', WALLS)
def test_a_built_in_stock_reset_is_put_back(fw, picture):
    def stock():
        pics.picture.value = b'screensaver_default'  # neither the default nor a file
    pics = Pictures(fw, [b'screensaver_default'], picture=picture, stock=stock)
    pics.run()
    assert pics.picture.value == picture


@pytest.mark.parametrize('picture', [b'/mnt/mmc/lockscreenimage/gone.jpg', b'q2pod:invalid'])
def test_any_other_picture_stock_reset_stays_reset(fw, picture):
    def stock():
        pics.picture.value = b'screensaver_default'
    pics = Pictures(fw, [b'screensaver_default'], picture=picture, stock=stock)
    pics.run()
    assert pics.picture.value == b'screensaver_default'


@pytest.mark.parametrize('picture', [b'/mnt/mmc/lockscreenimage/a.jpg', WALLS[EMBER]])
def test_a_picture_stock_kept_is_kept(fw, picture):
    pics = Pictures(fw, [b'screensaver_default', b'/mnt/mmc/lockscreenimage/a.jpg'], picture=picture)
    pics.run()
    assert pics.picture.value == picture


def test_a_card_image_s_name_as_long_as_the_picture_holds_comes_back_whole(fw):
    """g_screensaver_picture holds 255 characters and its nul: a card image named that long can be picked, so it
    goes back on the list whole."""
    long = b'/mnt/mmc/lockscreenimage/' + b'x' * (255 - 29) + b'.jpg'
    assert len(long) == 255
    pics = Pictures(fw, [b'screensaver_default', long, b'/mnt/mmc/lockscreenimage/b.jpg'])
    pics.run()
    assert pics.names == [b'screensaver_default', *WALLS, long, b'/mnt/mmc/lockscreenimage/b.jpg']


def test_a_list_past_stock_s_five_card_images_keeps_five_of_them(fw):
    """Stock lists five card images at most, and five is all there's room to keep: given more, the default and the
    built-ins still lead, then five of the cards in their order, no more."""
    cards = [b'/mnt/mmc/lockscreenimage/%d.jpg' % i for i in range(7)]
    pics = Pictures(fw, [b'screensaver_default', *cards])
    pics.run()
    assert pics.names[:7] == [b'screensaver_default', *WALLS]
    kept = pics.names[7:]
    assert len(kept) == 5 and kept == [card for card in cards if card in kept]


@pytest.mark.parametrize('picture', [WALLS[AURORA], WALLS[BLACK]])
def test_a_built_in_stock_reset_is_put_back_beside_five_card_images(fw, picture):
    def stock():
        pics.picture.value = b'screensaver_default'
    cards = [b'/mnt/mmc/lockscreenimage/' + bytes([97 + i]) * 200 + b'.jpg' for i in range(5)]
    pics = Pictures(fw, [b'screensaver_default', *cards], picture=picture, stock=stock)
    pics.run()
    assert pics.picture.value == picture and pics.names == [b'screensaver_default', *WALLS, *cards]


# The lock window and the picker.

class Config:
    """config.ini, its value or the default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v.encode() for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


class Window:
    """Widgets by name, each a zeroed block with its box's width and height; widget_lookup finds them. The
    properties set on them are read as they're set: a style key can be on the caller's stack."""
    def __init__(self, fw, names, size=(375, 320)):
        self.fw, self.widgets, self.blocks, self.set = fw, {}, [], []
        for name in names:
            self.add(name, *size)
        fw.mock('widget_lookup', lambda win, name, *_: self.widgets.get(C.string_at(name).decode(), 0))
        fw.mock('widget_set_prop_int', lambda w, key, value, *_: self.set.append(
            ('widget_set_prop_int', w, C.string_at(key).decode(), C.c_int(value).value)))
        fw.mock('widget_set_prop_str', lambda w, key, value, *_: self.set.append(
            ('widget_set_prop_str', w, C.string_at(key).decode(), C.string_at(value).decode())))

    def add(self, name, w=375, h=320):
        self.blocks.append(C.create_string_buffer(0x400))
        struct.pack_into('<ii', self.blocks[-1], W_W, w, h)
        self.widgets[name] = C.addressof(self.blocks[-1])
        return self.widgets[name]

    def __getitem__(self, name):
        return self.widgets[name]

    def calls(self, stub):
        """stub's calls as (widget's name, its other arguments)."""
        names = {v: k for k, v in self.widgets.items()}
        return [(names.get(args[0], args[0]), args[1:]) for _, args in self.fw.calls(stub)]

    def props(self, stub, name):
        """{property: value} that stub set on widget name, a str for widget_set_prop_str."""
        return {key: value for kind, w, key, value in self.set if kind == stub and w == self.widgets.get(name)}


class Bitmaps:
    """bitmap_create_ex's bitmaps: a header (flags at 0xc, stock's own flags given) and pixels of their line length
    exactly."""
    def __init__(self, fw, pad=0, create=True, lock=True, flags=0):
        self.fw, self.pad, self.create_ok, self.lock_ok, self.flags = fw, pad, create, lock, flags
        self.made, self.destroyed, self.unlocked = {}, [], []
        fw.mock('bitmap_create_ex', self.create)
        fw.mock('bitmap_lock_buffer_for_write', lambda bm, *_: self.made[bm][3] if self.lock_ok else 0)
        fw.mock('bitmap_get_line_length', lambda bm, *_: self.made[bm][2])
        fw.mock('bitmap_unlock_buffer', lambda bm, *_: self.unlocked.append(bm))
        fw.mock('bitmap_destroy', lambda bm, *_: self.destroyed.append(bm))

    def create(self, w, h, line, fmt, *_):
        if not self.create_ok:
            return 0
        assert line == w * 4 and fmt == 1  # RGBA8888
        line += self.pad * 4
        bm = block(self.fw, size=0x40)
        C.c_ushort.from_address(bm + 0xc).value = self.flags
        self.made[bm] = (w, h, line, block(self.fw, size=line * h))
        return bm

    def pixels(self, bm):
        w, h, line, data = self.made[bm]
        flat = (C.c_uint * (line // 4 * h)).from_address(data)
        return [list(flat[y * line // 4:y * line // 4 + w]) for y in range(h)]

    def opaque(self, bm):
        return C.c_ushort.from_address(bm + 0xc).value & 1

    def header_flags(self, bm):
        return C.c_ushort.from_address(bm + 0xc).value


def handlers(fw, event):
    """widget_on's handlers for event: [(widget, call)], call(ctx) running the handler."""
    out = []
    for _, (w, evt, fn, ctx, *_) in fw.calls('widget_on'):
        if evt == event:
            out.append((w, lambda c=ctx, f=fn: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, 0)))
    return out


def draws(fw):
    """canvas_draw_image's calls: (bitmap, src rect, dst rect), read while the rects lived."""
    seen = []
    fw.mock('canvas_draw_image', lambda canvas, bm, src, dst, *_: seen.append(
        (bm, tuple((C.c_int * 4).from_address(src)), tuple((C.c_int * 4).from_address(dst)))))
    return seen


def lock_window(fw, picture, size=(375, 320), names=('img_screensaver',), **config):
    Config(fw, **config)
    fw.var('fw_ctx_g_screensaver_picture', C.c_char * 256).value = picture
    fw.mock('stock_saver_trampoline', lambda *_: 31)
    win = Window(fw, names, size)
    assert fw.fn('lockscreen_init', C.c_int, C.c_void_p, C.c_void_p)(0x7700, 0x7800) == 31
    return win


def paint(fw, w):
    fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(w, 0x6600)


@pytest.mark.parametrize('wall', range(5))
def test_a_built_in_is_rendered_at_the_picture_s_size_and_painted_over_it(fw, wall):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    win = lock_window(fw, WALLS[wall], size=(120, 90))
    img = win['img_screensaver']
    (name, (empty, *_)), = win.calls('image_base_set_image')
    assert name == 'img_screensaver' and C.string_at(empty) == b''  # nothing to look up on each paint
    bm, = bitmaps.made
    assert bitmaps.made[bm][:2] == (120, 90) and bitmaps.opaque(bm) and bitmaps.unlocked == [bm]
    assert bitmaps.pixels(bm) == [row[:120] for row in render(fw, wall, 120, 90)[1]]
    paint(fw, img)
    assert seen == [(bm, (0, 0, 120, 90), (0, 0, 120, 90))]
    assert [args[:2] for _, args in fw.calls('widget_on_paint_children')] == [(img, 0x6600)]  # its children on top


def test_the_wallpaper_follows_the_bitmap_s_line_length(fw):
    bitmaps = Bitmaps(fw, pad=3)
    lock_window(fw, WALLS[TIDE], size=(40, 30))
    bm, = bitmaps.made
    assert bitmaps.pixels(bm) == render(fw, TIDE, 40, 30)[1]


def test_black_needs_no_bitmap_the_window_is_black(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    win = lock_window(fw, WALLS[BLACK])
    paint(fw, win['img_screensaver'])
    assert bitmaps.made == {} and seen == [] and fw.calls('widget_on_paint_children') == []
    assert lk(fw).built_in == 1


def test_a_card_picture_is_stock_s_to_paint(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    win = lock_window(fw, b'/mnt/mmc/lockscreenimage/a.jpg')
    paint(fw, win['img_screensaver'])
    assert bitmaps.made == {} and seen == [] and fw.calls('image_base_set_image') == []


def test_one_render_serves_every_lock_window_of_that_picture_and_size(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    lock_window(fw, WALLS[AURORA], size=(60, 50))
    bm, = bitmaps.made
    win = lock_window(fw, WALLS[AURORA], size=(60, 50))  # the next screen-on: a new window and widget
    paint(fw, win['img_screensaver'])
    assert list(bitmaps.made) == [bm] and seen == [(bm, (0, 0, 60, 50), (0, 0, 60, 50))]


@pytest.mark.parametrize('picture, size', [(WALLS[SUNRISE], (60, 50)), (WALLS[AURORA], (61, 50)), (WALLS[AURORA], (60, 51))])
def test_a_new_picture_or_size_renders_again_and_frees_the_old(fw, picture, size):
    bitmaps = Bitmaps(fw)
    lock_window(fw, WALLS[AURORA], size=(60, 50))
    old, = bitmaps.made
    lock_window(fw, picture, size=size)
    assert len(bitmaps.made) == 2 and bitmaps.destroyed == [old]


def test_black_after_a_wallpaper_frees_it(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    lock_window(fw, WALLS[EMBER], size=(30, 20))
    old, = bitmaps.made
    win = lock_window(fw, WALLS[BLACK], size=(30, 20))
    paint(fw, win['img_screensaver'])
    assert bitmaps.destroyed == [old] and seen == []


def test_a_bitmap_that_can_t_be_made_paints_nothing(fw):
    Bitmaps(fw, create=False)
    seen = draws(fw)
    win = lock_window(fw, WALLS[AURORA], size=(30, 20))
    paint(fw, win['img_screensaver'])
    assert seen == [] and lk(fw).full is None


def test_a_bitmap_that_can_t_be_locked_goes(fw):
    bitmaps, seen = Bitmaps(fw, lock=False), draws(fw)
    win = lock_window(fw, WALLS[AURORA], size=(30, 20))
    paint(fw, win['img_screensaver'])
    bm, = bitmaps.made
    assert bitmaps.destroyed == [bm] and bitmaps.unlocked == [] and seen == []


def test_the_picture_s_end_forgets_it_and_keeps_the_wallpaper(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    win = lock_window(fw, WALLS[AURORA], size=(30, 20))
    img = win['img_screensaver']
    (w, forget), = handlers(fw, EVT_DESTROY)
    assert w == img
    assert forget() == 0
    paint(fw, img)  # a new widget at the same address is no lock picture
    assert seen == [] and bitmaps.destroyed == [] and lk(fw).img is None
    lock_window(fw, WALLS[AURORA], size=(30, 20))
    assert len(bitmaps.made) == 1


def test_an_old_picture_s_end_forgets_nothing(fw):
    Bitmaps(fw)
    seen = draws(fw)
    lock_window(fw, WALLS[AURORA], size=(30, 20))
    win = lock_window(fw, WALLS[AURORA], size=(30, 20))
    first, second = handlers(fw, EVT_DESTROY)
    first[1]()  # the first window's widget goes after the second came
    paint(fw, win['img_screensaver'])
    assert len(seen) == 1


def test_paint_of_no_widget_or_another_does_nothing(fw):
    Bitmaps(fw)
    seen = draws(fw)
    win = lock_window(fw, WALLS[AURORA], size=(30, 20))
    paint(fw, 0)
    paint(fw, win.add('label', 30, 20))
    assert seen == []


def test_the_cover_repaints_after_stock_reloads_it(fw):
    win = Window(fw, ['img_playcover'])
    order = []
    fw.mock('stock_savercover_trampoline', lambda w, *_: order.append(('stock', w)) or 12)
    fw.mock('widget_invalidate_force', lambda w, *_: order.append(('invalidate', w)))
    assert fw.fn('lockscreen_cover', C.c_int, C.c_void_p)(0x7700) == 12
    assert order == [('stock', 0x7700), ('invalidate', win['img_playcover'])]


def test_no_cover_widget_no_repaint(fw):
    Window(fw, [])
    fw.mock('stock_savercover_trampoline', lambda *_: 3)
    assert fw.fn('lockscreen_cover', C.c_int, C.c_void_p)(0x7700) == 3
    assert fw.calls('widget_invalidate_force') == []


def test_a_render_that_failed_is_tried_again_at_the_next_lock_window(fw):
    """Out of memory once leaves that lock window stock's, not every one after it."""
    bitmaps = Bitmaps(fw, create=False)
    lock_window(fw, WALLS[AURORA], size=(30, 20))
    bitmaps.create_ok = True
    win = lock_window(fw, WALLS[AURORA], size=(30, 20))
    seen = draws(fw)
    paint(fw, win['img_screensaver'])
    bm, = bitmaps.made
    assert seen == [(bm, (0, 0, 30, 20), (0, 0, 30, 20))]


def test_a_window_without_its_picture_widget_leaves_a_built_in_alone(fw):
    bitmaps = Bitmaps(fw)
    lock_window(fw, WALLS[AURORA], names=('label_battery',))
    assert bitmaps.made == {} and lk(fw).img is None and fw.calls('widget_on') == []


def test_the_wallpaper_marks_its_bitmap_opaque_keeping_stock_s_flags(fw):
    bitmaps = Bitmaps(fw, flags=0x8)  # BITMAP_FLAG_CHANGED, say
    lock_window(fw, WALLS[SUNRISE], size=(30, 20))
    bm, = bitmaps.made
    assert bitmaps.header_flags(bm) == 0x8 | 1


@pytest.mark.parametrize('first, then', [((AURORA, (60, 50)), (AURORA, (50, 60))),
                                         ((AURORA, (60, 51)), (SUNRISE, (60, 50))),
                                         ((TIDE, (375, 320)), (EMBER, (375, 320)))])
def test_each_picture_and_size_is_a_render_of_its_own(fw, first, then):
    """A picture turned on its side, or another picture whose size is a row off, is never the old render."""
    bitmaps = Bitmaps(fw)
    lock_window(fw, WALLS[first[0]], size=first[1])
    old, = bitmaps.made
    win = lock_window(fw, WALLS[then[0]], size=then[1])
    new, = set(bitmaps.made) - {old}
    assert bitmaps.made[new][:2] == then[1] and bitmaps.destroyed == [old]
    seen = draws(fw)
    paint(fw, win['img_screensaver'])
    assert [bm for bm, _, _ in seen] == [new]


@pytest.mark.parametrize('flow', ['info', 'large', 'rows', 'cover'])
def test_every_widget_is_found_however_deep_it_sits(fw, clock, flow):
    """Stock's windows nest their widgets (the clocks in view_timeinfo, the song's in view_playinfo, the
    thumbnails in scroll_view's rows): every lookup searches the whole tree."""
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    if flow == 'info':
        lock_window(fw, WALLS[TIDE], names=INFO, THEME='1')
    elif flow == 'large':
        lock_window(fw, WALLS[TIDE], names=CLOCK, LOCKCLOCK='1')
    elif flow == 'rows':
        Picker(fw, [b'screensaver_default', *WALLS])
    else:
        Window(fw, ['img_playcover'])
        fw.mock('stock_savercover_trampoline', lambda *_: 0)
        fw.fn('lockscreen_cover', C.c_int, C.c_void_p)(0x7700)
    lookups = fw.calls('widget_lookup')
    assert lookups and [args[2] for _, args in lookups] == [1] * len(lookups)


# The picker's thumbnails (lockscreen_rows).

class Picker:
    """The Lock screen page: stock's rows in scroll_view (Time show, About), the list (none unless listed), and
    the grid's image_%u thumbnails, 110 x 94, which tk_snprintf names as vsnprintf would."""
    def __init__(self, fw, names, children=3, listed=True, **config):
        Config(fw, **config)
        self.pics = Pictures(fw, names)
        if not listed:
            fw.var('fw_ctx_p_deque_screensaverlist', C.c_void_p).value = 0
        self.win = Window(fw, ['scroll_view'])
        self.thumbs = [self.win.add('image_%d' % i, 110, 94) for i in range(len(names))]
        self.rows = [self.win.add('row%d' % i, 375, 60) for i in range(children)]
        # list_row's label sits in its button in the row: switch_row restacks the row
        self.parent = self.win.add('button')
        label = self.win.add('label')
        struct.pack_into('<Q', self.win.blocks[-1], W_PARENT, self.parent)
        fw.mock('hscroll_label_create', lambda *_: label)
        fw.mock('widget_count_children', lambda *_: len(self.rows))
        fw.mock('widget_get_child', lambda view, i, *_: self.rows[i])
        fw.mock('stock_saverrows_trampoline', lambda *_: 44)
        assert fw.fn('lockscreen_rows', C.c_int, C.c_void_p)(0x7700) == 44


def test_the_clock_row_follows_time_show_78_high(fw):
    picker = Picker(fw, [b'screensaver_default'])
    last = picker.rows[-1]
    assert (last, 375, 78) in [args[:3] for _, args in fw.calls('widget_resize')]  # its width kept
    assert (last, 1) in [args[:2] for _, args in fw.calls('widget_restack')]


def test_a_page_with_its_switch_off_gets_no_rows(fw):
    Picker(fw, [b'screensaver_default'], children=1)
    assert fw.calls('widget_resize') == [] and fw.calls('image_base_set_image') == []


def test_the_built_ins_thumbnails_paint_their_wallpaper_once_at_their_size(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    picker = Picker(fw, [b'screensaver_default', *WALLS, b'/mnt/mmc/lockscreenimage/a.jpg'])
    cleared = [w for w, _ in picker.win.calls('image_base_set_image')]
    assert cleared == ['image_%d' % i for i in range(1, 7)]  # not the default's or the card's
    for i, thumb in enumerate(picker.thumbs):
        paint(fw, thumb)
        paint(fw, thumb)
    assert len(bitmaps.made) == 6 and all(size[:2] == (110, 94) for size in bitmaps.made.values())
    assert seen == [(bm, (0, 0, 110, 94), (0, 0, 110, 94)) for bm in bitmaps.made for _ in range(2)]
    for bm, wall in zip(bitmaps.made, range(6)):
        assert bitmaps.pixels(bm) == render(fw, wall, 110, 94)[1]


@pytest.mark.parametrize('wall', [AURORA, TIDE])
def test_a_thumbnail_s_end_frees_its_bitmap_and_forgets_it(fw, wall):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    picker = Picker(fw, [b'screensaver_default', WALLS[wall]])
    paint(fw, picker.thumbs[1])
    bm, = bitmaps.made
    (w, forget), = handlers(fw, EVT_DESTROY)
    assert w == picker.thumbs[1]
    forget()
    assert bitmaps.destroyed == [bm]
    paint(fw, picker.thumbs[1])
    assert len(seen) == 1


def test_a_thumbnail_never_seen_goes_with_nothing_to_free(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    picker = Picker(fw, [b'screensaver_default', WALLS[TIDE], WALLS[EMBER]])
    paint(fw, picker.thumbs[2])
    (_, tide), (_, ember) = handlers(fw, EVT_DESTROY)
    tide()
    assert bitmaps.destroyed == []
    paint(fw, picker.thumbs[1])
    paint(fw, picker.thumbs[2])
    assert len(seen) == 2 and len(bitmaps.made) == 1  # Tide's is forgotten, Ember's stays


def test_a_rebuilt_grid_keeps_the_rendered_thumbnails(fw):
    bitmaps = Bitmaps(fw)
    picker = Picker(fw, [b'screensaver_default', WALLS[TIDE]])
    paint(fw, picker.thumbs[1])
    fw.fn('lockscreen_rows', C.c_int, C.c_void_p)(0x7700)  # the switch turned: same widgets, rebuilt
    paint(fw, picker.thumbs[1])
    assert len(bitmaps.made) == 1


@pytest.mark.parametrize('large', [0, 1])
def test_the_clock_row_is_the_lock_clock_s_setting(fw, large):
    """A click on the row it adds steps Q2POD LOCKCLOCK, Standard and Large in turn."""
    Picker(fw, [b'screensaver_default'], LOCKCLOCK=str(large))
    for _, click in handlers(fw, EVT_CLICK):  # with no toolbar, the page's switch row's too
        click()
    (value, section, key, *_), = [args for _, args in fw.calls('write_int_config')]
    assert (value, C.string_at(section), C.string_at(key)) == (1 - large, b'Q2POD', b'LOCKCLOCK')


def test_time_show_and_about_alone_still_get_the_clock_row(fw):
    """With the switch off stock's page has its two rows, Time show and About: the Clock row follows Time show."""
    picker = Picker(fw, [b'screensaver_default'], children=2)
    assert (picker.rows[-1], 375, 78) in [args[:3] for _, args in fw.calls('widget_resize')]


@pytest.mark.parametrize('toolbar', [0, 1])
def test_a_set_without_a_toolbar_gets_the_page_s_switch_as_a_row(fw, st, toolbar):
    """Under a geometry set with no toolbar, stock's switch moves to a row of its own (switch_row); with one, it
    stays where stock put it."""
    v_n, at = len(tools_ipod.GEOM_VALS), tools_ipod.GEOM_VALS.index('toolbar')  # themes.inc's geom_vals order
    table = (C.c_short * (fw.sizes['theme_engine__geom_vals'] // 2)).in_dll(fw.lib, 'theme_engine__geom_vals')
    st('geom').value = next(g for g in range(len(table) // v_n) if bool(table[g * v_n + at]) == bool(toolbar))
    Picker(fw, [b'screensaver_default'])
    names = [C.string_at(args[1]) for _, args in fw.calls('widget_set_name')]
    assert names.count(b'_switch_row') == 1 - toolbar


def test_a_page_with_no_list_looks_for_no_thumbnails(fw):
    Bitmaps(fw)
    picker = Picker(fw, [b'screensaver_default', *WALLS], listed=False)
    assert not [name for name, _ in fw.calls() if 'deque' in name]
    assert picker.win.calls('image_base_set_image') == [] and handlers(fw, EVT_DESTROY) == []
    assert (picker.rows[-1], 375, 78) in [args[:3] for _, args in fw.calls('widget_resize')]  # the row still comes


# The Large clock (Q2POD LOCKCLOCK 1), outside Classic.

CLOCK = ['img_screensaver', 'view_timeinfo', 'clock_time', 'clock_date', 'label_battery', 'img_battery']


def boxes(win):
    return {name: args[:4] for name, args in win.calls('widget_move_resize')}


@pytest.mark.parametrize('picture', [WALLS[AURORA], b'screensaver_default'])
@pytest.mark.parametrize('battery, shown', [('0', 0), ('1', 1)])
def test_large_lays_the_clock_out_above_the_card(fw, picture, battery, shown):
    Bitmaps(fw)
    win = lock_window(fw, picture, names=CLOCK, LOCKCLOCK='1', BATTERY=battery)
    box = boxes(win)
    x, y, w, h = box['view_timeinfo']
    assert y == 14 and y + h <= 190  # a top line at y 14, all above the now-playing card
    assert box['clock_date'][0] == 0 and box['clock_date'][2] == 375 and box['clock_date'][1] == 0
    tx, ty, tw, th = box['clock_time']
    assert (tx, tw) == (0, 375) and ty > 0 and ty + th <= h  # across the screen, under the date
    px, py, pw, ph = box['label_battery']
    assert px > 375 // 2 and px + pw <= 375  # at the date line's right end
    assert win.props('widget_set_prop_int', 'clock_date') == {'style:normal:font_size': 22}
    assert win.props('widget_set_prop_int', 'clock_time') == {'style:normal:font_size': 116}
    for name in ('clock_date', 'clock_time'):
        assert win.props('widget_set_prop_str', name) == {'style:normal:text_align_h': 'center'}
    visible = {name: args[0] for name, args in win.calls('widget_set_visible')}
    assert visible == {'img_battery': 0, 'label_battery': shown}


def test_standard_leaves_stock_s_clock(fw):
    Bitmaps(fw)
    win = lock_window(fw, WALLS[AURORA], names=CLOCK, LOCKCLOCK='0')
    assert fw.calls('widget_move_resize') == [] and win.calls('widget_set_visible') == []


@pytest.mark.parametrize('missing', CLOCK[1:])
def test_large_with_a_widget_missing_moves_nothing(fw, missing):
    Bitmaps(fw)
    lock_window(fw, WALLS[AURORA], names=[n for n in CLOCK if n != missing], LOCKCLOCK='1')
    assert fw.calls('widget_move_resize') == [] and fw.calls('widget_set_prop_int') == []


def test_large_s_lines_share_the_box_across_the_screen(fw):
    """The box spans the screen; the date's box holds its 22 px and the percent sits on the date's line; the time
    runs from under the date to the box's foot."""
    Bitmaps(fw)
    win = lock_window(fw, b'screensaver_default', names=CLOCK, LOCKCLOCK='1', BATTERY='1')
    box = boxes(win)
    x, y, w, h = box['view_timeinfo']
    assert (x, w) == (0, 375)
    dx, dy, dw, dh = box['clock_date']
    assert dh >= win.props('widget_set_prop_int', 'clock_date')['style:normal:font_size']
    px, py, pw, ph = box['label_battery']
    assert py + ph / 2 == dy + dh / 2 and ph <= dh and pw >= 2 * ph  # wide enough for "100%"
    tx, ty, tw, th = box['clock_time']
    assert dy < ty and ty + th == h


# Classic's info screen (THEME 1).

INFO = CLOCK + ['pgbar_battery', 'view_shade', 'view_playinfo', 'img_playcover', 'img_slide', 'scrlabel_title',
                'scrlabel_artist']


@pytest.fixture
def clock(fw):
    """The time of day in minutes, None when the clock can't be read."""
    state = {'minutes': 7 * 60 + 5}
    def local(now, out, *_):
        C.c_int.from_address(out + 4).value = state['minutes'] % 60
        C.c_int.from_address(out + 8).value = state['minutes'] // 60
        return out
    fw.mock('fw_time', lambda *_: -1 if state['minutes'] is None else 1700000000)
    fw.mock('fw_localtime_r', local)
    return state


class Battery:
    """label_battery's text, a wide string."""
    def __init__(self, fw, text='88%'):
        self.set(text)
        fw.mock('widget_get_text', lambda *_: C.addressof(self.wide))

    def set(self, text):
        self.wide = (C.c_uint * (len(text) + 1))(*map(ord, text), 0)


def timers(fw):
    """timer_add's timers, numbered from 1: [(fn, ms)]."""
    added = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: added.append((fn, ms)) or len(added))
    return added


@pytest.mark.parametrize('picture, page', [(b'', 1), (b'screensaver_default', 1), (WALLS[TIDE], 0), (WALLS[AURORA], 0),
                                           (b'/mnt/mmc/lockscreenimage/a.jpg', 0)])
def test_classic_s_info_screen_dresses_stock_s_widgets(fw, clock, picture, page):
    """Over the page (no picture, or the default) or a picture: the bar's date, a tray with the clock, one
    with the song; stock's shade and battery icon and bar hidden."""
    Bitmaps(fw)
    Battery(fw)
    added = timers(fw)
    win = lock_window(fw, picture, names=INFO, THEME='1', BATTERY='1')
    state = lk(fw)
    assert state.info == 1 and state.page == page and state.img == win['img_screensaver']
    assert win.props('widget_set_prop_str', 'clock_date')['format'] == 'MMM D, Y'
    visible = {name: args[0] for name, args in win.calls('widget_set_visible')}
    assert visible == {'view_shade': 0, 'img_battery': 0, 'pgbar_battery': 0, 'label_battery': 1}
    cleared = [name for name, _ in win.calls('image_base_set_image')]
    assert cleared == ([] if picture.startswith(b'/') else ['img_screensaver'])  # a card's file is stock's
    assert [ms for _, ms in added] == [1000] and state.timer == 1  # a look each second
    assert len(handlers(fw, EVT_DESTROY)) == 1  # whichever picture: its end stops the timer


def test_classic_s_info_screen_with_the_icon_hides_the_percent(fw, clock):
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    win = lock_window(fw, WALLS[TIDE], names=INFO, THEME='1', BATTERY='0')
    assert {name: args[0] for name, args in win.calls('widget_set_visible')}['label_battery'] == 0


@pytest.mark.parametrize('large', [0, 1])
def test_classic_s_clock_box_holds_large_s_bigger_digits(fw, clock, large):
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    win = lock_window(fw, WALLS[TIDE], names=INFO, THEME='1', LOCKCLOCK=str(large))
    box = boxes(win)
    assert lk(fw).large == large
    info, time = box['view_timeinfo'], box['clock_time']
    assert time[1] + time[3] <= info[3]  # the time inside its box
    assert win.props('widget_set_prop_int', 'clock_time')['style:normal:font_size'] == (166 if large else 112)


def test_the_info_screen_s_end_stops_its_timer(fw, clock):
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    lock_window(fw, b'', names=INFO, THEME='1')
    (_, forget), = handlers(fw, EVT_DESTROY)
    forget()
    state = lk(fw)
    assert (state.img, state.info, state.timer, state.date, state.title) == (None, 0, 0, None, None)
    assert [args[0] for _, args in fw.calls('timer_remove')] == [1]


def info(fw, clock, battery='88%'):
    Bitmaps(fw)
    bat = Battery(fw, battery)
    added = timers(fw)
    lock_window(fw, WALLS[TIDE], names=INFO, THEME='1', BATTERY='1')
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    tick = C.CFUNCTYPE(C.c_int, C.c_void_p)(added[0][0])
    return bat, added, tick


def invalidated(fw):
    return [args[0] for _, args in fw.calls('widget_invalidate_force')]


def test_the_info_screen_repaints_only_when_the_minute_changes(fw, clock):
    _, _, tick = info(fw, clock)
    img = lk(fw).img
    assert tick(0) == RET_REPEAT and invalidated(fw) == []
    clock['minutes'] += 1
    assert tick(0) == RET_REPEAT and invalidated(fw) == [img]
    assert tick(0) == RET_REPEAT and invalidated(fw) == [img]


def test_the_info_screen_repaints_when_the_battery_changes(fw, clock):
    bat, _, tick = info(fw, clock)
    bat.set('87%')
    assert tick(0) == RET_REPEAT and invalidated(fw) == [lk(fw).img]


def test_the_info_screen_s_timer_ends_while_the_screen_is_off(fw, clock):
    _, _, tick = info(fw, clock)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    clock['minutes'] += 1
    assert tick(0) == RET_OK and lk(fw).timer == 0 and invalidated(fw) == []


def test_info_poll_starts_it_again_when_the_screen_lights(fw, clock):
    _, added, tick = info(fw, clock)
    poll = fw.fn('info_poll', None)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    tick(0)
    poll()  # still dark
    assert len(added) == 1
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    clock['minutes'] += 3  # what it shows now is the state it compares with
    poll()
    assert len(added) == 2 and added[1][1] == 1000 and lk(fw).timer == 2
    poll()  # running already
    assert len(added) == 2
    assert tick(0) == RET_REPEAT and invalidated(fw) == []


def test_info_poll_without_the_info_screen_does_nothing(fw, clock):
    added = timers(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.fn('info_poll', None)()
    assert added == []


class Canvas:
    """A canvas with its lcd, whose fill colour stock's setter writes."""
    def __init__(self, fw, fill=0xff123456):
        self.lcd = C.create_string_buffer(0x200)
        self.raw = C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.raw, 0x38, C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, 0xbc, fill)
        self.p = C.addressof(self.raw)
        self.fills = []
        fw.mock('canvas_set_fill_color', lambda c, color, *_: self.set(color))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fills.append(
            (C.c_int(x).value, C.c_int(y).value, w, h, struct.unpack_from('<I', self.lcd, 0xbc)[0])))

    def set(self, color):
        struct.pack_into('<I', self.lcd, 0xbc, color & 0xffffffff)


@pytest.mark.parametrize('picture, black, shown', [(WALLS[BLACK], 1, 0), (WALLS[TIDE], 0, 0), (WALLS[BLACK], 1, 1)])
def test_black_s_info_screen_paints_black_and_gives_the_fill_back(fw, clock, picture, black, shown):
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    win = lock_window(fw, picture, names=INFO, THEME='1')
    fw.mock('widget_get_visible', lambda *_: shown)  # the song's card and the slide hint
    art = []
    fw.mock('widget_load_image', lambda w, name, bm, *_: art.append(C.string_at(name)) or 1)  # none drawn
    canvas = Canvas(fw)
    fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(win['img_screensaver'], canvas.p)
    assert ((0, 26, 375, 294, 0xff000000) in canvas.fills) == bool(black)  # under the bar, whichever half
    assert (b'classic_light_hint_shadow' in art) == bool(shown)  # the hint's shadow only under a shown hint
    assert struct.unpack_from('<I', canvas.lcd, 0xbc)[0] == 0xff123456


def test_a_window_without_its_picture_gets_no_info_screen(fw, clock):
    Bitmaps(fw)
    added = timers(fw)
    win = lock_window(fw, b'', names=[n for n in INFO if n != 'img_screensaver'], THEME='1', LOCKCLOCK='1')
    assert lk(fw).info == 0 and added == []
    assert boxes(win)['view_timeinfo'][1] == 14  # Large's own layout, as outside Classic


def test_the_info_screen_paints_nothing_without_an_lcd(fw, clock):
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    win = lock_window(fw, WALLS[BLACK], names=INFO, THEME='1')
    no_lcd = C.create_string_buffer(0x100)
    fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(win['img_screensaver'], C.addressof(no_lcd))
    assert fw.calls('canvas_fill_rect') == []


@functools.cache
def art_size(name):
    """The size tools/art.py draws the info screen's art at: the dark face and the slide hint's shadow (its PNG's
    own width and height); None for any other name."""
    if name == 'classic_light_clock_dark':
        return tools_art.CLOCK_DARK_PX, tools_art.CLOCK_DARK_PX
    if name == 'classic_light_hint_shadow':
        return struct.unpack('>II', tools_art.hint_shadow()[16:24])  # IHDR's width, height
    return None


class Art:
    """widget_load_image of the art tools/art.py draws for Classic, at its size; any other name fails, as an
    asset that isn't there. drawn: canvas_draw_image's (art's name, dst rect), None for a bitmap of the payload's."""
    def __init__(self, fw):
        self.loaded, self.drawn = None, []
        fw.mock('widget_load_image', self.load)
        fw.mock('canvas_draw_image', self.draw)

    def load(self, w, name, bitmap, *_):
        self.loaded = C.string_at(name).decode()
        size = art_size(self.loaded)
        if not size:
            return 1  # RET_FAIL
        C.c_uint.from_address(bitmap).value, C.c_uint.from_address(bitmap + 4).value = size
        return RET_OK

    def draw(self, canvas, bm, src, dst, *_):
        self.drawn.append((self.loaded, tuple((C.c_int * 4).from_address(dst))))
        self.loaded = None

    def at(self, name):
        return [dst for drawn, dst in self.drawn if drawn == name]


def info_paint(fw, picture=WALLS[TIDE], shown=1, names=INFO, **config):
    """Classic's info screen over picture, painted once with the song's card and the hint shown or not: (window,
    art, canvas)."""
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    win = lock_window(fw, picture, names=names, THEME='1', **config)
    fw.mock('widget_get_visible', lambda *_: shown)
    art, canvas = Art(fw), Canvas(fw)
    fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(win['img_screensaver'], canvas.p)
    return win, art, canvas


def channels(color):
    return color & 255, color >> 8 & 255, color >> 16 & 255


def dark(color):
    """Black or near it, at any strength: each channel under a quarter."""
    return color >> 24 and max(channels(color)) < 0x40


def light(color):
    """White or near it, at any strength: each channel over three quarters."""
    return color >> 24 and min(channels(color)) >= 0xc0


def rings(canvas):
    """paint_tray's trays under the bar, top first, each by the box of the dark ring that frames it: the dark fills
    short of the screen's sides that run on down the rows together (the ring's rounded rows, and inside them the
    tray's own darkest rows)."""
    found, bar = [], bar_height(canvas)
    for y, x, w in sorted((y, x, w) for x, y, w, h, color in canvas.fills if dark(color) and y >= bar and w < 375):
        if found and y <= found[-1][3] + 1:
            found[-1] = [min(found[-1][0], x), found[-1][1], max(found[-1][2], x + w), y]
        else:
            found.append([x, y, x + w, y])
    return [(x0, y0, x1 - x0, y1 - y0 + 1) for x0, y0, x1, y1 in found]


def bar_height(canvas):
    """The dark bar's rows: full-width rows of a pixel from the top."""
    return max(y + h for x, y, w, h, _ in canvas.fills if (x, w, h) == (0, 375, 1) and y < 60)


@pytest.mark.parametrize('missing', INFO[1:])
def test_the_info_screen_with_a_widget_missing_touches_none_that_isn_t_there(fw, clock, missing):
    win, _, _ = info_paint(fw, names=[n for n in INFO if n != missing], BATTERY='1')
    assert lk(fw).info == 1 and missing not in win.widgets
    assert [(name, args) for name, args in fw.calls() if name.startswith(('widget_', 'image_')) and args[0] == 0] == []


@pytest.mark.parametrize('large', [0, 1])
def test_the_clock_s_tray_holds_the_time(fw, clock, large):
    """The clock box spans the screen from its top. Standard: a tray under the bar holds the face, centred on its
    rows, and right of it the time on the tray's rows, the box ending above the song's card. Large: no face, the
    time across the tray and down to the box's foot, over the card's top rows (the card stays above it for taps)."""
    win, art, canvas = info_paint(fw, LOCKCLOCK=str(large))
    box = boxes(win)
    info, time, card = box['view_timeinfo'], box['clock_time'], box['view_playinfo']
    assert info[:3] == (0, 0, 375)
    rx, ry, rw, rh = rings(canvas)[0]
    tray = rx + 1, ry + 1, rw - 2, rh - 2
    assert tray[0] + tray[2] / 2 == 375 / 2 and tray[1] >= bar_height(canvas)
    faces = art.at('classic_light_clock_dark')
    if large:
        assert faces == [] and (time[0], time[2]) == (tray[0], tray[2])
        assert time[1] + time[3] == info[3] > card[1]
    else:
        (fx, fy, fw_, fh), = faces
        assert (time[1], time[3]) == (tray[1], tray[3])
        assert tray[0] <= fx and fx + fw_ < time[0] and time[0] + time[2] <= tray[0] + tray[2]
        assert time[2] >= tray[2] / 2  # the tray's right part, beside the face
        assert fy + fh / 2 == tray[1] + tray[3] / 2
        assert time[1] + time[3] <= info[3] <= card[1]
    assert win.props('widget_set_prop_str', 'clock_time')['style:normal:text_align_h'] == 'center'


def test_the_date_and_the_percent_sit_in_the_dark_bar(fw, clock, st):
    """The date spans the bar on the real bar's title row, centred; the percent fills the bar's height, set
    right."""
    title = C.create_string_buffer(0x400)
    struct.pack_into('<i', title, W_Y, 1)
    st('bar_title', C.c_void_p).value = C.addressof(title)
    win, _, canvas = info_paint(fw, BATTERY='1')
    bar = bar_height(canvas)
    x, y, w, h = boxes(win)['clock_date']
    assert (x, y, w) == (0, 1, 375) and y + h <= bar
    assert h >= win.props('widget_set_prop_int', 'clock_date')['style:normal:font_size']
    assert win.props('widget_set_prop_str', 'clock_date')['style:normal:text_align_h'] == 'center'
    px, py, pw, ph = boxes(win)['label_battery']
    assert (py, ph) == (0, bar)
    assert win.props('widget_set_prop_str', 'label_battery')['style:normal:text_align_h'] == 'right'
    size = win.props('widget_set_prop_int', 'label_battery')['style:normal:font_size']
    assert 0 < size < bar and pw >= 2 * size  # wide enough for "100%"
    # set right against the bar's battery, at the bar's right end
    battery = [(x, x + w) for x, y, w, h, _ in canvas.fills if y < bar and w < 375 and x > 375 // 2]
    left, right = min(x for x, _ in battery), max(x for _, x in battery)
    assert px + pw <= left < px + pw + (right - left)


@pytest.mark.parametrize('shown', [0, 1])
def test_the_song_s_tray_paints_only_while_its_card_shows(fw, clock, shown):
    """A hidden card takes its tray's ring and the row under it."""
    _, _, canvas = info_paint(fw, shown=shown)
    assert len(rings(canvas)) == 1 + shown


def test_the_song_s_card_holds_its_tray_the_cover_and_the_labels(fw, clock):
    """The card's box is the tray's ring and the light row under it, its own background cleared for the tray. In
    it the cover, square, as far in from the tray's left as from its top and centred on its rows; right of it the
    title over the artist, from the same x to the same width inside the tray, set left, the title the bigger, the
    artist in the title's light ink, dimmer."""
    win, _, canvas = info_paint(fw)
    box = boxes(win)
    card = box['view_playinfo']
    rx, ry, rw, rh = rings(canvas)[1]
    assert card == (rx, ry, rw, rh + 1)
    below = [(x, w) for x, y, w, _, color in canvas.fills if y == ry + rh and light(color)]
    assert below and all(rx <= x and x + w <= rx + rw for x, w in below)
    assert sum(w for _, w in below) >= 0.9 * rw  # across the ring, short of its rounded corners
    assert win.props('widget_set_prop_int', 'view_playinfo')['style:normal:bg_color'] == 0
    tray = rx + 1 - card[0], ry + 1 - card[1], rw - 2, rh - 2  # in the card
    cx, cy, cw, ch = box['img_playcover']
    assert cw == ch and cx - tray[0] == cy - tray[1] and cy + ch / 2 == tray[1] + tray[3] / 2
    title, artist = box['scrlabel_title'], box['scrlabel_artist']
    assert title[0] == artist[0] > cx + cw and title[2] == artist[2] and title[0] + title[2] <= tray[0] + tray[2]
    assert tray[1] <= title[1] < artist[1] and artist[1] + artist[3] <= tray[1] + tray[3]
    size = {n: win.props('widget_set_prop_int', n)['style:normal:font_size'] for n in ('scrlabel_title', 'scrlabel_artist')}
    assert size['scrlabel_title'] > size['scrlabel_artist'] > 0
    assert title[3] >= size['scrlabel_title'] and artist[3] >= size['scrlabel_artist']  # each box holds its line
    for name in ('scrlabel_title', 'scrlabel_artist'):
        assert win.props('widget_set_prop_str', name)['style:normal:text_align_h'] == 'left'
    ink = {n: win.props('widget_set_prop_int', n)['style:normal:text_color'] & 0xffffffff
           for n in ('scrlabel_title', 'scrlabel_artist')}  # what AWTK draws them in
    assert ink['scrlabel_title'] & 0xffffff == ink['scrlabel_artist'] & 0xffffff and light(ink['scrlabel_title'])
    assert ink['scrlabel_artist'] >> 24 < ink['scrlabel_title'] >> 24


@pytest.mark.parametrize('shown', [0, 1])
def test_the_slide_hint_is_centred_under_the_card_dimmed_on_its_shadow(fw, clock, shown):
    """Stock's >>> flat at the size of the shadow's marks, centred under the card, its white dimmed but showing; its
    shadow, HINT_PAD past it all round, paints only while it shows."""
    win, art, _ = info_paint(fw, shown=shown)
    box = boxes(win)
    x, y, w, h = box['img_slide']
    card = box['view_playinfo']
    assert x + w / 2 == 375 / 2 and card[1] + card[3] <= y and y + h <= 320
    opacity, = [args[0] for name, args in win.calls('widget_set_opacity') if name == 'img_slide']
    assert 0 < opacity < 255
    assert [args[0] for name, args in win.calls('image_set_draw_type') if name == 'img_slide'] == [8]  # FILL: to the box
    pad = tools_art.HINT_PAD
    shadow = (x - pad, y - pad, w + 2 * pad, h + 2 * pad)
    assert art.at('classic_light_hint_shadow') == [shadow] * shown


def test_the_info_screen_paints_only_over_its_picture(fw, clock):
    """ringnav_paint calls lockscreen_paint for every widget: the clocks' and the song's own paint nothing more."""
    win, art, canvas = info_paint(fw)
    painted = len(canvas.fills), len(art.drawn)
    for name in ('clock_time', 'view_playinfo', 'scrlabel_title'):
        fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(win[name], canvas.p)
    assert (len(canvas.fills), len(art.drawn)) == painted


def test_a_card_picture_after_a_built_in_shows_no_wallpaper_under_the_info_screen(fw, clock):
    """The built-in's render stays for its next lock window, but a card image's is stock's to paint."""
    Bitmaps(fw)
    Battery(fw)
    timers(fw)
    lock_window(fw, WALLS[AURORA], names=INFO, THEME='1')
    win = lock_window(fw, b'/mnt/mmc/lockscreenimage/a.jpg', names=INFO, THEME='1')
    fw.mock('widget_get_visible', lambda *_: 1)
    art, canvas = Art(fw), Canvas(fw)
    fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(win['img_screensaver'], canvas.p)
    assert lk(fw).full and [dst for name, dst in art.drawn if name is None] == []
    assert fw.calls('widget_on_paint_children') == []


@pytest.mark.parametrize('picture, page', [(b'', 1), (b'screensaver_default', 1), (WALLS[TIDE], 0),
                                           (b'/mnt/mmc/lockscreenimage/a.jpg', 0)])
def test_the_page_paints_under_the_bar_only_with_no_picture(fw, clock, picture, page):
    _, _, canvas = info_paint(fw, picture)
    bar = bar_height(canvas)
    assert len([f for f in canvas.fills if f[:4] == (0, bar, 375, 320 - bar)]) == page


def whites(canvas):
    """The pixels white (or near it) was filled at, at any coverage."""
    return {(x + i, y + j) for x, y, w, h, color in canvas.fills if light(color) for j in range(h) for i in range(w)}


def pixel(at):
    """The pixel holding the point at."""
    return math.floor(at[0]), math.floor(at[1])


def face_centre(art):
    (x, y, w, h), = art.at('classic_light_clock_dark')
    return x + w / 2, y + h / 2


def along(centre, degrees, distance):
    """distance px from centre at degrees clockwise from 12."""
    a = math.radians(degrees)
    return centre[0] + distance * math.sin(a), centre[1] - distance * math.cos(a)


def apart(a, b):
    return abs((a - b + 180) % 360 - 180)


@pytest.mark.parametrize('h, m', [(0, 0), (3, 0), (7, 5), (9, 40), (12, 59)])
def test_the_hands_show_the_time(fw, clock, h, m):
    """The hour hand at h % 12 * 30 + m / 2 degrees and the minute hand at m * 6, out from the face's centre: each
    white along its own angle well past the hub, the minute hand's tip past the hour hand's, both inside the face.
    Out past the hands' broad feet the face is white nowhere else: not off their angles, nor past the hour hand's
    tip off the minute hand's."""
    clock['minutes'] = h * 60 + m
    _, art, canvas = info_paint(fw)
    centre, radius, lit = face_centre(art), tools_art.CLOCK_DARK_PX / 2, whites(canvas)
    hour, minute = h % 12 * 30 + m / 2, m * 6

    def reach(degrees):
        """How far out the face's white goes along degrees, to a quarter pixel."""
        out = [d / 4 for d in range(int(4 * radius) + 1) if pixel(along(centre, degrees, d / 4)) in lit]
        return max(out, default=0)
    away = max(range(0, 360, 5), key=lambda a: min(apart(a, hour), apart(a, minute)))  # as far from both as can be
    hub, hour_tip, minute_tip = reach(away), reach(hour), reach(minute)
    assert 2 * hub < hour_tip <= minute_tip < radius
    if apart(hour, minute) > 20:  # on its own: where the two coincide they reach as one
        assert hour_tip < minute_tip
    for a in range(0, 360, 5):
        for d in range(math.ceil(hour_tip / 2), math.floor(radius) + 1):
            if apart(a, minute) > 20 and (apart(a, hour) > 20 or d > hour_tip + 1):
                assert pixel(along(centre, a, d)) not in lit, (a, d)


def test_at_12_the_hands_stand_straight_up_from_the_face_s_centre(fw, clock):
    clock['minutes'] = 0
    _, art, canvas = info_paint(fw)
    cx, cy = face_centre(art)
    near = [(x, y, w, color >> 24) for x, y, w, _, color in canvas.fills
            if light(color) and abs(y - cy) < tools_art.CLOCK_DARK_PX / 2 and abs(x - cx) < 10]
    weight = sum(w * a for _, _, w, a in near)
    assert sum((x + w / 2) * w * a for x, _, w, a in near) / weight == pytest.approx(cx, abs=0.1)
    assert all(y < cy + 4 for _, y, _, _ in near)  # nothing below the pivot


def test_a_clock_that_can_t_be_read_shows_the_face_without_hands(fw, clock):
    clock['minutes'] = None
    _, art, canvas = info_paint(fw)
    cx, cy = face_centre(art)
    assert not [f for f in canvas.fills if light(f[4]) and abs(f[1] - cy) < 40 and abs(f[0] - cx) < 40]


class Texts:
    """widget_get_text by widget: a wide string for each name given, none for the rest."""
    def __init__(self, fw, win, **texts):
        self.win, self.wide = win, {}
        for name, value in texts.items():
            self.set(name, value)
        fw.mock('widget_get_text', lambda w, *_: C.addressof(self.wide[w]) if w in self.wide else 0)

    def set(self, name, value):
        self.wide[self.win[name]] = (C.c_uint * (len(value) + 1))(*map(ord, value), 0)


def readable(fw, win):
    """widget_get_prop_int reads back what widget_set_prop_int set, else the default, as a widget's props do."""
    def get(w, key, default, *_):
        name = C.string_at(key).decode()
        set_ = [v for kind, at, k, v in win.set if kind == 'widget_set_prop_int' and at == w and k == name]
        return set_[-1] if set_ else default
    fw.mock('widget_get_prop_int', get)


def test_the_song_s_and_the_date_s_labels_take_the_face_their_text_has(fw, clock):
    """Stock writes their text after the window loads, in any script: each paint gives each label the face
    theme_face finds for its text at the label's size (Classic's own, bold at its fallback size, or the system
    font), only when that changed."""
    Bitmaps(fw)
    timers(fw)
    win = lock_window(fw, WALLS[TIDE], names=INFO, THEME='1')
    labels = {'scrlabel_title': 'Song', 'scrlabel_artist': 'Песня', 'clock_date': '日本'}
    size = {name: win.props('widget_set_prop_int', name)['style:normal:font_size'] for name in labels}  # at load
    texts = Texts(fw, win, **labels)
    readable(fw, win)
    theme_face = fw.fn('theme_face', C.c_char_p, C.c_void_p, C.c_int, C.POINTER(C.c_int))

    def wanted(name, value):
        wide, px = (C.c_uint * (len(value) + 1))(*map(ord, value), 0), C.c_int(size[name])
        face = theme_face(wide, -1, C.byref(px))
        return {'style:normal:font_name': (face or b'default').decode(), 'style:normal:font_size': px.value}
    want = {name: wanted(name, value) for name, value in labels.items()}
    assert len({w['style:normal:font_name'] for w in want.values()}) == 3  # Classic's own, bold and the system's
    fw.mock('widget_get_visible', lambda *_: 1)
    canvas = Canvas(fw)

    def painted():
        mark = len(win.set)
        fw.fn('lockscreen_paint', None, C.c_void_p, C.c_void_p)(win['img_screensaver'], canvas.p)
        return {name: {k: v for _, at, k, v in win.set[mark:] if at == win[name] and k.startswith('style:normal:font_')}
                for name in labels}
    assert painted() == want
    assert painted() == {name: {} for name in labels}  # the same text: nothing to set
    texts.set('scrlabel_title', '日本')
    assert painted()['scrlabel_title'] == wanted('scrlabel_title', '日本')


@pytest.mark.parametrize('large', [0, 1])
def test_the_info_screen_s_labels_load_in_the_theme_s_face(fw, clock, large):
    """Before stock writes their text, the date, the time and the song's labels are in Classic's own face."""
    win, _, _ = info_paint(fw, LOCKCLOCK=str(large))
    latin = (C.c_uint * 2)(ord('A'), 0)
    face = fw.fn('theme_face', C.c_char_p, C.c_void_p, C.c_int, C.c_void_p)(latin, -1, None).decode()
    for name in ('clock_date', 'clock_time', 'scrlabel_title', 'scrlabel_artist'):
        assert [v for kind, at, k, v in win.set if at == win[name] and k == 'style:normal:font_name'][0] == face


@pytest.fixture(scope='module')
def scratch(library):
    """navigation.c's scratch_t's field offsets as this build lays it out."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


class Playing:
    """What the player reports (2 playing, 3 paused, 0 neither) and the status bar's battery icon, charging or
    not, as the info screen reads them."""
    def __init__(self, fw, st, status=2):
        self.status, self.icon = status, C.create_string_buffer(b'bar_battery', 16)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        self.bar_icon = C.create_string_buffer(0x400)
        st('bar_icon', C.c_void_p).value = C.addressof(self.bar_icon)
        fw.mock('widget_get_prop_str', lambda w, key, default, *_:
                C.addressof(self.icon) if w == C.addressof(self.bar_icon) and C.string_at(key) == b'image' else default)

    def charging(self, on):
        self.icon.value = b'bar_charge' if on else b'bar_battery'


def ticking(fw, clock, st, status, battery='88%'):
    playing = Playing(fw, st, status)
    bat, _, tick = info(fw, clock, battery)
    return playing, bat, tick


@pytest.mark.parametrize('was, now', [(2, 3), (3, 2), (0, 2), (2, 0)])
def test_the_info_screen_repaints_when_the_song_pauses_plays_or_stops(fw, clock, st, was, now):
    playing, _, tick = ticking(fw, clock, st, was)
    assert tick(0) == RET_REPEAT and invalidated(fw) == []
    playing.status = now
    assert tick(0) == RET_REPEAT and invalidated(fw) == [lk(fw).img]


@pytest.mark.parametrize('status', [2, 3])
def test_the_info_screen_repaints_every_minute_playing_or_paused(fw, clock, st, status):
    _, _, tick = ticking(fw, clock, st, status)
    clock['minutes'] = 7 * 60 + 4
    tick(0)
    before = len(invalidated(fw))
    for n in range(1, 4):
        clock['minutes'] += 1
        assert tick(0) == RET_REPEAT and len(invalidated(fw)) == before + n


@pytest.mark.parametrize('status', [2, 3])
def test_the_info_screen_repaints_on_each_percent_and_on_charging(fw, clock, st, status):
    playing, bat, tick = ticking(fw, clock, st, status, '89%')
    bat.set('88%')
    assert tick(0) == RET_REPEAT and len(invalidated(fw)) == 1
    playing.charging(True)
    assert tick(0) == RET_REPEAT and len(invalidated(fw)) == 2
    assert tick(0) == RET_REPEAT and len(invalidated(fw)) == 2
