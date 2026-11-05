"""Original raster art the theme files state in numbers, drawn at build time: small vector shapes, antialiased
over sub-rows, written as 8-bit RGBA PNGs. No Apple assets or reference pixels; every shape is drawn here."""
import math
import re
import struct
import zlib
from build import ROOT

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
    """An SVG path's d (absolute M L H V C A Z, an M's further pairs lines) as closed polylines."""
    tokens = re.findall(r'[MLHVCAZ]|-?[0-9.]+', d)
    polys, pts, i, cmd = [], [], 0, None

    def num():
        nonlocal i
        i += 1
        return float(tokens[i - 1])
    while i < len(tokens):
        if tokens[i] in 'MLHVCAZ':
            cmd = tokens[i]
            i += 1
        if cmd == 'Z':
            polys.append(pts)
            pts = []
            continue
        if cmd == 'M':
            pts = [(num(), num())]
            cmd = 'L'
        elif cmd == 'L':
            pts.append((num(), num()))
        elif cmd == 'H':
            pts.append((num(), pts[-1][1]))
        elif cmd == 'V':
            pts.append((pts[-1][0], num()))
        elif cmd == 'C':
            p0, p1, p2, p3 = pts[-1], (num(), num()), (num(), num()), (num(), num())
            for k in range(1, 25):
                t = k / 24
                pts.append(tuple((1 - t)**3 * p0[a] + 3 * (1 - t)**2 * t * p1[a] + 3 * (1 - t) * t**2 * p2[a] + t**3 * p3[a] for a in (0, 1)))
        elif cmd == 'A':
            rx, ry, phi, large, sweep = num(), num(), num(), num(), num()
            pts += _arc(pts[-1], rx, ry, phi, large, sweep, (num(), num()))
    return polys + ([pts] if pts else [])


def stroke(polys, width):
    """Closed polylines' stroke, round joined, as polygons a union fills."""
    r, out = width / 2, []
    for poly in polys:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
            n = math.hypot(x1 - x0, y1 - y0)
            if n:
                nx, ny = (y1 - y0) / n * r, (x0 - x1) / n * r
                out.append([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)])
            out.append([(x1 + r * math.cos(a * math.pi / 8), y1 + r * math.sin(a * math.pi / 8)) for a in range(16)])
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
BOLT = 'M305.6 6 301.8 12.4 304.6 12.4 303.8 17.2 308 10.6 305.2 10.6 306.6 6Z'
BOLT_BOX = (292, 5, 27, 13)  # the battery's inside (navigation.c nano_battery)
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


NOTE_BOX = (222, 66, 100, 115)  # the drawing's viewBox
STATE = {'play': 'M270.5 4.5 282.5 11.5 270.5 18.5Z', 'pause': 'M270.5 4.5H275V18.5H270.5ZM278 4.5H282.5V18.5H278Z'}
STATE_BOX = (266, 0, 20, 22)  # img_state's box in the bar (tools/ipod.py status_bar)
STATE_Y = (4, 19)  # the fill's and edge's first and last rows


def images(t):
    """Theme t's generated images, {name: PNG bytes}; those its values state."""
    out = {}
    if t['bolt']:
        shape = path(BOLT)
        out[f'{t["id"]}_bolt'] = png(*BOLT_BOX[2:], compose(BOLT_BOX, [(stroke(shape, 1.6), lambda x, y: (0xffffff, t['bolt_halo_a'] / 255)),
                                                                       (shape, lambda x, y: (t['bolt'], 1))]))
    if t['state_stops']:
        fill, edge = gradient(t['state_stops'], *STATE_Y), gradient(t['state_edge'], *STATE_Y)
        for name, d in STATE.items():
            shape = path(d)
            for half, halo in (('l', t['state_halo_light']), ('d', t['state_halo_dark'])):
                out[f'{t["id"]}_{name}_{half}'] = png(*STATE_BOX[2:], compose(STATE_BOX, [
                    (stroke(shape, 2.6), lambda x, y, a=halo / 255: (0xffffff, a)),
                    (shape, lambda x, y: (fill(y), 1)),
                    (stroke(shape, 1), lambda x, y: (edge(y), 1))]))
    drawing = ROOT/f'assets/themes/{t["id"]}/note.svg'
    if drawing.exists():
        out[f'{t["id"]}_note'] = png(*NOTE_BOX[2:], note(drawing.read_text()))
    return out
