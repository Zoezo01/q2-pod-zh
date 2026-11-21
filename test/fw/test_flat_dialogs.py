"""The flat look's prompts (dialog.confirm painter flat, Modern's): the confirm's cells, track and words. Classic's are
test_nav_painters'. The hold menu a round card; the toast stays stock's under Modern."""
import ctypes as C

import pytest

import test_nav_hold as hold_
from test_nav_hold import recs, ui  # noqa: F401 (fixtures)
from test_nav_painters import MODERN, SETS, Canvas, Confirm, Scheme, Ui, g, layout, paint_bg, rgba, theme  # noqa: F401 (layout: a fixture)

MODERN_SET = next((i for i, s in enumerate(SETS) if s.name == 'modern'), 0)


def modern(fw, st, light=0, **config):
    """test_nav_painters' classic, under Modern."""
    theme(fw, MODERN, LIGHT=str(light).encode(), **config)
    st('geom').value = MODERN_SET
    for flag, value in (('g_backlight_status', 1), ('g_usblink_status', 0)):
        g(fw, flag, value)
    return Ui(fw)


def flat_on(fw):
    return fw.fn('flat_on', C.c_uint)()


def test_the_flat_confirm_s_cells_are_side_by_side_8_apart(fw, st):
    confirm = Confirm(fw, modern(fw, st))
    confirm.paint()
    (x0, y0, w0, h0), (x1, y1, w1, h1) = confirm.cancel.box, confirm.enter.box
    assert (y0, h0) == (y1, h1) == (250, 36) and x1 == x0 + w0 + 8  # the page form's row, a gap between
    assert abs((x0 + x1 + w1) - 375) <= 1 and w0 > 6 * 10 and w1 > 2 * 10  # centred, each wider than its text
    confirm.paint()
    assert (confirm.cancel.box, confirm.enter.box) == ((x0, y0, w0, h0), (x1, y1, w1, h1))  # dressed once


