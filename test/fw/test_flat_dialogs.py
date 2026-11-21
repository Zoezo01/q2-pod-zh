"""The flat look's prompts (dialog.confirm painter flat, Modern's): the confirm's cells, track and words. Classic's are
test_nav_painters'. The hold menu stays stock's under Modern."""
import ctypes as C

import pytest

from test_nav_painters import MODERN, SETS, Confirm, Scheme, Ui, g, layout, rgba, theme  # noqa: F401 (layout: a fixture)

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
    S = Scheme(fw, layout)
    canvas = confirm.paint()
    x0, y0, w0, h0 = confirm.cancel.box
    x1, _, w1, _ = confirm.enter.box
    drawn = [x for x in range(375) if canvas.px.get((x, y0 + h0 // 2)) != rgba(S.paper)]
    assert (min(drawn), max(drawn) + 1) == (x0, x1 + w1) and x0 + w0 not in drawn  # both cells, the paper between
    assert canvas.px[x0 + 1, y0] == rgba(S.paper) and canvas.images == []  # a pill: its corner the paper's; no art
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
    """White on the held cell, dark on a bright fill; the half's ink on the other. Final: the light half turns what
    it sets first, and Light Mode's text setter turns it again."""
    confirm = Confirm(fw, modern(fw, st, light, ACCENT=accent))
    canvas = confirm.paint()
    flip = fw.fn('flip', C.c_uint, C.c_uint)
    final = lambda c: rgba(flip(flip(c)) if light else c)
    held = 0x1c1c1e if fw.fn('bright_tone', C.c_int, C.c_uint)(flat_on(fw)) else 0xffffff
    drawn = {t: (r, c) for t, r, c in canvas.texts}
    assert len(canvas.texts) == 2 and drawn['Cancel'][0] == confirm.cancel.box and drawn['OK'][0] == confirm.enter.box
    assert (drawn['Cancel'][1], drawn['OK'][1]) == (final(held), final((0xffffff, 0x1c1c1e)[light]))


def test_a_row_action_s_flat_tray_is_the_toast_s_box(fw, st):
    ui = modern(fw, st)
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
    ((bx, by, bw, bh), _, radius), = boxes
    assert radius == 12 and 2 * bx + bw == 375 and all(bx <= x and x + w <= bx + bw and by <= y and y + h <= by + bh
                                                        for x, y, w, h in cells)
    held = 0x1c1c1e if fw.fn('bright_tone', C.c_int, C.c_uint)(flat_on(fw)) else 0xffffff  # Graphite's silver: dark
    assert (inks['Cancel'], inks['Delete']) == (rgba(held), rgba(0xffffff))  # the other cell white on the dark box
