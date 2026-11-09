"""patch/chapters.c: ffmpeg's ffmetadata into chapters, its pipe, and an MP4's sample count before a play."""
import ctypes as C
import random
import struct

import pytest

from conftest import block

MAX = 512
BYTES = 256 * 1024
EOVERFLOW = 79


class Chapter(C.Structure):
    _fields_ = [('sec', C.c_int), ('title', C.c_char * 256)]


class Chapters(C.Structure):
    _fields_ = [('pid', C.c_int), ('fd', C.c_int), ('used', C.c_int), ('done', C.c_int), ('failed', C.c_int),
                ('count', C.c_int), ('at', C.c_ulonglong), ('rows', Chapter * MAX), ('raw', C.c_char * (BYTES + 1))]


def parsed(fw, raw):
    """chapters_parse's count and its rows as (sec, title), with an exact CHAPTER_MAX rows' block."""
    parse = fw.fn('chapters_parse', C.c_int, C.c_void_p, C.c_void_p)
    calloc, free = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t), fw.fn('free', None, C.c_void_p)
    text, rows = block(fw, raw), calloc(MAX, C.sizeof(Chapter))
    n = parse(text, rows)
    table = C.cast(rows, C.POINTER(Chapter))
    out = [(table[i].sec, table[i].title) for i in range(n)]
    free(text), free(rows)
    return n if n < 0 else out


def meta(*chapters, head=b';FFMETADATA1\n'):
    return head + b''.join(b'[CHAPTER]\n' + c for c in chapters)


def row(start, title, base=b'1/1000'):
    return b'TIMEBASE=%s\nSTART=%d\nEND=%d\ntitle=%s\n' % (base, start, start + 1, title)


REAL = (b';FFMETADATA1\ntitle=The Book\nartist=Someone\nencoder=Lavf58.29.100\n'
        b'[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=61000\ntitle=Chapter 1\n'
        b'[CHAPTER]\nTIMEBASE=1/1000\nSTART=61000\nEND=120500\ntitle=Caf\xc3\xa9 \\= \\; \\# \\\\ \\\nmore\n'
        b'[STREAM]\ntitle=a stream\n')


def test_decimal_reads_digits_and_stops_where_they_stop(fw):
    decimal = fw.fn('chapters__decimal', C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_ulonglong))
    def read(s):
        buf = C.create_string_buffer(s)
        p, v = C.c_char_p(C.addressof(buf)), C.c_ulonglong(99)
        return decimal(C.byref(p), C.byref(v)), v.value, p.value
    assert read(b'123x') == (1, 123, b'x')
    assert read(b'0') == (1, 0, b'')
    assert read(b'') == (0, 0, b'') and read(b'x') == (0, 0, b'x') and read(b'-1')[0] == 0
    assert read(b'9223372036854775807') == (1, 2**63 - 1, b'')  # the largest that fits
    assert read(b'9223372036854775808')[0] == 0
    assert read(b'99999999999999999999999999')[0] == 0


def test_be_is_big_endian(fw):
    be = fw.fn('chapters__be', C.c_ulonglong, C.c_char_p, C.c_int)
    assert be(b'\x12\x34\x56\x78', 4) == 0x12345678
    assert be(b'\xff' * 8, 8) == 2**64 - 1
    assert be(b'\xff', 0) == 0 and be(b'\x80', 1) == 0x80


def test_a_file_with_chapters(fw):
    assert parsed(fw, REAL) == [(0, b'Chapter 1'), (61, b'Caf\xc3\xa9 = ; # \\ \nmore')]


def test_it_must_be_ffmetadata(fw):
    for bad in (b'', b';FFMETADATA', b';FFMETADATA1', b';FFMETADATA1 \n', b';FFMETADATA2\n', b'[CHAPTER]\n', b' ;FFMETADATA1\n'):
        assert parsed(fw, bad) == -1, bad


def test_no_chapters_is_none_not_an_error(fw):
    assert parsed(fw, b';FFMETADATA1\n') == []
    assert parsed(fw, b';FFMETADATA1\r\n') == []
    assert parsed(fw, b';FFMETADATA1\ntitle=Book\n[STREAM]\ntitle=x\n') == []


