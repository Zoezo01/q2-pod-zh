"""Classic Modern (THEME 3): the flat look's Quick Settings, Set Date and prompts over Classic's own pages."""
from q2emu import *
from scenarios.flat_pages import CARD, FLAT_AT, FLAT_REST, REST, bare, bg, boot_flat, confirm_flat, qs_flat, scrub_bands, slider_flat, time_flat

CM = {'THEME': '3', 'CLASSIC_LIGHT_ACCENT': '9'}  # Classic Blue, its preset
BLUE = 0x207cde  # Classic's lit tile under Classic Blue (flat.c flat_on)
GROUND = {'0': 0, '1': 0xf2f2f7}  # its theme.xml pages.quick light_ground
PAPER = {'0': 0, '1': 0xffffff}  # Classic's

# Quick Settings in each half: the cards on the theme's ground, USB's disc in Classic Blue, the glyphs flat_.
for light in '01':
    q = qs_flat(light, **CM); bare(q); bg(q, q.win)
    assert q.bands[0][:5] == (0, 0, 375, 320, color_t(GROUND[light])), q.bands[0]
    card, rest = round_box(q.bands[1:], 18, 20, 339, 196, 26)
    bar, rest = round_box(rest, 18, 226, 339, 62, 26)
    assert set(card) == set(bar) == {color_t(CARD[light])}
    for i, (x, y) in enumerate(FLAT_AT):
        disc, rest = round_box(rest, x, y, 54, 54, 27)
        assert set(disc) == {color_t(BLUE if i == 4 else REST[light])}, (light, i)
    assert all(n.startswith('flat_') for n, _ in qs_arts(q)) and q.lcd_colors() == LCD_COLORS; passed()

# Set Date: the day where Classic's is, the month and the year 6 px apart (pages.date gap), the selectors with the boxes,
# the 24-hour time's at Classic's places; its words in Classic's face.
for light in '01':
    m = time_flat(light=light, **CM); bare(m); bg(m, m.win)
    assert [wbox(m, s)[0] for s in m.sels[:3]] == [182, 248, 299] and [wbox(m, s)[0] for s in m.sels[3:]] == [214, 271]
    assert {t['font'][0] for t in m.letters} == {'classic-light'} and wbox(m, m.ok) == (127, 224, 121, 36)
    assert m.lcd_colors() == LCD_COLORS; passed()

def booted(m):
    """theme_boot's first system_bar load, which writes Classic's set (q2emu classic_light_boot under THEME 3)."""
    m.record_syscalls(lambda page: False); m.handlers[int(manifest['patch_symbols']['stock_ui_load_trampoline'], 16)] = 'stock_ui_load'
    m.call(address=HOOKS['ui_loader_load_widget_with_parent'][0], args=(m.string('system_bar'), 0, 0, 0), gap=0)


# A prompt on Classic's set: the paper under its 26 px bar, the flat pills, Cancel holding in Classic Blue, OK at rest.
for light in '01':
    m, d, cells = confirm_flat(light, boot=booted, **CM); m.paint(d)
    (x0, y0, w0, h0), (x1, _, w1, _) = wbox(m, cells[0]), wbox(m, cells[1])
    inside = lambda x, w: {b[4] for b in m.bands if b[2] > 1 and x <= b[0] and b[0] + b[2] <= x + w and y0 <= b[1] < y0 + h0}
    assert (y0, h0) == (250, 36) and color_t(BLUE) in inside(x0, w0) and inside(x1, w1) == {color_t(FLAT_REST[light])}
    assert (0, 26, 375, 294, color_t(PAPER[light])) in [b[:5] for b in m.bands] and not m.image_draws; passed()

# Brightness, each half: Classic's paper, the bar lit in Classic Blue, the flat suns.
for light in '01':
    m = slider_flat('backlight_page', 'slider_backlight', 'view_backlight', 'label_backlight', ('img_left', 'img_right'), light,
                    config=CM, min=1, max=10, value=4)
    bare(m); bg(m, m.win); assert m.bands[0][:5] == (0, 0, 375, 294, color_t(PAPER[light]))
    under, rest = round_box(scrub_bands(m), 0, 17, 309, 36, 18); lit, rest = round_box(rest, 0, 17, 36 + 273 * 3 // 9, 36, 18)
    assert set(under) == {color_t(FLAT_REST[light])} and set(lit) == {color_t(BLUE)} and not rest
    assert [n for n, _ in qs_arts(m)] == ['flat_drop_dim', 'flat_drop_bright_q' + 'dl'[int(light)]] and m.lcd_colors() == LCD_COLORS; passed()

# Max Volume, each half: Classic's speakers, the far one the half's grey until reached.
for light in '01':
    m = slider_flat('maxvol_page', 'slider_maxvol', 'view_maxvol', 'label_vol', ('img_volleft', 'img_volright'), light, config=CM, max=100, value=50)
    scrub_bands(m); assert [n for n, _ in qs_arts(m)] == ['classic_light_spk_quiet_w', 'classic_light_spk_loud_' + 'dl'[int(light)]]; passed()

# Balance: L and R in Classic's face.
m = slider_flat('balance_page', 'slider_balance', 'view_balance', 'label_balance', ('img_left', 'img_right'), config=CM, min=-10, max=10, value=0)
L, R = [next(c for c in m.nodes[m.win]['children'] if m.nodes[c].get('text') == t) for t in 'LR']
assert {(m.nodes[w]['style:normal:font_name'], m.nodes[w]['style:normal:font_size']) for w in (L, R)} == {('classic-light', 21)}; passed()

# Default Volume, each half: On holding in Classic Blue, the words in Classic's face.
for light in '01':
    q = boot_flat(light, 1, **CM); bare(q); bg(q, q.win); letters = q.letters
    track, rest = round_box(q.bands[1:], 128, 32, 120, 36, 18); cell, rest = round_box(rest, 188, 32, 60, 36, 18)
    assert set(track) == {color_t(FLAT_REST[light])} and set(cell) == {color_t(BLUE)} and not rest
    assert [(t['text'], t['font']) for t in letters] == [('Off', ('classic-light', 19)), ('On', ('classic-light', 19))]; passed()
