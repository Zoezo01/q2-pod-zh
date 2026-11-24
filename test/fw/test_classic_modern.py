"""Classic Modern (THEME 3): Classic, but for the flat look's screens over its own pages (pages.own and dialog.confirm
painter classic-light+flat), in Classic's lit colour and face, Quick Settings on its pale grey and Set Date's fields
6 px apart. Modern's flat screens are test_flat_pages' and test_flat_dialogs'; Classic's test_nav_paint's."""
import ctypes as C

import pytest

from conftest import Config, rgba
from test_nav_paint import (ACCENTS, CLASSIC, CLASSIC_BLUE, GRAPHITE, MODERN, RED, RED_PX, TIDAL, WHITE, bitmaps,
                            image_add, msg_paint, named, slider_paint, slider_scrub, SLIDER_PAGES, SLIDERS,
                            layout, st, stock_h, theme, ui_load, world)  # noqa: F401 (fixtures, by name)
from test_flat_pages import TimeCanvas, face, flip
from test_nav_paint import quick_page as paint_quick_page
import test_flat_pages as flat_pages
import test_nav_loop as loop
from test_nav_loop import keys, stock_bytes  # noqa: F401 (fixtures, by name)
import test_nav_painters as painters
CLASSIC_MODERN = '3'
LIGHTS = pytest.mark.parametrize('light', ['0', '1'])


def flat_on(fw):
    return fw.fn('flat_on', C.c_uint)()


def red(fw, accent, light):
    """Classic's lit tile's colour for an accent of the nine: its red tone, flipped in the light half."""
    return flip(fw, ACCENTS[accent][RED]) if light == '1' else ACCENTS[accent][RED]


@LIGHTS
@pytest.mark.parametrize('accent', [CLASSIC_BLUE, TIDAL, GRAPHITE])
def test_what_is_on_is_classic_s_lit_tile(fw, light, accent):
    """Classic Blue's own #207cde in both halves, another accent's red tone, as Classic's Quick Settings lights its
    tiles."""
    theme(fw, CLASSIC_MODERN, LIGHT=light, CLASSIC_LIGHT_ACCENT=str(accent))
    assert flat_on(fw) == (0x207cde if accent == CLASSIC_BLUE else red(fw, accent, light))


@pytest.mark.parametrize('number, face', [(CLASSIC_MODERN, 'classic-light'), (MODERN, 'bold')])
def test_the_flat_look_s_words_are_in_the_theme_s_face(fw, number, face):
    theme(fw, number)
    assert C.string_at(fw.fn('flat_face', C.c_void_p)()).decode() == face


@pytest.mark.parametrize('number, own', [(CLASSIC_MODERN, 1), (CLASSIC, 1), (MODERN, 0)])
def test_classic_modern_keeps_classic_s_other_pages_and_dialogs(fw, number, own):
    """Its QR page, lock screen, ratings, T9, flip and toast are Classic's (classic_pages, classic_dialogs), the
    flip's panel and the hold menu the flat card, and a card game is told Classic's pop-up."""
    theme(fw, number)
    assert fw.fn('addon__qr_light', C.c_int)() == own
    popup = C.create_string_buffer(512)
    fw.fn('popup_env', None, C.c_char_p, C.c_uint)(popup, 512)
    assert popup.value.split()[0] == (b'classic' if own else b'modern')


@LIGHTS
def test_cover_flow_s_flip_panel_is_the_hold_menu_s_card(fw, light):
    """coverflow.c panel_paint with flip_open's card (six 32 px rows): the panel on the shelf's ground, the card a
    pixel in, its paper, the line under the 57 px header and the ring, as the hold menu's."""
    theme(fw, CLASSIC_MODERN, LIGHT=light)
    card = fw.var('fl', loop.Flip).card = 56 + 6 * 32 + 10
    canvas = painters.Canvas(fw)
    for over in (0, 1):
        fw.fn('coverflow__panel_paint', None, C.c_void_p, C.c_int)(canvas.addr, over)
    paper, line = fw.fn('flat_page', C.c_uint)(), 0xd1d1d6 if light == '1' else 0x38383a
    assert canvas.rects[0] == ((0, 0, 294, 285), paper | 0xff000000)  # the ground
    assert canvas.row(30, 1, 293) == [rgba(paper)] * 292 and canvas.row(56, 0, 294) == [rgba(line)] * 294
    for y in (0, card + 1):  # the ring: inside the panel, so the turn carries it
        assert canvas.row(y, 12, 282) == [rgba(line)] * 270
    assert canvas.row(card + 2, 0, 294) == [paper | 0xff000000] * 294


