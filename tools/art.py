"""Original raster art the theme files state in numbers, drawn at build time: small vector shapes, antialiased
over sub-rows, written as 8-bit RGBA PNGs. No Apple assets or reference pixels; every shape is drawn here."""
import functools
import math
import re
import struct
import zlib
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from build import ROOT, need
from ipod import CLASSIC_LIGHT_FONT_SRC, STATE_W, inc

SUB = 8  # sub-rows per pixel; columns are exact within a sub-row


def _arc(p0, rx, ry, phi, large, sweep, p1, steps=48):
    """SVG's endpoint arc as points after p0."""
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    k = x1**2 / rx**2 + y1**2 / ry**2
    if k > 1:
        rx, ry = rx * math.sqrt(k), ry * math.sqrt(k)
    num = max(0, rx**2 * ry**2 - rx**2 * y1**2 - ry**2 * x1**2)
    f = math.sqrt(num / (rx**2 * y1**2 + ry**2 * x1**2)) * (1 if large != sweep else -1)
    cx1, cy1 = f * rx * y1 / ry, -f * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (p0[0] + p1[0]) / 2, s * cx1 + c * cy1 + (p0[1] + p1[1]) / 2
    a0 = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    a1 = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx)
    d = a1 - a0
    if sweep and d < 0:
        d += 2 * math.pi
    elif not sweep and d > 0:
        d -= 2 * math.pi
    out = []
    for i in range(1, steps + 1):
        a = a0 + d * i / steps
        x, y = rx * math.cos(a), ry * math.sin(a)
        out.append((c * x - s * y + cx, s * x + c * y + cy))
    return out


def path(d):
    """An SVG path's d (M L H V C Q A Z, absolute or relative, an M's further pairs lines) as closed polylines."""
    tokens = re.findall(r'[MLHVCQAZmlhvcqaz]|-?[0-9]*\.?[0-9]+', d)
    polys, pts, i, cmd = [], [], 0, None
    cur = (0.0, 0.0)

    def num():
        nonlocal i
        i += 1
        return float(tokens[i - 1])

    def at(rel):  # the next pair, from the current point in a lower-case command
        x, y = num(), num()
        return (cur[0] + x, cur[1] + y) if rel else (x, y)
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        cmd = need(cmd, 'SVG path data before its first command')
        rel, op = cmd.islower(), cmd.upper()
        if op == 'Z':
            polys.append(pts)
            cur, pts = pts[0], []
            continue
        if op == 'M':
            cur = at(rel)
            pts = [cur]
            cmd = 'l' if rel else 'L'
        elif op == 'L':
            cur = at(rel)
            pts.append(cur)
        elif op == 'H':
            cur = (num() + (cur[0] if rel else 0), cur[1])
            pts.append(cur)
        elif op == 'V':
            cur = (cur[0], num() + (cur[1] if rel else 0))
            pts.append(cur)
        elif op in 'CQ':
            ctrl = [cur, *(at(rel) for _ in range(3 if op == 'C' else 2))]
            n = len(ctrl) - 1
            for k in range(1, 25):
                t = k / 24
                weights = [math.comb(n, m) * (1 - t)**(n - m) * t**m for m in range(n + 1)]
                pts.append(tuple(sum(w * p[a] for w, p in zip(weights, ctrl)) for a in (0, 1)))
            cur = pts[-1]
        elif op == 'A':
            rx, ry, phi, large, sweep = num(), num(), num(), num(), num()
            end = at(rel)
            pts += _arc(cur, rx, ry, phi, large, sweep, end)
            cur = pts[-1]
    return polys + ([pts] if pts else [])


def stroke(polys, width, closed=True):
    """Polylines' stroke, round joined and capped, as polygons a union fills."""
    r, out = width / 2, []
    circle = lambda x, y: [(x + r * math.cos(a * math.pi / 8), y + r * math.sin(a * math.pi / 8)) for a in range(16)]
    for poly in polys:
        if not closed:
            out.append(circle(*poly[0]))
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + (poly[:1] if closed else [])):
            n = math.hypot(x1 - x0, y1 - y0)
            if n:
                nx, ny = (y1 - y0) / n * r, (x0 - x1) / n * r
                out.append([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)])
            out.append(circle(x1, y1))
    return out


def coverage(polys, box):
    """Each pixel's covered fraction of box (x, y, w, h), the polygons' union, each polygon even-odd."""
    x0, y0, w, h = box
    cov = [0.0] * (w * h)
    for sub in range(h * SUB):
        y = y0 + (sub + 0.5) / SUB
        spans = []
        for poly in polys:
            xs = sorted(ax + (y - ay) * (bx - ax) / (by - ay)
                        for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]) if (ay <= y) != (by <= y))
            spans += zip(xs[::2], xs[1::2])
        spans.sort()
        row, end = sub // SUB * w, None
        for a, b in spans:
            if end is not None and a < end:
                a = end
            if end is None or b > end:
                end = b
            a, b = max(a - x0, 0), min(b - x0, w)
            if a >= b:  # inside the union so far
                continue
            for px in range(int(a), math.ceil(b)):
                cov[row + px] += (min(b, px + 1) - max(a, px)) / SUB
    return cov


def gradient(stops, y0, y1):
    """A y -> 0xRRGGBB function: stops (pos %, 0xRRGGBB) from y0 to y1, as an SVG linearGradient's."""
    def at(y):
        p = max(stops[0][0], min(100, (y - y0) * 100 / (y1 - y0)))  # before the first stop, its colour
        for (pa, ca), (pb, cb) in zip(stops, stops[1:]):
            if p <= pb:
                t = (p - pa) / (pb - pa)
                return sum(round((ca >> s & 255) + ((cb >> s & 255) - (ca >> s & 255)) * t) << s for s in (16, 8, 0))
        return stops[-1][1]
    return at


