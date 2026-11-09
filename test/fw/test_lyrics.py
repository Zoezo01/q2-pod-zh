"""patch/lyrics.c: LRC text into lines and word stamps, and the page's wrap, clock and lit width."""
import ctypes as C
import random

import pytest

from conftest import block, text

FAR = 0x7fffffff
TAG_MAX = 65536
PRIME = 16777619
SEED = 2166136261


class Mark(C.Structure):  # lrc_mark, and ly_mark, the same two words
    _fields_ = [('ms', C.c_int), ('at', C.c_uint)]


class Line(C.Structure):
    _fields_ = [('time', C.c_int), ('at', C.c_int), ('text', C.c_char_p), ('mark', C.c_uint), ('marks', C.c_uint)]


class LyLine(C.Structure):
    _fields_ = [('ms', C.c_int), ('text', C.c_uint), ('n', C.c_uint), ('mark', C.c_uint), ('marks', C.c_uint),
                ('px', C.c_ubyte), ('rows', C.c_ubyte), ('start', C.c_ushort * 4), ('len', C.c_ushort * 4), ('w', C.c_ushort * 4)]


class Tab(C.Structure):
    _fields_ = [('n', C.c_uint), ('sum', C.c_uint), ('line', C.POINTER(LyLine)), ('mark', C.POINTER(Mark)),
                ('text', C.POINTER(C.c_uint)), ('adv', C.POINTER(C.c_ushort))]


class Ly(C.Structure):
    _fields_ = [('tab', C.POINTER(Tab)), ('next', C.POINTER(Tab)), ('lrc', C.c_void_p), ('rows', C.c_uint), ('sum', C.c_uint),
                ('ms', C.c_int), ('cur', C.c_int), ('fill', C.c_int), ('lit_at', C.c_uint), ('sec', C.c_int), ('run', C.c_int),
                ('guess', C.c_int), ('lo', C.c_int), ('hi', C.c_int), ('pos', C.c_int), ('w', C.c_int)]


def state(fw):
    return fw.var('lyrics__ly', Ly)


def stamp(fw, s, opener=b'[', closer=b']'):
    fn = fw.fn('lyrics__lrc_time', C.c_void_p, C.c_void_p, C.c_char, C.c_char, C.POINTER(C.c_int))
    buf, ms = text(s), C.c_int(-1)
    end = fn(buf, opener, closer, C.byref(ms))
    return (ms.value, end - C.addressof(buf)) if end else None


@pytest.mark.parametrize('s,ms,length', [
    (b'[01:02]', 62000, 7), (b'[01:02]x', 62000, 7),
    (b'[01:02.5]', 62500, 9), (b'[01:02.50]', 62500, 10), (b'[01:02.05]', 62050, 10),  # hundredths
    (b'[01:02:50]', 62500, 10),                                                         # a colon before them
    (b'[01:02.123]', 62123, 11), (b'[01:02.1239]', 62123, 12),                          # past a millisecond: ignored
    (b'[01:02.]', 62000, 8),
    (b'[1:02]', 62000, 6), (b'[0:00]', 0, 6),
    (b'[1234:00]', 74040000, 9),
    (b'[9999:59.999]', 599999999, 13),                                                  # the longest stays under LRC_FAR
])
def test_a_time(fw, s, ms, length):
    assert stamp(fw, s) == (ms, length)


@pytest.mark.parametrize('s', [
    b'', b'[', b'[]', b'[:]', b'[:05]', b'[00]', b'[00:', b'[00:05', b'[00:5]', b'[00:60]', b'[00:123]', b'[12345:00]',
    b'[ar:Name]', b'[00:05.x]', b'[00:05.5', b'[00:05.5x]', b'[-1:00]', b' [00:05]', b'00:05]', b'(00:05)',
])
def test_not_a_time(fw, s):
    assert stamp(fw, s) is None


def test_a_word_stamps_brackets(fw):
    assert stamp(fw, b'<00:01.00>', b'<', b'>') == (1000, 10)
    assert stamp(fw, b'[00:01.00]', b'<', b'>') is None
    assert stamp(fw, b'<00:01.00]', b'<', b'>') is None


def stamps(fw, s, n=8):
    fn = fw.fn('lyrics__lrc_stamps', C.c_int, C.POINTER(C.c_void_p), C.POINTER(C.c_int), C.c_int)
    buf, t = text(s), (C.c_int * max(n, 1))()
    p = C.c_void_p(C.addressof(buf))
    k = fn(C.byref(p), t, n)
    return list(t[:k]), C.string_at(p)


def test_leading_stamps(fw):
    assert stamps(fw, b'[00:01][00:02.50]text [00:03]') == ([1000, 2500], b'text [00:03]')


def test_no_stamps_moves_nothing(fw):
    assert stamps(fw, b'[ar:Name]') == ([], b'[ar:Name]')
    assert stamps(fw, b'') == ([], b'')


def test_stamps_past_the_room_are_passed_over(fw):
    assert stamps(fw, b'[00:01][00:02][00:03]x', 2) == ([1000, 2000], b'x')
    assert stamps(fw, b'[00:01]x', 0) == ([], b'x')


def words(fw, s, cap=8, keep=1, n=0):
    fn = fw.fn('lyrics__lrc_words', None, C.c_void_p, C.c_void_p, C.c_uint, C.POINTER(C.c_uint), C.c_int)
    buf, m, count = text(s), (Mark * 16)(), C.c_uint(n)
    fn(buf, m, cap, C.byref(count), keep)
    return buf.value, [(x.ms, x.at) for x in m[:count.value]]