def test_the_books_tags_are_not_a_chapters(fw):
    raw = b';FFMETADATA1\ntitle=Book\n[CHAPTER]\nTIMEBASE=1/1\nSTART=3\n[STREAM]\ntitle=audio\n'
    assert parsed(fw, raw) == [(3, b'')]


@pytest.mark.parametrize('base,start,sec', [
    (b'1/1000', 1999, 1),            # whole seconds, down
    (b'1/90000', 270000, 3),
    (b'1/1000000000', 3600000000000, 3600),
    (b'3/2', 3, 4),                  # 4.5, with the remainder's share kept
    (b'1/1', 0x7fffffff, 0x7fffffff),
])
def test_start_over_timebase_is_seconds(fw, base, start, sec):
    assert parsed(fw, meta(row(start, b'x', base))) == [(sec, b'x')]


def test_fields_come_in_any_order_and_the_last_title_counts(fw):
    assert parsed(fw, meta(b'title=a\nSTART=5\ntitle=b\nTIMEBASE=1/1\n')) == [(5, b'b')]


def test_crlf_lines(fw):
    assert parsed(fw, b';FFMETADATA1\r\n[CHAPTER]\r\nTIMEBASE=1/1\r\nSTART=7\r\ntitle=x\r\n') == [(7, b'x')]


def test_the_last_line_needs_no_newline(fw):
    assert parsed(fw, meta(b'TIMEBASE=1/1\nSTART=7\ntitle=x')) == [(7, b'x')]


def test_a_backslash_at_the_very_end_is_itself(fw):
    assert parsed(fw, meta(b'TIMEBASE=1/1\nSTART=7\ntitle=a\\')) == [(7, b'a\\')]


@pytest.mark.parametrize('chapter', [
    b'START=1\ntitle=no base\n', b'TIMEBASE=1/1\ntitle=no start\n', b'',
    b'TIMEBASE=1\nSTART=1\n', b'TIMEBASE=1/\nSTART=1\n', b'TIMEBASE=/1\nSTART=1\n', b'TIMEBASE=1/2x\nSTART=1\n',
    b'TIMEBASE=0/1\nSTART=1\n', b'TIMEBASE=1/0\nSTART=1\n',
    b'TIMEBASE=1/1\nSTART=\n', b'TIMEBASE=1/1\nSTART=-1\n', b'TIMEBASE=1/1\nSTART=1 \n',
    b'TIMEBASE=2147483648/1\nSTART=1\n', b'TIMEBASE=1/2147483648\nSTART=1\n',
    b'TIMEBASE=1/1\nSTART=2147483648\n', b'TIMEBASE=2/1\nSTART=1073741824\n',
    b'TIMEBASE=1/1\nSTART=9223372036854775808\n',
])
def test_a_bad_chapter_fails_the_whole_list(fw, chapter):
    assert parsed(fw, meta(chapter)) == -1
    assert parsed(fw, meta(row(0, b'good'), chapter)) == -1


def test_time_going_back_is_sorted_and_a_tie_is_kept(fw):
    assert parsed(fw, meta(row(5000, b'a'), row(4999, b'b'))) == [(4, b'b'), (5, b'a')]
    assert parsed(fw, meta(row(5000, b'a'), row(5999, b'b'))) == [(5, b'a'), (5, b'b')]


def test_a_section_cut_after_its_header_fails(fw):
    assert parsed(fw, meta(row(0, b'a')) + b'[CHAPTER]') == -1


def test_chapter_max_is_a_hard_limit(fw):
    full = [row(i * 1000, b'c') for i in range(MAX)]
    assert len(parsed(fw, meta(*full))) == MAX
    assert parsed(fw, meta(*full, row(MAX * 1000, b'c'))) == -1


@pytest.mark.parametrize('title,kept', [
    (b'a' * 255, b'a' * 255),                               # exactly fits
    (b'a' * 256, b'a' * 255),
    (b'a' * 5000, b'a' * 255),
    (b'a' * 253 + b'\xc3\xa9', b'a' * 253 + b'\xc3\xa9'),   # a character that just fits
    (b'a' * 254 + b'\xc3\xa9', b'a' * 254),                 # one that would be cut
    (b'a' * 253 + b'\xe2\x82\xac', b'a' * 253),
    (b'a' * 252 + b'\xf0\x9f\x93\x9a', b'a' * 252),
    (b'a' * 254 + b'\xc3\xa9' + b'b' * 40, b'a' * 254),
])
def test_a_long_title_is_cut_at_a_whole_character(fw, title, kept):
    assert parsed(fw, meta(row(0, title))) == [(0, kept)]


