"""patch/books.c from the card to the page (docs/internals.md#books): the Books page's list, a book opened, laid
out, paged both ways through its window and left at its mark, an EPUB prepared on its worker, stored deflate blocks,
and Videos' output, DAC and q2video's start (#videos)."""
import ctypes as C
import io
import random
import re
import struct
import zipfile
import zlib

import pytest

from conftest import FNV_PRIME, O, Files, block, claim, fnv1a

ROOT = '/mnt/mmc/Books'
MARKS = '/mnt/data/ringnav-books'
BOOK_DIR = '/mnt/mmc/.books'
VIDEO_BIN = b'/usr/bin/q2video'
READY, PREPARING, BAD = range(3)
LIST, READER = range(2)
RET_REPEAT, RET_STOP = 8, 11
CENTER, PREV, NEXT, RETURN = O['KEY_CENTER'], O['KEY_PREV'], O['KEY_NEXT'], O['KEY_RETURN']
EVT_CLICK, EVT_DESTROY, EVT_KEY_UP = O['EVT_CLICK'], O['EVT_DESTROY'], O['EVT_KEY_UP']
SCREEN_W, SCREEN_H = 375, 320
CHAR_W = 10  # each character's advance on this canvas, in px
THREAD = 0x7e57


class Mark(C.Structure):
    _fields_ = [('key', C.c_uint), ('pos', C.c_uint)]


class Bk(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page list view reader sheet info path'.split()),
                *((k, C.c_int) for k in 'n screen target current state info_on'.split()),
                ('file', C.c_char_p), ('cache', C.c_char * 48), ('tmp', C.c_char * 48),
                *((k, C.c_uint) for k in 'key size pos end prev base len'.split()),
                ('buf', C.c_void_p), ('thread', C.c_ulong), ('timer', C.c_uint), ('poll', C.c_uint), ('running', C.c_int),
                ('finished', C.c_int), ('result', C.c_int), ('cancel', C.c_int), ('marks', Mark * 64)]


class Vid(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'paused volume gain pid sock seek'.split()),
                *((k, C.c_uint) for k in 'polled control_at seek_at key at paused_at'.split())]


def book_key(path):
    """A book's key in the marks: FNV-1a of its path and a separator multiply (coverflow.c fnv), never 0."""
    return (fnv1a(path.encode()) * FNV_PRIME & 0xffffffff) or 1


def marks(*pairs):
    """The marks file: 64 key/offset pairs, the most recent first."""
    return b''.join(struct.pack('<II', *p) for p in pairs).ljust(512, b'\0')


WORDS = 'the a of to in reading pages wheel turns café naïve €5 Zoë résumé long-winded paragraph ends here'.split()


def prose(seed, size):
    """Paragraphs of words, one a line, about size bytes: no word as wide as a line, so lines break between words."""
    rng = random.Random(seed)
    paras, n = [], 0
    while n < size:
        paras.append(' '.join(rng.choice(WORDS) for _ in range(rng.randrange(2, 30))))
        n += len(paras[-1].encode()) + 1
    return '\n'.join(paras).encode()