def test_word_stamps_are_cut_and_noted_at_their_place_in_the_cut_text(fw):
    assert words(fw, b'<00:01.00>one <00:02.50>two') == (b'one two', [(1000, 0), (2500, 4)])
    assert words(fw, b'one<00:01.00>') == (b'one', [(1000, 3)])


def test_word_stamps_kept_or_not_are_cut(fw):
    assert words(fw, b'<00:01.00>one <00:02.00>two', keep=0) == (b'one two', [])


def test_marks_stop_at_their_cap_and_the_text_is_still_cut(fw):
    assert words(fw, b'<00:01.00>a<00:02.00>b<00:03.00>c', cap=2) == (b'abc', [(1000, 0), (2000, 1)])
    assert words(fw, b'<00:01.00>a', cap=0) == (b'a', [])


def test_marks_go_after_those_already_there(fw):
    assert words(fw, b'<00:09.00>a', n=2)[1][2] == (9000, 0)


def test_other_angle_brackets_are_text(fw):
    assert words(fw, b'a <b> c < 1') == (b'a <b> c < 1', [])
    assert words(fw, b'') == (b'', [])


@pytest.mark.parametrize('s,k', [
    (b' ', 1), (b'\t', 1), (b'\v', 1), (b'\f', 1), (b' x', 1),
    (b'\xc2\xa0', 2), (b'\xc2\xa0x', 2),
    (b'\xe3\x80\x80', 3), (b'\xe2\x80\x8b', 3), (b'\xef\xbb\xbf', 3),
    (b'', 0), (b'a', 0), (b'\n', 0), (b'\r', 0), (b'\xc2', 0), (b'\xc2a', 0), (b'\xc2\xa1', 0),
    (b'\xe3\x80', 0), (b'\xe3\x80\x81', 0), (b'\xe2\x80\x8c', 0), (b'\xef\xbb', 0), (b'\xe3', 0),
])
def test_a_space_at_the_start(fw, s, k):
    assert fw.fn('lyrics__lrc_space', C.c_int, C.c_void_p)(block(fw, s)) == k


def trim(fw, s, marks=()):
    fn = fw.fn('lyrics__lrc_trim', C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint)
    buf, m = block(fw, s), (Mark * max(len(marks), 1))(*[Mark(0, at) for at in marks])
    return C.string_at(fn(buf, m, len(marks))), [x.at for x in m[:len(marks)]]


@pytest.mark.parametrize('s,kept', [
    (b'hi', b'hi'), (b'', b''), (b'   ', b''),
    (b'  hi  ', b'hi'), (b'\t\v\fhi\f\v\t', b'hi'),
    (b'a b', b'a b'),
    (b'\xe3\x80\x80hi\xc2\xa0\xef\xbb\xbf \t', b'hi'),
    (b'\xe2\x80\x8b\xe2\x80\x8b', b''),
    (b'\xc2\xa0', b''),
    (b'\xc3\xa9 ', b'\xc3\xa9'),                         # a letter's last byte is no space's
    (b'a\xa0', b'a\xa0'), (b'a\xc2', b'a\xc2'), (b'a\x80\x80', b'a\x80\x80'),
])
def test_trim(fw, s, kept):
    assert trim(fw, s)[0] == kept


def test_marks_move_with_the_trim_and_stay_inside(fw):
    assert trim(fw, b'  hi  ', [0, 1, 2, 3, 4, 6]) == (b'hi', [0, 0, 0, 1, 2, 2])


def order(fw, a, b):
    fn = fw.fn('lyrics__lrc_order', C.c_int, C.c_void_p, C.c_void_p)
    return fn(C.byref(a), C.byref(b))


def test_order_by_time_then_by_the_file(fw):
    late, early = Line(5, 0), Line(1, 9)
    assert order(fw, late, early) == 1 and order(fw, early, late) == -1
    assert order(fw, Line(5, 2), Line(5, 7)) < 0 and order(fw, Line(5, 7), Line(5, 2)) > 0
    assert order(fw, Line(5, 2), Line(5, 2)) == 0


def lrc(fw, raw):
    """lrc_parse's lines as (ms, text, [(mark ms, byte)]). Its calloc'd tables are the firmware's to size."""
    fn = fw.fn('lyrics__lrc_parse', C.c_int, C.c_void_p, C.POINTER(C.POINTER(Line)), C.POINTER(C.POINTER(Mark)))
    buf, lp, mp = block(fw, raw), C.POINTER(Line)(), C.POINTER(Mark)()
    n = fn(buf, C.byref(lp), C.byref(mp))
    out = []
    for i in range(n):
        line = lp[i]
        out.append((line.time, line.text, [(mp[line.mark + k].ms, mp[line.mark + k].at) for k in range(line.marks)]))
    return out


def test_plain_text_is_a_line_a_line_at_no_time(fw):
    assert lrc(fw, b'one\ntwo') == [(FAR, b'one', []), (FAR, b'two', [])]


@pytest.mark.parametrize('raw', [b'a\nb\n', b'a\r\nb\r\n', b'a\rb\r', b'a\r\r\nb', b'\n\na\n\n\nb\n\n', b'a\n   \n\xc2\xa0\nb'])
def test_any_line_ending_and_no_blank_lines(fw, raw):
    assert [line[1] for line in lrc(fw, raw)] == [b'a', b'b']