def seeds():
    two = meta(row(0, b'One'), row(5000, b'Two \\= two'))
    return [two, REAL, two.replace(b'\n', b'\r\n')]


def hostile(seed, samples, count=2000):
    """Random bytes, every truncation of the samples, and mutations of them."""
    r = random.Random(seed)
    for _ in range(count):
        yield r.randbytes(r.randrange(300))
    for s in samples:
        for k in range(len(s)):
            yield s[:k]
    for _ in range(count):
        b = bytearray(r.choice(samples))
        for _ in range(r.randrange(1, 6)):
            b[r.randrange(len(b))] = r.choice(b'\\\n\r[]=/0159\x00\xff\xc3')
        yield bytes(b)


def test_hostile_ffmetadata(fw):
    head = b';FFMETADATA1\n'
    for raw in hostile(1, seeds()):
        n = parsed(fw, raw if raw[:13] == head else head + raw)
        if n == -1:
            continue
        assert len(n) <= MAX
        secs = [sec for sec, _ in n]
        assert secs == sorted(secs) and all(sec >= 0 and len(title) < 256 for sec, title in n)


class Pipe:
    """ffmpeg behind chapter_pipe2 and fork: read gives chunks, then EOF; waitpid what the test says."""
    def __init__(self, fw, chunks=(), reap=lambda: (123, 0)):
        self.chunks, self.reap, self.closed, self.killed = list(chunks), reap, [], []
        self.errno = C.c_int(0)
        def pipe2(p, flags, *_):
            C.cast(p, C.POINTER(C.c_int))[0], C.cast(p, C.POINTER(C.c_int))[1] = 5, 6
        def read(fd, buf, n, *_):
            assert fd == 5 and n > 0
            if not self.chunks:
                return 0
            chunk = self.chunks.pop(0)
            if isinstance(chunk, int):
                self.errno.value = chunk
                return -1
            assert len(chunk) <= n
            C.memmove(buf, chunk, len(chunk))
            return len(chunk)
        def waitpid(pid, status, options, *_):
            assert options == 1  # WNOHANG: never blocks the UI
            result, code = self.reap()
            if result > 0:
                C.cast(status, C.POINTER(C.c_int))[0] = code
            return result
        fw.mock('chapter_pipe2', pipe2)
        fw.mock('fw_fork', lambda *_: 123)
        fw.mock('fw_read', read)
        fw.mock('fw_waitpid', waitpid)
        fw.mock('fw_close', lambda fd, *_: self.closed.append(fd))
        fw.mock('chapter_kill', lambda pid, sig, *_: self.killed.append((pid, sig)))
        fw.mock('fw___errno_location', lambda *_: C.addressof(self.errno))
        self.start = fw.fn('chapters_start', C.POINTER(Chapters), C.c_char_p, C.c_ulonglong)
        self.poll = fw.fn('chapters_poll', C.c_int, C.POINTER(Chapters), C.c_ulonglong)


def rows_of(c):
    return [(c.contents.rows[i].sec, c.contents.rows[i].title) for i in range(c.contents.count)]


def test_a_read_in_chunks_then_parsed(fw):
    raw = meta(row(0, b'One'), row(5000, b'Two'))
    pipe = Pipe(fw, [raw[:10], raw[10:40], raw[40:]])
    c = pipe.start(b'/mnt/mmc/a.m4b', 1000)
    assert c.contents.fd == 5 and pipe.closed == [6] and c.contents.pid == 123
    assert pipe.poll(c, 1100) == 1
    assert c.contents.done == 1 and c.contents.failed == 0 and rows_of(c) == [(0, b'One'), (5, b'Two')]
    assert pipe.poll(c, 1200) == 1  # done stays done


def test_it_reads_a_bounded_amount_a_tick_and_drains_after_the_exit(fw):
    raw = meta(row(0, b'One'))
    chunks = [bytes([b]) for b in raw]
    assert len(chunks) > 16
    pipe = Pipe(fw, chunks)
    c = pipe.start(b'a.m4b', 0)
    assert pipe.poll(c, 1) == 0 and c.contents.used == 16  # 16 reads, the exit seen, output left
    while not pipe.poll(c, 2):
        pass
    assert c.contents.used == len(raw) and rows_of(c) == [(0, b'One')]


