"""The flat look's own pages (pages.own painter flat, Modern's): Quick Settings' page, buttons and brightness bar,
patch/flat.c's colours and bar, and its images through the image hook. Classic's are test_nav_paint's."""
import calendar
import ctypes as C
import struct

import pytest

from conftest import O, Config
from test_nav_paint import (ACCENTS, GRAPHITE, ICONS, LABELS, MINIMAL, MODERN, PAPER, RED, RED_PX, TIDAL, WHITE, Canvas, Quick, bitmaps,
                            channels, fill_props, image_add, layout, luma, paint_bg, quick_window, rgb_of, rgba, scheme, scrub,
                            SLIDER_PAGES, SLIDERS, USB_LUN, Files, added, count, inside, msg_paint, named, poll,
                            whole, on_page, slider_scrub, slider_paint, ui_load,
                            st, stock_h, theme, vol_paint, volume, world)  # noqa: F401 (fixtures, by name)
from test_nav_painters import Quick as QuickInit
import test_nav_loop as loop
from test_nav_loop import SELECTOR_INDEX, keys, stock_bytes  # noqa: F401 (fixtures, by name)
from test_nav_display_library import POINTER_MOVE, SLIDE_PX, QuickPage, Stock, Ui, pressdown
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
    assert fw.fn('flat_on', C.c_uint)() == (flip(fw, want) if light == '1' else want)


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
    lit = fw.fn('flat_on', C.c_uint)()
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
                                         ('Sys 设置', ['Sys', '设置']), ('x' * 40, ['x' * 8, 'x' * 31])])
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
    on = rgba(fw.fn('flat_on', C.c_uint)())
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


@pytest.mark.parametrize('value, light, accent', [(50, '1', TIDAL), (50, '0', TIDAL), (90, '0', TIDAL), (100, '0', TIDAL), (50, '0', GRAPHITE),
                                                  (100, '1', GRAPHITE)])
def test_the_bar_s_suns_take_the_ink_that_shows(fw, world, value, light, accent):
    """The small one is on the fill: white, or dark on a bright one. The large one is quiet in the half until the
    fill reaches it, then the small one's ink."""
    canvas = flat_scrub(fw, world, value, LIGHT=light, ACCENT=str(accent))
    small = '_l' if bright(fw, fw.fn('flat_on', C.c_uint)()) else ''
    large = small if value == 100 else '_ql' if light == '1' else '_qd'
    assert [n for n, _ in canvas.arts] == ['flat_drop_dim' + small, 'flat_drop_bright' + large]
    _, rest = lit_of(fw, canvas)
    x, top = min(f[0] for f in rest), min(f[1] for f in rest)
    (_, dim), (_, bright_) = canvas.arts
    assert dim[:2] == (x + 6, top + 6) and bright_[:2] == (x + 309 - 33, top + 3)


@pytest.mark.parametrize('loud, quiet, light', [(1, 'spk_quiet', '0'), (0, 'spk_quiet', '1')])
def test_the_flat_bar_s_speakers(fw, world, loud, quiet, light):
    """flat_bar's other marks, the slider pages': the near speaker white on the fill, the far one in the half's ink
    until the fill reaches it."""
    theme(fw, MODERN, LIGHT=light, ACCENT=str(TIDAL))
    canvas = Canvas(fw)
    r = (C.c_int * 4)(10, 20, 300, 36)
    fw.fn('flat_bar', None, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_uint, C.c_int)(
        None, canvas.p, r, 300 if loud else 100, 0x2c2c2e, 2)
    far = 'spk_loud' if loud else 'spk_loud_ql' if light == '1' else 'spk_loud_qd'
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
    assert [n for n, _ in canvas.arts][0] == 'flat_spk_quiet' and not canvas.texts and not canvas.rounded
    assert canvas.fill == 0xff123456