def test_nothing_to_show(fw):
    assert lrc(fw, b'') == [] and lrc(fw, b'  \n\t\r\n') == []


def test_lrc_lines_keep_their_times(fw):
    assert lrc(fw, b'[00:01.00]a\n[00:02.50] b \n') == [(1000, b'a', []), (2500, b'b', [])]


def test_lrc_tags_and_lines_before_the_first_stamp_go(fw):
    assert lrc(fw, b'[ar:Me]\n[ti:Song]\nplain\n[00:01.00]a\n') == [(1000, b'a', [])]


def test_a_line_without_a_stamp_after_one_goes(fw):
    assert lrc(fw, b'[00:01.00]a\nplain\n[by:x]\n[00:02.00]b') == [(1000, b'a', []), (2000, b'b', [])]


def test_a_line_with_several_stamps_comes_once_for_each_in_time_order(fw):
    assert lrc(fw, b'[00:03.00][00:01.00]a\n[00:02.00]b\n') == [(1000, b'a', []), (2000, b'b', []), (3000, b'a', [])]


def test_a_line_holds_at_most_eight_stamps(fw):
    raw = b''.join(b'[00:0%d.00]' % i for i in range(9)) + b'a'
    assert [line[0] for line in lrc(fw, raw)] == [i * 1000 for i in range(8)]


def test_lines_at_one_time_stay_in_file_order(fw):
    assert [line[1] for line in lrc(fw, b'[00:01.00]z\n[00:01.00]a\n[00:00.50]m\n[00:01.00]b')] == [b'm', b'z', b'a', b'b']


def test_a_stamp_alone_is_a_blank_line_but_still_means_lrc(fw):
    assert lrc(fw, b'plain\n[00:01.00]\n[00:02.00]b') == [(2000, b'b', [])]
    assert lrc(fw, b'plain\n[00:01.00]  ') == []


def test_word_stamps_of_a_synced_line(fw):
    assert lrc(fw, b'[00:01.00]<00:01.00>one <00:02.00>two') == [(1000, b'one two', [(1000, 0), (2000, 4)])]


def test_word_stamps_follow_the_trim(fw):
    assert lrc(fw, b'[00:01.00]  <00:01.00>one<00:02.00>  ') == [(1000, b'one', [(1000, 0), (2000, 3)])]


def test_a_line_shown_twice_has_no_word_stamps(fw):
    assert lrc(fw, b'[00:01.00][00:05.00]<00:01.00>one') == [(1000, b'one', []), (5000, b'one', [])]


def test_an_unsynced_line_loses_its_word_stamps(fw):
    assert lrc(fw, b'<00:01.00>one') == [(FAR, b'one', [])]


def test_a_blank_lines_word_stamps_go_with_it(fw):
    assert lrc(fw, b'[00:01.00]<00:01.00>\n[00:02.00]<00:02.00>b') == [(2000, b'b', [(2000, 0)])]


def test_word_stamps_stay_with_their_lines_after_the_sort(fw):
    assert lrc(fw, b'[00:02.00]<00:02.00>b\n[00:01.00]<00:01.00>a') == [(1000, b'a', [(1000, 0)]), (2000, b'b', [(2000, 0)])]


def test_lines_starting_past_the_tag_limit_are_not_read(fw):
    lines = lrc(fw, b'a\n' * 40000)
    assert len(lines) == TAG_MAX // 2


def test_a_line_crossing_the_limit_is_kept_whole(fw):
    lines = lrc(fw, b'a' * (TAG_MAX - 1) + b'bcd\nnext\n')
    assert len(lines) == 1 and lines[0][1] == b'a' * (TAG_MAX - 1) + b'bcd'


def test_stamps_past_the_limit_cannot_overrun_the_tables(fw):
    raw = b'x' * TAG_MAX + b'[00:01.00]<00:01.00>a<00:02.00>b' * 200 + b'\n'
    assert len(lrc(fw, raw)) <= 1


SAMPLES = [b'[ar:Someone]\n[00:01.00]<00:01.00>one <00:02.00>two\n[00:03.00][00:05.00]three\r\n  [00:04.00]  \n',
           b'\xef\xbb\xbf[00:00.10]caf\xc3\xa9 \xe4\xbd\xa0\xe5\xa5\xbd\n[00:02:50]<00:02.50>\xc2\xa0x\xe3\x80\x80\r',
           b'plain\nlines\n\nof text']


def hostile(seed, samples, count=2000):
    """Random bytes, every truncation of the samples, and mutations of them."""
    r = random.Random(seed)
    for _ in range(count):
        yield r.randbytes(r.randrange(200)).replace(b'\0', b'[')
    for s in samples:
        for k in range(len(s)):
            yield s[:k]
    for _ in range(count):
        b = bytearray(r.choice(samples) * r.randrange(1, 3))
        for _ in range(r.randrange(1, 8)):
            b[r.randrange(len(b))] = r.choice(b'[]<>:.0159\n\r \xc2\xa0\xe3\x80\xef\xff')
        yield bytes(b)


def wrap_fn(fw):
    return fw.fn('lyrics__ly_wrap', C.c_int, C.POINTER(LyLine), C.c_void_p, C.c_void_p)


def ptr(array, i):
    return C.addressof(array.contents) + i * C.sizeof(array._type_)