def test_waiting_on_a_slow_ffmpeg(fw):
    results = [(0, 0), (0, 0), (123, 0)]
    pipe = Pipe(fw, [11, meta(row(0, b'One')), 4], reap=lambda: results.pop(0))
    c = pipe.start(b'a.m4b', 0)
    assert pipe.poll(c, 1) == 0 and c.contents.fd == 5  # EAGAIN: try again
    assert pipe.poll(c, 2) == 0 and c.contents.fd == 5  # EINTR too
    assert pipe.poll(c, 3) == 1 and rows_of(c) == [(0, b'One')]


def test_a_failed_read_fails_it(fw):
    results = [(0, 0), (123, 9)]
    pipe = Pipe(fw, [5], reap=lambda: results.pop(0))  # EIO
    c = pipe.start(b'a.m4b', 0)
    assert pipe.poll(c, 1) == 0
    assert pipe.killed == [(123, 9)] and c.contents.fd == -1 and c.contents.failed == 1
    assert pipe.poll(c, 2) == 1 and c.contents.count == -1


def test_ffmpegs_failure_is_ours(fw):
    pipe = Pipe(fw, [meta(row(0, b'One'))], reap=lambda: (123, 256))
    c = pipe.start(b'a.m4b', 0)
    assert pipe.poll(c, 1) == 1 and c.contents.count == -1 and c.contents.failed == 1


def test_bad_output_fails_after_a_clean_exit(fw):
    pipe = Pipe(fw, [b'not ffmetadata\n'])
    c = pipe.start(b'a.m4b', 0)
    assert pipe.poll(c, 1) == 1 and c.contents.count == -1 and c.contents.failed == 1


def test_an_interrupted_wait_tries_again(fw):
    results = [(-1, 0), (123, 0)]
    pipe = Pipe(fw, [meta(row(0, b'One'))], reap=lambda: results.pop(0))
    pipe.errno.value = 4
    c = pipe.start(b'a.m4b', 0)
    assert pipe.poll(c, 1) == 0 and c.contents.done == 0
    assert pipe.poll(c, 2) == 1 and rows_of(c) == [(0, b'One')]


def test_a_hung_ffmpeg_is_killed_after_ten_seconds(fw):
    gone = []
    pipe = Pipe(fw, [11] * 8, reap=lambda: (123, 9) if gone else (0, 0))
    c = pipe.start(b'a.m4b', 1000)
    assert pipe.poll(c, 10999) == 0 and not pipe.killed
    assert pipe.poll(c, 11000) == 0 and pipe.killed == [(123, 9)] and c.contents.failed == 1
    gone.append(1)
    assert pipe.poll(c, 11001) == 1 and c.contents.count == -1


def test_output_past_the_buffer_kills_it_and_never_overruns(fw):
    gone = []
    pipe = Pipe(fw, [b'x' * 4096] * 100, reap=lambda: (123, 9) if gone else (0, 0))
    c = pipe.start(b'a.m4b', 0)
    for t in range(1, 80):
        if pipe.poll(c, t):
            break
        assert c.contents.used <= BYTES
        gone.extend(pipe.killed)
    assert pipe.killed == [(123, 9)] and c.contents.used == BYTES and c.contents.failed == 1 and c.contents.count == -1


def test_no_pipe_is_a_failed_start(fw):
    pipe = Pipe(fw)
    fw.mock('chapter_pipe2', lambda *_: -1)
    c = pipe.start(b'a.m4b', 0)
    assert c.contents.failed == 1 and c.contents.done == 1 and pipe.poll(c, 1) == 1
    assert not fw.calls('fw_fork')


def test_no_fork_is_a_failed_start(fw):
    pipe = Pipe(fw)
    fw.mock('fw_fork', lambda *_: -1)
    c = pipe.start(b'a.m4b', 0)
    assert c.contents.failed == 1 and c.contents.done == 1 and pipe.closed == [6, 5] and not pipe.killed