def laid(w, h, layers):
    """RGBA bytes: layers (coverage per pixel, paint(x, y) -> (0xRRGGBB, alpha 0 to 1)) source-over, straight alpha."""
    px = [(0.0, 0.0, 0.0, 0.0)] * (w * h)
    for cov, paint in layers:
        for i, c in enumerate(cov):
            if c:
                rgb, a = paint(i % w + 0.5, i // w + 0.5)
                a *= min(c, 1)
                if not a:
                    continue
                r, g, b, d = px[i]
                out = a + d * (1 - a)
                if not out:  # a soft layer's clear pixel, on nothing
                    continue
                px[i] = (*((v * d * (1 - a) + (rgb >> s & 255) * a) / out for v, s in ((r, 16), (g, 8), (b, 0))), out)
    return b''.join(bytes((round(r), round(g), round(b), round(a * 255))) for r, g, b, a in px)


def compose(box, layers):
    """box's RGBA bytes: layers (polygons, paint(x, y) -> (0xRRGGBB, alpha 0 to 1)[, clip polygons]) source-over,
    straight alpha."""
    x0, y0, w, h = box
    def layer(polys, paint, *clip):
        cov = coverage(polys, box)
        if clip:
            cov = [a * b for a, b in zip(cov, coverage(clip[0], box))]
        return cov, lambda x, y: paint(x0 + x, y0 + y)
    return laid(w, h, [layer(*l) for l in layers])


def png(w, h, rgba):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    rows = b''.join(b'\0' + rgba[y * 4 * w:(y + 1) * 4 * w] for y in range(h))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


# Screen coordinates, the bar's rows and columns; each image's box is where the payload draws it.
BOLT = 'M301.67 6 298.56 11.24 300.85 11.24 300.2 15.16 303.64 9.76 301.35 9.76 302.49 6Z'
PLUG = 'M295.45 8.78h2.78v1.23H295.45ZM295.45 10.99h2.78v1.23H295.45ZM298.07 7.47h3.52a3.03 3.03 0 0 1 0 6.05h-3.52ZM303.64 9.93h4.09v1.23h-4.09Z'
BOLT_BOX = (290, 5, 24, 11)  # the battery's inside, the set's batt_w - 2 x batt_h - 2, in a frame of its own: classic_light_battery draws it at the inside's top-left
def note(svg):
    """The note's RGBA bytes from its drawing, note.svg: see that file."""
    def linear(name):
        """The gradient's stops [(pos %, alpha 0 to 1, grey 0 to 1)] and its axis's ends."""
        g = need(re.search(rf'<linearGradient id="{name}"([^>]*)>(.*?)</linearGradient>', svg, re.S), f'Missing gradient {name}')
        attr = dict(re.findall(r'(\w+)="([^"]*)"', g[1]))
        stops = [(float(a['offset']) * 100, float(a.get('stop-opacity', 1)), int(a['stop-color'][1], 16) / 15)
                 for a in map(lambda t: dict(re.findall(r'([\w-]+)="([^"]*)"', t)), re.findall(r'<stop ([^>]*)/>', g[2]))]
        return stops, (float(attr['x1']), float(attr['x2'])) if attr['x1'] != attr['x2'] else (float(attr['y1']), float(attr['y2']))

    def at(g, v, channel):
        stops, (a, b) = g
        p = (v - a) * 100 / (b - a)
        if p <= stops[0][0]:
            return stops[0][channel]
        for s0, s1 in zip(stops, stops[1:]):
            if p <= s1[0]:
                return s0[channel] + (s1[channel] - s0[channel]) * (p - s0[0]) / (s1[0] - s0[0])
        return stops[-1][channel]
    shape = path(need(re.search(r'id="shape" d="([^"]*)"', svg), 'Missing shape')[1])
    gloss = path(need(re.search(r'id="gloss" d="([^"]*)"', svg), 'Missing gloss')[1])
    fill, gfill, fade = linear('shape_fill'), linear('gloss_fill'), linear('gloss_fade')
    return compose(NOTE_BOX, [(shape, lambda x, y: (0xffffff, at(fill, y, 1))),
                              (gloss, lambda x, y: (0xffffff, at(gfill, y, 1) * at(fade, x, 2)), shape),
                              (stroke(shape, 1), lambda x, y: (0xffffff, 0.42))])


SORT = 'M0 0H16V2H0ZM0 5H11V7H0ZM0 10H6V12H0Z'
SORT_BOX = (0, 0, 16, 12)  # Songs' Sort, white as stock's glyphs: Light Mode flips it to the ink
# Home's pane (coverflow.c): original drawings in the note's coordinates. Each but the note is fitted into a 92 px box
# centred on PANE_CENTRE, the note's own place standing for its size; all share one fill and stroke (icon).
# The volume bar's two ends (navigation.c vol_paint), where an iPod classic's are: a speaker, and one with its
# waves, the iPod bar's height; white as SORT, Light Mode flips them to the ink. The waves end within the cone's
# height, so the box's rows hold them.
SPEAKER = 'M0 4.3H3L7.5 0.9V12.1L3 8.7H0Z'
SPEAKER_BOX, WAVES_BOX = (0, 0, 8, 13), (0, 0, 18, 13)


def wave(r, cx=7, cy=6.5, half=5.6):  # pragma: no mutate block
    """An arc of radius r about the cone's tip, 96 degrees, or as much of it as keeps within half above and below."""
    dy = min(r * math.sin(math.radians(48)), half)
    dx = math.sqrt(r * r - dy * dy)
    return f'M{cx + dx:.2f} {cy - dy:.2f}A{r} {r} 0 0 1 {cx + dx:.2f} {cy + dy:.2f}'


WAVES = [wave(r) for r in (3.2, 6.6, 10)]
PANE_ICONS = {
    'note': 'M254 82.5 317 71.5V156A16 12.5 -12 1 1 308.5 145.5V101L262 109.5V165A17 12.5 -12 1 1 254 153.5Z',
    'folder': 'M230 92q0-6 6-6h24l8 9h43q6 0 6 6v60q0 6-6 6h-75q-6 0-6-6Z',
    'stack': 'M246 94h60v60h-60ZM228 104l14-4v48l-14-4ZM324 104l-14-4v48l14-4Z',
    'game': 'M251 96h44c16 0 25 15 27 35 2 17-3 28-11 28-7 0-11-6-16-13-3-4-6-6-11-6h-22c-5 0-8 2-11 6-5 7-9 13-16 13-8 0-13-11-11-28 2-20 11-35 27-35Z'
            'M243 109v9h-9v8h9v9h8v-9h9v-8h-9v-9ZM296 108a6 6 0 1 0 .01 0ZM284 120a6 6 0 1 0 .01 0Z',
    'wave': 'M273 113a11 11 0 1 0 .01 0ZM293.1 105.9A27 27 0 0 1 293.1 142.1L287.9 137.4A20 20 0 0 0 287.9 110.6Z'
            'M304.2 95.9A42 42 0 0 1 304.2 152.1L299.0 147.4A35 35 0 0 0 299.0 100.6ZM252.9 142.1A27 27 0 0 1 252.9 105.9L258.1 110.6A20 20 0 0 0 258.1 137.4Z'
            'M241.8 152.1A42 42 0 0 1 241.8 95.9L247.0 100.6A35 35 0 0 0 247.0 147.4Z',
    'gear': 'M313.7 140.8L309.6 148.4L296.3 147.3L291.3 151.4L289.8 164.7L281.6 167.2L273.0 157.0L266.6 156.4L256.2 164.7L248.6 160.6L249.7 147.3'
            'L245.6 142.3L232.3 140.8L229.8 132.6L240.0 124.0L240.6 117.6L232.3 107.2L236.4 99.6L249.7 100.7L254.7 96.6L256.2 83.3L264.4 80.8L273.0 91.0'
            'L279.4 91.6L289.8 83.3L297.4 87.4L296.3 100.7L300.4 105.7L313.7 107.2L316.2 115.4L306.0 124.0L305.4 130.4ZM273 108a16 16 0 1 0 .01 0Z'}
PANE_CENTRE, PANE_FIT = (278, 124), 92
ICON_BOX = (230, 76, 96, 96)  # the icons' image in the drawing's coordinates: home.pane.icon_x, icon_y place it
ICON_GRADIENT = ((232, 74), (310, 168), ((0, 1), (.42, .9), (.52, .5), (1, .3)))  # across the drawing, white in these alphas
CHEVRON = 'M2.9 3 8.4 8.5 2.9 14'
CHEVRON_STROKE = 3.2
CHEVRON_BOX = (0, 0, 12, 16)  # the row's chevron (navigation.c paint_chevrons, paint_into)
def icon(d, fit):
    """A pane icon's RGBA bytes: its subpaths even-odd in the white gradient, stroked at .42, 1 px in its own scale."""
    polys = path(d)
    xs, ys = [p[0] for poly in polys for p in poly], [p[1] for poly in polys for p in poly]
    k = min(PANE_FIT / (max(xs) - min(xs)), PANE_FIT / (max(ys) - min(ys))) if fit else 1
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    place = (lambda p: (PANE_CENTRE[0] + k * (p[0] - cx), PANE_CENTRE[1] + k * (p[1] - cy))) if fit else (lambda p: p)
    local = (lambda x, y: (cx + (x - PANE_CENTRE[0]) / k, cy + (y - PANE_CENTRE[1]) / k)) if fit else (lambda x, y: (x, y))
    (ax, ay), (bx, by), stops = ICON_GRADIENT

    def paint(x, y):
        x, y = local(x, y)
        t = max(0, min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / ((bx - ax)**2 + (by - ay)**2)))
        for (p0, a0), (p1, a1) in zip(stops, stops[1:]):
            if t <= p1:
                return 0xffffff, a0 + (a1 - a0) * (t - p0) / (p1 - p0)
    placed = [[place(p) for p in poly] for poly in polys]
    # one even-odd polygon: each subpath joined from the first's start and back, those seams crossed twice
    whole = [q for poly in placed for q in (*poly, poly[0], placed[0][0])]
    return compose(ICON_BOX if fit else NOTE_BOX, [([whole], paint), (stroke(placed, k), lambda x, y: (0xffffff, 0.42))])


