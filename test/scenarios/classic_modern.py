"""Classic Modern (THEME 3): the flat look's Quick Settings, Set Date and prompts over Classic's own pages."""
from q2emu import *
from scenarios.flat_pages import CARD, FLAT_AT, FLAT_REST, REST, bare, bg, confirm_flat, qs_flat, time_flat

CM = {'THEME': '3', 'CLASSIC_LIGHT_ACCENT': '9'}  # Classic Blue, its preset
BLUE = 0x207cde  # Classic's lit tile under Classic Blue (flat.c flat_on)
GROUND = {'0': 0, '1': 0xf2f2f7}  # its theme.xml pages.quick light_ground

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

# Set Date: the day where Classic's is, the month and the year 6 px apart (pages.date gap), the selectors with the boxes;
# its words in Classic's face.
for light in '01':
    m = time_flat(light=light, **CM); bare(m); bg(m, m.win)
    assert [wbox(m, s)[0] for s in m.sels[:3]] == [182, 248, 299] and [wbox(m, s)[0] for s in m.sels[3:]] == [186, 243]
    assert {t['font'][0] for t in m.letters} == {'classic-light'} and wbox(m, m.ok) == (127, 224, 121, 36)
    assert m.lcd_colors() == LCD_COLORS; passed()

# A prompt: the flat pills, Cancel holding in Classic Blue, OK at rest.
for light in '01':
    m, d, cells = confirm_flat(light, **CM); m.paint(d)
    (x0, y0, w0, h0), (x1, _, w1, _) = wbox(m, cells[0]), wbox(m, cells[1])
    inside = lambda x, w: {b[4] for b in m.bands if b[2] > 1 and x <= b[0] and b[0] + b[2] <= x + w and y0 <= b[1] < y0 + h0}
    assert (y0, h0) == (250, 36) and color_t(BLUE) in inside(x0, w0) and inside(x1, w1) == {color_t(FLAT_REST[light])}
    assert not m.image_draws; passed()