def lay_out(fw, t, adv=10):
    """As ly_layout, at a steady adv px a character."""
    wrap = wrap_fn(fw)
    for i in range(t.contents.n):
        line = t.contents.line[i]
        for k in range(line.n):
            t.contents.adv[line.text + k] = adv
        rows = wrap(C.pointer(line), ptr(t.contents.text, line.text), ptr(t.contents.adv, line.text))
        line.px, line.rows = 34, min(rows, 4)


def test_hostile_lyrics_through_the_loader_to_the_page(fw):
    parse = fw.fn('lyrics__lrc_parse', C.c_int, C.c_void_p, C.POINTER(C.POINTER(Line)), C.POINTER(C.POINTER(Mark)))
    publish = fw.fn('lyrics_publish', None, C.POINTER(Line), C.c_uint, C.POINTER(Mark))
    line_at = fw.fn('lyrics__ly_line_at', C.c_int, C.c_int)
    lit = fw.fn('lyrics__ly_lit', C.c_int, C.c_uint, C.c_int)
    free = fw.fn('free', None, C.c_void_p)
    ly, r = state(fw), random.Random(3)
    for raw in hostile(3, SAMPLES):
        buf, lp, mp = block(fw, raw), C.POINTER(Line)(), C.POINTER(Mark)()
        n = parse(buf, C.byref(lp), C.byref(mp))
        assert n >= 0
        marks = 0
        for i in range(n):
            line = lp[i]
            assert line.text and line.text.strip(b' \t')
            assert all(mp[line.mark + k].at <= len(line.text) for k in range(line.marks))
            marks += line.marks
        synced = n > 0 and lp[0].time != FAR
        publish(lp, n if synced else 0, mp)  # as ringnav_lyric sends them
        assert bool(ly.next) == synced
        if synced:
            t = ly.next
            assert t.contents.n == n
            lay_out(fw, t)
            ly.tab = t
            for ms in sorted(r.randrange(0, 1000000) for _ in range(4)):
                cur = line_at(ms)
                assert -1 <= cur < n
                if cur >= 0:
                    line = t.contents.line[cur]
                    full = sum(line.w[:line.rows])
                    assert 0 <= lit(cur, ms) <= full
            ly.tab = None
        free(buf), free(lp), free(mp)


def fnv(h, x):
    return ((h ^ x) * PRIME) & 0xffffffff