NOTE_BOX = (222, 66, 100, 115)  # the drawing's viewBox
# Internet Radio's cover under Classic (playing.station, #204's generic cover's family), an original drawing on a 320 px
# square, drawn at the size it fills: its rows' gradient, a light band across its top, and a mast over its soft shadow.
# The mast's gradient and the shadow are stated at 160 px.
STATION_BAND = 'M0 0H320V64L240 80 0 120Z'
STATION_MAST = ('M96 86a90 90 0 0 0 0 110l18-14a66 66 0 0 1 0-82ZM224 86a90 90 0 0 1 0 110l-18-14a66 66 0 0 0 0-82Z'
                'M126 108a50 50 0 0 0 0 66l16-13a28 28 0 0 1 0-40ZM194 108a50 50 0 0 1 0 66l-16-13a28 28 0 0 0 0-40Z'
                'M160 118a23 23 0 1 1-.1 0ZM150 170h20l28 100h-76Z')
STATION_MAST_Y = (19, 134)  # the mast's gradient's ends, in the image's rows
STATION_SHADOW = (1.5, 2.5)  # the shadow: rows down, and its blur's standard deviation in px
@functools.cache
def station(stops, mast_stops, band_a, shadow_a, size):
    """The station cover's RGBA bytes, size px square."""
    scale = size / 160
    placed = lambda d: [[(x * scale / 2, y * scale / 2) for x, y in poly] for poly in path(d)]
    box = (0, 0, size, size)
    mast, down, sd, reach = placed(STATION_MAST), STATION_SHADOW[0] * scale, STATION_SHADOW[1] * scale, round(8 * scale)
    held = coverage([[(x, y + down) for x, y in poly] for poly in mast], box)
    kernel = [math.exp(-(k * k) / (2 * sd * sd)) for k in range(-reach, reach + 1)]
    total = sum(kernel)
    at = lambda v, x, y: v[max(0, min(size - 1, y)) * size + max(0, min(size - 1, x))]
    across = [sum(w * at(held, x + k - reach, y) for k, w in enumerate(kernel)) / total for y in range(size) for x in range(size)]
    shadow = [sum(w * at(across, x, y + k - reach) for k, w in enumerate(kernel)) / total for y in range(size) for x in range(size)]
    whole = [[(0, 0), (size, 0), (size, size), (0, size)]]
    fill, ink = gradient(stops, 0, size), gradient(mast_stops, *(y * scale for y in STATION_MAST_Y))
    return compose(box, [(whole, lambda x, y: (fill(y), 1)),
                         (placed(STATION_BAND), lambda x, y: (0xffffff, band_a / 255)),
                         (whole, lambda x, y: (0, shadow[int(y) * size + int(x)] * shadow_a / 255)),
                         (mast, lambda x, y: (ink(y), 1))])
STATE = {'play': 'M270.5 4.5 282.5 11.5 270.5 18.5Z', 'pause': 'M270.5 4.5H275V18.5H270.5ZM278 4.5H282.5V18.5H278Z'}
STATE_BOX = (266, 0, STATE_W, 22)  # the frame the state art is drawn in, on the bar's rows; the art itself is position-free
STATE_Y = (4, 19)  # the fill's and edge's first and last rows


# The volume's speakers and the slider pages' suns, their boxes the image; drawn on the bar and page in these colours.
SPEAKER = 'M0 4.5h3.5L9 .5v13L3.5 9.5H0Z'
SPEAKER_ARCS = ('M12 4.5a3.5 3.5 0 0 1 0 5', 'M14.5 2.5a7 7 0 0 1 0 9', 'M17 .8a10 10 0 0 1 0 12.4')
SPEAKER_INKS = {'l': 0x393c39, 'd': 0xc6c3c6, 'w': 0xffffff}
SUNS = {'small': (16, 3.5, 5.5, 7.5), 'big': (22, 4.5, 7, 10)}  # box, then in a 22 unit square scaled to it: disc's radius, a ray's first and last radius
DARK_STATE = {'play': 'M1.53 1.45 8.47 5.5 1.53 9.55Z', 'pause': 'M1.53 1.45H4.13V9.55H1.53ZM5.87 1.45H8.47V9.55H5.87Z'}  # 10 x 11
def sun(box, disc, r0, r1):
    """A white disc and its 8 rays at 45 degrees, stroked 1.6 with round caps, in 22 units scaled to a box px square."""
    c, k = box / 2, box / 22
    disc, r0, r1 = disc * k, r0 * k, r1 * k
    at = lambda r, a: (c + r * math.cos(a * math.pi / 4), c + r * math.sin(a * math.pi / 4))
    circle = [(c + disc * math.cos(a * math.pi / 24), c + disc * math.sin(a * math.pi / 24)) for a in range(48)]
    rays = [q for a in range(8) for q in stroke([[at(r0, a), at(r1, a)]], 1.6 * k, closed=False)]
    return png(box, box, compose((0, 0, box, box), [([circle, *rays], lambda x, y: (0xffffff, 1))]))
def speaker(loud, rgb):
    """The speaker, with its three arcs when loud."""
    w = 22 if loud else 12
    arcs = [q for d in SPEAKER_ARCS for q in stroke(path(d), 1.5, closed=False)] if loud else []
    return png(w, 14, compose((0, 0, w, 14), [(path(SPEAKER) + arcs, lambda x, y: (rgb, 1))]))


# The soft shadows and edges of the slider pages' and panels' boxes, as CSS's box-shadow draws them: name, the box's
# w, h and corner radius, the shadow's blur, its drop and alpha, a 1 px ring's alpha. The image reaches blur + 2 past
# the shadow's box on every side and is clear under the box itself.
SHADOWS = {'band_shadow': (322, 46, 8, 5, 2, 0x66, 0), 'panel_shadow': (319, 48, 12, 6, 2, 0x33, 0),
           'rate_shadow': (304, 110, 12, 6, 2, 0x33, 0), 'tray_shadow': (125, 44, 9, 6, 2, 0x77, 0x66)}
BLACK, WHITE = 0, 255
@functools.cache
def classic_light_font():
    """The theme face's source, opened once a build."""
    return TTFont(CLASSIC_LIGHT_FONT_SRC, lazy=True)
# Quick Settings' page under Classic: navigation.c quick_page paints each 60 px button (qs_tile), and these are the glyphs
# over it in the tile's ink: QS_INK, white on (_l on a bright accent, Graphite's silver), _l and _d off in the light and dark half. Own drawings; stock's image names (the demo's own
# strings) decide which button, and QS_LIT (stock.h, qs_lit) which are on.
def circle(cx, cy, r):
    return f'M{cx - r} {cy}A{r} {r} 0 1 1 {cx + r} {cy}A{r} {r} 0 1 1 {cx - r} {cy}Z'
SPOKES = tuple(tuple((round(r * math.sin(math.radians(a)), 3), round(-r * math.cos(math.radians(a)), 3)) for r in (8, 12)) for a in range(0, 360, 45))
# A glyph on a -30..30 box, centre at 0: (stroke's width, open paths, closed paths), filled discs (x, y, r), segments 3 wide.
QS_GLYPHS = {
    'wifi': ((3, ('M-14 -3a20 20 0 0 1 28 0', 'M-8 3a11 11 0 0 1 16 0'), ()), ((0, 9, 2.8),), ()),
    'bt': ((2.6, ('M-7 -7 7 6 0 12V-12L7 -6-7 7',), ()), (), ()),
    'lock': ((2.4, ('M-5 -3v-4a5 5 0 0 1 10 0v4',), ('M-7 -3H7A2 2 0 0 1 9 -1V9A2 2 0 0 1 7 11H-7A2 2 0 0 1 -9 9V-1A2 2 0 0 1 -7 -3Z',)), (), ()),
    # stock's three USB modes, each its own: storage a stick, DAC a USB-C port, audio the trident
    'stick': ((2.4, (), ('M-4 -14H4V-8H-4Z', 'M-7 -8H7V10A2 2 0 0 1 5 12H-5A2 2 0 0 1 -7 10Z')), (), ()),
    'usbc': ((2.4, (), ('M-7 -8A7 7 0 0 1 7 -8V8A7 7 0 0 1 -7 8Z',)), (), (((0, -7), (0, 7)),)),
    'usb': ((2.4, ('M0 12V-10', 'M0 6l-7-6V-5', 'M0 2l7-6V-8'), ('M0 -13l-4 5h8Z',)), (), ()),
    'sl': ((2.4, ('M-8 -12V12', 'M0 -12V12', 'M8 -12V12'), ()), ((-8, 3, 3.6), (0, -4, 3.6), (8, 5, 3.6)), ()),
    'gear': ((2.4, (), (circle(0, 0, 5),)), (), SPOKES),
}
QS_FONT_PX, QS_LINE, QS_DIM = 15, 17, .45
QS_INK = {'': 0xffffff, '_l': 0x262626, '_d': 0xe4e6e9}
# Stock's name: glyph or text lines, on, the glyph's opacity (disabled: dimmed).
QS_BUTTONS = {
    'wifi': ('wifi', 0, 1), 'wifiopen': ('wifi', 1, 1), 'bt': ('bt', 0, 1), 'btopen': ('bt', 1, 1),
    'keylock': ('lock', 0, 1), 'keylockopen': ('lock', 1, 1),
    'lowgain': (('Low', 'Gain'), 0, 1), 'lowgaindisable': (('Low', 'Gain'), 0, QS_DIM),
    'highgain': (('High', 'Gain'), 1, 1), 'highgaindisable': (('High', 'Gain'), 0, QS_DIM),
    'lo': (('LO',), 1, 1), 'po': (('PO',), 0, 1), 'sa': (('SA',), 0, 1),
    'usbstorage': ('stick', 0, 1), 'usbaudio': ('usb', 1, 1), 'usbdac': ('usbc', 1, 1),
    'playset': ('sl', 0, 1), 'sysset': ('gear', 0, 1),
}
def rounded_path(x, y, w, h, r):
    return (f'M{x + r} {y}H{x + w - r}A{r} {r} 0 0 1 {x + w} {y + r}V{y + h - r}A{r} {r} 0 0 1 {x + w - r} {y + h}'
            f'H{x + r}A{r} {r} 0 0 1 {x} {y + h - r}V{y + r}A{r} {r} 0 0 1 {x + r} {y}Z')