def quick_page(fw, world, images, light='0', **config):
    """test_flat_pages' quick_page under Classic Modern."""
    return paint_quick_page(fw, world, images, light, number=CLASSIC_MODERN, size=54, **config)


@pytest.mark.parametrize('light, ground', [('0', 0), ('1', 0xf2f2f7)])
def test_quick_settings_cards_are_on_the_theme_s_ground(fw, world, light, ground):
    """The light half's pale grey (pages.quick light_ground) under the white cards, not Classic's white paper; black
    in the dark half."""
    win, _, canvas = quick_page(fw, world, {}, light=light)
    assert canvas.fills[0] == (0, 0, *world.at(win)[2:], rgba(ground))
    cards = [f[4] for f in canvas.fills[1:]]
    assert max(set(cards), key=cards.count) == rgba((0x1c1c1e, 0xffffff)[int(light)])  # the cards, their corners aside
    assert fw.fn('flat_page', C.c_uint)() == (0, 0xffffff)[int(light)]  # the other pages' paper stays Classic's


@LIGHTS
def test_an_on_quick_button_is_classic_blue_s_under_its_flat_glyph(fw, world, stock_h, light):
    on = stock_h['QS_LIT'][0]
    _, icons, canvas = quick_page(fw, world, {'img_wifiswitch': on}, light=light, CLASSIC_LIGHT_ACCENT=str(CLASSIC_BLUE))
    assert face(canvas, world.at(icons['img_wifiswitch'])) == rgba(0x207cde)
    assert canvas.art(f'flat_{on}') and not [n for n, _ in canvas.arts if n.startswith('classic_light_')]


def slider(fw, world, st, kind, light='0'):
    """test_flat_pages' flat_slider under Classic Modern, Tidal: (window, slider)."""
    theme(fw, CLASSIC_MODERN, LIGHT=light, CLASSIC_LIGHT_ACCENT=str(TIDAL))
    win = world.window(SLIDER_PAGES[kind])
    view = world.add(win, 'view_slider', box=(0, 120, 375, 60))
    s = world.add(view, SLIDERS[kind], 'slider', box=(81, 15, 204, 30), min=0, max=100, value=50)
    world.add(win, 'label_balance', 'label', text='')
    ui_load(fw, st, SLIDER_PAGES[kind].encode(), win)
    return win, s


@LIGHTS
def test_default_volume_s_switch_is_in_classic_s_face_on_its_paper(fw, world, st, light):
    win, _ = slider(fw, world, st, flat_pages.SLIDER_BOOT, light)
    world.add(win, 'img_bootswitch', 'image', image='switch_on')
    canvas = slider_paint(fw, win)
    assert canvas.fills[0] == (0, 0, 375, 294, rgba((0, 0xffffff)[int(light)]))  # Classic's paper
    assert [(t, f) for t, _, _, f in canvas.texts] == [('Off', ('classic-light', 19)), ('On', ('classic-light', 19))]
    assert [f[0] for f in canvas.fills if f[4] == rgba(red(fw, TIDAL, light))] and canvas.arts == []  # On lit, Classic's


def test_balance_s_l_and_r_are_in_classic_s_face(fw, world, st):
    win, _ = slider(fw, world, st, flat_pages.SLIDER_BALANCE)
    ends = [w for w in world[win].children if world[w].text in ('L', 'R')]
    assert len(ends) == 2 and {world[w].props['style:normal:font_name'] for w in ends} == {'classic-light'}


@LIGHTS
def test_the_slider_s_bar_is_lit_in_classic_s_colour(fw, world, st, light):
    _, s = slider(fw, world, st, flat_pages.SLIDER_LIGHT, light)
    canvas = slider_scrub(fw, s)
    assert rgba(red(fw, TIDAL, light)) in {f[4] for f in canvas.fills}
    assert [n for n, _ in canvas.arts] == ['flat_drop_dim', 'flat_drop_bright' + ('_ql' if light == '1' else '_qd')]


def test_update_library_s_words_are_in_classic_s_face(fw, world, st):
    theme(fw, CLASSIC_MODERN)
    win = world.window('updatemusic_dialog', 'dialog')
    for name in ('img_animation', 'label_scanfolder'):
        world.add(win, name, 'image')
    world.add(win, 'label_msg', 'label', text='Scanned 0')
    world.add(win, 'btn_no', 'button', box=(100, 250, 175, 40), text='Stop')
    fw.mock('fw_pthread_create', lambda *_: 0)
    ui_load(fw, st, b'updatemusic_dialog', win)
    heading, btn = (world[named(world, win, n)].props for n in ('msg_heading', 'btn_no'))
    assert (heading['style:normal:font_name'], heading['style:normal:font_size']) == ('classic-light', 27)
    assert (btn['style:normal:font_name'], btn['style:normal:font_size']) == ('classic-light', 21)
    assert [n for n, _ in msg_paint(fw, win).arts if n != 'widget_paint'] == ['flat_msg_note_d']