def test_the_child_runs_ffmpeg_on_the_path_with_its_stdout_on_the_pipe(fw):
    pipe = Pipe(fw)
    fw.mock('fw_fork', lambda *_: 0)
    argv = []
    def execv(path, args, *_):
        table = C.cast(args, C.POINTER(C.c_char_p))
        i = 0
        while table[i]:
            argv.append(table[i])
            i += 1
        assert C.string_at(path) == b'/usr/bin/ffmpeg'
    fw.mock('chapter_execv', execv)
    pipe.start(b'/mnt/mmc/a b.m4b', 0)
    assert argv == [b'/usr/bin/ffmpeg', b'-nostdin', b'-v', b'error', b'-i', b'/mnt/mmc/a b.m4b', b'-f', b'ffmetadata', b'pipe:1']
    assert (6, 1) in [c[1][:2] for c in fw.calls('fw_dup2')]


def box(name, body=b''):
    return struct.pack('>I', 8 + len(body)) + name + body


def table(count, name=b'stsz'):
    return box(name, struct.pack('>III', 0, 0, count))


def book(*tables):
    """moov with a trak of each table, as an audiobook has its one."""
    traks = [box(b'trak', box(b'mdia', box(b'minf', box(b'stbl', t)))) for t in tables]
    return box(b'ftyp', b'M4B ') + box(b'moov', box(b'mvhd', b'\0' * 100) + b''.join(traks)) + box(b'mdat', b'\0' * 64)


class Card:
    """fopen, fseek, fread and fclose over files in a dict. A missing file sets errno."""
    def __init__(self, fw, files, errno=2):
        self.files, self.open, self.at, self.reads = files, {}, {}, 0
        self.errno = C.c_int(errno)
        def fopen(path, mode, *_):
            name = C.string_at(path)
            if name not in files:
                return 0
            f = len(self.open) + 1
            self.open[f], self.at[f] = name, 0
            return f
        def fseek(f, at, whence, *_):
            assert whence == 0
            self.at[f] = at
        def fread(buf, size, n, f, *_):
            self.reads += 1
            data = files[self.open[f]][self.at[f]:self.at[f] + size * n]
            C.memmove(buf, data, len(data))
            self.at[f] += len(data)
            return len(data) // size
        fw.mock('fw_fopen', fopen)
        fw.mock('fw_fseek', fseek)
        fw.mock('fw_fread', fread)
        fw.mock('fw___errno_location', lambda *_: C.addressof(self.errno))


def index(fw, files, path=b'a.m4b', errno=2):
    Card(fw, files, errno)
    return fw.fn('mp4_index_bytes', C.c_ulonglong, C.c_char_p)(path)


def test_samples_are_counted_through_the_box_tree(fw):
    assert index(fw, {b'a.m4b': book(table(1000))}) == 1000 * 28


def test_every_track_counts(fw):
    assert index(fw, {b'a.m4b': book(table(1000), table(500), table(7, b'stz2'))}) == 1507 * 28


@pytest.mark.parametrize('name', [b'a.m4b', b'a.M4A', b'a.b.Mp4', b'/x.y/z.m4a'])
def test_the_mp4_family_by_extension(fw, name):
    assert index(fw, {name: book(table(1))}, name) == 28


@pytest.mark.parametrize('name', [b'a.mp3', b'a', b'.m4bx', b'a.m4b.flac', b''])
def test_anything_else_is_not_opened(fw, name):
    assert index(fw, {name: book(table(1))}, name) == 0 and not fw.calls('fw_fopen')


def test_no_path_is_no_mp4(fw):
    assert fw.fn('mp4_index_bytes', C.c_ulonglong, C.c_char_p)(None) == 0


def test_a_file_too_large_to_open_says_so(fw):
    assert index(fw, {}, errno=EOVERFLOW) == 2**64 - 1


def test_a_missing_file_is_no_mp4(fw):
    assert index(fw, {}, errno=2) == 0


def test_a_box_with_a_64_bit_size(fw):
    stbl = table(5)
    moov = struct.pack('>I', 1) + b'moov' + struct.pack('>Q', 16 + 8 + len(stbl)) + box(b'stbl', stbl)
    assert index(fw, {b'a.m4b': moov}) == 5 * 28


def test_a_box_of_size_zero_runs_to_the_end(fw):
    body = box(b'stbl', table(5))
    assert index(fw, {b'a.m4b': struct.pack('>I', 0) + b'moov' + body}) == 5 * 28