def rounded(w, h, x, y, bw, bh, r):
    """A rounded box's coverage of a w x h image, 0 to 1 per pixel."""
    return [min(c, 1) for c in coverage(path(rounded_path(x, y, bw, bh, r)), (0, 0, w, h))]
# The message screens' round icon, an original drawing on a -50..50 box: the circle's rows, an inner ring, its glyph, a soft shadow.
ORB_STOPS = ((2, 0xfff7c2), (14.6, 0xffe8a6), (21, 0xffdb8b), (25, 0xfed072), (30, 0xf7c155), (35.4, 0xfaaa2d), (40.6, 0xf09310),
             (45.8, 0xe48600), (51, 0xdc8001), (72, 0xda8100), (81, 0xe98001), (94, 0xeb951a), (98, 0xdc9f42))
ORB_R, ORB_RING_R, ORB_RING_W, ORB_RING, ORB_RING_A = 49.5, 48.6, 1.1, 0xffe2a0, .55
ORB_INK = 0x160b00
ORB_PAD, ORB_DROP, ORB_SIGMA, ORB_SHADOW_A = 8, 2, 4, 0x66  # px past the box for the shadow, its drop and blur on the screen
ORB_NOTE = 'M-15 -19 20 -26V14A9 7 0 1 1 13 7V-13L-8 -9V21A9 7 0 1 1 -15 14Z'
ORB_USB = ('M0 28V-20', 'M0 14 -18 0V-10', 'M0 6 18 -8V-18')  # stroked 5 wide, under its heads
ORB_USB_HEADS = 'M0 -32 -8 -20H8Z'
ORB_USB_SQUARE = 'M13 -24H23V-15H13Z'
ORB_USB_DISCS = ((-18, -13, 5), (0, 29, 6))
# Set Time and Set Date. A box's soft shadow as CSS's box-shadow draws it, clear under the box. Each image reaches past
# its box by its PAD; navigation.c's TIME_*_PAD say the same.
TRAY_H, TRAY_R, TRAY_PAD, TRAY_WIDTHS = 48, 7, 7, (108, 165, 164)
TRAY_STOPS = ((0, 0x565c66), (20, 0x3a3f47), (44, 0x22252c), (50, 0x121419), (100, 0x0f1014))
CELL_H, CELL_R, CELL_PAD, CELL_WIDTHS = 42, 6, 4, (45, 60)
CELL_LIT = ((0, 0x7cc3f8), (22, 0x4aa3ee), (49, 0x2f8de4), (50, 0x0d68cc), (100, 0x0a5ab8))
CELL_OFF = ((0, 0x454c56), (22, 0x353b44), (49, 0x2a2f37), (50, 0x0e1116), (100, 0x13161b))
TILE_W, TILE_H, TILE_PAD, TILE_HEAD = 118, 100, 6, 22
TILE_PAGE = ((0, 0xffffff), (100, 0xf4f5f7))
TILE_TOP = ((0, 0xef6a72), (55, 0xe0444e), (100, 0xcf2a35))
CLOCK_PX, CLOCK_SHADOW = 110, (86, 26)
CLOCK_RING = ((0, 0xffffff), (24, 0xf4f6f8), (46, 0xb4bbc3), (58, 0x7f8892), (72, 0xa9b0b9), (100, 0xe8ecf0))
CLOCK_RIM = ((0, 0x6d7680), (100, 0xd5dae0))
def blurred(cov, w, h, sigma):
    """cov under a Gaussian of sigma, its edge values carried on past the image."""
    r = math.ceil(3 * sigma)
    k = [math.exp(-i * i / (2 * sigma * sigma)) for i in range(-r, r + 1)]
    k = [v / sum(k) for v in k]
    across = [sum(v * cov[y * w + min(max(x + i - r, 0), w - 1)] for i, v in enumerate(k)) for y in range(h) for x in range(w)]
    return [sum(v * across[min(max(y + i - r, 0), h - 1) * w + x] for i, v in enumerate(k)) for y in range(h) for x in range(w)]
def inked(w, h, layers):
    """RGBA bytes of layers (coverage, alpha 0 to 255, grey), the first on top."""
    out = bytearray()
    for i in range(w * h):
        a = g = 0.0
        for cov, alpha, grey in layers:
            x = cov[i] * alpha / 255 * (1 - a)
            g, a = g + grey * x, a + x
        v = round(a * 255) and round(g / a)
        out += bytes((v, v, v, round(a * 255)))
    return bytes(out)
def outside(cov, box):
    return [c * (1 - b) for c, b in zip(cov, box)]
@functools.lru_cache(maxsize=None)
def shadow(bw, bh, r, blur, drop, alpha, ring):
    """box-shadow: 0 drop blur black at alpha, under 0 0 0 1px black at ring; the image's first row is the shadow's less blur + 2."""
    pad = blur + 2
    w, h = bw + 2 * pad, bh + 2 * pad
    box = rounded(w, h, pad, pad - drop, bw, bh, r)
    soft = blurred(rounded(w, h, pad, pad, bw, bh, r), w, h, blur / 2)
    rim = outside(rounded(w, h, pad - 1, pad - drop - 1, bw + 2, bh + 2, r + 1), box)
    return png(w, h, inked(w, h, [(rim, ring, BLACK), (outside(soft, box), alpha, BLACK)]))
@functools.lru_cache(maxsize=None)
def track_inset():
    """The switch's 100 x 26, R 6 track: inset 0 2px 2px at 0xdd, inset 0 0 0 1px at 0x88, 0 1px white at 0x29 under it."""
    w, h, r, p = 100, 26, 6, 3
    open_ = [1 - c for c in rounded(w + 2 * p, h + 2 * p + 1, p, p + 2, w, h, r)]
    soft = blurred(open_, w + 2 * p, h + 2 * p + 1, 1)
    soft = [soft[(y + p) * (w + 2 * p) + x + p] for y in range(h + 1) for x in range(w)]
    box = rounded(w, h + 1, 0, 0, w, h, r)
    rim = [b * (1 - c) for b, c in zip(box, rounded(w, h + 1, 1, 1, w - 2, h - 2, r - 1))]
    return png(w, h + 1, inked(w, h + 1, [([b * s for b, s in zip(box, soft)], 0xdd, BLACK), (rim, 0x88, BLACK),
                                          (outside(rounded(w, h + 1, 0, 1, w, h, r), box), 0x29, WHITE)]))
