"""The flat look's own pages (pages.own painter flat, Modern's): Quick Settings' page, buttons and brightness bar,
patch/flat.c's colours and bar, and its images through the image hook. Classic's are test_nav_paint's."""
import ctypes as C

import pytest

from conftest import O
from test_nav_paint import (ACCENTS, GRAPHITE, ICONS, LABELS, MINIMAL, MODERN, PAPER, RED, RED_PX, TIDAL, WHITE, Canvas, Quick, bitmaps,
                            channels, fill_props, image_add, layout, luma, paint_bg, quick_window, rgb_of, rgba, scheme, scrub,
                            st, stock_h, theme, vol_paint, volume, world)  # noqa: F401 (fixtures, by name)
from test_nav_painters import Quick as QuickInit
MODERN_ACCENTS = [0, 1, 2, 3, 4, 5, 6, 8]  # Modern's colorSchemes, by ACCENT


def mix(a, b):
    """Halfway from a to b, per channel, as theme_engine.c's mix(a, b, 1, 2)."""
    return sum(((a >> s & 255) + (b >> s & 255)) // 2 << s for s in (0, 8, 16))


def flip(fw, c):
    return fw.fn('flip', C.c_uint, C.c_uint)(c)


def bright(fw, c):
    return fw.fn('bright_tone', C.c_int, C.c_uint)(c)


@pytest.mark.parametrize('accent', MODERN_ACCENTS)
@pytest.mark.parametrize('light', ['0', '1'])
def test_what_is_on_is_modern_s_selection_bar(fw, accent, light):
    """The bar's middle row, its top and bottom tones halfway, final: flipped in the light half as the bar's ink is.
    Graphite's bar, a grey like a button at rest, gives way to its silver red tone."""
    theme(fw, MODERN, LIGHT=light, ACCENT=str(accent))
    want = ACCENTS[accent][RED] if accent == GRAPHITE else mix(ACCENTS[accent][0], ACCENTS[accent][1])
    assert fw.fn('quick_on', C.c_uint)() == (flip(fw, want) if light == '1' else want)


@pytest.mark.parametrize('light, paper', [('0', 0), ('1', PAPER * 0x010101)])
def test_a_flat_page_s_ground_is_the_half_s_paper(fw, light, paper):
    theme(fw, MODERN, LIGHT=light)
    assert fw.fn('flat_page', C.c_uint)() == paper


def quick_page(fw, world, images, light='0', pressed=None, **config):
    theme(fw, MODERN, LIGHT=light, **config)
    win = world.window('statusbar_dialog', 'dialog')
    icons = {}
    for i, (name, image) in enumerate(images.items()):
        icons[name] = world.add(win, name, 'image', box=(30 + 80 * (i % 4), 40 + 100 * (i // 4), 54, 54), image=image)
        world.add(win, LABELS[name], 'label', box=(0, 0, 80, 18), text='Label')
    if pressed:
        fw.var('quick_settings__quick', Quick).pressed = icons[pressed]
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    return win, icons, canvas


def face(canvas, icon_box, row=27):
    """A quick button's disc, over its icon's box, on one of its rows: its colour there, the row's widest fill."""
    x, y, w, _ = icon_box
    return max((fw_, c) for fx, fy, fw_, h, c in canvas.fills if (fy, h) == (y + row, 1) and x <= fx and fx + fw_ <= x + w)[1]


def grey(color):
    r, g, b = channels(rgb_of(color))
    return max(r, g, b) - min(r, g, b) <= 4


@pytest.mark.parametrize('light', ['0', '1'])
def test_the_quick_page_is_the_paper_under_two_cards(fw, world, light):
    win, _, canvas = quick_page(fw, world, {}, light=light)
    ground = canvas.fills[0]
    assert ground == (0, 0, *world.at(win)[2:], rgba(fw.fn('flat_page', C.c_uint)()))  # the whole window, one fill
    wide = max(f[2] for f in canvas.fills[1:])
    rows = [f for f in canvas.fills[1:] if f[2] == wide]  # the cards' rows between their corners
    ys = [f[1] for f in rows]
    assert len([b for a, b in zip(ys, ys[1:]) if b - a > 1]) == 1  # two runs of rows: two cards, one under the other
    assert len({(f[0], f[2], f[4]) for f in rows}) == 1 and rows[0][4] != ground[4]  # alike, off the ground
    assert canvas.fill == 0xff123456


@pytest.mark.parametrize('accent', MODERN_ACCENTS)
def test_an_on_button_is_modern_s_bar_under_a_glyph_that_shows(fw, world, stock_h, accent):
    on, off = stock_h['QS_LIT'][0], 'drop_wifi'
    assert off not in stock_h['QS_LIT']
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': on, 'img_btswitch': off}, ACCENT=str(accent))
    lit = fw.fn('quick_on', C.c_uint)()
    assert face(canvas, world.at(icons['img_wifiswitch'])) == rgba(lit)
    plain = face(canvas, world.at(icons['img_btswitch']))
    assert grey(plain) and luma(rgb_of(plain)) < 0x60  # a dark grey at rest
    ink = '_l' if bright(fw, lit) else ''
    assert canvas.art(f'flat_{on}{ink}') == [world.at(icons['img_wifiswitch'])[:2] + (20, 10)]
    assert canvas.art(f'flat_{off}') == [world.at(icons['img_btswitch'])[:2] + (20, 10)]  # white at rest, in the dark half
    assert not [n for n, _ in canvas.arts if n.startswith('classic_light_')]


def test_a_light_button_at_rest_is_a_flat_light_grey_under_a_dark_glyph(fw, world):
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': 'drop_wifi'}, light='1')
    box = world.at(icons['img_wifiswitch'])
    shades = {face(canvas, box, row) for row in (5, 27, 48)}
    assert len(shades) == 1 and grey(*shades) and luma(rgb_of(*shades)) > 0xc0  # no gradient, no ring
    assert canvas.art('flat_drop_wifi_l') and not canvas.art('flat_drop_wifi')


@pytest.mark.parametrize('image', ['drop_wifi', 'drop_wifiopen'])
def test_a_pressed_quick_button_is_a_sixth_darker(fw, world, image):
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': image, 'img_btswitch': image}, pressed='img_wifiswitch')
    pressed, shown = face(canvas, world.at(icons['img_wifiswitch'])), face(canvas, world.at(icons['img_btswitch']))
    assert channels(rgb_of(pressed)) == tuple(v * 5 // 6 for v in channels(rgb_of(shown)))


def test_each_quick_button_has_its_label_under_it_in_the_system_face(fw, world):
    _, icons, canvas = quick_page(fw, world, {name: 'drop_wifi' for name in ICONS})
    for name, icon in icons.items():
        x, y, w, h = world.at(icon)
        lx, ly, lw, _ = world.at(world.find(world.top, LABELS[name]))
        assert 2 * lx + lw == 2 * x + w and ly >= y + h  # centred under its icon
    assert sorted(r[:2] for _, r, *_ in canvas.texts) == sorted(world.at(world.find(world.top, LABELS[n]))[:2] for n in icons)
    assert {(c, f) for _, _, c, f in canvas.texts} == {(rgba(0x98989d), (None, 14))}  # the dark half's quiet ink, final


@pytest.mark.parametrize('text, lines', [('Wi-Fi', ['Wi-Fi']), ('Output Way', ['Output', 'Way']),
                                         ('Low Gain', ['Low Gain']), ('Equalisers', ['Equalise', 'rs']),
                                         ('Sys Settings', ['Sys', 'Settings']), ('x' * 40, ['x' * 8, 'x' * 31])])
def test_a_flat_quick_label_wraps_at_the_last_space_that_fits(fw, world, text, lines):
    """10 px a character here, 86 px a line: a word too long for one breaks where the line is full; a line holds
    31 characters at most."""
    theme(fw, MODERN, LIGHT='1')
    win = world.window('statusbar_dialog', 'dialog')
    label = world.add(win, 'label_wifi', 'label', text=text)
    canvas = Canvas(fw)
    x, y = 100, 200  # its first line's box
    draw = fw.fn('quick_settings__quick_label_flat', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)
    draw(canvas.p, label, x, y)
    assert [t for t, *_ in canvas.texts] == lines
    boxes = [r for _, r, *_ in canvas.texts]
    box = world.at(label)
    assert boxes[0][:2] == box[:2] == (x, y) and {(b[0], b[2], b[3]) for b in boxes} == {(x, box[2], boxes[0][3])}
    assert all(0 < b[1] - a[1] < a[3] for a, b in zip(boxes, boxes[1:]))  # each under the last, tighter than a box
    assert box[1] + box[3] == boxes[-1][1] + boxes[-1][3]  # stock's box round all its lines, for its repaint
    assert {c for _, _, c, _ in canvas.texts} == {rgba(0x6e6e73)}  # the light half's quiet ink, final
    assert world[label].props['style:normal:text_color'] == 0  # its own ink clear
    sets = len(fw.calls('widget_set_prop_int'))
    draw(canvas.p, label, x, y)
    assert len(fw.calls('widget_set_prop_int')) == sets  # the box once


def test_an_empty_flat_quick_label_draws_nothing(fw, world):
    theme(fw, MODERN)
    label = world.add(world.window('statusbar_dialog', 'dialog'), 'label_wifi', 'label', text='')
    canvas = Canvas(fw)
    fw.fn('quick_settings__quick_label_flat', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(canvas.p, label, 100, 200)
    assert canvas.texts == [] and fw.calls('widget_move_resize') == []


def test_quick_settings_under_minimal_is_still_stock_s(fw, world):
    theme(fw, MINIMAL)
    win = world.window('statusbar_dialog', 'dialog')
    world.add(win, 'img_wifiswitch', 'image', box=(30, 40, 60, 60), image='drop_wifi')
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    assert canvas.fills == [] and canvas.arts == []


def test_modern_s_page_lays_its_buttons_on_the_flat_grid(fw, st):
    quick = QuickInit(fw, st, 2)
    boxes = [quick.icons[n].box for n in QuickInit.ICONS]
    assert [b[0] for b in boxes] == [33, 118, 203, 288] * 2 and [b[1] for b in boxes] == [34] * 4 + [122] * 4
    assert {b[2:] for b in boxes} == {(54, 54)}
    assert quick.menu.box == (0, 0, quick.menu_was[2], 226)  # the buttons' card and the gap under it
    view = quick.slider.parent
    assert view.box[1:] == (233, 375, 48) and quick.slider.box == (33, 0, 309, 48)  # the bar's box, its 48 rows about it
    assert [c[1][1] for c in fw.calls('widget_set_opacity')] == [0] * 8
    down = quick.fn('quick_down')
    assert all(len(quick.ui.handled(quick.icons[n], down)) == 1 for n in QuickInit.ICONS)
    assert quick.ui.handled(quick.slider, quick.fn('quick_moved')) == [view.addr] * 2


def test_modern_s_brightness_clears_stock_s_bar_for_its_own(fw, world):
    theme(fw, MODERN)
    _, _, slider = quick_window(world)
    paint_bg(fw, slider, Canvas(fw))
    states = ('normal', 'pressed', 'over', 'focused')
    assert fill_props(world, slider) == {'_ringnav_fill': 1, **{f'style:{s}:fg_color': 0 for s in states},
                                         **{f'style:{s}:bg_color': 0 for s in states}}


def flat_scrub(fw, world, value, **config):
    theme(fw, MODERN, **config)
    return scrub(fw, world, quick_window(world)[2], value)


def lit_of(fw, canvas):
    on = rgba(fw.fn('quick_on', C.c_uint)())
    return [f for f in canvas.fills if f[4] == on], [f for f in canvas.fills if f[4] != on]


def test_the_brightness_fill_grows_with_the_value_from_its_round_end(fw, world):
    """Never less than the bar's own height, its round end; from there the value's share of the rest."""
    theme(fw, MODERN, ACCENT=str(TIDAL))
    _, _, slider = quick_window(world)
    ends, bars = {}, set()
    for value in (0, 25, 50, 100):
        canvas = scrub(fw, world, slider, value)
        lit, rest = lit_of(fw, canvas)
        x = min(f[0] for f in rest)
        bars.add((x, max(f[0] + f[2] for f in rest) - x, min(f[1] for f in rest), len({f[1] for f in rest})))
        assert {f[1] for f in lit} == {f[1] for f in rest}  # on every one of its rows
        ends[value] = max(f[0] + f[2] for f in lit) - x
    (_, w, _, h), = bars  # the bar stays
    assert (w, h) == (309, 36) and ends == {v: h + (w - h) * v // 100 for v in ends}


@pytest.mark.parametrize('value, light, accent', [(50, '1', TIDAL), (50, '0', TIDAL), (100, '0', TIDAL), (50, '0', GRAPHITE),
                                                  (100, '1', GRAPHITE)])
def test_the_bar_s_suns_take_the_ink_that_shows(fw, world, value, light, accent):
    """The small one is on the fill: white, or dark on a bright one. The large one is quiet in the half until the
    fill reaches it, then the small one's ink."""
    canvas = flat_scrub(fw, world, value, LIGHT=light, ACCENT=str(accent))
    small = '_l' if bright(fw, fw.fn('quick_on', C.c_uint)()) else ''
    large = small if value == 100 else '_ql' if light == '1' else '_qd'
    assert [n for n, _ in canvas.arts] == ['flat_drop_dim' + small, 'flat_drop_bright' + large]
    _, rest = lit_of(fw, canvas)
    x, top = min(f[0] for f in rest), min(f[1] for f in rest)
    (_, dim), (_, bright_) = canvas.arts
    assert dim[:2] == (x + 6, top + 6) and bright_[:2] == (x + 309 - 33, top + 3)


@pytest.mark.parametrize('loud, quiet, light', [(1, 'spk_quiet_w', '0'), (0, 'spk_quiet_w', '1')])
def test_the_flat_bar_s_speakers(fw, world, loud, quiet, light):
    """flat_bar's other marks, the slider pages': the near speaker white on the fill, the far one in the half's ink
    until the fill reaches it."""
    theme(fw, MODERN, LIGHT=light, ACCENT=str(TIDAL))
    canvas = Canvas(fw)
    r = (C.c_int * 4)(10, 20, 300, 36)
    fw.fn('flat_bar', None, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_uint, C.c_int)(
        None, canvas.p, r, 300 if loud else 100, 0x2c2c2e, 2)
    far = 'spk_loud_w' if loud else 'spk_loud_l' if light == '1' else 'spk_loud_d'
    assert canvas.arts and [(n, d[:2]) for n, d in canvas.arts] == [('flat_' + quiet, (22, 31)), ('flat_' + far, (276, 31))]


@pytest.mark.parametrize('light', ['0', '1'])
def test_the_flat_look_s_images_are_final(fw, bitmaps, light):
    theme(fw, MODERN, LIGHT=light)
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, b'flat_drop_wifi', bm)
    assert bm.rows() == [[RED_PX, WHITE]]


def test_classic_s_images_are_not_final_under_modern(fw, bitmaps):
    """art_final is the look's own prefix: Classic's names under Modern flip as before."""
    theme(fw, MODERN, LIGHT='1', STATUSBAR='2')
    bm = bitmaps(fw, [[WHITE]])
    image_add(fw, b'classic_light_band_top', bm)
    assert bm.rows() != [[WHITE]]


@pytest.mark.parametrize('level', [0, 50, 100])
def test_over_the_flat_quick_settings_the_volume_is_the_bar_s_card(fw, world, st, level):
    """The wheel on stock's volume over Modern's Quick Settings: the bar's card with the volume in the bar's place,
    between the speakers, no number; stock's slider moved onto the card for its repaints."""
    theme(fw, MODERN, ACCENT=str(TIDAL))
    dialog, vol, label = volume(fw, world, st, level, under='statusbar_dialog')
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    card = (18, 226, 339, 62)
    assert world.at(vol) == card and world[vol].visible == world[label].visible == 0
    assert min(f[1] for f in canvas.fills) == card[1] and max(f[1] + f[3] for f in canvas.fills) == card[1] + card[3]
    lit, rest = lit_of(fw, canvas)
    assert max(f[0] + f[2] for f in lit) - 33 == 36 + 273 * level // 100 and min(f[1] for f in lit) == 239
    assert [n for n, _ in canvas.arts][0] == 'flat_spk_quiet_w' and not canvas.texts and not canvas.rounded
    assert canvas.fill == 0xff123456


def test_over_modern_s_other_windows_the_volume_stays_its_panel(fw, world, st):
    theme(fw, MODERN)
    dialog, vol, _ = volume(fw, world, st, 37)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert world.at(vol) == (O['VOL_PANEL_X'], O['VOL_PANEL_Y'], 375 - 2 * O['VOL_PANEL_X'], O['VOL_PANEL_H']) and canvas.texts