@pytest.mark.parametrize('light', [0, 1])
def test_the_flat_page_form_lays_paper_and_each_cell_its_own_pill(fw, st, layout, light):
    confirm = Confirm(fw, modern(fw, st, light))
    paper = rgba((0, Scheme(fw, layout).paper)[light])  # Modern's one row holds the light half's paper
    canvas = confirm.paint()
    x0, y0, w0, h0 = confirm.cancel.box
    x1, _, w1, _ = confirm.enter.box
    drawn = [x for x in range(375) if canvas.px.get((x, y0 + h0 // 2)) != paper]
    assert (min(drawn), max(drawn) + 1) == (x0, x1 + w1) and x0 + w0 not in drawn  # both cells, the paper between
    assert canvas.px[x0 + 1, y0] == paper and canvas.images == []  # a pill: its corner the paper's; no art
    assert canvas.restored()


@pytest.mark.parametrize('light', [0, 1])
def test_the_flat_cell_that_holds_is_flat_in_what_is_on(fw, st, light):
    confirm = Confirm(fw, modern(fw, st, light))
    canvas = confirm.paint()
    mid = lambda cell: canvas.px[cell.box[0] + cell.box[2] // 2, cell.box[1] + cell.box[3] // 2]
    edge = lambda cell: canvas.px[cell.box[0] + cell.box[2] // 2, cell.box[1] + 2]
    assert confirm.win.props['_ringnav_index'] == 0
    assert mid(confirm.cancel) == edge(confirm.cancel) == rgba(flat_on(fw)) != mid(confirm.enter)
    confirm.win.props['_ringnav_index'] = 1
    confirm.paint()
    assert mid(confirm.enter) == rgba(flat_on(fw)) != mid(confirm.cancel)


@pytest.mark.parametrize('light', [0, 1])
@pytest.mark.parametrize('accent', [b'1', b'4'])  # Crimson, Tidal: a dark and a bright fill
def test_the_flat_cells_words_have_no_shadow(fw, st, light, accent):
    """White on the held cell, dark on a bright fill; the half's ink on the other; final in either half."""
    confirm = Confirm(fw, modern(fw, st, light, ACCENT=accent))
    canvas = confirm.paint()
    final = rgba
    held = 0x1c1c1e if fw.fn('bright_tone', C.c_int, C.c_uint)(flat_on(fw)) else 0xffffff
    drawn = {t: (r, c) for t, r, c in canvas.texts}
    assert len(canvas.texts) == 2 and drawn['Cancel'][0] == confirm.cancel.box and drawn['OK'][0] == confirm.enter.box
    assert (drawn['Cancel'][1], drawn['OK'][1]) == (final(held), final((0xffffff, 0x1c1c1e)[light]))


@pytest.mark.parametrize('light', [0, 1])
def test_a_row_action_s_flat_tray_is_the_toast_s_box(fw, st, light):
    """The toast's dark box in either half, final (flat_box), its words white and final too."""
    ui = modern(fw, st, light)
    seen = {}

    def open_dialog(name, prompt, *_):
        confirm = Confirm(fw, ui, ['Delete "Song"?'])
        canvas = confirm.paint()
        seen['tray'] = (ui.text(confirm.enter), confirm.lines[0].visible, list(canvas.images), list(canvas.boxes),
                        [confirm.cancel.box, confirm.enter.box], {t: c for t, r, c in canvas.texts})
        return 1
    fw.mock('navigator_to_with_context', open_dialog)
    verb = C.create_string_buffer(b'Delete')
    assert fw.fn('playlists__confirm_over', C.c_int, C.c_void_p, C.c_char_p)(None, verb) == 1
    text, shown, images, boxes, cells, inks = seen['tray']
    assert text == 'Delete' and not shown and images == []  # the cell says what it's for; no question, no shadow
    ((bx, by, bw, bh), color, radius), = boxes
    assert color == 0xee2b2b2b and radius == 12 and 2 * bx + bw == 375  # navigation.h TOAST_RGBA, centred
    assert all(bx <= x and x + w <= bx + bw and by <= y and y + h <= by + bh for x, y, w, h in cells)
    held = 0x1c1c1e if fw.fn('bright_tone', C.c_int, C.c_uint)(flat_on(fw)) else 0xffffff  # Graphite's silver: dark
    assert (inks['Cancel'], inks['Delete']) == (rgba(held), rgba(0xffffff))  # the other cell white on the dark box


def modern_hold(fw, ui, st, recs, mcl, light=0):
    """Now Playing's hold menu under Modern: (the hold, its navbar's box, its list's box, the canvas after both paints)."""
    theme(fw, MODERN, LIGHT=str(light).encode())  # read before use_set marks the config read
    hold_.use_set(st, hold_.MINIMAL)  # Modern's geometry set, Minimal's
    st('theme').value = MODERN
    hold = hold_.Hold(fw, ui, st, recs, mcl).now_playing([hold_.song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    canvas = Canvas(fw)
    for paint in ('ringnav_paint_bg', 'ringnav_paint'):
        fw.fn(paint, C.c_int, C.c_void_p, C.c_void_p)(hold.dialog, canvas.addr)
    box = lambda name: ui.nodes[ui.lookup(hold.dialog, name, 1)].rect()
    return hold, box('view_navbar'), box('list_view'), canvas


def test_modern_s_hold_menu_is_a_round_card_its_rows_capped_by_the_glass(fw, ui, st, recs, mcl):
    """Cover Flow's panel's width, centred under the bar: a 44 px header, the theme's 48 px rows, as many as clear
    the glass's corners with HOLD_FOOT of paper under them (four), the rest scrolling, as Classic's past six."""
    hold, nav, lst, canvas = modern_hold(fw, ui, st, recs, mcl)
    rows = [ui.nodes[item].rect() for item in ui.kids(hold.view())]
    assert len(rows) == len(hold_.PLAYING_ROWS) > 4 and all((x, w, h) == (0, 294, 48) for x, _, w, h in rows)
    assert [y for _, y, _, _ in rows] == [48 * i for i in range(len(rows))]  # each a row of the scroll, none dropped
    assert nav[0::2] == (40, 294) and nav[3] == 44 and lst == (40, nav[1] + 44, 294, 4 * 48)
    foot = lst[1] + lst[3] + 10  # HOLD_FOOT
    assert foot <= 308  # its ring's last row, foot: the glass hides 38 px there (tools/ipod.py corner_inset), the ring is at 39
    paper = [c for (x, y), c in canvas.px.items() if 40 + 12 <= x < 334 - 12 and nav[1] <= y < foot]
    assert paper and set(paper) <= {0xff000000, rgba(0x38383a)}  # the dark half's paper, the line under the header
    assert canvas.row(nav[1] + 43, 52, 322) == [rgba(0x38383a)] * 270 and canvas.row(nav[1] - 1, 60, 314) == [rgba(0x38383a)] * 254
    assert canvas.row(foot, 60, 314) == [rgba(0x38383a)] * 254  # the hairline a pixel outside the card
    assert not canvas.boxes and canvas.restored()


def test_modern_s_hold_card_in_the_light_half_is_its_paper(fw, ui, st, recs, mcl):
    hold, nav, lst, canvas = modern_hold(fw, ui, st, recs, mcl, light=1)
    paper = fw.fn('flat_page', C.c_uint)() | 0xff000000
    assert canvas.row(nav[1] + 20, 60, 314) == [paper] * 254 and canvas.row(nav[1] + 43, 60, 314) == [rgba(0xd1d1d6)] * 254


def test_modern_s_play_mode_choices_are_the_card_s_rows(fw, ui, st, recs, mcl):
    """Repeat's six choices, four showing: the rest a wheel away."""
    hold, nav, lst, canvas = modern_hold(fw, ui, st, recs, mcl)
    hold.pick('Repeat')
    rows = [ui.nodes[i].rect() for i in ui.kids(hold.view())]
    assert len(rows) == 6 and all((x, w, h) == (0, 294, 48) for x, _, w, h in rows)
    assert ui.nodes[ui.lookup(hold.dialog, 'list_view', 1)].rect()[3] == 4 * 48


def test_modern_keeps_stocks_toast(fw, ui, st, recs, mcl):
    """As on main, where Modern's dialogs were stock's: no toast box."""
    canvas = Canvas(fw)
    theme(fw, MODERN)
    toast = Ui(fw).window('msginfo_dialog', kind='dialog')
    paint_bg(fw, toast, canvas)
    assert '_toast' not in toast.props and not canvas.boxes


def classic_modern_hold(fw, ui, st, recs, mcl, light=0):
    """Now Playing's hold menu under Classic Modern, on Classic's set, as modern_hold's."""
    theme(fw, 3, LIGHT=str(light).encode())
    hold_.use_set(st, hold_.CLASSIC)
    st('theme').value = 3
    hold = hold_.Hold(fw, ui, st, recs, mcl).now_playing([hold_.song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    canvas = Canvas(fw)
    for paint in ('ringnav_paint_bg', 'ringnav_paint'):
        fw.fn(paint, C.c_int, C.c_void_p, C.c_void_p)(hold.dialog, canvas.addr)
    box = lambda name: ui.nodes[ui.lookup(hold.dialog, name, 1)].rect()
    return hold, box('view_navbar'), box('list_view'), canvas


def test_classic_modern_s_hold_menu_is_the_card_on_classic_s_32_px_rows(fw, ui, st, recs, mcl):
    """The card, header, foot and ring as Modern's, on Classic's rows: six of 32 px fit where Modern's four of 48 do."""
    hold, nav, lst, canvas = classic_modern_hold(fw, ui, st, recs, mcl)
    rows = [ui.nodes[item].rect() for item in ui.kids(hold.view())]
    assert len(rows) == len(hold_.PLAYING_ROWS) and all((x, w, h) == (0, 294, 32) for x, _, w, h in rows)
    assert nav[0::2] == (40, 294) and nav[3] == 44 and lst == (40, nav[1] + 44, 294, min(6, len(rows)) * 32)
    assert lst[1] + lst[3] + 10 <= 308  # HOLD_FOOT clear of the glass's corner, as Modern's


@pytest.mark.parametrize('flat', ['Modern', 'Classic Modern'])
@pytest.mark.parametrize('light', [0, 1])
def test_the_flat_hold_card_s_title_reads_on_its_paper(fw, ui, st, recs, mcl, flat, light):
    """The title's white is stock's raw colour, which the style hook flips at each paint: the half's dark ink on the
    light paper, white on the dark."""
    hold = (modern_hold if flat == 'Modern' else classic_modern_hold)(fw, ui, st, recs, mcl, light)[0]
    title = ui.lookup(hold.dialog, 'scrlabel_title', 1)
    stored = ui.nodes[title].props['style:normal:text_color'] & 0xffffffff
    st('paint_w', C.c_void_p).value = title
    fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))
    c = C.c_uint(stored)
    fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)(C.byref(c), None, b'text_color', 0)
    drawn = c.value & 0xffffff
    assert (max(drawn >> s & 255 for s in (0, 8, 16)) <= 0x20) if light else drawn == 0xffffff, hex(drawn)
