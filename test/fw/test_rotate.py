"""The screen's flush (patch/rotate.c): ringnav_rotate, with stand-ins for stock's bitmap calls. For the Q2's turn,
270, each pixel of the rectangle goes where stock's rotate_bgra8888_image puts it, image pixel (x, y) at framebuffer
pixel (image height - 1 - y, x), and nothing else on the page changes, the bytes either side of it included; every
other case goes to stock's body with its own arguments. Like stock's, the hook checks no bounds: AWTK's flushes stay
inside the canvas, and so does every rectangle here. test/patch.py's scenarios/rotate.py runs the hook against that
body itself, with a stock build."""
import ctypes as C
import random

import pytest

from conftest import block

GUARD = 64  # bytes either side of the page, compared too
STOCK = 0x5a  # what the stand-in for stock's body answers
LOCKS = ('write', 'read', 'unlock', 'unlock')  # the page locked for writing, the canvas for reading, both unlocked


class Flush:
    """A framebuffer page and a canvas (each width, height, line bytes) of random bytes, as stock's accessors give
    them: ok says whether each lock gives its buffer, shift moves each buffer's start off a word. calls: the hook's
    calls to stock, in order."""
    def __init__(self, fw, page=(320, 375, 1280), canvas=(375, 320, 1500), ok=(True, True), shift=(0, 0)):
        (_, h, line), (_, self.ih, self.iline) = page, canvas
        self.line, rnd = line, random.Random(270)
        self.fb, self.img = block(fw, size=0x48), block(fw, size=0x48)  # bitmap_t: only the stand-ins read them
        self.page, self.canvas = rnd.randbytes(h * line + 2 * GUARD), rnd.randbytes(self.ih * self.iline)
        self.p, c = block(fw, size=len(self.page) + 4), block(fw, size=len(self.canvas) + 4)
        self.d0, self.s0 = self.p + GUARD + shift[0], c + shift[1]
        self.calls = []
        size = {self.fb: (line, h), self.img: (self.iline, self.ih)}
        fw.mock('bitmap_get_physical_line_length', lambda b, *_: size[b][0])
        fw.mock('bitmap_get_physical_height', lambda b, *_: size[b][1])
        fw.mock('bitmap_lock_buffer_for_write', lambda b, *_: self.note('write', b) or (self.d0 if ok[0] else 0))
        fw.mock('bitmap_lock_buffer_for_read', lambda b, *_: self.note('read', b) or (self.s0 if ok[1] else 0))
        fw.mock('bitmap_unlock_buffer', lambda b, *_: self.note('unlock', b))
        fw.mock('stock_rotate_trampoline',
                lambda fb, img, r, o, *_: self.note('stock', fb, img, r, C.c_int(o).value) or STOCK)
        self.rotate = fw.fn('ringnav_rotate', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int)

    def note(self, *call):
        self.calls.append(call)

    def run(self, rect, o=270):
        """The hook's answer and the page after it, from the first bytes each time; the canvas is only read."""
        C.memmove(self.p, self.page, len(self.page))
        C.memmove(self.s0, self.canvas, len(self.canvas))
        self.r, self.calls = (C.c_int * 4)(*rect), []
        got = self.rotate(self.fb, self.img, C.addressof(self.r), o)
        assert C.string_at(self.s0, len(self.canvas)) == self.canvas
        return got, C.string_at(self.p, len(self.page))

    def turned(self, rect):
        """The page as stock's turn leaves it: the image's row j is the framebuffer's column ih - 1 - j."""
        out, (x, y, w, h) = bytearray(self.page), rect
        for j in range(y, y + h):
            for i in range(x, x + w):
                d, s = GUARD + i * self.line + (self.ih - 1 - j) * 4, j * self.iline + 4 * i
                out[d:d + 4] = self.canvas[s:s + 4]
        return bytes(out)

    def locked(self):
        return [(k, b) for k, b in zip(LOCKS, (self.fb, self.img, self.fb, self.img))]


def test_turns_each_pixel_as_stock(fw):
    """The Q2's own pair: the whole frame, Cover Flow's band and its captions', every corner, the four edge lines, an
    odd rectangle and random ones. Stock's body is never asked."""
    f, rnd = Flush(fw), random.Random(375)
    rects = [(0, 0, 375, 320), (0, 30, 375, 282), (0, 260, 375, 52), (13, 7, 101, 63),
             *((x, y, 1, 1) for x in (0, 374) for y in (0, 319)),
             (0, 0, 375, 1), (0, 319, 375, 1), (0, 0, 1, 320), (374, 0, 1, 320)]
    for _ in range(12):
        x, y = rnd.randrange(375), rnd.randrange(320)
        rects.append((x, y, rnd.randint(1, 375 - x), rnd.randint(1, 320 - y)))
    for r in rects:
        got, page = f.run(r)
        assert got == 0 and page == f.turned(r), r
        assert f.calls == f.locked(), r


@pytest.mark.parametrize('page, canvas', [((320, 375, 1292), (375, 320, 1508)),  # padded lines
                                          ((320, 375, 1280), (375, 200, 1500)),  # a shorter canvas
                                          ((37, 101, 148), (101, 37, 404))])  # an odd-sized pair
def test_goes_by_each_bitmaps_lines(fw, page, canvas):
    f, (w, h, _) = Flush(fw, page, canvas), canvas
    for r in [(0, 0, w, h), (3, 5, w - 9, h - 7)]:
        got, out = f.run(r)
        assert got == 0 and out == f.turned(r), r


WHOLE = (0, 0, 375, 320)


@pytest.mark.parametrize('o, rect, kw, locks', [
    *((o, WHOLE, {}, False) for o in (0, 90, 180, -90, 271)),  # another turn
    *((270, r, {}, False) for r in ((0, 0, 0, 320), (0, 0, 375, 0), (5, 5, -1, 10))),  # nothing to turn
    (270, WHOLE, {'canvas': (375, 320, 1502)}, True), (270, WHOLE, {'page': (320, 375, 1282)}, True),  # lines off a word
    (270, WHOLE, {'shift': (2, 0)}, True), (270, WHOLE, {'shift': (0, 1)}, True),  # buffers off a word
    (270, WHOLE, {'ok': (False, True)}, True), (270, WHOLE, {'ok': (True, False)}, True)])  # a lock that fails
def test_hands_the_rest_to_stock(fw, o, rect, kw, locks):
    """Stock's own body, with the hook's arguments, and its answer is the hook's. The hook writes nothing itself, and
    any lock it took is let go before stock's body runs."""
    f = Flush(fw, **kw)
    got, page = f.run(rect, o)
    assert got == STOCK and page == f.page
    assert f.calls == [*(f.locked() if locks else []), ('stock', f.fb, f.img, C.addressof(f.r), o)]
