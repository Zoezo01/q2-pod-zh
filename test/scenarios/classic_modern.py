"""Classic Modern (THEME 3): the flat look's Quick Settings, Set Date, prompts, volume, hold card, Home pane and Now Playing marks
over Classic's own pages."""
from q2emu import *
from scenarios.flat_pages import CARD, FLAT_AT, FLAT_REST, REST, bare, bg, boot_flat, confirm_flat, place, qs_flat, scrub_bands, slider_flat, time_flat

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


# The flat volume panel, the hold card, Home's pane and Now Playing's marks, as Modern's (scenarios/flat_pages.py) on
# Classic's layout. Classic's machines (q2emu classic_light_new, classic_light_on) under THEME 3: classic_light swapped.
import contextlib
import sys

import q2emu


@contextlib.contextmanager
def as_cm(light):
    """q2emu's Classic set-up writes THEME 3, Classic Blue and the half instead of Classic's."""
    was = q2emu.classic_light
    q2emu.classic_light = lambda k: (k.config.update(LIGHT=light, **CM), k)[1]
    try: yield
    finally: q2emu.classic_light = was


# The volume over Classic's other windows (volume.c vol_paint, VOL_FLAT): the toast's box at Classic's panel's x, the same in
# both halves, the thin white bar on grey between the white speakers, no number.
for light in '01':
    m = Machine(); m.config.update(LIGHT=light, **CM); m.image_size = (12, 14); booted(m)
    home = m.node('window', 'home_page'); m.word(home + O['W_PARENT'], m.wm)
    sv, lv = m.node('slider', 'slider_vol', max=100, value=50), m.node('label', 'label_vol')
    dlg = m.node('dialog', 'volume_dialog', [sv, lv]); m.word(dlg + O['W_PARENT'], m.wm); place(m, dlg, 0, 0, 375, 320)
    m.word(syms['system_bar'], m.node('window', 'system_bar')); m.nodes[m.wm] = {'children': [home, dlg]}; m.top = dlg; bare(m); bg(m, dlg)
    panel = (28, O['VOL_PANEL_Y'], 319, O['VOL_PANEL_H']); y = panel[1] + panel[3] // 2 - 2
    assert [(r['rect'], r['radius'], r['color']) for r in m.rounded] == [(panel, 12, 0xee2b2b2b)] and wbox(m, sv) == panel
    track = {(b[0], b[2], b[4]) for b in m.bands if b[1] == y + 1 and b[2] > 1}
    assert track == {(69, 225, color_t(0x6b6b6b)), (69, 111, color_t(0xffffff))}, track
    assert art_at(m) == [('flat_spk_quiet', (44, 273)), ('flat_spk_loud', (309, 273))] and not m.letters; passed()


# The hold menu (queue_menu.c hold_open, flat.c hold_card_flat): the round card on Classic's 32 px rows, six of them clear
# of the glass with HOLD_FOOT of paper under them where Modern's 48 px rows fit four; the header and ring as Modern's.
def hold_cm(light='0', **kw):
    SETUP[:] = [lambda k: (k.config.update(LIGHT=light, **CM), k)]
    try: m = QueueMachine(**kw)
    finally: SETUP.clear()
    booted(m)
    m.press(6000); assert m.hold() == 11; d = m.top; m.word(d + O['W_PARENT'], m.wm); place(m, d, 0, 0, 375, 320); parents(m, d); m.clip = (0, 0, 375, 320)
    return m, d, named(m, d, 'list_view'), named(m, d, 'view_navbar')
for light, line in (('0', 0x38383a), ('1', 0xd1d1d6)):  # on Classic's paper
    paper = PAPER[light]; m, d, lst, nav = hold_cm(light, page='playing_page', pos=1); items = m.nodes[m.view]['children']; n = len(items)
    x, y, w, h = wbox(m, nav); shown = min(n, 6); foot = y + 44 + shown * 32 + 10
    assert (x, w, h) == (40, 294, 44) and wbox(m, lst) == (40, y + 44, 294, shown * 32) and foot <= 308, (n, wbox(m, lst))
    assert [wbox(m, i) for i in items] == [(0, 32 * k, 294, 32) for k in range(n)] and m.nodes[d]['highlight'] == ''
    title = named(m, nav, 'scrlabel_title'); ink = 0xffffff if light == '0' else 0
    assert m.nodes[title]['style:normal:font_size'] == 25 and m.nodes[title]['style:normal:text_color'] & 0xffffff == ink, m.nodes[title]
    bare(m); bg(m, d); rows = {b[1]: b for b in m.bands if b[0] == 40 and b[2] == 294}
    assert rows[y + 20][4] == rows[foot - 11][4] == color_t(paper) and rows[y + 43][4] == color_t(line), sorted(rows)
    m.paint(d); assert {b[4] for b in m.bands if b[1] == foot and b[2] > 100} == {color_t(line)}; passed()


# Home's Split where no cover shows (coverflow.c home_pane_paint, pane_art): Classic's pane, which follows the rows, flat in
# the half's colour, each row's icon flat in the half's ink at Classic's places, the caption quiet at 17 px in the system's face.
P = sys.modules['__main__']  # test/patch.py's PaneMachine
PANE_ROWS = [('note', '3 Songs'), ('note', '7 Songs'), ('icon_stack', '6 Albums'), ('icon_folder', None), ('icon_game', '4 Games'),
             ('icon_wave', '5 Services'), ('icon_gear', '4.0 GB Free')]