@functools.lru_cache(maxsize=None)
def lit_edge():
    """The lit cell's 50 x 26, R 6 edges, the image a pixel larger each way: inset 0 1px white at 0x73, inset 0 -1px at 0x59, 0 0 0 1px at 0x77."""
    w, h = 52, 28
    box = rounded(w, h, 1, 1, 50, 26, 6)
    inner = lambda dy: [b * (1 - c) for b, c in zip(box, rounded(w, h, 1, 1 + dy, 50, 26, 6))]
    return png(w, h, inked(w, h, [(inner(1), 0x73, WHITE), (inner(-1), 0x59, BLACK),
                                  (outside(rounded(w, h, 0, 0, w, h, 7), box), 0x77, BLACK)]))
def over(top, under):
    """RGBA bytes of straight-alpha top source-over under."""
    out = bytearray()
    for i in range(0, len(top), 4):
        a, d = top[i + 3] / 255, under[i + 3] / 255
        e = a + d * (1 - a)
        out += bytes(round((top[i + k] * a + under[i + k] * d * (1 - a)) / e) if e else 0 for k in range(3)) + bytes((round(e * 255),))
    return bytes(out)
def butt_stroke(poly, width, closed=False):
    """An SVG default stroke's polygons: butt caps, mitre joins under its limit of 4, else bevelled."""
    r, out = width / 2, []
    pts = [p for i, p in enumerate(poly) if not i or math.dist(p, poly[i - 1]) > 1e-6]
    if closed:
        pts += [pts[0]] if math.dist(pts[-1], pts[0]) > 1e-6 else []
    def normal(a, b):
        n = math.hypot(b[0] - a[0], b[1] - a[1])
        return (b[1] - a[1]) / n * r, (a[0] - b[0]) / n * r
    for a, b in zip(pts, pts[1:]):
        nx, ny = normal(a, b)
        out.append([(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)])
    for a, b, c in zip(pts, pts[1:], pts[2:] + (pts[1:2] if closed else [])):
        (ax, ay), (bx, by) = normal(a, b), normal(b, c)
        cross = ax * by - ay * bx
        if abs(cross) < 1e-9:
            continue
        s = 1 if cross > 0 else -1  # the outer side
        ax, ay, bx, by = ax * s, ay * s, bx * s, by * s
        mx, my = ax + bx, ay + by
        scale = 2 * r * r / (mx * mx + my * my)  # 2 * scale is the squared mitre ratio
        tip = [(b[0] + mx * scale, b[1] + my * scale)] if 2 * scale <= 16 else []
        out.append([b, (b[0] + ax, b[1] + ay), *tip, (b[0] + bx, b[1] + by)])
    return out
def qs_text(lines):
    """Text lines' outlines from the theme's face, centred on the button, QS_LINE apart. One polygon a glyph: its contours
    joined, each edge between them walked twice, so that coverage's even-odd keeps the counters."""
    from fontTools.pens.basePen import BasePen
    font = classic_light_font()
    glyphs, cmap, k = font.getGlyphSet(), font.getBestCmap(), QS_FONT_PX / font['head'].unitsPerEm
    hhea = font['hhea']
    out = []
    class Flat(BasePen):
        def __init__(self, x, y):
            super().__init__(glyphs)
            self.x, self.y, self.polys = x, y, []
        def at(self, p):
            return self.x + p[0] * k, self.y - p[1] * k
        def _moveTo(self, p):
            self.polys.append([self.at(p)])
        def _lineTo(self, p):
            self.polys[-1].append(self.at(p))
        def _curveToOne(self, a, b, c):
            p0, ctrl = self.polys[-1][-1], [self.at(q) for q in (a, b, c)]
            for t in (i / 12 for i in range(1, 13)):
                u = 1 - t
                self.polys[-1].append(tuple(u**3 * p0[n] + 3 * u * u * t * ctrl[0][n] + 3 * u * t * t * ctrl[1][n] + t**3 * ctrl[2][n] for n in (0, 1)))
    for i, line in enumerate(lines):
        width = sum(glyphs[cmap[ord(c)]].width for c in line) * k
        # the line box's baseline, as CSS centres the face's ascent and descent in it
        y = 30 - len(lines) * QS_LINE / 2 + i * QS_LINE + (QS_LINE + (hhea.ascent + hhea.descent) * k) / 2
        x = 30 - width / 2
        for c in line:
            pen = Flat(x, y)
            glyphs[cmap[ord(c)]].draw(pen)
            out.append([p for contour in pen.polys for p in (*contour, contour[0])] + [pen.polys[0][0]])
            x += glyphs[cmap[ord(c)]].width * k
    return out
def qs_glyph(glyph, ink):
    """A glyph's layers for compose on the button's 60 x 60 box, in ink."""
    paint = lambda x, y: (ink, 1)
    if isinstance(glyph, tuple):  # text
        return [(qs_text(glyph), paint)]
    (width, opened, closed), discs, segments = QS_GLYPHS[glyph]
    moved = lambda poly: [(x + 30, y + 30) for x, y in poly]
    polys = [q for d in opened for poly in path(d) for q in butt_stroke(moved(poly), width)]
    polys += [q for d in closed for poly in path(d) for q in butt_stroke(moved(poly), width, closed=True)]
    polys += [q for a, b in segments for q in butt_stroke(moved([a, b]), 3)]
    polys += [poly for x, y, r in discs for poly in map(moved, path(circle(x, y, r)))]
    return [(polys, paint)]
@functools.lru_cache(maxsize=None)
def qs_mark(glyph, opacity, ink):
    """One button's glyph, RGBA bytes on clear, at opacity."""
    mark = bytearray(compose((0, 0, 60, 60), qs_glyph(glyph, ink)))
    mark[3::4] = bytes(round(a * opacity) for a in mark[3::4])
    return png(60, 60, bytes(mark))
def qs_lit():
    """QS_LIT: the names whose button is on, NUL-separated, for navigation.c."""
    return ''.join(f'drop_{n}\\0' for n, (_, lit, _) in QS_BUTTONS.items() if lit)
@functools.lru_cache(maxsize=None)
def qs_track():
    """The brightness track's edges over its 239 x 11, R 6 rows: inset 0 1px 2px black, and the row under it, white at 0x22."""
    w, h, r, p = 239, 11, 6, 4
    W, H = w + 2 * p, h + 2 * p
    soft = blurred([1 - c for c in rounded(W, H, p, p + 1, w, h, r)], W, H, 1)
    soft = [soft[(y + p) * W + x + p] if y < h else 0 for y in range(h + 1) for x in range(w)]
    box = rounded(w, h + 1, 0, 0, w, h, r)
    return png(w, h + 1, inked(w, h + 1, [([b * s for b, s in zip(box, soft)], 255, 0), (outside(rounded(w, h + 1, 0, 1, w, h, r), box), 0x22, 255)]))
@functools.lru_cache(maxsize=None)
def qs_knob():
    """The 23 px knob at (3, 3) of 29: its ramp over box-shadow 0 1px 3px #000c."""
    w = 29
    disc = rounded(w, w, 3, 3, 23, 23, 11.5)
    soft = blurred(rounded(w, w, 3, 4, 23, 23, 11.5), w, w, 1.5)
    ramp = gradient(((0, 0xffffff), (50, 0xd9dbde), (100, 0xb8bbc0)), 3, 26)
    top = compose((0, 0, w, w), [(path(circle(14.5, 14.5, 11.5)), lambda x, y: (ramp(y), 1))])
    return png(w, w, over(top, inked(w, w, [(outside(soft, disc), 0xcc, 0)])))
# The lock screen's hint: the soft shadow (0 1px 2px #00000080) of stock's three marks, drawn at 21 x 7, HINT_PAD past them all round.
HINT_MARK = 'M1.5 1.5L8.2 8L1.5 14.5'
HINT_MARKS, HINT_PAD, HINT_DROP, HINT_SIGMA, HINT_A = (0, 10.5, 21), 4, 1, 1, 0x80
def hint_shadow():
    w, h = 21 + 2 * HINT_PAD, 7 + 2 * HINT_PAD
    marks = [[((x + at) * 0.7 + HINT_PAD, y * 7 / 16 + HINT_PAD + HINT_DROP) for x, y in poly] for at in HINT_MARKS for poly in stroke(path(HINT_MARK), 2.6, closed=False)]
    return png(w, h, inked(w, h, [(blurred([min(v, 1) for v in coverage(marks, (0, 0, w, h))], w, h, HINT_SIGMA), HINT_A, 0)]))


