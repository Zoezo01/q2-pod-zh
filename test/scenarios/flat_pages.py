"""The flat look's own pages, under Modern: Quick Settings' page, buttons, labels and brightness bar."""
from q2emu import *

QS_ICONS = ('img_wifiswitch', 'img_btswitch', 'img_lock', 'img_gain', 'img_usbmode', 'img_po', 'img_playset', 'img_sysset')
QS_IMAGES = ('drop_wifi', 'drop_bt', 'drop_keylock', 'drop_lowgain', 'drop_usbdac', 'drop_po', 'drop_playset', 'drop_sysset')
QS_LABELS = (('label_wifi', 'WiFi'), ('label_bt', 'BT'), ('label_lock', 'Off'), ('label_gain', 'Low'), ('label_usbmode', 'USB'),
             ('label_outputway', 'Output Options'), ('label_playset', 'Play'), ('label_sysset', 'Set'))
FLAT_AT = [((33, 118, 203, 288)[i % 4], (34, 122)[i // 4]) for i in range(8)]  # quick_open's flat grid, 54 px buttons
CARD = {'0': 0x1c1c1e, '1': 0xffffff}  # quick_settings.c qs_card
REST = {'0': 0x3a3a3c, '1': 0xe9e9ec}  # qs_rest


def place(m, w, *g):
    for k, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), g): m.word(w + O[k], v)
def bare(m): m.bands = []; m.letters = []; m.rounded = []; m.image_draws = []; m.image_loads = []; m.calls = []
def bg(m, w): m.call(address=HOOKS['widget_on_paint_background'][0], args=(w, m.canvas, 0, 0), gap=0)


def qs_flat(light='0', accent='4', **config):
    """Quick Settings under Modern, as Classic's qs_new lays it out: the dialog as its asset leaves it, through stock's init."""
    SETUP[:] = [lambda k: (k.config.update(THEME='2', LIGHT=light, ACCENT=accent, **config), k)]
    try: q = LoadQueue()
    finally: SETUP.clear()
    q.mock('dialog_quit', 'widget_dispatch_event_to_target_recursive'); q.image_size = (54, 54)
    q.budget = 900000  # as Classic's page: two cards and eight discs, row by row, each row's corners a pixel at a time
    q.handlers[int(manifest['patch_symbols']['stock_quicksettings_trampoline'], 16)] = 'stock_quicksettings'
    q.btns = [q.node('image', n, image=i) for n, i in zip(QS_ICONS, QS_IMAGES)]
    q.labels = [q.node('label', n, text=t) for n, t in QS_LABELS]
    q.menu = q.node('view', 'view_menu', [*q.btns, *q.labels]); place(q, q.menu, 0, 20, 375, 224)
    for i, w in enumerate(q.btns): place(q, w, (30, 115, 199, 283)[i % 4], 118 * (i // 4), 60, 60); q.byte(w + 0x34, 255)
    for i, w in enumerate(q.labels): place(q, w, (30, 115, 199, 283)[i % 4] - 10, 118 * (i // 4) + 66, 80, 40)
    q.slider = q.node('slider', 'slider_backlight', min=1, max=10, value=4); place(q, q.slider, 68, 0, 239, 48)
    q.suns = [q.node('image', 'image0'), q.node('image', 'image1')]
    q.view = q.node('view', 'view_backlight', [q.slider, *q.suns]); place(q, q.view, 0, 256, 375, 48)
    q.win = q.top = q.node('dialog', 'statusbar_dialog', [q.menu, q.view]); place(q, q.win, 0, 0, 375, 320); parents(q, q.win)
    q.word(q.win + O['W_PARENT'], q.wm)
    q.nodes[q.wm] = {'children': [q.win]}; q.word(syms['system_bar'], q.node('window', 'system_bar'))
    q.call(address=HOOKS['dialog_statusbar_dialog_init'][0], args=(q.win, 0, 0, 0), gap=0)
    return q


def on_of(q, light, accent):
    """What is on, final: Modern's bar's middle row (flat.c flat_on), Graphite's red tone, flipped in the light half as
    the bar's ink is."""
    a = ACCENTS[int(accent)]
    c = a[3] if accent == '0' else sum(((a[0] >> k & 255) + (a[1] >> k & 255)) // 2 << k for k in (0, 8, 16))  # Graphite: its silver
    return q.call(address=payload_syms['flip'], args=(c, 0, 0, 0), gap=0) & 0xffffff if light == '1' else c


# The page, each half and a hued and the grey accent: the paper, two cards, eight 54 px discs on the flat grid (USB on, in
# the bar's colour), each glyph flat_, white or dark (_l) as it shows; stock's images hidden and still tappable.
for light in '01':
    for accent in '40':
        q = qs_flat(light, accent); bare(q); bg(q, q.win)
        paper = 0 if light == '0' else O['LIGHT_PAPER'] * 0x010101
        assert q.bands[0][:5] == (0, 0, 375, 320, color_t(paper)), q.bands[0]
        card, rest = round_box(q.bands[1:], 18, 20, 339, 196, 26)
        bar, rest = round_box(rest, 18, 226, 339, 62, 26)
        assert set(card) == set(bar) == {color_t(CARD[light])}
        on = on_of(q, light, accent); bright = q.call(address=payload_syms['bright_tone'], args=(on, 0, 0, 0), gap=0)
        for i, (x, y) in enumerate(FLAT_AT):
            disc, rest = round_box(rest, x, y, 54, 54, 27)
            assert set(disc) == {color_t(on if i == 4 else REST[light])}, (light, accent, i)
        ink = lambda i: '_l' if (bright if i == 4 else light == '1') else ''
        assert qs_arts(q) == [('flat_' + n + ink(i), FLAT_AT[i]) for i, n in enumerate(QS_IMAGES)]
        assert wbox(q, q.menu) == (0, 0, 375, 226) and [wbox(q, w) for w in q.btns] == [(*a, 54, 54) for a in FLAT_AT]
        assert wbox(q, q.view) == (0, 233, 375, 48) and wbox(q, q.slider) == (33, 0, 309, 48)
        assert all(q.u.mem_read(w + 0x34, 1)[0] == 0 and q.nodes[w].get('visible', 1) for w in q.btns)
        assert q.lcd_colors() == LCD_COLORS; passed()
# Each label centred under its button in the system face, 14 px, quiet; Output Options on two lines.
for light, quiet in (('0', 0x98989d), ('1', 0x6e6e73)):
    q = qs_flat(light); bare(q); bg(q, q.win)
    assert [wbox(q, w) for w in q.labels] == [(a[0] - 16, a[1] + 57, 86, 30 if n == 'label_outputway' else 16) for a, (n, t) in zip(FLAT_AT, QS_LABELS)]
    assert [l['text'] for l in q.letters] == ['WiFi', 'BT', 'Off', 'Low', 'USB', 'Output', 'Options', 'Play', 'Set']
    assert {(l['color'], l['font'][1]) for l in q.letters} == {(color_t(quiet), 14)} and q.lcd_colors() == LCD_COLORS; passed()
# Pressed: the disc a sixth darker until the release.
q = qs_flat(); qs_down(q, q.btns[1]); bare(q); bg(q, q.win)
rest = round_box(round_box(q.bands[1:], 18, 20, 339, 196, 26)[1], 18, 226, 339, 62, 26)[1]
discs = []
for x, y in FLAT_AT:
    disc, rest = round_box(rest, x, y, 54, 54, 27); discs.append(disc[0])
assert discs[1] == color_t(sum(((REST['0'] >> k & 255) * 5 // 6) << k for k in (0, 8, 16))) and discs[0] == color_t(REST['0'])
qs_event(q, q.btns[1], O['EVT_POINTER_UP_BEFORE']); bare(q); bg(q, q.win)
rest = round_box(round_box(q.bands[1:], 18, 20, 339, 196, 26)[1], 18, 226, 339, 62, 26)[1]
assert set(round_box(round_box(rest, *FLAT_AT[0], 54, 54, 27)[1], *FLAT_AT[1], 54, 54, 27)[0]) == {color_t(REST['0'])}; passed()
# The brightness bar: 36 rows at 239, 4 of 1..10 filled from its round end, the suns at its ends, stock's bar clear.
for light, accent, far in (('0', '4', 'flat_drop_bright_qd'), ('1', '4', 'flat_drop_bright_ql'), ('0', '0', 'flat_drop_bright_qd')):
    q = qs_flat(light, accent); bare(q); bg(q, q.slider)
    q.call(address=HOOKS['widget_on_paint_border'][0], args=(q.slider, q.canvas, 0, 0), gap=0)
    assert all(q.nodes[q.slider][f'style:{s}:{c}_color'] == 0 for s in ('normal', 'pressed', 'over', 'focused') for c in ('fg', 'bg'))
    under, rest = round_box(q.bands, 0, 6, 309, 36, 18)  # in the slider's coordinates: 33, 233 less
    lit, rest = round_box(rest, 0, 6, 36 + 273 * 3 // 9, 36, 18)
    on = on_of(q, light, accent); bright = q.call(address=payload_syms['bright_tone'], args=(on, 0, 0, 0), gap=0)
    assert set(under) == {color_t(REST[light])} and set(lit) == {color_t(on)} and not rest
    near = 'flat_drop_dim_l' if bright else 'flat_drop_dim'
    assert qs_arts(q) == [(near, (6, 12)), (far, (309 - 33, 9))] and q.lcd_colors() == LCD_COLORS; passed()
# ... full: the far sun takes the near one's ink.
q = qs_flat(); q.nodes[q.slider]['value'] = 10; bare(q); bg(q, q.slider)
q.call(address=HOOKS['widget_on_paint_border'][0], args=(q.slider, q.canvas, 0, 0), gap=0)
assert [n for n, _ in qs_arts(q)] == ['flat_drop_dim', 'flat_drop_bright']; passed()

# The slider pages under Modern (navigation.c slider_open, classic_pages.c slider_paint and slider_scrub): the half's
# paper, flat_bar's 36 px bar on the slider's rows 17 to 52, the caption, the number with L and R, Default Volume's pill.
FLAT_REST = {'0': 0x2c2c2e, '1': 0xe9e9ec}  # flat.c flat_rest
FLAT_INK = {'0': 0xffffff, '1': 0x1c1c1e}  # flat_ink


def paper_of(light): return 0 if light == '0' else O['LIGHT_PAPER'] * 0x010101


def slider_flat(name, slider, view, label, ends, light='0', accent='4', **kw):
    """test/patch.py's slider_win under Modern: stock's page as it loads, before its init."""
    m = LoadPlain(); m.config.update(THEME='2', LIGHT=light, ACCENT=accent)
    s = m.node('slider', slider, **kw); lab = m.node('label', label, text='5'); l, r = (m.node('image', n) for n in ends)
    dec, add = m.node('image', 'img_dec'), m.node('image', 'img_add')
    v = m.node('view', view, [lab, s, l, r, dec, add]); nav = m.node('view', 'view_navbar', visible=0)
    win = m.node('window', name, [nav, v]); parents(m, win); m.word(win + O['W_PARENT'], m.wm)
    place(m, win, 0, 26, 375, 294); place(m, v, 0, 0, 375, 240); place(m, s, 31, 60, 313, 70); place(m, lab, 100, 15, 175, 32)
    m.top = win; m.nodes[m.wm] = {'children': [win]}; ui_loaded(m, name, win)
    m.win, m.slider, m.view, m.label = win, s, v, lab
    return m


def scrub_bands(m):
    bare(m); m.call(address=HOOKS['widget_on_paint_border'][0], args=(m.slider, m.canvas, 0, 0), gap=0); return m.bands


# Brightness, each half: the paper alone under the children; the slider's box, the caption's; the bar under its fill,
# 4 of 1..10, a sun at each end.
for light in '01':
    m = slider_flat('backlight_page', 'slider_backlight', 'view_backlight', 'label_backlight', ('img_left', 'img_right'), light,
                    min=1, max=10, value=4)
    bare(m); bg(m, m.win)
    assert m.bands == [(0, 0, 375, 294, color_t(paper_of(light)), *m.bands[0][5:])] and not m.image_draws and not m.letters
    assert wbox(m, m.slider) == (33, 115, 309, 70) and wbox(m, m.view) == (0, 0, 375, 294)
    cap = named(m, m.win, 'caption'); assert wbox(m, cap) == (16, 196, 343, 22) and m.nodes[cap]['text'] == 'Press Centre When Finished'
    assert (m.nodes[cap]['style:normal:font_name'], m.nodes[cap]['style:normal:font_size']) == ('default', 17)
    under, rest = round_box(scrub_bands(m), 0, 17, 309, 36, 18); lit, rest = round_box(rest, 0, 17, 36 + 273 * 3 // 9, 36, 18)
    on = on_of(m, light, '4')
    assert set(under) == {color_t(FLAT_REST[light])} and set(lit) == {color_t(on)} and not rest
    assert [n for n, _ in qs_arts(m)] == ['flat_drop_dim', 'flat_drop_bright_q' + 'dl'[int(light)]] and m.lcd_colors() == LCD_COLORS; passed()
# Max Volume: the speakers, no triangle and no band's repaint on a step.
m = slider_flat('maxvol_page', 'slider_maxvol', 'view_maxvol', 'label_vol', ('img_volleft', 'img_volright'), max=100, value=50)
assert wbox(m, m.slider) == (33, 115, 309, 70) and not [h for h in m.nodes[m.slider].get('handlers', []) if h[0] == O['EVT_VALUE_CHANGING']]
scrub_bands(m); assert [n for n, _ in qs_arts(m)] == ['flat_spk_quiet', 'flat_spk_loud_qd']; passed()
# Balance: the number over the bar, L and R at its ends in the bold face, quiet; no marks in the bar.
m = slider_flat('balance_page', 'slider_balance', 'view_balance', 'label_balance', ('img_left', 'img_right'), min=-10, max=10, value=0)
L, R = [next(c for c in m.nodes[m.win]['children'] if m.nodes[c].get('text') == t) for t in 'LR']
assert wbox(m, m.label) == (0, 85, 375, 34) and m.nodes[m.label]['visible'] == 1 and wbox(m, L) == (37, 89, 20, 26) and wbox(m, R) == (318, 89, 20, 26)
assert {(m.nodes[w]['style:normal:font_name'], m.nodes[w]['style:normal:font_size']) for w in (L, R)} == {('bold', 21)}
scrub_bands(m); assert not m.image_draws; passed()


def boot_flat(light='0', on=1):
    """test/patch.py's boot_page under Modern."""
    SETUP[:] = [lambda k: (k.config.update(THEME='2', LIGHT=light, ACCENT='4'), k)]
    try: q = LoadQueue(queue=1)
    finally: SETUP.clear()
    win = q.ready = QueueMachine.bootvol(q); parents(q, win); q.word(win + O['W_PARENT'], q.wm); place(q, win, 0, 26, 375, 294)
    q.bar_title = q.node('hscroll_label', 'label_title'); q.word(syms['system_bar'], q.node('window', 'system_bar', [q.bar_title]))
    q.view = named(q, win, 'view_bootvol'); q.nav = named(q, win, 'view_navbar'); place(q, q.view, 0, 50, 375, 240); place(q, q.slider, 31, 60, 313, 70)
    q.nodes[q.title]['text'] = 'Default Volume'; q.nodes[q.switch]['image'] = 'switch_on' if on else 'switch_off'; q.nodes[q.view]['visible'] = on
    q.below = q.top; q.stack = [q.top]; q.nodes[q.wm] = dict(type='window_manager', name='', children=[q.top, win], visible=1)
    kids = q.nodes[q.nav]['children']; q.nodes[q.nav]['children'] = []; ui_loaded(q, 'playset/bootvol_page', win); q.nodes[q.nav]['children'] = kids
    q.win = q.top = win; q.stack.append(win); return q


# Default Volume, each half, On and Off: the pill at rest, the half that holds in what is on, its word white, the other's
# in the half's ink, both in the bold face; the hit box as Classic's.
for light in '01':
    for on in (1, 0):
        q = boot_flat(light, on); bare(q); bg(q, q.win); letters = q.letters  # on_of's call clears them
        assert q.bands[0][:5] == (0, 0, 375, 294, color_t(paper_of(light)))
        track, rest = round_box(q.bands[1:], 128, 32, 120, 36, 18); cell, rest = round_box(rest, 188 if on else 128, 32, 60, 36, 18)
        assert set(track) == {color_t(FLAT_REST[light])} and set(cell) == {color_t(on_of(q, light, '4'))} and not rest
        ink = color_t(FLAT_INK[light]) | 0xff000000
        assert [(t['text'], t['rect'], t['font']) for t in letters] == [('Off', (128, 32, 60, 36), ('bold', 19)), ('On', (188, 32, 60, 36), ('bold', 19))]
        assert [t['color'] for t in letters] == ([ink, 0xffffffff] if on else [0xffffffff, ink])
        assert wbox(q, named(q, q.win, 'switch_hit')) == (125, 28, 125, 44) and wbox(q, q.slider) == (33, 115, 309, 70); passed()


def time_flat(flag=1, vals=(4, 10, 4, 16, 42), light='0'):
    """test/patch.py's time_form under Modern: stock's Set Time page as it loads, through stock's init."""
    m = LoadPlain(); m.config.update(THEME='2', LIGHT=light, ACCENT='4'); m.u.mem_write(syms['g_time24h_flag'], bytes([flag]))
    names = ('tselector_year', 'tselector_month', 'tselector_day', 'tselector_hour', 'tselector_min')
    sels = [m.node('text_selector', n, options=o) for n, o in zip(names, (31, 12, 31, 24, 60))]
    for s, v in zip(sels, vals): m.word(s + O['SELECTOR_INDEX'], v); m.byte(s + 0x34, 255)
    backs = [m.node('view', 'view_back') for _ in '12']; dot = m.node('image', 'img_dot')
    pd = m.node('view', 'view', [backs[0], *sels[:3]]); pt = m.node('view', 'view', [backs[1], *sels[3:], dot])
    slide = m.node('slide_view', 'slide_view', [m.node('view', 'view_date', [pd]), m.node('view', 'view_time', [pt])], value=0)
    ind, ok = m.node('slide_indicator', 'slide_indicator'), m.node('button', 'btn_enter', text='OK'); m.byte(ok + 0x34, 255)
    nav = m.node('view', 'view_navbar', [m.node('label', 'title', text='Manual')], visible=0)
    body = m.node('view', 'slide_view_view', [slide, ind, ok]); win = m.node('window', 'manualtime_page', [nav, body])
    parents(m, win); m.word(win + O['W_PARENT'], m.wm); place(m, win, 0, 26, 375, 294); place(m, body, 0, 0, 375, 240)
    place(m, slide, 0, 0, 375, 240); place(m, ok, 44, 181, 287, 40)
    for s, g in zip(sels, ((44, 0, 80, 170), (182, 0, 55, 170), (274, 0, 55, 170), (61, 0, 100, 170), (224, 0, 100, 170))): place(m, s, *g)
    m.top = win; m.nodes[m.wm] = {'children': [win]}; ui_loaded(m, 'systemset/manualtime_page', win)
    m.handlers[int(manifest['patch_symbols']['stock_manualtime_trampoline'], 16)] = 'stock_manualtime'
    assert m.call(address=HOOKS['systemset_manualtime_page_init'][0], args=(win, 0, 0, 0), gap=0) == 0
    m.win, m.sels, m.slide, m.ok = win, sels, slide, ok
    return m


# Set Date and Set Time under Modern (clock.c time_paint_flat): the paper, Classic's cells, the field being set in what is
# on, the rest grey; Save a pill on the prompts' row, its touch the pill; the clock's face flat_, no Classic art.
for light in '01':
    m = time_flat(light=light); bare(m); bg(m, m.win)
    assert m.bands[0][:5] == (0, 0, 375, 294, color_t(paper_of(light))) and wbox(m, m.ok) == (127, 224, 121, 36) and m.nodes[m.ok]['text'] == 'Save'
    assert not [n for n, _ in qs_arts(m) if n.startswith('classic_light_')]
    texts = {t['text']: t for t in m.letters}; assert {'Nov', '05', 'Save'} <= set(texts) and not qs_arts(m)
    m.nodes[m.slide]['value'] = 1; bare(m); bg(m, m.win); texts = {t['text']: t for t in m.letters}
    assert {'16', '42', ':', 'Save'} <= set(texts) and [n for n, _ in qs_arts(m)] == ['flat_clock_face_' + 'dl'[int(light)]]
    assert m.lcd_colors() == LCD_COLORS; passed()


def confirm_flat(light='0'):
    """Shut Down? under Modern: stock's confirminfo_dialog as its asset leaves it."""
    m = Machine(); m.config.update(THEME='2', LIGHT=light, ACCENT='4')
    d = m.node('dialog', 'confirminfo_dialog'); m.word(d + O['W_PARENT'], m.wm); place(m, d, 0, 0, 375, 320); m.top = d; m.clip = (0, 0, 375, 320)
    m.char_px = 52.6 / 6; m.image_size = (315, 68)
    cells = [m.entry(d, O['CONFIRM_TOP'] + i * 48) for i in range(2)]
    for b, name, text in zip(cells, ('img_cancel', 'img_enter'), ('Cancel', 'OK')):
        m.nodes[b].update(type='button', name=name, text=text); place(m, b, 0, m.get(b + O['W_Y']), 375, 48)
    prompt = m.node('hscroll_label', '', text='Shut Down?'); place(m, prompt, 10, 79, 355, 24)
    m.nodes[d]['children'] = [*cells, prompt]; parents(m, d)
    return m, d, cells


# A confirm under Modern (navigation.c confirm_dress, confirm_track_flat, confirm_lit, confirm_labels): two 36 px pills 8
# apart on the page form's row, Cancel holding in what is on, OK grey; the words with no shadow; no image.
for light in '01':
    m, d, cells = confirm_flat(light); m.paint(d)
    (x0, y0, w0, h0), (x1, y1, w1, h1) = wbox(m, cells[0]), wbox(m, cells[1])
    assert (y0, h0) == (y1, h1) == (250, 36) and x1 == x0 + w0 + 8 and abs(x0 + x1 + w1 - 375) <= 1 and not m.image_draws
    inside = lambda x, w: {b[4] for b in m.bands if b[2] > 1 and x <= b[0] and b[0] + b[2] <= x + w and y0 <= b[1] < y0 + h0}
    held, other = inside(x0, w0), inside(x1, w1)
    on = color_t(on_of(m, light, '4')); assert on in held and other == {color_t(FLAT_REST[light])}; passed()  # both at rest, then the held one lit
# ... with stock's delete checkbox: not a cell (the wheel stops on OK, nothing lit behind it), its box where stock put
# it, nothing of the payload's drawn on it: stock's check_button paints itself on the paper.
m, d, cells = confirm_flat()
box = m.node('check_button', 'msg_delcheckbutton', text='Also delete source file'); place(m, box, 10, 124, 355, 24)
m.nodes[d]['children'].append(box); parents(m, d)
for _ in range(3): bare(m); m.paint(d); m.call(O['KEY_NEXT'])
bare(m); m.paint(d); on = color_t(on_of(m, '0', '4')); x1, y1, w1, h1 = wbox(m, cells[1])
assert m.selected(d) == 1 and all(x1 <= b[0] < x1 + w1 and y1 <= b[1] < y1 + h1 for b in m.bands if b[4] == on)
assert wbox(m, box) == (10, 124, 355, 24) and [l['text'] for l in m.letters] == ['Cancel', 'OK'] and not m.image_draws; passed()


# The volume over Modern's other windows (volume.c vol_paint, VOL_FLAT): the toast's box, as wide as Classic's panel and
# the same in both halves, a thin white bar on grey between the white speakers, no number; the slider moved onto it.
for light in '01':
    m = Machine(); m.config.update(THEME='2', LIGHT=light, ACCENT='4'); m.image_size = (12, 14)
    home = m.node('window', 'home_page'); m.word(home + O['W_PARENT'], m.wm)
    sv, lv = m.node('slider', 'slider_vol', max=100, value=50), m.node('label', 'label_vol')
    dlg = m.node('dialog', 'volume_dialog', [sv, lv]); m.word(dlg + O['W_PARENT'], m.wm); place(m, dlg, 0, 0, 375, 320)
    m.word(syms['system_bar'], m.node('window', 'system_bar')); m.nodes[m.wm] = {'children': [home, dlg]}; m.top = dlg; bare(m); bg(m, dlg)
    panel = (28, O['VOL_PANEL_Y'], 319, O['VOL_PANEL_H']); y = panel[1] + panel[3] // 2 - 2
    assert [(r['rect'], r['radius'], r['color']) for r in m.rounded] == [(panel, 12, 0xee2b2b2b)] and wbox(m, sv) == panel
    track = {(b[0], b[2], b[4]) for b in m.bands if b[1] == y + 1 and b[2] > 1}  # a middle row: its round ends a pixel in
    assert track == {(69, 225, color_t(0x6b6b6b)), (69, 111, color_t(0xffffff))}, track  # half of 227, over the rest
    assert art_at(m) == [('flat_spk_quiet', (44, 273)), ('flat_spk_loud', (309, 273))] and not m.letters; passed()


# The hold menu under Modern (queue_menu.c hold_open, flat.c hold_card_flat): a round card as wide as Classic's panel,
# centred under the bar: the 44 px header, the theme's 48 px rows, four at most so its ring clears the glass with
# HOLD_FOOT of paper under them, the rest scrolling; flat and opaque on the half's paper, a line under the header.
def hold_flat(light='0', **kw):
    SETUP[:] = [lambda k: (k.config.update(THEME='2', LIGHT=light, ACCENT='4'), k)]
    try: m = QueueMachine(**kw)
    finally: SETUP.clear()
    m.press(6000); assert m.hold() == 11; d = m.top; m.word(d + O['W_PARENT'], m.wm); place(m, d, 0, 0, 375, 320); parents(m, d); m.clip = (0, 0, 375, 320)
    return m, d, named(m, d, 'list_view'), named(m, d, 'view_navbar')
for light, paper, line in (('0', 0, 0x38383a), ('1', O['LIGHT_PAPER'] * 0x010101, 0xd1d1d6)):
    m, d, lst, nav = hold_flat(light, page='playing_page', pos=1); items = m.nodes[m.view]['children']; n = len(items)
    x, y, w, h = wbox(m, nav); foot = y + 44 + 4 * 48 + 10
    assert n > 4 and (x, w, h) == (40, 294, 44) and wbox(m, lst) == (40, y + 44, 294, 192) and foot <= 308
    assert [wbox(m, i) for i in items] == [(0, 48 * k, 294, 48) for k in range(n)] and m.nodes[d]['highlight'] == ''
    bare(m); bg(m, d); rows = {b[1]: b for b in m.bands if b[0] == 40 and b[2] == 294}
    assert rows[y + 20][4] == rows[foot - 11][4] == color_t(paper) and rows[y + 43][4] == color_t(line), sorted(rows)
    m.paint(d); assert {b[4] for b in m.bands if b[1] == foot and b[2] > 100} == {color_t(line)}; passed()  # the ring, its corners aside
# ... more rows than show: the wheel walks every one, the last brought into the list.
m, d, lst, nav = hold_flat(page='playing_page', pos=1); items = m.nodes[m.view]['children']; n = len(items); m.top = d
place(m, m.view, 0, 0, 294, 192); m.word(m.view + O['VIEW_CONTENT_H'], 48 * n); m.paint(m.view)
for _ in range(n - 1): assert m.call() == 11
top = m.get(m.view + O['SCROLL_Y']); assert m.selected(m.view) == n - 1 and 0 <= 48 * (n - 1) - top <= 192 - 48 and m.nodes[m.view]['virtual_h'] == 48 * n; passed()


# Home's Split under Modern where no cover shows (coverflow.c home_pane_paint): the pane flat in the half's colour, the
# flat look's note, the queue's count quiet in the system's face; Now Playing's on every row.
for light, pane, note, quiet in (('0', 0x1c1c1c, 'flat_pane_note', 0x98989d), ('1', 0xf2f2f7, 'flat_pane_note_l', 0x6e6e73)):
    SETUP[:] = [lambda k: (k.config.update(THEME='2', LIGHT=light, ACCENT='4'), k)]
    try: m = QueueMachine(queue=3)
    finally: SETUP.clear()
    m.top = home = asset_tree(m, HOME_PAGE); m.handlers[HOOKS['home_page_init'][0] + 12] = 'stock_home'
    m.call(address=HOOKS['home_page_init'][0], args=(home, 0, 0, 0), gap=0); pane_nocover(m)
    art = named(m, home, 'img_homeart'); bare(m); m.call(address=payload_syms['coverflow_home_clip'], args=(art, m.canvas, 0, 0), gap=0)
    loads = [m.text(c[2]) for c in m.calls if c[0] == 'widget_load_image']
    assert {b[4] for b in m.bands if b[2] > 1} == {color_t(pane)} and loads == [note], (loads, {b[4] for b in m.bands})
    assert [(l['text'], l['font'][1], l['color']) for l in m.letters] == [('3 Songs', 17, color_t(quiet))], m.letters; passed()


# Now Playing's marks under Modern (np_marks.c): stock's More, play mode and heart left as their state and touch but
# unseen (opacity 0), and drawn on the page in the flat look's glyphs, the half's ink; stock's under Minimal.
for theme_, light in (('2', '0'), ('2', '1'), ('0', '0')):
    SETUP[:] = [lambda k: (k.config.update(THEME=theme_, LIGHT=light, ACCENT='4'), k)]
    try: q = LoadQueue(queue=3, pos=1)
    finally: SETUP.clear()
    q.handlers[HOOKS['playing_page_init'][0] + 12] = 'stock_playing'; q.image_size = (32, 32)
    marks = [q.node('image', n, image=i) for n, i in (('img_more', 'play_more'), ('img_playmode', 'play_cycle1'), ('img_fav', 'play_fav'))]
    q.win = q.top = q.node('window', 'playing_page', marks); q.word(q.win + O['W_PARENT'], q.wm); parents(q, q.win); place(q, q.win, 0, 26, 375, 294)
    for i, w in enumerate(marks): place(q, w, 20 + 60 * i, 250, 40, 40); q.byte(w + 0x34, 255)  # stock's opacity
    q.word(syms['system_bar'], q.node('window', 'system_bar')); assert q.call(address=HOOKS['playing_page_init'][0], args=(q.win, 7, 0, 0), gap=0) == 0
    bare(q); bg(q, q.win); flat = theme_ == '2'; half = 'dl'[int(light)]
    assert [q.get(w + 0x34) & 255 for w in marks] == ([0, 0, 0] if flat else [255, 255, 255]), [q.get(w + 0x34) & 255 for w in marks]
    drawn = list(zip(q.image_loads, [d[1][:2] for d in q.image_draws]))  # the names as loaded: they are on the stack
    assert drawn == ([(f'flat_np_{g}_{half}', (24 + 60 * i, 254)) for i, g in enumerate(('more', 'repeat1', 'heart_on'))] if flat else []), drawn; passed()