@pytest.mark.parametrize('number, xs', [(CLASSIC_MODERN, [182, 248, 299]), (MODERN, [186, 250, 299])])
def test_set_date_s_fields_are_6_px_apart(fw, keys, number, xs):
    """Classic Modern's date.gap: the day where Classic's is, the month and the year 6 px apart; Modern keeps Classic's
    places. The selectors, the touch, are where the boxes are drawn."""
    Config(fw, THEME=number.encode())
    top, _ = loop.open_time(fw, loop.Ui(fw), at=(4, 2, 4, 21, 5))
    cells = [top.find(n) for n in loop.DATE]
    assert [w.box()[0] for w in cells] == xs
    canvas = TimeCanvas(fw)
    loop.time_paint(fw, top, canvas)
    assert [canvas.over(w) for w in cells] and all(any(f[:2] == w.box()[:2] for f in canvas.fills) for w in cells)


@LIGHTS
def test_set_time_s_fields_keep_classic_s_places_and_the_flat_face(fw, keys, light):
    Config(fw, THEME=CLASSIC_MODERN.encode(), LIGHT=light.encode())
    top, _ = loop.open_time(fw, loop.Ui(fw), twelve=True, at=(4, 2, 4, 21, 5))
    top.find(b'slide_view').ints[b'value'] = 1
    top.ints[b'_edit_field'] = 3
    canvas = TimeCanvas(fw)
    loop.time_paint(fw, top, canvas)
    assert [top.find(n).box()[0] for n in (b'tselector_hour', b'tselector_min')] == [186, 243]
    assert canvas.art == ['flat_clock_face_' + 'dl'[int(light)]] and top.find(b'btn_enter').box() == (127, 224, 121, 36)
    assert canvas.over(top.find(b'tselector_hour')) == rgba(flat_on(fw)) == rgba(0x207cde)  # Classic Blue, its preset


@LIGHTS
def test_a_prompt_s_cell_that_holds_is_classic_blue_s(fw, st, light):
    painters.theme(fw, CLASSIC_MODERN, LIGHT=light.encode())
    st('geom').value = painters.CLASSIC_SET
    painters.g(fw, 'g_backlight_status', 1)
    painters.g(fw, 'g_usblink_status', 0)
    confirm = painters.Confirm(fw, painters.Ui(fw))
    canvas = confirm.paint()
    x, y, w, h = confirm.cancel.box
    assert (y, h) == (250, 36) and canvas.px[x + w // 2, y + h // 2] == rgba(0x207cde)  # the flat look's cell, Classic's blue
    assert canvas.images == []


@LIGHTS
@pytest.mark.parametrize('name', [b'classic_light_bolt', b'flat_drop_wifi_l', b'flat_pane_icon_folder_l', b'flat_np_more_d'])
def test_classic_s_images_and_the_flat_look_s_are_final(fw, bitmaps, light, name):
    """Its bar and lists draw Classic's images, its flat screens the flat look's: both final, as each theme has them."""
    theme(fw, CLASSIC_MODERN, LIGHT=light)
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, name, bm)
    assert bm.rows() == [[RED_PX, WHITE]]


@LIGHTS
@pytest.mark.parametrize('value, far', [(50, None), (100, '_w')])
def test_the_volume_pages_speakers_are_classic_s(fw, world, st, light, value, far):
    """Classic's speakers (SPEAKER_INKS) in the bar's ends: the near one white on Tidal's fill, the far one the half's
    _l or _d until the fill reaches it; not the flat look's quiet greys."""
    theme(fw, CLASSIC_MODERN, LIGHT=light, CLASSIC_LIGHT_ACCENT=str(TIDAL))
    win = world.window(SLIDER_PAGES[flat_pages.SLIDER_MAX])
    view = world.add(win, 'view_slider', box=(0, 120, 375, 60))
    s = world.add(view, SLIDERS[flat_pages.SLIDER_MAX], 'slider', box=(81, 15, 204, 30), min=0, max=100, value=value)
    ui_load(fw, st, SLIDER_PAGES[flat_pages.SLIDER_MAX].encode(), win)
    canvas = slider_scrub(fw, s)
    assert [n for n, _ in canvas.arts] == ['classic_light_spk_quiet_w', 'classic_light_spk_loud' + (far or ('_d', '_l')[int(light)])]
