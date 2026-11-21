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
    """What is on, final: Modern's bar's middle row (flat.c quick_on), Graphite's red tone, flipped in the light half as
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