@pytest.mark.parametrize('accent', [TIDAL, GRAPHITE])
def test_the_flat_volume_in_the_light_half_is_final(fw, world, st, accent):
    """The card white, the bar's rest the light half's grey and its fill what is on, as Quick Settings' own bar."""
    theme(fw, MODERN, ACCENT=str(accent), LIGHT='1')
    dialog, vol, label = volume(fw, world, st, 50, under='statusbar_dialog')
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    lit, rest = lit_of(fw, canvas)
    card = [f for f in rest if f[1] == 232 and f[2] > 1]  # a row of the card above the bar, its corners aside
    bar = [f for f in rest if f[1] == 239 + 18 and 33 <= f[0] < 342 and f[2] > 1]
    assert lit and {f[4] for f in card} == {rgba(0xffffff)} and {f[4] for f in bar} == {rgba(0xe9e9ec)}  # qs_card, qs_rest
    assert canvas.fill == 0xff123456


@pytest.mark.parametrize('light', ['0', '1'])
@pytest.mark.parametrize('level, maximum, part', [(0, 100, 0), (1, 100, 4), (50, 100, 113), (100, 100, 227), (40, 80, 113)])
def test_over_modern_s_other_windows_the_volume_is_the_flat_panel(fw, world, st, light, level, maximum, part):
    """The toast's box, as wide as Classic's panel, the same in both halves: a thin white bar on grey, its share
    level / maximum of the track (never less than its round ends) between the white speakers, and no number."""
    theme(fw, MODERN, ACCENT=str(TIDAL), LIGHT=light)
    dialog, vol, label = volume(fw, world, st, level, maximum)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    x, y, w, h = 28, O['VOL_PANEL_Y'], 375 - 56, O['VOL_PANEL_H']
    assert world.at(vol) == (x, y, w, h) and world[vol].visible == world[label].visible == 0
    assert canvas.rounded == [((x, y, w, h), 0xee2b2b2b, 12)]
    track = [f for f in canvas.fills if f[1] in range(y + h // 2 - 2, y + h // 2 + 2)]
    lit = [f for f in track if f[4] & 0xffffff == 0xffffff]  # with its round ends' softer columns
    assert {f[4] for f in track if f[2] > 4} - {rgba(0xffffff)} == {rgba(0x6b6b6b)}  # the corners aside
    assert (max(f[0] + f[2] for f in lit) - (x + 40) if lit else 0) == part
    assert [(n, r[:2]) for n, r in canvas.arts] == [('flat_spk_quiet', (x + 16, y + 17)), ('flat_spk_loud', (x + w - 38, y + 17))]
    assert not canvas.texts and canvas.fill == 0xff123456


# The slider pages (slider_open, slider_paint, slider_scrub) in the flat look.

SLIDER_LIGHT, SLIDER_MAX, SLIDER_BALANCE, SLIDER_BOOT = range(4)


def flat_slider(fw, world, st, kind, value=50, band=True, number=None, **config):
    """test_nav_paint's slider_page under Modern: (window, slider)."""
    theme(fw, MODERN, ACCENT=str(TIDAL), **config)
    win = world.window(SLIDER_PAGES[kind])
    view = world.add(win, 'view_slider', box=(0, 120, 375, 60))
    slider = world.add(view, SLIDERS[kind], 'slider', box=(81, 15, 204, 30), min=0, max=100, value=value)
    if number is not None:
        world.add(win, 'label_balance', 'label', text=number)
    ui_load(fw, st, SLIDER_PAGES[kind].encode(), win)
    world[view].visible = band
    return win, slider


@pytest.mark.parametrize('kind, ends', [(SLIDER_MAX, ('spk_quiet', 'spk_loud_qd')), (SLIDER_BOOT, ('spk_quiet', 'spk_loud_qd')),
                                        (SLIDER_LIGHT, ('drop_dim', 'drop_bright_qd')), (SLIDER_BALANCE, ())])
def test_each_flat_slider_page_s_bar_has_its_marks(fw, world, st, kind, ends):
    """In the bar's ends, the near one on the fill and the far one quiet (the dark half's, here); none on Balance.
    The page under it draws no image."""
    win, slider = flat_slider(fw, world, st, kind)
    canvas = slider_scrub(fw, slider)
    assert [n for n, _ in canvas.arts] == ['flat_' + e for e in ends]
    if ends:
        w = world.at(slider)[2]
        (_, low), (_, high) = canvas.arts
        assert 0 < low[0] < w / 2 < high[0] < w and min(low[1], high[1]) >= 17  # one in each end, on the bar's rows
    assert slider_paint(fw, win).arts == []


def test_a_flat_slider_page_s_caption_is_a_quiet_line_that_turns_with_the_half(fw, world, st):
    win, slider = flat_slider(fw, world, st, SLIDER_MAX)
    canvas = slider_paint(fw, win)
    caption = named(world, win, 'caption')
    props = world[caption].props
    assert canvas.texts == [] and len(canvas.fills) == 1  # the page alone: no shadow, the caption its own widget
    assert (props['style:normal:font_name'], props['_final_n']) == ('default', 0)  # not final: Light Mode's hook flips it
    r, g, b = channels(rgb_of(props['style:normal:text_color'] & 0xffffffff))
    assert max(r, g, b) - min(r, g, b) <= O['LIGHT_SPREAD'] and 0x60 < g < 0xc0  # a grey, which the hook flips
    assert on_page(world, caption)[1] >= sum(on_page(world, slider)[1::2])  # under the slider


@pytest.mark.parametrize('light', ['0', '1'])
def test_a_flat_slider_page_is_the_half_s_paper(fw, world, st, light):
    win, _ = flat_slider(fw, world, st, SLIDER_LIGHT, LIGHT=light)
    canvas = slider_paint(fw, win)
    assert canvas.fills == [(0, 0, 375, 294, rgba(fw.fn('flat_page', C.c_uint)()))] and canvas.fill == 0xff123456


def test_a_hidden_band_draws_no_flat_bar(fw, world, st):
    """Stock hides the slider's view while Default Volume is off: the slider never paints, and the page has none of it."""
    win, _ = flat_slider(fw, world, st, SLIDER_MAX, band=False)
    canvas = slider_paint(fw, win)
    assert canvas.arts == [] and len(canvas.fills) == 1 and world[named(world, win, 'caption')].visible  # the caption still


def test_the_flat_value_s_label_is_over_the_bar_and_turns_with_the_half(fw, world, st):
    win, slider = flat_slider(fw, world, st, SLIDER_BALANCE, number='')
    label = named(world, win, 'label_balance')
    world.set_text(label, 'L 3')
    slider_paint(fw, win)
    x, y, w, h = world.at(label)
    _, sy, _, sh = on_page(world, slider)
    assert (x, w) == (0, world.at(win)[2]) and y + h <= sy + (sh - 36) // 2 and world[label].visible  # over the bar
    assert world[label].props['_final_n'] == 0  # its ink turns with the half


@pytest.mark.parametrize('image, held', [('switch_on', 1), ('switch_off', 0)])
def test_default_volume_s_flat_switch_is_a_pill_with_the_half_that_holds_lit(fw, world, st, image, held):
    win, _ = flat_slider(fw, world, st, SLIDER_BOOT)
    world.add(win, 'img_bootswitch', 'image', image=image)
    canvas = slider_paint(fw, win)
    on = rgba(fw.fn('flat_on', C.c_uint)())
    lit = [f for f in canvas.fills if f[4] == on]
    assert min(f[0] for f in lit) == (188 if held else 128) and max(f[0] + f[2] for f in lit) == (248 if held else 188)
    assert [(t, r, face) for t, r, _, face in canvas.texts] == [('Off', (128, 32, 60, 36), ('bold', 19)), ('On', (188, 32, 60, 36), ('bold', 19))]
    assert canvas.arts == []


@pytest.mark.parametrize('value', [0, 50, 100])
def test_the_flat_slider_s_bar_is_quick_settings_bar_on_its_own_rows(fw, world, st, value):
    """The slider's width, 36 rows in the middle of its touch; its fill never less than its round end."""
    _, slider = flat_slider(fw, world, st, SLIDER_BALANCE, value)
    canvas = slider_scrub(fw, slider)
    w, h = world.at(slider)[2:]
    lit, rest = lit_of(fw, canvas)
    x, rows = min(f[0] for f in rest), {f[1] for f in rest}
    assert (x, max(f[0] + f[2] for f in rest) - x, w) == (0, 309, 309) and len(rows) == 36 and 2 * min(rows) + 36 == h
    assert max(f[0] + f[2] for f in lit) == 36 + (w - 36) * value // 100 and {f[1] for f in lit} == rows
    assert canvas.fill == 0xff123456


@pytest.mark.parametrize('start, counts', [(226, 1), (240, 1), (225, 0)])
def test_the_flat_swipe_up_starts_at_the_bar_s_card(fw, st, start, counts):
    """The flat bar's touch starts at 233, above Classic's line: a swipe from its card counts, as Classic's from its."""
    ui = Ui(fw)
    Config(fw, THEME='2')
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    pressdown(fw).value = start
    stock.send(qs.dialog, POINTER_MOVE, x=100, y=start - SLIDE_PX)
    assert pressdown(fw).value == (O['QS_SWIPE_STOCK'] if counts else start)


@pytest.mark.parametrize('part, lit_w', [(0, 0), (10, 36), (36, 36), (200, 200)])
def test_the_flat_bar_s_fill_is_none_or_at_least_its_round_end(fw, part, lit_w):
    """Nothing lit at 0, and less than the round end is the round end: no corner's pixels inside the bar or past it."""
    theme(fw, MODERN, ACCENT=str(TIDAL))
    canvas = Canvas(fw)
    r = (C.c_int * 4)(10, 20, 300, 36)
    fw.fn('flat_bar', None, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_uint, C.c_int)(None, canvas.p, r, part, 0x2c2c2e, 0)
    lit, rest = lit_of(fw, canvas)
    assert all(10 <= f[0] and f[0] + f[2] <= 310 for f in canvas.fills)
    assert (max(f[0] + f[2] for f in lit) - 10 if lit else 0) == lit_w and (not lit or min(f[0] for f in lit) >= 10)


class Rect(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('w', C.c_int), ('h', C.c_int)]


@pytest.mark.parametrize('accent', [a for a in MODERN_ACCENTS if a != GRAPHITE])
def test_what_is_on_is_the_bar_paint_selection_draws(fw, layout, accent):
    """The lit colour against the bar itself: paint_selection's gradient of the live scheme's tones, its middle
    row, so the bar and what is lit can't drift apart."""
    theme(fw, MODERN, ACCENT=str(accent))
    row = C.c_void_p.from_address(fw.fn('scheme', C.c_void_p)() + layout['scheme']['tones']).value  # its ACCENTS row
    tones = (C.c_uint * 5).from_address(row or 0)
    canvas = Canvas(fw)
    fw.fn('navigation__gradient', None, C.c_void_p, Rect, C.c_uint, C.c_uint, C.c_uint, C.c_int)(
        canvas.p, Rect(0, 0, 375, 49), tones[0], tones[1], tones[4], 0)
    assert canvas.rows(x=0, w=375)[24] == rgba(fw.fn('flat_on', C.c_uint)())


@pytest.mark.parametrize('light', ['0', '1'])
def test_classic_s_status_bar_keeps_its_images_final_under_modern(fw, bitmaps, light):
    """Status Bar: Classic under Modern: the bar's theme is art_final under its own prefix, the flat look's aside."""
    theme(fw, MODERN, LIGHT=light, STATUSBAR='3')
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, b'classic_light_bolt', bm)
    assert bm.rows() == [[RED_PX, WHITE]]


# The message screens (msg_open, msg_paint) in the flat look: Update Library's dialog and USB mode's page.

def flat_scan(fw, world, st, text='Scanned 0', **config):
    """test_nav_paint's scan_dialog under Modern: (dialog, the count's threads)."""
    theme(fw, MODERN, ACCENT=str(TIDAL), **config)
    win = world.window('updatemusic_dialog', 'dialog')
    for name in ('img_animation', 'label_scanfolder'):
        world.add(win, name, 'image')
    world.add(win, 'label_msg', 'label', text=text)
    world.add(win, 'btn_no', 'button', box=(100, 250, 175, 40), text='Stop')
    threads = []
    fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: threads.append(fn) or 0)
    ui_load(fw, st, b'updatemusic_dialog', win)
    return win, threads