def epub(*chapters):
    """An EPUB of chapters, each one document's paragraph, in spine order."""
    items = ''.join(f'<item id="c{k}" href="c{k}.xhtml" media-type="application/xhtml+xml"/>' for k in range(len(chapters)))
    refs = ''.join(f'<itemref idref="c{k}"/>' for k in range(len(chapters)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('mimetype', 'application/epub+zip')
        z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
        z.writestr('OEBPS/c.opf', f'<package><manifest>{items}</manifest><spine>{refs}</spine></package>')
        for k, text in enumerate(chapters):
            z.writestr(f'OEBPS/c{k}.xhtml', f'<html><body><p>{text}</p></body></html>')
    return buf.getvalue()


class Rect:
    def __init__(self, x, y, w, h):
        self.x, self.y, self.w, self.h = (C.c_int(v).value for v in (x, y, w, h))


class Widgets:
    """AWTK as far as the page reads it: each constructor a zeroed widget holding its box and parent; the texts,
    int props and visibility set, and the handlers bound."""
    def __init__(self, fw):
        self.blocks, self.made, self.text, self.props, self.shown, self.handlers = [], [], {}, {}, {}, []
        for name in ('window_create', 'scroll_view_create', 'list_view_create', 'list_item_create', 'image_create',
                     'hscroll_label_create'):
            fw.mock(name, lambda parent, x, y, w, h, *_, kind=name: self.new(kind, parent, x, y, w, h))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.new(
            C.string_at(kind).decode(), parent, x, y, w, SCREEN_H))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_set_prop_int', lambda w, k, v, *_: self.props.__setitem__((w, C.string_at(k).decode()), C.c_int(v).value))
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: self.props.get((w, C.string_at(k).decode()), C.c_int(d).value))
        fw.mock('widget_set_visible', lambda w, v, *_: self.shown.__setitem__(w, C.c_int(v).value))
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)))

    def new(self, kind, parent, x, y, w, h):
        self.blocks.append(C.create_string_buffer(0x200))
        struct.pack_into('<4i', self.blocks[-1], 0, *(C.c_int(v).value for v in (x, y, w, h)))
        struct.pack_into('<Q', self.blocks[-1], O['W_PARENT'], parent)
        self.made.append((kind, C.addressof(self.blocks[-1]), parent))
        return C.addressof(self.blocks[-1])

    def get_text(self, w, out, n, *_):
        s = self.text.get(w, '').encode()[:n - 1]
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0

    def of(self, kind, parent):
        return [w for k, w, p in self.made if k == kind and p == parent]

    def handler(self, w, event):
        (fn, ctx), = [(fn, ctx) for ww, evt, fn, ctx in self.handlers if ww == w and evt == event][-1:]
        return lambda e=0: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, e)


def folders(fw, paths):
    """opendir and readdir over the card's files: each folder's files (d_type 8) and subfolders (4)."""
    class Dirent(C.Structure):
        _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]
    tree, open_dirs, keep = {}, {}, []
    for path in paths:
        parts = path.split('/')
        for k in range(2, len(parts)):
            entry = (parts[k], 8 if k == len(parts) - 1 else 4)
            listed = tree.setdefault('/'.join(parts[:k]), [])
            if entry not in listed:
                listed.append(entry)

    def opendir(path, *_):
        name = C.string_at(path).decode()
        if name not in tree:
            return 0
        open_dirs[0x3000 + len(open_dirs)] = iter(tree[name])
        return 0x3000 + len(open_dirs) - 1

    def readdir(d, *_):
        for name, kind in open_dirs[d]:
            keep.append(Dirent(0, 0, 0, kind, name.encode()))
            return C.addressof(keep[-1])
        return 0
    fw.mock('fw_opendir', opendir)
    fw.mock('fw_readdir', readdir)