def line_sum(h, ms, s):
    h = fnv(h, ms if ms == FAR else ms // 1000)
    for c in s:
        h = fnv(h, c)
    return fnv(h, 0x10000)


def ly_sum(fw, h, ms, s):
    array = (C.c_uint * max(len(s), 1))(*s)
    return fw.fn('lyrics__ly_sum', C.c_uint, C.c_uint, C.c_int, C.c_void_p, C.c_uint)(h, ms, array, len(s))


@pytest.mark.parametrize('ms,s', [(0, []), (1500, [65, 0x4f60]), (FAR, [97]), (999, [97])])
def test_the_sum_is_fnv_1a_of_stock_seconds_and_characters(fw, ms, s):
    assert ly_sum(fw, SEED, ms, s) == line_sum(SEED, ms, s)


def test_the_sum_sees_what_stocks_rows_hold(fw):
    assert ly_sum(fw, SEED, 1000, [97]) == ly_sum(fw, SEED, 1999, [97])  # whole seconds
    assert ly_sum(fw, SEED, 1000, [97]) != ly_sum(fw, SEED, 2000, [97])
    assert ly_sum(fw, SEED, 1000, [97]) != ly_sum(fw, SEED, 1000, [98])
    assert ly_sum(fw, SEED, 1000, [97, 98]) != ly_sum(fw, ly_sum(fw, SEED, 1000, [97]), 1000, [98])  # a line's end counts


@pytest.mark.parametrize('n,marks,chars', [(1, 0, 0), (3, 2, 10), (1, 1, 1), (50, 200, 4000)])
def test_a_table_holds_all_it_was_sized_for(fw, n, marks, chars):
    t = fw.fn('lyrics__ly_alloc', C.POINTER(Tab), C.c_uint, C.c_uint, C.c_uint)(n, marks, chars).contents
    assert t.n == n and t.sum == SEED
    t.line[n - 1].ms = 5
    if marks:
        t.mark[marks - 1].ms = 5
    if chars:
        t.text[chars - 1], t.adv[chars - 1] = 5, 5
    addresses = [ptr(t.line, 0), ptr(t.line, n), ptr(t.mark, marks), ptr(t.text, chars)]
    assert addresses == [ptr(t.line, 0), ptr(t.mark, 0), ptr(t.text, 0), ptr(t.adv, 0)]  # end to end


def test_lyrics_gone_forgets_the_rows(fw):
    ly = state(fw)
    ly.lrc, ly.rows, ly.cur = 1234, 5, 3
    fw.fn('lyrics_gone', None)()
    assert not ly.lrc and ly.rows == 0 and ly.cur == -1


@pytest.mark.parametrize('c,cjk', [
    (0x2e7f, 0), (0x2e80, 1), (0x3042, 1), (0x4f60, 1), (0x9fff, 1), (0xa000, 0), (0xabff, 0), (0xac00, 1), (0xd7af, 1),
    (0xd7b0, 0), (0xfeff, 0), (0xff00, 1), (0xffef, 1), (0xfff0, 0), (0x41, 0), (0xe9, 0), (0x1f600, 0),
])
def test_cjk(fw, c, cjk):
    assert fw.fn('lyrics__ly_cjk', C.c_int, C.c_uint)(c) == cjk


def wrap(fw, s, width=100, adv=10):
    """ly_wrap's count and rows (start, length, width), at adv px a character (or a list of them)."""
    state(fw).w = width
    chars = [ord(c) for c in s]
    advs = adv if isinstance(adv, list) else [adv] * len(chars)
    line = LyLine(n=len(chars))
    k = wrap_fn(fw)(C.byref(line), (C.c_uint * (len(chars) + 1))(*chars), (C.c_ushort * (len(chars) + 1))(*advs))
    return k, [(line.start[r], line.len[r], line.w[r]) for r in range(min(k, 4))]


# At width 100 and 10 px a character, with LYRIC_BOLD's 2 px: 9 characters fit a row.
@pytest.mark.parametrize('s,rows', [
    ('', []), ('    ', []),
    ('a', [(0, 1, 10)]),
    ('aaaaaaaaa', [(0, 9, 90)]),
    ('aaaa bbbb', [(0, 9, 90)]),
    ('aaaa bbbbb', [(0, 4, 40), (5, 5, 50)]),                 # before the space, which goes
    ('aaaa  bbbbb', [(0, 4, 40), (6, 5, 50)]),                # both spaces
    ('aaaaaaaaa b', [(0, 9, 90), (10, 1, 10)]),               # the space where the row ends
    ('  ab', [(2, 2, 20)]), ('ab  ', [(0, 2, 20)]),
    ('a' * 25, [(0, 9, 90), (9, 9, 90), (18, 7, 70)]),        # one word: mid-word
    ('aaa ' + 'b' * 12, [(0, 3, 30), (4, 9, 90), (13, 3, 30)]),
    ('你' * 12, [(0, 9, 90), (9, 3, 30)]),
    ('aaaaaaaa你bbbb', [(0, 9, 90), (9, 4, 40)]),             # after a CJK character, in Latin text
    ('aaaaaaa你你bbbb', [(0, 9, 90), (9, 4, 40)]),
    ('aaaaa你bbbbb', [(0, 6, 60), (6, 5, 50)]),               # before one when its word won't fit
])
def test_wrap_at_100(fw, s, rows):
    assert wrap(fw, s) == (len(rows), rows)


def test_a_row_fits_to_the_pixel(fw):
    assert wrap(fw, 'a' * 10, width=102)[1] == [(0, 10, 100)]
    assert wrap(fw, 'a' * 10, width=101)[1] == [(0, 9, 90), (9, 1, 10)]


def test_the_page_width_before_rows_are_laid_out(fw):
    assert wrap(fw, 'a' * 34, width=0)[0] == 1  # the screen's 375 less LYRIC_X a side
    assert wrap(fw, 'a' * 35, width=0)[0] == 2


def test_a_character_wider_than_the_row_still_goes_on_one(fw):
    assert wrap(fw, 'ab', adv=200) == (2, [(0, 1, 200), (1, 1, 200)])


def test_a_long_line_counts_every_row_and_keeps_four(fw):
    k, rows = wrap(fw, 'a' * 100)
    assert k == 12 and rows == [(0, 9, 90), (9, 9, 90), (18, 9, 90), (27, 9, 90)]


def test_characters_with_no_advance_never_wrap(fw):
    assert wrap(fw, 'a' * 500, adv=0) == (1, [(0, 500, 0)])


def test_mixed_advances_measure_what_the_row_keeps(fw):
    assert wrap(fw, 'ab cd', width=40, adv=[10, 20, 5, 30, 1]) == (2, [(0, 2, 30), (3, 2, 31)])


def table(fw, lines, adv=10):
    """A published table of (ms, text, [(mark ms, character)]) laid out at adv px a character."""
    alloc = fw.fn('lyrics__ly_alloc', C.POINTER(Tab), C.c_uint, C.c_uint, C.c_uint)
    t = alloc(len(lines), sum(len(x[2]) for x in lines), sum(len(x[1]) for x in lines))
    c = k = 0
    for i, (ms, s, marks) in enumerate(lines):
        line = t.contents.line[i]
        line.ms, line.text, line.n, line.mark, line.marks = ms, c, len(s), k, len(marks)
        for ch in s:
            t.contents.text[c] = ord(ch)
            c += 1
        for m in marks:
            t.contents.mark[k] = Mark(*m)
            k += 1
    state(fw).tab = t
    state(fw).w = 343
    lay_out(fw, t, adv)
    return t.contents


def line_at(fw, ms):
    return fw.fn('lyrics__ly_line_at', C.c_int, C.c_int)(ms)


def test_the_line_playing_at_a_time(fw):
    table(fw, [(1000, 'a', []), (2000, 'b', []), (2000, 'c', []), (3000, 'd', [])])
    assert [line_at(fw, ms) for ms in (0, 999, 1000, 1999, 2000, 2999, 3000, FAR - 1)] == [-1, -1, 0, 0, 1, 1, 3, 3]


def test_unsynced_lines_are_never_the_line_playing(fw):
    table(fw, [(FAR, 'a', []), (FAR, 'b', [])])
    assert line_at(fw, 0) == -1 and line_at(fw, FAR) == -1


def test_no_lines_no_line(fw):
    table(fw, [])
    assert line_at(fw, 5000) == -1


def along(fw, c):
    line = state(fw).tab.contents.line
    return fw.fn('lyrics__ly_along', C.c_int, C.POINTER(LyLine), C.c_uint)(line, c)


def test_distance_along_rows_laid_end_to_end(fw):
    t = table(fw, [(0, 'abc defg', [])])
    line = t.line[0]
    line.rows, line.start[0], line.len[0], line.w[0], line.start[1], line.len[1], line.w[1] = 2, 0, 3, 60, 4, 4, 28
    for k, a in enumerate([10, 20, 30, 5, 7, 8, 6, 7]):
        t.adv[k] = a
    assert [along(fw, c) for c in range(8)] == [0, 10, 30, 60, 60, 67, 75, 81]
    assert along(fw, 8) == 88 and along(fw, 1000) == 88  # past its end: all of it


def test_distance_with_no_rows(fw):
    t = table(fw, [(0, '', [])])
    assert along(fw, 0) == 0 and along(fw, 5) == 0


def sung(fw, s, a=0):
    t = table(fw, [(0, s, [])])
    return fw.fn('lyrics__ly_sung', C.c_int, C.POINTER(LyLine), C.c_uint)(t.line, a)


@pytest.mark.parametrize('s,a,ms', [
    ('', 0, 800), ('ab', 0, 800), ('a' * 8, 0, 800),   # the least
    ('a' * 9, 0, 810), ('a' * 12, 0, 1080),
    ('a' * 12, 2, 900), ('a' * 12, 12, 800),
    ('你' * 3, 0, 810),                                 # three to the syllable
    ('a b,c.d' * 3, 0, 1080),                           # spaces and stops take none
    (' ,.' * 20, 0, 800),
])
def test_time_to_sing(fw, s, a, ms):
    assert sung(fw, s, a) == ms


def lit(fw, i, ms):
    return fw.fn('lyrics__ly_lit', C.c_int, C.c_uint, C.c_int)(i, ms)


def test_a_line_without_word_stamps_lights_whole_when_it_starts(fw):
    table(fw, [(1000, 'a' * 10, [])])
    assert lit(fw, 0, 999) == 0 and lit(fw, 0, 1000) == 100 and lit(fw, 0, 90000) == 100


def word_line(fw, next_ms=5000):
    # ten letters: "aaaaa" from 1000, "bbbbb" from 2000, the next line then
    return table(fw, [(1000, 'a' * 5 + 'b' * 5, [(1000, 0), (2000, 5)]), (next_ms, 'c', [])])


@pytest.mark.parametrize('ms,x', [
    (0, 0), (999, 0), (1000, 0), (1500, 25), (1999, 49),   # the first word's span
    (2000, 50), (2400, 75), (2799, 99),                    # the last word: 800 ms at the sung pace
    (2800, 100), (4000, 100),
])
def test_a_word_timed_line_lights_from_stamp_to_stamp(fw, ms, x):
    word_line(fw)
    assert lit(fw, 0, ms) == x


@pytest.mark.parametrize('ms,x', [(2000, 50), (2200, 75), (2399, 99), (2400, 100)])
def test_it_is_done_by_the_time_the_next_line_starts(fw, ms, x):
    word_line(fw, 2400)
    assert lit(fw, 0, ms) == x


def test_a_next_line_at_the_same_time_is_no_end(fw):
    table(fw, [(1000, 'a' * 10, [(1000, 0)]), (1000, 'b', []), (1400, 'c', [])])
    assert lit(fw, 0, 1200) == 50


def test_a_stamp_ending_the_line_ends_it(fw):
    table(fw, [(1000, 'a' * 10, [(1000, 0), (2000, 10)])])
    assert lit(fw, 0, 1500) == 50 and lit(fw, 0, 2000) == 100


def test_a_stamp_before_its_line_starts_counts_from_the_line(fw):
    table(fw, [(1000, 'a' * 10, [(10, 0), (2000, 5)])])
    assert lit(fw, 0, 1000) == 0 and lit(fw, 0, 1500) == 25


def test_stamps_at_one_time_divide_by_nothing(fw):
    table(fw, [(1000, 'a' * 10, [(1000, 0), (1000, 5), (1000, 5)]), (1000, 'b', [])])
    assert lit(fw, 0, 1000) == 50


def test_a_last_word_past_the_next_line_has_no_time_left(fw):
    table(fw, [(1000, 'a' * 10, [(1000, 0), (3000, 5)]), (2000, 'b', [])])
    assert lit(fw, 0, 2999) == 49 and lit(fw, 0, 3000) == 100


def test_light_only_moves_forward_across_rows(fw):
    s = ('word ' * 40)[:-1]
    marks = [(1000 + 300 * k, 5 * k) for k in range(40)]
    t = table(fw, [(1000, s, marks), (14000, 'next', [])])
    assert t.line[0].rows == 4  # of the 6 it takes
    seen = [lit(fw, 0, ms) for ms in range(0, 15000, 40)]
    assert seen == sorted(seen) and seen[0] == 0 and seen[-1] == sum(t.line[0].w[:4])


def published(fw, raw):
    fn = fw.fn('lyrics__lrc_parse', C.c_int, C.c_void_p, C.POINTER(C.POINTER(Line)), C.POINTER(C.POINTER(Mark)))
    buf, lp, mp = block(fw, raw), C.POINTER(Line)(), C.POINTER(Mark)()
    n = fn(buf, C.byref(lp), C.byref(mp))
    fw.fn('lyrics_publish', None, C.POINTER(Line), C.c_uint, C.POINTER(Mark))(lp, n, mp)
    return state(fw).next


def test_publish_decodes_utf8_and_puts_stamps_on_characters(fw):
    t = published(fw, '[00:01.00]é<00:01.50>a<00:02.00>你b\n'.encode()).contents
    line = t.line[0]
    assert [t.text[i] for i in range(line.n)] == [0xe9, 0x61, 0x4f60, 0x62]
    assert [(t.mark[k].ms, t.mark[k].at) for k in range(line.marks)] == [(1500, 1), (2000, 2)]


def test_publish_takes_a_lone_latin1_byte_as_a_character(fw):
    t = published(fw, b'[00:01.00]caf\xe9\n').contents
    assert [t.text[i] for i in range(t.line[0].n)] == [0x63, 0x61, 0x66, 0xe9]


def test_publish_lays_lines_end_to_end(fw):
    t = published(fw, '[00:01.00]ab\n[00:02.00]<00:02.00>c<00:03.00>d\n[00:04.00]e\n'.encode()).contents
    assert [(t.line[i].ms, t.line[i].text, t.line[i].n, t.line[i].mark, t.line[i].marks) for i in range(3)] == \
        [(1000, 0, 2, 0, 0), (2000, 2, 2, 0, 2), (4000, 4, 1, 2, 0)]


def test_publish_sums_as_the_rows_labels_will(fw):
    t = published(fw, '[00:01.50]aé\n[00:02.00]b\n'.encode()).contents
    assert t.sum == line_sum(line_sum(SEED, 1500, [0x61, 0xe9]), 2000, [0x62])


def test_publish_replaces_the_last_table_and_none_clears_it(fw):
    published(fw, b'[00:01.00]a\n')
    assert published(fw, b'[00:01.00]b\n').contents.text[0] == 0x62
    fw.fn('lyrics_publish', None, C.c_void_p, C.c_uint, C.c_void_p)(None, 0, None)
    assert not state(fw).next


def take(fw):
    return fw.fn('lyrics__ly_take', C.c_int)()


def test_the_rows_take_the_table_made_for_them(fw):
    ly, t = state(fw), published(fw, b'[00:01.00]a\n[00:02.00]b\n')
    ly.rows, ly.sum, ly.cur = 2, t.contents.sum, 7
    made = C.addressof(t.contents)
    assert take(fw) == 1 and not ly.next and ly.cur == -1 and C.addressof(ly.tab.contents) == made


def test_the_rows_leave_a_table_made_for_others(fw):
    ly, t = state(fw), published(fw, b'[00:01.00]a\n')
    ly.rows, ly.sum = 2, t.contents.sum
    assert take(fw) == 0 and ly.next
    ly.rows, ly.sum = 1, t.contents.sum + 1
    assert take(fw) == 0 and ly.next


def test_nothing_to_take(fw):
    assert take(fw) == 0


def test_lyrics_on_needs_the_same_rows(fw):
    on = fw.fn('lyrics_on', C.c_int, C.c_void_p)
    fw.mock('widget_count_children', lambda *_: 2)
    ly = state(fw)
    table(fw, [(0, 'a', []), (1, 'b', [])])
    ly.lrc, ly.rows = 77, 2
    assert on(77) == 1 and on(78) == 0 and on(None) == 0
    fw.mock('widget_count_children', lambda *_: 3)
    assert on(77) == 0


class Player:
    """The stock player behind lyrics_now: its second, play state and the clock."""
    def __init__(self, fw):
        self.sec, self.now, self.status = -1, 5000, 2
        def playtime(sec, total, *_):
            C.cast(sec, C.POINTER(C.c_int))[0] = self.sec
        fw.mock('player_playtime_and_length', playtime)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('time_now_ms', lambda *_: self.now)
        self.fn = fw.fn('lyrics_now', C.c_int)

    def at(self, sec, step=200):
        self.now += step
        self.sec = sec
        return self.fn()


def test_no_position_yet_is_zero(fw):
    p = Player(fw)
    assert p.fn() == 0
    assert state(fw).sec == -1 and state(fw).run == 0


def test_stopped_the_position_stays_at_its_second(fw):
    p = Player(fw)
    p.status = 3
    assert p.at(10) == 10000 and p.at(10) == 10000


def test_a_first_look_while_playing_guesses_mid_second(fw):
    p = Player(fw)
    assert p.at(10) == 10000 + (999 + 250) // 2


def test_the_seconds_change_narrows_it_to_a_poll(fw):
    p = Player(fw)
    p.at(10, 0)
    assert p.at(11, 1000) == 11125   # up to 250 ms past it
    assert state(fw).hi - state(fw).lo == 250


def test_the_position_runs_on_between_changes(fw):
    p = Player(fw)
    p.at(10, 0)
    p.at(11, 1000)
    assert p.at(11, 200) == 11325 and p.at(11, 200) == 11525


def test_never_more_than_a_poll_past_its_second(fw):
    p = Player(fw)
    p.at(10, 0)
    p.at(11, 1000)
    assert p.at(11, 5000) == 11000 + 999 + 250


def test_never_before_its_second(fw):
    p = Player(fw)
    p.at(10, 0)
    p.status = 3
    assert p.at(20) == 20000  # seeked while paused


def test_a_change_the_bounds_cant_hold_starts_them_from_it(fw):
    p = Player(fw)
    p.at(10, 0)
    p.at(11, 1000)
    assert p.at(12, 10) == 12125
    assert state(fw).hi - state(fw).lo == 250


def test_a_seek_starts_the_bounds_again(fw):
    p = Player(fw)
    p.at(10, 0)
    p.at(11, 1000)
    assert p.at(100, 200) == 100000 + (999 + 250) // 2
    assert state(fw).hi - state(fw).lo == 999 + 250


def test_paused_it_holds_and_on_play_goes_on_from_there(fw):
    p = Player(fw)
    p.at(10, 0)
    held = p.at(11, 1000)
    p.status = 3
    assert p.at(11, 200) == held and p.at(11, 700) == held
    p.status = 2
    assert p.at(11, 100) == held and p.at(11, 100) == held + 100


def test_lyrics_sec_lets_the_clock_go(fw):
    p = Player(fw)
    p.at(10, 0)
    p.sec = 42
    assert fw.fn('lyrics_sec', C.c_int)() == 42000
    assert state(fw).sec == -1 and state(fw).run == 0
    p.sec = -1
    assert fw.fn('lyrics_sec', C.c_int)() == 0


def test_the_line_pass_repaints_on_a_new_line_and_paces_the_light(fw):
    now = [1000]
    fw.mock('time_now_ms', lambda *_: now[0])
    fw.mock('widget_count_children', lambda *_: 2)
    fw.mock('widget_get_child', lambda lrc, i, *_: 500 + i)
    fn = fw.fn('lyrics_line', C.c_int, C.c_void_p, C.c_int, C.c_int)
    table(fw, [(1000, 'a' * 10, [(1000, 0), (2000, 5)]), (5000, 'b', [])])
    ly = state(fw)
    ly.lrc, ly.rows, ly.cur = 77, 2, -1
    painted = lambda: [c[1][0] for c in fw.calls('widget_invalidate_force')]
    assert fn(None, 1000, 1) == -1  # not its rows
    assert fn(77, 500, 1) == -1 and painted() == []
    assert fn(77, 1100, 1) == 0 and painted() == [500]        # the line starts
    now[0] += 10
    assert fn(77, 1200, 1) == 0 and painted() == [500]        # moved, but too soon
    now[0] += 56
    assert fn(77, 1300, 1) == 0 and painted() == [500, 500]   # LYRIC_PAINT_MS on
    assert fn(77, 5000, 1) == 1 and painted() == [500, 500, 500, 501]  # the last line, then the new
    assert ly.ms == 5000 and ly.cur == 1


def test_the_line_pass_repaints_nothing_off_the_page(fw):
    fw.mock('time_now_ms', lambda *_: 1000)
    fw.mock('widget_count_children', lambda *_: 1)
    table(fw, [(1000, 'a', [])])
    ly = state(fw)
    ly.lrc, ly.rows, ly.cur = 77, 1, -1
    assert fw.fn('lyrics_line', C.c_int, C.c_void_p, C.c_int, C.c_int)(77, 1000, 0) == 0
    assert not fw.calls('widget_invalidate_force')


class File:
    """fopen and fread over one file's bytes; fopen fails with none."""
    def __init__(self, fw, data):
        self.at = {}
        def fopen(path, mode, *_):
            if data is None:
                return 0
            f = len(self.at) + 1
            self.at[f] = 0
            return f
        def fread(buf, size, n, f, *_):
            piece = data[self.at[f]:self.at[f] + size * n]
            C.memmove(buf, piece, len(piece))
            self.at[f] += len(piece)
            return len(piece) // size
        fw.mock('fw_fopen', fopen)
        fw.mock('fw_fread', fread)


def lrc_open(fw, path=b'/mnt/mmc/a.lrc'):
    return fw.fn('ringnav_lrc_open', C.c_void_p, C.c_char_p, C.c_char_p)(path, b'r')


def test_stocks_lrc_open_also_keeps_the_text(fw):
    File(fw, b'[00:01.00]a\n')
    assert lrc_open(fw) == 1  # stock gets its own handle
    assert C.string_at(fw.var('lyrics__lrc_file', C.c_void_p).value) == b'[00:01.00]a\n'
    assert len(fw.calls('fw_fclose')) == 1  # only ours


def test_a_failed_open_keeps_nothing(fw):
    File(fw, b'old')
    lrc_open(fw)
    File(fw, None)
    assert lrc_open(fw) is None and not fw.var('lyrics__lrc_file', C.c_void_p).value


def test_the_lrc_text_stops_at_the_tag_limit(fw):
    File(fw, b'a' * (TAG_MAX + 100))
    lrc_open(fw)
    assert len(C.string_at(fw.var('lyrics__lrc_file', C.c_void_p).value)) == TAG_MAX


@pytest.mark.parametrize('data,ok', [
    (b'', 1), (b'plain', 1), ('caf\xe9'.encode(), 1), ('😀 ok ’'.encode(), 1),
    (b'caf\xe9', 0), (b'\xd6\xd0\xce\xc4', 0), (b'a\xf0\x9f', 0),
])
def test_utf8_or_not(fw, data, ok):
    File(fw, data)
    assert fw.fn('ringnav_lrc_utf8', C.c_int, C.c_char_p)(b'a.lrc') == ok


def test_a_missing_lrc_is_not_utf8(fw):
    File(fw, None)
    assert fw.fn('ringnav_lrc_utf8', C.c_int, C.c_char_p)(b'a.lrc') == 0
