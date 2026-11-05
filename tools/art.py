"""Original raster art the theme files state in numbers, drawn at build time: small vector shapes, antialiased
over sub-rows, written as 8-bit RGBA PNGs. No Apple assets or reference pixels; every shape is drawn here."""
import math
import re
import struct
import zlib
from build import ROOT
from ipod import STATE_W

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
        p = max(0, min(100, (y - y0) * 100 / (y1 - y0)))
        for (pa, ca), (pb, cb) in zip(stops, stops[1:]):
            if p <= pb:
                t = (p - pa) / (pb - pa)
                return sum(round((ca >> s & 255) + ((cb >> s & 255) - (ca >> s & 255)) * t) << s for s in (16, 8, 0))
        return stops[-1][1]
    return at


def compose(box, layers):
    """box's RGBA bytes: layers (polygons, paint(x, y) -> (0xRRGGBB, alpha 0 to 1)[, clip polygons]) source-over,
    straight alpha."""
    x0, y0, w, h = box
    px = [(0.0, 0.0, 0.0, 0.0)] * (w * h)
    for polys, paint, *clip in layers:
        cov = coverage(polys, box)
        if clip:
            cov = [a * b for a, b in zip(cov, coverage(clip[0], box))]
        for i, c in enumerate(cov):
            if c:
                rgb, a = paint(x0 + i % w + 0.5, y0 + i // w + 0.5)
                a *= min(c, 1)
                r, g, b, d = px[i]
                out = a + d * (1 - a)
                px[i] = (*((v * d * (1 - a) + (rgb >> s & 255) * a) / out for v, s in ((r, 16), (g, 8), (b, 0))), out)
    return b''.join(bytes((round(r), round(g), round(b), round(a * 255))) for r, g, b, a in px)


def png(w, h, rgba):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    rows = b''.join(b'\0' + rgba[y * 4 * w:(y + 1) * 4 * w] for y in range(h))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


# Screen coordinates, the bar's rows and columns; each image's box is where the payload draws it.
BOLT = 'M302.6 6 298.8 12.4 301.6 12.4 300.8 17.2 305 10.6 302.2 10.6 303.6 6Z'
PLUG = 'M295 9.4h3.4v1.5H295ZM295 12.1h3.4v1.5H295ZM298.2 7.8h4.3a3.7 3.7 0 0 1 0 7.4h-4.3ZM305 10.8h5v1.5h-5Z'
BOLT_BOX = (290, 5, 26, 13)  # the battery's inside (navigation.c classic_light_battery)
def note(svg):
    """The note's RGBA bytes from its drawing, note.svg: see that file."""
    def linear(name):
        """The gradient's stops [(pos %, alpha 0 to 1, grey 0 to 1)] and its axis's ends."""
        g = re.search(rf'<linearGradient id="{name}"([^>]*)>(.*?)</linearGradient>', svg, re.S)
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
    shape = path(re.search(r'id="shape" d="([^"]*)"', svg)[1])
    gloss = path(re.search(r'id="gloss" d="([^"]*)"', svg)[1])
    fill, gfill, fade = linear('shape_fill'), linear('gloss_fill'), linear('gloss_fade')
    return compose(NOTE_BOX, [(shape, lambda x, y: (0xffffff, at(fill, y, 1))),
                              (gloss, lambda x, y: (0xffffff, at(gfill, y, 1) * at(fade, x, 2)), shape),
                              (stroke(shape, 1), lambda x, y: (0xffffff, 0.42))])


SORT = 'M0 0H16V2H0ZM0 5H11V7H0ZM0 10H6V12H0Z'
SORT_BOX = (0, 0, 16, 12)  # Songs' Sort, white as stock's glyphs: Light Mode flips it to the ink
# Home's pane (coverflow.c): original drawings in the note's coordinates. Each but the note is fitted into a 92 px box
# centred on PANE_CENTRE, the note's own place standing for its size; all share one fill and stroke (icon).
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
STATE = {'play': 'M270.5 4.5 282.5 11.5 270.5 18.5Z', 'pause': 'M270.5 4.5H275V18.5H270.5ZM278 4.5H282.5V18.5H278Z'}
STATE_BOX = (266, 0, STATE_W, 22)  # the frame the state art is drawn in, on the bar's rows; the art itself is position-free
STATE_Y = (4, 19)  # the fill's and edge's first and last rows


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
    return out