class Shelf:
    """The Books page over a card of files: its rows, timers and worker, the reader as books_paint draws it on a
    canvas whose characters are CHAR_W wide (measure=False: no width, so only line breaks end a line)."""
    def __init__(self, fw, files, measure=True, thread=0, root=ROOT):
        self.fw, self.root = fw, root
        self.bk = fw.var('books__bk', Bk)
        self.card = Files(fw, dict(files))
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p).decode() in self.card.files else -1)
        self.unlinked, self.joins, self.removed, self.left, self.timers, self.workers = [], [], [], [], [], []
        self.started = 0
        fw.mock('fw_unlink', lambda p, *_: self.unlink(C.string_at(p).decode()))
        folders(fw, self.card.files)
        self.w = Widgets(fw)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('timer_remove', lambda t, *_: self.removed.append(t))
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.start(t, fn) if not thread else thread)
        fw.mock('fw_pthread_join', lambda t, *_: self.joins.append((t, self.bk.cancel)))
        fw.mock('navigator_back', lambda *_: self.left.append(1))
        self.lcd = C.create_string_buffer(0x200)
        struct.pack_into('<I', self.lcd, O['LCD_TEXT_COLOR'], 0xff123456)  # the colours the next widget paints in
        struct.pack_into('<I', self.lcd, O['LCD_FILL_COLOR'], 0xff654321)
        self.canvas = C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.canvas, O['CANVAS_LCD'], C.addressof(self.lcd))
        self.drawn, self.fills, self.inks, self.texts, self.fonts = [], [], [], [], []
        if measure:
            fw.mock('canvas_measure_text', lambda canvas, s, n, *_: CHAR_W * n)
        fw.mock('canvas_draw_text', lambda canvas, s, n, x, y, *_: self.drawn.append(
            (C.c_int(x).value, C.c_int(y).value, ''.join(map(chr, (C.c_uint * n).from_address(s))))))
        fw.mock('canvas_fill_rect', lambda canvas, *box: self.fills.append(Rect(*box[:4])))
        fw.mock('canvas_set_fill_color', lambda canvas, c, *_: self.inks.append(c & 0xffffffff))
        fw.mock('canvas_set_text_color', lambda canvas, c, *_: self.texts.append(c & 0xffffffff))
        fw.mock('canvas_set_font', lambda canvas, name, size, *_: self.fonts.append((name, size)))
        self.open()

    def open(self):
        self.fw.fn('books_open', None, C.c_char_p)(self.root.encode())

    def unlink(self, path):
        self.unlinked.append(path)
        self.card.files.pop(path, None)

    def start(self, t, fn):
        C.c_ulong.from_address(t).value = THREAD
        self.workers.append(fn)
        self.started += 1
        return 0

    def address(self, name):
        return C.cast(getattr(self.fw.lib, 'books__' + name), C.c_void_p).value

    def fire(self, name):
        """The newest timer running name, as the next loop pass would."""
        fn = [f for f, _ in self.timers if f == self.address(name)][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)

    def run_worker(self):
        assert C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.workers.pop())(None) is None

    def items(self):
        return self.w.of('list_item_create', self.bk.view)

    def rows(self):
        return [self.w.text[self.w.of('hscroll_label_create', item)[0]] for item in self.items()]

    def click(self, row):
        """The row pressed, and the reader its timer opens."""
        self.w.handler(self.items()[row], EVT_CLICK)()
        assert self.fire('to_reader') == 0

    def key(self, key):
        return self.fw.fn('books_key', C.c_int, C.c_void_p, C.c_uint)(self.bk.page, key)

    def back(self):
        """Return's release on the page, and the leave its timer runs."""
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, O['EVENT_KEY'], RETURN)
        assert self.w.handler(self.bk.page, EVT_KEY_UP)(C.addressof(event)) == RET_STOP
        assert self.fire('leave') == 0

    def close(self):
        """The page destroyed, as leaving Books does."""
        self.w.handler(self.bk.page, EVT_DESTROY)()

    def paint(self, widget=None):
        """books_paint on the reader's sheet (or widget): the lines drawn, as (x, y, text)."""
        self.drawn, self.fills = [], []
        self.fw.fn('books_paint', None, C.c_void_p, C.c_void_p)(self.bk.sheet if widget is None else widget, C.addressof(self.canvas))
        return self.drawn

    def read(self):
        """Every page from here on: [(its start, its lines' text)], the wheel turned until it stops."""
        pages = []
        while True:
            pages.append((self.bk.pos, [line for _, _, line in self.paint()]))
            here = self.bk.pos
            assert self.key(NEXT) == 1
            if self.bk.pos == here:
                return pages

    def caption(self):
        return self.w.text.get(self.bk.info) if self.w.shown.get(self.bk.info) else None


# The list.

def test_the_shelf_lists_each_book_by_path_under_its_name(fw):
    files = {f'{ROOT}/b.txt': b'b', f'{ROOT}/Sub/c.TXT': b'c', f'{ROOT}/A.epub': b'', f'{ROOT}/.hidden.txt': b'h',
             f'{ROOT}/film.mp4': b'', f'{ROOT}/notes.pdf': b'', f'{ROOT}/Sub/Deep/d.txt': b'd'}
    shelf = Shelf(fw, files)
    assert shelf.rows() == ['A', 'c', 'b']  # by path, byte order: "A.epub" < "Sub/" < "b.txt"
    shelf.click(1)
    assert shelf.bk.file == f'{ROOT}/Sub/c.TXT'.encode() and shelf.bk.screen == READER


def test_an_empty_shelf_says_so(fw):
    shelf = Shelf(fw, {f'{ROOT}/notes.pdf': b''})
    assert shelf.rows() == [] and 'No books' in shelf.w.text.values()


