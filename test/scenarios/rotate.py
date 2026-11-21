"""The screen's flush (patch/rotate.c): ringnav_rotate, entered where soft_rotate_image calls
rotate_bgra8888_image, against stock's own body through its trampoline, byte for byte over the whole page
and the bytes either side of it. The Q2's own pair (a 320 x 375 page of 1280-byte lines, the 375 x 320
canvas of 1500), its whole frame, Cover Flow's band, every corner, edge and line and random rectangles;
padded lines, a shorter canvas and an odd-sized pair; and what stock keeps: other turns, empty rectangles,
lines or pixels that aren't whole words."""
import random
from q2emu import *

ENTRY = HOOKS['rotate_bgra8888_image'][0]  # its jump to ringnav_rotate, where soft_rotate_image calls it
STOCK = int(manifest['patch_symbols']['stock_rotate_trampoline'], 16)
POOL, POOL_SIZE = 0x6000000, 0x400000
GUARD = 64  # bytes either side of the page, compared too


class RotateMachine(Machine):
    """Stock's own accessors read the bitmap_t (w @0, h @4, line @8, flags and format @0xc); the read lock,
    as Machine's write lock does, gives the pixels' address kept at +0x14. Counts entries to stock's body,
    and instructions from the start (a block hook added later misses the blocks already translated)."""
    def __init__(self):
        super().__init__()
        self.u.mem_map(POOL, POOL_SIZE)
        self.img, self.pages = POOL, (POOL + 0x100000, POOL + 0x200000)
        self.structs = POOL + 0x300000
        self.stock_runs = self.insns = 0
        self.u.hook_add(UC_HOOK_CODE, lambda *a: setattr(self, 'stock_runs', self.stock_runs + 1), begin=STOCK, end=STOCK)
        self.u.hook_add(UC_HOOK_BLOCK, lambda u, a, size, x: setattr(self, 'insns', self.insns + size // 4))

    def hook(self, u, address, size, unused):
        if self.handlers.get(address) == 'bitmap_lock_buffer_for_read':
            self.returns(self.get(u.reg_read(REGS[0]) + 0x14), clobber=True)
            return
        return super().hook(u, address, size, unused)

    def bitmap(self, at, w, h, line, pixels):
        self.word(at, w); self.word(at + 4, h); self.word(at + 8, line)
        self.u.mem_write(at + 0xc, struct.pack('<HH', 0, O['LCD_BGRA8888'])); self.word(at + 0x14, pixels)
        return at


m = RotateMachine()
rnd = random.Random(270)


def rotate(fb_size, fb_line, img_size, img_line, rect, o=270, img_shift=0):
    """Both runs on the same page and canvas: (stock's, ours) results equal, and whether ours handed it on."""
    (fw, fh), (iw, ih) = fb_size, img_size
    canvas = rnd.randbytes(ih * img_line)
    page = rnd.randbytes(fh * fb_line + 2 * GUARD)
    m.u.mem_write(m.img + img_shift, canvas)
    img = m.bitmap(m.structs, iw, ih, img_line, m.img + img_shift)
    r = m.structs + 0x80
    for i, v in enumerate(rect): m.word(r + 4 * i, v)
    out = []
    for k, entry in enumerate((STOCK, ENTRY)):
        m.u.mem_write(m.pages[k], page)
        fb = m.bitmap(m.structs + 0x40 + 0x100 * k, fw, fh, fb_line, m.pages[k] + GUARD)
        runs = m.stock_runs
        ret = m.call(address=entry, args=(fb, img, r, o), gap=0, count=20_000_000)
        out.append((ret, bytes(m.u.mem_read(m.pages[k], len(page))), m.stock_runs - runs))
        assert bytes(m.u.mem_read(m.img + img_shift, len(canvas))) == canvas  # only read
    (r1, p1, _), (r2, p2, handed) = out
    assert r1 == r2 == 0 and p1 == p2, (fb_size, fb_line, img_size, img_line, rect, o)
    assert p1 != page or rect[2] <= 0 or rect[3] <= 0 or o not in (90, 180, 270)  # something was copied
    return handed


Q2 = ((320, 375), 1280, (375, 320), 1500)
for rect in ((0, 0, 375, 320), (0, 30, 375, 282), (0, 260, 375, 52),  # the frame, Cover Flow's band, its captions'
             (0, 0, 1, 1), (374, 0, 1, 1), (0, 319, 1, 1), (374, 319, 1, 1),  # the corners
             (0, 0, 375, 1), (0, 319, 375, 1), (0, 0, 1, 320), (374, 0, 1, 320), (13, 7, 101, 63)):
    assert not rotate(*Q2, rect), rect
for _ in range(32):
    x, y = rnd.randrange(375), rnd.randrange(320)
    assert not rotate(*Q2, (x, y, rnd.randint(1, min(96, 375 - x)), rnd.randint(1, min(96, 320 - y))))
passed()
# lines padded past the pixels, a canvas shorter than the page is wide, an odd-sized pair
for fb_line, img_line in ((1292, 1500), (2048, 1500), (1280, 1508), (1312, 1536)):
    for rect in ((0, 256, 375, 64), (17, 41, 200, 99)):
        assert not rotate((320, 375), fb_line, (375, 320), img_line, rect), (fb_line, img_line, rect)
assert not rotate((320, 375), 1280, (375, 200), 1500, (0, 0, 375, 200))
assert not rotate((320, 375), 1280, (375, 200), 1500, (40, 150, 9, 50))
for rect in ((0, 0, 101, 37), (100, 36, 1, 1), (5, 3, 61, 19)):
    assert not rotate((37, 101), 148, (101, 37), 404, rect), rect
passed()
# stock's own: the other turns (a square pair, in reach of each), empty rectangles, and lines or pixels that
# aren't whole words, which word loads can't take
for o in (0, 90, 180):
    assert rotate((64, 64), 256, (64, 64), 256, (3, 5, 40, 30), o) == 1, o
for rect in ((10, 10, 0, 5), (10, 10, 5, 0)):
    assert rotate(*Q2, rect) == 1, rect
assert rotate((320, 375), 1280, (375, 320), 1502, (0, 30, 375, 282)) == 1
assert rotate((320, 375), 1282, (375, 320), 1500, (0, 30, 375, 282)) == 1
assert rotate(*Q2, (0, 30, 375, 282), img_shift=2) == 1
passed()
# A Cover Flow frame's flush in instructions: word copies, about 6 a pixel
fb = m.bitmap(m.structs + 0x40, 320, 375, 1280, m.pages[0] + GUARD)
img = m.bitmap(m.structs, 375, 320, 1500, m.img)
r = m.structs + 0x80
for i, v in enumerate((0, 30, 375, 282)): m.word(r + 4 * i, v)
n = m.insns
m.call(address=ENTRY, args=(fb, img, r, 270), gap=0, count=20_000_000)
n = m.insns - n
assert 5 * 375 * 282 < n < 7 * 375 * 282, n
cost_line('screen flush rotate 375x282', n); passed()