def test_tables_only_count_inside_the_walked_boxes(fw):
    assert index(fw, {b'a.m4b': box(b'udta', table(9))}) == 0


def test_the_walk_stops_at_its_depth(fw):
    inside = table(1)
    for _ in range(5):
        inside = box(b'moov', inside)
    assert index(fw, {b'a.m4b': inside}) == 28  # five boxes down, stsz is still read
    assert index(fw, {b'a.m4b': box(b'moov', inside)}) == 0


def test_a_table_too_short_for_its_count_is_passed_over(fw):
    assert index(fw, {b'a.m4b': box(b'stsz', b'\0' * 11) + table(3)}) == 3 * 28


@pytest.mark.parametrize('size', [2, 7])
def test_a_box_smaller_than_its_header_ends_the_walk(fw, size):
    assert index(fw, {b'a.m4b': table(3) + struct.pack('>I', size) + b'moov' + table(4)}) == 3 * 28


@pytest.mark.parametrize('size', [21, 1000, 0x7fffffff])
def test_a_box_past_its_parents_end_ends_the_parents_walk(fw, size):
    lie = struct.pack('>I', size) + b'stsz' + struct.pack('>III', 0, 0, 9)
    assert index(fw, {b'a.m4b': box(b'moov', table(3) + lie) + table(4)}) == 7 * 28


def test_a_box_that_just_fits_its_parent_is_read(fw):
    assert index(fw, {b'a.m4b': box(b'moov', table(3) + struct.pack('>I', 20) + b'stsz' + struct.pack('>III', 0, 0, 9))}) == 12 * 28


def test_a_box_past_2gb_ends_the_walk(fw):
    files = {b'a.m4b': box(b'free', b'') + struct.pack('>I', 1) + b'free' + struct.pack('>Q', 2**31) + table(1)}
    Card(fw, files)
    assert fw.fn('mp4_index_bytes', C.c_ulonglong, C.c_char_p)(b'a.m4b') == 0
    assert max(c[1][1] for c in fw.calls('fw_fseek')) < 2**31


def test_mp4_files_are_closed(fw):
    index(fw, {b'a.m4b': book(table(1))})
    assert len(fw.calls('fw_fclose')) == 1


def fits(fw, need_samples, meminfo):
    files = {b'a.m4b': book(table(need_samples))}
    if meminfo is not None:
        files[b'/proc/meminfo'] = meminfo
    Card(fw, files)
    return fw.fn('mp4_fits', C.c_int, C.c_char_p)(b'a.m4b')


def info(kb):
    return b'MemTotal:       122000 kB\nMemFree:         9000 kB\nMemAvailable:   %d kB\nBuffers: 1 kB\n' % kb


def test_the_index_must_fit_memavailable_less_the_spare(fw):
    samples = 1024 * 10  # 280 KB of index
    assert fits(fw, samples, info(280 + 16384)) == 1
    assert fits(fw, samples, info(280 + 16384 - 1)) == 0


def test_no_estimate_plays_as_stock(fw):
    assert fits(fw, 10**8, b'MemTotal: 1 kB\n') == 1
    assert fits(fw, 10**8, None) == 1


def test_a_file_that_is_no_mp4_fits_without_reading_memory(fw):
    Card(fw, {})
    assert fw.fn('mp4_fits', C.c_int, C.c_char_p)(b'a.mp3') == 1 and not fw.calls('fw_fopen')


def test_a_file_too_large_to_open_never_fits(fw):
    Card(fw, {b'/proc/meminfo': info(10**9)}, errno=EOVERFLOW)
    assert fw.fn('mp4_fits', C.c_int, C.c_char_p)(b'a.m4b') == 0


def test_hostile_mp4(fw):
    samples = [book(table(1000), table(5, b'stz2')), table(1), box(b'moov', box(b'trak', table(3)))]
    card = Card(fw, {})
    bytes_of = fw.fn('mp4_index_bytes', C.c_ulonglong, C.c_char_p)
    for data in hostile(2, samples, 1500):
        card.files[b'a.m4b'] = data
        card.open.clear()
        card.reads = 0
        assert bytes_of(b'a.m4b') % 28 == 0
        assert card.reads <= len(data) + 2  # a walk always moves on