def test_the_page_opens_once(fw):
    shelf = Shelf(fw, {f'{ROOT}/a.txt': b'a'})
    page = shelf.bk.page
    shelf.open()
    assert shelf.bk.page == page and len(shelf.w.of('window_create', 0)) == 1 and shelf.rows() == ['a']


def test_no_page_no_shelf(fw):
    fw.mock('window_create', lambda *_: 0)
    reads = []
    fw.mock('fw_fopen', lambda path, *_: reads.append(C.string_at(path)) and 0)
    fw.fn('books_open', None, C.c_char_p)(ROOT.encode())
    assert not fw.var('books__bk', Bk).page and reads == []


# The reader.

def test_a_book_reads_page_by_page_with_every_word_once(fw):
    text = prose(1, 2500)
    shelf = Shelf(fw, {f'{ROOT}/b.txt': text})
    shelf.click(0)
    assert shelf.w.shown[shelf.bk.reader] == 1 and shelf.w.shown[shelf.bk.list] == 0
    pages = shelf.read()
    rows, = {len(lines) for _, lines in pages[:-1]}  # every page but the last is full, all of one height
    assert len(pages) > 3 and rows > 1 and 0 < len(pages[-1][1]) <= rows
    assert [w for _, lines in pages for line in lines for w in line.split()] == text.decode().split()
    for start, lines in pages:  # each page opens on the file's own text
        assert text[start:].decode().lstrip('\n').startswith(lines[0])


def test_each_line_fits_between_equal_margins_on_even_rows(fw):
    shelf = Shelf(fw, {f'{ROOT}/b.txt': prose(2, 1500)})
    shelf.click(0)
    tops = set()
    while True:
        drawn = shelf.paint()
        (x,), ys = {x for x, _, _ in drawn}, [y for _, y, _ in drawn]
        assert x > 0 and all(len(line) * CHAR_W <= SCREEN_W - 2 * x for _, _, line in drawn)
        pitch = ys[1] - ys[0]
        assert pitch > 0 and ys == [ys[0] + k * pitch for k in range(len(ys))]
        tops.add((ys[0], pitch))
        here = shelf.bk.pos
        shelf.key(NEXT)
        if shelf.bk.pos == here:
            break
    assert len(tops) == 1 and ys[0] > 0  # every page starts on the same row


def test_paging_back_never_skips_text_and_stops_at_the_start(fw):
    shelf = Shelf(fw, {f'{ROOT}/b.txt': prose(3, 2500)})
    shelf.click(0)
    assert shelf.key(PREV) == 1 and shelf.bk.pos == 0  # nothing before the first page
    forward = shelf.read()
    back = 0
    while True:
        here = shelf.bk.pos
        shelf.key(PREV)
        if shelf.bk.pos == here:
            break
        shelf.paint()
        assert shelf.bk.pos < here <= shelf.bk.end  # the page before reaches this one
        back += 1
    assert shelf.bk.pos == 0 and back >= len(forward) - 1