# The confirm tray's soft shadow on the page (0, 2, blur 6, #00000077): the box where the payload draws it, the
# tray's rounded box, and the blur's standard deviation, half its radius.
TRAY_SHADOW_BOX = (30, 248, 315, 68)
TRAY = 'M47 254H328A9 9 0 0 1 337 263V297A9 9 0 0 1 328 306H47A9 9 0 0 1 38 297V263A9 9 0 0 1 47 254Z'
TRAY_SHADOW = (2, 3, 0x77)  # rows down, standard deviation, alpha


def tray_shadow():
    """The shadow's RGBA bytes: black, its alpha the tray's coverage blurred across and then down."""
    w, h = TRAY_SHADOW_BOX[2:]
    down, sd, alpha = TRAY_SHADOW
    held = coverage([[(x, y + down) for x, y in poly] for poly in path(TRAY)], TRAY_SHADOW_BOX)
    reach = 3 * sd
    kernel = [math.exp(-(k * k) / (2 * sd * sd)) for k in range(-reach, reach + 1)]
    total = sum(kernel)
    at = lambda v, x, y: v[y * w + x] if 0 <= x < w and 0 <= y < h else 0
    across = [sum(c * at(held, x + k - reach, y) for k, c in enumerate(kernel)) / total for y in range(h) for x in range(w)]
    soft = [sum(c * at(across, x, y + k - reach) for k, c in enumerate(kernel)) / total for y in range(h) for x in range(w)]
    return b''.join(bytes((0, 0, 0, round(v * alpha))) for v in soft)
@functools.cache
def orb(side, usb):
    """The icon's RGBA bytes: side px for its box, ORB_PAD more all round, the circle centred."""
    size, k = side + 2 * ORB_PAD, side / 100
    c = size / 2
    placed = lambda polys: [[(c + x * k, c + y * k) for x, y in poly] for poly in polys]
    disc = lambda x, y, r: placed([[(x + r * math.cos(a * math.pi / 64), y + r * math.sin(a * math.pi / 64)) for a in range(128)]])
    box = (0, 0, size, size)
    if usb:
        ink = [q for d in ORB_USB for q in stroke(placed(path(d)), 5 * k, closed=False)] + placed(path(ORB_USB_HEADS)) + placed(path(ORB_USB_SQUARE))
        ink += [q for a in ORB_USB_DISCS for q in disc(*a)]
    else:
        ink = placed(path(ORB_NOTE))
    shape = disc(0, 0, ORB_R)
    held = coverage([[(x, y + ORB_DROP) for x, y in poly] for poly in shape], box)
    shadow = blurred([min(v, 1) for v in held], size, size, ORB_SIGMA)
    fill = gradient(((0, ORB_STOPS[0][1]), *ORB_STOPS, (100, ORB_STOPS[-1][1])), c - ORB_R * k, c + ORB_R * k)
    return compose(box, [([[(0, 0), (size, 0), (size, size), (0, size)]], lambda x, y: (0, shadow[int(y) * size + int(x)] * ORB_SHADOW_A / 255)),
                         (shape, lambda x, y: (fill(y), 1)),
                         (stroke(disc(0, 0, ORB_RING_R), ORB_RING_W * k), lambda x, y: (ORB_RING, ORB_RING_A)),
                         (ink, lambda x, y: (ORB_INK, 1))])
# The info screen's dark face, on a -50..50 box at CLOCK_DARK_PX: its numerals in the theme's own outlines. The hands are the payload's (navigation.c paint_clock).
CLOCK_DARK_PX = 96
def outlines(text, size, cx, baseline):
    """text's glyphs centred on cx, in the theme's face at size, as one even-odd polygon per glyph."""
    from fontTools.pens.basePen import BasePen
    font = classic_light_font()
    glyphs, cmap, k = font.getGlyphSet(), font.getBestCmap(), size / font['head'].unitsPerEm
    names = [cmap[ord(c)] for c in text]
    x = cx - sum(glyphs[n].width for n in names) * k / 2

    class Flat(BasePen):
        def __init__(self):
            super().__init__(glyphs)
            self.parts = []

        def _moveTo(self, p):
            self.parts.append([p])

        def _lineTo(self, p):
            self.parts[-1].append(p)

        def _curveToOne(self, a, b, c):
            p0 = self.parts[-1][-1]
            for i in range(1, 9):
                t = i / 8
                self.parts[-1].append(tuple((1 - t)**3 * p0[j] + 3 * (1 - t)**2 * t * a[j] + 3 * (1 - t) * t**2 * b[j] + t**3 * c[j] for j in (0, 1)))
    out = []
    for name in names:
        pen = Flat()
        glyphs[name].draw(pen)
        placed = [[(x + px * k, baseline - py * k) for px, py in part] for part in pen.parts]
        if placed:
            out.append([q for part in placed for q in (*part, part[0], placed[0][0])])
        x += glyphs[name].width * k
    return out


def clock_dark():
    """The dark face's RGBA bytes: disc r 49, its stroke, numerals 1..12 at r 38."""
    size = CLOCK_DARK_PX
    k = size / 100
    place = lambda polys: [[((x + 50) * k, (y + 50) * k) for x, y in poly] for poly in polys]
    circle = [(49 * math.cos(a * math.pi / 60), 49 * math.sin(a * math.pi / 60)) for a in range(120)]
    numerals = []
    for i in range(1, 13):
        a = i * math.pi / 6
        numerals += outlines(str(i), 11, 38 * math.sin(a), 4 - 38 * math.cos(a))
    return compose((0, 0, size, size), [(place([circle]), lambda x, y: (0x0c0f15, 1)),
                                        (stroke(place([circle]), .8 * k), lambda x, y: (0xffffff, 0x66 / 255)),
                                        (place(numerals), lambda x, y: (0xffffff, 1))])


# The T9 keyboard's glyphs and the search field, each in its own box.
KB_GLYPHS = {'kb_del': ((22, 18), (('M8 1h15v16H8L1 9Z', None, 0xffffff), ('M12 6 18 12', 2, 0x5b6472), ('M18 6 12 12', 2, 0x5b6472))),
             'kb_shift': ((22, 18), (('M9 1 17 10h-4v7H5v-7H1Z', None, 0xffffff),)),
             'kb_back': ((13, 24), (('M12 2 2 12l10 10', 2.6, 0xffffff),)),
             'field_glass': ((16, 16), (('M11 6.5A4.5 4.5 0 1 1 2 6.5 4.5 4.5 0 1 1 11 6.5Z', 2, 0x8a8f96), ('M10 10l4.5 4.5', 2.4, 0x8a8f96)))}
KB_VIEW = {'kb_del': (24, 18), 'kb_shift': (18, 18), 'kb_back': (14, 24), 'field_glass': (16, 16)}
def kb_glyph(name):
    """A glyph's PNG: its viewBox fitted and centred in its box, each part filled, or stroked open at its width."""
    (w, h), parts = KB_GLYPHS[name]
    vw, vh = KB_VIEW[name]
    k = min(w / vw, h / vh)
    dx, dy = (w - vw * k) / 2, (h - vh * k) / 2
    place = lambda polys: [[(x * k + dx, y * k + dy) for x, y in poly] for poly in polys]
    layers = [((place(path(d)) if width is None else place(stroke(path(d), width, closed=d.endswith('Z')))), lambda x, y, c=rgb: (c, 1)) for d, width, rgb in parts]
    return png(w, h, compose((0, 0, w, h), layers))