def test_the_flat_scan_dialog_is_a_heading_a_line_and_cancel_s_pill(fw, world, st):
    win, threads = flat_scan(fw, world, st)
    heading, line, btn = (named(world, win, n) for n in ('msg_heading', 'msg_line', 'btn_no'))
    assert world[heading].text and world.at(heading)[3] > world.at(line)[3] == 22
    assert world.at(btn) == (127, 248, 121, 36) and world[btn].props['style:normal:font_name'] == 'bold'
    assert (world[heading].props['style:normal:font_name'], world[heading].props['style:normal:font_size']) == ('bold', 27)
    canvas = msg_paint(fw, win)
    rest = rgba(0x2c2c2e)
    pill = [f for f in canvas.fills if f[4] == rest and 248 <= f[1] < 284]
    assert {f[1] for f in pill} == set(range(248, 284)) and canvas.texts == []
    assert [n for n, _ in canvas.arts if n != 'widget_paint'] == ['flat_msg_note_d'] and len(threads) == 1


def test_the_flat_scan_s_bar_is_thin_and_fills_to_its_part(fw, world, st, whole):
    whole.value = 1
    win, threads = flat_scan(fw, world, st, 'Scanned 352')
    before = msg_paint(fw, win)
    count(fw, threads, 704)
    assert poll(world) == 8 and world[named(world, win, 'msg_line')].text == '352 of 704 songs'
    (target, box), = world.invalid
    bar = added(msg_paint(fw, win), before)
    on = rgba(fw.fn('flat_on', C.c_uint)())
    lit = [f for f in bar if f[4] == on]
    assert {f[1] for f in bar} == set(range(222, 226)) and min(f[0] for f in bar) == 88 and max(f[0] + f[2] for f in bar) == 288
    assert abs(max(f[0] + f[2] for f in lit) - (88 + 200 * 352 // 704)) <= 1  # its round end's soft pixel aside
    assert target == win and all(inside(f[:4], box) for f in bar) and inside(world.at(named(world, win, 'msg_line')), box)


def flat_usb(fw, world, st, **config):
    """test_nav_paint's usb_page under Modern, with its config."""
    theme(fw, MODERN, **config)
    fw.var('g_usbmode', C.c_ubyte).value = 0
    win = world.window('usbmode_page')
    world.add(win, 'img_usbmode', 'image')
    world.add(win, 'scrlabel_info', 'hscroll_label', box=(20, 200, 335, 30), text='USB Connected')
    ui_load(fw, st, b'usbmode_page', win)
    return win


@pytest.mark.parametrize('light, ink, quiet', [('0', 0xffffff, 0x98989d), ('1', 0x1c1c1e, 0x6e6e73)])
def test_a_flat_message_page_is_the_half_s_paper_under_its_ink(fw, world, st, light, ink, quiet):
    win = flat_usb(fw, world, st, LIGHT=light)
    canvas = msg_paint(fw, win)
    final = lambda n: world[named(world, win, n)].props['_final'] & 0xffffffff
    assert canvas.fills[0][4] == rgba(fw.fn('flat_page', C.c_uint)()) and canvas.art(f'flat_msg_usb_{"l" if light == "1" else "d"}')
    assert (final('msg_heading'), final('msg_line')) == (0xff000000 | ink, 0xff000000 | quiet) and canvas.texts == []


def test_modern_s_usb_page_says_connected_eject_and_then_ok_to_disconnect(fw, world, st):
    """Modern takes the own pages' words: Connected over Eject before disconnecting, stock's line hidden; the eject
    turns the heading to OK to Disconnect and empties the line, as Classic's."""
    files = Files(fw, {USB_LUN: b'/dev/mmcblk0p1\n'})
    win = flat_usb(fw, world, st)
    text = lambda n: world[named(world, win, n)].text
    assert (text('msg_heading'), text('msg_line')) == ('Connected', 'Eject before disconnecting')
    assert world[named(world, win, 'scrlabel_info')].visible == 0
    assert poll(world) == 8
    files.files[USB_LUN] = b''
    assert poll(world) == 7 and (text('msg_heading'), text('msg_line')) == ('OK to Disconnect', '')


# Set Date and Set Time (time_open, time_paint) in the flat look, on test_nav_loop's selectors.

class TimeCanvas(loop.Canvas):
    """test_nav_loop's canvas, its rounded boxes kept: (x, y, w, h, colour)."""
    def __init__(self, fw):
        super().__init__(fw)
        self.fills = []
        fw.mock('canvas_fill_rounded_rect', self.box)

    def box(self, c, r, bg, color, radius, *_):
        self.drawn.append('canvas_fill_rounded_rect')
        self.fills.append((*struct.unpack_from('<4i', C.string_at(r, 16)), C.c_uint.from_address(color).value))
        return 0

    def over(self, w):
        """The colour of the last rounded box over the middle of w's box."""
        x, y, ww, h = w.box()
        return [c for fx, fy, fw_, fh, c in self.fills if fx <= x + ww // 2 < fx + fw_ and fy <= y + h // 2 < fy + fh][-1]


@pytest.mark.parametrize('edited', range(len(loop.DATE)))
def test_modern_paints_the_date_page_flat(fw, keys, edited):
    loop.Config(fw, THEME=b'2')
    top, _ = loop.open_time(fw, loop.Ui(fw), at=(4, 2, 4, 21, 5))
    top.ints[b'_edit_field'] = edited
    canvas = TimeCanvas(fw)
    loop.time_paint(fw, top, canvas)
    cells = [top.find(n) for n in loop.DATE]
    assert [[int(t) for t in loop.in_box(canvas, w)] for w in cells] == [[w.int(SELECTOR_INDEX) + first] for w, first in zip(cells, loop.DATE.values())]
    assert calendar.month_abbr[loop.held(top)[0]] in [t for t, _ in canvas.texts]  # the tile's month
    faces = [canvas.over(w) for w in cells]
    assert len(set(faces)) == 2 and faces.count(faces[edited]) == 1  # the edited field's cell alone holds
    assert canvas.art == [] and canvas.fill() == 0xff123456  # the tile and the cells are boxes, not images


@pytest.mark.parametrize('twelve, hour, shown', [(True, 21, ['9', '05', 'PM']), (False, 21, ['21', '05'])])
def test_modern_paints_the_time_page_flat(fw, keys, twelve, hour, shown):
    loop.Config(fw, THEME=b'2')
    top, _ = loop.open_time(fw, loop.Ui(fw), twelve=twelve, at=(4, 2, 4, hour, 5))
    top.find(b'slide_view').ints[b'value'] = 1
    top.ints[b'_edit_field'] = 3
    canvas = TimeCanvas(fw)
    loop.time_paint(fw, top, canvas)
    boxes = [top.find(b'tselector_hour'), top.find(b'tselector_min')] + [w for w in top.find(b'time').children if w.kind == b'image']
    assert [t for w in boxes for t in loop.in_box(canvas, w)] == shown
    ok = top.find(b'btn_enter')
    assert ok.box() == (127, 224, 121, 36) and loop.in_box(canvas, ok) == [ok.utf8.decode()]  # Save's pill, its touch
    faces = [canvas.over(w) for w in boxes]
    assert ':' in [t for t, _ in canvas.texts] and len(set(faces)) == 2 and faces.count(faces[0]) == 1  # the hour holds
    assert canvas.art == ['flat_clock_face_d'] and canvas.over(ok) == faces[1]  # the dark half's face; Save at rest


def test_modern_s_date_page_in_the_light_half_is_final(fw, keys):
    """The boxes in their final colours, Light Mode's ink held off them (flat_box): the tile and Save at the light
    half's rest, the field being set in what is on, as Quick Settings has them."""
    loop.Config(fw, THEME=b'2', LIGHT=b'1')
    top, _ = loop.open_time(fw, loop.Ui(fw), at=(4, 2, 4, 21, 5))
    top.ints[b'_edit_field'] = 1
    canvas = TimeCanvas(fw)
    loop.time_paint(fw, top, canvas)
    on, rest = fw.fn('flat_on', C.c_uint)(), fw.var('flat_rest', C.c_uint * 2)[1]
    final = rgba
    assert canvas.over(top.find(b'tselector_month')) == final(on)  # the field being set
    assert canvas.over(top.find(b'tselector_year')) == canvas.over(top.find(b'btn_enter')) == final(rest)  # Save at rest
    assert canvas.fills[0][4] == final(rest) and canvas.fills[0][:2] != top.find(b'btn_enter').box()[:2]  # the tile first


@pytest.mark.parametrize('twelve, last', [(True, 5), (False, 4)])
def test_modern_set_time_walks_its_fields_then_saves(fw, keys, twelve, last):
    """Classic's model under Modern: Centre walks every field, AM/PM the sixth in 12-hour, and the next Centre saves,
    with no stop on OK; the side buttons stop at the last field."""
    loop.Config(fw, THEME=b'2')
    ui = loop.Ui(fw)
    top, _ = loop.open_time(fw, ui, twelve=twelve)
    assert fw.fn('fields', C.c_int, C.c_void_p)(top.p) == last + 1
    seen = []
    for _ in range(last):
        loop.centre(ui, keys)
        seen.append(loop.field(top))
    assert seen == list(range(1, last + 1)) and ui.clicks == []
    loop.centre(ui, keys)
    assert ui.clicks == [(b'btn_enter', loop.EVT_CLICK)]
    top, _ = loop.open_time(fw, loop.Ui(fw), twelve=twelve)
    for _ in range(8):
        keys(loop.KEY_FWD_BTN, 10)
    assert loop.field(top) == last


# Home's pane where no cover shows (coverflow.c home_pane_paint), Modern's: flat, the note and the queue's count.

def modern_home(fw, **keys):
    import test_coverflow as cf
    _, ui, h, home = cf.home_open(fw, cf.MODERN, **keys)
    return cf, ui, h, home


@pytest.mark.parametrize('light, pane, note, quiet', [('0', 0x1c1c1c, 'flat_pane_note', 0x98989d),
                                                      ('1', 0xf2f2f7, 'flat_pane_note_l', 0x6e6e73)])
def test_modern_s_pane_is_flat_with_the_half_s_note_and_a_quiet_count(fw, mcl, light, pane, note, quiet):
    cf, ui, h, home = modern_home(fw, LIGHT=light)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_void_p).value = cf.Deques(fw).new(range(1, 4))
    home.kind = cf.PANE_NOW
    bitmaps, canvas = cf.Bitmaps(fw, {note: (100, 115, cf.rgba(1, 1, lambda x, y: (0, 0, 0, 0)))}), cf.Canvas(fw)
    inks = []
    fw.mock('canvas_set_text_color', lambda c, colour, *_: inks.append(colour & 0xffffffff))
    fw.fn('home_pane_paint', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.p, 5, -3, cf.HOME_PW, 294)
    assert {r[0] for r in canvas.rects} == {rgba(pane)}  # one colour: no light across it, no shadow
    assert bitmaps.loads == [note] and [i[2] for i in canvas.images] == [(5 + 40, -3 + 57, 100, 115)]
    assert canvas.texts == [('3 首', (5, -3 + 185, 182, 24), 17)] and canvas.fonts[-1][0] == ''  # the system's face
    assert rgba(quiet) in inks and canvas.fills[-1] == 0x12345678


def test_modern_s_pane_stays_now_playing_s_on_every_row(fw):
    """icon_x 0: the highlighted row doesn't change the pane."""
    cf, ui, h, home = modern_home(fw)
    home.kind = cf.PANE_NOW
    for name in cf.Homes.ROWS:
        assert cf.home_row(fw, h.view, h.rows[name]) == cf.PANE_NOW


@pytest.mark.parametrize('mode, shown', [('0', 1), ('1', 0), ('2', 0)])
def test_only_split_shows_modern_s_pane(fw, mode, shown):
    """Split's art, the pane where no cover shows, is hidden in Full and Carousel."""
    cf, ui, h, home = modern_home(fw, HOME=mode)
    assert ui[h.art].visible == shown


# Classic Modern's pane: Classic's, which follows the rows, in the flat look's colours, icons and caption.

PANE_ICON = ['note', 'note', 'note', 'icon_stack', 'icon_folder', 'icon_game', 'icon_game', 'icon_wave', 'icon_gear']  # pane_icons


@pytest.mark.parametrize('light, pane, k, quiet', [('0', 0x1c1c1c, '', 0x98989d), ('1', 0xf2f2f7, '_l', 0x6e6e73)])
def test_classic_modern_s_pane_is_flat_each_row_s_icon_the_half_s(fw, mcl, light, pane, k, quiet):
    """Each kind's icon flat at Classic's places (the note's for Now Playing's, Library's and Shuffle's), the pane one
    colour, and the count quiet at 17 px in the system's face."""
    import test_coverflow as cf
    t, ui, h, home = cf.home_open(fw, 3, LIGHT=light)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_void_p).value = cf.Deques(fw).new(range(1, 4))
    for kind, name in enumerate(PANE_ICON):
        art = f'flat_pane_{name}{k}'
        bitmaps, canvas = cf.Bitmaps(fw, {art: (100, 115, cf.rgba(1, 1, lambda x, y: (0, 0, 0, 0)))}), cf.Canvas(fw)
        inks = []
        fw.mock('canvas_set_text_color', lambda c, colour, *_: inks.append(colour & 0xffffffff))
        home.kind = kind
        fw.fn('home_pane_paint', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.p, 5, -3, cf.HOME_PW, 294)
        note = kind <= cf.PANE_SHUFFLE
        at = (t['pane_note_x'], t['pane_note_y']) if note else (t['pane_icon_x'], t['pane_icon_y'])
        assert {r[0] for r in canvas.rects} == {rgba(pane)} and bitmaps.loads == [art], kind
        assert [i[2] for i in canvas.images] == [(5 + at[0], -3 + at[1], 100, 115)], kind
        assert all(px == 17 for _, _, px in canvas.texts) and canvas.fills[-1] == 0x12345678
        if kind == cf.PANE_NOW:
            assert canvas.texts == [('3 首', (5, -3 + t['pane_count_y'], t['pane_count_w'], 24), 17)]
            assert canvas.fonts[-1][0] == '' and rgba(quiet) in inks


@pytest.mark.parametrize('light, k', [('0', ''), ('1', '_l')])
def test_classic_modern_s_service_pane_keeps_its_icon_and_names_it_quiet(fw, light, k):
    import test_coverflow as cf
    t, ui, h, home = cf.home_open(fw, 3, LIGHT=light)
    row = ui.make(0, 'view')
    image, label = ui.make(row, 'image'), ui.make(row, 'hscroll_label')
    ui[image].image, ui[label].text = 'stream_radio', 'Internet Radio Stations'
    home.kind, home.row, home.row_image, home.row_label = cf.PANE_SERVICE, row, image, label
    side = t['pane_svc_px']
    bitmaps = cf.Bitmaps(fw, {'classic_light_stream_radio': (side, side, cf.rgba(1, 1, lambda x, y: (0, 0, 0, 0)))})
    canvas = cf.Canvas(fw)
    fw.fn('home_pane_paint', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.p, 5, -3, cf.HOME_PW, 294)
    assert bitmaps.loads == ['classic_light_stream_radio']  # the service's own icon, as Classic's
    (text, box, px), = canvas.texts
    assert box == (5, -3 + t['pane_count_y'], t['pane_count_w'], 24) and 17 * 3 // 4 <= px <= 17 and text.endswith('…')
    assert all(face == '' for face, _ in canvas.fonts[1:])  # the system's face


@pytest.mark.parametrize('index, light, fill', [(3, 0, 0x1c1c1c), (3, 1, 0x63717b), (1, 1, 0x63717b), (2, 1, 0xf2f2f7)],
                         ids=['Classic Modern dark', 'Classic Modern light', 'Classic light', 'Modern light'])
def test_the_lock_screen_s_page_keeps_classic_s_colours(fw, index, light, fill):
    """paint_page (the lock screen's info page) in pages.own.fill, Classic's slate under Classic Modern though its
    pane is the flat grey; a theme without it fills with its pane's colour, as Modern's."""
    import test_coverflow as cf
    cf.themed(fw, index, LIGHT=str(light))
    canvas = cf.Canvas(fw)
    fw.fn('paint_page', None, C.c_void_p, C.c_void_p, C.c_int)(None, canvas.p, 26)
    assert [r[0] for r in canvas.rects] == [rgba(fill)]