def test_the_window_follows_the_page_through_a_long_book(fw):
    lines = [b'line %05d ' % k + b'.' * 88 + b'\n' for k in range(1000)]  # 100 bytes, a row each
    text, path = b''.join(lines), f'{ROOT}/long.txt'
    shelf = Shelf(fw, {path: text, MARKS: marks((book_key(path), 50000))}, measure=False)
    shelf.click(0)
    assert shelf.bk.pos == 50000 and shelf.bk.len < len(text)  # never read whole
    bases, heights = set(), set()

    def page():
        drawn = [line.encode() + b'\n' for _, _, line in shelf.paint()]
        assert drawn == lines[shelf.bk.pos // 100:shelf.bk.pos // 100 + len(drawn)]
        assert shelf.bk.base <= shelf.bk.pos < shelf.bk.base + shelf.bk.len
        bases.add(shelf.bk.base)
        heights.add(len(drawn))

    def walk(key, far):
        for _ in range(100):  # more pages than either walk takes
            page()
            if far(shelf.bk.pos):
                return
            shelf.key(key)
        raise AssertionError('the wheel stopped short')
    walk(PREV, lambda pos: pos < 50000 - (8 << 10))  # behind where the first window starts, 8 KB before the mark
    walk(NEXT, lambda pos: pos > 50000 + (32 << 10))  # and past where any 32 KB from there ends
    assert len(bases) >= 3  # read again behind the page and ahead of it
    rows, = heights  # mid-book, every page full: the window never cuts one short
    assert rows > 1


def test_the_bar_under_the_page_fills_to_where_the_page_ends(fw):
    shelf = Shelf(fw, {f'{ROOT}/b.txt': prose(4, 2500)})
    shelf.click(0)
    done = []
    while True:
        drawn = shelf.paint()
        track, bar = shelf.fills[-2:]
        assert (bar.x, bar.y, bar.h) == (track.x, track.y, track.h) and bar.w <= track.w
        assert track.y > drawn[-1][1] and track.y + track.h <= SCREEN_H and track.x + track.w <= SCREEN_W
        assert abs(bar.w - track.w * shelf.bk.end // shelf.bk.size) <= 1
        done.append(bar.w)
        here = shelf.bk.pos
        shelf.key(NEXT)
        if shelf.bk.pos == here:
            break
    assert 0 < done[0] < done[-1] == track.w and done == sorted(done)


def test_a_book_over_12_mb_keeps_its_bar_in_proportion(fw):
    shelf = Shelf(fw, {f'{ROOT}/b.txt': prose(5, 600)})
    shelf.click(0)
    b = shelf.bk
    b.size, b.base, b.pos = 0xf0000000, 0xe0000000, 0xe0000000  # where 32-bit end * width would overflow
    shelf.paint()
    track, bar = shelf.fills[-2:]
    assert abs(bar.w - track.w * b.end / b.size) <= 1 and bar.w < track.w


def test_paint_puts_the_canvas_colours_back_and_uses_the_default_font(fw):
    shelf = Shelf(fw, {f'{ROOT}/b.txt': prose(6, 300)})
    shelf.click(0)
    shelf.paint()
    assert shelf.texts[-1] == 0xff123456 and shelf.inks[-1] == 0xff654321  # as the lcd had them
    assert len(shelf.texts) >= 2 and shelf.fonts and all(name == 0 for name, _ in shelf.fonts)


def test_only_the_reader_s_sheet_gets_the_page(fw):
    shelf = Shelf(fw, {f'{ROOT}/b.txt': prose(7, 300)})
    shelf.click(0)
    assert shelf.paint(shelf.bk.list) == [] and shelf.paint(shelf.bk.reader) == [] and shelf.fills == []
    assert shelf.paint()


def test_centre_shows_how_far_in_and_the_title_then_hides_it(fw):
    shelf = Shelf(fw, {f'{ROOT}/My Book.txt': prose(8, 2500)})
    shelf.click(0)
    assert shelf.caption() is None

    def shown():
        caption = shelf.caption()
        pct = re.match(r'(\d+)%', caption)
        assert pct and 'My Book' in caption and '.txt' not in caption  # the title, without its extension
        return int(pct[1])
    assert shelf.key(CENTER) == 1 and shown() == 0
    seen = []
    for _ in range(4):
        shelf.paint()
        shelf.key(NEXT)
        seen.append(shown())
    assert seen == sorted(seen) and 0 < seen[0] and seen[-1] < 100
    shelf.key(CENTER)
    assert shelf.caption() is None


def test_return_keeps_the_page_and_the_book_reopens_there(fw):
    path = f'{ROOT}/b.txt'
    shelf = Shelf(fw, {path: prose(9, 4000), f'{ROOT}/c.txt': b'other'})
    shelf.click(0)
    for _ in range(3):
        shelf.paint()
        shelf.key(NEXT)
    page, pos = shelf.paint(), shelf.bk.pos
    shelf.back()
    assert shelf.bk.screen == LIST and shelf.w.shown[shelf.bk.list] == 1 and shelf.w.shown[shelf.bk.reader] == 0
    assert shelf.card.files[MARKS][:8] == struct.pack('<II', book_key(path), pos)
    assert shelf.w.props[(shelf.bk.view, '_ringnav_index')] == 0  # the list's bar on the book
    shelf.back()
    assert shelf.left == [1]  # Return on the list leaves Books
    shelf.close()
    assert not shelf.bk.page and not shelf.bk.buf and shelf.bk.n == 0
    shelf.open()
    shelf.click(0)
    assert shelf.bk.pos == pos and shelf.paint() == page
    shelf.back()
    shelf.click(1)  # another book: its own mark, none
    assert shelf.bk.pos == 0 and shelf.card.files[MARKS][:8] == struct.pack('<II', book_key(path), pos)
    shelf.back()
    assert shelf.w.props[(shelf.bk.view, '_ringnav_index')] == 1


def test_closing_the_page_keeps_the_page_read_and_lets_go(fw):
    path = f'{ROOT}/b.txt'
    shelf = Shelf(fw, {path: prose(10, 3000)})
    shelf.click(0)
    shelf.paint()
    shelf.key(NEXT)
    page, pos = shelf.bk.page, shelf.bk.pos
    shelf.close()
    assert shelf.card.files[MARKS][:8] == struct.pack('<II', book_key(path), pos)
    assert fw.fn('books_key', C.c_int, C.c_void_p, C.c_uint)(page, NEXT) == 0  # its keys are stock's again
    assert shelf.paint() == [] and not shelf.bk.page


@pytest.mark.parametrize('gone', [False, True])
def test_an_empty_or_missing_book_says_it_can_t_open(fw, gone):
    path = f'{ROOT}/b.txt'
    shelf = Shelf(fw, {path: b'' if not gone else b'text'})
    if gone:
        del shelf.card.files[path]  # listed, then gone from the card before it opened
    shelf.click(0)
    assert shelf.bk.state == BAD and shelf.caption() == "Can't open this book" and shelf.paint() == []
    shelf.back()
    assert MARKS not in shelf.card.files  # no page to keep


# EPUB.

def epub_shelf(fw, data, **kw):
    path = f'{ROOT}/b.epub'
    key = book_key(path)
    return Shelf(fw, {path: data}, **kw), f'{BOOK_DIR}/{key:08x}.txt', f'{BOOK_DIR}/{key:08x}.tmp'


def test_an_epub_is_prepared_once_then_read_a_chapter_a_page(fw):
    shelf, cache, tmp = epub_shelf(fw, epub('One two three', 'Four &amp; five'))
    shelf.click(0)
    assert shelf.bk.state == PREPARING and shelf.caption() == 'Preparing…'
    assert shelf.paint() == [] and shelf.key(NEXT) == 1 and shelf.bk.pos == 0  # the wheel is the reader's, for nothing
    assert shelf.fire('poll') == RET_REPEAT  # still converting
    shelf.run_worker()
    assert shelf.fire('poll') == 0
    assert cache in shelf.card.files and tmp not in shelf.card.files
    assert shelf.bk.state == READY and shelf.caption() is None
    assert shelf.read() == [(0, ['One two three']), (13, ['Four & five'])]
    shelf.back()
    shelf.click(0)  # opened again: the text at once, at its page
    assert shelf.started == 1
    assert shelf.bk.state == READY and [line for *_, line in shelf.paint()] == ['Four & five']


def test_an_epub_that_won_t_convert_says_so_and_keeps_no_page(fw):
    shelf, cache, tmp = epub_shelf(fw, b'PK not a zip')
    shelf.click(0)
    shelf.run_worker()
    assert shelf.fire('poll') == 0
    assert shelf.bk.state == BAD and shelf.caption() == "Can't open this book" and tmp in shelf.unlinked
    assert shelf.paint() == [] and cache not in shelf.card.files
    shelf.back()
    assert MARKS not in shelf.card.files


def test_no_worker_no_book(fw):
    shelf, _, _ = epub_shelf(fw, epub('One'), thread=11)
    shelf.click(0)
    assert shelf.bk.state == BAD and shelf.caption() == "Can't open this book"
    assert not [fn for fn, _ in shelf.timers if fn == shelf.address('poll')]


def test_leaving_while_preparing_cancels_and_joins_the_worker(fw):
    shelf, cache, tmp = epub_shelf(fw, epub('One'))
    shelf.click(0)
    poll = len(shelf.timers)
    shelf.back()
    assert shelf.joins == [(THREAD, 1)]  # cancelled first, then joined
    assert poll in shelf.removed and tmp in shelf.unlinked and not shelf.bk.running
    assert shelf.bk.screen == LIST and shelf.bk.state == READY and MARKS not in shelf.card.files


def test_closing_while_preparing_cancels_too(fw):
    shelf, _, tmp = epub_shelf(fw, epub('One'))
    shelf.click(0)
    shelf.close()
    assert shelf.joins == [(THREAD, 1)] and tmp in shelf.unlinked and MARKS not in shelf.card.files


# Stored deflate blocks (book_inflate).

def inflate(fw, packed, cap, n=None):
    """book_inflate of the stream's first n bytes (all of them by default), packed whole in the buffer."""
    out = block(fw, size=cap)
    got = fw.fn('book_inflate', C.c_int, C.c_void_p, C.c_uint, C.c_void_p, C.c_uint)(
        block(fw, packed, size=len(packed)), len(packed) if n is None else n, out, cap)
    return C.string_at(out, got) if got >= 0 else None


def stored(data, final=1, padding=0):
    """One stored block (RFC 1951 3.2.4): BFINAL, BTYPE 00 and five bits of padding, LEN, NLEN, the bytes."""
    return bytes([final | padding << 3]) + struct.pack('<HH', len(data), len(data) ^ 0xffff) + data


def test_stored_blocks_come_out_byte_for_byte(fw):
    plain = bytes(range(256)) * 300  # over 65535 bytes: two stored blocks
    c = zlib.compressobj(0, zlib.DEFLATED, -15)
    packed = c.compress(plain) + c.flush()
    assert inflate(fw, packed, len(plain)) == plain
    assert inflate(fw, packed, len(plain) - 1) is None


def test_a_stored_block_is_checked_against_its_complement_and_the_input(fw):
    good = stored(b'hello')
    assert inflate(fw, good, 5) == b'hello'
    for n in range(len(good)):  # the stream cut anywhere short, though the buffer holds it all
        assert inflate(fw, good, 5, n) is None, n
    assert inflate(fw, good[:1] + struct.pack('<HH', 5, 4 ^ 0xffff) + b'hello', 5) is None  # NLEN isn't LEN's complement


def test_a_stored_block_s_padding_bits_are_ignored(fw):
    assert inflate(fw, stored(b'hel', final=0, padding=0x1f) + stored(b'lo', padding=0x15), 5) == b'hello'


def test_a_sync_flush_s_empty_stored_block_joins_two_runs(fw):
    first, second = b'first run of words ' * 20, b'and the second run ' * 20
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = c.compress(first) + c.flush(zlib.Z_SYNC_FLUSH) + c.compress(second) + c.flush()
    assert inflate(fw, packed, len(first + second)) == first + second


# Videos: the output q2video plays on, the DAC, and the helper's start.

@pytest.fixture
def mixer():
    """USB_MIXER at stock's address, put back after: conftest's map outlives a load."""
    claim(O['USB_MIXER'])
    cell = C.c_int.from_address(O['USB_MIXER'])
    saved = cell.value
    yield cell
    cell.value = saved


@pytest.mark.parametrize('way, usbvol, level, volume, device, vol', [
    (0, 0, 0, 40, None, b'40'),               # the headphone DAC, which keeps the volume set
    (1, 0, 0, 40, b'plug:bluealsa', b'40'),   # hciplayer's soft volume
    (2, 1, -2, 40, b'usb', b'40'),            # a variable USB volume on a DAC with no control: soft
    (2, 1, 0, 40, b'usb', b'h40'),            # the DAC's own control applies it: the helper only shows it
    (2, 0, -2, 40, b'usb', b'h40'),           # a fixed USB volume
    (2, 0, 0, 100, b'usb', b'h100'),          # the longest, in its 5 bytes
])
def test_the_output_q2video_plays_on(fw, mixer, way, usbvol, level, volume, device, vol):
    fw.mock('mclGetOutputWay', lambda *_: way)
    fw.var('g_volume', C.c_ubyte).value = volume
    fw.var('g_usbvol_mode', C.c_ubyte).value = usbvol
    mixer.value = level
    out = block(fw, size=5)
    assert fw.fn('output_device', C.c_char_p, C.c_void_p)(out) == device and C.string_at(out) == vol


@pytest.mark.parametrize('output, dacoff, channel, power', [
    (0, 0, [(0, 2)], []), (1, -1, [(1, 2)], []),  # a jack: headset mode, as player_start
    (2, -1, [], [1]), (2, 0, [], []), (2, 5, [], []),  # Bluetooth's way left alone; the DAC powered when off (-1)
])
def test_the_dac_is_readied_as_stock_readies_it_for_a_track(fw, output, dacoff, channel, power):
    fw.var('g_headset_output', C.c_ubyte).value = output
    fw.var('fw_ctx_g_dacoff_time', C.c_int).value = dacoff
    fw.var('g_volume', C.c_ubyte).value = 33
    fw.fn('dac_take', None)()
    assert [args[:2] for _, args in fw.calls('config_outputchannel')] == channel
    assert [args[0] for _, args in fw.calls('mclSetDacPwr')] == power
    assert fw.calls('mclSetPcmMode') and [args[0] for _, args in fw.calls('mclSetMute')] == [0]
    assert [args[:2] for _, args in fw.calls('device_set_volume')] == [(33, 1)]


def execl(fw):
    """The child's execl calls, each up to its NULL (the stub sees six arguments), read while its stack holds them."""
    calls = []

    def run(*args):
        calls.append([])
        for a in args:
            if not a:
                break
            calls[-1].append(C.string_at(a))
        return -1
    fw.mock('fw_execl', run)
    return calls


@pytest.mark.parametrize('way, radio, want', [
    (0, 0, [VIDEO_BIN, VIDEO_BIN, b'plughw:0,0', b'/m/a.mp4']),
    (1, 0, [VIDEO_BIN, VIDEO_BIN, b'plug:bluealsa', b'/m/a.mp4', b'40']),
    (0, 1, [VIDEO_BIN, VIDEO_BIN, b'-r', b'plughw:0,0', b'/m/a.mp4']),
    (1, 1, [VIDEO_BIN, VIDEO_BIN, b'-r', b'plug:bluealsa', b'/m/a.mp4', b'40']),
])
def test_the_child_runs_q2video_on_the_output_and_exits_127_if_it_can_t(fw, way, radio, want):
    fw.mock('mclGetOutputWay', lambda *_: way)
    fw.var('g_volume', C.c_ubyte).value = 40
    fw.mock('fw_fork', lambda *_: 0)
    exits, calls = [], execl(fw)
    fw.mock('child_exit', lambda status, *_: exits.append(status))
    fw.fn('video_start', C.c_int, C.c_char_p, C.c_int)(b'/m/a.mp4', radio)
    assert calls == [want] and exits == [127]


def test_q2video_starts_once_the_player_lets_go_of_the_pcm(fw):
    order = []
    for name in ('player_stop', 'mclSetMute', 'fw_fork'):
        fw.mock(name, lambda *_, n=name: order.append(n) or (4321 if n == 'fw_fork' else 0))
    fw.mock('mclGetOutputWay', lambda *_: 0)
    start = fw.fn('video_start', C.c_int, C.c_char_p, C.c_int)
    assert start(b'/m/a.mp4', 0) == 4321 and order == ['player_stop', 'mclSetMute', 'fw_fork']
    order.clear()
    fw.mock('mclGetOutputWay', lambda *_: 1)  # Bluetooth: the DAC left alone
    assert start(b'/m/a.mp4', 0) == 4321 and order == ['player_stop', 'fw_fork']
    fw.mock('fw_fork', lambda *_: -1)
    assert start(b'/m/a.mp4', 0) == 0  # no process


def test_the_helper_s_own_pause_is_read_unless_a_key_just_set_it(fw):
    card = Files(fw)
    now = [100000]
    fw.mock('time_now_ms', lambda *_: now[0])
    v = fw.var('books__vid', Vid)
    v.pid = 5
    v.volume = fw.var('g_volume', C.c_ubyte).value = 30
    v.gain = fw.fn('headset_gain', C.c_int)()
    poll = fw.fn('video_poll', None)

    def state(paused, later=400):
        card.files['/tmp/q2video.state'] = struct.pack('<i', paused)
        now[0] += later
        poll()
        return v.paused
    assert state(1) == 1 and state(0) == 0
    assert state(7) == 0 and state(-1) == 0  # neither paused nor playing: kept
    fw.fn('video_key', None, C.c_uint)(O['KEY_PLAY'])  # paused by a key
    assert v.paused == 1 and state(0, 400) == 1  # the helper hasn't caught up: its stale state waits
    assert state(0, 600) == 0  # a second after the key, its state stands