FIELD_W, FIELD_H = 32, 31  # the field's image: 15 px caps round a column the payload stretches, the row below it in the image
def field():
    """The search field's RGBA bytes: R 15 white, its ring inside, its top inset soft (0, 2, blur 2), the row below it."""
    w, h, box = FIELD_W, FIELD_H, (0, 0, FIELD_W, FIELD_H)
    body = path(rounded_path(0, 0, 32, 30, 15))[0]
    inner = path(rounded_path(1, 1, 30, 28, 14))[0]
    ring = [*body, body[0], inner[0], *inner[::-1], inner[0], body[0]]
    held = coverage([[(x, y + 2) for x, y in body]], box)  # the inset's hole, 2 rows down
    kernel = [math.exp(-k * k / 2) for k in range(-3, 4)]
    at = lambda v, x, y: v[max(0, min(h - 1, y)) * w + max(0, min(w - 1, x))]
    across = [sum(a * at(held, x + i - 3, y) for i, a in enumerate(kernel)) / sum(kernel) for y in range(h) for x in range(w)]
    soft = [sum(a * at(across, x, y + i - 3) for i, a in enumerate(kernel)) / sum(kernel) for y in range(h) for x in range(w)]
    return compose(box, [([[(x, y + 1) for x, y in body]], lambda x, y: (0xffffff, 0x59 / 255)), ([body], lambda x, y: (0xffffff, 1)),
                         ([body], lambda x, y: (0, (1 - soft[int(y) * w + int(x)]) * 0x4d / 255)), ([ring], lambda x, y: (0, 0x2e / 255))])

# The band's rim as images, as its corners bend it: inset 0 1px white at 0x59 along its top, 0 1px white at 0x1f under its
# foot; a strip each of the corner's rows and the rim's, for the band's 322 x 46, R 8.
@functools.lru_cache(maxsize=None)
def band_rim():
    w, h = 322, 47
    box, down = rounded(w, h, 0, 0, w, 46, 8), rounded(w, h, 0, 1, w, 46, 8)
    rgba = inked(w, h, [([b * (1 - d) for b, d in zip(box, down)], 0x59, WHITE), (outside(down, box), 0x1f, WHITE)])
    return png(w, 9, rgba[:9 * 4 * w]), png(w, 9, rgba[(h - 9) * 4 * w:])


# A coverage table per radius of the round boxes the painter draws a row at a time (navigation.c paint_round_rows): their
# corners' pixels take the row's colour at this alpha, so they stay the scheme's.
CORNERS = (2, 5, 6, 7, 8, 9, 10, 11, 12, 13)
def corner_cover(r):
    """A radius r corner's coverage out of 255, r rows of r pixels, the outermost first."""
    n = 16
    inside = lambda x, y: sum(math.hypot(r - x - (i + .5) / n, r - y - (j + .5) / n) <= r for i in range(n) for j in range(n))
    return bytes(round(inside(x, y) * 255 / n ** 2) for y in range(r) for x in range(r))
def corner_table():
    """The tables as a C initialiser, by radius."""
    rows = (f'[{r}] = (const unsigned char[]){{ {",".join(map(str, corner_cover(r)))} }}' for r in CORNERS)
    return '{ ' + ', '.join(rows) + ' }'


# The rating panel's hearts: filled, or the outline alone, in the panel's ink; 32 x 30.
RATE_HEART = 'M16 28C6 20 2 15 2 9.6 2 2 12 0 16 7.2 20 0 30 2 30 9.6 30 15 26 20 16 28Z'
RATE_HEART_INKS = {'l': 0x000000, 'd': 0xffffff}
def rate_heart(on, rgb):
    shape = path(RATE_HEART)
    return png(32, 30, compose((0, 0, 32, 30), [((shape if on else []) + stroke(shape, 2.4), lambda x, y: (rgb, 1))]))
def ink(rgb, alpha):
    return lambda x, y: (rgb, alpha / 255)
@functools.lru_cache(maxsize=None)
def tray(bw):
    """A tray: rows, a light row under it, 0 2px 5px black at 0x66. The box's top-left is at TRAY_PAD, TRAY_PAD."""
    p = TRAY_PAD
    w, h = bw + 2 * p, TRAY_H + 2 * p + 2
    box = rounded(w, h, p, p, bw, TRAY_H, TRAY_R)
    soft = blurred(rounded(w, h, p, p + 2, bw, TRAY_H, TRAY_R), w, h, 2.5)
    rows = gradient(TRAY_STOPS, p, p + TRAY_H)
    return png(w, h, laid(w, h, [(outside(soft, box), ink(0, 0x66)),
                                 (outside(rounded(w, h, p, p + 1, bw, TRAY_H, TRAY_R), box), ink(0xffffff, 0x26)),
                                 (box, lambda x, y: (rows(y), 1))]))
@functools.lru_cache(maxsize=None)
def cell(bw, lit):
    """A cell. Lit: inset 0 1px white at 0x66, 0 1px 2px black at 0xaa. Unlit: inset 0 1px 1px black at 0xaa, a 1 px ring
    inside at 0x77, a light row under it. The box's top-left is at CELL_PAD, CELL_PAD."""
    p = CELL_PAD
    w, h = bw + 2 * p, CELL_H + 2 * p
    box, lower = (rounded(w, h, p, p + d, bw, CELL_H, CELL_R) for d in (0, 1))
    rows = gradient(CELL_LIT if lit else CELL_OFF, p, p + CELL_H)
    fill = (box, lambda x, y: (rows(y), 1))
    if lit:
        top = [b * (1 - c) for b, c in zip(box, lower)]
        layers = [(outside(blurred(lower, w, h, 1), box), ink(0, 0xaa)), fill, (top, ink(0xffffff, 0x66))]
    else:
        inner = rounded(w, h, p + 1, p + 1, bw - 2, CELL_H - 2, CELL_R - 1)
        layers = [(outside(lower, box), ink(0xffffff, 0x24)), fill, ([b * (1 - c) for b, c in zip(box, inner)], ink(0, 0x77)),
                  ([b * (1 - c) for b, c in zip(box, blurred(lower, w, h, .5))], ink(0, 0xaa))]
    return png(w, h, laid(w, h, layers))
