"""Now Playing's marks in the flat look (np_marks.c, Modern's): More, the play mode and the heart stay stock's images,
unseen, and are drawn on their parent in the flat look's plain glyphs, the half's. Classic's and Minimal's stay stock's."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_playing import CLASSIC, MINIMAL, MODERN, Now, theme, ui, st  # noqa: F401 (fixtures)

MARKS = ('img_more', 'img_playmode', 'img_fav')
GLYPHS = {'play_more': 'np_more', 'play_moredown': 'np_more_q', 'play_fav': 'np_heart_on', 'play_unfav': 'np_heart_off',
          'play_order': 'np_order', 'play_random': 'np_shuffle', 'play_cycle': 'np_repeat', 'play_cycle1': 'np_repeat1'}


class Page:
    """Now Playing with its three marks, stock's images on the page, opened under theme t."""
    def __init__(self, fw, ui, mcl, st, t, light=0, images=('play_more', 'play_order', 'play_unfav')):
        theme(fw, st, t, light)
        self.fw, self.ui, self.opacity = fw, ui, {}
        fw.mock('widget_set_opacity', lambda w, v, *_: self.opacity.__setitem__(w, v))
        self.now = Now(fw, ui, mcl, st, opened=False)
        self.marks = [ui.widget(self.now.win, name, 300 + 10 * i, 250, 40, 40) for i, name in enumerate(MARKS)]
        for w, image in zip(self.marks, images):
            ui.set_str(w, b'image', image.encode())
        assert self.now.open() == 41

    def paint(self, w=None):
        """np_marks on w (the page): the images loaded, and where each was drawn."""
        loads, draws = [], []
        def load(owner, name, bitmap, *_):
            loads.append(C.string_at(name).decode())
            C.memmove(bitmap, struct.pack('<II', 32, 32), 8)
            return 0
        self.fw.mock('widget_load_image', load)
        self.fw.mock('canvas_draw_image', lambda c, b, s, d, *_: draws.append(struct.unpack('<4i', C.string_at(d, 16))))
        lcd, raw = (C.c_char * 0x200)(), (C.c_char * 0x100)()
        C.c_void_p.from_buffer(raw, O['CANVAS_LCD']).value = C.addressof(lcd)
        self.fw.fn('np_marks', None, C.c_void_p, C.c_void_p)(w or self.now.win, C.addressof(raw))
        return loads, draws


@pytest.mark.parametrize('light, half', [(0, 'd'), (1, 'l')])
def test_modern_draws_each_mark_in_its_flat_glyph_the_half_s(fw, ui, mcl, st, light, half):
    page = Page(fw, ui, mcl, st, MODERN, light)
    assert [page.opacity.get(w) for w in page.marks] == [0, 0, 0]  # unseen, still there for touch
    loads, draws = page.paint()
    assert sorted(loads) == sorted(f'flat_{g}_{half}' for g in ('np_more', 'np_order', 'np_heart_off'))
    assert sorted(draws) == sorted((300 + 10 * i + 4, 254, 32, 32) for i in range(3))  # centred on each image's box


@pytest.mark.parametrize('image', GLYPHS)
def test_each_state_has_its_own_glyph(fw, ui, mcl, st, image):
    """Heart on and off, each play mode and More, pressed or not: a name each, so each reads apart."""
    page = Page(fw, ui, mcl, st, MODERN, images=(image, 'play_order', 'play_unfav'))
    loads, _ = page.paint()
    assert f'flat_{GLYPHS[image]}_d' in loads


def test_an_image_without_a_glyph_is_drawn_as_it_is_and_a_hidden_one_not_at_all(fw, ui, mcl, st):
    page = Page(fw, ui, mcl, st, MODERN, images=('play_more', 'cover_other', 'play_fav'))
    ui.shown[page.marks[0]] = 0
    loads, _ = page.paint()
    assert sorted(loads) == ['cover_other', 'flat_np_heart_on_d']


def test_only_the_page_s_own_paint_draws_them(fw, ui, mcl, st):
    page = Page(fw, ui, mcl, st, MODERN)
    assert page.paint(page.now.slider) == ([], [])


@pytest.mark.parametrize('t', [CLASSIC, MINIMAL], ids=['Classic', 'Minimal'])
def test_classic_and_minimal_keep_stock_s_marks(fw, ui, mcl, st, t):
    page = Page(fw, ui, mcl, st, t)
    assert not page.opacity.keys() & set(page.marks) and page.paint() == ([], [])


def test_another_theme_picked_with_the_page_open_gives_stock_its_images_back(fw, ui, mcl, st):
    page = Page(fw, ui, mcl, st, MODERN)
    theme(fw, st, CLASSIC)
    assert page.paint() == ([], []) and [page.opacity[w] for w in page.marks] == [255, 255, 255]
    assert page.paint() == ([], [])