for light, pane, quiet in (('0', 0x1c1c1c, 0x98989d), ('1', 0xf2f2f7, 0x6e6e73)):
    with as_cm(light): m = P.pane_home(SHORTCUT='6')
    view = named(m, m.top, 'scroll_view_home'); pane_nocover(m); m.advance(1500); m.paint(view); k = '_l' if light == '1' else ''
    for row, (icon, caption) in enumerate(PANE_ROWS):
        if row: assert m.call() == 11
        art = named(m, m.top, 'img_homeart'); bare(m); m.call(address=payload_syms['coverflow_home_clip'], args=(art, m.canvas, 0, 0), gap=0)
        loads = [m.text(c[2]) for c in m.calls if c[0] == 'widget_load_image']
        assert loads == [f'flat_pane_{icon}{k}'] and {b[4] for b in m.bands if b[2] > 1} == {color_t(pane)}, (row, loads)
        assert [(l['text'], l['font'], l['color']) for l in m.letters] == ([(caption, ('default', 17), color_t(quiet))] if caption else []), (row, m.letters)
    passed()

# ... on Streaming's list: each service's own icon, its name quiet in the system's face, a long one cut.
for light, quiet in (('0', 0x98989d), ('1', 0x6e6e73)):
    with as_cm(light): m = split_stream(light=light)
    card_app(m, 'deezer', 'name=A long name for a card app here\n'); sview = named(m, m.top, 'scroll_view_streamsset')
    stock_tidal(m, sview); assert stream_open(m) == 0; m.art_size = (40, 40); m.paint(sview); m.advance(10); m.paint(sview)
    for i, name in enumerate(['TIDAL', 'Spotify', 'A long name for a card app here']):
        if i: assert m.call() == 11
        loaded, draws, caption = stream_shows(m)
        assert [(l['font'][0], l['color']) for l in m.letters] == [('default', color_t(quiet))], (i, m.letters)
        assert caption == [name] if i < 2 else caption[0].endswith('…'), (i, caption)
    passed()


# Now Playing's marks (np_marks.c) on Classic's page: stock's More, play mode and heart unseen and drawn in the flat glyphs,
# the half's ink, centred on each image's box.
for light in '01':
    SETUP[:] = [lambda k: (k.config.update(LIGHT=light, **CM), k)]
    try: q = LoadQueue(queue=3, pos=1)
    finally: SETUP.clear()
    booted(q); q.handlers[HOOKS['playing_page_init'][0] + 12] = 'stock_playing'; q.image_size = (32, 32)
    marks = [q.node('image', n, image=i) for n, i in (('img_more', 'play_more'), ('img_playmode', 'play_cycle1'), ('img_fav', 'play_fav'))]
    q.win = q.top = q.node('window', 'playing_page', marks); q.word(q.win + O['W_PARENT'], q.wm); parents(q, q.win); place(q, q.win, 0, 26, 375, 294)
    for i, w in enumerate(marks): place(q, w, 20 + 60 * i, 250, 40, 40); q.byte(w + 0x34, 255)
    q.word(syms['system_bar'], q.node('window', 'system_bar')); assert q.call(address=HOOKS['playing_page_init'][0], args=(q.win, 7, 0, 0), gap=0) == 0
    bare(q); bg(q, q.win); half = 'dl'[int(light)]
    assert [q.get(w + 0x34) & 255 for w in marks] == [0, 0, 0]
    drawn = list(zip(q.image_loads, [d[1][:2] for d in q.image_draws]))
    assert drawn == [(f'flat_np_{g}_{half}', (24 + 60 * i, 254)) for i, g in enumerate(('more', 'repeat1', 'heart_on'))], drawn; passed()

# ... and Internet Radio's heart (radio.c np_open), under both flat themes: stock's image unseen, the flat heart drawn on the page.
for theme_, light in (('3', '0'), ('3', '1'), ('2', '0')):
    CONFIG.clear(); CONFIG.update(THEME=theme_, LIGHT=light, CLASSIC_LIGHT_ACCENT='9')
    r = RadioMachine({'/mnt/mmc/Radio/favourites.m3u': bytearray(b'#EXTM3U\n#EXTINF:-1,One\nhttp://one/a\n')}); r.tree['/mnt/mmc/Radio'] = [('favourites.m3u', 8)]
    r.top = r.node('window', 'home_page'); assert r.call(address=payload_syms['radio_open'], args=(0, r.event, 0, 0), gap=0, count=5_000_000) == 0
    r.advance(0, clear=False); r.page = r.top; r.row(0); r.forked = 4343; r.row(0); rnp = r.top; assert r.nodes[rnp]['name'] == 'radionp_page'; r.advance(500)
    heart = next(w for w in r.nodes[rnp]['children'] if r.nodes[w].get('image') in ('play_fav', 'play_unfav'))
    assert r.get(heart + 0x34) & 255 == 0, theme_
    r.calls = []; r.call(address=payload_syms['np_marks'], args=(rnp, r.canvas, 0, 0), gap=0)
    drawn = [r.text(c[2]) if c[0] == 'widget_load_image' else c[0] for c in r.calls if c[0] in ('widget_load_image', 'canvas_draw_image')]
    assert drawn == [f'flat_np_{r.nodes[heart]["image"][5:].replace("unfav", "heart_off").replace("fav", "heart_on")}_{"dl"[int(light)]}', 'canvas_draw_image'], drawn
    r.destroy(rnp); passed()
CONFIG.clear()