@functools.lru_cache(maxsize=None)
def tile():
    """The calendar tile: its page, its 22-row header (a light top row), R 6 above and 7 below; black at 0x66 on the row
    under it, 0 3px 4px black at 0x59. The box's top-left is at TILE_PAD, TILE_PAD."""
    p, bw, bh = TILE_PAD, TILE_W, TILE_H
    w, h = bw + 2 * p, bh + 2 * p
    def shape(dy):
        x0, x1, y0, y1 = p, p + bw, p + dy, p + bh + dy
        d = (f'M{x0 + 6} {y0}H{x1 - 6}A6 6 0 0 1 {x1} {y0 + 6}V{y1 - 7}A7 7 0 0 1 {x1 - 7} {y1}'
             f'H{x0 + 7}A7 7 0 0 1 {x0} {y1 - 7}V{y0 + 6}A6 6 0 0 1 {x0 + 6} {y0}Z')
        return [min(c, 1) for c in coverage(path(d), (0, 0, w, h))]
    box = shape(0)
    page, head = gradient(TILE_PAGE, p, p + bh), gradient(TILE_TOP, p, p + TILE_HEAD)
    rows = lambda a, b: [c if a <= i // w - p < b else 0 for i, c in enumerate(box)]
    return png(w, h, laid(w, h, [(outside(blurred(shape(3), w, h, 2), box), ink(0, 0x59)), (outside(shape(1), box), ink(0, 0x66)),
                                 (box, lambda x, y: (page(y), 1)), (rows(0, TILE_HEAD), lambda x, y: (head(y), 1)),
                                 (rows(0, 1), ink(0xffffff, 0x55))]))
def numeral(text, size, cx, cy):
    """text's outlines in the theme's font as one even-odd polygon each glyph, centred on cx, its baseline 3.3 / 9.5 of size under cy."""
    font = classic_light_font()
    glyphs, cmap, s = font.getGlyphSet(), font.getBestCmap(), size / font['head'].unitsPerEm
    x = cx - sum(glyphs[cmap[ord(c)]].width for c in text) * s / 2
    out = []
    for c in text:
        glyph = glyphs[cmap[ord(c)]]
        pen = SVGPathPen(glyphs, ntos=lambda v: f'{v:.3f}')
        glyph.draw(TransformPen(pen, (s, 0, 0, -s, x, cy + size * 3.3 / 9.5)))
        contours = path(pen.getCommands())
        out.append([q for one in contours for q in (*one, one[0], contours[0][0])])  # the bridges cancel: counters stay open
        x += glyph.width * s
    return out
@functools.lru_cache(maxsize=None)
def clock_chrome():
    """The clock without its hands, drawn on a -50..50 box."""
    k = CLOCK_PX / 100
    fit = lambda polys: [[(CLOCK_PX / 2 + x * k, CLOCK_PX / 2 + y * k) for x, y in poly] for poly in polys]
    disc = lambda r: path(f'M{-r} 0A{r} {r} 0 1 1 {r} 0A{r} {r} 0 1 1 {-r} 0Z')
    ramp = lambda stops, r: gradient(stops, CLOCK_PX / 2 - r * k, CLOCK_PX / 2 + r * k)
    ring, rim = ramp(CLOCK_RING, 49.5), ramp(CLOCK_RIM, 44)
    a = [i * math.pi / 6 for i in range(1, 13)]
    numerals = [q for i in range(12) for q in numeral(str(i + 1), 9.5, 34 * math.sin(a[i]), -34 * math.cos(a[i]))]
    box = (0, 0, CLOCK_PX, CLOCK_PX)
    return png(CLOCK_PX, CLOCK_PX, compose(box, [
        (fit(disc(49.5)), lambda x, y: (ring(y), 1)),
        (fit(stroke(path('M-41 -22A46.5 46.5 0 0 1 41 -22'), 1.6, closed=False)), ink(0xffffff, .95 * 255)),
        (fit(disc(44)), ink(0xffffff, 255)),
        (fit(stroke(disc(44), 1.2)), lambda x, y: (rim(y), 1)),
        (fit(numerals), ink(0x5a6068, 255))]))
@functools.lru_cache(maxsize=None)
def clock_shadow():
    """The shadow under the face: a radial ramp out to the corners' ellipse, as CSS's, inside the box's own ellipse."""
    w, h = CLOCK_SHADOW
    def paint(x, y):
        d = math.hypot((x - w / 2) / (w / 2), (y - h / 2) / (h / 2)) / math.sqrt(2)
        a = 0x70 + (0x40 - 0x70) * d / .45 if d < .45 else 0x40 * max(0, .72 - d) / .27
        return 0, a / 255
    return png(w, h, compose((0, 0, w, h), [(path(f'M0 {h / 2}A{w / 2} {h / 2} 0 1 1 {w} {h / 2}A{w / 2} {h / 2} 0 1 1 0 {h / 2}Z'), paint)]))


def images(t):
    """Theme t's generated images, {name: PNG bytes}; those its values state."""
    out, prefix = {}, t['id'].replace('-', '_')
    if t['chevron_x']:  # white on the band in both halves, its point on the middle row
        out[f'{prefix}_sort_bars'] = png(*SORT_BOX[2:], compose(SORT_BOX, [(path(SORT), lambda x, y: (0xffffff, 1))]))
        out[f'{prefix}_chevron'] = png(*CHEVRON_BOX[2:], compose(CHEVRON_BOX, [
            (stroke(path(CHEVRON), CHEVRON_STROKE, closed=False), lambda x, y: (0xffffff, 1))]))
    if t['bolt']:
        shape = path(BOLT)
        out[f'{prefix}_bolt'] = png(*BOLT_BOX[2:], compose(BOLT_BOX, [(stroke(shape, 1.6), lambda x, y: (0xffffff, t['bolt_halo_a'] / 255)),
                                                                       (shape, lambda x, y: (t['bolt'], 1))]))
        out[f'{prefix}_plug'] = png(*BOLT_BOX[2:], compose(BOLT_BOX, [(path(PLUG), lambda x, y: (t['bolt'], 1))]))
    if t['np_track']:  # the iPod's bar on Now Playing: its volume's ends
        white = lambda x, y: (0xffffff, 1)
        waves = stroke([p for d in WAVES for p in path(d)], 1.5, closed=False)
        out[f'{prefix}_vol_lo'] = png(*SPEAKER_BOX[2:], compose(SPEAKER_BOX, [(path(SPEAKER), white)]))
        out[f'{prefix}_vol_hi'] = png(*WAVES_BOX[2:], compose(WAVES_BOX, [(path(SPEAKER), white), (waves, white)]))
    if t['state_stops']:
        fill, edge = gradient(t['state_stops'], *STATE_Y), gradient(t['state_edge'], *STATE_Y)
        for name, d in STATE.items():
            shape = path(d)
            for half, halo in (('l', t['state_halo_light']), ('d', t['state_halo_dark'])):
                out[f'{prefix}_{name}_{half}'] = png(*STATE_BOX[2:], compose(STATE_BOX, [
                    *[(stroke(shape, 2.6), lambda x, y, a=halo / 255: (0xffffff, a))][:halo > 0],  # none at alpha 0
                    (shape, lambda x, y: (fill(y), 1)),
                    (stroke(shape, 1), lambda x, y: (edge(y), 1))]))
    drawing = ROOT/f'assets/themes/{t["id"]}/note.svg'
    if drawing.exists():
        out[f'{prefix}_note'] = png(*NOTE_BOX[2:], note(drawing.read_text()))
    if t['pane_icon_x']:
        for name, d in PANE_ICONS.items():
            fit = name != 'note'
            out[f'{prefix}_icon_{name}'] = png(*(ICON_BOX if fit else NOTE_BOX)[2:], icon(d, fit))
    if t['painter[PAINT_VOLUME]']:
        for half, rgb in SPEAKER_INKS.items():
            out |= {f'{prefix}_spk_quiet_{half}': speaker(False, rgb), f'{prefix}_spk_loud_{half}': speaker(True, rgb)}
    if t['painter[PAINT_PAGES]']:
        out |= {f'{prefix}_sun_{name}': sun(*a) for name, a in SUNS.items()}
        out |= {f'{prefix}_{name}_w': png(10, 11, compose((0, 0, 10, 11), [(path(d), lambda x, y: (0xffffff, 1))])) for name, d in DARK_STATE.items()}
        out |= {f'{prefix}_orb_note': png(116, 116, orb(100, False)), f'{prefix}_orb_usb': png(130, 130, orb(114, True))}
        out |= {f'{prefix}_{name}': shadow(*a) for name, a in SHADOWS.items()}
        out |= {f'{prefix}_track_inset': track_inset(), f'{prefix}_lit_edge': lit_edge()}
        for name, (glyph, lit, opacity) in QS_BUTTONS.items():
            out |= {f'{prefix}_drop_{name}{k}': qs_mark(glyph, opacity, QS_INK[k]) for k in (['', '_l'] if lit else ['_l', '_d'])}
        out |= {f'{prefix}_drop_track': qs_track(), f'{prefix}_drop_knob': qs_knob()}
        out[f'{prefix}_clock_dark'] = png(CLOCK_DARK_PX, CLOCK_DARK_PX, clock_dark())
        out |= {f'{prefix}_{name}': kb_glyph(name) for name in KB_GLYPHS}
        out[f'{prefix}_field'] = png(FIELD_W, FIELD_H, field())
        out[f'{prefix}_band_top'], out[f'{prefix}_band_foot'] = band_rim()
        for half, rgb in RATE_HEART_INKS.items():
            out |= {f'{prefix}_heart_on_{half}': rate_heart(True, rgb), f'{prefix}_heart_off_{half}': rate_heart(False, rgb)}
        out |= {f'{prefix}_tray_{w}': tray(w) for w in TRAY_WIDTHS}
        for w in CELL_WIDTHS:
            out |= {f'{prefix}_cell_{w}': cell(w, False), f'{prefix}_cell_lit_{w}': cell(w, True)}
        out |= {f'{prefix}_tile': tile(), f'{prefix}_clock_chrome': clock_chrome(), f'{prefix}_clock_shadow': clock_shadow(), f'{prefix}_hint_shadow': hint_shadow()}
    if t['painter[PAINT_CONFIRM]']:
        out[f'{prefix}_confirm_shadow'] = png(*TRAY_SHADOW_BOX[2:], tray_shadow())
    if t['station_stops']:  # Internet Radio's Now Playing art, as large as the slanted art (navigation.c stream_ipod)
        px = inc('NP_IPOD_ART')
        out[f'{prefix}_station'] = png(px, px, station(t['station_stops'], t['station_mast'], t['station_band_a'], t['station_shadow_a'], px))
    return out
