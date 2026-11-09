"""lockscreen.c (docs/internals.md#lock-screen): the six built-in wallpapers wall_render draws, their place in
stock's picture list, the lock window's wallpaper and the picker's thumbnails, the Large clock and Classic's
info screen."""
import ctypes as C
import struct

import pytest

from conftest import O, block

WALLS = [b'q2pod:' + n for n in (b'aurora', b'sunrise', b'graphite', b'tide', b'ember', b'black')]
AURORA, SUNRISE, GRAPHITE, TIDE, EMBER, BLACK = range(6)
EVT_DESTROY = O['EVT_DESTROY']
W_W, W_PARENT = O['W_W'], O['W_PARENT']
RET_OK, RET_REPEAT = 0, 8
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
    """bitmap_create_ex's bitmaps: a header (flags at 0xc) and pixels of their line length exactly."""
    def __init__(self, fw, pad=0, create=True, lock=True):
        self.fw, self.pad, self.create_ok, self.lock_ok = fw, pad, create, lock
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
        self.made[bm] = (w, h, line, block(self.fw, size=line * h))
        return bm

    def pixels(self, bm):
        w, h, line, data = self.made[bm]
        flat = (C.c_uint * (line // 4 * h)).from_address(data)
        return [list(flat[y * line // 4:y * line // 4 + w]) for y in range(h)]

    def opaque(self, bm):
        return C.c_ushort.from_address(bm + 0xc).value & 1


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


# The picker's thumbnails (lockscreen_rows).

class Picker:
    """The Lock screen page: stock's rows in scroll_view (Time show, About), the list, and the grid's
    image_%u thumbnails, 110 x 94."""
    def __init__(self, fw, names, children=3):
        Config(fw)
        self.pics = Pictures(fw, names)
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
        fw.mock('tk_snprintf', lambda s, n, f, i, *_: C.memmove(s, b'image_%d\0' % i, len(b'image_%d' % i) + 1) or 0)
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


def test_a_thumbnail_s_end_frees_its_bitmap_and_forgets_it(fw):
    bitmaps, seen = Bitmaps(fw), draws(fw)
    picker = Picker(fw, [b'screensaver_default', WALLS[TIDE]])
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


@pytest.mark.parametrize('picture, page', [(b'', 1), (b'screensaver_default', 1), (WALLS[TIDE], 0),
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
